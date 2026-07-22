from __future__ import annotations

import ast
from pathlib import Path


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _function_source(name: str) -> str:
    source = FLOW_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    node = next(
        item
        for item in ast.parse(source).body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def test_successful_legacy_commit_chain_remains_the_v4_execution_base():
    main_source = _function_source("_run_single_product_flow")

    assert "_locate_product_row_at_position(" in main_source
    assert "product_position >= SINGLE_PRODUCT_SCROLL_START_POSITION" in main_source
    assert "_prepare_scrolled_product_for_click(" in main_source
    assert "_assert_dialog_context(" in main_source
    assert "_fill_target_price(" in main_source
    assert '"SUBMIT_INTENT_RECORDED"' in main_source
    assert "_confirm_price_dialog(" in main_source
    assert "_wait_after_submit_price(" in main_source
    assert '"CURRENT_LIST_FAST_PATH"' in main_source


def test_v4_batch_fast_path_skips_only_redundant_preparation():
    main_source = _function_source("_run_single_product_flow")

    assert '"BATCH_PREPARED_WINDOW"' in main_source
    assert '"BATCH_PREFLIGHT_REUSED"' in main_source
    assert '"BATCH_PREFLIGHT_VALIDATED"' in main_source
    assert '"full_page_enumeration_skipped": True' in main_source
    assert "_locate_product_row_at_position(" in main_source
    assert "_assert_dialog_context(" in main_source
    assert "_wait_after_submit_price(" in main_source

    orchestrator_source = _function_source("_run_commit_batch_v4")
    viewport_source = _function_source("_commit_v4_prepare_first_target_for_click")
    assert "_commit_v4_prepare_first_target_for_click(" in orchestrator_source
    assert 'direction="up"' in viewport_source
    assert "_price_element_in_clickable_view(" in viewport_source


def test_v4_item_adapter_uses_the_successful_queue_parameters():
    function = _function_source("_commit_v4_stable_request")

    assert '"product_keyword": "%s%s"' in function
    assert '"page_position_hint": int(row["position"])' in function
    assert '"reuse_product_list": True' in function
    assert '"batch_preflight_reuse": True' in function
    assert '"final_save_required": False' in function
    assert '"fast_post_submit_verify": True' in function


def test_v4_orchestrator_calls_stable_single_product_flow_and_stops_after_first_failure():
    function = _function_source("_run_commit_batch_v4")

    assert "_run_single_product_flow(stable_args)" in function
    assert '"_prepared_window": window' in function
    assert '"_batch_preflight_validated": True' in function
    assert 'if output["status"] != "VERIFIED":' in function
    assert "break" in function
