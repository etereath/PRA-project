from __future__ import annotations

import hashlib
import json
import msvcrt
import os
import re
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from app.emergency_offline_fence import (
        validate_emergency_authorization_binding,
    )
except ImportError:
    try:
        from .emergency_offline_fence import (
            validate_emergency_authorization_binding,
        )
    except ImportError:
        from emergency_offline_fence import (
            validate_emergency_authorization_binding,
        )

try:
    from app.shadowbot_contract_primitives import (
        ORDER_SCAN_CONTRACT_VERSION,
        ORDER_SCAN_RESULT_SCHEMA_VERSION,
        build_order_scan_failure_result,
        build_v4_recovery_result,
        canonical_positive_price,
        derive_v4_batch_semantics,
        derive_v5_batch_semantics,
        normalize_contract_grade,
        normalize_contract_sku,
        normalize_contract_text,
        normalize_order_scan_request,
        order_scan_instruction_hash,
        sha256_json,
        v4_result_counts,
        v4_result_item_skeleton,
        v5_result_counts,
    )
except ImportError:
    try:
        from .shadowbot_contract_primitives import (
            ORDER_SCAN_CONTRACT_VERSION,
            ORDER_SCAN_RESULT_SCHEMA_VERSION,
            build_order_scan_failure_result,
            build_v4_recovery_result,
            canonical_positive_price,
            derive_v4_batch_semantics,
            derive_v5_batch_semantics,
            normalize_contract_grade,
            normalize_contract_sku,
            normalize_contract_text,
            normalize_order_scan_request,
            order_scan_instruction_hash,
            sha256_json,
            v4_result_counts,
            v4_result_item_skeleton,
            v5_result_counts,
        )
    except ImportError:
        from shadowbot_contract_primitives import (
            ORDER_SCAN_CONTRACT_VERSION,
            ORDER_SCAN_RESULT_SCHEMA_VERSION,
            build_order_scan_failure_result,
            build_v4_recovery_result,
            canonical_positive_price,
            derive_v4_batch_semantics,
            derive_v5_batch_semantics,
            normalize_contract_grade,
            normalize_contract_sku,
            normalize_contract_text,
            normalize_order_scan_request,
            order_scan_instruction_hash,
            sha256_json,
            v4_result_counts,
            v4_result_item_skeleton,
            v5_result_counts,
        )


SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "CREDENTIAL_TARGET_MISSING",
        "CREDENTIAL_MANAGER_UNAVAILABLE",
        "CREDENTIAL_NOT_FOUND",
        "CREDENTIAL_ACCESS_DENIED",
        "CREDENTIAL_FORMAT_INVALID",
        "CREDENTIAL_READ_FAILED",
    }
)


INSTRUCTION_HASH_FIELDS = (
    "task_id",
    "operation_id",
    "execution_attempt_id",
    "execution_mode",
    "platform_name",
    "platform_sku",
    "product_keyword",
    "expected_product_name",
    "expected_grade",
    "expected_spec",
    "spec_verification_required",
    "expected_old_price",
    "target_price",
    "applet_uri",
)

V2_CONTRACT_VERSION = 2
V2_DEFAULT_LIMITS = {"max_pages": 20, "max_scrolls": 100, "max_seconds": 300}
V2_HARD_LIMITS = {"max_pages": 100, "max_scrolls": 500, "max_seconds": 900}
V2_HARD_MAX_PRODUCTS = 50
V2_MAX_REQUEST_BYTES = 256 * 1024
V2_MAX_RESULT_BYTES = 4 * 1024 * 1024
V4_CONTRACT_VERSION = 4
V4_SCHEMA_VERSION = "shadowbot-commit-batch-request-1.2"
V4_RESULT_SCHEMA_VERSION = "shadowbot-commit-batch-result-1.1"
V4_PHASE_SCHEMA_VERSION = "shadowbot-commit-batch-phase-1.0"
V4_DEVELOPMENT_FAULT_INJECTIONS = frozenset({"AFTER_SUBMIT_CLICK_UNKNOWN"})
V5_CONTRACT_VERSION = 5
V5_MANIFEST_SCHEMA_VERSION = "shadowbot-listing-action-manifest-1.0"
V5_REQUEST_SCHEMA_VERSION = "shadowbot-listing-action-batch-request-1.0"
V5_RESULT_SCHEMA_VERSION = "shadowbot-listing-action-batch-result-1.0"
V5_PHASE_SCHEMA_VERSION = "shadowbot-listing-action-batch-phase-1.0"
V5_DEVELOPMENT_FAULT_INJECTIONS = frozenset(
    {"AFTER_ACTION_CLICK_UNKNOWN"}
)


def _v6_validate_request(request):
    normalized = normalize_order_scan_request(request)
    if request.get("instruction_hash") != order_scan_instruction_hash(
        request
    ):
        raise ValueError("ORDER_SCAN_INSTRUCTION_HASH_MISMATCH")
    if str(request.get("platform_name") or "") != normalized["platform_name"]:
        raise ValueError("ORDER_SCAN_REQUEST_INVALID")
    expires_at = datetime.fromisoformat(str(request["expires_at"]))
    if (
        expires_at.tzinfo is None
        or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc)
    ):
        raise ValueError("request expired")


def _v6_failed_result(
    request,
    request_sha256,
    *,
    worker_id,
    error_code,
    error_message,
):
    return build_order_scan_failure_result(
        request,
        request_sha256,
        worker_id=worker_id,
        error_code=error_code,
        error_message=error_message,
        observed_at=_now_iso(),
    )


def _v2_normalize_text(value):
    return normalize_contract_text(value)


def _v2_normalize_sku(value):
    return normalize_contract_sku(value)


