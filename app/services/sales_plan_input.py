from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.sales_settlement_models import SalesPlanInputManifest


SALES_PLAN_INPUT_VERSION = "sales-plan-input-v1"


class SalesPlanInputService:
    """Build a deterministic read-only planning input, not a forecast."""

    def __init__(self, repository: OperationalSummaryRepository) -> None:
        self.repository = repository

    def build(
        self,
        *,
        platform_name: str,
        settled_platform_trade_date: date,
    ) -> SalesPlanInputManifest:
        next_trade_date = settled_platform_trade_date + timedelta(days=1)
        summaries = self.repository.list_current_summaries(
            platform_name=platform_name,
            platform_trade_date=settled_platform_trade_date,
        )
        next_orders = self.repository.list_order_snapshots(
            platform_name=platform_name,
            platform_trade_date=next_trade_date,
        )
        latest_next_order = max(
            next_orders,
            key=lambda item: (
                item.scan_completed_at,
                item.observation_batch_id,
            ),
            default=None,
        )
        inventory = self.repository.list_inventory_observations(
            platform_name=platform_name,
            platform_trade_date=next_trade_date,
        )
        input_refs: list[tuple[str, str, str]] = []
        summary_payload = []
        for summary in summaries:
            input_refs.append(
                (
                    "TRADE_DAY_SUMMARY",
                    summary.summary_id,
                    summary.input_manifest_sha256,
                )
            )
            summary_payload.append(
                {
                    "summary_id": summary.summary_id,
                    "scope_type": summary.scope_type,
                    "scope_key": summary.scope_key,
                    "fact_source": (
                        summary.fact_source.value
                        if summary.fact_source is not None
                        else None
                    ),
                    "quality_level": summary.quality_level.value,
                    "summary_status": summary.summary_status.value,
                    "sold_qty": summary.sold_qty,
                    "order_count": summary.order_count,
                    "transaction_amount_total": (
                        format(summary.transaction_amount_total, "f")
                        if summary.transaction_amount_total is not None
                        else None
                    ),
                    "input_manifest_sha256": (
                        summary.input_manifest_sha256
                    ),
                }
            )

        early_order_payload = None
        if latest_next_order is not None:
            input_refs.append(
                (
                    "EARLY_ORDER_OBSERVATION_BATCH",
                    latest_next_order.observation_batch_id,
                    latest_next_order.content_sha256,
                )
            )
            early_order_payload = {
                "observation_batch_id": (
                    latest_next_order.observation_batch_id
                ),
                "trade_day_status": latest_next_order.trade_day_status,
                "batch_status": latest_next_order.batch_status,
                "scope_complete": latest_next_order.scope_complete,
                "order_count": len(latest_next_order.items),
                "sold_qty": sum(
                    item.order_qty for item in latest_next_order.items
                ),
                "transaction_amount_total": format(
                    sum(
                        (
                            item.order_transaction_amount
                            for item in latest_next_order.items
                        ),
                        start=Decimal("0"),
                    ),
                    "f",
                ),
                "observed_at": latest_next_order.scan_completed_at.isoformat(),
            }

        inventory_payload = []
        for observation in inventory:
            input_refs.append(
                (
                    "PRODUCT_OBSERVATION_BATCH",
                    observation.observation_batch_id,
                    observation.content_sha256,
                )
            )
            inventory_payload.append(
                {
                    "observation_item_id": observation.observation_item_id,
                    "internal_sku": observation.internal_sku,
                    "observed_at": observation.observed_at.isoformat(),
                    "observed_inventory": observation.observed_inventory,
                    "observed_online": observation.observed_online,
                    "mapping_status": observation.mapping_status.value,
                    "mapping_version": observation.mapping_version,
                }
            )

        deduplicated: dict[tuple[str, str], str] = {}
        for input_type, ref_id, digest in input_refs:
            identity = (input_type, ref_id)
            existing = deduplicated.get(identity)
            if existing is not None and existing != digest:
                raise ValueError(
                    "One plan input identity has conflicting hashes"
                )
            deduplicated[identity] = digest
        deduplicated_refs = tuple(
            (input_type, ref_id, digest)
            for (input_type, ref_id), digest in sorted(
                deduplicated.items()
            )
        )
        payload = {
            "schema_version": SALES_PLAN_INPUT_VERSION,
            "platform_name": platform_name,
            "settled_platform_trade_date": (
                settled_platform_trade_date.isoformat()
            ),
            "next_platform_trade_date": next_trade_date.isoformat(),
            "closed_trade_day_summaries": summary_payload,
            "next_trade_day_early_orders": early_order_payload,
            "next_trade_day_inventory_trajectory": inventory_payload,
            "prediction_performed": False,
            "platform_write_performed": False,
            "input_refs": [
                {
                    "input_type": input_type,
                    "input_ref_id": ref_id,
                    "input_sha256": digest,
                }
                for input_type, ref_id, digest in deduplicated_refs
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return SalesPlanInputManifest(
            platform_name=platform_name,
            settled_platform_trade_date=settled_platform_trade_date,
            payload=payload,
            input_refs=deduplicated_refs,
            manifest_sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
        )
