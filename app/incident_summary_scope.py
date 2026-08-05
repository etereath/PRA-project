from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.operational_models import PlatformTradeDaySummary, TradeDaySummaryInput


_SKU_SUBJECT_TYPES = frozenset({"SKU", "INTERNAL_SKU", "LISTING"})
_SKU_DEPENDENT_AGGREGATES = frozenset({"VARIETY", "GRADE", "TIME_BUCKET"})


def incident_blocks_summary_scope(
    connection,
    *,
    incident: Any,
    summary: PlatformTradeDaySummary,
    input_rows: Iterable[TradeDaySummaryInput],
) -> bool:
    """Match one blocking Incident to one frozen summary input scope."""

    source_type = str(incident["source_type"] or "")
    source_ref_id = str(incident["source_ref_id"] or "")
    subject_type = normalize_incident_subject_type(incident["subject_type"])
    subject_key = str(incident["subject_key"] or "")
    summary_scope = str(summary.scope_type or "").strip().upper()
    inputs = tuple(input_rows)
    input_ref_ids = {str(item.input_ref_id) for item in inputs if item.input_ref_id}

    if (
        source_type == "TRADE_DAY_SUMMARY"
        and source_ref_id == summary.summary_id
    ):
        return True
    if subject_type == "PLATFORM":
        return True
    if source_ref_id and source_ref_id in input_ref_ids:
        return True
    if subject_type == summary_scope and subject_key == summary.scope_key:
        return True
    if subject_type != "SKU":
        return False
    if summary_scope == "SKU":
        return subject_key == summary.scope_key
    if summary_scope == "PLATFORM":
        # A single listing problem does not invalidate a separately trusted
        # platform total merely because that SKU contributes to the total.
        return False
    if summary_scope not in _SKU_DEPENDENT_AGGREGATES:
        return False
    return _selected_inputs_include_sku(
        connection,
        input_rows=inputs,
        internal_sku=subject_key,
    )


def normalize_incident_subject_type(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in _SKU_SUBJECT_TYPES:
        return "SKU"
    return normalized


def _selected_inputs_include_sku(
    connection,
    *,
    input_rows: tuple[TradeDaySummaryInput, ...],
    internal_sku: str,
) -> bool:
    order_batches = [
        item.input_ref_id
        for item in input_rows
        if item.input_type == "ORDER_OBSERVATION_BATCH"
    ]
    if order_batches:
        placeholders = ",".join("?" for _ in order_batches)
        row = connection.execute(
            f"""
            SELECT 1 FROM order_observation_items
            WHERE observation_batch_id IN ({placeholders})
              AND internal_sku = ?
            LIMIT 1
            """,
            (*order_batches, internal_sku),
        ).fetchone()
        if row is not None:
            return True
    estimate_segments = [
        item.input_ref_id
        for item in input_rows
        if item.input_type == "SALES_ESTIMATE_SEGMENT"
    ]
    if estimate_segments:
        placeholders = ",".join("?" for _ in estimate_segments)
        row = connection.execute(
            f"""
            SELECT 1 FROM sales_estimate_segments
            WHERE estimate_segment_id IN ({placeholders})
              AND internal_sku = ?
            LIMIT 1
            """,
            (*estimate_segments, internal_sku),
        ).fetchone()
        if row is not None:
            return True
    return False
