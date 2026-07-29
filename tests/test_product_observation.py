from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

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
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        now,
                        now,
                    ),
                )
        connection.commit()
    return repository


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
    return repository, ProductObservationImporter(
        repository,
        mappings=mappings,
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


def test_same_result_content_across_batch_ids_is_not_accumulated(
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
    retry = replace(retry, automation_run_id="run-pulse-scan-2")

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

    mismatched_time_importer = ProductObservationImporter(
        repository,
        mappings=importer.mappings,
        operational_time=OperationalTimeService(
            policy=OperationalTimePolicy(
                policy_version="TEST_POLICY_V2"
            )
        ),
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
