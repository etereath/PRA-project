from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.mayi_huatuan_order import (
    MAYI_HUATUAN_PLATFORM,
    MayiHuatuanOrderReadOnlyAdapter,
    page_capture_from_json,
)
from app.automation_models import AutomationJob, AutomationRunClaim
from app.enums import AutomationRunStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    CHILD_ONLY,
    FULL_MARKET_SCAN,
    INTERVAL_MINUTES,
    ORDER_SCAN,
)
from app.services.operational_time import OperationalTimeService
from app.services.order_observation import (
    OrderObservationBatchInput,
    OrderObservationError,
    OrderObservationImporter,
    order_identity_fingerprint,
    raw_observation_sha256,
)
from app.services.product_mapping import compile_product_mapping_rows


NOW = datetime(2026, 7, 31, 9, 5, tzinfo=timezone.utc)
SCHEDULED_FOR = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "order_observation"
    / "mayi_huatuan_complete.json"
)


class FixtureReader:
    def __init__(self, capture) -> None:
        self.capture = capture

    def read_orders_read_only(self, request):
        return self.capture


def _mapping_row(
    mapping_id: str,
    name: str,
    grade: str,
    *,
    status: str,
    sku: str = "",
    candidate: str = "",
) -> dict[str, object]:
    return {
        "mapping_id": mapping_id,
        "mapping_kind": "PRODUCT",
        "platform_name": MAYI_HUATUAN_PLATFORM,
        "platform_product_name": name,
        "grade": grade,
        "internal_sku": sku,
        "candidate_internal_sku": candidate,
        "mapping_status": status,
    }


def _mappings():
    return compile_product_mapping_rows(
        [
            _mapping_row(
                "MAP-SYN-A",
                "合成玫瑰甲",
                "A级",
                status="VERIFIED",
                sku="SYN-A",
            ),
            _mapping_row(
                "MAP-SYN-B",
                "合成玫瑰乙",
                "B级",
                status="AMBIGUOUS",
                candidate="SYN-B1",
            ),
        ],
        source_workbook_sha256="a" * 64,
    )


def _job(
    job_id: str,
    job_type: str,
    *,
    schedule_kind: str,
    enabled: bool,
) -> AutomationJob:
    return AutomationJob(
        job_id=job_id,
        job_type=job_type,
        display_name=job_type,
        enabled=enabled,
        schedule_kind=schedule_kind,
        schedule_expression=(
            "60" if schedule_kind == INTERVAL_MINUTES else "-"
        ),
        priority=50,
        config={
            "platform_name": MAYI_HUATUAN_PLATFORM,
            "catchup_policy": "LATEST_ONLY",
        },
    )


def _runtime_with_order_claim(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    repository = AutomationRepository(runtime)
    parent_job = repository.upsert_job(
        _job(
            "FULL",
            FULL_MARKET_SCAN,
            schedule_kind=INTERVAL_MINUTES,
            enabled=True,
        ),
        now=SCHEDULED_FOR,
    )
    child_job = repository.upsert_job(
        _job(
            "ORDER",
            ORDER_SCAN,
            schedule_kind=CHILD_ONLY,
            enabled=False,
        ),
        now=SCHEDULED_FOR,
    )
    parent = repository.ensure_run(
        job=parent_job,
        scheduled_for=SCHEDULED_FOR,
        time_context=OperationalTimeService().classify(SCHEDULED_FOR),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=SCHEDULED_FOR,
    )[0]
    parent_claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="parent-owner",
        now=SCHEDULED_FOR,
        lease_seconds=600,
    )
    assert parent_claim is not None
    child, _ = repository.ensure_child_run_fenced(
        parent_claim,
        child_job,
        relation_type="ORDER_SCAN_CHILD",
        now=SCHEDULED_FOR,
    )
    assert repository.finish_run(
        parent_claim,
        outcome=_success_outcome(),
        now=SCHEDULED_FOR + timedelta(seconds=1),
    )
    claim = repository.claim_run(
        run_id=child.run_id,
        owner_token="order-owner",
        now=SCHEDULED_FOR + timedelta(seconds=2),
        lease_seconds=600,
    )
    assert claim is not None
    return runtime, repository, claim


def _success_outcome():
    from app.automation_models import AutomationRunOutcome

    return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)


