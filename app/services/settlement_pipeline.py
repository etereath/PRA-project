from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from app.automation_models import AutomationRun
from app.enums import DataQualityLevel
from app.inventory_models import InventorySalesBatchResult
from app.operational_models import SummaryMutationResult
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.sales_settlement_models import SettlementSnapshot
from app.services.sales_plan_input import (
    PLAN_ELIGIBLE_QUALITIES,
    SalesPlanInputService,
)
from app.services.authoritative_inventory import InventorySalesApplicationService
from app.services.trade_day_settlement import TradeDaySettlementService


SETTLEMENT_SNAPSHOT_VERSION = "settlement-snapshot-v1"
MAX_SETTLEMENT_EVENT_PAYLOAD_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class SettlementPipelineResult:
    mutations: tuple[SummaryMutationResult, ...]
    snapshot: SettlementSnapshot
    inventory_result: InventorySalesBatchResult | None = None


class SettlementPipeline:
    """Orchestrate existing settlement state and derive read-only projections."""

    def __init__(
        self,
        repository: OperationalSummaryRepository,
        *,
        settlement_service: TradeDaySettlementService | None = None,
        plan_input_service: SalesPlanInputService | None = None,
        inventory_sales_service: InventorySalesApplicationService | None = None,
    ) -> None:
        self.repository = repository
        self.settlement_service = settlement_service or TradeDaySettlementService(
            repository
        )
        self.plan_input_service = plan_input_service or SalesPlanInputService(
            repository
        )
        self.inventory_sales_service = inventory_sales_service

    def run(
        self,
        run: AutomationRun,
        *,
        changed_by: str = "automation-settlement",
        transaction_validator=None,
    ) -> SettlementPipelineResult:
        mutations = self.settlement_service.create_provisionals_for_run(
            run,
            changed_by=changed_by,
            transaction_validator=transaction_validator,
        )
        if not mutations:
            raise ValueError("Settlement pipeline produced no summary scopes")
        platform_trade_date = mutations[0].summary.platform_trade_date
        readback = self.repository.list_current_summaries(
            platform_name=run.platform_name,
            platform_trade_date=platform_trade_date,
        )
        expected = {
            (item.summary.scope_type, item.summary.scope_key): (
                item.summary.summary_id,
                item.summary.input_manifest_sha256,
            )
            for item in mutations
        }
        persisted = {
            (item.scope_type, item.scope_key): (
                item.summary_id,
                item.input_manifest_sha256,
            )
            for item in readback
        }
        if any(
            persisted.get(scope) != identity
            for scope, identity in expected.items()
        ):
            raise RuntimeError("Settlement readback did not match persisted summaries")

        inventory_result = None
        if self.inventory_sales_service is not None:
            inventory_result = self.inventory_sales_service.apply_current_sku_summaries(
                platform_name=run.platform_name,
                platform_trade_date=platform_trade_date,
                actor=changed_by,
            )

        plan_input = self.plan_input_service.build(
            platform_name=run.platform_name,
            settled_platform_trade_date=platform_trade_date,
            plan_for_seller_operation_date=run.seller_operation_date,
            as_of=run.scheduled_for,
            summaries=readback,
        )
        platform_summary = next(
            item
            for item in readback
            if item.scope_type == "PLATFORM"
            and item.scope_key == run.platform_name
        )
        cancellation = self.settlement_service.cancellation_for_summary(
            platform_summary
        )
        management_report = build_management_report(
            summaries=readback,
            cancellation=cancellation,
            seller_operation_date=run.seller_operation_date,
        )
        summary_projection = tuple(
            {
                "summary_id": item.summary_id,
                "scope_type": item.scope_type,
                "scope_key": item.scope_key,
                "summary_status": item.summary_status.value,
                "quality_level": item.quality_level.value,
                "fact_source": (
                    item.fact_source.value if item.fact_source is not None else None
                ),
                "sold_qty": item.sold_qty,
                "order_count": item.order_count,
                "transaction_amount_total": (
                    format(item.transaction_amount_total, "f")
                    if item.transaction_amount_total is not None
                    else None
                ),
                "input_manifest_sha256": item.input_manifest_sha256,
            }
            for item in sorted(
                readback,
                key=lambda value: (value.scope_type, value.scope_key),
            )
        )
        audit_receipt = {
            "readback_passed": True,
            "summary_count": len(summary_projection),
            "summary_refs": [
                {
                    "summary_id": item["summary_id"],
                    "input_manifest_sha256": item["input_manifest_sha256"],
                }
                for item in summary_projection
            ],
            "plan_input_manifest_sha256": plan_input.manifest_sha256,
            "source_ref_count": len(plan_input.input_refs),
        }
        snapshot_core = {
            "schema_version": SETTLEMENT_SNAPSHOT_VERSION,
            "platform_name": run.platform_name,
            "platform_trade_date": platform_trade_date.isoformat(),
            "seller_operation_date": run.seller_operation_date.isoformat(),
            "summaries": summary_projection,
            "sales_plan_input": plan_input.payload,
            "management_report": management_report,
            "audit_receipt": audit_receipt,
        }
        snapshot_sha256 = "sha256:" + hashlib.sha256(
            _canonical_json_bytes(snapshot_core)
        ).hexdigest()
        audit_receipt = {**audit_receipt, "snapshot_sha256": snapshot_sha256}
        snapshot = SettlementSnapshot(
            platform_name=run.platform_name,
            platform_trade_date=platform_trade_date,
            seller_operation_date=run.seller_operation_date,
            summaries=summary_projection,
            sales_plan_input=plan_input,
            management_report=management_report,
            audit_receipt=audit_receipt,
            snapshot_sha256=snapshot_sha256,
        )
        validate_settlement_event_payload_size(snapshot_event_payload(snapshot))
        return SettlementPipelineResult(
            mutations=mutations,
            snapshot=snapshot,
            inventory_result=inventory_result,
        )


