from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import (
    EXECUTION_MODE_RECONCILE,
    SIDE_EFFECT_NOT_APPLIED,
    SIDE_EFFECT_UNKNOWN,
    SIDE_EFFECT_VERIFIED,
    STATUS_FAILED,
    STATUS_NEEDS_RECONCILIATION,
    STATUS_NOT_APPLIED,
    STATUS_VERIFIED,
    ShadowBotResultContract,
)
from scripts.prepare_shadowbot_e2e_chain import PreparedShadowBotChain, prepare_shadowbot_chain_from_args


DEFAULT_PLATFORM = "蚂蚁花团供应商"
DEFAULT_SKU = "SKU-AISHA-C"
DEFAULT_PRODUCT_NAME = "艾莎"
DEFAULT_GRADE = "C级"
DEFAULT_OLD_PRICE = "19.00"
DEFAULT_TARGET_PRICE = "19.50"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local ShadowBot E2E demo covering three result branches.")
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--request-dir", type=Path, required=True)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM)
    parser.add_argument("--sku", default=DEFAULT_SKU)
    parser.add_argument("--product-name", default=DEFAULT_PRODUCT_NAME)
    parser.add_argument("--grade", default=DEFAULT_GRADE)
    parser.add_argument("--expected-old-price", default=DEFAULT_OLD_PRICE)
    parser.add_argument("--target-price", default=DEFAULT_TARGET_PRICE)
    parser.add_argument("--trade-date", default="2026-06-26")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run_local_demo_from_args(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_local_demo_from_args(args: argparse.Namespace) -> dict[str, object]:
    args.request_dir.mkdir(parents=True, exist_ok=True)
    original_env = {
        "SHADOWBOT_RUNNER_TYPE": os.environ.get("SHADOWBOT_RUNNER_TYPE"),
        "SHADOWBOT_REQUEST_DIR": os.environ.get("SHADOWBOT_REQUEST_DIR"),
    }
    os.environ["SHADOWBOT_RUNNER_TYPE"] = "filedrop"
    os.environ["SHADOWBOT_REQUEST_DIR"] = str(args.request_dir)
    try:
        branches = {
            "success": _prepare_and_start(args, suffix="SUCCESS"),
            "pre_submit_failed": _prepare_and_start(args, suffix="PREFAIL"),
            "post_submit_unknown": _prepare_and_start(args, suffix="UNKNOWN"),
        }
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    repository = SQLiteRuntimeRepository(args.runtime_db)

    repository_executor = _executor_for_existing_attempt(repository, args.request_dir)
    repository_executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=branches["success"].execution_attempt_id,
            status=STATUS_VERIFIED,
            run_success_flag=True,
            business_operation_completed=True,
            side_effect_state=SIDE_EFFECT_VERIFIED,
            retryable=False,
            **_binding_fields(repository, branches["success"].execution_attempt_id),
            raw_output=_result_payload(
                branches["success"],
                status=STATUS_VERIFIED,
                side_effect_state=SIDE_EFFECT_VERIFIED,
                old_price=args.expected_old_price,
                target_price=args.target_price,
                actual_price=args.target_price,
                evidence_type="AFTER_SUBMIT",
            ),
        )
    )
    repository_executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=branches["pre_submit_failed"].execution_attempt_id,
            status=STATUS_FAILED,
            run_success_flag=False,
            business_operation_completed=False,
            side_effect_state="NOT_STARTED",
            retryable=False,
            error_code="PRODUCT_NOT_FOUND",
            **_binding_fields(repository, branches["pre_submit_failed"].execution_attempt_id),
            raw_output=_result_payload(
                branches["pre_submit_failed"],
                status=STATUS_FAILED,
                side_effect_state="NOT_STARTED",
                old_price=args.expected_old_price,
                target_price=args.target_price,
                actual_price=args.expected_old_price,
                evidence_type="ERROR",
                error_code="PRODUCT_NOT_FOUND",
            ),
        )
    )
    repository_executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=branches["post_submit_unknown"].execution_attempt_id,
            status=STATUS_NEEDS_RECONCILIATION,
            run_success_flag=None,
            business_operation_completed=None,
            side_effect_state=SIDE_EFFECT_UNKNOWN,
            retryable=False,
            error_code="SUBMIT_RESULT_UNKNOWN",
            **_binding_fields(repository, branches["post_submit_unknown"].execution_attempt_id),
            raw_output=_result_payload(
                branches["post_submit_unknown"],
                status=STATUS_NEEDS_RECONCILIATION,
                side_effect_state=SIDE_EFFECT_UNKNOWN,
                old_price=args.expected_old_price,
                target_price=args.target_price,
                actual_price="",
                evidence_type="UNKNOWN",
                error_code="SUBMIT_RESULT_UNKNOWN",
            ),
        )
    )
    reconcile_attempt_id = "RECONCILE-" + hashlib.sha256(
        branches["post_submit_unknown"].execution_attempt_id.encode("utf-8")
    ).hexdigest()[:20]
    reconcile_attempt = repository.get_shadowbot_execution_attempt(reconcile_attempt_id)
    if reconcile_attempt is None:
        raise RuntimeError("automatic reconcile attempt was not created")
    repository_executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=reconcile_attempt_id,
            status=STATUS_NOT_APPLIED,
            run_success_flag=True,
            business_operation_completed=False,
            side_effect_state=SIDE_EFFECT_NOT_APPLIED,
            retryable=False,
            error_code="SUBMIT_NOT_APPLIED",
            **_binding_fields(repository, reconcile_attempt_id),
            raw_output={
                "execution_attempt_id": reconcile_attempt_id,
                "status": STATUS_NOT_APPLIED,
                "execution_mode": EXECUTION_MODE_RECONCILE,
                "side_effect_state": SIDE_EFFECT_NOT_APPLIED,
                "old_price": args.expected_old_price,
                "target_price": args.target_price,
                "actual_price": args.expected_old_price,
                "evidence_status": "COMPLETE",
                "evidence": [
                    {
                        "type": "RECONCILE",
                        "storage_uri": r"\\pra-evidence\demo\reconcile.png",
                        "sha256": "demo-reconcile-sha256",
                        "upload_status": "SUCCESS",
                    }
                ],
            },
        )
    )

    return {
        "runtime_db": str(args.runtime_db),
        "request_dir": str(args.request_dir),
        "branches": {
            name: {
                "task_id": prepared.task_id,
                "approval_id": prepared.approval_id,
                "operation_id": prepared.operation_id,
                "execution_attempt_id": prepared.execution_attempt_id,
            }
            for name, prepared in branches.items()
        },
        "reconcile": {
            "operation_id": reconcile_attempt.operation_id,
            "execution_attempt_id": reconcile_attempt_id,
            "shadowbot_run_id": reconcile_attempt.shadowbot_run_id,
        },
    }


