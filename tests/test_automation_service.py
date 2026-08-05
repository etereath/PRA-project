from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

from app.automation_models import (
    AutomationJob,
    AutomationRunOutcome,
)
from app.enums import (
    AutomationRunStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.listing_observation_identity import (
    listing_observation_source_identities,
    listing_observation_source_identity_sha256,
)
from app.models import ShadowBotOperationLedger, Task
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    AutomationHeartbeatStore,
    AutomationExecutionContext,
    AutomationSchedulePlanner,
    AutomationService,
    CHILD_ONLY,
    DAILY_LOCAL_TIME,
    FULL_MARKET_SCAN,
    INTERVAL_MINUTES,
    LISTING_STATUS_SCAN,
    ONLINE_PULSE,
    ORDER_SCAN,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    ensure_default_automation_jobs,
)
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
    OperationalTimeService,
)
from scripts.run_automation_service import (
    ProcessFileLock,
    automation_service_lock_path,
)


PLATFORM = "蚂蚁花团供应商"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def runtime_repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    return repository


@pytest.fixture
def repository(
    runtime_repository: SQLiteRuntimeRepository,
) -> AutomationRepository:
    return AutomationRepository(runtime_repository)


def _job(
    *,
    job_id: str,
    job_type: str,
    minutes: int = 10,
    priority: int = 50,
    enabled: bool = True,
    schedule_kind: str = INTERVAL_MINUTES,
) -> AutomationJob:
    return AutomationJob(
        job_id=job_id,
        job_type=job_type,
        display_name=f"{job_type} 测试作业",
        enabled=enabled,
        schedule_kind=schedule_kind,
        schedule_expression=(
            str(minutes) if schedule_kind == INTERVAL_MINUTES else "-"
        ),
        priority=priority,
        config={
            "platform_name": PLATFORM,
            "catchup_policy": "LATEST_ONLY",
        },
    )


def _store_job(
    repository: AutomationRepository,
    job: AutomationJob,
    *,
    now: datetime,
) -> AutomationJob:
    return repository.upsert_job(job, now=now)


def _ensure_run(
    repository: AutomationRepository,
    job: AutomationJob,
    *,
    scheduled_for: datetime,
):
    return repository.ensure_run(
        job=job,
        scheduled_for=scheduled_for,
        time_context=OperationalTimeService().classify(scheduled_for),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=scheduled_for,
    )[0]


