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
    validate_live_automation_claim_in_transaction,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    AutomationExecutionContext,
    AutomationHandler,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    SALES_PLAN_INPUT_BUILD,
)
from app.services.sales_plan_input import SalesPlanInputService
from app.services.trade_day_settlement import TradeDaySettlementService


@dataclass(frozen=True, slots=True)
class TradeDaySettlementAutomationHandler:
    service: TradeDaySettlementService

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

        results = self.service.create_provisionals_for_run(
            run,
            transaction_validator=validate_claim,
        )
        manifest_sha256 = _settlement_run_manifest_sha256(results)
        context.bind_input_manifest(manifest_sha256)
        platform_result = next(
            result
            for result in results
            if result.summary.scope_type == "PLATFORM"
        )
        return AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS,
            output_manifest_sha256=manifest_sha256,
            event_payload={
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
    service: SalesPlanInputService

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
        manifest = self.service.build(
            platform_name=run.platform_name,
            settled_platform_trade_date=settled_date,
        )
        context.bind_input_manifest(manifest.manifest_sha256)
        return AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS,
            output_manifest_sha256=manifest.manifest_sha256,
            event_payload={
                "schema_version": manifest.payload["schema_version"],
                "settled_platform_trade_date": settled_date.isoformat(),
                "source_ref_count": len(manifest.input_refs),
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
    dimensions = repository.list_sku_dimensions(
        platform_name=platform_name,
    )
    return {
        PLATFORM_TRADE_DAY_SETTLEMENT: (
            TradeDaySettlementAutomationHandler(
                TradeDaySettlementService(
                    repository,
                    sku_dimensions=dimensions,
                )
            )
        ),
        SALES_PLAN_INPUT_BUILD: SalesPlanInputAutomationHandler(
            SalesPlanInputService(repository)
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
