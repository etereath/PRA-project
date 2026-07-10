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

import xbot
from xbot import print, sleep, win32
from xbot.selector import Selector

from . import package


WINDOW_TITLE_DEFAULT = "蚂蚁花团供应商"
WINDOW_X_DEFAULT = 0
WINDOW_Y_DEFAULT = 0
WINDOW_WIDTH_DEFAULT = 562
WINDOW_HEIGHT_DEFAULT = 1056
ELEMENT_TIMEOUT_DEFAULT = 15

ROW_INDEX_START = 1
ROW_INDEX_STEP = 16
PRICE_INDEX_OFFSET = 9
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
}

DEFAULT_REQUEST = {
    "execution_mode": "READ_ONLY",
    "window_title": WINDOW_TITLE_DEFAULT,
    "max_product_rows": DEFAULT_MAX_PRODUCT_ROWS,
}

TZ_SHANGHAI = timezone(timedelta(hours=8))


class SliceError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _now_iso():
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


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
        "operation_id": str(_get_arg(request, "operation_id", "")),
        "task_id": str(_get_arg(request, "task_id", "")),
        "execution_attempt_id": str(_get_arg(request, "execution_attempt_id", "")),
        "execution_mode": str(_get_arg(request, "execution_mode", "")),
        "phase": phase,
        "side_effect_state": str(result.get("side_effect_state") or "NOT_STARTED"),
        "request_file_sha256": str(_get_arg(request, "request_file_sha256", "")),
        "instruction_hash": str(_get_arg(request, "instruction_hash", "")),
        "worker_id": str(_get_arg(request, "worker_id", "")),
        "cleanup_confirmed": result.get("cleanup_action") == "PRICE_DIALOG_CANCELLED",
        "updated_at": _now_iso(),
    }
    if include_result_snapshot:
        payload["result_snapshot"] = dict(result)
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


def _assert_identity(field_name, actual, expected):
    if expected and _normalize_text(actual) != _normalize_text(expected):
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


def _clone_row_selector(base_name, inferred_name, parent_index, static_text_index):
    base = package.selector(base_name)
    value = copy.deepcopy(base.__dict__["value"])
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


def _generic_product_name_selector():
    base = package.selector(SELECTOR_TEMPLATES["name"])
    value = copy.deepcopy(base.__dict__["value"])
    value["id"] = str(uuid.uuid4())
    value["name"] = "动态_全部商品名称"
    value["screenshot"] = ""

    selected_nodes = [node for node in value["path"] if node.get("selected") is True]
    indexed_wx_views = [
        node
        for node in selected_nodes
        if node.get("name") == "wx-view"
        and any(attr.get("name") == "index" for attr in node.get("attributes", []))
    ]
    if not indexed_wx_views:
        raise SliceError("SELECTOR_BUILD_FAILED", "基础名称选择器缺少 wx-view index")
    target_node = indexed_wx_views[-1]
    target_node["attributes"] = [
        attr for attr in target_node.get("attributes", []) if attr.get("name") != "index"
    ]

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
        node
        for node in value["path"]
        if node.get("selected") is True and node.get("name") == "StaticText"
    ]
    if not static_nodes:
        raise SliceError("SELECTOR_BUILD_FAILED", "基础名称选择器缺少 StaticText")
    _set_path_attribute(static_nodes[-1], "role", "StaticText")
    _set_path_attribute(static_nodes[-1], "index", 1)
    return Selector(value)


def _row_parent_index(element):
    parent_element = element.parent()
    raw_attributes = parent_element.get_all_attributes() or []
    if isinstance(raw_attributes, dict):
        attributes = raw_attributes
    else:
        attributes = {str(name): value for name, value in raw_attributes}
    if "index" in attributes:
        return int(attributes["index"])

    for candidate in (str(parent_element), str(element)):
        match = re.search(r'wx-view\[@index="(\d+)"\]', candidate)
        if match:
            return int(match.group(1))
    raise SliceError(
        "SELECTOR_BUILD_FAILED",
        "无法从商品名称父元素读取 wx-view index: %s" % str(parent_element),
    )


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
    raise SliceError("SELECTOR_BUILD_FAILED", "不支持的行字段: " + field)



