from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.enums import (
    DataQualityLevel,
    FactSource,
    SellerPhase,
    SummaryStatus,
)
from app.operational_models import TradeDaySummaryInput
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.trade_day_summary import TradeDaySummaryService


class AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(
            2026,
            7,
            29,
            12,
            0,
            tzinfo=timezone.utc,
        )

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


@pytest.fixture
def summary_service(
    tmp_path: Path,
) -> tuple[
    TradeDaySummaryService,
    OperationalSummaryRepository,
    SQLiteRuntimeRepository,
]:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    repository = OperationalSummaryRepository(runtime)
    return (
        TradeDaySummaryService(repository, clock=AdvancingClock()),
        repository,
        runtime,
    )


def _input(name: str) -> tuple[TradeDaySummaryInput, ...]:
    return (
        TradeDaySummaryInput(
            input_type="BATCH",
            input_ref_id=name,
            input_sha256=f"sha256:{name}",
        ),
    )


def _insert_blocking_incident(
    runtime: SQLiteRuntimeRepository,
    summary_id: str,
    *,
    incident_id: str,
) -> None:
    now = "2026-07-29T12:00:00+00:00"
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO operational_incidents(
                incident_id, dedupe_key, category,
                source_type, source_ref_id,
                severity, incident_status, blocks_finalization,
                subject_type, subject_key, title,
                first_detected_at, last_detected_at,
                created_at, updated_at
            ) VALUES (
                ?, ?,
                'ORDER_DATA_INCONSISTENT',
                'TRADE_DAY_SUMMARY', ?,
                'S3', 'OPEN', 1,
                'SUMMARY', ?, 'unclassified difference',
                ?, ?, ?, ?
            )
            """,
            (
                incident_id,
                f"summary-blocker:{incident_id}",
                summary_id,
                summary_id,
                now,
                now,
                now,
                now,
            ),
        )


def _insert_scoped_blocking_incident(
    runtime: SQLiteRuntimeRepository,
    *,
    incident_id: str,
    category: str = "ORDER_DATA_INCONSISTENT",
    platform_name: str = "platform",
    platform_trade_date: str = "2026-07-29",
    subject_type: str = "PLATFORM",
    subject_key: str = "platform",
    source_type: str = "ORDER_SCAN",
    source_ref_id: str = "order-scan-run",
    severity: str = "S2",
) -> None:
    now = "2026-07-29T12:00:00+00:00"
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO operational_incidents(
                incident_id, dedupe_key, category,
                source_type, source_ref_id,
                severity, incident_status, blocks_finalization,
                platform_name, platform_trade_date,
                subject_type, subject_key, title,
                first_detected_at, last_detected_at,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, 'OPEN', 1, ?, ?, ?, ?,
                'scoped blocker', ?, ?, ?, ?
            )
            """,
            (
                incident_id,
                f"scoped-blocker:{incident_id}",
                category,
                source_type,
                source_ref_id,
                severity,
                platform_name,
                platform_trade_date,
                subject_type,
                subject_key,
                now,
                now,
                now,
                now,
            ),
        )


def _create_provisional(service: TradeDaySummaryService):
    return service.create_provisional(
        platform_name="platform",
        platform_trade_date=date(2026, 7, 29),
        seller_operation_date=date(2026, 7, 30),
        seller_phase=SellerPhase.NORMAL_SALES,
        scope_type="PLATFORM",
        scope_key="platform",
        fact_source=FactSource.SCAN_ESTIMATED,
        quality_level=DataQualityLevel.SCAN_ESTIMATED_HIGH,
        sold_qty=12,
        order_count=None,
        transaction_amount_total=None,
        quality_reason="complete scan estimate",
        source_proportions={"SCAN_ESTIMATED": 1.0},
        input_manifest_sha256="sha256:provisional",
        mapping_version="mapping-v1",
        algorithm_version="estimate-v1",
        inputs=_input("scan-1"),
        changed_by="settlement-service",
    )


