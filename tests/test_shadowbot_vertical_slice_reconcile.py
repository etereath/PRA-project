import ast
from pathlib import Path


FLOW_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "vertical_slice_read_price.py"
WORKER_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "shadowbot_queue_worker.py"


def _load_reconcile_helper():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_build_reconcile_update"
    )
    module = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace["_build_reconcile_update"]


def _load_named_helpers(*names):
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(body=wanted, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return {name: namespace[name] for name in names}


def test_reconcile_verified_when_actual_price_matches_target():
    build_update = _load_reconcile_helper()

    update = build_update("19.50", "19.00", "19.50")

    assert update["status"] == "VERIFIED"
    assert update["run_success_flag"] is True
    assert update["business_operation_completed"] is True
    assert update["side_effect_state"] == "VERIFIED"
    assert update["error_code"] == ""
    assert update["retryable"] is False


def test_reconcile_not_applied_when_actual_price_matches_expected_old_price():
    build_update = _load_reconcile_helper()

    update = build_update("19.00", "19.00", "19.50")

    assert update["status"] == "NOT_APPLIED"
    assert update["run_success_flag"] is True
    assert update["business_operation_completed"] is False
    assert update["side_effect_state"] == "NOT_APPLIED"
    assert update["error_code"] == "SUBMIT_NOT_APPLIED"
    assert update["retryable"] is False


def test_reconcile_needs_reconciliation_when_actual_price_is_other_value():
    build_update = _load_reconcile_helper()

    update = build_update("20.00", "19.00", "19.50")

    assert update["status"] == "SIDE_EFFECT_UNKNOWN"
    assert update["run_success_flag"] is None
    assert update["business_operation_completed"] is None
    assert update["side_effect_state"] == "UNKNOWN"
    assert update["error_code"] == "POST_SUBMIT_PRICE_MISMATCH"
    assert update["retryable"] is False


def test_reconcile_mode_is_read_only_and_does_not_reference_confirm_selector():
    source = FLOW_PATH.read_text(encoding="utf-8")
    reconcile_start = source.index('elif execution_mode == "RECONCILE":')
    preview_start = source.index('else:', reconcile_start)
    reconcile_block = source[reconcile_start:preview_start]

    assert '"RECONCILE"' in source
    assert "价格弹窗_确认按钮" not in reconcile_block
    assert "_build_reconcile_update" in reconcile_block
    assert "_capture_window" in reconcile_block
    assert "_run_fill_preview" not in reconcile_block
    assert "SIDE_EFFECT_UNKNOWN" in source


def test_commit_mode_records_submit_intent_and_verifies_after_submit():
    source = FLOW_PATH.read_text(encoding="utf-8")
    commit_start = source.index('else:', source.index('elif execution_mode == "RECONCILE":'))
    commit_block = source[commit_start:]

    assert '"COMMIT"' in source
    assert "SUBMIT_INTENT_RECORDED" in commit_block
    assert "CONFIRM_PRICE_DIALOG" in commit_block
    assert "CLICK_FINAL_SAVE" in commit_block
    assert "_confirm_price_dialog" in commit_block
    assert "_click_final_save" in commit_block
    assert "FINAL_SAVE_BUTTON_NODE_NAMES" in source
    assert '"wx-button"' in source
    assert '"wx-van-button"' in source
    assert "final_save_node" in commit_block
    assert "_wait_after_submit_price" in commit_block
    assert "AFTER_SUBMIT" in commit_block
    assert "FINAL_SAVE_NOT_FOUND" in commit_block
    assert "SUBMIT_RESULT_UNKNOWN" in commit_block
    assert "SUBMIT_NOT_APPLIED" in commit_block
    assert "POST_SUBMIT_PRICE_MISMATCH" in commit_block


def test_commit_side_effect_exception_is_not_retryable_failed():
    source = FLOW_PATH.read_text(encoding="utf-8")

    assert "def _has_submit_side_effect(" in source
    assert "def _mark_submit_result_unknown(" in source
    assert 'if execution_mode == "COMMIT" and _has_submit_side_effect(result):' in source
    assert '"status": "SIDE_EFFECT_UNKNOWN"' in source
    assert '"error_code": "SUBMIT_RESULT_UNKNOWN"' in source
    assert '"original_error_code": original_error_code' in source
    assert '"retryable": False' in source


def test_commit_side_effect_exception_helper_marks_reconciliation():
    helpers = _load_named_helpers("_has_submit_side_effect", "_mark_submit_result_unknown")
    result = {"side_effect_state": "SUBMIT_CLICKED"}

    assert helpers["_has_submit_side_effect"](result) is True
    helpers["_mark_submit_result_unknown"](
        result,
        "VERIFY_AFTER_SUBMIT",
        "ELEMENT_NOT_FOUND",
        "list price disappeared",
    )

    assert result["status"] == "SIDE_EFFECT_UNKNOWN"
    assert result["run_success_flag"] is None
    assert result["business_operation_completed"] is None
    assert result["current_step"] == "VERIFY_AFTER_SUBMIT"
    assert result["side_effect_state"] == "UNKNOWN"
    assert result["error_code"] == "SUBMIT_RESULT_UNKNOWN"
    assert result["original_error_code"] == "ELEMENT_NOT_FOUND"
    assert result["original_error_message"] == "list price disappeared"
    assert result["retryable"] is False


def test_single_product_flow_initializes_execution_mode_before_input_validation():
    source = FLOW_PATH.read_text(encoding="utf-8")
    main_start = source.index("def _run_single_product_flow(args, allow_contract_dispatch=False):")
    try_start = source.index("    try:", main_start)
    pre_try_block = source[main_start:try_start]

    assert 'execution_mode = ""' in pre_try_block
    assert 'current_step = "VALIDATE_INPUT"' in pre_try_block


def test_product_locator_prefers_dynamically_discovered_parent_index():
    locate = _load_named_helpers("_locate_product_row")["_locate_product_row"]
    locate.__globals__.update(
        {
            "_enumerate_product_rows": lambda window, timeout: [
                {
                    "source": "DYNAMIC",
                    "parent_index": 49,
                    "name": "艾莎",
                    "grade": "C级",
                }
            ],
            "_list_name_matches": lambda actual, expected_name, expected_grade: actual
            == expected_name,
            "_normalize_text": lambda value: value,
        }
    )

    parent_index, name, grade = locate(object(), "艾莎", "C级", 3, 15)

    assert parent_index == 49
    assert name == "艾莎"
    assert grade == "C级"


def test_product_locator_no_longer_relies_only_on_three_fixed_rows():
    source = FLOW_PATH.read_text(encoding="utf-8")

    assert "window.find_all(selector" in source
    assert '"source": "DYNAMIC"' in source
    assert '"source": "FIXED_FALLBACK"' in source


def test_submit_intent_phase_is_written_before_inner_confirm():
    source = FLOW_PATH.read_text(encoding="utf-8")
    intent_index = source.index('_write_phase(request, result, "SUBMIT_INTENT_RECORDED")')
    confirm_index = source.index("_confirm_price_dialog(window, timeout_seconds)", intent_index)

    assert intent_index < confirm_index


def test_stop_signal_is_not_honored_after_submit_intent():
    source = FLOW_PATH.read_text(encoding="utf-8")
    intent_index = source.index('_write_phase(request, result, "SUBMIT_INTENT_RECORDED")')
    commit_tail = source[intent_index : source.index("\n    except SliceError as exc:", intent_index)]

    assert "_check_stop_before_submit" not in commit_tail
    assert '_write_phase(request, result, "SUBMIT_CLICKED")' in commit_tail
    assert "VERIFY_AFTER_SUBMIT" in commit_tail


def test_queue_worker_is_bounded_single_instance_and_writes_heartbeat():
    source = WORKER_PATH.read_text(encoding="utf-8")

    assert "SHADOWBOT_WORKER_MAX_HOURS" in source
    assert "SHADOWBOT_WORKER_MAX_TASKS" in source
    assert "msvcrt.LK_NBLCK" in source
    assert 'self.root / "heartbeat.json"' in source
    assert 'self.control / "stop.signal"' in source
    assert "if __package__:" in source
    assert "from . import vertical_slice_read_price" in source
    assert '"queue_phase": "RESULT_WRITTEN"' in source
