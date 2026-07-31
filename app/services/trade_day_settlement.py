from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from contextlib import closing
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
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
)
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
        with closing(
            self.repository.runtime_repository.connect_write()
        ) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                frozen_orders = self.repository.list_order_snapshots(
                    platform_name=run.platform_name,
                    platform_trade_date=target_trade_date,
                    connection=connection,
                )
                frozen_estimates = self.repository.list_estimate_segments(
                    platform_name=run.platform_name,
                    platform_trade_date=target_trade_date,
                    connection=connection,
                )
                frozen_dimensions = self._current_sku_dimensions(
                    run.platform_name,
                    connection=connection,
                )
                scopes = self._automatic_scopes(
                    platform_name=run.platform_name,
                    platform_trade_date=target_trade_date,
                    orders=frozen_orders,
                    estimates=frozen_estimates,
                    dimensions=frozen_dimensions,
                )
                coverage_started_at, coverage_ended_at = (
                    self.estimate_service.operational_time.platform_trade_day_window(
                        target_trade_date,
                        policy_version=run.time_policy_version,
                    )
                )
                results = tuple(
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
                        frozen_selection=self.fact_selector.select(
                            platform_name=run.platform_name,
                            platform_trade_date=target_trade_date,
                            scope_type=scope_type,
                            scope_key=scope_key,
                            order_snapshots=frozen_orders,
                            estimate_segments=frozen_estimates,
                            sku_dimensions=frozen_dimensions,
                            estimate_algorithm_version=(
                                self.estimate_service.algorithm_version
                            ),
                            coverage_started_at=coverage_started_at,
                            coverage_ended_at=coverage_ended_at,
                        ),
                        connection=connection,
                    )
                    for scope_type, scope_key in scopes
                )
                connection.commit()
                return results
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

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
        frozen_selection: SalesFactSelection | None = None,
        connection=None,
    ) -> SummaryMutationResult:
        if materialize_estimate_segments:
            self.materialize_estimates(
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
                transaction_validator=transaction_validator,
            )
        selection = frozen_selection
        if selection is None:
            selection = self.select_evidence(
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
                scope_type=scope_type,
                scope_key=scope_key,
                time_policy_version=time_policy_version,
            ).selection
        inputs = _summary_inputs(
            (
                *selection.input_refs,
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
        existing = self.repository.get_current_summary(
            series_id,
            connection=connection,
        )
        if existing is not None:
            if existing.summary_status is not SummaryStatus.PROVISIONAL:
                return SummaryMutationResult(summary=existing, changed=False)
            return self.summary_service.revise_current(
                existing.summary_id,
                fact_source=selection.fact_source,
                quality_level=selection.quality_level,
                sold_qty=selection.sold_qty,
                order_count=selection.order_count,
                transaction_amount_total=(
                    selection.transaction_amount_total
                ),
                quality_reason=selection.quality_reason,
                source_proportions=selection.source_proportions,
                input_manifest_sha256=manifest_sha256,
                mapping_version=selection.mapping_version,
                algorithm_version=SETTLEMENT_ALGORITHM_VERSION,
                inputs=inputs,
                changed_by=changed_by,
                trigger_type="SETTLEMENT_REFRESH",
                trigger_ref_id=trigger_ref_id,
                transaction_validator=transaction_validator,
                connection=connection,
            )
        return self.summary_service.create_provisional(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            seller_operation_date=seller_operation_date,
            seller_phase=seller_phase,
            scope_type=scope_type,
            scope_key=scope_key,
            fact_source=selection.fact_source,
            quality_level=selection.quality_level,
            sold_qty=selection.sold_qty,
            order_count=selection.order_count,
            transaction_amount_total=(
                selection.transaction_amount_total
            ),
            quality_reason=selection.quality_reason,
            source_proportions=selection.source_proportions,
            input_manifest_sha256=manifest_sha256,
            mapping_version=selection.mapping_version,
            algorithm_version=SETTLEMENT_ALGORITHM_VERSION,
            time_policy_version=time_policy_version,
            inputs=inputs,
            changed_by=changed_by,
            trigger_ref_id=trigger_ref_id,
            transaction_validator=transaction_validator,
            connection=connection,
        )

    def refresh_after_order_import(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        observation_batch_id: str,
        changed_by: str = "order-history-import",
    ) -> tuple[SummaryMutationResult, ...]:
        """Refresh existing summary series from one committed order import."""

        summaries = self.repository.list_current_summaries(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        if not summaries:
            return ()
        orders = self.repository.list_order_snapshots(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        estimates = self.repository.list_estimate_segments(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        dimensions = self._current_sku_dimensions(platform_name)
        results: list[SummaryMutationResult] = []
        for current in summaries:
            coverage_started_at, coverage_ended_at = (
                self.estimate_service.operational_time.platform_trade_day_window(
                    platform_trade_date,
                    policy_version=current.time_policy_version,
                )
            )
            selection = self.fact_selector.select(
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
                scope_type=current.scope_type,
                scope_key=current.scope_key,
                order_snapshots=orders,
                estimate_segments=estimates,
                sku_dimensions=dimensions,
                estimate_algorithm_version=(
                    self.estimate_service.algorithm_version
                ),
                coverage_started_at=coverage_started_at,
                coverage_ended_at=coverage_ended_at,
            )
            if (
                selection.fact_source is not FactSource.ORDER_OBSERVED
                or selection.selected_order_batch_id != observation_batch_id
                or selection.sold_qty is None
                or selection.order_count is None
                or selection.transaction_amount_total is None
            ):
                continue
            inputs = _summary_inputs(
                (
                    _settlement_window_ref(
                        platform_name=platform_name,
                        platform_trade_date=platform_trade_date,
                        scope_type=current.scope_type,
                        scope_key=current.scope_key,
                        time_policy_version=current.time_policy_version,
                    ),
                    *selection.input_refs,
                )
            )
            manifest_sha256 = input_manifest_sha256(inputs)
            common = {
                "quality_level": selection.quality_level,
                "sold_qty": selection.sold_qty,
                "order_count": selection.order_count,
                "transaction_amount_total": (
                    selection.transaction_amount_total
                ),
                "quality_reason": "ORDER_HISTORY_IMPORT_REFRESH",
                "source_proportions": selection.source_proportions,
                "input_manifest_sha256": manifest_sha256,
                "mapping_version": selection.mapping_version,
                "algorithm_version": SETTLEMENT_ALGORITHM_VERSION,
                "inputs": inputs,
                "changed_by": changed_by,
                "trigger_ref_id": observation_batch_id,
            }
            if current.summary_status is SummaryStatus.FINAL:
                result = self.summary_service.revise_final(
                    current.summary_id,
                    fact_source=FactSource.ORDER_OBSERVED,
                    **common,
                )
            else:
                result = self.summary_service.refresh_non_final_from_order(
                    current.summary_id,
                    **common,
                )
            results.append(result)
        return tuple(results)

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
        failed_intervals = set()
        scan_executions = self.repository.list_product_scan_executions(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
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
                if any(
                    execution.critical_failure
                    and before.observed_at
                    < execution.scan_started_at
                    <= after.observed_at
                    for execution in scan_executions
                ):
                    failed_intervals.add(identity)
        segments = self.estimate_service.build_adjacent_segments(
            observations,
            adjustments_by_interval=adjustments_by_interval,
            unresolved_intervals=unresolved_intervals,
            failed_intervals=failed_intervals,
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
        orders: tuple[OrderSnapshot, ...] | None = None,
        estimates: tuple[SalesEstimateSegment, ...] | None = None,
        dimensions: Mapping[str, Mapping[str, str]] | None = None,
    ) -> tuple[tuple[str, str], ...]:
        orders = orders if orders is not None else self.repository.list_order_snapshots(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
        )
        estimates = (
            estimates
            if estimates is not None
            else self.repository.list_estimate_segments(
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
            )
        )
        dimensions = dimensions or self._current_sku_dimensions(platform_name)
        scopes: set[tuple[str, str]] = {("PLATFORM", platform_name)}
        if estimates:
            scopes.update(
                ("SKU", segment.internal_sku)
                for segment in estimates
                if segment.internal_sku
            )
            scopes.update(
                (
                    "VARIETY",
                    dimensions.get(segment.internal_sku, {}).get("variety", ""),
                )
                for segment in estimates
                if dimensions.get(segment.internal_sku, {})
                .get("variety", "")
                .strip()
            )
            scopes.update(
                (
                    "GRADE",
                    dimensions.get(segment.internal_sku, {}).get("grade", ""),
                )
                for segment in estimates
                if dimensions.get(segment.internal_sku, {})
                .get("grade", "")
                .strip()
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
        if orders or estimates:
            scopes.update(
                ("TIME_BUCKET", f"{hour:02}:00-{hour:02}:59")
                for hour in range(24)
            )
            for sku, sku_dimension in dimensions.items():
                if sku_dimension.get("variety", "").strip():
                    scopes.add(("VARIETY", sku_dimension["variety"].strip()))
                if sku_dimension.get("grade", "").strip():
                    scopes.add(("GRADE", sku_dimension["grade"].strip()))
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
            time_policy_version=current.time_policy_version,
        )
        selection = evidence.selection
        if selection.fact_source is not FactSource.ORDER_OBSERVED:
            raise ValueError("OBSERVED requires an accepted order batch")
        previous_inputs = self.repository.list_inputs(summary_id)
        inputs = _summary_inputs(
            (
                *_retained_control_refs(previous_inputs),
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
            time_policy_version=current.time_policy_version,
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
            time_policy_version=current.time_policy_version,
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

        refs = list(
            _retained_control_refs(self.repository.list_inputs(summary_id))
        )
        refs.extend(selection.input_refs)
        refs.extend(estimate.input_refs)
        refs.append(
            (
                "RECONCILIATION_DECISION",
                resolved_decision.decision_ref_id,
                resolved_decision.decision_sha256,
            )
        )
        refs.append(
            _reconciliation_binding_ref(
                selection=selection,
                estimate=estimate,
                decision=resolved_decision,
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
        return self.summary_service.transition(
            summary_id,
            to_status=SummaryStatus.RECONCILED,
            fact_source=selection.fact_source,
            quality_level=selection.quality_level,
            sold_qty=selection.sold_qty,
            order_count=selection.order_count,
            transaction_amount_total=selection.transaction_amount_total,
            quality_reason=f"RECONCILED:{resolved_decision.classification}",
            source_proportions=selection.source_proportions,
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
            time_policy_version=current.time_policy_version,
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
        decision_inputs = tuple(
            item
            for item in prior_inputs
            if item.input_type == "RECONCILIATION_DECISION"
        )
        binding_inputs = tuple(
            item
            for item in prior_inputs
            if item.input_type == "RECONCILIATION_BINDING"
        )
        if len(decision_inputs) != 1:
            raise ValueError("FINAL requires a reconciliation decision input")
        if len(binding_inputs) != 1:
            raise ValueError("FINAL requires one reconciliation evidence binding")
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
        estimate = self._select_estimate_only(
            current.platform_name,
            current.platform_trade_date,
            current.scope_type,
            current.scope_key,
            evidence.estimate_segments,
            time_policy_version=current.time_policy_version,
        )
        difference = (
            selection.sold_qty - estimate.sold_qty
            if selection.sold_qty is not None and estimate.sold_qty is not None
            else None
        )
        classification = _reconciliation_classification(current.quality_reason)
        decision_input = decision_inputs[0]
        persisted_decision = ReconciliationDecision(
            decision_ref_id=decision_input.input_ref_id,
            decision_sha256=decision_input.input_sha256,
            classification=classification,
            difference_qty=difference,
        )
        current_binding = _reconciliation_binding_ref(
            selection=selection,
            estimate=estimate,
            decision=persisted_decision,
        )
        stored_binding = binding_inputs[0]
        if current_binding != (
            stored_binding.input_type,
            stored_binding.input_ref_id,
            stored_binding.input_sha256,
        ):
            raise ValueError(
                "FINAL estimate evidence changed after reconciliation; "
                "reconcile again"
            )
        if classification in {"MATCHED", "NO_COMPARABLE_ESTIMATE"}:
            expected_automatic = _resolve_reconciliation_decision(
                None,
                difference_qty=difference,
            )
            if expected_automatic != persisted_decision:
                raise ValueError(
                    "FINAL automatic reconciliation decision is stale"
                )
        refs = [
            (item.input_type, item.input_ref_id, item.input_sha256)
            for item in prior_inputs
        ]
        inputs = _summary_inputs(refs)
        def validate_in_transaction(connection) -> None:
            transaction_evidence = self.select_evidence(
                platform_name=current.platform_name,
                platform_trade_date=current.platform_trade_date,
                scope_type=current.scope_type,
                scope_key=current.scope_key,
                time_policy_version=current.time_policy_version,
                connection=connection,
            )
            recomputed = transaction_evidence.selection
            transaction_estimate = self._select_estimate_only(
                current.platform_name,
                current.platform_trade_date,
                current.scope_type,
                current.scope_key,
                transaction_evidence.estimate_segments,
                time_policy_version=current.time_policy_version,
                connection=connection,
            )
            if (
                recomputed.quality_level is not DataQualityLevel.ORDER_COMPLETE
                or recomputed.selected_order_batch_status != "CLOSED"
                or recomputed.selected_order_batch_id
                != selection.selected_order_batch_id
                or recomputed.sold_qty != selection.sold_qty
                or recomputed.order_count != selection.order_count
                or recomputed.transaction_amount_total
                != selection.transaction_amount_total
                or recomputed.input_refs != selection.input_refs
            ):
                raise ValueError("FINAL order evidence changed during validation")
            transaction_difference = (
                recomputed.sold_qty - transaction_estimate.sold_qty
                if recomputed.sold_qty is not None
                and transaction_estimate.sold_qty is not None
                else None
            )
            transaction_decision = ReconciliationDecision(
                decision_ref_id=decision_input.input_ref_id,
                decision_sha256=decision_input.input_sha256,
                classification=classification,
                difference_qty=transaction_difference,
            )
            if _reconciliation_binding_ref(
                selection=recomputed,
                estimate=transaction_estimate,
                decision=transaction_decision,
            ) != current_binding:
                raise ValueError(
                    "FINAL estimate evidence changed during validation"
                )
            compared = self._latest_cancellation(
                transaction_evidence,
                recomputed,
            )
            if (
                (cancellation is None) != (compared is None)
                or (
                    cancellation is not None
                    and (
                        compared is None
                        or compared.status != "DETERMINED"
                        or compared.comparison_sha256
                        != cancellation.comparison_sha256
                    )
                )
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
        time_policy_version: str = DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        connection=None,
    ) -> SettlementEvidence:
        orders = self.repository.list_order_snapshots(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            connection=connection,
        )
        estimates = self.repository.list_estimate_segments(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            connection=connection,
        )
        coverage_started_at, coverage_ended_at = (
            self.estimate_service.operational_time.platform_trade_day_window(
                platform_trade_date,
                policy_version=time_policy_version,
            )
        )
        dimensions = self._current_sku_dimensions(
            platform_name,
            connection=connection,
        )
        selection = self.fact_selector.select(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
            order_snapshots=orders,
            estimate_segments=estimates,
            sku_dimensions=dimensions,
            estimate_algorithm_version=self.estimate_service.algorithm_version,
            coverage_started_at=coverage_started_at,
            coverage_ended_at=coverage_ended_at,
        )
        return SettlementEvidence(selection, orders, estimates)

    def _select_estimate_only(
        self,
        platform_name: str,
        platform_trade_date: date,
        scope_type: str,
        scope_key: str,
        estimates: tuple[SalesEstimateSegment, ...],
        *,
        time_policy_version: str = DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        connection=None,
    ) -> SalesFactSelection:
        coverage_started_at, coverage_ended_at = (
            self.estimate_service.operational_time.platform_trade_day_window(
                platform_trade_date,
                policy_version=time_policy_version,
            )
        )
        return self.fact_selector.select(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
            order_snapshots=(),
            estimate_segments=estimates,
            sku_dimensions=self._current_sku_dimensions(
                platform_name,
                connection=connection,
            ),
            estimate_algorithm_version=self.estimate_service.algorithm_version,
            coverage_started_at=coverage_started_at,
            coverage_ended_at=coverage_ended_at,
        )

    def _current_sku_dimensions(
        self,
        platform_name: str,
        *,
        connection=None,
    ) -> dict[str, dict[str, str]]:
        current = self.repository.list_sku_dimensions(
            platform_name=platform_name,
            connection=connection,
        )
        return current or dict(self.sku_dimensions)

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

    def cancellation_for_summary(
        self,
        summary,
    ) -> OrderCancellationResult | None:
        evidence = self.select_evidence(
            platform_name=summary.platform_name,
            platform_trade_date=summary.platform_trade_date,
            scope_type=summary.scope_type,
            scope_key=summary.scope_key,
            time_policy_version=summary.time_policy_version,
        )
        return self._latest_cancellation(evidence, evidence.selection)

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


def _reconciliation_classification(quality_reason: str) -> str:
    prefix = "RECONCILED:"
    if not quality_reason.startswith(prefix):
        raise ValueError("FINAL reconciliation classification is missing")
    classification = quality_reason[len(prefix) :].strip()
    if not classification:
        raise ValueError("FINAL reconciliation classification is blank")
    return classification


def _reconciliation_binding_ref(
    *,
    selection: SalesFactSelection,
    estimate: SalesFactSelection,
    decision: ReconciliationDecision,
) -> tuple[str, str, str]:
    payload = {
        "order": {
            "sold_qty": selection.sold_qty,
            "order_count": selection.order_count,
            "transaction_amount_total": (
                format(selection.transaction_amount_total, "f")
                if selection.transaction_amount_total is not None
                else None
            ),
            "mapping_version": selection.mapping_version,
            "input_refs": list(selection.input_refs),
        },
        "estimate": {
            "sold_qty": estimate.sold_qty,
            "quality_level": estimate.quality_level.value,
            "mapping_version": estimate.mapping_version,
            "algorithm_version": estimate.algorithm_version,
            "input_refs": list(estimate.input_refs),
        },
        "difference_qty": decision.difference_qty,
        "decision": {
            "ref_id": decision.decision_ref_id,
            "sha256": decision.decision_sha256,
            "classification": decision.classification,
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return (
        "RECONCILIATION_BINDING",
        f"reconciliation:{decision.decision_ref_id}",
        digest,
    )


def _retained_control_refs(
    inputs: list[TradeDaySummaryInput],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (item.input_type, item.input_ref_id, item.input_sha256)
        for item in inputs
        if item.input_type == "SETTLEMENT_WINDOW"
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
