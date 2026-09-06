from __future__ import annotations

import ast
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


class _SliceError(Exception):
    def __init__(self, code, message, retryable):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _load_reader():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "_order_normalize_qty",
        "_order_normalize_amount",
        "_order_calculate_transaction_amount",
        "_order_normalize_created_at",
        "_order_scoped_element_text",
        "_order_indexed_children_from_grade_anchor",
        "_order_read_rows",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    observed = iter(
        (
            "2026-07-10T09:44:10+00:00",
            "2026-07-10T09:44:11+00:00",
        )
    )
    namespace = {
        "Decimal": Decimal,
        "InvalidOperation": InvalidOperation,
        "SliceError": _SliceError,
        "datetime": datetime,
        "re": re,
        "ORDER_ROW_INDEX_STEP": 9,
        "_now_iso": lambda: next(observed),
        "_order_row_anchor_collection_selector": lambda: "grade-anchors",
    }
    module = ast.Module(body=nodes, type_ignores=[])
    exec(
        compile(ast.fix_missing_locations(module), str(FLOW_PATH), "exec"),
        namespace,
    )
    return namespace["_order_read_rows"]


class _Element:
    def __init__(self, text="", children=None, fail_if_read=False):
        self._text = text
        self._children = list(children or [])
        self._parent = None
        self._fail_if_read = fail_if_read
        for child in self._children:
            child._parent = self

    def get_text(self):
        if self._fail_if_read:
            raise AssertionError("non-allow-listed order field was read")
        return self._text

    def get_value(self):
        if self._fail_if_read:
            raise AssertionError("non-allow-listed order field was read")
        return ""

    def children(self):
        if self._fail_if_read:
            raise AssertionError("non-allow-listed order field was traversed")
        return list(self._children)

    def parent(self):
        return self._parent


def _field(text):
    return _Element(children=[_Element(text=text)])


def _indexed_order_list(*rows):
    blocked = lambda: _Element(fail_if_read=True)
    children = []
    anchors = []
    for grade, name, qty, price, created_at in rows:
        order_children = [
            blocked(),
            blocked(),
            _field(grade),
            _field(name),
            blocked(),
            _field(qty),
            _field(price),
            _field(created_at),
            blocked(),
        ]
        children.extend(order_children)
        anchors.append(order_children[2]._children[0])
    indexed_list = _Element(children=children)
    return indexed_list, anchors


class _Window:
    def __init__(self, anchors):
        self.anchors = anchors
        self.find_all_calls = 0

    def find_all(self, selector, timeout):
        assert selector == "grade-anchors"
        assert timeout == 5
        self.find_all_calls += 1
        return list(self.anchors)


def test_order_reader_uses_one_anchor_collection_and_frozen_child_indexes():
    _, anchors = _indexed_order_list(
        (
            "B级",
            "艾莎",
            "数量 1扎",
            "¥12.00",
            "下单时间：2026-07-10 17:44:10",
        ),
        (
            "C级",
            "卡布奇诺",
            "数量 2扎",
            "¥9.50",
            "下单时间：2026-07-10 17:45:11",
        ),
    )
    window = _Window(anchors)

    rows = _load_reader()(window, 5, 20)

    assert window.find_all_calls == 1
    assert rows == [
        {
            "order_created_at": "2026-07-10 17:44:10",
            "platform_product_name": "艾莎",
            "grade": "B级",
            "order_qty": "1",
            "order_transaction_amount": "12.00",
            "observed_at": "2026-07-10T09:44:10+00:00",
        },
        {
            "order_created_at": "2026-07-10 17:45:11",
            "platform_product_name": "卡布奇诺",
            "grade": "C级",
            "order_qty": "2",
            "order_transaction_amount": "19.00",
            "observed_at": "2026-07-10T09:44:11+00:00",
        },
    ]


def test_order_reader_rejects_anchor_and_index_two_mismatch():
    indexed_list, anchors = _indexed_order_list(
        (
            "B级",
            "艾莎",
            "数量 1扎",
            "¥12.00",
            "下单时间：2026-07-10 17:44:10",
        ),
    )
    indexed_list._children[2], indexed_list._children[3] = (
        indexed_list._children[3],
        indexed_list._children[2],
    )

    with pytest.raises(_SliceError) as raised:
        _load_reader()(_Window(anchors), 5, 20)

    assert raised.value.code == "ORDER_CARD_INDEX_MISMATCH"


def test_order_reader_rejects_an_incomplete_card():
    indexed_list, anchors = _indexed_order_list(
        (
            "B级",
            "艾莎",
            "数量 1扎",
            "¥12.00",
            "下单时间：2026-07-10 17:44:10",
        ),
    )
    indexed_list._children = indexed_list._children[:7]

    with pytest.raises(_SliceError) as raised:
        _load_reader()(_Window(anchors), 5, 20)

    assert raised.value.code == "ORDER_LIST_STRUCTURE_MISMATCH"
