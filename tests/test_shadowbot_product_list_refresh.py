import ast
from pathlib import Path


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _load_refresh_helper(find_element, select_online=None):
    tree = ast.parse(FLOW_PATH.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "SliceError":
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_refresh_product_list":
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ELEMENTS"
            for target in node.targets
        ):
            nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "_find_element": find_element,
        "_select_online_product_list": select_online or (
            lambda _window, _timeout_seconds, result: result.update(
                {"active_listing_filter": "ONLINE"}
            )
        ),
        "_find_product_list_container": lambda window, timeout_seconds: find_element(
            window, "蚂蚁_商品管理_目标商品_容器", timeout_seconds
        ),
        "_now_iso": lambda: "2026-07-11T10:00:00+08:00",
        "sleep": lambda _seconds: None,
    }
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace["_refresh_product_list"], namespace["SliceError"], namespace["ELEMENTS"]


class _ClickTarget:
    def __init__(self, calls):
        self.calls = calls

    def click(self):
        self.calls.append("clicked")


def test_refresh_clicks_management_even_when_list_container_is_already_available():
    calls = []

    def find_element(_window, selector, _timeout):
        calls.append(selector)
        if selector == "价格弹窗_容器":
            raise slice_error("ELEMENT_NOT_FOUND", "dialog is closed", True)
        if selector == "蚂蚁_首页_商品管理_入口":
            return _ClickTarget(calls)
        assert selector == "蚂蚁_商品管理_目标商品_容器"
        return object()

    def select_online(_window, _timeout_seconds, result):
        calls.append("select_online")
        result["active_listing_filter"] = "ONLINE"

    refresh, slice_error, _elements = _load_refresh_helper(find_element, select_online)
    result = {"product_list_refreshes": []}

    event = refresh(object(), 5, result, "BEFORE_PRICE_READ")

    assert calls == [
        "价格弹窗_容器",
        "蚂蚁_首页_商品管理_入口",
        "clicked",
        "select_online",
        "蚂蚁_商品管理_目标商品_容器",
        "蚂蚁_商品管理_目标商品_容器",
    ]
    assert event["status"] == "SUCCESS"
    assert event["stage"] == "BEFORE_PRICE_READ"
    assert result["product_list_refreshes"] == [event]
    assert result["active_listing_filter"] == "ONLINE"


def test_refresh_failure_is_normalized_and_audited():
    def find_element(_window, selector, _timeout):
        if selector == "价格弹窗_容器":
            raise slice_error("ELEMENT_NOT_FOUND", "dialog is closed", True)
        raise slice_error("ELEMENT_NOT_FOUND", "management entry missing", True)

    refresh, slice_error, _elements = _load_refresh_helper(find_element)
    result = {"product_list_refreshes": []}

    try:
        refresh(object(), 5, result, "BEFORE_PRICE_READ")
    except slice_error as exc:
        assert exc.code == "PRODUCT_LIST_REFRESH_FAILED"
        assert exc.retryable is True
    else:
        raise AssertionError("expected PRODUCT_LIST_REFRESH_FAILED")

    event = result["product_list_refreshes"][0]
    assert event["status"] == "FAILED"
    assert event["error_code"] == "PRODUCT_LIST_REFRESH_FAILED"
    assert "management entry missing" in event["error_message"]


def test_main_flow_refreshes_before_initial_read_preview_cancel_and_submit_verification():
    source = FLOW_PATH.read_text(encoding="utf-8")

    # One helper definition plus refreshes before the initial read, after a
    # cancelled preview, and after a real submit. Login recovery must happen
    # before the first refresh.
    assert source.count('_refresh_product_list(') == 4
    login_check = source.index('current_step = "CHECK_LOGIN"')
    initial_refresh = source.index('"BEFORE_PRICE_READ"')
    initial_locate = source.index('current_step = "LOCATE_PRODUCT"', initial_refresh)
    preview_cancel = source.index('current_step = "CANCEL_PREVIEW"')
    preview_refresh = source.index('"AFTER_PREVIEW_CANCEL"', preview_cancel)
    preview_post_read = source.index('"PREVIEW_INPUT_MISMATCH"', preview_refresh)
    post_refresh = source.index('"AFTER_SUBMIT_VERIFY"')
    post_verify = source.index('current_step = "VERIFY_AFTER_SUBMIT"', post_refresh)

    assert login_check < initial_refresh < initial_locate
    assert preview_cancel < preview_refresh < preview_post_read
    assert post_refresh < post_verify
    assert "row_index, list_name, list_grade = _locate_product_row(" in source[post_refresh:post_verify]
    assert "_wait_after_submit_price" in source[post_verify:]


def test_refresh_explicitly_selects_online_listing_context():
    source = FLOW_PATH.read_text(encoding="utf-8")
    refresh_start = source.index("def _refresh_product_list(")
    refresh_end = source.index("def _select_online_product_list(", refresh_start)
    refresh_source = source[refresh_start:refresh_end]

    assert 'ONLINE_LIST_LABEL = "上架中"' in source
    assert "_select_online_product_list(window, timeout_seconds, result)" in refresh_source
    assert '_set_path_attribute(target_node, "acc-name", label)' in source
    assert '"dynamic_online_listing_tab"' in source
    assert 'result["active_listing_filter"] = "ONLINE"' in source
