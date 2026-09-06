import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import xbot  # noqa: F401 - ShadowBot runtime import initializes host bindings.
from xbot import print, sleep, win32
from xbot.selector import Selector

from . import package

try:
    from app.emergency_offline_fence import (
        EmergencyOfflineFenceError,
        record_emergency_final_click_fence_won,
        revalidate_emergency_offline_facts,
    )
except ImportError:
    try:
        from .emergency_offline_fence import (
            EmergencyOfflineFenceError,
            record_emergency_final_click_fence_won,
            revalidate_emergency_offline_facts,
        )
    except ImportError:
        from emergency_offline_fence import (
            EmergencyOfflineFenceError,
            record_emergency_final_click_fence_won,
            revalidate_emergency_offline_facts,
        )

try:
    from app.shadowbot_contract_primitives import (
        ORDER_SCAN_CONTRACT_VERSION,
        ORDER_SCAN_RESULT_SCHEMA_VERSION,
        contract_identity_key,
        derive_v4_batch_semantics,
        derive_v5_batch_semantics,
        normalize_contract_grade,
        normalize_contract_sku,
        normalize_contract_text,
        normalize_order_scan_request,
        set_offline_confirmation_matches,
        set_online_confirmation_matches,
        sha256_json,
        v4_result_counts,
        v5_result_counts,
    )
except ImportError:
    try:
        from .shadowbot_contract_primitives import (
            ORDER_SCAN_CONTRACT_VERSION,
            ORDER_SCAN_RESULT_SCHEMA_VERSION,
            contract_identity_key,
            derive_v4_batch_semantics,
            derive_v5_batch_semantics,
            normalize_contract_grade,
            normalize_contract_sku,
            normalize_contract_text,
            normalize_order_scan_request,
            set_offline_confirmation_matches,
            set_online_confirmation_matches,
            sha256_json,
            v4_result_counts,
            v5_result_counts,
        )
    except ImportError:
        from shadowbot_contract_primitives import (
            ORDER_SCAN_CONTRACT_VERSION,
            ORDER_SCAN_RESULT_SCHEMA_VERSION,
            contract_identity_key,
            derive_v4_batch_semantics,
            derive_v5_batch_semantics,
            normalize_contract_grade,
            normalize_contract_sku,
            normalize_contract_text,
            normalize_order_scan_request,
            set_offline_confirmation_matches,
            set_online_confirmation_matches,
            sha256_json,
            v4_result_counts,
            v5_result_counts,
        )


WINDOW_TITLE_DEFAULT = "蚂蚁花团供应商"
WINDOW_X_DEFAULT = 0
WINDOW_Y_DEFAULT = 0
WINDOW_WIDTH_DEFAULT = 562
WINDOW_HEIGHT_DEFAULT = 1056
ELEMENT_TIMEOUT_DEFAULT = 15
APPLET_LAUNCH_TIMEOUT_DEFAULT = 20
SINGLE_PRODUCT_SCROLL_START_POSITION = 4
SINGLE_PRODUCT_MAX_SCROLL_ATTEMPTS = 3
SINGLE_PRODUCT_SCROLL_WHEEL_TIMES = 5
SINGLE_PRODUCT_CLICK_TOP_MARGIN = 200
SINGLE_PRODUCT_CLICK_BOTTOM_MARGIN = 90
MAX_PAGE_POSITION_HINT = 100
INLINE_OLD_PRICE_MAX_AGE_SECONDS = 60
FAST_POST_SUBMIT_VERIFY_SECONDS = 6
APPLET_URI_PREFIXES = ("weixin://launchapplet/",)

ROW_INDEX_START = 1
ROW_INDEX_STEP = 16
WAITING_ROW_INDEX_STEP = 15
INDEXED_ENUMERATION_MAX_ROWS = 50
PRICE_INDEX_OFFSET = 9
WAITING_PRICE_INDEX_OFFSET = 8
INVENTORY_INDEX_OFFSET = 6
INVENTORY_TEXT_INDEX = 0
DEFAULT_MAX_PRODUCT_ROWS = 3

ELEMENTS = {
    "product_management": "蚂蚁_首页_商品管理_入口",
    "target_container": "蚂蚁_商品管理_目标商品_容器",
    "price_popup": "价格弹窗_容器",
    "price_input": "价格弹窗_修改后价格_输入框",
    "price_cancel": "价格弹窗_取消按钮",
    "price_confirm": "价格弹窗_确认按钮",
    "dialog_product_name": "价格弹窗_当前商品_值模板",
    "dialog_grade": "价格弹窗_当前等级_值模板",
    "dialog_current_price": "价格弹窗_当前价格_值模板",
}

SELECTOR_TEMPLATES = {
    "name": "商品_2_名称",
    "grade": "商品_2_等级",
    "inventory": "商品_1_库存",
}
WAITING_SELECTOR_TEMPLATES = {
    "name": "待上架_商品1_名称",
    "grade": "待上架_商品1_等级",
    "inventory": "待上架_商品1_库存",
}

DIALOG_VALUE_CHILD_INDEXES = {
    ELEMENTS["dialog_product_name"]: 3,
    ELEMENTS["dialog_grade"]: 5,
    ELEMENTS["dialog_current_price"]: 7,
}
ONLINE_LIST_LABEL = "上架中"
WAITING_LIST_LABEL = "待上架"
WAITING_LIST_SELECTOR = "按钮_待上架"
ONLINE_SET_OFFLINE_BUTTON_SELECTOR = "上架中_商品1_下架按钮"
ONLINE_SET_OFFLINE_PROMPT_SELECTOR = "上架中_下架确认弹窗_提示文本"
ONLINE_SET_OFFLINE_CONFIRM_SELECTOR = "上架中_下架确认弹窗_确认按钮"
ONLINE_SET_OFFLINE_CANCEL_SELECTOR = "上架中_下架确认弹窗_取消按钮"
WAITING_SET_ONLINE_BUTTON_SELECTOR = "待上架_商品1_上架按钮"
WAITING_SET_ONLINE_PROMPT_SELECTOR = "待上架_上架确认弹窗_提示文本"
WAITING_SET_ONLINE_CONFIRM_SELECTOR = "待上架_上架确认弹窗_确认按钮"
WAITING_SET_ONLINE_CANCEL_SELECTOR = "待上架_上架确认弹窗_取消按钮"
WAITING_PRICE_POPUP_SELECTOR = "待上架_商品1_修改价格弹窗"
WAITING_PRICE_INPUT_SELECTOR = "待上架_商品1_修改价格弹窗_价格输入框"
WAITING_PRICE_CANCEL_SELECTOR = "待上架_商品1_修改价格弹窗_取消按钮"
WAITING_PRICE_CONFIRM_SELECTOR = "待上架_商品1_修改价格弹窗_确认按钮"
WAITING_INVENTORY_POPUP_SELECTOR = "待上架_修改库存弹窗"
WAITING_INVENTORY_INPUT_SELECTOR = "待上架_商品1_修改库存弹窗_库存输入框"
WAITING_INVENTORY_CANCEL_SELECTOR = "待上架_商品1_修改库存弹窗_取消按钮"
WAITING_INVENTORY_CONFIRM_SELECTOR = "待上架_商品1_修改库存弹窗_确认按钮"
WAITING_SET_ONLINE_INDEX_OFFSET = 13
ONLINE_SET_OFFLINE_INDEX_OFFSET = 14
PRODUCT_LIST_END_LABEL = "没有更多了"
PRODUCT_LIST_EMPTY_LABEL = "暂无商品"
ORDER_MANAGEMENT_ENTRY_SELECTOR = "蚂蚁_订单管理_入口"
ORDER_LIST_CONTAINER_SELECTOR = "订单管理_容器"
ORDER_ROW_SELECTOR_TEMPLATES = {
    "grade": "订单管理_订单1_等级",
    "platform_product_name": "订单管理_订单1_品种",
    "order_qty": "订单管理_订单1_数量",
    "unit_price": "订单管理_订单1_单价",
    "order_created_at": "订单管理_订单1_下单时间",
}
ORDER_DATE_PICKER_SELECTORS = {
    "year": "订单管理_订单日期选择_选择框年",
    "month": "订单管理_订单日期选择_选择框_月",
    "day": "订单管理_订单日期选择_选择框_日",
    "confirm": "订单管理_订单日期选择_选择框确认按钮",
    "cancel": "订单管理_订单日期选择_选择框取消按钮",
}
ORDER_ROW_INDEX_STEP = 9
ORDER_LIST_END_LABEL = "没有更多了"
ORDER_LIST_EMPTY_LABEL = "暂无订单"
V5_KEYBOARD_LOAD_WAIT_SECONDS = 0.1
V5_KEYBOARD_END_LOAD_WAIT_SECONDS = 0.8

DEFAULT_REQUEST = {
    "execution_mode": "READ_ONLY",
    "window_title": WINDOW_TITLE_DEFAULT,
    "max_product_rows": DEFAULT_MAX_PRODUCT_ROWS,
}

TZ_SHANGHAI = timezone(timedelta(hours=8))

_SAFE_LOGIN_PHASE_FIELDS = frozenset(
    {
        "verification_required",
        "verification_detected_at",
        "verification_deadline_at",
        "verification_markers",
        "verification_completed_at",
        "verification_completed",
        "employee_mode_clicked",
        "employee_mode_clicked_at",
        "account_password_submitted",
        "account_password_submitted_at",
        "login_markers",
        "login_completed_at",
    }
)
_SENSITIVE_OUTPUT_KEYS = frozenset(
    {
        "account",
        "password",
        "username",
        "credentialblob",
        "credential_target",
        "credential_provider",
        "_credential_provider",
    }
)
_SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "CREDENTIAL_TARGET_MISSING",
        "CREDENTIAL_MANAGER_UNAVAILABLE",
        "CREDENTIAL_NOT_FOUND",
        "CREDENTIAL_ACCESS_DENIED",
        "CREDENTIAL_FORMAT_INVALID",
        "CREDENTIAL_READ_FAILED",
    }
)


class SliceError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _now_iso():
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _safe_login_phase_snapshot(login_state):
    """Keep phase files limited to non-secret login state fields."""
    if not isinstance(login_state, dict):
        return {}
    return {
        key: value
        for key, value in login_state.items()
        if key in _SAFE_LOGIN_PHASE_FIELDS
    }


