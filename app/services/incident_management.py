from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

from app.enums import (
    IncidentCategory,
    IncidentEventType,
    IncidentStatus,
    ReviewTaskStatus,
)
from app.exceptions import IncidentTransitionError
from app.models import (
    IncidentMutationResult,
    NotificationOutbox,
    OperationalIncident,
    OperationalIncidentEvent,
    ReviewTask,
    ReviewToken,
)
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.notification_outbox import (
    NotificationOutboxService,
    OutboxReviewNotificationService,
)
from app.services.runtime import ReviewTokenCreationResult, ReviewTokenService


@dataclass(frozen=True, slots=True)
class IncidentDetection:
    event_key: str
    category: IncidentCategory
    source_type: str
    source_ref_id: str
    severity: str
    blocks_finalization: bool
    subject_type: str
    subject_key: str
    title: str
    description: str
    occurred_at: datetime
    platform_name: str | None = None
    platform_trade_date: date | None = None
    seller_operation_date: date | None = None
    reason: str = ""
    payload: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class IncidentReviewCreationResult:
    incident: OperationalIncident
    review_task: ReviewTask
    review_token: ReviewToken
    notification: NotificationOutbox
    raw_token: str
    mobile_review_url: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class IncidentNotificationTiming:
    incident_id: str
    review_task_id: str
    channel: str
    escalation_state: str
    notification_count: int
    decision_window_started_at: datetime | None
    next_notification_at: datetime | None
    automatic_inference_allowed: bool


@dataclass(frozen=True, slots=True)
class IncidentMidpointReminderResult:
    status: str
    notification: NotificationOutbox | None = None


@dataclass(frozen=True, slots=True)
class IncidentPulseEligibility:
    incident_id: str
    review_task_id: str
    eligible: bool
    reason: str
    pulse_run_id: str | None = None
    observation_batch_id: str | None = None
    observation_item_id: str | None = None
    pulse_scheduled_for: datetime | None = None
    automatic_eligibility_reached_at: datetime | None = None
    observed_price: Decimal | None = None
    mapping_version: str = ""


