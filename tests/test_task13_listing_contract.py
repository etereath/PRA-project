from __future__ import annotations

from copy import deepcopy
import pytest

from app.exceptions import ValidationError
from app.services.listing_automation_gate import evaluate_automation_gate
from app.services.shadowbot_commit_batch import (
    CONTRACT_VERSION as V4_CONTRACT_VERSION,
)
from app.services.shadowbot_commit_batch import SCHEMA_VERSION as V4_REQUEST_SCHEMA
from app.services.shadowbot_listing_action_contract import (
    build_listing_action_manifest,
    build_listing_action_phase,
    build_listing_action_recovery_result,
    build_listing_action_request,
    compute_listing_instruction_hash,
    compute_listing_phase_hash,
    compute_listing_result_hash,
    required_development_confirmation,
    validate_listing_anomaly,
    validate_listing_action_manifest,
    validate_listing_action_phase,
    validate_listing_action_request,
    validate_listing_action_result,
    validate_listing_sync_snapshot,
)
from app.shadowbot_listing_contract import (
    V5_CONTRACT_VERSION,
    V5_ANOMALY_SCHEMA_VERSION,
    V5_GATE_SUMMARY_SCHEMA_VERSION,
    V5_PHASE_SCHEMA_VERSION,
    V5_RESULT_SCHEMA_VERSION,
    V5_SNAPSHOT_SCHEMA_VERSION,
    derive_automation_disposition,
    derive_listing_location,
    derive_v5_batch_semantics,
    listing_snapshot_is_valid,
    project_online_status,
    v5_result_counts,
)
from app.shadowbot_contract_primitives import (
    contract_identity_key,
    parse_set_offline_confirmation_identity,
    parse_set_online_confirmation_identity,
    set_offline_confirmation_matches,
    set_online_confirmation_matches,
)


PLATFORM = "蚂蚁花团供应商"
EXPIRES_AT = "2027-07-25T12:00:00+08:00"


def test_plain_aisha_and_ten_stem_aisha_are_distinct_page_identities() -> None:
    plain = contract_identity_key(PLATFORM, None, "艾莎", "A级")
    ten_stem = contract_identity_key(PLATFORM, None, "艾莎（10枝）", "A级")

    assert plain == "蚂蚁花团供应商|name:艾莎|grade:A"
    assert ten_stem == "蚂蚁花团供应商|name:艾莎(10枝)|grade:A"
    assert plain != ten_stem


def test_set_online_confirmation_rejects_ten_stem_variant_for_plain_aisha() -> None:
    plain_prompt = "您确定上架【A级 艾莎】吗？"
    ten_stem_prompt = "您确定上架【A级 艾莎（10枝）】吗？"

    assert parse_set_online_confirmation_identity(plain_prompt) == {
        "product_name": "艾莎",
        "grade": "A级",
    }
    assert set_online_confirmation_matches(plain_prompt, "艾莎", "A级")
    assert not set_online_confirmation_matches(
        ten_stem_prompt,
        "艾莎",
        "A级",
    )


def test_set_offline_confirmation_rejects_ten_stem_variant_for_plain_aisha() -> None:
    plain_prompt = "您确定下架【A级 艾莎】吗？"
    ten_stem_prompt = "您确定下架【A级 艾莎（10枝）】吗？"

    assert parse_set_offline_confirmation_identity(plain_prompt) == {
        "product_name": "艾莎",
        "grade": "A级",
    }
    assert set_offline_confirmation_matches(plain_prompt, "艾莎", "A级")
    assert not set_offline_confirmation_matches(
        ten_stem_prompt,
        "艾莎",
        "A级",
    )


@pytest.fixture
def identity_mapping() -> dict[str, dict[str, str]]:
    return {
        "AISHA-B-60-Z": {
            "expected_product_name": "艾莎",
            "expected_grade": "B级",
        },
        "CAPPUCCINO-B-60-Z": {
            "expected_product_name": "卡布奇诺",
            "expected_grade": "B级",
        },
    }


