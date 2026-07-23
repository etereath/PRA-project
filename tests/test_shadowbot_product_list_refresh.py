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
        "_find_product_list_container": lambda window, timeout_seconds: find_element(
            window, "蚂蚁_商品管理_目标商品_容器", timeout_seconds
        ),
        "_now_iso": lambda: "2026-07-11T10:00:00+08:00",
        "_select_online_product_list": select_online
        or (lambda _window, _timeout_seconds, _result: None),
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

    refresh, slice_error, _elements = _load_refresh_helper(
        find_element,
        select_online=lambda _window, _timeout_seconds, _result: calls.append(
            "selected_online"
        ),
    )
    result = {"product_list_refreshes": []}

    event = refresh(object(), 5, result, "BEFORE_PRICE_READ")

    assert calls == [
        "价格弹窗_容器",
        "蚂蚁_首页_商品管理_入口",
        "clicked",
        "selected_online",
        "蚂蚁_商品管理_目标商品_容器",
        "蚂蚁_商品管理_目标商品_容器",
    ]
    assert event["status"] == "SUCCESS"
    assert event["stage"] == "BEFORE_PRICE_READ"
    assert result["product_list_refreshes"] == [event]


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


def test_main_flow_refreshes_before_initial_read_and_once_before_submit_verification():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_single_product_flow"
    )
    main_source = ast.get_source_segment(source, main_node)

    # The verified single-item path prepares/reuses the list before its price
    # read and retains the full-refresh fallback after the fast verification.
    login_check = main_source.index('current_step = "CHECK_LOGIN"')
    prepare_call = main_source.index("_prepare_product_list(", login_check)
    initial_refresh = main_source.index('"BEFORE_PRICE_READ"', prepare_call)
    initial_locate = main_source.index('current_step = "LOCATE_PRODUCT"', initial_refresh)
    post_refresh = main_source.index('"AFTER_SUBMIT_VERIFY"')
    post_verify = main_source.index('current_step = "VERIFY_AFTER_SUBMIT"', post_refresh)

    assert login_check < prepare_call < initial_refresh < initial_locate
    assert post_refresh < post_verify
    assert "_locate_product_row_at_position(" in main_source[post_refresh:post_verify]
    assert "_wait_after_submit_price" in main_source[post_verify:]
