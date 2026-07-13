"""Regression coverage for selector constraints that change across mini-program sessions."""

from __future__ import annotations

import ast
import re
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
