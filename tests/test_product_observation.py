from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier

import pytest

from app.automation_models import AutomationRunClaim, AutomationRunOutcome
from app.enums import AutomationRunStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.product_mapping import compile_product_mapping_rows
from app.services.product_observation import (
    LISTING_STATUS_SCAN,
    ONLINE_PULSE,
    PRODUCT_OBSERVATION_INPUT_SCHEMA_VERSION,
    ProductObservationBatchInput,
    ProductObservationError,
    ProductObservationImporter,
    ProductObservationInput,
    listing_snapshot_to_observation_batch,
    product_observation_batch_from_payload,
)
from app.services.operational_time import (
    OperationalTimePolicy,
    OperationalTimeService,
)


PLATFORM = "蚂蚁花团供应商"
TEST_OWNER = "product-observation-test-owner"
TEST_NOW = datetime(2026, 7, 29, 12, 2, tzinfo=timezone.utc)


def _evidence(seed: str) -> str:
    return "sha256:" + seed[0].lower() * 64


def _mapping_row(
    mapping_id: str,
    product_name: str,
    grade: str,
    sku: str,
) -> dict[str, object]:
    return {
        "mapping_id": mapping_id,
        "mapping_kind": "PRODUCT",
        "platform_name": PLATFORM,
        "platform_product_name": product_name,
        "grade": grade,
        "internal_sku": sku,
        "candidate_internal_sku": "",
        "mapping_status": "VERIFIED",
    }


