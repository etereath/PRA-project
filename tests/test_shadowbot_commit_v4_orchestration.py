from __future__ import annotations

import ast
import json
import os
import time
from pathlib import Path


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


class SliceError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _load_functions():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "_commit_v4_item_result",
            "_commit_v4_stable_request",
            "_commit_v4_counts",
            "_commit_v4_page_snapshot",
            "_commit_v4_update_page_snapshot",
            "_commit_v4_prepare_first_target_for_click",
            "_run_commit_batch_v4",
        }
    ]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "json": json,
        "os": os,
        "SliceError": SliceError,
        "ELEMENT_TIMEOUT_DEFAULT": 15,
        "APPLET_LAUNCH_TIMEOUT_DEFAULT": 20,
        "WINDOW_TITLE_DEFAULT": "蚂蚁花团供应商",
        "WINDOW_X_DEFAULT": 0,
        "WINDOW_Y_DEFAULT": 0,
        "WINDOW_WIDTH_DEFAULT": 562,
        "WINDOW_HEIGHT_DEFAULT": 1056,
        "SINGLE_PRODUCT_SCROLL_START_POSITION": 4,
        "SINGLE_PRODUCT_MAX_SCROLL_ATTEMPTS": 3,
        "time": time,
    }
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace


def _item(task_id, sku, name, grade, old_price, target_price):
    return {
        "item_id": "ITEM-" + task_id,
        "source_task_id": task_id,
        "internal_sku": sku,
        "expected_product_name": name,
        "expected_grade": grade,
        "expected_old_price": old_price,
        "target_price": target_price,
        "item_payload_sha256": "sha256:" + task_id.lower().ljust(64, "0")[:64],
        "operation_id": "OP-" + task_id,
        "item_execution_attempt_id": "ATTEMPT-" + task_id,
        "write_identity_key": "蚂蚁花团供应商|" + sku,
        "page_identity_key": "蚂蚁花团供应商|%s|%s" % (name, grade),
    }


def test_v4_batch_restores_upper_viewport_before_first_visible_row():
    namespace = _load_functions()
    clickable_states = iter((False, True))
    scroll_directions = []
    namespace.update(
        {
            "_find_element": lambda *args, **kwargs: object(),
            "_row_field_selector": lambda row, field: (row, field),
            "_price_element_in_clickable_view": lambda *args, **kwargs: (
                next(clickable_states),
                {"x": 1, "y": 1, "width": 1, "height": 1},
            ),
            "_advance_product_list": lambda window, timeout, direction: (
                scroll_directions.append(direction) or True
            ),
        }
    )

    result = namespace["_commit_v4_prepare_first_target_for_click"](
        object(),
        15,
        {"position": 1, "parent_index": 1},
        window_x=0,
        window_y=0,
        window_width=562,
        window_height=1056,
    )

    assert result["status"] == "SUCCESS"
    assert result["direction"] == "up"
    assert len(result["attempts"]) == 2
    assert scroll_directions == ["up"]


def test_v4_batch_defers_row_four_to_existing_downward_scroll_path():
    namespace = _load_functions()

    result = namespace["_commit_v4_prepare_first_target_for_click"](
        object(),
        15,
        {"position": 4, "parent_index": 49},
        window_x=0,
        window_y=0,
        window_width=562,
        window_height=1056,
    )

    assert result["status"] == "DEFERRED_TO_ITEM_SCROLL"
    assert result["attempts"] == []


def test_v4_stable_request_forwards_validated_batch_fault_injection():
    namespace = _load_functions()
    item = _item(
        "TASK-AISHA-B",
        "AISHA-B-60-Z",
        "艾莎",
        "B级",
        "10.10",
        "10.20",
    )
    request = {
        "execution_attempt_id": "ATTEMPT-CONTROLLED-UNKNOWN-0001",
        "operation_id": "OP-CONTROLLED-UNKNOWN-0001",
        "task_id": "BATCHTASK-CONTROLLED-UNKNOWN-0001",
        "instruction_hash": "sha256:" + "a" * 64,
        "batch_id": "BATCH-T12-CONTROLLED-UNKNOWN-0001",
        "platform_name": "蚂蚁花团供应商",
        "fault_injection": "AFTER_SUBMIT_CLICK_UNKNOWN",
    }

    stable_request = namespace["_commit_v4_stable_request"](
        request,
        item,
        {"position": 4},
        1,
        1,
    )

    assert stable_request["fault_injection"] == "AFTER_SUBMIT_CLICK_UNKNOWN"


