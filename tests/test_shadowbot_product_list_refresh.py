import ast
from pathlib import Path


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _load_readiness_helpers(find_element, find_container, bounding_dict):
    tree = ast.parse(FLOW_PATH.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "SliceError":
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            "_product_list_empty_marker_visible",
            "_require_product_list_ready",
        }:
            nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "PRODUCT_LIST_EMPTY_LABEL": "暂无商品",
        "_exact_acc_label_selector": lambda label, _name: label,
        "_find_element": find_element,
        "_find_product_list_container": find_container,
        "_bounding_dict": bounding_dict,
    }
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace


def test_empty_list_readiness_precedes_long_container_lookup():
    calls = []
    marker = object()
    namespace = _load_readiness_helpers(
        lambda _window, selector, timeout: (
            calls.append(("empty", selector, timeout)),
            marker,
        )[1],
        lambda _window, _timeout: calls.append(("container",)),
        lambda element: (
            {"x": 0, "y": 0, "width": 100, "height": 20}
            if element is marker
            else {}
        ),
    )

    readiness = namespace["_require_product_list_ready"](object(), 30)

    assert readiness == "EMPTY_LIST_MARKER"
    assert calls == [("empty", "暂无商品", 1.0)]


def test_hidden_empty_marker_does_not_override_product_container():
    calls = []
    marker = object()
    container = object()
    namespace = _load_readiness_helpers(
        lambda _window, selector, timeout: (
            calls.append(("empty", selector, timeout)),
            marker,
        )[1],
        lambda _window, timeout: (
            calls.append(("container", timeout)),
            container,
        )[1],
        lambda element: (
            {"x": 0, "y": 0, "width": 0, "height": 0}
            if element is marker
            else {}
        ),
    )

    readiness = namespace["_require_product_list_ready"](object(), 30)

    assert readiness == "PRODUCT_LIST_CONTAINER"
    assert calls == [
        ("empty", "暂无商品", 1.0),
        ("container", 30),
    ]


def _load_refresh_helper(
    find_element,
    select_online=None,
    require_ready=None,
    *,
    collect_labels=None,
    login_state=None,
    recover_login=None,
):
    tree = ast.parse(FLOW_PATH.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "SliceError":
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            "_recover_visible_login_after_navigation",
            "_click_business_navigation_entry",
            "_refresh_product_list",
        }:
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
        "_require_product_list_ready": require_ready
        or (
            lambda window, timeout_seconds: (
                find_element(
                    window,
                    "蚂蚁_商品管理_目标商品_容器",
                    timeout_seconds,
                ),
                "PRODUCT_LIST_CONTAINER",
            )[1]
        ),
        "_now_iso": lambda: "2026-07-11T10:00:00+08:00",
        "_select_online_product_list": select_online
        or (lambda _window, _timeout_seconds, _result: None),
        "_collect_ui_state_labels": collect_labels or (lambda _window: []),
        "_login_page_state": login_state or (lambda _labels: ("NORMAL", [])),
        "_recover_login_if_needed": recover_login
        or (lambda *_args, **_kwargs: False),
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
    assert event["readiness"] == "PRODUCT_LIST_CONTAINER"
    assert result["product_list_refreshes"] == [event]


def test_refresh_accepts_explicit_empty_list_marker_as_ready():
    calls = []

    def find_element(_window, selector, _timeout):
        calls.append(selector)
        if selector == "价格弹窗_容器":
            raise slice_error("ELEMENT_NOT_FOUND", "dialog is closed", True)
        if selector == "蚂蚁_首页_商品管理_入口":
            return _ClickTarget(calls)
        raise AssertionError("container lookup must be replaced by empty readiness")

    refresh, slice_error, _elements = _load_refresh_helper(
        find_element,
        select_online=lambda _window, _timeout_seconds, _result: calls.append(
            "selected_online"
        ),
        require_ready=lambda _window, _timeout_seconds: "EMPTY_LIST_MARKER",
    )
    result = {"product_list_refreshes": []}

    event = refresh(object(), 5, result, "BEFORE_SET_ONLINE")

    assert event["status"] == "SUCCESS"
    assert event["readiness"] == "EMPTY_LIST_MARKER"
    assert calls == [
        "价格弹窗_容器",
        "蚂蚁_首页_商品管理_入口",
        "clicked",
        "selected_online",
    ]


def test_refresh_recovers_login_only_after_first_management_click_then_reclicks():
    calls = []

    def find_element(_window, selector, _timeout):
        calls.append(selector)
        if selector == "价格弹窗_容器":
            raise slice_error("ELEMENT_NOT_FOUND", "dialog is closed", True)
        if selector == "蚂蚁_首页_商品管理_入口":
            return _ClickTarget(calls)
        raise AssertionError("unexpected selector: " + selector)

    def recover_login(*_args, **_kwargs):
        assert _kwargs["detected_state"] == "ACCOUNT_PASSWORD"
        assert _kwargs["detected_markers"] == ["欢迎登录"]
        calls.append("recovered_login")
        return True

    refresh, slice_error, _elements = _load_refresh_helper(
        find_element,
        select_online=lambda _window, _timeout_seconds, _result: calls.append(
            "selected_online"
        ),
        require_ready=lambda _window, _timeout_seconds: "PRODUCT_LIST_CONTAINER",
        collect_labels=lambda _window: ["欢迎登录", "账号密码登录"],
        login_state=lambda _labels: ("ACCOUNT_PASSWORD", ["欢迎登录"]),
        recover_login=recover_login,
    )
    result = {"product_list_refreshes": []}

    event = refresh(
        object(),
        5,
        result,
        "BEFORE_SET_ONLINE",
        {"execution_mode": "COMMIT"},
        {"mode": "automatic"},
        object(),
    )

    first_click = calls.index("clicked")
    recovery = calls.index("recovered_login")
    second_click = calls.index("clicked", first_click + 1)
    assert first_click < recovery < second_click
    assert event["login_recovered_after_navigation"] is True
    assert result["login"]["check_path"] == (
        "POST_FIRST_ACTION_LOGIN_CHECK_RECOVERED"
    )
    assert calls[-1] == "selected_online"


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


def test_listing_actions_defer_login_detection_to_first_navigation_click():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for function_name in (
        "_run_listing_sync_v5",
        "_run_listing_action_reconcile_v5",
        "_run_set_online_v5",
        "_run_set_offline_v5",
    ):
        node = next(
            item
            for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == function_name
        )
        function_source = ast.get_source_segment(source, node)
        initial_refresh = function_source.index("_refresh_product_list(")
        assert "_recover_login_if_needed(" not in function_source[:initial_refresh]
        refresh_call = function_source[
            initial_refresh : function_source.index(")", initial_refresh) + 1
        ]
        assert "request" in refresh_call


def test_order_read_defers_login_detection_to_first_navigation_click():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "_run_order_scan_v6"
    )
    function_source = ast.get_source_segment(source, node)
    first_navigation = function_source.index("_click_business_navigation_entry(")
    assert "_recover_login_if_needed(" not in function_source[:first_navigation]
    assert "clicker=_order_click_element" in function_source[first_navigation:]