def _repository_with_run(tmp_path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    now = "2026-07-29T08:00:00+00:00"
    with closing(repository.connect_write()) as connection:
        for job_type, suffix in (
            (ONLINE_PULSE, "pulse"),
            (LISTING_STATUS_SCAN, "listing"),
        ):
            connection.execute(
                """
                INSERT INTO automation_jobs(
                    job_id, job_type, display_name, enabled,
                    schedule_kind, schedule_expression, priority,
                    config_json, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?, 100, '{}', ?, ?)
                """,
                (
                    f"job-{suffix}-scan",
                    job_type,
                    f"{job_type} test job",
                    "INTERVAL",
                    "10m",
                    now,
                    now,
                ),
            )
            for run_number in (1, 2):
                connection.execute(
                    """
                    INSERT INTO automation_runs(
                        run_id, job_id, job_type, logical_run_key,
                        run_status, platform_name, platform_trade_date,
                        seller_operation_date, seller_phase,
                        time_policy_version, scheduled_for, started_at,
                        lease_owner, lease_version, lease_expires_at,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        f"run-{suffix}-scan-{run_number}",
                        f"job-{suffix}-scan",
                        job_type,
                        f"{suffix}-scan-20260729-{run_number}",
                        "RUNNING",
                        PLATFORM,
                        "2026-07-29",
                        "2026-07-29",
                        "NORMAL_SALES",
                        "CN_SINGLE_PLATFORM_2026_V1",
                        now,
                        now,
                        TEST_OWNER,
                        1,
                        (TEST_NOW + timedelta(hours=1)).isoformat(),
                        now,
                        now,
                    ),
                )
        connection.commit()
    return repository


class _ClaimingProductObservationImporter(ProductObservationImporter):
    """Keep legacy contract tests concise while exercising fenced writes."""

    def import_batch(
        self,
        batch: ProductObservationBatchInput,
        *,
        claim: AutomationRunClaim | None = None,
    ):
        if claim is None:
            run = AutomationRepository(self.repository).get_run(
                batch.automation_run_id
            )
            if run is None or run.lease_expires_at is None:
                raise AssertionError("test Automation Run lease is missing")
            claim = AutomationRunClaim(
                run=run,
                owner_token=run.lease_owner,
                lease_version=run.lease_version,
                lease_expires_at=run.lease_expires_at,
                reclaimed=False,
            )
        return super().import_batch(batch, claim=claim)


def _importer(tmp_path) -> tuple[
    SQLiteRuntimeRepository,
    ProductObservationImporter,
]:
    repository = _repository_with_run(tmp_path)
    mappings = compile_product_mapping_rows(
        [
            _mapping_row("MAP-AISHA-A", "艾莎", "A", "AISHA-A"),
            _mapping_row(
                "MAP-CAPPUCCINO-B",
                "卡布奇诺",
                "B",
                "CAPPUCCINO-B",
            ),
        ],
        source_workbook_sha256="a" * 64,
    )
    return repository, _ClaimingProductObservationImporter(
        repository,
        mappings=mappings,
        clock=lambda: TEST_NOW,
    )


def _set_run_status(
    repository: SQLiteRuntimeRepository,
    *,
    run_id: str,
    run_status: str,
) -> None:
    with closing(repository.connect_write()) as connection:
        connection.execute(
            "UPDATE automation_runs SET run_status = ? WHERE run_id = ?",
            (run_status, run_id),
        )
        connection.commit()


def _stored_claim(
    repository: SQLiteRuntimeRepository,
    run_id: str,
) -> AutomationRunClaim:
    run = AutomationRepository(repository).get_run(run_id)
    if run is None or run.lease_expires_at is None:
        raise AssertionError("test Automation Run lease is missing")
    return AutomationRunClaim(
        run=run,
        owner_token=run.lease_owner,
        lease_version=run.lease_version,
        lease_expires_at=run.lease_expires_at,
        reclaimed=False,
    )


def _batch(
    *,
    batch_id: str = "batch-1",
    scan_type: str = ONLINE_PULSE,
    items: tuple[ProductObservationInput, ...],
) -> ProductObservationBatchInput:
    is_pulse = scan_type == ONLINE_PULSE
    return ProductObservationBatchInput(
        observation_batch_id=batch_id,
        automation_run_id=(
            "run-pulse-scan-1" if is_pulse else "run-listing-scan-1"
        ),
        platform_name=PLATFORM,
        scan_type=scan_type,
        batch_status="ACCEPTED",
        scan_started_at=datetime(
            2026,
            7,
            29,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        scan_completed_at=datetime(
            2026,
            7,
            29,
            12,
            1,
            tzinfo=timezone.utc,
        ),
        requested_scope={
            "pages": ["online"] if is_pulse else ["online", "waiting"]
        },
        scope_complete=True,
        end_marker_verified=True,
        items=items,
    )


def test_terminal_run_allows_exact_batch_idempotent_replay(tmp_path) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        batch_id="batch-terminal-exact",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("1"),
            ),
        ),
    )
    first = importer.import_batch(batch)
    _set_run_status(
        repository,
        run_id="run-pulse-scan-1",
        run_status="SUCCESS",
    )

    replay = importer.import_batch(batch)

    assert replay.already_imported
    assert replay.observation_batch_id == first.observation_batch_id
    assert replay.content_sha256 == first.content_sha256


def test_terminal_run_allows_new_batch_id_same_content_replay(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        batch_id="batch-terminal-content",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("2"),
            ),
        ),
    )
    first = importer.import_batch(batch)
    _set_run_status(
        repository,
        run_id="run-pulse-scan-1",
        run_status="SUCCESS",
    )

    replay = importer.import_batch(
        replace(
            batch,
            observation_batch_id="batch-terminal-content-retry",
        )
    )

    assert replay.already_imported
    assert replay.observation_batch_id == first.observation_batch_id
    assert replay.content_sha256 == first.content_sha256
    with closing(repository.connect_read()) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM product_observation_batches"
            ).fetchone()[0]
            == 1
        )


def test_terminal_run_rejects_new_batch_id_with_different_content(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        batch_id="batch-terminal-new-content",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                observed_inventory=10,
                evidence_sha256=_evidence("3"),
            ),
        ),
    )
    importer.import_batch(batch)
    _set_run_status(
        repository,
        run_id="run-pulse-scan-1",
        run_status="SUCCESS",
    )
    changed = replace(
        batch,
        observation_batch_id="batch-terminal-new-content-retry",
        items=(
            replace(
                batch.items[0],
                observed_inventory=9,
            ),
        ),
    )

    with pytest.raises(ProductObservationError, match="not accepting"):
        importer.import_batch(changed)


def test_terminal_idempotent_replay_still_validates_run_identity(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        batch_id="batch-terminal-run-identity",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("4"),
            ),
        ),
    )
    importer.import_batch(batch)
    with closing(repository.connect_write()) as connection:
        connection.execute(
            """
            UPDATE automation_runs
            SET run_status = 'SUCCESS', platform_name = '其他平台'
            WHERE run_id = ?
            """,
            ("run-pulse-scan-1",),
        )
        connection.commit()

    with pytest.raises(ProductObservationError, match="platform"):
        importer.import_batch(batch)


def test_live_claim_can_write_observation_and_complete_run(tmp_path) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        batch_id="batch-live-claim",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026, 7, 29, 9, 30, tzinfo=timezone.utc
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("7"),
            ),
        ),
    )
    claim = _stored_claim(repository, batch.automation_run_id)

    result = importer.import_batch(batch, claim=claim)

    assert result.already_imported is False
    assert AutomationRepository(repository).finish_run(
        claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=TEST_NOW + timedelta(seconds=1),
    )


def test_expired_unreclaimed_claim_cannot_write_observation(tmp_path) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        batch_id="batch-expired-claim",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026, 7, 29, 9, 30, tzinfo=timezone.utc
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("8"),
            ),
        ),
    )
    claim = _stored_claim(repository, batch.automation_run_id)
    importer.clock = lambda: claim.lease_expires_at

    with pytest.raises(ProductObservationError, match="lease is not live"):
        importer.import_batch(batch, claim=claim)

    with closing(repository.connect_read()) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM product_observation_batches"
            ).fetchone()[0]
            == 0
        )


def test_reclaimed_old_owner_cannot_write_second_fact_batch(tmp_path) -> None:
    repository, importer = _importer(tmp_path)
    first = _batch(
        batch_id="batch-before-reclaim",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026, 7, 29, 9, 30, tzinfo=timezone.utc
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("9"),
            ),
        ),
    )
    old_claim = _stored_claim(repository, first.automation_run_id)
    importer.import_batch(first, claim=old_claim)
    with closing(repository.connect_write()) as connection:
        connection.execute(
            """
            UPDATE automation_runs
            SET lease_owner = 'new-owner',
                lease_version = lease_version + 1,
                lease_expires_at = ?
            WHERE run_id = ?
            """,
            (
                (TEST_NOW + timedelta(hours=2)).isoformat(),
                first.automation_run_id,
            ),
        )
        connection.commit()
    late = replace(
        first,
        observation_batch_id="batch-late-old-owner",
        items=(
            replace(
                first.items[0],
                evidence_sha256=_evidence("a"),
            ),
        ),
    )

    with pytest.raises(ProductObservationError, match="lease is not live"):
        importer.import_batch(late, claim=old_claim)
    new_claim = _stored_claim(repository, first.automation_run_id)
    with pytest.raises(
        ProductObservationError,
        match="already has different observation content",
    ):
        importer.import_batch(late, claim=new_claim)

    with closing(repository.connect_read()) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM product_observation_batches"
            ).fetchone()[0]
            == 1
        )


def test_online_pulse_writes_positive_observations_only_and_is_idempotent(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                observed_price=Decimal("12.50"),
                observed_inventory=18,
                evidence_sha256=_evidence("a"),
            ),
        )
    )

    first = importer.import_batch(batch)
    second = importer.import_batch(batch)

    assert first.item_count == 1
    assert not first.already_imported
    assert second.already_imported
    assert second.content_sha256 == first.content_sha256
    with closing(repository.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT internal_sku, mapping_status, observed_online
            FROM product_observation_items
            ORDER BY observation_item_id
            """
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("AISHA-A", "VERIFIED", 1)
        ]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM listing_status"
            ).fetchone()[0]
            == 0
        )


