from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.enums import (
    ReviewTaskStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.models import ReviewTask, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import DEFAULT_RUNTIME_DB, RuntimeTaskService
from app.services.shadowbot_executor import (
    ALLOWED_EXECUTION_MODES,
    EXECUTION_MODE_COMMIT,
    ShadowBotApproval,
    ShadowBotApprovedPayload,
    ShadowBotExecutionRequest,
    ShadowBotExecutor,
    build_shadowbot_task_runner_from_environment,
    compute_approved_payload_hash,
)


@dataclass(slots=True)
class PreparedShadowBotChain:
    task_id: str
    approval_id: str
    operation_id: str
    execution_attempt_id: str
    approved_payload_hash: str
    execution_mode: str
    started: bool
    shadowbot_run_id: str = ""
    status: str = "PREPARED"
    side_effect_state: str = "NOT_STARTED"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the first ShadowBot E2E chain.")
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--platform-sku", default="", help="Platform SKU; defaults to --sku for compatibility.")
    parser.add_argument("--product-name", required=True)
    parser.add_argument("--grade", required=True)
    parser.add_argument("--expected-old-price", required=True)
    parser.add_argument("--target-price", required=True)
    parser.add_argument(
        "--execution-mode",
        choices=sorted(ALLOWED_EXECUTION_MODES),
        default=EXECUTION_MODE_COMMIT,
    )
    parser.add_argument("--task-id", default="")
    parser.add_argument("--approval-id", default="")
    parser.add_argument("--operation-id", default="")
    parser.add_argument("--execution-attempt-id", default="")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--approved-by", default="local-operator")
    parser.add_argument("--approval-ttl-minutes", type=int, default=60)
    parser.add_argument("--source-read-attempt-id", default="")
    parser.add_argument(
        "--evidence-share-dir",
        default="",
        help="Optional per-attempt evidence destination override.",
    )
    parser.add_argument("--start", action="store_true", help="Start ShadowBot after creating the approved review.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = prepare_shadowbot_chain_from_args(args)
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


def prepare_shadowbot_chain_from_args(args: argparse.Namespace) -> PreparedShadowBotChain:
    execution_mode = str(getattr(args, "execution_mode", EXECUTION_MODE_COMMIT) or EXECUTION_MODE_COMMIT)
    source_read_attempt_id = str(getattr(args, "source_read_attempt_id", "") or "")
    trade_date = date.fromisoformat(args.trade_date) if args.trade_date else date.today()
    task_id = args.task_id or f"TASK-SHADOWBOT-{uuid4().hex[:12]}"
    approval_id = args.approval_id or f"REVIEW-SHADOWBOT-{uuid4().hex[:12]}"
    operation_id = args.operation_id or f"OP-SHADOWBOT-{uuid4().hex[:12]}"
    execution_attempt_id = args.execution_attempt_id or f"ATTEMPT-SHADOWBOT-{uuid4().hex[:12]}"

    repository = SQLiteRuntimeRepository(args.runtime_db)
    task_service = RuntimeTaskService(repository)
    task_service.init_schema()

    payload = ShadowBotApprovedPayload(
        operation_id=operation_id,
        task_id=task_id,
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
    approved_payload_hash = compute_approved_payload_hash(payload)
    created_at = datetime.now()
    approved_at = datetime.now(UTC)

    task_service.create_tasks(
        [
            Task(
                task_id=task_id,
                internal_sku=args.sku,
                platform_name=args.platform,
                action_type=TaskActionType.UPDATE_PRICE,
                priority=1,
                task_status=TaskStatus.PENDING,
                created_at=created_at,
                origin_type=TaskOriginType.MANUAL,
                origin_ref_id=f"acceptance:{operation_id}",
                target_price=payload.target_price,
                trade_date=trade_date,
                scope_type="sku",
                scope_key=args.sku,
                dedupe_key=f"{trade_date.isoformat()}|shadowbot|{operation_id}|update_price",
                decision_trace={
                    "source": "prepare_shadowbot_e2e_chain",
                    "operation_id": operation_id,
                    "expected_old_price": str(payload.expected_old_price),
                    "target_price": str(payload.target_price),
                    "source_read_attempt_id": source_read_attempt_id,
                },
            )
        ]
    )
    repository.insert_review_tasks(
        [
            ReviewTask(
                review_task_id=approval_id,
                trade_date=trade_date,
                scope_type="sku",
                scope_key=args.sku,
                dedupe_key=f"{trade_date.isoformat()}|shadowbot-review|{operation_id}",
                source_task_id=task_id,
                review_type="shadowbot_update_price",
                review_status=ReviewTaskStatus.APPROVED,
                internal_sku=args.sku,
                platform_name=args.platform,
                reason="approved ShadowBot update_price chain",
                review_payload={
                    "approved_payload_hash": approved_payload_hash,
                    "source_read_attempt_id": source_read_attempt_id,
                },
                resolution_payload={
                    "approved_payload_hash": approved_payload_hash,
                    "source_read_attempt_id": source_read_attempt_id,
                },
                required_by=created_at + timedelta(minutes=args.approval_ttl_minutes),
                created_at=created_at,
                updated_at=created_at,
                resolved_by=args.approved_by,
                resolved_at=created_at,
                resolution_note="Prepared by scripts/prepare_shadowbot_e2e_chain.py",
            )
        ]
    )

    started = False
    shadowbot_run_id = ""
    status = "PREPARED"
    side_effect_state = "NOT_STARTED"
    if args.start:
        approval = ShadowBotApproval(
            approval_id=approval_id,
            approval_status="APPROVED",
            approved_payload=payload,
            approved_payload_hash=approved_payload_hash,
            approved_at=approved_at,
            expires_at=approved_at + timedelta(minutes=args.approval_ttl_minutes),
        )
        executor = ShadowBotExecutor(repository, build_shadowbot_task_runner_from_environment())
        runner_payload = {}
        if str(getattr(args, "evidence_share_dir", "") or "").strip():
            runner_payload["evidence_share_dir"] = str(args.evidence_share_dir).strip()
        start_result = executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id=operation_id,
                execution_attempt_id=execution_attempt_id,
                execution_mode=execution_mode,
                approval=approval,
                runner_payload=runner_payload,
            )
        )
        started = True
        shadowbot_run_id = start_result.shadowbot_run_id
        status = start_result.status
        side_effect_state = start_result.side_effect_state

    return PreparedShadowBotChain(
        task_id=task_id,
        approval_id=approval_id,
        operation_id=operation_id,
        execution_attempt_id=execution_attempt_id,
        approved_payload_hash=approved_payload_hash,
        execution_mode=execution_mode,
        started=started,
        shadowbot_run_id=shadowbot_run_id,
        status=status,
        side_effect_state=side_effect_state,
    )


if __name__ == "__main__":
    raise SystemExit(main())