def _v2_normalize_request(request):
    if request.get("contract_version") != V2_CONTRACT_VERSION:
        raise ValueError("UNKNOWN_CONTRACT_VERSION")
    execution_mode = str(request.get("execution_mode") or "").strip().upper()
    if execution_mode != "READ_ONLY":
        raise ValueError("READ_ONLY_REQUIRED")
    read_batch_id = str(request.get("read_batch_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", read_batch_id):
        raise ValueError("invalid read_batch_id")
    products = request.get("products", [])
    if not isinstance(products, list) or len(products) > V2_HARD_MAX_PRODUCTS:
        raise ValueError("PRODUCT_COUNT_LIMIT_EXCEEDED")
    identities = set()
    item_ids = set()
    platforms = set()
    normalized_products = []
    for product in products:
        if not isinstance(product, dict):
            raise ValueError("invalid product target")
        item_id = str(product.get("item_id") or "").strip()
        platform = str(product.get("platform") or "").strip()
        name = str(product.get("expected_product_name") or "").strip()
        grade = str(product.get("expected_grade") or "").strip()
        if not item_id or not platform or not name or not grade or item_id in item_ids:
            raise ValueError("invalid or duplicate product target")
        sku = _v2_normalize_sku(product.get("platform_sku"))
        identity = (
            f"{_v2_normalize_text(platform)}|sku:{sku}"
            if sku
            else f"{_v2_normalize_text(platform)}|name:{_v2_normalize_text(name)}|grade:{normalize_contract_grade(grade)}"
        )
        if identity in identities:
            raise ValueError("DUPLICATE_TARGET_IDENTITY")
        item_ids.add(item_id)
        identities.add(identity)
        platforms.add(_v2_normalize_text(platform))
        normalized_product = {
            "item_id": item_id,
            "platform": platform,
            "platform_sku": sku,
            "expected_product_name": name,
            "expected_grade": grade,
        }
        normalized_products.append(normalized_product)
    platform_name = str(request.get("platform_name") or "").strip()
    if not platform_name and normalized_products:
        platform_name = normalized_products[0]["platform"]
    if not platform_name:
        raise ValueError("platform_name is required")
    if len(platforms) > 1 or (platforms and _v2_normalize_text(platform_name) not in platforms):
        raise ValueError("SINGLE_PLATFORM_REQUIRED")
    raw_limits = request.get("limits") or {}
    if not isinstance(raw_limits, dict):
        raise ValueError("invalid limits")
    limits = {}
    for name, default in V2_DEFAULT_LIMITS.items():
        raw_value = raw_limits.get(name, default)
        if isinstance(raw_value, bool) or (isinstance(raw_value, float) and not raw_value.is_integer()):
            raise ValueError(f"{name} must be an integer")
        value = int(raw_value)
        if not 1 <= value <= V2_HARD_LIMITS[name]:
            raise ValueError(f"{name.upper()}_LIMIT_EXCEEDED")
        limits[name] = value
    normalized = {
        "contract_version": V2_CONTRACT_VERSION,
        "execution_mode": execution_mode,
        "read_batch_id": read_batch_id,
        "platform_name": platform_name,
        "capture_evidence": _as_bool(request.get("capture_evidence", False)),
        "products": normalized_products,
        "limits": limits,
    }
    return normalized


def _v2_instruction_hash(request):
    canonical = {
        "task_id": str(request.get("task_id") or ""),
        "operation_id": str(request.get("operation_id") or ""),
        "execution_attempt_id": str(request.get("execution_attempt_id") or ""),
        "request": _v2_normalize_request(request),
        "applet_uri": str(request.get("applet_uri") or ""),
        "window_title": str(request.get("window_title") or ""),
    }
    return sha256_json(canonical)


def _v4_sha256(payload):
    return sha256_json(payload)


def _v4_price(value, name):
    try:
        return canonical_positive_price(value, require_canonical=True)
    except ValueError:
        raise ValueError(name + " must be a canonical positive price")


def _v4_item_payload(platform_name, item):
    return {
        "platform_name": str(platform_name or "").strip(),
        "source_task_id": str(item.get("source_task_id") or "").strip(),
        "internal_sku": str(item.get("internal_sku") or "").strip().upper(),
        "expected_product_name": _v2_normalize_text(
            item.get("expected_product_name")
        ),
        "expected_grade": normalize_contract_grade(item.get("expected_grade")),
        "expected_old_price": _v4_price(item.get("expected_old_price"), "expected_old_price"),
        "target_price": _v4_price(item.get("target_price"), "target_price"),
    }


def _v4_manifest_hash(platform_name, items):
    payloads = sorted(
        (_v4_item_payload(platform_name, item) for item in items),
        key=lambda item: (item["source_task_id"], item["internal_sku"]),
    )
    return _v4_sha256({"items": payloads})


def _v4_instruction_hash(request):
    canonical = {
        "schema_version": request.get("schema_version"),
        "contract_version": request.get("contract_version"),
        "execution_profile": request.get("execution_profile"),
        "task_id": request.get("task_id"),
        "operation_id": request.get("operation_id"),
        "execution_attempt_id": request.get("execution_attempt_id"),
        "execution_mode": request.get("execution_mode"),
        "batch_id": request.get("batch_id"),
        "platform_name": request.get("platform_name"),
        "items": request.get("items"),
        "manifest_sha256": request.get("manifest_sha256"),
        "development_confirmation": request.get("development_confirmation"),
        "applet_uri": request.get("applet_uri"),
        "window_title": request.get("window_title"),
        "capture_evidence": request.get("capture_evidence"),
        "created_at": request.get("created_at"),
        "expires_at": request.get("expires_at"),
    }
    if "fault_injection" in request:
        canonical["fault_injection"] = request.get("fault_injection")
    return _v4_sha256(canonical)


def _v4_stable_id(prefix, payload):
    return prefix + "-" + _v4_sha256(payload).replace("sha256:", "", 1)[:32]


def _v4_write_identity_key(platform_name, internal_sku):
    return "|".join(
        (
            _v2_normalize_text(platform_name),
            str(internal_sku or "").strip().upper(),
        )
    )


def _v4_page_identity_key(platform_name, product_name, grade):
    return "|".join(
        (
            _v2_normalize_text(platform_name),
            _v2_normalize_text(product_name),
            normalize_contract_grade(grade),
        )
    )


def _v4_result_item_skeleton(item, *, status="NOT_ATTEMPTED", error_code="", error_message=""):
    return v4_result_item_skeleton(
        item,
        status=status,
        error_code=error_code,
        error_message=error_message,
    )


def _v4_result_counts(items):
    return v4_result_counts(items)


def _v4_batch_status(counts):
    return derive_v4_batch_semantics(counts)["batch_status"]


def _v5_manifest_hash(request):
    manifest = {
        "schema_version": V5_MANIFEST_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "batch_id": request.get("batch_id"),
        "action_type": request.get("action_type"),
        "execution_mode": request.get("execution_mode"),
        "platform_name": request.get("platform_name"),
        "mapping_source_version": request.get("mapping_source_version"),
        "scan_scope": request.get("scan_scope"),
        "items": sorted(
            [
                {
                    name: item.get(name)
                    for name in (
                        "item_id",
                        "source_task_id",
                        "internal_sku",
                        "expected_product_name",
                        "expected_grade",
                        "action_type",
                        "expected_old_status",
                        "target_status",
                        "target_price",
                        "target_inventory",
                        "task_expires_at",
                        "item_payload_sha256",
                        "operation_id",
                        "write_identity_key",
                        "page_identity_key",
                    )
                    if name in item
                }
                for item in request.get("items") or []
            ],
            key=lambda item: (
                str(item.get("source_task_id") or ""),
                str(item.get("internal_sku") or ""),
            ),
        ),
    }
    return sha256_json(manifest)


def _v5_instruction_hash(request):
    fields = (
        "schema_version",
        "contract_version",
        "execution_profile",
        "execution_mode",
        "action_type",
        "task_id",
        "operation_id",
        "execution_attempt_id",
        "batch_id",
        "platform_name",
        "mapping_source_version",
        "scan_scope",
        "items",
        "manifest_sha256",
        "gate_summary",
        "development_confirmation",
        "applet_uri",
        "window_title",
        "capture_evidence",
        "fault_injection",
        "fault_injection_item_ordinal",
        "source_execution_attempt_id",
        "source_result_id",
        "emergency_authorization",
        "created_at",
        "expires_at",
    )
    return sha256_json(
        {name: request.get(name) for name in fields if name in request}
    )


def _v5_validate_request(request):
    if (
        request.get("contract_version") != V5_CONTRACT_VERSION
        or request.get("schema_version") != V5_REQUEST_SCHEMA_VERSION
    ):
        raise ValueError("LISTING_ACTION_REQUEST_INVALID")
    action_type = str(request.get("action_type") or "").strip().lower()
    if action_type not in {"sync_status", "set_online", "set_offline"}:
        raise ValueError("UNSUPPORTED_V5_ACTION")
    execution_mode = str(request.get("execution_mode") or "").upper()
    allowed_modes = (
        {"READ_ONLY"}
        if action_type == "sync_status"
        else {"COMMIT", "RECONCILE"}
    )
    if execution_mode not in allowed_modes:
        raise ValueError("LISTING_ACTION_EXECUTION_MODE_INVALID")
    expected_scope = (
        "online_and_waiting"
        if action_type in {"sync_status", "set_online"}
        else "online"
    )
    if request.get("scan_scope") != expected_scope:
        raise ValueError("LISTING_ACTION_SCAN_SCOPE_INVALID")
    items = request.get("items")
    if action_type == "sync_status":
        if items != []:
            raise ValueError("SYNC_STATUS_ITEMS_FORBIDDEN")
        for forbidden in (
            "task_id",
            "operation_id",
            "gate_summary",
            "development_confirmation",
            "emergency_authorization",
        ):
            if forbidden in request:
                raise ValueError("SYNC_STATUS_WRITE_FIELD_FORBIDDEN")
    else:
        if not isinstance(items, list) or not items:
            raise ValueError("LISTING_ACTION_ITEMS_REQUIRED")
        if not str(request.get("task_id") or "").strip():
            raise ValueError("invalid task_id")
        if not str(request.get("operation_id") or "").strip():
            raise ValueError("invalid operation_id")
        if execution_mode == "COMMIT":
            gate_summary = request.get("gate_summary")
            gate_items = (
                gate_summary.get("items")
                if isinstance(gate_summary, dict)
                else None
            )
            if (
                not isinstance(gate_items, list)
                or len(gate_items) != len(items)
                or any(
                    gate.get("decision")
                    not in {"EXECUTE", "ALREADY_APPLIED"}
                    or gate.get("lock_status") != "ACTIVE"
                    or gate.get("block_reasons") != []
                    for gate in gate_items
                )
            ):
                raise ValueError("LISTING_ACTION_GATE_INVALID")
            if (
                str(request.get("execution_profile") or "").lower()
                == "development"
            ):
                expected = (
                    "确认授权批次 %s 以上%d项真实COMMIT"
                    % (request.get("batch_id"), len(items))
                )
                confirmation = request.get(
                    "development_confirmation"
                )
                if (
                    not isinstance(confirmation, dict)
                    or confirmation.get("confirmation_text") != expected
                    or not str(
                        confirmation.get("confirmed_by") or ""
                    ).strip()
                ):
                    raise ValueError(
                        "LISTING_ACTION_CONFIRMATION_INVALID"
                    )
            elif "development_confirmation" in request:
                raise ValueError("PRODUCTION_CONFIRMATION_FORBIDDEN")
            if (
                "source_execution_attempt_id" in request
                or "source_result_id" in request
            ):
                raise ValueError("LISTING_ACTION_RECONCILE_SOURCE_FORBIDDEN")
            emergency = request.get("emergency_authorization")
            if emergency is not None:
                validate_emergency_authorization_binding(emergency)
                if (
                    action_type != "set_offline"
                    or len(items) != 1
                    or emergency.get("source_task_id")
                    != items[0].get("source_task_id")
                    or emergency.get("platform_name")
                    != request.get("platform_name")
                    or emergency.get("internal_sku")
                    != items[0].get("internal_sku")
                ):
                    raise ValueError("EMERGENCY_AUTHORIZATION_SCOPE_MISMATCH")
        else:
            if len(items) != 1:
                raise ValueError("LISTING_ACTION_RECONCILE_ITEM_COUNT_INVALID")
            for forbidden in (
                "gate_summary",
                "development_confirmation",
                "fault_injection",
                "fault_injection_item_ordinal",
                "emergency_authorization",
            ):
                if forbidden in request:
                    raise ValueError("LISTING_ACTION_RECONCILE_WRITE_FIELD_FORBIDDEN")
            if not str(
                request.get("source_execution_attempt_id") or ""
            ).strip() or not str(request.get("source_result_id") or "").strip():
                raise ValueError("LISTING_ACTION_RECONCILE_SOURCE_REQUIRED")
            if request.get("task_id") != items[0].get("source_task_id"):
                raise ValueError("LISTING_ACTION_RECONCILE_TASK_MISMATCH")
            if request.get("operation_id") != items[0].get("operation_id"):
                raise ValueError("LISTING_ACTION_RECONCILE_OPERATION_MISMATCH")
    fault_injection = str(request.get("fault_injection") or "").strip()
    fault_ordinal = request.get("fault_injection_item_ordinal")
    if fault_injection:
        if (
            str(request.get("execution_profile") or "").lower()
            != "development"
            or execution_mode != "COMMIT"
            or action_type != "set_offline"
            or fault_injection not in V5_DEVELOPMENT_FAULT_INJECTIONS
            or request.get("fault_injection") != fault_injection.upper()
        ):
            raise ValueError("UNSAFE_TEST_PARAMETER_REJECTED")
        if (
            isinstance(fault_ordinal, bool)
            or not isinstance(fault_ordinal, int)
            or not 1 <= fault_ordinal <= len(items)
        ):
            raise ValueError("UNSAFE_TEST_PARAMETER_REJECTED")
    elif fault_ordinal is not None:
        raise ValueError("UNSAFE_TEST_PARAMETER_REJECTED")
    for name in (
        "execution_attempt_id",
        "batch_id",
        "platform_name",
        "mapping_source_version",
        "manifest_sha256",
        "instruction_hash",
        "created_at",
        "expires_at",
    ):
        if not str(request.get(name) or "").strip():
            raise ValueError("invalid " + name)
    if request.get("manifest_sha256") != _v5_manifest_hash(request):
        raise ValueError("LISTING_ACTION_MANIFEST_HASH_MISMATCH")
    if request.get("instruction_hash") != _v5_instruction_hash(request):
        raise ValueError("LISTING_ACTION_INSTRUCTION_HASH_MISMATCH")
    expires_at = datetime.fromisoformat(str(request["expires_at"]))
    if (
        expires_at.tzinfo is None
        or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc)
    ):
        raise ValueError("request expired")