def test_online_pulse_rejects_negative_observation(tmp_path) -> None:
    _, importer = _importer(tmp_path)
    batch = _batch(
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=False,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("b"),
            ),
        )
    )

    with pytest.raises(
        ProductObservationError,
        match="positive observations only",
    ):
        importer.import_batch(batch)


def test_online_pulse_json_boundary_parses_exact_values(tmp_path) -> None:
    repository, importer = _importer(tmp_path)
    payload = {
        "schema_version": PRODUCT_OBSERVATION_INPUT_SCHEMA_VERSION,
        "observation_batch_id": "batch-json-pulse",
        "automation_run_id": "run-pulse-scan-1",
        "platform_name": PLATFORM,
        "scan_type": ONLINE_PULSE,
        "batch_status": "ACCEPTED",
        "scan_started_at": "2026-07-29T09:00:00+00:00",
        "scan_completed_at": "2026-07-29T09:00:02+00:00",
        "requested_scope": {"pages": ["online"]},
        "scope_complete": True,
        "end_marker_verified": True,
        "items": [
            {
                "platform_product_name": "艾莎",
                "grade": "A",
                "observed_at": "2026-07-29T09:00:01+00:00",
                "observed_online": True,
                "page_identity_key": "online:艾莎:A",
                "observed_price": "12.50",
                "observed_inventory": 18,
                "evidence_sha256": _evidence("c"),
            }
        ],
    }

    batch = product_observation_batch_from_payload(payload)
    result = importer.import_batch(batch)

    assert result.item_count == 1
    with closing(repository.connect_read()) as connection:
        row = connection.execute(
            """
            SELECT observed_price, observed_inventory
            FROM product_observation_items
            WHERE observation_batch_id = ?
            """,
            ("batch-json-pulse",),
        ).fetchone()
    assert tuple(row) == ("12.50", 18)