def _prepare_and_start(args: argparse.Namespace, *, suffix: str) -> PreparedShadowBotChain:
    return prepare_shadowbot_chain_from_args(
        argparse.Namespace(
            runtime_db=args.runtime_db,
            platform=args.platform,
            sku=args.sku,
            product_name=args.product_name,
            grade=args.grade,
            expected_old_price=args.expected_old_price,
            target_price=args.target_price,
            task_id=f"TASK-DEMO-{suffix}",
            approval_id=f"REVIEW-DEMO-{suffix}",
            operation_id=f"OP-DEMO-{suffix}",
            execution_attempt_id=f"ATTEMPT-DEMO-{suffix}",
            trade_date=args.trade_date,
            approved_by="local-demo",
            approval_ttl_minutes=60,
            start=True,
        )
    )


def _executor_for_existing_attempt(repository: SQLiteRuntimeRepository, request_dir: Path):
    from app.services.shadowbot_executor import FileDropShadowBotTaskRunner, ShadowBotExecutor

    return ShadowBotExecutor(repository, FileDropShadowBotTaskRunner(request_dir))


def _binding_fields(repository: SQLiteRuntimeRepository, execution_attempt_id: str) -> dict[str, str]:
    attempt = repository.get_shadowbot_execution_attempt(execution_attempt_id)
    if attempt is None:
        raise RuntimeError(f"missing ShadowBot attempt: {execution_attempt_id}")
    operation = repository.get_shadowbot_operation(attempt.operation_id)
    if operation is None:
        raise RuntimeError(f"missing ShadowBot operation: {attempt.operation_id}")
    return {
        "operation_id": operation.operation_id,
        "task_id": operation.task_id,
        "execution_mode": attempt.execution_mode,
        "instruction_hash": attempt.instruction_hash,
        "request_file_sha256": attempt.request_file_sha256,
        "worker_id": "local-demo",
    }


def _result_payload(
    prepared: PreparedShadowBotChain,
    *,
    status: str,
    side_effect_state: str,
    old_price: str,
    target_price: str,
    actual_price: str,
    evidence_type: str,
    error_code: str = "",
) -> dict[str, object]:
    return {
        "operation_id": prepared.operation_id,
        "execution_attempt_id": prepared.execution_attempt_id,
        "shadowbot_run_id": prepared.shadowbot_run_id,
        "execution_mode": "COMMIT",
        "status": status,
        "side_effect_state": side_effect_state,
        "old_price": old_price,
        "target_price": target_price,
        "actual_price": actual_price,
        "evidence_status": "COMPLETE" if not error_code else "PARTIAL",
        "error_code": error_code,
        "evidence": [
            {
                "type": evidence_type,
                "storage_uri": rf"\\pra-evidence\demo\{prepared.execution_attempt_id}.png",
                "sha256": f"demo-{prepared.execution_attempt_id.lower()}-sha256",
                "upload_status": "SUCCESS",
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
