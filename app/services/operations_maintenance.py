from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from app.enums import AutomationRunStatus, IncidentCategory
from app.operations_web.auth import AuthorizationBackend, Capability, Principal
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.incident_automation import (
    ensure_incident_notification_automation_job,
)
from app.services.incident_management import IncidentDetection, IncidentManagementService
from app.services.maintenance_automation import (
    ensure_release_backup_automation_job,
)
from app.services.notification_outbox import NotificationOutboxService
from app.services.operational_time import OperationalTimeService
from app.services.shadowbot_worker_health import build_shadowbot_worker_health_report


@dataclass(frozen=True, slots=True)
class MaintenanceReceipt:
    intent_type: str
    status: str
    reference_id: str
    message: str
    replayed: bool = False


class OperationsMaintenanceError(RuntimeError):
    """A typed maintenance request was rejected before any external action."""


class OperationsMaintenanceApplicationService:
    """Submit fixed maintenance intents without running host tools in a Web request."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        authorization: AuthorizationBackend,
        *,
        queue_root: Path,
        platform_name: str,
        notification_channel: str,
        automation_heartbeat: Path | None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime = runtime_repository
        self.automation = AutomationRepository(runtime_repository)
        self.authorization = authorization
        self.queue_root = queue_root
        self.platform_name = platform_name.strip()
        self.notification_channel = notification_channel.strip().lower()
        self.automation_heartbeat = (
            Path(automation_heartbeat) if automation_heartbeat is not None else None
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        if not self.platform_name:
            raise ValueError("platform_name must not be blank")
        if not self.notification_channel:
            raise ValueError("notification_channel must not be blank")

    def request_worker_recovery(
        self,
        principal: Principal,
        *,
        idempotency_key: str,
    ) -> MaintenanceReceipt:
        self._authorize(principal)
        _, digest = self._idempotency(idempotency_key)
        now = self._now()
        try:
            report = build_shadowbot_worker_health_report(
                self.queue_root,
                expected_status="RUNNING",
                max_age_seconds=30,
                now=now,
            )
        except Exception as exc:
            report = {
                "ok": False,
                "error_code": "WORKER_HEARTBEAT_UNREADABLE",
                "error_type": type(exc).__name__,
            }
        if report.get("ok") is True:
            return MaintenanceReceipt(
                intent_type="WORKER_RECOVERY",
                status="NO_ACTION",
                reference_id="",
                message="影刀执行端当前运行正常，无需恢复。",
            )

        self._require_automation_capability(
            "worker_recovery_handler_registered",
            "影刀执行端自动恢复暂不可用，请联系管理员。",
        )

        context = self._time_context(now)
        job = ensure_incident_notification_automation_job(
            self.automation,
            platform_name=self.platform_name,
            now=now,
        )
        if not job.enabled:
            raise OperationsMaintenanceError("影刀执行端自动恢复已停用，请联系管理员。")
        detection = IncidentManagementService(self.runtime).detect(
            IncidentDetection(
                event_key=f"web-worker-recovery:{digest}",
                category=IncidentCategory.WORKER_UNAVAILABLE,
                source_type="WEB_MAINTENANCE",
                source_ref_id=f"maintenance:{digest}",
                severity="S3",
                blocks_finalization=True,
                platform_name=self.platform_name,
                platform_trade_date=context.platform_trade_date,
                seller_operation_date=context.seller_operation_date,
                subject_type="worker",
                subject_key="test2",
                title="影刀执行端不可用",
                description="系统检查发现影刀执行端当前不可用。",
                occurred_at=now,
                reason=str(report.get("error_code") or "heartbeat_not_running"),
                payload={
                    "heartbeat_status": str(report.get("status") or "UNKNOWN"),
                    "heartbeat_age_seconds": report.get("age_seconds"),
                },
            )
        )
        run, created = self.automation.ensure_run(
            job=job,
            scheduled_for=now,
            time_context=context,
            initial_status=AutomationRunStatus.SCHEDULED,
            now=now,
            logical_run_key=f"web-maintenance:worker-recovery:{digest}",
            event_type="MAINTENANCE_INTENT_SCHEDULED",
            event_payload={
                "actor": principal.subject,
                "intent_type": "WORKER_RECOVERY",
                "incident_id": detection.incident.incident_id,
                "request_fingerprint": digest,
                "platform_write_performed": False,
            },
        )
        return MaintenanceReceipt(
            intent_type="WORKER_RECOVERY",
            status="SCHEDULED",
            reference_id=run.run_id,
            message="已安排检查并恢复影刀执行端。",
            replayed=not created,
        )

    def request_notification_test(
        self,
        principal: Principal,
        *,
        idempotency_key: str,
    ) -> MaintenanceReceipt:
        self._authorize(principal)
        _, digest = self._idempotency(idempotency_key)
        self._require_notification_worker()
        outbox = NotificationOutboxService(self.runtime)
        notification_key = outbox.notification_key(
            "system_test",
            f"maintenance:{digest}",
            "v1",
            self.notification_channel,
            "operations",
        )
        replayed = (
            self.runtime.get_notification_outbox_by_key(notification_key) is not None
        )
        notification = outbox.enqueue(
            notification_type="system_test",
            notification_key=notification_key,
            recipient_type="role",
            recipient_ref="operations",
            channel=self.notification_channel,
            payload={
                "message": "通知测试：如果收到本消息，说明飞书通知可以正常送达。",
                "reason": f"由系统管理员 {principal.subject} 发起，不会创建业务任务。",
                "platform_name": self.platform_name,
            },
            priority=10,
        )
        return MaintenanceReceipt(
            intent_type="NOTIFICATION_TEST",
            status="QUEUED",
            reference_id=notification.notification_id,
            message="通知测试已提交，正在等待发送。",
            replayed=replayed,
        )

    def request_backup(
        self,
        principal: Principal,
        *,
        idempotency_key: str,
    ) -> MaintenanceReceipt:
        self._authorize(principal)
        _, digest = self._idempotency(idempotency_key)
        now = self._now()
        self._require_automation_capability(
            "release_backup_handler_registered",
            "数据备份暂不可用，请联系管理员。",
            now=now,
        )
        context = self._time_context(now)
        job = ensure_release_backup_automation_job(
            self.automation,
            platform_name=self.platform_name,
            now=now,
        )
        if not job.enabled:
            raise OperationsMaintenanceError("数据备份已停用，请联系管理员。")
        run, created = self.automation.ensure_run(
            job=job,
            scheduled_for=now,
            time_context=context,
            initial_status=AutomationRunStatus.SCHEDULED,
            now=now,
            logical_run_key=f"web-maintenance:release-backup:{digest}",
            event_type="MAINTENANCE_INTENT_SCHEDULED",
            event_payload={
                "actor": principal.subject,
                "intent_type": "RELEASE_BACKUP",
                "request_fingerprint": digest,
                "platform_write_performed": False,
            },
        )
        return MaintenanceReceipt(
            intent_type="RELEASE_BACKUP",
            status="SCHEDULED",
            reference_id=run.run_id,
            message="数据备份已安排。",
            replayed=not created,
        )

    def _authorize(self, principal: Principal) -> None:
        if not self.authorization.allows(principal, Capability.SYSTEM_ADMIN):
            raise OperationsMaintenanceError("当前账号没有系统维护权限。")

    def _time_context(self, now: datetime):
        try:
            return OperationalTimeService(
                policies=self.automation.load_operational_time_policies()
            ).classify(now)
        except Exception as exc:
            raise OperationsMaintenanceError(
                "运营时间策略不可用，未创建维护请求。"
            ) from exc

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise OperationsMaintenanceError("系统维护时钟必须包含时区。")
        return value.astimezone(UTC)

    def _require_automation_capability(
        self,
        capability: str,
        message: str,
        *,
        now: datetime | None = None,
    ) -> None:
        heartbeat = self.automation_heartbeat
        payload = self._fresh_heartbeat(
            heartbeat,
            schema_version="automation-heartbeat-1.0",
            service_name="自动任务服务",
            now=now,
        )
        if payload.get(capability) is not True:
            raise OperationsMaintenanceError(message)

    def _require_notification_worker(self) -> None:
        payload = self._fresh_heartbeat(
            self.queue_root / "control" / "pra_queue_services_heartbeat.json",
            schema_version="queue-services-heartbeat-1.0",
            service_name="通知服务",
        )
        if payload.get("service") != "shadowbot_queue_services":
            raise OperationsMaintenanceError("通知服务配置异常，未发送测试通知。")
        if payload.get("notification_worker_enabled") is not True:
            raise OperationsMaintenanceError("通知发送未启用，未发送测试通知。")
        if str(payload.get("notification_channel") or "").strip().lower() != (
            self.notification_channel
        ):
            raise OperationsMaintenanceError("通知通道配置不一致，未创建测试通知。")

    def _fresh_heartbeat(
        self,
        path: Path | None,
        *,
        schema_version: str,
        service_name: str,
        now: datetime | None = None,
    ) -> dict[str, object]:
        if path is None or not path.is_file():
            raise OperationsMaintenanceError(
                f"{service_name} 没有可用心跳，未创建维护请求。"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("heartbeat must be an object")
            if payload.get("schema_version") != schema_version:
                raise ValueError("heartbeat schema mismatch")
            status = str(payload.get("status") or "UNKNOWN").upper()
            updated_raw = payload.get("last_cycle_at") or payload.get("updated_at")
            updated_at = datetime.fromisoformat(
                str(updated_raw or "").replace("Z", "+00:00")
            )
            if updated_at.tzinfo is None or updated_at.utcoffset() is None:
                raise ValueError("heartbeat timestamp must include timezone")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise OperationsMaintenanceError(
                f"{service_name} 心跳无法验证，未创建维护请求。"
            ) from exc
        current = now or self._now()
        age = current - updated_at.astimezone(UTC)
        if (
            status != "RUNNING"
            or age < timedelta(seconds=-5)
            or age > timedelta(seconds=30)
        ):
            raise OperationsMaintenanceError(
                f"{service_name} 当前未运行，未创建维护请求。"
            )
        return payload

    @staticmethod
    def _idempotency(value: str) -> tuple[str, str]:
        key = value.strip()
        if not 8 <= len(key) <= 200:
            raise OperationsMaintenanceError("本次维护请求已失效，请刷新页面后重试。")
        return key, hashlib.sha256(key.encode("utf-8")).hexdigest()
