from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.emergency_offline_fence import (
    EmergencyOfflineFenceError,
    build_emergency_authorization_binding,
    revalidate_emergency_offline_facts,
)
from app.enums import TaskActionType, TaskOriginType, TaskStatus
from app.exceptions import ValidationError
from app.models import ListingStatus, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.emergency_offline_authorization import (
    EMERGENCY_EVENT_TYPE,
    EmergencyOfflineAuthorizationService,
)
from app.services.emergency_offline_shadow import EmergencyOfflineShadowDecision
from app.services.shadowbot_listing_action_contract import (
    build_listing_action_request,
    compute_listing_result_hash,
)
from app.services.shadowbot_listing_action_pipeline import (
    _persist_prepared_write_batch,
    import_listing_action_result,
    propose_listing_action_batch,
)
from app.services.shadowbot_listing_sync import (
    import_listing_sync_result,
    prepare_listing_sync_batch,
)
from app.shadowbot_contract_primitives import contract_identity_key
from app.shadowbot_listing_contract import derive_v5_batch_semantics, v5_result_counts
from shadowbot.test2 import shadowbot_queue_worker

NOW = datetime(2026, 8, 3, 2, tzinfo=timezone.utc)


class _Shadow:
    def __init__(self, decision: EmergencyOfflineShadowDecision) -> None:
        self.decision = decision

    def evaluate(self, **kwargs) -> EmergencyOfflineShadowDecision:
        return self.decision


def _decision() -> EmergencyOfflineShadowDecision:
    return EmergencyOfflineShadowDecision(
        evaluation_mode="SHADOW",
        incident_id="INCIDENT-1",
        review_task_id="REVIEW-1",
        eligible_without_feature_flag=True,
        authorization_eligible=False,
        automatic_allowlisted=True,
        blockers=("FEATURE_FLAG_DISABLED",),
        severity="S4",
        price_reason="EXTREME_PRICE_AT_OR_BELOW_80_PERCENT_OF_BASE_COST",
        policy_version="POLICY-1",
        policy_canonical_sha256="a" * 64,
        emergency_ratio=Decimal("0.80"),
        base_cost=Decimal("10.00"),
        base_cost_source_ref="products.xlsx:sha256:test",
        emergency_threshold=Decimal("8.0000"),
        second_observed_price=Decimal("8.00"),
        first_observation_id="OBS-1",
        second_observation_id="OBS-2",
        pulse_run_id="PULSE-RUN-2",
        mapping_version="mapping-v1",
    )


def _repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    _seed_authorization_facts(repository)
    return repository


