from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.automation_repository import AutomationRepository  # noqa: E402
from app.repositories.sqlite_connection import (  # noqa: E402
    SQLiteConnectionConfig,
)
from app.repositories.sqlite_runtime_repository import (  # noqa: E402
    SQLiteRuntimeRepository,
)
from app.services.automation import (  # noqa: E402
    AutomationHeartbeatStore,
    AutomationService,
    ensure_default_automation_jobs,
    safe_automation_error_message,
    ONLINE_PULSE,
)
from app.services.incident_automation import (  # noqa: E402
    build_incident_notification_handlers,
    ensure_incident_notification_automation_job,
)
from app.services.maintenance_automation import (  # noqa: E402
    build_maintenance_handlers,
    ensure_release_backup_automation_job,
)
from app.services.listing_automation_runtime import (  # noqa: E402
    build_listing_read_only_handlers,
)
from app.services.operational_time import (  # noqa: E402
    OperationalTimeService,
)
from app.services.operations_automation import (  # noqa: E402
    build_operations_control_handlers,
    validate_rule_workbooks,
)
from app.services.order_automation_runtime import (  # noqa: E402
    build_order_read_only_handlers,
)
from app.services.runtime import DEFAULT_RUNTIME_DB  # noqa: E402
from app.services.settlement_automation import (  # noqa: E402
    build_sales_settlement_handlers,
)
from app.services.shadowbot_worker_recovery import (  # noqa: E402
    build_worker_recovery_coordinator_from_environment,
)
from scripts.release_backup import create_backup, verify_backup  # noqa: E402