class IncidentManagementService:
    """Trusted Incident command boundary; it does not execute platform actions."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository
        self.repository = OperationalIncidentRepository(runtime_repository)

    def detect(self, detection: IncidentDetection) -> IncidentMutationResult:
        return self.repository.record_detection(
            event_key=detection.event_key,
            dedupe_key=self.build_dedupe_key(detection),
            category=detection.category,
            source_type=detection.source_type,
            source_ref_id=detection.source_ref_id,
            severity=detection.severity,
            blocks_finalization=detection.blocks_finalization,
            platform_name=detection.platform_name,
            platform_trade_date=detection.platform_trade_date,
            seller_operation_date=detection.seller_operation_date,
            subject_type=detection.subject_type,
            subject_key=detection.subject_key,
            title=detection.title,
            description=detection.description,
            occurred_at=detection.occurred_at,
            event_payload={"reason": detection.reason, **(detection.payload or {})},
        )

    def transition(
        self,
        incident_id: str,
        *,
        to_status: IncidentStatus,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str = "",
        reason: str = "",
    ) -> IncidentMutationResult:
        return self.repository.transition_status(
            incident_id,
            to_status=to_status,
            event_key=event_key,
            occurred_at=occurred_at,
            source_type=source_type,
            source_ref_id=source_ref_id,
            event_payload={"reason": reason},
        )

    def acknowledge(
        self,
        incident_id: str,
        *,
        event_key: str,
        occurred_at: datetime,
        actor: str,
        note: str = "",
    ) -> IncidentMutationResult:
        return self.repository.acknowledge(
            incident_id,
            event_key=event_key,
            occurred_at=occurred_at,
            source_type="HUMAN_REVIEW",
            source_ref_id=actor,
            event_payload={"actor": actor, "note": note},
        )

    def change_severity(
        self,
        incident_id: str,
        *,
        severity: str,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str = "",
        reason: str = "",
    ) -> IncidentMutationResult:
        return self.repository.change_severity(
            incident_id,
            severity=severity,
            event_key=event_key,
            occurred_at=occurred_at,
            source_type=source_type,
            source_ref_id=source_ref_id,
            reason=reason,
        )

    def record_recovery(
        self,
        incident_id: str,
        *,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str = "",
        payload: dict[str, object] | None = None,
    ) -> IncidentMutationResult:
        return self.repository.record_related_event(
            incident_id,
            event_type=IncidentEventType.RECOVERY_RECORDED,
            event_key=event_key,
            occurred_at=occurred_at,
            source_type=source_type,
            source_ref_id=source_ref_id,
            event_payload=payload,
        )

    def get(self, incident_id: str) -> OperationalIncident | None:
        return self.repository.get(incident_id)

    def list_events(self, incident_id: str) -> list[OperationalIncidentEvent]:
        return self.repository.list_events(incident_id)

    @staticmethod
    def build_dedupe_key(detection: IncidentDetection) -> str:
        identity = {
            "category": detection.category.value,
            "platform_name": detection.platform_name or "",
            "platform_trade_date": (
                detection.platform_trade_date.isoformat()
                if detection.platform_trade_date is not None
                else ""
            ),
            "subject_type": detection.subject_type.strip(),
            "subject_key": detection.subject_key.strip(),
            "reason": detection.reason.strip(),
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class IncidentReviewService:
    """Create the decision-first Incident Review bundle without a source task."""

    REVIEW_TYPE = "emergency_protection"
    ALLOWED_ACTIONS = [
        ReviewTaskStatus.ADJUSTED.value,
        ReviewTaskStatus.APPROVED.value,
        ReviewTaskStatus.REJECTED.value,
    ]

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository
        self.incidents = OperationalIncidentRepository(runtime_repository)
        self.tokens = ReviewTokenService(runtime_repository)
        self.notifications = OutboxReviewNotificationService(runtime_repository)

    def create_initial_review(
        self,
        incident_id: str,
        *,
        required_by: datetime,
        created_at: datetime,
        failure_injector: Callable[[str], None] | None = None,
    ) -> IncidentReviewCreationResult:
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise IncidentTransitionError(f"Incident not found: {incident_id}")
        if incident.severity not in {"S3", "S4"}:
            raise IncidentTransitionError(
                "price protection Review requires S3 or S4 Incident"
            )
        if incident.incident_status not in {
            IncidentStatus.OPEN,
            IncidentStatus.WAITING_HUMAN,
        }:
            raise IncidentTransitionError(
                f"Incident cannot create Review from {incident.incident_status.value}"
            )
        if created_at.tzinfo is None or required_by.tzinfo is None:
            raise ValueError("Incident Review timestamps must be timezone-aware")
        if required_by <= created_at:
            raise ValueError("Incident Review required_by must be after created_at")

        review_dedupe_key = (
            f"incident:{incident.incident_id}:occurrence:{incident.occurrence_count}:"
            f"{self.REVIEW_TYPE}"
        )
        existing = self.runtime_repository.get_pending_review_task_by_dedupe_key(
            review_dedupe_key
        )
        if existing is not None:
            return self._existing_result(incident, existing)

        review_task_id = _stable_review_id(review_dedupe_key)
        review_task = ReviewTask(
            review_task_id=review_task_id,
            trade_date=incident.platform_trade_date,
            scope_type="incident",
            scope_key=incident.incident_id,
            dedupe_key=review_dedupe_key,
            source_task_id=None,
            review_type=self.REVIEW_TYPE,
            review_status=ReviewTaskStatus.PENDING,
            internal_sku=(
                incident.subject_key
                if incident.subject_type in {"internal_sku", "listing"}
                else None
            ),
            platform_name=incident.platform_name,
            reason=incident.title,
            review_payload={
                "incident_id": incident.incident_id,
                "incident_occurrence_count": incident.occurrence_count,
                "severity": incident.severity,
                "actions": [
                    {"status": "adjusted", "label": "改价到"},
                    {"status": "approved", "label": "立即下架"},
                    {"status": "rejected", "label": "我来处理"},
                ],
            },
            required_by=required_by,
            created_at=created_at,
            updated_at=created_at,
        )
        token = self.tokens.build_reconstructable_token_candidate(
            review_task,
            allowed_actions=self.ALLOWED_ACTIONS,
            expires_at=required_by,
            created_at=created_at,
        )
        notification, compatibility_log = (
            self.notifications.outbox_service.build_review_notification_candidate(
                review_task,
                event_version=f"occurrence-{incident.occurrence_count}",
            )
        )
        event_key = f"incident-review:{review_task.review_task_id}"
        event = OperationalIncidentEvent(
            event_id=_stable_event_id(event_key),
            event_key=event_key,
            incident_id=incident.incident_id,
            event_type=IncidentEventType.REVIEW_RECORDED,
            occurred_at=created_at,
            source_type="INCIDENT_REVIEW_SERVICE",
            source_ref_id=review_task.review_task_id,
            from_status=incident.incident_status,
            to_status=IncidentStatus.WAITING_HUMAN,
            severity=incident.severity,
            event_payload={
                "review_task_id": review_task.review_task_id,
                "review_token_id": token.review_token.token_id,
                "notification_id": notification.notification_id,
                "allowed_actions": self.ALLOWED_ACTIONS,
            },
            created_at=created_at,
        )
        inserted_review, inserted_outbox = (
            self.runtime_repository.insert_review_task_with_notification_outbox(
                review_task,
                notification,
                review_token=token.review_token,
                incident_event=event,
                compatibility_log=compatibility_log,
                failure_injector=failure_injector,
            )
        )
        if inserted_review != 1 or inserted_outbox != 1:
            existing = self.runtime_repository.get_pending_review_task_by_dedupe_key(
                review_dedupe_key
            )
            if existing is None:
                raise RuntimeError("Incident Review duplicate could not be resolved")
            current = self.incidents.get(incident_id)
            if current is None:
                raise RuntimeError("Incident disappeared after Review replay")
            return self._existing_result(current, existing)
        current = self.incidents.get(incident_id)
        if current is None:
            raise RuntimeError("Incident disappeared after Review creation")
        return IncidentReviewCreationResult(
            incident=current,
            review_task=review_task,
            review_token=token.review_token,
            notification=notification,
            raw_token=token.raw_token,
            mobile_review_url=token.mobile_review_url,
        )

    def _existing_result(
        self,
        incident: OperationalIncident,
        review_task: ReviewTask,
    ) -> IncidentReviewCreationResult:
        tokens = self.runtime_repository.list_review_tokens_by_review_task_id(
            review_task.review_task_id
        )
        review_token = next(
            (
                candidate
                for candidate in tokens
                if candidate.note == self.tokens.RECONSTRUCTABLE_TOKEN_NOTE
            ),
            None,
        )
        if review_token is None:
            raise RuntimeError("Incident Review has no reconstructable token")
        token: ReviewTokenCreationResult = self.tokens.reconstruct_token(review_token)
        notifications = self.runtime_repository.list_notification_outbox(
            related_review_task_id=review_task.review_task_id
        )
        notification = next(
            (
                candidate
                for candidate in notifications
                if candidate.notification_type == "mobile_review_required"
            ),
            None,
        )
        if notification is None:
            raise RuntimeError("Incident Review has no initial Outbox intent")
        return IncidentReviewCreationResult(
            incident=incident,
            review_task=review_task,
            review_token=review_token,
            notification=notification,
            raw_token=token.raw_token,
            mobile_review_url=token.mobile_review_url,
            replayed=True,
        )


def _stable_review_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"incident-review-{digest}"


def _stable_event_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"incident-event-{digest}"


class IncidentNotificationService:
    """Project proven delivery time and create the single optional S4 reminder."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository
        self.outbox_service = NotificationOutboxService(runtime_repository)
        self.incidents = OperationalIncidentRepository(runtime_repository)

    def sync_initial_delivery(
        self,
        incident_id: str,
        review_task_id: str,
    ) -> IncidentNotificationTiming:
        state = self.runtime_repository.sync_incident_initial_notification_state(
            incident_id=incident_id,
            review_task_id=review_task_id,
        )
        return _notification_timing(
            incident_id,
            review_task_id,
            state,
            decision_window_started_at=self._initial_sent_at(
                incident_id,
                review_task_id,
            ),
            incident_severity=self._incident_severity(incident_id),
        )

    def enqueue_midpoint_if_due(
        self,
        incident_id: str,
        review_task_id: str,
        *,
        now: datetime,
    ) -> IncidentMidpointReminderResult:
        review = self.runtime_repository.get_review_task(review_task_id)
        if review is None:
            raise ValueError(f"Review not found: {review_task_id}")
        outboxes = self.runtime_repository.list_notification_outbox(
            related_review_task_id=review_task_id
        )
        initial_candidates = [
            candidate
            for candidate in outboxes
            if candidate.notification_type == "mobile_review_required"
        ]
        initial = (
            min(
                initial_candidates,
                key=lambda item: (
                    item.created_at.isoformat() if item.created_at is not None else "",
                    item.notification_id,
                ),
            )
            if initial_candidates
            else None
        )
        if initial is None:
            raise ValueError("Incident Review has no initial notification Outbox")
        candidate_builder = NotificationOutboxService(
            self.runtime_repository,
            clock=lambda: now,
        )
        candidate, compatibility_log = (
            candidate_builder.build_review_notification_candidate(
                review,
                event_version="s4-midpoint-v1",
                recipient_type=initial.recipient_type,
                recipient_ref=initial.recipient_ref,
                channel=initial.channel,
                priority=max(initial.priority, 90),
                message="极端低价仍未处理，请立即选择改价、立即下架或我来处理。",
            )
        )
        status = self.runtime_repository.enqueue_incident_midpoint_notification_atomic(
            incident_id=incident_id,
            review_task_id=review_task_id,
            notification=candidate,
            compatibility_log=compatibility_log,
            now=now,
        )
        if status == "MIDPOINT_QUEUED":
            return IncidentMidpointReminderResult(status=status, notification=candidate)
        existing = next(
            (
                item
                for item in self.runtime_repository.list_notification_outbox(
                    related_review_task_id=review_task_id
                )
                if item.notification_key == candidate.notification_key
            ),
            None,
        )
        return IncidentMidpointReminderResult(status=status, notification=existing)

    def sync_midpoint_delivery(
        self,
        incident_id: str,
        review_task_id: str,
        notification_id: str,
    ) -> IncidentNotificationTiming:
        state = self.runtime_repository.sync_incident_midpoint_notification_state(
            incident_id=incident_id,
            notification_id=notification_id,
        )
        return _notification_timing(
            incident_id,
            review_task_id,
            state,
            decision_window_started_at=self._initial_sent_at(
                incident_id,
                review_task_id,
            ),
            incident_severity=self._incident_severity(incident_id),
        )

    def enqueue_status_notification(
        self,
        incident_id: str,
        *,
        notification_kind: str,
        source_event_key: str,
        message: str,
        related_task_id: str | None = None,
        deadline_at: datetime | None = None,
        priority: int = 80,
    ) -> NotificationOutbox:
        allowed_kinds = {
            "incident_recovered",
            "worker_recovered",
            "worker_recovery_failed",
            "incident_task_success",
            "incident_task_failed",
            "incident_task_unknown",
        }
        if notification_kind not in allowed_kinds:
            raise ValueError(
                f"unsupported Incident notification kind: {notification_kind}"
            )
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        if not source_event_key.strip() or not message.strip():
            raise ValueError("Incident notification requires event key and message")
        recipient_type = (
            os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role"
        )
        recipient_ref = (
            os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip()
            or "operations"
        )
        channel = os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "").strip().lower()
        if not channel:
            channel = "unconfigured"
        notification_key = self.outbox_service.notification_key(
            notification_kind,
            incident_id,
            source_event_key,
            channel,
            recipient_ref,
        )
        return self.outbox_service.enqueue(
            notification_type=notification_kind,
            notification_key=notification_key,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            related_task_id=related_task_id,
            payload={
                "message": message,
                "reason": incident.title,
                "platform_name": incident.platform_name or "",
                "trade_date": (
                    incident.platform_trade_date.isoformat()
                    if incident.platform_trade_date is not None
                    else ""
                ),
            },
            priority=priority,
            max_attempts=3,
            deadline_at=deadline_at,
        )

    def evaluate_online_pulse_eligibility(
        self,
        incident_id: str,
        review_task_id: str,
        *,
        initial_observation_id: str | None = None,
    ) -> IncidentPulseEligibility:
        """Select the first complete imported pulse after proven notification delivery.

        This is a read-only 6A-1 gate.  It does not evaluate the emergency price
        threshold, create a task, publish a queue request, or authorize a platform
        action; those remain disabled until the later policy/authorization phases.
        """

        incident = self.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        review = self.runtime_repository.get_review_task(review_task_id)
        if review is None or review.scope_key != incident_id:
            raise ValueError("Incident Review is not bound to the requested Incident")
        if incident.severity != "S4":
            return _ineligible_pulse(incident_id, review_task_id, "INCIDENT_NOT_S4")
        if review.review_status is not ReviewTaskStatus.PENDING:
            return _ineligible_pulse(incident_id, review_task_id, "REVIEW_RESOLVED")
        if incident.incident_status not in {
            IncidentStatus.OPEN,
            IncidentStatus.WAITING_HUMAN,
        }:
            return _ineligible_pulse(
                incident_id,
                review_task_id,
                "INCIDENT_NOT_ACTIVE",
            )
        timing = self.sync_initial_delivery(incident_id, review_task_id)
        if not timing.automatic_inference_allowed:
            return _ineligible_pulse(
                incident_id,
                review_task_id,
                "INITIAL_DELIVERY_NOT_CONFIRMED",
            )
        if (
            incident.platform_name is None
            or incident.platform_trade_date is None
            or incident.subject_type not in {"internal_sku", "listing"}
        ):
            return _ineligible_pulse(
                incident_id,
                review_task_id,
                "INCIDENT_SCOPE_UNSUPPORTED",
            )
        first_observation_id = (
            initial_observation_id or incident.source_ref_id
        ).strip()
        initial_identity = self.incidents.get_product_observation_identity(
            first_observation_id
        )
        if (
            initial_identity is None
            or str(initial_identity["platform_name"]) != incident.platform_name
            or str(initial_identity["platform_trade_date"])
            != incident.platform_trade_date.isoformat()
            or str(initial_identity["internal_sku"] or "") != incident.subject_key
            or str(initial_identity["mapping_status"]) != "VERIFIED"
        ):
            return _ineligible_pulse(
                incident_id,
                review_task_id,
                "INITIAL_OBSERVATION_NOT_BOUND",
            )
        decision_started_at = timing.decision_window_started_at
        if decision_started_at is None:
            return _ineligible_pulse(
                incident_id,
                review_task_id,
                "INITIAL_DELIVERY_NOT_CONFIRMED",
            )
        candidate = self.incidents.find_qualified_online_pulse_observation(
            platform_name=incident.platform_name,
            platform_trade_date=incident.platform_trade_date,
            internal_sku=incident.subject_key,
            decision_window_started_at=decision_started_at,
            initial_observation_id=first_observation_id,
        )
        if candidate is None:
            return _ineligible_pulse(
                incident_id,
                review_task_id,
                "WAITING_FOR_QUALIFIED_ONLINE_PULSE",
            )
        try:
            observed_price = Decimal(str(candidate["observed_price"]))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("qualified pulse price is not a valid decimal") from exc
        if not observed_price.is_finite() or observed_price < 0:
            raise ValueError("qualified pulse price must be finite and non-negative")
        qualified_at = max(
            parsed
            for parsed in (
                _optional_datetime(candidate.get("scan_completed_at")),
                _optional_datetime(candidate.get("imported_at")),
                _optional_datetime(candidate.get("pulse_finished_at")),
                _optional_datetime(candidate.get("target_finished_at")),
            )
            if parsed is not None
        )
        return IncidentPulseEligibility(
            incident_id=incident_id,
            review_task_id=review_task_id,
            eligible=True,
            reason="QUALIFIED_ONLINE_PULSE_IMPORTED",
            pulse_run_id=str(candidate["pulse_run_id"]),
            observation_batch_id=str(candidate["observation_batch_id"]),
            observation_item_id=str(candidate["observation_item_id"]),
            pulse_scheduled_for=_optional_datetime(
                candidate.get("pulse_scheduled_for")
            ),
            automatic_eligibility_reached_at=qualified_at,
            observed_price=observed_price,
            mapping_version=str(candidate["mapping_version"] or ""),
        )

    def _incident_severity(self, incident_id: str) -> str:
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        return incident.severity

    def _initial_sent_at(
        self,
        incident_id: str,
        review_task_id: str,
    ) -> datetime | None:
        initial_notification_id = next(
            (
                str(event.event_payload.get("notification_id") or "")
                for event in self.incidents.list_events(incident_id)
                if event.event_type is IncidentEventType.REVIEW_RECORDED
                and event.event_payload.get("review_task_id") == review_task_id
                and event.event_payload.get("notification_id")
            ),
            "",
        )
        initial = next(
            (
                candidate
                for candidate in self.runtime_repository.list_notification_outbox(
                    related_review_task_id=review_task_id
                )
                if candidate.notification_id == initial_notification_id
            ),
            None,
        )
        return (
            initial.sent_at
            if initial is not None and initial.status == "SENT"
            else None
        )


