"""Regression coverage for selector constraints that change across mini-program sessions."""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path


FLOW_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "vertical_slice_read_price.py"


def _load_page_id_normalizer():
    tree = ast.parse(FLOW_PATH.read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_remove_dynamic_page_id_constraints"
    )
    namespace = {"re": re}
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(FLOW_PATH), "exec"), namespace)
    return namespace["_remove_dynamic_page_id_constraints"]


def test_dynamic_page_id_constraint_is_removed_without_touching_stable_ids():
    normalize = _load_page_id_normalizer()
    selector_value = {
        "path": [
            {
                "attributes": [
                    {"name": "id", "value": "page-103"},
                    {"name": "class", "value": "page_wrapper"},
                ]
            },
            {
                "attributes": [
                    {"name": "id", "value": "container"},
                    {"name": "index", "value": "1"},
                ]
            },
        ]
    }

    assert normalize(selector_value) is selector_value
    assert selector_value["path"][0]["attributes"] == [
        {"name": "class", "value": "page_wrapper"}
    ]
    assert selector_value["path"][1]["attributes"] == [
        {"name": "id", "value": "container"},
        {"name": "index", "value": "1"},
    ]


def test_product_list_container_uses_dynamic_page_id_fallback():
    source = FLOW_PATH.read_text(encoding="utf-8")

    assert "def _find_product_list_container" in source
    assert "动态_商品管理列表容器" in source
    assert "_find_product_list_container(window, timeout_seconds)" in source


def test_order_date_values_use_global_exact_accessibility_labels():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_order_picker_value_selector"
    )
    helper_source = ast.get_source_segment(source, helper)

    assert "_exact_acc_label_selector(" in helper_source
    assert "value[\"path\"].append" not in helper_source


def test_order_transaction_amount_is_calculated_from_displayed_price_and_qty():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calculator = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_order_calculate_transaction_amount"
    )
    namespace = {"Decimal": Decimal}
    module = ast.Module(body=[calculator], type_ignores=[])
    exec(
        compile(ast.fix_missing_locations(module), str(FLOW_PATH), "exec"),
        namespace,
    )

    calculate = namespace["_order_calculate_transaction_amount"]
    assert calculate("5.50", "2") == "11.00"
    assert calculate("3.33", "3") == "9.99"


def test_order_reader_does_not_locate_a_separate_total_amount_element():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selector = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_order_row_field_selector"
    )
    reader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_order_read_rows"
    )
    selector_source = ast.get_source_segment(source, selector)
    reader_source = ast.get_source_segment(source, reader)

    assert '"order_transaction_amount":' not in selector_source
    assert 'read_field("unit_price", _order_normalize_amount)' in reader_source
    assert (
        "_order_calculate_transaction_amount(unit_price, qty)"
        in reader_source
    )
    assert '"订单卡片字段关联校验失败: " + field' in reader_source


def test_v5_waiting_row_scroll_probes_before_adaptive_keyboard_navigation():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_v5_prepare_row_for_click"
    )
    helper_source = ast.get_source_segment(source, helper)

    boundary_at = helper_source.index("_price_element_in_clickable_view(")
    focus_at = helper_source.index("focus_element.click()")
    send_key_at = helper_source.index('"{" + keyboard_key + "}"')

    assert boundary_at < focus_at < send_key_at
    assert '"PGUP" if center_y < safe_top else "PGDN"' in helper_source
    assert '"keyboard_key": keyboard_key' in helper_source
    assert "required_page_downs" not in helper_source
    assert "_advance_product_list(" not in helper_source


def test_v5_row_scroll_probes_the_actual_listing_action_button():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_v5_prepare_row_for_click"
    )
    helper_source = ast.get_source_segment(source, helper)

    assert 'click_target="price"' in helper_source
    assert 'click_target == "set_online_action"' in helper_source
    assert "WAITING_SET_ONLINE_BUTTON_SELECTOR" in helper_source
    assert 'click_target == "set_offline_action"' in helper_source
    assert "ONLINE_SET_OFFLINE_BUTTON_SELECTOR" in helper_source
    assert "_price_element_in_clickable_view(\n            target_element," in helper_source


def test_v5_set_offline_uses_online_only_scan_and_final_confirmation():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_set_offline_v5"
    )
    helper_source = ast.get_source_segment(source, helper)

    assert 'page_type="online"' in helper_source
    assert 'page_type="waiting"' not in helper_source
    assert "ONLINE_SET_OFFLINE_BUTTON_SELECTOR" in helper_source
    assert "ONLINE_SET_OFFLINE_CONFIRM_SELECTOR" in helper_source
    assert "_assert_set_offline_confirmation_identity(" in helper_source
    assert '"ACTION_INTENT_RECORDED"' in helper_source
    assert '"ACTION_CLICKED"' in helper_source
    assert '"POSTCHECK_STILL_ONLINE"' in helper_source
    assert "return _run_set_offline_v5(args, request, result)" in source