def _v5_validate_sync_request(request):
    """Backward-compatible entrypoint retained for the v5 sync unit tests."""

    _v5_validate_request(request)
    if request.get("action_type") != "sync_status":
        raise ValueError("UNSUPPORTED_V5_ACTION")


def _v5_failed_sync_result(
    request,
    request_sha256,
    *,
    worker_id,
    error_code,
    error_message,
):
    observed_at = _now_iso()
    result_id = _v4_stable_id(
        "RESULT",
        {
            "batch_id": request.get("batch_id"),
            "execution_attempt_id": request.get("execution_attempt_id"),
        },
    )
    snapshot_id = _v4_stable_id(
        "SNAPSHOT",
        {
            "batch_id": request.get("batch_id"),
            "execution_attempt_id": request.get("execution_attempt_id"),
        },
    )
    snapshot = {
        "schema_version": "shadowbot-listing-sync-snapshot-1.0",
        "snapshot_id": snapshot_id,
        "platform_name": request.get("platform_name", ""),
        "execution_attempt_id": request.get("execution_attempt_id", ""),
        "mapping_source_version": request.get("mapping_source_version", ""),
        "result_id": result_id,
        "scan_started_at": observed_at,
        "scan_completed_at": observed_at,
        "online_scan_started_at": observed_at,
        "online_scan_completed_at": observed_at,
        "waiting_scan_started_at": observed_at,
        "waiting_scan_completed_at": observed_at,
        "online_scan_complete": False,
        "waiting_scan_complete": False,
        "online_end_marker_verified": False,
        "waiting_end_marker_verified": False,
        "snapshot_complete": False,
        "instruction_hash": request.get("instruction_hash", ""),
        "status": "FAILED",
        "error_code": str(error_code or "WORKER_EXECUTION_FAILED"),
        "evidence_manifest_sha256": sha256_json([]),
        "items": [],
    }
    result = {
        "schema_version": V5_RESULT_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "action_type": "sync_status",
        "batch_id": request.get("batch_id", ""),
        "execution_attempt_id": request.get("execution_attempt_id", ""),
        "execution_mode": "READ_ONLY",
        "manifest_sha256": request.get("manifest_sha256", ""),
        "instruction_hash": request.get("instruction_hash", ""),
        "request_file_sha256": "sha256:" + request_sha256,
        "worker_id": worker_id,
        "queue_phase": "RESULT_WRITTEN",
        "worker_heartbeat_at": observed_at,
        "result_id": result_id,
        "started_at": observed_at,
        "ended_at": observed_at,
        "status": "FAILED",
        "run_success_flag": False,
        "business_operation_completed": False,
        "side_effect_state": "NOT_STARTED",
        "error_code": str(error_code or "WORKER_EXECUTION_FAILED"),
        "error_message": str(error_message or "")[:1000],
        "retryable": False,
        "snapshot": snapshot,
    }
    result["result_payload_sha256"] = sha256_json(dict(result))
    return result


