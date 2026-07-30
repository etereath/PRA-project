"""Dependency-free canonical primitives shared by PRA and the ShadowBot host.

The deployment sync copies this exact file beside the ShadowBot Worker.  Keep
the module compatible with the ShadowBot interpreter and free of ``app``
imports so both trust boundaries execute identical normalization and hashing.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation


_WHITESPACE_RE = re.compile(r"\s+")
_SET_ONLINE_CONFIRMATION_RE = re.compile(
    r"^\s*您确定上架[【\[]\s*(?P<grade>.+?)\s+(?P<product_name>.+?)\s*[】\]]吗[？?]\s*$"
)
_SET_OFFLINE_CONFIRMATION_RE = re.compile(
    r"^\s*您确定下架[【\[]\s*(?P<grade>.+?)\s+(?P<product_name>.+?)\s*[】\]]吗[？?]\s*$"
)

ORDER_SCAN_CONTRACT_VERSION = 6
ORDER_SCAN_REQUEST_SCHEMA_VERSION = "shadowbot-order-scan-request-1.0"
ORDER_SCAN_RESULT_SCHEMA_VERSION = "shadowbot-order-scan-result-1.0"
ORDER_SCAN_DEFAULT_LIMITS = {
    "max_rows": 500,
    "max_scrolls": 100,
    "max_seconds": 300,
}
ORDER_SCAN_HARD_LIMITS = {
    "max_rows": 2000,
    "max_scrolls": 500,
    "max_seconds": 900,
}


def normalize_contract_text(value):
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE_RE.sub(" ", normalized).strip().casefold()


def normalize_contract_grade(value):
    normalized = normalize_contract_text(value).upper()
    return normalized[:-1].rstrip() if normalized.endswith("级") else normalized


def normalize_contract_sku(value):
    normalized = normalize_contract_text(value)
    return normalized.upper() if normalized else None


def contract_identity_key(platform, sku, product_name, grade):
    normalized_platform = normalize_contract_text(platform)
    normalized_sku = normalize_contract_sku(sku)
    if normalized_sku:
        return "%s|sku:%s" % (normalized_platform, normalized_sku)
    return "%s|name:%s|grade:%s" % (
        normalized_platform,
        normalize_contract_text(product_name),
        normalize_contract_grade(grade),
    )


def parse_set_online_confirmation_identity(value):
    """Return the exact visible grade/name identity from the final dialog."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    matched = _SET_ONLINE_CONFIRMATION_RE.fullmatch(normalized)
    if matched is None:
        raise ValueError("set-online confirmation prompt is invalid")
    product_name = matched.group("product_name").strip()
    grade = matched.group("grade").strip()
    if not normalize_contract_text(product_name) or not normalize_contract_grade(
        grade
    ):
        raise ValueError("set-online confirmation identity is incomplete")
    return {
        "product_name": product_name,
        "grade": grade,
    }


def set_online_confirmation_matches(value, expected_product_name, expected_grade):
    """Require exact name + grade; suffix variants are distinct products."""

    identity = parse_set_online_confirmation_identity(value)
    return (
        normalize_contract_text(identity["product_name"])
        == normalize_contract_text(expected_product_name)
        and normalize_contract_grade(identity["grade"])
        == normalize_contract_grade(expected_grade)
    )


