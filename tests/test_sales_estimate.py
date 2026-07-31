from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from contextlib import closing
import json

import pytest

from app.enums import DataQualityLevel, ProductMappingStatus
from app.sales_settlement_models import (
    InventoryAdjustmentSourceRef,
    InventoryObservationPoint,
)
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.sales_estimate import (
    SalesEstimateError,
    SalesEstimateService,
)


TRADE_DATE = date(2026, 7, 31)
START = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
EVIDENCE = "sha256:" + "a" * 64


def _point(
    name: str,
    *,
    observed_at: datetime,
    inventory: int | None,
    online: bool = True,
    mapping_status: ProductMappingStatus = ProductMappingStatus.VERIFIED,
    mapping_version: str = "mapping-v1",
    batch_status: str = "ACCEPTED",
    trade_date: date = TRADE_DATE,
) -> InventoryObservationPoint:
    return InventoryObservationPoint(
        observation_item_id=name,
        observation_batch_id=f"batch-{name}",
        platform_name="platform",
        internal_sku="SKU-1",
        platform_trade_date=trade_date,
        observed_at=observed_at,
        observed_inventory=inventory,
        observed_online=online,
        mapping_status=mapping_status,
        mapping_version=mapping_version,
        scan_type="ONLINE_PULSE",
        batch_status=batch_status,
        scope_complete=True,
        end_marker_verified=True,
        content_sha256="sha256:" + "b" * 64,
    )


def _ref(
    source_type: str,
    *,
    adjustment_id: str,
    qty: int,
    minute: int = 5,
    suffix: str = "1",
) -> InventoryAdjustmentSourceRef:
    return InventoryAdjustmentSourceRef(
        adjustment_id=adjustment_id,
        source_type=source_type,
        source_ref_id=f"ref-{suffix}",
        adjustment_qty=qty,
        occurred_at=START + timedelta(minutes=minute),
        evidence_sha256=EVIDENCE,
    )


def _attestation(suffix: str = "attestation") -> InventoryAdjustmentSourceRef:
    return _ref(
        "ADJUSTMENT_COVERAGE_ATTESTATION",
        adjustment_id=f"coverage-{suffix}",
        qty=0,
        suffix=suffix,
    )


@pytest.mark.parametrize(
    ("before_qty", "after_qty", "adjustment_qty", "expected"),
    [
        (20, 12, 3, 5),
        (10, 15, -7, 2),
        (10, 10, 0, 0),
    ],
)
def test_estimate_formula_uses_signed_known_adjustment(
    before_qty: int,
    after_qty: int,
    adjustment_qty: int,
    expected: int,
) -> None:
    service = SalesEstimateService(clock=lambda: START + timedelta(hours=1))
    refs = [_attestation()]
    if adjustment_qty:
        refs.append(
            _ref(
                "PRA_INVENTORY_WRITE",
                adjustment_id="write-1",
                qty=adjustment_qty,
                suffix="write",
            )
        )
    segment = service.calculate_segment(
        _point("before", observed_at=START, inventory=before_qty),
        _point(
            "after",
            observed_at=START + timedelta(minutes=10),
            inventory=after_qty,
        ),
        adjustments=refs,
    )

    assert segment.estimation_eligible
    assert segment.estimated_sold_qty == expected
    assert segment.known_inventory_adjustment == adjustment_qty
    assert segment.quality_level is DataQualityLevel.SCAN_ESTIMATED_HIGH
    assert segment.confidence == "HIGH"


def test_no_record_does_not_prove_adjustment_coverage() -> None:
    service = SalesEstimateService()
    segment = service.calculate_segment(
        _point("before", observed_at=START, inventory=20),
        _point(
            "after",
            observed_at=START + timedelta(minutes=10),
            inventory=15,
        ),
    )

    assert not segment.estimation_eligible
    assert segment.estimated_sold_qty is None
    assert segment.estimation_reason == "ADJUSTMENT_COVERAGE_UNPROVEN"
    assert segment.quality_level is DataQualityLevel.SCAN_ESTIMATED_LOW


def test_unreadable_inventory_is_never_fabricated_as_zero() -> None:
    service = SalesEstimateService()
    with pytest.raises(SalesEstimateError, match="inventory_after"):
        service.calculate_segment(
            _point("before", observed_at=START, inventory=20),
            _point(
                "after",
                observed_at=START + timedelta(minutes=10),
                inventory=None,
            ),
            adjustments=(_attestation(),),
        )


def test_medium_gap_is_eligible_but_long_gap_is_not() -> None:
    service = SalesEstimateService()
    before = _point("before", observed_at=START, inventory=20)
    medium = service.calculate_segment(
        before,
        _point(
            "medium",
            observed_at=START + timedelta(minutes=20),
            inventory=18,
        ),
        adjustments=(_attestation(),),
    )
    low = service.calculate_segment(
        before,
        _point(
            "low",
            observed_at=START + timedelta(minutes=30),
            inventory=18,
        ),
        adjustments=(
            _ref(
                "ADJUSTMENT_COVERAGE_ATTESTATION",
                adjustment_id="coverage-low",
                qty=0,
                minute=25,
                suffix="low",
            ),
        ),
    )

    assert medium.quality_level is DataQualityLevel.SCAN_ESTIMATED_MEDIUM
    assert medium.estimated_sold_qty == 2
    assert not low.estimation_eligible
    assert low.estimated_sold_qty is None
    assert low.estimation_reason == "SCAN_GAP_EXCEEDED"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"unresolved_inventory_write": True}, "UNRESOLVED_INVENTORY_WRITE"),
        ({"critical_scan_failure": True}, "OBSERVATION_INCOMPLETE"),
        ({"conflicting_observation": True}, "CONFLICTING_OBSERVATION"),
        ({"overlaps_existing": True}, "OVERLAPPING_INTERVAL"),
    ],
)
def test_safety_conditions_make_interval_ineligible(kwargs, reason) -> None:
    service = SalesEstimateService()
    segment = service.calculate_segment(
        _point("before", observed_at=START, inventory=20),
        _point(
            "after",
            observed_at=START + timedelta(minutes=10),
            inventory=18,
        ),
        adjustments=(_attestation(),),
        **kwargs,
    )
    assert not segment.estimation_eligible
    assert segment.estimated_sold_qty is None
    assert segment.estimation_reason == reason