def _seed_accepted_listing_coverage_facts(
    repository: AutomationRepository,
    *,
    listing_run_id: str,
    manifest_sha256: str,
    now: datetime,
    observation_trade_date: str | None = None,
    observation_internal_sku: str | None = None,
    observation_mapping_status: str = "UNMAPPED",
) -> None:
    run = repository.get_run(listing_run_id)
    assert run is not None
    suffix = listing_run_id[-12:]
    batch_id = f"BATCH-COVER-{suffix}"
    result_id = f"RESULT-COVER-{suffix}"
    snapshot_id = f"SNAPSHOT-COVER-{suffix}"
    timestamp = now.isoformat()
    evidence_manifest_sha256 = "sha256:" + "b" * 64
    source_item = {
        "snapshot_item_id": f"SNAPSHOT-ITEM-COVER-{suffix}",
        "internal_sku": None,
        "product_name": "测试商品",
        "grade": "A",
        "page_identity_key": "online:test",
        "affected_internal_skus": [],
        "online_occurrences": 1,
        "waiting_occurrences": 0,
        "online_row_identities": ["online:test-row"],
        "waiting_row_identities": [],
    }
    source_identities = listing_observation_source_identities(
        snapshot_id=snapshot_id,
        evidence_manifest_sha256=evidence_manifest_sha256,
        snapshot_items=(source_item,),
    )
    source_identity = source_identities[0]
    source_mapping_identity_sha256 = (
        listing_observation_source_identity_sha256(source_identities)
    )
    with repository.runtime_repository.connect_write() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET status = 'VERIFIED', result_id = ?, updated_at = ?
            WHERE batch_id = ? AND manifest_sha256 = ?
            """,
            (
                result_id,
                timestamp,
                batch_id,
                manifest_sha256,
            ),
        )
        connection.execute(
            """
            INSERT INTO shadowbot_listing_result_receipts(
                result_id, batch_id, execution_attempt_id,
                instruction_hash, manifest_sha256, result_sha256,
                source_result_path, accepted_at, ack_state
            ) VALUES (?, ?, ?, 'instruction', ?, ?, '', ?, 'PENDING')
            """,
            (
                result_id,
                batch_id,
                f"ATTEMPT-{suffix}",
                manifest_sha256,
                "a" * 64,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO listing_sync_snapshots(
                snapshot_id, batch_id, platform_name,
                execution_attempt_id, scan_started_at,
                scan_completed_at, online_scan_started_at,
                online_scan_completed_at, waiting_scan_started_at,
                waiting_scan_completed_at, online_scan_complete,
                waiting_scan_complete, snapshot_complete,
                online_end_marker_verified,
                waiting_end_marker_verified, instruction_hash,
                result_id, status, error_code,
                evidence_manifest_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1,
                      1, 1, 'instruction', ?, 'VERIFIED', '', ?)
            """,
            (
                snapshot_id,
                batch_id,
                run.platform_name,
                f"ATTEMPT-{suffix}",
                timestamp,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
                result_id,
                evidence_manifest_sha256,
            ),
        )
        connection.execute(
            """
            INSERT INTO listing_sync_snapshot_items(
                snapshot_item_id, snapshot_id, internal_sku,
                product_name, grade, page_identity_key,
                affected_internal_skus_json, online_occurrences,
                waiting_occurrences, listing_location,
                online_row_identities_json,
                waiting_row_identities_json,
                online_observed_price, waiting_observed_price,
                online_observed_inventory,
                waiting_observed_inventory, diagnostic_code,
                online_observed_at, waiting_observed_at
            ) VALUES (?, ?, NULL, '测试商品', 'A', 'online:test',
                      '[]', 1, 0, 'ambiguous', ?, '[]',
                      '10.00', NULL, 1, NULL, 'UNMAPPED_PRODUCT',
                      ?, NULL)
            """,
            (
                source_item["snapshot_item_id"],
                snapshot_id,
                json.dumps(
                    source_item["online_row_identities"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO product_observation_batches(
                observation_batch_id, automation_run_id,
                platform_name, scan_type, batch_status,
                scan_started_at, scan_completed_at,
                requested_scope_json, scope_complete,
                end_marker_verified, content_sha256,
                time_policy_version, error_code, error_message,
                created_at
            ) VALUES (?, ?, ?, 'LISTING_STATUS_SCAN', 'ACCEPTED',
                      ?, ?, ?, 1, 1, ?, ?, '', '', ?)
            """,
            (
                f"OBSERVATION-{suffix}",
                listing_run_id,
                run.platform_name,
                timestamp,
                timestamp,
                json.dumps(
                    {
                        "pages": ["online", "waiting"],
                        "source_snapshot_id": snapshot_id,
                        "source_manifest_sha256": manifest_sha256,
                        "source_result_sha256": "a" * 64,
                        "source_platform_trade_date": (
                            run.platform_trade_date.isoformat()
                        ),
                        "source_conversion_sha256": (
                            "sha256:" + "c" * 64
                        ),
                        "source_mapping_identity_sha256": (
                            source_mapping_identity_sha256
                        ),
                        "validated_mapping_identity_sha256": (
                            source_mapping_identity_sha256
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "sha256:" + "c" * 64,
                run.time_policy_version,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO product_observation_items(
                observation_item_id, observation_batch_id,
                internal_sku, platform_product_name, grade,
                observed_price, observed_inventory, observed_online,
                observed_at, platform_trade_date,
                seller_operation_date, seller_phase,
                page_identity_key, mapping_status, mapping_version,
                evidence_sha256
            ) VALUES (?, ?, ?, '测试商品', 'A', '10.00', 1, 1,
                      ?, ?, ?, ?, 'online:test', ?, '', ?)
            """,
            (
                f"OBSERVATION-ITEM-{suffix}",
                f"OBSERVATION-{suffix}",
                observation_internal_sku,
                timestamp,
                observation_trade_date
                or run.platform_trade_date.isoformat(),
                run.seller_operation_date.isoformat(),
                run.seller_phase.value,
                observation_mapping_status,
                source_identity.evidence_sha256,
            ),
        )
        connection.commit()


def _prepare_listing_coverage_batch(
    repository: AutomationRepository,
    *,
    listing_run_id: str,
    manifest_sha256: str,
    now: datetime,
) -> None:
    run = repository.get_run(listing_run_id)
    assert run is not None
    suffix = listing_run_id[-12:]
    batch_id = f"BATCH-COVER-{suffix}"
    timestamp = now.isoformat()
    with repository.runtime_repository.connect_write() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO shadowbot_batch_registry(
                batch_id, batch_type, contract_version,
                platform_name, created_at
            ) VALUES (?, 'sync_status', 5, ?, ?)
            """,
            (batch_id, run.platform_name, timestamp),
        )
        connection.execute(
            """
            INSERT INTO shadowbot_listing_action_batches(
                batch_id, contract_version, execution_profile,
                action_type, platform_name, manifest_sha256,
                status, batch_target_count, created_at, updated_at
            ) VALUES (?, 5, 'production', 'sync_status', ?, ?,
                      'PREPARED', 0, ?, ?)
            """,
            (
                batch_id,
                run.platform_name,
                manifest_sha256,
                timestamp,
                timestamp,
            ),
        )
        connection.commit()


def test_scheduler_only_does_not_terminally_merge_hourly_pulse(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    jobs = ensure_default_automation_jobs(
        repository,
        platform_name=PLATFORM,
        now=now,
    )

    first = AutomationSchedulePlanner(repository).materialize(now=now)
    second = AutomationSchedulePlanner(repository).materialize(now=now)

    assert len(jobs) == 8
    assert first.created_run_ids
    assert first.merged_run_ids == ()
    assert second.created_run_ids == ()
    pulse = repository.list_runs(
        job_id="AUTOMATION-ONLINE-PULSE-10M"
    )[0]
    full = repository.list_runs(
        job_id="AUTOMATION-FULL-MARKET-SCAN-HOURLY"
    )[0]
    assert pulse.run_status is AutomationRunStatus.SCHEDULED
    assert full.run_status is AutomationRunStatus.SCHEDULED
    assert repository.list_links(child_run_id=pulse.run_id) == []


def test_default_full_market_scan_is_aligned_to_local_hour_plus_ten(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 31, 10, 12, tzinfo=timezone.utc)
    ensure_default_automation_jobs(
        repository,
        platform_name=PLATFORM,
        now=now,
    )

    AutomationSchedulePlanner(repository).materialize(now=now)

    full = repository.list_runs(
        job_id="AUTOMATION-FULL-MARKET-SCAN-HOURLY"
    )
    assert len(full) == 1
    assert (
        full[0].scheduled_for.hour,
        full[0].scheduled_for.minute,
    ) == (10, 10)


def test_successful_full_handler_finalizes_pulse_coverage(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    listing_job = _store_job(
        repository,
        _job(
            job_id="LISTING",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
            priority=50,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    full = _ensure_run(repository, full_job, scheduled_for=now)
    manifest_sha256 = "sha256:" + "d" * 64

    def full_handler(run, context):
        context.ensure_child_run(
            child_job_id=listing_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    def listing_handler(run, context):
        _prepare_listing_coverage_batch(
            repository,
            listing_run_id=run.run_id,
            manifest_sha256=manifest_sha256,
            now=now,
        )
        context.bind_input_manifest(manifest_sha256)
        _seed_accepted_listing_coverage_facts(
            repository,
            listing_run_id=run.run_id,
            manifest_sha256=manifest_sha256,
            now=now,
        )
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    cycle = AutomationService(
        repository,
        handlers={
            FULL_MARKET_SCAN: full_handler,
            LISTING_STATUS_SCAN: listing_handler,
            ONLINE_PULSE: lambda run, context: AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS
            ),
        },
        clock=MutableClock(now),
    ).run_cycle()

    listing_child = repository.list_links(parent_run_id=full.run_id)[0]
    assert cycle.completed_run_ids == (
        full.run_id,
        listing_child.child_run_id,
    )
    assert cycle.scheduled.merged_run_ids == (pulse.run_id,)
    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.MERGED
    assert [
        (link.parent_run_id, link.relation_type)
        for link in repository.list_links(child_run_id=pulse.run_id)
    ] == [(listing_child.child_run_id, "MERGED_RUN")]


@pytest.mark.parametrize(
    "mismatch_kind",
    ["trade-date", "mapping-identity"],
)
def test_incompatible_listing_facts_cannot_merge_pulse(
    repository: AutomationRepository,
    mismatch_kind: str,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    listing_job = _store_job(
        repository,
        _job(
            job_id="LISTING",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    _ensure_run(repository, full_job, scheduled_for=now)
    manifest_sha256 = "sha256:" + "9" * 64

    def full_handler(run, context):
        context.ensure_child_run(
            child_job_id=listing_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    def listing_handler(run, context):
        _prepare_listing_coverage_batch(
            repository,
            listing_run_id=run.run_id,
            manifest_sha256=manifest_sha256,
            now=now,
        )
        context.bind_input_manifest(manifest_sha256)
        fact_overrides: dict[str, object]
        if mismatch_kind == "trade-date":
            fact_overrides = {
                "observation_trade_date": (
                    run.platform_trade_date + timedelta(days=1)
                ).isoformat()
            }
        else:
            fact_overrides = {
                "observation_internal_sku": "AISHA-B",
                "observation_mapping_status": "VERIFIED",
            }
        _seed_accepted_listing_coverage_facts(
            repository,
            listing_run_id=run.run_id,
            manifest_sha256=manifest_sha256,
            now=now,
            **fact_overrides,
        )
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    cycle = AutomationService(
        repository,
        handlers={
            FULL_MARKET_SCAN: full_handler,
            LISTING_STATUS_SCAN: listing_handler,
            ONLINE_PULSE: lambda run, context: AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS
            ),
        },
        clock=MutableClock(now),
    ).run_cycle()

    assert pulse.run_id in cycle.completed_run_ids
    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.SUCCESS
    assert not any(
        link.relation_type == "MERGED_RUN"
        for link in repository.list_links(child_run_id=pulse.run_id)
    )


def test_sleep_records_missed_pulses_and_only_catches_latest_window(
    repository: AutomationRepository,
) -> None:
    first_now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse = _store_job(
        repository,
        _job(
            job_id="PULSE",
            job_type=ONLINE_PULSE,
            minutes=10,
            priority=60,
        ),
        now=first_now,
    )
    full = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=first_now,
    )
    planner = AutomationSchedulePlanner(repository)
    planner.materialize(now=first_now)

    wake_at = first_now + timedelta(hours=1, minutes=5)
    result = planner.materialize(now=wake_at)

    pulse_runs = repository.list_runs(job_id=pulse.job_id)
    full_runs = repository.list_runs(job_id=full.job_id)
    assert len(result.missed_run_ids) == 7
    assert sum(
        run.run_status is AutomationRunStatus.MISSED
        for run in pulse_runs
    ) == 6
    assert sum(
        run.run_status is AutomationRunStatus.MERGED
        for run in pulse_runs
    ) == 0
    assert len(full_runs) == 2
    assert [run.run_status for run in full_runs] == [
        AutomationRunStatus.SCHEDULED,
        AutomationRunStatus.MISSED,
    ]


def test_merge_never_crosses_platform_trade_date_cutoff(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 10, 6, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    pre_cutoff_job = _store_job(
        repository,
        AutomationJob(
            job_id="PRE-CUTOFF",
            job_type="PRE_CUTOFF_FULL_SCAN",
            display_name="截单前完整扫描",
            enabled=True,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression="17:55",
            priority=30,
            config={
                "platform_name": PLATFORM,
                "catchup_policy": "LATEST_ONLY",
            },
        ),
        now=now,
    )

    result = AutomationSchedulePlanner(repository).materialize(now=now)

    pulse = repository.list_runs(job_id=pulse_job.job_id)[0]
    pre_cutoff = repository.list_runs(job_id=pre_cutoff_job.job_id)[0]
    assert pulse.platform_trade_date != pre_cutoff.platform_trade_date
    assert result.merged_run_ids == ()
    assert pulse.run_status is AutomationRunStatus.SCHEDULED
    assert pre_cutoff.run_status is AutomationRunStatus.SCHEDULED


def test_long_sleep_is_bounded_to_prevent_task_storm(
    repository: AutomationRepository,
) -> None:
    first_now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=first_now,
    )
    planner = AutomationSchedulePlanner(
        repository,
        max_windows_per_job=4,
    )
    planner.materialize(now=first_now)

    result = planner.materialize(now=first_now + timedelta(days=1))

    assert len(result.created_run_ids) == 4
    assert len(result.missed_run_ids) == 4
    assert result.truncated_window_count == 140
    runs = repository.list_runs(job_id=job.job_id)
    assert len(runs) == 5
    events = repository.list_events(runs[-2].run_id)
    assert any(
        event.event_type == "MISSED_WINDOWS_TRUNCATED"
        for event in events
    )


def test_existing_scheduled_window_expires_without_a_new_due_window(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = AutomationJob(
        job_id="DAILY",
        job_type="DAILY_TEST",
        display_name="每日测试",
        enabled=True,
        schedule_kind=DAILY_LOCAL_TIME,
        schedule_expression="10:00",
        priority=50,
        config={
            "platform_name": PLATFORM,
            "catchup_policy": "LATEST_ONLY",
            "max_lateness_seconds": 60,
        },
    )
    _store_job(repository, job, now=now)
    planner = AutomationSchedulePlanner(repository)
    first = planner.materialize(now=now)
    run_id = first.created_run_ids[0]

    second = planner.materialize(now=now + timedelta(minutes=2))

    assert second.created_run_ids == ()
    assert second.missed_run_ids == (run_id,)
    assert repository.get_run(
        run_id
    ).run_status is AutomationRunStatus.MISSED


def test_restart_reconciles_preexisting_scheduled_merge_candidates(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    listing_job = _store_job(
        repository,
        _job(
            job_id="LISTING",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    full = _ensure_run(repository, full_job, scheduled_for=now)

    result = AutomationSchedulePlanner(repository).materialize(
        now=now,
        executable_job_types=[FULL_MARKET_SCAN, LISTING_STATUS_SCAN],
    )

    assert result.created_run_ids == ()
    assert result.merged_run_ids == ()
    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.SCHEDULED
    links = repository.list_links(child_run_id=pulse.run_id)
    assert [(item.parent_run_id, item.relation_type) for item in links] == [
        (full.run_id, "COVERAGE_CANDIDATE")
    ]

    claim = repository.claim_run(
        run_id=full.run_id,
        owner_token="full-owner",
        now=now,
        lease_seconds=60,
    )
    assert claim is not None
    listing_child, created = repository.ensure_child_run_fenced(
        claim,
        listing_job,
        relation_type="LISTING_STATUS_CHILD",
        now=now,
    )
    assert created
    assert repository.finish_run(
        claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=1),
    )
    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.SCHEDULED
    assert [
        (item.parent_run_id, item.relation_type)
        for item in repository.list_links(child_run_id=pulse.run_id)
    ] == [(listing_child.run_id, "COVERAGE_CANDIDATE")]
    listing_claim = repository.claim_run(
        run_id=listing_child.run_id,
        owner_token="listing-owner",
        now=now + timedelta(seconds=1),
        lease_seconds=60,
    )
    assert listing_claim is not None
    manifest_sha256 = "sha256:" + "e" * 64
    _prepare_listing_coverage_batch(
        repository,
        listing_run_id=listing_child.run_id,
        manifest_sha256=manifest_sha256,
        now=now,
    )
    repository.bind_run_input_manifest(
        listing_claim,
        manifest_sha256=manifest_sha256,
        now=now + timedelta(seconds=1),
    )
    _seed_accepted_listing_coverage_facts(
        repository,
        listing_run_id=listing_child.run_id,
        manifest_sha256=manifest_sha256,
        now=now + timedelta(seconds=1),
    )
    assert repository.finish_run(
        listing_claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=2),
    )
    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.MERGED


def test_successful_parent_without_listing_child_releases_pulse(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    full = _ensure_run(repository, full_job, scheduled_for=now)
    AutomationSchedulePlanner(repository).materialize(
        now=now,
        executable_job_types=[FULL_MARKET_SCAN, LISTING_STATUS_SCAN],
    )
    claim = repository.claim_run(
        run_id=full.run_id,
        owner_token="full-owner",
        now=now,
        lease_seconds=60,
    )
    assert claim is not None

    assert repository.finish_run(
        claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=1),
    )

    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.SCHEDULED
    assert repository.list_links(child_run_id=pulse.run_id) == []


@pytest.mark.parametrize(
    "child_status",
    [
        AutomationRunStatus.SUCCESS,
        AutomationRunStatus.PARTIAL,
        AutomationRunStatus.FAILED,
        AutomationRunStatus.CANCELLED,
    ],
)
def test_incomplete_listing_child_releases_pulse(
    repository: AutomationRepository,
    child_status: AutomationRunStatus,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    listing_job = _store_job(
        repository,
        _job(
            job_id="LISTING",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    full = _ensure_run(repository, full_job, scheduled_for=now)
    AutomationSchedulePlanner(repository).materialize(
        now=now,
        executable_job_types=[FULL_MARKET_SCAN, LISTING_STATUS_SCAN],
    )
    parent_claim = repository.claim_run(
        run_id=full.run_id,
        owner_token="full-owner",
        now=now,
        lease_seconds=60,
    )
    assert parent_claim is not None
    listing_child, _ = repository.ensure_child_run_fenced(
        parent_claim,
        listing_job,
        relation_type="LISTING_STATUS_CHILD",
        now=now,
    )
    assert repository.finish_run(
        parent_claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=1),
    )
    listing_claim = repository.claim_run(
        run_id=listing_child.run_id,
        owner_token="listing-owner",
        now=now + timedelta(seconds=1),
        lease_seconds=60,
    )
    assert listing_claim is not None

    assert repository.finish_run(
        listing_claim,
        AutomationRunOutcome(status=child_status),
        now=now + timedelta(seconds=2),
    )

    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.SCHEDULED
    assert repository.list_links(child_run_id=pulse.run_id) == []


@pytest.mark.parametrize("target_running", [False, True])
def test_restart_without_required_handlers_releases_coverage_candidate(
    repository: AutomationRepository,
    target_running: bool,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    full = _ensure_run(repository, full_job, scheduled_for=now)
    planner = AutomationSchedulePlanner(repository)
    planner.materialize(
        now=now,
        executable_job_types=[FULL_MARKET_SCAN, LISTING_STATUS_SCAN],
    )
    if target_running:
        claim = repository.claim_run(
            run_id=full.run_id,
            owner_token="full-owner",
            now=now,
            lease_seconds=1,
        )
        assert claim is not None

    planner.materialize(
        now=now + timedelta(seconds=2),
        executable_job_types=[],
    )

    assert repository.list_links(child_run_id=pulse.run_id) == []
    fallback = repository.claim_next(
        owner_token="pulse-owner",
        now=now + timedelta(seconds=2),
        lease_seconds=60,
        allowed_job_types=[ONLINE_PULSE],
    )
    assert fallback is not None
    assert fallback.run.run_id == pulse.run_id


def test_order_child_failure_does_not_undo_accepted_listing_coverage(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    listing_job = _store_job(
        repository,
        _job(
            job_id="LISTING",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    order_job = _store_job(
        repository,
        _job(
            job_id="ORDER",
            job_type=ORDER_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    full = _ensure_run(repository, full_job, scheduled_for=now)
    AutomationSchedulePlanner(repository).materialize(
        now=now,
        executable_job_types=[FULL_MARKET_SCAN, LISTING_STATUS_SCAN],
    )
    parent_claim = repository.claim_run(
        run_id=full.run_id,
        owner_token="full-owner",
        now=now,
        lease_seconds=60,
    )
    assert parent_claim is not None
    listing_child, _ = repository.ensure_child_run_fenced(
        parent_claim,
        listing_job,
        relation_type="LISTING_STATUS_CHILD",
        now=now,
    )
    order_child, _ = repository.ensure_child_run_fenced(
        parent_claim,
        order_job,
        relation_type="ORDER_SCAN_CHILD",
        now=now,
    )
    assert repository.finish_run(
        parent_claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=1),
    )
    listing_claim = repository.claim_run(
        run_id=listing_child.run_id,
        owner_token="listing-owner",
        now=now + timedelta(seconds=1),
        lease_seconds=60,
    )
    assert listing_claim is not None
    manifest_sha256 = "sha256:" + "f" * 64
    _prepare_listing_coverage_batch(
        repository,
        listing_run_id=listing_child.run_id,
        manifest_sha256=manifest_sha256,
        now=now,
    )
    repository.bind_run_input_manifest(
        listing_claim,
        manifest_sha256=manifest_sha256,
        now=now + timedelta(seconds=1),
    )
    _seed_accepted_listing_coverage_facts(
        repository,
        listing_run_id=listing_child.run_id,
        manifest_sha256=manifest_sha256,
        now=now + timedelta(seconds=1),
    )
    assert repository.finish_run(
        listing_claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=2),
    )
    order_claim = repository.claim_run(
        run_id=order_child.run_id,
        owner_token="order-owner",
        now=now + timedelta(seconds=2),
        lease_seconds=60,
    )
    assert order_claim is not None
    assert repository.finish_run(
        order_claim,
        AutomationRunOutcome(status=AutomationRunStatus.FAILED),
        now=now + timedelta(seconds=3),
    )

    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.MERGED


@pytest.mark.parametrize(
    "target_status",
    [AutomationRunStatus.FAILED, AutomationRunStatus.PARTIAL],
)
def test_incomplete_coverage_target_releases_pulse_for_fallback(
    repository: AutomationRepository,
    target_status: AutomationRunStatus,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE, priority=60),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    full = _ensure_run(repository, full_job, scheduled_for=now)
    AutomationSchedulePlanner(repository).materialize(
        now=now,
        executable_job_types=[FULL_MARKET_SCAN, LISTING_STATUS_SCAN],
    )
    claim = repository.claim_run(
        run_id=full.run_id,
        owner_token="full-owner",
        now=now,
        lease_seconds=60,
    )
    assert claim is not None

    assert repository.finish_run(
        claim,
        AutomationRunOutcome(
            status=target_status,
            error_code="TEST_FAILURE",
        ),
        now=now + timedelta(seconds=1),
    )

    assert repository.get_run(
        pulse.run_id
    ).run_status is AutomationRunStatus.SCHEDULED
    assert repository.list_links(child_run_id=pulse.run_id) == []
    fallback = repository.claim_next(
        owner_token="pulse-owner",
        now=now + timedelta(seconds=1),
        lease_seconds=60,
        allowed_job_types=[ONLINE_PULSE],
    )
    assert fallback is not None
    assert fallback.run.run_id == pulse.run_id


def test_disabled_target_never_creates_coverage_candidate(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            enabled=False,
        ),
        now=now,
    )
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    _ensure_run(repository, full_job, scheduled_for=now)

    AutomationSchedulePlanner(repository).materialize(
        now=now,
        executable_job_types=[FULL_MARKET_SCAN, LISTING_STATUS_SCAN],
    )

    assert repository.list_links(child_run_id=pulse.run_id) == []


def test_disabled_regular_job_is_not_claimed_but_expired_run_is_recovered(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    scheduled = _ensure_run(repository, job, scheduled_for=now)
    repository.upsert_job(
        AutomationJob(
            job_id=job.job_id,
            job_type=job.job_type,
            display_name=job.display_name,
            enabled=False,
            schedule_kind=job.schedule_kind,
            schedule_expression=job.schedule_expression,
            priority=job.priority,
            config=job.config,
        ),
        now=now,
    )
    assert repository.claim_next(
        owner_token="owner",
        now=now,
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    ) is None
    assert repository.claim_run(
        run_id=scheduled.run_id,
        owner_token="owner",
        now=now,
        lease_seconds=10,
    ) is None

    repository.upsert_job(job, now=now)
    first = repository.claim_run(
        run_id=scheduled.run_id,
        owner_token="first",
        now=now,
        lease_seconds=10,
    )
    assert first is not None
    repository.upsert_job(
        AutomationJob(
            job_id=job.job_id,
            job_type=job.job_type,
            display_name=job.display_name,
            enabled=False,
            schedule_kind=job.schedule_kind,
            schedule_expression=job.schedule_expression,
            priority=job.priority,
            config=job.config,
        ),
        now=now,
    )
    recovered = repository.claim_next(
        owner_token="second",
        now=now + timedelta(seconds=11),
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    )
    assert recovered is not None
    assert recovered.reclaimed is True


def test_run_claim_is_single_owner_until_lease_expires(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)

    first = repository.claim_next(
        owner_token="owner-1",
        now=now,
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    )
    overlapping = repository.claim_next(
        owner_token="owner-2",
        now=now + timedelta(seconds=5),
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    )
    reclaimed = repository.claim_next(
        owner_token="owner-2",
        now=now + timedelta(seconds=11),
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    )

    assert first is not None
    assert first.run.run_id == run.run_id
    assert overlapping is None
    assert reclaimed is not None
    assert reclaimed.reclaimed is True
    assert reclaimed.lease_version == first.lease_version + 1
    assert (
        repository.finish_run(
            first,
            AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS
            ),
            now=now + timedelta(seconds=12),
        )
        is False
    )
    assert repository.finish_run(
        reclaimed,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=12),
    )
    events = repository.list_events(run.run_id)
    assert [event.event_type for event in events][-2:] == [
        "LEASE_RECLAIMED",
        "RUN_FINISHED",
    ]


def test_live_ui_run_blocks_second_ui_but_not_non_ui_handler(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    full_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=10,
        ),
        now=now,
    )
    pulse_job = _store_job(
        repository,
        _job(
            job_id="PULSE",
            job_type=ONLINE_PULSE,
            priority=20,
        ),
        now=now,
    )
    settlement_job = _store_job(
        repository,
        _job(
            job_id="SETTLEMENT",
            job_type=PLATFORM_TRADE_DAY_SETTLEMENT,
            priority=30,
        ),
        now=now,
    )
    full = _ensure_run(repository, full_job, scheduled_for=now)
    pulse = _ensure_run(repository, pulse_job, scheduled_for=now)
    settlement = _ensure_run(
        repository,
        settlement_job,
        scheduled_for=now,
    )
    full_claim = repository.claim_run(
        run_id=full.run_id,
        owner_token="full-owner",
        now=now,
        lease_seconds=10,
    )
    assert full_claim is not None

    assert repository.claim_run(
        run_id=pulse.run_id,
        owner_token="pulse-owner",
        now=now,
        lease_seconds=10,
    ) is None
    non_ui_claim = repository.claim_next(
        owner_token="settlement-owner",
        now=now,
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE, PLATFORM_TRADE_DAY_SETTLEMENT],
    )
    assert non_ui_claim is not None
    assert non_ui_claim.run.run_id == settlement.run_id

    reclaimed = repository.claim_next(
        owner_token="recovery-owner",
        now=now + timedelta(seconds=11),
        lease_seconds=10,
        allowed_job_types=[FULL_MARKET_SCAN, ONLINE_PULSE],
    )
    assert reclaimed is not None
    assert reclaimed.run.run_id == full.run_id
    assert reclaimed.reclaimed is True


def test_second_instance_cannot_claim_ui_while_handler_is_active(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    first_job = _store_job(
        repository,
        _job(
            job_id="PULSE-A",
            job_type=ONLINE_PULSE,
            priority=10,
        ),
        now=now,
    )
    second_job = _store_job(
        repository,
        _job(
            job_id="PULSE-B",
            job_type=ONLINE_PULSE,
            priority=20,
        ),
        now=now,
    )
    _ensure_run(repository, first_job, scheduled_for=now)
    second = _ensure_run(repository, second_job, scheduled_for=now)
    entered = Event()
    release = Event()

    def blocking_handler(run, context):
        entered.set()
        assert release.wait(timeout=10)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    service = AutomationService(
        repository,
        handlers={ONLINE_PULSE: blocking_handler},
        clock=MutableClock(now),
        max_runs_per_cycle=1,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.run_cycle)
        assert entered.wait(timeout=10)
        assert repository.claim_run(
            run_id=second.run_id,
            owner_token="second-instance",
            now=now,
            lease_seconds=60,
        ) is None
        release.set()
        cycle = future.result(timeout=10)

    assert len(cycle.completed_run_ids) == 1
    assert repository.get_run(
        second.run_id
    ).run_status is AutomationRunStatus.SCHEDULED


def test_expired_handler_writeback_stops_cycle_and_restart_recovers_run(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    clock = MutableClock(now)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)

    def stale_handler(run, context):
        clock.value += timedelta(seconds=11)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    first_cycle = AutomationService(
        repository,
        handlers={ONLINE_PULSE: stale_handler},
        clock=clock,
        lease_seconds=10,
        owner_token="first-instance",
    ).run_cycle()

    assert first_cycle.claimed_run_ids == (run.run_id,)
    assert first_cycle.completed_run_ids == ()
    assert first_cycle.errors == (f"LEASE_LOST:{run.run_id}",)
    assert repository.get_run(
        run.run_id
    ).run_status is AutomationRunStatus.RUNNING

    recovered_cycle = AutomationService(
        repository,
        handlers={
            ONLINE_PULSE: lambda run, context: AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS
            )
        },
        clock=clock,
        lease_seconds=10,
        owner_token="restarted-instance",
    ).run_cycle()

    assert recovered_cycle.completed_run_ids == (run.run_id,)
    assert repository.get_run(
        run.run_id
    ).run_status is AutomationRunStatus.SUCCESS


def test_handler_heartbeat_extends_fenced_lease(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    clock = MutableClock(now)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    _ensure_run(repository, job, scheduled_for=now)
    heartbeats: list[bool] = []

    def handler(run, context):
        clock.value += timedelta(seconds=5)
        heartbeats.append(context.heartbeat())
        clock.value += timedelta(seconds=6)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    service = AutomationService(
        repository,
        handlers={ONLINE_PULSE: handler},
        clock=clock,
        lease_seconds=10,
    )
    cycle = service.run_cycle()

    assert heartbeats == [True]
    assert cycle.errors == ()
    assert len(cycle.completed_run_ids) == 1
    assert repository.get_run(
        cycle.completed_run_ids[0]
    ).run_status is AutomationRunStatus.SUCCESS


def test_handler_exception_is_bounded_failed_result(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)

    def failing_handler(run, context):
        raise RuntimeError("模拟扫描失败")

    service = AutomationService(
        repository,
        handlers={ONLINE_PULSE: failing_handler},
        clock=MutableClock(now),
    )
    cycle = service.run_cycle()

    assert cycle.completed_run_ids == (run.run_id,)
    stored = repository.get_run(run.run_id)
    assert stored is not None
    assert stored.run_status is AutomationRunStatus.FAILED
    assert stored.error_code == "AUTOMATION_HANDLER_FAILED"
    assert stored.error_message == "模拟扫描失败"


def test_claim_order_uses_job_priority(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    low_priority_job = _store_job(
        repository,
        _job(
            job_id="LOW",
            job_type="LOW_PRIORITY",
            priority=90,
        ),
        now=now,
    )
    high_priority_job = _store_job(
        repository,
        _job(
            job_id="HIGH",
            job_type="HIGH_PRIORITY",
            priority=10,
        ),
        now=now,
    )
    low = _ensure_run(
        repository,
        low_priority_job,
        scheduled_for=now,
    )
    high = _ensure_run(
        repository,
        high_priority_job,
        scheduled_for=now,
    )
    calls: list[str] = []

    def handler(run, context):
        calls.append(run.run_id)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    first_cycle = AutomationService(
        repository,
        handlers={
            "LOW_PRIORITY": handler,
            "HIGH_PRIORITY": handler,
        },
        clock=MutableClock(now),
        max_runs_per_cycle=1,
    ).run_cycle()

    assert first_cycle.completed_run_ids == (high.run_id,)
    assert calls == [high.run_id]
    assert repository.get_run(
        low.run_id
    ).run_status is AutomationRunStatus.SCHEDULED


def test_full_scan_handler_can_create_idempotent_child_run_and_link(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    parent_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
        ),
        now=now,
    )
    child_job = _store_job(
        repository,
        _job(
            job_id="LISTING-CHILD",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    parent = _ensure_run(repository, parent_job, scheduled_for=now)
    child_ids: list[str] = []

    def full_handler(run, context):
        child, created = context.ensure_child_run(
            child_job_id=child_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )
        assert created is True
        replay, replay_created = context.ensure_child_run(
            child_job_id=child_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )
        assert replay_created is False
        assert replay.run_id == child.run_id
        child_ids.append(child.run_id)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    cycle = AutomationService(
        repository,
        handlers={FULL_MARKET_SCAN: full_handler},
        clock=MutableClock(now),
    ).run_cycle()

    assert cycle.completed_run_ids == (parent.run_id,)
    assert len(child_ids) == 1
    child = repository.get_run(child_ids[0])
    assert child is not None
    assert child.run_status is AutomationRunStatus.SCHEDULED
    links = repository.list_links(parent_run_id=parent.run_id)
    assert [(link.child_run_id, link.relation_type) for link in links] == [
        (child.run_id, "LISTING_STATUS_CHILD")
    ]


def test_expired_or_reclaimed_parent_cannot_create_orphan_child(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    parent_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
        ),
        now=now,
    )
    child_job = _store_job(
        repository,
        _job(
            job_id="CHILD",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    parent = _ensure_run(repository, parent_job, scheduled_for=now)
    old_claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="old",
        now=now,
        lease_seconds=10,
    )
    assert old_claim is not None
    reclaimed = repository.claim_next(
        owner_token="new",
        now=now + timedelta(seconds=11),
        lease_seconds=10,
        allowed_job_types=[FULL_MARKET_SCAN],
    )
    assert reclaimed is not None
    old_context = AutomationExecutionContext(
        claim=old_claim,
        repository=repository,
        operational_time=OperationalTimeService(),
        clock=MutableClock(now + timedelta(seconds=11)),
        lease_seconds=10,
    )

    with pytest.raises(RuntimeError, match="lease was lost"):
        old_context.ensure_child_run(
            child_job_id=child_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )

    assert repository.list_runs(job_id=child_job.job_id) == []


def test_child_creation_rolls_back_when_link_insert_fails(
    repository: AutomationRepository,
    runtime_repository: SQLiteRuntimeRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    parent_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
        ),
        now=now,
    )
    child_job = _store_job(
        repository,
        _job(
            job_id="CHILD",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    parent = _ensure_run(repository, parent_job, scheduled_for=now)
    claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="owner",
        now=now,
        lease_seconds=60,
    )
    assert claim is not None
    with runtime_repository.connect_write() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_test_child_link
            BEFORE INSERT ON automation_run_links
            BEGIN
                SELECT RAISE(ABORT, 'test link failure');
            END
            """
        )
        connection.commit()
    context = AutomationExecutionContext(
        claim=claim,
        repository=repository,
        operational_time=OperationalTimeService(),
        clock=MutableClock(now),
        lease_seconds=60,
    )

    with pytest.raises(Exception, match="test link failure"):
        context.ensure_child_run(
            child_job_id=child_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )

    assert repository.list_runs(job_id=child_job.job_id) == []


def test_child_inherits_frozen_parent_policy_and_rejects_cross_platform(
    repository: AutomationRepository,
    runtime_repository: SQLiteRuntimeRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    parent_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
        ),
        now=now,
    )
    child_job = _store_job(
        repository,
        _job(
            job_id="CHILD",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    cross_platform = AutomationJob(
        job_id="CROSS-CHILD",
        job_type=LISTING_STATUS_SCAN,
        display_name="跨平台子任务",
        enabled=False,
        schedule_kind=CHILD_ONLY,
        schedule_expression="-",
        priority=50,
        config={"platform_name": "其他平台"},
    )
    _store_job(repository, cross_platform, now=now)
    parent = _ensure_run(repository, parent_job, scheduled_for=now)
    claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="owner",
        now=now,
        lease_seconds=60,
    )
    assert claim is not None
    runtime_repository.replace_current_operational_time_policy(
        expected_current_policy_version=(
            DEFAULT_OPERATIONAL_TIME_POLICY_VERSION
        ),
        successor_policy_version="CN_SINGLE_PLATFORM_2026_V2",
        effective_from=now + timedelta(seconds=1),
        platform_cutoff_local_time="19:00",
        seller_cutoff_local_time="21:00",
        peak_start_local_time="17:00",
        created_by="pytest",
    )
    context = AutomationExecutionContext(
        claim=claim,
        repository=repository,
        operational_time=OperationalTimeService(),
        clock=MutableClock(now + timedelta(seconds=2)),
        lease_seconds=60,
    )
    child, _ = context.ensure_child_run(
        child_job_id=child_job.job_id,
        relation_type="LISTING_STATUS_CHILD",
    )
    assert child.time_policy_version == parent.time_policy_version
    assert child.platform_trade_date == parent.platform_trade_date
    assert child.seller_operation_date == parent.seller_operation_date
    assert child.seller_phase == parent.seller_phase

    with pytest.raises(ValueError, match="platform_name must match"):
        context.ensure_child_run(
            child_job_id=cross_platform.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )


def test_child_only_run_requires_valid_parent_link_before_claim(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    parent_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
        ),
        now=now,
    )
    child_job = _store_job(
        repository,
        _job(
            job_id="CHILD",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    orphan = _ensure_run(repository, child_job, scheduled_for=now)
    assert repository.claim_next(
        owner_token="owner",
        now=now,
        lease_seconds=60,
        allowed_job_types=[LISTING_STATUS_SCAN],
    ) is None
    assert repository.claim_run(
        run_id=orphan.run_id,
        owner_token="owner",
        now=now,
        lease_seconds=60,
    ) is None

    parent = _ensure_run(repository, parent_job, scheduled_for=now)
    parent_claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="parent-owner",
        now=now,
        lease_seconds=60,
    )
    assert parent_claim is not None
    child, _ = repository.ensure_child_run_fenced(
        parent_claim,
        child_job,
        relation_type="LISTING_STATUS_CHILD",
        now=now,
    )

    assert repository.claim_next(
        owner_token="early-child-owner",
        now=now,
        lease_seconds=60,
        allowed_job_types=[LISTING_STATUS_SCAN],
    ) is None
    assert repository.finish_run(
        parent_claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=1),
    )
    claimed = repository.claim_next(
        owner_token="child-owner",
        now=now + timedelta(seconds=1),
        lease_seconds=60,
        allowed_job_types=[LISTING_STATUS_SCAN],
    )
    assert claimed is not None
    assert claimed.run.run_id == child.run_id
    assert claimed.run.run_id != orphan.run_id


def test_parent_failure_cancels_unstarted_child_run(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    parent_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
        ),
        now=now,
    )
    child_job = _store_job(
        repository,
        _job(
            job_id="CHILD",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    parent = _ensure_run(repository, parent_job, scheduled_for=now)
    parent_claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="parent-owner",
        now=now,
        lease_seconds=60,
    )
    assert parent_claim is not None
    child, _ = repository.ensure_child_run_fenced(
        parent_claim,
        child_job,
        relation_type="LISTING_STATUS_CHILD",
        now=now,
    )

    assert repository.finish_run(
        parent_claim,
        AutomationRunOutcome(
            status=AutomationRunStatus.FAILED,
            error_code="PARENT_HANDLER_FAILED",
        ),
        now=now + timedelta(seconds=1),
    )

    stored_child = repository.get_run(child.run_id)
    assert stored_child is not None
    assert stored_child.run_status is AutomationRunStatus.CANCELLED
    assert stored_child.error_code == "PARENT_RUN_NOT_SUCCESSFUL"


def test_unknown_reconcile_blocks_scan_dispatch_but_not_scheduling(
    repository: AutomationRepository,
    runtime_repository: SQLiteRuntimeRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)
    runtime_repository.insert_task(
        Task(
            task_id="TASK-UNKNOWN",
            internal_sku="SKU-A",
            platform_name=PLATFORM,
            action_type=TaskActionType.UPDATE_PRICE,
            priority=1,
            task_status=TaskStatus.PENDING,
            created_at=now,
            origin_type=TaskOriginType.MANUAL,
            origin_ref_id="test:unknown",
            target_price=Decimal("10.00"),
            dedupe_key="TASK-UNKNOWN",
        )
    )
    runtime_repository.insert_shadowbot_operation(
        ShadowBotOperationLedger(
            operation_id="OP-UNKNOWN",
            task_id="TASK-UNKNOWN",
            platform=PLATFORM,
            product_identity={"internal_sku": "SKU-A"},
            expected_old_price=Decimal("9.00"),
            target_price=Decimal("10.00"),
            status="NEEDS_RECONCILIATION",
            created_at=now,
            updated_at=now,
        )
    )
    calls: list[str] = []

    def handler(run, context):
        calls.append(run.run_id)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    cycle = AutomationService(
        repository,
        handlers={ONLINE_PULSE: handler},
        clock=MutableClock(now),
    ).run_cycle()

    assert calls == []
    assert cycle.blocked_reason == "UNKNOWN_OR_RECONCILE_ACTIVE"
    assert repository.get_run(
        run.run_id
    ).run_status is AutomationRunStatus.SCHEDULED


def test_ui_blocker_is_rechecked_atomically_before_each_claim(
    repository: AutomationRepository,
    runtime_repository: SQLiteRuntimeRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    non_ui_job = _store_job(
        repository,
        _job(
            job_id="SETTLEMENT",
            job_type=PLATFORM_TRADE_DAY_SETTLEMENT,
            priority=1,
        ),
        now=now,
    )
    ui_job = _store_job(
        repository,
        _job(
            job_id="PULSE",
            job_type=ONLINE_PULSE,
            priority=60,
        ),
        now=now,
    )
    non_ui_run = _ensure_run(
        repository,
        non_ui_job,
        scheduled_for=now,
    )
    ui_run = _ensure_run(repository, ui_job, scheduled_for=now)

    def create_blocker(run, context):
        runtime_repository.insert_task(
            Task(
                task_id="TASK-BETWEEN-CLAIMS",
                internal_sku="SKU-A",
                platform_name=PLATFORM,
                action_type=TaskActionType.UPDATE_PRICE,
                priority=1,
                task_status=TaskStatus.PENDING,
                created_at=now,
                origin_type=TaskOriginType.MANUAL,
                origin_ref_id="test:between-claims",
                target_price=Decimal("10.00"),
                dedupe_key="TASK-BETWEEN-CLAIMS",
            )
        )
        runtime_repository.insert_shadowbot_operation(
            ShadowBotOperationLedger(
                operation_id="OP-BETWEEN-CLAIMS",
                task_id="TASK-BETWEEN-CLAIMS",
                platform=PLATFORM,
                product_identity={"internal_sku": "SKU-A"},
                expected_old_price=Decimal("9.00"),
                target_price=Decimal("10.00"),
                status="NEEDS_RECONCILIATION",
                created_at=now,
                updated_at=now,
            )
        )
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    cycle = AutomationService(
        repository,
        handlers={
            PLATFORM_TRADE_DAY_SETTLEMENT: create_blocker,
            ONLINE_PULSE: lambda run, context: AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS
            ),
        },
        clock=MutableClock(now),
    ).run_cycle()

    assert cycle.completed_run_ids == (non_ui_run.run_id,)
    assert cycle.blocked_reason == "UNKNOWN_OR_RECONCILE_ACTIVE"
    assert repository.get_run(
        ui_run.run_id
    ).run_status is AutomationRunStatus.SCHEDULED


def test_pending_incident_action_preempts_scheduled_ui_automation(
    repository: AutomationRepository,
    runtime_repository: SQLiteRuntimeRepository,
) -> None:
    now = datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc)
    ui_job = _store_job(
        repository,
        _job(job_id="PULSE-URGENT-PREEMPT", job_type=ONLINE_PULSE),
        now=now,
    )
    ui_run = _ensure_run(repository, ui_job, scheduled_for=now)
    runtime_repository.insert_task(
        Task(
            task_id="TASK-INCIDENT-HUMAN-URGENT",
            internal_sku="SKU-A",
            platform_name=PLATFORM,
            action_type=TaskActionType.UPDATE_PRICE,
            priority=0,
            task_status=TaskStatus.PENDING,
            created_at=now,
            origin_type=TaskOriginType.MANUAL,
            origin_ref_id="incident-review:REVIEW-URGENT",
            expected_old_price=Decimal("8.00"),
            target_price=Decimal("10.00"),
            dedupe_key="TASK-INCIDENT-HUMAN-URGENT",
        )
    )
    calls: list[str] = []

    cycle = AutomationService(
        repository,
        handlers={
            ONLINE_PULSE: lambda run, context: (
                calls.append(run.run_id)
                or AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)
            )
        },
        clock=MutableClock(now),
    ).run_cycle()

    assert calls == []
    assert cycle.blocked_reason == "URGENT_INCIDENT_TASK_PENDING"
    assert repository.get_run(ui_run.run_id).run_status is AutomationRunStatus.SCHEDULED