def snapshot_event_payload(snapshot: SettlementSnapshot) -> dict[str, object]:
    return {
        "schema_version": SETTLEMENT_SNAPSHOT_VERSION,
        "platform_trade_date": snapshot.platform_trade_date.isoformat(),
        "seller_operation_date": snapshot.seller_operation_date.isoformat(),
        "summaries": list(snapshot.summaries),
        "sales_plan_input": snapshot.sales_plan_input.payload,
        "management_report": snapshot.management_report,
        "audit_receipt": snapshot.audit_receipt,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "prediction_performed": False,
        "platform_write_performed": False,
    }


def validate_settlement_event_payload_size(payload: dict[str, object]) -> None:
    size = len(_canonical_json_bytes(payload))
    if size > MAX_SETTLEMENT_EVENT_PAYLOAD_BYTES:
        raise ValueError(
            "Settlement snapshot exceeds the bounded Automation Event payload"
        )


def build_management_report(
    *,
    summaries,
    cancellation,
    seller_operation_date,
) -> dict[str, object]:
    platform = next(item for item in summaries if item.scope_type == "PLATFORM")
    metric_state = _metric_state(platform)
    sold_qty = (
        platform.sold_qty
        if metric_state in {"VALUE", "CONFIRMED_ZERO"}
        else None
    )
    order_count = (
        platform.order_count
        if metric_state in {"VALUE", "CONFIRMED_ZERO"}
        else None
    )
    amount = (
        platform.transaction_amount_total
        if metric_state in {"VALUE", "CONFIRMED_ZERO"}
        else None
    )
    average_amount = None
    average_state = metric_state
    if metric_state == "VALUE":
        if amount is not None and sold_qty:
            average_amount = amount / Decimal(sold_qty)
        else:
            average_state = "EVIDENCE_INSUFFICIENT"
    elif metric_state == "CONFIRMED_ZERO":
        average_state = "CONFIRMED_ZERO"

    top_varieties = _top_scope_projection(
        summaries,
        scope_type="VARIETY",
        platform_state=metric_state,
    )
    top_grades = _top_scope_projection(
        summaries,
        scope_type="GRADE",
        platform_state=metric_state,
    )
    peak_periods = _top_scope_projection(
        summaries,
        scope_type="TIME_BUCKET",
        platform_state=metric_state,
    )
    if cancellation is None:
        cancellation_metric = {
            "state": (
                "NO_DATA_OR_UNREADABLE"
                if metric_state == "NO_DATA_OR_UNREADABLE"
                else "EVIDENCE_INSUFFICIENT"
            ),
            "value": None,
            "reason": "缺少两份可比较的完整订单快照",
        }
    elif cancellation.status == "DETERMINED":
        cancelled_qty = cancellation.cancelled_qty or 0
        cancellation_metric = {
            "state": "CONFIRMED_ZERO" if cancelled_qty == 0 else "VALUE",
            "value": cancelled_qty,
            "reason": "",
        }
    else:
        cancellation_metric = {
            "state": "EVIDENCE_INSUFFICIENT",
            "value": None,
            "reason": "取消量无法可靠确定",
        }
    quality_text = {
        "VALUE": "数据完整，可用于销售判断",
        "CONFIRMED_ZERO": "数据完整，当天暂无成交",
        "EVIDENCE_INSUFFICIENT": "证据不足，暂不能形成可信结论",
        "NO_DATA_OR_UNREADABLE": "无数据或本次不可读取",
    }[metric_state]
    return {
        "seller_operation_date": seller_operation_date.isoformat(),
        "sales": {
            "state": metric_state,
            "sold_qty": sold_qty,
            "order_count": order_count,
            "transaction_amount_total": (
                format(amount, "f") if amount is not None else None
            ),
            "message": "当天暂无成交" if metric_state == "CONFIRMED_ZERO" else "",
        },
        "average_transaction_amount_per_unit": {
            "state": average_state,
            "value": (
                format(average_amount, "f")
                if average_amount is not None
                else None
            ),
        },
        "top_varieties": top_varieties,
        "top_grades": top_grades,
        "peak_periods": peak_periods,
        "cancelled_qty": cancellation_metric,
        "data_quality": quality_text,
    }


