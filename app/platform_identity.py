"""Application-owned platform aliases used at business/RPA boundaries.

Rules may keep a short business-facing platform name while runtime listing
snapshots and ShadowBot use the canonical platform name.  Keep those aliases
centralized here so public rule services do not duplicate platform-specific
string comparisons.
"""

from __future__ import annotations

from app.listing_identity import normalize_listing_text


PLATFORM_ALIASES: dict[str, tuple[str, ...]] = {
    "蚂蚁花团供应商": (
        "蚂蚁",
        "MAYI_HUATUAN_SUPPLIER",
        "ant_flower_wechat",
    ),
}


def canonical_platform_name(value: object) -> str:
    raw_value = str(value or "").strip()
    normalized_value = normalize_listing_text(raw_value)
    for canonical_name, aliases in PLATFORM_ALIASES.items():
        accepted_names = (canonical_name, *aliases)
        if any(normalize_listing_text(name) == normalized_value for name in accepted_names):
            return canonical_name
    return raw_value


def platform_identity_key(value: object) -> str:
    return normalize_listing_text(canonical_platform_name(value))


def platform_names_match(left: object, right: object) -> bool:
    return platform_identity_key(left) == platform_identity_key(right)
