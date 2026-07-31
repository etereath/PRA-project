from __future__ import annotations

import json
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Iterable

from app.enums import (
    DataQualityLevel,
    FactSource,
    SellerPhase,
    SummaryStatus,
    ProductMappingStatus,
)
from app.operational_models import (
    PlatformTradeDaySummary,
    TradeDaySummaryEvent,
    TradeDaySummaryInput,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.sales_settlement_models import (
    InventoryAdjustmentSourceRef,
    InventoryObservationPoint,
    OrderSnapshot,
    OrderSnapshotItem,
    SalesEstimateSegment,
)


class OperationalSummaryRepository:
    """Transactional persistence for versioned platform trade-day summaries."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository

    def get_summary(
        self,
        summary_id: str,
    ) -> PlatformTradeDaySummary | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM platform_trade_day_summaries
                WHERE summary_id = ?
                """,
                (summary_id,),
            ).fetchone()
        return _row_to_summary(row) if row is not None else None

    def get_current_summary(
        self,
        summary_series_id: str,
    ) -> PlatformTradeDaySummary | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM platform_trade_day_summaries
                WHERE summary_series_id = ? AND is_current = 1
                """,
                (summary_series_id,),
            ).fetchone()
        return _row_to_summary(row) if row is not None else None

    def list_events(self, summary_id: str) -> list[TradeDaySummaryEvent]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM platform_trade_day_summary_events
                WHERE summary_id = ?
                ORDER BY changed_at ASC, event_id ASC
                """,
                (summary_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_inputs(self, summary_id: str) -> list[TradeDaySummaryInput]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT inputs.input_type,
                       inputs.input_ref_id,
                       inputs.input_sha256
                FROM platform_trade_day_summary_inputs AS inputs
                INNER JOIN platform_trade_day_summaries AS summary
                    ON summary.summary_id = inputs.summary_id
                   AND summary.input_manifest_sha256
                       = inputs.input_manifest_sha256
                WHERE inputs.summary_id = ?
                ORDER BY inputs.input_type ASC, inputs.input_ref_id ASC
                """,
                (summary_id,),
            ).fetchall()
        return [
            TradeDaySummaryInput(
                input_type=str(row["input_type"]),
                input_ref_id=str(row["input_ref_id"]),
                input_sha256=str(row["input_sha256"]),
            )
            for row in rows
        ]

    def list_inventory_observations(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
    ) -> tuple[InventoryObservationPoint, ...]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT items.*, batches.platform_name,
                       batches.scan_type, batches.batch_status,
                       batches.scope_complete,
                       batches.end_marker_verified,
                       batches.content_sha256
                FROM product_observation_items AS items
                INNER JOIN product_observation_batches AS batches
                    ON batches.observation_batch_id
                       = items.observation_batch_id
                WHERE batches.platform_name = ?
                  AND items.platform_trade_date = ?
                ORDER BY items.internal_sku ASC,
                         items.observed_at ASC,
                         items.observation_item_id ASC
                """,
                (platform_name, platform_trade_date.isoformat()),
            ).fetchall()
        return tuple(_row_to_inventory_observation(row) for row in rows)

    def list_inventory_adjustment_sources(
        self,
        *,
        platform_name: str,
        internal_sku: str,
        interval_started_at: datetime,
        interval_ended_at: datetime,
    ) -> tuple[InventoryAdjustmentSourceRef, ...]:
        with closing(self.runtime_repository.connect_read()) as connection:
            action_rows = connection.execute(
                """
                SELECT items.*, batches.action_type
                FROM shadowbot_listing_action_batch_items AS items
                INNER JOIN shadowbot_listing_action_batches AS batches
                    ON batches.batch_id = items.batch_id
                WHERE batches.platform_name = ?
                  AND items.internal_sku = ?
                  AND julianday(COALESCE(
                        items.readback_observed_at,
                        items.updated_at
                      )) > julianday(?)
                  AND julianday(COALESCE(
                        items.readback_observed_at,
                        items.updated_at
                      )) <= julianday(?)
                ORDER BY items.item_id
                """,
                (
                    platform_name,
                    internal_sku,
                    _datetime_to_text(interval_started_at),
                    _datetime_to_text(interval_ended_at),
                ),
            ).fetchall()
            review_rows = connection.execute(
                """
                SELECT review_task_id, resolution_payload_json
                FROM review_tasks
                WHERE review_type = 'INVENTORY_ADJUSTMENT_ATTESTATION'
                  AND review_status IN ('approved', 'adjusted')
                  AND platform_name = ?
                  AND internal_sku = ?
                  AND julianday(resolved_at) > julianday(?)
                  AND julianday(resolved_at) <= julianday(?)
                ORDER BY review_task_id
                """,
                (
                    platform_name,
                    internal_sku,
                    _datetime_to_text(interval_started_at),
                    _datetime_to_text(interval_ended_at),
                ),
            ).fetchall()
        refs = list(_listing_adjustment_refs(action_rows))
        refs.extend(
            _review_adjustment_ref(row)
            for row in review_rows
        )
        return tuple(
            sorted(
                refs,
                key=lambda item: (
                    item.source_ref_id,
                    item.source_type,
                    item.adjustment_id,
                ),
            )
        )

    def has_unresolved_inventory_write(
        self,
        *,
        platform_name: str,
        internal_sku: str,
        interval_started_at: datetime,
        interval_ended_at: datetime,
    ) -> bool:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM shadowbot_listing_action_batch_items AS items
                INNER JOIN shadowbot_listing_action_batches AS batches
                    ON batches.batch_id = items.batch_id
                WHERE batches.platform_name = ?
                  AND items.internal_sku = ?
                  AND julianday(items.updated_at) > julianday(?)
                  AND julianday(items.updated_at) <= julianday(?)
                  AND (
                        items.operation_result IN (
                            'PARTIALLY_APPLIED', 'NEEDS_RECONCILIATION'
                        )
                        OR items.detail_effect_state = 'UNKNOWN'
                        OR items.listing_effect_state = 'UNKNOWN'
                      )
                LIMIT 1
                """,
                (
                    platform_name,
                    internal_sku,
                    _datetime_to_text(interval_started_at),
                    _datetime_to_text(interval_ended_at),
                ),
            ).fetchone()
        return row is not None

    def append_estimate_segment(
        self,
        segment: SalesEstimateSegment,
        *,
        transaction_validator: Callable[[object], None] | None = None,
    ) -> bool:
        values = _segment_values(segment)
        with closing(self.runtime_repository.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if transaction_validator is not None:
                    transaction_validator(connection)
                existing = connection.execute(
                    """
                    SELECT *
                    FROM sales_estimate_segments
                    WHERE estimate_segment_id = ?
                    """,
                    (segment.estimate_segment_id,),
                ).fetchone()
                if existing is not None:
                    stored_values = _segment_values(
                        _row_to_estimate_segment(existing)
                    )
                    if stored_values[:-1] != values[:-1]:
                        raise ValueError(
                            "estimate_segment_id was reused with different content"
                        )
                    connection.commit()
                    return False
                connection.execute(
                    """
                    INSERT INTO sales_estimate_segments(
                        estimate_segment_id, platform_name, internal_sku,
                        platform_trade_date, interval_started_at,
                        interval_ended_at, inventory_before, inventory_after,
                        known_inventory_adjustment,
                        known_adjustment_source_refs_json,
                        estimated_sold_qty, estimation_eligible,
                        estimation_reason, quality_level, mapping_version,
                        supporting_observation_ids_json, algorithm_version,
                        created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    values,
                )
                connection.commit()
                return True
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def list_estimate_segments(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        internal_sku: str | None = None,
    ) -> tuple[SalesEstimateSegment, ...]:
        query = """
            SELECT *
            FROM sales_estimate_segments
            WHERE platform_name = ? AND platform_trade_date = ?
        """
        values: list[object] = [
            platform_name,
            platform_trade_date.isoformat(),
        ]
        if internal_sku is not None:
            query += " AND internal_sku = ?"
            values.append(internal_sku)
        query += " ORDER BY interval_started_at, internal_sku, estimate_segment_id"
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return tuple(_row_to_estimate_segment(row) for row in rows)

    def list_order_snapshots(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
    ) -> tuple[OrderSnapshot, ...]:
        with closing(self.runtime_repository.connect_read()) as connection:
            return _list_order_snapshots_in_connection(
                connection,
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
            )

    def get_order_snapshot(
        self,
        observation_batch_id: str,
        *,
        connection=None,
    ) -> OrderSnapshot | None:
        if connection is not None:
            return _get_order_snapshot_in_connection(
                connection,
                observation_batch_id,
            )
        with closing(self.runtime_repository.connect_read()) as read_connection:
            return _get_order_snapshot_in_connection(
                read_connection,
                observation_batch_id,
            )

    def list_current_summaries(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
    ) -> tuple[PlatformTradeDaySummary, ...]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM platform_trade_day_summaries
                WHERE platform_name = ?
                  AND platform_trade_date = ?
                  AND is_current = 1
                ORDER BY scope_type, scope_key
                """,
                (platform_name, platform_trade_date.isoformat()),
            ).fetchall()
        return tuple(_row_to_summary(row) for row in rows)

    def list_sku_dimensions(
        self,
        *,
        platform_name: str,
    ) -> dict[str, dict[str, str]]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT internal_sku, variety, grade
                FROM listing_status
                WHERE platform_name = ?
                  AND internal_sku IS NOT NULL
                  AND internal_sku <> ''
                ORDER BY internal_sku, variety, grade
                """,
                (platform_name,),
            ).fetchall()
        dimensions: dict[str, dict[str, str]] = {}
        for row in rows:
            sku = str(row["internal_sku"])
            variety = str(row["variety"])
            grade = str(row["grade"])
            existing = dimensions.get(sku)
            if existing is not None and existing["variety"] != variety:
                raise ValueError(
                    "One internal SKU maps to multiple varieties"
                )
            dimensions[sku] = {"variety": variety, "grade": grade}
        return dimensions

    def insert_initial(
        self,
        summary: PlatformTradeDaySummary,
        event: TradeDaySummaryEvent,
        inputs: Iterable[TradeDaySummaryInput],
        transaction_validator: Callable[[object], None] | None = None,
    ) -> None:
        input_rows = tuple(inputs)
        with closing(self.runtime_repository.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if transaction_validator is not None:
                    transaction_validator(connection)
                existing = connection.execute(
                    """
                    SELECT summary_id
                    FROM platform_trade_day_summaries
                    WHERE summary_series_id = ? AND is_current = 1
                    """,
                    (summary.summary_series_id,),
                ).fetchone()
                if existing is not None:
                    raise ValueError(
                        "A current summary already exists for the series"
                    )
                _insert_summary(connection, summary)
                _insert_summary_inputs(
                    connection,
                    summary.summary_id,
                    summary.input_manifest_sha256,
                    input_rows,
                )
                _insert_event(connection, event)
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def transition(
        self,
        *,
        before: PlatformTradeDaySummary,
        after: PlatformTradeDaySummary,
        event: TradeDaySummaryEvent,
        inputs: Iterable[TradeDaySummaryInput],
        finalization_validator: Callable[[object], None] | None = None,
        transaction_validator: Callable[[object], None] | None = None,
    ) -> bool:
        input_rows = tuple(inputs)
        with closing(
            self.runtime_repository.connect_write()
        ) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if transaction_validator is not None:
                    transaction_validator(connection)
                if after.summary_status is SummaryStatus.FINAL:
                    if finalization_validator is None:
                        raise ValueError(
                            "FINAL requires an atomic evidence validator"
                        )
                    finalization_validator(connection)
                    blocking_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*)
                            FROM operational_incidents
                            WHERE source_type = 'TRADE_DAY_SUMMARY'
                              AND source_ref_id = ?
                              AND blocks_finalization = 1
                              AND resolved_at IS NULL
                            """,
                            (before.summary_id,),
                        ).fetchone()[0]
                    )
                    if blocking_count:
                        raise ValueError(
                            "Cannot finalize while blocking S3/S4 "
                            "incidents remain open"
                        )
                cursor = connection.execute(
                    """
                    UPDATE platform_trade_day_summaries
                    SET fact_source = ?,
                        quality_level = ?,
                        summary_status = ?,
                        sold_qty = ?,
                        order_count = ?,
                        transaction_amount_total = ?,
                        quality_reason = ?,
                        source_proportions_json = ?,
                        input_manifest_sha256 = ?,
                        mapping_version = ?,
                        algorithm_version = ?,
                        finalized_at = ?,
                        updated_at = ?
                    WHERE summary_id = ?
                      AND summary_status = ?
                      AND input_manifest_sha256 = ?
                      AND is_current = 1
                    """,
                    (
                        (
                            after.fact_source.value
                            if after.fact_source
                            else None
                        ),
                        after.quality_level.value,
                        after.summary_status.value,
                        after.sold_qty,
                        after.order_count,
                        _decimal_to_text(after.transaction_amount_total),
                        after.quality_reason,
                        _json_dump(after.source_proportions),
                        after.input_manifest_sha256,
                        after.mapping_version,
                        after.algorithm_version,
                        _datetime_to_text(after.finalized_at),
                        _datetime_to_text(after.updated_at),
                        before.summary_id,
                        before.summary_status.value,
                        before.input_manifest_sha256,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                _insert_summary_inputs(
                    connection,
                    after.summary_id,
                    after.input_manifest_sha256,
                    input_rows,
                )
                _insert_event(connection, event)
                connection.commit()
                return True
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def insert_revision(
        self,
        *,
        previous: PlatformTradeDaySummary,
        revision: PlatformTradeDaySummary,
        event: TradeDaySummaryEvent,
        inputs: Iterable[TradeDaySummaryInput],
    ) -> bool:
        input_rows = tuple(inputs)
        with closing(
            self.runtime_repository.connect_write()
        ) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE platform_trade_day_summaries
                    SET is_current = 0
                    WHERE summary_id = ?
                      AND summary_status = ?
                      AND is_current = 1
                      AND input_manifest_sha256 = ?
                    """,
                    (
                        previous.summary_id,
                        previous.summary_status.value,
                        previous.input_manifest_sha256,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                _insert_summary(connection, revision)
                _insert_summary_inputs(
                    connection,
                    revision.summary_id,
                    revision.input_manifest_sha256,
                    input_rows,
                )
                _insert_event(connection, event)
                connection.commit()
                return True
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def count_blocking_incidents(self, summary_id: str) -> int:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM operational_incidents
                WHERE source_type = 'TRADE_DAY_SUMMARY'
                  AND source_ref_id = ?
                  AND blocks_finalization = 1
                  AND resolved_at IS NULL
                """,
                (summary_id,),
            ).fetchone()
        return int(row[0]) if row is not None else 0


def _row_to_inventory_observation(row) -> InventoryObservationPoint:
    inventory = row["observed_inventory"]
    internal_sku = row["internal_sku"]
    return InventoryObservationPoint(
        observation_item_id=str(row["observation_item_id"]),
        observation_batch_id=str(row["observation_batch_id"]),
        platform_name=str(row["platform_name"]),
        internal_sku=(str(internal_sku) if internal_sku is not None else None),
        platform_trade_date=date.fromisoformat(
            str(row["platform_trade_date"])
        ),
        observed_at=_required_datetime(row["observed_at"]),
        observed_inventory=(int(inventory) if inventory is not None else None),
        observed_online=bool(row["observed_online"]),
        mapping_status=ProductMappingStatus(str(row["mapping_status"])),
        mapping_version=str(row["mapping_version"] or ""),
        scan_type=str(row["scan_type"]),
        batch_status=str(row["batch_status"]),
        scope_complete=bool(row["scope_complete"]),
        end_marker_verified=bool(row["end_marker_verified"]),
        content_sha256=str(row["content_sha256"]),
    )


def _listing_adjustment_refs(rows) -> tuple[InventoryAdjustmentSourceRef, ...]:
    refs: list[InventoryAdjustmentSourceRef] = []
    for row in rows:
        if (
            str(row["action_type"]) != "set_online"
            or str(row["operation_result"]) != "VERIFIED"
            or str(row["detail_effect_state"]) != "VERIFIED"
            or row["observed_inventory_before_action"] is None
            or row["observed_inventory_after_detail_save"] is None
            or row["readback_observed_at"] in (None, "")
        ):
            continue
        item_id = str(row["item_id"])
        adjustment_id = f"listing-action:{item_id}"
        evidence_sha256 = _prefixed_sha256(row["item_payload_sha256"])
        occurred_at = _required_datetime(row["readback_observed_at"])
        adjustment_qty = int(row["observed_inventory_before_action"]) - int(
            row["observed_inventory_after_detail_save"]
        )
        refs.append(
            InventoryAdjustmentSourceRef(
                adjustment_id=adjustment_id,
                source_type="SET_ONLINE_INVENTORY_RESET",
                source_ref_id=item_id,
                adjustment_qty=adjustment_qty,
                occurred_at=occurred_at,
                evidence_sha256=evidence_sha256,
            )
        )
        if row["target_inventory"] is not None:
            refs.append(
                InventoryAdjustmentSourceRef(
                    adjustment_id=adjustment_id,
                    source_type="TARGET_INVENTORY",
                    source_ref_id=f"{item_id}:target_inventory",
                    adjustment_qty=0,
                    occurred_at=occurred_at,
                    evidence_sha256=evidence_sha256,
                )
            )
    return tuple(refs)


def _review_adjustment_ref(row) -> InventoryAdjustmentSourceRef:
    payload = json.loads(str(row["resolution_payload_json"] or "{}"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version")
        != "inventory-adjustment-attestation-v1"
    ):
        raise ValueError(
            "Inventory adjustment review payload has an invalid schema"
        )
    source_type = str(payload.get("source_type") or "")
    if source_type not in {
        "MANUAL_PLATFORM_MODIFICATION",
        "RECONCILIATION_CORRECTION",
        "ADJUSTMENT_COVERAGE_ATTESTATION",
    }:
        raise ValueError(
            "Inventory adjustment review source_type is not permitted"
        )
    adjustment_qty = payload.get("adjustment_qty")
    if isinstance(adjustment_qty, bool) or not isinstance(adjustment_qty, int):
        raise ValueError(
            "Inventory adjustment review quantity must be an integer"
        )
    if (
        source_type == "ADJUSTMENT_COVERAGE_ATTESTATION"
        and adjustment_qty != 0
    ):
        raise ValueError("Coverage attestation quantity must be zero")
    adjustment_id = str(payload.get("adjustment_id") or "").strip()
    if not adjustment_id:
        raise ValueError("Inventory adjustment review adjustment_id is blank")
    return InventoryAdjustmentSourceRef(
        adjustment_id=adjustment_id,
        source_type=source_type,
        source_ref_id=str(row["review_task_id"]),
        adjustment_qty=adjustment_qty,
        occurred_at=_required_datetime(payload.get("occurred_at")),
        evidence_sha256=_prefixed_sha256(payload.get("evidence_sha256")),
    )


def _prefixed_sha256(value: object) -> str:
    text = str(value or "").strip().lower()
    if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
        return f"sha256:{text}"
    if (
        len(text) == 71
        and text.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in text[7:])
    ):
        return text
    raise ValueError("Inventory adjustment evidence SHA-256 is invalid")


def _segment_values(segment: SalesEstimateSegment) -> tuple[object, ...]:
    return (
        segment.estimate_segment_id,
        segment.platform_name,
        segment.internal_sku,
        segment.platform_trade_date.isoformat(),
        _datetime_to_text(segment.interval_started_at),
        _datetime_to_text(segment.interval_ended_at),
        segment.inventory_before,
        segment.inventory_after,
        segment.known_inventory_adjustment,
        _json_dump(
            [
                {
                    "adjustment_id": ref.adjustment_id,
                    "source_type": ref.source_type,
                    "source_ref_id": ref.source_ref_id,
                    "adjustment_qty": ref.adjustment_qty,
                    "occurred_at": _datetime_to_text(ref.occurred_at),
                    "evidence_sha256": ref.evidence_sha256,
                }
                for ref in segment.known_adjustment_source_refs
            ]
        ),
        segment.estimated_sold_qty,
        int(segment.estimation_eligible),
        segment.estimation_reason,
        segment.quality_level.value,
        segment.mapping_version,
        _json_dump(list(segment.supporting_observation_ids)),
        segment.algorithm_version,
        _datetime_to_text(segment.created_at),
    )


def _row_to_estimate_segment(row) -> SalesEstimateSegment:
    raw_refs = json.loads(str(row["known_adjustment_source_refs_json"] or "[]"))
    if not isinstance(raw_refs, list):
        raise ValueError("Stored adjustment source refs must be a list")
    raw_observation_ids = json.loads(
        str(row["supporting_observation_ids_json"] or "[]")
    )
    if not isinstance(raw_observation_ids, list):
        raise ValueError("Stored supporting observation ids must be a list")
    return SalesEstimateSegment(
        estimate_segment_id=str(row["estimate_segment_id"]),
        platform_name=str(row["platform_name"]),
        internal_sku=str(row["internal_sku"]),
        platform_trade_date=date.fromisoformat(
            str(row["platform_trade_date"])
        ),
        interval_started_at=_required_datetime(row["interval_started_at"]),
        interval_ended_at=_required_datetime(row["interval_ended_at"]),
        inventory_before=int(row["inventory_before"]),
        inventory_after=int(row["inventory_after"]),
        known_inventory_adjustment=int(row["known_inventory_adjustment"]),
        known_adjustment_source_refs=tuple(
            InventoryAdjustmentSourceRef(
                adjustment_id=str(item["adjustment_id"]),
                source_type=str(item["source_type"]),
                source_ref_id=str(item["source_ref_id"]),
                adjustment_qty=int(item["adjustment_qty"]),
                occurred_at=_required_datetime(item["occurred_at"]),
                evidence_sha256=str(item["evidence_sha256"]),
            )
            for item in raw_refs
        ),
        estimated_sold_qty=(
            int(row["estimated_sold_qty"])
            if row["estimated_sold_qty"] is not None
            else None
        ),
        estimation_eligible=bool(row["estimation_eligible"]),
        estimation_reason=str(row["estimation_reason"]),
        quality_level=DataQualityLevel(str(row["quality_level"])),
        mapping_version=str(row["mapping_version"]),
        supporting_observation_ids=tuple(
            str(item) for item in raw_observation_ids
        ),
        algorithm_version=str(row["algorithm_version"]),
        created_at=_required_datetime(row["created_at"]),
    )


def _list_order_snapshots_in_connection(
    connection,
    *,
    platform_name: str,
    platform_trade_date: date,
) -> tuple[OrderSnapshot, ...]:
    rows = connection.execute(
        """
        SELECT observation_batch_id
        FROM order_observation_batches
        WHERE platform_name = ?
          AND requested_platform_trade_date = ?
        ORDER BY scan_completed_at ASC, observation_batch_id ASC
        """,
        (platform_name, platform_trade_date.isoformat()),
    ).fetchall()
    return tuple(
        snapshot
        for row in rows
        if (
            snapshot := _get_order_snapshot_in_connection(
                connection,
                str(row["observation_batch_id"]),
            )
        )
        is not None
    )


def _get_order_snapshot_in_connection(
    connection,
    observation_batch_id: str,
) -> OrderSnapshot | None:
    batch = connection.execute(
        """
        SELECT *
        FROM order_observation_batches
        WHERE observation_batch_id = ?
        """,
        (observation_batch_id,),
    ).fetchone()
    if batch is None:
        return None
    item_rows = connection.execute(
        """
        SELECT *
        FROM order_observation_items
        WHERE observation_batch_id = ?
        ORDER BY order_identity_fingerprint, occurrence_no,
                 observation_item_id
        """,
        (observation_batch_id,),
    ).fetchall()
    requested_range = json.loads(str(batch["requested_range_json"] or "{}"))
    if not isinstance(requested_range, dict):
        raise ValueError("Stored requested order range must be an object")
    items = tuple(_row_to_order_snapshot_item(row) for row in item_rows)
    mapping_versions = {item.mapping_version for item in items}
    mapping_version = str(
        requested_range.get("accepted_mapping_version") or ""
    )
    if not mapping_version and len(mapping_versions) == 1:
        mapping_version = next(iter(mapping_versions))
    return OrderSnapshot(
        observation_batch_id=str(batch["observation_batch_id"]),
        platform_name=str(batch["platform_name"]),
        platform_trade_date=date.fromisoformat(
            str(batch["requested_platform_trade_date"])
        ),
        trade_day_status=str(batch["trade_day_status"]),
        capability_result=str(batch["capability_result"]),
        batch_status=str(batch["batch_status"]),
        source_batch_status=str(
            requested_range.get("source_batch_status")
            or batch["batch_status"]
        ),
        scope_complete=bool(batch["scope_complete"]),
        end_marker_verified=bool(batch["end_marker_verified"]),
        scan_started_at=_required_datetime(batch["scan_started_at"]),
        scan_completed_at=_required_datetime(batch["scan_completed_at"]),
        content_sha256=str(batch["content_sha256"]),
        time_policy_version=str(batch["time_policy_version"]),
        mapping_version=mapping_version,
        items=items,
    )


def _row_to_order_snapshot_item(row) -> OrderSnapshotItem:
    internal_sku = row["internal_sku"]
    return OrderSnapshotItem(
        observation_item_id=str(row["observation_item_id"]),
        order_identity_fingerprint=str(
            row["order_identity_fingerprint"]
        ),
        occurrence_no=int(row["occurrence_no"]),
        order_created_at=_required_datetime(row["order_created_at"]),
        platform_product_name=str(row["platform_product_name"]),
        grade=str(row["grade"]),
        internal_sku=(str(internal_sku) if internal_sku is not None else None),
        mapping_status=ProductMappingStatus(str(row["mapping_status"])),
        mapping_version=str(row["mapping_version"] or ""),
        order_qty=int(row["order_qty"]),
        order_transaction_amount=Decimal(
            str(row["order_transaction_amount"])
        ),
        raw_observation_sha256=str(row["raw_observation_sha256"]),
    )


def _insert_summary(connection, summary: PlatformTradeDaySummary) -> None:
    connection.execute(
        """
        INSERT INTO platform_trade_day_summaries(
            summary_id, summary_series_id, version_no,
            supersedes_summary_id, is_current,
            platform_name, platform_trade_date,
            seller_operation_date, seller_phase,
            scope_type, scope_key,
            fact_source, quality_level, summary_status,
            sold_qty, order_count, transaction_amount_total,
            quality_reason, source_proportions_json,
            input_manifest_sha256, mapping_version,
            algorithm_version, time_policy_version,
            finalized_at, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """,
        _summary_values(summary),
    )


def _summary_values(summary: PlatformTradeDaySummary) -> tuple[object, ...]:
    return (
        summary.summary_id,
        summary.summary_series_id,
        summary.version_no,
        summary.supersedes_summary_id,
        int(summary.is_current),
        summary.platform_name,
        summary.platform_trade_date.isoformat(),
        summary.seller_operation_date.isoformat(),
        summary.seller_phase.value,
        summary.scope_type,
        summary.scope_key,
        summary.fact_source.value if summary.fact_source else None,
        summary.quality_level.value,
        summary.summary_status.value,
        summary.sold_qty,
        summary.order_count,
        _decimal_to_text(summary.transaction_amount_total),
        summary.quality_reason,
        _json_dump(summary.source_proportions),
        summary.input_manifest_sha256,
        summary.mapping_version,
        summary.algorithm_version,
        summary.time_policy_version,
        _datetime_to_text(summary.finalized_at),
        _datetime_to_text(summary.created_at),
        _datetime_to_text(summary.updated_at),
    )


def _insert_summary_inputs(
    connection,
    summary_id: str,
    input_manifest_sha256: str,
    inputs: Iterable[TradeDaySummaryInput],
) -> None:
    created_at = _datetime_to_text(datetime.now().astimezone())
    for item in inputs:
        existing = connection.execute(
            """
            SELECT input_sha256
            FROM platform_trade_day_summary_inputs
            WHERE summary_id = ?
              AND input_manifest_sha256 = ?
              AND input_type = ?
              AND input_ref_id = ?
            """,
            (
                summary_id,
                input_manifest_sha256,
                item.input_type,
                item.input_ref_id,
            ),
        ).fetchone()
        if existing is not None:
            if str(existing["input_sha256"]) != item.input_sha256:
                raise ValueError(
                    "A summary input identity was reused with different "
                    "content"
                )
            continue
        connection.execute(
            """
            INSERT INTO platform_trade_day_summary_inputs(
                summary_id, input_manifest_sha256,
                input_type, input_ref_id, input_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                summary_id,
                input_manifest_sha256,
                item.input_type,
                item.input_ref_id,
                item.input_sha256,
                created_at,
            ),
        )


def _insert_event(connection, event: TradeDaySummaryEvent) -> None:
    connection.execute(
        """
        INSERT INTO platform_trade_day_summary_events(
            event_id, summary_id, from_status, to_status,
            trigger_type, trigger_ref_id,
            fact_source_before, fact_source_after,
            quality_level_before, quality_level_after,
            input_manifest_sha256, changed_at, changed_by, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.summary_id,
            event.from_status.value if event.from_status else None,
            event.to_status.value,
            event.trigger_type,
            event.trigger_ref_id,
            (
                event.fact_source_before.value
                if event.fact_source_before
                else None
            ),
            (
                event.fact_source_after.value
                if event.fact_source_after
                else None
            ),
            (
                event.quality_level_before.value
                if event.quality_level_before
                else None
            ),
            event.quality_level_after.value,
            event.input_manifest_sha256,
            _datetime_to_text(event.changed_at),
            event.changed_by,
            event.reason,
        ),
    )


def _row_to_summary(row) -> PlatformTradeDaySummary:
    fact_source = row["fact_source"]
    amount = row["transaction_amount_total"]
    return PlatformTradeDaySummary(
        summary_id=str(row["summary_id"]),
        summary_series_id=str(row["summary_series_id"]),
        version_no=int(row["version_no"]),
        supersedes_summary_id=row["supersedes_summary_id"],
        is_current=bool(row["is_current"]),
        platform_name=str(row["platform_name"]),
        platform_trade_date=_text_to_date(row["platform_trade_date"]),
        seller_operation_date=_text_to_date(
            row["seller_operation_date"]
        ),
        seller_phase=SellerPhase(str(row["seller_phase"])),
        scope_type=str(row["scope_type"]),
        scope_key=str(row["scope_key"]),
        fact_source=(
            FactSource(str(fact_source)) if fact_source is not None else None
        ),
        quality_level=DataQualityLevel(str(row["quality_level"])),
        summary_status=SummaryStatus(str(row["summary_status"])),
        sold_qty=(
            int(row["sold_qty"]) if row["sold_qty"] is not None else None
        ),
        order_count=(
            int(row["order_count"])
            if row["order_count"] is not None
            else None
        ),
        transaction_amount_total=(
            Decimal(str(amount)) if amount is not None else None
        ),
        quality_reason=str(row["quality_reason"] or ""),
        source_proportions=_json_load(row["source_proportions_json"]),
        input_manifest_sha256=str(row["input_manifest_sha256"]),
        mapping_version=str(row["mapping_version"] or ""),
        algorithm_version=str(row["algorithm_version"]),
        time_policy_version=str(row["time_policy_version"]),
        finalized_at=_text_to_datetime(row["finalized_at"]),
        created_at=_required_datetime(row["created_at"]),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _row_to_event(row) -> TradeDaySummaryEvent:
    from_status = row["from_status"]
    fact_before = row["fact_source_before"]
    fact_after = row["fact_source_after"]
    quality_before = row["quality_level_before"]
    return TradeDaySummaryEvent(
        event_id=str(row["event_id"]),
        summary_id=str(row["summary_id"]),
        from_status=(
            SummaryStatus(str(from_status))
            if from_status is not None
            else None
        ),
        to_status=SummaryStatus(str(row["to_status"])),
        trigger_type=str(row["trigger_type"]),
        trigger_ref_id=str(row["trigger_ref_id"] or ""),
        fact_source_before=(
            FactSource(str(fact_before))
            if fact_before is not None
            else None
        ),
        fact_source_after=(
            FactSource(str(fact_after)) if fact_after is not None else None
        ),
        quality_level_before=(
            DataQualityLevel(str(quality_before))
            if quality_before is not None
            else None
        ),
        quality_level_after=DataQualityLevel(
            str(row["quality_level_after"])
        ),
        input_manifest_sha256=str(row["input_manifest_sha256"]),
        changed_at=_required_datetime(row["changed_at"]),
        changed_by=str(row["changed_by"]),
        reason=str(row["reason"] or ""),
    )


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _text_to_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value not in (None, "") else None


def _required_datetime(value: object) -> datetime:
    parsed = _text_to_datetime(value)
    if parsed is None:
        raise ValueError("Required datetime is missing")
    return parsed


def _text_to_date(value: object):
    from datetime import date

    return date.fromisoformat(str(value))


def _decimal_to_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _json_dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_load(value: object) -> dict:
    parsed = json.loads(str(value or "{}"))
    return parsed if isinstance(parsed, dict) else {}
