"""Canonical identity for a product listing on a platform.

Platforms in the current integration do not expose the PRA internal SKU.  A
listing is therefore identified only by platform, product name/variety, and
grade.  Internal SKU remains an optional PRA-side reference and must never be
used as the platform matching key.
"""

from __future__ import annotations

from typing import Any

from app.shadowbot_contract_primitives import normalize_contract_grade, normalize_contract_text


def normalize_listing_text(value: Any) -> str:
    return normalize_contract_text(value)


def normalize_listing_grade(value: Any) -> str:
    return normalize_contract_grade(value)


def listing_identity_key(platform_name: Any, variety: Any, grade: Any) -> tuple[str, str, str]:
    return (
        normalize_listing_text(platform_name),
        normalize_listing_text(variety),
        normalize_listing_grade(grade),
    )


def require_listing_identity(platform_name: Any, variety: Any, grade: Any) -> tuple[str, str, str]:
    key = listing_identity_key(platform_name, variety, grade)
    if not all(key):
        raise ValueError("platform_name, variety, and grade are required for listing identity")
    return key