def _generic_acc_node_selector(node_name, selector_name):
    base = package.selector(SELECTOR_TEMPLATES["name"])
    value = copy.deepcopy(base.__dict__["value"])
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


def _read_text(window, selector_or_name, timeout_seconds):
    element = _find_element(window, selector_or_name, timeout_seconds)
    value = element.get_text()
    if value is None or not str(value).strip():
        try:
            value = element.get_value()
        except Exception:
            pass
    return str(value or "").strip()


def _enumerate_product_rows(window, timeout_seconds):
    selector = _generic_product_name_selector()
    name_elements = window.find_all(selector, timeout=min(timeout_seconds, 3))
    rows = []
    seen_parent_indexes = set()
    for element in name_elements:
        try:
            parent_index = _row_parent_index(element)
            if parent_index in seen_parent_indexes:
                continue
            seen_parent_indexes.add(parent_index)
            name = _strip_label(str(element.get_text() or "").strip(), ("商品名称", "名称"))
            grade = _strip_label(
                _read_text(
                    window,
                    _row_field_selector(parent_index, "grade"),
                    min(timeout_seconds, 3),
                ),
                ("商品等级", "等级"),
            )
            rows.append(
                {
                    "source": "DYNAMIC",
                    "parent_index": parent_index,
                    "name": name,
                    "grade": grade,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "source": "DYNAMIC",
                    "element": str(element),
                    "error": getattr(exc, "code", type(exc).__name__),
                    "detail": str(exc),
                }
            )
    return rows


def _locate_product_row(
    window, expected_name, expected_grade, max_rows, timeout_seconds
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

    for row in dynamic_rows:
        observed.append(row)
        if row.get("error"):
            continue
        if _list_name_matches(row["name"], expected_name, expected_grade) and (
            _normalize_text(row["grade"]) == _normalize_text(expected_grade)
        ):
            matches.append((row["parent_index"], row["name"], row["grade"]))

    if matches:
        unique_matches = {item[0]: item for item in matches}
        if len(unique_matches) > 1:
            raise SliceError(
                "PRODUCT_MATCH_AMBIGUOUS",
                "名称和等级匹配到多个商品父级 index: " + str(sorted(unique_matches)),
            )
        return next(iter(unique_matches.values()))

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
            _normalize_text(grade) == _normalize_text(expected_grade)
        ):
            matches.append((parent_index, name, grade))

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
    return matches[0]


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
        "status": "NEEDS_RECONCILIATION",
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
            "status": "NEEDS_RECONCILIATION",
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


def _read_price_input(element):
    raw = _element_text_or_value(element)
    if not raw:
        return ""
    try:
        return _parse_price(raw)
    except SliceError:
        return str(raw).strip()


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
    return {
        "product_name": _strip_label(
            _read_text(window, ELEMENTS["dialog_product_name"], timeout_seconds),
            ("当前商品", "商品", "商品名称", "名称"),
        ),
        "grade": _strip_label(
            _read_optional_text(window, ELEMENTS["dialog_grade"], timeout_seconds),
            ("当前等级", "等级"),
        ),
        "current_price": _parse_optional_price(
            _read_optional_text(window, ELEMENTS["dialog_current_price"], timeout_seconds)
        ),
    }