DEFAULT_HEARTBEAT_PATH = Path("data/runtime/automation_service/heartbeat.json")
DEFAULT_SHADOWBOT_QUEUE_DIR = Path(
    os.environ.get(
        "SHADOWBOT_QUEUE_DIR",
        "data/runtime/shadowbot_queue",
    )
)
DEFAULT_PRICE_RULES = PROJECT_ROOT / "data" / "samples" / "price_rules.xlsx"
DEFAULT_LISTING_RULES = PROJECT_ROOT / "data" / "samples" / "listing_rules.xlsx"
DEFAULT_IDENTITY_MAPPING = (
    PROJECT_ROOT / "shadowbot" / "test2" / "product_identity_mapping.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the independent PRA Automation scheduler and leased "
            "application-service dispatcher."
        )
    )
    parser.add_argument(
        "--runtime-db",
        type=Path,
        default=DEFAULT_RUNTIME_DB,
    )
    parser.add_argument(
        "--platform-name",
        default="蚂蚁花团供应商",
    )
    parser.add_argument(
        "--heartbeat",
        type=Path,
        default=DEFAULT_HEARTBEAT_PATH,
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--max-runs-per-cycle", type=int, default=8)
    parser.add_argument("--max-windows-per-job", type=int, default=16)
    parser.add_argument(
        "--read-only-scans-only",
        action="store_true",
        help=(
            "Run the formal scheduler with only selected READ_ONLY scan "
            "handlers and materialize no unrelated business runs."
        ),
    )
    parser.add_argument(
        "--enable-order-read-only",
        action="store_true",
        help=(
            "Register only FULL_MARKET_SCAN order dispatch and ORDER_SCAN "
            "READ_ONLY handlers; no platform-write handler is registered."
        ),
    )
    parser.add_argument(
        "--enable-listing-read-only",
        action="store_true",
        help=(
            "Register FULL_MARKET_SCAN and LISTING_STATUS_SCAN READ_ONLY "
            "handlers by reusing the Task 13 SYNC_STATUS chain."
        ),
    )
    parser.add_argument(
        "--online-pulse-only",
        action="store_true",
        help=(
            "受控验收时只注册 ONLINE_PULSE 上架中扫描；必须同时启用"
            "只读扫描模式和商品只读扫描。"
        ),
    )
    parser.add_argument(
        "--enable-incident-monitoring",
        action="store_true",
        help=(
            "Register the read-only Incident notification/reminder maintenance "
            "handler; it never registers a platform-write handler."
        ),
    )
    parser.add_argument(
        "--enable-worker-recovery",
        action="store_true",
        help=(
            "Attach the fail-closed ShadowBot host recovery coordinator to "
            "Incident monitoring. Real host actions additionally require "
            "PRA_ENABLE_SHADOWBOT_HOST_RECOVERY=true and a reviewed helper."
        ),
    )
    parser.add_argument(
        "--shadowbot-queue-dir",
        type=Path,
        default=DEFAULT_SHADOWBOT_QUEUE_DIR,
    )
    parser.add_argument(
        "--identity-mapping",
        type=Path,
        default=Path(
            os.environ.get(
                "PRA_SHADOWBOT_IDENTITY_MAPPING",
                DEFAULT_IDENTITY_MAPPING,
            )
        ),
    )
    parser.add_argument(
        "--applet-uri",
        default=os.environ.get("SHADOWBOT_APPLET_URI", ""),
    )
    parser.add_argument(
        "--enable-release-backup",
        action="store_true",
        help=(
            "Register the fixed manual-only release backup handler. "
            "It never accepts a script, path, or command from a Web request."
        ),
    )
    parser.add_argument("--release-wheel", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--price-rules", type=Path, default=DEFAULT_PRICE_RULES)
    parser.add_argument("--listing-rules", type=Path, default=DEFAULT_LISTING_RULES)
    parser.add_argument(
        "--order-timeout-seconds",
        type=float,
        default=330.0,
    )
    parser.add_argument("--once", action="store_true")
    return parser


class ProcessFileLock:
    """Cross-platform non-blocking single-process lock for one service."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._stream = None

    def __enter__(self) -> "ProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._stream.seek(0)
                if self._stream.read(1) == b"":
                    self._stream.write(b"0")
                    self._stream.flush()
                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    self._stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError:
            self._stream.close()
            self._stream = None
            raise RuntimeError("Automation Service 已有实例持有单实例锁。") from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None


def automation_service_lock_path(runtime_db: Path) -> Path:
    """Derive one lock identity from the normalized Runtime database path."""

    resolved = Path(runtime_db).resolve()
    normalized = os.path.normcase(str(resolved))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return resolved.parent / f".automation-service-{digest}.lock"


def _validate_formal_rule_configuration(args: argparse.Namespace) -> dict[str, str]:
    price_path = Path(args.price_rules).resolve(strict=False)
    listing_path = Path(args.listing_rules).resolve(strict=False)
    environment = os.environ.get("PRA_ENV", "development").strip().lower()
    samples_root = (PROJECT_ROOT / "data" / "samples").resolve(strict=False)
    if environment == "production" and any(
        path == samples_root or samples_root in path.parents
        for path in (price_path, listing_path)
    ):
        raise ValueError(
            "生产环境必须显式配置正式价格规则和上下架规则，不能使用 data/samples。"
        )
    return validate_rule_workbooks(
        price_rules_path=price_path,
        listing_rules_path=listing_path,
    )


def _service_mode(args: argparse.Namespace) -> str:
    listing = bool(args.enable_listing_read_only)
    order = bool(args.enable_order_read_only)
    incident = bool(args.enable_incident_monitoring)
    if args.online_pulse_only:
        return "ONLINE_PULSE_ONLY"
    if args.read_only_scans_only:
        if listing and order:
            return "LISTING_ORDER_READ_ONLY_SCANS_ONLY"
        if listing:
            return "LISTING_READ_ONLY_SCANS_ONLY"
        return "ORDER_READ_ONLY_SCANS_ONLY"
    if listing and order:
        return (
            "LISTING_ORDER_READ_ONLY_INCIDENT_AND_SETTLEMENT"
            if incident
            else "LISTING_ORDER_READ_ONLY_AND_SETTLEMENT"
        )
    if listing:
        return (
            "LISTING_READ_ONLY_INCIDENT_AND_SETTLEMENT"
            if incident
            else "LISTING_READ_ONLY_AND_SETTLEMENT"
        )
    if order:
        return (
            "ORDER_READ_ONLY_INCIDENT_AND_SETTLEMENT"
            if incident
            else "ORDER_READ_ONLY_AND_SETTLEMENT"
        )
    return "INCIDENT_AND_SETTLEMENT" if incident else "SETTLEMENT_ONLY"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = build_parser().parse_args()
    if args.poll_seconds < 0.2:
        raise ValueError("--poll-seconds 必须不小于 0.2。")
    if args.lease_seconds < 1:
        raise ValueError("--lease-seconds 必须为正整数。")
    if args.order_timeout_seconds <= 0:
        raise ValueError("--order-timeout-seconds 必须为正数。")
    if args.enable_worker_recovery and not args.enable_incident_monitoring:
        raise ValueError(
            "--enable-worker-recovery 要求同时启用 --enable-incident-monitoring。"
        )
    if args.read_only_scans_only and not (
        args.enable_listing_read_only or args.enable_order_read_only
    ):
        raise ValueError(
            "--read-only-scans-only 至少需要启用一种只读扫描。"
        )
    if args.read_only_scans_only and (
        args.enable_incident_monitoring
        or args.enable_worker_recovery
        or args.enable_release_backup
    ):
        raise ValueError(
            "只读扫描模式不能同时注册异常维护、Worker 恢复或发布备份。"
        )
    if args.online_pulse_only and not (
        args.read_only_scans_only
        and args.enable_listing_read_only
        and not args.enable_order_read_only
    ):
        raise ValueError(
            "--online-pulse-only 要求仅启用商品只读扫描模式。"
        )
    if args.enable_release_backup and (
        args.release_wheel is None or args.backup_dir is None
    ):
        raise ValueError(
            "--enable-release-backup 要求同时提供 --release-wheel 和 --backup-dir。"
        )

    heartbeat = AutomationHeartbeatStore(args.heartbeat)
    lock_path = automation_service_lock_path(args.runtime_db)
    service_instance_id = f"automation-service-{uuid4().hex}"
    lock_acquired = False
    try:
        with ProcessFileLock(lock_path):
            lock_acquired = True
            runtime_repository = SQLiteRuntimeRepository(
                args.runtime_db,
                connection_config=SQLiteConnectionConfig.from_environment(
                    purpose="background"
                ),
            )
            runtime_repository.require_current_schema(
                operation_name="Automation Service"
            )
            repository = AutomationRepository(runtime_repository)
            ensure_default_automation_jobs(
                repository,
                platform_name=args.platform_name,
                now=datetime.now(timezone.utc),
            )
            if args.enable_incident_monitoring:
                ensure_incident_notification_automation_job(
                    repository,
                    platform_name=args.platform_name,
                    now=datetime.now(timezone.utc),
                )
            if args.enable_release_backup:
                ensure_release_backup_automation_job(
                    repository,
                    platform_name=args.platform_name,
                    now=datetime.now(timezone.utc),
                )
            operational_time = OperationalTimeService(
                policies=repository.load_operational_time_policies()
            )
            if args.read_only_scans_only:
                handlers = {}
            else:
                _validate_formal_rule_configuration(args)
                handlers = dict(
                    build_sales_settlement_handlers(
                        runtime_repository=runtime_repository,
                        platform_name=args.platform_name,
                    )
                )
                handlers.update(
                    build_operations_control_handlers(
                        runtime_repository=runtime_repository,
                        price_rules_path=args.price_rules,
                        listing_rules_path=args.listing_rules,
                    )
                )
            if args.enable_order_read_only:
                handlers.update(
                    build_order_read_only_handlers(
                        runtime_repository=runtime_repository,
                        queue_dir=args.shadowbot_queue_dir,
                        operational_time=operational_time,
                        timeout_seconds=args.order_timeout_seconds,
                    )
                )
            if args.enable_listing_read_only:
                mapping_path = args.identity_mapping.resolve(strict=True)
                if mapping_path.suffix.lower() != ".json":
                    raise ValueError(
                        "商品状态定时扫描必须使用版本化 JSON 身份映射。"
                    )
                listing_handlers = dict(
                    build_listing_read_only_handlers(
                        runtime_repository=runtime_repository,
                        queue_dir=args.shadowbot_queue_dir,
                        mapping_path=mapping_path,
                        full_market_parent_handler=handlers.get(
                            "FULL_MARKET_SCAN"
                        ),
                        applet_uri=args.applet_uri,
                        timeout_seconds=args.order_timeout_seconds,
                    )
                )
                if args.online_pulse_only:
                    listing_handlers = {
                        ONLINE_PULSE: listing_handlers[ONLINE_PULSE]
                    }
                handlers.update(listing_handlers)
            if args.enable_incident_monitoring:
                worker_recovery = (
                    build_worker_recovery_coordinator_from_environment(
                        runtime_repository,
                        queue_dir=args.shadowbot_queue_dir,
                    )
                    if args.enable_worker_recovery
                    else None
                )
                handlers.update(
                    build_incident_notification_handlers(
                        runtime_repository=runtime_repository,
                        worker_recovery=worker_recovery,
                    )
                )
            if args.enable_release_backup:
                release_wheel = args.release_wheel.resolve(strict=True)
                backup_dir = args.backup_dir.resolve(strict=False)

                def execute_release_backup(backup_id: str) -> dict[str, object]:
                    target = backup_dir / backup_id
                    created = False
                    if target.is_dir():
                        manifest = verify_backup(target)
                    else:
                        created_path = create_backup(
                            runtime_db=args.runtime_db,
                            backup_dir=backup_dir,
                            wheel_path=release_wheel,
                            input_specs=(
                                ("price_rules.xlsx", args.price_rules),
                                ("listing_rules.xlsx", args.listing_rules),
                            ),
                            git_root=PROJECT_ROOT,
                            backup_id=backup_id,
                        )
                        manifest = verify_backup(created_path)
                        created = True
                    return {
                        "backup_id": str(manifest.get("backup_id") or ""),
                        "verified": True,
                        "created": created,
                    }

                handlers.update(
                    build_maintenance_handlers(
                        release_backup_executor=execute_release_backup,
                    )
                )
            service = AutomationService(
                repository,
                handlers=handlers,
                owner_token=service_instance_id,
                lease_seconds=args.lease_seconds,
                max_runs_per_cycle=args.max_runs_per_cycle,
                max_windows_per_job=args.max_windows_per_job,
                materialize_only_registered_handlers=(
                    args.read_only_scans_only
                ),
            )
            while True:
                cycle_started_at = datetime.now(timezone.utc)
                cycle = service.run_cycle()
                health = repository.health_snapshot(now=datetime.now(timezone.utc))
                payload = {
                    "schema_version": "automation-heartbeat-1.0",
                    "status": "RUNNING",
                    "mode": _service_mode(args),
                    "registered_job_types": sorted(handlers),
                    "platform_write_handlers_registered": False,
                    "worker_recovery_handler_registered": bool(
                        args.enable_worker_recovery
                    ),
                    "release_backup_handler_registered": bool(
                        args.enable_release_backup
                    ),
                    "shadowbot_host_recovery_enabled": (
                        os.environ.get(
                            "PRA_ENABLE_SHADOWBOT_HOST_RECOVERY",
                            "",
                        )
                        .strip()
                        .lower()
                        in {"1", "true", "yes"}
                    ),
                    "service_instance_id": service_instance_id,
                    "cycle_started_at": cycle_started_at.isoformat(),
                    "last_cycle_at": datetime.now(timezone.utc).isoformat(),
                    "scheduled_run_count": len(cycle.scheduled.created_run_ids),
                    "missed_run_count": len(cycle.scheduled.missed_run_ids),
                    "merged_run_count": len(cycle.scheduled.merged_run_ids),
                    "truncated_window_count": (cycle.scheduled.truncated_window_count),
                    "claimed_run_count": len(cycle.claimed_run_ids),
                    "completed_run_count": len(cycle.completed_run_ids),
                    "blocked_reason": cycle.blocked_reason,
                    "errors": list(cycle.errors),
                    "runtime_health": health,
                }
                heartbeat.write(payload)
                print(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if args.once:
                    heartbeat.write(
                        {
                            **payload,
                            "status": "STOPPED",
                            "stopped_at": datetime.now(timezone.utc).isoformat(),
                            "reason": "ONCE_COMPLETED",
                        }
                    )
                    return 0
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        if lock_acquired:
            heartbeat.write(
                {
                    "schema_version": "automation-heartbeat-1.0",
                    "status": "STOPPED",
                    "service_instance_id": service_instance_id,
                    "stopped_at": datetime.now(timezone.utc).isoformat(),
                    "reason": "KEYBOARD_INTERRUPT",
                }
            )
        return 0
    except Exception as exc:
        safe_message = safe_automation_error_message(exc)
        failure_payload = {
            "schema_version": "automation-heartbeat-1.0",
            "status": "FAILED",
            "service_instance_id": service_instance_id,
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "reason": "AUTOMATION_SERVICE_FAILED",
            "error_code": "AUTOMATION_SERVICE_FAILED",
            "error_type": type(exc).__name__,
            "error_message": safe_message,
        }
        if lock_acquired:
            try:
                heartbeat.write(failure_payload)
            except Exception:
                pass
        print(
            json.dumps(failure_payload, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
