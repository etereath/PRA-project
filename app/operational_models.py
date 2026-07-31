from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.enums import (
    DataQualityLevel,
    FactSource,
    SellerPhase,
    SummaryStatus,
)


@dataclass(frozen=True, slots=True)
class TradeDaySummaryInput:
    input_type: str
    input_ref_id: str
    input_sha256: str


@dataclass(frozen=True, slots=True)
class PlatformTradeDaySummary:
    summary_id: str
    summary_series_id: str
    version_no: int
    supersedes_summary_id: str | None
    is_current: bool
    platform_name: str
    platform_trade_date: date
    seller_operation_date: date
    seller_phase: SellerPhase
    scope_type: str
    scope_key: str
    fact_source: FactSource | None
    quality_level: DataQualityLevel
    summary_status: SummaryStatus
    sold_qty: int | None
    order_count: int | None
    transaction_amount_total: Decimal | None
    quality_reason: str
    source_proportions: dict[str, Any]
    input_manifest_sha256: str
    mapping_version: str
    algorithm_version: str
    time_policy_version: str
    finalized_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TradeDaySummaryEvent:
    event_id: str
    summary_id: str
    from_status: SummaryStatus | None
    to_status: SummaryStatus
    trigger_type: str
    trigger_ref_id: str
    fact_source_before: FactSource | None
    fact_source_after: FactSource | None
    quality_level_before: DataQualityLevel | None
    quality_level_after: DataQualityLevel
    input_manifest_sha256: str
    changed_at: datetime
    changed_by: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SummaryMutationResult:
    summary: PlatformTradeDaySummary
    changed: bool
    event: TradeDaySummaryEvent | None = None
    inputs: tuple[TradeDaySummaryInput, ...] = field(default_factory=tuple)