def test_heartbeat_is_atomic_utf8_json(tmp_path: Path) -> None:
    path = tmp_path / "自动化服务" / "heartbeat.json"
    store = AutomationHeartbeatStore(path)
    payload = {
        "schema_version": "automation-heartbeat-1.0",
        "status": "RUNNING",
        "说明": "调度健康",
    }

    store.write(payload)

    assert store.read() == payload
    assert json.loads(path.read_text(encoding="utf-8"))["说明"] == "调度健康"
    assert list(path.parent.glob("*.tmp")) == []


def test_process_file_lock_rejects_second_instance(tmp_path: Path) -> None:
    lock_path = tmp_path / "automation.lock"
    with ProcessFileLock(lock_path):
        with pytest.raises(RuntimeError, match="已有实例"):
            with ProcessFileLock(lock_path):
                pass


def test_lock_identity_depends_on_runtime_db_not_heartbeat(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.sqlite3"
    first_heartbeat = tmp_path / "first" / "heartbeat.json"
    second_heartbeat = tmp_path / "second" / "heartbeat.json"

    first_lock = automation_service_lock_path(runtime_db)
    second_lock = automation_service_lock_path(runtime_db)

    assert first_heartbeat != second_heartbeat
    assert first_lock == second_lock
    with ProcessFileLock(first_lock):
        with pytest.raises(RuntimeError, match="已有实例"):
            with ProcessFileLock(second_lock):
                pass


def test_lock_conflict_does_not_overwrite_active_heartbeat(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.sqlite3"
    heartbeat = tmp_path / "heartbeat.json"
    active_payload = {"status": "RUNNING", "marker": "active-owner"}
    heartbeat.write_text(
        json.dumps(active_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"

    with ProcessFileLock(automation_service_lock_path(runtime_db)):
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/run_automation_service.py",
                "--runtime-db",
                str(runtime_db),
                "--heartbeat",
                str(heartbeat),
                "--once",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=60,
        )

    assert completed.returncode == 2
    assert json.loads(heartbeat.read_text(encoding="utf-8")) == active_payload


def test_once_cli_writes_stopped_heartbeat_and_default_jobs(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.sqlite3"
    heartbeat = tmp_path / "heartbeat.json"
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_automation_service.py",
            "--runtime-db",
            str(runtime_db),
            "--heartbeat",
            str(heartbeat),
            "--once",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert payload["status"] == "STOPPED"
    assert payload["mode"] == "SETTLEMENT_ONLY"
    assert payload["registered_job_types"] == [
        "PLATFORM_TRADE_DAY_SETTLEMENT",
        "SALES_PLAN_INPUT_BUILD",
    ]
    runtime_repository = SQLiteRuntimeRepository(runtime_db)
    stored_jobs = AutomationRepository(runtime_repository).list_jobs()
    assert len(stored_jobs) == 8
    assert stored_jobs[0].display_name


def test_cli_failure_after_lock_writes_redacted_failed_heartbeat(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.sqlite3"
    heartbeat = tmp_path / "heartbeat.json"
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_automation_service.py",
            "--runtime-db",
            str(runtime_db),
            "--heartbeat",
            str(heartbeat),
            "--platform-name",
            "",
            "--once",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 2
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert payload["status"] == "FAILED"
    assert payload["reason"] == "AUTOMATION_SERVICE_FAILED"
    assert str(tmp_path) not in payload["error_message"]


def test_job_static_identity_is_immutable_for_existing_job_id(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    original = _job(job_id="STABLE", job_type=ONLINE_PULSE)
    _store_job(repository, original, now=now)

    with pytest.raises(ValueError, match="cannot change job_type"):
        _store_job(
            repository,
            _job(job_id="STABLE", job_type=FULL_MARKET_SCAN),
            now=now,
        )
    with pytest.raises(ValueError, match="cannot change schedule"):
        _store_job(
            repository,
            _job(
                job_id="STABLE",
                job_type=ONLINE_PULSE,
                minutes=60,
            ),
            now=now,
        )


def test_default_job_bootstrap_rejects_existing_static_drift(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    ensure_default_automation_jobs(
        repository,
        platform_name=PLATFORM,
        now=now,
    )
    with repository.runtime_repository.connect_write() as connection:
        connection.execute(
            """
            UPDATE automation_jobs
            SET schedule_expression = '30'
            WHERE job_id = 'AUTOMATION-ONLINE-PULSE-10M'
            """
        )
        connection.commit()

    with pytest.raises(ValueError, match="unexpected schedule"):
        ensure_default_automation_jobs(
            repository,
            platform_name=PLATFORM,
            now=now,
        )


def test_service_hot_reloads_operational_time_policy_chain(
    repository: AutomationRepository,
    runtime_repository: SQLiteRuntimeRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    effective_from = now + timedelta(minutes=5)
    runtime_repository.replace_current_operational_time_policy(
        expected_current_policy_version=(
            DEFAULT_OPERATIONAL_TIME_POLICY_VERSION
        ),
        successor_policy_version="CN_SINGLE_PLATFORM_2026_V2",
        effective_from=effective_from,
        platform_cutoff_local_time="19:00",
        seller_cutoff_local_time="21:00",
        peak_start_local_time="17:00",
        created_by="pytest",
    )
    clock = MutableClock(now)
    service = AutomationService(
        repository,
        handlers={},
        clock=clock,
    )

    service.run_cycle()
    clock.value = now + timedelta(minutes=10)
    service.run_cycle()

    runs = repository.list_runs(job_id=job.job_id)
    assert [run.time_policy_version for run in runs] == [
        "CN_SINGLE_PLATFORM_2026_V2",
        DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
    ]