def _seed_authorization_facts(repository: SQLiteRuntimeRepository) -> None:
    now = NOW.isoformat()
    sent_at = (NOW - timedelta(minutes=20)).isoformat()
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO automation_jobs(
                job_id, job_type, display_name, enabled, schedule_kind,
                schedule_expression, priority, config_json, created_at, updated_at
            ) VALUES
                ('PULSE-JOB', 'ONLINE_PULSE', 'pulse', 1, 'INTERVAL_MINUTES',
                 '10', 60, '{"platform_name":"platform"}', ?, ?),
                ('EMERGENCY-FLAG', 'SYSTEM_EMERGENCY_SET_OFFLINE', 'emergency',
                 1, 'CHILD_ONLY', '', 10,
                 '{"automatic_emergency_offline":true,"platform_name":"platform"}',
                 ?, ?)
            """,
            (now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO automation_runs(
                run_id, job_id, job_type, logical_run_key, run_status,
                platform_name, platform_trade_date, seller_operation_date,
                seller_phase, time_policy_version, scheduled_for, started_at,
                finished_at, lease_owner, lease_version, lease_expires_at,
                input_manifest_sha256, output_manifest_sha256,
                error_code, error_message, created_at, updated_at
            ) VALUES (
                'PULSE-RUN-2', 'PULSE-JOB', 'ONLINE_PULSE', 'pulse-run-2',
                'SUCCESS', 'platform', '2026-08-03', '2026-08-03',
                'NORMAL_SALES', 'CN_SINGLE_PLATFORM_2026_V1', ?, ?, ?, '', 0,
                NULL, '', 'sha256:pulse', '', '', ?, ?
            )
            """,
            (now, now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO product_observation_batches(
                observation_batch_id, automation_run_id, platform_name,
                scan_type, batch_status, scan_started_at, scan_completed_at,
                requested_scope_json, scope_complete, end_marker_verified,
                content_sha256, time_policy_version, error_code, error_message,
                created_at
            ) VALUES
                ('BATCH-1', 'PULSE-RUN-2', 'platform', 'LISTING_STATUS_SCAN',
                 'ACCEPTED', ?, ?, '{}', 1, 1, 'sha256:batch1',
                 'CN_SINGLE_PLATFORM_2026_V1', '', '', ?),
                ('BATCH-2', 'PULSE-RUN-2', 'platform', 'LISTING_STATUS_SCAN',
                 'ACCEPTED', ?, ?, '{}', 1, 1, 'sha256:batch2',
                 'CN_SINGLE_PLATFORM_2026_V1', '', '', ?)
            """,
            (sent_at, sent_at, sent_at, now, now, now),
        )
        for observation_id, batch_id, price, observed_at in (
            ("OBS-1", "BATCH-1", "7.90", sent_at),
            ("OBS-2", "BATCH-2", "8.00", now),
        ):
            connection.execute(
                """
                INSERT INTO product_observation_items(
                    observation_item_id, observation_batch_id, internal_sku,
                    platform_product_name, grade, observed_price,
                    observed_inventory, observed_online, observed_at,
                    platform_trade_date, seller_operation_date, seller_phase,
                    page_identity_key, mapping_status, mapping_version,
                    evidence_sha256
                ) VALUES (?, ?, 'SKU-1', 'product', 'B', ?, 5, 1, ?,
                          '2026-08-03', '2026-08-03', 'NORMAL_SALES',
                          'platform|sku:SKU-1', 'VERIFIED', 'mapping-v1', ?)
                """,
                (
                    observation_id,
                    batch_id,
                    price,
                    observed_at,
                    f"sha256:{observation_id.lower()}",
                ),
            )
        connection.execute(
            """
            INSERT INTO emergency_offline_policies(
                policy_version, platform_name, emergency_ratio,
                approved_by, approved_at, created_at, retired_at
            ) VALUES ('POLICY-1', 'platform', '0.80', 'admin', ?, ?, NULL)
            """,
            (sent_at, sent_at),
        )
        connection.execute(
            """
            INSERT INTO operational_incidents(
                incident_id, dedupe_key, category, source_type, source_ref_id,
                severity, incident_status, blocks_finalization, platform_name,
                platform_trade_date, seller_operation_date, subject_type,
                subject_key, title, description, first_detected_at,
                last_detected_at, resolved_at, occurrence_count, created_at,
                updated_at
            ) VALUES (
                'INCIDENT-1', 'incident-1', 'PRICE_ANOMALY',
                'PRODUCT_OBSERVATION', 'OBS-1', 'S4', 'WAITING_HUMAN', 0,
                'platform', '2026-08-03', '2026-08-03', 'internal_sku',
                'SKU-1', 'extreme price', '', ?, ?, NULL, 1, ?, ?
            )
            """,
            (sent_at, sent_at, sent_at, sent_at),
        )
        connection.execute(
            """
            INSERT INTO review_tasks(
                review_task_id, trade_date, scope_type, scope_key, dedupe_key,
                source_task_id, review_type, review_status, internal_sku,
                platform_name, reason, review_payload_json,
                resolution_payload_json, required_by, created_at, updated_at
            ) VALUES (
                'REVIEW-1', '2026-08-03', 'incident', 'INCIDENT-1', 'review-1',
                NULL, 'emergency_protection', 'pending', 'SKU-1', 'platform',
                'extreme price', '{}', '{}', ?, ?, ?
            )
            """,
            ((NOW + timedelta(hours=1)).isoformat(), sent_at, sent_at),
        )
        connection.execute(
            """
            INSERT INTO notification_outbox(
                notification_id, notification_key, notification_type,
                related_task_id, related_review_task_id, recipient_type,
                recipient_ref, channel, priority, payload_json, status,
                attempt_count, max_attempts, sent_at, created_at, updated_at
            ) VALUES (
                'OUTBOX-1', 'outbox-1', 'mobile_review_required', NULL,
                'REVIEW-1', 'operator', 'admin', 'feishu', 100, '{}', 'SENT',
                1, 3, ?, ?, ?
            )
            """,
            (sent_at, sent_at, sent_at),
        )
        event_payload = json.dumps(
            {
                "review_task_id": "REVIEW-1",
                "notification_id": "OUTBOX-1",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO operational_incident_events(
                event_id, event_key, incident_id, event_type, occurred_at,
                source_type, source_ref_id, from_status, to_status, severity,
                event_payload_json, created_at
            ) VALUES (
                'INCIDENT-EVENT-REVIEW', 'incident-review-1', 'INCIDENT-1',
                'REVIEW_RECORDED', ?, 'INCIDENT_REVIEW_SERVICE', 'REVIEW-1',
                'OPEN', 'WAITING_HUMAN', 'S4', ?, ?
            )
            """,
            (sent_at, event_payload, sent_at),
        )