def _execute(items, rows, outcomes):
    namespace = _load_functions()
    calls = []
    prepared_window = object()
    items_by_task = {item["source_task_id"]: item for item in items}
    page_rows = []
    for row_id, supplied_row in rows.items():
        row = dict(supplied_row)
        item = items_by_task.get(row_id)
        if item is not None:
            row.setdefault("name", item["expected_product_name"])
            row.setdefault("grade", item["expected_grade"])
        row.setdefault("inventory", 1)
        row.setdefault("listing_status", "ONLINE")
        page_rows.append(row)

    def stable_single_product_flow(args):
        request = json.loads(args["request_json"])
        request["_prepared_window_reused"] = args.get("_prepared_window") is prepared_window
        request["_batch_preflight_validated"] = args.get("_batch_preflight_validated") is True
        calls.append(request)
        outcome = outcomes[request["task_id"]]
        args["result_json"] = json.dumps(
            {
                "status": outcome["status"],
                "side_effect_state": outcome["side_effect_state"],
                "actual_price": outcome.get("actual_price"),
                "submit_intent_at": "2026-07-22T00:00:00+00:00",
                "submit_clicked_at": "2026-07-22T00:00:01+00:00",
                "readback_observed_at": "2026-07-22T00:00:02+00:00",
                "error_code": outcome.get("error_code", ""),
                "error_message": outcome.get("error_message", ""),
                "retryable": False,
            },
            ensure_ascii=False,
        )

    namespace.update(
        {
            "_write_phase": lambda *args, **kwargs: None,
            "_as_int": lambda request, key, default, minimum=1: int(request.get(key, default)),
            "_get_arg": lambda container, key, default=None: container.get(key, default),
            "_get_or_open_and_prepare_window": lambda *args, **kwargs: (prepared_window, {}),
            "sleep": lambda *args, **kwargs: None,
            "_recover_login_if_needed": lambda *args, **kwargs: None,
            "_commit_v4_prepare_product_list": lambda *args, **kwargs: None,
            "_commit_v4_prepare_first_target_for_click": lambda *args, **kwargs: {
                "status": "SUCCESS",
                "direction": "none",
                "attempts": [],
            },
            "_commit_v4_scan_target_rows": lambda window, timeout, supplied: (
                page_rows,
                {item["source_task_id"]: dict(rows[item["source_task_id"]]) for item in supplied},
            ),
            "_multi_product_text": lambda value: str(value or "").strip().casefold(),
            "_multi_product_grade": lambda value: str(value or "").strip().upper(),
            "_multi_product_utc_now": lambda: "2026-07-22T00:00:00+00:00",
            "_run_single_product_flow": stable_single_product_flow,
            "_now_iso": lambda: "2026-07-22T00:00:00+00:00",
            "_set_result": lambda args, result: result,
        }
    )
    request = {
        "contract_version": 4,
        "batch_id": "BATCH-T12-ORCHESTRATION-0001",
        "manifest_sha256": "sha256:" + "a" * 64,
        "task_id": "BATCHTASK-ORCHESTRATION-0001",
        "operation_id": "OP-ORCHESTRATION-0001",
        "execution_attempt_id": "ATTEMPT-ORCHESTRATION-0001",
        "instruction_hash": "sha256:" + "b" * 64,
        "execution_mode": "COMMIT",
        "platform_name": "蚂蚁花团供应商",
        "items": items,
        "capture_evidence": False,
    }
    result = {"task_id": request["task_id"], "side_effect_state": "NOT_STARTED"}
    output = namespace["_run_commit_batch_v4"]({}, request, result)
    return output, calls


def test_v4_sorts_live_page_rows_and_keeps_skipped_middle_positions():
    aisha = _item("TASK-AISHA-B", "AISHA-B-60-Z", "艾莎", "B级", "26.30", "26.40")
    cappuccino = _item(
        "TASK-CAPPUCCINO-B",
        "CAPPUCCINO-B-60-Z",
        "卡布奇诺",
        "B级",
        "46.30",
        "46.40",
    )
    rows = {
        "TASK-AISHA-B": {"position": 4, "parent_index": 49, "price": "26.30"},
        "TASK-CAPPUCCINO-B": {"position": 1, "parent_index": 1, "price": "46.30"},
    }
    outcomes = {
        task_id: {
            "status": "VERIFIED",
            "side_effect_state": "VERIFIED",
            "actual_price": target,
        }
        for task_id, target in (
            ("TASK-AISHA-B", "26.40"),
            ("TASK-CAPPUCCINO-B", "46.40"),
        )
    }

    result, calls = _execute([aisha, cappuccino], rows, outcomes)

    assert result["preflight_page_order"] == ["TASK-CAPPUCCINO-B", "TASK-AISHA-B"]
    assert [call["task_id"] for call in calls] == ["TASK-CAPPUCCINO-B", "TASK-AISHA-B"]
    assert [call["page_position_hint"] for call in calls] == [1, 4]
    assert calls[0]["product_keyword"] == "B级卡布奇诺"
    assert calls[1]["reuse_product_list"] is True
    assert all(call["batch_preflight_reuse"] is True for call in calls)
    assert all(call["_prepared_window_reused"] is True for call in calls)
    assert all(call["_batch_preflight_validated"] is True for call in calls)
    assert result["batch_performance"]["prepared_window_reused"] is True
    assert result["batch_status"] == "VERIFIED"
    assert result["counts"]["verified"] == 2
    assert result["page_snapshot"]["total_count"] == 2
    assert [product["price"] for product in result["page_snapshot"]["products"]] == [
        "26.40",
        "46.40",
    ]