def test_listing_scan_calculates_each_items_18_and_20_boundaries(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    batch = _batch(
        batch_id="batch-boundaries",
        scan_type=LISTING_STATUS_SCAN,
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    10,
                    0,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("d"),
            ),
            ProductObservationInput(
                platform_product_name="卡布奇诺",
                grade="B",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    12,
                    0,
                    tzinfo=timezone.utc,
                ),
                observed_online=False,
                page_identity_key="waiting:卡布奇诺:B",
                evidence_sha256=_evidence("e"),
            ),
        ),
    )

    importer.import_batch(batch)

    with closing(repository.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT platform_product_name, platform_trade_date,
                   seller_operation_date, seller_phase,
                   observed_online
            FROM product_observation_items
            WHERE observation_batch_id = ?
            ORDER BY observed_at
            """,
            ("batch-boundaries",),
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (
            "艾莎",
            "2026-07-30",
            "2026-07-29",
            "DELIVERY_OVERLAP",
            1,
        ),
        (
            "卡布奇诺",
            "2026-07-30",
            "2026-07-30",
            "NORMAL_SALES",
            0,
        ),
    ]


def test_same_batch_id_with_different_content_is_rejected(tmp_path) -> None:
    _, importer = _importer(tmp_path)
    first = _batch(
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                evidence_sha256=_evidence("f"),
            ),
        )
    )
    changed = _batch(
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=datetime(
                    2026,
                    7,
                    29,
                    9,
                    30,
                    tzinfo=timezone.utc,
                ),
                observed_online=True,
                page_identity_key="online:艾莎:A",
                observed_inventory=99,
                evidence_sha256=_evidence("f"),
            ),
        )
    )

    importer.import_batch(first)
    with pytest.raises(
        ProductObservationError,
        match="different envelope or content",
    ):
        importer.import_batch(changed)


def test_same_result_content_within_one_run_is_not_accumulated(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    first_item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("1"),
    )
    second_item = ProductObservationInput(
        platform_product_name="卡布奇诺",
        grade="B",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            31,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:卡布奇诺:B",
        evidence_sha256=_evidence("2"),
    )
    first = _batch(
        batch_id="batch-content-first",
        items=(first_item, second_item),
    )
    retry = _batch(
        batch_id="batch-content-retry",
        items=(second_item, first_item),
    )
    first_result = importer.import_batch(first)
    retry_result = importer.import_batch(retry)

    assert retry_result.already_imported
    assert retry_result.observation_batch_id == first_result.observation_batch_id
    assert retry_result.content_sha256 == first_result.content_sha256
    with closing(repository.connect_read()) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM product_observation_batches"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM product_observation_items"
            ).fetchone()[0]
            == 2
        )


def test_same_result_content_across_runs_preserves_each_run_audit(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("a"),
    )
    first = _batch(batch_id="batch-run-1", items=(item,))
    second = replace(
        _batch(batch_id="batch-run-2", items=(item,)),
        automation_run_id="run-pulse-scan-2",
    )

    first_result = importer.import_batch(first)
    second_result = importer.import_batch(second)

    assert not first_result.already_imported
    assert not second_result.already_imported
    assert first_result.content_sha256 == second_result.content_sha256
    with closing(repository.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT automation_run_id, observation_batch_id
            FROM product_observation_batches
            ORDER BY automation_run_id
            """
        ).fetchall()
        item_count = connection.execute(
            "SELECT COUNT(*) FROM product_observation_items"
        ).fetchone()[0]
    assert [tuple(row) for row in rows] == [
        ("run-pulse-scan-1", "batch-run-1"),
        ("run-pulse-scan-2", "batch-run-2"),
    ]
    assert item_count == 2


