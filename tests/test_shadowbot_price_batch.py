from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.services.shadowbot_price_batch import (
    CONTRACT_VERSION,
    PRICE_BATCH_ERROR_CODES,
    WRITE_LOCK_STATES,
    PriceBatchContractError,
    aggregate_batch_counts,
    build_page_identity_key,
    build_task11_source_binding,
    canonical_json_bytes,
    compute_normalized_request_digest,
    normalize_price_batch_request,
    normalize_price_string,
    sha256_jcs,
)


NOW = datetime(2026, 7, 20, 8, 12, tzinfo=timezone.utc)


def _task11_files():
    products = [
        {
            "item_id": "READ-ITEM-001",
            "platform": "蚂蚁花团供应商",
            "platform_sku": None,
            "expected_product_name": "艾莎",
            "expected_grade": "B级",
        },
        {
            "item_id": "READ-ITEM-002",
            "platform": "蚂蚁花团供应商",
            "platform_sku": None,
            "expected_product_name": "卡布奇诺",
            "expected_grade": "C级",
        },
    ]
    request = {
        "contract_version": 2,
        "read_batch_id": "READ-BATCH-TASK12-001",
        "platform_name": "蚂蚁花团供应商",
        "applet_uri": "weixin://launchapplet/?app_id=example",
        "window_title": "蚂蚁花团供应商",
        "products": products,
    }
    result = {
        "contract_version": 2,
        "read_batch_id": request["read_batch_id"],
        "platform_name": request["platform_name"],
        "request_file_sha256": "a" * 64,
        "total_count": 2,
        "success_count": 2,
        "failed_count": 0,
        "skipped_count": 0,
        "manual_check_count": 0,
        "product_snapshots": [
            {
                "item_id": "READ-ITEM-001",
                "platform": "蚂蚁花团供应商",
                "product_name": "艾莎",
                "grade": "B级",
                "price": "8.60",
                "inventory": 12,
                "listing_status": "ONLINE",
                "observed_at": "2026-07-20T08:10:00Z",
            },
            {
                "item_id": "READ-ITEM-002",
                "platform": "蚂蚁花团供应商",
                "product_name": "卡布奇诺",
                "grade": "C级",
                "price": "9.20",
                "inventory": 7,
                "listing_status": "ONLINE",
                "observed_at": "2026-07-20T08:10:01Z",
            },
        ],
    }
    manifest = {
        "archive_verified": True,
        "request_file_sha256": "sha256:" + "a" * 64,
        "result_file_sha256": "sha256:" + "b" * 64,
        "phase_file_sha256": "sha256:" + "c" * 64,
    }
    return request, result, manifest


def _source_binding():
    return build_task11_source_binding(
        *_task11_files(),
        platform_key="ant_flower_wechat",
        accepted_platform_names=("蚂蚁花团供应商",),
        now=NOW,
    )


def _build_source(request, result, manifest, *, now):
    return build_task11_source_binding(
        request,
        result,
        manifest,
        platform_key="ant_flower_wechat",
        accepted_platform_names=("蚂蚁花团供应商",),
        now=now,
    )


def _batch_request(source=None):
    source = source or _source_binding()
    return {
        "contract_version": CONTRACT_VERSION,
        "batch_id": "PRICE-BATCH-TASK12-001",
        "platform": "ant_flower_wechat",
        "batch_type": "SERIAL_PRICE_UPDATE",
        "execution_mode": "FILL_PREVIEW",
        "stop_policy": "PAUSE_ON_UNCERTAIN",
        "capture_evidence": False,
        "source_read_batch_id": source["source_read_batch_id"],
        "source_snapshot_sha256": source["source_snapshot_sha256"],
        "source_page_context_sha256": source["source_page_context_sha256"],
        "source_observed_at": source["source_observed_at"],
        "source_snapshot_max_age_seconds": 300,
        "items": [
            {
                "item_id": "ITEM-001",
                "ordinal": 1,
                "source_item_id": "READ-ITEM-001",
                "task_id": "TASK-001",
                "review_task_id": "REVIEW-001",
                "operation_id": "OP-001",
                "approved_payload_hash": "sha256:" + "d" * 64,
                "platform_sku": None,
                "expected_product_name": "艾莎",
                "expected_grade": "B级",
                "approved_expected_old_price": "8.60",
                "target_price": "8.80",
            },
            {
                "item_id": "ITEM-002",
                "ordinal": 2,
                "source_item_id": "READ-ITEM-002",
                "task_id": "TASK-002",
                "review_task_id": "REVIEW-002",
                "operation_id": "OP-002",
                "approved_payload_hash": "sha256:" + "e" * 64,
                "platform_sku": None,
                "expected_product_name": "卡布奇诺",
                "expected_grade": "C级",
                "approved_expected_old_price": "9.20",
                "target_price": "9.50",
            },
        ],
    }