def _observe(service: TradeDaySummaryService, summary_id: str):
    return service.transition(
        summary_id,
        to_status=SummaryStatus.OBSERVED,
        fact_source=FactSource.ORDER_OBSERVED,
        quality_level=DataQualityLevel.ORDER_COMPLETE,
        sold_qty=11,
        order_count=5,
        transaction_amount_total=Decimal("123.45"),
        quality_reason="complete order batch",
        source_proportions={"ORDER_OBSERVED": 1.0},
        input_manifest_sha256="sha256:observed",
        mapping_version="mapping-v1",
        algorithm_version="orders-v1",
        inputs=_input("order-1"),
        changed_by="order-importer",
        trigger_type="ORDER_BATCH_ACCEPTED",
    )


def _reconcile(service: TradeDaySummaryService, summary_id: str):
    return service.transition(
        summary_id,
        to_status=SummaryStatus.RECONCILED,
        fact_source=FactSource.ORDER_OBSERVED,
        quality_level=DataQualityLevel.ORDER_COMPLETE,
        sold_qty=11,
        order_count=5,
        transaction_amount_total=Decimal("123.45"),
        quality_reason="difference classified",
        source_proportions={
            "ORDER_OBSERVED": 1.0,
            "SCAN_ESTIMATED": 1.0,
        },
        input_manifest_sha256="sha256:reconciled",
        mapping_version="mapping-v1",
        algorithm_version="reconcile-v1",
        inputs=(*_input("order-1"), *_input("scan-1")),
        changed_by="reconciliation-service",
        trigger_type="RECONCILIATION_COMPLETED",
    )


def _finalize(service: TradeDaySummaryService, summary_id: str):
    return service.transition(
        summary_id,
        to_status=SummaryStatus.FINAL,
        fact_source=FactSource.ORDER_OBSERVED,
        quality_level=DataQualityLevel.ORDER_COMPLETE,
        sold_qty=11,
        order_count=5,
        transaction_amount_total=Decimal("123.45"),
        quality_reason="final policy passed",
        source_proportions={
            "ORDER_OBSERVED": 1.0,
            "SCAN_ESTIMATED": 1.0,
        },
        input_manifest_sha256="sha256:final",
        mapping_version="mapping-v1",
        algorithm_version="final-v1",
        inputs=(*_input("order-1"), *_input("scan-1")),
        changed_by="finalization-service",
        trigger_type="FINALIZATION_POLICY",
        trigger_ref_id="test-finalization-policy-v1",
        finalization_validator=lambda connection: None,
    )


def test_summary_lifecycle_is_forward_only_and_audited(
    summary_service,
) -> None:
    service, repository, _ = summary_service

    provisional = _create_provisional(service)
    observed = _observe(service, provisional.summary.summary_id)
    reconciled = _reconcile(service, provisional.summary.summary_id)
    final = _finalize(service, provisional.summary.summary_id)

    assert provisional.changed
    assert observed.summary.summary_status is SummaryStatus.OBSERVED
    assert reconciled.summary.summary_status is SummaryStatus.RECONCILED
    assert final.summary.summary_status is SummaryStatus.FINAL
    assert final.summary.finalized_at is not None
    events = repository.list_events(provisional.summary.summary_id)
    assert [event.to_status for event in events] == [
        SummaryStatus.PROVISIONAL,
        SummaryStatus.OBSERVED,
        SummaryStatus.RECONCILED,
        SummaryStatus.FINAL,
    ]
    assert {
        item.input_ref_id
        for item in repository.list_inputs(provisional.summary.summary_id)
    } == {"scan-1", "order-1"}

    repeated = _finalize(service, provisional.summary.summary_id)
    assert not repeated.changed
    assert len(repository.list_events(provisional.summary.summary_id)) == 4