def test_target_inventory_intent_alone_is_not_an_adjustment() -> None:
    service = SalesEstimateService()
    segment = service.calculate_segment(
        _point("before", observed_at=START, inventory=20),
        _point(
            "after",
            observed_at=START + timedelta(minutes=10),
            inventory=18,
        ),
        adjustments=(
            _attestation(),
            _ref(
                "TARGET_INVENTORY",
                adjustment_id="target-1",
                qty=0,
                suffix="target",
            ),
        ),
    )
    assert not segment.estimation_eligible
    assert segment.estimation_reason == "TARGET_INVENTORY_NOT_VERIFIED"


def test_same_physical_adjustment_is_counted_once() -> None:
    service = SalesEstimateService()
    segment = service.calculate_segment(
        _point("before", observed_at=START, inventory=20),
        _point(
            "after",
            observed_at=START + timedelta(minutes=10),
            inventory=12,
        ),
        adjustments=(
            _attestation(),
            _ref(
                "PRA_INVENTORY_WRITE",
                adjustment_id="same-1",
                qty=3,
                suffix="pra",
            ),
            _ref(
                "RECONCILIATION_CORRECTION",
                adjustment_id="same-1",
                qty=3,
                suffix="reconcile",
            ),
        ),
    )
    assert segment.known_inventory_adjustment == 3
    assert segment.estimated_sold_qty == 5


def test_conflicting_quantity_for_one_adjustment_is_rejected() -> None:
    service = SalesEstimateService()
    with pytest.raises(SalesEstimateError, match="conflicting quantities"):
        service.calculate_segment(
            _point("before", observed_at=START, inventory=20),
            _point(
                "after",
                observed_at=START + timedelta(minutes=10),
                inventory=12,
            ),
            adjustments=(
                _attestation(),
                _ref(
                    "PRA_INVENTORY_WRITE",
                    adjustment_id="same-1",
                    qty=3,
                    suffix="pra",
                ),
                _ref(
                    "RECONCILIATION_CORRECTION",
                    adjustment_id="same-1",
                    qty=4,
                    suffix="reconcile",
                ),
            ),
        )


def test_estimate_repository_exact_replay_and_conflict(tmp_path: Path) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    repository = OperationalSummaryRepository(runtime)
    service = SalesEstimateService(
        clock=lambda: START + timedelta(hours=1)
    )
    segment = service.calculate_segment(
        _point("before", observed_at=START, inventory=20),
        _point(
            "after",
            observed_at=START + timedelta(minutes=10),
            inventory=18,
        ),
        adjustments=(_attestation(),),
    )

    assert repository.append_estimate_segment(segment)
    assert not repository.append_estimate_segment(segment)
    assert repository.list_estimate_segments(
        platform_name="platform",
        platform_trade_date=TRADE_DATE,
    ) == (segment,)
    with pytest.raises(ValueError, match="different content"):
        repository.append_estimate_segment(
            replace(segment, estimated_sold_qty=99)
        )


def test_review_attestation_is_auditable_adjustment_source(
    tmp_path: Path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    payload = {
        "schema_version": "inventory-adjustment-attestation-v1",
        "source_type": "MANUAL_PLATFORM_MODIFICATION",
        "adjustment_id": "manual-1",
        "adjustment_qty": 3,
        "occurred_at": (START + timedelta(minutes=5)).isoformat(),
        "evidence_sha256": EVIDENCE,
    }
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO review_tasks(
                review_task_id, scope_type, scope_key, dedupe_key,
                review_type, review_status, internal_sku, platform_name,
                reason, review_payload_json, resolution_payload_json,
                created_at, updated_at, resolved_by, resolved_at
            ) VALUES (
                'review-1', 'SKU', 'SKU-1', 'review-1',
                'INVENTORY_ADJUSTMENT_ATTESTATION', 'approved',
                'SKU-1', 'platform', 'manual inventory evidence', '{}', ?,
                ?, ?, 'operator', ?
            )
            """,
            (
                json.dumps(payload, ensure_ascii=False),
                START.isoformat(),
                (START + timedelta(minutes=5)).isoformat(),
                (START + timedelta(minutes=5)).isoformat(),
            ),
        )
    refs = OperationalSummaryRepository(
        runtime
    ).list_inventory_adjustment_sources(
        platform_name="platform",
        internal_sku="SKU-1",
        interval_started_at=START,
        interval_ended_at=START + timedelta(minutes=10),
    )
    assert len(refs) == 1
    assert refs[0].source_type == "MANUAL_PLATFORM_MODIFICATION"
    assert refs[0].adjustment_qty == 3
    assert refs[0].source_ref_id == "review-1"
