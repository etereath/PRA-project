from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import pytest

from app.shadowbot_contract_primitives import normalize_contract_grade


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _source() -> str:
    return FLOW_PATH.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    source = _source()
    lines = source.splitlines()
    node = next(
        item
        for item in ast.parse(source).body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


class _SharedListTestError(Exception):
    def __init__(self, code, message, retryable):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _shared_list_helper(events):
    class Element:
        def click(self):
            events.append("CLICK_FIRST")

    class Window:
        def activate(self):
            events.append("ACTIVATE")

        def wait_active(self, timeout):
            events.append("WAIT_ACTIVE")

    class Win32:
        @staticmethod
        def send_keys(key, *_args):
            events.append(key)

    element = Element()
    namespace = {
        "V5_KEYBOARD_LOAD_WAIT_SECONDS": 0.0,
        "V5_KEYBOARD_END_LOAD_WAIT_SECONDS": 0.0,
        "SliceError": _SharedListTestError,
        "_find_element": lambda *_args: element,
        "os": os,
        "sleep": lambda *_args: None,
        "time": time,
        "win32": Win32(),
    }
    exec(_function_source("_materialize_list_with_end_and_restore"), namespace)
    return namespace["_materialize_list_with_end_and_restore"], Window(), element


def test_read_only_uses_the_verified_index_sequence_instead_of_find_all():
    function = _function_source("_enumerate_product_rows")

    assert "INDEXED_ENUMERATION_MAX_ROWS" in function
    assert "ROW_INDEX_START + row_index_step * (position - 1)" in function
    assert "WAITING_ROW_INDEX_STEP" in function
    assert '"source": "INDEXED_SEQUENCE"' in function
    assert ".find_all(" not in function


def test_worker_grade_matching_normalizes_the_chinese_grade_suffix():
    locator = _function_source("_locate_product_row_at_position")
    assertion = _function_source("_assert_grade_identity")

    assert normalize_contract_grade("E") == "E"
    assert normalize_contract_grade("E级") == "E"
    assert "_multi_product_grade(grade)" in locator
    assert "_multi_product_grade(expected_grade)" in locator
    assert "_multi_product_grade(actual)" in assertion
    assert "_multi_product_grade(expected)" in assertion


def test_commit_paths_use_the_grade_aware_identity_assertion():
    source = _source()

    assert source.count('_assert_grade_identity("商品等级", list_grade, expected_grade)') == 3
    assert '_assert_grade_identity("price dialog grade", context["grade"], expected_grade)' in source


def test_row_hydration_supports_full_page_read_and_targeted_pre_commit():
    function = _function_source("_multi_product_enumerate_rows")
    snapshot_items = _function_source("_v5_snapshot_items")

    assert "targets=None" in function
    assert "row_cache=None" in function
    assert "metrics=None" in function
    assert "is_target = not targets or any(" in function
    assert '"non_target_rows_skipped"' in function
    assert '"row_cache_hits"' in function
    assert 'online_rows[0].get("inventory") is not None' in snapshot_items
    assert 'waiting_rows[0].get("inventory") is not None' in snapshot_items


def test_read_only_reports_the_verified_performance_strategy():
    function = _function_source("_run_multi_product_read_flow")

    assert '"FULL_PAGE_FIELDS_WITH_MAPPING_WARNINGS"' in function
    assert "targets=None" in function
    assert "row_cache=row_cache" in function
    assert "metrics=read_performance" in function


def test_read_only_stops_at_the_structured_end_marker_before_scrolling():
    source = _source()
    enumeration = _function_source("_enumerate_product_rows")
    marker_probe = _function_source("_product_list_end_marker_visible")
    flow = _function_source("_run_multi_product_read_flow")

    assert 'PRODUCT_LIST_END_LABEL = "没有更多了"' in source
    assert 'scan_state["next_index_missing"] = True' in enumeration
    assert 'scan_state["missing_parent_index"] = parent_index' in enumeration
    assert "PRODUCT_LIST_END_LABEL" in marker_probe
    end_check = flow.index('scan_state.get("next_index_missing")')
    scroll_call = flow.index("_advance_product_list(window, timeout_seconds)")
    assert end_check < scroll_call
    assert '"INDEX_SEQUENCE_COMPLETE_WITH_END_MARKER"' in flow
    assert '"scrolls": 0' in flow


def test_read_only_uses_one_no_progress_scroll_as_a_fallback():
    flow = _function_source("_run_multi_product_read_flow")

    assert "previous_fingerprint" in flow
    assert "current_fingerprint == previous_fingerprint" in flow
    assert '"NO_PROGRESS_AFTER_SCROLL"' in flow
    assert "len(fingerprints) >= 3" not in flow


def test_read_only_keeps_the_verified_business_entry_login_fast_path():
    function = _function_source("_recover_login_if_needed")

    assert 'ELEMENTS["product_management"]' in function
    assert '"check_path": "BUSINESS_ENTRY_FAST_PATH"' in function
    assert '"FULL_UI_STATE_SCAN"' in function


def test_v4_contract_remains_available_but_read_only_evidence_is_optional():
    source = _source()
    main_function = _function_source("_run_single_product_flow")

    assert "def _run_commit_batch_v4" in source
    assert 'request.get("contract_version") == 4' in main_function
    read_only_branch = main_function.split(
        'if execution_mode == "READ_ONLY":', 1
    )[1].split('elif execution_mode == "RECONCILE":', 1)[0]
    assert "if capture_evidence:" in read_only_branch


def test_task13_sync_reuses_indexed_reader_with_page_specific_price_offset():
    source = _source()
    selector = _function_source("_row_field_selector")
    sync_flow = _function_source("_run_listing_sync_v5")
    page_scan = _function_source("_v5_scan_page")
    v5_identity_scan = _function_source("_enumerate_product_rows")

    assert "WAITING_PRICE_INDEX_OFFSET = 8" in source
    assert "WAITING_ROW_INDEX_STEP = 15" in source
    assert "PRICE_INDEX_OFFSET = 9" in source
    assert "WAITING_SELECTOR_TEMPLATES" in selector
    assert "page_type=page_type" in page_scan
    assert "_enumerate_product_rows" in page_scan
    assert "window.find_all" not in v5_identity_scan
    assert "WAITING_ROW_INDEX_STEP" in v5_identity_scan
    assert "row_index_step" in v5_identity_scan
    assert 'page_type="online"' in sync_flow
    assert 'page_type="waiting"' in sync_flow
    assert sync_flow.index('page_type="online"') < sync_flow.index(
        "_select_waiting_product_list"
    )
    assert sync_flow.index("_select_waiting_product_list") < sync_flow.index(
        'page_type="waiting"'
    )
    assert sync_flow.count("targets=None") == 2
    assert 'timing_stage="sync_online_scan"' in sync_flow
    assert 'timing_stage="sync_waiting_scan"' in sync_flow
    assert '"listing_sync_total"' in sync_flow


def test_task13_sync_requires_complete_or_empty_markers_and_never_calls_write_actions():
    source = _source()
    page_scan = _function_source("_v5_scan_page")
    materialize = _function_source("_materialize_list_with_end_and_restore")
    sync_flow = _function_source("_run_listing_sync_v5")

    assert "_product_list_end_marker_visible" in page_scan
    assert "_product_list_empty_marker_visible" in page_scan
    assert '"termination_reason": "EMPTY_LIST_MARKER"' in page_scan
    assert "END_MARKER_NOT_VERIFIED" in page_scan
    assert "V5_KEYBOARD_LOAD_WAIT_SECONDS = 0.1" in source
    assert "V5_KEYBOARD_END_LOAD_WAIT_SECONDS = 0.8" in source
    assert "SCROLL_CONTROL_UNVERIFIED" not in sync_flow
    assert "focus_action()" in materialize
    assert '"{END}"' in materialize
    assert '"{HOME}"' in materialize
    assert "initial_rows" not in page_scan
    assert "expected_top_identity" not in page_scan
    assert materialize.index("focus_action()") < materialize.index(
        '"{END}"'
    )
    assert materialize.index('"{END}"') < materialize.index('"{HOME}"')
    assert materialize.index('"{HOME}"') < materialize.index(
        "top_state_reader()"
    )
    assert page_scan.count("_enumerate_product_rows(") == 1
    assert "preload_rows" not in page_scan
    assert "current_rows = _enumerate_product_rows" not in page_scan
    assert "LIST_TOP_NOT_VERIFIED" in page_scan
    assert "FULL_LIST_NOT_MATERIALIZED_AFTER_HOME" in page_scan
    assert "missing_after_home" not in page_scan
    assert "_advance_product_list" not in page_scan
    assert "_v5_reset_page_to_top" not in sync_flow
    assert '"online_end_marker_verified": True' in sync_flow
    assert '"waiting_end_marker_verified": True' in sync_flow
    for forbidden in (
        "_confirm_price_dialog",
        "_fill_target_price",
        "_commit_v4",
        "ACTION_CLICKED",
    ):
        assert forbidden not in sync_flow


def test_all_task13_listing_flows_share_end_load_then_home_full_scan():
    page_scan = _function_source("_v5_scan_page")
    materialize = _function_source("_materialize_list_with_end_and_restore")
    sync_flow = _function_source("_run_listing_sync_v5")
    online_flow = _function_source("_run_set_online_v5")
    offline_flow = _function_source("_run_set_offline_v5")
    post_failure_flow = _function_source("_v5_post_failure_snapshot")

    assert "no row enumeration is allowed before HOME" in page_scan
    assert materialize.index('"{END}"') < materialize.index(
        "sleep(float(end_load_wait_seconds))"
    )
    assert materialize.index(
        "sleep(float(end_load_wait_seconds))"
    ) < materialize.index('"{HOME}"')
    assert materialize.index('"{HOME}"') < materialize.index(
        "top_state_reader()"
    )
    assert sync_flow.count("_v5_scan_page(") == 2
    assert online_flow.count("_v5_scan_page(") == 3
    assert offline_flow.count("_v5_scan_page(") == 2
    assert post_failure_flow.count("_v5_scan_page(") == 2
    for flow in (sync_flow, online_flow, offline_flow, post_failure_flow):
        assert '"{END}"' not in flow


def test_order_scan_reuses_the_shared_end_home_materialization_flow():
    materialize = _function_source("_materialize_list_with_end_and_restore")
    order_flow = _function_source("_run_order_scan_v6")
    safe_focus = _function_source("_order_focus_list_container_corner")

    assert "_materialize_list_with_end_and_restore(" in order_flow
    assert "focus_action=lambda: _order_focus_list_container_corner(" in order_flow
    assert "first_item_selector" not in order_flow
    assert "_order_row_field_selector(1" not in safe_focus
    assert "platform_product_name" not in safe_focus
    assert "_find_order_scroll_container" in safe_focus
    assert 'ORDER_LIST_CONTAINER_SELECTOR = "订单管理_容器"' in _source()
    assert "win32.mouse_move(" in safe_focus
    assert "win32.mouse_click(" in safe_focus
    assert 'bounding["width"] - inset' in safe_focus
    assert 'bounding["height"] / 2.0' in safe_focus
    assert '"ORDER_SCROLL_FOCUS_FAILED"' in safe_focus
    assert "empty_marker_visible=_order_list_empty_marker_visible" in order_flow
    assert "end_marker_visible=_order_list_end_marker_visible" in order_flow
    assert "top_state_reader=lambda: _order_top_row_state(" in order_flow
    assert '"ORDER_LIST_TOP_NOT_VERIFIED"' in order_flow
    assert '"{END}"' not in order_flow
    assert '"{HOME}"' not in order_flow
    assert "mouse_wheel" not in order_flow
    assert "max_end_actions=max_scrolls" in order_flow
    assert "max_end_seconds=max_seconds" in order_flow
    assert '"scroll_count": scroll_count' in order_flow
    assert '"scroll_progress_verified": scroll_progress_verified' in order_flow
    assert "focus_action()" in materialize
    assert "while end_action_count < max(1, int(max_end_actions))" in materialize


def test_shared_list_helper_focuses_then_ends_homes_and_verifies_top():
    events = []
    helper, window, focus_element = _shared_list_helper(events)

    result = helper(
        window,
        {},
        1,
        focus_action=focus_element.click,
        empty_marker_visible=lambda *_args: False,
        end_marker_visible=lambda *_args: events.append("TAIL") or True,
        top_state_reader=lambda: events.append("TOP") or {"row": 1},
        stop_error_code="STOPPED",
        stop_error_message="stopped",
        end_error_code="END_FAILED",
        end_error_message="end failed",
        top_error_code="TOP_FAILED",
        top_error_message="top failed",
    )

    assert events.index("CLICK_FIRST") < events.index("{END}")
    assert events.index("{END}") < events.index("TAIL")
    assert events.index("TAIL") < events.index("{HOME}")
    assert events.index("{HOME}") < events.index("TOP")
    assert result["scroll_count"] == 1
    assert result["scroll_progress_verified"] is True
    assert result["top_state"] == {"row": 1}


def test_shared_list_helper_homes_even_when_end_marker_is_missing():
    events = []
    helper, window, focus_element = _shared_list_helper(events)

    with pytest.raises(_SharedListTestError) as raised:
        helper(
            window,
            {},
            1,
            focus_action=focus_element.click,
            empty_marker_visible=lambda *_args: False,
            end_marker_visible=lambda *_args: False,
            top_state_reader=lambda: {"row": 1},
            stop_error_code="STOPPED",
            stop_error_message="stopped",
            end_error_code="END_FAILED",
            end_error_message="end failed",
            top_error_code="TOP_FAILED",
            top_error_message="top failed",
        )

    assert raised.value.code == "END_FAILED"
    assert raised.value.scroll_count == 1
    assert raised.value.scroll_progress_verified is False
    assert events.index("{END}") < events.index("{HOME}")


def test_shared_list_helper_repeats_end_until_tail_is_verified():
    events = []
    helper, window, focus_element = _shared_list_helper(events)

    result = helper(
        window,
        {},
        1,
        focus_action=focus_element.click,
        empty_marker_visible=lambda *_args: False,
        end_marker_visible=lambda *_args: events.count("{END}") >= 3,
        top_state_reader=lambda: {"row": 1},
        stop_error_code="STOPPED",
        stop_error_message="stopped",
        end_error_code="END_FAILED",
        end_error_message="end failed",
        top_error_code="TOP_FAILED",
        top_error_message="top failed",
        max_end_actions=5,
        max_end_seconds=30,
    )

    assert events.count("{END}") == 3
    assert events.index("{END}") < events.index("{HOME}")
    assert result["scroll_count"] == 3
    assert result["scroll_progress_verified"] is True
