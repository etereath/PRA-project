from __future__ import annotations

import argparse
import json
import msvcrt
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.exceptions import NotificationDeliveryError, ValidationError  # noqa: E402
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository  # noqa: E402
from app.services.automation import AutomationHeartbeatStore  # noqa: E402
from app.services.notification_outbox import (  # noqa: E402
    NotificationOutboxWorker,
    is_test_notification_channel,
)
from app.services.runtime import DEFAULT_RUNTIME_DB, ReviewTaskService  # noqa: E402
from app.services.shadowbot_executor import (  # noqa: E402
    build_shadowbot_task_runner_from_environment,
)
from app.services.shadowbot_product_read import (  # noqa: E402
    DEFAULT_INVENTORY_PRODUCTS_PATH,
)
from app.services.shadowbot_queue import (  # noqa: E402
    ShadowBotLoginVerificationMonitor,
    ShadowBotQueuePaths,
    ShadowBotQueueWatchdog,
    ShadowBotResultImporter,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ShadowBot result importer and queue watchdog")
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    parser.add_argument("--products", type=Path, default=DEFAULT_INVENTORY_PRODUCTS_PATH)
    parser.add_argument("--queue-dir", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--stale-seconds", type=int, default=30)
    parser.add_argument("--heartbeat", type=Path, default=None)
    parser.add_argument("--once", action="store_true")
    return parser


def run_cycle(
    importer: ShadowBotResultImporter,
    watchdog: ShadowBotQueueWatchdog,
    login_monitor: ShadowBotLoginVerificationMonitor | None = None,
    notification_worker: NotificationOutboxWorker | None = None,
    review_service: ReviewTaskService | None = None,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    if login_monitor is not None:
        try:
            events.extend(login_monitor.inspect())
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            events.append(
                {
                    "status": "RETRY_PENDING",
                    "error_code": "LOGIN_VERIFICATION_MONITOR_FAILED",
                    "error_message": str(exc),
                }
            )
    events.extend(importer.import_available())
    try:
        events.extend(watchdog.inspect())
    except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
        events.append(
            {
                "status": "RETRY_PENDING",
                "error_code": "WATCHDOG_INSPECTION_FAILED",
                "error_message": str(exc),
            }
        )
    if review_service is not None:
        try:
            reminder_summary = review_service.renew_overdue_manual_reviews()
            if reminder_summary.renewed_review_tasks:
                events.append(
                    {
                        "status": "REVIEW_REMINDERS_RENEWED",
                        "scanned_review_tasks": (
                            reminder_summary.scanned_review_tasks
                        ),
                        "renewed_review_tasks": (
                            reminder_summary.renewed_review_tasks
                        ),
                        "notification_logs_created": (
                            reminder_summary.notification_logs_created
                        ),
                    }
                )
            for error in reminder_summary.errors or []:
                events.append(
                    {
                        "status": "RETRY_PENDING",
                        "error_code": "REVIEW_REMINDER_FAILED",
                        "error_message": error,
                    }
                )
        except (OSError, ValueError) as exc:
            events.append(
                {
                    "status": "RETRY_PENDING",
                    "error_code": "REVIEW_REMINDER_SCAN_FAILED",
                    "error_message": str(exc),
                }
            )
    if notification_worker is not None:
        try:
            recovered = notification_worker.run_watchdog()
            if recovered:
                events.append(
                    {
                        "status": "NOTIFICATION_LEASES_RECOVERED",
                        "recovered_count": len(recovered),
                    }
                )
            for _ in range(10):
                delivered = notification_worker.run_once()
                if delivered is None:
                    break
                events.append(
                    {
                        "status": "NOTIFICATION_DELIVERED",
                        "notification_id": delivered.notification_id,
                        "delivery_status": delivered.status,
                        "channel": delivered.channel,
                    }
                )
        except (NotificationDeliveryError, OSError, ValueError) as exc:
            events.append(
                {
                    "status": "RETRY_PENDING",
                    "error_code": "NOTIFICATION_WORKER_FAILED",
                    "error_message": str(exc),
                }
            )
    return events


def main() -> int:
    args = build_parser().parse_args()
    queue_dir = args.queue_dir or Path(
        os.environ.get("SHADOWBOT_QUEUE_DIR")
        or os.environ.get("SHADOWBOT_REQUEST_DIR")
        or "data/runtime/shadowbot_queue"
    )
    os.environ["SHADOWBOT_QUEUE_DIR"] = str(queue_dir)
    # --queue-dir is an explicit process boundary.  Keep the legacy alias in
    # lockstep so Executor-created RECONCILE requests cannot land elsewhere.
    os.environ["SHADOWBOT_REQUEST_DIR"] = str(queue_dir)
    paths = ShadowBotQueuePaths(queue_dir)
    paths.ensure()
    lock_path = paths.control / "pra_queue_services.lock"
    heartbeat = AutomationHeartbeatStore(
        args.heartbeat or paths.control / "pra_queue_services_heartbeat.json"
    )
    repository = SQLiteRuntimeRepository(args.runtime_db)
    repository.init_schema()
    review_service = ReviewTaskService(repository)
    notification_channel = os.environ.get(
        "DEFAULT_NOTIFICATION_CHANNEL", ""
    ).strip().lower()
    notification_worker = None
    if notification_channel:
        allow_test_channels = (
            os.environ.get("DEV_MODE", "false").strip().lower() == "true"
        )
        if (
            is_test_notification_channel(notification_channel)
            and not allow_test_channels
        ):
            print(
                json.dumps(
                    {
                        "status": "CONFIGURATION_ERROR",
                        "error_code": "TEST_NOTIFICATION_CHANNEL_DISABLED",
                        "channel": notification_channel,
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        notification_worker = NotificationOutboxWorker.for_channel(
            repository,
            notification_channel,
            allow_test_channels=allow_test_channels,
        )
    runner = build_shadowbot_task_runner_from_environment()
    importer = ShadowBotResultImporter(
        repository,
        runner,
        queue_dir,
        inventory_products_path=args.products,
    )
    login_monitor = ShadowBotLoginVerificationMonitor(repository, runner, queue_dir)
    watchdog = ShadowBotQueueWatchdog(
        queue_dir,
        stale_seconds=args.stale_seconds,
        repository=repository,
    )
    with lock_path.open("a+b") as lock_file:
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print(json.dumps({"status": "ALREADY_RUNNING", "lock_path": str(lock_path)}, ensure_ascii=False))
            return 2
        started_at = datetime.now(UTC)
        cycle_count = 0
        try:
            while True:
                heartbeat.write(
                    _service_heartbeat_payload(
                        status="RUNNING",
                        started_at=started_at,
                        cycle_count=cycle_count,
                        notification_worker_enabled=notification_worker is not None,
                        notification_channel=notification_channel,
                    )
                )
                events = run_cycle(
                    importer,
                    watchdog,
                    login_monitor,
                    notification_worker,
                    review_service,
                )
                cycle_count += 1
                for event in events:
                    print(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str), flush=True)
                heartbeat.write(
                    _service_heartbeat_payload(
                        status="RUNNING",
                        started_at=started_at,
                        cycle_count=cycle_count,
                        last_event_count=len(events),
                        notification_worker_enabled=notification_worker is not None,
                        notification_channel=notification_channel,
                    )
                )
                if args.once:
                    heartbeat.write(
                        _service_heartbeat_payload(
                            status="STOPPED",
                            started_at=started_at,
                            cycle_count=cycle_count,
                            last_event_count=len(events),
                            reason="once_complete",
                            notification_worker_enabled=notification_worker is not None,
                            notification_channel=notification_channel,
                        )
                    )
                    return 0
                time.sleep(max(args.poll_seconds, 0.2))
        except KeyboardInterrupt:
            heartbeat.write(
                _service_heartbeat_payload(
                    status="STOPPED",
                    started_at=started_at,
                    cycle_count=cycle_count,
                    reason="operator_interrupt",
                    notification_worker_enabled=notification_worker is not None,
                    notification_channel=notification_channel,
                )
            )
            return 130
        except Exception as exc:
            heartbeat.write(
                _service_heartbeat_payload(
                    status="FAILED",
                    started_at=started_at,
                    cycle_count=cycle_count,
                    reason=type(exc).__name__,
                    notification_worker_enabled=notification_worker is not None,
                    notification_channel=notification_channel,
                )
            )
            raise


def _service_heartbeat_payload(
    *,
    status: str,
    started_at: datetime,
    cycle_count: int,
    last_event_count: int = 0,
    reason: str = "",
    notification_worker_enabled: bool = False,
    notification_channel: str = "",
) -> dict[str, object]:
    return {
        "schema_version": "queue-services-heartbeat-1.0",
        "service": "shadowbot_queue_services",
        "status": status,
        "started_at": started_at.isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "cycle_count": cycle_count,
        "last_event_count": last_event_count,
        "notification_worker_enabled": notification_worker_enabled,
        "notification_channel": notification_channel,
        "components": [
            "result_importer",
            "queue_watchdog",
            "login_verification_monitor",
            "review_reminder",
            "notification_outbox",
        ],
        "reason": reason,
    }


if __name__ == "__main__":
    raise SystemExit(main())
