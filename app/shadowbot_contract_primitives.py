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