def test_summary_rejects_skip_and_unavailable_zero(
    summary_service,
) -> None:
    service, _, _ = summary_service
    provisional = _create_provisional(service)

    with pytest.raises(ValueError, match="Illegal summary transition"):
        service.transition(
            provisional.summary.summary_id,
            to_status=SummaryStatus.RECONCILED,
            fact_source=FactSource.ORDER_OBSERVED,
            quality_level=DataQualityLevel.ORDER_COMPLETE,
            sold_qty=1,
            order_count=1,
            transaction_amount_total=Decimal("1"),
            quality_reason="skip",
            source_proportions={"ORDER_OBSERVED": 1.0},
            input_manifest_sha256="sha256:skip",
            mapping_version="mapping-v1",
            algorithm_version="reconcile-v1",
            inputs=_input("order-skip"),
            changed_by="test",
            trigger_type="TEST",
        )

    with pytest.raises(ValueError, match="metrics NULL"):
        service.create_provisional(
            platform_name="platform",
            platform_trade_date=date(2026, 7, 30),
            seller_operation_date=date(2026, 7, 31),
            seller_phase=SellerPhase.NORMAL_SALES,
            scope_type="PLATFORM",
            scope_key="platform",
            fact_source=None,
            quality_level=DataQualityLevel.UNAVAILABLE,
            sold_qty=0,
            order_count=None,
            transaction_amount_total=None,
            quality_reason="unavailable",
            source_proportions={},
            input_manifest_sha256="sha256:unavailable",
            mapping_version="",
            algorithm_version="settlement-v1",
            inputs=(),
            changed_by="test",
        )


def test_finalization_is_blocked_by_open_s3_incident(
    summary_service,
) -> None:
    service, _, runtime = summary_service
    summary_id = _create_provisional(service).summary.summary_id
    _observe(service, summary_id)
    _reconcile(service, summary_id)
    _insert_blocking_incident(
        runtime,
        summary_id,
        incident_id="INCIDENT-1",
    )

    with pytest.raises(ValueError, match="blocking operational incidents"):
        _finalize(service, summary_id)


def test_finalization_requires_atomic_evidence_validator(
    summary_service,
) -> None:
    service, _, _ = summary_service
    summary_id = _create_provisional(service).summary.summary_id
    _observe(service, summary_id)
    _reconcile(service, summary_id)

    with pytest.raises(ValueError, match="atomic evidence validator"):
        service.transition(
            summary_id,
            to_status=SummaryStatus.FINAL,
            fact_source=FactSource.ORDER_OBSERVED,
            quality_level=DataQualityLevel.ORDER_COMPLETE,
            sold_qty=11,
            order_count=5,
            transaction_amount_total=Decimal("123.45"),
            quality_reason="missing validator",
            source_proportions={"ORDER_OBSERVED": 1.0},
            input_manifest_sha256="sha256:no-validator",
            mapping_version="mapping-v1",
            algorithm_version="final-v1",
            inputs=_input("order-1"),
            changed_by="test",
            trigger_type="FINALIZATION_POLICY",
            trigger_ref_id="policy-v1",
        )


