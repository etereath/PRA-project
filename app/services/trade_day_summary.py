from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Iterable
from uuid import uuid4

from app.enums import (
    DataQualityLevel,
    FactSource,
    SellerPhase,
    SummaryStatus,
)
from app.operational_models import (
    PlatformTradeDaySummary,
    SummaryMutationResult,
    TradeDaySummaryEvent,
    TradeDaySummaryInput,
)
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
)
from app.utils import utc_now


ALLOWED_SUMMARY_TRANSITIONS = {
    SummaryStatus.PROVISIONAL: SummaryStatus.OBSERVED,
    SummaryStatus.OBSERVED: SummaryStatus.RECONCILED,
    SummaryStatus.RECONCILED: SummaryStatus.FINAL,
}

ORDER_QUALITY_LEVELS = {
    DataQualityLevel.ORDER_COMPLETE,
    DataQualityLevel.ORDER_PARTIAL,
}
SCAN_QUALITY_LEVELS = {
    DataQualityLevel.SCAN_ESTIMATED_HIGH,
    DataQualityLevel.SCAN_ESTIMATED_MEDIUM,
    DataQualityLevel.SCAN_ESTIMATED_LOW,
}


class TradeDaySummaryService:
    """Enforce the frozen one-way lifecycle and versioned late-data rules."""

    def __init__(
        self,
        repository: OperationalSummaryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self._clock = clock or utc_now

    def create_provisional(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        seller_operation_date: date,
        seller_phase: SellerPhase,
        scope_type: str,
        scope_key: str,
        fact_source: FactSource | None,
        quality_level: DataQualityLevel,
        sold_qty: int | None,
        order_count: int | None,
        transaction_amount_total: Decimal | None,
        quality_reason: str,
        source_proportions: dict,
        input_manifest_sha256: str,
        mapping_version: str,
        algorithm_version: str,
        inputs: Iterable[TradeDaySummaryInput],
        changed_by: str,
        trigger_ref_id: str = "",
        time_policy_version: str = (
            DEFAULT_OPERATIONAL_TIME_POLICY_VERSION
        ),
        transaction_validator: Callable[[object], None] | None = None,
    ) -> SummaryMutationResult:
        now = _require_aware(self._clock())
        series_id = build_summary_series_id(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
        )
        existing = self.repository.get_current_summary(series_id)
        if existing is not None:
            if (
                existing.summary_status is SummaryStatus.PROVISIONAL
                and existing.input_manifest_sha256
                == input_manifest_sha256
            ):
                return SummaryMutationResult(summary=existing, changed=False)
            raise ValueError(
                "A current summary already exists for this business scope"
            )

        summary = PlatformTradeDaySummary(
            summary_id=f"summary-{uuid4()}",
            summary_series_id=series_id,
            version_no=1,
            supersedes_summary_id=None,
            is_current=True,
            platform_name=_require_text(platform_name, "platform_name"),
            platform_trade_date=platform_trade_date,
            seller_operation_date=seller_operation_date,
            seller_phase=seller_phase,
            scope_type=_validate_scope_type(scope_type),
            scope_key=_require_text(scope_key, "scope_key"),
            fact_source=fact_source,
            quality_level=quality_level,
            summary_status=SummaryStatus.PROVISIONAL,
            sold_qty=sold_qty,
            order_count=order_count,
            transaction_amount_total=transaction_amount_total,
            quality_reason=quality_reason,
            source_proportions=dict(source_proportions),
            input_manifest_sha256=_require_text(
                input_manifest_sha256,
                "input_manifest_sha256",
            ),
            mapping_version=mapping_version,
            algorithm_version=_require_text(
                algorithm_version,
                "algorithm_version",
            ),
            time_policy_version=_require_text(
                time_policy_version,
                "time_policy_version",
            ),
            finalized_at=None,
            created_at=now,
            updated_at=now,
        )
        _validate_summary(summary)
        event = _build_event(
            summary=summary,
            from_summary=None,
            changed_at=now,
            changed_by=changed_by,
            trigger_type="SETTLEMENT",
            trigger_ref_id=trigger_ref_id,
            reason=quality_reason,
        )
        input_rows = tuple(inputs)
        self.repository.insert_initial(
            summary,
            event,
            input_rows,
            transaction_validator=transaction_validator,
        )
        return SummaryMutationResult(
            summary=summary,
            changed=True,
            event=event,
            inputs=input_rows,
        )

    def transition(
        self,
        summary_id: str,
        *,
        to_status: SummaryStatus,
        fact_source: FactSource | None,
        quality_level: DataQualityLevel,
        sold_qty: int | None,
        order_count: int | None,
        transaction_amount_total: Decimal | None,
        quality_reason: str,
        source_proportions: dict,
        input_manifest_sha256: str,
        mapping_version: str,
        algorithm_version: str,
        inputs: Iterable[TradeDaySummaryInput],
        changed_by: str,
        trigger_type: str,
        trigger_ref_id: str = "",
        finalization_validator: Callable[[object], None] | None = None,
        transaction_validator: Callable[[object], None] | None = None,
    ) -> SummaryMutationResult:
        before = self._require_current(summary_id)
        if to_status is SummaryStatus.FINAL:
            if finalization_validator is None:
                raise ValueError(
                    "FINAL requires an atomic evidence validator"
                )
            if trigger_type != "FINALIZATION_POLICY":
                raise ValueError(
                    "FINAL requires trigger_type FINALIZATION_POLICY"
                )
            _require_text(trigger_ref_id, "trigger_ref_id")
        elif finalization_validator is not None:
            raise ValueError(
                "Atomic finalization validator is only valid for FINAL"
            )
        if (
            before.summary_status is to_status
            and before.input_manifest_sha256 == input_manifest_sha256
        ):
            return SummaryMutationResult(summary=before, changed=False)
        same_status_material_revision = before.summary_status is to_status
        if (
            same_status_material_revision
            and before.summary_status is not SummaryStatus.PROVISIONAL
        ):
            return self._create_revision(
                previous=before,
                fact_source=fact_source,
                quality_level=quality_level,
                sold_qty=sold_qty,
                order_count=order_count,
                transaction_amount_total=transaction_amount_total,
                quality_reason=quality_reason,
                source_proportions=source_proportions,
                input_manifest_sha256=input_manifest_sha256,
                mapping_version=mapping_version,
                algorithm_version=algorithm_version,
                inputs=inputs,
                changed_by=changed_by,
                trigger_type="MATERIAL_INPUT_REVISION",
                trigger_ref_id=trigger_ref_id,
            )
        if not same_status_material_revision:
            expected = ALLOWED_SUMMARY_TRANSITIONS.get(
                before.summary_status
            )
            if expected is not to_status:
                raise ValueError(
                    f"Illegal summary transition "
                    f"{before.summary_status.value} -> {to_status.value}"
                )

        now = _require_aware(self._clock())
        after = replace(
            before,
            fact_source=fact_source,
            quality_level=quality_level,
            summary_status=to_status,
            sold_qty=sold_qty,
            order_count=order_count,
            transaction_amount_total=transaction_amount_total,
            quality_reason=quality_reason,
            source_proportions=dict(source_proportions),
            input_manifest_sha256=_require_text(
                input_manifest_sha256,
                "input_manifest_sha256",
            ),
            mapping_version=mapping_version,
            algorithm_version=_require_text(
                algorithm_version,
                "algorithm_version",
            ),
            finalized_at=(
                now if to_status is SummaryStatus.FINAL else None
            ),
            updated_at=now,
        )
        _validate_summary(after)
        event = _build_event(
            summary=after,
            from_summary=before,
            changed_at=now,
            changed_by=changed_by,
            trigger_type=trigger_type,
            trigger_ref_id=trigger_ref_id,
            reason=quality_reason,
        )
        input_rows = tuple(inputs)
        if not self.repository.transition(
            before=before,
            after=after,
            event=event,
            inputs=input_rows,
            finalization_validator=finalization_validator,
            transaction_validator=transaction_validator,
        ):
            raise RuntimeError(
                "Summary changed concurrently; reload before retrying"
            )
        return SummaryMutationResult(
            summary=after,
            changed=True,
            event=event,
            inputs=input_rows,
        )

    def revise_current(
        self,
        summary_id: str,
        *,
        fact_source: FactSource | None,
        quality_level: DataQualityLevel,
        sold_qty: int | None,
        order_count: int | None,
        transaction_amount_total: Decimal | None,
        quality_reason: str,
        source_proportions: dict,
        input_manifest_sha256: str,
        mapping_version: str,
        algorithm_version: str,
        inputs: Iterable[TradeDaySummaryInput],
        changed_by: str,
        trigger_type: str = "MATERIAL_INPUT_REVISION",
        trigger_ref_id: str = "",
        transaction_validator: Callable[[object], None] | None = None,
    ) -> SummaryMutationResult:
        """Audit a PROVISIONAL update or restart later states as OBSERVED."""

        previous = self._require_current(summary_id)
        if previous.summary_status is SummaryStatus.PROVISIONAL:
            return self.transition(
                summary_id,
                to_status=SummaryStatus.PROVISIONAL,
                fact_source=fact_source,
                quality_level=quality_level,
                sold_qty=sold_qty,
                order_count=order_count,
                transaction_amount_total=transaction_amount_total,
                quality_reason=quality_reason,
                source_proportions=source_proportions,
                input_manifest_sha256=input_manifest_sha256,
                mapping_version=mapping_version,
                algorithm_version=algorithm_version,
                inputs=inputs,
                changed_by=changed_by,
                trigger_type=trigger_type,
                trigger_ref_id=trigger_ref_id,
                transaction_validator=transaction_validator,
            )
        return self._create_revision(
            previous=previous,
            fact_source=fact_source,
            quality_level=quality_level,
            sold_qty=sold_qty,
            order_count=order_count,
            transaction_amount_total=transaction_amount_total,
            quality_reason=quality_reason,
            source_proportions=source_proportions,
            input_manifest_sha256=input_manifest_sha256,
            mapping_version=mapping_version,
            algorithm_version=algorithm_version,
            inputs=inputs,
            changed_by=changed_by,
            trigger_type=trigger_type,
            trigger_ref_id=trigger_ref_id,
        )

    def revise_final(
        self,
        summary_id: str,
        *,
        fact_source: FactSource,
        quality_level: DataQualityLevel,
        sold_qty: int,
        order_count: int,
        transaction_amount_total: Decimal,
        quality_reason: str,
        source_proportions: dict,
        input_manifest_sha256: str,
        mapping_version: str,
        algorithm_version: str,
        inputs: Iterable[TradeDaySummaryInput],
        changed_by: str,
        trigger_ref_id: str = "",
    ) -> SummaryMutationResult:
        previous = self._require_current(summary_id)
        if previous.summary_status is not SummaryStatus.FINAL:
            raise ValueError("Only a current FINAL summary can be revised")
        return self._create_revision(
            previous=previous,
            fact_source=fact_source,
            quality_level=quality_level,
            sold_qty=sold_qty,
            order_count=order_count,
            transaction_amount_total=transaction_amount_total,
            quality_reason=quality_reason,
            source_proportions=source_proportions,
            input_manifest_sha256=input_manifest_sha256,
            mapping_version=mapping_version,
            algorithm_version=algorithm_version,
            inputs=inputs,
            changed_by=changed_by,
            trigger_type="LATE_DATA_REVISION",
            trigger_ref_id=trigger_ref_id,
        )

    def _create_revision(
        self,
        *,
        previous: PlatformTradeDaySummary,
        fact_source: FactSource | None,
        quality_level: DataQualityLevel,
        sold_qty: int | None,
        order_count: int | None,
        transaction_amount_total: Decimal | None,
        quality_reason: str,
        source_proportions: dict,
        input_manifest_sha256: str,
        mapping_version: str,
        algorithm_version: str,
        inputs: Iterable[TradeDaySummaryInput],
        changed_by: str,
        trigger_type: str,
        trigger_ref_id: str,
    ) -> SummaryMutationResult:
        if previous.input_manifest_sha256 == input_manifest_sha256:
            return SummaryMutationResult(summary=previous, changed=False)

        now = _require_aware(self._clock())
        revision = replace(
            previous,
            summary_id=f"summary-{uuid4()}",
            version_no=previous.version_no + 1,
            supersedes_summary_id=previous.summary_id,
            fact_source=fact_source,
            quality_level=quality_level,
            summary_status=SummaryStatus.OBSERVED,
            sold_qty=sold_qty,
            order_count=order_count,
            transaction_amount_total=transaction_amount_total,
            quality_reason=quality_reason,
            source_proportions=dict(source_proportions),
            input_manifest_sha256=_require_text(
                input_manifest_sha256,
                "input_manifest_sha256",
            ),
            mapping_version=mapping_version,
            algorithm_version=_require_text(
                algorithm_version,
                "algorithm_version",
            ),
            finalized_at=None,
            created_at=now,
            updated_at=now,
        )
        _validate_summary(revision)
        event = _build_event(
            summary=revision,
            from_summary=previous,
            changed_at=now,
            changed_by=changed_by,
            trigger_type=trigger_type,
            trigger_ref_id=trigger_ref_id,
            reason=quality_reason,
        )
        input_rows = tuple(inputs)
        if not self.repository.insert_revision(
            previous=previous,
            revision=revision,
            event=event,
            inputs=input_rows,
        ):
            raise RuntimeError(
                "Summary changed concurrently; reload before retrying"
            )
        return SummaryMutationResult(
            summary=revision,
            changed=True,
            event=event,
            inputs=input_rows,
        )

    def _require_current(
        self,
        summary_id: str,
    ) -> PlatformTradeDaySummary:
        summary = self.repository.get_summary(summary_id)
        if summary is None:
            raise ValueError(f"Unknown summary_id: {summary_id}")
        if not summary.is_current:
            raise ValueError("Only the current summary version may change")
        return summary


def build_summary_series_id(
    *,
    platform_name: str,
    platform_trade_date: date,
    scope_type: str,
    scope_key: str,
) -> str:
    identity = "\x1f".join(
        (
            _require_text(platform_name, "platform_name"),
            platform_trade_date.isoformat(),
            _validate_scope_type(scope_type),
            _require_text(scope_key, "scope_key"),
        )
    )
    return "summary-series-v1-" + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


def _validate_summary(summary: PlatformTradeDaySummary) -> None:
    if summary.version_no <= 0:
        raise ValueError("version_no must be positive")
    if summary.sold_qty is not None and summary.sold_qty < 0:
        raise ValueError("sold_qty must not be negative")
    if summary.order_count is not None and summary.order_count < 0:
        raise ValueError("order_count must not be negative")
    if (
        summary.transaction_amount_total is not None
        and summary.transaction_amount_total < 0
    ):
        raise ValueError("transaction_amount_total must not be negative")

    if summary.quality_level is DataQualityLevel.UNAVAILABLE:
        if summary.fact_source is not None:
            raise ValueError("UNAVAILABLE facts must have a NULL source")
        if any(
            value is not None
            for value in (
                summary.sold_qty,
                summary.order_count,
                summary.transaction_amount_total,
            )
        ):
            raise ValueError("UNAVAILABLE facts must keep metrics NULL")
    elif summary.fact_source is FactSource.ORDER_OBSERVED:
        if summary.quality_level not in ORDER_QUALITY_LEVELS:
            raise ValueError("ORDER_OBSERVED requires an order quality")
    elif summary.fact_source is FactSource.SCAN_ESTIMATED:
        if summary.quality_level not in SCAN_QUALITY_LEVELS:
            raise ValueError("SCAN_ESTIMATED requires a scan quality")
    else:
        raise ValueError("Available facts require a frozen fact_source")

    if summary.summary_status in {
        SummaryStatus.OBSERVED,
        SummaryStatus.RECONCILED,
        SummaryStatus.FINAL,
    } and summary.fact_source is not FactSource.ORDER_OBSERVED:
        raise ValueError(
            f"{summary.summary_status.value} requires ORDER_OBSERVED"
        )
    if summary.summary_status is SummaryStatus.FINAL:
        if summary.quality_level is not DataQualityLevel.ORDER_COMPLETE:
            raise ValueError("FINAL requires ORDER_COMPLETE")
        if summary.finalized_at is None:
            raise ValueError("FINAL requires finalized_at")
    elif summary.finalized_at is not None:
        raise ValueError("Only FINAL may have finalized_at")


def _build_event(
    *,
    summary: PlatformTradeDaySummary,
    from_summary: PlatformTradeDaySummary | None,
    changed_at: datetime,
    changed_by: str,
    trigger_type: str,
    trigger_ref_id: str,
    reason: str,
) -> TradeDaySummaryEvent:
    return TradeDaySummaryEvent(
        event_id=f"summary-event-{uuid4()}",
        summary_id=summary.summary_id,
        from_status=(
            from_summary.summary_status if from_summary else None
        ),
        to_status=summary.summary_status,
        trigger_type=_require_text(trigger_type, "trigger_type"),
        trigger_ref_id=trigger_ref_id,
        fact_source_before=(
            from_summary.fact_source if from_summary else None
        ),
        fact_source_after=summary.fact_source,
        quality_level_before=(
            from_summary.quality_level if from_summary else None
        ),
        quality_level_after=summary.quality_level,
        input_manifest_sha256=summary.input_manifest_sha256,
        changed_at=changed_at,
        changed_by=_require_text(changed_by, "changed_by"),
        reason=reason,
    )


def _validate_scope_type(value: str) -> str:
    normalized = _require_text(value, "scope_type").upper()
    allowed = {"PLATFORM", "VARIETY", "GRADE", "SKU", "TIME_BUCKET"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported scope_type: {value}")
    return normalized


def _require_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Summary clock must return a timezone-aware datetime")
    return value
