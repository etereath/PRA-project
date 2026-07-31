from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from app.enums import DataQualityLevel, ProductMappingStatus
from app.operational_models import PlatformTradeDaySummary
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.sales_settlement_models import (
    OrderSnapshot,
    SalesPlanInputManifest,
)
from app.services.operational_time import OperationalTimeService


SALES_PLAN_INPUT_VERSION = "sales-plan-input-v2"
PLAN_ELIGIBLE_QUALITIES = frozenset(
    {
        DataQualityLevel.ORDER_COMPLETE,
        DataQualityLevel.SCAN_ESTIMATED_HIGH,
        DataQualityLevel.SCAN_ESTIMATED_MEDIUM,
    }
)


class SalesPlanInputService:
    """Project one frozen settlement snapshot into planning facts."""

    def __init__(
        self,
        repository: OperationalSummaryRepository,
        *,
        operational_time: OperationalTimeService | None = None,
        clock=None,
    ) -> None:
        self.repository = repository
        self.operational_time = operational_time or OperationalTimeService()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def build(
        self,
        *,
        platform_name: str,
        settled_platform_trade_date: date,
        plan_for_seller_operation_date: date | None = None,
        as_of: datetime | None = None,
        summaries: Iterable[PlatformTradeDaySummary] | None = None,
    ) -> SalesPlanInputManifest:
        plan_date = (
            plan_for_seller_operation_date
            or settled_platform_trade_date + timedelta(days=1)
        )
        observed_as_of = _as_utc(as_of or self.clock(), "as_of")
        current_seller_date = self.operational_time.classify(
            observed_as_of
        ).seller_operation_date
        projection_role = (
            "CURRENT_OPERATIONS"
            if plan_date == current_seller_date
            else "AUDIT_ONLY"
        )
        frozen_summaries = tuple(
            summaries
            if summaries is not None
            else self.repository.list_current_summaries(
                platform_name=platform_name,
                platform_trade_date=settled_platform_trade_date,
            )
        )
        platform_summaries = tuple(
            summary
            for summary in frozen_summaries
            if summary.scope_type == "PLATFORM"
            and summary.scope_key == platform_name
        )
        if len(platform_summaries) != 1:
            raise ValueError(
                "Sales plan input requires exactly one current PLATFORM summary"
            )
        platform_summary = platform_summaries[0]
        settlement_eligible = (
            platform_summary.quality_level in PLAN_ELIGIBLE_QUALITIES
            and platform_summary.sold_qty is not None
        )
        eligibility_reasons = []
        if not settlement_eligible:
            eligibility_reasons.append(
                f"SETTLEMENT_QUALITY_{platform_summary.quality_level.value}"
            )
        if projection_role != "CURRENT_OPERATIONS":
            eligibility_reasons.append("HISTORICAL_AUDIT_ONLY")

        input_refs: list[tuple[str, str, str]] = []
        summary_payload = []
        for summary in sorted(
            frozen_summaries,
            key=lambda item: (item.scope_type, item.scope_key),
        ):
            input_refs.append(
                (
                    "TRADE_DAY_SUMMARY",
                    summary.summary_id,
                    summary.input_manifest_sha256,
                )
            )
            eligible = (
                summary.quality_level in PLAN_ELIGIBLE_QUALITIES
                and summary.sold_qty is not None
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
                    "sold_qty": summary.sold_qty if eligible else None,
                    "order_count": summary.order_count if eligible else None,
                    "transaction_amount_total": (
                        format(summary.transaction_amount_total, "f")
                        if eligible
                        and summary.transaction_amount_total is not None
                        else None
                    ),
                    "plan_eligible": eligible,
                    "ineligibility_reason": (
                        ""
                        if eligible
                        else f"QUALITY_{summary.quality_level.value}"
                    ),
                    "input_manifest_sha256": summary.input_manifest_sha256,
                }
            )

        early_orders = self.repository.list_order_snapshots(
            platform_name=platform_name,
            platform_trade_date=plan_date,
        )
        early_order_payload, early_ref = self._early_order_projection(
            early_orders,
            plan_for_seller_operation_date=plan_date,
        )
        if early_ref is not None:
            input_refs.append(early_ref)

        seller_started_at, seller_ended_at = (
            self.operational_time.seller_operation_day_window(plan_date)
        )
        inventory = tuple(
            observation
            for observation in (
                self.repository.list_inventory_observations_for_seller_operation_date(
                    platform_name=platform_name,
                    seller_operation_date=plan_date,
                )
            )
            if seller_started_at <= observation.observed_at < seller_ended_at
            and observation.batch_status == "ACCEPTED"
            and observation.scope_complete
            and observation.end_marker_verified
            and observation.mapping_status is ProductMappingStatus.VERIFIED
        )
        inventory_by_sku: dict[str, list] = {}
        for observation in inventory:
            input_refs.append(
                (
                    "PRODUCT_OBSERVATION_BATCH",
                    observation.observation_batch_id,
                    observation.content_sha256,
                )
            )
            inventory_by_sku.setdefault(
                observation.internal_sku or "",
                [],
            ).append(observation)
        inventory_payload = [
            _inventory_trajectory_projection(sku, points)
            for sku, points in sorted(inventory_by_sku.items())
            if sku
        ]

        deduplicated_refs = _deduplicate_refs(input_refs)
        executable = (
            settlement_eligible
            and projection_role == "CURRENT_OPERATIONS"
        )
        payload = {
            "schema_version": SALES_PLAN_INPUT_VERSION,
            "platform_name": platform_name,
            "settled_platform_trade_date": (
                settled_platform_trade_date.isoformat()
            ),
            "plan_for_seller_operation_date": plan_date.isoformat(),
            "projection_role": projection_role,
            "plan_input_status": "ELIGIBLE" if executable else "INELIGIBLE",
            "ineligibility_reasons": eligibility_reasons,
            "closed_trade_day_summaries": summary_payload,
            "pre_plan_early_signal": early_order_payload,
            "seller_operation_inventory_trajectory": inventory_payload,
            "prediction_performed": False,
            "executable_advice_generated": False,
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
        return SalesPlanInputManifest(
            platform_name=platform_name,
            settled_platform_trade_date=settled_platform_trade_date,
            plan_for_seller_operation_date=plan_date,
            projection_role=projection_role,
            payload=payload,
            input_refs=deduplicated_refs,
            manifest_sha256=sales_plan_manifest_sha256(payload),
        )

    def _early_order_projection(
        self,
        snapshots: tuple[OrderSnapshot, ...],
        *,
        plan_for_seller_operation_date: date,
    ) -> tuple[dict[str, object], tuple[str, str, str] | None]:
        candidates = tuple(
            snapshot
            for snapshot in snapshots
            if snapshot.trade_day_status == "OPEN"
            and snapshot.capability_result == "SUCCEEDED"
            and snapshot.source_batch_status == "ACCEPTED"
            and snapshot.scope_complete
            and snapshot.end_marker_verified
        )
        latest = max(
            candidates,
            key=lambda item: (
                item.scan_completed_at,
                item.observation_batch_id,
            ),
            default=None,
        )
        if latest is None:
            state = "UNAVAILABLE" if snapshots else "NO_DATA"
            return (
                {
                    "feature_role": "PRE_PLAN_EARLY_SIGNAL",
                    "quality_state": state,
                    "order_count": None,
                    "sold_qty": None,
                    "transaction_amount_total": None,
                    "reason": (
                        "NO_ACCEPTABLE_OPEN_ORDER_SNAPSHOT"
                        if snapshots
                        else "NO_ORDER_SNAPSHOT"
                    ),
                },
                None,
            )
        seller_start, _ = self.operational_time.seller_operation_day_window(
            plan_for_seller_operation_date,
            policy_version=latest.time_policy_version,
        )
        early_start = seller_start - timedelta(hours=2)
        items = tuple(
            item
            for item in latest.items
            if early_start <= item.order_created_at < seller_start
        )
        amount = sum(
            (item.order_transaction_amount for item in items),
            start=Decimal("0"),
        )
        payload = {
            "feature_role": "PRE_PLAN_EARLY_SIGNAL",
            "quality_state": "CONFIRMED",
            "source_platform_trade_date": (
                latest.platform_trade_date.isoformat()
            ),
            "window_started_at": early_start.isoformat(),
            "window_ended_at": seller_start.isoformat(),
            "observation_batch_id": latest.observation_batch_id,
            "order_count": len(items),
            "sold_qty": sum(item.order_qty for item in items),
            "transaction_amount_total": format(amount, "f"),
            "trusted_zero": not items,
            "observed_at": latest.scan_completed_at.isoformat(),
            "time_policy_version": latest.time_policy_version,
        }
        return (
            payload,
            (
                "EARLY_ORDER_OBSERVATION_BATCH",
                latest.observation_batch_id,
                latest.content_sha256,
            ),
        )


def _deduplicate_refs(
    refs: Iterable[tuple[str, str, str]],
) -> tuple[tuple[str, str, str], ...]:
    deduplicated: dict[tuple[str, str], str] = {}
    for input_type, ref_id, digest in refs:
        identity = (input_type, ref_id)
        existing = deduplicated.get(identity)
        if existing is not None and existing != digest:
            raise ValueError("One plan input identity has conflicting hashes")
        deduplicated[identity] = digest
    return tuple(
        (input_type, ref_id, digest)
        for (input_type, ref_id), digest in sorted(deduplicated.items())
    )


def _inventory_trajectory_projection(sku: str, points) -> dict[str, object]:
    ordered = sorted(
        points,
        key=lambda item: (item.observed_at, item.observation_item_id),
    )
    prices = [
        item.observed_price
        for item in ordered
        if item.observed_price is not None
    ]
    inventories = [
        item.observed_inventory
        for item in ordered
        if item.observed_inventory is not None
    ]
    price_change_count = sum(
        1
        for previous, current in zip(prices, prices[1:])
        if current != previous
    )
    return {
        "internal_sku": sku,
        "observation_count": len(ordered),
        "first_observed_at": ordered[0].observed_at.isoformat(),
        "last_observed_at": ordered[-1].observed_at.isoformat(),
        "opening_price": (
            format(ordered[0].observed_price, "f")
            if ordered[0].observed_price is not None
            else None
        ),
        "closing_price": (
            format(ordered[-1].observed_price, "f")
            if ordered[-1].observed_price is not None
            else None
        ),
        "minimum_price": format(min(prices), "f") if prices else None,
        "maximum_price": format(max(prices), "f") if prices else None,
        "price_change_count": price_change_count,
        "opening_inventory": ordered[0].observed_inventory,
        "closing_inventory": ordered[-1].observed_inventory,
        "minimum_inventory": min(inventories) if inventories else None,
        "maximum_inventory": max(inventories) if inventories else None,
        "online_observation_count": sum(
            1 for item in ordered if item.observed_online
        ),
        "mapping_version": ordered[-1].mapping_version,
        "quality": "ACCEPTED_COMPLETE",
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sales_plan_manifest_sha256(payload: dict[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