def _service(repository: SQLiteRuntimeRepository) -> EmergencyOfflineAuthorizationService:
    service = EmergencyOfflineAuthorizationService(repository)
    service.shadow = _Shadow(_decision())  # type: ignore[assignment]
    return service


def _authorize(service: EmergencyOfflineAuthorizationService, **overrides):
    values = {
        "authorization_id": "AUTHORIZATION-0001",
        "incident_id": "INCIDENT-1",
        "review_task_id": "REVIEW-1",
        "products_path": Path("unused.xlsx"),
        "feature_flag_job_id": "EMERGENCY-FLAG",
        "authorized_at": NOW + timedelta(seconds=1),
        "expires_at": NOW + timedelta(minutes=10),
        "initial_observation_id": "OBS-1",
    }
    values.update(overrides)
    return service.authorize(**values)


def test_authorization_event_task_and_incident_transition_are_atomic(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    result = _authorize(_service(repository))

    assert result.task.origin_type is TaskOriginType.SYSTEM_EMERGENCY
    assert result.task.action_type is TaskActionType.SET_OFFLINE
    assert result.task.task_status is TaskStatus.PENDING
    assert result.task.origin_ref_id == "emergency:AUTHORIZATION-0001"
    assert result.evidence["emergency_ratio"] == "0.80"
    assert result.evidence["runtime_db_path"] == str(repository.db_path.resolve())
    with closing(repository.connect_read()) as connection:
        event = connection.execute(
            "SELECT event_type FROM automation_run_events WHERE event_id = ?",
            (result.authorization_event_id,),
        ).fetchone()
        incident = connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()
    assert event["event_type"] == EMERGENCY_EVENT_TYPE
    assert incident["incident_status"] == "AUTO_PROTECTING"


def test_authorization_accepts_shadow_decision_after_feature_flag_is_enabled(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = EmergencyOfflineAuthorizationService(repository)
    service.shadow = _Shadow(  # type: ignore[assignment]
        replace(
            _decision(),
            authorization_eligible=True,
            blockers=(),
        )
    )

    result = _authorize(service)

    assert result.task.origin_type is TaskOriginType.SYSTEM_EMERGENCY
    assert result.task.action_type is TaskActionType.SET_OFFLINE


def test_exact_authorization_replay_returns_same_task(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = _service(repository)

    first = _authorize(service)
    replay = _authorize(service)

    assert not first.replayed
    assert replay.replayed
    assert first.task.task_id == replay.task.task_id
    assert first.evidence_sha256 == replay.evidence_sha256


def test_disabled_flag_and_manual_task_fail_before_any_authorization_write(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE automation_jobs SET enabled = 0 WHERE job_id = 'EMERGENCY-FLAG'"
        )
    with pytest.raises(ValidationError, match="flag is disabled"):
        _authorize(_service(repository))
    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE origin_type = 'SYSTEM_EMERGENCY'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM automation_run_events "
            "WHERE event_type = ?",
            (EMERGENCY_EVENT_TYPE,),
        ).fetchone()[0] == 0


def test_manual_task_blocks_authorization(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manual = Task(
        task_id="MANUAL-TASK-0001",
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=TaskActionType.SET_OFFLINE,
        priority=100,
        task_status=TaskStatus.PENDING,
        created_at=NOW,
        target_status="offline",
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id="web:manual-1",
        expires_at=NOW + timedelta(hours=1),
    )
    repository.insert_task(manual)

    with pytest.raises(ValidationError, match="manual task"):
        _authorize(_service(repository))


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_authorization_event",
        "after_emergency_task",
        "after_incident_transition",
    ],
)
def test_database_failure_rolls_back_entire_authorization(
    tmp_path: Path,
    failure_point: str,
) -> None:
    repository = _repository(tmp_path)

    def fail(point: str) -> None:
        if point == failure_point:
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        _authorize(_service(repository), failure_injector=fail)

    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE origin_type = 'SYSTEM_EMERGENCY'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM automation_run_events "
            "WHERE event_type = ?",
            (EMERGENCY_EVENT_TYPE,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0] == "WAITING_HUMAN"


def test_authorized_task_builds_replay_safe_click_binding(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    result = _authorize(_service(repository))

    with closing(repository.connect_read()) as connection:
        binding = build_emergency_authorization_binding(
            connection,
            task_id=result.task.task_id,
        )
        evidence = revalidate_emergency_offline_facts(
            connection,
            binding=binding,
            now=NOW + timedelta(seconds=2),
            allowed_task_statuses={"pending"},
        )

    assert binding["authorization_event_id"] == result.authorization_event_id
    assert binding["authorization_evidence_sha256"] == result.evidence_sha256
    assert evidence["review_task_id"] == "REVIEW-1"


@pytest.mark.parametrize("revocation", ["flag", "review"])
def test_click_binding_fails_closed_after_mutable_veto(
    tmp_path: Path,
    revocation: str,
) -> None:
    repository = _repository(tmp_path)
    result = _authorize(_service(repository))
    with closing(repository.connect_write()) as connection, connection:
        binding = build_emergency_authorization_binding(
            connection,
            task_id=result.task.task_id,
        )
        if revocation == "flag":
            connection.execute(
                "UPDATE automation_jobs SET enabled = 0 "
                "WHERE job_id = 'EMERGENCY-FLAG'"
            )
        else:
            connection.execute(
                "UPDATE review_tasks SET review_status = 'approved' "
                "WHERE review_task_id = 'REVIEW-1'"
            )

    with closing(repository.connect_read()) as connection, pytest.raises(
        EmergencyOfflineFenceError
    ):
        revalidate_emergency_offline_facts(
            connection,
            binding=binding,
            now=NOW + timedelta(seconds=2),
            allowed_task_statuses={"pending"},
        )


def _seed_listing_context(
    repository: SQLiteRuntimeRepository,
    mapping_path: Path,
) -> None:
    mapping_path.write_text(
        json.dumps(
            {
                "mapping_version": "mapping-v1",
                "mappings": [
                    {
                        "internal_sku": "SKU-1",
                        "expected_product_name": "product",
                        "expected_grade": "B",
                        "status": "active",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repository.upsert_listing_status(
        ListingStatus(
            listing_status_id="LISTING-EMERGENCY-1",
            platform_name="platform",
            internal_sku="SKU-1",
            variety="product",
            grade="B",
            current_price=Decimal("8.00"),
            platform_stock_qty=5,
            online_status="online",
            updated_at=NOW,
        )
    )
    manifest = prepare_listing_sync_batch(
        repository,
        batch_id="BATCH-EMERGENCY-SYNC",
        platform_name="platform",
        mapping_path=mapping_path,
    )
    request = build_listing_action_request(
        manifest,
        execution_profile="production",
        execution_attempt_id="ATTEMPT-EMERGENCY-SYNC",
        applet_uri="weixin://launchapplet/test",
    )
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE shadowbot_listing_action_batches "
            "SET instruction_hash = ?, execution_attempt_id = ?, status = 'QUEUED' "
            "WHERE batch_id = ?",
            (request["instruction_hash"], request["execution_attempt_id"], request["batch_id"]),
        )
    snapshot_id = "SNAPSHOT-EMERGENCY-SYNC"
    observed_at = NOW.isoformat()
    snapshot = {
        "schema_version": "shadowbot-listing-sync-snapshot-1.0",
        "snapshot_id": snapshot_id,
        "platform_name": "platform",
        "execution_attempt_id": request["execution_attempt_id"],
        "mapping_source_version": request["mapping_source_version"],
        "result_id": "RESULT-EMERGENCY-SYNC",
        "scan_started_at": observed_at,
        "scan_completed_at": observed_at,
        "online_scan_started_at": observed_at,
        "online_scan_completed_at": observed_at,
        "waiting_scan_started_at": observed_at,
        "waiting_scan_completed_at": observed_at,
        "online_scan_complete": True,
        "waiting_scan_complete": True,
        "online_end_marker_verified": True,
        "waiting_end_marker_verified": True,
        "snapshot_complete": True,
        "instruction_hash": request["instruction_hash"],
        "status": "VERIFIED",
        "error_code": "",
        "evidence_manifest_sha256": "sha256:" + "a" * 64,
        "items": [
            {
                "snapshot_item_id": "SNAPSHOT-EMERGENCY-SYNC-ITEM",
                "internal_sku": "SKU-1",
                "product_name": "product",
                "grade": "B",
                "page_identity_key": contract_identity_key(
                    "platform", None, "product", "B"
                ),
                "affected_internal_skus": ["SKU-1"],
                "online_occurrences": 1,
                "waiting_occurrences": 0,
                "mapping_ambiguous": False,
                "listing_location": "online_only",
                "online_row_identities": ["online:parent-index:1"],
                "waiting_row_identities": [],
                "online_observed_price": "8.00",
                "waiting_observed_price": None,
                "online_observed_inventory": 5,
                "waiting_observed_inventory": None,
                "diagnostic_code": "",
                "online_observed_at": observed_at,
                "waiting_observed_at": None,
            }
        ],
    }
    result = {
        "schema_version": "shadowbot-listing-action-batch-result-1.0",
        "contract_version": 5,
        "action_type": "sync_status",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": "READ_ONLY",
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": "sha256:" + "b" * 64,
        "result_id": snapshot["result_id"],
        "started_at": observed_at,
        "ended_at": observed_at,
        "snapshot": snapshot,
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    import_listing_sync_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="c" * 64,
        source_result_path="synthetic.result.json",
    )


def _emergency_write_request(proposal: dict[str, object]) -> dict[str, object]:
    manifest = proposal["manifest"]
    gate_items = proposal["gate_items"]
    gate_summary = {
        "schema_version": "shadowbot-listing-action-gate-summary-1.0",
        "gate_phase": "PRE_PUBLISH",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "items": [
            {
                "internal_sku": manifest["items"][0]["internal_sku"],
                "operation_id": manifest["items"][0]["operation_id"],
                "decision": gate_items[0]["decision"],
                "lock_status": "ACTIVE",
                "lock_operation_id": manifest["items"][0]["operation_id"],
                "block_reasons": [],
            }
        ],
    }
    return build_listing_action_request(
        manifest,
        execution_profile="production",
        execution_attempt_id="ATTEMPT-EMERGENCY-COMMIT",
        applet_uri="weixin://launchapplet/test",
        gate_summary=gate_summary,
        batch_task_id="BATCH-TASK-EMERGENCY",
        batch_operation_id="BATCH-OP-EMERGENCY",
        emergency_authorization=proposal["emergency_authorization"],
    )


def test_emergency_reuses_v5_persistence_and_shared_write_lock(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    current = datetime.now(timezone.utc)
    authorized = _authorize(
        _service(repository),
        authorized_at=current,
        expires_at=current + timedelta(minutes=10),
    )
    mapping_path = tmp_path / "mapping.json"
    _seed_listing_context(repository, mapping_path)

    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-EMERGENCY-COMMIT",
        task_ids=[authorized.task.task_id],
        mapping_path=mapping_path,
        execution_profile="production",
    )
    assert proposal["publishable"] is True
    request = _emergency_write_request(proposal)
    shadowbot_queue_worker._v5_validate_request(request)
    _persist_prepared_write_batch(repository, request)

    with closing(repository.connect_read()) as connection:
        revalidate_emergency_offline_facts(
            connection,
            binding=request["emergency_authorization"],
            now=current + timedelta(seconds=1),
            allowed_task_statuses={"running"},
            operation_id=request["items"][0]["operation_id"],
            require_active_lock=True,
        )
        assert connection.execute(
            "SELECT task_status FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()[0] == "running"
        lock = connection.execute(
            "SELECT status, operation_id FROM shadowbot_write_locks"
        ).fetchone()
        assert tuple(lock) == ("ACTIVE", request["items"][0]["operation_id"])
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_execution_attempts "
            "WHERE execution_mode = 'COMMIT'"
        ).fetchone()[0] == 1

    request_item = request["items"][0]
    observed_at = datetime.now(timezone.utc).isoformat()
    output = {
        "source_task_id": request_item["source_task_id"],
        "operation_id": request_item["operation_id"],
        "item_execution_attempt_id": request_item["item_execution_attempt_id"],
        "internal_sku": request_item["internal_sku"],
        "item_payload_sha256": request_item["item_payload_sha256"],
        "operation_result": "VERIFIED",
        "detail_effect_state": "NOT_STARTED",
        "listing_effect_state": "VERIFIED",
        "detail_save_clicked": False,
        "action_confirm_clicked": True,
        "observed_price_before_action": "8.00",
        "observed_inventory_before_action": 5,
        "observed_price_after_detail_save": None,
        "observed_inventory_after_detail_save": None,
        "detail_save_clicked_at": None,
        "action_clicked_at": observed_at,
        "readback_observed_at": observed_at,
        "actual_price": "8.00",
        "actual_inventory": 5,
        "error_code": "",
        "error_message": "",
    }
    counts = v5_result_counts([output])
    result = {
        "schema_version": "shadowbot-listing-action-batch-result-1.0",
        "contract_version": 5,
        "action_type": "set_offline",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": "COMMIT",
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": "sha256:" + "d" * 64,
        "result_id": "RESULT-EMERGENCY-COMMIT",
        "started_at": observed_at,
        "ended_at": observed_at,
        "items": [output],
        "counts": counts,
        **derive_v5_batch_semantics(counts),
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    summary = import_listing_action_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="e" * 64,
        source_result_path="synthetic.commit.result.json",
    )
    assert summary["status"] == "VERIFIED"
    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT task_status FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()[0] == "success"
        assert connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0] == "WAITING_HUMAN"
        recovery = connection.execute(
            "SELECT event_payload_json FROM operational_incident_events "
            "WHERE incident_id = 'INCIDENT-1' AND event_type = 'RECOVERY_RECORDED'"
        ).fetchone()
        assert json.loads(recovery[0])["automatic_reonline_allowed"] is False


def test_emergency_persistence_rolls_back_if_flag_changes_after_proposal(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    current = datetime.now(timezone.utc)
    authorized = _authorize(
        _service(repository),
        authorized_at=current,
        expires_at=current + timedelta(minutes=10),
    )
    mapping_path = tmp_path / "mapping.json"
    _seed_listing_context(repository, mapping_path)
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-EMERGENCY-COMMIT",
        task_ids=[authorized.task.task_id],
        mapping_path=mapping_path,
        execution_profile="production",
    )
    request = _emergency_write_request(proposal)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE automation_jobs SET enabled = 0 "
            "WHERE job_id = 'EMERGENCY-FLAG'"
        )

    with pytest.raises(ValidationError, match="FEATURE_FLAG_DISABLED"):
        _persist_prepared_write_batch(repository, request)
    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_write_locks"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_operations"
        ).fetchone()[0] == 0