def test_concurrent_same_run_content_retry_writes_one_fact_set(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("b"),
    )
    batches = (
        _batch(batch_id="batch-concurrent-1", items=(item,)),
        _batch(batch_id="batch-concurrent-2", items=(item,)),
    )
    start = Barrier(2)

    def import_after_barrier(
        batch: ProductObservationBatchInput,
    ):
        start.wait()
        return importer.import_batch(batch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(import_after_barrier, batches))

    assert {result.already_imported for result in results} == {False, True}
    assert len({result.observation_batch_id for result in results}) == 1
    with closing(repository.connect_read()) as connection:
        batch_count = connection.execute(
            "SELECT COUNT(*) FROM product_observation_batches"
        ).fetchone()[0]
        item_count = connection.execute(
            "SELECT COUNT(*) FROM product_observation_items"
        ).fetchone()[0]
    assert batch_count == 1
    assert item_count == 1


def test_listing_scope_page_order_is_normalized_before_hashing(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("c"),
    )
    first = _batch(
        batch_id="batch-pages-normal",
        scan_type=LISTING_STATUS_SCAN,
        items=(item,),
    )
    retry = replace(
        first,
        observation_batch_id="batch-pages-reversed",
        requested_scope={"pages": ["waiting", "online"]},
    )

    first_result = importer.import_batch(first)
    retry_result = importer.import_batch(retry)

    assert retry_result.already_imported
    assert retry_result.observation_batch_id == first_result.observation_batch_id
    assert retry_result.content_sha256 == first_result.content_sha256
    with closing(repository.connect_read()) as connection:
        row = connection.execute(
            """
            SELECT requested_scope_json
            FROM product_observation_batches
            WHERE observation_batch_id = ?
            """,
            (first_result.observation_batch_id,),
        ).fetchone()
    assert row["requested_scope_json"] == '{"pages":["online","waiting"]}'


@pytest.mark.parametrize(
    (
        "batch_status",
        "scope_complete",
        "end_marker_verified",
        "with_items",
        "error_code",
        "error_message",
    ),
    [
        ("ACCEPTED", True, True, True, "", ""),
        ("PARTIAL", False, False, True, "PAGE_PARTIAL", "部分页面失败"),
        ("UNAVAILABLE", False, False, False, "PLATFORM_UNAVAILABLE", ""),
        ("FAILED", False, False, False, "SCAN_FAILED", "扫描失败"),
    ],
)
def test_batch_status_matrix_accepts_legal_combinations(
    tmp_path,
    batch_status: str,
    scope_complete: bool,
    end_marker_verified: bool,
    with_items: bool,
    error_code: str,
    error_message: str,
) -> None:
    _, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("d"),
    )
    batch = replace(
        _batch(
            batch_id=f"batch-legal-{batch_status.lower()}",
            items=(item,) if with_items else (),
        ),
        batch_status=batch_status,
        scope_complete=scope_complete,
        end_marker_verified=end_marker_verified,
        error_code=error_code,
        error_message=error_message,
    )

    result = importer.import_batch(batch)

    assert result.item_count == int(with_items)


