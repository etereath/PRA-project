"""Run a controlled Task 13.5-4 order READ_ONLY acceptance.

The script uses a caller-provided disposable Runtime database. It prints only
status, counts, identifiers, and hashes; observed order values are never
written to the repository or emitted to stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.mayi_huatuan_order import (  # noqa: E402
    MAYI_HUATUAN_PLATFORM,
    MayiHuatuanOrderReadOnlyAdapter,
)
from app.automation_models import (  # noqa: E402
    AutomationJob,
    AutomationRunOutcome,
)
from app.enums import AutomationRunStatus  # noqa: E402
from app.repositories.automation_repository import (  # noqa: E402
    AutomationRepository,
)
from app.repositories.sqlite_runtime_repository import (  # noqa: E402
    SQLiteRuntimeRepository,
)
from app.services.automation import (  # noqa: E402
    AutomationExecutionContext,
    CHILD_ONLY,
    FULL_MARKET_SCAN,
    INTERVAL_MINUTES,
    ORDER_SCAN,
)
from app.services.operational_time import (  # noqa: E402
    OperationalTimeService,
)
from app.services.order_observation import (  # noqa: E402
    OrderObservationImporter,
)
from app.services.order_scan_automation import (  # noqa: E402
    FullMarketScanOrderCoordinator,
    OrderScanHandler,
)
from app.services.product_mapping import (  # noqa: E402
    compile_product_mapping_rows,
)
from app.services.shadowbot_order_read import (  # noqa: E402
    ShadowBotFileQueueOrderTransport,
    ShadowBotOrderPageReader,
)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _job(
    job_id: str,
    job_type: str,
    *,
    schedule_kind: str,
    enabled: bool,
) -> AutomationJob:
    return AutomationJob(
        job_id=job_id,
        job_type=job_type,
        display_name=job_type,
        enabled=enabled,
        schedule_kind=schedule_kind,
        schedule_expression=(
            "60" if schedule_kind == INTERVAL_MINUTES else "-"
        ),
        priority=50,
        config={
            "platform_name": MAYI_HUATUAN_PLATFORM,
            "catchup_policy": "LATEST_ONLY",
        },
    )


def run_acceptance(
    *,
    runtime_db: Path,
    queue_dir: Path,
    timeout_seconds: float,
    target_trade_date: date | None = None,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    operational_time = OperationalTimeService()
    time_context = operational_time.classify(now)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    attempt_id = f"ORDER-READ-T1354-{timestamp}"

    runtime = SQLiteRuntimeRepository(runtime_db)
    runtime.init_schema()
    repository = AutomationRepository(runtime)
    parent_job = repository.upsert_job(
        _job(
            "T1354-ACCEPT-FULL",
            FULL_MARKET_SCAN,
            schedule_kind=INTERVAL_MINUTES,
            enabled=True,
        ),
        now=now,
    )
    repository.upsert_job(
        _job(
            "AUTOMATION-ORDER-SCAN-CHILD",
            ORDER_SCAN,
            schedule_kind=CHILD_ONLY,
            enabled=False,
        ),
        now=now,
    )
    parent = repository.ensure_run(
        job=parent_job,
        scheduled_for=now,
        time_context=time_context,
        initial_status=AutomationRunStatus.SCHEDULED,
        now=now,
    )[0]
    parent_claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token=f"{attempt_id}-parent",
        now=now,
        lease_seconds=600,
    )
    if parent_claim is None:
        raise RuntimeError("could not claim acceptance parent run")
    parent_context = AutomationExecutionContext(
        claim=parent_claim,
        repository=repository,
        operational_time=operational_time,
        clock=lambda: datetime.now(timezone.utc),
        lease_seconds=600,
    )
    parent_outcome = FullMarketScanOrderCoordinator(
        parent_handler=lambda run, context: AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS
        )
    )(parent_claim.run, parent_context)
    if not repository.finish_run(
        parent_context.claim,
        parent_outcome,
        now=datetime.now(timezone.utc),
    ):
        raise RuntimeError("could not finish acceptance parent run")

    child_run_id = str(
        parent_outcome.event_payload["order_scan_child_run_id"]
    )
    child_claim = repository.claim_run(
        run_id=child_run_id,
        owner_token=f"{attempt_id}-child",
        now=datetime.now(timezone.utc),
        lease_seconds=600,
    )
    if child_claim is None:
        raise RuntimeError("could not claim acceptance ORDER_SCAN run")

    transport = ShadowBotFileQueueOrderTransport(
        queue_dir,
        timeout_seconds=timeout_seconds,
    )
    reader = ShadowBotOrderPageReader(
        transport,
        attempt_id_factory=lambda: attempt_id,
    )
    mappings = compile_product_mapping_rows(
        (),
        source_workbook_sha256=hashlib.sha256(b"").hexdigest(),
    )
    handler = OrderScanHandler(
        adapter=MayiHuatuanOrderReadOnlyAdapter(
            reader,
            operational_time=operational_time,
        ),
        importer=OrderObservationImporter(
            runtime,
            operational_time=operational_time,
            clock=lambda: datetime.now(timezone.utc),
        ),
        mappings_provider=lambda: mappings,
        batch_id_factory=lambda run: f"ORDER-BATCH-{run.run_id}",
        target_trade_date=(
            (lambda run: target_trade_date)
            if target_trade_date is not None
            else (lambda run: run.platform_trade_date)
        ),
    )
    child_context = AutomationExecutionContext(
        claim=child_claim,
        repository=repository,
        operational_time=operational_time,
        clock=lambda: datetime.now(timezone.utc),
        lease_seconds=600,
    )
    child_outcome = handler(child_claim.run, child_context)
    if not repository.finish_run(
        child_context.claim,
        child_outcome,
        now=datetime.now(timezone.utc),
    ):
        raise RuntimeError("could not finish acceptance ORDER_SCAN run")

    with closing(runtime.connect_read()) as connection:
        batch = connection.execute(
            """
            SELECT trade_day_status, capability_result, batch_status,
                   scope_complete, end_marker_verified, content_sha256
            FROM order_observation_batches
            WHERE automation_run_id = ?
            """,
            (child_run_id,),
        ).fetchone()
        item_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM order_observation_items
            WHERE observation_batch_id = ?
            """,
            (f"ORDER-BATCH-{child_run_id}",),
        ).fetchone()[0]
    if batch is None:
        raise RuntimeError("order observation batch was not imported")

    archive_dir = queue_dir / "archive" / attempt_id
    queue_counts = {
        name: len(list((queue_dir / name).glob("*")))
        for name in ("inbox", "working", "results")
    }
    return {
        "schema_version": "task13.5-4-order-readonly-acceptance-1.0",
        "execution_mode": "READ_ONLY",
        "platform_name": MAYI_HUATUAN_PLATFORM,
        "platform_trade_date": (
            target_trade_date or time_context.platform_trade_date
        ).isoformat(),
        "trade_day_status": str(batch["trade_day_status"]),
        "capability_result": str(batch["capability_result"]),
        "batch_status": str(batch["batch_status"]),
        "scope_complete": bool(batch["scope_complete"]),
        "end_marker_verified": bool(batch["end_marker_verified"]),
        "item_count": int(item_count),
        "content_sha256": str(batch["content_sha256"]),
        "automation_run_status": child_outcome.status.value,
        "execution_attempt_id": attempt_id,
        "result_imported": True,
        "result_archived": archive_dir.is_dir(),
        "queue_counts": queue_counts,
        "platform_write_operations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Task 13.5-4 controlled order READ_ONLY acceptance"
    )
    parser.add_argument("--runtime-db", required=True, type=Path)
    parser.add_argument("--queue-dir", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=330.0)
    parser.add_argument(
        "--target-trade-date",
        type=date.fromisoformat,
        default=None,
        help="Optional current or historical platform trade date (YYYY-MM-DD)",
    )
    args = parser.parse_args()
    result = run_acceptance(
        runtime_db=args.runtime_db,
        queue_dir=args.queue_dir,
        timeout_seconds=args.timeout_seconds,
        target_trade_date=args.target_trade_date,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if (
        result["batch_status"] == "ACCEPTED"
        and result["scope_complete"]
        and result["end_marker_verified"]
        and result["result_archived"]
        and result["queue_counts"] == {
            "inbox": 0,
            "working": 0,
            "results": 0,
        }
    ) else 1


if __name__ == "__main__":
    _configure_console()
    raise SystemExit(main())