def _notification_timing(
    incident_id: str,
    review_task_id: str,
    state: dict[str, object],
    *,
    decision_window_started_at: datetime | None,
    incident_severity: str,
) -> IncidentNotificationTiming:
    escalation_state = str(state["escalation_state"])
    return IncidentNotificationTiming(
        incident_id=incident_id,
        review_task_id=review_task_id,
        channel=str(state["channel"]),
        escalation_state=escalation_state,
        notification_count=int(state["notification_count"]),
        decision_window_started_at=decision_window_started_at,
        next_notification_at=_optional_datetime(state.get("next_notification_at")),
        automatic_inference_allowed=(
            incident_severity == "S4"
            and decision_window_started_at is not None
            and escalation_state
            not in {
                "WAITING_INITIAL_DELIVERY",
                "INITIAL_DELIVERY_BLOCKED",
                "MIDPOINT_REVIEW_RESOLVED",
                "MIDPOINT_CONDITION_CLEARED",
            }
        ),
    )


def _ineligible_pulse(
    incident_id: str,
    review_task_id: str,
    reason: str,
) -> IncidentPulseEligibility:
    return IncidentPulseEligibility(
        incident_id=incident_id,
        review_task_id=review_task_id,
        eligible=False,
        reason=reason,
    )


def _optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
