"""Pure action-specific automation gate for Task 13."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from app.shadowbot_listing_contract import (
    V5_GATE_DECISIONS,
    V5_GATE_PHASES,
    V5_LISTING_LOCATIONS,
    V5_WRITE_LOCK_STATUSES,
)


_WRITABLE_ACTIONS = frozenset({"update_price", "set_online", "set_offline"})
_ANOMALOUS_LOCATIONS = frozenset({"both", "neither", "ambiguous"})


@dataclass(frozen=True, slots=True)
class AutomationGateResult:
    decision: str
    action_type: str
    internal_sku: str
    gate_phase: str
    allowed_actions: tuple[str, ...]
    block_reasons_by_action: dict[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        if self.decision not in V5_GATE_DECISIONS:
            raise ValueError("invalid automation gate decision")


def evaluate_automation_gate(
    *,
    action_type: str,
    internal_sku: str,
    gate_phase: str,
    online_status: str = "",
    listing_location: str | None = None,
    snapshot_valid: bool = False,
    fresh_sync_required: bool = False,
    online_scan_complete: bool = False,
    online_occurrences: int | None = None,
    observed_price: Any = None,
    observed_inventory: Any = None,
    target_price: Any = None,
    target_inventory: Any = None,
    open_reviews: Iterable[Mapping[str, Any]] = (),
    write_locks: Iterable[Mapping[str, Any]] = (),
    requesting_operation_id: str | None = None,
) -> AutomationGateResult:
    action = str(action_type or "").strip().lower()
    sku = str(internal_sku or "").strip().upper()
    phase = str(gate_phase or "").strip().upper()
    if action not in _WRITABLE_ACTIONS:
        raise ValueError("unsupported automation gate action_type")
    if not sku:
        raise ValueError("internal_sku is required")
    if phase not in V5_GATE_PHASES:
        raise ValueError("invalid automation gate phase")

    reasons: list[str] = []
    reasons.extend(review_block_reasons(action, open_reviews))
    reasons.extend(
        _write_lock_block_reasons(
            phase,
            write_locks,
            requesting_operation_id=requesting_operation_id,
        )
    )

    location = (
        str(listing_location or "").strip().lower()
        if listing_location not in (None, "")
        else None
    )
    historical_snapshot_usable = snapshot_valid or phase != "PRE_PUBLISH"
    if not historical_snapshot_usable:
        location = None
    if location is not None and location not in V5_LISTING_LOCATIONS:
        reasons.append("LISTING_LOCATION_INVALID")
    elif location in _ANOMALOUS_LOCATIONS:
        reasons.append("LISTING_ANOMALY_REVIEW_OPEN")
    if fresh_sync_required and not snapshot_valid:
        reasons.append("FRESH_SYNC_REQUIRED")
    if reasons:
        return _blocked(action, sku, phase, reasons)

    if action == "update_price":
        if str(online_status or "").strip().lower() != "online":
            return _blocked(action, sku, phase, ["EXPECTED_ONLINE_ONLY"])
        return _allowed("EXECUTE", action, sku, phase)

    if action == "set_offline":
        online_scan_usable = online_scan_complete and historical_snapshot_usable
        if online_scan_usable and _occurrences(online_occurrences) == 0:
            return _allowed("ALREADY_APPLIED", action, sku, phase)
        if online_scan_usable and _occurrences(online_occurrences) == 1:
            return _allowed("EXECUTE", action, sku, phase)
        if online_scan_usable and _occurrences(online_occurrences) > 1:
            return _blocked(
                action,
                sku,
                phase,
                ["DUPLICATE_PAGE_IDENTITY"],
            )
        if location == "online_only":
            return _allowed("EXECUTE", action, sku, phase)
        current_status = str(online_status or "").strip().lower()
        if current_status == "online":
            return _allowed("EXECUTE", action, sku, phase)
        if current_status == "offline":
            return _allowed("ALREADY_APPLIED", action, sku, phase)
        return _blocked(action, sku, phase, ["EXPECTED_ONLINE_ONLY"])

    if phase == "PRE_PUBLISH" and location is None:
        return _allowed("EXECUTE", action, sku, phase)
    if location == "waiting_only":
        return _allowed("EXECUTE", action, sku, phase)
    if location == "online_only":
        if _same_price(observed_price, target_price) and _same_inventory(
            observed_inventory,
            target_inventory,
        ):
            return _allowed("ALREADY_APPLIED", action, sku, phase)
        return _blocked(action, sku, phase, ["LISTING_DATA_MISMATCH"])
    return _blocked(action, sku, phase, ["EXPECTED_WAITING_ONLY"])


def review_block_reasons(
    action: str,
    open_reviews: Iterable[Mapping[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for review in open_reviews:
        blocked_actions = review.get("blocked_actions")
        if not isinstance(blocked_actions, (list, tuple, set, frozenset)):
            reasons.append("REVIEW_BLOCKED_ACTIONS_INVALID")
            continue
        normalized = {str(value or "").strip().lower() for value in blocked_actions}
        if not normalized or not normalized.issubset(_WRITABLE_ACTIONS):
            reasons.append("REVIEW_BLOCKED_ACTIONS_INVALID")
            continue
        if action in normalized:
            reason_code = str(review.get("reason_code") or "").strip().upper()
            reasons.append(reason_code or "LISTING_ANOMALY_REVIEW_OPEN")
    return reasons


# Backwards-compatible private alias for callers that imported the helper while
# the gate module was still internal-only.
_review_block_reasons = review_block_reasons


def _write_lock_block_reasons(
    phase: str,
    write_locks: Iterable[Mapping[str, Any]],
    *,
    requesting_operation_id: str | None,
) -> list[str]:
    reasons: list[str] = []
    requesting = str(requesting_operation_id or "").strip()
    for lock in write_locks:
        status = str(lock.get("status") or "").strip().upper()
        owner = str(lock.get("operation_id") or "").strip()
        if status not in V5_WRITE_LOCK_STATUSES:
            reasons.append("WRITE_LOCK_STATE_INVALID")
            continue
        if status == "RELEASED":
            continue
        if phase == "PRE_PUBLISH":
            reasons.append(_lock_reason(status))
            continue
        if status == "ACTIVE" and requesting and owner == requesting:
            continue
        reasons.append(_lock_reason(status))
    return reasons


def _lock_reason(status: str) -> str:
    if status == "UNKNOWN":
        return "OPERATION_RECONCILIATION_PENDING"
    if status == "REVIEW_BLOCKED":
        return "PARTIAL_OPERATION_REVIEW_PENDING"
    return "WRITE_LOCK_ACTIVE"


def _allowed(
    decision: str,
    action: str,
    sku: str,
    phase: str,
) -> AutomationGateResult:
    return AutomationGateResult(
        decision=decision,
        action_type=action,
        internal_sku=sku,
        gate_phase=phase,
        allowed_actions=(action,),
        block_reasons_by_action={},
    )


def _blocked(
    action: str,
    sku: str,
    phase: str,
    reasons: Iterable[str],
) -> AutomationGateResult:
    normalized = tuple(sorted({str(reason).strip().upper() for reason in reasons if reason}))
    return AutomationGateResult(
        decision="BLOCKED",
        action_type=action,
        internal_sku=sku,
        gate_phase=phase,
        allowed_actions=(),
        block_reasons_by_action={action: normalized},
    )


def _occurrences(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("online_occurrences must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError("online_occurrences must be a non-negative integer")
    if result < 0:
        raise ValueError("online_occurrences must be a non-negative integer")
    return result


def _same_price(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)).quantize(Decimal("0.01")) == Decimal(
            str(right)
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return False


def _same_inventory(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        return int(left) == int(right) and int(left) >= 0
    except (TypeError, ValueError):
        return False