def _online_items() -> list[dict[str, object]]:
    return [
        {
            "source_task_id": "TASK-AISHA-ONLINE-001",
            "internal_sku": "AISHA-B-60-Z",
            "expected_old_status": "offline",
            "target_status": "online",
            "target_price": "26.40",
            "target_inventory": "12",
            "expires_at": EXPIRES_AT,
        },
        {
            "source_task_id": "TASK-CAPPUCCINO-ONLINE-001",
            "internal_sku": "CAPPUCCINO-B-60-Z",
            "expected_old_status": "offline",
            "target_status": "online",
            "target_price": "46.40",
            "target_inventory": "8",
            "expires_at": EXPIRES_AT,
        },
    ]


def _offline_items() -> list[dict[str, object]]:
    return [
        {
            "source_task_id": "TASK-AISHA-OFFLINE-001",
            "internal_sku": "AISHA-B-60-Z",
            "expected_old_status": "online",
            "target_status": "offline",
            "expires_at": EXPIRES_AT,
        }
    ]


def _manifest(
    identity_mapping: dict[str, dict[str, str]],
    *,
    action_type: str = "set_online",
    task_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return build_listing_action_manifest(
        batch_id="BATCH-T13-CONTRACT-001",
        action_type=action_type,
        task_items=(
            _online_items()
            if task_items is None and action_type == "set_online"
            else _offline_items()
            if task_items is None and action_type == "set_offline"
            else task_items
        ),
        identity_mapping=identity_mapping,
        platform_name=PLATFORM,
        mapping_source_version="products-v1",
    )


def _gate_summary(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": V5_GATE_SUMMARY_SCHEMA_VERSION,
        "gate_phase": "PRE_PUBLISH",
        "evaluated_at": "2026-07-25T12:00:00+08:00",
        "items": [
            {
                "internal_sku": item["internal_sku"],
                "operation_id": item["operation_id"],
                "decision": "EXECUTE",
                "lock_status": "ACTIVE",
                "lock_operation_id": item["operation_id"],
                "block_reasons": [],
            }
            for item in manifest["items"]
        ],
    }


def _request(
    manifest: dict[str, object],
    *,
    profile: str = "production",
) -> dict[str, object]:
    action = manifest["action_type"]
    kwargs: dict[str, object] = {
        "execution_profile": profile,
        "execution_attempt_id": "ATTEMPT-T13-CONTRACT-001",
        "applet_uri": "wx-applet://supplier",
    }
    if action in {"set_online", "set_offline"}:
        kwargs.update(
            {
                "batch_task_id": "TASK-BATCH-T13-CONTRACT-001",
                "batch_operation_id": "OPERATION-T13-CONTRACT-001",
                "gate_summary": _gate_summary(manifest),
            }
        )
        if profile == "development":
            kwargs.update(
                {
                    "confirmation_text": required_development_confirmation(
                        str(manifest["batch_id"]),
                        len(manifest["items"]),
                    ),
                    "confirmed_by": "operator:test",
                }
            )
    return build_listing_action_request(manifest, **kwargs)


def test_v4_contract_is_unchanged() -> None:
    assert V4_CONTRACT_VERSION == 4
    assert V4_REQUEST_SCHEMA == "shadowbot-commit-batch-request-1.2"
    assert V5_CONTRACT_VERSION == 5


def test_v5_manifest_hash_is_stable_and_input_order_independent(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    original = _manifest(identity_mapping)
    reversed_manifest = _manifest(
        identity_mapping,
        task_items=list(reversed(_online_items())),
    )

    assert original["manifest_sha256"] == reversed_manifest["manifest_sha256"]
    assert original["action_type"] == "set_online"
    assert original["scan_scope"] == "online_and_waiting"
    assert all("ordinal" not in item for item in original["items"])


def test_v5_manifest_rejects_missing_online_fields_and_offline_fake_fields(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    missing_inventory = _online_items()
    missing_inventory[0].pop("target_inventory")
    with pytest.raises(ValidationError, match="inventory"):
        _manifest(identity_mapping, task_items=missing_inventory)

    offline_with_price = _offline_items()
    offline_with_price[0]["target_price"] = "26.40"
    with pytest.raises(ValidationError, match="SET_OFFLINE"):
        _manifest(
            identity_mapping,
            action_type="set_offline",
            task_items=offline_with_price,
        )


def test_v5_manifest_rejects_mixed_action_and_duplicate_page_identity(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    tampered = _manifest(identity_mapping)
    tampered["items"][0]["action_type"] = "set_offline"
    with pytest.raises(ValidationError, match="一种动作"):
        validate_listing_action_manifest(tampered)

    duplicate_mapping = deepcopy(identity_mapping)
    duplicate_mapping["CAPPUCCINO-B-60-Z"] = {
        "expected_product_name": "艾莎",
        "expected_grade": "B级",
    }
    with pytest.raises(ValidationError, match="同一平台页面身份"):
        _manifest(duplicate_mapping)


def test_v5_production_and_development_requests(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    manifest = _manifest(identity_mapping)
    production = _request(manifest)
    validate_listing_action_request(production)
    assert "development_confirmation" not in production
    assert production["action_type"] == "set_online"
    assert production["items"][0]["item_execution_attempt_id"].startswith(
        "ATTEMPT-"
    )

    development = _request(manifest, profile="development")
    validate_listing_action_request(development)
    assert development["development_confirmation"]["confirmed_by"] == "operator:test"

    with pytest.raises(ValidationError, match="确认文本"):
        build_listing_action_request(
            manifest,
            execution_profile="development",
            execution_attempt_id="ATTEMPT-T13-CONTRACT-002",
            applet_uri="wx-applet://supplier",
            batch_task_id="TASK-BATCH-T13-CONTRACT-002",
            batch_operation_id="OPERATION-T13-CONTRACT-002",
            gate_summary=_gate_summary(manifest),
        )


def test_v5_request_rejects_position_fields_and_blocked_gate(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    request = _request(_manifest(identity_mapping))
    request["items"][0]["row_index"] = 1
    with pytest.raises(ValidationError, match="页面位置"):
        validate_listing_action_request(request)

    manifest = _manifest(identity_mapping)
    blocked_gate = _gate_summary(manifest)
    blocked_gate["items"][0]["decision"] = "BLOCKED"
    blocked_gate["items"][0]["block_reasons"] = ["WRITE_LOCK_ACTIVE"]
    with pytest.raises(ValidationError, match="BLOCKED"):
        build_listing_action_request(
            manifest,
            execution_profile="production",
            execution_attempt_id="ATTEMPT-T13-CONTRACT-003",
            applet_uri="wx-applet://supplier",
            batch_task_id="TASK-BATCH-T13-CONTRACT-003",
            batch_operation_id="OPERATION-T13-CONTRACT-003",
            gate_summary=blocked_gate,
        )


def test_sync_status_request_has_no_write_fields() -> None:
    manifest = build_listing_action_manifest(
        batch_id="BATCH-T13-SYNC-001",
        action_type="sync_status",
        task_items=[],
        identity_mapping=None,
        platform_name=PLATFORM,
        mapping_source_version="products-v1",
    )
    request = _request(manifest)

    validate_listing_action_request(request)
    assert request["execution_mode"] == "READ_ONLY"
    assert request["items"] == []
    assert "task_id" not in request
    assert "operation_id" not in request
    assert "gate_summary" not in request


def test_action_clicked_phase_is_bound_and_hashed(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    request = _request(_manifest(identity_mapping))
    phase = build_listing_action_phase(
        request,
        request_file_sha256="sha256:" + "a" * 64,
        phase_name="ACTION_CLICKED",
        worker_id="worker:test",
        current_item=request["items"][0],
        clicked_at="2026-07-25T12:02:00+08:00",
        detail_effect_state="VERIFIED",
        listing_effect_state="UNKNOWN",
    )

    assert phase["schema_version"] == V5_PHASE_SCHEMA_VERSION
    assert phase["phase_snapshot_sha256"] == compute_listing_phase_hash(phase)
    validate_listing_action_phase(
        request,
        phase,
        request_file_sha256="sha256:" + "a" * 64,
    )

    phase["current_item"]["internal_sku"] = "OTHER-SKU"
    with pytest.raises(ValidationError, match="绑定"):
        validate_listing_action_phase(
            request,
            phase,
            request_file_sha256="sha256:" + "a" * 64,
        )


def test_v5_phase_item_states_cover_complete_batch_and_recovery_matrix(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    request = _request(_manifest(identity_mapping))
    states = []
    for item in request["items"]:
        states.append(
            {
                **{
                    name: item[name]
                    for name in (
                        "source_task_id",
                        "operation_id",
                        "item_execution_attempt_id",
                        "internal_sku",
                        "item_payload_sha256",
                    )
                },
                "operation_result": "NOT_ATTEMPTED",
                "detail_effect_state": "VERIFIED",
                "listing_effect_state": "NOT_STARTED",
                "detail_save_clicked": True,
                "action_confirm_clicked": False,
                "detail_save_clicked_at": "2026-07-25T12:02:00+08:00",
                "action_clicked_at": None,
                "readback_observed_at": None,
                "error_code": "",
                "error_message": "",
            }
        )
    phase = build_listing_action_phase(
        request,
        request_file_sha256="sha256:" + "a" * 64,
        phase_name="ACTION_INTENT_RECORDED",
        worker_id="worker:test",
        current_item=request["items"][1],
        item_states=states,
        detail_effect_state="VERIFIED",
        listing_effect_state="NOT_STARTED",
    )

    recovered = build_listing_action_recovery_result(
        request,
        phase,
        request_file_sha256="sha256:" + "a" * 64,
        recovered_at="2026-07-25T12:03:00+08:00",
        worker_id="worker:test",
    )

    assert [item["operation_result"] for item in recovered["items"]] == [
        "PARTIALLY_APPLIED",
        "NEEDS_RECONCILIATION",
    ]
    assert recovered["counts"]["partial_effect_count"] == 1
    assert recovered["counts"]["unknown_count"] == 1
    assert recovered["batch_status"] == "UNKNOWN"
    assert recovered["side_effect_state"] == "UNKNOWN"
    validate_listing_action_result(
        recovered,
        request=request,
        request_file_sha256="sha256:" + "a" * 64,
    )


def test_v5_phase_rejects_incomplete_item_state_ledger(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    request = _request(_manifest(identity_mapping))
    state = {
        **{
            name: request["items"][0][name]
            for name in (
                "source_task_id",
                "operation_id",
                "item_execution_attempt_id",
                "internal_sku",
                "item_payload_sha256",
            )
        },
        "operation_result": "NOT_ATTEMPTED",
        "detail_effect_state": "NOT_STARTED",
        "listing_effect_state": "NOT_STARTED",
        "detail_save_clicked": False,
        "action_confirm_clicked": False,
    }

    with pytest.raises(ValidationError, match="完整批次"):
        build_listing_action_phase(
            request,
            request_file_sha256="sha256:" + "a" * 64,
            phase_name="PREFLIGHT_VALIDATED",
            worker_id="worker:test",
            current_item=request["items"][0],
            item_states=[state],
        )


def test_v5_instruction_hash_detects_request_tampering(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    request = _request(_manifest(identity_mapping))

    assert request["instruction_hash"] == compute_listing_instruction_hash(request)
    request["capture_evidence"] = not request["capture_evidence"]
    with pytest.raises(ValidationError, match="HASH"):
        validate_listing_action_request(request)


def test_v5_controlled_unknown_is_bound_to_development_offline_ordinal(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    manifest = _manifest(identity_mapping, action_type="set_offline")
    confirmation = required_development_confirmation(
        str(manifest["batch_id"]),
        len(manifest["items"]),
    )
    request = build_listing_action_request(
        manifest,
        execution_profile="development",
        execution_attempt_id="ATTEMPT-T13-FAULT-001",
        applet_uri="wx-applet://supplier",
        batch_task_id="TASK-BATCH-T13-FAULT-001",
        batch_operation_id="OPERATION-T13-FAULT-001",
        gate_summary=_gate_summary(manifest),
        confirmation_text=confirmation,
        confirmed_by="operator:test",
        fault_injection="AFTER_ACTION_CLICK_UNKNOWN",
        fault_injection_item_ordinal=1,
    )

    assert request["fault_injection"] == "AFTER_ACTION_CLICK_UNKNOWN"
    assert request["fault_injection_item_ordinal"] == 1
    validate_listing_action_request(request)

    tampered = deepcopy(request)
    tampered["fault_injection_item_ordinal"] = 2
    with pytest.raises(ValidationError, match="商品序号"):
        validate_listing_action_request(tampered)

    with pytest.raises(ValidationError, match="只允许开发阶段 SET_OFFLINE"):
        build_listing_action_request(
            manifest,
            execution_profile="production",
            execution_attempt_id="ATTEMPT-T13-FAULT-002",
            applet_uri="wx-applet://supplier",
            batch_task_id="TASK-BATCH-T13-FAULT-002",
            batch_operation_id="OPERATION-T13-FAULT-002",
            gate_summary=_gate_summary(manifest),
            fault_injection="AFTER_ACTION_CLICK_UNKNOWN",
            fault_injection_item_ordinal=1,
        )


@pytest.mark.parametrize(
    ("online", "waiting", "ambiguous", "expected"),
    [
        (1, 0, False, "online_only"),
        (0, 1, False, "waiting_only"),
        (1, 1, False, "both"),
        (0, 0, False, "neither"),
        (2, 0, False, "ambiguous"),
        (1, 0, True, "ambiguous"),
    ],
)
def test_listing_location_matrix(
    online: int,
    waiting: int,
    ambiguous: bool,
    expected: str,
) -> None:
    assert (
        derive_listing_location(
            online,
            waiting,
            mapping_ambiguous=ambiguous,
        )
        == expected
    )


def test_listing_location_projection_and_snapshot_validity() -> None:
    assert project_online_status("online_only") == "online"
    assert project_online_status("waiting_only") == "offline"
    assert project_online_status("both") == "online"
    assert project_online_status("neither") == "offline"
    assert project_online_status("ambiguous", "online") == "online"
    assert listing_snapshot_is_valid(
        snapshot_complete=True,
        scan_started_at="2026-07-25T12:00:00+08:00",
        last_listing_change_at="2026-07-25T11:59:59+08:00",
    )
    assert not listing_snapshot_is_valid(
        snapshot_complete=True,
        scan_started_at="2026-07-25T11:00:00+08:00",
        last_listing_change_at="2026-07-25T12:00:00+08:00",
    )
    assert (
        derive_automation_disposition(listing_location="online_only")
        == "actionable"
    )
    assert (
        derive_automation_disposition(
            listing_location="online_only",
            has_blocking_write_lock=True,
        )
        == "manual_review"
    )
    assert (
        derive_automation_disposition(listing_location="both")
        == "manual_review"
    )


def test_v5_count_identity_and_unknown_priority() -> None:
    items = [
        {
            "operation_result": "VERIFIED",
            "action_confirm_clicked": True,
        },
        {
            "operation_result": "PARTIALLY_APPLIED",
            "detail_save_clicked": True,
        },
        {
            "operation_result": "NEEDS_RECONCILIATION",
            "action_confirm_clicked": True,
        },
        {
            "operation_result": "NOT_ATTEMPTED",
        },
        {
            "operation_result": "NOT_APPLIED",
        },
    ]
    counts = v5_result_counts(items)
    semantics = derive_v5_batch_semantics(counts)

    assert counts["batch_target_count"] == 5
    assert (
        counts["verified_count"]
        + counts["unknown_count"]
        + counts["partial_effect_count"]
        + counts["not_attempted_count"]
        + counts["failed_count"]
        == 5
    )
    assert semantics["batch_status"] == "UNKNOWN"
    assert semantics["partial_effect_count"] == 1
    assert semantics["requires_manual_review"] is True


@pytest.mark.parametrize(
    ("outcomes", "expected_status"),
    [
        (["NOT_ATTEMPTED"], "FAILED"),
        (["NOT_APPLIED", "NOT_ATTEMPTED"], "FAILED"),
        (["VERIFIED", "NOT_ATTEMPTED"], "PARTIAL"),
        (["VERIFIED", "NEEDS_RECONCILIATION"], "UNKNOWN"),
        (["PARTIALLY_APPLIED", "NOT_ATTEMPTED"], "PARTIAL"),
    ],
)
def test_v5_shared_batch_terminal_semantics_matrix(
    outcomes: list[str],
    expected_status: str,
) -> None:
    counts = v5_result_counts(
        [{"operation_result": outcome} for outcome in outcomes]
    )

    assert derive_v5_batch_semantics(counts)["batch_status"] == expected_status


def test_v5_result_validator_uses_shared_semantics(
    identity_mapping: dict[str, dict[str, str]],
) -> None:
    request = _request(_manifest(identity_mapping))
    request_file_sha256 = "sha256:" + "b" * 64
    items = []
    for index, request_item in enumerate(request["items"]):
        items.append(
            {
                "source_task_id": request_item["source_task_id"],
                "operation_id": request_item["operation_id"],
                "item_execution_attempt_id": request_item[
                    "item_execution_attempt_id"
                ],
                "internal_sku": request_item["internal_sku"],
                "item_payload_sha256": request_item["item_payload_sha256"],
                "operation_result": (
                    "VERIFIED" if index == 0 else "ALREADY_APPLIED"
                ),
                "action_confirm_clicked": index == 0,
            }
        )
    counts = v5_result_counts(items)
    semantics = derive_v5_batch_semantics(counts)
    result = {
        "schema_version": V5_RESULT_SCHEMA_VERSION,
        "contract_version": V5_CONTRACT_VERSION,
        "action_type": "set_online",
        "execution_mode": "COMMIT",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": request_file_sha256,
        "started_at": "2026-07-25T12:01:00+08:00",
        "ended_at": "2026-07-25T12:02:00+08:00",
        "batch_status": semantics["batch_status"],
        "items": items,
        "counts": counts,
        **semantics,
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)

    validate_listing_action_result(
        result,
        request=request,
        request_file_sha256=request_file_sha256,
    )
    assert result["batch_status"] == "VERIFIED"

    tampered_binding = deepcopy(result)
    tampered_binding["items"][0]["internal_sku"] = "OTHER-SKU"
    tampered_binding["result_payload_sha256"] = compute_listing_result_hash(
        tampered_binding
    )
    with pytest.raises(ValidationError, match="BINDING"):
        validate_listing_action_result(
            tampered_binding,
            request=request,
            request_file_sha256=request_file_sha256,
        )

    tampered_hash = deepcopy(result)
    tampered_hash["ended_at"] = "2026-07-25T12:03:00+08:00"
    with pytest.raises(ValidationError, match="HASH"):
        validate_listing_action_result(
            tampered_hash,
            request=request,
            request_file_sha256=request_file_sha256,
        )


def test_snapshot_contract_requires_two_complete_pages() -> None:
    snapshot = {
        "schema_version": V5_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": "SNAPSHOT-T13-001",
        "platform_name": PLATFORM,
        "execution_attempt_id": "ATTEMPT-T13-SYNC-001",
        "mapping_source_version": "products-v1",
        "result_id": "RESULT-T13-SYNC-001",
        "scan_started_at": "2026-07-25T12:00:00+08:00",
        "scan_completed_at": "2026-07-25T12:00:10+08:00",
        "online_scan_started_at": "2026-07-25T12:00:00+08:00",
        "online_scan_completed_at": "2026-07-25T12:00:04+08:00",
        "waiting_scan_started_at": "2026-07-25T12:00:05+08:00",
        "waiting_scan_completed_at": "2026-07-25T12:00:10+08:00",
        "online_scan_complete": True,
        "waiting_scan_complete": True,
        "online_end_marker_verified": True,
        "waiting_end_marker_verified": True,
        "snapshot_complete": True,
        "instruction_hash": "sha256:" + "c" * 64,
        "status": "VERIFIED",
        "error_code": None,
        "evidence_manifest_sha256": "sha256:" + "d" * 64,
        "items": [
            {
                "snapshot_item_id": "SNAPSHOT-ITEM-T13-001",
                "internal_sku": "AISHA-B-60-Z",
                "product_name": "艾莎",
                "grade": "B级",
                "page_identity_key": "page:aisha-b",
                "affected_internal_skus": ["AISHA-B-60-Z"],
                "online_occurrences": 1,
                "waiting_occurrences": 0,
                "mapping_ambiguous": False,
                "listing_location": "online_only",
                "online_row_identities": ["online:row:1"],
                "waiting_row_identities": [],
                "online_observed_price": "26.40",
                "waiting_observed_price": None,
                "online_observed_inventory": "12",
                "waiting_observed_inventory": None,
                "online_observed_at": "2026-07-25T12:00:03+08:00",
                "waiting_observed_at": None,
                "diagnostic_code": "",
            }
        ],
    }

    validate_listing_sync_snapshot(snapshot)
    snapshot["waiting_end_marker_verified"] = False
    with pytest.raises(ValidationError, match="COMPLETENESS"):
        validate_listing_sync_snapshot(snapshot)


def test_listing_anomaly_contract_supports_unmapped_and_multi_sku_conflict() -> None:
    unmapped = {
        "schema_version": V5_ANOMALY_SCHEMA_VERSION,
        "anomaly_case_id": "ANOMALY-T13-UNMAPPED-001",
        "snapshot_id": "SNAPSHOT-T13-001",
        "snapshot_item_id": "SNAPSHOT-ITEM-T13-UNMAPPED-001",
        "platform_name": PLATFORM,
        "internal_sku": None,
        "page_identity_key": "page:unknown-b",
        "affected_internal_skus": [],
        "anomaly_subject_key": "subject:page:unknown-b",
        "dedupe_key": "dedupe:unmapped:unknown-b",
        "reason_code": "UNMAPPED_PRODUCT",
        "diagnostic_message": "库存录入中不存在该页面商品。",
        "resolution_policy": "MANUAL_ONLY",
        "blocked_actions": ["update_price", "set_online", "set_offline"],
        "review_task_id": None,
        "created_at": "2026-07-25T12:00:00+08:00",
        "cleared_at": None,
        "cleared_by_snapshot_id": None,
    }
    validate_listing_anomaly(unmapped)

    conflict = deepcopy(unmapped)
    conflict.update(
        {
            "anomaly_case_id": "ANOMALY-T13-CONFLICT-001",
            "snapshot_item_id": "SNAPSHOT-ITEM-T13-CONFLICT-001",
            "page_identity_key": "page:aisha-a",
            "affected_internal_skus": ["AISHA-A-50-Z", "AISHA-A-60-Z"],
            "anomaly_subject_key": "subject:sku-set:aisha-a",
            "dedupe_key": "dedupe:identity-conflict:aisha-a",
            "reason_code": "IDENTITY_MAPPING_CONFLICT",
            "diagnostic_message": "同一页面身份对应多个内部 SKU。",
        }
    )
    validate_listing_anomaly(conflict)

    conflict["affected_internal_skus"] = ["AISHA-A-60-Z"]
    with pytest.raises(ValidationError, match="多个受影响 SKU"):
        validate_listing_anomaly(conflict)


def test_update_price_first_stage_gate_does_not_require_snapshot() -> None:
    result = evaluate_automation_gate(
        action_type="update_price",
        internal_sku="AISHA-B-60-Z",
        gate_phase="PRE_PUBLISH",
        online_status="online",
    )

    assert result.decision == "EXECUTE"


def test_gate_blocks_reviews_and_all_blocking_lock_states() -> None:
    for status in ("ACTIVE", "UNKNOWN", "REVIEW_BLOCKED"):
        result = evaluate_automation_gate(
            action_type="set_offline",
            internal_sku="AISHA-B-60-Z",
            gate_phase="PRE_PUBLISH",
            online_status="online",
            listing_location="online_only",
            write_locks=[
                {
                    "status": status,
                    "operation_id": "OPERATION-OTHER-001",
                }
            ],
        )
        assert result.decision == "BLOCKED"

    review_result = evaluate_automation_gate(
        action_type="set_online",
        internal_sku="AISHA-B-60-Z",
        gate_phase="PRE_PUBLISH",
        listing_location="waiting_only",
        open_reviews=[
            {
                "blocked_actions": [
                    "update_price",
                    "set_online",
                    "set_offline",
                ],
                "reason_code": "PRESENT_IN_BOTH_LISTS",
            }
        ],
    )
    assert review_result.decision == "BLOCKED"
    assert (
        "PRESENT_IN_BOTH_LISTS"
        in review_result.block_reasons_by_action["set_online"]
    )


@pytest.mark.parametrize(
    ("lock_status", "expected_reason"),
    [
        ("ACTIVE", "WRITE_LOCK_ACTIVE"),
        ("UNKNOWN", "OPERATION_RECONCILIATION_PENDING"),
        ("REVIEW_BLOCKED", "PARTIAL_OPERATION_REVIEW_PENDING"),
    ],
)
def test_same_sku_write_lock_is_shared_across_all_write_actions(
    lock_status: str,
    expected_reason: str,
) -> None:
    action_contexts = {
        "update_price": {
            "online_status": "online",
        },
        "set_online": {
            "listing_location": "waiting_only",
            "snapshot_valid": True,
            "fresh_sync_required": True,
            "target_price": "26.40",
            "target_inventory": 12,
        },
        "set_offline": {
            "online_status": "online",
            "listing_location": "online_only",
            "snapshot_valid": True,
        },
    }

    for lock_origin_action in action_contexts:
        shared_lock = {
            "status": lock_status,
            "operation_id": f"OPERATION-{lock_origin_action.upper()}-001",
            "origin_action_type": lock_origin_action,
        }
        for requested_action, context in action_contexts.items():
            result = evaluate_automation_gate(
                action_type=requested_action,
                internal_sku="AISHA-B-60-Z",
                gate_phase="PRE_PUBLISH",
                write_locks=[shared_lock],
                **context,
            )

            assert result.decision == "BLOCKED"
            assert expected_reason in result.block_reasons_by_action[requested_action]


def test_post_publish_gate_only_allows_own_active_lock() -> None:
    own = evaluate_automation_gate(
        action_type="set_offline",
        internal_sku="AISHA-B-60-Z",
        gate_phase="POST_PUBLISH_PREFLIGHT",
        online_scan_complete=True,
        online_occurrences=1,
        requesting_operation_id="OPERATION-T13-001",
        write_locks=[
            {"status": "ACTIVE", "operation_id": "OPERATION-T13-001"}
        ],
    )
    other = evaluate_automation_gate(
        action_type="set_offline",
        internal_sku="AISHA-B-60-Z",
        gate_phase="POST_PUBLISH_PREFLIGHT",
        online_scan_complete=True,
        online_occurrences=1,
        requesting_operation_id="OPERATION-T13-001",
        write_locks=[
            {"status": "ACTIVE", "operation_id": "OPERATION-T13-OTHER"}
        ],
    )

    assert own.decision == "EXECUTE"
    assert other.decision == "BLOCKED"


def test_set_offline_ignores_invalidated_historical_location() -> None:
    result = evaluate_automation_gate(
        action_type="set_offline",
        internal_sku="AISHA-A-70-Z",
        gate_phase="PRE_PUBLISH",
        online_status="online",
        listing_location="waiting_only",
        snapshot_valid=False,
        online_scan_complete=True,
        online_occurrences=0,
    )

    assert result.decision == "EXECUTE"


def test_set_online_and_set_offline_action_specific_gate() -> None:
    waiting = evaluate_automation_gate(
        action_type="set_online",
        internal_sku="AISHA-B-60-Z",
        gate_phase="PRE_PUBLISH",
        listing_location="waiting_only",
        snapshot_valid=True,
        fresh_sync_required=True,
        target_price="26.40",
        target_inventory=12,
    )
    already_online = evaluate_automation_gate(
        action_type="set_online",
        internal_sku="AISHA-B-60-Z",
        gate_phase="PRE_PUBLISH",
        listing_location="online_only",
        snapshot_valid=True,
        fresh_sync_required=True,
        observed_price="26.40",
        observed_inventory=12,
        target_price="26.40",
        target_inventory=12,
    )
    mismatch = evaluate_automation_gate(
        action_type="set_online",
        internal_sku="AISHA-B-60-Z",
        gate_phase="PRE_PUBLISH",
        listing_location="online_only",
        snapshot_valid=True,
        fresh_sync_required=True,
        observed_price="26.30",
        observed_inventory=12,
        target_price="26.40",
        target_inventory=12,
    )
    already_offline = evaluate_automation_gate(
        action_type="set_offline",
        internal_sku="AISHA-B-60-Z",
        gate_phase="PRE_PUBLISH",
        snapshot_valid=True,
        online_scan_complete=True,
        online_occurrences=0,
    )

    assert waiting.decision == "EXECUTE"
    assert already_online.decision == "ALREADY_APPLIED"
    assert mismatch.decision == "BLOCKED"
    assert already_offline.decision == "ALREADY_APPLIED"


def test_set_online_pre_publish_defers_missing_page_fact_to_worker_preflight() -> None:
    result = evaluate_automation_gate(
        action_type="set_online",
        internal_sku="AISHA-E-45-Z",
        gate_phase="PRE_PUBLISH",
        online_status="",
        listing_location=None,
        snapshot_valid=False,
        fresh_sync_required=False,
        target_price="7.00",
        target_inventory=1,
    )

    assert result.decision == "EXECUTE"
    assert result.block_reasons_by_action == {}