def test_finalization_rechecks_concurrent_incident_in_write_transaction(
    summary_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, runtime = summary_service
    summary_id = _create_provisional(service).summary.summary_id
    _observe(service, summary_id)
    _reconcile(service, summary_id)
    original_transition = repository.transition
    writer_errors: list[BaseException] = []

    def insert_concurrently() -> None:
        try:
            _insert_blocking_incident(
                runtime,
                summary_id,
                incident_id="INCIDENT-CONCURRENT",
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            writer_errors.append(exc)

    def transition_after_concurrent_insert(**kwargs):
        writer = threading.Thread(
            target=insert_concurrently,
        )
        writer.start()
        writer.join(timeout=10)
        assert not writer.is_alive()
        assert not writer_errors
        return original_transition(**kwargs)

    monkeypatch.setattr(
        repository,
        "transition",
        transition_after_concurrent_insert,
    )

    with pytest.raises(ValueError, match="blocking operational incidents"):
        _finalize(service, summary_id)
    persisted = repository.get_summary(summary_id)
    assert persisted is not None
    assert persisted.summary_status is SummaryStatus.RECONCILED


def test_finalization_matches_platform_incident_without_summary_id(
    summary_service,
) -> None:
    service, _, runtime = summary_service
    _insert_scoped_blocking_incident(
        runtime,
        incident_id="INCIDENT-PLATFORM-PREEXISTING",
    )
    summary_id = _create_provisional(service).summary.summary_id
    _observe(service, summary_id)
    _reconcile(service, summary_id)

    with pytest.raises(ValueError, match="blocking operational incidents"):
        _finalize(service, summary_id)


def test_sku_price_incident_does_not_blanket_block_platform_total(
    summary_service,
) -> None:
    service, _, runtime = summary_service
    summary_id = _create_provisional(service).summary.summary_id
    _observe(service, summary_id)
    _reconcile(service, summary_id)
    _insert_scoped_blocking_incident(
        runtime,
        incident_id="INCIDENT-SKU-PRICE",
        category="PRICE_ANOMALY",
        subject_type="SKU",
        subject_key="SKU-1",
        source_type="PRICE_OBSERVATION",
        source_ref_id="price-observation-1",
        severity="S4",
    )

    result = _finalize(service, summary_id)

    assert result.summary.summary_status is SummaryStatus.FINAL


def test_selected_input_dependency_blocks_aggregate_finalization(
    summary_service,
) -> None:
    service, _, runtime = summary_service
    summary_id = _create_provisional(service).summary.summary_id
    _observe(service, summary_id)
    _reconcile(service, summary_id)
    _insert_scoped_blocking_incident(
        runtime,
        incident_id="INCIDENT-SKU-ORDER-INPUT",
        subject_type="SKU",
        subject_key="SKU-1",
        source_type="ORDER_INPUT",
        source_ref_id="order-1",
    )

    with pytest.raises(ValueError, match="blocking operational incidents"):
        _finalize(service, summary_id)


def test_provisional_material_change_is_atomically_audited(
    summary_service,
) -> None:
    service, repository, _ = summary_service
    original = _create_provisional(service).summary

    revision = service.transition(
        original.summary_id,
        to_status=SummaryStatus.PROVISIONAL,
        fact_source=FactSource.SCAN_ESTIMATED,
        quality_level=DataQualityLevel.SCAN_ESTIMATED_HIGH,
        sold_qty=13,
        order_count=None,
        transaction_amount_total=None,
        quality_reason="new complete scan",
        source_proportions={"SCAN_ESTIMATED": 1.0},
        input_manifest_sha256="sha256:provisional-revised",
        mapping_version="mapping-v2",
        algorithm_version="estimate-v2",
        inputs=_input("scan-2"),
        changed_by="settlement-service",
        trigger_type="SCAN_REPLACED",
    )

    assert revision.summary.summary_id == original.summary_id
    assert revision.summary.version_no == 1
    assert revision.summary.supersedes_summary_id is None
    assert revision.summary.summary_status is SummaryStatus.PROVISIONAL
    assert revision.summary.is_current
    assert revision.event is not None
    assert revision.event.from_status is SummaryStatus.PROVISIONAL
    assert revision.event.to_status is SummaryStatus.PROVISIONAL
    assert {
        item.input_ref_id
        for item in repository.list_inputs(original.summary_id)
    } == {"scan-2"}
    with closing(repository.runtime_repository.connect_read()) as connection:
        manifest_rows = connection.execute(
            """
            SELECT input_manifest_sha256, input_ref_id
            FROM platform_trade_day_summary_inputs
            WHERE summary_id = ?
            ORDER BY input_manifest_sha256, input_ref_id
            """,
            (original.summary_id,),
        ).fetchall()
    assert {
        (str(row["input_manifest_sha256"]), str(row["input_ref_id"]))
        for row in manifest_rows
    } == {
        ("sha256:provisional", "scan-1"),
        ("sha256:provisional-revised", "scan-2"),
    }

    repeated = service.transition(
        revision.summary.summary_id,
        to_status=SummaryStatus.PROVISIONAL,
        fact_source=FactSource.SCAN_ESTIMATED,
        quality_level=DataQualityLevel.SCAN_ESTIMATED_HIGH,
        sold_qty=13,
        order_count=None,
        transaction_amount_total=None,
        quality_reason="new complete scan",
        source_proportions={"SCAN_ESTIMATED": 1.0},
        input_manifest_sha256="sha256:provisional-revised",
        mapping_version="mapping-v2",
        algorithm_version="estimate-v2",
        inputs=_input("scan-2"),
        changed_by="settlement-service",
        trigger_type="SCAN_REPLACED",
    )
    assert not repeated.changed
    assert repeated.summary.summary_id == revision.summary.summary_id


def test_observed_material_change_creates_new_observed_version(
    summary_service,
) -> None:
    service, repository, _ = summary_service
    original_id = _create_provisional(service).summary.summary_id
    observed = _observe(service, original_id).summary

    revision = service.transition(
        observed.summary_id,
        to_status=SummaryStatus.OBSERVED,
        fact_source=FactSource.ORDER_OBSERVED,
        quality_level=DataQualityLevel.ORDER_COMPLETE,
        sold_qty=12,
        order_count=6,
        transaction_amount_total=Decimal("130.00"),
        quality_reason="replacement complete order batch",
        source_proportions={"ORDER_OBSERVED": 1.0},
        input_manifest_sha256="sha256:observed-revised",
        mapping_version="mapping-v2",
        algorithm_version="orders-v2",
        inputs=_input("order-2"),
        changed_by="order-importer",
        trigger_type="ORDER_BATCH_REPLACED",
    )

    old = repository.get_summary(original_id)
    assert old is not None
    assert not old.is_current
    assert revision.summary.version_no == 2
    assert revision.summary.supersedes_summary_id == observed.summary_id
    assert revision.summary.summary_status is SummaryStatus.OBSERVED
    assert revision.event is not None
    assert revision.event.from_status is SummaryStatus.OBSERVED
    assert revision.event.to_status is SummaryStatus.OBSERVED


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("summary_series_id", "SERIES-OTHER"),
        ("version_no", 99),
        ("supersedes_summary_id", "SUMMARY-OTHER"),
        ("platform_name", "other-platform"),
        ("platform_trade_date", "2099-01-01"),
        ("seller_operation_date", "2099-01-02"),
        ("seller_phase", "PEAK_SALES"),
        ("scope_type", "GRADE"),
        ("scope_key", "other-scope"),
        ("created_at", "2099-01-01T00:00:00+00:00"),
        ("updated_at", "2099-01-01T00:00:00+00:00"),
    ],
)
def test_final_database_trigger_protects_business_identity(
    summary_service,
    column: str,
    value: object,
) -> None:
    service, _, runtime = summary_service
    summary_id = _create_provisional(service).summary.summary_id
    _observe(service, summary_id)
    _reconcile(service, summary_id)
    _finalize(service, summary_id)

    with closing(runtime.connect_write()) as connection:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="FINAL summary content is immutable",
        ):
            connection.execute(
                f"""
                UPDATE platform_trade_day_summaries
                SET {column} = ?
                WHERE summary_id = ?
                """,
                (value, summary_id),
            )


