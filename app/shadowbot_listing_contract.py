"""Dependency-free Task 13 listing contract primitives.

This module freezes the v5 vocabulary shared by PRA, the ShadowBot Worker,
Importer, Watchdog, reports, and tests.  It deliberately does not import
database models or services so it can later be deployed beside the Worker
without pulling the PRA application into the RPA host.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation


V5_CONTRACT_VERSION = 5
V5_MANIFEST_SCHEMA_VERSION = "shadowbot-listing-action-manifest-1.0"
V5_REQUEST_SCHEMA_VERSION = "shadowbot-listing-action-batch-request-1.0"
V5_RESULT_SCHEMA_VERSION = "shadowbot-listing-action-batch-result-1.0"
V5_PHASE_SCHEMA_VERSION = "shadowbot-listing-action-batch-phase-1.0"
V5_SNAPSHOT_SCHEMA_VERSION = "shadowbot-listing-sync-snapshot-1.0"
V5_ANOMALY_SCHEMA_VERSION = "shadowbot-listing-anomaly-1.0"
V5_GATE_SUMMARY_SCHEMA_VERSION = "shadowbot-listing-action-gate-summary-1.0"

V5_ACTION_TYPES = frozenset({"set_online", "set_offline", "sync_status"})
V5_WRITE_ACTION_TYPES = frozenset({"set_online", "set_offline"})
V5_DEVELOPMENT_FAULT_INJECTIONS = frozenset(
    {"AFTER_ACTION_CLICK_UNKNOWN"}
)
V5_SCAN_SCOPES = frozenset({"online", "online_and_waiting"})
V5_LISTING_LOCATIONS = frozenset(
    {"online_only", "waiting_only", "both", "neither", "ambiguous"}
)
V5_ONLINE_STATUSES = frozenset({"online", "offline"})
V5_GATE_PHASES = frozenset({"PRE_PUBLISH", "POST_PUBLISH_PREFLIGHT"})
V5_GATE_DECISIONS = frozenset({"EXECUTE", "ALREADY_APPLIED", "BLOCKED"})
V5_WRITE_LOCK_STATUSES = frozenset(
    {"ACTIVE", "UNKNOWN", "REVIEW_BLOCKED", "RELEASED"}
)
V5_OPERATION_RESULTS = frozenset(
    {
        "VERIFIED",
        "NOT_APPLIED",
        "PARTIALLY_APPLIED",
        "NEEDS_RECONCILIATION",
    }
)
V5_ITEM_OUTCOMES = frozenset(
    {
        "VERIFIED",
        "ALREADY_APPLIED",
        "NOT_APPLIED",
        "PARTIALLY_APPLIED",
        "NEEDS_RECONCILIATION",
        "FAILED",
        "NOT_ATTEMPTED",
    }
)
V5_BATCH_STATUSES = frozenset({"VERIFIED", "PARTIAL", "FAILED", "UNKNOWN"})
V5_SIDE_EFFECT_STATES = frozenset(
    {"NOT_STARTED", "NOT_APPLIED", "VERIFIED", "PARTIAL", "UNKNOWN"}
)
V5_RESOLUTION_STATUSES = frozenset(
    {"UNRESOLVED", "MANUAL_HANDLED", "CORRECTIVE_ACTION_AUTHORIZED"}
)
V5_ANOMALY_REASON_CODES = frozenset(
    {
        "UNMAPPED_PRODUCT",
        "IDENTITY_MAPPING_CONFLICT",
        "ABSENT_FROM_BOTH_LISTS",
        "DUPLICATE_PAGE_IDENTITY",
        "PRESENT_IN_BOTH_LISTS",
    }
)
V5_ANOMALY_RESOLUTION_POLICIES = frozenset(
    {"AUTO_CLEAR_BY_COMPLETE_SNAPSHOT", "MANUAL_ONLY"}
)
V5_PHASE_NAMES = frozenset(
    {
        "CLAIMED",
        "UI_STARTED",
        "PREFLIGHT_VALIDATED",
        "DETAIL_SAVE_INTENT_RECORDED",
        "DETAIL_SAVE_CLICKED",
        "DETAILS_VERIFIED",
        "ACTION_INTENT_RECORDED",
        "ACTION_CLICKED",
        "POST_FAILURE_SCAN_STARTED",
        "POST_FAILURE_SCAN_COMPLETED",
        "FINAL_VERIFICATION",
        "RESULT_WRITTEN",
    }
)


def canonical_nonnegative_inventory(value, require_canonical=False):
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise ValueError("inventory must be a canonical non-negative integer")
    raw = str(value).strip()
    if not raw or not raw.isdigit():
        raise ValueError("inventory must be a canonical non-negative integer")
    normalized = str(int(raw))
    if require_canonical and raw != normalized:
        raise ValueError("inventory must be a canonical non-negative integer")
    return int(normalized)


def canonical_optional_price(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("price must be a canonical positive decimal")
    raw = str(value).strip()
    try:
        price = Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("price must be a canonical positive decimal")
    formatted = format(price, ".2f")
    if price <= 0 or raw != formatted:
        raise ValueError("price must be a canonical positive decimal")
    return formatted


def derive_listing_location(
    online_occurrences,
    waiting_occurrences,
    *,
    mapping_ambiguous=False,
):
    online_count = _occurrence_count(online_occurrences, "online_occurrences")
    waiting_count = _occurrence_count(waiting_occurrences, "waiting_occurrences")
    if mapping_ambiguous or online_count > 1 or waiting_count > 1:
        return "ambiguous"
    if online_count == 1 and waiting_count == 1:
        return "both"
    if online_count == 1:
        return "online_only"
    if waiting_count == 1:
        return "waiting_only"
    return "neither"


def project_online_status(listing_location, current_online_status=None):
    location = str(listing_location or "").strip().lower()
    if location not in V5_LISTING_LOCATIONS:
        raise ValueError("invalid listing_location")
    if location in {"online_only", "both"}:
        return "online"
    if location in {"waiting_only", "neither"}:
        return "offline"
    current = str(current_online_status or "").strip().lower()
    if current not in V5_ONLINE_STATUSES:
        raise ValueError("ambiguous listing_location requires current online_status")
    return current


def derive_automation_disposition(
    *,
    listing_location,
    has_open_review=False,
    has_blocking_write_lock=False,
):
    location = str(listing_location or "").strip().lower()
    if location not in V5_LISTING_LOCATIONS:
        raise ValueError("invalid listing_location")
    if (
        location in {"both", "neither", "ambiguous"}
        or bool(has_open_review)
        or bool(has_blocking_write_lock)
    ):
        return "manual_review"
    return "actionable"


def listing_snapshot_is_valid(
    *,
    snapshot_complete,
    scan_started_at,
    last_listing_change_at=None,
):
    if snapshot_complete is not True:
        return False
    scan_started = _aware_datetime(scan_started_at, "scan_started_at")
    if last_listing_change_at in (None, ""):
        return True
    last_change = _aware_datetime(
        last_listing_change_at,
        "last_listing_change_at",
    )
    return scan_started >= last_change


def v5_result_counts(items):
    if not isinstance(items, list) or not items:
        raise ValueError("v5 result items must be a non-empty list")
    counts = {
        "batch_target_count": len(items),
        "attempted_count": 0,
        "verified_count": 0,
        "verified_applied_count": 0,
        "already_applied_count": 0,
        "unknown_count": 0,
        "partial_effect_count": 0,
        "not_attempted_count": 0,
        "failed_count": 0,
        "not_applied_count": 0,
    }
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("v5 result item must be an object")
        outcome = str(item.get("operation_result") or "").strip().upper()
        if outcome not in V5_ITEM_OUTCOMES:
            raise ValueError("invalid v5 result item outcome")
        if bool(item.get("action_confirm_clicked")) or bool(
            item.get("detail_save_clicked")
        ):
            counts["attempted_count"] += 1
        if outcome == "VERIFIED":
            counts["verified_count"] += 1
            counts["verified_applied_count"] += 1
        elif outcome == "ALREADY_APPLIED":
            counts["verified_count"] += 1
            counts["already_applied_count"] += 1
        elif outcome == "NEEDS_RECONCILIATION":
            counts["unknown_count"] += 1
        elif outcome == "PARTIALLY_APPLIED":
            counts["partial_effect_count"] += 1
        elif outcome == "NOT_ATTEMPTED":
            counts["not_attempted_count"] += 1
        else:
            counts["failed_count"] += 1
            if outcome == "NOT_APPLIED":
                counts["not_applied_count"] += 1
    if (
        counts["verified_count"]
        + counts["unknown_count"]
        + counts["partial_effect_count"]
        + counts["not_attempted_count"]
        + counts["failed_count"]
        != counts["batch_target_count"]
    ):
        raise ValueError("v5 result count identity failed")
    return counts


def derive_v5_batch_semantics(counts):
    total = int(counts.get("batch_target_count") or 0)
    if total <= 0:
        raise ValueError("v5 result counts must contain at least one target")
    verified = int(counts.get("verified_count") or 0)
    unknown = int(counts.get("unknown_count") or 0)
    partial_effect = int(counts.get("partial_effect_count") or 0)
    not_attempted = int(counts.get("not_attempted_count") or 0)
    failed = int(counts.get("failed_count") or 0)
    if verified + unknown + partial_effect + not_attempted + failed != total:
        raise ValueError("v5 result count identity failed")
    if unknown:
        batch_status = "UNKNOWN"
    elif partial_effect:
        batch_status = "PARTIAL"
    elif verified == total:
        batch_status = "VERIFIED"
    elif verified:
        batch_status = "PARTIAL"
    else:
        batch_status = "FAILED"
    if unknown:
        side_effect_state = "UNKNOWN"
    elif partial_effect:
        side_effect_state = "PARTIAL"
    elif int(counts.get("verified_applied_count") or 0):
        side_effect_state = "VERIFIED"
    elif int(counts.get("not_applied_count") or 0):
        side_effect_state = "NOT_APPLIED"
    else:
        side_effect_state = "NOT_STARTED"
    requires_manual_review = unknown > 0 or partial_effect > 0
    return {
        "batch_status": batch_status,
        "status": batch_status,
        "run_success_flag": batch_status == "VERIFIED",
        "business_operation_completed": int(counts.get("attempted_count") or 0)
        > 0,
        "side_effect_state": side_effect_state,
        "requires_manual_review": requires_manual_review,
        "reconciliation_pending": unknown > 0,
        "partial_effect_count": partial_effect,
    }


def v5_phase_matches_request(request, phase, request_file_sha256):
    if not isinstance(request, dict) or not isinstance(phase, dict):
        return False
    expected = {
        "schema_version": V5_PHASE_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "action_type": str(request.get("action_type") or ""),
        "batch_id": str(request.get("batch_id") or ""),
        "execution_attempt_id": str(request.get("execution_attempt_id") or ""),
        "instruction_hash": str(request.get("instruction_hash") or ""),
        "manifest_sha256": str(request.get("manifest_sha256") or ""),
        "request_file_sha256": str(request_file_sha256 or ""),
    }
    return all(phase.get(name) == value for name, value in expected.items())


def _occurrence_count(value, name):
    if isinstance(value, bool):
        raise ValueError(name + " must be a non-negative integer")
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise ValueError(name + " must be a non-negative integer")
    if count < 0 or str(value).strip() != str(count):
        raise ValueError(name + " must be a non-negative integer")
    return count


def _aware_datetime(value, name):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            raise ValueError(name + " must be an ISO-8601 datetime")
    if parsed.tzinfo is None:
        raise ValueError(name + " must contain a timezone")
    return parsed
