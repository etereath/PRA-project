from __future__ import annotations

import json
import time
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread

import pytest
from openpyxl import Workbook

from app.emergency_offline_fence import (
    EMERGENCY_FINAL_CLICK_FENCE_TASK_MESSAGE,
    EMERGENCY_HUMAN_PREEMPTED_TASK_MESSAGE,
    EmergencyOfflineFenceError,
    build_emergency_authorization_binding,
    record_emergency_final_click_fence_won,
    revalidate_emergency_offline_facts,
)
from app.enums import ReviewTaskStatus, TaskActionType, TaskOriginType, TaskStatus
from app.exceptions import ValidationError
from app.models import ListingStatus, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import PRODUCT_HEADERS
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
    ensure_listing_action_reconcile_attempt,
    import_listing_action_result,
    propose_listing_action_batch,
)
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_listing_sync import (
    import_listing_sync_result,
    prepare_listing_sync_batch,
)
from app.shadowbot_contract_primitives import contract_identity_key
from app.shadowbot_listing_contract import derive_v5_batch_semantics, v5_result_counts
NOW = datetime(2026, 8, 3, 2, tzinfo=timezone.utc)
TEST_CURRENT = NOW + timedelta(hours=1)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return TEST_CURRENT.replace(tzinfo=None)
        return TEST_CURRENT.astimezone(tz)


