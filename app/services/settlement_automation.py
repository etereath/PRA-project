from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from typing import Mapping

from app.automation_models import AutomationRun, AutomationRunOutcome
from app.enums import AutomationRunStatus
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.automation_repository import (
    AutomationRepository,
    validate_live_automation_claim_in_transaction,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    AutomationExecutionContext,
    AutomationHandler,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    SALES_PLAN_INPUT_BUILD,
)
from app.services.authoritative_inventory import InventorySalesApplicationService
from app.services.inventory_alert import InventoryAlertService
from app.services.sales_plan_input import (
    SalesPlanInputService,
    sales_plan_manifest_sha256,
)
from app.services.settlement_pipeline import (
    SettlementPipeline,
    snapshot_event_payload,
)
from app.services.trade_day_settlement import TradeDaySettlementService


@dataclass(frozen=True, slots=True)
class TradeDaySettlementAutomationHandler:
    pipeline: SettlementPipeline

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != PLATFORM_TRADE_DAY_SETTLEMENT:
            raise ValueError(
                "TradeDaySettlementAutomationHandler only accepts settlement"
            )
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost before settlement")

        def validate_claim(connection) -> None:
            validate_live_automation_claim_in_transaction(
                connection,
                context.claim,
                now=context.clock(),
            )

        pipeline_result = self.pipeline.run(
            run,
            transaction_validator=validate_claim,
        )
        results = pipeline_result.mutations
        manifest_sha256 = _settlement_run_manifest_sha256(results)
        context.bind_input_manifest(manifest_sha256)
        platform_result = next(
            result
            for result in results
            if result.summary.scope_type == "PLATFORM"
        )
        return AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS,
            output_manifest_sha256=(
                pipeline_result.snapshot.snapshot_sha256
            ),
            event_payload={
                **snapshot_event_payload(pipeline_result.snapshot),
                "summary_id": platform_result.summary.summary_id,
                "summary_ids": [
                    result.summary.summary_id for result in results
                ],
                "summary_count": len(results),
                "summary_status": (
                    platform_result.summary.summary_status.value
                ),
                "quality_level": (
                    platform_result.summary.quality_level.value
                ),
                "fact_source": (
                    platform_result.summary.fact_source.value
                    if platform_result.summary.fact_source is not None
                    else None
                ),
                "platform_trade_date": (
                    platform_result.summary.platform_trade_date.isoformat()
                ),
                "changed": any(result.changed for result in results),
                "platform_write_performed": False,
            },
        )


@dataclass(frozen=True, slots=True)
class SalesPlanInputAutomationHandler:
    automation_repository: AutomationRepository

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != SALES_PLAN_INPUT_BUILD:
            raise ValueError(
                "SalesPlanInputAutomationHandler only accepts plan input"
            )
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost before plan input")
        settled_date = run.platform_trade_date - timedelta(days=1)
        settlement_runs = self.automation_repository.list_runs(
            job_id="AUTOMATION-TRADE-DAY-SETTLEMENT",
            statuses=(AutomationRunStatus.SUCCESS,),
        )
        source_run = next(
            (
                candidate
                for candidate in settlement_runs
                if candidate.platform_name == run.platform_name
                and candidate.seller_operation_date
                == run.seller_operation_date
                and candidate.platform_trade_date - timedelta(days=1)
                == settled_date
            ),
            None,
        )
        if source_run is None:
            raise ValueError(
                "Sales plan input requires a successful settlement pipeline"
            )
        finished = next(
            (
                event
                for event in reversed(
                    self.automation_repository.list_events(source_run.run_id)
                )
                if event.event_type == "RUN_FINISHED"
            ),
            None,
        )
        if finished is None or not isinstance(
            finished.payload.get("sales_plan_input"),
            dict,
        ):
            raise ValueError("Settlement pipeline has no persisted plan projection")
        plan_payload = dict(finished.payload["sales_plan_input"])
        manifest_sha256 = sales_plan_manifest_sha256(plan_payload)
        audit_receipt = finished.payload.get("audit_receipt")
        if (
            not isinstance(audit_receipt, dict)
            or audit_receipt.get("plan_input_manifest_sha256")
            != manifest_sha256
        ):
            raise ValueError("Persisted plan projection failed hash readback")
        context.bind_input_manifest(manifest_sha256)
        eligible = plan_payload.get("plan_input_status") == "ELIGIBLE"
        return AutomationRunOutcome(
            status=(
                AutomationRunStatus.SUCCESS
                if eligible
                else AutomationRunStatus.SKIPPED
            ),
            output_manifest_sha256=manifest_sha256,
            error_code="" if eligible else "PLAN_INPUT_INELIGIBLE",
            event_payload={
                "schema_version": plan_payload["schema_version"],
                "settled_platform_trade_date": settled_date.isoformat(),
                "plan_for_seller_operation_date": (
                    run.seller_operation_date.isoformat()
                ),
                "source_settlement_run_id": source_run.run_id,
                "source_ref_count": int(
                    audit_receipt.get("source_ref_count") or 0
                ),
                "projection_role": plan_payload.get("projection_role"),
                "plan_input_status": plan_payload.get("plan_input_status"),
                "recovered_from_settlement_snapshot": True,
                "prediction_performed": False,
                "platform_write_performed": False,
            },
        )


def build_sales_settlement_handlers(
    *,
    runtime_repository: SQLiteRuntimeRepository,
    platform_name: str,
) -> Mapping[str, AutomationHandler]:
    repository = OperationalSummaryRepository(runtime_repository)
    settlement_service = TradeDaySettlementService(repository)
    plan_service = SalesPlanInputService(repository)
    pipeline = SettlementPipeline(
        repository,
        settlement_service=settlement_service,
        plan_input_service=plan_service,
        inventory_sales_service=InventorySalesApplicationService(
            runtime_repository,
            alert_evaluator=InventoryAlertService(
                runtime_repository
            ).evaluate_transaction,
        ),
    )
    automation_repository = AutomationRepository(runtime_repository)
    return {
        PLATFORM_TRADE_DAY_SETTLEMENT: (
            TradeDaySettlementAutomationHandler(
                pipeline
            )
        ),
        SALES_PLAN_INPUT_BUILD: SalesPlanInputAutomationHandler(
            automation_repository,
        ),
    }


def _settlement_run_manifest_sha256(results) -> str:
    payload = [
        {
            "summary_id": result.summary.summary_id,
            "scope_type": result.summary.scope_type,
            "scope_key": result.summary.scope_key,
            "input_manifest_sha256": (
                result.summary.input_manifest_sha256
            ),
        }
        for result in sorted(
            results,
            key=lambda item: (
                item.summary.scope_type,
                item.summary.scope_key,
            ),
        )
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
