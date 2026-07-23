import copy
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

import xbot  # noqa: F401 - ShadowBot runtime import initializes host bindings.
from xbot import print, sleep, win32
from xbot.selector import Selector

from . import package

try:
    from app.shadowbot_contract_primitives import (
        contract_identity_key,
        normalize_contract_grade,
        normalize_contract_sku,
        normalize_contract_text,
        sha256_json,
    )
except ImportError:
    try:
        from .shadowbot_contract_primitives import (
            contract_identity_key,
            normalize_contract_grade,
            normalize_contract_sku,
            normalize_contract_text,
            sha256_json,
        )
    except ImportError:
        from shadowbot_contract_primitives import (
            contract_identity_key,
            normalize_contract_grade,
            normalize_contract_sku,
            normalize_contract_text,
            sha256_json,
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
INDEXED_ENUMERATION_MAX_ROWS = 50
PRICE_INDEX_OFFSET = 9
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

DIALOG_VALUE_CHILD_INDEXES = {
    ELEMENTS["dialog_product_name"]: 3,
    ELEMENTS["dialog_grade"]: 5,
    ELEMENTS["dialog_current_price"]: 7,
}
ONLINE_LIST_LABEL = "上架中"
PRODUCT_LIST_END_LABEL = "没有更多了"

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
        payload["schema_version"] = "shadowbot-commit-batch-phase-1.0"
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
            "phase": phase,
            "side_effect_state": str(
                result.get("side_effect_state") or "NOT_STARTED"
            ),
            "submit_intent_at": result.get("submit_intent_at"),
            "submit_clicked_at": result.get("submit_clicked_at"),
            "readback_observed_at": result.get("readback_observed_at"),
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


def _row_field_selector(row_parent_index, field):
    if field == "name":
        return _clone_row_selector(
            SELECTOR_TEMPLATES["name"],
            "动态_index_%d_商品名称" % row_parent_index,
            row_parent_index,
            1,
        )
    if field == "grade":
        return _clone_row_selector(
            SELECTOR_TEMPLATES["grade"],
            "动态_index_%d_商品等级" % row_parent_index,
            row_parent_index,
            0,
        )
    if field == "price":
        return _clone_row_selector(
            SELECTOR_TEMPLATES["name"],
            "动态_index_%d_商品价格" % row_parent_index,
            row_parent_index + PRICE_INDEX_OFFSET,
            0,
        )
    if field == "inventory":
        return _clone_row_value_selector(
            SELECTOR_TEMPLATES["inventory"],
            "动态_index_%d_商品库存" % row_parent_index,
            row_parent_index + INVENTORY_INDEX_OFFSET,
        )
    raise SliceError("SELECTOR_BUILD_FAILED", "不支持的行字段: " + field)



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


def _enumerate_product_rows(window, timeout_seconds, scan_state=None):
    """Probe only the deterministic product-row index sequence: 1, 17, 33..."""
    row_timeout = min(timeout_seconds, 3)
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
        parent_index = ROW_INDEX_START + ROW_INDEX_STEP * (position - 1)
        try:
            name = _strip_label(
                _read_text(
                    window,
                    _row_field_selector(parent_index, "name"),
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
                    _row_field_selector(parent_index, "grade"),
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
):
    """Read only target values and reuse hydrated rows across repeated viewports."""
    started = time.time()
    identity_started = time.time()
    identity_rows = _enumerate_product_rows(
        window,
        timeout_seconds,
        scan_state=scan_state,
    )
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
                    window, row.get("parent_index"), timeout_seconds
                )
                _multi_product_metric_add(metrics, "price_reads")
            except SliceError as exc:
                row["price_error_code"] = "PRICE_PARSE_FAILED"
                row["price_error_message"] = str(exc.message)
            try:
                row["inventory"] = _read_row_inventory(
                    window, row.get("parent_index"), timeout_seconds
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


def _advance_product_list(window, timeout_seconds, direction="down"):
    """Best-effort bounded scroll hook; never claims progress without a call."""
    try:
        container = _find_product_list_container(window, min(timeout_seconds, 3))
    except Exception:
        return False
    try:
        container.hover(True, 0.2)
        win32.mouse_wheel(direction, SINGLE_PRODUCT_SCROLL_WHEEL_TIMES, "none", 1)
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
        _find_product_list_container(window, timeout_seconds)
        sleep(0.5)
        _find_product_list_container(window, timeout_seconds)
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
    event.update({"status": "SUCCESS", "ended_at": _now_iso()})
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
        _find_product_list_container(window, timeout_seconds)
        sleep(0.2)
        _find_product_list_container(window, timeout_seconds)
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
    event.update({"status": "SUCCESS", "ended_at": _now_iso()})
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


def _open_price_dialog(window, row_index, timeout_seconds):
    price_element = _find_element(window, _row_field_selector(row_index, "price"), timeout_seconds)
    price_element.click()
    sleep(0.8)
    _find_element(window, ELEMENTS["price_popup"], timeout_seconds)


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


def _fill_target_price(window, target_price, timeout_seconds, fault_injection=""):
    input_element = _find_element(window, ELEMENTS["price_input"], timeout_seconds)
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


def _cancel_price_dialog(window, timeout_seconds):
    _find_element(window, ELEMENTS["price_cancel"], timeout_seconds).click()
    sleep(0.5)


def _confirm_price_dialog(window, timeout_seconds):
    _find_element(window, ELEMENTS["price_confirm"], timeout_seconds).click()
    sleep(1.2)


def _read_row_price(window, row_index, timeout_seconds):
    raw_price = _read_text(window, _row_field_selector(row_index, "price"), timeout_seconds)
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


def _read_row_inventory(window, row_index, timeout_seconds):
    raw_inventory = _read_text(window, _row_field_selector(row_index, "inventory"), timeout_seconds)
    return _parse_inventory(raw_inventory)


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
        if item.get("submit_attempted"):
            counts["attempted"] += 1
        counts[str(item["status"]).lower()] += 1
    return counts


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
    if counts["verified"] == counts["total"]:
        batch_status = "VERIFIED"
    elif counts["verified"]:
        batch_status = "PARTIAL"
    elif counts["unknown"]:
        batch_status = "UNKNOWN"
    else:
        batch_status = "FAILED"
    result.update(
        {
            "counts": counts,
            "batch_status": batch_status,
            "status": batch_status,
            "run_success_flag": batch_status == "VERIFIED",
            "business_operation_completed": counts["attempted"] > 0,
            "side_effect_state": "VERIFIED" if batch_status == "VERIFIED" else result["side_effect_state"],
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
