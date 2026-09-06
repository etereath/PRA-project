import ast
import re
from pathlib import Path


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


def test_login_page_is_classified_from_welcome_and_login_labels():
    classify = _load_login_classifier()["_login_required_from_labels"]

    required, markers = classify(["欢迎使用蚂蚁花团供应商端", "登录"])

    assert required is True
    assert markers == ["欢迎使用蚂蚁花团供应商端"]


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
