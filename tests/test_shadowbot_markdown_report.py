from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.shadowbot_markdown_report import (
    MarkdownReportError,
    render_formal_boundary_markdown,
    write_formal_boundary_markdown,
)


def _payload() -> dict[str, object]:
    return {
        "generated_at": "2026-07-19T15:33:27+00:00",
        "overall_status": "PASSED",
        "execution_mode": "READ_ONLY",
        "platform_name": "蚂蚁花团供应商",
        "run_identity": {
            "task_id": "TASK-T11",
            "execution_attempt_id": "ATTEMPT-T11",
            "operation_id": "READ-OP-T11",
            "read_batch_id": "READ-BATCH-T11",
            "shadowbot_run_id": "filequeue:ATTEMPT-T11",
        },
        "sort_change": {
            "sort_rule": "等级优先",
            "before_order": "卡布奇诺 B级 → 艾莎 B级",
            "after_order": "卡布奇诺 B级 → 艾莎 B级 → 艾莎 C级",
            "observed_order": "卡布奇诺 B级 → 艾莎 B级 → 艾莎 C级",
        },
        "test_results": [
            {
                "product_name": "卡布奇诺",
                "grade": "B级",
                "item_status": "SUCCESS",
                "inventory": 1,
                "price": "24.00",
                "listing_status": "ONLINE",
                "position": 1,
                "row_identity": "parent-index:1",
                "platform_sku": "SKU-CAPPUCCINO-B",
                "evidence": [
                    {
                        "evidence_id": "EVD-T11-CAPPUCCINO",
                        "upload_status": "SUCCESS",
                        "hash_verified": True,
                    }
                ],
            },
            {
                "product_name": "不存在的花",
                "grade": "B级",
                "item_status": "FAILED",
                "error_code": "PRODUCT_NOT_FOUND",
                "position": 2,
                "row_identity": "-",
                "platform_sku": "SKU-NOT-FOUND",
                "evidence": [],
            },
        ],
        "count_identity": {
            "total_count": 2,
            "processed_count": 2,
            "success_count": 1,
            "failed_count": 1,
            "skipped_count": 0,
            "manual_check_count": 0,
            "formula": "2 = 1 + 1 + 0 + 0",
            "passed": True,
        },
        "database_readback": {
            "readback_passed": True,
            "attempt_status": "READ_COMPLETED",
            "execution_mode": "READ_ONLY",
            "execution_log_count": 1,
            "execution_log_success": True,
            "result_id": "RESULT-T11",
            "request_hash_matches": True,
            "result_hash_recorded": True,
            "read_only_note": "只读回读",
        },
        "validation_passed": True,
        "side_effect_started": False,
        "final_queue_state": {
            "heartbeat_status": "STOPPED",
            "inbox_empty": True,
            "working_empty": True,
            "results_empty": True,
            "stop_signal_present": False,
        },
        "encoding_check": {
            "json_question_marks": 0,
            "replacement_characters": 0,
        },
    }


class ShadowBotMarkdownReportTests(unittest.TestCase):
    def test_render_contains_boundary_sections_and_utf8_text(self):
        markdown = render_formal_boundary_markdown(_payload())
        self.assertIn("# 任务11实机测试报告", markdown)
        self.assertIn("## 逐商品读取结果与证据", markdown)
        self.assertIn("卡布奇诺", markdown)
        self.assertIn("不存在的花", markdown)
        self.assertIn("PRODUCT_NOT_FOUND", markdown)
        self.assertIn("数据库回读", markdown)
        self.assertIn("计数恒等式", markdown)
        self.assertIn("EVD-T11-CAPPUCCINO", markdown)
        self.assertNotIn("DUPLICATE_TARGET_IDENTITY", markdown)
        self.assertNotIn("\ufffd", markdown)

    def test_write_round_trips_explicit_utf8(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "acceptance.json"
            output = root / "reports" / "acceptance.md"
            source.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
            written = write_formal_boundary_markdown(source, output)
            self.assertEqual(written, output)
            content = output.read_text(encoding="utf-8")
            self.assertEqual(content.count("?"), 0)
            self.assertNotIn("\ufffd", content)

    def test_rejects_missing_boundary_sections(self):
        payload = _payload()
        payload.pop("database_readback")
        with self.assertRaises(MarkdownReportError):
            render_formal_boundary_markdown(payload)
