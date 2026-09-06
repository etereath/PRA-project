from __future__ import annotations

import unittest

from app.services.shadowbot_product_read import (
    build_read_batch_id,
    CONTRACT_VERSION,
    ProductCandidate,
    ProductReadContractError,
    ProductTarget,
    aggregate_product_snapshots,
    fingerprint_has_no_progress,
    normalize_multi_product_request,
    normalize_inventory,
    normalize_price,
    resolve_product_match,
    stable_viewport_fingerprint,
    validate_evidence_binding,
)


def _request(products, **overrides):
    payload = {
        "contract_version": CONTRACT_VERSION,
        "execution_mode": "READ_ONLY",
        "read_batch_id": "READ-BATCH-TEST-001",
        "products": products,
    }
    payload.update(overrides)
    return payload


def _product(item_id="ITEM-001", *, sku="SKU-001", name="艾莎", grade="B", platform="ant_flower_wechat"):
    return {
        "item_id": item_id,
        "platform": platform,
        "platform_sku": sku,
        "expected_product_name": name,
        "expected_grade": grade,
    }


class ProductReadContractTests(unittest.TestCase):
    def test_core_owned_batch_id_is_safe_and_nonempty(self):
        batch_id = build_read_batch_id()
        self.assertRegex(batch_id, r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")

    def test_request_normalizes_optional_sku_and_limits(self):
        result = normalize_multi_product_request(
            _request([_product(sku=" ")], limits={"max_pages": 2, "max_scrolls": 3, "max_seconds": 4})
        )
        self.assertIsNone(result["products"][0]["platform_sku"])
        self.assertEqual(result["limits"], {"max_pages": 2, "max_scrolls": 3, "max_seconds": 4})
        self.assertFalse(result["capture_evidence"])
        self.assertTrue(normalize_multi_product_request(_request([_product()], capture_evidence=True))["capture_evidence"])

    def test_read_only_allows_no_product_targets_when_platform_is_explicit(self):
        normalized = normalize_multi_product_request(
            _request([], platform_name="ant_flower_wechat")
        )

        self.assertEqual(normalized["execution_mode"], "READ_ONLY")
        self.assertEqual(normalized["platform_name"], "ant_flower_wechat")
        self.assertEqual(normalized["products"], [])

    def test_request_rejects_non_read_only_and_duplicate_identity(self):
        with self.assertRaisesRegex(ProductReadContractError, "READ_ONLY_REQUIRED"):
            normalize_multi_product_request(_request([_product()], execution_mode="COMMIT"))
        with self.assertRaisesRegex(ProductReadContractError, "READ_ONLY_REQUIRED"):
            normalize_multi_product_request(_request([_product()], execution_mode="PRE_COMMIT"))
        with self.assertRaisesRegex(ProductReadContractError, "DUPLICATE_TARGET_IDENTITY"):
            normalize_multi_product_request(_request([_product(), _product("ITEM-002")]))

    def test_request_rejects_multiple_platforms_and_hard_limits(self):
        with self.assertRaisesRegex(ProductReadContractError, "SINGLE_PLATFORM_REQUIRED"):
            normalize_multi_product_request(_request([_product(), _product("ITEM-002", platform="other")]))
        with self.assertRaisesRegex(ProductReadContractError, "MAX_PAGES_LIMIT_EXCEEDED"):
            normalize_multi_product_request(_request([_product()], limits={"max_pages": 101}))

    def test_sku_matching_is_exact_and_ambiguous_is_not_auto_resolved(self):
        target = ProductTarget("ITEM-001", "ant_flower_wechat", "SKU-001", "艾莎", "B")
        candidate = ProductCandidate("ant_flower_wechat", "sku-001", "艾莎", "B", "8.50", "ONLINE")
        self.assertIs(resolve_product_match(target, [candidate]), candidate)
        ambiguous = ProductCandidate("ant_flower_wechat", "SKU-001", "艾莎", "B", "9.00", "OFFLINE")
        self.assertEqual(resolve_product_match(target, [candidate, ambiguous]), "AMBIGUOUS_MATCH")

    def test_name_and_grade_matching_normalizes_text(self):
        target = ProductTarget("ITEM-001", "ant_flower_wechat", None, " 艾莎 ", "b")
        candidate = ProductCandidate("ant_flower_wechat", None, "艾莎", "B", "8.50", "ONLINE")
        self.assertIs(resolve_product_match(target, [candidate]), candidate)
        self.assertEqual(resolve_product_match(target, []), "PRODUCT_NOT_FOUND")

    def test_viewport_fingerprint_ignores_volatile_fields(self):
        first = ProductCandidate("ant_flower_wechat", "SKU-001", "艾莎", "B", "8.50", "ONLINE", "row-1")
        changed = ProductCandidate("ant_flower_wechat", "SKU-001", "艾莎", "B", "9.50", "OFFLINE", "row-99")
        other = ProductCandidate("ant_flower_wechat", "SKU-002", "卡罗拉", "A", "7.00", "ONLINE", "row-2")
        self.assertEqual(stable_viewport_fingerprint([first]), stable_viewport_fingerprint([changed]))
        self.assertNotEqual(stable_viewport_fingerprint([first]), stable_viewport_fingerprint([first, other]))
        self.assertTrue(fingerprint_has_no_progress(["a", "a", "a"]))
        self.assertFalse(fingerprint_has_no_progress(["a", "b", "a"]))

    def test_price_parser_rejects_binary_float_and_invalid_values(self):
        self.assertEqual(normalize_price("9.00"), "9.00")
        with self.assertRaisesRegex(ProductReadContractError, "PRICE_PARSE_FAILED"):
            normalize_price(9.0)
        with self.assertRaisesRegex(ProductReadContractError, "PRICE_PARSE_FAILED"):
            normalize_price("NaN")

    def test_inventory_parser_accepts_non_negative_integer_only(self):
        self.assertEqual(normalize_inventory("20"), 20)
        self.assertEqual(normalize_inventory(0), 0)
        with self.assertRaisesRegex(ProductReadContractError, "INVENTORY_PARSE_FAILED"):
            normalize_inventory(1.5)
        with self.assertRaisesRegex(ProductReadContractError, "INVENTORY_PARSE_FAILED"):
            normalize_inventory(-1)

    def test_evidence_binding_requires_item_and_batch_scoped_relative_hash(self):
        evidence = [{
            "evidence_id": "EVD-001",
            "evidence_type": "PRODUCT_READ",
            "relative_path": "READ-BATCH-TEST-001/ITEM-001.png",
            "sha256": "a" * 64,
            "read_batch_id": "READ-BATCH-TEST-001",
            "item_id": "ITEM-001",
            "execution_attempt_id": "ATTEMPT-001",
        }]
        validate_evidence_binding(
            evidence,
            read_batch_id="READ-BATCH-TEST-001",
            item_id="ITEM-001",
            execution_attempt_id="ATTEMPT-001",
        )
        evidence[0]["item_id"] = "ITEM-002"
        with self.assertRaisesRegex(ProductReadContractError, "EVIDENCE_BINDING_FAILED"):
            validate_evidence_binding(
                evidence,
                read_batch_id="READ-BATCH-TEST-001",
                item_id="ITEM-001",
                execution_attempt_id="ATTEMPT-001",
            )

    def test_aggregate_status_and_counts(self):
        result = aggregate_product_snapshots(
            read_batch_id="READ-BATCH-TEST-001",
            contract_version=2,
            started_at="2026-07-19T00:00:00+00:00",
            completed_at="2026-07-19T00:00:01+00:00",
            snapshots=[
                {"item_id": "ITEM-001", "item_status": "SUCCESS", "listing_status": "ONLINE", "error_code": None},
                {"item_id": "ITEM-002", "item_status": "MANUAL_CHECK_REQUIRED", "listing_status": "UNKNOWN", "error_code": "AMBIGUOUS_MATCH"},
            ],
        )
        self.assertEqual(result["overall_status"], "PARTIAL")
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["success_count"] + result["manual_check_count"], 2)


if __name__ == "__main__":
    unittest.main()