def parse_set_offline_confirmation_identity(value):
    """Return the exact visible grade/name identity from the final dialog."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    matched = _SET_OFFLINE_CONFIRMATION_RE.fullmatch(normalized)
    if matched is None:
        raise ValueError("set-offline confirmation prompt is invalid")
    product_name = matched.group("product_name").strip()
    grade = matched.group("grade").strip()
    if not normalize_contract_text(product_name) or not normalize_contract_grade(
        grade
    ):
        raise ValueError("set-offline confirmation identity is incomplete")
    return {
        "product_name": product_name,
        "grade": grade,
    }


def set_offline_confirmation_matches(value, expected_product_name, expected_grade):
    """Require exact name + grade before the final set-offline confirmation."""

    identity = parse_set_offline_confirmation_identity(value)
    return (
        normalize_contract_text(identity["product_name"])
        == normalize_contract_text(expected_product_name)
        and normalize_contract_grade(identity["grade"])
        == normalize_contract_grade(expected_grade)
    )


def canonical_positive_price(value, require_canonical=False, reject_float=False):
    if isinstance(value, bool) or value is None or (reject_float and isinstance(value, float)):
        raise ValueError("price must be a canonical positive decimal")
    raw = str(value).strip()
    try:
        price = Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("price must be a canonical positive decimal")
    formatted = format(price, ".2f")
    if price <= 0 or (require_canonical and raw != formatted):
        raise ValueError("price must be a canonical positive decimal")
    return formatted


def canonical_json_bytes(payload, default=None):
    options = {
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": (",", ":"),
    }
    if default is not None:
        options["default"] = default
    return json.dumps(payload, **options).encode("utf-8")


def sha256_json(payload, prefixed=True, default=None):
    digest = hashlib.sha256(canonical_json_bytes(payload, default=default)).hexdigest()
    return ("sha256:" + digest) if prefixed else digest


def normalize_order_scan_request(request):
    """Normalize the v6 read-only order request on both trust boundaries."""

    if not isinstance(request, dict):
        raise ValueError("ORDER_SCAN_REQUEST_INVALID")
    if (
        request.get("contract_version") != ORDER_SCAN_CONTRACT_VERSION
        or request.get("schema_version")
        != ORDER_SCAN_REQUEST_SCHEMA_VERSION
    ):
        raise ValueError("ORDER_SCAN_REQUEST_INVALID")
    if str(request.get("execution_mode") or "").strip().upper() != "READ_ONLY":
        raise ValueError("ORDER_SCAN_READ_ONLY_REQUIRED")
    required_text = (
        "automation_run_id",
        "observation_batch_id",
        "execution_attempt_id",
        "platform_name",
        "requested_platform_trade_date",
        "created_at",
        "expires_at",
    )
    normalized = {
        "schema_version": ORDER_SCAN_REQUEST_SCHEMA_VERSION,
        "contract_version": ORDER_SCAN_CONTRACT_VERSION,
        "execution_mode": "READ_ONLY",
    }
    for field in required_text:
        value = str(request.get(field) or "").strip()
        if not value:
            raise ValueError("ORDER_SCAN_REQUEST_INVALID")
        normalized[field] = value
    requested_date = normalized["requested_platform_trade_date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested_date):
        raise ValueError("ORDER_SCAN_REQUEST_INVALID")
    limits = request.get("limits") or {}
    if not isinstance(limits, dict):
        raise ValueError("ORDER_SCAN_REQUEST_INVALID")
    normalized_limits = {}
    for name, default in ORDER_SCAN_DEFAULT_LIMITS.items():
        raw = limits.get(name, default)
        if isinstance(raw, bool) or (
            isinstance(raw, float) and not raw.is_integer()
        ):
            raise ValueError("ORDER_SCAN_REQUEST_INVALID")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError("ORDER_SCAN_REQUEST_INVALID") from None
        if not 1 <= value <= ORDER_SCAN_HARD_LIMITS[name]:
            raise ValueError("ORDER_SCAN_REQUEST_INVALID")
        normalized_limits[name] = value
    normalized["limits"] = normalized_limits
    normalized["window_title"] = str(
        request.get("window_title") or "蚂蚁花团供应商"
    ).strip()
    normalized["applet_uri"] = str(request.get("applet_uri") or "").strip()
    normalized["element_timeout_seconds"] = _bounded_contract_integer(
        request.get("element_timeout_seconds", 15),
        minimum=1,
        maximum=120,
    )
    normalized["applet_launch_timeout_seconds"] = _bounded_contract_integer(
        request.get("applet_launch_timeout_seconds", 20),
        minimum=1,
        maximum=120,
    )
    return normalized


def order_scan_instruction_hash(request):
    return sha256_json(normalize_order_scan_request(request))


def _bounded_contract_integer(value, minimum, maximum):
    if isinstance(value, bool) or (
        isinstance(value, float) and not value.is_integer()
    ):
        raise ValueError("ORDER_SCAN_REQUEST_INVALID")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("ORDER_SCAN_REQUEST_INVALID") from None
    if not minimum <= parsed <= maximum:
        raise ValueError("ORDER_SCAN_REQUEST_INVALID")
    return parsed


V4_RESULT_ITEM_FIELDS = (
    "preflight_row",
    "preflight_price",
    "execution_ordinal",
    "submit_attempted",
    "side_effect_state",
    "preflight_observed_at",
    "submit_intent_at",
    "submit_clicked_at",
    "readback_observed_at",
    "actual_price",
    "status",
    "error_code",
    "error_message",
)

V4_ITEM_BINDING_FIELDS = (
    "item_id",
    "source_task_id",
    "operation_id",
    "item_execution_attempt_id",
    "write_identity_key",
    "page_identity_key",
    "internal_sku",
    "expected_product_name",
    "expected_grade",
    "expected_old_price",
    "target_price",
    "item_payload_sha256",
)

V4_SUBMIT_RISK_PHASES = frozenset(
    {
        "SUBMIT_INTENT_RECORDED",
        "SUBMIT_CLICKED",
        "AFTER_SUBMIT_VERIFY",
        "VERIFIED",
        "FINAL_VERIFICATION",
        "RESULT_WRITTEN",
    }
)


def v4_result_item_skeleton(
    item,
    *,
    status="NOT_ATTEMPTED",
    error_code="",
    error_message="",
):
    result = {name: item.get(name, "") for name in V4_ITEM_BINDING_FIELDS}
    result.update(
        {
            "preflight_row": None,
            "preflight_price": None,
            "execution_ordinal": None,
            "submit_attempted": False,
            "side_effect_state": "NOT_STARTED",
            "preflight_observed_at": None,
            "submit_intent_at": None,
            "submit_clicked_at": None,
            "readback_observed_at": None,
            "actual_price": None,
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
        }
    )
    return result


def v4_result_counts(items):
    counts = {
        "total": len(items),
        "attempted": 0,
        "verified": 0,
        "not_applied": 0,
        "failed": 0,
        "unknown": 0,
        "not_attempted": 0,
    }
    for item in items:
        if item.get("submit_attempted") is True:
            counts["attempted"] += 1
        status = str(item.get("status") or "").lower()
        if status not in counts or status in {"total", "attempted"}:
            raise ValueError("invalid COMMIT result item status")
        counts[status] += 1
    return counts


def derive_v4_batch_semantics(counts):
    """Return the single canonical top-level meaning of a v4 item ledger."""

    total = int(counts.get("total") or 0)
    if total <= 0:
        raise ValueError("v4 result counts must contain at least one item")
    if int(counts.get("verified") or 0) == total:
        batch_status = "VERIFIED"
    elif int(counts.get("unknown") or 0) > 0:
        batch_status = "UNKNOWN"
    elif int(counts.get("verified") or 0) > 0:
        batch_status = "PARTIAL"
    else:
        batch_status = "FAILED"
    if int(counts.get("unknown") or 0) > 0:
        side_effect_state = "UNKNOWN"
    elif int(counts.get("verified") or 0) > 0:
        side_effect_state = "VERIFIED"
    elif int(counts.get("not_applied") or 0) > 0:
        side_effect_state = "NOT_APPLIED"
    else:
        side_effect_state = "NOT_STARTED"
    return {
        "batch_status": batch_status,
        "status": batch_status,
        "run_success_flag": batch_status == "VERIFIED",
        "business_operation_completed": int(counts.get("attempted") or 0) > 0,
        "side_effect_state": side_effect_state,
    }


def v5_result_counts(items):
    """Count v5 item outcomes using the worker-safe shared contract."""

    if not isinstance(items, list) or not items:
        raise ValueError("v5 result items must be a non-empty list")
    valid_outcomes = {
        "VERIFIED",
        "ALREADY_APPLIED",
        "NOT_APPLIED",
        "PARTIALLY_APPLIED",
        "NEEDS_RECONCILIATION",
        "FAILED",
        "NOT_ATTEMPTED",
    }
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
        if outcome not in valid_outcomes:
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
    """Derive the only authoritative v5 batch terminal semantics."""

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
        "business_operation_completed": int(
            counts.get("attempted_count") or 0
        )
        > 0,
        "side_effect_state": side_effect_state,
        "requires_manual_review": requires_manual_review,
        "reconciliation_pending": unknown > 0,
        "partial_effect_count": partial_effect,
    }


def v4_phase_matches_request(request, phase, request_file_sha256):
    if not isinstance(phase, dict):
        return False
    expected = {
        "schema_version": "shadowbot-commit-batch-phase-1.0",
        "contract_version": 4,
        "batch_id": str(request.get("batch_id") or ""),
        "execution_attempt_id": str(request.get("execution_attempt_id") or ""),
        "instruction_hash": str(request.get("instruction_hash") or ""),
        "manifest_sha256": str(request.get("manifest_sha256") or ""),
        "request_file_sha256": str(request_file_sha256 or ""),
    }
    return all(phase.get(name) == value for name, value in expected.items())


def _v4_item_binding_matches(expected, supplied):
    return isinstance(supplied, dict) and all(
        str(supplied.get(name) or "") == str(expected.get(name) or "")
        for name in V4_ITEM_BINDING_FIELDS
    )


def _v4_unknown_items(request, error_code, error_message):
    items = []
    for item in request.get("items") or []:
        recovered = v4_result_item_skeleton(
            item,
            status="UNKNOWN",
            error_code=error_code,
            error_message=error_message,
        )
        recovered["submit_attempted"] = True
        recovered["side_effect_state"] = "UNKNOWN"
        items.append(recovered)
    return items


def build_v4_recovery_result(
    request,
    phase,
    *,
    request_file_sha256,
    recovered_at,
    worker_id="",
    error_code="WORKER_INTERRUPTED",
    error_message="v4 execution interrupted",
):
    """Build a fail-closed v4 result from a bound durable phase snapshot."""

    if not v4_phase_matches_request(request, phase, request_file_sha256):
        items = _v4_unknown_items(
            request,
            "PHASE_UNAVAILABLE_SIDE_EFFECT_UNKNOWN",
            "phase is missing, damaged, or not bound to the v4 request",
        )
        phase_name = "PHASE_UNAVAILABLE"
        page_snapshot = None
    else:
        phase_name = str(phase.get("phase") or "CLAIMED").upper()
        items = [v4_result_item_skeleton(item) for item in request.get("items") or []]
        by_task_id = {str(item["source_task_id"]): item for item in items}
        expected_by_task_id = {
            str(item["source_task_id"]): item for item in request.get("items") or []
        }
        snapshot = phase.get("batch_result_snapshot")
        if not isinstance(snapshot, dict):
            candidate = phase.get("result_snapshot")
            if isinstance(candidate, dict) and candidate.get("contract_version") == 4:
                snapshot = candidate
        snapshot_valid = snapshot is None or (
            isinstance(snapshot, dict)
            and str(snapshot.get("batch_id") or "") == str(request.get("batch_id") or "")
            and str(snapshot.get("execution_attempt_id") or "")
            == str(request.get("execution_attempt_id") or "")
            and str(snapshot.get("instruction_hash") or "")
            == str(request.get("instruction_hash") or "")
            and str(snapshot.get("manifest_sha256") or "")
            == str(request.get("manifest_sha256") or "")
        )
        if not snapshot_valid:
            items = _v4_unknown_items(
                request,
                "PHASE_SNAPSHOT_BINDING_INVALID",
                "phase result snapshot is not bound to the v4 request",
            )
            by_task_id = {str(item["source_task_id"]): item for item in items}
            snapshot = None
        elif isinstance(snapshot, dict):
            for snapshot_item in snapshot.get("items") or []:
                task_id = str(
                    snapshot_item.get("source_task_id") or ""
                ) if isinstance(snapshot_item, dict) else ""
                target = by_task_id.get(task_id)
                expected = expected_by_task_id.get(task_id)
                if target is None or expected is None:
                    continue
                if not _v4_item_binding_matches(expected, snapshot_item):
                    items = _v4_unknown_items(
                        request,
                        "PHASE_SNAPSHOT_BINDING_INVALID",
                        "phase item snapshot is not bound to the v4 request",
                    )
                    by_task_id = {
                        str(item["source_task_id"]): item for item in items
                    }
                    snapshot = None
                    break
                for name in V4_RESULT_ITEM_FIELDS:
                    if name in snapshot_item:
                        target[name] = snapshot_item[name]
        page_snapshot = (
            json.loads(json.dumps(snapshot["page_snapshot"], ensure_ascii=False))
            if isinstance(snapshot, dict)
            and isinstance(snapshot.get("page_snapshot"), dict)
            else None
        )
        current_task_id = str(phase.get("current_source_task_id") or "")
        current = by_task_id.get(current_task_id)
        item_phase = phase.get("item_phase")
        if current is not None and isinstance(item_phase, dict):
            expected = expected_by_task_id[current_task_id]
            if not _v4_item_binding_matches(expected, item_phase):
                items = _v4_unknown_items(
                    request,
                    "PHASE_ITEM_BINDING_INVALID",
                    "phase current item is not bound to the v4 request",
                )
                current = None
            else:
                if not current.get("execution_ordinal") and phase.get(
                    "execution_ordinal"
                ):
                    current["execution_ordinal"] = int(phase["execution_ordinal"])
                for name in (
                    "status",
                    "submit_attempted",
                    "side_effect_state",
                    "submit_intent_at",
                    "submit_clicked_at",
                    "readback_observed_at",
                    "actual_price",
                    "error_code",
                    "error_message",
                ):
                    if item_phase.get(name) not in (None, ""):
                        current[name] = item_phase[name]
                child_snapshot = phase.get("result_snapshot")
                child_snapshot_bound = (
                    isinstance(child_snapshot, dict)
                    and str(child_snapshot.get("task_id") or "")
                    == str(expected.get("source_task_id") or "")
                    and str(child_snapshot.get("operation_id") or "")
                    == str(expected.get("operation_id") or "")
                    and str(child_snapshot.get("execution_attempt_id") or "")
                    == str(expected.get("item_execution_attempt_id") or "")
                    and str(child_snapshot.get("instruction_hash") or "")
                    == str(expected.get("item_payload_sha256") or "")
                )
                if child_snapshot_bound:
                    for name in (
                        "status",
                        "side_effect_state",
                        "submit_intent_at",
                        "submit_clicked_at",
                        "readback_observed_at",
                        "actual_price",
                        "error_code",
                        "error_message",
                    ):
                        if child_snapshot.get(name) not in (None, ""):
                            current[name] = child_snapshot[name]
        if current is not None:
            current_status = str(current.get("status") or "").upper()
            current_side_effect = str(
                current.get("side_effect_state") or "NOT_STARTED"
            ).upper()
            if (
                current_status == "VERIFIED"
                and current_side_effect == "VERIFIED"
                and str(current.get("actual_price") or "")
                == str(current.get("target_price") or "")
                and current.get("submit_intent_at")
                and current.get("submit_clicked_at")
                and current.get("readback_observed_at")
            ):
                current["submit_attempted"] = True
            elif (
                current_status == "NOT_APPLIED"
                and current_side_effect == "NOT_APPLIED"
            ):
                current["submit_attempted"] = bool(
                    current.get("submit_clicked_at")
                )
            elif current_status == "UNKNOWN":
                current["submit_attempted"] = True
                current["side_effect_state"] = "UNKNOWN"
        if current is not None and str(current.get("status") or "").upper() not in {
            "VERIFIED",
            "NOT_APPLIED",
            "UNKNOWN",
        }:
            side_effect = str(
                (item_phase or {}).get("side_effect_state")
                or phase.get("side_effect_state")
                or "NOT_STARTED"
            ).upper()
            if phase_name in V4_SUBMIT_RISK_PHASES or side_effect != "NOT_STARTED":
                current.update(
                    {
                        "submit_attempted": True,
                        "side_effect_state": "UNKNOWN",
                        "status": "UNKNOWN",
                        "error_code": "SUBMIT_RESULT_UNKNOWN",
                        "error_message": error_message,
                    }
                )
        if (
            not isinstance(snapshot, dict)
            and current is None
            and phase_name not in {
                "CLAIMED",
                "UI_STARTED",
                "PREFLIGHT_VALIDATED",
                "LOGIN_VERIFICATION_REQUIRED",
            }
        ):
            items = _v4_unknown_items(
                request,
                "PHASE_INSUFFICIENT_SIDE_EFFECT_UNKNOWN",
                "bound phase has no sufficient item ledger",
            )

    counts = v4_result_counts(items)
    semantics = derive_v4_batch_semantics(counts)
    result = {
        "schema_version": "shadowbot-commit-batch-result-1.1",
        "contract_version": 4,
        "task_id": request.get("task_id", ""),
        "operation_id": request.get("operation_id", ""),
        "execution_attempt_id": request.get("execution_attempt_id", ""),
        "execution_mode": "COMMIT",
        "batch_id": request.get("batch_id", ""),
        "manifest_sha256": request.get("manifest_sha256", ""),
        "instruction_hash": request.get("instruction_hash", ""),
        "request_file_sha256": request_file_sha256,
        "worker_id": (
            worker_id
            or (phase.get("worker_id", "") if isinstance(phase, dict) else "")
        ),
        **semantics,
        "error_code": (
            "SUBMIT_RESULT_UNKNOWN" if counts["unknown"] else error_code
        ),
        "error_message": error_message,
        "retryable": False,
        "recovered_phase": phase_name,
        "items": items,
        "counts": counts,
        "ended_at": recovered_at,
    }
    if page_snapshot is not None:
        result["page_snapshot"] = page_snapshot
    return result
