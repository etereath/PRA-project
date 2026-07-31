from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Mapping

from app.automation_models import AutomationRun
from app.enums import DataQualityLevel, FactSource, SummaryStatus
from app.operational_models import (
    SummaryMutationResult,
    TradeDaySummaryInput,
)
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.sales_settlement_models import (
    OrderCancellationResult,
    OrderSnapshot,
    ReconciliationDecision,
    SalesEstimateSegment,
    SalesFactSelection,
)
from app.services.order_cancellation import OrderCancellationService
from app.services.sales_fact_selection import SalesFactSelectionService
from app.services.sales_estimate import SalesEstimateService
from app.services.trade_day_summary import (
    TradeDaySummaryService,
    build_summary_series_id,
)


SETTLEMENT_ALGORITHM_VERSION = "trade-day-settlement-v1"
DEFAULT_FINALIZATION_POLICY_VERSION = "finalization-policy-v1"


@dataclass(frozen=True, slots=True)
class SettlementEvidence:
    selection: SalesFactSelection
    order_snapshots: tuple[OrderSnapshot, ...]
    estimate_segments: tuple[SalesEstimateSegment, ...]


class TradeDaySettlementService:
    """Apply the frozen order/estimate authority and summary lifecycle."""

    def __init__(
        self,
        repository: OperationalSummaryRepository,
        *,
        summary_service: TradeDaySummaryService | None = None,
        fact_selector: SalesFactSelectionService | None = None,
        cancellation_service: OrderCancellationService | None = None,
        estimate_service: SalesEstimateService | None = None,
        sku_dimensions: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.repository = repository
        self.summary_service = summary_service or TradeDaySummaryService(
            repository
        )
        self.fact_selector = fact_selector or SalesFactSelectionService()
        self.cancellation_service = (
            cancellation_service or OrderCancellationService()
        )
        self.estimate_service = estimate_service or SalesEstimateService()
        self.sku_dimensions = dict(sku_dimensions or {})

    def create_provisional_for_run(
        self,
        run: AutomationRun,
        *,
        scope_type: str = "PLATFORM",
        scope_key: str | None = None,
        changed_by: str = "automation-settlement",
        transaction_validator: Callable[[object], None] | None = None,
    ) -> SummaryMutationResult:
        target_trade_date = run.platform_trade_date - timedelta(days=1)
        return self.create_provisional(
            platform_name=run.platform_name,
            platform_trade_date=target_trade_date,
            seller_operation_date=run.seller_operation_date,
            seller_phase=run.seller_phase,
            time_policy_version=run.time_policy_version,
            scope_type=scope_type,
            scope_key=scope_key or run.platform_name,
            changed_by=changed_by,
            trigger_ref_id=run.run_id,
            transaction_validator=transaction_validator,
        )

    def create_provisionals_for_run(
        self,
        run: AutomationRun,
        *,
        changed_by: str = "automation-settlement",
        transaction_validator: Callable[[object], None] | None = None,
    ) -> tuple[SummaryMutationResult, ...]:
        target_trade_date = run.platform_trade_date - timedelta(days=1)
        self.materialize_estimates(
            platform_name=run.platform_name,
            platform_trade_date=target_trade_date,
            transaction_validator=transaction_validator,
        )
        scopes = self._automatic_scopes(
            platform_name=run.platform_name,
            platform_trade_date=target_trade_date,
        )
        return tuple(
            self.create_provisional(
                platform_name=run.platform_name,
                platform_trade_date=target_trade_date,
                seller_operation_date=run.seller_operation_date,
                seller_phase=run.seller_phase,
                time_policy_version=run.time_policy_version,
                scope_type=scope_type,
                scope_key=scope_key,
                changed_by=changed_by,
                trigger_ref_id=run.run_id,
                transaction_validator=transaction_validator,
                materialize_estimate_segments=False,
            )
            for scope_type, scope_key in scopes
        )

    def create_provisional(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        seller_operation_date,
        seller_phase,
        time_policy_version: str,
        scope_type: str,
        scope_key: str,
        changed_by: str,
        trigger_ref_id: str = "",
        transaction_validator: Callable[[object], None] | None = None,
        materialize_estimate_segments: bool = True,
    ) -> SummaryMutationResult:
        if materialize_estimate_segments:
            self.materialize_estimates(
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
                transaction_validator=transaction_validator,
            )
        evidence = self.select_evidence(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
        )
        inputs = _summary_inputs(
            (
                *evidence.selection.input_refs,
                _settlement_window_ref(
                    platform_name=platform_name,
                    platform_trade_date=platform_trade_date,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    time_policy_version=time_policy_version,
                ),
            )
        )
        manifest_sha256 = input_manifest_sha256(inputs)
        series_id = build_summary_series_id(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
        )
        existing = self.repository.get_current_summary(series_id)
        if existing is not None:
            if existing.summary_status is not SummaryStatus.PROVISIONAL:
                return SummaryMutationResult(summary=existing, changed=False)
            return self.summary_service.revise_current(
                existing.summary_id,
                fact_source=evidence.selection.fact_source,
                quality_level=evidence.selection.quality_level,
                sold_qty=evidence.selection.sold_qty,
                order_count=evidence.selection.order_count,
                transaction_amount_total=(
                    evidence.selection.transaction_amount_total
                ),
                quality_reason=evidence.selection.quality_reason,
                source_proportions=evidence.selection.source_proportions,
                input_manifest_sha256=manifest_sha256,
                mapping_version=evidence.selection.mapping_version,
                algorithm_version=SETTLEMENT_ALGORITHM_VERSION,
                inputs=inputs,
                changed_by=changed_by,
                trigger_type="SETTLEMENT_REFRESH",
                trigger_ref_id=trigger_ref_id,
                transaction_validator=transaction_validator,
            )
        return self.summary_service.create_provisional(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            seller_operation_date=seller_operation_date,
            seller_phase=seller_phase,
            scope_type=scope_type,
            scope_key=scope_key,
            fact_source=evidence.selection.fact_source,
            quality_level=evidence.selection.quality_level,
            sold_qty=evidence.selection.sold_qty,
            order_count=evidence.selection.order_count,
            transaction_amount_total=(
                evidence.selection.transaction_amount_total
            ),
            quality_reason=evidence.selection.quality_reason,
            source_proportions=evidence.selection.source_proportions,
            input_manifest_sha256=manifest_sha256,
            mapping_version=evidence.selection.mapping_version,
            algorithm_version=SETTLEMENT_ALGORITHM_VERSION,
            time_policy_version=time_policy_version,
            inputs=inputs,
            changed_by=changed_by,
            trigger_ref_id=trigger_ref_id,
            transaction_validator=transaction_validator,
        )

    def materialize_estimates(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        transaction_validator: Callable[[object], None] | None = None,
    ) -> tuple[SalesEstimateSegment, ...]:
        observations = self.repository.list_inventory_observations(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        grouped = defaultdict(list)
        for observation in observations:
            grouped[
                (
                    observation.platform_name,
                    observation.internal_sku or "",
                    observation.platform_trade_date,
                )
            ].append(observation)
        adjustments_by_interval = {}
        unresolved_intervals = set()
        for points in grouped.values():
            ordered = sorted(
                points,
                key=lambda item: (
                    item.observed_at,
                    item.observation_item_id,
                ),
            )
            for before, after in zip(ordered, ordered[1:]):
                if after.observed_at <= before.observed_at or not before.internal_sku:
                    continue
                identity = (
                    before.observation_item_id,
                    after.observation_item_id,
                )
                adjustments_by_interval[identity] = (
                    self.repository.list_inventory_adjustment_sources(
                        platform_name=before.platform_name,
                        internal_sku=before.internal_sku,
                        interval_started_at=before.observed_at,
                        interval_ended_at=after.observed_at,
                    )
                )
                if self.repository.has_unresolved_inventory_write(
                    platform_name=before.platform_name,
                    internal_sku=before.internal_sku,
                    interval_started_at=before.observed_at,
                    interval_ended_at=after.observed_at,
                ):
                    unresolved_intervals.add(identity)
        segments = self.estimate_service.build_adjacent_segments(
            observations,
            adjustments_by_interval=adjustments_by_interval,
            unresolved_intervals=unresolved_intervals,
        )
        for segment in segments:
            self.repository.append_estimate_segment(
                segment,
                transaction_validator=transaction_validator,
            )
        return segments

    def _automatic_scopes(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
    ) -> tuple[tuple[str, str], ...]:
        orders = self.repository.list_order_snapshots(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        estimates = self.repository.list_estimate_segments(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        scopes: set[tuple[str, str]] = {("PLATFORM", platform_name)}
        if estimates:
            scopes.update(
                ("SKU", segment.internal_sku)
                for segment in estimates
                if segment.internal_sku
            )
        if orders:
            scopes.update(
                ("GRADE", item.grade)
                for snapshot in orders
                for item in snapshot.items
                if item.grade.strip()
            )
            scopes.update(
                ("SKU", item.internal_sku)
                for snapshot in orders
                for item in snapshot.items
                if item.internal_sku
            )
            scopes.update(
                ("TIME_BUCKET", f"{hour:02}:00-{hour:02}:59")
                for hour in range(24)
            )
            for sku, dimensions in self.sku_dimensions.items():
                if dimensions.get("variety", "").strip():
                    scopes.add(("VARIETY", dimensions["variety"].strip()))
                scopes.add(("SKU", sku))
        scope_order = {
            "PLATFORM": 0,
            "VARIETY": 1,
            "GRADE": 2,
            "SKU": 3,
            "TIME_BUCKET": 4,
        }
        return tuple(
            sorted(
                scopes,
                key=lambda item: (scope_order[item[0]], item[1]),
            )
        )

    def observe(
        self,
        summary_id: str,
        *,
        changed_by: str,
        trigger_ref_id: str = "",
    ) -> SummaryMutationResult:
        current = self._require_summary(summary_id, SummaryStatus.PROVISIONAL)
        evidence = self.select_evidence(
            platform_name=current.platform_name,
            platform_trade_date=current.platform_trade_date,
            scope_type=current.scope_type,
            scope_key=current.scope_key,
        )
        selection = evidence.selection
        if selection.fact_source is not FactSource.ORDER_OBSERVED:
            raise ValueError("OBSERVED requires an accepted order batch")
        previous_inputs = self.repository.list_inputs(summary_id)
        inputs = _summary_inputs(
            (
                *(
                    (
                        item.input_type,
                        item.input_ref_id,
                        item.input_sha256,
                    )
                    for item in previous_inputs
                ),
                *selection.input_refs,
            )
        )
        return self.summary_service.transition(
            summary_id,
            to_status=SummaryStatus.OBSERVED,
            fact_source=selection.fact_source,
            quality_level=selection.quality_level,
            sold_qty=selection.sold_qty,
            order_count=selection.order_count,
            transaction_amount_total=selection.transaction_amount_total,
            quality_reason=selection.quality_reason,
            source_proportions=selection.source_proportions,
            input_manifest_sha256=input_manifest_sha256(inputs),
            mapping_version=selection.mapping_version,
            algorithm_version=SETTLEMENT_ALGORITHM_VERSION,
            inputs=inputs,
            changed_by=changed_by,
            trigger_type="ORDER_BATCH_ACCEPTED",
            trigger_ref_id=(
                trigger_ref_id or selection.selected_order_batch_id or ""
            ),
        )

    def reconcile(
        self,
        summary_id: str,
        *,
        changed_by: str,
        decision: ReconciliationDecision | None = None,
    ) -> SummaryMutationResult:
        current = self._require_summary(summary_id, SummaryStatus.OBSERVED)
        evidence = self.select_evidence(
            platform_name=current.platform_name,
            platform_trade_date=current.platform_trade_date,
            scope_type=current.scope_type,
            scope_key=current.scope_key,
        )
        selection = evidence.selection
        if selection.fact_source is not FactSource.ORDER_OBSERVED:
            raise ValueError("RECONCILED requires order observations")
        estimate = self._select_estimate_only(
            current.platform_name,
            current.platform_trade_date,
            current.scope_type,
            current.scope_key,
            evidence.estimate_segments,
        )
        difference = (
            selection.sold_qty - estimate.sold_qty
            if selection.sold_qty is not None and estimate.sold_qty is not None
            else None
        )
        resolved_decision = _resolve_reconciliation_decision(
            decision,
            difference_qty=difference,
        )
        cancellation = self._latest_cancellation(evidence, selection)
        if cancellation is not None and cancellation.status != "DETERMINED":
            raise ValueError("Cancellation comparison is not deterministic")

        refs = [
            (item.input_type, item.input_ref_id, item.input_sha256)
            for item in self.repository.list_inputs(summary_id)
        ]
        refs.extend(selection.input_refs)
        refs.extend(estimate.input_refs)
        refs.append(
            (
                "RECONCILIATION_DECISION",
                resolved_decision.decision_ref_id,
                resolved_decision.decision_sha256,
            )
        )
        if cancellation is not None:
            refs.append(
                (
                    "CANCELLATION_COMPARISON",
                    (
                        f"{cancellation.previous_batch_id}"
                        f"..{cancellation.current_batch_id}"
                    ),
                    cancellation.comparison_sha256,
                )
            )
        inputs = _summary_inputs(refs)
        proportions = dict(selection.source_proportions)
        if estimate.fact_source is FactSource.SCAN_ESTIMATED:
            proportions["SCAN_ESTIMATED"] = 1.0
        return self.summary_service.transition(
            summary_id,
            to_status=SummaryStatus.RECONCILED,
            fact_source=selection.fact_source,
            quality_level=selection.quality_level,
            sold_qty=selection.sold_qty,
            order_count=selection.order_count,
            transaction_amount_total=selection.transaction_amount_total,
            quality_reason=f"RECONCILED:{resolved_decision.classification}",
            source_proportions=proportions,
            input_manifest_sha256=input_manifest_sha256(inputs),
            mapping_version=selection.mapping_version,
            algorithm_version=SETTLEMENT_ALGORITHM_VERSION,
            inputs=inputs,
            changed_by=changed_by,
            trigger_type="RECONCILIATION_COMPLETED",
            trigger_ref_id=resolved_decision.decision_ref_id,
        )

    def finalize(
        self,
        summary_id: str,
        *,
        changed_by: str,
        policy_version: str = DEFAULT_FINALIZATION_POLICY_VERSION,
    ) -> SummaryMutationResult:
        current = self._require_summary(summary_id, SummaryStatus.RECONCILED)
        if "UNCLASSIFIED" in current.quality_reason:
            raise ValueError("FINAL requires every difference to be classified")
        evidence = self.select_evidence(
            platform_name=current.platform_name,
            platform_trade_date=current.platform_trade_date,
            scope_type=current.scope_type,
            scope_key=current.scope_key,
        )
        selection = evidence.selection
        if (
            selection.fact_source is not FactSource.ORDER_OBSERVED
            or selection.quality_level is not DataQualityLevel.ORDER_COMPLETE
            or selection.selected_order_batch_status != "CLOSED"
            or not selection.selected_order_batch_id
        ):
            raise ValueError("FINAL requires a complete CLOSED order batch")
        if (
            current.sold_qty != selection.sold_qty
            or current.order_count != selection.order_count
            or current.transaction_amount_total
            != selection.transaction_amount_total
            or current.mapping_version != selection.mapping_version
        ):
            raise ValueError(
                "FINAL evidence changed after reconciliation; reconcile again"
            )
        cancellation = self._latest_cancellation(evidence, selection)
        if cancellation is not None and cancellation.status != "DETERMINED":
            raise ValueError("FINAL requires deterministic cancellation evidence")

        prior_inputs = self.repository.list_inputs(summary_id)
        if not any(
            item.input_type == "RECONCILIATION_DECISION"
            for item in prior_inputs
        ):
            raise ValueError("FINAL requires a reconciliation decision input")
        selected_order_ref = selection.input_refs[0]
        if not any(
            item.input_type == selected_order_ref[0]
            and item.input_ref_id == selected_order_ref[1]
            and item.input_sha256 == selected_order_ref[2]
            for item in prior_inputs
        ):
            raise ValueError(
                "FINAL authoritative order input was not reconciled"
            )
        refs = [
            (item.input_type, item.input_ref_id, item.input_sha256)
            for item in prior_inputs
        ]
        refs.extend(selection.input_refs)
        inputs = _summary_inputs(refs)
        previous_snapshot = self._previous_complete_snapshot(
            evidence,
            selection.selected_order_batch_id,
        )

        def validate_in_transaction(connection) -> None:
            stored = self.repository.get_order_snapshot(
                selection.selected_order_batch_id or "",
                connection=connection,
            )
            if stored is None:
                raise ValueError("FINAL order input no longer exists")
            recomputed = self.fact_selector.select(
                platform_name=current.platform_name,
                platform_trade_date=current.platform_trade_date,
                scope_type=current.scope_type,
                scope_key=current.scope_key,
                order_snapshots=(stored,),
                estimate_segments=(),
                sku_dimensions=self.sku_dimensions,
            )
            if (
                recomputed.quality_level is not DataQualityLevel.ORDER_COMPLETE
                or recomputed.selected_order_batch_status != "CLOSED"
                or recomputed.sold_qty != selection.sold_qty
                or recomputed.order_count != selection.order_count
                or recomputed.transaction_amount_total
                != selection.transaction_amount_total
                or stored.content_sha256
                != selection.input_refs[0][2]
                or stored.time_policy_version != current.time_policy_version
            ):
                raise ValueError("FINAL order evidence changed during validation")
            if previous_snapshot is not None:
                previous_stored = self.repository.get_order_snapshot(
                    previous_snapshot.observation_batch_id,
                    connection=connection,
                )
                if previous_stored is None:
                    raise ValueError("FINAL cancellation input no longer exists")
                compared = self.cancellation_service.compare(
                    previous_stored,
                    stored,
                )
                if (
                    cancellation is None
                    or compared.status != "DETERMINED"
                    or compared.comparison_sha256
                    != cancellation.comparison_sha256
                ):
                    raise ValueError("FINAL cancellation evidence changed")

        return self.summary_service.transition(
            summary_id,
            to_status=SummaryStatus.FINAL,
            fact_source=selection.fact_source,
            quality_level=selection.quality_level,
            sold_qty=selection.sold_qty,
            order_count=selection.order_count,
            transaction_amount_total=selection.transaction_amount_total,
            quality_reason=f"FINAL_POLICY_PASSED:{policy_version}",
            source_proportions=current.source_proportions,
            input_manifest_sha256=input_manifest_sha256(inputs),
            mapping_version=selection.mapping_version,
            algorithm_version=SETTLEMENT_ALGORITHM_VERSION,
            inputs=inputs,
            changed_by=changed_by,
            trigger_type="FINALIZATION_POLICY",
            trigger_ref_id=policy_version,
            finalization_validator=validate_in_transaction,
        )

    def select_evidence(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        scope_type: str,
        scope_key: str,
    ) -> SettlementEvidence:
        orders = self.repository.list_order_snapshots(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        estimates = self.repository.list_estimate_segments(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        selection = self.fact_selector.select(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
            order_snapshots=orders,
            estimate_segments=estimates,
            sku_dimensions=self.sku_dimensions,
        )
        return SettlementEvidence(selection, orders, estimates)

    def _select_estimate_only(
        self,
        platform_name: str,
        platform_trade_date: date,
        scope_type: str,
        scope_key: str,
        estimates: tuple[SalesEstimateSegment, ...],
    ) -> SalesFactSelection:
        return self.fact_selector.select(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
            order_snapshots=(),
            estimate_segments=estimates,
            sku_dimensions=self.sku_dimensions,
        )

    def _latest_cancellation(
        self,
        evidence: SettlementEvidence,
        selection: SalesFactSelection,
    ) -> OrderCancellationResult | None:
        current_id = selection.selected_order_batch_id
        if not current_id:
            return None
        previous = self._previous_complete_snapshot(evidence, current_id)
        current = next(
            snapshot
            for snapshot in evidence.order_snapshots
            if snapshot.observation_batch_id == current_id
        )
        return (
            self.cancellation_service.compare(previous, current)
            if previous is not None
            else None
        )

    def _previous_complete_snapshot(
        self,
        evidence: SettlementEvidence,
        current_batch_id: str,
    ) -> OrderSnapshot | None:
        ordered = sorted(
            (
                snapshot
                for snapshot in evidence.order_snapshots
                if snapshot.trade_day_status == "CLOSED"
                and snapshot.capability_result == "SUCCEEDED"
                and snapshot.source_batch_status == "ACCEPTED"
                and snapshot.scope_complete
                and snapshot.end_marker_verified
            ),
            key=lambda item: (
                item.scan_completed_at,
                item.observation_batch_id,
            ),
        )
        ids = [item.observation_batch_id for item in ordered]
        if current_batch_id not in ids:
            return None
        index = ids.index(current_batch_id)
        return ordered[index - 1] if index > 0 else None

    def _require_summary(self, summary_id: str, status: SummaryStatus):
        summary = self.repository.get_summary(summary_id)
        if summary is None:
            raise ValueError(f"Unknown summary_id: {summary_id}")
        if not summary.is_current or summary.summary_status is not status:
            raise ValueError(f"Summary must be current {status.value}")
        return summary


def _resolve_reconciliation_decision(
    decision: ReconciliationDecision | None,
    *,
    difference_qty: int | None,
) -> ReconciliationDecision:
    if difference_qty is None:
        return _automatic_decision("NO_COMPARABLE_ESTIMATE", None)
    if difference_qty == 0:
        return _automatic_decision("MATCHED", 0)
    if decision is None:
        raise ValueError("A non-zero order/estimate difference needs a decision")
    if decision.classification == "UNCLASSIFIED_DIFFERENCE":
        raise ValueError("Unclassified differences cannot be reconciled")
    if decision.difference_qty != difference_qty:
        raise ValueError("Reconciliation decision difference_qty is stale")
    if not decision.decision_ref_id.strip():
        raise ValueError("Reconciliation decision_ref_id must not be blank")
    if not decision.decision_sha256.startswith("sha256:"):
        raise ValueError("Reconciliation decision_sha256 is invalid")
    return decision


def _automatic_decision(
    classification: str,
    difference_qty: int | None,
) -> ReconciliationDecision:
    payload = {
        "classification": classification,
        "difference_qty": difference_qty,
        "algorithm_version": SETTLEMENT_ALGORITHM_VERSION,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return ReconciliationDecision(
        decision_ref_id=f"auto-reconciliation-{digest[:24]}",
        decision_sha256=f"sha256:{digest}",
        classification=classification,
        difference_qty=difference_qty,
    )


def _summary_inputs(
    refs,
) -> tuple[TradeDaySummaryInput, ...]:
    deduplicated: dict[tuple[str, str], str] = {}
    for input_type, input_ref_id, input_sha256 in refs:
        identity = (str(input_type), str(input_ref_id))
        digest = str(input_sha256)
        existing = deduplicated.get(identity)
        if existing is not None and existing != digest:
            raise ValueError(
                "One summary input identity has conflicting hashes"
            )
        deduplicated[identity] = digest
    return tuple(
        TradeDaySummaryInput(
            input_type=input_type,
            input_ref_id=input_ref_id,
            input_sha256=input_sha256,
        )
        for (input_type, input_ref_id), input_sha256 in sorted(
            deduplicated.items()
        )
    )


def _settlement_window_ref(
    *,
    platform_name: str,
    platform_trade_date: date,
    scope_type: str,
    scope_key: str,
    time_policy_version: str,
) -> tuple[str, str, str]:
    payload = {
        "platform_name": platform_name,
        "platform_trade_date": platform_trade_date.isoformat(),
        "scope_type": scope_type,
        "scope_key": scope_key,
        "time_policy_version": time_policy_version,
        "algorithm_version": SETTLEMENT_ALGORITHM_VERSION,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return (
        "SETTLEMENT_WINDOW",
        f"{platform_name}:{platform_trade_date.isoformat()}:{scope_type}:{scope_key}",
        f"sha256:{digest}",
    )


def input_manifest_sha256(inputs: tuple[TradeDaySummaryInput, ...]) -> str:
    payload = [
        {
            "input_type": item.input_type,
            "input_ref_id": item.input_ref_id,
            "input_sha256": item.input_sha256,
        }
        for item in sorted(
            inputs,
            key=lambda value: (value.input_type, value.input_ref_id),
        )
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
