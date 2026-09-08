import ast
import copy
import re
import uuid
from pathlib import Path
from types import SimpleNamespace


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _load_login_classifier():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted_names = {
        "_normalize_text",
        "_login_required_from_labels",
        "_meaningful_ui_labels",
        "_classify_unavailable_ui",
    }
    wanted_constants = {
        "LOGIN_REQUIRED_MARKERS",
        "NETWORK_OR_LOAD_ERROR_MARKERS",
        "IGNORED_UI_CHROME_LABELS",
    }
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in wanted_names
        )
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in wanted_constants
                for target in node.targets
            )
        )
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"re": re}
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace


def _load_login_label_collector():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted_names = {
        "_normalize_text",
        "_element_attributes",
        "_element_label",
        "_login_required_from_labels",
        "_login_page_state",
        "_collect_ui_state_labels",
    }
    wanted_constants = {
        "UI_STATE_NODE_NAMES",
        "LOGIN_REQUIRED_MARKERS",
        "LOGIN_VERIFICATION_MARKERS",
        "LOGIN_CREDENTIAL_REJECTED_MARKERS",
    }
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in wanted_names
        )
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in wanted_constants
                for target in node.targets
            )
        )
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "re": re,
        "_generic_acc_node_selector": lambda node, _name: node,
    }
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace


def _load_stable_selector(source_value):
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted_names = {
        "_remove_dynamic_page_id_constraints",
        "_stable_captured_selector",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted_names
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "copy": copy,
        "re": re,
        "uuid": uuid,
        "package": SimpleNamespace(
            selector=lambda _name: SimpleNamespace(value=source_value)
        ),
        "Selector": lambda value: value,
    }
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace["_stable_captured_selector"]


def _load_login_form_finder():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_find_login_form_inputs"
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    class SliceError(Exception):
        def __init__(self, code, message, retryable=False):
            super().__init__(message)
            self.code = code
            self.message = message
            self.retryable = retryable

    namespace = {
        "SliceError": SliceError,
        "_generic_acc_node_selector": lambda node, _name: node,
        "_bounding_dict": lambda element: element.bounds,
    }
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace


class _FakeInput:
    def __init__(self, x, y, width, height, label):
        self.bounds = {"x": x, "y": y, "width": width, "height": height}
        self.label = label


class _FakeLoginWindow:
    def __init__(self, elements_by_node):
        self.elements_by_node = elements_by_node

    def find_all(self, selector, timeout):
        del timeout
        return self.elements_by_node.get(selector, [])


def test_login_page_is_classified_from_welcome_and_login_labels():
    classify = _load_login_classifier()["_login_required_from_labels"]

    required, markers = classify(["欢迎使用蚂蚁花团供应商端", "登录"])

    assert required is True
    assert markers == ["欢迎使用蚂蚁花团供应商端"]


def test_login_label_collection_stops_after_first_conclusive_text_role():
    namespace = _load_login_label_collector()
    calls: list[str] = []

    class LabelElement:
        def __init__(self, value: str) -> None:
            self.value = value

        def get_text(self) -> str:
            return self.value

        def get_value(self):
            return None

        def get_all_attributes(self):
            return []

    class Window:
        def find_all(self, selector, timeout):
            del timeout
            calls.append(selector)
            if selector == "StaticText":
                return [
                    LabelElement("欢迎使用蚂蚁花团供应商端"),
                    LabelElement("登录"),
                ]
            raise AssertionError("login was already conclusive")

    labels = namespace["_collect_ui_state_labels"](Window())

    assert calls == ["StaticText"]
    assert labels == ["欢迎使用蚂蚁花团供应商端", "登录"]


def test_login_selector_removes_dynamic_page_instance_constraints():
    source_value = {
        "id": "captured-id",
        "name": "登录页_账号输入框",
        "screenshot": "captured.png",
        "path": [
            {
                "name": "Document",
                "attributes": [
                    {"name": "id", "value": "page-103"},
                    {
                        "name": "value",
                        "value": "https://servicewechat.com/example/page-frame.html",
                    },
                    {"name": "class", "value": "page-frame"},
                ],
            },
            {
                "name": "Edit",
                "attributes": [{"name": "acc-name", "value": "请输入您的账号"}],
            },
        ],
    }

    selector = _load_stable_selector(source_value)(
        "登录页_账号输入框",
        "dynamic_login_account_input",
    )

    document_attributes = selector["path"][0]["attributes"]
    assert {attribute["name"] for attribute in document_attributes} == {"class"}
    assert selector["name"] == "dynamic_login_account_input"
    assert selector["id"] != "captured-id"
    assert selector["screenshot"] == ""
    assert source_value["path"][0]["attributes"][0]["value"] == "page-103"


