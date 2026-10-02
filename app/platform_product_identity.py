"""Canonical, versioned platform-product identity primitives."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


PLATFORM_PRODUCT_IDENTITY_SCHEMA_VERSION = "platform-product-identity-v1"
STABLE_IDENTITY_TYPE = "stable_platform_product_id"
DISPLAY_IDENTITY_TYPE = "normalized_name_grade"


class PlatformProductIdentityError(ValueError):
    """Raised when a public platform-product identity is not canonicalizable."""


def canonical_identity_json(value: Mapping[str, object] | str) -> str:
    """Return deterministic JSON after recursively normalizing an identity."""

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PlatformProductIdentityError(
                "platform_product_identity_json must be valid JSON."
            ) from exc
    elif isinstance(value, Mapping):
        parsed = dict(value)
    else:
        raise PlatformProductIdentityError(
            "platform_product_identity must be an object."
        )
    if not isinstance(parsed, dict):
        raise PlatformProductIdentityError(
            "platform_product_identity must be an object."
        )
    normalized = _normalize_object(parsed, "platform_product_identity")
    for field_name in ("schema_version", "identity_type"):
        field_value = normalized.get(field_name)
        if not isinstance(field_value, str) or not field_value:
            raise PlatformProductIdentityError(
                f"platform_product_identity requires non-empty {field_name}."
            )
    components = normalized.get("components")
    if not isinstance(components, dict) or not components:
        raise PlatformProductIdentityError(
            "platform_product_identity requires non-empty components."
        )
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def identity_digest(value: Mapping[str, object] | str) -> str:
    canonical = canonical_identity_json(value)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def legacy_workbook_identity(
    *,
    platform_product_id: object,
    normalized_platform_product_name: str,
    normalized_grade: str,
) -> dict[str, object]:
    """Explicitly adapt one legacy workbook row to the public v1 identity."""

    stable_id = str(platform_product_id or "").strip()
    if stable_id:
        identity_type = STABLE_IDENTITY_TYPE
        components = {"platform_product_id": stable_id}
    else:
        name = str(normalized_platform_product_name or "").strip()
        grade = str(normalized_grade or "").strip()
        if not name or not grade:
            raise PlatformProductIdentityError(
                "legacy mapping requires platform_product_id or normalized name and grade."
            )
        identity_type = DISPLAY_IDENTITY_TYPE
        components = {
            "platform_product_name": name,
            "grade": grade,
        }
    return {
        "schema_version": PLATFORM_PRODUCT_IDENTITY_SCHEMA_VERSION,
        "identity_type": identity_type,
        "components": components,
    }


def _normalize_object(value: Mapping[object, object], path: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            raise PlatformProductIdentityError(f"{path} contains an empty key.")
        if key in result:
            raise PlatformProductIdentityError(f"{path} contains duplicate key {key!r}.")
        result[key] = _normalize_value(raw_value, f"{path}.{key}")
    return result


def _normalize_value(value: object, path: str) -> Any:
    if isinstance(value, Mapping):
        return _normalize_object(value, path)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_value(item, f"{path}[]") for item in value]
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise PlatformProductIdentityError(f"{path} must not be blank.")
        return normalized
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PlatformProductIdentityError(f"{path} must be finite.")
        return value
    raise PlatformProductIdentityError(
        f"{path} contains unsupported value type {type(value).__name__}."
    )
