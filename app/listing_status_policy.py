"""Shared rules for current listing visibility and price-task eligibility."""

from __future__ import annotations

from app.models import ListingStatus


def has_current_platform_stock(status: ListingStatus) -> bool:
    return status.platform_stock_qty > 0


def is_price_task_listing(status: ListingStatus) -> bool:
    return status.online_status == "online" and has_current_platform_stock(status)
