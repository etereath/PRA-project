from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from scripts.prepare_shadowbot_e2e_chain import prepare_shadowbot_chain_from_args
from scripts.verify_shadowbot_filequeue_acceptance import verify_acceptance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight and prepare a controlled ShadowBot COMMIT acceptance")
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--source-read-attempt-id", required=True)
    parser.add_argument("--target-price", required=True)
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--confirmation-text", default="")
    parser.add_argument("--max-read-age-minutes", type=int, default=10)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--approval-id", default="")
    parser.add_argument("--operation-id", default="")
    parser.add_argument("--execution-attempt-id", default="")
    parser.add_argument("--start", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = prepare_commit_acceptance_from_args(args)
    except ValidationError as exc:
        print(json.dumps({"ok": False, "error_code": "COMMIT_PREFLIGHT_FAILED", "error_message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def prepare_commit_acceptance_from_args(args: argparse.Namespace) -> dict[str, object]:
    repository = SQLiteRuntimeRepository(args.runtime_db)
    repository.init_schema()
    source_attempt = repository.get_shadowbot_execution_attempt(args.source_read_attempt_id)
    if source_attempt is None:
        raise ValidationError("source READ_ONLY attempt does not exist.")
    if source_attempt.execution_mode != "READ_ONLY" or source_attempt.status != "READ_COMPLETED":
        raise ValidationError("source attempt must be a completed READ_ONLY attempt.")
    if source_attempt.ended_at is None:
        raise ValidationError("source READ_ONLY attempt has no ended_at.")
    ended_at = source_attempt.ended_at
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=UTC)
    max_age = timedelta(minutes=max(int(args.max_read_age_minutes), 1))
    if datetime.now(UTC) - ended_at.astimezone(UTC) > max_age:
        raise ValidationError("source READ_ONLY attempt is stale; run a new READ_ONLY before COMMIT.")

    acceptance = verify_acceptance(
        runtime_db=args.runtime_db,
        queue_dir=args.queue_dir,
        execution_attempt_id=args.source_read_attempt_id,
        execution_mode="READ_ONLY",
        require_shared_evidence=True,
    )
    if not acceptance["ok"]:
        raise ValidationError("source READ_ONLY acceptance failed: " + ", ".join(acceptance["failed_checks"]))

    operation = repository.get_shadowbot_operation(source_attempt.operation_id)
    if operation is None:
        raise ValidationError("source READ_ONLY operation does not exist.")
    product = operation.product_identity
    name = str(product.get("expected_product_name") or product.get("name") or "").strip()
    grade = str(product.get("expected_grade") or product.get("grade") or "").strip()
    internal_sku = str(product.get("internal_sku") or product.get("sku") or "").strip()
    platform_sku = str(product.get("platform_sku") or product.get("sku") or internal_sku).strip()
    actual_price = str(source_attempt.raw_output.get("actual_price") or source_attempt.raw_output.get("old_price") or "").strip()
    if not actual_price:
        raise ValidationError("source READ_ONLY result has no actual price.")
    try:
        old_decimal = Decimal(actual_price)
        target_decimal = Decimal(str(args.target_price))
    except InvalidOperation as exc:
        raise ValidationError("old price and target price must be valid decimals.") from exc
    if old_decimal == target_decimal:
        raise ValidationError("target price must differ from the latest actual price.")

    active_files = []
    for directory in ("inbox", "working", "results"):
        active_files.extend((args.queue_dir / directory).glob("*"))
    if active_files:
        raise ValidationError("queue must be empty before controlled COMMIT acceptance.")
    if (args.queue_dir / "control" / "stop.signal").exists():
        raise ValidationError("stop.signal must be removed before COMMIT acceptance.")

    required_confirmation = f"COMMIT {grade}{name} {old_decimal:.2f} -> {target_decimal:.2f}"
    preflight = {
        "ok": True,
        "ready_to_start": bool(args.start),
        "source_read_attempt_id": args.source_read_attempt_id,
        "source_read_ended_at": ended_at.astimezone(UTC).isoformat(),
        "platform_name": operation.platform,
        "product_name": name,
        "grade": grade,
        "expected_old_price": f"{old_decimal:.2f}",
        "target_price": f"{target_decimal:.2f}",
        "required_confirmation_text": required_confirmation,
        "confirmed_by": args.confirmed_by,
    }
    if not args.start:
        return preflight
    if str(args.confirmation_text) != required_confirmation:
        raise ValidationError("confirmation text mismatch; COMMIT was not started.")

    suffix = uuid4().hex[:12]
    prepared = prepare_shadowbot_chain_from_args(
        argparse.Namespace(
            runtime_db=args.runtime_db,
            platform=operation.platform,
            sku=internal_sku,
            platform_sku=platform_sku,
            product_name=name,
            grade=grade,
            expected_old_price=f"{old_decimal:.2f}",
            target_price=f"{target_decimal:.2f}",
            execution_mode="COMMIT",
            task_id=args.task_id or f"TASK-ACCEPT-COMMIT-{suffix}",
            approval_id=args.approval_id or f"REVIEW-ACCEPT-COMMIT-{suffix}",
            operation_id=args.operation_id or f"OP-ACCEPT-COMMIT-{suffix}",
            execution_attempt_id=args.execution_attempt_id or f"ATTEMPT-ACCEPT-COMMIT-{suffix}",
            trade_date="",
            approved_by=args.confirmed_by,
            approval_ttl_minutes=max(int(args.max_read_age_minutes), 1),
            source_read_attempt_id=args.source_read_attempt_id,
            start=True,
        )
    )
    preflight["prepared"] = asdict(prepared)
    return preflight


if __name__ == "__main__":
    raise SystemExit(main())