def _v5_failed_result(
    request,
    request_sha256,
    *,
    worker_id,
    error_code,
    error_message,
    phase_data=None,
):
    if request.get("action_type") == "sync_status":
        return _v5_failed_sync_result(
            request,
            request_sha256,
            worker_id=worker_id,
            error_code=error_code,
            error_message=error_message,
        )
    observed_at = _now_iso()
    phase_data = phase_data if isinstance(phase_data, dict) else {}
    phase_name = str(phase_data.get("phase") or "").strip().upper()
    current_operation = str(
        (phase_data.get("current_item") or {}).get("operation_id") or ""
    )
    phase_items = {
        str(item.get("operation_id") or ""): item
        for item in phase_data.get("item_states") or []
        if isinstance(item, dict)
    }
    is_reconcile = (
        str(request.get("execution_mode") or "").upper()
        == "RECONCILE"
    )
    items = []
    for request_item in request.get("items") or []:
        operation_id = str(request_item.get("operation_id") or "")
        phase_item = phase_items.get(operation_id, {})
        is_current = (
            not current_operation
            or current_operation == operation_id
        )
        recorded_outcome = str(
            phase_item.get("operation_result") or ""
        ).strip().upper()
        if is_reconcile:
            outcome = "NEEDS_RECONCILIATION"
            detail_effect = "UNKNOWN"
            listing_effect = "UNKNOWN"
            action_clicked = False
            detail_clicked = False
        elif recorded_outcome in {"VERIFIED", "ALREADY_APPLIED"}:
            outcome = recorded_outcome
            detail_effect = str(
                phase_item.get("detail_effect_state") or "NOT_APPLIED"
            ).upper()
            listing_effect = str(
                phase_item.get("listing_effect_state") or "VERIFIED"
            ).upper()
            action_clicked = bool(phase_item.get("action_confirm_clicked"))
            detail_clicked = bool(phase_item.get("detail_save_clicked"))
        elif is_current and phase_name in {
            "ACTION_INTENT_RECORDED",
            "ACTION_CLICKED",
        }:
            outcome = "NEEDS_RECONCILIATION"
            detail_effect = str(
                phase_item.get("detail_effect_state")
                or phase_data.get("detail_effect_state")
                or "UNKNOWN"
            ).upper()
            listing_effect = "UNKNOWN"
            action_clicked = (
                phase_name == "ACTION_CLICKED"
                or bool(phase_item.get("action_confirm_clicked"))
            )
            detail_clicked = bool(phase_item.get("detail_save_clicked"))
        elif is_current and phase_name == "DETAIL_SAVE_INTENT_RECORDED":
            outcome = "NEEDS_RECONCILIATION"
            detail_effect = "UNKNOWN"
            listing_effect = "NOT_STARTED"
            action_clicked = False
            detail_clicked = bool(phase_item.get("detail_save_clicked"))
        elif bool(phase_item.get("detail_save_clicked")):
            detail_effect = str(
                phase_item.get("detail_effect_state")
                or phase_data.get("detail_effect_state")
                or "UNKNOWN"
            ).upper()
            outcome = (
                "PARTIALLY_APPLIED"
                if detail_effect == "VERIFIED"
                else "NEEDS_RECONCILIATION"
            )
            listing_effect = "NOT_STARTED"
            action_clicked = False
            detail_clicked = True
        else:
            outcome = "NOT_APPLIED" if is_current else "NOT_ATTEMPTED"
            detail_effect = "NOT_APPLIED" if is_current else "NOT_STARTED"
            listing_effect = "NOT_APPLIED" if is_current else "NOT_STARTED"
            action_clicked = False
            detail_clicked = False
        items.append(
            {
                name: request_item.get(name)
                for name in (
                    "source_task_id",
                    "operation_id",
                    "item_execution_attempt_id",
                    "internal_sku",
                    "item_payload_sha256",
                )
            }
            | {
                "operation_result": outcome,
                "detail_effect_state": detail_effect,
                "listing_effect_state": listing_effect,
                "detail_save_clicked": detail_clicked,
                "action_confirm_clicked": action_clicked,
                "error_code": str(error_code or "WORKER_EXECUTION_FAILED"),
                "error_message": str(error_message or "")[:1000],
            }
        )
        for name in (
            "observed_price_before_action",
            "observed_inventory_before_action",
            "observed_price_after_detail_save",
            "observed_inventory_after_detail_save",
            "actual_price",
            "actual_inventory",
            "detail_save_clicked_at",
            "action_clicked_at",
            "readback_observed_at",
        ):
            if name in phase_item:
                items[-1][name] = phase_item.get(name)
    counts = v5_result_counts(items)
    semantics = derive_v5_batch_semantics(counts)
    result = {
        "schema_version": V5_RESULT_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "action_type": request.get("action_type", ""),
        "batch_id": request.get("batch_id", ""),
        "execution_attempt_id": request.get("execution_attempt_id", ""),
        "execution_mode": request.get("execution_mode", "COMMIT"),
        "manifest_sha256": request.get("manifest_sha256", ""),
        "instruction_hash": request.get("instruction_hash", ""),
        "request_file_sha256": "sha256:" + request_sha256,
        "worker_id": worker_id,
        "queue_phase": "RESULT_WRITTEN",
        "worker_heartbeat_at": observed_at,
        "result_id": _v4_stable_id(
            "RESULT",
            {
                "batch_id": request.get("batch_id"),
                "execution_attempt_id": request.get("execution_attempt_id"),
            },
        ),
        "started_at": observed_at,
        "ended_at": observed_at,
        "items": items,
        "counts": counts,
        **semantics,
        "error_code": str(error_code or "WORKER_EXECUTION_FAILED"),
        "error_message": str(error_message or "")[:1000],
        "retryable": False,
    }
    result["result_payload_sha256"] = sha256_json(dict(result))
    return result