@pytest.mark.parametrize(
    (
        "batch_status",
        "scope_complete",
        "end_marker_verified",
        "with_items",
        "error_code",
        "error_message",
        "message",
    ),
    [
        ("ACCEPTED", True, True, True, "UNEXPECTED", "", "error fields"),
        ("ACCEPTED", True, True, True, "", "unexpected", "error fields"),
        ("PARTIAL", False, False, True, "", "partial", "error_code"),
        ("UNAVAILABLE", True, False, False, "UNAVAILABLE", "", "scope_complete"),
        ("UNAVAILABLE", False, False, False, "", "", "error_code"),
        ("FAILED", True, False, False, "FAILED", "", "scope_complete"),
        ("FAILED", False, False, False, "", "", "error_code"),
        ("FAILED", False, False, True, "FAILED", "", "observations"),
    ],
)
def test_batch_status_matrix_rejects_contradictory_combinations(
    tmp_path,
    batch_status: str,
    scope_complete: bool,
    end_marker_verified: bool,
    with_items: bool,
    error_code: str,
    error_message: str,
    message: str,
) -> None:
    _, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("e"),
    )
    batch = replace(
        _batch(
            batch_id=f"batch-invalid-{batch_status.lower()}-{message}",
            items=(item,) if with_items else (),
        ),
        batch_status=batch_status,
        scope_complete=scope_complete,
        end_marker_verified=end_marker_verified,
        error_code=error_code,
        error_message=error_message,
    )

    with pytest.raises(ProductObservationError, match=message):
        importer.import_batch(batch)


def test_scan_type_scope_and_run_job_type_are_strongly_bound(
    tmp_path,
) -> None:
    _, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("3"),
    )
    wrong_run = _batch(batch_id="batch-wrong-run", items=(item,))
    wrong_run = replace(
        wrong_run,
        automation_run_id="run-listing-scan-1",
    )

    with pytest.raises(ProductObservationError, match="job_type"):
        importer.import_batch(wrong_run)

    wrong_scope = _batch(
        batch_id="batch-wrong-scope",
        scan_type=LISTING_STATUS_SCAN,
        items=(item,),
    )
    wrong_scope = replace(
        wrong_scope,
        requested_scope={"pages": ["online"]},
    )
    with pytest.raises(ProductObservationError, match="online and waiting"):
        importer.import_batch(wrong_scope)


def test_run_status_and_time_policy_are_strongly_bound(tmp_path) -> None:
    repository, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("8"),
    )
    with closing(repository.connect_write()) as connection:
        connection.execute(
            "UPDATE automation_runs SET run_status = 'SUCCESS' WHERE run_id = ?",
            ("run-pulse-scan-1",),
        )
        connection.commit()

    with pytest.raises(ProductObservationError, match="not accepting"):
        importer.import_batch(
            _batch(batch_id="batch-finished-run", items=(item,))
        )

    with closing(repository.connect_write()) as connection:
        connection.execute(
            "UPDATE automation_runs SET run_status = 'RUNNING' WHERE run_id = ?",
            ("run-pulse-scan-1",),
        )
        connection.commit()

    mismatched_time_importer = _ClaimingProductObservationImporter(
        repository,
        mappings=importer.mappings,
        operational_time=OperationalTimeService(
            policy=OperationalTimePolicy(
                policy_version="TEST_POLICY_V2"
            )
        ),
        clock=lambda: TEST_NOW,
    )
    with pytest.raises(ProductObservationError, match="time policy"):
        mismatched_time_importer.import_batch(
            _batch(batch_id="batch-wrong-time-policy", items=(item,))
        )


def test_run_platform_is_strongly_bound(tmp_path) -> None:
    _, importer = _importer(tmp_path)
    item = ProductObservationInput(
        platform_product_name="艾莎",
        grade="A",
        observed_at=datetime(
            2026,
            7,
            29,
            9,
            30,
            tzinfo=timezone.utc,
        ),
        observed_online=True,
        page_identity_key="online:艾莎:A",
        evidence_sha256=_evidence("9"),
    )

    with pytest.raises(ProductObservationError, match="platform"):
        importer.import_batch(
            replace(
                _batch(batch_id="batch-wrong-platform", items=(item,)),
                platform_name="其他平台",
            )
        )