@pytest.fixture(autouse=True)
def _freeze_listing_pipeline_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import shadowbot_listing_action_pipeline

    monkeypatch.setattr(shadowbot_listing_action_pipeline, "datetime", _FixedDateTime)


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
                'extreme price', '{"incident_id":"INCIDENT-1"}', '{}', ?, ?, ?
            )
            """,
            ((NOW + timedelta(hours=1)).isoformat(), sent_at, sent_at),
        )
        connection.execute(
            """
            INSERT INTO review_tokens(
                token_id, review_task_id, token_hash, token_subject,
                allowed_actions, expires_at, used_at, revoked_at,
                created_at, created_by, last_used_at, note
            ) VALUES (
                'TOKEN-1', 'REVIEW-1', 'synthetic-token-hash', 'operations',
                '["adjusted","approved","rejected"]', ?, NULL, NULL,
                ?, 'synthetic-fixture', NULL, 'synthetic'
            )
            """,
            ((TEST_CURRENT + timedelta(days=7)).isoformat(), sent_at),
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
    service.product_cost_reader = lambda *_args, **_kwargs: (  # type: ignore[method-assign]
        Decimal("10.00"),
        "products.xlsx:sha256:test",
        "",
    )
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


def _write_products_workbook(path: Path, *, base_cost: int) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(PRODUCT_HEADERS)
    sheet.append(
        [
            "SKU-1",
            "Synthetic flower",
            "B",
            "60",
            "bundle",
            base_cost,
            50,
            True,
            8,
            12,
            "",
            "synthetic",
            "green",
        ]
    )
    workbook.save(path)


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
    service.product_cost_reader = lambda *_args, **_kwargs: (  # type: ignore[method-assign]
        Decimal("10.00"),
        "products.xlsx:sha256:test",
        "",
    )

    result = _authorize(service)

    assert result.task.origin_type is TaskOriginType.SYSTEM_EMERGENCY
    assert result.task.action_type is TaskActionType.SET_OFFLINE


def test_automatic_authorization_cost_snapshot_fails_closed_after_database_lock_wait(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    products_path = tmp_path / "products-lock-wait.xlsx"
    _write_products_workbook(products_path, base_cost=10)
    reader = EmergencyOfflineAuthorizationService(repository).product_cost_reader
    base_cost, source_ref, error = reader(products_path, internal_sku="SKU-1")
    assert error == ""
    reached_database_lock = Event()

    class SignallingShadow(_Shadow):
        def evaluate(self, **kwargs) -> EmergencyOfflineShadowDecision:
            decision = super().evaluate(**kwargs)
            reached_database_lock.set()
            return decision

    second_repository = SQLiteRuntimeRepository(repository.db_path)
    service = EmergencyOfflineAuthorizationService(second_repository)
    service.shadow = SignallingShadow(  # type: ignore[assignment]
        replace(
            _decision(),
            base_cost=base_cost,
            base_cost_source_ref=source_ref,
        )
    )
    errors: list[BaseException] = []
    blocker = repository.connect_write()
    blocker.execute("BEGIN IMMEDIATE")

    def authorize() -> None:
        try:
            _authorize(service, products_path=products_path)
        except BaseException as exc:  # noqa: BLE001 - thread assertion capture
            errors.append(exc)

    thread = Thread(target=authorize)
    thread.start()
    assert reached_database_lock.wait(timeout=2)
    _write_products_workbook(products_path, base_cost=11)
    blocker.commit()
    blocker.close()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ValidationError)
    assert "authoritative product cost changed" in str(errors[0])
    assert not any(
        task.origin_type is TaskOriginType.SYSTEM_EMERGENCY
        for task in repository.list_tasks()
    )


def test_review_lock_first_prevents_waiting_authorization(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE review_tasks SET review_payload_json = ? "
            "WHERE review_task_id = 'REVIEW-1'",
            ('{"incident_id":"INCIDENT-1"}',),
        )
    review_repository = SQLiteRuntimeRepository(repository.db_path)
    authorization_repository = SQLiteRuntimeRepository(repository.db_path)
    review_has_lock = Event()
    release_review = Event()
    review_results: list[object] = []
    authorization_errors: list[BaseException] = []

    def hold_review_transaction(point: str) -> None:
        if point == "after_incident_review_resolution":
            review_has_lock.set()
            assert release_review.wait(timeout=5)

    def resolve_review() -> None:
        review_results.append(
            review_repository.resolve_mobile_review_atomic(
                review_task_id="REVIEW-1",
                token_hash="synthetic-token-hash",
                status=ReviewTaskStatus.REJECTED,
                actor_source="mobile_review_token",
                note="operator handling",
                now=NOW + timedelta(seconds=1),
                failure_injector=hold_review_transaction,
            )
        )

    def authorize() -> None:
        try:
            _authorize(_service(authorization_repository))
        except BaseException as exc:  # noqa: BLE001 - thread assertion capture
            authorization_errors.append(exc)

    review_thread = Thread(target=resolve_review)
    review_thread.start()
    assert review_has_lock.wait(timeout=2)
    authorization_thread = Thread(target=authorize)
    authorization_thread.start()
    release_review.set()
    review_thread.join(timeout=5)
    authorization_thread.join(timeout=5)

    assert not review_thread.is_alive()
    assert not authorization_thread.is_alive()
    assert len(review_results) == 1
    assert len(authorization_errors) == 1
    assert isinstance(authorization_errors[0], ValidationError)
    assert "Review already resolved" in str(authorization_errors[0])
    assert not any(
        task.origin_type is TaskOriginType.SYSTEM_EMERGENCY
        for task in repository.list_tasks()
    )


def test_authorization_lock_first_is_preempted_by_waiting_formal_review(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE review_tasks SET review_payload_json = ? "
            "WHERE review_task_id = 'REVIEW-1'",
            ('{"incident_id":"INCIDENT-1"}',),
        )
    authorization_repository = SQLiteRuntimeRepository(repository.db_path)
    review_repository = SQLiteRuntimeRepository(repository.db_path)
    authorization_has_lock = Event()
    release_authorization = Event()
    authorization_results: list[object] = []
    review_results: list[object] = []

    def hold_authorization_transaction(point: str) -> None:
        if point == "after_emergency_task":
            authorization_has_lock.set()
            assert release_authorization.wait(timeout=5)

    def authorize() -> None:
        authorization_results.append(
            _authorize(
                _service(authorization_repository),
                failure_injector=hold_authorization_transaction,
            )
        )

    def resolve_review() -> None:
        review_results.append(
            review_repository.resolve_mobile_review_atomic(
                review_task_id="REVIEW-1",
                token_hash="synthetic-token-hash",
                status=ReviewTaskStatus.REJECTED,
                actor_source="mobile_review_token",
                note="operator handling",
                now=NOW + timedelta(seconds=2),
            )
        )

    authorization_thread = Thread(target=authorize)
    authorization_thread.start()
    assert authorization_has_lock.wait(timeout=2)
    review_thread = Thread(target=resolve_review)
    review_thread.start()
    release_authorization.set()
    authorization_thread.join(timeout=5)
    review_thread.join(timeout=5)

    assert not authorization_thread.is_alive()
    assert not review_thread.is_alive()
    assert len(authorization_results) == 1
    assert len(review_results) == 1
    emergency_task = authorization_results[0].task
    stored_task = repository.get_task(emergency_task.task_id)
    assert stored_task is not None
    assert stored_task.task_status is TaskStatus.CANCELLED
    with closing(repository.connect_read()) as connection:
        incident = connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()
    assert incident["incident_status"] == "WAITING_HUMAN"


def test_exact_authorization_replay_returns_same_task(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = _service(repository)

    first = _authorize(service)
    replay = _authorize(service)

    assert not first.replayed
    assert replay.replayed
    assert first.task.task_id == replay.task.task_id
    assert first.evidence_sha256 == replay.evidence_sha256


def test_reissued_pending_notification_invalidates_old_sent_window(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO notification_outbox(
                notification_id, notification_key, notification_type,
                related_task_id, related_review_task_id, recipient_type,
                recipient_ref, channel, priority, payload_json, status,
                attempt_count, max_attempts, created_at, updated_at
            ) VALUES (
                'OUTBOX-REISSUED', 'outbox-reissued', 'mobile_review_required',
                NULL, 'REVIEW-1', 'operator', 'admin', 'feishu', 100, '{}',
                'PENDING', 0, 3, ?, ?
            )
            """,
            (NOW.isoformat(), NOW.isoformat()),
        )

    with pytest.raises(ValidationError, match="initial Review notification is not SENT"):
        _authorize(_service(repository))

    assert not any(
        task.origin_type is TaskOriginType.SYSTEM_EMERGENCY
        for task in repository.list_tasks()
    )


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