def test_final_late_data_creates_observed_revision(
    summary_service,
) -> None:
    service, repository, runtime = summary_service
    original_id = _create_provisional(service).summary.summary_id
    _observe(service, original_id)
    _reconcile(service, original_id)
    original_final = _finalize(service, original_id).summary

    revision = service.revise_final(
        original_id,
        fact_source=FactSource.ORDER_OBSERVED,
        quality_level=DataQualityLevel.ORDER_COMPLETE,
        sold_qty=12,
        order_count=6,
        transaction_amount_total=Decimal("130.00"),
        quality_reason="late order batch",
        source_proportions={"ORDER_OBSERVED": 1.0},
        input_manifest_sha256="sha256:late-order",
        mapping_version="mapping-v1",
        algorithm_version="orders-v1",
        inputs=_input("order-late"),
        changed_by="order-importer",
    )

    old = repository.get_summary(original_id)
    assert old is not None
    assert old.summary_status is SummaryStatus.FINAL
    assert not old.is_current
    assert revision.summary.version_no == 2
    assert revision.summary.supersedes_summary_id == original_final.summary_id
    assert revision.summary.summary_status is SummaryStatus.OBSERVED
    assert revision.summary.is_current
    assert (
        repository.get_current_summary(original_final.summary_series_id)
        == revision.summary
    )
    with closing(runtime.connect_write()) as connection:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="FINAL summary content is immutable",
        ):
            connection.execute(
                """
                UPDATE platform_trade_day_summaries
                SET sold_qty = 999
                WHERE summary_id = ?
                """,
                (original_id,),
            )
