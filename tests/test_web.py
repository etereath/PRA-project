from __future__ import annotations

import unittest

from app.web import (
    _resolve_table_path,
    default_dashboard_state,
    default_execution_state,
    default_table_editor_state,
    render_dashboard_page,
    render_execution_page,
    render_table_editor_page,
)


class WebTests(unittest.TestCase):
    def test_render_dashboard_page_contains_console_title(self) -> None:
        html = render_dashboard_page(
            params=default_dashboard_state(),
            message="ok",
            message_level="success",
            validation_summary=None,
            generation_summary=None,
            preview_ready=False,
        )
        self.assertIn("\u7ba1\u7406\u53f0", html)
        self.assertIn("\u4efb\u52a1\u751f\u6210", html)

    def test_render_table_editor_contains_management_ui(self) -> None:
        html = render_table_editor_page(
            params=default_table_editor_state(),
            headers=["internal_sku", "product_name"],
            records=[{"internal_sku": "SKU-001", "product_name": "rose"}],
            message="ok",
            message_level="success",
            table_issues=[],
        )
        self.assertIn("Excel", html)
        self.assertIn("\u4fdd\u5b58\u5f53\u524d\u4fee\u6539", html)
        self.assertIn("\u5185\u90e8 SKU", html)
        self.assertIn("\u5546\u54c1\u540d\u79f0", html)
        self.assertIn("SKU-001", html)

    def test_render_table_editor_marks_invalid_cells(self) -> None:
        html = render_table_editor_page(
            params=default_table_editor_state(),
            headers=["internal_sku", "product_name"],
            records=[{"internal_sku": "SKU-001", "product_name": ""}],
            message="error",
            message_level="error",
            table_issues=[(2, "product_name", "\u8be5\u5b57\u6bb5\u5fc5\u586b")],
        )
        self.assertIn("cell-input invalid", html)
        self.assertIn("\u8be5\u5b57\u6bb5\u5fc5\u586b", html)

    def test_switching_table_uses_new_default_path_when_old_default_was_posted(self) -> None:
        resolved = _resolve_table_path(
            table_name="listing_rules",
            previous_table_name="products",
            posted_path="D:/PRA project/data/samples/products.xlsx",
        )
        self.assertTrue(resolved.endswith("data\\samples\\listing_rules.xlsx"))

    def test_render_execution_page_contains_form(self) -> None:
        html = render_execution_page(
            params=default_execution_state(),
            message="ok",
            message_level="success",
            execution_summary=None,
        )
        self.assertIn("\u6a21\u62df\u6267\u884c", html)
        self.assertIn("mock_executor", html)


if __name__ == "__main__":
    unittest.main()
