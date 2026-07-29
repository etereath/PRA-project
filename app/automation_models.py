from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.enums import AutomationRunStatus, SellerPhase


@dataclass(frozen=True, slots=True)
class AutomationJob:
    """Versioned-in-place scheduler configuration stored in Runtime SQLite."""

    job_id: str
    job_type: str
    display_name: str
    enabled: bool
    schedule_kind: str
    schedule_expression: str
    priority: int
    config: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AutomationRun:
    """One logical automation window and its fenced execution lease."""

    run_id: str
    job_id: str
    job_type: str
    logical_run_key: str
    run_status: AutomationRunStatus
    platform_name: str
    platform_trade_date: date
    seller_operation_date: date
    seller_phase: SellerPhase
    time_policy_version: str
    scheduled_for: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lease_owner: str = ""
    lease_version: int = 0
    lease_expires_at: datetime | None = None
    input_manifest_sha256: str = ""
    output_manifest_sha256: str = ""
    error_code: str = ""
    error_message: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AutomationRunEvent:
    event_id: str
    run_id: str
    event_type: str
    from_status: AutomationRunStatus | None
    to_status: AutomationRunStatus | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AutomationRunLink:
    parent_run_id: str
    child_run_id: str
    relation_type: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AutomationRunClaim:
    run: AutomationRun
    owner_token: str
    lease_version: int
    lease_expires_at: datetime
    reclaimed: bool


@dataclass(frozen=True, slots=True)
class AutomationRunOutcome:
    """Bounded terminal result returned by an automation handler."""

    status: AutomationRunStatus
    output_manifest_sha256: str = ""
    error_code: str = ""
    error_message: str = ""
    event_payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        terminal_statuses = {
            AutomationRunStatus.SUCCESS,
            AutomationRunStatus.PARTIAL,
            AutomationRunStatus.FAILED,
            AutomationRunStatus.SKIPPED,
            AutomationRunStatus.CANCELLED,
        }
        if self.status not in terminal_statuses:
            raise ValueError(
                "AutomationRunOutcome status must be a handler terminal state"
            )


@dataclass(frozen=True, slots=True)
class AutomationScheduleResult:
    created_run_ids: tuple[str, ...] = ()
    missed_run_ids: tuple[str, ...] = ()
    merged_run_ids: tuple[str, ...] = ()
    truncated_window_count: int = 0


@dataclass(frozen=True, slots=True)
class AutomationCycleResult:
    scheduled: AutomationScheduleResult
    claimed_run_ids: tuple[str, ...] = ()
    completed_run_ids: tuple[str, ...] = ()
    blocked_reason: str = ""
    errors: tuple[str, ...] = ()
