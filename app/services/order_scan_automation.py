from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.adapters.mayi_huatuan_order import (
    MayiHuatuanOrderReadOnlyAdapter,
)
from app.automation_models import (
    AutomationRun,
    AutomationRunOutcome,
)
from app.enums import AutomationRunStatus
from app.services.automation import AutomationExecutionContext
from app.services.order_observation import (
    OrderObservationImporter,
)
from app.services.product_mapping import CompiledProductMappings


ORDER_SCAN_CHILD_JOB_ID = "AUTOMATION-ORDER-SCAN-CHILD"
ORDER_SCAN_CHILD_RELATION = "ORDER_SCAN_CHILD"
ORDER_HISTORY_IMPORT_RELATION = "ORDER_HISTORY_IMPORT"


class ParentAutomationHandler(Protocol):
    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome: ...


@dataclass(frozen=True, slots=True)
class OrderScanHandler:
    adapter: MayiHuatuanOrderReadOnlyAdapter
    importer: OrderObservationImporter
    mappings_provider: Callable[[], CompiledProductMappings]
    batch_id_factory: Callable[[AutomationRun], str]
    target_trade_date: Callable[[AutomationRun], date] = (
        lambda run: run.platform_trade_date
    )

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != "ORDER_SCAN":
            raise ValueError("OrderScanHandler only accepts ORDER_SCAN")
        reader = self.adapter.reader
        set_wait_callback = getattr(reader, "set_wait_callback", None)
        if callable(set_wait_callback):
            set_wait_callback(context.heartbeat)
        try:
            batch = self.adapter.scan(
                observation_batch_id=self.batch_id_factory(run),
                automation_run_id=run.run_id,
                platform_name=run.platform_name,
                requested_platform_trade_date=self.target_trade_date(run),
            )
            imported = self.importer.import_batch(
                batch,
                mappings=self.mappings_provider(),
                claim=context.claim,
            )
            acknowledge = getattr(
                reader,
                "acknowledge_last_result",
                None,
            )
            if callable(acknowledge):
                acknowledge()
        finally:
            if callable(set_wait_callback):
                set_wait_callback(None)
        status = {
            "ACCEPTED": AutomationRunStatus.SUCCESS,
            "PARTIAL": AutomationRunStatus.PARTIAL,
            "UNAVAILABLE": AutomationRunStatus.SKIPPED,
            "FAILED": AutomationRunStatus.FAILED,
        }[imported.batch_status]
        return AutomationRunOutcome(
            status=status,
            output_manifest_sha256=imported.content_sha256,
            error_code=(
                ""
                if status is AutomationRunStatus.SUCCESS
                else f"ORDER_SCAN_{imported.batch_status}"
            ),
            error_message="",
            event_payload={
                "relation_type": ORDER_HISTORY_IMPORT_RELATION,
                "observation_batch_id": imported.observation_batch_id,
                "platform_trade_date": (
                    imported.requested_platform_trade_date.isoformat()
                ),
                "trade_day_status": imported.trade_day_status,
                "capability_result": imported.capability_result,
                "batch_status": imported.batch_status,
                "item_count": imported.item_count,
            },
        )


@dataclass(frozen=True, slots=True)
class FullMarketScanOrderCoordinator:
    """Attach the existing child-only ORDER_SCAN to a real parent handler."""

    parent_handler: ParentAutomationHandler
    child_job_id: str = ORDER_SCAN_CHILD_JOB_ID

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type not in {
            "FULL_MARKET_SCAN",
            "PRE_CUTOFF_FULL_SCAN",
        }:
            raise ValueError(
                "order child coordination requires a full-scan parent"
            )
        outcome = self.parent_handler(run, context)
        if outcome.status not in {
            AutomationRunStatus.SUCCESS,
            AutomationRunStatus.PARTIAL,
        }:
            return outcome
        child, _ = context.ensure_child_run(
            child_job_id=self.child_job_id,
            relation_type=ORDER_SCAN_CHILD_RELATION,
        )
        payload = dict(outcome.event_payload)
        payload.update(
            {
                "order_scan_child_run_id": child.run_id,
                "order_scan_relation": ORDER_SCAN_CHILD_RELATION,
            }
        )
        return AutomationRunOutcome(
            status=outcome.status,
            output_manifest_sha256=outcome.output_manifest_sha256,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
            event_payload=payload,
        )