def test_v4_returns_every_page_product_and_overlays_committed_prices():
    cappuccino = _item(
        "TASK-CAPPUCCINO-B",
        "CAPPUCCINO-B-60-Z",
        "卡布奇诺",
        "B级",
        "46.30",
        "46.40",
    )
    rows = {
        "TASK-CAPPUCCINO-B": {
            "position": 1,
            "parent_index": 1,
            "price": "46.30",
            "inventory": 7,
        },
        "NON-TARGET-AISHA-B": {
            "position": 2,
            "parent_index": 17,
            "name": "艾莎",
            "grade": "B级",
            "price": "26.30",
            "inventory": 4,
            "listing_status": "ONLINE",
        },
    }
    outcomes = {
        "TASK-CAPPUCCINO-B": {
            "status": "VERIFIED",
            "side_effect_state": "VERIFIED",
            "actual_price": "46.40",
        }
    }

    result, calls = _execute([cappuccino], rows, outcomes)

    assert len(calls) == 1
    assert result["page_snapshot"]["total_count"] == 2
    by_identity = {
        (product["product_name"], product["grade"]): product
        for product in result["page_snapshot"]["products"]
    }
    assert by_identity[("卡布奇诺", "B级")]["price"] == "46.40"
    assert by_identity[("卡布奇诺", "B级")]["price_status"] == "VERIFIED_AFTER_COMMIT"
    assert by_identity[("艾莎", "B级")]["price"] == "26.30"
    assert by_identity[("艾莎", "B级")]["inventory"] == 4


def test_v4_stops_on_unknown_and_leaves_later_items_not_attempted():
    first = _item("TASK-1", "SKU-1", "卡布奇诺", "B级", "46.30", "46.40")
    second = _item("TASK-2", "SKU-2", "艾莎", "B级", "26.30", "26.40")
    third = _item("TASK-3", "SKU-3", "艾莎", "D级", "15.20", "15.30")
    rows = {
        "TASK-1": {"position": 1, "parent_index": 1, "price": "46.30"},
        "TASK-2": {"position": 4, "parent_index": 49, "price": "26.30"},
        "TASK-3": {"position": 5, "parent_index": 65, "price": "15.20"},
    }
    outcomes = {
        "TASK-1": {
            "status": "VERIFIED",
            "side_effect_state": "VERIFIED",
            "actual_price": "46.40",
        },
        "TASK-2": {
            "status": "SIDE_EFFECT_UNKNOWN",
            "side_effect_state": "UNKNOWN",
            "actual_price": None,
            "error_code": "SUBMIT_RESULT_UNKNOWN",
        },
    }

    result, calls = _execute([third, second, first], rows, outcomes)

    assert [call["task_id"] for call in calls] == ["TASK-1", "TASK-2"]
    assert result["batch_status"] == "PARTIAL"
    assert result["counts"] == {
        "total": 3,
        "attempted": 2,
        "verified": 1,
        "not_applied": 0,
        "failed": 0,
        "unknown": 1,
        "not_attempted": 1,
    }
    by_task = {item["source_task_id"]: item for item in result["items"]}
    assert by_task["TASK-3"]["status"] == "NOT_ATTEMPTED"
    assert by_task["TASK-3"]["submit_attempted"] is False


def test_v4_old_price_change_blocks_the_whole_queue_before_submit():
    first = _item("TASK-1", "SKU-1", "卡布奇诺", "B级", "46.30", "46.40")
    second = _item("TASK-2", "SKU-2", "艾莎", "B级", "26.30", "26.40")
    rows = {
        "TASK-1": {"position": 1, "parent_index": 1, "price": "46.31"},
        "TASK-2": {"position": 4, "parent_index": 49, "price": "26.30"},
    }

    result, calls = _execute([second, first], rows, {})

    assert calls == []
    assert result["batch_status"] == "FAILED"
    assert result["error_code"] == "OLD_PRICE_CHANGED"
    assert result["counts"]["failed"] == 1
    assert result["counts"]["not_attempted"] == 1
    assert result["counts"]["attempted"] == 0