def _assert_code(code, callable_):
    with pytest.raises(PriceBatchContractError) as caught:
        callable_()
    assert caught.value.code == code


def test_error_codes_and_write_lock_states_are_frozen():
    assert "SOURCE_SNAPSHOT_EXPIRED" in PRICE_BATCH_ERROR_CODES
    assert "SOURCE_PAGE_CONTEXT_HASH_MISMATCH" in PRICE_BATCH_ERROR_CODES
    assert WRITE_LOCK_STATES == {
        "PENDING",
        "STARTING",
        "RUNNING",
        "SUBMIT_INTENT_RECORDED",
        "SUBMIT_CLICKED",
        "UNKNOWN",
        "NEEDS_RECONCILIATION",
    }


def test_jcs_profile_is_deterministic_and_rejects_binary_float():
    left = {"艾莎": [1, True, None], "a": "x"}
    right = {"a": "x", "艾莎": [1, True, None]}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_jcs(left) == sha256_jcs(right)
    with pytest.raises(TypeError, match="binary floats"):
        canonical_json_bytes({"price": 8.6})


def test_source_binding_uses_real_task11_request_result_and_sidecar_fields():
    binding = _source_binding()
    assert binding["source_read_batch_id"] == "READ-BATCH-TASK12-001"
    assert binding["platform"] == "ant_flower_wechat"
    assert binding["source_observed_at"] == "2026-07-20T08:10:00Z"
    assert binding["page_context"]["applet_identity_sha256"].startswith("sha256:")
    assert "account_context_hash" not in binding["page_context"]
    assert list(binding["source_items"]) == ["READ-ITEM-001", "READ-ITEM-002"]


def test_source_binding_replays_checked_in_task11_real_machine_artifact():
    root = Path("docs/reports/artifacts/task11/coverage-r1")
    request_path = next(root.glob("*.request.json"))
    result_path = next(root.glob("*.result.json"))
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    validation = json.loads((root / "validation.json").read_text(encoding="utf-8-sig"))
    manifest = {
        "archive_verified": validation["all_pass"],
        "request_file_sha256": validation["request_sha256"],
        "result_file_sha256": validation["result_sha256"],
    }
    completed_at = datetime.fromisoformat(result["ended_at"].replace("Z", "+00:00"))
    binding = _build_source(request, result, manifest, now=completed_at)
    assert binding["platform"] == "ant_flower_wechat"
    assert len(binding["source_items"]) == 5
    assert binding["source_request_file_sha256"] == "sha256:" + validation["request_sha256"]


def test_source_binding_rejects_missing_context_unverified_archive_and_expiry():
    request, result, manifest = _task11_files()
    request["window_title"] = ""
    _assert_code(
        "SOURCE_CONTEXT_UNAVAILABLE",
        lambda: _build_source(request, result, manifest, now=NOW),
    )
    request, result, manifest = _task11_files()
    manifest["archive_verified"] = False
    _assert_code(
        "SOURCE_ARCHIVE_NOT_VERIFIED",
        lambda: _build_source(request, result, manifest, now=NOW),
    )
    request, result, manifest = _task11_files()
    _assert_code(
        "SOURCE_SNAPSHOT_EXPIRED",
        lambda: _build_source(request, result, manifest, now=NOW + timedelta(minutes=6)),
    )


