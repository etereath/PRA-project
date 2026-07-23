from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from app.exceptions import ValidationError
from app.repositories.workbook_repository import save_table_records
from app.services.shadowbot_commit_batch import (
    build_commit_manifest,
    build_commit_request,
    compute_instruction_hash,
    load_identity_mapping,
    validate_request,
)
from app.services.shadowbot_executor import ShadowBotFileQueueRunner


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _flow_function_source(name: str) -> str:
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


class ShadowBotCommitBatchContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = {
            "AISHA-B-60-Z": {
                "expected_product_name": "艾莎",
                "expected_grade": "B级",
            },
            "CAPPUCCINO-B-60-Z": {
                "expected_product_name": "卡布奇诺",
                "expected_grade": "B级",
            },
        }
        self.items = [
            {
                "source_task_id": "TASK-AISHA-B-001",
                "internal_sku": "AISHA-B-60-Z",
                "expected_old_price": "26.30",
                "target_price": "26.40",
            },
            {
                "source_task_id": "TASK-CAPPUCCINO-B-001",
                "internal_sku": "CAPPUCCINO-B-60-Z",
                "expected_old_price": "46.30",
                "target_price": "46.40",
            },
        ]

    def build_manifest(self, **overrides):
        arguments = {
            "batch_id": "BATCH-T12-001",
            "task_items": self.items,
            "identity_mapping": self.mapping,
            "platform_name": "蚂蚁花团供应商",
        }
        arguments.update(overrides)
        return build_commit_manifest(**arguments)

    def build_request(self, manifest, *, profile="production", **overrides):
        arguments = {
            "execution_profile": profile,
            "batch_task_id": "TASK-BATCH-T12-001",
            "operation_id": "OPERATION-T12-001",
            "execution_attempt_id": "ATTEMPT-T12-001",
            "applet_uri": "wx-applet://supplier",
        }
        arguments.update(overrides)
        return build_commit_request(manifest, **arguments)

    def build_read_result(self):
        return {
            "contract_version": 2,
            "execution_mode": "READ_ONLY",
            "status": "READ_COMPLETED",
            "side_effect_state": "NOT_STARTED",
            "platform_name": "蚂蚁花团供应商",
            "read_batch_id": "READ-BATCH-T12-PLAN-001",
            "execution_attempt_id": "ATTEMPT-T12-PLAN-READ-001",
            "result_id": "RESULT-T12-PLAN-READ-001",
            "request_file_sha256": "a" * 64,
            "product_snapshots": [
                {
                    "item_id": "READ-CAPPUCCINO-B",
                    "platform": "蚂蚁花团供应商",
                    "product_name": "卡布奇诺",
                    "grade": "B级",
                    "price": "46.30",
                    "listing_status": "ONLINE",
                    "item_status": "SUCCESS",
                    "locator_summary": "parent_index=1",
                    "row_identity": "parent-index:1",
                    "observed_at": "2026-07-22T05:18:41+08:00",
                },
                {
                    "item_id": "READ-AISHA-B",
                    "platform": "蚂蚁花团供应商",
                    "product_name": "艾莎",
                    "grade": "B级",
                    "price": "26.30",
                    "listing_status": "ONLINE",
                    "item_status": "SUCCESS",
                    "locator_summary": "parent_index=49",
                    "row_identity": "parent-index:49",
                    "observed_at": "2026-07-22T05:18:41+08:00",
                },
            ],
        }

    def test_mapping_uses_internal_sku_and_validates_platform(self) -> None:
        payload = {
            "schema_version": "shadowbot-product-identity-mapping-1.0",
            "platform_name": "蚂蚁花团供应商",
            "mappings": [
                {
                    "internal_sku": "AISHA-B-60-Z",
                    "expected_product_name": "艾莎",
                    "expected_grade": "B级",
                    "status": "active",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mapping.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            mapping = load_identity_mapping(path, expected_platform_name="蚂蚁花团供应商")
            self.assertIn("AISHA-B-60-Z", mapping)
            with self.assertRaisesRegex(ValidationError, "平台"):
                load_identity_mapping(path, expected_platform_name="其他平台")

    def test_inventory_workbook_is_the_commit_identity_mapping_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "products.xlsx"
            save_table_records(
                "products",
                path,
                [
                    {
                        "internal_sku": "CAPPUCCINO-E-45-Z",
                        "product_name": "卡布奇诺",
                        "grade": "E",
                        "stem_length": "45",
                        "unit": "扎",
                        "base_cost": "10.00",
                        "current_stock": 1,
                        "sale_enabled": True,
                    }
                ],
            )

            mapping = load_identity_mapping(
                path,
                expected_platform_name="蚂蚁花团供应商",
            )

        self.assertEqual(
            mapping["CAPPUCCINO-E-45-Z"],
            {
                "expected_product_name": "卡布奇诺",
                "expected_grade": "E",
            },
        )

    def test_formal_items_use_task_fields_and_have_no_input_ordinal(self) -> None:
        manifest = self.build_manifest()

        first = manifest["items"][0]
        self.assertEqual(first["source_task_id"], "TASK-AISHA-B-001")
        self.assertEqual(first["internal_sku"], "AISHA-B-60-Z")
        self.assertEqual(first["expected_product_name"], "艾莎")
        self.assertNotIn("ordinal", first)
        self.assertNotIn("listing_status_id", first)
        self.assertNotIn("read_batch_id", first)

    def test_manifest_hash_does_not_bind_input_order(self) -> None:
        original = self.build_manifest()
        reversed_manifest = self.build_manifest(task_items=list(reversed(self.items)))

        self.assertEqual(original["manifest_sha256"], reversed_manifest["manifest_sha256"])

    def test_manifest_hash_binds_source_task_sku_and_prices(self) -> None:
        original = self.build_manifest()
        changed_items = [dict(item) for item in self.items]
        changed_items[0]["target_price"] = "26.50"
        changed = self.build_manifest(task_items=changed_items)

        self.assertNotEqual(original["manifest_sha256"], changed["manifest_sha256"])

    def test_production_request_needs_no_user_confirmation(self) -> None:
        request = self.build_request(self.build_manifest())

        validate_request(request)
        self.assertEqual(request["execution_profile"], "production")
        self.assertNotIn("development_confirmation", request)

    def test_development_request_requires_exact_confirmation(self) -> None:
        manifest = self.build_manifest()
        with self.assertRaisesRegex(ValidationError, "确认文本"):
            self.build_request(manifest, profile="development")

        request = self.build_request(
            manifest,
            profile="development",
            confirmation_text=manifest["development_confirmation_text"],
            confirmed_by="project-owner",
        )
        validate_request(request)
        self.assertEqual(request["development_confirmation"]["confirmed_by"], "project-owner")

    def test_production_rejects_development_confirmation(self) -> None:
        with self.assertRaisesRegex(ValidationError, "不得携带"):
            self.build_request(
                self.build_manifest(),
                confirmation_text="unexpected",
                confirmed_by="unexpected",
            )

    def test_development_request_can_bind_controlled_unknown_fault(self) -> None:
        manifest = self.build_manifest()
        request = self.build_request(
            manifest,
            profile="development",
            confirmation_text=manifest["development_confirmation_text"],
            confirmed_by="project-owner",
            fault_injection="AFTER_SUBMIT_CLICK_UNKNOWN",
        )

        validate_request(request)
        self.assertEqual(
            request["fault_injection"],
            "AFTER_SUBMIT_CLICK_UNKNOWN",
        )
        self.assertEqual(request["instruction_hash"], compute_instruction_hash(request))

    def test_production_request_rejects_fault_injection(self) -> None:
        with self.assertRaisesRegex(ValidationError, "不得携带故障注入"):
            self.build_request(
                self.build_manifest(),
                fault_injection="AFTER_SUBMIT_CLICK_UNKNOWN",
            )

    def test_snapshot_and_page_fields_are_rejected(self) -> None:
        invalid = [dict(self.items[0], page_position=1)]
        with self.assertRaisesRegex(ValidationError, "非正式输入字段"):
            self.build_manifest(task_items=invalid)

        request = self.build_request(self.build_manifest())
        request["snapshot_version"] = 3
        request["instruction_hash"] = compute_instruction_hash(request)
        with self.assertRaisesRegex(ValidationError, "非合同字段"):
            validate_request(request)

    def test_duplicate_page_identity_fails_before_request_creation(self) -> None:
        duplicate_mapping = {
            "AISHA-B-60-Z": self.mapping["AISHA-B-60-Z"],
            "CAPPUCCINO-B-60-Z": self.mapping["AISHA-B-60-Z"],
        }
        with self.assertRaisesRegex(ValidationError, "同一平台页面身份"):
            self.build_manifest(identity_mapping=duplicate_mapping)

    def test_item_payload_tampering_is_rejected(self) -> None:
        request = self.build_request(self.build_manifest())
        request["items"][0]["expected_old_price"] = "26.31"
        request["instruction_hash"] = compute_instruction_hash(request)

        with self.assertRaisesRegex(ValidationError, "payload 哈希不匹配"):
            validate_request(request)

    def test_file_queue_publishes_entire_batch_as_one_request(self) -> None:
        request = self.build_request(self.build_manifest())
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_dir = Path(temp_dir) / "queue"
            result = ShadowBotFileQueueRunner(queue_dir).start(request)
            ready_files = list((queue_dir / "inbox").glob("*.ready.json"))
            self.assertEqual(len(ready_files), 1)
            written = json.loads(ready_files[0].read_text(encoding="utf-8"))
            self.assertEqual(len(written["items"]), 2)
            self.assertEqual(result.raw_output["instruction_hash"], request["instruction_hash"])

    def test_worker_batch_only_adds_page_planning_before_stable_commit(self) -> None:
        source = _flow_function_source("_run_commit_batch_v4")

        self.assertIn("_commit_v4_scan_target_rows", source)
        self.assertIn("_commit_v4_prepare_first_target_for_click", source)
        self.assertIn("plan = sorted", source)
        self.assertIn("_run_single_product_flow(stable_args)", source)
        self.assertNotIn("_open_price_dialog", source)
        self.assertNotIn("_fill_target_price", source)
        self.assertNotIn("_confirm_price_dialog", source)
        self.assertNotIn("_commit_v4_scroll_cached_row_into_view", source)

    def test_planned_item_reuses_dynamic_row_and_verified_commit_controls(self) -> None:
        source = _flow_function_source("_commit_v4_stable_request")

        self.assertIn('"page_position_hint": int(row["position"])', source)
        self.assertIn('"reuse_product_list": True', source)
        self.assertIn('"batch_preflight_reuse": True', source)
        self.assertIn('"final_save_required": False', source)
        self.assertIn('"fast_post_submit_verify": True', source)


if __name__ == "__main__":
    unittest.main()