def _v4_validate_request(request):
    if request.get("contract_version") != V4_CONTRACT_VERSION or request.get("schema_version") != V4_SCHEMA_VERSION:
        raise ValueError("COMMIT_BATCH_SCHEMA_INVALID")
    if str(request.get("execution_mode") or "").upper() != "COMMIT":
        raise ValueError("COMMIT_REQUIRED")
    profile = str(request.get("execution_profile") or "").strip().lower()
    if profile not in {"development", "production"}:
        raise ValueError("INVALID_EXECUTION_PROFILE")
    if "fault_injection" in request:
        fault_injection = str(request.get("fault_injection") or "").strip().upper()
        if (
            profile != "development"
            or fault_injection not in V4_DEVELOPMENT_FAULT_INJECTIONS
            or request.get("fault_injection") != fault_injection
        ):
            raise ValueError("UNSAFE_TEST_PARAMETER_REJECTED")
    for name in ("task_id", "operation_id", "execution_attempt_id", "batch_id"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", str(request.get(name) or "")):
            raise ValueError("invalid " + name)
    platform_name = str(request.get("platform_name") or "").strip()
    items = request.get("items")
    if not platform_name or not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise ValueError("invalid COMMIT batch items")
    task_ids = set()
    skus = set()
    identities = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid COMMIT item")
        required = (
            "item_id",
            "source_task_id",
            "internal_sku",
            "expected_product_name",
            "expected_grade",
            "operation_id",
            "item_execution_attempt_id",
            "write_identity_key",
            "page_identity_key",
        )
        if any(not str(item.get(name) or "").strip() for name in required):
            raise ValueError("invalid COMMIT item")
        task_id = str(item["source_task_id"])
        sku = str(item["internal_sku"]).strip().upper()
        identity = (
            _v2_normalize_text(item["expected_product_name"]),
            normalize_contract_grade(item["expected_grade"]),
        )
        if task_id in task_ids or sku in skus or identity in identities:
            raise ValueError("duplicate COMMIT item identity")
        task_ids.add(task_id)
        skus.add(sku)
        identities.add(identity)
        if item.get("item_payload_sha256") != _v4_sha256(_v4_item_payload(platform_name, item)):
            raise ValueError("COMMIT item payload hash mismatch")
        expected_item_id = _v4_stable_id(
            "ITEM",
            {
                "batch_id": request["batch_id"],
                "source_task_id": task_id,
            },
        )
        if item["item_id"] != expected_item_id:
            raise ValueError("COMMIT item_id mismatch")
        expected_operation_id = _v4_stable_id(
            "OP",
            {
                "source_task_id": task_id,
                "item_payload_sha256": item["item_payload_sha256"],
            },
        )
        if item["operation_id"] != expected_operation_id:
            raise ValueError("COMMIT operation_id mismatch")
        expected_attempt_id = _v4_stable_id(
            "ATTEMPT",
            {
                "execution_attempt_id": request["execution_attempt_id"],
                "item_id": item["item_id"],
            },
        )
        if item["item_execution_attempt_id"] != expected_attempt_id:
            raise ValueError("COMMIT item execution attempt mismatch")
        if item["write_identity_key"] != _v4_write_identity_key(platform_name, sku):
            raise ValueError("COMMIT write identity mismatch")
        if item["page_identity_key"] != _v4_page_identity_key(
            platform_name,
            item["expected_product_name"],
            item["expected_grade"],
        ):
            raise ValueError("COMMIT page identity mismatch")
    if request.get("manifest_sha256") != _v4_manifest_hash(platform_name, items):
        raise ValueError("COMMIT manifest hash mismatch")
    confirmation = request.get("development_confirmation")
    if profile == "development":
        required_text = "确认授权批次 %s 以上%d项真实COMMIT" % (request["batch_id"], len(items))
        if not isinstance(confirmation, dict) or confirmation.get("confirmation_text") != required_text:
            raise ValueError("development confirmation mismatch")
        if not str(confirmation.get("confirmed_by") or "").strip() or not str(confirmation.get("confirmed_at") or "").strip():
            raise ValueError("development confirmation incomplete")
    elif confirmation is not None:
        raise ValueError("production request must not contain development confirmation")
    if not isinstance(request.get("capture_evidence"), bool):
        raise ValueError("capture_evidence must be boolean")
    if request.get("instruction_hash") != _v4_instruction_hash(request):
        raise ValueError("COMMIT batch instruction_hash mismatch")
    expires_at = datetime.fromisoformat(str(request.get("expires_at") or ""))
    if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise ValueError("request expired")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path, content, max_attempts=8):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        for attempt in range(max(int(max_attempts), 1)):
            try:
                os.replace(str(temporary), str(path))
                return
            except OSError as exc:
                retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33)
                if not retryable or attempt + 1 >= max_attempts:
                    raise
                time.sleep(min(0.05 * (2 ** attempt), 0.5))
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def _instruction_hash(payload):
    canonical = {name: payload.get(name, "") for name in INSTRUCTION_HASH_FIELDS}
    return sha256_json(canonical)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def _load_config(args):
    config_path = Path(__file__).with_name("shadowbot_worker_config.json")
    config = {}
    if config_path.exists():
        loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, dict):
            config.update(loaded)
    if args:
        try:
            config.update({key: value for key, value in args.items() if value not in (None, "")})
        except AttributeError:
            pass
    config.setdefault("queue_dir", os.environ.get("SHADOWBOT_QUEUE_DIR", r"D:\PRA_Runtime\shadowbot_queue"))
    config.setdefault("poll_seconds", int(os.environ.get("SHADOWBOT_WORKER_POLL_SECONDS", "3")))
    config.setdefault("max_hours", int(os.environ.get("SHADOWBOT_WORKER_MAX_HOURS", "8")))
    config.setdefault("max_tasks", int(os.environ.get("SHADOWBOT_WORKER_MAX_TASKS", "50")))
    config.setdefault("heartbeat_seconds", 5)
    config.setdefault("allow_fault_injection", False)
    config.setdefault("login_auto_enabled", True)
    # Credential targets are machine-local identifiers.  Keep the repository
    # default empty so a deployment must configure its own target explicitly.
    config.setdefault("login_credential_target", "")
    config.setdefault("login_employee_mode_required", True)
    config.setdefault("login_employee_mode_selector", "登录页_员工按钮")
    config.setdefault("login_employee_mode_wait_seconds", 1)
    config.setdefault("login_account_selector", "登录页_账号输入框")
    config.setdefault("login_password_selector", "登录页_密码输入框")
    config.setdefault("login_submit_selector", "登录页_登录按钮")
    config.setdefault("login_verification_wait_seconds", 600)
    config.setdefault("login_post_submit_wait_seconds", 8)
    return config