def test_price_batch_normalizes_prices_and_binds_source_items():
    source = _source_binding()
    request = _batch_request(source)
    request["items"][0]["target_price"] = "8.8"
    normalized = normalize_price_batch_request(request, source_binding=source, now=NOW)
    assert normalized["items"][0]["target_price"] == "8.80"
    assert normalized["items"][0]["page_identity_key"].startswith("sha256:")
    assert normalized["normalized_request_digest"] == compute_normalized_request_digest(normalized)


def test_price_batch_rejects_float_and_more_than_two_fraction_digits():
    _assert_code(
        "INVALID_PRICE_TYPE",
        lambda: normalize_price_string(8.8, error_code="TARGET_PRICE_INVALID"),
    )
    _assert_code(
        "TARGET_PRICE_INVALID",
        lambda: normalize_price_string("8.801", error_code="TARGET_PRICE_INVALID"),
    )


def test_price_batch_rejects_duplicate_page_identity_even_with_different_sku():
    source = _source_binding()
    request = _batch_request(source)
    request["items"][1].update(
        {
            "source_item_id": "READ-ITEM-001",
            "expected_product_name": "艾莎",
            "expected_grade": "B级",
            "platform_sku": "OTHER-SKU",
        }
    )
    _assert_code(
        "DUPLICATE_PAGE_IDENTITY",
        lambda: normalize_price_batch_request(request, source_binding=source, now=NOW),
    )


def test_request_digest_changes_when_item_order_changes():
    source = _source_binding()
    first = normalize_price_batch_request(_batch_request(source), source_binding=source, now=NOW)
    reordered_request = _batch_request(source)
    reordered_request["items"] = list(reversed(reordered_request["items"]))
    for ordinal, item in enumerate(reordered_request["items"], start=1):
        item["ordinal"] = ordinal
    reordered = normalize_price_batch_request(reordered_request, source_binding=source, now=NOW)
    assert first["normalized_request_digest"] != reordered["normalized_request_digest"]


def test_request_rejects_source_hash_and_identity_mismatch():
    source = _source_binding()
    request = _batch_request(source)
    request["source_snapshot_sha256"] = "sha256:" + "0" * 64
    _assert_code(
        "SOURCE_SNAPSHOT_HASH_MISMATCH",
        lambda: normalize_price_batch_request(request, source_binding=source, now=NOW),
    )
    request = _batch_request(source)
    request["items"][0]["expected_grade"] = "A级"
    _assert_code(
        "SOURCE_ITEM_IDENTITY_MISMATCH",
        lambda: normalize_price_batch_request(request, source_binding=source, now=NOW),
    )


def test_provided_digest_must_match_and_batch_id_is_excluded():
    source = _source_binding()
    request = _batch_request(source)
    normalized = normalize_price_batch_request(request, source_binding=source, now=NOW)
    replay = deepcopy(request)
    replay["batch_id"] = "PRICE-BATCH-TASK12-999"
    replay_normalized = normalize_price_batch_request(replay, source_binding=source, now=NOW)
    assert normalized["normalized_request_digest"] == replay_normalized["normalized_request_digest"]
    request["normalized_request_digest"] = "sha256:" + "0" * 64
    _assert_code(
        "NORMALIZED_REQUEST_DIGEST_MISMATCH",
        lambda: normalize_price_batch_request(request, source_binding=source, now=NOW),
    )


def test_batch_count_equations_exclude_reconcile_diagnostic_count():
    counts = aggregate_batch_counts(
        ["PREVIEWED", "VERIFIED", "FAILED", "SKIPPED", "CANCELLED", "NEEDS_RECONCILIATION"]
    )
    assert counts["processed_count"] == 6
    assert counts["total_count"] == 6
    assert "reconciled_item_count" not in counts


def test_page_identity_reuses_task11_normalization():
    assert build_page_identity_key(platform="ANT", product_name=" 艾莎 ", grade="b级") == build_page_identity_key(
        platform="ant", product_name="艾莎", grade="B级"
    )