def _assert_dialog_context(context, expected_name, expected_grade, expected_old_price):
    _assert_list_name(context["product_name"], expected_name, expected_grade)
    if context.get("grade"):
        _assert_identity("price dialog grade", context["grade"], expected_grade)
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
    result["result_path"] = result_path
    result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    with open(result_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(result_json)
    if args is not None:
        try:
            args["result_json"] = result_json
        except Exception:
            pass
    print(result_json)
    return result_json


def main(args):
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
        "side_effect_state": "NOT_STARTED",
        "selector_model": {
            "row_index_start": ROW_INDEX_START,
            "row_index_step": ROW_INDEX_STEP,
            "price_index_offset": PRICE_INDEX_OFFSET,
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

        product_keyword = _required_text(request, "product_keyword")
        expected_name = _required_text(request, "expected_product_name")
        expected_grade = _required_text(request, "expected_grade")
        expected_spec = str(_get_arg(request, "expected_spec", "")).strip()
        if bool(_get_arg(request, "spec_verification_required", False)):
            raise SliceError("INPUT_INVALID", "current platform adapter cannot verify expected_spec", False)
        execution_mode = str(_get_arg(request, "execution_mode", "READ_ONLY")).strip().upper()
        if execution_mode not in ("READ_ONLY", "FILL_PREVIEW", "COMMIT", "RECONCILE"):
            raise SliceError("INPUT_INVALID", "unsupported execution_mode, expected READ_ONLY/FILL_PREVIEW/COMMIT/RECONCILE: " + execution_mode)
        target_price = ""
        expected_old_price = ""
        if execution_mode in ("FILL_PREVIEW", "COMMIT", "RECONCILE"):
            target_price = _parse_target_price(_required_text(request, "target_price"))
            expected_old_price = _parse_expected_old_price(
                _required_text(request, "expected_old_price")
            )

        fault_injection = str(_get_arg(request, "fault_injection", "")).strip().upper()
        window_title = str(_get_arg(request, "window_title", WINDOW_TITLE_DEFAULT)).strip()
        timeout_seconds = _as_int(
            request, "element_timeout_seconds", ELEMENT_TIMEOUT_DEFAULT, minimum=1
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
                "worker_id": str(_get_arg(request, "worker_id", "")),
            }
        )
        _write_phase(request, result, "UI_STARTED")
        _check_stop_before_submit(request, result)

        current_step = "GET_AND_PREPARE_WINDOW"
        result["current_step"] = current_step
        window = _get_and_prepare_window(
            window_title,
            window_x,
            window_y,
            window_width,
            window_height,
        )
        sleep(1)

        current_step = "OPEN_PRODUCT_MANAGEMENT"
        result["current_step"] = current_step
        try:
            _find_element(window, ELEMENTS["target_container"], 2)
        except SliceError:
            try:
                product_management = _find_element(
                    window, ELEMENTS["product_management"], timeout_seconds
                )
            except SliceError as exc:
                _raise_classified_ui_error(window)
                raise exc
            product_management.click()
            sleep(1)
            try:
                _find_element(window, ELEMENTS["target_container"], timeout_seconds)
            except SliceError as exc:
                _raise_classified_ui_error(window)
                raise exc

        current_step = "LOCATE_PRODUCT"
        result["current_step"] = current_step
        row_index, list_name, list_grade = _locate_product_row(
            window, expected_name, expected_grade, max_product_rows, timeout_seconds
        )
        _assert_list_name(list_name, expected_name, expected_grade)
        _assert_identity("商品等级", list_grade, expected_grade)
        result.update(
            {
                "product_name": list_name,
                "grade": list_grade,
                "matched_row_index": row_index,
            }
        )

        current_step = "READ_OLD_PRICE"
        result["current_step"] = current_step
        actual_price = _read_row_price(window, row_index, timeout_seconds)
        result.update({"old_price": actual_price, "actual_price": actual_price})

        if execution_mode in ("FILL_PREVIEW", "COMMIT") and expected_old_price and actual_price != expected_old_price:
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
            evidence = _capture_window(
                window,
                evidence_dir,
                execution_attempt_id,
                "READ_OLD_PRICE",
                "supply_price",
                evidence_share_dir,
                evidence_storage_uri_prefix,
                fault_injection,
            )
            result.update(
                {
                    "status": "READ_COMPLETED",
                    "run_success_flag": True,
                    "business_operation_completed": False,
                    "current_step": "COMPLETE",
                    "evidence": [evidence],
                    "evidence_status": _summarize_evidence_status([evidence]),
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
            before_evidence = _capture_window(
                window,
                evidence_dir,
                execution_attempt_id,
                "BEFORE_SUBMIT" if execution_mode == "COMMIT" else "FILL_PREVIEW",
                "before_submit" if execution_mode == "COMMIT" else "fill_preview",
                evidence_share_dir,
                evidence_storage_uri_prefix,
                fault_injection,
            )

            if execution_mode == "FILL_PREVIEW":
                current_step = "CANCEL_PREVIEW"
                result["current_step"] = current_step
                _cancel_price_dialog(window, timeout_seconds)
                result.update(
                    {
                        "status": "PREVIEW_COMPLETED",
                        "run_success_flag": True,
                        "business_operation_completed": False,
                        "current_step": "COMPLETE",
                        "evidence": [before_evidence],
                        "evidence_status": _summarize_evidence_status([before_evidence]),
                    }
                )
            else:
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

                current_step = "CLICK_FINAL_SAVE"
                result["current_step"] = current_step
                final_save_clicked = False
                final_save_label = ""
                final_save_node = ""
                try:
                    final_save_info = _click_final_save(window, timeout_seconds, final_save_labels)
                    final_save_label = final_save_info.get("label", "")
                    final_save_node = final_save_info.get("node", "")
                    final_save_clicked = True
                    result["final_save_clicked_at"] = _now_iso()
                except SliceError as exc:
                    if exc.code != "FINAL_SAVE_NOT_FOUND":
                        raise
                    result["final_save_warning"] = exc.message
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
                after_price, after_price_read_error = _wait_after_submit_price(
                    window, row_index, timeout_seconds, target_price
                )
                result["actual_price"] = after_price
                if after_price_read_error:
                    result["after_submit_read_warning"] = after_price_read_error

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
                evidence_items = [before_evidence, after_evidence]
                if not after_price:
                    result.update(
                        {
                            "status": "NEEDS_RECONCILIATION",
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
                            "status": "SUCCESS",
                            "run_success_flag": True,
                            "business_operation_completed": True,
                            "side_effect_state": "VERIFIED",
                            "error_code": "",
                            "error_message": "",
                            "retryable": False,
                        }
                    )
                elif after_price == expected_old_price:
                    if final_save_clicked:
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
                                "status": "NEEDS_RECONCILIATION",
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
                            "status": "NEEDS_RECONCILIATION",
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
        if result.get("status") in ("SUCCESS", "ALREADY_APPLIED", "VERIFIED", "READ_COMPLETED", "PREVIEW_COMPLETED", "NOT_APPLIED"):
            _write_phase(request, result, "VERIFIED", include_result_snapshot=True)
    except SliceError as exc:
        if (
            execution_mode in ("FILL_PREVIEW", "COMMIT")
            and current_step in ("FILL_TARGET_PRICE", "CAPTURE_BEFORE_SUBMIT")
            and not _has_submit_side_effect(result)
            and "window" in locals()
        ):
            try:
                _cancel_price_dialog(window, timeout_seconds)
                result["cleanup_action"] = "PRICE_DIALOG_CANCELLED"
            except Exception as cleanup_exc:
                result["cleanup_error"] = str(cleanup_exc)
        if execution_mode == "COMMIT" and _has_submit_side_effect(result):
            _mark_submit_result_unknown(result, current_step, exc.code, exc.message)
        else:
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
        if execution_mode == "COMMIT" and _has_submit_side_effect(result):
            _mark_submit_result_unknown(result, current_step, "UNKNOWN_ERROR", str(exc))
        else:
            result.update(
                {
                    "status": "FAILED",
                    "run_success_flag": False,
                    "business_operation_completed": False,
                    "current_step": current_step,
                    "error_code": "UNKNOWN_ERROR",
                    "error_message": str(exc),
                    "retryable": False,
                }
            )

    result["ended_at"] = _now_iso()
    if "request" in locals() and result.get("status") == "NEEDS_RECONCILIATION":
        _write_phase(request, result, "SUBMIT_CLICKED")
    return _set_result(args, result)