@pytest.mark.parametrize(
    ("observed_at", "price", "evidence", "message"),
    [
        (
            datetime(2026, 7, 29, 8, 59, tzinfo=timezone.utc),
            None,
            _evidence("4"),
            "scan interval",
        ),
        (
            datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc),
            Decimal("-1.00"),
            _evidence("5"),
            "canonical positive",
        ),
        (
            datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc),
            Decimal("NaN"),
            _evidence("6"),
            "canonical positive",
        ),
        (
            datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc),
            Decimal("Infinity"),
            _evidence("7"),
            "canonical positive",
        ),
        (
            datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc),
            Decimal("12.5"),
            _evidence("8"),
            "canonical positive",
        ),
        (
            datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc),
            Decimal("12.50"),
            "not-a-sha",
            "evidence_sha256",
        ),
    ],
)
def test_observation_rejects_invalid_time_price_and_evidence(
    tmp_path,
    observed_at: datetime,
    price: Decimal | None,
    evidence: str,
    message: str,
) -> None:
    _, importer = _importer(tmp_path)
    batch = _batch(
        batch_id=f"invalid-{message}-{evidence[:4]}",
        items=(
            ProductObservationInput(
                platform_product_name="艾莎",
                grade="A",
                observed_at=observed_at,
                observed_online=True,
                page_identity_key="online:艾莎:A",
                observed_price=price,
                evidence_sha256=evidence,
            ),
        ),
    )

    with pytest.raises(ProductObservationError, match=message):
        importer.import_batch(batch)


def test_task13_complete_snapshot_adapts_both_pages_to_v14_items(
    tmp_path,
) -> None:
    repository, importer = _importer(tmp_path)
    snapshot = {
        "schema_version": "shadowbot-listing-sync-snapshot-1.0",
        "snapshot_id": "SNAPSHOT-ADAPTER-1",
        "platform_name": PLATFORM,
        "execution_attempt_id": "ATTEMPT-ADAPTER-1",
        "mapping_source_version": "sha256:" + "a" * 64,
        "result_id": "RESULT-ADAPTER-1",
        "scan_started_at": "2026-07-29T09:00:00+00:00",
        "scan_completed_at": "2026-07-29T09:00:03+00:00",
        "online_scan_started_at": "2026-07-29T09:00:00+00:00",
        "online_scan_completed_at": "2026-07-29T09:00:01+00:00",
        "waiting_scan_started_at": "2026-07-29T09:00:01+00:00",
        "waiting_scan_completed_at": "2026-07-29T09:00:03+00:00",
        "online_scan_complete": True,
        "waiting_scan_complete": True,
        "online_end_marker_verified": True,
        "waiting_end_marker_verified": True,
        "snapshot_complete": True,
        "instruction_hash": "sha256:" + "b" * 64,
        "status": "VERIFIED",
        "error_code": "",
        "evidence_manifest_sha256": "sha256:" + "c" * 64,
        "items": [
            {
                "snapshot_item_id": "SNAPSHOT-ADAPTER-1-ITEM-1",
                "internal_sku": "AISHA-A",
                "product_name": "艾莎",
                "grade": "A",
                "page_identity_key": "platform|name:艾莎|grade:A",
                "affected_internal_skus": ["AISHA-A"],
                "online_occurrences": 1,
                "waiting_occurrences": 1,
                "mapping_ambiguous": False,
                "listing_location": "both",
                "online_row_identities": ["online:row:1"],
                "waiting_row_identities": ["waiting:row:1"],
                "online_observed_price": "21.00",
                "waiting_observed_price": "20.00",
                "online_observed_inventory": 9,
                "waiting_observed_inventory": 8,
                "diagnostic_code": "PRESENT_IN_BOTH_LISTS",
                "online_observed_at": "2026-07-29T09:00:01+00:00",
                "waiting_observed_at": "2026-07-29T09:00:02+00:00",
            }
        ],
    }

    batch = listing_snapshot_to_observation_batch(
        snapshot,
        automation_run_id="run-listing-scan-1",
    )
    result = importer.import_batch(batch)

    assert batch.scan_type == LISTING_STATUS_SCAN
    assert batch.requested_scope["source_snapshot_id"] == (
        "SNAPSHOT-ADAPTER-1"
    )
    assert result.item_count == 2
    with closing(repository.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT observed_online, observed_price, observed_inventory,
                   mapping_status
            FROM product_observation_items
            WHERE observation_batch_id = ?
            ORDER BY observed_online DESC
            """,
            (batch.observation_batch_id,),
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (1, "21.00", 9, "VERIFIED"),
        (0, "20.00", 8, "VERIFIED"),
    ]