def _safe_output_payload(value):
    """Recursively remove credential-shaped fields before output/logging."""
    if isinstance(value, dict):
        return {
            key: _safe_output_payload(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_OUTPUT_KEYS
        }
    if isinstance(value, list):
        return [_safe_output_payload(item) for item in value]
    return value


def _replace_file_with_retry(source, destination, max_attempts=8):
    max_attempts = max(int(max_attempts), 1)
    for attempt in range(max_attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            retryable = isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in (5, 32, 33)
            if not retryable or attempt + 1 >= max_attempts:
                raise
            time.sleep(min(0.05 * (2 ** attempt), 0.5))


def _write_phase(request, result, phase, include_result_snapshot=False):
    phase_path = str(_get_arg(request, "_phase_file_path", "")).strip()
    if not phase_path:
        return
    payload = {
        "operation_id": str(
            _get_arg(
                request,
                "phase_parent_operation_id",
                _get_arg(request, "operation_id", ""),
            )
        ),
        "task_id": str(
            _get_arg(
                request,
                "phase_parent_task_id",
                _get_arg(request, "task_id", ""),
            )
        ),
        "execution_attempt_id": str(
            _get_arg(
                request,
                "phase_parent_execution_attempt_id",
                _get_arg(request, "execution_attempt_id", ""),
            )
        ),
        "execution_mode": str(_get_arg(request, "execution_mode", "")),
        "phase": phase,
        "side_effect_state": str(result.get("side_effect_state") or "NOT_STARTED"),
        "request_file_sha256": str(_get_arg(request, "request_file_sha256", "")),
        "instruction_hash": str(
            _get_arg(
                request,
                "phase_parent_instruction_hash",
                _get_arg(request, "instruction_hash", ""),
            )
        ),
        "worker_id": str(_get_arg(request, "worker_id", "")),
        "cleanup_confirmed": result.get("cleanup_action") == "PRICE_DIALOG_CANCELLED",
        "updated_at": _now_iso(),
    }
    if int(_get_arg(request, "contract_version", 0) or 0) == 4 or _get_arg(
        request, "commit_batch_id", ""
    ):
        payload.update(
            {
                "schema_version": "shadowbot-commit-batch-phase-1.0",
                "contract_version": 4,
                "batch_id": str(
                    _get_arg(
                        request,
                        "phase_parent_batch_id",
                        _get_arg(
                            request,
                            "batch_id",
                            _get_arg(request, "commit_batch_id", ""),
                        ),
                    )
                ),
                "manifest_sha256": str(
                    _get_arg(
                        request,
                        "phase_parent_manifest_sha256",
                        _get_arg(request, "manifest_sha256", ""),
                    )
                ),
            }
        )
    parent_snapshot = _get_arg(request, "phase_parent_result_snapshot", None)
    if isinstance(parent_snapshot, dict):
        payload["batch_result_snapshot"] = _safe_output_payload(parent_snapshot)
        payload["current_source_task_id"] = str(
            _get_arg(request, "phase_parent_current_source_task_id", "")
        )
        payload["execution_ordinal"] = int(
            _get_arg(request, "phase_parent_execution_ordinal", 0) or 0
        )
        payload["item_phase"] = {
            "item_id": str(_get_arg(request, "commit_item_id", "")),
            "source_task_id": str(
                _get_arg(request, "phase_parent_current_source_task_id", "")
            ),
            "operation_id": str(_get_arg(request, "operation_id", "")),
            "item_execution_attempt_id": str(
                _get_arg(request, "execution_attempt_id", "")
            ),
            "write_identity_key": str(
                _get_arg(request, "write_identity_key", "")
            ),
            "page_identity_key": str(
                _get_arg(request, "page_identity_key", "")
            ),
            "internal_sku": str(_get_arg(request, "platform_sku", "")),
            "expected_product_name": str(
                _get_arg(request, "expected_product_name", "")
            ),
            "expected_grade": str(_get_arg(request, "expected_grade", "")),
            "expected_old_price": str(
                _get_arg(request, "expected_old_price", "")
            ),
            "target_price": str(_get_arg(request, "target_price", "")),
            "item_payload_sha256": str(
                _get_arg(request, "instruction_hash", "")
            ),
            "phase": phase,
            "status": str(result.get("status") or ""),
            "submit_attempted": str(
                result.get("side_effect_state") or "NOT_STARTED"
            ).upper()
            != "NOT_STARTED",
            "side_effect_state": str(
                result.get("side_effect_state") or "NOT_STARTED"
            ),
            "submit_intent_at": result.get("submit_intent_at"),
            "submit_clicked_at": result.get("submit_clicked_at"),
            "readback_observed_at": result.get("readback_observed_at"),
            "actual_price": result.get("actual_price"),
            "error_code": str(result.get("error_code") or ""),
            "error_message": str(result.get("error_message") or ""),
            "updated_at": payload["updated_at"],
        }
    if include_result_snapshot:
        payload["result_snapshot"] = _safe_output_payload(dict(result))
    if isinstance(result.get("login"), dict):
        payload["login"] = _safe_login_phase_snapshot(result["login"])
    os.makedirs(os.path.dirname(phase_path), exist_ok=True)
    temporary = phase_path + ".tmp_" + uuid.uuid4().hex
    with open(temporary, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        file_obj.flush()
        os.fsync(file_obj.fileno())
    try:
        _replace_file_with_retry(temporary, phase_path)
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def _check_stop_before_submit(request, result):
    stop_path = str(_get_arg(request, "_stop_signal_path", "")).strip()
    if stop_path and os.path.exists(stop_path) and not _has_submit_side_effect(result):
        raise SliceError("WORKER_STOP_REQUESTED", "worker stop requested at safe checkpoint", True)


def _check_inline_old_price_fresh(observed_monotonic, now_monotonic=None):
    current = time.monotonic() if now_monotonic is None else float(now_monotonic)
    age_seconds = current - float(observed_monotonic)
    if age_seconds < 0 or age_seconds > INLINE_OLD_PRICE_MAX_AGE_SECONDS:
        raise SliceError(
            "FRESH_READ_EXPIRED",
            "inline old-price observation is older than 60 seconds",
            retryable=False,
        )
    return age_seconds


def _get_arg(args, name, default=None):
    if args is None:
        return default
    try:
        value = args.get(name, default)
    except AttributeError:
        try:
            value = args[name]
        except (KeyError, TypeError):
            value = default
    return default if value is None else value


def _required_text(args, name):
    value = str(_get_arg(args, name, "")).strip()
    if not value:
        raise SliceError("INPUT_INVALID", "missing required parameter: " + name)
    return value


def _load_pending_request_file():
    request_path = os.path.join(os.path.dirname(__file__), "pending_request.json")
    if not os.path.exists(request_path):
        return ""
    with open(request_path, "r", encoding="utf-8-sig") as file_obj:
        raw = file_obj.read()
    consumed_path = request_path + ".consumed_" + datetime.now(TZ_SHANGHAI).strftime("%Y%m%d%H%M%S")
    try:
        os.replace(request_path, consumed_path)
    except Exception:
        try:
            os.remove(request_path)
        except Exception:
            pass
    return raw


def _request_payload(args):
    raw = _get_arg(args, "request_json", "")
    if raw is None or not str(raw).strip():
        raw = _load_pending_request_file()
    payload = dict(DEFAULT_REQUEST)
    if raw is not None and str(raw).strip():
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError) as exc:
            raise SliceError("INPUT_INVALID", "request_json is not valid JSON: " + str(exc))
        if not isinstance(parsed, dict):
            raise SliceError("INPUT_INVALID", "request_json top-level value must be an object")
        payload.update(parsed)
        return payload

    for name in (
        "task_id",
        "execution_attempt_id",
        "product_keyword",
        "expected_product_name",
        "expected_grade",
        "expected_old_price",
        "execution_mode",
        "target_price",
        "window_title",
        "element_timeout_seconds",
        "max_product_rows",
        "window_x",
        "window_y",
        "window_width",
        "window_height",
        "evidence_dir",
        "evidence_share_dir",
        "evidence_storage_uri_prefix",
        "applet_uri",
        "applet_launch_timeout_seconds",
    ):
        value = _get_arg(args, name, None)
        if value is not None and str(value).strip():
            payload[name] = value
    return payload

def _as_int(args, name, default, minimum=0):
    value = _get_arg(args, name, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SliceError("INPUT_INVALID", "参数必须是整数: " + name)
    if parsed < minimum:
        raise SliceError("INPUT_INVALID", "参数超出允许范围: " + name)
    return parsed


def _normalize_text(value):
    return re.sub(r"\s+", "", str(value or "")).strip()


def _strip_label(value, labels):
    text = str(value or "").strip()
    for label in labels:
        if text.startswith(label):
            text = text[len(label):]
            break
    return _normalize_text(text.lstrip(":："))


def _assert_grade_identity(field_name, actual, expected):
    if expected and _multi_product_grade(actual) != _multi_product_grade(expected):
        raise SliceError(
            "PRODUCT_IDENTITY_MISMATCH",
            "%s不一致，期望=%s，实际=%s" % (field_name, expected, actual),
        )


def _assert_list_name(actual, expected_name, expected_grade):
    actual_value = _normalize_text(actual)
    allowed = {
        _normalize_text(expected_name),
        _normalize_text(expected_grade + expected_name),
    }
    if actual_value not in allowed:
        raise SliceError(
            "PRODUCT_IDENTITY_MISMATCH",
            "商品名称不一致，期望=%s（列表也允许等级+名称），实际=%s"
            % (expected_name, actual),
        )


def _list_name_matches(actual, expected_name, expected_grade):
    actual_value = _normalize_text(actual)
    return actual_value in {
        _normalize_text(expected_name),
        _normalize_text(expected_grade + expected_name),
    }


def _set_path_attribute(node, attribute_name, value, operator="Equal"):
    for attribute in node.get("attributes", []):
        if attribute.get("name") == attribute_name:
            attribute.update(
                {
                    "value": str(value),
                    "required": True,
                    "operator": operator,
                }
            )
            return
    node.setdefault("attributes", []).append(
        {
            "name": attribute_name,
            "value": str(value),
            "required": True,
            "operator": operator,
        }
    )


def _remove_dynamic_page_id_constraints(value):
    """Page instances are regenerated after login; keep their stable class path only."""
    for node in value.get("path", []):
        attributes = node.get("attributes", [])
        node["attributes"] = [
            attribute
            for attribute in attributes
            if not (
                attribute.get("name") == "id"
                and re.fullmatch(r"page-\d+", str(attribute.get("value") or ""))
            )
            and not (
                node.get("name") == "Document"
                and attribute.get("name") == "value"
                and "servicewechat.com" in str(attribute.get("value") or "")
            )
        ]
    return value


def _as_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


def _clone_row_selector(base_name, inferred_name, parent_index, static_text_index):
    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = inferred_name
    value["screenshot"] = ""

    selected_nodes = [node for node in value["path"] if node.get("selected") is True]
    indexed_wx_views = [
        node
        for node in selected_nodes
        if node.get("name") == "wx-view"
        and any(attr.get("name") == "index" for attr in node.get("attributes", []))
    ]
    if not indexed_wx_views:
        raise SliceError("SELECTOR_BUILD_FAILED", "基础选择器缺少可替换 wx-view index")
    target_node = indexed_wx_views[-1]
    _set_path_attribute(target_node, "index", parent_index)

    target_position = value["path"].index(target_node)
    for parent_node in reversed(value["path"][:target_position]):
        if parent_node.get("name") == "wx-view":
            parent_node["selected"] = True
            _set_path_attribute(
                parent_node,
                "class",
                "van-checkbox-group",
                operator="Contains",
            )
            break

    static_nodes = [
        node for node in selected_nodes if node.get("name") == "StaticText"
    ]
    if not static_nodes:
        raise SliceError("SELECTOR_BUILD_FAILED", "基础选择器缺少 StaticText")
    _set_path_attribute(static_nodes[-1], "role", "StaticText")
    _set_path_attribute(static_nodes[-1], "index", static_text_index)
    return Selector(value)


def _clone_row_value_selector(base_name, inferred_name, target_index):
    """Clone a captured row value whose terminal wx-view exposes the text directly."""
    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = inferred_name
    value["screenshot"] = ""

    selected_nodes = [node for node in value["path"] if node.get("selected") is True]
    indexed_wx_views = [
        node
        for node in selected_nodes
        if node.get("name") == "wx-view"
        and any(attr.get("name") == "index" for attr in node.get("attributes", []))
    ]
    if not indexed_wx_views:
        raise SliceError("SELECTOR_BUILD_FAILED", "库存选择器缺少可替换 wx-view index")
    target_node = indexed_wx_views[-1]
    _set_path_attribute(target_node, "index", target_index)

    target_position = value["path"].index(target_node)
    for parent_node in reversed(value["path"][:target_position]):
        if parent_node.get("name") == "wx-view":
            parent_node["selected"] = True
            _set_path_attribute(
                parent_node,
                "class",
                "van-checkbox-group",
                operator="Contains",
            )
            break

    # The captured inventory target is a visual wx-view wrapper. Its value is
    # exposed by the child StaticText node in the live WeChat accessibility
    # tree, while get_text() on the grouping wrapper is empty.
    grade_value = package.selector(SELECTOR_TEMPLATES["grade"]).__dict__["value"]
    static_nodes = [node for node in grade_value["path"] if node.get("name") == "StaticText"]
    if not static_nodes:
        raise SliceError("SELECTOR_BUILD_FAILED", "库存选择器缺少 StaticText 值节点模板")
    static_node = copy.deepcopy(static_nodes[-1])
    static_node["selected"] = True
    static_node["attributes"] = [
        attribute
        for attribute in static_node.get("attributes", [])
        if attribute.get("name") not in {"acc-name", "name-from"}
    ]
    _set_path_attribute(static_node, "role", "StaticText")
    _set_path_attribute(static_node, "index", INVENTORY_TEXT_INDEX)
    value["path"].append(static_node)
    return Selector(value)


def _row_field_selector(row_parent_index, field, page_type="online"):
    waiting = str(page_type or "online").strip().lower() == "waiting"
    templates = WAITING_SELECTOR_TEMPLATES if waiting else SELECTOR_TEMPLATES
    price_offset = WAITING_PRICE_INDEX_OFFSET if waiting else PRICE_INDEX_OFFSET
    if field == "name":
        return _clone_row_selector(
            templates["name"],
            "动态_index_%d_商品名称" % row_parent_index,
            row_parent_index,
            1,
        )
    if field == "grade":
        return _clone_row_selector(
            templates["grade"],
            "动态_index_%d_商品等级" % row_parent_index,
            row_parent_index,
            0,
        )
    if field == "price":
        return _clone_row_selector(
            templates["name"],
            "动态_index_%d_商品价格" % row_parent_index,
            row_parent_index + price_offset,
            0,
        )
    if field == "inventory":
        return _clone_row_value_selector(
            templates["inventory"],
            "动态_index_%d_商品库存" % row_parent_index,
            row_parent_index + INVENTORY_INDEX_OFFSET,
        )
    raise SliceError("SELECTOR_BUILD_FAILED", "不支持的行字段: " + field)


def _waiting_row_action_selector(row_parent_index, base_name, inferred_name):
    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = inferred_name
    value["screenshot"] = ""
    selected_nodes = [
        node for node in value["path"] if node.get("selected") is True
    ]
    indexed_wx_views = [
        node
        for node in selected_nodes
        if node.get("name") == "wx-view"
        and any(
            attribute.get("name") == "index"
            for attribute in node.get("attributes", [])
        )
    ]
    if not indexed_wx_views:
        raise SliceError(
            "SELECTOR_BUILD_FAILED",
            "待上架动作选择器缺少可替换 wx-view index",
        )
    _set_path_attribute(
        indexed_wx_views[-1],
        "index",
        int(row_parent_index) + WAITING_SET_ONLINE_INDEX_OFFSET,
    )
    return Selector(value)


def _online_row_action_selector(row_parent_index, base_name, inferred_name):
    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = inferred_name
    value["screenshot"] = ""
    selected_nodes = [
        node for node in value["path"] if node.get("selected") is True
    ]
    indexed_wx_views = [
        node
        for node in selected_nodes
        if node.get("name") == "wx-view"
        and any(
            attribute.get("name") == "index"
            for attribute in node.get("attributes", [])
        )
    ]
    if not indexed_wx_views:
        raise SliceError(
            "SELECTOR_BUILD_FAILED",
            "上架中动作选择器缺少可替换 wx-view index",
        )
    _set_path_attribute(
        indexed_wx_views[-1],
        "index",
        int(row_parent_index) + ONLINE_SET_OFFLINE_INDEX_OFFSET,
    )
    return Selector(value)


def _generic_acc_node_selector(node_name, selector_name):
    base = package.selector(SELECTOR_TEMPLATES["name"])
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = selector_name
    value["screenshot"] = ""
    for node in value["path"]:
        if node.get("type") == "acc":
            node["selected"] = False
    target_node = value["path"][-1]
    target_node["name"] = node_name
    target_node["type"] = "acc"
    target_node["selected"] = True
    target_node["attributes"] = []
    return Selector(value)


def _exact_acc_label_selector(label, selector_name):
    selector = _generic_acc_node_selector("StaticText", selector_name)
    value = copy.deepcopy(selector.__dict__["value"])
    target_node = value["path"][-1]
    _set_path_attribute(target_node, "role", "StaticText")
    _set_path_attribute(target_node, "acc-name", label)
    return Selector(value)


def _clone_dialog_value_selector(base_name, inferred_name):
    """Make a captured dialog value selector independent of row scroll state."""
    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = inferred_name
    value["screenshot"] = ""

    static_nodes = [node for node in value["path"] if node.get("name") == "StaticText"]
    if not static_nodes:
        raise SliceError("SELECTOR_BUILD_FAILED", "价格弹窗值模板缺少 StaticText")
    target_node = static_nodes[-1]
    target_position = value["path"].index(target_node)
    dialog_views = [
        node for node in value["path"][:target_position] if node.get("name") == "wx-view"
    ]
    if len(dialog_views) < 2:
        raise SliceError("SELECTOR_BUILD_FAILED", "价格弹窗值模板缺少弹窗字段路径")
    popup_node = dialog_views[-2]
    field_node = dialog_views[-1]
    popup_node["selected"] = True
    popup_node["attributes"] = [
        attribute
        for attribute in popup_node.get("attributes", [])
        if attribute.get("name") not in {"class", "index"}
    ]
    _set_path_attribute(popup_node, "class", "van-dialog", operator="Contains")

    child_index = DIALOG_VALUE_CHILD_INDEXES.get(base_name)
    if child_index is None:
        raise SliceError("SELECTOR_BUILD_FAILED", "不支持的价格弹窗值模板: " + str(base_name))
    field_node["selected"] = True
    field_node["attributes"] = [
        attribute
        for attribute in field_node.get("attributes", [])
        if attribute.get("name") != "index"
    ]
    _set_path_attribute(field_node, "index", child_index)

    target_node["selected"] = True
    target_node["attributes"] = [
        attribute
        for attribute in target_node.get("attributes", [])
        if attribute.get("name")
        not in {"acc-name", "explicit-name", "index", "name-from", "value"}
    ]
    _set_path_attribute(target_node, "role", "StaticText")
    return Selector(value)


def _clone_dynamic_static_text_selector(
    base_name,
    inferred_name,
    expected_text=None,
):
    """Remove captured business text while retaining the dialog structure."""

    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = inferred_name
    value["screenshot"] = ""
    static_nodes = [
        node for node in value["path"] if node.get("name") == "StaticText"
    ]
    if not static_nodes:
        raise SliceError(
            "SELECTOR_BUILD_FAILED",
            "弹窗文本模板缺少 StaticText",
            retryable=False,
        )
    target_node = static_nodes[-1]
    target_node["selected"] = True
    target_node["attributes"] = [
        attribute
        for attribute in target_node.get("attributes", [])
        if attribute.get("name")
        not in {"acc-name", "explicit-name", "name-from", "value"}
    ]
    _set_path_attribute(target_node, "role", "StaticText")
    if expected_text is not None:
        _set_path_attribute(
            target_node,
            "acc-name",
            str(expected_text),
        )
    return Selector(value)


def _assert_set_online_confirmation_identity(
    prompt_text,
    expected_product_name,
    expected_grade,
):
    try:
        matched = set_online_confirmation_matches(
            prompt_text,
            expected_product_name,
            expected_grade,
        )
    except ValueError as exc:
        raise SliceError(
            "SET_ONLINE_CONFIRMATION_INVALID",
            "上架确认弹窗无法解析: " + str(exc),
            retryable=False,
        )
    if not matched:
        raise SliceError(
            "PRODUCT_IDENTITY_MISMATCH",
            "上架确认弹窗商品身份与目标不一致，拒绝确认",
            retryable=False,
        )


def _assert_set_offline_confirmation_identity(
    prompt_text,
    expected_product_name,
    expected_grade,
):
    try:
        matched = set_offline_confirmation_matches(
            prompt_text,
            expected_product_name,
            expected_grade,
        )
    except ValueError as exc:
        raise SliceError(
            "SET_OFFLINE_CONFIRMATION_INVALID",
            "下架确认弹窗无法解析: " + str(exc),
            retryable=False,
        )
    if not matched:
        raise SliceError(
            "PRODUCT_IDENTITY_MISMATCH",
            "下架确认弹窗商品身份与目标不一致，拒绝确认",
            retryable=False,
        )


def _element_attributes(element):
    try:
        raw = element.get_all_attributes()
    except Exception:
        return {}
    try:
        return dict(raw or [])
    except Exception:
        return {}


def _element_label(element):
    parts = []
    for method_name in ("get_text", "get_value"):
        method = getattr(element, method_name, None)
        if callable(method):
            try:
                value = method()
                if value is not None and str(value).strip():
                    parts.append(str(value).strip())
            except Exception:
                pass
    attributes = _element_attributes(element)
    for attr_name in ("acc-name", "name", "value"):
        value = attributes.get(attr_name)
        if value is not None and str(value).strip():
            parts.append(str(value).strip())
    return [part for part in parts if part]


FINAL_SAVE_BUTTON_NODE_NAMES = ("Button", "wx-button", "wx-van-button")
UI_STATE_NODE_NAMES = (
    "StaticText",
    "Edit",
    "TextBox",
    "wx-input",
    "Button",
    "wx-button",
)
LOGIN_REQUIRED_MARKERS = (
    "欢迎使用蚂蚁花团供应商端",
    "请输入您的账号",
    "请输入您的密码",
)
LOGIN_VERIFICATION_MARKERS = (
    "验证码",
    "短信验证码",
    "请输入验证码",
    "获取验证码",
)
LOGIN_CREDENTIAL_REJECTED_MARKERS = (
    "账号或密码错误",
    "账号密码错误",
    "密码错误",
    "账号不存在",
)
NETWORK_OR_LOAD_ERROR_MARKERS = (
    "网络异常",
    "网络错误",
    "网络连接失败",
    "连接失败",
    "加载失败",
    "请检查网络",
    "重新加载",
    "加载中",
)
IGNORED_UI_CHROME_LABELS = (
    "蚂蚁花团供应商",
    "微信",
    "关闭",
    "最小化",
    "更多",
)


def _login_required_from_labels(labels):
    normalized = [_normalize_text(label) for label in labels if str(label).strip()]
    marker_hits = [
        marker
        for marker in LOGIN_REQUIRED_MARKERS
        if any(_normalize_text(marker) in label for label in normalized)
    ]
    has_login_label = any(label == _normalize_text("登录") for label in normalized)
    return bool(marker_hits) and (has_login_label or len(marker_hits) >= 2), marker_hits


def _meaningful_ui_labels(labels):
    ignored = {_normalize_text(label) for label in IGNORED_UI_CHROME_LABELS}
    meaningful = []
    for label in labels:
        normalized = _normalize_text(label)
        if not normalized or normalized in ignored:
            continue
        if normalized.startswith(_normalize_text("松开使用")) and normalized.endswith(
            _normalize_text("打开")
        ):
            continue
        if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", normalized):
            continue
        if normalized not in meaningful:
            meaningful.append(normalized)
    return meaningful


def _classify_unavailable_ui(labels):
    login_required, login_hits = _login_required_from_labels(labels)
    if login_required:
        return "LOGIN_REQUIRED", login_hits

    normalized = [_normalize_text(label) for label in labels if str(label).strip()]
    raw_network_hits = [
        marker
        for marker in NETWORK_OR_LOAD_ERROR_MARKERS
        if any(_normalize_text(marker) in label for label in normalized)
    ]
    network_hits = [
        marker
        for marker in raw_network_hits
        if not any(
            marker != other
            and _normalize_text(marker) in _normalize_text(other)
            for other in raw_network_hits
        )
    ]
    if network_hits:
        return "NETWORK_OR_LOAD_ERROR", network_hits

    if not _meaningful_ui_labels(labels):
        return "MINI_PROGRAM_BLANK_SCREEN", []
    return "", []


def _login_page_state(labels):
    normalized = [_normalize_text(label) for label in labels if str(label).strip()]
    rejected = [
        marker for marker in LOGIN_CREDENTIAL_REJECTED_MARKERS
        if any(_normalize_text(marker) in label for label in normalized)
    ]
    if rejected:
        return "CREDENTIALS_REJECTED", rejected
    verification = [
        marker for marker in LOGIN_VERIFICATION_MARKERS
        if any(_normalize_text(marker) in label for label in normalized)
    ]
    if verification:
        return "VERIFICATION_REQUIRED", verification
    login_required, markers = _login_required_from_labels(labels)
    return ("ACCOUNT_PASSWORD", markers) if login_required else ("", [])


def _collect_ui_state_labels(window):
    labels = []
    for node_name in UI_STATE_NODE_NAMES:
        selector = _generic_acc_node_selector(
            node_name, "dynamic_ui_state_" + node_name
        )
        try:
            elements = window.find_all(selector, timeout=1)
        except Exception:
            elements = []
        for element in elements:
            labels.extend(_element_label(element))
    return labels


def _raise_classified_ui_error(window):
    labels_seen = _collect_ui_state_labels(window)
    ui_error_code, marker_hits = _classify_unavailable_ui(labels_seen)
    if not ui_error_code:
        return
    messages = {
        "LOGIN_REQUIRED": "小程序登录状态已失效，需要人工重新登录",
        "NETWORK_OR_LOAD_ERROR": "小程序网络连接或页面加载异常",
        "MINI_PROGRAM_BLANK_SCREEN": "小程序页面无可识别业务内容，疑似白屏",
    }
    raise SliceError(
        ui_error_code,
        messages[ui_error_code]
        + "；matched="
        + str(marker_hits)
        + "; labels_seen="
        + str(labels_seen[:30]),
        retryable=ui_error_code != "LOGIN_REQUIRED",
    )


def _login_config_value(login_config, name, default=""):
    if isinstance(login_config, dict):
        return login_config.get(name, default)
    return default


def _safe_login_markers(markers):
    return [str(marker)[:80] for marker in markers[:5]]


def _wait_for_manual_login_verification(window, request, result, timeout_seconds, login_config, markers):
    wait_seconds = max(float(_login_config_value(login_config, "verification_wait_seconds", 600)), 1.0)
    deadline = time.time() + wait_seconds
    login_state = result.setdefault("login", {})
    login_state.update(
        {
            "verification_required": True,
            "verification_detected_at": _now_iso(),
            "verification_deadline_at": datetime.fromtimestamp(
                deadline, TZ_SHANGHAI
            ).isoformat(timespec="seconds"),
            "verification_markers": _safe_login_markers(markers),
        }
    )
    _write_phase(request, result, "LOGIN_VERIFICATION_REQUIRED")
    while time.time() < deadline:
        _check_stop_before_submit(request, result)
        labels = _collect_ui_state_labels(window)
        state, state_markers = _login_page_state(labels)
        if state == "CREDENTIALS_REJECTED":
            raise SliceError(
                "LOGIN_CREDENTIALS_REJECTED",
                "login credentials were rejected; markers=" + str(_safe_login_markers(state_markers)),
                retryable=False,
            )
        try:
            _find_element(window, ELEMENTS["product_management"], 0.5)
        except SliceError:
            sleep(2)
            continue
        login_state["verification_completed_at"] = _now_iso()
        login_state["verification_completed"] = True
        return True
    # Do one last safe homepage check after the deadline.  A user can finish
    # the OTP between the final polling sleep and the loop condition.
    _check_stop_before_submit(request, result)
    try:
        _find_element(window, ELEMENTS["product_management"], 0.5)
    except SliceError:
        pass
    else:
        login_state["verification_completed_at"] = _now_iso()
        login_state["verification_completed"] = True
        return True
    raise SliceError(
        "LOGIN_VERIFICATION_TIMEOUT",
        "manual phone verification did not complete before timeout",
        retryable=False,
    )


def _attempt_automatic_login(window, request, result, timeout_seconds, login_config, credential_provider, markers):
    if not bool(_login_config_value(login_config, "auto_enabled", True)):
        raise SliceError("LOGIN_REQUIRED", "automatic login is disabled", retryable=False)
    safe_provider_error_codes = frozenset(
        {
            "CREDENTIAL_TARGET_MISSING",
            "CREDENTIAL_MANAGER_UNAVAILABLE",
            "CREDENTIAL_NOT_FOUND",
            "CREDENTIAL_ACCESS_DENIED",
            "CREDENTIAL_FORMAT_INVALID",
            "CREDENTIAL_READ_FAILED",
        }
    )
    provider_error_code = ""
    if isinstance(request, dict):
        provider_error_code = str(request.get("_provider_error_code", "") or "").strip()
    if provider_error_code not in safe_provider_error_codes:
        provider_error_code = ""
    if credential_provider is None:
        if provider_error_code:
            result["provider_error_code"] = provider_error_code
        raise SliceError("LOGIN_CREDENTIALS_UNAVAILABLE", "credential provider is unavailable", retryable=False)
    employee_mode_required = bool(_login_config_value(login_config, "employee_mode_required", False))
    employee_mode_selector = str(_login_config_value(login_config, "employee_mode_selector", "")).strip()
    account_selector = str(_login_config_value(login_config, "account_selector", "")).strip()
    password_selector = str(_login_config_value(login_config, "password_selector", "")).strip()
    submit_selector = str(_login_config_value(login_config, "submit_selector", "")).strip()
    if not account_selector or not password_selector or not submit_selector or (employee_mode_required and not employee_mode_selector):
        raise SliceError("LOGIN_AUTOMATION_NOT_CONFIGURED", "login selectors are not configured", retryable=False)
    if bool(result.get("login", {}).get("account_password_submitted")):
        raise SliceError(
            "LOGIN_CREDENTIALS_REJECTED",
            "account/password login was already submitted for this attempt",
            retryable=False,
        )
    try:
        credentials = credential_provider.get_login_credentials()
    except Exception as exc:
        provider_error_code = str(getattr(exc, "error_code", "") or "").strip()
        if provider_error_code not in safe_provider_error_codes:
            provider_error_code = ""
        if provider_error_code:
            result["provider_error_code"] = provider_error_code
        raise SliceError(
            "LOGIN_CREDENTIALS_UNAVAILABLE",
            "credential provider failed: " + type(exc).__name__,
            retryable=False,
        ) from exc
    try:
        if employee_mode_selector:
            _find_element(window, employee_mode_selector, timeout_seconds).click()
            sleep(max(float(_login_config_value(login_config, "employee_mode_wait_seconds", 1)), 0.0))
            result.setdefault("login", {}).update(
                {
                    "employee_mode_clicked": True,
                    "employee_mode_clicked_at": _now_iso(),
                }
            )
        _set_login_input_value(_find_element(window, account_selector, timeout_seconds), credentials.account)
        _set_login_input_value(_find_element(window, password_selector, timeout_seconds), credentials.password)
        _find_element(window, submit_selector, timeout_seconds).click()
    except Exception as exc:
        raise SliceError(
            "LOGIN_AUTOFILL_FAILED",
            "account/password login autofill failed: " + type(exc).__name__,
            retryable=False,
        ) from exc
    result.setdefault("login", {}).update(
        {
            "account_password_submitted": True,
            "account_password_submitted_at": _now_iso(),
            "login_markers": _safe_login_markers(markers),
        }
    )
    _write_phase(request, result, "LOGIN_ACCOUNT_PASSWORD_SUBMITTED")
    deadline = time.time() + max(float(_login_config_value(login_config, "post_submit_wait_seconds", 8)), 1.0)
    while time.time() < deadline:
        _check_stop_before_submit(request, result)
        labels = _collect_ui_state_labels(window)
        state, state_markers = _login_page_state(labels)
        if state == "VERIFICATION_REQUIRED":
            return _wait_for_manual_login_verification(
                window, request, result, timeout_seconds, login_config, state_markers
            )
        if state == "CREDENTIALS_REJECTED":
            raise SliceError(
                "LOGIN_CREDENTIALS_REJECTED",
                "login credentials were rejected; markers=" + str(_safe_login_markers(state_markers)),
                retryable=False,
            )
        try:
            _find_element(window, ELEMENTS["product_management"], 0.5)
            result.setdefault("login", {})["login_completed_at"] = _now_iso()
            return True
        except SliceError:
            sleep(1)
    # A challenge can be rendered above the original account/password form, or
    # arrive after the short post-submit observation window.  Only an explicit
    # rejection marker is evidence that the credentials are wrong.  In every
    # other unresolved post-submit state, preserve the attempt and hand it to
    # the manual verification path instead of making an irreversible diagnosis.
    return _wait_for_manual_login_verification(
        window,
        request,
        result,
        timeout_seconds,
        login_config,
        ["ACCOUNT_PASSWORD_FORM_REMAINS_AFTER_SUBMIT"],
    )


def _recover_login_if_needed(window, request, result, timeout_seconds, login_config, credential_provider):
    # The business navigation entry is a precise positive signal that the
    # authenticated mini-program shell is already available.  Prefer this
    # bounded probe over six broad UI-state find_all scans on every request.
    try:
        _find_element(
            window,
            ELEMENTS["product_management"],
            min(float(timeout_seconds), 0.5),
        )
    except SliceError:
        result.setdefault("login", {})["check_path"] = "FULL_UI_STATE_SCAN"
    else:
        result.setdefault("login", {}).update(
            {
                "check_path": "BUSINESS_ENTRY_FAST_PATH",
                "login_completed_at": _now_iso(),
            }
        )
        return False

    labels = _collect_ui_state_labels(window)
    state, markers = _login_page_state(labels)
    if state == "ACCOUNT_PASSWORD":
        return _attempt_automatic_login(
            window, request, result, timeout_seconds, login_config, credential_provider, markers
        )
    if state == "VERIFICATION_REQUIRED":
        return _wait_for_manual_login_verification(
            window, request, result, timeout_seconds, login_config, markers
        )
    if state == "CREDENTIALS_REJECTED":
        raise SliceError(
            "LOGIN_CREDENTIALS_REJECTED",
            "login credentials were rejected; markers=" + str(_safe_login_markers(markers)),
            retryable=False,
        )
    return False


def _find_button_by_exact_label(window, labels, timeout_seconds):
    selectors = [
        (node_name, _generic_acc_node_selector(node_name, "dynamic_all_" + node_name))
        for node_name in FINAL_SAVE_BUTTON_NODE_NAMES
    ]
    deadline = time.time() + timeout_seconds
    normalized_labels = {_normalize_text(label) for label in labels}
    last_error = None
    last_seen = []
    while time.time() < deadline:
        matches = []
        last_seen = []
        for node_name, selector in selectors:
            try:
                buttons = window.find_all(selector, timeout=1)
            except Exception as exc:
                last_error = exc
                buttons = []
            for button in buttons:
                labels_seen = _element_label(button)
                if labels_seen:
                    last_seen.append({"node": node_name, "labels": labels_seen})
                if any(_normalize_text(label) in normalized_labels for label in labels_seen):
                    matches.append((button, labels_seen, node_name))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SliceError(
                "FINAL_SAVE_AMBIGUOUS",
                "multiple final save buttons matched: "
                + str([{"node": item[2], "labels": item[1]} for item in matches]),
                retryable=False,
            )
        sleep(0.5)
    raise SliceError(
        "FINAL_SAVE_NOT_FOUND",
        "final save button not found: "
        + str(labels)
        + "; last_seen="
        + str(last_seen[:20])
        + "; last_error="
        + str(last_error),
        retryable=False,
    )


def _find_element(window, selector_or_name, timeout_seconds):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            selector = (
                package.selector(selector_or_name)
                if isinstance(selector_or_name, str)
                else selector_or_name
            )
            remaining = max(0.1, deadline - time.time())
            return window.find(selector, timeout=min(1, remaining))
        except Exception as exc:
            last_error = exc
            sleep(0.5)
    detail = str(last_error) if last_error else "元素未出现"
    selector_label = (
        selector_or_name if isinstance(selector_or_name, str) else str(selector_or_name)
    )
    raise SliceError(
        "ELEMENT_NOT_FOUND",
        "未找到元素 %s: %s" % (selector_label, detail),
        retryable=True,
    )


def _get_and_prepare_window(window_title, x, y, width, height, max_attempts=3):
    last_error = None
    for attempt in range(max_attempts):
        try:
            window = win32.get(window_title)
            window.set_state("restore")
            window.move(x=x, y=y)
            window.resize(width=width, height=height)
            window.activate()
            return window
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                sleep(0.5)
    raise SliceError(
        "WINDOW_NOT_AVAILABLE",
        "无法获取或准备小程序窗口 %s: %s" % (window_title, str(last_error)),
        retryable=True,
    )


def _validate_applet_uri(applet_uri):
    uri = str(applet_uri or "").strip()
    if not uri:
        raise SliceError(
            "APPLET_URI_MISSING",
            "target mini program window is not available and applet_uri is missing",
            retryable=False,
        )
    if not uri.lower().startswith(APPLET_URI_PREFIXES):
        raise SliceError(
            "APPLET_URI_INVALID",
            "applet_uri must start with an allowed WeChat launchapplet prefix",
            retryable=False,
        )
    return uri


def _launch_applet_uri(applet_uri, uri_launcher=None):
    uri = _validate_applet_uri(applet_uri)
    launcher = uri_launcher or getattr(os, "startfile", None)
    if not callable(launcher):
        raise SliceError(
            "APPLET_URI_OPEN_FAILED",
            "Windows URI launcher is unavailable",
            retryable=True,
        )
    try:
        launcher(uri)
    except Exception as exc:
        raise SliceError(
            "APPLET_URI_OPEN_FAILED",
            "failed to open target mini program URI: " + str(exc),
            retryable=True,
        )
    return uri


def _get_or_open_and_prepare_window(
    window_title,
    x,
    y,
    width,
    height,
    applet_uri,
    launch_timeout_seconds=APPLET_LAUNCH_TIMEOUT_DEFAULT,
    uri_launcher=None,
):
    try:
        window = _get_and_prepare_window(window_title, x, y, width, height, max_attempts=1)
        return window, {
            "source": "EXISTING_WINDOW",
            "uri_opened": False,
            "window_ready_at": _now_iso(),
        }
    except SliceError as existing_window_error:
        if existing_window_error.code != "WINDOW_NOT_AVAILABLE":
            raise

    _launch_applet_uri(applet_uri, uri_launcher=uri_launcher)
    opened_at = _now_iso()
    attempts = max(1, int(float(launch_timeout_seconds) / 0.5))
    try:
        window = _get_and_prepare_window(window_title, x, y, width, height, max_attempts=attempts)
    except SliceError as exc:
        if exc.code == "WINDOW_NOT_AVAILABLE":
            raise SliceError(
                "WINDOW_NOT_AVAILABLE",
                "target mini program window did not appear after URI launch: " + str(exc.message),
                retryable=True,
            )
        raise
    return window, {
        "source": "URI_LAUNCHED",
        "uri_opened": True,
        "uri_opened_at": opened_at,
        "window_ready_at": _now_iso(),
    }


def _read_text(window, selector_or_name, timeout_seconds):
    element = _find_element(window, selector_or_name, timeout_seconds)
    value = element.get_text()
    if value is None or not str(value).strip():
        try:
            value = element.get_value()
        except Exception:
            pass
    return str(value or "").strip()


def _enumerate_product_rows(
    window,
    timeout_seconds,
    scan_state=None,
    page_type="online",
):
    """Probe the page-specific deterministic product-row index sequence."""
    row_timeout = min(timeout_seconds, 3)
    row_index_step = (
        WAITING_ROW_INDEX_STEP
        if str(page_type or "online").strip().lower() == "waiting"
        else ROW_INDEX_STEP
    )
    rows = []
    if scan_state is not None:
        scan_state.clear()
        scan_state.update(
            {
                "next_index_missing": False,
                "missing_parent_index": None,
                "rows_read": 0,
            }
        )
    for position in range(1, INDEXED_ENUMERATION_MAX_ROWS + 1):
        parent_index = ROW_INDEX_START + row_index_step * (position - 1)
        try:
            name = _strip_label(
                _read_text(
                    window,
                    _row_field_selector(parent_index, "name", page_type),
                    row_timeout,
                ),
                ("商品名称", "名称"),
            )
        except SliceError as exc:
            if exc.code == "ELEMENT_NOT_FOUND":
                if scan_state is not None:
                    scan_state["next_index_missing"] = True
                    scan_state["missing_parent_index"] = parent_index
                break
            rows.append(
                {
                    "source": "INDEXED_SEQUENCE",
                    "position": position,
                    "parent_index": parent_index,
                    "row_identity": "parent-index:%s" % parent_index,
                    "error": exc.code,
                    "detail": str(exc.message),
                }
            )
            break
        if not name:
            if scan_state is not None:
                scan_state["next_index_missing"] = True
                scan_state["missing_parent_index"] = parent_index
            break
        try:
            grade = _strip_label(
                _read_text(
                    window,
                    _row_field_selector(parent_index, "grade", page_type),
                    row_timeout,
                ),
                ("商品等级", "等级"),
            )
            rows.append(
                {
                    "source": "INDEXED_SEQUENCE",
                    "position": position,
                    "parent_index": parent_index,
                    "row_identity": "parent-index:%s" % parent_index,
                    "name": name,
                    "grade": grade,
                    "platform_sku": None,
                    "listing_status": "UNKNOWN",
                }
            )
        except SliceError as exc:
            rows.append(
                {
                    "source": "INDEXED_SEQUENCE",
                    "position": position,
                    "parent_index": parent_index,
                    "row_identity": "parent-index:%s" % parent_index,
                    "name": name,
                    "error": exc.code,
                    "detail": str(exc.message),
                }
            )
    if scan_state is not None:
        scan_state["rows_read"] = len(rows)
    return rows


def _enumerate_product_rows_by_index(window, timeout_seconds, max_rows=20):
    """Read product identities from the captured 1, 17, 33... row sequence."""
    rows = []
    row_timeout = min(timeout_seconds, 1)
    for position in range(1, max_rows + 1):
        parent_index = ROW_INDEX_START + ((position - 1) * ROW_INDEX_STEP)
        try:
            name = _strip_label(
                _read_text(window, _row_field_selector(parent_index, "name"), row_timeout),
                ("商品名称", "名称"),
            )
            grade = _strip_label(
                _read_text(window, _row_field_selector(parent_index, "grade"), row_timeout),
                ("商品等级", "等级"),
            )
        except SliceError:
            if rows:
                break
            continue
        if not name or not grade:
            if rows:
                break
            continue
        rows.append(
            {
                "source": "INDEX_SEQUENCE",
                "position": position,
                "parent_index": parent_index,
                "row_identity": "parent-index:%s" % parent_index,
                "name": name,
                "grade": grade,
                "platform_sku": None,
                "listing_status": "ONLINE",
            }
        )
    return rows


def _multi_product_text(value):
    return normalize_contract_text(value)


def _multi_product_utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _v5_record_timing(timing_trace, stage, started_at, **details):
    if timing_trace is None:
        return
    entry = {
        "stage": str(stage),
        "duration_ms": max(
            0,
            int(round((time.perf_counter() - float(started_at)) * 1000)),
        ),
    }
    entry.update(details)
    timing_trace.append(entry)


def _multi_product_grade(value):
    return normalize_contract_grade(value)


def _multi_product_sku(value):
    return normalize_contract_sku(value)


def _multi_product_identity(platform, sku, name, grade):
    return contract_identity_key(platform, sku, name, grade)


def _multi_product_target_matches(target, row):
    platform = str(target.get("platform") or "")
    if _multi_product_text(row.get("platform") or platform) != _multi_product_text(platform):
        return False
    target_sku = _multi_product_sku(target.get("platform_sku"))
    if target_sku:
        row_sku = _multi_product_sku(row.get("platform_sku"))
        # The supplier mini-program cards do not expose SKU in their
        # accessibility tree.  Keep exact SKU matching whenever the UI
        # exposes one; otherwise use the visible name+grade identity and let
        # the caller reject multiple candidates as AMBIGUOUS_MATCH.
        if row_sku:
            return row_sku == target_sku
    return (
        _multi_product_text(row.get("name")) == _multi_product_text(target.get("expected_product_name"))
        and _multi_product_grade(row.get("grade")) == _multi_product_grade(target.get("expected_grade"))
    )


def _multi_product_fingerprint(rows):
    counts = {}
    for row in rows:
        identity = _multi_product_identity(
            row.get("platform", ""),
            row.get("platform_sku"),
            row.get("name", ""),
            row.get("grade", ""),
        )
        counts[identity] = counts.get(identity, 0) + 1
    return sha256_json(sorted(counts.items()), prefixed=False)


def _multi_product_metric_add(metrics, key, amount=1):
    if metrics is None:
        return
    metrics[key] = metrics.get(key, 0) + amount


def _multi_product_enumerate_rows(
    window,
    timeout_seconds,
    targets=None,
    row_cache=None,
    metrics=None,
    scan_state=None,
    page_type="online",
    identity_rows=None,
):
    """Read only target values and reuse hydrated rows across repeated viewports."""
    started = time.time()
    identity_started = time.time()
    if identity_rows is None:
        identity_rows = _enumerate_product_rows(
            window,
            timeout_seconds,
            scan_state=scan_state,
            page_type=page_type,
        )
    else:
        identity_rows = [dict(row) for row in identity_rows]
    _multi_product_metric_add(metrics, "identity_scan_calls")
    _multi_product_metric_add(metrics, "identity_rows_seen", len(identity_rows))
    if metrics is not None:
        metrics["identity_scan_seconds"] = round(
            metrics.get("identity_scan_seconds", 0.0)
            + (time.time() - identity_started),
            3,
        )
    targets = list(targets or [])
    row_cache = row_cache if row_cache is not None else {}
    rows = []
    for row in identity_rows:
        if row.get("error"):
            rows.append(row)
            continue
        row["platform"] = str(row.get("platform") or "")
        listing_status = str(row.get("listing_status") or "UNKNOWN").upper()
        row["listing_status"] = (
            "ONLINE" if listing_status == "UNKNOWN" else listing_status
        )
        cache_key = (
            row.get("row_identity"),
            _multi_product_text(row.get("name")),
            _multi_product_grade(row.get("grade")),
        )
        cached = row_cache.get(cache_key)
        if cached is not None:
            cached_row = dict(cached)
            cached_row["position"] = row.get("position")
            cached_row["listing_status"] = row["listing_status"]
            rows.append(cached_row)
            _multi_product_metric_add(metrics, "row_cache_hits")
            continue

        is_target = not targets or any(
            _multi_product_target_matches(target, row) for target in targets
        )
        if is_target:
            _multi_product_metric_add(metrics, "target_rows_hydrated")
            try:
                row["price"] = _read_row_price(
                    window,
                    row.get("parent_index"),
                    timeout_seconds,
                    page_type=page_type,
                )
                _multi_product_metric_add(metrics, "price_reads")
            except SliceError as exc:
                row["price_error_code"] = "PRICE_PARSE_FAILED"
                row["price_error_message"] = str(exc.message)
            try:
                row["inventory"] = _read_row_inventory(
                    window,
                    row.get("parent_index"),
                    timeout_seconds,
                    page_type=page_type,
                )
                _multi_product_metric_add(metrics, "inventory_reads")
            except SliceError as exc:
                row["inventory_error_code"] = "INVENTORY_PARSE_FAILED"
                row["inventory_error_message"] = str(exc.message)
        else:
            _multi_product_metric_add(metrics, "non_target_rows_skipped")
        row_cache[cache_key] = dict(row)
        rows.append(row)
    _multi_product_metric_add(metrics, "pages_scanned")
    if metrics is not None:
        metrics["row_enumeration_seconds"] = round(
            metrics.get("row_enumeration_seconds", 0.0) + (time.time() - started),
            3,
        )
    return rows


def _advance_product_list(
    window,
    timeout_seconds,
    direction="down",
    *,
    anchor_parent_index=None,
    page_type="online",
    wheel_times=SINGLE_PRODUCT_SCROLL_WHEEL_TIMES,
    settle_seconds=0.75,
    wheel_delay_after=1.0,
    viewport=None,
    keyboard_key=None,
):
    """Best-effort bounded scroll hook; never claims progress without a call."""
    try:
        container = _find_product_scroll_view(window, min(timeout_seconds, 3))
    except Exception:
        try:
            container = _find_product_list_container(
                window,
                min(timeout_seconds, 3),
            )
        except Exception:
            return False
    try:
        window.activate()
        window.wait_active(timeout=min(float(timeout_seconds), 3.0))
        if keyboard_key:
            win32.send_keys(
                str(keyboard_key),
                50,
                False,
                float(wheel_delay_after),
                True,
                False,
            )
            sleep(float(settle_seconds))
            return True
        anchor = container
        if anchor_parent_index is not None:
            try:
                anchor = _find_element(
                    window,
                    _row_field_selector(
                        anchor_parent_index,
                        "name",
                        page_type,
                    ),
                    min(timeout_seconds, 3),
                )
            except Exception:
                anchor = container
        try:
            if viewport is not None:
                cursor_x = int(
                    float(viewport["x"]) + (float(viewport["width"]) / 2.0)
                )
                cursor_y = int(
                    float(viewport["y"])
                    + min(
                        max(float(viewport["height"]) * 0.65, 300.0),
                        float(viewport["height"]) - 100.0,
                    )
                )
            else:
                bounding = _bounding_dict(container)
                cursor_x = int(bounding["x"] + (bounding["width"] / 2.0))
                cursor_y = int(
                    bounding["y"]
                    + min(
                        max(bounding["height"] * 0.15, 120.0),
                        350.0,
                    )
                )
            win32.mouse_move(
                cursor_x,
                cursor_y,
                "screen",
                "instant",
                0.2,
            )
        except Exception:
            anchor.hover(True, 0.2)
        win32.mouse_wheel(
            direction,
            int(wheel_times),
            "none",
            float(wheel_delay_after),
        )
        sleep(float(settle_seconds))
        return True
    except Exception:
        pass
    for target in (container, window):
        for method_name in ("scroll", "scroll_to", "wheel"):
            method = getattr(target, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                ((), {"direction": direction}),
                ((direction,), {}),
                ((0, 800 if direction == "down" else -800), {}),
            ):
                try:
                    method(*args, **kwargs)
                    sleep(0.5)
                    return True
                except Exception:
                    continue
    return False


def _product_list_end_marker_visible(window, timeout_seconds):
    """Return true only for the explicit structured end-of-list marker."""
    selector = _exact_acc_label_selector(
        PRODUCT_LIST_END_LABEL,
        "dynamic_product_list_end_marker",
    )
    try:
        _find_element(window, selector, min(float(timeout_seconds), 1.0))
        return True
    except SliceError:
        return False


def _validate_multi_product_request_for_flow(request):
    if request.get("contract_version") != 2:
        raise SliceError("UNKNOWN_CONTRACT_VERSION", "task 11 requires contract_version=2", False)
    execution_mode = str(request.get("execution_mode") or "").strip().upper()
    if execution_mode != "READ_ONLY":
        raise SliceError(
            "READ_ONLY_REQUIRED",
            "v2 only permits READ_ONLY",
            False,
        )
    read_batch_id = str(request.get("read_batch_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", read_batch_id):
        raise SliceError("INPUT_INVALID", "read_batch_id is missing or malformed", False)
    products = request.get("products", [])
    if not isinstance(products, list) or len(products) > 50:
        raise SliceError("PRODUCT_COUNT_LIMIT_EXCEEDED", "products must contain 0-50 mapping hints", False)
    item_ids = set()
    identities = set()
    platforms = set()
    for product in products:
        if not isinstance(product, dict):
            raise SliceError("INPUT_INVALID", "each product target must be an object", False)
        item_id = str(product.get("item_id") or "").strip()
        platform = str(product.get("platform") or "").strip()
        name = str(product.get("expected_product_name") or "").strip()
        grade = str(product.get("expected_grade") or "").strip()
        if not item_id or not platform or not name or not grade or item_id in item_ids:
            raise SliceError("INPUT_INVALID", "item_id, platform, name, and grade are required and unique", False)
        identity = _multi_product_identity(platform, product.get("platform_sku"), name, grade)
        if identity in identities:
            raise SliceError("DUPLICATE_TARGET_IDENTITY", "duplicate normalized product identity", False)
        item_ids.add(item_id)
        identities.add(identity)
        platforms.add(_multi_product_text(platform))
    platform_name = str(request.get("platform_name") or "").strip()
    if not platform_name and products:
        platform_name = str(products[0].get("platform") or "").strip()
    if not platform_name:
        raise SliceError("INPUT_INVALID", "platform_name is required", False)
    if len(platforms) > 1 or (platforms and _multi_product_text(platform_name) not in platforms):
        raise SliceError("SINGLE_PLATFORM_REQUIRED", "task 11 accepts one platform per batch", False)
    limits = request.get("limits") or {}
    if not isinstance(limits, dict):
        raise SliceError("INPUT_INVALID", "limits must be an object", False)
    for key, hard_limit in (("max_pages", 100), ("max_scrolls", 500), ("max_seconds", 900)):
        raw_value = limits.get(key, {"max_pages": 20, "max_scrolls": 100, "max_seconds": 300}[key])
        if isinstance(raw_value, bool) or (isinstance(raw_value, float) and not raw_value.is_integer()):
            raise SliceError("INPUT_INVALID", "%s must be an integer" % key, False)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise SliceError("INPUT_INVALID", "%s must be an integer" % key, False)
        if not 1 <= value <= hard_limit:
            raise SliceError("%s_LIMIT_EXCEEDED" % key.upper(), "%s exceeds hard limit" % key, False)
    return products


def _multi_product_page_item_id(platform, name, grade, row_identity):
    identity = "%s|%s|%s|%s" % (
        _multi_product_text(platform),
        _multi_product_text(name),
        _multi_product_grade(grade),
        str(row_identity or ""),
    )
    return "UNMAPPED-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _refresh_for_multi_product_read(window, timeout_seconds, result):
    refresh = globals()["_refresh_product_list"]
    return refresh(window, timeout_seconds, result, "BEFORE_PRODUCT_READ")


def _run_multi_product_read_flow(args, request, result):
    """Execute the dynamic bounded READ_ONLY adapter path."""
    flow_started_clock = time.time()
    started_at = result.get("started_at") or _multi_product_utc_now()
    execution_attempt_id = str(result.get("execution_attempt_id") or "")
    products = _validate_multi_product_request_for_flow(request)
    timeout_seconds = _as_int(request, "element_timeout_seconds", ELEMENT_TIMEOUT_DEFAULT, minimum=1)
    launch_timeout = _as_int(request, "applet_launch_timeout_seconds", APPLET_LAUNCH_TIMEOUT_DEFAULT, minimum=1)
    max_pages = int((request.get("limits") or {}).get("max_pages", 20))
    max_scrolls = int((request.get("limits") or {}).get("max_scrolls", 100))
    max_seconds = int((request.get("limits") or {}).get("max_seconds", 300))
    window_title = str(_get_arg(request, "window_title", WINDOW_TITLE_DEFAULT)).strip()
    applet_uri = str(_get_arg(request, "applet_uri", "")).strip()
    evidence_dir = str(
        _get_arg(
            request,
            "evidence_dir",
            os.path.join(os.environ.get("LOCALAPPDATA", os.getcwd()), "ShadowBot", "evidence", "vertical_slice"),
        )
    ).strip()
    evidence_share_dir = str(_get_arg(request, "evidence_share_dir", "")).strip()
    evidence_storage_uri_prefix = str(_get_arg(request, "evidence_storage_uri_prefix", "")).strip()
    # Section 17: screenshots are diagnostics/manual-review opt-ins.  The
    # structured accessibility-tree read remains authoritative when this is
    # false (the default), and capture failures must not change item status.
    capture_evidence = request.get("capture_evidence") is True
    login_config = _get_arg(args, "_login_config", {})
    credential_provider = _get_arg(args, "_credential_provider", None)
    read_batch_id = str(request.get("read_batch_id") or "").strip()
    execution_mode = str(request.get("execution_mode") or "READ_ONLY").strip().upper()
    read_performance = {
        "version": 1,
        "strategy": "FULL_PAGE_FIELDS_WITH_MAPPING_WARNINGS",
        "login_check_seconds": 0.0,
        "login_check_path": "",
        "identity_scan_calls": 0,
        "identity_rows_seen": 0,
        "price_reads": 0,
        "inventory_reads": 0,
        "target_rows_hydrated": 0,
        "non_target_rows_skipped": 0,
        "unmapped_products_discovered": 0,
        "row_cache_hits": 0,
        "pages_scanned": 0,
        "scrolls": 0,
        "termination_reason": "",
    }
    result.update(
        {
            "schema_version": "shadowbot-result-2.0",
            "contract_version": 2,
            "read_batch_id": read_batch_id,
            "execution_mode": execution_mode,
            "platform_name": str(request.get("platform_name") or ""),
            "evidence_capture_enabled": capture_evidence,
            # Keep the v2 result bound to the leased attempt just like the
            # legacy single-product path.  The importer uses these fields to
            # reject results that cannot be tied to the executor lease.
            "operation_id": str(request.get("operation_id") or ""),
            "instruction_hash": str(request.get("instruction_hash") or ""),
            "request_file_sha256": str(request.get("request_file_sha256") or ""),
            "lease_owner_token": str(request.get("lease_owner_token") or ""),
            "lease_version": int(request.get("lease_version") or 0),
            "worker_id": str(request.get("worker_id") or ""),
            "product_snapshots": [],
            "warnings": [],
            "read_performance": read_performance,
            "started_at": started_at,
        }
    )
    try:
        _write_phase(request, result, "UI_STARTED")
        window_started_clock = time.time()
        window, launch = _get_or_open_and_prepare_window(
            window_title,
            _as_int(request, "window_x", WINDOW_X_DEFAULT),
            _as_int(request, "window_y", WINDOW_Y_DEFAULT),
            _as_int(request, "window_width", WINDOW_WIDTH_DEFAULT, minimum=100),
            _as_int(request, "window_height", WINDOW_HEIGHT_DEFAULT, minimum=100),
            applet_uri,
            launch_timeout,
        )
        read_performance["window_prepare_seconds"] = round(
            time.time() - window_started_clock,
            3,
        )
        result["applet_launch"] = launch
        sleep(1)
        login_check_started_clock = time.time()
        try:
            _recover_login_if_needed(
                window,
                request,
                result,
                timeout_seconds,
                login_config,
                credential_provider,
            )
        finally:
            read_performance["login_check_seconds"] = round(
                time.time() - login_check_started_clock,
                3,
            )
            read_performance["login_check_path"] = str(
                result.get("login", {}).get("check_path", "")
            )
        result["current_step"] = "REFRESH_PRODUCT_LIST"
        refresh_started_clock = time.time()
        try:
            _refresh_for_multi_product_read(window, timeout_seconds, result)
        except SliceError as refresh_error:
            # The mini-program can expire an otherwise valid session between
            # the initial login check and the product-list refresh.  Recover
            # the login state once, then retry the read-only refresh; without
            # this retry the worker reports a misleading container-not-found
            # failure while the UI has already returned to the login page.
            labels = _collect_ui_state_labels(window)
            login_state, _ = _login_page_state(labels)
            if login_state not in {"ACCOUNT_PASSWORD", "VERIFICATION_REQUIRED"}:
                raise refresh_error
            _recover_login_if_needed(window, request, result, timeout_seconds, login_config, credential_provider)
            _refresh_for_multi_product_read(window, timeout_seconds, result)
        read_performance["refresh_seconds"] = round(
            time.time() - refresh_started_clock,
            3,
        )
        list_loaded = True
        observed_rows = []
        row_cache = {}
        previous_fingerprint = None
        scroll_count = 0
        started_clock = time.time()
        for page in range(max_pages):
            stop_path = str(request.get("_stop_signal_path") or "").strip()
            if stop_path and os.path.exists(stop_path):
                raise SliceError("BATCH_STOPPED", "worker stop requested before the next read page", True)
            scan_state = {}
            current_rows = _multi_product_enumerate_rows(
                window,
                timeout_seconds,
                targets=None,
                row_cache=row_cache,
                metrics=read_performance,
                scan_state=scan_state,
            )
            if page == 0 and not current_rows:
                list_loaded = False
                read_performance["termination_reason"] = "LIST_NOT_LOADED"
                break
            current_fingerprint = _multi_product_fingerprint(current_rows)
            known = {(row.get("row_identity"), row.get("name"), row.get("grade")): row for row in observed_rows}
            for row in current_rows:
                known[(row.get("row_identity"), row.get("name"), row.get("grade"))] = row
            observed_rows = list(known.values())
            if scan_state.get("next_index_missing") and _product_list_end_marker_visible(
                window,
                timeout_seconds,
            ):
                read_performance["termination_reason"] = (
                    "INDEX_SEQUENCE_COMPLETE_WITH_END_MARKER"
                )
                break
            if previous_fingerprint is not None and current_fingerprint == previous_fingerprint:
                read_performance["termination_reason"] = "NO_PROGRESS_AFTER_SCROLL"
                break
            previous_fingerprint = current_fingerprint
            if page + 1 >= max_pages or scroll_count >= max_scrolls or time.time() - started_clock >= max_seconds:
                read_performance["termination_reason"] = "READ_LIMIT_REACHED"
                break
            if not _advance_product_list(window, timeout_seconds):
                read_performance["termination_reason"] = "SCROLL_UNAVAILABLE"
                break
            scroll_count += 1
            read_performance["scrolls"] = scroll_count
        snapshot_targets = list(products)
        if execution_mode == "READ_ONLY":
            snapshot_targets = []
            platform_name = str(request.get("platform_name") or "")
            ordered_rows = sorted(
                observed_rows,
                key=lambda row: (
                    int(row.get("parent_index") or 0),
                    str(row.get("row_identity") or ""),
                ),
            )
            for row in ordered_rows:
                mapping_matches = [
                    product
                    for product in products
                    if _multi_product_target_matches(product, row)
                ]
                if len(mapping_matches) == 1:
                    snapshot_target = dict(mapping_matches[0])
                    snapshot_target["_mapping_status"] = "MAPPED"
                else:
                    item_id = _multi_product_page_item_id(
                        platform_name,
                        row.get("name"),
                        row.get("grade"),
                        row.get("row_identity"),
                    )
                    snapshot_target = {
                        "item_id": item_id,
                        "platform": platform_name,
                        "platform_sku": None,
                        "expected_product_name": row.get("name", ""),
                        "expected_grade": row.get("grade", ""),
                        "_mapping_status": (
                            "UNMAPPED" if not mapping_matches else "AMBIGUOUS"
                        ),
                    }
                    if not mapping_matches:
                        warning = {
                            "warning_code": "UNMAPPED_PRODUCT_DISCOVERED",
                            "item_id": item_id,
                            "platform_name": platform_name,
                            "product_name": row.get("name", ""),
                            "grade": row.get("grade", ""),
                            "row_identity": row.get("row_identity", ""),
                            "parent_index": row.get("parent_index"),
                        }
                        result["warnings"].append(warning)
                        _multi_product_metric_add(
                            read_performance,
                            "unmapped_products_discovered",
                        )
                snapshot_target["_observed_row"] = row
                snapshot_targets.append(snapshot_target)
        for target in snapshot_targets:
            stop_path = str(request.get("_stop_signal_path") or "").strip()
            if stop_path and os.path.exists(stop_path):
                raise SliceError("BATCH_STOPPED", "worker stop requested before the next product read", True)
            if execution_mode == "READ_ONLY":
                matches = [target.get("_observed_row")]
            else:
                matches = [row for row in observed_rows if _multi_product_target_matches(target, row)]
            item_id = str(target["item_id"])
            snapshot = {
                "item_id": item_id,
                "platform": target.get("platform", ""),
                "platform_sku": target.get("platform_sku"),
                "product_name": "",
                "grade": "",
                "price": None,
                "inventory": None,
                "currency": "CNY",
                "listing_status": "UNKNOWN",
                "observed_at": _multi_product_utc_now(),
                "item_status": "FAILED",
                "error_code": "PRODUCT_NOT_FOUND",
                "error_message": "",
                "source_execution_attempt_id": execution_attempt_id,
                "row_identity": "",
                "locator_summary": "",
                "evidence": [],
                "mapping_status": str(target.get("_mapping_status") or "MAPPED"),
            }
            if not list_loaded:
                snapshot["error_code"] = "LIST_NOT_LOADED"
                snapshot["error_message"] = "商品列表未能确认加载或滚动未能确认前进"
            elif len(matches) > 1:
                snapshot["item_status"] = "MANUAL_CHECK_REQUIRED"
                snapshot["error_code"] = "AMBIGUOUS_MATCH"
                snapshot["error_message"] = "多个候选商品匹配目标，拒绝自动选择"
            elif len(matches) == 1:
                row = matches[0]
                snapshot.update(
                    {
                        "product_name": row.get("name", ""),
                        "grade": row.get("grade", ""),
                        "price": row.get("price"),
                        "inventory": row.get("inventory"),
                        "listing_status": str(row.get("listing_status") or "UNKNOWN").upper(),
                        "row_identity": row.get("row_identity", ""),
                        "locator_summary": "parent_index=%s" % row.get("parent_index", ""),
                        "parent_index": row.get("parent_index"),
                    }
                )
                if row.get("price_error_code"):
                    snapshot["error_code"] = "PRICE_PARSE_FAILED"
                    snapshot["error_message"] = row.get("price_error_message", "")
                elif row.get("inventory_error_code"):
                    snapshot["error_code"] = "INVENTORY_PARSE_FAILED"
                    snapshot["error_message"] = row.get("inventory_error_message", "")
                elif snapshot["listing_status"] == "UNKNOWN":
                    snapshot["item_status"] = "MANUAL_CHECK_REQUIRED"
                    snapshot["error_code"] = "LISTING_STATUS_UNKNOWN"
                else:
                    snapshot["item_status"] = "SUCCESS"
                    snapshot["error_code"] = None
                if snapshot["mapping_status"] == "UNMAPPED":
                    snapshot["warning_code"] = "UNMAPPED_PRODUCT_DISCOVERED"
                elif snapshot["mapping_status"] == "AMBIGUOUS":
                    snapshot["item_status"] = "MANUAL_CHECK_REQUIRED"
                    snapshot["error_code"] = "AMBIGUOUS_MATCH"
                    snapshot["error_message"] = "页面商品匹配到多个映射，拒绝绑定正式 SKU"
            if capture_evidence:
                try:
                    evidence = _capture_window(
                        window,
                        evidence_dir,
                        execution_attempt_id + "_" + _safe_path_part(item_id),
                        "PRODUCT_READ",
                        "product_read",
                        evidence_share_dir,
                        evidence_storage_uri_prefix,
                    )
                except SliceError as exc:
                    evidence = {
                        "evidence_id": "EVD-%s-%s" % (read_batch_id, _safe_path_part(item_id)),
                        "type": "PRODUCT_READ",
                        "storage_uri": "",
                        "storage_path": "",
                        "local_path": "",
                        "sha256": "",
                        "storage_sha256": "",
                        "hash_verified": False,
                        "size_bytes": 0,
                        "captured_at": _multi_product_utc_now(),
                        "upload_status": "FAILED",
                        "upload_error": str(exc.message),
                    }
                evidence.update(
                    {
                        "evidence_id": "EVD-%s-%s" % (read_batch_id, _safe_path_part(item_id)),
                        "evidence_type": "PRODUCT_READ",
                        "read_batch_id": read_batch_id,
                        "item_id": item_id,
                        "execution_attempt_id": execution_attempt_id,
                        "captured_at": _multi_product_utc_now(),
                        "relative_path": "%s/%s/%s" % (
                            read_batch_id,
                            _safe_path_part(item_id),
                            os.path.basename(str(evidence.get("storage_path") or evidence.get("local_path") or "")),
                        ),
                    }
                )
                # Section 17: diagnostic capture is never allowed to rewrite
                # a structured read outcome.  Keep a valid hashed record when
                # possible; otherwise expose a diagnostic-only failure and
                # leave evidence empty so Importer can still accept the read.
                if not evidence.get("sha256"):
                    snapshot["evidence_status"] = "FAILED"
                    snapshot["evidence_error"] = evidence.get("upload_error", "evidence hash is missing")
                    snapshot["evidence"] = []
                else:
                    snapshot["evidence"] = [evidence]
            else:
                snapshot["evidence_status"] = "SKIPPED"
            result["product_snapshots"].append(snapshot)
            result["current_item_id"] = item_id
            result["processed_count"] = len(result["product_snapshots"])
            _write_phase(request, result, "PRODUCT_READ")
        counts = {status: sum(item["item_status"] == status for item in result["product_snapshots"]) for status in ("SUCCESS", "FAILED", "SKIPPED", "MANUAL_CHECK_REQUIRED")}
        total = len(result["product_snapshots"])
        if counts["SUCCESS"] == total:
            overall_status = "COMPLETED"
        elif counts["SUCCESS"] == 0 and counts["MANUAL_CHECK_REQUIRED"] == 0 and counts["SKIPPED"] == 0:
            overall_status = "FAILED"
        else:
            overall_status = "PARTIAL"
        result.update(
            {
                "total_count": total,
                "success_count": counts["SUCCESS"],
                "failed_count": counts["FAILED"],
                "skipped_count": counts["SKIPPED"],
                "manual_check_count": counts["MANUAL_CHECK_REQUIRED"],
                "overall_status": overall_status,
                "status": "READ_COMPLETED",
                "run_success_flag": True,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": "",
                "error_message": "",
                "current_step": "COMPLETE",
                "ended_at": _multi_product_utc_now(),
            }
        )
        read_performance["total_seconds"] = round(
            time.time() - flow_started_clock,
            3,
        )
        _write_phase(request, result, "VERIFIED", include_result_snapshot=True)
    except SliceError as exc:
        read_performance["total_seconds"] = round(
            time.time() - flow_started_clock,
            3,
        )
        result.update(
            {
                "status": "FAILED",
                "run_success_flag": False,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": exc.code,
                "error_message": exc.message,
                "retryable": exc.retryable,
                "ended_at": _multi_product_utc_now(),
            }
        )
    return _set_result(args, result)


def _locate_product_row(
    window,
    expected_name,
    expected_grade,
    max_rows,
    timeout_seconds,
    include_position=False,
):
    matches = []
    observed = []
    try:
        dynamic_rows = _enumerate_product_rows(window, timeout_seconds)
    except Exception as exc:
        dynamic_rows = []
        observed.append(
            {
                "source": "DYNAMIC",
                "error": getattr(exc, "code", type(exc).__name__),
                "detail": str(exc),
            }
        )

    for fallback_position, row in enumerate(dynamic_rows, start=1):
        observed.append(row)
        if row.get("error"):
            continue
        if _list_name_matches(row["name"], expected_name, expected_grade) and (
            _multi_product_grade(row["grade"])
            == _multi_product_grade(expected_grade)
        ):
            matches.append(
                (
                    row["parent_index"],
                    row["name"],
                    row["grade"],
                    int(row.get("position") or fallback_position),
                )
            )

    if matches:
        unique_matches = {item[0]: item for item in matches}
        if len(unique_matches) > 1:
            raise SliceError(
                "PRODUCT_MATCH_AMBIGUOUS",
                "名称和等级匹配到多个商品父级 index: " + str(sorted(unique_matches)),
            )
        matched = next(iter(unique_matches.values()))
        return matched if include_position else matched[:3]

    row_timeout = min(timeout_seconds, 3)
    for row_number in range(1, max_rows + 1):
        parent_index = ROW_INDEX_START + ROW_INDEX_STEP * (row_number - 1)
        try:
            name = _strip_label(
                _read_text(window, _row_field_selector(parent_index, "name"), row_timeout),
                ("商品名称", "名称"),
            )
            grade = _strip_label(
                _read_text(window, _row_field_selector(parent_index, "grade"), row_timeout),
                ("商品等级", "等级"),
            )
        except SliceError as exc:
            observed.append(
                {
                    "source": "FIXED_FALLBACK",
                    "row": row_number,
                    "parent_index": parent_index,
                    "error": exc.code,
                }
            )
            continue
        observed.append(
            {
                "source": "FIXED_FALLBACK",
                "row": row_number,
                "parent_index": parent_index,
                "name": name,
                "grade": grade,
            }
        )
        if _list_name_matches(name, expected_name, expected_grade) and (
            _multi_product_grade(grade) == _multi_product_grade(expected_grade)
        ):
            matches.append((parent_index, name, grade, row_number))

    if not matches:
        raise SliceError(
            "PRODUCT_NOT_FOUND",
            "未找到名称=%s、等级=%s的商品；已读取=%s"
            % (expected_name, expected_grade, json.dumps(observed, ensure_ascii=False)),
        )
    if len(matches) > 1:
        raise SliceError(
            "PRODUCT_MATCH_AMBIGUOUS",
            "名称和等级匹配到多个商品父级 index: " + str([item[0] for item in matches]),
        )
    return matches[0] if include_position else matches[0][:3]


def _locate_product_row_at_position(
    window,
    expected_name,
    expected_grade,
    page_position,
    max_rows,
    timeout_seconds,
):
    """Resolve one user-approved page position and fail closed on identity drift."""
    if isinstance(page_position, bool):
        raise SliceError("INPUT_INVALID", "page_position_hint 必须为正整数", retryable=False)
    try:
        position = int(page_position)
    except (TypeError, ValueError):
        raise SliceError("INPUT_INVALID", "page_position_hint 必须为正整数", retryable=False)
    if position < 1 or position > MAX_PAGE_POSITION_HINT:
        raise SliceError(
            "INPUT_INVALID",
            "page_position_hint 超出允许范围: %s" % position,
            retryable=False,
        )
    parent_index = ROW_INDEX_START + ((position - 1) * ROW_INDEX_STEP)
    row_timeout = min(timeout_seconds, 3)
    name = _read_text(window, _row_field_selector(parent_index, "name"), row_timeout)
    grade = _read_text(window, _row_field_selector(parent_index, "grade"), row_timeout)
    if not _list_name_matches(name, expected_name, expected_grade) or (
        _multi_product_grade(grade) != _multi_product_grade(expected_grade)
    ):
        raise SliceError(
            "PRODUCT_POSITION_HINT_MISMATCH",
            "授权位置%d身份不匹配: expected=%s/%s, actual=%s/%s"
            % (position, expected_grade, expected_name, grade, name),
            retryable=False,
        )
    return parent_index, name, grade, position


def _bounding_dict(element):
    bounding = element.get_bounding(True)
    if isinstance(bounding, dict):
        return {
            "x": float(bounding["x"]),
            "y": float(bounding["y"]),
            "width": float(bounding["width"]),
            "height": float(bounding["height"]),
        }
    if isinstance(bounding, (list, tuple)) and len(bounding) >= 4:
        return {
            "x": float(bounding[0]),
            "y": float(bounding[1]),
            "width": float(bounding[2]),
            "height": float(bounding[3]),
        }
    raise SliceError(
        "ELEMENT_NOT_VISIBLE",
        "无法解析目标价格元素边界: " + str(bounding),
        retryable=True,
    )


def _price_element_in_clickable_view(
    price_element,
    *,
    window_x,
    window_y,
    window_width,
    window_height,
):
    bounding = _bounding_dict(price_element)
    center_x = bounding["x"] + bounding["width"] / 2.0
    center_y = bounding["y"] + bounding["height"] / 2.0
    left = float(window_x)
    right = left + float(window_width)
    top = float(window_y) + SINGLE_PRODUCT_CLICK_TOP_MARGIN
    bottom = float(window_y) + float(window_height) - SINGLE_PRODUCT_CLICK_BOTTOM_MARGIN
    return left <= center_x <= right and top <= center_y <= bottom, bounding


def _prepare_scrolled_product_for_click(
    window,
    expected_name,
    expected_grade,
    max_rows,
    timeout_seconds,
    product_position,
    page_position_hint=0,
    *,
    window_x,
    window_y,
    window_width,
    window_height,
):
    if product_position < SINGLE_PRODUCT_SCROLL_START_POSITION:
        raise SliceError(
            "INPUT_INVALID",
            "滚动准备只允许用于第4个及之后的商品",
            retryable=False,
        )
    attempts = []
    for scroll_attempt in range(1, SINGLE_PRODUCT_MAX_SCROLL_ATTEMPTS + 1):
        if not _advance_product_list(window, timeout_seconds, direction="down"):
            raise SliceError(
                "ELEMENT_NOT_VISIBLE",
                "商品位于第%d个，但商品列表无法向下滚动" % product_position,
                retryable=True,
            )
        if page_position_hint:
            row_index, name, grade, confirmed_position = _locate_product_row_at_position(
                window,
                expected_name,
                expected_grade,
                page_position_hint,
                max_rows,
                timeout_seconds,
            )
        else:
            row_index, name, grade, confirmed_position = _locate_product_row(
                window,
                expected_name,
                expected_grade,
                max_rows,
                timeout_seconds,
                include_position=True,
            )
        price_element = _find_element(
            window,
            _row_field_selector(row_index, "price"),
            timeout_seconds,
        )
        clickable, bounding = _price_element_in_clickable_view(
            price_element,
            window_x=window_x,
            window_y=window_y,
            window_width=window_width,
            window_height=window_height,
        )
        attempts.append(
            {
                "scroll_attempt": scroll_attempt,
                "wheel_times": SINGLE_PRODUCT_SCROLL_WHEEL_TIMES,
                "product_position": confirmed_position,
                "price_bounding": bounding,
                "clickable": clickable,
            }
        )
        if clickable:
            return row_index, name, grade, confirmed_position, attempts
    raise SliceError(
        "ELEMENT_NOT_VISIBLE",
        "商品位于第%d个，滚动后价格元素仍不在安全点击区域: %s"
        % (product_position, json.dumps(attempts, ensure_ascii=False)),
        retryable=True,
    )


def _parse_price(raw_text):
    normalized = str(raw_text or "").replace(",", "")
    matches = re.findall(r"\d+(?:\.\d{1,2})?", normalized)
    if len(matches) != 1:
        raise SliceError("OLD_PRICE_PARSE_FAILED", "供货价格无法唯一解析: " + str(raw_text))
    try:
        value = Decimal(matches[0])
    except InvalidOperation:
        raise SliceError("OLD_PRICE_PARSE_FAILED", "供货价格格式错误: " + str(raw_text))
    if value <= 0:
        raise SliceError("OLD_PRICE_PARSE_FAILED", "供货价格必须大于 0: " + str(raw_text))
    return format(value.quantize(Decimal("0.01")), "f")


def _parse_optional_price(raw_text):
    if not str(raw_text or "").strip():
        return ""
    return _parse_price(raw_text)


def _parse_target_price(raw_text):
    normalized = str(raw_text or "").strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", normalized):
        raise SliceError("INPUT_INVALID", "target_price 格式无效: " + str(raw_text))
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        raise SliceError("INPUT_INVALID", "target_price 格式无效: " + str(raw_text))
    if value <= 0:
        raise SliceError("INPUT_INVALID", "target_price 必须大于 0")
    return format(value.quantize(Decimal("0.01")), "f")


def _build_reconcile_update(actual_price, expected_old_price, target_price):
    if actual_price == target_price:
        return {
            "status": "VERIFIED",
            "run_success_flag": True,
            "business_operation_completed": True,
            "side_effect_state": "VERIFIED",
            "error_code": "",
            "error_message": "",
            "retryable": False,
        }
    if actual_price == expected_old_price:
        return {
            "status": "NOT_APPLIED",
            "run_success_flag": True,
            "business_operation_completed": False,
            "side_effect_state": "NOT_APPLIED",
            "error_code": "SUBMIT_NOT_APPLIED",
            "error_message": "实际价格仍为审批旧价，未观察到目标价生效",
            "retryable": False,
        }
    return {
        "status": "SIDE_EFFECT_UNKNOWN",
        "run_success_flag": None,
        "business_operation_completed": None,
        "side_effect_state": "UNKNOWN",
        "error_code": "POST_SUBMIT_PRICE_MISMATCH",
        "error_message": "实际价格=%s，既不是审批旧价=%s，也不是目标价=%s"
        % (actual_price, expected_old_price, target_price),
        "retryable": False,
    }


def _parse_expected_old_price(raw_text):
    expected = _parse_target_price(raw_text)
    return expected


def _has_submit_side_effect(result):
    return result.get("side_effect_state") in (
        "SUBMIT_INTENT_RECORDED",
        "SUBMIT_CLICKED",
        "UNKNOWN",
    )


def _mark_submit_result_unknown(result, current_step, original_error_code, original_error_message):
    result.update(
        {
            "status": "SIDE_EFFECT_UNKNOWN",
            "run_success_flag": None,
            "business_operation_completed": None,
            "current_step": current_step,
            "side_effect_state": "UNKNOWN",
            "error_code": "SUBMIT_RESULT_UNKNOWN",
            "error_message": "submit side effect may have happened; original_error_code=%s; original_error_message=%s"
            % (original_error_code, original_error_message),
            "original_error_code": original_error_code,
            "original_error_message": original_error_message,
            "retryable": False,
        }
    )


def _prove_submit_intent_not_clicked(result, window, timeout_seconds):
    if (
        result.get("side_effect_state") != "SUBMIT_INTENT_RECORDED"
        or result.get("submit_clicked_at")
    ):
        return False
    try:
        _cancel_price_dialog(window, timeout_seconds)
    except Exception as exc:
        result["submit_not_clicked_proof_error"] = type(exc).__name__
        return False
    result.update(
        {
            "status": "NOT_APPLIED",
            "run_success_flag": True,
            "business_operation_completed": False,
            "side_effect_state": "NOT_APPLIED",
            "submit_attempted": False,
            "submit_not_clicked_proof": {
                "proof_type": "PRICE_DIALOG_CANCELLED_AFTER_SUBMIT_INTENT",
                "proved_at": _now_iso(),
            },
            "cleanup_action": "PRICE_DIALOG_CANCELLED",
            "error_code": "SUBMIT_NOT_CLICKED",
            "error_message": "submit intent was recorded, but the still-open dialog was cancelled before any submit click",
            "retryable": False,
        }
    )
    return True


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path_part(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())


def _storage_uri_for_path(storage_path, storage_uri_prefix, relative_parts):
    if storage_uri_prefix:
        prefix = str(storage_uri_prefix).rstrip("\\/")
        return prefix + "/" + "/".join(relative_parts)
    return storage_path


def _copy_evidence_to_share(
    local_path,
    evidence_share_dir,
    storage_uri_prefix,
    execution_attempt_id,
    fault_injection="",
):
    if not evidence_share_dir:
        return {
            "storage_uri": "",
            "storage_path": "",
            "storage_sha256": "",
            "hash_verified": False,
            "upload_status": "SKIPPED",
            "upload_error": "未配置 evidence_share_dir",
            "error_code": "",
        }

    safe_attempt_id = _safe_path_part(execution_attempt_id)
    filename = os.path.basename(local_path)
    target_dir = os.path.abspath(os.path.join(evidence_share_dir, safe_attempt_id))
    target_path = os.path.abspath(os.path.join(target_dir, filename))
    try:
        os.makedirs(target_dir, exist_ok=True)
        shutil.copy2(local_path, target_path)
        if str(fault_injection or "").strip().upper() == "EVIDENCE_HASH_MISMATCH":
            with open(target_path, "ab") as file_obj:
                file_obj.write(b"shadowbot-test-hash-mismatch")
        local_hash = _sha256(local_path)
        storage_hash = _sha256(target_path)
        if local_hash != storage_hash:
            return {
                "storage_uri": _storage_uri_for_path(
                    target_path, storage_uri_prefix, [safe_attempt_id, filename]
                ),
                "storage_path": target_path,
                "storage_sha256": storage_hash,
                "hash_verified": False,
                "upload_status": "FAILED",
                "upload_error": "共享证据哈希不一致",
                "error_code": "EVIDENCE_HASH_MISMATCH",
            }
        return {
            "storage_uri": _storage_uri_for_path(
                target_path, storage_uri_prefix, [safe_attempt_id, filename]
            ),
            "storage_path": target_path,
            "storage_sha256": storage_hash,
            "hash_verified": True,
            "upload_status": "SUCCESS",
            "upload_error": "",
            "error_code": "",
        }
    except Exception as exc:
        return {
            "storage_uri": "",
            "storage_path": target_path,
            "storage_sha256": "",
            "hash_verified": False,
            "upload_status": "FAILED",
            "upload_error": str(exc),
            "error_code": "EVIDENCE_UPLOAD_FAILED",
        }


def _summarize_evidence_status(evidence_items):
    if not evidence_items:
        return "NONE"
    statuses = [item.get("upload_status", "") for item in evidence_items]
    if all(status == "SUCCESS" for status in statuses):
        return "COMPLETE"
    if all(status == "SKIPPED" for status in statuses):
        return "LOCAL_ONLY"
    if any(status == "FAILED" for status in statuses):
        return "FAILED"
    return "PARTIAL"


def _capture_window(
    window,
    evidence_dir,
    execution_attempt_id,
    evidence_type,
    suffix,
    evidence_share_dir,
    evidence_storage_uri_prefix,
    fault_injection="",
):
    os.makedirs(evidence_dir, exist_ok=True)
    safe_attempt_id = _safe_path_part(execution_attempt_id)
    filename = "%s_%s.png" % (safe_attempt_id, suffix)
    path = os.path.abspath(os.path.join(evidence_dir, filename))
    try:
        win32.screenshot.save_window_to_file(window.hWnd, path, "png")
    except Exception as exc:
        raise SliceError("SCREENSHOT_FAILED", "窗口截图失败: " + str(exc), retryable=True)
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        raise SliceError("SCREENSHOT_FAILED", "截图文件不存在或为空", retryable=True)
    local_sha256 = _sha256(path)
    shared = _copy_evidence_to_share(
        path,
        evidence_share_dir,
        evidence_storage_uri_prefix,
        execution_attempt_id,
        fault_injection,
    )
    evidence = {
        "evidence_id": str(uuid.uuid4()),
        "type": evidence_type,
        "local_path": path,
        "storage_uri": shared["storage_uri"],
        "storage_path": shared["storage_path"],
        "sha256": local_sha256,
        "storage_sha256": shared["storage_sha256"],
        "hash_verified": shared["hash_verified"],
        "size_bytes": os.path.getsize(path),
        "captured_at": _now_iso(),
        "upload_status": shared["upload_status"],
        "upload_error": shared["upload_error"],
    }
    if shared["upload_status"] == "FAILED":
        raise SliceError(
            shared.get("error_code") or "EVIDENCE_UPLOAD_FAILED",
            "证据复制到共享目录失败: " + shared["upload_error"],
            retryable=True,
        )
    return evidence



def _element_text_or_value(element):
    for method_name in ("get_value", "get_text"):
        method = getattr(element, method_name, None)
        if not callable(method):
            continue
        try:
            value = method()
        except Exception:
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _set_input_value(element, value):
    last_error = None
    try:
        element.click()
        sleep(0.2)
    except Exception as exc:
        last_error = exc

    # Prefer native element APIs, then fall back to ShadowBot's clipboard input for WeChat mini program fields.
    for method_name in ("set_value", "set_text", "input_text", "input"):
        method = getattr(element, method_name, None)
        if not callable(method):
            continue
        for args in ((value,), (value, True), (value, False)):
            try:
                method(*args)
                sleep(0.3)
                return
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                break

    clipboard_input = getattr(element, "clipboard_input", None)
    if callable(clipboard_input):
        for args in ((value, False, 1), (value, False), (value,)):
            try:
                clipboard_input(*args)
                sleep(0.5)
                return
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                break

    raise SliceError(
        "PRICE_INPUT_FAILED",
        "failed to write target_price by native or clipboard input methods: " + str(last_error),
        retryable=True,
    )


def _set_login_input_value(element, value):
    """Fill a credential field without exposing it through the clipboard."""
    try:
        element.click()
        sleep(0.2)
    except Exception:
        pass

    # Clipboard input is intentionally excluded here: Clipboard History and
    # other local observers are outside the protected CredentialProvider path.
    for method_name in ("set_value", "set_text", "input_text", "input"):
        method = getattr(element, method_name, None)
        if not callable(method):
            continue
        for method_args in ((value,), (value, True), (value, False)):
            try:
                method(*method_args)
                sleep(0.3)
                return
            except TypeError:
                continue
            except Exception:
                break
    raise SliceError(
        "LOGIN_AUTOFILL_FAILED",
        "credential field does not support a native input method",
        retryable=False,
    )


def _read_price_input(element):
    raw = _element_text_or_value(element)
    if not raw:
        return ""
    try:
        return _parse_price(raw)
    except SliceError:
        return str(raw).strip()


def _refresh_product_list(window, timeout_seconds, result, stage):
    """Force the mini-program list to fetch current platform data before a list price read."""
    event = {
        "stage": stage,
        "started_at": _now_iso(),
        "refresh_entry": ELEMENTS["product_management"],
        "status": "STARTED",
    }
    result.setdefault("product_list_refreshes", []).append(event)
    try:
        # Refreshing while a price dialog is open can discard an uncommitted draft.
        try:
            _find_element(window, ELEMENTS["price_popup"], 0.2)
        except SliceError:
            pass
        else:
            raise SliceError(
                "PRODUCT_LIST_REFRESH_FAILED",
                "price dialog is still open; refusing to refresh product list",
                retryable=True,
            )

        _find_element(window, ELEMENTS["product_management"], timeout_seconds).click()
        sleep(1)
        _select_online_product_list(window, timeout_seconds, result)
        # Require two independent observations so the first stale WebView frame is not accepted.
        # A structured empty-list marker is also a complete, valid online page.
        readiness = _require_product_list_ready(window, timeout_seconds)
        sleep(0.5)
        readiness = _require_product_list_ready(window, timeout_seconds)
    except SliceError as exc:
        event.update(
            {
                "status": "FAILED",
                "error_code": "PRODUCT_LIST_REFRESH_FAILED",
                "error_message": exc.message,
                "ended_at": _now_iso(),
            }
        )
        raise SliceError(
            "PRODUCT_LIST_REFRESH_FAILED",
            "product list refresh failed: " + exc.message,
            retryable=exc.retryable,
        )
    event.update(
        {
            "status": "SUCCESS",
            "readiness": readiness,
            "ended_at": _now_iso(),
        }
    )
    return event


def _find_product_list_container(window, timeout_seconds):
    try:
        return _find_element(window, ELEMENTS["target_container"], timeout_seconds)
    except SliceError as captured_error:
        base = package.selector(ELEMENTS["target_container"])
        value = copy.deepcopy(base.__dict__["value"])
        _remove_dynamic_page_id_constraints(value)
        value["id"] = str(uuid.uuid4())
        value["name"] = "动态_商品管理列表容器"
        value["screenshot"] = ""
        try:
            return _find_element(window, Selector(value), timeout_seconds)
        except SliceError as dynamic_error:
            raise SliceError(
                "ELEMENT_NOT_FOUND",
                "商品管理列表容器未出现；captured="
                + captured_error.message
                + "; dynamic="
                + dynamic_error.message,
                retryable=True,
            )


def _product_list_empty_marker_visible(window, timeout_seconds):
    selector = _exact_acc_label_selector(
        PRODUCT_LIST_EMPTY_LABEL,
        "dynamic_product_list_empty_marker",
    )
    try:
        _find_element(window, selector, min(float(timeout_seconds), 1.0))
        return True
    except SliceError:
        return False


def _require_product_list_ready(window, timeout_seconds):
    try:
        _find_product_list_container(window, timeout_seconds)
        return "PRODUCT_LIST_CONTAINER"
    except SliceError:
        if _product_list_empty_marker_visible(window, timeout_seconds):
            return "EMPTY_LIST_MARKER"
        raise


def _find_product_scroll_view(window, timeout_seconds):
    base = package.selector(ELEMENTS["target_container"])
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    scroll_index = None
    for index, node in enumerate(value.get("path") or []):
        node["selected"] = False
        if node.get("name") == "wx-scroll-view":
            scroll_index = index
    if scroll_index is None:
        raise SliceError(
            "SELECTOR_BUILD_FAILED",
            "商品列表容器选择器缺少 wx-scroll-view 节点",
            False,
        )
    value["path"] = value["path"][: scroll_index + 1]
    scroll_node = value["path"][-1]
    scroll_node["selected"] = True
    _set_path_attribute(scroll_node, "role", "Grouping")
    _set_path_attribute(scroll_node, "tag", "wx-scroll-view")
    _set_path_attribute(
        scroll_node,
        "class",
        "scroll-view",
        operator="Contains",
    )
    value["id"] = str(uuid.uuid4())
    value["name"] = "动态_商品管理_滚动视口"
    value["screenshot"] = ""
    return _find_element(window, Selector(value), timeout_seconds)


def _select_online_product_list(window, timeout_seconds, result):
    selector = _exact_acc_label_selector(
        ONLINE_LIST_LABEL,
        "dynamic_online_listing_tab",
    )
    try:
        target = _find_element(window, selector, timeout_seconds)
        try:
            target.click()
        except Exception:
            target.parent().click()
    except Exception as exc:
        if isinstance(exc, SliceError):
            detail = exc.message
        else:
            detail = str(exc)
        raise SliceError(
            "ONLINE_LIST_NOT_FOUND",
            "上架中 listing tab could not be selected: " + detail,
            retryable=True,
        )
    sleep(1)
    result["active_listing_filter"] = "ONLINE"
    result["active_listing_filter_selected_at"] = _now_iso()


def _select_waiting_product_list(window, timeout_seconds, result):
    try:
        try:
            target = _find_element(
                window,
                WAITING_LIST_SELECTOR,
                timeout_seconds,
            )
        except SliceError:
            target = _find_element(
                window,
                _exact_acc_label_selector(
                    WAITING_LIST_LABEL,
                    "dynamic_waiting_listing_tab",
                ),
                timeout_seconds,
            )
        try:
            target.click()
        except Exception:
            target.parent().click()
    except Exception as exc:
        detail = exc.message if isinstance(exc, SliceError) else str(exc)
        raise SliceError(
            "WAITING_LIST_NOT_FOUND",
            "待上架 listing tab could not be selected: " + detail,
            retryable=True,
        )
    sleep(1)
    _require_product_list_ready(window, timeout_seconds)
    result["active_listing_filter"] = "WAITING"
    result["active_listing_filter_selected_at"] = _now_iso()


def _reuse_current_product_list(window, timeout_seconds, result, stage):
    """Reuse an already-open product list when its structured rows are readable."""
    event = {
        "stage": stage,
        "started_at": _now_iso(),
        "refresh_entry": "CURRENT_PRODUCT_LIST",
        "status": "STARTED",
        "reused": True,
    }
    result.setdefault("product_list_refreshes", []).append(event)
    try:
        try:
            _find_element(window, ELEMENTS["price_popup"], 0.2)
        except SliceError:
            pass
        else:
            raise SliceError(
                "PRODUCT_LIST_REFRESH_FAILED",
                "price dialog is still open; refusing to reuse product list",
                retryable=True,
            )

        # The mini-program can already be on the online product list even when
        # the captured tab selector is temporarily unavailable.  Structured
        # product rows are the actual prerequisite for identity matching, so
        # accept the current page when at least one complete row is readable.
        # Keep the proven selector/container path as the fallback for pages
        # that do not expose readable rows yet.
        try:
            current_rows = _enumerate_product_rows_by_index(
                window, min(timeout_seconds, 3)
            )
        except Exception:
            current_rows = []
        readable_rows = [
            row
            for row in current_rows
            if not row.get("error") and row.get("name") and row.get("grade")
        ]
        if readable_rows:
            event.update(
                {
                    "status": "SUCCESS",
                    "readiness": "STRUCTURED_PRODUCT_ROWS",
                    "readable_row_count": len(readable_rows),
                    "ended_at": _now_iso(),
                }
            )
            return event

        _select_online_product_list(window, timeout_seconds, result)
        readiness = _require_product_list_ready(window, timeout_seconds)
        sleep(0.2)
        readiness = _require_product_list_ready(window, timeout_seconds)
    except SliceError as exc:
        event.update(
            {
                "status": "FAILED",
                "error_code": "PRODUCT_LIST_REUSE_FAILED",
                "error_message": exc.message,
                "ended_at": _now_iso(),
            }
        )
        raise
    event.update(
        {
            "status": "SUCCESS",
            "readiness": readiness,
            "ended_at": _now_iso(),
        }
    )
    return event


def _prepare_product_list(window, timeout_seconds, result, stage, reuse_requested=False):
    if reuse_requested:
        try:
            return _reuse_current_product_list(window, timeout_seconds, result, stage)
        except SliceError as exc:
            result.setdefault("batch_fast_path_fallbacks", []).append(
                {
                    "stage": stage,
                    "reason": exc.code,
                    "at": _now_iso(),
                }
            )
    return _refresh_product_list(window, timeout_seconds, result, stage)


def _open_price_dialog(
    window,
    row_index,
    timeout_seconds,
    page_type="online",
    settle_seconds=0.8,
):
    price_element = _find_element(
        window,
        _row_field_selector(row_index, "price", page_type),
        timeout_seconds,
    )
    price_element.click()
    if float(settle_seconds) > 0:
        sleep(float(settle_seconds))
    _find_element(
        window,
        WAITING_PRICE_POPUP_SELECTOR
        if str(page_type).lower() == "waiting"
        else ELEMENTS["price_popup"],
        timeout_seconds,
    )


def _read_optional_text(window, selector_or_name, timeout_seconds):
    try:
        return _read_text(window, selector_or_name, timeout_seconds)
    except SliceError as exc:
        if exc.code == "ELEMENT_NOT_FOUND":
            return ""
        raise


def _read_dialog_context(window, timeout_seconds):
    product_name_selector = _clone_dialog_value_selector(
        ELEMENTS["dialog_product_name"], "dynamic_dialog_product_name"
    )
    grade_selector = _clone_dialog_value_selector(
        ELEMENTS["dialog_grade"], "dynamic_dialog_grade"
    )
    current_price_selector = _clone_dialog_value_selector(
        ELEMENTS["dialog_current_price"], "dynamic_dialog_current_price"
    )
    return {
        "product_name": _strip_label(
            _read_text(window, product_name_selector, timeout_seconds),
            ("当前商品", "商品", "商品名称", "名称"),
        ),
        "grade": _strip_label(
            _read_optional_text(window, grade_selector, timeout_seconds),
            ("当前等级", "等级"),
        ),
        "current_price": _parse_optional_price(
            _read_optional_text(window, current_price_selector, timeout_seconds)
        ),
    }

def _assert_dialog_context(context, expected_name, expected_grade, expected_old_price):
    _assert_list_name(context["product_name"], expected_name, expected_grade)
    if context.get("grade"):
        _assert_grade_identity("price dialog grade", context["grade"], expected_grade)
    if expected_old_price and context.get("current_price") and context["current_price"] != expected_old_price:
        raise SliceError(
            "OLD_PRICE_CHANGED",
            "expected_old_price=%s, dialog_current_price=%s" % (expected_old_price, context["current_price"]),
            retryable=False,
        )


def _fill_target_price(
    window,
    target_price,
    timeout_seconds,
    fault_injection="",
    page_type="online",
):
    input_element = _find_element(
        window,
        WAITING_PRICE_INPUT_SELECTOR
        if str(page_type).lower() == "waiting"
        else ELEMENTS["price_input"],
        timeout_seconds,
    )
    _set_input_value(input_element, target_price)
    readback = _read_price_input(input_element)
    if str(fault_injection or "").strip().upper() == "PRICE_READBACK_MISMATCH":
        readback = "0.01" if target_price != "0.01" else "0.02"
    if readback and readback != target_price:
        raise SliceError(
            "TARGET_PRICE_VERIFY_FAILED",
            "target_price readback mismatch, expected=%s, actual=%s" % (target_price, readback),
            retryable=True,
        )
    return readback


def _cancel_price_dialog(window, timeout_seconds, page_type="online"):
    _find_element(
        window,
        WAITING_PRICE_CANCEL_SELECTOR
        if str(page_type).lower() == "waiting"
        else ELEMENTS["price_cancel"],
        timeout_seconds,
    ).click()
    sleep(0.5)


def _confirm_price_dialog(
    window,
    timeout_seconds,
    page_type="online",
    settle_seconds=1.2,
):
    _find_element(
        window,
        WAITING_PRICE_CONFIRM_SELECTOR
        if str(page_type).lower() == "waiting"
        else ELEMENTS["price_confirm"],
        timeout_seconds,
    ).click()
    if float(settle_seconds) > 0:
        sleep(float(settle_seconds))


def _update_waiting_inventory(
    window,
    row_index,
    target_inventory,
    timeout_seconds,
):
    inventory_element = _find_element(
        window,
        _row_field_selector(row_index, "inventory", "waiting"),
        timeout_seconds,
    )
    inventory_element.click()
    sleep(0.8)
    _find_element(window, WAITING_INVENTORY_POPUP_SELECTOR, timeout_seconds)
    input_element = _find_element(
        window,
        WAITING_INVENTORY_INPUT_SELECTOR,
        timeout_seconds,
    )
    _set_input_value(input_element, str(int(target_inventory)))
    readback = _read_price_input(input_element)
    if readback and str(int(Decimal(readback))) != str(int(target_inventory)):
        _find_element(
            window,
            WAITING_INVENTORY_CANCEL_SELECTOR,
            timeout_seconds,
        ).click()
        raise SliceError(
            "TARGET_INVENTORY_VERIFY_FAILED",
            "target inventory input readback mismatch",
            retryable=True,
        )
    _find_element(
        window,
        WAITING_INVENTORY_CONFIRM_SELECTOR,
        timeout_seconds,
    ).click()
    sleep(1.2)


def _read_row_price(window, row_index, timeout_seconds, page_type="online"):
    raw_price = _read_text(
        window,
        _row_field_selector(row_index, "price", page_type),
        timeout_seconds,
    )
    return _parse_price(raw_price)


def _parse_inventory(raw_text):
    normalized = str(raw_text or "").replace(",", "").strip()
    matches = re.findall(r"-?\d+(?:\.\d+)?", normalized)
    if len(matches) != 1 or "." in matches[0]:
        raise SliceError("INVENTORY_PARSE_FAILED", "库存无法唯一解析: " + str(raw_text))
    try:
        value = int(matches[0])
    except (TypeError, ValueError):
        raise SliceError("INVENTORY_PARSE_FAILED", "库存格式错误: " + str(raw_text))
    if value < 0:
        raise SliceError("INVENTORY_PARSE_FAILED", "库存不能为负数: " + str(raw_text))
    return value


def _read_row_inventory(
    window,
    row_index,
    timeout_seconds,
    page_type="online",
):
    raw_inventory = _read_text(
        window,
        _row_field_selector(row_index, "inventory", page_type),
        timeout_seconds,
    )
    return _parse_inventory(raw_inventory)


def _v5_wait_row_price(
    window,
    row_index,
    timeout_seconds,
    target_price,
    page_type="waiting",
):
    deadline = time.time() + max(float(timeout_seconds), 1.0)
    last_value = ""
    while time.time() < deadline:
        try:
            last_value = _read_row_price(
                window,
                row_index,
                min(float(timeout_seconds), 1.0),
                page_type=page_type,
            )
            if str(last_value) == str(target_price):
                return last_value
        except SliceError:
            pass
        sleep(V5_KEYBOARD_LOAD_WAIT_SECONDS)
    raise SliceError(
        "DETAIL_PRICE_SETTLE_TIMEOUT",
        "价格确认后行数据未更新到目标值，最后读取=%s" % str(last_value),
        retryable=True,
    )


def _v5_wait_row_inventory(
    window,
    row_index,
    timeout_seconds,
    target_inventory,
    page_type="waiting",
):
    deadline = time.time() + max(float(timeout_seconds), 1.0)
    last_value = None
    while time.time() < deadline:
        try:
            last_value = _read_row_inventory(
                window,
                row_index,
                min(float(timeout_seconds), 1.0),
                page_type=page_type,
            )
            if int(last_value) == int(target_inventory):
                return last_value
        except SliceError:
            pass
        sleep(V5_KEYBOARD_LOAD_WAIT_SECONDS)
    raise SliceError(
        "DETAIL_INVENTORY_SETTLE_TIMEOUT",
        "库存确认后行数据未更新到目标值，最后读取=%s"
        % str(last_value),
        retryable=True,
    )


def _v5_wait_row_identity_changed(
    window,
    row_index,
    timeout_seconds,
    *,
    page_type,
    expected_name,
    expected_grade,
):
    deadline = time.time() + max(float(timeout_seconds), 1.0)
    expected_identity = (
        _multi_product_text(expected_name),
        _multi_product_grade(expected_grade),
    )
    while time.time() < deadline:
        try:
            current_name = _strip_label(
                _read_text(
                    window,
                    _row_field_selector(row_index, "name", page_type),
                    min(float(timeout_seconds), 1.0),
                ),
                ("商品名称", "名称"),
            )
            current_grade = _strip_label(
                _read_text(
                    window,
                    _row_field_selector(row_index, "grade", page_type),
                    min(float(timeout_seconds), 1.0),
                ),
                ("商品等级", "等级"),
            )
            current_identity = (
                _multi_product_text(current_name),
                _multi_product_grade(current_grade),
            )
            if current_identity != expected_identity:
                return current_identity
        except SliceError as exc:
            if exc.code == "ELEMENT_NOT_FOUND":
                return None
        sleep(V5_KEYBOARD_LOAD_WAIT_SECONDS)
    raise SliceError(
        "ACTION_ROW_SETTLE_TIMEOUT",
        "%s 操作确认后目标行仍未移出原位置" % str(page_type),
        retryable=True,
    )


def _wait_after_submit_price(window, row_index, timeout_seconds, target_price):
    deadline = time.time() + max(float(timeout_seconds), 5.0)
    last_price = ""
    last_error = ""
    while time.time() < deadline:
        try:
            price = _read_row_price(window, row_index, 2)
            last_price = price
            if price == target_price:
                return price, ""
        except Exception as exc:
            last_error = str(exc)
        sleep(0.8)
    return last_price, last_error


def _parse_label_list(raw_value, default_labels):
    text = str(raw_value or "").strip()
    if not text:
        return list(default_labels)
    labels = [item.strip() for item in re.split(r"[,?|/]", text) if item.strip()]
    return labels or list(default_labels)


def _click_final_save(window, timeout_seconds, labels):
    button, labels_seen, node_name = _find_button_by_exact_label(window, labels, timeout_seconds)
    button.click()
    sleep(1.2)
    label = labels_seen[0] if labels_seen else ""
    return {"label": label, "node": node_name}

def _result_output_path(execution_attempt_id):
    output_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", os.getcwd()),
        "ShadowBot",
        "results",
        "vertical_slice",
    )
    safe_attempt_id = _safe_path_part(execution_attempt_id)
    return os.path.abspath(os.path.join(output_dir, safe_attempt_id + ".json"))


def _set_result(args, result):
    result_path = _result_output_path(result.get("execution_attempt_id", "result"))
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    safe_result = _safe_output_payload(dict(result))
    safe_result["result_path"] = result_path
    result_json = json.dumps(safe_result, ensure_ascii=False, separators=(",", ":"))
    with open(result_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(result_json)
    if args is not None:
        try:
            args["result_json"] = result_json
        except Exception:
            pass
    print(result_json)
    return result_json


def _commit_v4_matches(item, row):
    return (
        _multi_product_text(row.get("name"))
        == _multi_product_text(item.get("expected_product_name"))
        and _multi_product_grade(row.get("grade"))
        == _multi_product_grade(item.get("expected_grade"))
    )


def _commit_v4_scan_target_rows(window, timeout_seconds, items):
    rows = _enumerate_product_rows_by_index(window, timeout_seconds)
    if not rows:
        rows = _enumerate_product_rows(window, timeout_seconds)
    ordered_rows = sorted(
        (dict(row) for row in rows if not row.get("error")),
        key=lambda row: int(row["parent_index"]),
    )
    for position, row in enumerate(ordered_rows, start=1):
        row["position"] = position
        row["price"] = _read_row_price(
            window,
            row["parent_index"],
            timeout_seconds,
        )
        row["inventory"] = _read_row_inventory(
            window,
            row["parent_index"],
            timeout_seconds,
        )
        row["listing_status"] = str(
            row.get("listing_status") or "ONLINE"
        ).upper()
    matched = {}
    claimed_parent_indexes = {}
    for item in items:
        task_id = str(item["source_task_id"])
        candidates = [row for row in ordered_rows if _commit_v4_matches(item, row)]
        if not candidates:
            raise SliceError(
                "PRODUCT_NOT_FOUND",
                "未找到任务商品：%s %s" % (item["expected_product_name"], item["expected_grade"]),
                retryable=False,
            )
        if len(candidates) != 1:
            raise SliceError(
                "AMBIGUOUS_MATCH",
                "任务商品页面身份不唯一：%s %s" % (item["expected_product_name"], item["expected_grade"]),
                retryable=False,
            )
        row = dict(candidates[0])
        parent_index = int(row["parent_index"])
        if parent_index in claimed_parent_indexes:
            raise SliceError(
                "AMBIGUOUS_MATCH",
                "多个任务匹配到同一页面商品：%s、%s"
                % (claimed_parent_indexes[parent_index], task_id),
                retryable=False,
            )
        claimed_parent_indexes[parent_index] = task_id
        matched[task_id] = row

    return ordered_rows, matched


def _commit_v4_page_snapshot(platform_name, rows):
    captured_at = _multi_product_utc_now()
    products = []
    for row in rows:
        products.append(
            {
                "position": int(row["position"]),
                "parent_index": int(row["parent_index"]),
                "product_name": str(row.get("name") or "").strip(),
                "grade": str(row.get("grade") or "").strip(),
                "price": str(row.get("price") or "").strip(),
                "price_status": "OBSERVED_AT_PREFLIGHT",
                "inventory": int(row["inventory"]),
                "listing_status": str(
                    row.get("listing_status") or "UNKNOWN"
                ).upper(),
                "observed_at": captured_at,
            }
        )
    return {
        "capture_basis": "BATCH_PREFLIGHT_PLUS_COMMIT_READBACK",
        "captured_at": captured_at,
        "finalized_at": captured_at,
        "platform_name": str(platform_name or "").strip(),
        "total_count": len(products),
        "products": products,
    }


def _commit_v4_update_page_snapshot(result, item_result):
    snapshot = result.get("page_snapshot")
    if not isinstance(snapshot, dict):
        return
    for product in snapshot.get("products") or []:
        if (
            _multi_product_text(product.get("product_name"))
            == _multi_product_text(item_result.get("expected_product_name"))
            and _multi_product_grade(product.get("grade"))
            == _multi_product_grade(item_result.get("expected_grade"))
        ):
            if item_result.get("status") == "VERIFIED":
                product["price"] = str(item_result.get("actual_price") or "")
                product["price_status"] = "VERIFIED_AFTER_COMMIT"
                product["observed_at"] = item_result.get("readback_observed_at")
            elif item_result.get("status") == "UNKNOWN":
                product["price_status"] = "UNKNOWN_AFTER_SUBMIT"
            return


def _commit_v4_item_result(item):
    return {
        "item_id": item["item_id"],
        "source_task_id": item["source_task_id"],
        "internal_sku": item["internal_sku"],
        "expected_product_name": item["expected_product_name"],
        "expected_grade": item["expected_grade"],
        "expected_old_price": item["expected_old_price"],
        "target_price": item["target_price"],
        "item_payload_sha256": item["item_payload_sha256"],
        "operation_id": item["operation_id"],
        "item_execution_attempt_id": item["item_execution_attempt_id"],
        "write_identity_key": item["write_identity_key"],
        "page_identity_key": item["page_identity_key"],
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
        "status": "NOT_ATTEMPTED",
        "error_code": "",
        "error_message": "",
    }


def _commit_v4_stable_request(request, item, row, execution_ordinal, batch_size):
    """Adapt one planned item to the already-verified single-item COMMIT contract."""
    parent_attempt_id = str(request.get("execution_attempt_id") or "").strip()
    item_attempt_id = str(item["item_execution_attempt_id"])
    stable_request = {
        "schema_version": "shadowbot-request-1.0",
        "task_id": item["source_task_id"],
        "execution_attempt_id": item_attempt_id,
        "execution_mode": "COMMIT",
        "product_keyword": "%s%s"
        % (item["expected_grade"], item["expected_product_name"]),
        "expected_product_name": item["expected_product_name"],
        "expected_grade": item["expected_grade"],
        "expected_spec": "",
        "spec_verification_required": False,
        "expected_old_price": item["expected_old_price"],
        "target_price": item["target_price"],
        "operation_id": item["operation_id"],
        "platform_name": request.get("platform_name", ""),
        "platform_sku": item["internal_sku"],
        "instruction_hash": item["item_payload_sha256"],
        "commit_batch_id": request.get("batch_id", ""),
        "commit_item_id": item["item_id"],
        "write_identity_key": item["write_identity_key"],
        "page_identity_key": item["page_identity_key"],
        "commit_batch_ordinal": execution_ordinal,
        "commit_batch_size": batch_size,
        "page_position_hint": int(row["position"]),
        "reuse_product_list": True,
        "batch_preflight_reuse": True,
        "final_save_required": False,
        "fast_post_submit_verify": True,
        "capture_evidence": bool(request.get("capture_evidence", False)),
        "phase_parent_operation_id": str(request.get("operation_id") or ""),
        "phase_parent_task_id": str(request.get("task_id") or ""),
        "phase_parent_execution_attempt_id": parent_attempt_id,
        "phase_parent_instruction_hash": str(request.get("instruction_hash") or ""),
        "phase_parent_batch_id": str(request.get("batch_id") or ""),
        "phase_parent_manifest_sha256": str(
            request.get("manifest_sha256") or ""
        ),
        "phase_parent_current_source_task_id": item["source_task_id"],
        "phase_parent_execution_ordinal": execution_ordinal,
    }
    for key in (
        "window_title",
        "applet_uri",
        "element_timeout_seconds",
        "applet_launch_timeout_seconds",
        "max_product_rows",
        "window_x",
        "window_y",
        "window_width",
        "window_height",
        "evidence_dir",
        "evidence_share_dir",
        "evidence_storage_uri_prefix",
        "worker_id",
        "request_file_sha256",
        "fault_injection",
        "_phase_file_path",
        "_stop_signal_path",
    ):
        if key in request:
            stable_request[key] = request[key]
    return stable_request


def _commit_v4_counts(items):
    return v4_result_counts(items)


def _commit_v4_prepare_product_list(window, timeout_seconds, result, stage):
    """Use the product-list preparation path proven by the successful queue."""
    return _prepare_product_list(
        window,
        timeout_seconds,
        result,
        stage,
        reuse_requested=True,
    )


def _commit_v4_prepare_first_target_for_click(
    window,
    timeout_seconds,
    row,
    *,
    window_x,
    window_y,
    window_width,
    window_height,
):
    """Restore the upper viewport before an ascending batch starts at rows 1-3."""
    position = int(row["position"])
    event = {
        "target_position": position,
        "direction": "none",
        "attempts": [],
        "status": "STARTED",
    }
    if position >= SINGLE_PRODUCT_SCROLL_START_POSITION:
        event.update(
            {
                "status": "DEFERRED_TO_ITEM_SCROLL",
                "reason": "FIRST_TARGET_REQUIRES_DOWNWARD_SCROLL",
            }
        )
        return event

    for attempt in range(0, SINGLE_PRODUCT_MAX_SCROLL_ATTEMPTS + 1):
        price_element = _find_element(
            window,
            _row_field_selector(int(row["parent_index"]), "price"),
            timeout_seconds,
        )
        clickable, bounding = _price_element_in_clickable_view(
            price_element,
            window_x=window_x,
            window_y=window_y,
            window_width=window_width,
            window_height=window_height,
        )
        event["attempts"].append(
            {
                "attempt": attempt,
                "price_bounding": bounding,
                "clickable": clickable,
            }
        )
        if clickable:
            event["status"] = "SUCCESS"
            return event
        if attempt >= SINGLE_PRODUCT_MAX_SCROLL_ATTEMPTS:
            break
        event["direction"] = "up"
        if not _advance_product_list(window, timeout_seconds, direction="up"):
            raise SliceError(
                "ELEMENT_NOT_VISIBLE",
                "批次首个商品不在可点击区域，且商品列表无法向上滚动",
                retryable=True,
            )

    raise SliceError(
        "ELEMENT_NOT_VISIBLE",
        "批次首个商品向上恢复视口后仍不可点击: %s"
        % json.dumps(event["attempts"], ensure_ascii=False),
        retryable=True,
    )


def _run_commit_batch_v4(args, request, result):
    """Execute one formal queue with one preflight refresh and one final refresh."""
    batch_started_monotonic = time.monotonic()
    items = [dict(item) for item in request.get("items") or []]
    item_results = [_commit_v4_item_result(item) for item in items]
    by_task_id = {item["source_task_id"]: item for item in item_results}
    result.update(
        {
            "schema_version": "shadowbot-commit-batch-result-1.1",
            "contract_version": 4,
            "batch_id": request.get("batch_id", ""),
            "manifest_sha256": request.get("manifest_sha256", ""),
            "execution_attempt_id": request.get("execution_attempt_id", ""),
            "instruction_hash": request.get("instruction_hash", ""),
            "execution_mode": "COMMIT",
            "platform_name": request.get("platform_name", ""),
            "items": item_results,
            "batch_status": "FAILED",
            "counts": _commit_v4_counts(item_results),
            "side_effect_state": "NOT_STARTED",
            "batch_performance": {
                "version": 1,
                "preflight_seconds": 0.0,
                "item_execution_seconds": [],
                "total_seconds": 0.0,
                "prepared_window_reused": True,
            },
        }
    )
    timeout_seconds = _as_int(request, "element_timeout_seconds", ELEMENT_TIMEOUT_DEFAULT, minimum=1)
    window_x = _as_int(request, "window_x", WINDOW_X_DEFAULT)
    window_y = _as_int(request, "window_y", WINDOW_Y_DEFAULT)
    window_width = _as_int(request, "window_width", WINDOW_WIDTH_DEFAULT, minimum=100)
    window_height = _as_int(request, "window_height", WINDOW_HEIGHT_DEFAULT, minimum=100)
    execution_error = None
    window = None
    try:
        _write_phase(request, result, "UI_STARTED")
        window, launch = _get_or_open_and_prepare_window(
            str(_get_arg(request, "window_title", WINDOW_TITLE_DEFAULT)).strip(),
            window_x,
            window_y,
            window_width,
            window_height,
            str(_get_arg(request, "applet_uri", "")).strip(),
            _as_int(
                request,
                "applet_launch_timeout_seconds",
                APPLET_LAUNCH_TIMEOUT_DEFAULT,
                minimum=1,
            ),
        )
        result["applet_launch"] = launch
        sleep(1)
        _recover_login_if_needed(
            window,
            request,
            result,
            timeout_seconds,
            _get_arg(args, "_login_config", {}),
            _get_arg(args, "_credential_provider", None),
        )
        _commit_v4_prepare_product_list(window, timeout_seconds, result, "BATCH_PREFLIGHT")
        page_rows, preflight = _commit_v4_scan_target_rows(
            window,
            timeout_seconds,
            items,
        )
        result["page_snapshot"] = _commit_v4_page_snapshot(
            request.get("platform_name", ""),
            page_rows,
        )
        for item in items:
            task_id = item["source_task_id"]
            row = preflight[task_id]
            output = by_task_id[task_id]
            output["preflight_row"] = int(row["position"])
            output["preflight_price"] = row["price"]
            output["preflight_observed_at"] = result["page_snapshot"]["captured_at"]
            if row["price"] != item["expected_old_price"]:
                output.update(
                    {
                        "status": "FAILED",
                        "error_code": "OLD_PRICE_CHANGED",
                        "error_message": "任务 %s 旧价不一致：输入=%s，页面=%s"
                        % (task_id, item["expected_old_price"], row["price"]),
                    }
                )
                raise SliceError(
                    "OLD_PRICE_CHANGED",
                    output["error_message"],
                    retryable=False,
                )
        plan = sorted(items, key=lambda item: preflight[item["source_task_id"]]["position"])
        result["preflight_page_order"] = [item["source_task_id"] for item in plan]
        first_task_id = plan[0]["source_task_id"]
        result["batch_viewport_preparation"] = (
            _commit_v4_prepare_first_target_for_click(
                window,
                timeout_seconds,
                preflight[first_task_id],
                window_x=window_x,
                window_y=window_y,
                window_width=window_width,
                window_height=window_height,
            )
        )
        result["batch_performance"]["preflight_seconds"] = round(
            time.monotonic() - batch_started_monotonic,
            3,
        )
        _write_phase(request, result, "PREFLIGHT_VALIDATED", include_result_snapshot=True)

        for execution_ordinal, item in enumerate(plan, start=1):
            item_started_monotonic = time.monotonic()
            task_id = item["source_task_id"]
            output = by_task_id[task_id]
            output["execution_ordinal"] = execution_ordinal
            result["current_source_task_id"] = task_id
            stop_path = str(request.get("_stop_signal_path") or "").strip()
            if stop_path and os.path.exists(stop_path):
                raise SliceError("BATCH_STOPPED", "worker stop requested before next COMMIT item", True)
            row = preflight[task_id]
            stable_request = _commit_v4_stable_request(
                request,
                item,
                row,
                execution_ordinal,
                len(plan),
            )
            stable_request["phase_parent_result_snapshot"] = dict(result)
            stable_args = {
                "request_json": json.dumps(
                    stable_request,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "_login_config": _get_arg(args, "_login_config", {}),
                "_credential_provider": _get_arg(args, "_credential_provider", None),
                "_prepared_window": window,
                "_batch_preflight_validated": True,
            }
            _run_single_product_flow(stable_args)
            stable_result = json.loads(str(stable_args.get("result_json") or "{}"))
            result["batch_performance"]["item_execution_seconds"].append(
                {
                    "source_task_id": task_id,
                    "execution_ordinal": execution_ordinal,
                    "seconds": round(time.monotonic() - item_started_monotonic, 3),
                    "full_page_enumeration_skipped": True,
                }
            )
            stable_status = str(stable_result.get("status") or "FAILED").upper()
            stable_side_effect = str(
                stable_result.get("side_effect_state") or "NOT_STARTED"
            ).upper()
            output["stable_execution_attempt_id"] = stable_request["execution_attempt_id"]
            output["actual_price"] = stable_result.get("actual_price")
            output["error_code"] = str(stable_result.get("error_code") or "")
            output["error_message"] = str(stable_result.get("error_message") or "")
            output["submit_attempted"] = stable_side_effect != "NOT_STARTED"
            output["side_effect_state"] = stable_side_effect
            output["submit_intent_at"] = stable_result.get("submit_intent_at")
            output["submit_clicked_at"] = stable_result.get("submit_clicked_at")
            output["readback_observed_at"] = stable_result.get(
                "readback_observed_at"
            )
            if stable_status == "VERIFIED" and stable_side_effect == "VERIFIED":
                output["status"] = "VERIFIED"
            elif stable_side_effect == "NOT_APPLIED":
                output["status"] = "NOT_APPLIED"
            elif stable_side_effect in (
                "SUBMIT_INTENT_RECORDED",
                "SUBMIT_CLICKED",
                "UNKNOWN",
            ):
                output["status"] = "UNKNOWN"
            else:
                output["status"] = "FAILED"
            _commit_v4_update_page_snapshot(result, output)
            if output["submit_attempted"]:
                result["side_effect_state"] = stable_side_effect
            if output["status"] != "VERIFIED":
                execution_error = SliceError(
                    output["error_code"] or "COMMIT_ITEM_FAILED",
                    output["error_message"] or "stable COMMIT item did not verify",
                    retryable=bool(stable_result.get("retryable", False)),
                )
                break
    except SliceError as exc:
        execution_error = exc
        result["error_code"] = exc.code
        result["error_message"] = exc.message
        result["retryable"] = exc.retryable

    counts = _commit_v4_counts(item_results)
    semantics = derive_v4_batch_semantics(counts)
    result.update(
        {
            "counts": counts,
            **semantics,
            "error_code": str(getattr(execution_error, "code", "") or result.get("error_code") or ""),
            "error_message": str(getattr(execution_error, "message", "") or result.get("error_message") or ""),
            "retryable": bool(getattr(execution_error, "retryable", False)),
            "current_step": "COMPLETE",
            "ended_at": _now_iso(),
        }
    )
    result["batch_performance"]["total_seconds"] = round(
        time.monotonic() - batch_started_monotonic,
        3,
    )
    _write_phase(request, result, "FINAL_VERIFICATION", include_result_snapshot=True)
    return _set_result(args, result)


def _v5_stable_id(prefix, payload):
    return str(prefix) + "-" + sha256_json(payload, prefixed=False)[:24]


def _v5_write_phase(
    request,
    phase,
    current_item=None,
    item_states=None,
    clicked_at=None,
    detail_effect_state="NOT_STARTED",
    listing_effect_state="NOT_STARTED",
):
    phase_path = str(request.get("_phase_file_path") or "").strip()
    if not phase_path:
        return
    payload = {
        "schema_version": "shadowbot-listing-action-batch-phase-1.0",
        "contract_version": 5,
        "action_type": request.get("action_type", ""),
        "batch_id": request.get("batch_id", ""),
        "execution_attempt_id": request.get("execution_attempt_id", ""),
        "instruction_hash": request.get("instruction_hash", ""),
        "manifest_sha256": request.get("manifest_sha256", ""),
        "request_file_sha256": request.get("request_file_sha256", ""),
        "worker_id": request.get("worker_id", ""),
        "phase": phase,
        "phase_at": _multi_product_utc_now(),
        "detail_effect_state": detail_effect_state,
        "listing_effect_state": listing_effect_state,
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
    if clicked_at:
        payload["clicked_at"] = clicked_at
    payload["phase_snapshot_sha256"] = sha256_json(dict(payload))
    os.makedirs(os.path.dirname(phase_path), exist_ok=True)
    temporary = phase_path + ".tmp_" + uuid.uuid4().hex
    with open(temporary, "w", encoding="utf-8") as file_obj:
        json.dump(
            payload,
            file_obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        file_obj.flush()
        os.fsync(file_obj.fileno())
    try:
        _replace_file_with_retry(temporary, phase_path)
    finally:
        try:
            os.remove(temporary)
        except OSError:
            pass


def _v5_load_mapping(request):
    mapping_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "product_identity_mapping.json",
    )
    with open(mapping_path, "rb") as file_obj:
        mapping_bytes = file_obj.read()
    actual_version = "sha256:" + hashlib.sha256(mapping_bytes).hexdigest()
    if actual_version != str(request.get("mapping_source_version") or ""):
        raise SliceError(
            "MAPPING_SOURCE_VERSION_MISMATCH",
            "Worker 商品映射版本与请求不一致",
            retryable=False,
        )
    try:
        mapping_data = json.loads(mapping_bytes.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise SliceError(
            "MAPPING_SOURCE_INVALID",
            "Worker 商品映射文件无法解析: " + type(exc).__name__,
            retryable=False,
        )
    if str(mapping_data.get("platform_name") or "").strip() != str(
        request.get("platform_name") or ""
    ).strip():
        raise SliceError(
            "MAPPING_SOURCE_INVALID",
            "Worker 商品映射平台不一致",
            retryable=False,
        )
    mappings = []
    for raw in mapping_data.get("mappings") or []:
        if str(raw.get("status") or "active").strip().lower() != "active":
            continue
        sku = normalize_contract_sku(raw.get("internal_sku"))
        name = str(raw.get("expected_product_name") or "").strip()
        grade = str(raw.get("expected_grade") or "").strip()
        if not sku or not name or not grade:
            raise SliceError(
                "MAPPING_SOURCE_INVALID",
                "Worker 商品映射包含不完整项目",
                retryable=False,
            )
        mappings.append(
            {
                "internal_sku": sku,
                "product_name": name,
                "grade": grade,
                "page_identity_key": contract_identity_key(
                    request["platform_name"],
                    None,
                    name,
                    grade,
                ),
            }
        )
    return mappings


def _materialize_list_with_end_and_restore(
    window,
    request,
    timeout_seconds,
    *,
    focus_action,
    empty_marker_visible,
    end_marker_visible,
    top_state_reader,
    stop_error_code,
    stop_error_message,
    end_error_code,
    end_error_message,
    top_error_code,
    top_error_message,
    load_wait_seconds=V5_KEYBOARD_LOAD_WAIT_SECONDS,
    end_load_wait_seconds=V5_KEYBOARD_END_LOAD_WAIT_SECONDS,
    max_end_actions=1,
    max_end_seconds=None,
):
    """Materialize one list through the verified focus/END/HOME control flow."""

    if empty_marker_visible(window, timeout_seconds):
        return {
            "trusted_empty": True,
            "end_marker_verified": False,
            "scroll_count": 0,
            "scroll_progress_verified": True,
            "top_state": None,
        }
    focus_action()
    sleep(float(load_wait_seconds))
    end_action_count = 0
    end_marker_verified = False
    try:
        end_deadline = None
        if max_end_seconds is not None:
            end_deadline = time.time() + max(1.0, float(max_end_seconds))
        while end_action_count < max(1, int(max_end_actions)):
            stop_path = str(request.get("_stop_signal_path") or "").strip()
            if stop_path and os.path.exists(stop_path):
                raise SliceError(
                    stop_error_code,
                    stop_error_message,
                    True,
                )
            if end_deadline is not None and time.time() > end_deadline:
                break
            window.activate()
            window.wait_active(timeout=min(float(timeout_seconds), 3.0))
            win32.send_keys(
                "{END}",
                50,
                False,
                0.0,
                True,
                False,
            )
            end_action_count += 1
            sleep(float(end_load_wait_seconds))
            if end_marker_visible(window, timeout_seconds):
                end_marker_verified = True
                break
        if not end_marker_verified:
            error = SliceError(
                end_error_code,
                end_error_message,
                True,
            )
            error.scroll_count = end_action_count
            error.scroll_progress_verified = False
            raise error
    finally:
        try:
            window.activate()
            window.wait_active(timeout=min(float(timeout_seconds), 3.0))
            win32.send_keys(
                "{HOME}",
                50,
                False,
                0.0,
                True,
                False,
            )
            sleep(float(load_wait_seconds))
        except Exception:
            pass
    top_state = None
    top_ready_deadline = time.time() + min(float(timeout_seconds), 3.0)
    while time.time() < top_ready_deadline:
        try:
            candidate = top_state_reader()
        except Exception:
            candidate = None
        if candidate:
            top_state = candidate
            break
        sleep(float(load_wait_seconds))
    if not top_state:
        error = SliceError(
            top_error_code,
            top_error_message,
            True,
        )
        error.scroll_count = end_action_count
        error.scroll_progress_verified = end_marker_verified
        raise error
    return {
        "trusted_empty": False,
        "end_marker_verified": end_marker_verified,
        "scroll_count": end_action_count,
        "scroll_progress_verified": bool(end_action_count),
        "top_state": top_state,
    }


def _v5_scan_page(
    window,
    request,
    timeout_seconds,
    *,
    page_type,
    targets=None,
    timing_trace=None,
    timing_stage="",
    max_pages=20,
    max_scrolls=100,
):
    """Materialize with END, return HOME, then perform one full page scan.

    SET_ONLINE, SET_OFFLINE, post-failure reconciliation, and independent
    SYNC_STATUS all call this shared reader.  END is only a loading action:
    no row enumeration is allowed before HOME.
    """
    scan_timer = time.perf_counter()
    scan_started_at = _multi_product_utc_now()
    termination_reason = ""

    def read_product_top_state():
        candidate_rows = _enumerate_product_rows(
            window,
            timeout_seconds,
            page_type=page_type,
        )
        if candidate_rows and not candidate_rows[0].get("error"):
            return candidate_rows
        return None

    materialized = _materialize_list_with_end_and_restore(
        window,
        request,
        timeout_seconds,
        focus_action=lambda: _find_element(
            window,
            _row_field_selector(ROW_INDEX_START, "name", page_type),
            timeout_seconds,
        ).click(),
        empty_marker_visible=_product_list_empty_marker_visible,
        end_marker_visible=_product_list_end_marker_visible,
        top_state_reader=read_product_top_state,
        stop_error_code="BATCH_STOPPED",
        stop_error_message="worker stop requested during SYNC_STATUS",
        end_error_code="END_MARKER_NOT_VERIFIED",
        end_error_message=(
            page_type + " 页面聚焦商品列表并发送 END 后仍未确认结束标记"
        ),
        top_error_code="LIST_TOP_NOT_VERIFIED",
        top_error_message=page_type + " 页面 HOME 后商品元素未恢复",
    )
    if materialized["trusted_empty"]:
        completed_at = _multi_product_utc_now()
        scan_duration_ms = max(
            0,
            int(round((time.perf_counter() - scan_timer) * 1000)),
        )
        if timing_trace is not None:
            timing_trace.append(
                {
                    "stage": str(
                        timing_stage
                        or ("%s_page_scan" % str(page_type))
                    ),
                    "duration_ms": scan_duration_ms,
                    "page_type": str(page_type),
                    "identity_rows": 0,
                    "target_rows_hydrated": 0,
                    "price_reads": 0,
                    "inventory_reads": 0,
                    "non_target_rows_skipped": 0,
                }
            )
        return {
            "page_type": page_type,
            "scan_started_at": scan_started_at,
            "scan_completed_at": completed_at,
            "duration_ms": scan_duration_ms,
            "scan_complete": True,
            "end_marker_verified": True,
            "termination_reason": "EMPTY_LIST_MARKER",
            "rows": [],
        }
    termination_reason = "KEYBOARD_END_WITH_END_MARKER"
    home_ready_rows = materialized["top_state"]
    scan_metrics = {}
    rows = _multi_product_enumerate_rows(
        window,
        timeout_seconds,
        targets=targets,
        row_cache={},
        metrics=scan_metrics,
        page_type=page_type,
        identity_rows=home_ready_rows,
    )
    if not rows:
        raise SliceError(
            "LIST_NOT_LOADED",
            page_type + " 页面回到顶部后商品列表为空或未加载",
            True,
        )
    for row in rows:
        if row.get("error"):
            raise SliceError(
                str(row.get("error") or "ROW_READ_FAILED"),
                str(row.get("detail") or "商品行读取失败"),
                True,
            )
        if row.get("price_error_code") or row.get("inventory_error_code"):
            raise SliceError(
                str(
                    row.get("price_error_code")
                    or row.get("inventory_error_code")
                ),
                str(
                    row.get("price_error_message")
                    or row.get("inventory_error_message")
                ),
                True,
            )
    if not _product_list_end_marker_visible(
        window,
        timeout_seconds,
    ):
        raise SliceError(
            "FULL_LIST_NOT_MATERIALIZED_AFTER_HOME",
            page_type + " 页面 END 加载后回顶，完整元素树或结束标记不可验证",
            True,
        )
    completed_at = _multi_product_utc_now()
    rows = sorted(
        rows,
        key=lambda row: (
            int(row.get("parent_index") or 0),
            str(row.get("row_identity") or ""),
        ),
    )
    for position, row in enumerate(rows, start=1):
        row["position"] = position
        row["page_type"] = page_type
        row["observed_at"] = completed_at
    scan_duration_ms = max(
        0,
        int(round((time.perf_counter() - scan_timer) * 1000)),
    )
    if timing_trace is not None:
        timing_trace.append(
            {
                "stage": str(
                    timing_stage
                    or ("%s_page_scan" % str(page_type))
                ),
                "duration_ms": scan_duration_ms,
                "page_type": str(page_type),
                "identity_rows": len(rows),
                "target_rows_hydrated": int(
                    scan_metrics.get("target_rows_hydrated", 0)
                ),
                "price_reads": int(scan_metrics.get("price_reads", 0)),
                "inventory_reads": int(
                    scan_metrics.get("inventory_reads", 0)
                ),
                "non_target_rows_skipped": int(
                    scan_metrics.get("non_target_rows_skipped", 0)
                ),
            }
        )
    return {
        "page_type": page_type,
        "scan_started_at": scan_started_at,
        "scan_completed_at": completed_at,
        "duration_ms": scan_duration_ms,
        "scan_complete": True,
        "end_marker_verified": True,
        "termination_reason": termination_reason,
        "rows": rows,
    }


def _v5_reset_page_to_top(window, request, timeout_seconds, page_type):
    viewport = {
        "x": _as_int(request, "window_x", WINDOW_X_DEFAULT),
        "y": _as_int(request, "window_y", WINDOW_Y_DEFAULT),
        "width": _as_int(
            request,
            "window_width",
            WINDOW_WIDTH_DEFAULT,
            minimum=100,
        ),
        "height": _as_int(
            request,
            "window_height",
            WINDOW_HEIGHT_DEFAULT,
            minimum=100,
        ),
    }
    for _ in range(2):
        if not _advance_product_list(
            window,
            timeout_seconds,
            direction="up",
            page_type=page_type,
            wheel_times=1,
            settle_seconds=1.0,
            wheel_delay_after=0.2,
            viewport=viewport,
            keyboard_key="{HOME}",
        ):
            raise SliceError(
                "SCROLL_UNAVAILABLE",
                page_type + " 页面无法在扫描前回到列表顶部",
                True,
            )


def _v5_prepare_row_for_click(
    window,
    request,
    timeout_seconds,
    *,
    row,
    page_type,
    click_target="price",
):
    """Scroll only when the pre-scan proved that the target is row 4 or later.

    The visibility probe must use the element that will actually be clicked.
    A row's price can still be visible while the action button below it is
    already outside the safe viewport.
    """

    position = int(row.get("position") or 0)
    parent_index = int(row["parent_index"])
    if position < SINGLE_PRODUCT_SCROLL_START_POSITION:
        return
    viewport = {
        "window_x": _as_int(request, "window_x", WINDOW_X_DEFAULT),
        "window_y": _as_int(request, "window_y", WINDOW_Y_DEFAULT),
        "window_width": _as_int(
            request,
            "window_width",
            WINDOW_WIDTH_DEFAULT,
            minimum=100,
        ),
        "window_height": _as_int(
            request,
            "window_height",
            WINDOW_HEIGHT_DEFAULT,
            minimum=100,
        ),
    }
    attempts = []
    for attempt in range(0, SINGLE_PRODUCT_MAX_SCROLL_ATTEMPTS + 1):
        if click_target == "set_online_action":
            target_element = _find_element(
                window,
                _waiting_row_action_selector(
                    parent_index,
                    WAITING_SET_ONLINE_BUTTON_SELECTOR,
                    "动态_index_%d_上架按钮" % parent_index,
                ),
                timeout_seconds,
            )
        elif click_target == "set_offline_action":
            target_element = _find_element(
                window,
                _online_row_action_selector(
                    parent_index,
                    ONLINE_SET_OFFLINE_BUTTON_SELECTOR,
                    "动态_index_%d_下架按钮" % parent_index,
                ),
                timeout_seconds,
            )
        else:
            target_element = _find_element(
                window,
                _row_field_selector(parent_index, "price", page_type),
                timeout_seconds,
            )
        clickable, bounding = _price_element_in_clickable_view(
            target_element,
            **viewport,
        )
        center_y = bounding["y"] + bounding["height"] / 2.0
        safe_top = float(viewport["window_y"]) + SINGLE_PRODUCT_CLICK_TOP_MARGIN
        keyboard_key = "NONE"
        if not clickable:
            keyboard_key = "PGUP" if center_y < safe_top else "PGDN"
        attempts.append(
            {
                "attempt": attempt,
                "position": position,
                "keyboard_key": keyboard_key,
                "click_target": click_target,
                "target_bounding": bounding,
                "clickable": clickable,
            }
        )
        if clickable:
            return attempts
        if attempt >= SINGLE_PRODUCT_MAX_SCROLL_ATTEMPTS:
            break
        focus_element = _find_element(
            window,
            _row_field_selector(ROW_INDEX_START, "name", page_type),
            timeout_seconds,
        )
        focus_element.click()
        sleep(V5_KEYBOARD_LOAD_WAIT_SECONDS)
        window.activate()
        window.wait_active(timeout=min(float(timeout_seconds), 3.0))
        win32.send_keys(
            "{" + keyboard_key + "}",
            50,
            False,
            0.0,
            True,
            False,
        )
        sleep(0.3)
    raise SliceError(
        "ELEMENT_NOT_VISIBLE",
        "第%d个商品滚动后仍不在安全点击区域: %s"
        % (position, json.dumps(attempts, ensure_ascii=False)),
        retryable=True,
    )


def _v5_snapshot_items(request, snapshot_id, mappings, online_scan, waiting_scan):
    mapping_by_identity = {}
    for mapping in mappings:
        mapping_by_identity.setdefault(mapping["page_identity_key"], []).append(
            mapping
        )
    rows_by_page = {"online": {}, "waiting": {}}
    for page_type, scan in (
        ("online", online_scan),
        ("waiting", waiting_scan),
    ):
        for row in scan["rows"]:
            identity = contract_identity_key(
                request["platform_name"],
                None,
                row.get("name"),
                row.get("grade"),
            )
            rows_by_page[page_type].setdefault(identity, []).append(row)
    identities = sorted(
        set(mapping_by_identity)
        | set(rows_by_page["online"])
        | set(rows_by_page["waiting"])
    )
    items = []
    for identity in identities:
        mapping_candidates = mapping_by_identity.get(identity, [])
        online_rows = rows_by_page["online"].get(identity, [])
        waiting_rows = rows_by_page["waiting"].get(identity, [])
        mapping_ambiguous = (
            len(mapping_candidates) != 1
            or len(online_rows) > 1
            or len(waiting_rows) > 1
        )
        sample = (
            online_rows[0]
            if online_rows
            else waiting_rows[0]
            if waiting_rows
            else mapping_candidates[0]
        )
        affected = sorted(
            set(
                mapping["internal_sku"]
                for mapping in mapping_candidates
            )
        )
        internal_sku = (
            mapping_candidates[0]["internal_sku"]
            if len(mapping_candidates) == 1
            else None
        )
        if mapping_ambiguous:
            listing_location = "ambiguous"
        elif online_rows and waiting_rows:
            listing_location = "both"
        elif online_rows:
            listing_location = "online_only"
        elif waiting_rows:
            listing_location = "waiting_only"
        else:
            listing_location = "neither"
        diagnostic_code = ""
        if not mapping_candidates:
            diagnostic_code = "UNMAPPED_PRODUCT"
        elif len(mapping_candidates) > 1:
            diagnostic_code = "IDENTITY_MAPPING_CONFLICT"
        elif len(online_rows) > 1 or len(waiting_rows) > 1:
            diagnostic_code = "DUPLICATE_PAGE_IDENTITY"
        elif listing_location == "both":
            diagnostic_code = "PRESENT_IN_BOTH_LISTS"
        elif listing_location == "neither":
            diagnostic_code = "ABSENT_FROM_BOTH_LISTS"
        item = {
            "snapshot_item_id": _v5_stable_id(
                "SNAPSHOT-ITEM",
                {
                    "snapshot_id": snapshot_id,
                    "page_identity_key": identity,
                },
            ),
            "internal_sku": internal_sku,
            "product_name": str(
                sample.get("name")
                or sample.get("product_name")
                or ""
            ).strip(),
            "grade": str(sample.get("grade") or "").strip(),
            "page_identity_key": identity,
            "affected_internal_skus": affected,
            "online_occurrences": len(online_rows),
            "waiting_occurrences": len(waiting_rows),
            "mapping_ambiguous": mapping_ambiguous,
            "listing_location": listing_location,
            "online_row_identities": [
                "online:" + str(row.get("row_identity") or "")
                for row in online_rows
            ],
            "waiting_row_identities": [
                "waiting:" + str(row.get("row_identity") or "")
                for row in waiting_rows
            ],
            "online_observed_price": (
                str(online_rows[0].get("price") or "") if online_rows else None
            ),
            "waiting_observed_price": (
                str(waiting_rows[0].get("price") or "") if waiting_rows else None
            ),
            "online_observed_inventory": (
                int(online_rows[0]["inventory"])
                if online_rows
                and online_rows[0].get("inventory") is not None
                else None
            ),
            "waiting_observed_inventory": (
                int(waiting_rows[0]["inventory"])
                if waiting_rows
                and waiting_rows[0].get("inventory") is not None
                else None
            ),
            "diagnostic_code": diagnostic_code,
            "online_observed_at": (
                online_rows[0]["observed_at"] if online_rows else None
            ),
            "waiting_observed_at": (
                waiting_rows[0]["observed_at"] if waiting_rows else None
            ),
        }
        items.append(item)
    return items


def _v5_dismiss_unconfirmed_detail_or_listing_dialog(window):
    """Best-effort cleanup before a read-only post-failure page scan."""

    for selector_name in (
        ONLINE_SET_OFFLINE_CANCEL_SELECTOR,
        WAITING_SET_ONLINE_CANCEL_SELECTOR,
        WAITING_PRICE_CANCEL_SELECTOR,
        WAITING_INVENTORY_CANCEL_SELECTOR,
    ):
        try:
            _find_element(window, selector_name, 1).click()
            sleep(0.5)
            return
        except Exception:
            continue
    try:
        window.activate()
        win32.send_keys("{ESC}", 50, False, 0.0, True, False)
        sleep(0.5)
    except Exception:
        pass


def _v5_post_failure_snapshot(
    window,
    request,
    result,
    timeout_seconds,
    mappings,
):
    """Capture final two-page facts without changing the write outcome."""

    scan_started_at = _multi_product_utc_now()
    snapshot_id = _v5_stable_id(
        "SNAPSHOT-POSTFAIL",
        {
            "batch_id": request["batch_id"],
            "execution_attempt_id": request["execution_attempt_id"],
        },
    )
    _v5_dismiss_unconfirmed_detail_or_listing_dialog(window)
    _select_online_product_list(window, timeout_seconds, result)
    online_scan = _v5_scan_page(
        window,
        request,
        timeout_seconds,
        page_type="online",
    )
    _select_waiting_product_list(window, timeout_seconds, result)
    waiting_scan = _v5_scan_page(
        window,
        request,
        timeout_seconds,
        page_type="waiting",
    )
    completed_at = _multi_product_utc_now()
    return {
        "schema_version": "shadowbot-listing-sync-snapshot-1.0",
        "snapshot_id": snapshot_id,
        "platform_name": request["platform_name"],
        "execution_attempt_id": request["execution_attempt_id"],
        "mapping_source_version": request["mapping_source_version"],
        "result_id": "PENDING-" + request["execution_attempt_id"],
        "scan_started_at": scan_started_at,
        "scan_completed_at": completed_at,
        "online_scan_started_at": online_scan["scan_started_at"],
        "online_scan_completed_at": online_scan["scan_completed_at"],
        "waiting_scan_started_at": waiting_scan["scan_started_at"],
        "waiting_scan_completed_at": waiting_scan["scan_completed_at"],
        "online_scan_complete": True,
        "waiting_scan_complete": True,
        "online_end_marker_verified": True,
        "waiting_end_marker_verified": True,
        "snapshot_complete": True,
        "instruction_hash": request["instruction_hash"],
        "status": "VERIFIED",
        "error_code": "",
        "evidence_manifest_sha256": sha256_json(
            {
                "online": online_scan,
                "waiting": waiting_scan,
            }
        ),
        "items": _v5_snapshot_items(
            request,
            snapshot_id,
            mappings,
            online_scan,
            waiting_scan,
        ),
    }


def _v5_failed_post_failure_snapshot(request, error):
    completed_at = _multi_product_utc_now()
    return {
        "schema_version": "shadowbot-listing-sync-snapshot-1.0",
        "snapshot_id": _v5_stable_id(
            "SNAPSHOT-POSTFAIL",
            {
                "batch_id": request["batch_id"],
                "execution_attempt_id": request["execution_attempt_id"],
            },
        ),
        "platform_name": request["platform_name"],
        "execution_attempt_id": request["execution_attempt_id"],
        "mapping_source_version": request["mapping_source_version"],
        "result_id": "PENDING-" + request["execution_attempt_id"],
        "scan_started_at": completed_at,
        "scan_completed_at": completed_at,
        "online_scan_started_at": completed_at,
        "online_scan_completed_at": completed_at,
        "waiting_scan_started_at": completed_at,
        "waiting_scan_completed_at": completed_at,
        "online_scan_complete": False,
        "waiting_scan_complete": False,
        "online_end_marker_verified": False,
        "waiting_end_marker_verified": False,
        "snapshot_complete": False,
        "instruction_hash": request["instruction_hash"],
        "status": "FAILED",
        "error_code": str(getattr(error, "code", "") or "POST_FAILURE_SCAN_FAILED"),
        "evidence_manifest_sha256": sha256_json([]),
        "items": [],
    }


def _run_listing_sync_v5(args, request, result):
    flow_timer = time.perf_counter()
    started_at = _multi_product_utc_now()
    timing_trace = result.setdefault("timing_trace", [])
    result_id = _v5_stable_id(
        "RESULT",
        {
            "batch_id": request["batch_id"],
            "execution_attempt_id": request["execution_attempt_id"],
        },
    )
    snapshot_id = _v5_stable_id(
        "SNAPSHOT",
        {
            "batch_id": request["batch_id"],
            "execution_attempt_id": request["execution_attempt_id"],
        },
    )
    timeout_seconds = _as_int(
        request,
        "element_timeout_seconds",
        ELEMENT_TIMEOUT_DEFAULT,
        minimum=1,
    )
    snapshot = None
    try:
        mappings = _v5_load_mapping(request)
        _v5_write_phase(request, "UI_STARTED")
        stage_timer = time.perf_counter()
        window, launch = _get_or_open_and_prepare_window(
            str(request.get("window_title") or WINDOW_TITLE_DEFAULT),
            WINDOW_X_DEFAULT,
            WINDOW_Y_DEFAULT,
            WINDOW_WIDTH_DEFAULT,
            WINDOW_HEIGHT_DEFAULT,
            str(request.get("applet_uri") or ""),
            APPLET_LAUNCH_TIMEOUT_DEFAULT,
        )
        result["applet_launch"] = launch
        sleep(1)
        _v5_record_timing(
            timing_trace,
            "window_prepare",
            stage_timer,
        )
        login_config = _get_arg(args, "_login_config", {})
        credential_provider = _get_arg(args, "_credential_provider", None)
        stage_timer = time.perf_counter()
        _recover_login_if_needed(
            window,
            request,
            result,
            timeout_seconds,
            login_config,
            credential_provider,
        )
        _v5_record_timing(
            timing_trace,
            "login_check",
            stage_timer,
        )
        stage_timer = time.perf_counter()
        _refresh_product_list(
            window,
            timeout_seconds,
            result,
            "BEFORE_LISTING_SYNC",
        )
        _v5_record_timing(
            timing_trace,
            "product_list_refresh",
            stage_timer,
        )
        online_scan = _v5_scan_page(
            window,
            request,
            timeout_seconds,
            page_type="online",
            targets=None,
            timing_trace=timing_trace,
            timing_stage="sync_online_scan",
        )
        _select_waiting_product_list(window, timeout_seconds, result)
        waiting_scan = _v5_scan_page(
            window,
            request,
            timeout_seconds,
            page_type="waiting",
            targets=None,
            timing_trace=timing_trace,
            timing_stage="sync_waiting_scan",
        )
        items = _v5_snapshot_items(
            request,
            snapshot_id,
            mappings,
            online_scan,
            waiting_scan,
        )
        completed_at = _multi_product_utc_now()
        snapshot = {
            "schema_version": "shadowbot-listing-sync-snapshot-1.0",
            "snapshot_id": snapshot_id,
            "platform_name": request["platform_name"],
            "execution_attempt_id": request["execution_attempt_id"],
            "mapping_source_version": request["mapping_source_version"],
            "result_id": result_id,
            "scan_started_at": started_at,
            "scan_completed_at": completed_at,
            "online_scan_started_at": online_scan["scan_started_at"],
            "online_scan_completed_at": online_scan["scan_completed_at"],
            "waiting_scan_started_at": waiting_scan["scan_started_at"],
            "waiting_scan_completed_at": waiting_scan["scan_completed_at"],
            "online_scan_complete": True,
            "waiting_scan_complete": True,
            "online_end_marker_verified": True,
            "waiting_end_marker_verified": True,
            "snapshot_complete": True,
            "instruction_hash": request["instruction_hash"],
            "status": "VERIFIED",
            "error_code": "",
            "evidence_manifest_sha256": sha256_json(
                {
                    "online": online_scan,
                    "waiting": waiting_scan,
                }
            ),
            "items": items,
        }
        result.update(
            {
                "result_id": result_id,
                "started_at": started_at,
                "ended_at": completed_at,
                "status": "VERIFIED",
                "run_success_flag": True,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": "",
                "error_message": "",
                "retryable": False,
                "snapshot": snapshot,
            }
        )
        _v5_write_phase(request, "FINAL_VERIFICATION")
    except SliceError as exc:
        completed_at = _multi_product_utc_now()
        snapshot = {
            "schema_version": "shadowbot-listing-sync-snapshot-1.0",
            "snapshot_id": snapshot_id,
            "platform_name": request["platform_name"],
            "execution_attempt_id": request["execution_attempt_id"],
            "mapping_source_version": request["mapping_source_version"],
            "result_id": result_id,
            "scan_started_at": started_at,
            "scan_completed_at": completed_at,
            "online_scan_started_at": started_at,
            "online_scan_completed_at": completed_at,
            "waiting_scan_started_at": completed_at,
            "waiting_scan_completed_at": completed_at,
            "online_scan_complete": False,
            "waiting_scan_complete": False,
            "online_end_marker_verified": False,
            "waiting_end_marker_verified": False,
            "snapshot_complete": False,
            "instruction_hash": request["instruction_hash"],
            "status": "FAILED",
            "error_code": exc.code,
            "evidence_manifest_sha256": sha256_json([]),
            "items": [],
        }
        result.update(
            {
                "result_id": result_id,
                "started_at": started_at,
                "ended_at": completed_at,
                "status": "FAILED",
                "run_success_flag": False,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": exc.code,
                "error_message": exc.message,
                "retryable": exc.retryable,
                "snapshot": snapshot,
            }
        )
    _v5_record_timing(
        timing_trace,
        "listing_sync_total",
        flow_timer,
    )
    return _set_result(args, result)


def _v5_write_result_semantics(items):
    counts = v5_result_counts(items)
    return counts, derive_v5_batch_semantics(counts)


def _v5_result_item(request_item):
    return {
        name: request_item.get(name)
        for name in (
            "source_task_id",
            "operation_id",
            "item_execution_attempt_id",
            "internal_sku",
            "item_payload_sha256",
        )
    } | {
        "operation_result": "NOT_ATTEMPTED",
        "detail_effect_state": "NOT_STARTED",
        "listing_effect_state": "NOT_STARTED",
        "detail_save_clicked": False,
        "action_confirm_clicked": False,
        "observed_price_before_action": None,
        "observed_inventory_before_action": None,
        "observed_price_after_detail_save": None,
        "observed_inventory_after_detail_save": None,
        "actual_price": None,
        "actual_inventory": None,
        "detail_save_clicked_at": None,
        "action_clicked_at": None,
        "readback_observed_at": None,
        "error_code": "",
        "error_message": "",
    }


def _v5_item_identity(request, request_item):
    return contract_identity_key(
        request["platform_name"],
        None,
        request_item["expected_product_name"],
        request_item["expected_grade"],
    )


def _v5_matching_rows(request, request_item, rows):
    expected_identity = _v5_item_identity(request, request_item)
    return [
        row
        for row in rows
        if contract_identity_key(
            request["platform_name"],
            None,
            row.get("name"),
            row.get("grade"),
        )
        == expected_identity
    ]


def _v5_phase_for_items(
    request,
    phase,
    request_item,
    item_results,
    *,
    clicked_at=None,
):
    current = next(
        (
            item
            for item in item_results
            if item.get("operation_id") == request_item.get("operation_id")
        ),
        None,
    )
    _v5_write_phase(
        request,
        phase,
        current_item=request_item,
        item_states=item_results,
        clicked_at=clicked_at,
        detail_effect_state=(
            str(current.get("detail_effect_state") or "NOT_STARTED")
            if current
            else "NOT_STARTED"
        ),
        listing_effect_state=(
            str(current.get("listing_effect_state") or "NOT_STARTED")
            if current
            else "NOT_STARTED"
        ),
    )


def _v5_open_emergency_click_fence(request, request_item):
    binding = request.get("emergency_authorization")
    if binding is None:
        return None
    runtime_db_path = str(binding.get("runtime_db_path") or "")
    if (
        not runtime_db_path
        or not os.path.isabs(runtime_db_path)
        or not os.path.isfile(runtime_db_path)
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_RUNTIME_DB_UNAVAILABLE")
    connection = sqlite3.connect(runtime_db_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        revalidate_emergency_offline_facts(
            connection,
            binding=binding,
            now=datetime.now(timezone.utc),
            allowed_task_statuses={"running"},
            operation_id=str(request_item.get("operation_id") or ""),
            require_active_lock=True,
        )
        record_emergency_final_click_fence_won(
            connection,
            binding=binding,
            crossed_at=datetime.now(timezone.utc),
        )
        return connection
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
        raise


def _v5_close_emergency_click_fence(connection, *, crossed_click_boundary=False):
    if connection is None:
        return
    try:
        if connection.in_transaction:
            if crossed_click_boundary:
                connection.commit()
            else:
                connection.rollback()
    finally:
        connection.close()


def _v5_classify_interrupted_items(
    item_results,
    *,
    current_operation_id,
    error_code,
    error_message,
    detail_intent_operation_id="",
):
    for item in item_results:
        outcome = str(item.get("operation_result") or "").upper()
        if outcome in {"VERIFIED", "ALREADY_APPLIED"}:
            continue
        operation_id = str(item.get("operation_id") or "")
        is_current = operation_id == str(current_operation_id or "")
        if item.get("action_confirm_clicked"):
            item["operation_result"] = "NEEDS_RECONCILIATION"
            item["listing_effect_state"] = "UNKNOWN"
        elif operation_id == str(detail_intent_operation_id or ""):
            item["operation_result"] = "NEEDS_RECONCILIATION"
            item["detail_effect_state"] = "UNKNOWN"
            item["listing_effect_state"] = "NOT_STARTED"
        elif (
            item.get("detail_save_clicked")
            and str(item.get("detail_effect_state") or "").upper() == "UNKNOWN"
        ):
            item["operation_result"] = "NEEDS_RECONCILIATION"
            item["listing_effect_state"] = "NOT_STARTED"
        elif (
            item.get("detail_save_clicked")
            and str(item.get("detail_effect_state") or "").upper() == "VERIFIED"
        ):
            item["operation_result"] = "PARTIALLY_APPLIED"
            item["listing_effect_state"] = "NOT_STARTED"
        elif is_current:
            item["operation_result"] = "NOT_APPLIED"
            item["detail_effect_state"] = "NOT_APPLIED"
            item["listing_effect_state"] = "NOT_APPLIED"
        else:
            item["operation_result"] = "NOT_ATTEMPTED"
            item["detail_effect_state"] = "NOT_STARTED"
            item["listing_effect_state"] = "NOT_STARTED"
        if is_current:
            item["error_code"] = str(error_code or "")
            item["error_message"] = str(error_message or "")


def _v5_raise_controlled_action_unknown(request, execution_ordinal):
    fault_injection = str(
        request.get("fault_injection") or ""
    ).strip().upper()
    fault_ordinal = request.get("fault_injection_item_ordinal")
    if (
        fault_injection == "AFTER_ACTION_CLICK_UNKNOWN"
        and fault_ordinal == execution_ordinal
    ):
        raise SliceError(
            "CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN",
            "受控故障：最终确认已点击，跳过结果回读",
            retryable=False,
        )


def _run_listing_action_reconcile_v5(args, request, result):
    """Read-only reconciliation for one UNKNOWN v5 listing operation."""

    flow_timer = time.perf_counter()
    started_at = _multi_product_utc_now()
    timing_trace = result.setdefault("timing_trace", [])
    request_item = dict((request.get("items") or [])[0])
    output = _v5_result_item(request_item)
    timeout_seconds = _as_int(
        request,
        "element_timeout_seconds",
        ELEMENT_TIMEOUT_DEFAULT,
        minimum=1,
    )
    execution_error = None
    try:
        _v5_load_mapping(request)
        _v5_phase_for_items(
            request,
            "UI_STARTED",
            request_item,
            [output],
        )
        stage_timer = time.perf_counter()
        window, launch = _get_or_open_and_prepare_window(
            str(request.get("window_title") or WINDOW_TITLE_DEFAULT),
            WINDOW_X_DEFAULT,
            WINDOW_Y_DEFAULT,
            WINDOW_WIDTH_DEFAULT,
            WINDOW_HEIGHT_DEFAULT,
            str(request.get("applet_uri") or ""),
            APPLET_LAUNCH_TIMEOUT_DEFAULT,
        )
        result["applet_launch"] = launch
        sleep(1)
        _v5_record_timing(
            timing_trace,
            "window_prepare",
            stage_timer,
        )
        stage_timer = time.perf_counter()
        _recover_login_if_needed(
            window,
            request,
            result,
            timeout_seconds,
            _get_arg(args, "_login_config", {}),
            _get_arg(args, "_credential_provider", None),
        )
        _v5_record_timing(
            timing_trace,
            "login_check",
            stage_timer,
        )
        stage_timer = time.perf_counter()
        _refresh_product_list(
            window,
            timeout_seconds,
            result,
            "BEFORE_LISTING_RECONCILE",
        )
        _v5_record_timing(
            timing_trace,
            "product_list_refresh",
            stage_timer,
        )
        online_scan = _v5_scan_page(
            window,
            request,
            timeout_seconds,
            page_type="online",
            targets=[request_item],
            timing_trace=timing_trace,
            timing_stage="reconcile_online_scan",
        )
        online_rows = _v5_matching_rows(
            request,
            request_item,
            online_scan["rows"],
        )
        waiting_rows = []
        if request["action_type"] == "set_online":
            _select_waiting_product_list(
                window,
                timeout_seconds,
                result,
            )
            waiting_scan = _v5_scan_page(
                window,
                request,
                timeout_seconds,
                page_type="waiting",
                targets=[request_item],
                timing_trace=timing_trace,
                timing_stage="reconcile_waiting_scan",
            )
            waiting_rows = _v5_matching_rows(
                request,
                request_item,
                waiting_scan["rows"],
            )
            readback_at = waiting_scan["scan_completed_at"]
        else:
            readback_at = online_scan["scan_completed_at"]
        _v5_phase_for_items(
            request,
            "PREFLIGHT_VALIDATED",
            request_item,
            [output],
        )
        if len(online_rows) > 1 or len(waiting_rows) > 1:
            output.update(
                {
                    "operation_result": "NEEDS_RECONCILIATION",
                    "listing_effect_state": "UNKNOWN",
                    "readback_observed_at": readback_at,
                    "error_code": "PRODUCT_IDENTITY_NOT_UNIQUE",
                    "error_message": "对账扫描发现目标身份不唯一",
                }
            )
        elif request["action_type"] == "set_offline":
            if not online_rows:
                output.update(
                    {
                        "operation_result": "VERIFIED",
                        "detail_effect_state": "NOT_APPLIED",
                        "listing_effect_state": "VERIFIED",
                        "readback_observed_at": readback_at,
                    }
                )
            else:
                row = online_rows[0]
                output.update(
                    {
                        "operation_result": "NOT_APPLIED",
                        "detail_effect_state": "NOT_APPLIED",
                        "listing_effect_state": "NOT_APPLIED",
                        "observed_price_before_action": str(
                            row.get("price")
                        ),
                        "observed_inventory_before_action": int(
                            row.get("inventory")
                        ),
                        "actual_price": str(row.get("price")),
                        "actual_inventory": int(row.get("inventory")),
                        "readback_observed_at": readback_at,
                    }
                )
        elif online_rows and waiting_rows:
            output.update(
                {
                    "operation_result": "NEEDS_RECONCILIATION",
                    "listing_effect_state": "UNKNOWN",
                    "readback_observed_at": readback_at,
                    "error_code": "PRESENT_IN_BOTH_LISTS",
                    "error_message": "目标同时存在于上架中和待上架",
                }
            )
        elif not online_rows and not waiting_rows:
            output.update(
                {
                    "operation_result": "NEEDS_RECONCILIATION",
                    "listing_effect_state": "UNKNOWN",
                    "readback_observed_at": readback_at,
                    "error_code": "ABSENT_FROM_BOTH_LISTS",
                    "error_message": "目标在两个完整列表中均不存在",
                }
            )
        elif waiting_rows:
            row = waiting_rows[0]
            output.update(
                {
                    "operation_result": "NOT_APPLIED",
                    "detail_effect_state": "NOT_APPLIED",
                    "listing_effect_state": "NOT_APPLIED",
                    "observed_price_before_action": str(row.get("price")),
                    "observed_inventory_before_action": int(
                        row.get("inventory")
                    ),
                    "actual_price": str(row.get("price")),
                    "actual_inventory": int(row.get("inventory")),
                    "readback_observed_at": readback_at,
                }
            )
        else:
            row = online_rows[0]
            observed_price = str(row.get("price"))
            observed_inventory = int(row.get("inventory"))
            output.update(
                {
                    "observed_price_before_action": observed_price,
                    "observed_inventory_before_action": observed_inventory,
                    "actual_price": observed_price,
                    "actual_inventory": observed_inventory,
                    "readback_observed_at": readback_at,
                }
            )
            if (
                observed_price == str(request_item.get("target_price"))
                and observed_inventory
                == int(request_item.get("target_inventory"))
            ):
                output.update(
                    {
                        "operation_result": "VERIFIED",
                        "detail_effect_state": "VERIFIED",
                        "listing_effect_state": "VERIFIED",
                    }
                )
            else:
                output.update(
                    {
                        "operation_result": "NEEDS_RECONCILIATION",
                        "detail_effect_state": "UNKNOWN",
                        "listing_effect_state": "VERIFIED",
                        "error_code": "LISTING_DATA_MISMATCH",
                        "error_message": "商品已在线但价格或库存不符合目标",
                    }
                )
        _v5_phase_for_items(
            request,
            "FINAL_VERIFICATION",
            request_item,
            [output],
        )
    except SliceError as exc:
        execution_error = exc
        output.update(
            {
                "operation_result": "NEEDS_RECONCILIATION",
                "listing_effect_state": "UNKNOWN",
                "error_code": exc.code,
                "error_message": exc.message,
            }
        )
    _v5_record_timing(
        timing_trace,
        "listing_action_reconcile_total",
        flow_timer,
        internal_sku=request_item["internal_sku"],
    )
    counts, semantics = _v5_write_result_semantics([output])
    result.update(
        {
            "started_at": started_at,
            "ended_at": _multi_product_utc_now(),
            "items": [output],
            "counts": counts,
            **semantics,
            "error_code": str(
                getattr(execution_error, "code", "")
                or output.get("error_code")
                or ""
            ),
            "error_message": str(
                getattr(execution_error, "message", "")
                or output.get("error_message")
                or ""
            ),
            "retryable": False,
        }
    )
    return _set_result(args, result)


def _run_set_online_v5(args, request, result):
    flow_timer = time.perf_counter()
    started_at = _multi_product_utc_now()
    timing_trace = result.setdefault("timing_trace", [])
    request_items = list(request.get("items") or [])
    item_results = [_v5_result_item(item) for item in request_items]
    outputs_by_operation = {
        item["operation_id"]: output
        for item, output in zip(request_items, item_results)
    }
    timeout_seconds = _as_int(
        request,
        "element_timeout_seconds",
        ELEMENT_TIMEOUT_DEFAULT,
        minimum=1,
    )
    current_item = request_items[0]
    detail_intent_operation_id = ""
    execution_error = None
    window = None
    mappings = []
    try:
        mappings = _v5_load_mapping(request)
        _v5_phase_for_items(
            request,
            "UI_STARTED",
            current_item,
            item_results,
        )
        stage_timer = time.perf_counter()
        window, launch = _get_or_open_and_prepare_window(
            str(request.get("window_title") or WINDOW_TITLE_DEFAULT),
            WINDOW_X_DEFAULT,
            WINDOW_Y_DEFAULT,
            WINDOW_WIDTH_DEFAULT,
            WINDOW_HEIGHT_DEFAULT,
            str(request.get("applet_uri") or ""),
            APPLET_LAUNCH_TIMEOUT_DEFAULT,
        )
        result["applet_launch"] = launch
        sleep(1)
        _v5_record_timing(
            timing_trace,
            "window_prepare",
            stage_timer,
        )
        stage_timer = time.perf_counter()
        _recover_login_if_needed(
            window,
            request,
            result,
            timeout_seconds,
            _get_arg(args, "_login_config", {}),
            _get_arg(args, "_credential_provider", None),
        )
        _v5_record_timing(
            timing_trace,
            "login_check",
            stage_timer,
        )
        stage_timer = time.perf_counter()
        _refresh_product_list(
            window,
            timeout_seconds,
            result,
            "BEFORE_SET_ONLINE",
        )
        _v5_record_timing(
            timing_trace,
            "product_list_refresh",
            stage_timer,
        )
        online_scan = _v5_scan_page(
            window,
            request,
            timeout_seconds,
            page_type="online",
            targets=request_items,
            timing_trace=timing_trace,
            timing_stage="preflight_online_scan",
        )
        _select_waiting_product_list(window, timeout_seconds, result)
        waiting_scan = _v5_scan_page(
            window,
            request,
            timeout_seconds,
            page_type="waiting",
            targets=request_items,
            timing_trace=timing_trace,
            timing_stage="preflight_waiting_scan",
        )
        snapshot_items = _v5_snapshot_items(
            request,
            _v5_stable_id(
                "PREFLIGHT",
                {
                    "batch_id": request["batch_id"],
                    "execution_attempt_id": request["execution_attempt_id"],
                },
            ),
            mappings,
            online_scan,
            waiting_scan,
        )
        plans = []
        for request_item in request_items:
            current_item = request_item
            output = outputs_by_operation[request_item["operation_id"]]
            matched_snapshot_items = [
                item
                for item in snapshot_items
                if item.get("internal_sku") == request_item["internal_sku"]
            ]
            if len(matched_snapshot_items) != 1:
                raise SliceError(
                    "PRODUCT_IDENTITY_NOT_UNIQUE",
                    "预扫描无法按 SKU 唯一定位目标",
                    retryable=False,
                )
            preflight = matched_snapshot_items[0]
            target_price = str(request_item["target_price"])
            target_inventory = int(request_item["target_inventory"])
            location = str(preflight.get("listing_location") or "")
            observed_price = (
                preflight.get("waiting_observed_price")
                if location == "waiting_only"
                else preflight.get("online_observed_price")
            )
            observed_inventory = (
                preflight.get("waiting_observed_inventory")
                if location == "waiting_only"
                else preflight.get("online_observed_inventory")
            )
            output["observed_price_before_action"] = observed_price
            output["observed_inventory_before_action"] = observed_inventory
            if location == "online_only":
                if (
                    str(observed_price) != target_price
                    or int(observed_inventory) != target_inventory
                ):
                    raise SliceError(
                        "LISTING_DATA_MISMATCH",
                        "目标已在线但价格或库存不符合合同",
                        retryable=False,
                    )
                output.update(
                    {
                        "operation_result": "ALREADY_APPLIED",
                        "detail_effect_state": "NOT_APPLIED",
                        "listing_effect_state": "NOT_APPLIED",
                        "actual_price": observed_price,
                        "actual_inventory": observed_inventory,
                        "readback_observed_at": _multi_product_utc_now(),
                    }
                )
                continue
            if location != "waiting_only":
                raise SliceError(
                    "EXPECTED_WAITING_ONLY",
                    "SET_ONLINE 目标不是唯一 waiting_only",
                    retryable=False,
                )
            matching_rows = _v5_matching_rows(
                request,
                request_item,
                waiting_scan["rows"],
            )
            if len(matching_rows) != 1:
                raise SliceError(
                    "PRODUCT_IDENTITY_NOT_UNIQUE",
                    "待上架页面目标身份不唯一",
                    retryable=False,
                )
            plans.append(
                {
                    "request_item": request_item,
                    "row": matching_rows[0],
                    "target_price": target_price,
                    "target_inventory": target_inventory,
                }
            )
        plans.sort(key=lambda item: int(item["row"]["parent_index"]))
        _v5_phase_for_items(
            request,
            "PREFLIGHT_VALIDATED",
            plans[0]["request_item"] if plans else current_item,
            item_results,
        )

        removed_waiting_items = 0
        for plan in plans:
            item_timer = time.perf_counter()
            request_item = plan["request_item"]
            current_item = request_item
            output = outputs_by_operation[request_item["operation_id"]]
            row = dict(plan["row"])
            row["parent_index"] = (
                int(row["parent_index"])
                - removed_waiting_items * WAITING_ROW_INDEX_STEP
            )
            row["position"] = (
                int(row["position"]) - removed_waiting_items
            )
            if int(row["parent_index"]) < ROW_INDEX_START:
                raise SliceError(
                    "EXECUTION_TRAJECTORY_INVALID",
                    "预扫描生成的待上架行轨迹无效",
                    retryable=False,
                )
            plan["row"] = row
            parent_index = int(row["parent_index"])
            _v5_prepare_row_for_click(
                window,
                request,
                timeout_seconds,
                row=row,
                page_type="waiting",
            )
            _v5_record_timing(
                timing_trace,
                "item_visibility_prepare",
                item_timer,
                internal_sku=request_item["internal_sku"],
            )
            observed_price = output["observed_price_before_action"]
            observed_inventory = output["observed_inventory_before_action"]
            target_price = plan["target_price"]
            target_inventory = plan["target_inventory"]
            price_timer = time.perf_counter()
            if str(observed_price) != target_price:
                _open_price_dialog(
                    window,
                    parent_index,
                    timeout_seconds,
                    page_type="waiting",
                    settle_seconds=0.0,
                )
                _fill_target_price(
                    window,
                    target_price,
                    timeout_seconds,
                    page_type="waiting",
                )
                detail_intent_operation_id = request_item["operation_id"]
                _v5_phase_for_items(
                    request,
                    "DETAIL_SAVE_INTENT_RECORDED",
                    request_item,
                    item_results,
                )
                _confirm_price_dialog(
                    window,
                    timeout_seconds,
                    page_type="waiting",
                    settle_seconds=0.0,
                )
                _v5_wait_row_price(
                    window,
                    parent_index,
                    timeout_seconds,
                    target_price,
                    page_type="waiting",
                )
                detail_intent_operation_id = ""
                output["detail_save_clicked"] = True
                output["detail_save_clicked_at"] = _multi_product_utc_now()
                output["detail_effect_state"] = "UNKNOWN"
                _v5_phase_for_items(
                    request,
                    "DETAIL_SAVE_CLICKED",
                    request_item,
                    item_results,
                )
            _v5_record_timing(
                timing_trace,
                "item_price_update",
                price_timer,
                internal_sku=request_item["internal_sku"],
                changed=str(observed_price) != target_price,
            )
            inventory_timer = time.perf_counter()
            if int(observed_inventory) != target_inventory:
                inventory_element = _find_element(
                    window,
                    _row_field_selector(
                        parent_index,
                        "inventory",
                        "waiting",
                    ),
                    timeout_seconds,
                )
                inventory_element.click()
                _find_element(
                    window,
                    WAITING_INVENTORY_POPUP_SELECTOR,
                    timeout_seconds,
                )
                inventory_input = _find_element(
                    window,
                    WAITING_INVENTORY_INPUT_SELECTOR,
                    timeout_seconds,
                )
                _set_input_value(inventory_input, str(target_inventory))
                inventory_readback = _read_price_input(inventory_input)
                if (
                    inventory_readback
                    and int(Decimal(inventory_readback)) != target_inventory
                ):
                    _find_element(
                        window,
                        WAITING_INVENTORY_CANCEL_SELECTOR,
                        timeout_seconds,
                    ).click()
                    raise SliceError(
                        "TARGET_INVENTORY_VERIFY_FAILED",
                        "目标库存输入回读不一致",
                        retryable=True,
                    )
                detail_intent_operation_id = request_item["operation_id"]
                _v5_phase_for_items(
                    request,
                    "DETAIL_SAVE_INTENT_RECORDED",
                    request_item,
                    item_results,
                )
                _find_element(
                    window,
                    WAITING_INVENTORY_CONFIRM_SELECTOR,
                    timeout_seconds,
                ).click()
                _v5_wait_row_inventory(
                    window,
                    parent_index,
                    timeout_seconds,
                    target_inventory,
                    page_type="waiting",
                )
                detail_intent_operation_id = ""
                output["detail_save_clicked"] = True
                output["detail_save_clicked_at"] = _multi_product_utc_now()
                output["detail_effect_state"] = "UNKNOWN"
                _v5_phase_for_items(
                    request,
                    "DETAIL_SAVE_CLICKED",
                    request_item,
                    item_results,
                )
            _v5_record_timing(
                timing_trace,
                "item_inventory_update",
                inventory_timer,
                internal_sku=request_item["internal_sku"],
                changed=int(observed_inventory) != target_inventory,
            )
            detail_readback_timer = time.perf_counter()
            output["observed_price_after_detail_save"] = _read_row_price(
                window,
                parent_index,
                timeout_seconds,
                page_type="waiting",
            )
            output["observed_inventory_after_detail_save"] = (
                _read_row_inventory(
                    window,
                    parent_index,
                    timeout_seconds,
                    page_type="waiting",
                )
            )
            if (
                str(output["observed_price_after_detail_save"])
                != target_price
                or int(output["observed_inventory_after_detail_save"])
                != target_inventory
            ):
                raise SliceError(
                    "DETAIL_READBACK_MISMATCH",
                    "资料保存后价格或库存回读不符合目标",
                    retryable=False,
                )
            output["detail_effect_state"] = (
                "VERIFIED" if output["detail_save_clicked"] else "NOT_APPLIED"
            )
            _v5_phase_for_items(
                request,
                "DETAILS_VERIFIED",
                request_item,
                item_results,
            )
            _v5_record_timing(
                timing_trace,
                "item_detail_readback",
                detail_readback_timer,
                internal_sku=request_item["internal_sku"],
            )
            action_visibility_timer = time.perf_counter()
            _v5_prepare_row_for_click(
                window,
                request,
                timeout_seconds,
                row=row,
                page_type="waiting",
                click_target="set_online_action",
            )
            _v5_record_timing(
                timing_trace,
                "item_action_visibility",
                action_visibility_timer,
                internal_sku=request_item["internal_sku"],
            )
            action_timer = time.perf_counter()
            action_button = _find_element(
                window,
                _waiting_row_action_selector(
                    parent_index,
                    WAITING_SET_ONLINE_BUTTON_SELECTOR,
                    "动态_index_%d_上架按钮" % parent_index,
                ),
                timeout_seconds,
            )
            action_button.click()
            prompt_text = _read_text(
                window,
                WAITING_SET_ONLINE_PROMPT_SELECTOR,
                timeout_seconds,
            )
            _assert_set_online_confirmation_identity(
                prompt_text,
                request_item["expected_product_name"],
                request_item["expected_grade"],
            )
            _v5_phase_for_items(
                request,
                "ACTION_INTENT_RECORDED",
                request_item,
                item_results,
            )
            _find_element(
                window,
                WAITING_SET_ONLINE_CONFIRM_SELECTOR,
                timeout_seconds,
            ).click()
            output["action_confirm_clicked"] = True
            output["action_clicked_at"] = _multi_product_utc_now()
            output["listing_effect_state"] = "UNKNOWN"
            _v5_phase_for_items(
                request,
                "ACTION_CLICKED",
                request_item,
                item_results,
                clicked_at=output["action_clicked_at"],
            )
            _v5_wait_row_identity_changed(
                window,
                parent_index,
                timeout_seconds,
                page_type="waiting",
                expected_name=request_item["expected_product_name"],
                expected_grade=request_item["expected_grade"],
            )
            removed_waiting_items += 1
            _v5_record_timing(
                timing_trace,
                "item_action_confirm",
                action_timer,
                internal_sku=request_item["internal_sku"],
            )

        final_online_scan = online_scan
        if plans:
            _select_online_product_list(window, timeout_seconds, result)
            final_online_scan = _v5_scan_page(
                window,
                request,
                timeout_seconds,
                page_type="online",
                targets=request_items,
                timing_trace=timing_trace,
                timing_stage="final_online_scan",
            )
        for request_item in request_items:
            current_item = request_item
            output = outputs_by_operation[request_item["operation_id"]]
            post_rows = _v5_matching_rows(
                request,
                request_item,
                final_online_scan["rows"],
            )
            if len(post_rows) != 1:
                raise SliceError(
                    "POSTCHECK_NOT_ONLINE",
                    "统一回读后未在上架中唯一找到目标",
                    retryable=False,
                )
            post_row = post_rows[0]
            target_price = str(request_item["target_price"])
            target_inventory = int(request_item["target_inventory"])
            if (
                str(post_row.get("price")) != target_price
                or int(post_row.get("inventory")) != target_inventory
            ):
                raise SliceError(
                    "POSTCHECK_DATA_MISMATCH",
                    "上架后价格或库存不符合目标",
                    retryable=False,
                )
            output.update(
                {
                    "operation_result": "VERIFIED",
                    "listing_effect_state": "VERIFIED",
                    "actual_price": str(post_row.get("price")),
                    "actual_inventory": int(post_row.get("inventory")),
                    "readback_observed_at": post_row.get("observed_at"),
                }
            )
        _v5_phase_for_items(
            request,
            "FINAL_VERIFICATION",
            current_item,
            item_results,
        )
    except SliceError as exc:
        execution_error = exc
        _v5_classify_interrupted_items(
            item_results,
            current_operation_id=current_item.get("operation_id"),
            error_code=exc.code,
            error_message=exc.message,
            detail_intent_operation_id=detail_intent_operation_id,
        )
        if window is not None and mappings and any(
            item.get("detail_save_clicked") or item.get("action_confirm_clicked")
            for item in item_results
        ):
            _v5_phase_for_items(
                request,
                "POST_FAILURE_SCAN_STARTED",
                current_item,
                item_results,
            )
            try:
                result["post_failure_snapshot"] = _v5_post_failure_snapshot(
                    window,
                    request,
                    result,
                    timeout_seconds,
                    mappings,
                )
                _v5_phase_for_items(
                    request,
                    "POST_FAILURE_SCAN_COMPLETED",
                    current_item,
                    item_results,
                )
            except Exception as recovery_exc:
                result["post_failure_snapshot"] = (
                    _v5_failed_post_failure_snapshot(
                        request,
                        recovery_exc,
                    )
                )
    _v5_record_timing(
        timing_trace,
        "set_online_total",
        flow_timer,
        item_count=len(request_items),
    )
    counts, semantics = _v5_write_result_semantics(item_results)
    result.update(
        {
            "started_at": started_at,
            "ended_at": _multi_product_utc_now(),
            "items": item_results,
            "counts": counts,
            **semantics,
            "error_code": str(getattr(execution_error, "code", "") or ""),
            "error_message": str(getattr(execution_error, "message", "") or ""),
            "retryable": False,
        }
    )
    return _set_result(args, result)


def _run_set_offline_v5(args, request, result):
    flow_timer = time.perf_counter()
    started_at = _multi_product_utc_now()
    timing_trace = result.setdefault("timing_trace", [])
    request_items = list(request.get("items") or [])
    item_results = [_v5_result_item(item) for item in request_items]
    outputs_by_operation = {
        item["operation_id"]: output
        for item, output in zip(request_items, item_results)
    }
    timeout_seconds = _as_int(
        request,
        "element_timeout_seconds",
        ELEMENT_TIMEOUT_DEFAULT,
        minimum=1,
    )
    current_item = request_items[0]
    execution_error = None
    try:
        _v5_load_mapping(request)
        _v5_phase_for_items(
            request,
            "UI_STARTED",
            current_item,
            item_results,
        )
        stage_timer = time.perf_counter()
        window, launch = _get_or_open_and_prepare_window(
            str(request.get("window_title") or WINDOW_TITLE_DEFAULT),
            WINDOW_X_DEFAULT,
            WINDOW_Y_DEFAULT,
            WINDOW_WIDTH_DEFAULT,
            WINDOW_HEIGHT_DEFAULT,
            str(request.get("applet_uri") or ""),
            APPLET_LAUNCH_TIMEOUT_DEFAULT,
        )
        result["applet_launch"] = launch
        sleep(1)
        _v5_record_timing(
            timing_trace,
            "window_prepare",
            stage_timer,
        )
        stage_timer = time.perf_counter()
        _recover_login_if_needed(
            window,
            request,
            result,
            timeout_seconds,
            _get_arg(args, "_login_config", {}),
            _get_arg(args, "_credential_provider", None),
        )
        _v5_record_timing(
            timing_trace,
            "login_check",
            stage_timer,
        )
        stage_timer = time.perf_counter()
        _refresh_product_list(
            window,
            timeout_seconds,
            result,
            "BEFORE_SET_OFFLINE",
        )
        _v5_record_timing(
            timing_trace,
            "product_list_refresh",
            stage_timer,
        )
        online_scan = _v5_scan_page(
            window,
            request,
            timeout_seconds,
            page_type="online",
            targets=request_items,
            timing_trace=timing_trace,
            timing_stage="preflight_online_scan",
        )
        plans = []
        for request_item in request_items:
            current_item = request_item
            output = outputs_by_operation[request_item["operation_id"]]
            matching_rows = _v5_matching_rows(
                request,
                request_item,
                online_scan["rows"],
            )
            if len(matching_rows) > 1:
                raise SliceError(
                    "PRODUCT_IDENTITY_NOT_UNIQUE",
                    "上架中页面目标身份不唯一",
                    retryable=False,
                )
            if not matching_rows:
                output.update(
                    {
                        "operation_result": "ALREADY_APPLIED",
                        "detail_effect_state": "NOT_APPLIED",
                        "listing_effect_state": "NOT_APPLIED",
                        "readback_observed_at": online_scan["scan_completed_at"],
                    }
                )
                continue
            row = matching_rows[0]
            output["observed_price_before_action"] = str(row.get("price"))
            output["observed_inventory_before_action"] = int(
                row.get("inventory")
            )
            output["detail_effect_state"] = "NOT_APPLIED"
            plans.append({"request_item": request_item, "row": row})
        plans.sort(key=lambda item: int(item["row"]["parent_index"]))
        _v5_phase_for_items(
            request,
            "PREFLIGHT_VALIDATED",
            plans[0]["request_item"] if plans else current_item,
            item_results,
        )

        removed_online_items = 0
        for execution_ordinal, plan in enumerate(plans, start=1):
            item_timer = time.perf_counter()
            request_item = plan["request_item"]
            current_item = request_item
            output = outputs_by_operation[request_item["operation_id"]]
            row = dict(plan["row"])
            row["parent_index"] = (
                int(row["parent_index"])
                - removed_online_items * ROW_INDEX_STEP
            )
            row["position"] = int(row["position"]) - removed_online_items
            if int(row["parent_index"]) < ROW_INDEX_START:
                raise SliceError(
                    "EXECUTION_TRAJECTORY_INVALID",
                    "预扫描生成的上架中行轨迹无效",
                    retryable=False,
                )
            parent_index = int(row["parent_index"])
            _v5_prepare_row_for_click(
                window,
                request,
                timeout_seconds,
                row=row,
                page_type="online",
                click_target="set_offline_action",
            )
            _v5_record_timing(
                timing_trace,
                "item_action_visibility",
                item_timer,
                internal_sku=request_item["internal_sku"],
            )
            action_timer = time.perf_counter()
            action_button = _find_element(
                window,
                _online_row_action_selector(
                    parent_index,
                    ONLINE_SET_OFFLINE_BUTTON_SELECTOR,
                    "动态_index_%d_下架按钮" % parent_index,
                ),
                timeout_seconds,
            )
            action_button.click()
            expected_prompt_text = "您确定下架【%s %s】吗？" % (
                request_item["expected_grade"],
                request_item["expected_product_name"],
            )
            prompt_text = _read_text(
                window,
                _clone_dynamic_static_text_selector(
                    ONLINE_SET_OFFLINE_PROMPT_SELECTOR,
                    "动态_下架确认弹窗_提示文本",
                    expected_prompt_text,
                ),
                timeout_seconds,
            )
            _assert_set_offline_confirmation_identity(
                prompt_text,
                request_item["expected_product_name"],
                request_item["expected_grade"],
            )
            confirm_button = _find_element(
                window,
                ONLINE_SET_OFFLINE_CONFIRM_SELECTOR,
                timeout_seconds,
            )
            click_fence = None
            click_boundary_crossed = False
            try:
                try:
                    click_fence = _v5_open_emergency_click_fence(
                        request,
                        request_item,
                    )
                except EmergencyOfflineFenceError as exc:
                    try:
                        _find_element(
                            window,
                            ONLINE_SET_OFFLINE_CANCEL_SELECTOR,
                            timeout_seconds,
                        ).click()
                    except Exception:
                        pass
                    raise SliceError(
                        "EMERGENCY_AUTHORIZATION_REVOKED",
                        str(exc),
                        retryable=False,
                    ) from exc
                _v5_phase_for_items(
                    request,
                    "ACTION_INTENT_RECORDED",
                    request_item,
                    item_results,
                )
                click_boundary_crossed = True
                confirm_button.click()
            finally:
                _v5_close_emergency_click_fence(
                    click_fence,
                    crossed_click_boundary=click_boundary_crossed,
                )
            output["action_confirm_clicked"] = True
            output["action_clicked_at"] = _multi_product_utc_now()
            output["listing_effect_state"] = "UNKNOWN"
            _v5_phase_for_items(
                request,
                "ACTION_CLICKED",
                request_item,
                item_results,
                clicked_at=output["action_clicked_at"],
            )
            _v5_raise_controlled_action_unknown(
                request,
                execution_ordinal,
            )
            _v5_wait_row_identity_changed(
                window,
                parent_index,
                timeout_seconds,
                page_type="online",
                expected_name=request_item["expected_product_name"],
                expected_grade=request_item["expected_grade"],
            )
            output.update(
                {
                    "operation_result": "VERIFIED",
                    "listing_effect_state": "VERIFIED",
                    "actual_price": output["observed_price_before_action"],
                    "actual_inventory": output[
                        "observed_inventory_before_action"
                    ],
                    "readback_observed_at": _multi_product_utc_now(),
                }
            )
            _v5_phase_for_items(
                request,
                "FINAL_VERIFICATION",
                request_item,
                item_results,
            )
            removed_online_items += 1
            _v5_record_timing(
                timing_trace,
                "item_action_confirm",
                action_timer,
                internal_sku=request_item["internal_sku"],
            )

        final_online_scan = online_scan
        if plans:
            final_online_scan = _v5_scan_page(
                window,
                request,
                timeout_seconds,
                page_type="online",
                targets=request_items,
                timing_trace=timing_trace,
                timing_stage="final_online_scan",
            )
        for plan in plans:
            request_item = plan["request_item"]
            current_item = request_item
            output = outputs_by_operation[request_item["operation_id"]]
            post_rows = _v5_matching_rows(
                request,
                request_item,
                final_online_scan["rows"],
            )
            if post_rows:
                raise SliceError(
                    "POSTCHECK_STILL_ONLINE",
                    "最终确认后目标仍存在于上架中",
                    retryable=False,
                )
            output.update(
                {
                    "operation_result": "VERIFIED",
                    "listing_effect_state": "VERIFIED",
                    "actual_price": output["observed_price_before_action"],
                    "actual_inventory": output[
                        "observed_inventory_before_action"
                    ],
                    "readback_observed_at": final_online_scan[
                        "scan_completed_at"
                    ],
                }
            )
        _v5_phase_for_items(
            request,
            "FINAL_VERIFICATION",
            current_item,
            item_results,
        )
    except SliceError as exc:
        execution_error = exc
        _v5_classify_interrupted_items(
            item_results,
            current_operation_id=current_item.get("operation_id"),
            error_code=exc.code,
            error_message=exc.message,
        )
    _v5_record_timing(
        timing_trace,
        "set_offline_total",
        flow_timer,
        item_count=len(request_items),
    )
    counts, semantics = _v5_write_result_semantics(item_results)
    result.update(
        {
            "started_at": started_at,
            "ended_at": _multi_product_utc_now(),
            "items": item_results,
            "counts": counts,
            **semantics,
            "error_code": str(getattr(execution_error, "code", "") or ""),
            "error_message": str(getattr(execution_error, "message", "") or ""),
            "retryable": False,
        }
    )
    return _set_result(args, result)


def _clone_order_row_selector(base_name, inferred_name, target_index):
    """Clone one captured order field without retaining captured business text."""

    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = inferred_name
    value["screenshot"] = ""
    selected_nodes = [
        node for node in value["path"] if node.get("selected") is True
    ]
    indexed_views = [
        node
        for node in selected_nodes
        if node.get("name") == "wx-view"
        and any(
            attribute.get("name") == "index"
            for attribute in node.get("attributes", [])
        )
    ]
    if not indexed_views:
        raise SliceError(
            "ORDER_SELECTOR_BUILD_FAILED",
            "订单字段模板缺少可替换的卡片索引",
            retryable=False,
        )
    _set_path_attribute(indexed_views[-1], "index", int(target_index))
    static_nodes = [
        node for node in value["path"] if node.get("name") == "StaticText"
    ]
    if not static_nodes:
        raise SliceError(
            "ORDER_SELECTOR_BUILD_FAILED",
            "订单字段模板缺少 StaticText",
            retryable=False,
        )
    target_node = static_nodes[-1]
    target_node["selected"] = True
    target_node["attributes"] = [
        attribute
        for attribute in target_node.get("attributes", [])
        if attribute.get("name")
        not in {"acc-name", "explicit-name", "name-from", "value"}
    ]
    _set_path_attribute(target_node, "role", "StaticText")
    return Selector(value)


def _order_row_field_selector(row_ordinal, field):
    base_index = {
        "grade": 2,
        "platform_product_name": 3,
        "order_qty": 5,
        "unit_price": 6,
        "order_created_at": 7,
    }.get(field)
    if base_index is None:
        raise SliceError(
            "ORDER_SELECTOR_BUILD_FAILED",
            "不支持的订单字段",
            retryable=False,
        )
    return _clone_order_row_selector(
        ORDER_ROW_SELECTOR_TEMPLATES[field],
        "动态_订单_%d_%s" % (int(row_ordinal), field),
        base_index + ORDER_ROW_INDEX_STEP * (int(row_ordinal) - 1),
    )


def _order_row_anchor_collection_selector():
    """Enumerate only the safe grade anchors, without reading whole cards."""

    base = package.selector(ORDER_ROW_SELECTOR_TEMPLATES["grade"])
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = "动态_订单_等级锚点集合"
    value["screenshot"] = ""
    selected_nodes = [
        node for node in value["path"] if node.get("selected") is True
    ]
    indexed_views = [
        node
        for node in selected_nodes
        if node.get("name") == "wx-view"
        and any(
            attribute.get("name") == "index"
            for attribute in node.get("attributes", [])
        )
    ]
    if not indexed_views:
        raise SliceError(
            "ORDER_SELECTOR_BUILD_FAILED",
            "订单等级模板缺少可移除的卡片索引",
            retryable=False,
        )
    anchor_view = indexed_views[-1]
    anchor_view["attributes"] = [
        attribute
        for attribute in anchor_view.get("attributes", [])
        if attribute.get("name") not in {"index", "acc-name", "value"}
    ]
    static_nodes = [
        node for node in value["path"] if node.get("name") == "StaticText"
    ]
    if not static_nodes:
        raise SliceError(
            "ORDER_SELECTOR_BUILD_FAILED",
            "订单等级模板缺少 StaticText",
            retryable=False,
        )
    target_node = static_nodes[-1]
    target_node["selected"] = True
    target_node["attributes"] = [
        attribute
        for attribute in target_node.get("attributes", [])
        if attribute.get("name")
        not in {"acc-name", "explicit-name", "name-from", "value"}
    ]
    _set_path_attribute(target_node, "role", "StaticText")
    return Selector(value)


def _order_picker_value_selector(column_name, expected_text):
    return _exact_acc_label_selector(
        str(expected_text),
        "动态_订单日期_%s_%s" % (
            column_name,
            str(expected_text),
        ),
    )


def _order_page_static_text_selector():
    """Enumerate StaticText from the order Page-Frame, not the product list."""

    base = package.selector(ORDER_ROW_SELECTOR_TEMPLATES["grade"])
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = "动态_订单页面文本集合"
    value["screenshot"] = ""
    static_nodes = [
        node for node in value["path"] if node.get("name") == "StaticText"
    ]
    document_positions = [
        position
        for position, node in enumerate(value["path"])
        if node.get("name") == "Document"
    ]
    if not static_nodes or not document_positions:
        raise SliceError(
            "ORDER_SELECTOR_BUILD_FAILED",
            "订单字段模板缺少 Page-Frame 或 StaticText",
            retryable=False,
        )
    target_node = copy.deepcopy(static_nodes[-1])
    value["path"] = value["path"][: document_positions[-1] + 1]
    target_node["selected"] = True
    target_node["attributes"] = [
        attribute
        for attribute in target_node.get("attributes", [])
        if attribute.get("name")
        not in {"acc-name", "explicit-name", "name-from", "value"}
    ]
    _set_path_attribute(target_node, "role", "StaticText")
    value["path"].append(target_node)
    return Selector(value)


def _order_page_state_labels(window):
    try:
        elements = window.find_all(
            _order_page_static_text_selector(),
            timeout=1,
        )
    except Exception:
        elements = []
    labels = []
    for element in elements:
        labels.extend(_element_label(element))
    return labels


def _order_click_element(element):
    try:
        element.click()
    except Exception:
        element.parent().click()


def _order_element_visible_in_container(container, element):
    container_bounds = _bounding_dict(container)
    element_bounds = _bounding_dict(element)
    element_center_x = element_bounds["x"] + element_bounds["width"] / 2.0
    element_center_y = element_bounds["y"] + element_bounds["height"] / 2.0
    return (
        element_bounds["width"] > 0
        and element_bounds["height"] > 0
        and container_bounds["x"] <= element_center_x
        <= container_bounds["x"] + container_bounds["width"]
        and container_bounds["y"] <= element_center_y
        <= container_bounds["y"] + container_bounds["height"]
    )


def _order_selected_date_from_labels(window):
    labels = _collect_ui_state_labels(window)
    labels.extend(_order_page_state_labels(window))
    for label in labels:
        match = re.fullmatch(
            r"\s*(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*",
            str(label or ""),
        )
        if not match:
            continue
        try:
            return "%04d-%02d-%02d" % tuple(
                int(part) for part in match.groups()
            )
        except ValueError:
            continue
    return ""


def _find_order_scroll_container(window, timeout_seconds):
    base = package.selector(ORDER_LIST_CONTAINER_SELECTOR)
    value = copy.deepcopy(base.__dict__["value"])
    _remove_dynamic_page_id_constraints(value)
    value["id"] = str(uuid.uuid4())
    value["name"] = "动态_订单列表容器"
    value["screenshot"] = ""
    return _find_element(window, Selector(value), timeout_seconds)


def _order_focus_list_container_corner(window, timeout_seconds):
    """Focus the order scroller through its captured right-edge gutter."""

    container = _find_order_scroll_container(window, timeout_seconds)
    bounding = _bounding_dict(container)
    if bounding["width"] < 24 or bounding["height"] < 24:
        raise SliceError(
            "ORDER_SCROLL_CONTAINER_INVALID",
            "订单列表容器边界过小，无法安全点击角点",
            retryable=True,
        )
    inset = max(4.0, min(8.0, bounding["width"] * 0.01))
    cursor_x = int(bounding["x"] + bounding["width"] - inset)
    cursor_y = int(bounding["y"] + (bounding["height"] / 2.0))
    try:
        window.activate()
        window.wait_active(timeout=min(float(timeout_seconds), 3.0))
        win32.mouse_move(
            cursor_x,
            cursor_y,
            "screen",
            "instant",
            0.1,
        )
        win32.mouse_click("left", "click", False, "none", 0.1)
    except Exception as exc:
        raise SliceError(
            "ORDER_SCROLL_FOCUS_FAILED",
            "订单列表容器右边缘聚焦失败: " + type(exc).__name__,
            retryable=True,
        ) from exc


def _order_click_visible_date(window, selected_date, timeout_seconds):
    candidates = (
        selected_date,
        selected_date.replace("-", "/"),
        "%s年%d月%d日"
        % (
            selected_date[:4],
            int(selected_date[5:7]),
            int(selected_date[8:10]),
        ),
    )
    for candidate in candidates:
        try:
            target = _find_element(
                window,
                _exact_acc_label_selector(
                    candidate,
                    "dynamic_order_current_date",
                ),
                min(float(timeout_seconds), 2.0),
            )
            _order_click_element(target)
            sleep(0.5)
            return
        except SliceError:
            continue
    raise SliceError(
        "ORDER_DATE_SELECTOR_NOT_FOUND",
        "未找到订单日期入口",
        retryable=True,
    )


def _order_select_picker_value(
    window,
    column_name,
    candidates,
    timeout_seconds,
    max_scrolls,
    scroll_direction,
):
    container = _find_element(
        window,
        ORDER_DATE_PICKER_SELECTORS[column_name],
        timeout_seconds,
    )
    if scroll_direction not in {"up", "down"}:
        raise SliceError(
            "ORDER_DATE_SCROLL_DIRECTION_INVALID",
            "订单日期滚动方向无效",
            retryable=False,
        )
    for _ in range(max(1, int(max_scrolls))):
        hidden_target_found = False
        for candidate in candidates:
            try:
                target = _find_element(
                    window,
                    _order_picker_value_selector(
                        column_name,
                        candidate,
                    ),
                    0.4,
                )
                if not _order_element_visible_in_container(
                    container,
                    target,
                ):
                    hidden_target_found = True
                    break
                _order_click_element(target)
                sleep(0.3)
                return
            except SliceError:
                continue
        if hidden_target_found:
            sleep(0.1)
        try:
            bounding = _bounding_dict(container)
            win32.mouse_move(
                int(bounding["x"] + bounding["width"] / 2.0),
                int(bounding["y"] + bounding["height"] / 2.0),
                "screen",
                "instant",
                0.1,
            )
            # The platform exposes only the selected day and about two
            # neighbours on either side. One wheel notch advances roughly
            # two to three days, so search again after every bounded notch.
            win32.mouse_wheel(scroll_direction, 1, "none", 0.1)
            sleep(0.3)
        except Exception:
            break
    raise SliceError(
        "ORDER_DATE_VALUE_NOT_FOUND",
        "目标订单日期不在有界日期选择范围内",
        retryable=False,
    )


def _order_find_picker_action(window, action, timeout_seconds):
    labels = {"confirm": "确认", "cancel": "取消"}
    label = labels.get(action)
    if not label:
        raise SliceError(
            "ORDER_DATE_PICKER_ACTION_INVALID",
            "订单日期选择器动作无效",
            retryable=False,
        )
    try:
        button, _, _ = _find_button_by_exact_label(
            window,
            (label,),
            min(float(timeout_seconds), 3.0),
        )
        return button
    except SliceError:
        pass
    try:
        return _find_element(
            window,
            _exact_acc_label_selector(
                label,
                "dynamic_order_picker_%s" % action,
            ),
            timeout_seconds,
        )
    except SliceError:
        return _find_element(
            window,
            ORDER_DATE_PICKER_SELECTORS[action],
            timeout_seconds,
        )


def _order_select_trade_date(
    window,
    requested_date,
    timeout_seconds,
    max_scrolls,
):
    selected_date = _order_selected_date_from_labels(window)
    if selected_date == requested_date:
        return selected_date
    if not selected_date:
        raise SliceError(
            "ORDER_DATE_NOT_READABLE",
            "订单页面未显示可验证的当前选择日期",
            retryable=True,
        )
    _order_click_visible_date(window, selected_date, timeout_seconds)
    scroll_direction = (
        "up" if requested_date < selected_date else "down"
    )
    year = int(requested_date[:4])
    month = int(requested_date[5:7])
    day = int(requested_date[8:10])
    try:
        _order_select_picker_value(
            window,
            "year",
            ("%d年" % year, str(year)),
            timeout_seconds,
            max_scrolls,
            scroll_direction,
        )
        _order_select_picker_value(
            window,
            "month",
            ("%d月" % month, "%02d月" % month, str(month), "%02d" % month),
            timeout_seconds,
            max_scrolls,
            scroll_direction,
        )
        _order_select_picker_value(
            window,
            "day",
            ("%d日" % day, "%02d日" % day, str(day), "%02d" % day),
            timeout_seconds,
            max_scrolls,
            scroll_direction,
        )
        confirm = _order_find_picker_action(
            window,
            "confirm",
            timeout_seconds,
        )
        _order_click_element(confirm)
        sleep(1)
    except Exception:
        try:
            cancel = _order_find_picker_action(
                window,
                "cancel",
                1,
            )
            _order_click_element(cancel)
        except Exception:
            pass
        raise
    verified = _order_selected_date_from_labels(window)
    if verified != requested_date:
        raise SliceError(
            "ORDER_DATE_MISMATCH",
            "订单页面选择日期与请求日期不一致",
            retryable=False,
        )
    return verified


def _order_marker_visible(window, label, timeout_seconds):
    try:
        _find_element(
            window,
            _exact_acc_label_selector(
                label,
                "dynamic_order_marker",
            ),
            min(float(timeout_seconds), 1.0),
        )
        return True
    except SliceError:
        return False


def _order_list_empty_marker_visible(window, timeout_seconds):
    return _order_marker_visible(
        window,
        ORDER_LIST_EMPTY_LABEL,
        timeout_seconds,
    )


def _order_list_end_marker_visible(window, timeout_seconds):
    return _order_marker_visible(
        window,
        ORDER_LIST_END_LABEL,
        timeout_seconds,
    )


def _order_top_row_state(window, timeout_seconds):
    grade = _read_text(
        window,
        _order_row_field_selector(1, "grade"),
        min(float(timeout_seconds), 2.0),
    )
    product_name = _read_text(
        window,
        _order_row_field_selector(1, "platform_product_name"),
        min(float(timeout_seconds), 2.0),
    )
    if not str(grade or "").strip() or not str(product_name or "").strip():
        return None
    return {
        "grade": str(grade).strip(),
        "platform_product_name": str(product_name).strip(),
    }


def _order_normalize_qty(value):
    match = re.search(r"(?<!\d)(\d+)(?:\s*扎)?", str(value or ""))
    if not match or int(match.group(1)) <= 0:
        raise SliceError(
            "ORDER_QTY_PARSE_FAILED",
            "订单数量字段无法解析",
            retryable=False,
        )
    return str(int(match.group(1)))


def _order_normalize_amount(value):
    numbers = re.findall(r"\d+(?:\.\d{1,2})?", str(value or "").replace(",", ""))
    if not numbers:
        raise SliceError(
            "ORDER_AMOUNT_PARSE_FAILED",
            "订单成交金额字段无法解析",
            retryable=False,
        )
    try:
        amount = Decimal(numbers[-1])
    except InvalidOperation:
        raise SliceError(
            "ORDER_AMOUNT_PARSE_FAILED",
            "订单成交金额字段无法解析",
            retryable=False,
        )
    if amount < 0:
        raise SliceError(
            "ORDER_AMOUNT_PARSE_FAILED",
            "订单成交金额不能为负数",
            retryable=False,
        )
    return format(amount, "f")


def _order_calculate_transaction_amount(unit_price, qty):
    return format(Decimal(str(unit_price)) * Decimal(str(qty)), "f")


def _order_normalize_created_at(value):
    match = re.search(
        r"(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
        str(value or ""),
    )
    if not match:
        raise SliceError(
            "ORDER_CREATED_AT_PARSE_FAILED",
            "订单下单时间必须精确到秒",
            retryable=False,
        )
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise SliceError(
            "ORDER_CREATED_AT_PARSE_FAILED",
            "订单下单时间无效",
            retryable=False,
        )
    return match.group(1)


def _order_scoped_element_text(element, max_depth=2):
    """Read text only inside one allow-listed field subtree."""

    for getter_name in ("get_text", "get_value"):
        try:
            value = getattr(element, getter_name)()
        except Exception:
            value = ""
        if str(value or "").strip():
            return str(value).strip()
    if int(max_depth) <= 0:
        return ""
    try:
        children = list(element.children() or [])
    except Exception:
        children = []
    for child in children:
        value = _order_scoped_element_text(child, int(max_depth) - 1)
        if value:
            return value
    return ""


def _order_indexed_children_from_grade_anchor(grade_anchor):
    try:
        grade_view = grade_anchor.parent()
        indexed_container = grade_view.parent()
        children = list(indexed_container.children() or [])
    except Exception as exc:
        raise SliceError(
            "ORDER_LIST_STRUCTURE_MISMATCH",
            "订单等级锚点无法解析到列表索引子元素: " + type(exc).__name__,
            retryable=True,
        ) from exc
    return children


def _order_read_rows(window, timeout_seconds, max_rows):
    field_indexes = {
        "grade": 2,
        "platform_product_name": 3,
        "order_qty": 5,
        "unit_price": 6,
        "order_created_at": 7,
    }
    try:
        grade_anchors = list(
            window.find_all(
                _order_row_anchor_collection_selector(),
                timeout=timeout_seconds,
            )
            or []
        )
    except Exception as exc:
        raise SliceError(
            "ORDER_ROW_COLLECTION_FAILED",
            "订单等级锚点集合读取失败: " + type(exc).__name__,
            retryable=True,
        ) from exc
    if not grade_anchors:
        raise SliceError(
            "ORDER_ROW_COLLECTION_EMPTY",
            "订单页面非空但未读取到等级锚点集合",
            retryable=True,
        )
    if len(grade_anchors) > int(max_rows):
        raise SliceError(
            "ORDER_ROW_LIMIT_EXCEEDED",
            "订单数量超过请求允许的最大读取条数",
            retryable=False,
        )
    indexed_children = _order_indexed_children_from_grade_anchor(
        grade_anchors[0]
    )
    required_last_index = (
        field_indexes["order_created_at"]
        + ORDER_ROW_INDEX_STEP * (len(grade_anchors) - 1)
    )
    if len(indexed_children) <= required_last_index:
        raise SliceError(
            "ORDER_LIST_STRUCTURE_MISMATCH",
            "订单列表子元素数量不足，无法按冻结 index 和步长读取",
            retryable=True,
        )
    rows = []
    for ordinal, grade_anchor in enumerate(grade_anchors, start=1):
        def read_field(field, normalizer=None, expected_anchor=None):
            try:
                target_index = (
                    field_indexes[field]
                    + ORDER_ROW_INDEX_STEP * (ordinal - 1)
                )
                value = _order_scoped_element_text(
                    indexed_children[target_index],
                )
                if not value:
                    raise SliceError(
                        "ORDER_CARD_FIELD_EMPTY",
                        "订单卡片字段为空: " + field,
                        retryable=True,
                    )
                if expected_anchor is not None:
                    anchor_value = _order_scoped_element_text(expected_anchor)
                    if value != anchor_value:
                        raise SliceError(
                            "ORDER_CARD_INDEX_MISMATCH",
                            "订单等级锚点与列表冻结 index 不一致",
                            retryable=True,
                        )
                return normalizer(value) if normalizer is not None else value
            except SliceError as exc:
                raise SliceError(
                    exc.code,
                    "第%d条订单字段索引关联校验失败: %s" % (
                        ordinal,
                        field,
                    ),
                    retryable=exc.retryable,
                )

        grade = read_field("grade", expected_anchor=grade_anchor)
        product_name = read_field("platform_product_name")
        qty = read_field("order_qty", _order_normalize_qty)
        unit_price = read_field("unit_price", _order_normalize_amount)
        amount = _order_calculate_transaction_amount(unit_price, qty)
        created_at = read_field(
            "order_created_at",
            _order_normalize_created_at,
        )
        grade = str(grade or "").strip()
        product_name = str(product_name or "").strip()
        if not grade or not product_name:
            raise SliceError(
                "ORDER_IDENTITY_PARSE_FAILED",
                "订单卡片缺少品种或等级",
                retryable=False,
            )
        if len(grade) > 200 or len(product_name) > 200:
            raise SliceError(
                "ORDER_IDENTITY_PARSE_FAILED",
                "订单卡片品种或等级超过长度限制",
                retryable=False,
            )
        rows.append(
            {
                "order_created_at": created_at,
                "platform_product_name": product_name,
                "grade": grade,
                "order_qty": qty,
                "order_transaction_amount": amount,
                "observed_at": _now_iso(),
            }
        )
    return rows


def _order_failed_capture(
    scan_started_at,
    selected_date,
    rows,
    page_count,
    scroll_count,
    scroll_progress_verified,
    error_code,
    error_message,
):
    return {
        "selected_platform_trade_date": selected_date or None,
        "scan_started_at": scan_started_at,
        "scan_completed_at": _now_iso(),
        "loading_completed": bool(
            selected_date and (rows or int(page_count) > 0)
        ),
        "scroll_completed": False,
        "no_more_marker_visible": False,
        "trusted_empty_marker_visible": False,
        "page_count": max(0, int(page_count)),
        "scroll_count": max(0, int(scroll_count)),
        "scroll_progress_verified": bool(scroll_progress_verified),
        "rows": list(rows),
        "unavailable_code": "",
        "failure_code": str(error_code or "ORDER_SCAN_FAILED"),
        "failure_message": str(error_message or "订单只读扫描失败")[:512],
    }


def _run_order_scan_v6(args, request, result):
    scan_started_at = _now_iso()
    selected_date = ""
    rows = []
    page_count = 0
    scroll_count = 0
    scroll_progress_verified = False
    result.update(
        {
            "schema_version": ORDER_SCAN_RESULT_SCHEMA_VERSION,
            "contract_version": ORDER_SCAN_CONTRACT_VERSION,
            "execution_attempt_id": str(
                request.get("execution_attempt_id") or ""
            ),
            "execution_mode": "READ_ONLY",
            "automation_run_id": str(
                request.get("automation_run_id") or ""
            ),
            "observation_batch_id": str(
                request.get("observation_batch_id") or ""
            ),
            "platform_name": str(request.get("platform_name") or ""),
            "requested_platform_trade_date": str(
                request.get("requested_platform_trade_date") or ""
            ),
            "instruction_hash": str(
                request.get("instruction_hash") or ""
            ),
            "status": "FAILED",
            "run_success_flag": False,
            "business_operation_completed": False,
            "side_effect_state": "NOT_STARTED",
            "current_step": "VALIDATE_ORDER_SCAN",
            "capture": _order_failed_capture(
                scan_started_at,
                "",
                (),
                0,
                0,
                False,
                "ORDER_SCAN_NOT_STARTED",
                "订单只读扫描尚未开始",
            ),
            "error_code": "",
            "error_message": "",
            "retryable": False,
            "started_at": scan_started_at,
            "ended_at": "",
        }
    )
    try:
        normalized = normalize_order_scan_request(request)
        timeout_seconds = int(
            normalized["element_timeout_seconds"]
        )
        max_rows = int(normalized["limits"]["max_rows"])
        max_scrolls = int(normalized["limits"]["max_scrolls"])
        max_seconds = int(normalized["limits"]["max_seconds"])
        requested_date = normalized["requested_platform_trade_date"]
        _write_phase(request, result, "UI_STARTED")
        result["current_step"] = "OPEN_ORDER_MANAGEMENT"
        window, launch = _get_or_open_and_prepare_window(
            normalized["window_title"],
            _as_int(request, "window_x", WINDOW_X_DEFAULT),
            _as_int(request, "window_y", WINDOW_Y_DEFAULT),
            _as_int(
                request,
                "window_width",
                WINDOW_WIDTH_DEFAULT,
                minimum=100,
            ),
            _as_int(
                request,
                "window_height",
                WINDOW_HEIGHT_DEFAULT,
                minimum=100,
            ),
            normalized["applet_uri"],
            int(normalized["applet_launch_timeout_seconds"]),
        )
        result["applet_launch"] = launch
        sleep(0.5)
        _recover_login_if_needed(
            window,
            request,
            result,
            timeout_seconds,
            _get_arg(args, "_login_config", {}),
            _get_arg(args, "_credential_provider", None),
        )
        entry = _find_element(
            window,
            ORDER_MANAGEMENT_ENTRY_SELECTOR,
            timeout_seconds,
        )
        _order_click_element(entry)
        sleep(1)
        result["current_step"] = "SELECT_ORDER_DATE"
        selected_date = _order_select_trade_date(
            window,
            requested_date,
            timeout_seconds,
            min(max_scrolls, 40),
        )
        result["current_step"] = "READ_ORDER_LIST"
        materialized = _materialize_list_with_end_and_restore(
            window,
            request,
            timeout_seconds,
            focus_action=lambda: _order_focus_list_container_corner(
                window,
                timeout_seconds,
            ),
            empty_marker_visible=_order_list_empty_marker_visible,
            end_marker_visible=_order_list_end_marker_visible,
            top_state_reader=lambda: _order_top_row_state(
                window,
                timeout_seconds,
            ),
            stop_error_code="ORDER_SCAN_STOPPED",
            stop_error_message=(
                "Worker 在订单只读扫描期间收到停止请求"
            ),
            end_error_code="ORDER_END_MARKER_NOT_VERIFIED",
            end_error_message=(
                "订单页面聚焦首项并发送 END 后仍未确认结束标记"
            ),
            top_error_code="ORDER_LIST_TOP_NOT_VERIFIED",
            top_error_message="订单页面 HOME 后首张订单卡片未恢复",
            max_end_actions=max_scrolls,
            max_end_seconds=max_seconds,
        )
        trusted_empty = bool(materialized["trusted_empty"])
        no_more = bool(materialized["end_marker_verified"])
        scroll_count = int(materialized["scroll_count"])
        scroll_progress_verified = bool(
            materialized["scroll_progress_verified"]
        )
        page_count = 1
        if not trusted_empty:
            rows = _order_read_rows(
                window,
                timeout_seconds,
                max_rows,
            )
        if trusted_empty and rows:
            raise SliceError(
                "ORDER_EMPTY_MARKER_CONFLICT",
                "可信空页标记与订单卡片同时出现",
                retryable=False,
            )
        if not trusted_empty and not rows:
            raise SliceError(
                "ORDER_ROWS_NOT_FOUND",
                "订单页面既非可信空页也没有可解析卡片",
                retryable=True,
            )
        capture = {
            "selected_platform_trade_date": selected_date,
            "scan_started_at": scan_started_at,
            "scan_completed_at": _now_iso(),
            "loading_completed": True,
            "scroll_completed": bool(trusted_empty or no_more),
            "no_more_marker_visible": bool(no_more),
            "trusted_empty_marker_visible": bool(trusted_empty),
            "page_count": page_count,
            "scroll_count": scroll_count,
            "scroll_progress_verified": scroll_progress_verified,
            "rows": rows,
            "unavailable_code": "",
            "failure_code": (
                "" if trusted_empty or no_more else "ORDER_SCROLL_INCOMPLETE"
            ),
            "failure_message": (
                ""
                if trusted_empty or no_more
                else "未在有界滚动内验证订单列表结束标记"
            ),
        }
        completed = bool(trusted_empty or no_more)
        result.update(
            {
                "status": "SUCCESS" if completed else "PARTIAL",
                "run_success_flag": completed,
                "capture": capture,
                "current_step": "ORDER_SCAN_COMPLETED",
                "error_code": (
                    "" if completed else "ORDER_SCROLL_INCOMPLETE"
                ),
                "error_message": capture["failure_message"],
                "retryable": not completed,
            }
        )
        _write_phase(request, result, "UI_READ_COMPLETED")
    except SliceError as exc:
        scroll_count = max(
            scroll_count,
            int(getattr(exc, "scroll_count", 0) or 0),
        )
        scroll_progress_verified = bool(
            getattr(
                exc,
                "scroll_progress_verified",
                scroll_progress_verified,
            )
        )
        result.update(
            {
                "capture": _order_failed_capture(
                    scan_started_at,
                    selected_date,
                    rows,
                    page_count,
                    scroll_count,
                    scroll_progress_verified,
                    exc.code,
                    exc.message,
                ),
                "error_code": exc.code,
                "error_message": exc.message,
                "retryable": bool(exc.retryable),
            }
        )
    except Exception as exc:
        result.update(
            {
                "capture": _order_failed_capture(
                    scan_started_at,
                    selected_date,
                    rows,
                    page_count,
                    scroll_count,
                    scroll_progress_verified,
                    "ORDER_SCAN_FAILED",
                    "订单只读扫描失败: " + type(exc).__name__,
                ),
                "error_code": "ORDER_SCAN_FAILED",
                "error_message": "订单只读扫描失败: "
                + type(exc).__name__,
                "retryable": True,
            }
        )
    result["ended_at"] = _now_iso()
    return _set_result(args, result)


def _run_single_product_flow(args, allow_contract_dispatch=False):
    started_at = _now_iso()
    current_step = "VALIDATE_INPUT"
    execution_mode = ""
    execution_attempt_id = "VS-" + uuid.uuid4().hex
    result = {
        "schema_version": "vertical-slice-1.5",
        "task_id": "",
        "execution_attempt_id": execution_attempt_id,
        "execution_mode": "",
        "status": "FAILED",
        "run_success_flag": False,
        "business_operation_completed": False,
        "current_step": current_step,
        "window_title": "",
        "product_keyword": "",
        "product_name": "",
        "grade": "",
        "spec": "",
        "spec_available": False,
        "old_price": "",
        "expected_old_price": "",
        "target_price": "",
        "actual_price": "",
        "applet_launch": {},
        "product_list_refreshes": [],
        "side_effect_state": "NOT_STARTED",
        "selector_model": {
            "row_index_start": ROW_INDEX_START,
            "row_index_step": ROW_INDEX_STEP,
            "waiting_row_index_step": WAITING_ROW_INDEX_STEP,
            "price_index_offset": PRICE_INDEX_OFFSET,
            "inventory_index_offset": INVENTORY_INDEX_OFFSET,
            "inventory_text_index": INVENTORY_TEXT_INDEX,
            "parent_class": "van-checkbox-group",
        },
        "error_code": "",
        "error_message": "",
        "retryable": False,
        "evidence": [],
        "evidence_status": "NONE",
        "started_at": started_at,
        "ended_at": "",
    }

    try:
        request = _request_payload(args)
        if (
            allow_contract_dispatch
            and request.get("contract_version")
            == ORDER_SCAN_CONTRACT_VERSION
        ):
            return _run_order_scan_v6(args, request, result)
        if allow_contract_dispatch and request.get("contract_version") == 5:
            action_type = str(request.get("action_type") or "").strip().lower()
            if (
                str(request.get("execution_mode") or "").strip().upper()
                == "RECONCILE"
            ):
                result.update(
                    {
                        "execution_attempt_id": str(
                            request.get("execution_attempt_id") or ""
                        ),
                        "execution_mode": "RECONCILE",
                    }
                )
                return _run_listing_action_reconcile_v5(
                    args,
                    request,
                    result,
                )
            if action_type == "set_online":
                result.update(
                    {
                        "execution_attempt_id": str(
                            request.get("execution_attempt_id") or ""
                        ),
                        "execution_mode": "COMMIT",
                    }
                )
                return _run_set_online_v5(args, request, result)
            if action_type == "set_offline":
                result.update(
                    {
                        "execution_attempt_id": str(
                            request.get("execution_attempt_id") or ""
                        ),
                        "execution_mode": "COMMIT",
                    }
                )
                return _run_set_offline_v5(args, request, result)
            if action_type != "sync_status":
                raise SliceError(
                    "LISTING_ACTION_NOT_IMPLEMENTED",
                    "当前 Worker 尚未实现 v5 写动作: " + action_type,
                    retryable=False,
                )
            result.update(
                {
                    "execution_attempt_id": str(
                        request.get("execution_attempt_id") or ""
                    ),
                    "execution_mode": "READ_ONLY",
                }
            )
            return _run_listing_sync_v5(args, request, result)
        task_id = _required_text(request, "task_id")
        execution_attempt_id = _required_text(request, "execution_attempt_id")
        if os.path.exists(_result_output_path(execution_attempt_id)):
            raise SliceError(
                "DUPLICATE_EXECUTION_ATTEMPT_ID",
                "execution_attempt_id 已存在，拒绝覆盖旧结果: " + execution_attempt_id,
                False,
        )
        result.update({"task_id": task_id, "execution_attempt_id": execution_attempt_id})

        if allow_contract_dispatch and request.get("contract_version") == 2:
            return _run_multi_product_read_flow(args, request, result)
        if allow_contract_dispatch and request.get("contract_version") == 4:
            return _run_commit_batch_v4(args, request, result)

        product_keyword = _required_text(request, "product_keyword")
        expected_name = _required_text(request, "expected_product_name")
        expected_grade = _required_text(request, "expected_grade")
        expected_spec = str(_get_arg(request, "expected_spec", "")).strip()
        if bool(_get_arg(request, "spec_verification_required", False)):
            raise SliceError("INPUT_INVALID", "current platform adapter cannot verify expected_spec", False)
        execution_mode = str(_get_arg(request, "execution_mode", "READ_ONLY")).strip().upper()
        if execution_mode not in ("READ_ONLY", "COMMIT", "RECONCILE"):
            raise SliceError("INPUT_INVALID", "unsupported execution_mode, expected READ_ONLY/COMMIT/RECONCILE: " + execution_mode)
        target_price = ""
        expected_old_price = ""
        if execution_mode in ("COMMIT", "RECONCILE"):
            target_price = _parse_target_price(_required_text(request, "target_price"))
            expected_old_price = _parse_expected_old_price(
                _required_text(request, "expected_old_price")
            )

        commit_batch_id = str(_get_arg(request, "commit_batch_id", "")).strip()
        commit_batch_ordinal = 0
        commit_batch_size = 0
        page_position_hint = 0
        if commit_batch_id:
            if execution_mode != "COMMIT":
                raise SliceError(
                    "INPUT_INVALID",
                    "commit_batch_id 仅允许用于 COMMIT",
                    retryable=False,
                )
            commit_batch_ordinal = _as_int(
                request, "commit_batch_ordinal", 0, minimum=1
            )
            commit_batch_size = _as_int(request, "commit_batch_size", 0, minimum=1)
            page_position_hint = _as_int(request, "page_position_hint", 0, minimum=1)
            if commit_batch_ordinal > commit_batch_size:
                raise SliceError(
                    "INPUT_INVALID",
                    "commit_batch_ordinal 不能大于 commit_batch_size",
                    retryable=False,
                )
        reuse_product_list = _as_bool(
            _get_arg(request, "reuse_product_list", False), default=False
        )
        batch_preflight_reuse = _as_bool(
            _get_arg(request, "batch_preflight_reuse", False), default=False
        )
        prepared_window = _get_arg(args, "_prepared_window", None)
        batch_preflight_validated = bool(
            _get_arg(args, "_batch_preflight_validated", False)
        )
        if batch_preflight_reuse and (
            prepared_window is None or not batch_preflight_validated
        ):
            raise SliceError(
                "INPUT_INVALID",
                "batch_preflight_reuse requires the validated batch window",
                retryable=False,
            )
        final_save_required = _as_bool(
            _get_arg(request, "final_save_required", False), default=False
        )
        fast_post_submit_verify = _as_bool(
            _get_arg(request, "fast_post_submit_verify", False), default=False
        )

        fault_injection = str(_get_arg(request, "fault_injection", "")).strip().upper()
        capture_evidence = _as_bool(_get_arg(request, "capture_evidence", True), default=True)
        login_config = _get_arg(args, "_login_config", {})
        credential_provider = _get_arg(args, "_credential_provider", None)
        window_title = str(_get_arg(request, "window_title", WINDOW_TITLE_DEFAULT)).strip()
        applet_uri = str(_get_arg(request, "applet_uri", "")).strip()
        timeout_seconds = _as_int(
            request, "element_timeout_seconds", ELEMENT_TIMEOUT_DEFAULT, minimum=1
        )
        applet_launch_timeout_seconds = _as_int(
            request,
            "applet_launch_timeout_seconds",
            APPLET_LAUNCH_TIMEOUT_DEFAULT,
            minimum=1,
        )
        max_product_rows = _as_int(
            request, "max_product_rows", DEFAULT_MAX_PRODUCT_ROWS, minimum=1
        )
        window_x = _as_int(request, "window_x", WINDOW_X_DEFAULT)
        window_y = _as_int(request, "window_y", WINDOW_Y_DEFAULT)
        window_width = _as_int(request, "window_width", WINDOW_WIDTH_DEFAULT, minimum=100)
        window_height = _as_int(request, "window_height", WINDOW_HEIGHT_DEFAULT, minimum=100)
        evidence_dir = str(
            _get_arg(
                request,
                "evidence_dir",
                os.path.join(os.environ.get("LOCALAPPDATA", os.getcwd()), "ShadowBot", "evidence", "vertical_slice"),
            )
        ).strip()
        evidence_share_dir = str(_get_arg(request, "evidence_share_dir", "")).strip()
        evidence_storage_uri_prefix = str(
            _get_arg(request, "evidence_storage_uri_prefix", "")
        ).strip()
        final_save_labels = _parse_label_list(
            _get_arg(request, "final_save_button_labels", ""),
            ("确定", "保存", "提交"),
        )

        result.update(
            {
                "window_title": window_title,
                "applet_uri": applet_uri,
                "product_keyword": product_keyword,
                "execution_mode": execution_mode,
                "expected_old_price": expected_old_price,
                "target_price": target_price,
                "operation_id": str(_get_arg(request, "operation_id", "")),
                "platform_name": str(_get_arg(request, "platform_name", "")),
                "platform_sku": str(_get_arg(request, "platform_sku", "")),
                "expected_spec": expected_spec,
                "instruction_hash": str(_get_arg(request, "instruction_hash", "")),
                "request_file_sha256": str(_get_arg(request, "request_file_sha256", "")),
                "lease_owner_token": str(_get_arg(request, "lease_owner_token", "")),
                "lease_version": int(_get_arg(request, "lease_version", 0) or 0),
                "worker_id": str(_get_arg(request, "worker_id", "")),
                "commit_batch_id": commit_batch_id,
                "commit_batch_ordinal": commit_batch_ordinal,
                "commit_batch_size": commit_batch_size,
                "page_position_hint": page_position_hint,
                "reuse_product_list": reuse_product_list,
                "batch_preflight_reuse": batch_preflight_reuse,
                "final_save_required": final_save_required,
                "fast_post_submit_verify": fast_post_submit_verify,
            }
        )
        _write_phase(request, result, "UI_STARTED")
        _check_stop_before_submit(request, result)

        current_step = "OPEN_APPLET"
        result["current_step"] = current_step
        if batch_preflight_reuse:
            window = prepared_window
            result["applet_launch"] = {
                "source": "BATCH_PREPARED_WINDOW",
                "uri_opened": False,
                "window_ready_at": _now_iso(),
            }
        else:
            window, applet_launch = _get_or_open_and_prepare_window(
                window_title,
                window_x,
                window_y,
                window_width,
                window_height,
                applet_uri,
                applet_launch_timeout_seconds,
            )
            result["applet_launch"] = applet_launch
            sleep(1)

        current_step = "GET_AND_PREPARE_WINDOW"
        result["current_step"] = current_step
        current_step = "CHECK_LOGIN"
        result["current_step"] = current_step
        if batch_preflight_reuse:
            result["login"] = {
                "check_path": "BATCH_PREFLIGHT_REUSED",
                "login_completed_at": _now_iso(),
            }
        else:
            _recover_login_if_needed(
                window,
                request,
                result,
                timeout_seconds,
                login_config,
                credential_provider,
            )

        current_step = "OPEN_PRODUCT_MANAGEMENT"
        result["current_step"] = current_step
        current_step = "REFRESH_PRODUCT_LIST"
        result["current_step"] = current_step
        if batch_preflight_reuse:
            now = _now_iso()
            refresh_event = {
                "stage": "BEFORE_PRICE_READ",
                "started_at": now,
                "ended_at": now,
                "refresh_entry": "BATCH_PREFLIGHT",
                "status": "SUCCESS",
                "reused": True,
                "readiness": "BATCH_PREFLIGHT_VALIDATED",
                "full_page_enumeration_skipped": True,
            }
            result.setdefault("product_list_refreshes", []).append(refresh_event)
        else:
            try:
                refresh_event = _prepare_product_list(
                    window,
                    timeout_seconds,
                    result,
                    "BEFORE_PRICE_READ",
                    reuse_requested=reuse_product_list,
                )
            except SliceError as exc:
                _raise_classified_ui_error(window)
                raise exc

        current_step = "LOCATE_PRODUCT"
        result["current_step"] = current_step
        if page_position_hint:
            row_index, list_name, list_grade, product_position = (
                _locate_product_row_at_position(
                    window,
                    expected_name,
                    expected_grade,
                    page_position_hint,
                    max_product_rows,
                    timeout_seconds,
                )
            )
        else:
            row_index, list_name, list_grade, product_position = _locate_product_row(
                window,
                expected_name,
                expected_grade,
                max_product_rows,
                timeout_seconds,
                include_position=True,
            )
        _assert_list_name(list_name, expected_name, expected_grade)
        _assert_grade_identity("商品等级", list_grade, expected_grade)
        if (
            execution_mode == "COMMIT"
            and product_position >= SINGLE_PRODUCT_SCROLL_START_POSITION
        ):
            current_step = "SCROLL_PRODUCT_INTO_VIEW"
            result["current_step"] = current_step
            original_position = product_position
            (
                row_index,
                list_name,
                list_grade,
                product_position,
                scroll_attempts,
            ) = _prepare_scrolled_product_for_click(
                window,
                expected_name,
                expected_grade,
                max_product_rows,
                timeout_seconds,
                product_position,
                page_position_hint=page_position_hint,
                window_x=window_x,
                window_y=window_y,
                window_width=window_width,
                window_height=window_height,
            )
            _assert_list_name(list_name, expected_name, expected_grade)
            _assert_grade_identity("商品等级", list_grade, expected_grade)
            result["product_scroll"] = {
                "triggered": True,
                "original_position": original_position,
                "scroll_attempts": scroll_attempts,
            }
        result.update(
            {
                "product_name": list_name,
                "grade": list_grade,
                "matched_row_index": row_index,
                "matched_product_position": product_position,
            }
        )
        refresh_event["matched_row_index"] = row_index
        refresh_event["matched_product_name"] = list_name
        refresh_event["matched_grade"] = list_grade
        refresh_event["matched_product_position"] = product_position

        current_step = "READ_OLD_PRICE"
        result["current_step"] = current_step
        actual_price = _read_row_price(window, row_index, timeout_seconds)
        old_price_observed_monotonic = time.monotonic()
        result.update(
            {
                "old_price": actual_price,
                "actual_price": actual_price,
                "observed_at": _now_iso(),
            }
        )

        if execution_mode == "COMMIT" and expected_old_price and actual_price != expected_old_price:
            raise SliceError(
                "OLD_PRICE_CHANGED",
                "expected_old_price=%s, page_old_price=%s" % (expected_old_price, actual_price),
                retryable=False,
            )
        _write_phase(request, result, "PRICE_VERIFIED")
        _check_stop_before_submit(request, result)

        if execution_mode == "READ_ONLY":
            current_step = "CAPTURE_EVIDENCE"
            result["current_step"] = current_step
            evidence_items = []
            if capture_evidence:
                evidence_items.append(
                    _capture_window(
                        window,
                        evidence_dir,
                        execution_attempt_id,
                        "READ_OLD_PRICE",
                        "supply_price",
                        evidence_share_dir,
                        evidence_storage_uri_prefix,
                        fault_injection,
                    )
                )
            result.update(
                {
                    "status": "READ_COMPLETED",
                    "run_success_flag": True,
                    "business_operation_completed": False,
                    "current_step": "COMPLETE",
                    "evidence": evidence_items,
                    "evidence_status": _summarize_evidence_status(evidence_items),
                }
            )
        elif execution_mode == "RECONCILE":
            current_step = "CAPTURE_RECONCILE_EVIDENCE"
            result["current_step"] = current_step
            evidence = _capture_window(
                window,
                evidence_dir,
                execution_attempt_id,
                "RECONCILE",
                "reconcile",
                evidence_share_dir,
                evidence_storage_uri_prefix,
                fault_injection,
            )
            result.update(_build_reconcile_update(actual_price, expected_old_price, target_price))
            result.update(
                {
                    "current_step": "COMPLETE",
                    "evidence": [evidence],
                    "evidence_status": _summarize_evidence_status([evidence]),
                }
            )
        else:
            current_step = "OPEN_PRICE_DIALOG"
            result["current_step"] = current_step
            _open_price_dialog(window, row_index, timeout_seconds)

            current_step = "VERIFY_PRICE_DIALOG"
            result["current_step"] = current_step
            dialog_context = _read_dialog_context(window, timeout_seconds)
            _assert_dialog_context(dialog_context, expected_name, expected_grade, expected_old_price)
            result.update(
                {
                    "dialog_product_name": dialog_context["product_name"],
                    "dialog_grade": dialog_context["grade"],
                    "dialog_current_price": dialog_context["current_price"],
                }
            )

            current_step = "FILL_TARGET_PRICE"
            result["current_step"] = current_step
            input_readback = _fill_target_price(window, target_price, timeout_seconds, fault_injection)
            result["input_price_readback"] = input_readback
            _write_phase(request, result, "TARGET_FILLED")

            current_step = "CAPTURE_BEFORE_SUBMIT"
            result["current_step"] = current_step
            before_evidence = None
            if capture_evidence:
                before_evidence = _capture_window(
                    window,
                    evidence_dir,
                    execution_attempt_id,
                    "BEFORE_SUBMIT",
                    "before_submit",
                    evidence_share_dir,
                    evidence_storage_uri_prefix,
                    fault_injection,
                )

            _check_inline_old_price_fresh(old_price_observed_monotonic)
            _check_stop_before_submit(request, result)
            current_step = "RECORD_SUBMIT_INTENT"
            result["current_step"] = current_step
            result["side_effect_state"] = "SUBMIT_INTENT_RECORDED"
            result["submit_intent_at"] = _now_iso()
            _write_phase(request, result, "SUBMIT_INTENT_RECORDED")

            current_step = "CONFIRM_PRICE_DIALOG"
            result["current_step"] = current_step
            _confirm_price_dialog(window, timeout_seconds)
            result["side_effect_state"] = "SUBMIT_CLICKED"
            result["submit_clicked_at"] = _now_iso()
            _write_phase(request, result, "SUBMIT_CLICKED")
            if fault_injection == "AFTER_SUBMIT_CLICK_UNKNOWN":
                raise SliceError(
                    "SUBMIT_RESULT_UNKNOWN",
                    "fault injection after submit click, before post-submit verification",
                    retryable=False,
                )

            final_save_clicked = False
            final_save_label = ""
            final_save_node = ""
            if final_save_required:
                current_step = "CLICK_FINAL_SAVE"
                result["current_step"] = current_step
                try:
                    final_save_info = _click_final_save(
                        window, timeout_seconds, final_save_labels
                    )
                    final_save_label = final_save_info.get("label", "")
                    final_save_node = final_save_info.get("node", "")
                    final_save_clicked = True
                    result["final_save_clicked_at"] = _now_iso()
                except SliceError as exc:
                    if exc.code != "FINAL_SAVE_NOT_FOUND":
                        raise
                    result["final_save_warning"] = exc.message
            else:
                result["final_save_skipped"] = True
                result["final_save_skip_reason"] = "PLATFORM_DIALOG_CONFIRM_IS_FINAL"
            result["final_save_clicked"] = final_save_clicked
            result["final_save_label"] = final_save_label
            result["final_save_node"] = final_save_node
            if final_save_clicked and fault_injection == "AFTER_FINAL_SAVE_UNKNOWN":
                raise SliceError(
                    "SUBMIT_RESULT_UNKNOWN",
                    "fault injection after final save click, before post-submit verification",
                    retryable=False,
                )

            current_step = "VERIFY_AFTER_SUBMIT"
            result["current_step"] = current_step
            after_price = ""
            after_price_read_error = ""
            if fast_post_submit_verify:
                fast_verify_started_at = _now_iso()
                after_price, after_price_read_error = _wait_after_submit_price(
                    window,
                    row_index,
                    min(timeout_seconds, FAST_POST_SUBMIT_VERIFY_SECONDS),
                    target_price,
                )
                result["fast_post_submit_verification"] = {
                    "started_at": fast_verify_started_at,
                    "ended_at": _now_iso(),
                    "actual_price": after_price,
                    "matched": after_price == target_price,
                }

            if after_price != target_price:
                if fast_post_submit_verify:
                    result.setdefault("batch_fast_path_fallbacks", []).append(
                        {
                            "stage": "AFTER_SUBMIT_VERIFY",
                            "reason": "CURRENT_LIST_TARGET_NOT_OBSERVED",
                            "at": _now_iso(),
                        }
                    )
                current_step = "REFRESH_PRODUCT_LIST"
                result["current_step"] = current_step
                refresh_event = _refresh_product_list(
                    window, timeout_seconds, result, "AFTER_SUBMIT_VERIFY"
                )
                current_step = "LOCATE_PRODUCT"
                result["current_step"] = current_step
                if page_position_hint:
                    row_index, list_name, list_grade, _ = (
                        _locate_product_row_at_position(
                            window,
                            expected_name,
                            expected_grade,
                            page_position_hint,
                            max_product_rows,
                            timeout_seconds,
                        )
                    )
                else:
                    row_index, list_name, list_grade = _locate_product_row(
                        window,
                        expected_name,
                        expected_grade,
                        max_product_rows,
                        timeout_seconds,
                    )
                _assert_list_name(list_name, expected_name, expected_grade)
                _assert_grade_identity("商品等级", list_grade, expected_grade)
                refresh_event["matched_row_index"] = row_index
                refresh_event["matched_product_name"] = list_name
                refresh_event["matched_grade"] = list_grade

                current_step = "VERIFY_AFTER_SUBMIT"
                result["current_step"] = current_step
                after_price, after_price_read_error = _wait_after_submit_price(
                    window, row_index, timeout_seconds, target_price
                )
                result["post_submit_verification_path"] = "FULL_REFRESH_FALLBACK"
            else:
                result["post_submit_verification_path"] = "CURRENT_LIST_FAST_PATH"
            result["actual_price"] = after_price
            result["readback_observed_at"] = _now_iso()
            if after_price_read_error:
                result["after_submit_read_warning"] = after_price_read_error

            after_evidence = None
            if capture_evidence:
                after_evidence = _capture_window(
                    window,
                    evidence_dir,
                    execution_attempt_id,
                    "AFTER_SUBMIT",
                    "after_submit",
                    evidence_share_dir,
                    evidence_storage_uri_prefix,
                    fault_injection,
                )
            evidence_items = [
                item for item in (before_evidence, after_evidence) if item is not None
            ]
            if not after_price:
                result.update(
                    {
                        "status": "SIDE_EFFECT_UNKNOWN",
                        "run_success_flag": None,
                        "business_operation_completed": None,
                        "side_effect_state": "UNKNOWN",
                        "error_code": "SUBMIT_RESULT_UNKNOWN",
                        "error_message": "after submit list price could not be read; read_warning="
                        + str(result.get("after_submit_read_warning", "")),
                        "retryable": False,
                    }
                )
            elif after_price == target_price:
                result.update(
                    {
                        "status": "VERIFIED",
                        "run_success_flag": True,
                        "business_operation_completed": True,
                        "side_effect_state": "VERIFIED",
                        "error_code": "",
                        "error_message": "",
                        "retryable": False,
                    }
                )
            elif after_price == expected_old_price:
                if final_save_clicked or not final_save_required:
                    result.update(
                        {
                            "status": "FAILED",
                            "run_success_flag": False,
                            "business_operation_completed": False,
                            "side_effect_state": "NOT_APPLIED",
                            "error_code": "SUBMIT_NOT_APPLIED",
                            "error_message": "after final save, list price is still expected_old_price",
                            "retryable": False,
                        }
                    )
                else:
                    result.update(
                        {
                            "status": "SIDE_EFFECT_UNKNOWN",
                            "run_success_flag": None,
                            "business_operation_completed": None,
                            "side_effect_state": "UNKNOWN",
                            "error_code": "FINAL_SAVE_NOT_FOUND",
                            "error_message": "price dialog confirmed but final save button was not found; actual price is still expected_old_price",
                            "retryable": False,
                        }
                    )
            else:
                result.update(
                    {
                        "status": "SIDE_EFFECT_UNKNOWN",
                        "run_success_flag": None,
                        "business_operation_completed": None,
                        "side_effect_state": "UNKNOWN",
                        "error_code": "POST_SUBMIT_PRICE_MISMATCH",
                        "error_message": "after submit actual_price=%s, not expected_old_price=%s and not target_price=%s"
                        % (after_price, expected_old_price, target_price),
                        "retryable": False,
                    }
                )
            result.update(
                {
                    "current_step": "COMPLETE",
                    "evidence": evidence_items,
                    "evidence_status": _summarize_evidence_status(evidence_items),
                }
            )
        if result.get("status") in ("VERIFIED", "READ_COMPLETED", "NOT_APPLIED"):
            _write_phase(request, result, "VERIFIED", include_result_snapshot=True)
    except SliceError as exc:
        if (
            execution_mode == "COMMIT"
            and current_step in ("FILL_TARGET_PRICE", "CAPTURE_BEFORE_SUBMIT")
            and not _has_submit_side_effect(result)
            and "window" in locals()
        ):
            try:
                _cancel_price_dialog(window, timeout_seconds)
                result["cleanup_action"] = "PRICE_DIALOG_CANCELLED"
            except Exception as cleanup_exc:
                result["cleanup_error"] = str(cleanup_exc)
        submit_proven_absent = (
            execution_mode == "COMMIT"
            and "window" in locals()
            and "timeout_seconds" in locals()
            and _prove_submit_intent_not_clicked(result, window, timeout_seconds)
        )
        if (
            execution_mode == "COMMIT"
            and _has_submit_side_effect(result)
            and not submit_proven_absent
        ):
            _mark_submit_result_unknown(result, current_step, exc.code, exc.message)
        elif not submit_proven_absent:
            result.update(
                {
                    "status": "FAILED",
                    "run_success_flag": False,
                    "business_operation_completed": False,
                    "current_step": current_step,
                    "error_code": exc.code,
                    "error_message": exc.message,
                    "retryable": exc.retryable,
                }
            )
    except Exception as exc:
        submit_proven_absent = (
            execution_mode == "COMMIT"
            and "window" in locals()
            and "timeout_seconds" in locals()
            and _prove_submit_intent_not_clicked(result, window, timeout_seconds)
        )
        if (
            execution_mode == "COMMIT"
            and _has_submit_side_effect(result)
            and not submit_proven_absent
        ):
            _mark_submit_result_unknown(result, current_step, "UNKNOWN_ERROR", str(exc))
        elif not submit_proven_absent:
            result.update(
                {
                    "status": "FAILED",
                    "run_success_flag": False,
                    "business_operation_completed": False,
                    "current_step": current_step,
                    "error_code": "UNKNOWN_ERROR",
                    "error_message": "unexpected runtime error: " + type(exc).__name__,
                    "retryable": False,
                }
            )

    result["ended_at"] = _now_iso()
    if "request" in locals() and result.get("status") == "SIDE_EFFECT_UNKNOWN":
        _write_phase(request, result, "SUBMIT_CLICKED")
    return _set_result(args, result)


def main(args):
    return _run_single_product_flow(args, allow_contract_dispatch=True)