def test_v5_set_offline_button_uses_the_captured_row_offset():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_online_row_action_selector"
    )
    helper_source = ast.get_source_segment(source, helper)

    assert "ONLINE_SET_OFFLINE_INDEX_OFFSET = 14" in source
    assert "if not indexed_wx_views:" in helper_source
    assert "indexed_wx_views[-1]" in helper_source
    assert (
        "int(row_parent_index) + ONLINE_SET_OFFLINE_INDEX_OFFSET"
        in helper_source
    )


def test_v5_set_offline_prompt_is_constrained_to_the_expected_identity():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_set_offline_v5"
    )
    helper_source = ast.get_source_segment(source, helper)

    assert "expected_prompt_text" in helper_source
    assert "您确定下架【%s %s】吗？" in helper_source
    assert "_clone_dynamic_static_text_selector(" in helper_source
    assert '"动态_下架确认弹窗_提示文本"' in helper_source


def test_v5_listing_writes_support_multi_item_strict_serial_batches():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    online = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_set_online_v5"
    )
    offline = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_set_offline_v5"
    )
    online_source = ast.get_source_segment(source, online)
    offline_source = ast.get_source_segment(source, offline)

    assert "T13_VERTICAL_SLICE_SINGLE_ITEM_ONLY" not in source
    assert "for request_item in request_items:" in online_source
    assert "for plan in plans:" in online_source
    assert "plans.sort(key=lambda item: int(item[\"row\"][\"parent_index\"]))" in online_source
    assert "removed_waiting_items = 0" in online_source
    assert "removed_waiting_items * WAITING_ROW_INDEX_STEP" in online_source
    assert "final_online_scan = _v5_scan_page(" in online_source
    assert "final_waiting_scan = _v5_scan_page(" not in online_source
    assert "item_scan = _v5_scan_page(" not in online_source
    assert "post_scan = _v5_scan_page(" not in online_source
    assert online_source.count("_v5_scan_page(") == 3
    assert "targets=request_items" in online_source
    assert "_v5_wait_row_price(" in online_source
    assert "_v5_wait_row_inventory(" in online_source
    assert "_v5_wait_row_identity_changed(" in online_source
    assert '"set_online_total"' in online_source
    details_verified_at = online_source.index('"DETAILS_VERIFIED"')
    action_visibility_at = online_source.index(
        'click_target="set_online_action"'
    )
    action_clicked_at = online_source.index('"ACTION_CLICKED"')
    final_verification_at = online_source.index(
        '"FINAL_VERIFICATION"', action_clicked_at
    )
    assert (
        details_verified_at
        < action_visibility_at
        < action_clicked_at
        < final_verification_at
    )
    assert "for request_item in request_items:" in offline_source
    assert "for plan in plans:" in offline_source
    assert "plans.sort(key=lambda item: int(item[\"row\"][\"parent_index\"]))" in offline_source
    assert "removed_online_items = 0" in offline_source
    assert "removed_online_items * ROW_INDEX_STEP" in offline_source
    assert 'click_target="set_offline_action"' in offline_source
    assert "action_scan = _v5_scan_page(" not in offline_source
    assert "post_scan = _v5_scan_page(" not in offline_source
    assert offline_source.count("_v5_scan_page(") == 2
    assert "targets=request_items" in offline_source
    assert "_v5_wait_row_identity_changed(" in offline_source
    assert '"set_offline_total"' in offline_source
    assert "_v5_classify_interrupted_items(" in online_source
    assert "_v5_classify_interrupted_items(" in offline_source
    assert (
        "for execution_ordinal, plan in enumerate(plans, start=1):"
        in offline_source
    )
    action_clicked_at = offline_source.index('"ACTION_CLICKED"')
    fault_at = offline_source.index(
        "_v5_raise_controlled_action_unknown(",
        action_clicked_at,
    )
    row_readback_at = offline_source.index(
        "_v5_wait_row_identity_changed(",
        action_clicked_at,
    )
    verified_at = offline_source.index(
        '"operation_result": "VERIFIED"',
        row_readback_at,
    )
    next_phase_at = offline_source.index(
        '"FINAL_VERIFICATION"',
        verified_at,
    )
    assert action_clicked_at < fault_at < row_readback_at < verified_at
    assert verified_at < next_phase_at


def test_v5_set_online_captures_post_failure_two_page_snapshot():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    online = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_set_online_v5"
    )
    recovery = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_v5_post_failure_snapshot"
    )
    online_source = ast.get_source_segment(source, online)
    recovery_source = ast.get_source_segment(source, recovery)

    assert '"POST_FAILURE_SCAN_STARTED"' in online_source
    assert '"POST_FAILURE_SCAN_COMPLETED"' in online_source
    assert 'result["post_failure_snapshot"]' in online_source
    assert 'page_type="online"' in recovery_source
    assert 'page_type="waiting"' in recovery_source
    assert '"snapshot_complete": True' in recovery_source