def test_authorization_requires_a_usable_human_review_token(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE review_tokens SET expires_at = ? WHERE review_task_id = 'REVIEW-1'",
            ((NOW - timedelta(seconds=1)).isoformat(),),
        )

    with pytest.raises(ValidationError, match="no usable token"):
        _authorize(_service(repository))

    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE origin_type = 'SYSTEM_EMERGENCY'"
        ).fetchone()[0] == 0


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


@pytest.mark.parametrize(
    "review_payload",
    [
        '{"blocked_actions":["set_offline"],"reason_code":"PAGE_DRIFT"}',
        '{"blocked_actions":"set_offline","reason_code":"MALFORMED"}',
    ],
)
def test_final_fence_blocks_review_created_after_request_persistence(
    tmp_path: Path,
    review_payload: str,
) -> None:
    repository = _repository(tmp_path)
    authorized = _authorize(_service(repository))
    with closing(repository.connect_read()) as connection:
        binding = build_emergency_authorization_binding(
            connection,
            task_id=authorized.task.task_id,
        )
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO review_tasks(
                review_task_id, trade_date, scope_type, scope_key, dedupe_key,
                source_task_id, review_type, review_status, internal_sku,
                platform_name, reason, review_payload_json,
                resolution_payload_json, required_by, created_at, updated_at
            ) VALUES (
                'REVIEW-LATE', '2026-08-03', 'sku', 'SKU-1', 'late-review',
                NULL, 'listing_anomaly', 'pending', 'SKU-1', 'platform',
                'late blocker', ?, '{}', ?, ?, ?
            )
            """,
            (
                review_payload,
                (NOW + timedelta(hours=1)).isoformat(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )

    with closing(repository.connect_read()) as connection:
        with pytest.raises(EmergencyOfflineFenceError, match="BLOCKING_REVIEW"):
            revalidate_emergency_offline_facts(
                connection,
                binding=binding,
                now=NOW + timedelta(seconds=2),
                allowed_task_statuses={"pending"},
            )


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
        "evaluated_at": TEST_CURRENT.isoformat(),
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


def _emergency_write_result(
    request: dict[str, object],
    *,
    result_id: str,
    outcome: str,
) -> dict[str, object]:
    request_item = request["items"][0]
    observed_at = TEST_CURRENT.isoformat()
    clicked = outcome in {"VERIFIED", "NEEDS_RECONCILIATION"}
    unknown = outcome == "NEEDS_RECONCILIATION"
    output = {
        "source_task_id": request_item["source_task_id"],
        "operation_id": request_item["operation_id"],
        "item_execution_attempt_id": request_item["item_execution_attempt_id"],
        "internal_sku": request_item["internal_sku"],
        "item_payload_sha256": request_item["item_payload_sha256"],
        "operation_result": outcome,
        "detail_effect_state": "NOT_STARTED" if clicked else "NOT_APPLIED",
        "listing_effect_state": (
            "UNKNOWN" if unknown else "VERIFIED" if clicked else "NOT_APPLIED"
        ),
        "detail_save_clicked": False,
        "action_confirm_clicked": clicked,
        "observed_price_before_action": "8.00",
        "observed_inventory_before_action": 5,
        "observed_price_after_detail_save": None,
        "observed_inventory_after_detail_save": None,
        "detail_save_clicked_at": None,
        "action_clicked_at": observed_at if clicked else None,
        "readback_observed_at": observed_at if outcome == "VERIFIED" else None,
        "actual_price": "8.00" if outcome == "VERIFIED" else None,
        "actual_inventory": 5 if outcome == "VERIFIED" else None,
        "error_code": (
            "CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN"
            if unknown
            else ""
            if clicked
            else "EMERGENCY_AUTHORIZATION_REVOKED"
        ),
        "error_message": (
            "platform readback unavailable"
            if unknown
            else ""
            if clicked
            else "formal Review won before click"
        ),
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
        "result_id": result_id,
        "started_at": observed_at,
        "ended_at": observed_at,
        "items": [output],
        "counts": counts,
        **derive_v5_batch_semantics(counts),
        "error_code": (
            "CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN"
            if unknown
            else ""
            if clicked
            else "EMERGENCY_AUTHORIZATION_REVOKED"
        ),
        "error_message": (
            "platform readback unavailable"
            if unknown
            else ""
            if clicked
            else "formal Review won before click"
        ),
        "retryable": False,
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    return result


def _prepare_worker_won_unknown_reconcile(
    tmp_path: Path,
) -> tuple[
    SQLiteRuntimeRepository,
    object,
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    repository = _repository(tmp_path)
    current = TEST_CURRENT
    authorized = _authorize(
        _service(repository),
        authorized_at=current,
        expires_at=current + timedelta(minutes=10),
    )
    mapping_path = tmp_path / "mapping-worker-unknown.json"
    _seed_listing_context(repository, mapping_path)
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-EMERGENCY-WORKER-UNKNOWN",
        task_ids=[authorized.task.task_id],
        mapping_path=mapping_path,
        execution_profile="production",
    )
    request = _emergency_write_request(proposal)
    _persist_prepared_write_batch(repository, request)

    with closing(repository.connect_write()) as worker_connection:
        worker_connection.execute("BEGIN IMMEDIATE")
        revalidate_emergency_offline_facts(
            worker_connection,
            binding=request["emergency_authorization"],
            now=current + timedelta(seconds=1),
            allowed_task_statuses={"running"},
            operation_id=request["items"][0]["operation_id"],
            require_active_lock=True,
        )
        record_emergency_final_click_fence_won(
            worker_connection,
            binding=request["emergency_authorization"],
            crossed_at=current + timedelta(seconds=1),
        )
        worker_connection.commit()

    review = repository.resolve_mobile_review_atomic(
        review_task_id="REVIEW-1",
        token_hash="synthetic-token-hash",
        status=ReviewTaskStatus.APPROVED,
        actor_source="mobile_review_token",
        emergency_base_cost=Decimal("10.00"),
        emergency_base_cost_source_ref="products.xlsx:sha256:test",
        emergency_product_snapshot_verifier=lambda: (
            Decimal("10.00"),
            "products.xlsx:sha256:test",
        ),
        now=current + timedelta(seconds=2),
    )
    assert review.source_task is None

    initial_result = _emergency_write_result(
        request,
        result_id="RESULT-EMERGENCY-WORKER-UNKNOWN",
        outcome="NEEDS_RECONCILIATION",
    )
    imported = import_listing_action_result(
        repository,
        request=request,
        result=initial_result,
        result_file_sha256="3" * 64,
        source_result_path="synthetic.worker-unknown.result.json",
    )
    assert imported["status"] == "UNKNOWN"
    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT task_status FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()[0] == "manual_review"
        assert connection.execute(
            "SELECT status FROM shadowbot_operations WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()[0] == "NEEDS_RECONCILIATION"
        assert connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()[0] == "UNKNOWN"
        assert connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0] == "AUTO_PROTECTING"

    runner = ShadowBotFileQueueRunner(tmp_path / "reconcile-queue")
    operation_id = request["items"][0]["operation_id"]
    publication = ensure_listing_action_reconcile_attempt(
        repository,
        runner,
        source_request=request,
        source_result=initial_result,
        operation_id=operation_id,
    )
    repeated = ensure_listing_action_reconcile_attempt(
        repository,
        runner,
        source_request=request,
        source_result=initial_result,
        operation_id=operation_id,
    )
    assert publication["status"] == "PUBLISHED"
    assert repeated["status"] == "ALREADY_EXISTS"
    reconcile_path = Path(publication["queue_request_path"])
    reconcile_request = json.loads(reconcile_path.read_text(encoding="utf-8-sig"))
    return repository, authorized, request, initial_result, reconcile_request


def _reconcile_result(
    request: dict[str, object],
    *,
    outcome: str,
    result_id: str,
) -> dict[str, object]:
    item = request["items"][0]
    observed_at = TEST_CURRENT.isoformat()
    output = {
        name: item[name]
        for name in (
            "source_task_id",
            "operation_id",
            "item_execution_attempt_id",
            "internal_sku",
            "item_payload_sha256",
        )
    } | {
        "operation_result": outcome,
        "detail_effect_state": "NOT_APPLIED",
        "listing_effect_state": (
            "VERIFIED"
            if outcome == "VERIFIED"
            else "NOT_APPLIED"
            if outcome == "NOT_APPLIED"
            else "UNKNOWN"
        ),
        "detail_save_clicked": False,
        "action_confirm_clicked": False,
        "observed_price_before_action": None,
        "observed_inventory_before_action": None,
        "observed_price_after_detail_save": None,
        "observed_inventory_after_detail_save": None,
        "actual_price": None,
        "actual_inventory": None,
        "detail_save_clicked_at": None,
        "action_clicked_at": None,
        "readback_observed_at": observed_at,
        "error_code": (
            "RECONCILE_STILL_UNKNOWN"
            if outcome == "NEEDS_RECONCILIATION"
            else ""
        ),
        "error_message": (
            "read-only reconcile remains inconclusive"
            if outcome == "NEEDS_RECONCILIATION"
            else ""
        ),
    }
    counts = v5_result_counts([output])
    result = {
        "schema_version": "shadowbot-listing-action-batch-result-1.0",
        "contract_version": 5,
        "action_type": "set_offline",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": "RECONCILE",
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": "sha256:" + "4" * 64,
        "result_id": result_id,
        "started_at": observed_at,
        "ended_at": observed_at,
        "items": [output],
        "counts": counts,
        **derive_v5_batch_semantics(counts),
        "error_code": output["error_code"],
        "error_message": output["error_message"],
        "retryable": False,
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    return result


def test_shadowbot_emergency_reuses_v5_persistence_and_shared_write_lock(
    tmp_path: Path,
) -> None:
    from shadowbot.test2 import shadowbot_queue_worker

    repository = _repository(tmp_path)
    current = TEST_CURRENT
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
    observed_at = TEST_CURRENT.isoformat()
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


def test_formal_review_wins_after_emergency_request_persistence_and_import_converges(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    review_repository = SQLiteRuntimeRepository(repository.db_path)
    worker_repository = SQLiteRuntimeRepository(repository.db_path)
    current = TEST_CURRENT
    authorized = _authorize(
        _service(repository),
        authorized_at=current,
        expires_at=current + timedelta(minutes=10),
    )
    mapping_path = tmp_path / "mapping-human-wins.json"
    _seed_listing_context(repository, mapping_path)
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-EMERGENCY-HUMAN-WINS",
        task_ids=[authorized.task.task_id],
        mapping_path=mapping_path,
        execution_profile="production",
    )
    request = _emergency_write_request(proposal)
    _persist_prepared_write_batch(repository, request)

    review_result = review_repository.resolve_mobile_review_atomic(
        review_task_id="REVIEW-1",
        token_hash="synthetic-token-hash",
        status=ReviewTaskStatus.APPROVED,
        actor_source="mobile_review_token",
        emergency_base_cost=Decimal("10.00"),
        emergency_base_cost_source_ref="products.xlsx:sha256:test",
        emergency_product_snapshot_verifier=lambda: (
            Decimal("10.00"),
            "products.xlsx:sha256:test",
        ),
        now=current + timedelta(seconds=2),
    )
    assert review_result.source_task is not None
    assert review_result.source_task.origin_type is TaskOriginType.MANUAL

    with closing(worker_repository.connect_write()) as worker_connection:
        worker_connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(EmergencyOfflineFenceError):
            revalidate_emergency_offline_facts(
                worker_connection,
                binding=request["emergency_authorization"],
                now=current + timedelta(seconds=3),
                allowed_task_statuses={"running"},
                operation_id=request["items"][0]["operation_id"],
                require_active_lock=True,
            )
        worker_connection.rollback()

    result = _emergency_write_result(
        request,
        result_id="RESULT-EMERGENCY-HUMAN-WINS",
        outcome="NOT_APPLIED",
    )
    summary = import_listing_action_result(
        worker_repository,
        request=request,
        result=result,
        result_file_sha256="1" * 64,
        source_result_path="synthetic.human-wins.result.json",
    )
    replay = import_listing_action_result(
        worker_repository,
        request=request,
        result=result,
        result_file_sha256="1" * 64,
        source_result_path="synthetic.human-wins.result.json",
    )

    assert summary["already_imported"] is False
    assert replay["already_imported"] is True
    with closing(repository.connect_read()) as connection:
        automatic_task = connection.execute(
            "SELECT task_status, result_message FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()
        manual_tasks = connection.execute(
            "SELECT task_status FROM tasks WHERE origin_type = 'MANUAL'"
        ).fetchall()
        operation = connection.execute(
            "SELECT status, operation_result FROM shadowbot_operations WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()
        attempt = connection.execute(
            "SELECT status, side_effect_state FROM shadowbot_execution_attempts "
            "WHERE execution_attempt_id = ?",
            (request["items"][0]["item_execution_attempt_id"],),
        ).fetchone()
        write_lock = connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()
        incident_status = connection.execute(
            "SELECT incident_status FROM operational_incidents WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0]
        recovery_payload = json.loads(
            connection.execute(
                "SELECT event_payload_json FROM operational_incident_events "
                "WHERE event_key LIKE 'emergency-result:RESULT-EMERGENCY-HUMAN-WINS:%'"
            ).fetchone()[0]
        )
    assert tuple(automatic_task) == (
        "cancelled",
        EMERGENCY_HUMAN_PREEMPTED_TASK_MESSAGE,
    )
    assert [row["task_status"] for row in manual_tasks] == ["pending"]
    assert tuple(operation) == ("FAILED", "NOT_APPLIED")
    assert tuple(attempt) == ("FAILED", "NOT_APPLIED")
    assert write_lock["status"] == "RELEASED"
    assert incident_status == "WAITING_HUMAN"
    assert recovery_payload["resolution_order"] == "HUMAN_PREEMPTED"
    assert recovery_payload["human_preempted_before_side_effect"] is True


def test_worker_final_fence_wins_and_late_review_creates_no_second_write_task(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    review_repository = SQLiteRuntimeRepository(repository.db_path)
    current = TEST_CURRENT
    authorized = _authorize(
        _service(repository),
        authorized_at=current,
        expires_at=current + timedelta(minutes=10),
    )
    mapping_path = tmp_path / "mapping-worker-wins.json"
    _seed_listing_context(repository, mapping_path)
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-EMERGENCY-WORKER-WINS",
        task_ids=[authorized.task.task_id],
        mapping_path=mapping_path,
        execution_profile="production",
    )
    request = _emergency_write_request(proposal)
    _persist_prepared_write_batch(repository, request)

    worker_connection = repository.connect_write()
    worker_connection.execute("BEGIN IMMEDIATE")
    revalidate_emergency_offline_facts(
        worker_connection,
        binding=request["emergency_authorization"],
        now=current + timedelta(seconds=1),
        allowed_task_statuses={"running"},
        operation_id=request["items"][0]["operation_id"],
        require_active_lock=True,
    )
    record_emergency_final_click_fence_won(
        worker_connection,
        binding=request["emergency_authorization"],
        crossed_at=current + timedelta(seconds=1),
    )

    started = Event()
    review_results = []
    review_errors: list[BaseException] = []

    def submit_late_review() -> None:
        started.set()
        try:
            review_results.append(
                review_repository.resolve_mobile_review_atomic(
                    review_task_id="REVIEW-1",
                    token_hash="synthetic-token-hash",
                    status=ReviewTaskStatus.APPROVED,
                    actor_source="mobile_review_token",
                    emergency_base_cost=Decimal("10.00"),
                    emergency_base_cost_source_ref="products.xlsx:sha256:test",
                    emergency_product_snapshot_verifier=lambda: (
                        Decimal("10.00"),
                        "products.xlsx:sha256:test",
                    ),
                    now=current + timedelta(seconds=2),
                )
            )
        except BaseException as exc:  # noqa: BLE001 - thread assertion capture
            review_errors.append(exc)

    review_thread = Thread(target=submit_late_review)
    review_thread.start()
    assert started.wait(timeout=2)
    time.sleep(0.1)
    worker_connection.commit()
    worker_connection.close()
    review_thread.join(timeout=5)

    assert not review_thread.is_alive()
    assert review_errors == []
    assert len(review_results) == 1
    assert review_results[0].source_task is None
    stored_review = review_repository.get_review_task("REVIEW-1")
    assert stored_review is not None
    assert stored_review.review_payload is not None
    assert stored_review.resolution_payload["decision"] == (
        "late_after_emergency_final_click_fence"
    )
    assert stored_review.resolution_payload["platform_side_effect_prevented"] is False

    with closing(repository.connect_read()) as connection:
        task_before_import = connection.execute(
            "SELECT task_status, result_message FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()
        assert tuple(task_before_import) == (
            "manual_review",
            EMERGENCY_FINAL_CLICK_FENCE_TASK_MESSAGE,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE origin_type = 'MANUAL'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT incident_status FROM operational_incidents WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0] == "AUTO_PROTECTING"

    result = _emergency_write_result(
        request,
        result_id="RESULT-EMERGENCY-WORKER-WINS",
        outcome="VERIFIED",
    )
    import_listing_action_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="2" * 64,
        source_result_path="synthetic.worker-wins.result.json",
    )
    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT task_status FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()[0] == "success"
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE origin_type = 'MANUAL'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT incident_status FROM operational_incidents WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0] == "WAITING_HUMAN"
        recovery_payload = json.loads(
            connection.execute(
                "SELECT event_payload_json FROM operational_incident_events "
                "WHERE event_key LIKE 'emergency-result:RESULT-EMERGENCY-WORKER-WINS:%'"
            ).fetchone()[0]
        )
    assert recovery_payload["resolution_order"] == "WORKER_FENCE_WON"
    assert recovery_payload["late_human_review_recorded"] is True


def test_worker_won_unknown_import_preserves_unique_reconcile_state(
    tmp_path: Path,
) -> None:
    repository, authorized, request, _source_result, reconcile_request = (
        _prepare_worker_won_unknown_reconcile(tmp_path)
    )
    with closing(repository.connect_read()) as connection:
        task = connection.execute(
            "SELECT task_status, result_message FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()
        operation = connection.execute(
            "SELECT status, operation_result FROM shadowbot_operations "
            "WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()
        write_lock = connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()
        incident_status = connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0]
        fence_events = connection.execute(
            "SELECT COUNT(*) FROM operational_incident_events "
            "WHERE event_key = ?",
            (f"emergency-final-click-fence:{authorized.task.task_id}",),
        ).fetchone()[0]
        recovery_events = connection.execute(
            "SELECT COUNT(*) FROM operational_incident_events "
            "WHERE event_type = 'RECOVERY_RECORDED'"
        ).fetchone()[0]
    assert task["task_status"] == "manual_review"
    assert task["result_message"] != EMERGENCY_FINAL_CLICK_FENCE_TASK_MESSAGE
    assert tuple(operation) == ("RUNNING", "NEEDS_RECONCILIATION")
    assert write_lock["status"] == "UNKNOWN"
    assert incident_status == "AUTO_PROTECTING"
    assert fence_events == 1
    assert recovery_events == 0
    assert reconcile_request["execution_mode"] == "RECONCILE"


def test_worker_won_unknown_reconcile_verified_converges(
    tmp_path: Path,
) -> None:
    repository, authorized, request, _source_result, reconcile_request = (
        _prepare_worker_won_unknown_reconcile(tmp_path)
    )
    result = _reconcile_result(
        reconcile_request,
        outcome="VERIFIED",
        result_id="RESULT-EMERGENCY-RECONCILE-VERIFIED",
    )
    imported = import_listing_action_result(
        repository,
        request=reconcile_request,
        result=result,
        result_file_sha256="5" * 64,
        source_result_path="synthetic.emergency-reconcile-verified.result.json",
    )
    assert imported["status"] == "VERIFIED"
    with closing(repository.connect_read()) as connection:
        task_status = connection.execute(
            "SELECT task_status FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()[0]
        operation = connection.execute(
            "SELECT status, operation_result FROM shadowbot_operations "
            "WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()
        lock_status = connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()[0]
        incident_status = connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0]
        payload = json.loads(
            connection.execute(
                "SELECT event_payload_json FROM operational_incident_events "
                "WHERE event_key LIKE "
                "'emergency-result:RESULT-EMERGENCY-RECONCILE-VERIFIED:%'"
            ).fetchone()[0]
        )
    assert task_status == "success"
    assert tuple(operation) == ("VERIFIED", "VERIFIED")
    assert lock_status == "RELEASED"
    assert incident_status == "WAITING_HUMAN"
    assert payload["resolution_order"] == "WORKER_FENCE_WON"
    assert payload["late_human_review_recorded"] is True


def test_worker_won_unknown_reconcile_not_applied_converges(
    tmp_path: Path,
) -> None:
    repository, authorized, request, _source_result, reconcile_request = (
        _prepare_worker_won_unknown_reconcile(tmp_path)
    )
    result = _reconcile_result(
        reconcile_request,
        outcome="NOT_APPLIED",
        result_id="RESULT-EMERGENCY-RECONCILE-NOT-APPLIED",
    )
    imported = import_listing_action_result(
        repository,
        request=reconcile_request,
        result=result,
        result_file_sha256="6" * 64,
        source_result_path="synthetic.emergency-reconcile-not-applied.result.json",
    )
    assert imported["status"] == "NOT_APPLIED"
    with closing(repository.connect_read()) as connection:
        task_status = connection.execute(
            "SELECT task_status FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()[0]
        operation = connection.execute(
            "SELECT status, operation_result FROM shadowbot_operations "
            "WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()
        lock_status = connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()[0]
        incident_status = connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0]
        payload = json.loads(
            connection.execute(
                "SELECT event_payload_json FROM operational_incident_events "
                "WHERE event_key LIKE "
                "'emergency-result:RESULT-EMERGENCY-RECONCILE-NOT-APPLIED:%'"
            ).fetchone()[0]
        )
    assert task_status == "failed"
    assert tuple(operation) == ("FAILED", "NOT_APPLIED")
    assert lock_status == "RELEASED"
    assert incident_status == "WAITING_HUMAN"
    assert payload["resolution_order"] == "WORKER_FENCE_WON"


def test_worker_won_reconcile_still_unknown_keeps_single_blocking_attempt(
    tmp_path: Path,
) -> None:
    repository, authorized, request, source_result, reconcile_request = (
        _prepare_worker_won_unknown_reconcile(tmp_path)
    )
    result = _reconcile_result(
        reconcile_request,
        outcome="NEEDS_RECONCILIATION",
        result_id="RESULT-EMERGENCY-RECONCILE-STILL-UNKNOWN",
    )
    imported = import_listing_action_result(
        repository,
        request=reconcile_request,
        result=result,
        result_file_sha256="7" * 64,
        source_result_path="synthetic.emergency-reconcile-unknown.result.json",
    )
    repeated = ensure_listing_action_reconcile_attempt(
        repository,
        ShadowBotFileQueueRunner(tmp_path / "unused-reconcile-queue"),
        source_request=request,
        source_result=source_result,
        operation_id=request["items"][0]["operation_id"],
    )
    assert imported["status"] == "NEEDS_RECONCILIATION"
    assert repeated["status"] == "ALREADY_EXISTS"
    with closing(repository.connect_read()) as connection:
        task_status = connection.execute(
            "SELECT task_status FROM tasks WHERE task_id = ?",
            (authorized.task.task_id,),
        ).fetchone()[0]
        operation_status = connection.execute(
            "SELECT status FROM shadowbot_operations WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()[0]
        lock_status = connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE operation_id = ?",
            (request["items"][0]["operation_id"],),
        ).fetchone()[0]
        incident_status = connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0]
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM shadowbot_execution_attempts "
            "WHERE operation_id = ? AND execution_mode = 'RECONCILE'",
            (request["items"][0]["operation_id"],),
        ).fetchone()[0]
    assert task_status == "manual_review"
    assert operation_status == "NEEDS_RECONCILIATION"
    assert lock_status == "UNKNOWN"
    assert incident_status == "AUTO_PROTECTING"
    assert attempt_count == 1


def test_worker_won_reconcile_exact_replay_does_not_duplicate_projection(
    tmp_path: Path,
) -> None:
    repository, _authorized, _request, _source_result, reconcile_request = (
        _prepare_worker_won_unknown_reconcile(tmp_path)
    )
    result = _reconcile_result(
        reconcile_request,
        outcome="VERIFIED",
        result_id="RESULT-EMERGENCY-RECONCILE-REPLAY",
    )
    first = import_listing_action_result(
        repository,
        request=reconcile_request,
        result=result,
        result_file_sha256="8" * 64,
        source_result_path="synthetic.emergency-reconcile-replay.result.json",
    )
    with closing(repository.connect_read()) as connection:
        before = {
            "incident": tuple(
                connection.execute(
                    "SELECT incident_status, updated_at FROM operational_incidents "
                    "WHERE incident_id = 'INCIDENT-1'"
                ).fetchone()
            ),
            "events": connection.execute(
                "SELECT COUNT(*) FROM operational_incident_events"
            ).fetchone()[0],
            "outbox": connection.execute(
                "SELECT COUNT(*) FROM notification_outbox"
            ).fetchone()[0],
            "notification_logs": connection.execute(
                "SELECT COUNT(*) FROM notification_logs"
            ).fetchone()[0],
        }
    replay = import_listing_action_result(
        repository,
        request=reconcile_request,
        result=result,
        result_file_sha256="8" * 64,
        source_result_path="synthetic.emergency-reconcile-replay.result.json",
    )
    with closing(repository.connect_read()) as connection:
        after = {
            "incident": tuple(
                connection.execute(
                    "SELECT incident_status, updated_at FROM operational_incidents "
                    "WHERE incident_id = 'INCIDENT-1'"
                ).fetchone()
            ),
            "events": connection.execute(
                "SELECT COUNT(*) FROM operational_incident_events"
            ).fetchone()[0],
            "outbox": connection.execute(
                "SELECT COUNT(*) FROM notification_outbox"
            ).fetchone()[0],
            "notification_logs": connection.execute(
                "SELECT COUNT(*) FROM notification_logs"
            ).fetchone()[0],
        }
    assert first["already_imported"] is False
    assert replay["already_imported"] is True
    assert after == before


def test_emergency_persistence_rolls_back_if_flag_changes_after_proposal(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    current = TEST_CURRENT
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
