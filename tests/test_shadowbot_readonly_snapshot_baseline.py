from __future__ import annotations

import ast
from pathlib import Path

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


def test_read_only_uses_the_verified_index_sequence_instead_of_find_all():
    function = _function_source("_enumerate_product_rows")

    assert "INDEXED_ENUMERATION_MAX_ROWS" in function
    assert "ROW_INDEX_START + ROW_INDEX_STEP * (position - 1)" in function
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

    assert "targets=None" in function
    assert "row_cache=None" in function
    assert "metrics=None" in function
    assert "is_target = not targets or any(" in function
    assert '"non_target_rows_skipped"' in function
    assert '"row_cache_hits"' in function


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
