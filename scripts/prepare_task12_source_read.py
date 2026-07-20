from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.enums import TaskActionType, TaskStatus
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import ShadowBotExecutor, ShadowBotFileQueueRunner
from app.services.shadowbot_product_read import normalize_multi_product_request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a fresh task 11 v2 READ_ONLY source for task 12 acceptance",
    )
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--source-request", type=Path, required=True)
    parser.add_argument("--read-batch-id", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--execution-attempt-id", default="")
    parser.add_argument("--lease-seconds", type=int, default=900)
    return parser


def prepare_task12_source_read(
    *,
    runtime_db: Path,
    queue_dir: Path,
    source_request: Path,
    read_batch_id: str = "",
    task_id: str = "",
    execution_attempt_id: str = "",
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _aware_utc(now or datetime.now(timezone.utc))
    suffix = timestamp.strftime("%Y%m%d-%H%M%S")
    batch_id = str(read_batch_id or f"READ-BATCH-T12-SOURCE-{suffix}").strip()
    runtime_task_id = str(task_id or f"TASK-T12-SOURCE-{suffix}").strip()
    attempt_id = str(execution_attempt_id or f"ATTEMPT-T12-SOURCE-{suffix}").strip()
    if not batch_id or not runtime_task_id or not attempt_id:
        raise ValueError("read batch, task, and execution attempt IDs are required")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")

    template = _read_json_object(source_request)
    raw_products = template.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise ValueError("source request must contain a non-empty products array")
    products: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(raw_products, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("source request products must contain objects")
        products.append(
            {
                "item_id": f"ITEM-T12-SOURCE-{ordinal:02d}-{suffix}",
                "expected_product_name": raw.get("expected_product_name"),
                "expected_grade": raw.get("expected_grade"),
                "platform": raw.get("platform"),
                "platform_sku": raw.get("platform_sku"),
            }
        )

    request_payload = normalize_multi_product_request(
        {
            "contract_version": 2,
            "execution_mode": "READ_ONLY",
            "read_batch_id": batch_id,
            "platform_name": str(template.get("platform_name") or "").strip(),
            "products": products,
            "limits": template.get(
                "limits",
                {"max_pages": 20, "max_scrolls": 100, "max_seconds": 300},
            ),
            "capture_evidence": False,
        }
    )
    platform_name = request_payload["products"][0]["platform"]
    runner_payload = {
        "applet_uri": str(template.get("applet_uri") or "").strip(),
        "window_title": str(template.get("window_title") or platform_name).strip(),
    }
    for optional_name in (
        "applet_launch_timeout_seconds",
        "element_timeout_seconds",
        "evidence_share_dir",
    ):
        if optional_name in template:
            runner_payload[optional_name] = template[optional_name]
    if not runner_payload["applet_uri"] or not runner_payload["window_title"]:
        raise ValueError("source request must provide applet_uri and window_title")

    repository = SQLiteRuntimeRepository(runtime_db)
    repository.init_schema()
    if repository.get_task(runtime_task_id) is None:
        repository.insert_task(
            Task(
                task_id=runtime_task_id,
                internal_sku=batch_id,
                platform_name=platform_name,
                action_type=TaskActionType.SYNC_STATUS,
                priority=100,
                task_status=TaskStatus.PENDING,
                created_at=timestamp.replace(tzinfo=None),
                trade_date=date.fromisoformat(timestamp.date().isoformat()),
                scope_type="read_batch",
                scope_key=batch_id,
                dedupe_key=f"{timestamp.date().isoformat()}|task12-source-read|{batch_id}",
                decision_trace={
                    "source": "prepare_task12_source_read",
                    "source_request": str(source_request.resolve()),
                    "capture_evidence": False,
                },
            )
        )

    executor = ShadowBotExecutor(repository, ShadowBotFileQueueRunner(queue_dir))
    started = executor.start_multi_product_read(
        task_id=runtime_task_id,
        execution_attempt_id=attempt_id,
        request_payload=request_payload,
        lease_seconds=lease_seconds,
        runner_payload=runner_payload,
    )
    attempt = repository.get_shadowbot_execution_attempt(attempt_id)
    if attempt is None:
        raise RuntimeError("source READ_ONLY attempt was not persisted")
    return {
        "ok": True,
        "execution_mode": "READ_ONLY",
        "capture_evidence": False,
        "read_batch_id": batch_id,
        "task_id": runtime_task_id,
        "operation_id": started.operation_id,
        "execution_attempt_id": attempt_id,
        "shadowbot_run_id": started.shadowbot_run_id,
        "queue_request_path": attempt.queue_request_path,
        "request_file_sha256": attempt.request_file_sha256,
        "product_count": len(products),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("source request JSON must contain an object")
    return payload


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    result = prepare_task12_source_read(
        runtime_db=args.runtime_db,
        queue_dir=args.queue_dir,
        source_request=args.source_request,
        read_batch_id=args.read_batch_id,
        task_id=args.task_id,
        execution_attempt_id=args.execution_attempt_id,
        lease_seconds=args.lease_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
