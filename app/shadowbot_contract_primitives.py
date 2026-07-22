"""Dependency-free canonical primitives shared by PRA and the ShadowBot host.

The deployment sync copies this exact file beside the ShadowBot Worker.  Keep
the module compatible with the ShadowBot interpreter and free of ``app``
imports so both trust boundaries execute identical normalization and hashing.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_contract_text(value):
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE_RE.sub(" ", normalized).strip().casefold()


def normalize_contract_grade(value):
    normalized = normalize_contract_text(value).upper()
    return normalized[:-1].rstrip() if normalized.endswith("级") else normalized


def normalize_contract_sku(value):
    normalized = normalize_contract_text(value)
    return normalized.upper() if normalized else None


def contract_identity_key(platform, sku, product_name, grade):
    normalized_platform = normalize_contract_text(platform)
    normalized_sku = normalize_contract_sku(sku)
    if normalized_sku:
        return "%s|sku:%s" % (normalized_platform, normalized_sku)
    return "%s|name:%s|grade:%s" % (
        normalized_platform,
        normalize_contract_text(product_name),
        normalize_contract_grade(grade),
    )


def canonical_positive_price(value, require_canonical=False, reject_float=False):
    if isinstance(value, bool) or value is None or (reject_float and isinstance(value, float)):
        raise ValueError("price must be a canonical positive decimal")
    raw = str(value).strip()
    try:
        price = Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("price must be a canonical positive decimal")
    formatted = format(price, ".2f")
    if price <= 0 or (require_canonical and raw != formatted):
        raise ValueError("price must be a canonical positive decimal")
    return formatted


def canonical_json_bytes(payload, default=None):
    options = {
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": (",", ":"),
    }
    if default is not None:
        options["default"] = default
    return json.dumps(payload, **options).encode("utf-8")


def sha256_json(payload, prefixed=True, default=None):
    digest = hashlib.sha256(canonical_json_bytes(payload, default=default)).hexdigest()
    return ("sha256:" + digest) if prefixed else digest