def test_login_form_finder_groups_wrapper_and_focusable_input_by_visual_row():
    namespace = _load_login_form_finder()
    account_wrapper = _FakeInput(100, 200, 400, 80, "account-wrapper")
    account_input = _FakeInput(100, 201, 400, 79, "account-input")
    password_wrapper = _FakeInput(100, 320, 400, 80, "password-wrapper")
    password_input = _FakeInput(100, 321, 400, 79, "password-input")
    window = _FakeLoginWindow(
        {
            "input": [],
            "Edit": [
                account_wrapper,
                account_input,
                password_wrapper,
                password_input,
            ],
        }
    )

    fields, node_name = namespace["_find_login_form_inputs"](window, 1)

    assert node_name == "Edit"
    assert fields["ACCOUNT_INPUT"] is account_input
    assert fields["PASSWORD_INPUT"] is password_input


def test_login_form_finder_fails_closed_when_visible_input_rows_are_ambiguous():
    namespace = _load_login_form_finder()
    window = _FakeLoginWindow(
        {
            "input": [
                _FakeInput(100, 200, 400, 80, "first"),
                _FakeInput(100, 320, 400, 80, "second"),
                _FakeInput(100, 440, 400, 80, "third"),
            ]
        }
    )

    try:
        namespace["_find_login_form_inputs"](window, 1)
    except Exception as exc:
        assert exc.code == "LOGIN_FORM_STRUCTURE_UNAVAILABLE"
        assert "row_count=3" in exc.message
    else:
        raise AssertionError("expected ambiguous login form to fail closed")


def test_login_page_is_classified_from_account_and_password_placeholders():
    classify = _load_login_classifier()["_login_required_from_labels"]

    required, markers = classify(["请输入您的账号", "请输入您的密码"])

    assert required is True
    assert markers == ["请输入您的账号", "请输入您的密码"]


def test_normal_page_is_not_classified_as_login_required():
    classify = _load_login_classifier()["_login_required_from_labels"]

    required, markers = classify(["商品管理", "艾莎", "C级", "11.00"])

    assert required is False
    assert markers == []


def test_network_error_is_classified_from_visible_retry_message():
    classify = _load_login_classifier()["_classify_unavailable_ui"]

    error_code, markers = classify(["网络连接失败", "请检查网络后重试"])

    assert error_code == "NETWORK_OR_LOAD_ERROR"
    assert markers == ["网络连接失败", "请检查网络"]


def test_stuck_loading_page_is_classified_as_load_error():
    classify = _load_login_classifier()["_classify_unavailable_ui"]

    error_code, markers = classify(["商品名称搜索", "上架中", "加载中..."])

    assert error_code == "NETWORK_OR_LOAD_ERROR"
    assert markers == ["加载中"]


def test_blank_screen_is_classified_when_only_shell_chrome_is_visible():
    classify = _load_login_classifier()["_classify_unavailable_ui"]

    error_code, markers = classify(
        ["蚂蚁花团供应商", "微信", "\ue660", "松开使用 蚂蚁花团供应商 打开"]
    )

    assert error_code == "MINI_PROGRAM_BLANK_SCREEN"
    assert markers == []


def test_unrecognized_business_page_is_not_misclassified_as_blank():
    classify = _load_login_classifier()["_classify_unavailable_ui"]

    error_code, markers = classify(["商品管理", "艾莎", "C级", "11.00"])

    assert error_code == ""
    assert markers == []


def test_login_required_error_is_non_retryable_and_precedes_generic_error():
    source = FLOW_PATH.read_text(encoding="utf-8")

    assert '"LOGIN_REQUIRED"' in source
    assert '"小程序登录状态已失效，需要人工重新登录' in source
    assert '"NETWORK_OR_LOAD_ERROR"' in source
    assert '"MINI_PROGRAM_BLANK_SCREEN"' in source
    assert "_raise_classified_ui_error(window)" in source
