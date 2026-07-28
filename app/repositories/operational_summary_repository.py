from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from app.enums import (
    DataQualityLevel,
    FactSource,
    SellerPhase,
    SummaryStatus,
)
from app.operational_models import (
    PlatformTradeDaySummary,
    TradeDaySummaryEvent,
    TradeDaySummaryInput,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


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
                SELECT input_type, input_ref_id, input_sha256
                FROM platform_trade_day_summary_inputs
                WHERE summary_id = ?
                ORDER BY input_type ASC, input_ref_id ASC
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

    def insert_initial(
        self,
        summary: PlatformTradeDaySummary,
        event: TradeDaySummaryEvent,
        inputs: Iterable[TradeDaySummaryInput],
    ) -> None:
        input_rows = tuple(inputs)
        with closing(
            self.runtime_repository.connect_write()
        ) as connection, connection:
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
            _insert_summary_inputs(connection, summary.summary_id, input_rows)
            _insert_event(connection, event)

    def transition(
        self,
        *,
        before: PlatformTradeDaySummary,
        after: PlatformTradeDaySummary,
        event: TradeDaySummaryEvent,
        inputs: Iterable[TradeDaySummaryInput],
    ) -> bool:
        input_rows = tuple(inputs)
        with closing(
            self.runtime_repository.connect_write()
        ) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if after.summary_status is SummaryStatus.FINAL:
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
                        seller_received_amount = ?,
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
                        _decimal_to_text(after.seller_received_amount),
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
                if (
                    before.summary_status is after.summary_status
                    and before.input_manifest_sha256
                    != after.input_manifest_sha256
                ):
                    connection.execute(
                        """
                        DELETE FROM platform_trade_day_summary_inputs
                        WHERE summary_id = ?
                        """,
                        (after.summary_id,),
                    )
                _insert_summary_inputs(
                    connection,
                    after.summary_id,
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
            sold_qty, order_count, seller_received_amount,
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
        _decimal_to_text(summary.seller_received_amount),
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
    inputs: Iterable[TradeDaySummaryInput],
) -> None:
    created_at = _datetime_to_text(datetime.now().astimezone())
    connection.executemany(
        """
        INSERT OR IGNORE INTO platform_trade_day_summary_inputs(
            summary_id, input_type, input_ref_id, input_sha256, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                summary_id,
                item.input_type,
                item.input_ref_id,
                item.input_sha256,
                created_at,
            )
            for item in inputs
        ],
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
    amount = row["seller_received_amount"]
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
        seller_received_amount=(
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