def _batch(claim: AutomationRunClaim) -> OrderObservationBatchInput:
    capture = page_capture_from_json(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )
    adapter = MayiHuatuanOrderReadOnlyAdapter(
        FixtureReader(capture),
        operational_time=OperationalTimeService(),
    )
    return adapter.scan(
        observation_batch_id="ORDER-BATCH-1",
        automation_run_id=claim.run.run_id,
        platform_name=MAYI_HUATUAN_PLATFORM,
        requested_platform_trade_date=date(2026, 7, 31),
    )


def _importer(runtime: SQLiteRuntimeRepository) -> OrderObservationImporter:
    return OrderObservationImporter(
        runtime,
        operational_time=OperationalTimeService(),
        clock=lambda: NOW,
    )


def test_import_preserves_duplicate_identity_multiset_and_mapping_states(
    tmp_path,
) -> None:
    runtime, _, claim = _runtime_with_order_claim(tmp_path)

    result = _importer(runtime).import_batch(
        _batch(claim),
        mappings=_mappings(),
        claim=claim,
    )

    assert result.item_count == 3
    assert sorted(result.occurrence_counts.values()) == [1, 2]
    duplicates = [
        item
        for item in result.items
        if item.platform_product_name == "合成玫瑰甲"
    ]
    assert [item.occurrence_no for item in duplicates] == [1, 2]
    assert duplicates[0].observation_item_id != duplicates[1].observation_item_id
    assert {item.mapping_status.value for item in result.items} == {
        "VERIFIED",
        "AMBIGUOUS",
    }
    assert result.batch_status == "PARTIAL"
    assert result.transaction_amount_total == Decimal("33.18")

    with closing(runtime.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT platform_name, trade_day_status,
                   order_identity_fingerprint, occurrence_no,
                   order_qty, order_transaction_amount
            FROM order_observation_items
            ORDER BY order_identity_fingerprint, occurrence_no
            """
        ).fetchall()
    assert len(rows) == 3
    assert all(str(row["platform_name"]) == MAYI_HUATUAN_PLATFORM for row in rows)
    assert all(str(row["trade_day_status"]) == "OPEN" for row in rows)


def test_import_preserves_unmapped_order_without_inventing_sku(
    tmp_path,
) -> None:
    runtime, _, claim = _runtime_with_order_claim(tmp_path)
    mappings = compile_product_mapping_rows(
        [
            _mapping_row(
                "MAP-OTHER",
                "其他合成商品",
                "A级",
                status="VERIFIED",
                sku="OTHER-SKU",
            )
        ],
        source_workbook_sha256="b" * 64,
    )

    result = _importer(runtime).import_batch(
        _batch(claim),
        mappings=mappings,
        claim=claim,
    )

    assert result.batch_status == "PARTIAL"
    assert {item.mapping_status.value for item in result.items} == {
        "UNMAPPED"
    }
    assert all(item.internal_sku is None for item in result.items)


def test_identity_is_stable_when_content_changes() -> None:
    created_at = datetime(2026, 7, 31, 9, 1, tzinfo=timezone.utc)
    observed_at = datetime(2026, 7, 31, 9, 2, tzinfo=timezone.utc)
    identity = order_identity_fingerprint(
        platform_name=MAYI_HUATUAN_PLATFORM,
        platform_trade_date=date(2026, 7, 31),
        order_created_at=created_at,
        platform_product_name="合成玫瑰甲",
        grade="A级",
    )
    first = raw_observation_sha256(
        platform_name=MAYI_HUATUAN_PLATFORM,
        platform_trade_date=date(2026, 7, 31),
        trade_day_status="OPEN",
        order_created_at=created_at,
        platform_product_name="合成玫瑰甲",
        grade="A级",
        order_qty=1,
        order_transaction_amount=Decimal("10"),
        observed_at=observed_at,
    )
    second = raw_observation_sha256(
        platform_name=MAYI_HUATUAN_PLATFORM,
        platform_trade_date=date(2026, 7, 31),
        trade_day_status="OPEN",
        order_created_at=created_at,
        platform_product_name="合成玫瑰甲",
        grade="A级",
        order_qty=2,
        order_transaction_amount=Decimal("20"),
        observed_at=observed_at,
    )

    assert identity.startswith("sha256:")
    assert first != second


def test_exact_replay_returns_original_even_after_run_is_terminal(
    tmp_path,
) -> None:
    runtime, repository, claim = _runtime_with_order_claim(tmp_path)
    importer = _importer(runtime)
    first = importer.import_batch(
        _batch(claim),
        mappings=_mappings(),
        claim=claim,
    )
    assert repository.finish_run(
        claim,
        _success_outcome(),
        now=NOW + timedelta(seconds=1),
    )

    replay = importer.import_batch(
        replace(_batch(claim), observation_batch_id="ORDER-BATCH-REPLAY"),
        mappings=_mappings(),
        claim=claim,
    )

    assert replay.replayed is True
    assert replay.observation_batch_id == first.observation_batch_id
    with closing(runtime.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_batches"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_items"
        ).fetchone()[0] == 3


def test_same_batch_id_with_different_content_is_conflict(tmp_path) -> None:
    runtime, _, claim = _runtime_with_order_claim(tmp_path)
    importer = _importer(runtime)
    batch = _batch(claim)
    importer.import_batch(batch, mappings=_mappings(), claim=claim)
    changed_item = replace(
        batch.items[0],
        order_qty=batch.items[0].order_qty + 1,
    )
    changed = replace(
        batch,
        items=(changed_item, *batch.items[1:]),
    )

    with pytest.raises(OrderObservationError, match="different content"):
        importer.import_batch(
            changed,
            mappings=_mappings(),
            claim=claim,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda batch: replace(batch, platform_name="other-platform"),
        lambda batch: replace(batch, automation_run_id="wrong-run"),
    ],
)
def test_platform_or_run_misbinding_is_rejected(
    tmp_path,
    mutation,
) -> None:
    runtime, _, claim = _runtime_with_order_claim(tmp_path)

    with pytest.raises(OrderObservationError):
        _importer(runtime).import_batch(
            mutation(_batch(claim)),
            mappings=_mappings(),
            claim=claim,
        )

    with closing(runtime.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_batches"
        ).fetchone()[0] == 0


def test_unsupported_and_unavailable_batches_do_not_invent_zero_orders(
    tmp_path,
) -> None:
    runtime, _, claim = _runtime_with_order_claim(tmp_path)
    source = _batch(claim)
    unsupported = replace(
        source,
        observation_batch_id="ORDER-BATCH-UNSUPPORTED",
        capability_result="UNSUPPORTED",
        batch_status="UNAVAILABLE",
        scope_complete=False,
        end_marker_verified=False,
        end_marker_kind="",
        items=(),
        error_code="ORDER_SCAN_UNSUPPORTED",
    )

    result = _importer(runtime).import_batch(
        unsupported,
        mappings=_mappings(),
        claim=claim,
    )

    assert result.capability_result == "UNSUPPORTED"
    assert result.batch_status == "UNAVAILABLE"
    assert result.item_count == 0
    assert result.transaction_amount_total == Decimal("0")


class FailingSecondInsertImporter(OrderObservationImporter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.insert_count = 0

    def _insert_item(self, connection, observation_batch_id, item) -> None:
        self.insert_count += 1
        if self.insert_count == 2:
            raise RuntimeError("injected database failure")
        super()._insert_item(connection, observation_batch_id, item)


def test_database_failure_rolls_back_entire_batch(tmp_path) -> None:
    runtime, _, claim = _runtime_with_order_claim(tmp_path)
    importer = FailingSecondInsertImporter(
        runtime,
        operational_time=OperationalTimeService(),
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="injected"):
        importer.import_batch(
            _batch(claim),
            mappings=_mappings(),
            claim=claim,
        )

    with closing(runtime.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_batches"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_items"
        ).fetchone()[0] == 0


def test_observed_at_outside_batch_is_rejected_before_write(tmp_path) -> None:
    runtime, _, claim = _runtime_with_order_claim(tmp_path)
    batch = _batch(claim)
    bad_item = replace(
        batch.items[0],
        observed_at=batch.scan_completed_at + timedelta(seconds=1),
    )

    with pytest.raises(OrderObservationError, match="scan interval"):
        _importer(runtime).import_batch(
            replace(batch, items=(bad_item,)),
            mappings=_mappings(),
            claim=claim,
        )


def test_order_table_does_not_contain_cancel_or_seller_received_fields(
    tmp_path,
) -> None:
    runtime, _, _ = _runtime_with_order_claim(tmp_path)
    with closing(runtime.connect_read()) as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(order_observation_items)"
            )
        }

    assert "order_qty" in columns
    assert "order_transaction_amount" in columns
    assert not {
        "effective_qty",
        "refund_qty",
        "invalid_qty",
        "cancelled_qty",
        "seller_received_amount",
    }.intersection(columns)