class QueueWorker:
    def __init__(self, config):
        self.root = Path(str(config["queue_dir"]))
        self.poll_seconds = max(float(config["poll_seconds"]), 0.2)
        self.max_hours = max(float(config["max_hours"]), 0.1)
        self.max_tasks = max(int(config["max_tasks"]), 1)
        self.heartbeat_seconds = max(float(config["heartbeat_seconds"]), 1.0)
        self.worker_id = socket.gethostname()
        self.inbox = self.root / "inbox"
        self.working = self.root / "working"
        self.results = self.root / "results"
        self.archive = self.root / "archive"
        self.quarantine = self.root / "quarantine"
        self.evidence = self.root / "evidence"
        self.control = self.root / "control"
        self.heartbeat = self.root / "heartbeat.json"
        self.heartbeat_error_log = self.control / "heartbeat_errors.jsonl"
        self.stop_signal = self.control / "stop.signal"
        self.allow_fault_injection = bool(config.get("allow_fault_injection", False))
        self.login_config = {
            "auto_enabled": _as_bool(config.get("login_auto_enabled", True)),
            "employee_mode_required": _as_bool(config.get("login_employee_mode_required", True)),
            "employee_mode_selector": str(config.get("login_employee_mode_selector") or "").strip(),
            "employee_mode_wait_seconds": max(float(config.get("login_employee_mode_wait_seconds", 1)), 0.0),
            "account_selector": str(config.get("login_account_selector") or "").strip(),
            "password_selector": str(config.get("login_password_selector") or "").strip(),
            "submit_selector": str(config.get("login_submit_selector") or "").strip(),
            "verification_wait_seconds": max(float(config.get("login_verification_wait_seconds", 600)), 1.0),
            "post_submit_wait_seconds": max(float(config.get("login_post_submit_wait_seconds", 8)), 1.0),
        }
        self.login_credential_target = str(config.get("login_credential_target") or "").strip()
        self.credential_provider_error_code = ""
        self.credential_provider = self._build_credential_provider()
        self._stop_heartbeat = threading.Event()
        self._heartbeat_write_failures = 0
        self._heartbeat_consecutive_failures = 0
        self._heartbeat_last_error = ""
        self._heartbeat_last_error_at = ""
        self._heartbeat_thread_restarts = 0
        for path in (self.inbox, self.working, self.results, self.archive, self.quarantine, self.evidence, self.control):
            path.mkdir(parents=True, exist_ok=True)

    def run(self):
        started_at = datetime.now(timezone.utc)
        processed = 0
        lock_path = self.control / "worker.lock"
        with lock_path.open("a+b") as lock_file:
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return {"status": "ALREADY_RUNNING", "worker_id": self.worker_id}
            heartbeat_thread = self._start_heartbeat_thread()
            try:
                while processed < self.max_tasks:
                    if not heartbeat_thread.is_alive() and not self._stop_heartbeat.is_set():
                        self._heartbeat_thread_restarts += 1
                        heartbeat_thread = self._start_heartbeat_thread()
                    if datetime.now(timezone.utc) - started_at >= timedelta(hours=self.max_hours):
                        break
                    if self.stop_signal.exists() and not list(self.working.glob("*.request.json")):
                        break
                    if list(self.working.glob("*.request.json")):
                        time.sleep(self.poll_seconds)
                        continue
                    claimed = self._claim_next()
                    if claimed is None:
                        time.sleep(self.poll_seconds)
                        continue
                    self._execute_claimed(*claimed)
                    processed += 1
            finally:
                self._stop_heartbeat.set()
                heartbeat_thread.join(timeout=self.heartbeat_seconds + 1)
                try:
                    self._write_heartbeat("STOPPED", processed)
                except Exception as exc:
                    self._record_heartbeat_error(exc)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        return {"status": "STOPPED", "worker_id": self.worker_id, "processed": processed}

    def _start_heartbeat_thread(self):
        thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name="shadowbot-heartbeat")
        thread.start()
        return thread

    def _heartbeat_loop(self):
        while not self._stop_heartbeat.is_set():
            try:
                self._write_heartbeat("RUNNING", None)
            except Exception as exc:
                self._record_heartbeat_error(exc)
            self._stop_heartbeat.wait(self.heartbeat_seconds)

    def _write_heartbeat(self, status, processed):
        payload = {
            "worker_id": self.worker_id,
            "status": status,
            "processed": processed,
            "updated_at": _now_iso(),
            "heartbeat_write_failures": self._heartbeat_write_failures,
            "heartbeat_consecutive_failures": self._heartbeat_consecutive_failures,
            "heartbeat_last_error": self._heartbeat_last_error,
            "heartbeat_last_error_at": self._heartbeat_last_error_at,
            "heartbeat_thread_restarts": self._heartbeat_thread_restarts,
        }
        _atomic_write(self.heartbeat, _json_bytes(payload))
        self._heartbeat_consecutive_failures = 0

    def _record_heartbeat_error(self, exc):
        self._heartbeat_write_failures += 1
        self._heartbeat_consecutive_failures += 1
        self._heartbeat_last_error = "%s: %s" % (type(exc).__name__, str(exc))
        self._heartbeat_last_error_at = _now_iso()
        event = {
            "worker_id": self.worker_id,
            "error": self._heartbeat_last_error,
            "occurred_at": self._heartbeat_last_error_at,
            "write_failures": self._heartbeat_write_failures,
            "consecutive_failures": self._heartbeat_consecutive_failures,
        }
        try:
            with self.heartbeat_error_log.open("a", encoding="utf-8") as file_obj:
                file_obj.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                file_obj.flush()
        except Exception:
            pass

    def _claim_next(self):
        for request_path in sorted(self.inbox.glob("*.ready.json")):
            checksum_path = request_path.with_suffix(request_path.suffix + ".sha256")
            request = None
            request_sha256 = ""
            try:
                request_bytes = request_path.read_bytes()
                request_sha256 = hashlib.sha256(request_bytes).hexdigest()
                if not checksum_path.exists() or checksum_path.read_text(encoding="ascii").strip().lower() != request_sha256:
                    raise ValueError("request checksum mismatch")
                request = json.loads(request_bytes.decode("utf-8-sig"))
                self._validate_request(request)
                attempt_id = str(request["execution_attempt_id"])
                working_request = self.working / (attempt_id + ".request.json")
                working_checksum = working_request.with_suffix(working_request.suffix + ".sha256")
                os.replace(str(request_path), str(working_request))
                os.replace(str(checksum_path), str(working_checksum))
                phase_path = self.working / (attempt_id + ".phase.json")
                self._write_phase(request, phase_path, "CLAIMED", "NOT_STARTED", request_sha256)
                return request, request_sha256, working_request, phase_path
            except Exception as exc:
                if str(exc) == "request expired" and isinstance(request, dict) and request_sha256:
                    self._write_rejected_request_result(
                        request,
                        request_sha256,
                        request_path,
                        checksum_path,
                        error_code="REQUEST_EXPIRED",
                        error_message=str(exc),
                    )
                else:
                    self._quarantine_request(request_path, checksum_path, str(exc))
        return None

    def _validate_request(self, request):
        fault_injection = str(request.get("fault_injection") or "").strip()
        if fault_injection and (
            not self.allow_fault_injection
            or request.get("contract_version")
            not in {V4_CONTRACT_VERSION, V5_CONTRACT_VERSION}
            or str(request.get("execution_profile") or "").strip().lower()
            != "development"
        ):
            raise ValueError("UNSAFE_TEST_PARAMETER_REJECTED")
        if request.get("contract_version") == V2_CONTRACT_VERSION:
            self._validate_multi_product_request(request)
            return
        if request.get("contract_version") == V4_CONTRACT_VERSION:
            _v4_validate_request(request)
            return
        if request.get("contract_version") == V5_CONTRACT_VERSION:
            _v5_validate_request(request)
            return
        if request.get("contract_version") == ORDER_SCAN_CONTRACT_VERSION:
            _v6_validate_request(request)
            return
        required = (
            "task_id",
            "operation_id",
            "execution_attempt_id",
            "execution_mode",
            "platform_name",
            "product_keyword",
            "expected_product_name",
            "expected_grade",
            "expected_old_price",
            "target_price",
            "instruction_hash",
            "expires_at",
        )
        missing = [name for name in required if not str(request.get(name) or "").strip()]
        if missing:
            raise ValueError("missing request fields: " + ", ".join(missing))
        if str(request.get("fault_injection") or "").strip():
            raise ValueError("UNSAFE_TEST_PARAMETER_REJECTED")
        if bool(request.get("spec_verification_required")):
            raise ValueError("current platform adapter cannot verify expected_spec")
        if request["instruction_hash"] != _instruction_hash(request):
            raise ValueError("instruction_hash mismatch")
        expires_at = datetime.fromisoformat(str(request["expires_at"]))
        if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("request expired")

    def _validate_multi_product_request(self, request):
        if len(_json_bytes(request)) > V2_MAX_REQUEST_BYTES:
            raise ValueError("REQUEST_SIZE_LIMIT_EXCEEDED")
        _v2_normalize_request(request)
        required = ("task_id", "operation_id", "execution_attempt_id", "created_at", "expires_at", "instruction_hash")
        missing = [name for name in required if not str(request.get(name) or "").strip()]
        if missing:
            raise ValueError("missing v2 request fields: " + ", ".join(missing))
        if request["instruction_hash"] != _v2_instruction_hash(request):
            raise ValueError("multi-product instruction_hash mismatch")
        expires_at = datetime.fromisoformat(str(request["expires_at"]))
        if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("request expired")

    def _execute_claimed(self, request, request_sha256, working_request, phase_path):
        attempt_id = str(request["execution_attempt_id"])
        is_v4 = request.get("contract_version") == V4_CONTRACT_VERSION
        is_v5 = request.get("contract_version") == V5_CONTRACT_VERSION
        is_v6 = (
            request.get("contract_version")
            == ORDER_SCAN_CONTRACT_VERSION
        )
        runtime_request = dict(request)
        runtime_request.update(
            {
                "request_file_sha256": (
                    "sha256:" + request_sha256 if is_v5 else request_sha256
                ),
                "lease_owner_token": request.get("lease_owner_token", ""),
                "lease_version": request.get("lease_version", 0),
                "worker_id": self.worker_id,
                "_phase_file_path": str(phase_path),
                "_stop_signal_path": str(self.stop_signal),
                "_provider_error_code": self.credential_provider_error_code,
            }
        )
        # Keep runtime-only objects (provider/config) out of request_json.  The
        # two underscore-prefixed paths are safe control metadata consumed by
        # the flow for phase writes and stop checks.
        runtime_request = {
            key: value
            for key, value in runtime_request.items()
            if not key.startswith("_")
            or key in {"_phase_file_path", "_stop_signal_path", "_provider_error_code"}
        }
        try:
            if __package__:
                from . import vertical_slice_read_price
            else:
                import vertical_slice_read_price

            raw_result = vertical_slice_read_price.main(
                {
                    "request_json": json.dumps(runtime_request, ensure_ascii=False),
                    "_credential_provider": self.credential_provider,
                    "_login_config": self.login_config,
                }
            )
            result = json.loads(raw_result) if isinstance(raw_result, str) else dict(raw_result)
        except Exception as exc:
            if is_v4:
                try:
                    phase_data = json.loads(
                        phase_path.read_text(encoding="utf-8-sig")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    phase_data = {}
                result = build_v4_recovery_result(
                    request,
                    phase_data,
                    request_file_sha256=request_sha256,
                    recovered_at=_now_iso(),
                    worker_id=self.worker_id,
                    error_code="WORKER_EXECUTION_FAILED",
                    error_message="worker execution failed: "
                    + type(exc).__name__,
                )
            elif is_v5:
                try:
                    phase_data = json.loads(
                        phase_path.read_text(encoding="utf-8-sig")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    phase_data = {}
                result = _v5_failed_result(
                    request,
                    request_sha256,
                    worker_id=self.worker_id,
                    error_code="WORKER_EXECUTION_FAILED",
                    error_message="worker execution failed: "
                    + type(exc).__name__,
                    phase_data=phase_data,
                )
            elif is_v6:
                result = _v6_failed_result(
                    request,
                    request_sha256,
                    worker_id=self.worker_id,
                    error_code="WORKER_EXECUTION_FAILED",
                    error_message="worker execution failed: "
                    + type(exc).__name__,
                )
            else:
                result = {
                    "status": "FAILED",
                    "run_success_flag": False,
                    "business_operation_completed": False,
                    "side_effect_state": "NOT_STARTED",
                    "error_code": "BATCH_STOPPED"
                    if request.get("contract_version") == V2_CONTRACT_VERSION
                    else "WORKER_EXECUTION_FAILED",
                    # A lower-level UI exception can echo the text that was passed
                    # to a credential field. Keep queue results free of secrets.
                    "error_message": "worker execution failed: "
                    + type(exc).__name__,
                    "retryable": False,
                }
        provider_error_code = str(result.get("provider_error_code") or "").strip()
        if provider_error_code in SAFE_PROVIDER_ERROR_CODES:
            result["provider_error_code"] = provider_error_code
        else:
            result.pop("provider_error_code", None)
        result.update(
            {
                "schema_version": (
                    ORDER_SCAN_RESULT_SCHEMA_VERSION
                    if is_v6
                    else V5_RESULT_SCHEMA_VERSION
                    if is_v5
                    else V4_RESULT_SCHEMA_VERSION
                    if is_v4
                    else "shadowbot-result-2.0"
                    if request.get("contract_version") == V2_CONTRACT_VERSION
                    else "shadowbot-result-1.0"
                ),
                "execution_attempt_id": attempt_id,
                "execution_mode": request["execution_mode"],
                "instruction_hash": request["instruction_hash"],
                "request_file_sha256": (
                    "sha256:" + request_sha256
                    if is_v5 or is_v6
                    else request_sha256
                ),
                "worker_id": self.worker_id,
                "queue_phase": "RESULT_WRITTEN",
                "worker_heartbeat_at": _now_iso(),
            }
        )
        if not is_v5 and not is_v6:
            result["task_id"] = request["task_id"]
            result["operation_id"] = request["operation_id"]
        if request.get("contract_version") == V2_CONTRACT_VERSION:
            result.setdefault("contract_version", V2_CONTRACT_VERSION)
            result.setdefault("read_batch_id", request.get("read_batch_id", ""))
            result.setdefault("total_count", len(request.get("products") or []))
        if is_v4:
            result.update(
                {
                    "contract_version": V4_CONTRACT_VERSION,
                    "batch_id": request.get("batch_id", ""),
                    "manifest_sha256": request.get("manifest_sha256", ""),
                }
            )
        if is_v5:
            result.update(
                {
                    "contract_version": V5_CONTRACT_VERSION,
                    "action_type": request.get("action_type", ""),
                    "batch_id": request.get("batch_id", ""),
                    "manifest_sha256": request.get("manifest_sha256", ""),
                }
            )
        if is_v6:
            result.update(
                {
                    "contract_version": ORDER_SCAN_CONTRACT_VERSION,
                    "automation_run_id": request.get(
                        "automation_run_id",
                        "",
                    ),
                    "observation_batch_id": request.get(
                        "observation_batch_id",
                        "",
                    ),
                    "platform_name": request.get("platform_name", ""),
                    "requested_platform_trade_date": request.get(
                        "requested_platform_trade_date",
                        "",
                    ),
                }
            )
        if not result.get("result_id"):
            result_identity = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            result["result_id"] = "RESULT-" + hashlib.sha256(result_identity).hexdigest()[:24]
        if is_v5:
            for snapshot_name in ("snapshot", "post_failure_snapshot"):
                snapshot = result.get(snapshot_name)
                if isinstance(snapshot, dict):
                    snapshot["result_id"] = result["result_id"]
            result["result_payload_sha256"] = sha256_json(
                {
                    name: value
                    for name, value in result.items()
                    if name != "result_payload_sha256"
                }
            )
        result_path = self.results / (attempt_id + ".result.json")
        content = _json_bytes(result)
        if request.get("contract_version") == V2_CONTRACT_VERSION and len(content) > V2_MAX_RESULT_BYTES:
            result = {
                "schema_version": "shadowbot-result-2.0",
                "contract_version": V2_CONTRACT_VERSION,
                "task_id": request["task_id"],
                "operation_id": request["operation_id"],
                "execution_attempt_id": attempt_id,
                "execution_mode": request["execution_mode"],
                "read_batch_id": request.get("read_batch_id", ""),
                "instruction_hash": request.get("instruction_hash", ""),
                "request_file_sha256": request_sha256,
                "worker_id": self.worker_id,
                "queue_phase": "RESULT_WRITTEN",
                "worker_heartbeat_at": _now_iso(),
                "status": "FAILED",
                "run_success_flag": False,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": "BATCH_STOPPED",
                "error_message": "result exceeds 4 MiB contract limit",
                "retryable": False,
                "product_snapshots": [],
            }
            result["result_id"] = "RESULT-" + hashlib.sha256(_json_bytes(result)).hexdigest()[:24]
            content = _json_bytes(result)
        if is_v4 and len(content) > V2_MAX_RESULT_BYTES:
            compact_items = []
            supplied_items = result.get("items")
            if not isinstance(supplied_items, list) or len(supplied_items) != len(
                request.get("items") or []
            ):
                supplied_items = [
                    _v4_result_item_skeleton(
                        item,
                        status="UNKNOWN",
                        error_code="RESULT_TOO_LARGE",
                        error_message="result exceeded the contract size limit",
                    )
                    for item in request.get("items") or []
                ]
            for request_item, supplied_item in zip(
                request.get("items") or [], supplied_items
            ):
                compact = _v4_result_item_skeleton(request_item)
                for name in (
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
                ):
                    if name in supplied_item:
                        compact[name] = supplied_item[name]
                compact["error_message"] = str(
                    supplied_item.get("error_message") or ""
                )[:512]
                compact_items.append(compact)
            compact_counts = _v4_result_counts(compact_items)
            compact_semantics = derive_v4_batch_semantics(compact_counts)
            result = {
                "schema_version": V4_RESULT_SCHEMA_VERSION,
                "contract_version": V4_CONTRACT_VERSION,
                "task_id": request["task_id"],
                "operation_id": request["operation_id"],
                "execution_attempt_id": attempt_id,
                "execution_mode": request["execution_mode"],
                "batch_id": request.get("batch_id", ""),
                "manifest_sha256": request.get("manifest_sha256", ""),
                "instruction_hash": request.get("instruction_hash", ""),
                "request_file_sha256": request_sha256,
                "worker_id": self.worker_id,
                "queue_phase": "RESULT_WRITTEN",
                "worker_heartbeat_at": _now_iso(),
                **compact_semantics,
                "error_code": (
                    ""
                    if compact_semantics["batch_status"] == "VERIFIED"
                    else "RESULT_COMPACTED"
                ),
                "error_message": (
                    ""
                    if compact_semantics["batch_status"] == "VERIFIED"
                    else "oversized result was reduced to its complete item ledger"
                ),
                "retryable": False,
                "items": compact_items,
                "counts": compact_counts,
            }
            result["result_id"] = "RESULT-" + hashlib.sha256(_json_bytes(result)).hexdigest()[:24]
            content = _json_bytes(result)
        if is_v6 and len(content) > V2_MAX_RESULT_BYTES:
            result = _v6_failed_result(
                request,
                request_sha256,
                worker_id=self.worker_id,
                error_code="ORDER_RESULT_TOO_LARGE",
                error_message="order result exceeds 4 MiB contract limit",
            )
            result["result_id"] = (
                "RESULT-"
                + hashlib.sha256(_json_bytes(result)).hexdigest()[:24]
            )
            content = _json_bytes(result)
        _atomic_write(result_path.with_suffix(result_path.suffix + ".sha256"), (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"))
        _atomic_write(result_path, content)
        self._write_phase(
            request,
            phase_path,
            "RESULT_WRITTEN",
            str(result.get("side_effect_state") or "NOT_STARTED"),
            request_sha256,
            result_snapshot=result if is_v4 or is_v5 else None,
        )

    def _build_credential_provider(self):
        try:
            if __package__:
                from .shadowbot_credentials import WindowsCredentialManagerProvider
            else:
                from shadowbot_credentials import WindowsCredentialManagerProvider
            return WindowsCredentialManagerProvider(self.login_credential_target)
        except Exception as exc:
            provider_error_code = str(getattr(exc, "error_code", "") or "").strip()
            if provider_error_code in SAFE_PROVIDER_ERROR_CODES:
                self.credential_provider_error_code = provider_error_code
            return None

    def _write_phase(
        self,
        request,
        phase_path,
        phase,
        side_effect_state,
        request_sha256,
        *,
        result_snapshot=None,
    ):
        if request.get("contract_version") == V5_CONTRACT_VERSION:
            payload = {
                "schema_version": V5_PHASE_SCHEMA_VERSION,
                "contract_version": V5_CONTRACT_VERSION,
                "action_type": request.get("action_type", ""),
                "batch_id": request.get("batch_id", ""),
                "execution_attempt_id": request.get(
                    "execution_attempt_id", ""
                ),
                "instruction_hash": request.get("instruction_hash", ""),
                "manifest_sha256": request.get("manifest_sha256", ""),
                "request_file_sha256": "sha256:" + request_sha256,
                "worker_id": self.worker_id,
                "phase": phase,
                "phase_at": _now_iso(),
                "detail_effect_state": "NOT_STARTED",
                "listing_effect_state": "NOT_STARTED",
            }
            payload["phase_snapshot_sha256"] = sha256_json(dict(payload))
            _atomic_write(phase_path, _json_bytes(payload))
            return
        payload = {
            "task_id": request.get("task_id", ""),
            "operation_id": request.get("operation_id", ""),
            "execution_attempt_id": request.get("execution_attempt_id", ""),
            "execution_mode": request.get("execution_mode", ""),
            "phase": phase,
            "side_effect_state": side_effect_state,
            "request_file_sha256": request_sha256,
            "instruction_hash": request.get("instruction_hash", ""),
            "worker_id": self.worker_id,
            "lease_owner_token": request.get("lease_owner_token", ""),
            "lease_version": request.get("lease_version", 0),
            "updated_at": _now_iso(),
        }
        if request.get("contract_version") == V2_CONTRACT_VERSION:
            payload.update(
                {
                    "contract_version": V2_CONTRACT_VERSION,
                    "read_batch_id": request.get("read_batch_id", ""),
                    "total_count": len(request.get("products") or []),
                }
            )
        if request.get("contract_version") == V4_CONTRACT_VERSION:
            payload.update(
                {
                    "schema_version": V4_PHASE_SCHEMA_VERSION,
                    "contract_version": V4_CONTRACT_VERSION,
                    "batch_id": request.get("batch_id", ""),
                    "manifest_sha256": request.get("manifest_sha256", ""),
                    "total_count": len(request.get("items") or []),
                }
            )
            if isinstance(result_snapshot, dict):
                payload["batch_result_snapshot"] = result_snapshot
        if (
            request.get("contract_version")
            == ORDER_SCAN_CONTRACT_VERSION
        ):
            payload.update(
                {
                    "schema_version": (
                        "shadowbot-order-scan-phase-1.0"
                    ),
                    "contract_version": ORDER_SCAN_CONTRACT_VERSION,
                    "automation_run_id": request.get(
                        "automation_run_id",
                        "",
                    ),
                    "observation_batch_id": request.get(
                        "observation_batch_id",
                        "",
                    ),
                    "requested_platform_trade_date": request.get(
                        "requested_platform_trade_date",
                        "",
                    ),
                }
            )
        _atomic_write(phase_path, _json_bytes(payload))

    def _write_rejected_request_result(
        self,
        request,
        request_sha256,
        request_path,
        checksum_path,
        error_code,
        error_message,
    ):
        attempt_id = str(request["execution_attempt_id"])
        working_request = self.working / (attempt_id + ".request.json")
        working_checksum = working_request.with_suffix(working_request.suffix + ".sha256")
        os.replace(str(request_path), str(working_request))
        os.replace(str(checksum_path), str(working_checksum))
        phase_path = self.working / (attempt_id + ".phase.json")
        if request.get("contract_version") == V5_CONTRACT_VERSION:
            result = _v5_failed_result(
                request,
                request_sha256,
                worker_id=self.worker_id,
                error_code=error_code,
                error_message=error_message,
            )
            result_path = self.results / (attempt_id + ".result.json")
            content = _json_bytes(result)
            _atomic_write(
                result_path.with_suffix(result_path.suffix + ".sha256"),
                (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"),
            )
            _atomic_write(result_path, content)
            self._write_phase(
                request,
                phase_path,
                "RESULT_WRITTEN",
                "NOT_STARTED",
                request_sha256,
            )
            return
        if (
            request.get("contract_version")
            == ORDER_SCAN_CONTRACT_VERSION
        ):
            result = _v6_failed_result(
                request,
                request_sha256,
                worker_id=self.worker_id,
                error_code=error_code,
                error_message=error_message,
            )
            result_path = self.results / (
                attempt_id + ".result.json"
            )
            content = _json_bytes(result)
            _atomic_write(
                result_path.with_suffix(
                    result_path.suffix + ".sha256"
                ),
                (
                    hashlib.sha256(content).hexdigest() + "\n"
                ).encode("ascii"),
            )
            _atomic_write(result_path, content)
            self._write_phase(
                request,
                phase_path,
                "RESULT_WRITTEN",
                "NOT_STARTED",
                request_sha256,
            )
            return
        result = {
            "schema_version": "shadowbot-result-1.0",
            "task_id": request["task_id"],
            "operation_id": request["operation_id"],
            "execution_attempt_id": attempt_id,
            "execution_mode": request["execution_mode"],
            "instruction_hash": request["instruction_hash"],
            "request_file_sha256": request_sha256,
            "lease_owner_token": request.get("lease_owner_token", ""),
            "lease_version": request.get("lease_version", 0),
            "worker_id": self.worker_id,
            "status": "FAILED",
            "run_success_flag": False,
            "business_operation_completed": False,
            "side_effect_state": "NOT_STARTED",
            "error_code": error_code,
            "error_message": error_message,
            "retryable": False,
            "queue_phase": "RESULT_WRITTEN",
            "worker_heartbeat_at": _now_iso(),
        }
        result_identity = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        result["result_id"] = "RESULT-" + hashlib.sha256(result_identity).hexdigest()[:24]
        result_path = self.results / (attempt_id + ".result.json")
        content = _json_bytes(result)
        _atomic_write(
            result_path.with_suffix(result_path.suffix + ".sha256"),
            (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"),
        )
        _atomic_write(result_path, content)
        self._write_phase(request, phase_path, "RESULT_WRITTEN", "NOT_STARTED", request_sha256)

    def _quarantine_request(self, request_path, checksum_path, error):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if request_path.exists():
            os.replace(str(request_path), str(self.quarantine / (stamp + "-" + request_path.name)))
        if checksum_path.exists():
            os.replace(str(checksum_path), str(self.quarantine / (stamp + "-" + checksum_path.name)))
        error_path = self.quarantine / (stamp + "-request-error.json")
        _atomic_write(error_path, _json_bytes({"error_code": "INPUT_INVALID", "error_message": error}))


def main(args):
    result = QueueWorker(_load_config(args)).run()
    result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if args is not None:
        try:
            args["result_json"] = result_json
        except Exception:
            pass
    print(result_json)
    return result_json
