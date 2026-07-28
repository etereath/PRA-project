from __future__ import annotations

import sqlite3
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
        seller_received_amount=None,
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
        seller_received_amount=Decimal("123.45"),
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
        seller_received_amount=Decimal("123.45"),
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
        seller_received_amount=Decimal("123.45"),
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
            seller_received_amount=Decimal("1"),
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
            seller_received_amount=None,
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
    now = "2026-07-29T12:00:00+00:00"
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO operational_incidents(
                incident_id, dedupe_key, source_type, source_ref_id,
                severity, incident_status, blocks_finalization,
                subject_type, subject_key, title,
                first_detected_at, last_detected_at,
                created_at, updated_at
            ) VALUES (
                'INCIDENT-1', 'summary-blocker',
                'TRADE_DAY_SUMMARY', ?,
                'S2', 'OPEN', 1,
                'SUMMARY', ?, 'unclassified difference',
                ?, ?, ?, ?
            )
            """,
            (summary_id, summary_id, now, now, now, now),
        )

    with pytest.raises(ValueError, match="blocking S3/S4"):
        _finalize(service, summary_id)


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
        seller_received_amount=Decimal("130.00"),
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
