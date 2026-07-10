from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import DEFAULT_RUNTIME_DB, RuntimeTaskService
from app.services.shadowbot_executor import (
    EXECUTION_MODE_COMMIT,
    FileDropShadowBotTaskRunner,
    ShadowBotApproval,
    ShadowBotApprovedPayload,
    ShadowBotExecutionRequest,
    ShadowBotExecutor,
    ShadowBotFileQueueRunner,
    ShadowBotResultContract,
    YingdaoOpenApiJobRunner,
    build_shadowbot_task_runner_from_environment,
    compute_approved_payload_hash,
    shadowbot_result_contract_from_data,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ShadowBot Executor bridge for PRA")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start a ShadowBot execution attempt from an approved review")
    start_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    start_parser.add_argument("--approval-id", required=True)
    start_parser.add_argument("--operation-id", required=True)
    start_parser.add_argument("--execution-attempt-id", required=True)
    start_parser.add_argument("--execution-mode", default=EXECUTION_MODE_COMMIT)
    start_parser.add_argument("--platform", required=True)
    start_parser.add_argument("--sku", required=True)
    start_parser.add_argument("--platform-sku", default="", help="Platform SKU; defaults to --sku for compatibility.")
    start_parser.add_argument("--product-name", required=True)
    start_parser.add_argument("--grade", required=True)
    start_parser.add_argument("--expected-old-price", required=True)
    start_parser.add_argument("--target-price", required=True)
    start_parser.add_argument("--request-dir", type=Path)
    start_parser.add_argument("--runner-command", default="")
    start_parser.add_argument(
        "--runner-type",
        choices=["filequeue", "filedrop", "yingdao_openapi"],
        default=None,
        help="Defaults to SHADOWBOT_RUNNER_TYPE or filedrop.",
    )

    import_parser = subparsers.add_parser("import-result", help="Import a ShadowBot result JSON and update PRA runtime")
    import_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    import_parser.add_argument("--result-json", required=True, type=Path)

    poll_parser = subparsers.add_parser(
        "poll-yingdao-result",
        help="Query Yingdao job output params and import the ShadowBot result JSON",
    )
    poll_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    poll_parser.add_argument("--job-uuid", required=True)
    poll_parser.add_argument("--result-param-name", default="shadowbot_result_json")

    check_parser = subparsers.add_parser(
        "check-yingdao-app-params",
        help="Check Yingdao app input/output params before starting a real job",
    )
    check_parser.add_argument("--request-param-name", default="request_json")
    check_parser.add_argument("--result-param-name", default="shadowbot_result_json")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = start_from_args(args)
            print(
                json.dumps(
                    {
                        "operation_id": result.operation_id,
                        "execution_attempt_id": result.execution_attempt_id,
                        "shadowbot_run_id": result.shadowbot_run_id,
                        "status": result.status,
                        "side_effect_state": result.side_effect_state,
                        "next_execution_mode": result.next_execution_mode,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "import-result":
            record_result_from_file(args.runtime_db, args.result_json)
            print(f"imported ShadowBot result: {args.result_json}")
            return 0
        if args.command == "poll-yingdao-result":
            result = poll_yingdao_result_from_args(args)
            print(
                json.dumps(
                    {
                        "job_uuid": args.job_uuid,
                        "execution_attempt_id": result.execution_attempt_id,
                        "status": result.status,
                        "side_effect_state": result.side_effect_state,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "check-yingdao-app-params":
            result = check_yingdao_app_params_from_args(args)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        parser.error("unknown command")
        return 2
    except ValidationError as exc:
        print(f"错误：{exc}")
        return 1


def start_from_args(args: argparse.Namespace):
    repository = SQLiteRuntimeRepository(args.runtime_db)
    RuntimeTaskService(repository).init_schema()
    runner = _runner_from_args(args)
    executor = ShadowBotExecutor(repository, runner)
    payload = ShadowBotApprovedPayload(
        operation_id=args.operation_id,
        task_id=_task_id_for_approval(repository, args.approval_id),
        platform=args.platform,
        product_identity={
            "internal_sku": args.sku,
            "platform_sku": getattr(args, "platform_sku", "") or args.sku,
            "name": args.product_name,
            "grade": args.grade,
        },
        expected_old_price=Decimal(str(args.expected_old_price)),
        target_price=Decimal(str(args.target_price)),
    )
    approval = ShadowBotApproval(
        approval_id=args.approval_id,
        approval_status="APPROVED",
        approved_payload=payload,
        approved_payload_hash=compute_approved_payload_hash(payload),
        approved_at=datetime.now(UTC),
    )
    return executor.start_execution(
        ShadowBotExecutionRequest(
            operation_id=args.operation_id,
            execution_attempt_id=args.execution_attempt_id,
            execution_mode=args.execution_mode,
            approval=approval,
        )
    )


def record_result_from_file(runtime_db: Path, result_json: Path) -> None:
    data = json.loads(result_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError("ShadowBot result JSON must be an object.")
    record_result_from_data(runtime_db, data)


def poll_yingdao_result_from_args(args: argparse.Namespace) -> ShadowBotResultContract:
    runner = YingdaoOpenApiJobRunner.from_environment()
    response = runner.query_job(args.job_uuid)
    return record_result_from_yingdao_job_query(
        args.runtime_db,
        response,
        result_param_name=args.result_param_name,
    )


def check_yingdao_app_params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    runner = YingdaoOpenApiJobRunner.from_environment()
    response = runner.query_robot_params()
    return check_yingdao_app_params(
        response,
        request_param_name=args.request_param_name,
        result_param_name=args.result_param_name,
    )


def check_yingdao_app_params(
    response: dict[str, Any],
    *,
    request_param_name: str = "request_json",
    result_param_name: str = "shadowbot_result_json",
) -> dict[str, Any]:
    if not bool(response.get("success")):
        raise ValidationError(
            f"Yingdao queryRobotParam failed: {response.get('code', '')} {response.get('msg', '')}".strip()
        )
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise ValidationError("Yingdao queryRobotParam response did not include app param data.")
    app_data = data[0] if isinstance(data[0], dict) else {}
    input_names = _param_names(app_data.get("inputParams"))
    output_names = _param_names(app_data.get("outputParams"))
    missing_inputs = [request_param_name] if request_param_name not in input_names else []
    missing_outputs = [result_param_name] if result_param_name not in output_names else []
    ok = not missing_inputs and not missing_outputs
    return {
        "ok": ok,
        "request_param_name": request_param_name,
        "result_param_name": result_param_name,
        "input_names": sorted(input_names),
        "output_names": sorted(output_names),
        "missing_inputs": missing_inputs,
        "missing_outputs": missing_outputs,
    }


def record_result_from_yingdao_job_query(
    runtime_db: Path,
    job_query_response: dict[str, Any],
    *,
    result_param_name: str = "shadowbot_result_json",
) -> ShadowBotResultContract:
    result_data = _extract_shadowbot_result_from_yingdao_job_query(
        job_query_response,
        result_param_name=result_param_name,
    )
    record_result_from_data(runtime_db, result_data)
    return _result_contract_from_data(result_data)


def record_result_from_data(runtime_db: Path, data: dict[str, Any]) -> None:
    executor = ShadowBotExecutor(
        SQLiteRuntimeRepository(runtime_db),
        build_shadowbot_task_runner_from_environment(),
    )
    executor.record_result(_result_contract_from_data(data))


def _task_id_for_approval(repository: SQLiteRuntimeRepository, approval_id: str) -> str:
    review = repository.get_review_task(approval_id)
    if review is None:
        raise ValidationError(f"approval record does not exist: {approval_id}")
    if not review.source_task_id:
        raise ValidationError(f"approval has no source_task_id: {approval_id}")
    return review.source_task_id


def _runner_from_args(args: argparse.Namespace):
    runner_type = str(getattr(args, "runner_type", "") or "").strip().lower()
    if not runner_type:
        runner_type = _env_runner_type()
    if runner_type == "yingdao_openapi":
        return YingdaoOpenApiJobRunner.from_environment()
    if runner_type in {"filequeue", "file_queue"}:
        if args.request_dir:
            return ShadowBotFileQueueRunner(args.request_dir, command=args.runner_command)
        return ShadowBotFileQueueRunner.from_environment()
    return FileDropShadowBotTaskRunner(
        args.request_dir or Path("data/runtime/shadowbot_requests"),
        command=args.runner_command,
    )


def _env_runner_type() -> str:
    from os import environ

    value = environ.get("SHADOWBOT_RUNNER_TYPE", "filedrop").strip().lower()
    if value in {"yingdao_openapi", "yingdao_job", "openapi_job"}:
        return "yingdao_openapi"
    if value in {"filequeue", "file_queue"}:
        return "filequeue"
    return "filedrop"


def _result_contract_from_data(data: dict[str, Any]) -> ShadowBotResultContract:
    return shadowbot_result_contract_from_data(data)


def _extract_shadowbot_result_from_yingdao_job_query(
    response: dict[str, Any],
    *,
    result_param_name: str,
) -> dict[str, Any]:
    if not bool(response.get("success")):
        raise ValidationError(f"Yingdao job/query failed: {response.get('code', '')} {response.get('msg', '')}".strip())
    data = response.get("data")
    if not isinstance(data, dict):
        raise ValidationError("Yingdao job/query response did not include data object.")
    outputs = _yingdao_job_outputs(data)
    raw_value = None
    for item in outputs:
        if str(item.get("name") or "") == result_param_name:
            raw_value = item.get("value")
            break
    if raw_value is None:
        status = str(data.get("status") or "")
        raise ValidationError(f"Yingdao job output param not found: {result_param_name}; job status={status}")
    if isinstance(raw_value, dict):
        result = raw_value
    else:
        result = json.loads(str(raw_value))
    if not isinstance(result, dict):
        raise ValidationError("ShadowBot result output param must be a JSON object.")
    result.setdefault(
        "yingdao_job",
        {
            "jobUuid": data.get("jobUuid"),
            "status": data.get("status"),
            "statusName": data.get("statusName"),
            "robotUuid": data.get("robotUuid"),
            "robotName": data.get("robotName"),
        },
    )
    return result


def _yingdao_job_outputs(data: dict[str, Any]) -> list[dict[str, Any]]:
    robot_params = data.get("robotParams")
    if isinstance(robot_params, dict):
        outputs = robot_params.get("outputs")
        if isinstance(outputs, list):
            return [item for item in outputs if isinstance(item, dict)]
    outputs = data.get("outputs")
    if isinstance(outputs, list):
        return [item for item in outputs if isinstance(item, dict)]
    return []


def _param_names(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    names: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            if name:
                names.add(name)
    return names


def _required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "")
    if not value:
        raise ValidationError(f"missing required ShadowBot result field: {key}")
    return value


def _nullable_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        if lowered in {"null", "none", ""}:
            return None
    raise ValidationError(f"invalid nullable boolean: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
