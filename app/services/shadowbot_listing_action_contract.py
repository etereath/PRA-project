"""Task 13 v5 listing-action manifest, request, result, phase, and snapshot contracts."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.exceptions import ValidationError
from app.shadowbot_contract_primitives import (
    canonical_positive_price,
    contract_identity_key,
    normalize_contract_grade,
    normalize_contract_sku,
    normalize_contract_text,
    sha256_json,
)
from app.shadowbot_listing_contract import (
    V5_ACTION_TYPES,
    V5_ANOMALY_REASON_CODES,
    V5_ANOMALY_RESOLUTION_POLICIES,
    V5_ANOMALY_SCHEMA_VERSION,
    V5_BATCH_STATUSES,
    V5_CONTRACT_VERSION,
    V5_DEVELOPMENT_FAULT_INJECTIONS,
    V5_GATE_SUMMARY_SCHEMA_VERSION,
    V5_ITEM_OUTCOMES,
    V5_LISTING_LOCATIONS,
    V5_MANIFEST_SCHEMA_VERSION,
    V5_PHASE_NAMES,
    V5_PHASE_SCHEMA_VERSION,
    V5_REQUEST_SCHEMA_VERSION,
    V5_RESULT_SCHEMA_VERSION,
    V5_SIDE_EFFECT_STATES,
    V5_SNAPSHOT_SCHEMA_VERSION,
    V5_WRITE_ACTION_TYPES,
    canonical_nonnegative_inventory,
    derive_listing_location,
    derive_v5_batch_semantics,
    v5_phase_matches_request,
    v5_result_counts,
)


MAX_ITEMS = 50
EXECUTION_PROFILES = frozenset({"development", "production"})
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_FORBIDDEN_POSITION_FIELDS = frozenset(
    {
        "ordinal",
        "page_position",
        "page_position_hint",
        "matched_product_position",
        "runtime_ordinal",
        "execution_ordinal",
        "row_index",
        "parent_index",
    }
)
_WRITE_ITEM_INPUT_FIELDS = frozenset(
    {
        "source_task_id",
        "internal_sku",
        "expected_old_status",
        "target_status",
        "target_price",
        "target_inventory",
        "expires_at",
    }
)
_WRITE_ITEM_FIELDS = frozenset(
    {
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
        "item_execution_attempt_id",
        "write_identity_key",
        "page_identity_key",
    }
)


def build_listing_action_manifest(
    *,
    batch_id: str,
    action_type: str,
    task_items: list[dict[str, Any]] | None,
    identity_mapping: Mapping[str, Mapping[str, str]] | None,
    platform_name: str,
    mapping_source_version: str,
) -> dict[str, Any]:
    _validate_id(batch_id, "batch_id")
    action = _action_type(action_type)
    platform = _required_value(platform_name, "platform_name")
    mapping_version = _required_value(
        mapping_source_version,
        "mapping_source_version",
    )
    if action == "sync_status":
        if task_items not in (None, []):
            raise ValidationError("SYNC_STATUS 不允许携带任务商品项。")
        items: list[dict[str, Any]] = []
        execution_mode = "READ_ONLY"
        scan_scope = "online_and_waiting"
    else:
        items = _build_write_items(
            batch_id=batch_id,
            action_type=action,
            task_items=task_items,
            identity_mapping=identity_mapping,
            platform_name=platform,
        )
        execution_mode = "COMMIT"
        scan_scope = (
            "online_and_waiting" if action == "set_online" else "online"
        )
    manifest = {
        "schema_version": V5_MANIFEST_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "batch_id": batch_id,
        "action_type": action,
        "execution_mode": execution_mode,
        "platform_name": platform,
        "mapping_source_version": mapping_version,
        "scan_scope": scan_scope,
        "items": items,
    }
    manifest["manifest_sha256"] = _manifest_sha256(manifest)
    if action in V5_WRITE_ACTION_TYPES:
        manifest["development_confirmation_text"] = (
            required_development_confirmation(batch_id, len(items))
        )
    validate_listing_action_manifest(manifest)
    return manifest


def validate_listing_action_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ValidationError("LISTING_ACTION_MANIFEST_INVALID")
    allowed = {
        "schema_version",
        "contract_version",
        "batch_id",
        "action_type",
        "execution_mode",
        "platform_name",
        "mapping_source_version",
        "scan_scope",
        "items",
        "manifest_sha256",
        "development_confirmation_text",
    }
    _reject_unexpected(manifest, allowed, "v5 manifest")
    if manifest.get("schema_version") != V5_MANIFEST_SCHEMA_VERSION:
        raise ValidationError("LISTING_ACTION_MANIFEST_INVALID")
    if manifest.get("contract_version") != V5_CONTRACT_VERSION:
        raise ValidationError("UNKNOWN_CONTRACT_VERSION")
    _validate_id(str(manifest.get("batch_id") or ""), "batch_id")
    action = _action_type(manifest.get("action_type"))
    execution_mode = str(manifest.get("execution_mode") or "").upper()
    allowed_modes = (
        {"READ_ONLY"}
        if action == "sync_status"
        else {"COMMIT", "RECONCILE"}
    )
    if execution_mode not in allowed_modes:
        raise ValidationError("LISTING_ACTION_EXECUTION_MODE_INVALID")
    _required_value(manifest.get("platform_name"), "platform_name")
    _required_value(
        manifest.get("mapping_source_version"),
        "mapping_source_version",
    )
    expected_scope = (
        "online_and_waiting"
        if action in {"set_online", "sync_status"}
        else "online"
    )
    if manifest.get("scan_scope") != expected_scope:
        raise ValidationError("LISTING_ACTION_SCAN_SCOPE_INVALID")
    items = manifest.get("items")
    if action == "sync_status":
        if items != [] or "development_confirmation_text" in manifest:
            raise ValidationError("SYNC_STATUS 合同不得包含写任务项或开发授权。")
    elif execution_mode == "COMMIT":
        _validate_write_items(
            manifest["platform_name"],
            action,
            items,
            batch_id=str(manifest["batch_id"]),
        )
        if manifest.get(
            "development_confirmation_text"
        ) != required_development_confirmation(str(manifest["batch_id"]), len(items)):
            raise ValidationError("开发阶段授权提示不匹配。")
    else:
        _validate_write_items(
            manifest["platform_name"],
            action,
            items,
            batch_id=str(manifest["batch_id"]),
        )
        if len(items) != 1 or "development_confirmation_text" in manifest:
            raise ValidationError("RECONCILE manifest 必须是单商品且不得包含写入授权。")
    if manifest.get("manifest_sha256") != _manifest_sha256(manifest):
        raise ValidationError("LISTING_ACTION_MANIFEST_HASH_MISMATCH")


def build_listing_action_request(
    manifest: dict[str, Any],
    *,
    execution_profile: str,
    execution_attempt_id: str,
    applet_uri: str,
    gate_summary: dict[str, Any] | None = None,
    batch_task_id: str = "",
    batch_operation_id: str = "",
    confirmation_text: str = "",
    confirmed_by: str = "",
    window_title: str = "微信",
    capture_evidence: bool = False,
    ttl_minutes: int = 30,
    fault_injection: str = "",
    fault_injection_item_ordinal: int | None = None,
) -> dict[str, Any]:
    validate_listing_action_manifest(manifest)
    profile = str(execution_profile or "").strip().lower()
    if profile not in EXECUTION_PROFILES:
        raise ValidationError("execution_profile 必须是 development 或 production。")
    _validate_id(execution_attempt_id, "execution_attempt_id")
    action = str(manifest["action_type"])
    if action in V5_WRITE_ACTION_TYPES:
        _validate_id(batch_task_id, "batch_task_id")
        _validate_id(batch_operation_id, "batch_operation_id")
        validate_gate_summary(
            gate_summary,
            expected_items=manifest["items"],
        )
    elif gate_summary is not None or batch_task_id or batch_operation_id:
        raise ValidationError("SYNC_STATUS 不允许携带写任务、operation 或 gate_summary。")

    development_confirmation: dict[str, str] | None = None
    if profile == "development" and action in V5_WRITE_ACTION_TYPES:
        required = required_development_confirmation(
            str(manifest["batch_id"]),
            len(manifest["items"]),
        )
        if str(confirmation_text or "").strip() != required:
            raise ValidationError("开发阶段确认文本与固定商品清单不匹配。")
        if not str(confirmed_by or "").strip():
            raise ValidationError("开发阶段 confirmed_by 不能为空。")
        development_confirmation = {
            "confirmed_by": str(confirmed_by).strip(),
            "confirmation_text": required,
        }
    elif confirmation_text or confirmed_by:
        raise ValidationError("当前 v5 请求不得携带开发阶段人工确认字段。")

    now = datetime.now(timezone.utc)
    request: dict[str, Any] = {
        "schema_version": V5_REQUEST_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "execution_profile": profile,
        "execution_mode": manifest["execution_mode"],
        "action_type": action,
        "execution_attempt_id": execution_attempt_id,
        "batch_id": manifest["batch_id"],
        "platform_name": manifest["platform_name"],
        "mapping_source_version": manifest["mapping_source_version"],
        "scan_scope": manifest["scan_scope"],
        "items": [
            {
                **dict(item),
                "item_execution_attempt_id": _stable_id(
                    "ATTEMPT",
                    {
                        "execution_attempt_id": execution_attempt_id,
                        "item_id": item["item_id"],
                    },
                ),
            }
            for item in manifest["items"]
        ],
        "manifest_sha256": manifest["manifest_sha256"],
        "applet_uri": str(applet_uri or "").strip(),
        "window_title": str(window_title or "微信").strip(),
        "capture_evidence": bool(capture_evidence),
        "created_at": now.isoformat(),
        "expires_at": (
            now + timedelta(minutes=max(1, int(ttl_minutes)))
        ).isoformat(),
    }
    if action in V5_WRITE_ACTION_TYPES:
        request["task_id"] = batch_task_id
        request["operation_id"] = batch_operation_id
        request["gate_summary"] = json.loads(
            json.dumps(gate_summary, ensure_ascii=False)
        )
    if development_confirmation is not None:
        development_confirmation["confirmed_at"] = now.isoformat()
        request["development_confirmation"] = development_confirmation
    normalized_fault = str(fault_injection or "").strip().upper()
    if normalized_fault:
        if (
            profile != "development"
            or action != "set_offline"
            or normalized_fault not in V5_DEVELOPMENT_FAULT_INJECTIONS
        ):
            raise ValidationError(
                "v5 故障注入只允许开发阶段 SET_OFFLINE 受控验收。"
            )
        if (
            isinstance(fault_injection_item_ordinal, bool)
            or not isinstance(fault_injection_item_ordinal, int)
            or not 1 <= fault_injection_item_ordinal <= len(request["items"])
        ):
            raise ValidationError("v5 故障注入商品序号无效。")
        request["fault_injection"] = normalized_fault
        request["fault_injection_item_ordinal"] = (
            fault_injection_item_ordinal
        )
    elif fault_injection_item_ordinal is not None:
        raise ValidationError("v5 故障注入商品序号缺少故障类型。")
    request["instruction_hash"] = compute_listing_instruction_hash(request)
    validate_listing_action_request(request)
    return request


def build_listing_action_reconcile_request(
    source_request: dict[str, Any],
    source_result: dict[str, Any],
    *,
    operation_id: str,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    """Derive one deterministic, read-only v5 RECONCILE from an UNKNOWN item."""

    validate_listing_action_request(source_request, check_expiry=False)
    validate_listing_action_result(
        source_result,
        request=source_request,
        request_file_sha256=str(
            source_result.get("request_file_sha256") or ""
        ),
    )
    if str(source_request.get("execution_mode") or "").upper() != "COMMIT":
        raise ValidationError("RECONCILE 来源必须是 v5 COMMIT。")
    source_item = next(
        (
            item
            for item in source_request["items"]
            if item.get("operation_id") == operation_id
        ),
        None,
    )
    source_output = next(
        (
            item
            for item in source_result["items"]
            if item.get("operation_id") == operation_id
        ),
        None,
    )
    if source_item is None or source_output is None:
        raise ValidationError("RECONCILE 来源商品绑定不存在。")
    if (
        str(source_output.get("operation_result") or "").upper()
        != "NEEDS_RECONCILIATION"
    ):
        raise ValidationError("只有 UNKNOWN 商品可以创建 RECONCILE。")
    source_execution_attempt_id = str(
        source_item.get("item_execution_attempt_id") or ""
    )
    _validate_id(
        source_execution_attempt_id,
        "source_execution_attempt_id",
    )
    execution_attempt_id = _stable_id(
        "RECONCILE",
        {"source_execution_attempt_id": source_execution_attempt_id},
    )
    item = {
        name: value
        for name, value in source_item.items()
        if name != "item_execution_attempt_id"
    }
    manifest = {
        "schema_version": V5_MANIFEST_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "batch_id": source_request["batch_id"],
        "action_type": source_request["action_type"],
        "execution_mode": "RECONCILE",
        "platform_name": source_request["platform_name"],
        "mapping_source_version": source_request[
            "mapping_source_version"
        ],
        "scan_scope": source_request["scan_scope"],
        "items": [item],
    }
    manifest["manifest_sha256"] = _manifest_sha256(manifest)
    validate_listing_action_manifest(manifest)
    item["item_execution_attempt_id"] = _stable_id(
        "ATTEMPT",
        {
            "execution_attempt_id": execution_attempt_id,
            "item_id": item["item_id"],
        },
    )
    now = datetime.now(timezone.utc)
    request = {
        "schema_version": V5_REQUEST_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "execution_profile": source_request["execution_profile"],
        "execution_mode": "RECONCILE",
        "action_type": source_request["action_type"],
        "task_id": item["source_task_id"],
        "operation_id": operation_id,
        "execution_attempt_id": execution_attempt_id,
        "batch_id": source_request["batch_id"],
        "platform_name": source_request["platform_name"],
        "mapping_source_version": source_request[
            "mapping_source_version"
        ],
        "scan_scope": source_request["scan_scope"],
        "items": [item],
        "manifest_sha256": manifest["manifest_sha256"],
        "source_execution_attempt_id": source_execution_attempt_id,
        "source_result_id": source_result["result_id"],
        "applet_uri": str(source_request.get("applet_uri") or ""),
        "window_title": str(
            source_request.get("window_title") or "微信"
        ),
        "capture_evidence": bool(
            source_request.get("capture_evidence")
        ),
        "created_at": now.isoformat(),
        "expires_at": (
            now + timedelta(minutes=max(1, int(ttl_minutes)))
        ).isoformat(),
    }
    request["instruction_hash"] = compute_listing_instruction_hash(request)
    validate_listing_action_request(request)
    return request


def compute_listing_instruction_hash(request: dict[str, Any]) -> str:
    canonical = {
        name: request.get(name)
        for name in (
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
            "created_at",
            "expires_at",
        )
        if name in request
    }
    return sha256_json(canonical)


def validate_listing_action_request(
    request: dict[str, Any],
    *,
    check_expiry: bool = True,
    allow_legacy_operation_id: bool = False,
) -> None:
    if not isinstance(request, dict):
        raise ValidationError("LISTING_ACTION_REQUEST_INVALID")
    allowed = {
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
        "created_at",
        "expires_at",
        "instruction_hash",
    }
    _reject_unexpected(request, allowed, "v5 request")
    if request.get("schema_version") != V5_REQUEST_SCHEMA_VERSION:
        raise ValidationError("LISTING_ACTION_REQUEST_INVALID")
    if request.get("contract_version") != V5_CONTRACT_VERSION:
        raise ValidationError("UNKNOWN_CONTRACT_VERSION")
    profile = str(request.get("execution_profile") or "").strip().lower()
    if profile not in EXECUTION_PROFILES:
        raise ValidationError("INVALID_EXECUTION_PROFILE")
    action = _action_type(request.get("action_type"))
    _validate_id(str(request.get("execution_attempt_id") or ""), "execution_attempt_id")
    _validate_id(str(request.get("batch_id") or ""), "batch_id")
    platform = _required_value(request.get("platform_name"), "platform_name")
    _required_value(
        request.get("mapping_source_version"),
        "mapping_source_version",
    )
    execution_mode = str(request.get("execution_mode") or "").upper()
    allowed_modes = (
        {"READ_ONLY"}
        if action == "sync_status"
        else {"COMMIT", "RECONCILE"}
    )
    if execution_mode not in allowed_modes:
        raise ValidationError("LISTING_ACTION_EXECUTION_MODE_INVALID")
    expected_scope = (
        "online_and_waiting"
        if action in {"set_online", "sync_status"}
        else "online"
    )
    if request.get("scan_scope") != expected_scope:
        raise ValidationError("LISTING_ACTION_SCAN_SCOPE_INVALID")
    items = request.get("items")
    if action == "sync_status":
        if items != []:
            raise ValidationError("SYNC_STATUS 请求不得携带写任务项。")
        for forbidden in (
            "task_id",
            "operation_id",
            "gate_summary",
            "development_confirmation",
        ):
            if forbidden in request:
                raise ValidationError("SYNC_STATUS 请求包含写操作字段。")
    else:
        _validate_id(str(request.get("task_id") or ""), "task_id")
        _validate_id(str(request.get("operation_id") or ""), "operation_id")
        _validate_write_items(
            platform,
            action,
            items,
            batch_id=str(request["batch_id"]),
            execution_attempt_id=str(request["execution_attempt_id"]),
            require_attempt_id=True,
            allow_legacy_operation_id=allow_legacy_operation_id,
        )
        if execution_mode == "COMMIT":
            validate_gate_summary(
                request.get("gate_summary"),
                expected_items=items,
            )
            confirmation = request.get("development_confirmation")
            if profile == "development":
                _validate_development_confirmation(
                    confirmation,
                    batch_id=str(request["batch_id"]),
                    item_count=len(items),
                )
            elif confirmation is not None:
                raise ValidationError("正式运行合同不得携带开发阶段人工确认。")
            for forbidden in (
                "source_execution_attempt_id",
                "source_result_id",
            ):
                if forbidden in request:
                    raise ValidationError("COMMIT 请求不得携带 RECONCILE 来源字段。")
        else:
            if len(items) != 1:
                raise ValidationError("RECONCILE 请求必须且只能包含一个商品。")
            for forbidden in (
                "gate_summary",
                "development_confirmation",
                "fault_injection",
                "fault_injection_item_ordinal",
            ):
                if forbidden in request:
                    raise ValidationError("RECONCILE 请求包含写入或测试字段。")
            _validate_id(
                str(request.get("source_execution_attempt_id") or ""),
                "source_execution_attempt_id",
            )
            _validate_id(
                str(request.get("source_result_id") or ""),
                "source_result_id",
            )
            if request.get("task_id") != items[0]["source_task_id"]:
                raise ValidationError("RECONCILE task_id 与商品项不匹配。")
            if request.get("operation_id") != items[0]["operation_id"]:
                raise ValidationError("RECONCILE operation_id 与商品项不匹配。")
    fault_injection = str(request.get("fault_injection") or "").strip()
    fault_ordinal = request.get("fault_injection_item_ordinal")
    if fault_injection:
        if (
            profile != "development"
            or execution_mode != "COMMIT"
            or action != "set_offline"
            or fault_injection not in V5_DEVELOPMENT_FAULT_INJECTIONS
            or request.get("fault_injection") != fault_injection.upper()
        ):
            raise ValidationError(
                "v5 故障注入只允许开发阶段 SET_OFFLINE 受控验收。"
            )
        if (
            isinstance(fault_ordinal, bool)
            or not isinstance(fault_ordinal, int)
            or not 1 <= fault_ordinal <= len(items)
        ):
            raise ValidationError("v5 故障注入商品序号无效。")
    elif fault_ordinal is not None:
        raise ValidationError("v5 故障注入商品序号缺少故障类型。")
    if not isinstance(request.get("capture_evidence"), bool):
        raise ValidationError("capture_evidence 必须是布尔值。")
    for name in ("created_at", "expires_at"):
        _aware_datetime(request.get(name), name)
    if (
        check_expiry
        and _aware_datetime(request.get("expires_at"), "expires_at").astimezone(
            timezone.utc
        )
        <= datetime.now(timezone.utc)
    ):
        raise ValidationError("v5 请求已过期。")
    if not _SHA256_RE.fullmatch(str(request.get("manifest_sha256") or "")):
        raise ValidationError("manifest_sha256 无效。")
    manifest_view = {
        "schema_version": V5_MANIFEST_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "batch_id": request["batch_id"],
        "action_type": action,
        "execution_mode": execution_mode,
        "platform_name": platform,
        "mapping_source_version": request["mapping_source_version"],
        "scan_scope": expected_scope,
        "items": [
            {name: item.get(name) for name in _WRITE_ITEM_FIELDS if name != "item_execution_attempt_id" and name in item}
            for item in items
        ],
    }
    if action in V5_WRITE_ACTION_TYPES:
        manifest_view["development_confirmation_text"] = (
            required_development_confirmation(str(request["batch_id"]), len(items))
        )
    if request.get("manifest_sha256") != _manifest_sha256(manifest_view):
        raise ValidationError("LISTING_ACTION_MANIFEST_HASH_MISMATCH")
    if request.get("instruction_hash") != compute_listing_instruction_hash(request):
        raise ValidationError("LISTING_ACTION_INSTRUCTION_HASH_MISMATCH")


def validate_gate_summary(
    summary: dict[str, Any] | None,
    *,
    expected_items: list[dict[str, Any]],
) -> None:
    if not isinstance(summary, dict):
        raise ValidationError("v5 写请求缺少 gate_summary。")
    allowed = {"schema_version", "gate_phase", "evaluated_at", "items"}
    _reject_unexpected(summary, allowed, "gate_summary")
    if summary.get("schema_version") != V5_GATE_SUMMARY_SCHEMA_VERSION:
        raise ValidationError("gate_summary schema_version 无效。")
    if summary.get("gate_phase") != "PRE_PUBLISH":
        raise ValidationError("发布请求 gate_summary 必须来自 PRE_PUBLISH。")
    _aware_datetime(summary.get("evaluated_at"), "gate_summary.evaluated_at")
    supplied_items = summary.get("items")
    if not isinstance(supplied_items, list) or len(supplied_items) != len(
        expected_items
    ):
        raise ValidationError("gate_summary 商品项数量不匹配。")
    expected_by_sku = {
        str(item["internal_sku"]).upper(): item for item in expected_items
    }
    seen: set[str] = set()
    allowed_item_fields = {
        "internal_sku",
        "operation_id",
        "decision",
        "lock_status",
        "lock_operation_id",
        "block_reasons",
    }
    for item in supplied_items:
        if not isinstance(item, dict):
            raise ValidationError("gate_summary item 必须是对象。")
        _reject_unexpected(item, allowed_item_fields, "gate_summary item")
        sku = str(item.get("internal_sku") or "").strip().upper()
        expected = expected_by_sku.get(sku)
        if expected is None or sku in seen:
            raise ValidationError("gate_summary SKU 绑定无效。")
        seen.add(sku)
        operation_id = str(item.get("operation_id") or "")
        if operation_id != str(expected.get("operation_id") or ""):
            raise ValidationError("gate_summary operation 绑定无效。")
        if item.get("decision") not in {"EXECUTE", "ALREADY_APPLIED"}:
            raise ValidationError("BLOCKED gate_summary 不得发布到 Worker。")
        if item.get("lock_status") != "ACTIVE":
            raise ValidationError("发布请求必须绑定 ACTIVE 写锁。")
        if str(item.get("lock_operation_id") or "") != operation_id:
            raise ValidationError("gate_summary 写锁所有者不匹配。")
        if item.get("block_reasons") != []:
            raise ValidationError("可发布 gate_summary 不得包含阻断原因。")


def build_listing_action_phase(
    request: dict[str, Any],
    *,
    request_file_sha256: str,
    phase_name: str,
    worker_id: str,
    current_item: dict[str, Any] | None = None,
    item_states: list[dict[str, Any]] | None = None,
    phase_at: str | None = None,
    clicked_at: str | None = None,
    detail_effect_state: str = "NOT_STARTED",
    listing_effect_state: str = "NOT_STARTED",
) -> dict[str, Any]:
    validate_listing_action_request(request, check_expiry=False)
    if not _SHA256_RE.fullmatch(str(request_file_sha256 or "")):
        raise ValidationError("request_file_sha256 无效。")
    phase = str(phase_name or "").strip().upper()
    if phase not in V5_PHASE_NAMES:
        raise ValidationError("v5 phase 名称无效。")
    now_text = phase_at or datetime.now(timezone.utc).isoformat()
    _aware_datetime(now_text, "phase_at")
    payload: dict[str, Any] = {
        "schema_version": V5_PHASE_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "action_type": request["action_type"],
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "instruction_hash": request["instruction_hash"],
        "manifest_sha256": request["manifest_sha256"],
        "request_file_sha256": request_file_sha256,
        "worker_id": _required_value(worker_id, "worker_id"),
        "phase": phase,
        "phase_at": now_text,
        "detail_effect_state": str(detail_effect_state or "").strip().upper(),
        "listing_effect_state": str(listing_effect_state or "").strip().upper(),
    }
    if current_item is not None:
        payload["current_item"] = {
            name: current_item.get(name)
            for name in (
                "source_task_id",
                "operation_id",
                "item_execution_attempt_id",
                "internal_sku",
                "item_payload_sha256",
            )
        }
    if item_states is not None:
        payload["item_states"] = [
            {
                name: item.get(name)
                for name in (
                    "source_task_id",
                    "operation_id",
                    "item_execution_attempt_id",
                    "internal_sku",
                    "item_payload_sha256",
                    "operation_result",
                    "detail_effect_state",
                    "listing_effect_state",
                    "detail_save_clicked",
                    "action_confirm_clicked",
                    "observed_price_before_action",
                    "observed_inventory_before_action",
                    "observed_price_after_detail_save",
                    "observed_inventory_after_detail_save",
                    "actual_price",
                    "actual_inventory",
                    "detail_save_clicked_at",
                    "action_clicked_at",
                    "readback_observed_at",
                    "error_code",
                    "error_message",
                )
            }
            for item in item_states
        ]
    if phase == "ACTION_CLICKED":
        if current_item is None or clicked_at in (None, ""):
            raise ValidationError("ACTION_CLICKED 必须绑定商品项和 clicked_at。")
        _aware_datetime(clicked_at, "clicked_at")
        payload["clicked_at"] = clicked_at
    payload["phase_snapshot_sha256"] = sha256_json(
        {name: value for name, value in payload.items()}
    )
    validate_listing_action_phase(
        request,
        payload,
        request_file_sha256=request_file_sha256,
    )
    return payload


def validate_listing_action_phase(
    request: dict[str, Any],
    phase: dict[str, Any],
    *,
    request_file_sha256: str,
) -> None:
    if not v5_phase_matches_request(request, phase, request_file_sha256):
        raise ValidationError("LISTING_ACTION_PHASE_BINDING_INVALID")
    allowed = {
        "schema_version",
        "contract_version",
        "action_type",
        "batch_id",
        "execution_attempt_id",
        "instruction_hash",
        "manifest_sha256",
        "request_file_sha256",
        "worker_id",
        "phase",
        "phase_at",
        "current_item",
        "item_states",
        "clicked_at",
        "detail_effect_state",
        "listing_effect_state",
        "phase_snapshot_sha256",
    }
    _reject_unexpected(phase, allowed, "v5 phase")
    phase_name = str(phase.get("phase") or "").strip().upper()
    if phase_name not in V5_PHASE_NAMES:
        raise ValidationError("v5 phase 名称无效。")
    _aware_datetime(phase.get("phase_at"), "phase_at")
    for name in ("detail_effect_state", "listing_effect_state"):
        if phase.get(name) not in V5_SIDE_EFFECT_STATES:
            raise ValidationError(name + " 无效。")
    current_item = phase.get("current_item")
    if current_item is not None:
        if not isinstance(current_item, dict):
            raise ValidationError("phase current_item 必须是对象。")
        expected = {
            str(item.get("operation_id") or ""): item
            for item in request.get("items") or []
        }.get(str(current_item.get("operation_id") or ""))
        if expected is None:
            raise ValidationError("phase current_item operation 绑定无效。")
        for name in (
            "source_task_id",
            "operation_id",
            "item_execution_attempt_id",
            "internal_sku",
            "item_payload_sha256",
        ):
            if str(current_item.get(name) or "") != str(expected.get(name) or ""):
                raise ValidationError("phase current_item 绑定无效。")
    item_states = phase.get("item_states")
    if item_states is not None:
        _validate_phase_item_states(request, item_states)
    if phase_name == "ACTION_CLICKED":
        if current_item is None:
            raise ValidationError("ACTION_CLICKED 缺少 current_item。")
        _aware_datetime(phase.get("clicked_at"), "clicked_at")
    expected_hash = compute_listing_phase_hash(phase)
    if phase.get("phase_snapshot_sha256") != expected_hash:
        raise ValidationError("LISTING_ACTION_PHASE_HASH_MISMATCH")


def compute_listing_phase_hash(phase: dict[str, Any]) -> str:
    return sha256_json(
        {
            name: value
            for name, value in phase.items()
            if name != "phase_snapshot_sha256"
        }
    )


def _validate_phase_item_states(
    request: dict[str, Any],
    item_states: Any,
) -> None:
    if not isinstance(item_states, list):
        raise ValidationError("phase item_states 必须是数组。")
    request_items = request.get("items") or []
    if len(item_states) != len(request_items):
        raise ValidationError("phase item_states 必须覆盖完整批次。")
    expected_by_operation = {
        str(item.get("operation_id") or ""): item for item in request_items
    }
    binding_fields = (
        "source_task_id",
        "operation_id",
        "item_execution_attempt_id",
        "internal_sku",
        "item_payload_sha256",
    )
    seen: set[str] = set()
    for item in item_states:
        if not isinstance(item, dict):
            raise ValidationError("phase item_states 商品项必须是对象。")
        operation_id = str(item.get("operation_id") or "")
        expected = expected_by_operation.get(operation_id)
        if expected is None or operation_id in seen:
            raise ValidationError("phase item_states operation 绑定无效。")
        seen.add(operation_id)
        if any(
            str(item.get(name) or "") != str(expected.get(name) or "")
            for name in binding_fields
        ):
            raise ValidationError("phase item_states 商品绑定无效。")
        if str(item.get("operation_result") or "").upper() not in V5_ITEM_OUTCOMES:
            raise ValidationError("phase item_states operation_result 无效。")
        for name in ("detail_effect_state", "listing_effect_state"):
            if str(item.get(name) or "").upper() not in V5_SIDE_EFFECT_STATES:
                raise ValidationError("phase item_states " + name + " 无效。")
        for name in ("detail_save_clicked", "action_confirm_clicked"):
            if type(item.get(name)) is not bool:
                raise ValidationError("phase item_states " + name + " 必须是布尔值。")
        for name in (
            "detail_save_clicked_at",
            "action_clicked_at",
            "readback_observed_at",
        ):
            if item.get(name) not in (None, ""):
                _aware_datetime(item.get(name), "phase item_states " + name)


def compute_listing_result_hash(result: dict[str, Any]) -> str:
    return sha256_json(
        {
            name: value
            for name, value in result.items()
            if name != "result_payload_sha256"
        }
    )


def build_listing_action_recovery_result(
    request: dict[str, Any],
    phase: dict[str, Any],
    *,
    request_file_sha256: str,
    recovered_at: str,
    worker_id: str,
    error_code: str = "WORKER_INTERRUPTED",
    error_message: str = "stale ShadowBot v5 batch recovered",
) -> dict[str, Any]:
    validate_listing_action_request(request, check_expiry=False)
    validate_listing_action_phase(
        request,
        phase,
        request_file_sha256=request_file_sha256,
    )
    _aware_datetime(recovered_at, "recovered_at")
    phase_name = str(phase.get("phase") or "").strip().upper()
    current_operation = str(
        (phase.get("current_item") or {}).get("operation_id") or ""
    )
    phase_items = {
        str(item.get("operation_id") or ""): item
        for item in phase.get("item_states") or []
        if isinstance(item, dict)
    }
    is_reconcile = (
        str(request.get("execution_mode") or "").upper()
        == "RECONCILE"
    )
    items: list[dict[str, Any]] = []
    for request_item in request.get("items") or []:
        operation_id = str(request_item.get("operation_id") or "")
        state = phase_items.get(operation_id, {})
        is_current = not current_operation or operation_id == current_operation
        outcome = str(state.get("operation_result") or "").strip().upper()
        detail_clicked = bool(state.get("detail_save_clicked"))
        action_clicked = bool(state.get("action_confirm_clicked"))
        detail_effect = str(
            state.get("detail_effect_state") or "NOT_STARTED"
        ).strip().upper()
        listing_effect = str(
            state.get("listing_effect_state") or "NOT_STARTED"
        ).strip().upper()
        if is_reconcile:
            outcome = "NEEDS_RECONCILIATION"
            detail_effect = "UNKNOWN"
            listing_effect = "UNKNOWN"
            detail_clicked = False
            action_clicked = False
        elif outcome not in {"VERIFIED", "ALREADY_APPLIED"}:
            if is_current and phase_name in {
                "ACTION_INTENT_RECORDED",
                "ACTION_CLICKED",
            }:
                outcome = "NEEDS_RECONCILIATION"
                listing_effect = "UNKNOWN"
                action_clicked = action_clicked or phase_name == "ACTION_CLICKED"
            elif is_current and phase_name == "DETAIL_SAVE_INTENT_RECORDED":
                outcome = "NEEDS_RECONCILIATION"
                detail_effect = "UNKNOWN"
                listing_effect = "NOT_STARTED"
            elif detail_clicked:
                outcome = (
                    "PARTIALLY_APPLIED"
                    if detail_effect == "VERIFIED"
                    else "NEEDS_RECONCILIATION"
                )
                listing_effect = "NOT_STARTED"
            elif is_current:
                outcome = "NOT_APPLIED"
                detail_effect = "NOT_APPLIED"
                listing_effect = "NOT_APPLIED"
            else:
                outcome = "NOT_ATTEMPTED"
                detail_effect = "NOT_STARTED"
                listing_effect = "NOT_STARTED"
        item = {
            name: request_item.get(name)
            for name in (
                "source_task_id",
                "operation_id",
                "item_execution_attempt_id",
                "internal_sku",
                "item_payload_sha256",
            )
        }
        item.update(
            {
                "operation_result": outcome,
                "detail_effect_state": detail_effect,
                "listing_effect_state": listing_effect,
                "detail_save_clicked": detail_clicked,
                "action_confirm_clicked": action_clicked,
                "error_code": str(error_code or "WORKER_INTERRUPTED"),
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
            if name in state:
                item[name] = state.get(name)
        items.append(item)
    counts = v5_result_counts(items)
    semantics = derive_v5_batch_semantics(counts)
    result = {
        "schema_version": V5_RESULT_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "action_type": request["action_type"],
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": request["execution_mode"],
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": request_file_sha256,
        "worker_id": _required_value(worker_id, "worker_id"),
        "queue_phase": "RESULT_WRITTEN",
        "worker_heartbeat_at": recovered_at,
        "result_id": "RESULT-" + sha256_json(
            {
                "batch_id": request["batch_id"],
                "execution_attempt_id": request["execution_attempt_id"],
                "recovery": True,
            },
            prefixed=False,
        )[:24],
        "started_at": recovered_at,
        "ended_at": recovered_at,
        "items": items,
        "counts": counts,
        **semantics,
        "error_code": str(error_code or "WORKER_INTERRUPTED"),
        "error_message": str(error_message or "")[:1000],
        "retryable": False,
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    validate_listing_action_result(
        result,
        request=request,
        request_file_sha256=request_file_sha256,
    )
    return result


def validate_listing_action_result(
    result: dict[str, Any],
    *,
    request: dict[str, Any] | None = None,
    request_file_sha256: str | None = None,
) -> None:
    if not isinstance(result, dict):
        raise ValidationError("LISTING_ACTION_RESULT_INVALID")
    if result.get("schema_version") != V5_RESULT_SCHEMA_VERSION:
        raise ValidationError("LISTING_ACTION_RESULT_INVALID")
    if result.get("contract_version") != V5_CONTRACT_VERSION:
        raise ValidationError("UNKNOWN_CONTRACT_VERSION")
    action = _action_type(result.get("action_type"))
    _validate_id(str(result.get("batch_id") or ""), "batch_id")
    _validate_id(
        str(result.get("execution_attempt_id") or ""),
        "execution_attempt_id",
    )
    for name in (
        "manifest_sha256",
        "instruction_hash",
        "request_file_sha256",
    ):
        if not _SHA256_RE.fullmatch(str(result.get(name) or "")):
            raise ValidationError(name + " 无效。")
    for name in ("started_at", "ended_at"):
        _aware_datetime(result.get(name), name)
    if action == "sync_status":
        if str(result.get("execution_mode") or "").upper() != "READ_ONLY":
            raise ValidationError("LISTING_ACTION_EXECUTION_MODE_INVALID")
        snapshot = result.get("snapshot")
        validate_listing_sync_snapshot(snapshot)
        for field_name in (
            "execution_attempt_id",
            "instruction_hash",
            "result_id",
        ):
            if str(snapshot.get(field_name) or "") != str(
                result.get(field_name) or ""
            ):
                raise ValidationError(
                    "LISTING_ACTION_RESULT_SNAPSHOT_BINDING_INVALID"
                )
    else:
        execution_mode = str(result.get("execution_mode") or "").upper()
        if execution_mode not in {"COMMIT", "RECONCILE"}:
            raise ValidationError("LISTING_ACTION_EXECUTION_MODE_INVALID")
        items = result.get("items")
        try:
            counts = v5_result_counts(items)
            semantics = derive_v5_batch_semantics(counts)
        except ValueError as exc:
            raise ValidationError("LISTING_ACTION_RESULT_ITEM_INVALID") from exc
        if result.get("counts") != counts:
            raise ValidationError("LISTING_ACTION_RESULT_COUNTS_MISMATCH")
        for name, expected in semantics.items():
            if result.get(name) != expected:
                raise ValidationError("LISTING_ACTION_RESULT_SEMANTICS_MISMATCH")
        if result.get("batch_status") not in V5_BATCH_STATUSES:
            raise ValidationError("LISTING_ACTION_RESULT_STATUS_INVALID")
        for item in items:
            if (
                str(item.get("operation_result") or "").upper()
                not in V5_ITEM_OUTCOMES
            ):
                raise ValidationError("LISTING_ACTION_RESULT_ITEM_INVALID")
        if execution_mode == "RECONCILE":
            if len(items) != 1:
                raise ValidationError("RECONCILE 结果必须且只能包含一个商品。")
            if any(
                bool(item.get(name))
                for item in items
                for name in (
                    "detail_save_clicked",
                    "action_confirm_clicked",
                )
            ):
                raise ValidationError("RECONCILE 结果不得包含写入点击。")
        post_failure_snapshot = result.get("post_failure_snapshot")
        if post_failure_snapshot is not None:
            validate_listing_sync_snapshot(post_failure_snapshot)
            for field_name in (
                "execution_attempt_id",
                "instruction_hash",
                "result_id",
            ):
                if str(post_failure_snapshot.get(field_name) or "") != str(
                    result.get(field_name) or ""
                ):
                    raise ValidationError(
                        "LISTING_ACTION_POST_FAILURE_SNAPSHOT_BINDING_INVALID"
                    )
            if request is not None and str(
                post_failure_snapshot.get("platform_name") or ""
            ) != str(request.get("platform_name") or ""):
                raise ValidationError(
                    "LISTING_ACTION_POST_FAILURE_SNAPSHOT_BINDING_INVALID"
                )
    if request is not None:
        if result.get("batch_id") != request.get("batch_id"):
            raise ValidationError("LISTING_ACTION_RESULT_BINDING_INVALID")
        if result.get("execution_attempt_id") != request.get(
            "execution_attempt_id"
        ):
            raise ValidationError("LISTING_ACTION_RESULT_BINDING_INVALID")
        if result.get("instruction_hash") != request.get("instruction_hash"):
            raise ValidationError("LISTING_ACTION_RESULT_BINDING_INVALID")
        if result.get("manifest_sha256") != request.get("manifest_sha256"):
            raise ValidationError("LISTING_ACTION_RESULT_BINDING_INVALID")
        if action != request.get("action_type"):
            raise ValidationError("LISTING_ACTION_RESULT_BINDING_INVALID")
        if action in V5_WRITE_ACTION_TYPES:
            _validate_result_item_bindings(
                result.get("items"),
                request.get("items"),
            )
    if (
        request_file_sha256 is not None
        and result.get("request_file_sha256") != request_file_sha256
    ):
        raise ValidationError("LISTING_ACTION_RESULT_BINDING_INVALID")
    if result.get("result_payload_sha256") != compute_listing_result_hash(result):
        raise ValidationError("LISTING_ACTION_RESULT_HASH_MISMATCH")


def validate_listing_sync_snapshot(snapshot: dict[str, Any] | None) -> None:
    if not isinstance(snapshot, dict):
        raise ValidationError("LISTING_SYNC_SNAPSHOT_INVALID")
    allowed = {
        "schema_version",
        "snapshot_id",
        "platform_name",
        "execution_attempt_id",
        "mapping_source_version",
        "result_id",
        "scan_started_at",
        "scan_completed_at",
        "online_scan_started_at",
        "online_scan_completed_at",
        "waiting_scan_started_at",
        "waiting_scan_completed_at",
        "online_scan_complete",
        "waiting_scan_complete",
        "online_end_marker_verified",
        "waiting_end_marker_verified",
        "snapshot_complete",
        "instruction_hash",
        "status",
        "error_code",
        "evidence_manifest_sha256",
        "items",
    }
    _reject_unexpected(snapshot, allowed, "listing sync snapshot")
    if snapshot.get("schema_version") != V5_SNAPSHOT_SCHEMA_VERSION:
        raise ValidationError("LISTING_SYNC_SNAPSHOT_INVALID")
    for name in (
        "snapshot_id",
        "platform_name",
        "execution_attempt_id",
        "mapping_source_version",
        "result_id",
    ):
        _required_value(snapshot.get(name), name)
    for name in (
        "scan_started_at",
        "scan_completed_at",
        "online_scan_started_at",
        "online_scan_completed_at",
        "waiting_scan_started_at",
        "waiting_scan_completed_at",
    ):
        _aware_datetime(snapshot.get(name), name)
    for name in (
        "online_scan_complete",
        "waiting_scan_complete",
        "online_end_marker_verified",
        "waiting_end_marker_verified",
        "snapshot_complete",
    ):
        if type(snapshot.get(name)) is not bool:
            raise ValidationError(name + " 必须是布尔值。")
    scan_started = _aware_datetime(snapshot.get("scan_started_at"), "scan_started_at")
    scan_completed = _aware_datetime(
        snapshot.get("scan_completed_at"),
        "scan_completed_at",
    )
    online_started = _aware_datetime(
        snapshot.get("online_scan_started_at"),
        "online_scan_started_at",
    )
    online_completed = _aware_datetime(
        snapshot.get("online_scan_completed_at"),
        "online_scan_completed_at",
    )
    waiting_started = _aware_datetime(
        snapshot.get("waiting_scan_started_at"),
        "waiting_scan_started_at",
    )
    waiting_completed = _aware_datetime(
        snapshot.get("waiting_scan_completed_at"),
        "waiting_scan_completed_at",
    )
    if not (
        scan_started
        <= online_started
        <= online_completed
        <= waiting_started
        <= waiting_completed
        <= scan_completed
    ):
        raise ValidationError("LISTING_SYNC_SNAPSHOT_TIME_ORDER_INVALID")
    complete_flags = (
        snapshot.get("online_scan_complete") is True,
        snapshot.get("waiting_scan_complete") is True,
        snapshot.get("online_end_marker_verified") is True,
        snapshot.get("waiting_end_marker_verified") is True,
    )
    if snapshot.get("snapshot_complete") != all(complete_flags):
        raise ValidationError("LISTING_SYNC_SNAPSHOT_COMPLETENESS_INVALID")
    if not _SHA256_RE.fullmatch(str(snapshot.get("instruction_hash") or "")):
        raise ValidationError("snapshot instruction_hash 无效。")
    if not _SHA256_RE.fullmatch(
        str(snapshot.get("evidence_manifest_sha256") or "")
    ):
        raise ValidationError("snapshot evidence_manifest_sha256 无效。")
    status = str(snapshot.get("status") or "").strip().upper()
    if status not in {"VERIFIED", "FAILED"}:
        raise ValidationError("LISTING_SYNC_SNAPSHOT_STATUS_INVALID")
    if snapshot.get("snapshot_complete") is True:
        if status != "VERIFIED" or snapshot.get("error_code") not in (None, ""):
            raise ValidationError("LISTING_SYNC_SNAPSHOT_STATUS_INVALID")
    elif status != "FAILED" or not str(snapshot.get("error_code") or "").strip():
        raise ValidationError("LISTING_SYNC_SNAPSHOT_STATUS_INVALID")
    items = snapshot.get("items")
    if not isinstance(items, list):
        raise ValidationError("LISTING_SYNC_SNAPSHOT_ITEMS_INVALID")
    if snapshot.get("snapshot_complete") is not True and items:
        raise ValidationError("失败快照不得生成 listing_location 商品项。")
    for item in items:
        _validate_snapshot_item(item)


def validate_listing_anomaly(anomaly: dict[str, Any]) -> None:
    if not isinstance(anomaly, dict):
        raise ValidationError("LISTING_ANOMALY_INVALID")
    allowed = {
        "schema_version",
        "anomaly_case_id",
        "snapshot_id",
        "snapshot_item_id",
        "platform_name",
        "internal_sku",
        "page_identity_key",
        "affected_internal_skus",
        "anomaly_subject_key",
        "dedupe_key",
        "reason_code",
        "diagnostic_message",
        "resolution_policy",
        "blocked_actions",
        "review_task_id",
        "created_at",
        "cleared_at",
        "cleared_by_snapshot_id",
    }
    _reject_unexpected(anomaly, allowed, "listing anomaly")
    if anomaly.get("schema_version") != V5_ANOMALY_SCHEMA_VERSION:
        raise ValidationError("LISTING_ANOMALY_INVALID")
    for name in (
        "anomaly_case_id",
        "snapshot_id",
        "snapshot_item_id",
        "platform_name",
        "page_identity_key",
        "anomaly_subject_key",
        "dedupe_key",
        "diagnostic_message",
    ):
        _required_value(anomaly.get(name), name)
    for name in ("anomaly_case_id", "snapshot_id", "snapshot_item_id"):
        _validate_id(str(anomaly[name]), name)
    reason = str(anomaly.get("reason_code") or "").strip().upper()
    if reason not in V5_ANOMALY_REASON_CODES:
        raise ValidationError("listing anomaly reason_code 无效。")
    policy = str(anomaly.get("resolution_policy") or "").strip().upper()
    if policy not in V5_ANOMALY_RESOLUTION_POLICIES:
        raise ValidationError("listing anomaly resolution_policy 无效。")
    blocked_actions = anomaly.get("blocked_actions")
    if (
        not isinstance(blocked_actions, list)
        or not blocked_actions
        or any(
            str(action or "").strip().lower()
            not in {"update_price", "set_online", "set_offline"}
            for action in blocked_actions
        )
    ):
        raise ValidationError("listing anomaly blocked_actions 无效。")
    affected = anomaly.get("affected_internal_skus")
    if not isinstance(affected, list):
        raise ValidationError("affected_internal_skus 必须是数组。")
    normalized_affected = [
        normalize_contract_sku(sku) for sku in affected
    ]
    if normalized_affected != sorted(set(normalized_affected)):
        raise ValidationError("affected_internal_skus 必须规范、排序且去重。")
    internal_sku = anomaly.get("internal_sku")
    normalized_sku = (
        normalize_contract_sku(internal_sku)
        if internal_sku not in (None, "")
        else None
    )
    if reason == "UNMAPPED_PRODUCT":
        if normalized_sku is not None or normalized_affected:
            raise ValidationError("UNMAPPED_PRODUCT 不得绑定 internal_sku。")
    elif reason == "IDENTITY_MAPPING_CONFLICT":
        if len(normalized_affected) < 2:
            raise ValidationError(
                "IDENTITY_MAPPING_CONFLICT 必须绑定多个受影响 SKU。"
            )
    elif normalized_sku is None:
        raise ValidationError(reason + " 必须绑定 internal_sku。")
    if normalized_sku is not None and internal_sku != normalized_sku:
        raise ValidationError("internal_sku 必须使用规范大写格式。")
    _aware_datetime(anomaly.get("created_at"), "created_at")
    cleared_at = anomaly.get("cleared_at")
    cleared_by = anomaly.get("cleared_by_snapshot_id")
    if (cleared_at in (None, "")) != (cleared_by in (None, "")):
        raise ValidationError("异常清除时间与快照必须同时存在。")
    if cleared_at not in (None, ""):
        _aware_datetime(cleared_at, "cleared_at")
        _validate_id(str(cleared_by), "cleared_by_snapshot_id")
    review_task_id = anomaly.get("review_task_id")
    if review_task_id not in (None, ""):
        _validate_id(str(review_task_id), "review_task_id")


def required_development_confirmation(batch_id: str, item_count: int) -> str:
    return f"确认授权批次 {batch_id} 以上{item_count}项真实COMMIT"


def _build_write_items(
    *,
    batch_id: str,
    action_type: str,
    task_items: list[dict[str, Any]] | None,
    identity_mapping: Mapping[str, Mapping[str, str]] | None,
    platform_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(task_items, list) or not task_items or len(task_items) > MAX_ITEMS:
        raise ValidationError(f"v5 写任务数量必须在 1 到 {MAX_ITEMS} 之间。")
    if not isinstance(identity_mapping, Mapping):
        raise ValidationError("v5 写任务缺少 SKU 页面身份映射。")
    normalized_mapping = {
        str(key).strip().upper(): value for key, value in identity_mapping.items()
    }
    seen_tasks: set[str] = set()
    seen_skus: set[str] = set()
    seen_page_identities: set[tuple[str, str, str]] = set()
    items: list[dict[str, Any]] = []
    for raw in task_items:
        if not isinstance(raw, dict):
            raise ValidationError("v5 写任务项必须是对象。")
        if _FORBIDDEN_POSITION_FIELDS.intersection(raw):
            raise ValidationError("v5 task item 禁止页面位置字段。")
        _reject_unexpected(raw, _WRITE_ITEM_INPUT_FIELDS, "v5 task item")
        task_id = _required_value(raw.get("source_task_id"), "source_task_id")
        _validate_id(task_id, "source_task_id")
        sku = _required_value(raw.get("internal_sku"), "internal_sku").upper()
        if task_id in seen_tasks or sku in seen_skus:
            raise ValidationError("同一 v5 批次任务 ID 或 SKU 重复。")
        seen_tasks.add(task_id)
        seen_skus.add(sku)
        identity = normalized_mapping.get(sku)
        if not isinstance(identity, Mapping):
            raise ValidationError("SKU 未找到唯一启用映射：" + sku)
        product_name = _required_value(
            identity.get("expected_product_name"),
            "expected_product_name",
        )
        grade = _required_value(identity.get("expected_grade"), "expected_grade")
        page_identity = (
            normalize_contract_text(platform_name),
            normalize_contract_text(product_name),
            normalize_contract_grade(grade),
        )
        if page_identity in seen_page_identities:
            raise ValidationError("多个 SKU 解析为同一平台页面身份。")
        seen_page_identities.add(page_identity)
        expected_old_status = _required_value(
            raw.get("expected_old_status"),
            "expected_old_status",
        ).lower()
        target_status = _required_value(
            raw.get("target_status"),
            "target_status",
        ).lower()
        expected_pair = (
            ("offline", "online")
            if action_type == "set_online"
            else ("online", "offline")
        )
        if (expected_old_status, target_status) != expected_pair:
            raise ValidationError("v5 动作与上下架状态条件不匹配。")
        expires_at = _required_value(raw.get("expires_at"), "expires_at")
        _aware_datetime(expires_at, "expires_at")
        item: dict[str, Any] = {
            "source_task_id": task_id,
            "internal_sku": sku,
            "expected_product_name": product_name,
            "expected_grade": grade,
            "action_type": action_type,
            "expected_old_status": expected_old_status,
            "target_status": target_status,
            "target_price": None,
            "target_inventory": None,
            "task_expires_at": expires_at,
        }
        if action_type == "set_online":
            try:
                item["target_price"] = canonical_positive_price(
                    raw.get("target_price"),
                    require_canonical=True,
                    reject_float=True,
                )
                item["target_inventory"] = canonical_nonnegative_inventory(
                    raw.get("target_inventory"),
                    require_canonical=True,
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        elif "target_price" in raw or "target_inventory" in raw:
            raise ValidationError("SET_OFFLINE 不得携带目标价格或目标库存。")
        item["item_payload_sha256"] = sha256_json(
            _item_payload(platform_name, item)
        )
        item["item_id"] = _stable_id(
            "ITEM",
            {"batch_id": batch_id, "source_task_id": task_id},
        )
        item["operation_id"] = _stable_id(
            "OP",
            {
                "batch_id": batch_id,
                "source_task_id": task_id,
                "item_payload_sha256": item["item_payload_sha256"],
            },
        )
        item["write_identity_key"] = contract_identity_key(
            platform_name,
            sku,
            product_name,
            grade,
        )
        item["page_identity_key"] = contract_identity_key(
            platform_name,
            None,
            product_name,
            grade,
        )
        items.append(item)
    return items


def _validate_write_items(
    platform_name: str,
    action_type: str,
    items: Any,
    *,
    batch_id: str,
    execution_attempt_id: str = "",
    require_attempt_id: bool = False,
    allow_legacy_operation_id: bool = False,
) -> None:
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ValidationError("v5 写任务 items 数量无效。")
    seen_tasks: set[str] = set()
    seen_skus: set[str] = set()
    seen_page_keys: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("v5 item 必须是对象。")
        if _FORBIDDEN_POSITION_FIELDS.intersection(item):
            raise ValidationError("v5 item 禁止页面位置字段。")
        _reject_unexpected(item, _WRITE_ITEM_FIELDS, "v5 item")
        for name in (
            "item_id",
            "source_task_id",
            "operation_id",
            "internal_sku",
            "expected_product_name",
            "expected_grade",
            "write_identity_key",
            "page_identity_key",
        ):
            _required_value(item.get(name), name)
        for name in ("item_id", "source_task_id", "operation_id"):
            _validate_id(str(item[name]), name)
        task_id = str(item["source_task_id"])
        sku = str(item["internal_sku"]).upper()
        page_key = str(item["page_identity_key"])
        if task_id in seen_tasks or sku in seen_skus or page_key in seen_page_keys:
            raise ValidationError("v5 item 任务、SKU 或页面身份重复。")
        seen_tasks.add(task_id)
        seen_skus.add(sku)
        seen_page_keys.add(page_key)
        if normalize_contract_sku(item["internal_sku"]) != sku:
            raise ValidationError("internal_sku 必须使用规范大写格式。")
        if item.get("action_type") != action_type:
            raise ValidationError("一个 v5 批次只能包含一种动作。")
        expected_pair = (
            ("offline", "online")
            if action_type == "set_online"
            else ("online", "offline")
        )
        if (
            item.get("expected_old_status"),
            item.get("target_status"),
        ) != expected_pair:
            raise ValidationError("v5 item 状态条件与动作不匹配。")
        if action_type == "set_online":
            try:
                canonical_positive_price(
                    item.get("target_price"),
                    require_canonical=True,
                    reject_float=True,
                )
                canonical_nonnegative_inventory(
                    item.get("target_inventory"),
                    require_canonical=True,
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        elif item.get("target_price") is not None or item.get(
            "target_inventory"
        ) is not None:
            raise ValidationError("SET_OFFLINE item 不得携带目标价格或库存。")
        _aware_datetime(item.get("task_expires_at"), "task_expires_at")
        if item.get("write_identity_key") != contract_identity_key(
            platform_name,
            sku,
            item["expected_product_name"],
            item["expected_grade"],
        ):
            raise ValidationError("write_identity_key 不匹配。")
        if item.get("page_identity_key") != contract_identity_key(
            platform_name,
            None,
            item["expected_product_name"],
            item["expected_grade"],
        ):
            raise ValidationError("page_identity_key 不匹配。")
        if item.get("item_payload_sha256") != sha256_json(
            _item_payload(platform_name, item)
        ):
            raise ValidationError("item_payload_sha256 不匹配。")
        if item.get("item_id") != _stable_id(
            "ITEM",
            {"batch_id": batch_id, "source_task_id": task_id},
        ):
            raise ValidationError("item_id 不匹配。")
        expected_operation_id = _stable_id(
            "OP",
            {
                "batch_id": batch_id,
                "source_task_id": task_id,
                "item_payload_sha256": item["item_payload_sha256"],
            },
        )
        if item.get("operation_id") != expected_operation_id:
            legacy_operation_id = _stable_id(
                "OP",
                {
                    "source_task_id": task_id,
                    "item_payload_sha256": item["item_payload_sha256"],
                },
            )
            if (
                not allow_legacy_operation_id
                or item.get("operation_id") != legacy_operation_id
            ):
                raise ValidationError("operation_id 不匹配。")
        if require_attempt_id:
            expected_attempt = _stable_id(
                "ATTEMPT",
                {
                    "execution_attempt_id": execution_attempt_id,
                    "item_id": item["item_id"],
                },
            )
            if item.get("item_execution_attempt_id") != expected_attempt:
                raise ValidationError("item_execution_attempt_id 不匹配。")


def _validate_snapshot_item(item: Any) -> None:
    if not isinstance(item, dict):
        raise ValidationError("snapshot item 必须是对象。")
    allowed = {
        "snapshot_item_id",
        "internal_sku",
        "product_name",
        "grade",
        "page_identity_key",
        "affected_internal_skus",
        "online_occurrences",
        "waiting_occurrences",
        "mapping_ambiguous",
        "listing_location",
        "online_row_identities",
        "waiting_row_identities",
        "online_observed_price",
        "waiting_observed_price",
        "online_observed_inventory",
        "waiting_observed_inventory",
        "diagnostic_code",
        "online_observed_at",
        "waiting_observed_at",
    }
    _reject_unexpected(item, allowed, "snapshot item")
    for name in (
        "snapshot_item_id",
        "product_name",
        "grade",
        "page_identity_key",
        "listing_location",
    ):
        _required_value(item.get(name), name)
    _validate_id(str(item["snapshot_item_id"]), "snapshot_item_id")
    try:
        if type(item.get("mapping_ambiguous")) is not bool:
            raise ValueError("mapping_ambiguous 必须是布尔值。")
        for count_name in ("online_occurrences", "waiting_occurrences"):
            count_value = item.get(count_name)
            if type(count_value) is not int or count_value < 0:
                raise ValueError(count_name + " 必须是非负整数。")
        derived = derive_listing_location(
            item.get("online_occurrences"),
            item.get("waiting_occurrences"),
            mapping_ambiguous=bool(item.get("mapping_ambiguous")),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if item.get("listing_location") not in V5_LISTING_LOCATIONS:
        raise ValidationError("snapshot item listing_location 无效。")
    if item.get("listing_location") != derived:
        raise ValidationError("snapshot item listing_location 与出现次数不一致。")
    if item.get("internal_sku") in (None, ""):
        if not item.get("diagnostic_code"):
            raise ValidationError("无 SKU snapshot item 必须包含 diagnostic_code。")
    else:
        try:
            normalized_sku = normalize_contract_sku(item["internal_sku"])
        except ValueError as exc:
            raise ValidationError("snapshot item internal_sku 无效。") from exc
        if item["internal_sku"] != normalized_sku:
            raise ValidationError("snapshot item internal_sku 必须规范化。")
    affected = item.get("affected_internal_skus")
    if not isinstance(affected, list):
        raise ValidationError("affected_internal_skus 必须是数组。")
    try:
        normalized_affected = [
            normalize_contract_sku(sku) for sku in affected
        ]
    except ValueError as exc:
        raise ValidationError("affected_internal_skus 包含无效 SKU。") from exc
    if normalized_affected != sorted(set(normalized_affected)):
        raise ValidationError("affected_internal_skus 必须规范、排序且去重。")
    for page in ("online", "waiting"):
        count = int(item[f"{page}_occurrences"])
        row_identities = item.get(f"{page}_row_identities")
        if not isinstance(row_identities, list) or len(row_identities) != count:
            raise ValidationError(page + " row identities 数量不匹配。")
        if (
            any(not str(identity or "").strip() for identity in row_identities)
            or len(row_identities) != len(set(row_identities))
        ):
            raise ValidationError(page + " row identities 必须非空且唯一。")
        observed_fields = (
            f"{page}_observed_price",
            f"{page}_observed_inventory",
            f"{page}_observed_at",
        )
        if count:
            if any(item.get(name) in (None, "") for name in observed_fields):
                raise ValidationError(page + " 页面观察字段不完整。")
            try:
                canonical_positive_price(
                    item[f"{page}_observed_price"],
                    require_canonical=True,
                    reject_float=True,
                )
                canonical_nonnegative_inventory(
                    item[f"{page}_observed_inventory"],
                    require_canonical=True,
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            _aware_datetime(item[f"{page}_observed_at"], f"{page}_observed_at")
        elif any(item.get(name) not in (None, "") for name in observed_fields):
            raise ValidationError(page + " 不存在时不得携带页面观察值。")


def _validate_result_item_bindings(
    result_items: Any,
    request_items: Any,
) -> None:
    if not isinstance(result_items, list) or not isinstance(request_items, list):
        raise ValidationError("LISTING_ACTION_RESULT_ITEM_BINDING_INVALID")
    if len(result_items) != len(request_items):
        raise ValidationError("LISTING_ACTION_RESULT_ITEM_BINDING_INVALID")
    expected_by_operation = {
        str(item.get("operation_id") or ""): item for item in request_items
    }
    if "" in expected_by_operation or len(expected_by_operation) != len(
        request_items
    ):
        raise ValidationError("LISTING_ACTION_RESULT_ITEM_BINDING_INVALID")
    seen_operations: set[str] = set()
    binding_fields = (
        "source_task_id",
        "operation_id",
        "item_execution_attempt_id",
        "internal_sku",
        "item_payload_sha256",
    )
    for item in result_items:
        if not isinstance(item, dict):
            raise ValidationError("LISTING_ACTION_RESULT_ITEM_BINDING_INVALID")
        operation_id = str(item.get("operation_id") or "")
        expected = expected_by_operation.get(operation_id)
        if expected is None or operation_id in seen_operations:
            raise ValidationError("LISTING_ACTION_RESULT_ITEM_BINDING_INVALID")
        seen_operations.add(operation_id)
        if any(
            str(item.get(name) or "") != str(expected.get(name) or "")
            for name in binding_fields
        ):
            raise ValidationError("LISTING_ACTION_RESULT_ITEM_BINDING_INVALID")


def _manifest_sha256(manifest: Mapping[str, Any]) -> str:
    canonical = {
        "schema_version": V5_MANIFEST_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "batch_id": manifest.get("batch_id"),
        "action_type": manifest.get("action_type"),
        "execution_mode": manifest.get("execution_mode"),
        "platform_name": manifest.get("platform_name"),
        "mapping_source_version": manifest.get("mapping_source_version"),
        "scan_scope": manifest.get("scan_scope"),
        "items": sorted(
            [
                {
                    name: item.get(name)
                    for name in _WRITE_ITEM_FIELDS
                    if name != "item_execution_attempt_id" and name in item
                }
                for item in manifest.get("items") or []
            ],
            key=lambda value: (
                str(value.get("source_task_id") or ""),
                str(value.get("internal_sku") or ""),
            ),
        ),
    }
    return sha256_json(canonical)


def _item_payload(platform_name: str, item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "platform_name": str(platform_name).strip(),
        "source_task_id": item.get("source_task_id"),
        "internal_sku": item.get("internal_sku"),
        "expected_product_name": item.get("expected_product_name"),
        "expected_grade": item.get("expected_grade"),
        "action_type": item.get("action_type"),
        "expected_old_status": item.get("expected_old_status"),
        "target_status": item.get("target_status"),
        "target_price": item.get("target_price"),
        "target_inventory": item.get("target_inventory"),
        "task_expires_at": item.get("task_expires_at"),
    }


def _validate_development_confirmation(
    confirmation: Any,
    *,
    batch_id: str,
    item_count: int,
) -> None:
    if not isinstance(confirmation, dict):
        raise ValidationError("开发阶段 v5 写请求缺少人工确认。")
    allowed = {"confirmed_by", "confirmed_at", "confirmation_text"}
    _reject_unexpected(confirmation, allowed, "development_confirmation")
    for name in allowed:
        _required_value(confirmation.get(name), name)
    _aware_datetime(confirmation["confirmed_at"], "confirmed_at")
    if confirmation["confirmation_text"] != required_development_confirmation(
        batch_id,
        item_count,
    ):
        raise ValidationError("开发阶段 v5 确认文本不匹配。")


def _action_type(value: Any) -> str:
    action = str(value or "").strip().lower()
    if action not in V5_ACTION_TYPES:
        raise ValidationError("v5 action_type 无效。")
    return action


def _required_value(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValidationError(name + " 不能为空。")
    return normalized


def _validate_id(value: str, name: str) -> None:
    if not _ID_RE.fullmatch(str(value or "")):
        raise ValidationError(name + " 格式无效。")


def _aware_datetime(value: Any, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ValidationError(name + " 必须是 ISO-8601 时间。") from exc
    if parsed.tzinfo is None:
        raise ValidationError(name + " 必须包含时区。")
    return parsed


def _reject_unexpected(
    payload: Mapping[str, Any],
    allowed: set[str] | frozenset[str],
    label: str,
) -> None:
    unexpected = sorted(set(payload) - set(allowed))
    if unexpected:
        raise ValidationError(label + " 包含非合同字段：" + ", ".join(unexpected))


def _stable_id(prefix: str, payload: Any) -> str:
    return prefix + "-" + sha256_json(payload, prefixed=False)[:24]
