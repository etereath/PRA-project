from __future__ import annotations

import argparse
import json
import msvcrt
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.exceptions import NotificationDeliveryError, ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.notification_outbox import (
    NotificationOutboxWorker,
    is_test_notification_channel,
)
from app.services.runtime import DEFAULT_RUNTIME_DB, ReviewTaskService
from app.services.shadowbot_executor import build_shadowbot_task_runner_from_environment
from app.services.shadowbot_product_read import DEFAULT_INVENTORY_PRODUCTS_PATH
from app.services.shadowbot_queue import (
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
        while True:
            for event in run_cycle(
                importer,
                watchdog,
                login_monitor,
                notification_worker,
                review_service,
            ):
                print(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str), flush=True)
            if args.once:
                return 0
            time.sleep(max(args.poll_seconds, 0.2))


if __name__ == "__main__":
    raise SystemExit(main())