def _metric_state(summary) -> str:
    if summary.quality_level is DataQualityLevel.UNAVAILABLE:
        return "NO_DATA_OR_UNREADABLE"
    if summary.quality_level not in PLAN_ELIGIBLE_QUALITIES:
        return "EVIDENCE_INSUFFICIENT"
    if summary.sold_qty == 0:
        return "CONFIRMED_ZERO"
    return "VALUE"


def _top_scope_projection(
    summaries,
    *,
    scope_type: str,
    platform_state: str,
) -> dict[str, object]:
    if platform_state == "CONFIRMED_ZERO":
        return {"state": "CONFIRMED_ZERO", "items": []}
    if platform_state != "VALUE":
        return {"state": platform_state, "items": []}
    candidates = [
        item
        for item in summaries
        if item.scope_type == scope_type
        and item.quality_level in PLAN_ELIGIBLE_QUALITIES
        and item.sold_qty is not None
        and item.sold_qty > 0
    ]
    if not candidates:
        return {
            "state": "EVIDENCE_INSUFFICIENT",
            "items": [],
            "reason": "对应范围证据不足",
        }
    ranked = sorted(
        candidates,
        key=lambda item: (-(item.sold_qty or 0), item.scope_key),
    )[:3]
    return {
        "state": "VALUE",
        "items": [
            {
                "name": item.scope_key,
                "sold_qty": item.sold_qty,
                "transaction_amount_total": (
                    format(item.transaction_amount_total, "f")
                    if item.transaction_amount_total is not None
                    else None
                ),
            }
            for item in ranked
        ],
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
