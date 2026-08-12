from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app.enums import IncidentCategory, IncidentStatus
from app.inventory_models import InventoryAlertResult, InventoryTransaction
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.operational_incident_repository import OperationalIncidentRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.incident_management import IncidentDetection, IncidentManagementService
from app.services.notification_outbox import NotificationOutboxService


class InventoryAlertService:
    """Project low real inventory into the existing Incident and Outbox chain."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.inventory = InventoryRepository(runtime_repository)
        self.incident_service = IncidentManagementService(runtime_repository)
        self.incidents = OperationalIncidentRepository(runtime_repository)
        self.outbox = NotificationOutboxService(runtime_repository)

    def evaluate_transaction(
        self,
        transaction: InventoryTransaction,
    ) -> InventoryAlertResult:
        policy = self.inventory.get_alert_policy(
            internal_sku=transaction.internal_sku
        )
        if policy is None or not policy.enabled:
            return InventoryAlertResult(
                "DISABLED",
                transaction.internal_sku,
                transaction.inventory_after,
                policy.threshold_qty if policy is not None else None,
            )
        active = next(
            (
                item
                for item in self.incidents.list_active(
                    category=IncidentCategory.INVENTORY_ANOMALY
                )
                if item.source_type == "INVENTORY_ALERT"
                and item.subject_type == "internal_sku"
                and item.subject_key == transaction.internal_sku
            ),
            None,
        )
        if transaction.inventory_after > policy.threshold_qty:
            if active is None:
                return InventoryAlertResult(
                    "ABOVE_THRESHOLD",
                    transaction.internal_sku,
                    transaction.inventory_after,
                    policy.threshold_qty,
                )
            event_key = f"inventory-alert-recovered:{transaction.transaction_id}"
            result = self.incident_service.transition(
                active.incident_id,
                to_status=IncidentStatus.RESOLVED,
                event_key=event_key,
                occurred_at=transaction.recorded_at,
                source_type="INVENTORY_ALERT",
                source_ref_id=transaction.transaction_id,
                reason="真实库存已恢复到预警阈值以上",
            )
            notification = self._enqueue(
                notification_type="inventory_recovered",
                incident_id=result.incident.incident_id,
                event_key=event_key,
                message=(
                    f"库存已恢复：{transaction.internal_sku} 当前 "
                    f"{transaction.inventory_after} 扎，高于阈值 {policy.threshold_qty} 扎。"
                ),
            )
            return InventoryAlertResult(
                "RECOVERED",
                transaction.internal_sku,
                transaction.inventory_after,
                policy.threshold_qty,
                result.incident.incident_id,
                notification.notification_id,
            )

        if active is not None and not _repeat_is_due(
            active.last_detected_at,
            transaction.recorded_at,
            policy.repeat_interval_minutes,
        ):
            return InventoryAlertResult(
                "BELOW_THRESHOLD_SUPPRESSED",
                transaction.internal_sku,
                transaction.inventory_after,
                policy.threshold_qty,
                active.incident_id,
            )

        event_key = f"inventory-alert-low:{transaction.transaction_id}"
        result = self.incident_service.detect(
            IncidentDetection(
                event_key=event_key,
                category=IncidentCategory.INVENTORY_ANOMALY,
                source_type="INVENTORY_ALERT",
                source_ref_id=transaction.transaction_id,
                severity="S2",
                blocks_finalization=False,
                subject_type="internal_sku",
                subject_key=transaction.internal_sku,
                title="真实库存偏低",
                description=(
                    f"当前 {transaction.inventory_after} 扎，预警阈值 "
                    f"{policy.threshold_qty} 扎。"
                ),
                occurred_at=transaction.recorded_at,
                reason="LOW_REAL_INVENTORY",
                payload={
                    "current_qty": transaction.inventory_after,
                    "threshold_qty": policy.threshold_qty,
                    "policy_key": policy.policy_key,
                    "policy_version": policy.version,
                },
            )
        )
        notification = self._enqueue(
            notification_type="inventory_low",
            incident_id=result.incident.incident_id,
            event_key=_notification_window_key(
                result.incident.incident_id,
                result.incident.first_detected_at,
                transaction.recorded_at,
                policy.repeat_interval_minutes,
            ),
            message=(
                f"库存偏低：{transaction.internal_sku} 当前 "
                f"{transaction.inventory_after} 扎，阈值 {policy.threshold_qty} 扎。"
            ),
        )
        return InventoryAlertResult(
            "REPEATED" if result.incident.occurrence_count > 1 else "DETECTED",
            transaction.internal_sku,
            transaction.inventory_after,
            policy.threshold_qty,
            result.incident.incident_id,
            notification.notification_id,
        )

    def _enqueue(
        self,
        *,
        notification_type: str,
        incident_id: str,
        event_key: str,
        message: str,
    ):
        recipient_type = (
            os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip()
            or "role"
        )
        recipient_ref = (
            os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip()
            or "operations"
        )
        channel = (
            os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "").strip().lower()
            or "unconfigured"
        )
        key = self.outbox.notification_key(
            notification_type,
            incident_id,
            event_key,
            channel,
            recipient_ref,
        )
        return self.outbox.enqueue(
            notification_type=notification_type,
            notification_key=key,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            payload={
                "message": message,
                "reason": "真实库存阈值",
                "platform_name": "",
                "trade_date": "",
            },
            priority=60,
            max_attempts=3,
        )


def _repeat_is_due(
    previous: datetime,
    current: datetime,
    repeat_interval_minutes: int,
) -> bool:
    previous_aware = _as_aware(previous)
    current_aware = _as_aware(current)
    return current_aware - previous_aware >= timedelta(
        minutes=repeat_interval_minutes
    )


def _notification_window_key(
    incident_id: str,
    first_detected_at: datetime,
    current: datetime,
    repeat_interval_minutes: int,
) -> str:
    elapsed = max(
        _as_aware(current) - _as_aware(first_detected_at),
        timedelta(0),
    )
    interval_seconds = repeat_interval_minutes * 60
    window = int(elapsed.total_seconds() // interval_seconds)
    return f"inventory-alert-notification:{incident_id}:{window}"


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
