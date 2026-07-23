"""Pure contract, matching, and aggregation logic for task 11.

The module intentionally has no ShadowBot/xbot or database dependencies.  It
defines the versioned READ_ONLY batch boundary that the queue worker and the
platform adapter can share without allowing any write mode to leak into the
multi-product path.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.exceptions import ValidationError
from app.listing_identity import (
    listing_identity_key,
)
from app.repositories.workbook_repository import load_products
from app.shadowbot_contract_primitives import (
    contract_identity_key,
    normalize_contract_grade,
    normalize_contract_sku,
    normalize_contract_text,
    sha256_json,
)


CONTRACT_VERSION = 2
EXECUTION_MODE_READ_ONLY = "READ_ONLY"
DEFAULT_MAX_PRODUCTS = 10
HARD_MAX_PRODUCTS = 50
DEFAULT_MAX_PAGES = 20
HARD_MAX_PAGES = 100
DEFAULT_MAX_SCROLLS = 100
HARD_MAX_SCROLLS = 500
DEFAULT_MAX_SECONDS = 300
HARD_MAX_SECONDS = 900
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
DEFAULT_INVENTORY_PRODUCTS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "samples" / "products.xlsx"
)

LISTING_STATUSES = frozenset({"ONLINE", "OFFLINE", "UNKNOWN"})
ITEM_STATUSES = frozenset({"SUCCESS", "FAILED", "SKIPPED", "MANUAL_CHECK_REQUIRED"})
OVERALL_STATUSES = frozenset({"COMPLETED", "PARTIAL", "FAILED"})
ERROR_CODES = frozenset(
    {
        "LIST_NOT_LOADED",
        "PRODUCT_NOT_FOUND",
        "AMBIGUOUS_MATCH",
        "PRICE_PARSE_FAILED",
        "INVENTORY_PARSE_FAILED",
        "LISTING_STATUS_UNKNOWN",
        "EVIDENCE_UNAVAILABLE",
        "EVIDENCE_BINDING_FAILED",
        "BATCH_STOPPED",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")
_SAFE_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class ProductReadContractError(ValidationError):
    """Raised when a task 11 request/result violates the frozen contract."""


@dataclass(frozen=True, slots=True)
class ProductTarget:
    item_id: str
    platform: str
    platform_sku: str | None
    expected_product_name: str
    expected_grade: str

    @property
    def identity_key(self) -> str:
        return contract_identity_key(
            self.platform,
            self.platform_sku,
            self.expected_product_name,
            self.expected_grade,
        )


@dataclass(frozen=True, slots=True)
class ProductCandidate:
    platform: str
    platform_sku: str | None
    product_name: str
    grade: str
    price: str | Decimal | int | float | None
    listing_status: str
    row_identity: str = ""
    locator_summary: str = ""
    inventory: str | int | None = None

    @property
    def identity_key(self) -> str:
        return contract_identity_key(
            self.platform,
            self.platform_sku,
            self.product_name,
            self.grade,
        )


def normalize_text(value: Any) -> str:
    """Normalize human-readable identity fields without changing their meaning."""
    return normalize_contract_text(value)


def normalize_grade(value: Any) -> str:
    return normalize_contract_grade(value)


def normalize_sku(value: Any) -> str | None:
    return normalize_contract_sku(value)


def build_read_batch_id(seed: str | None = None) -> str:
    """Build a non-predictable batch id when the core service owns creation."""

    import secrets

    prefix = normalize_text(seed).replace(" ", "-")[:32] if seed else "READ-BATCH"
    prefix = re.sub(r"[^A-Za-z0-9._:-]", "-", prefix) or "READ-BATCH"
    return f"{prefix.upper()}-{secrets.token_urlsafe(18)}"


def build_inventory_read_targets(
    platform_name: str,
    *,
    products_path: Path = DEFAULT_INVENTORY_PRODUCTS_PATH,
) -> list[dict[str, Any]]:
    """Build READ_ONLY identity hints from 商品资料与库存录入."""

    normalized_platform = str(platform_name or "").strip()
    if not normalized_platform:
        raise ProductReadContractError("INPUT_INVALID: platform_name is required.")
    try:
        products = load_products(Path(products_path))
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise ProductReadContractError(
            f"INVENTORY_MAPPING_SOURCE_INVALID: {products_path}"
        ) from exc
    if not products:
        raise ProductReadContractError("INVENTORY_MAPPING_SOURCE_EMPTY")

    targets: list[dict[str, Any]] = []
    seen_page_identities: dict[tuple[str, str, str], str] = {}
    for product in products:
        identity = listing_identity_key(
            normalized_platform,
            product.product_name,
            product.grade,
        )
        previous_sku = seen_page_identities.get(identity)
        if previous_sku is not None:
            raise ProductReadContractError(
                "INVENTORY_MAPPING_AMBIGUOUS: "
                f"{previous_sku} and {product.internal_sku} share "
                f"{product.product_name}/{product.grade}."
            )
        seen_page_identities[identity] = product.internal_sku
        targets.append(
            {
                "item_id": f"ITEM-{product.internal_sku}",
                "platform": normalized_platform,
                # 当前平台页面不暴露 SKU，实际匹配仍使用页面可见的商品名+等级。
                "platform_sku": None,
                "expected_product_name": product.product_name,
                "expected_grade": product.grade,
            }
        )
    return targets


def canonical_request_digest(payload: Mapping[str, Any]) -> str:
    """Hash only the normalized contract fields used for idempotency."""

    normalized = normalize_multi_product_request(payload, check_size=False)
    return sha256_json(normalized, prefixed=False)


def compute_multi_product_instruction_hash(payload: Mapping[str, Any]) -> str:
    """Hash the v2 request plus immutable runner identity fields."""

    normalized = normalize_multi_product_request(payload, check_size=False)
    immutable = {
        "task_id": str(payload.get("task_id") or ""),
        "operation_id": str(payload.get("operation_id") or ""),
        "execution_attempt_id": str(payload.get("execution_attempt_id") or ""),
        "request": normalized,
        "applet_uri": str(payload.get("applet_uri") or ""),
        "window_title": str(payload.get("window_title") or ""),
    }
    return sha256_json(immutable)


def normalize_multi_product_request(
    payload: Mapping[str, Any],
    *,
    check_size: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProductReadContractError("INPUT_INVALID: multi-product request must be an object.")
    if check_size:
        _check_json_size(payload, MAX_REQUEST_BYTES, "request")
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise ProductReadContractError("UNKNOWN_CONTRACT_VERSION")
    execution_mode = str(payload.get("execution_mode") or "").strip().upper()
    if execution_mode != EXECUTION_MODE_READ_ONLY:
        raise ProductReadContractError("READ_ONLY_REQUIRED")
    read_batch_id = str(payload.get("read_batch_id") or "").strip()
    if not _SAFE_BATCH_ID_RE.fullmatch(read_batch_id):
        raise ProductReadContractError("INPUT_INVALID: read_batch_id is missing or malformed.")

    raw_products = payload.get("products", [])
    if not isinstance(raw_products, Sequence) or isinstance(raw_products, (str, bytes, bytearray)):
        raise ProductReadContractError("INPUT_INVALID: products must be an array.")
    if len(raw_products) > HARD_MAX_PRODUCTS:
        raise ProductReadContractError("PRODUCT_COUNT_LIMIT_EXCEEDED")

    targets: list[ProductTarget] = []
    identities: dict[str, str] = {}
    platforms: set[str] = set()
    for raw in raw_products:
        if not isinstance(raw, Mapping):
            raise ProductReadContractError("INPUT_INVALID: each product must be an object.")
        item_id = str(raw.get("item_id") or "").strip()
        platform = str(raw.get("platform") or "").strip()
        product_name = str(raw.get("expected_product_name") or "").strip()
        grade = str(raw.get("expected_grade") or "").strip()
        if not item_id or not platform or not product_name or not grade:
            raise ProductReadContractError("INPUT_INVALID: item_id, platform, name, and grade are required.")
        sku = normalize_sku(raw.get("platform_sku"))
        target = ProductTarget(item_id, platform, sku, product_name, grade)
        if item_id in {existing.item_id for existing in targets}:
            raise ProductReadContractError("DUPLICATE_ITEM_ID")
        if target.identity_key in identities:
            raise ProductReadContractError("DUPLICATE_TARGET_IDENTITY")
        identities[target.identity_key] = item_id
        platforms.add(normalize_text(platform))
        targets.append(target)
    platform_name = str(payload.get("platform_name") or "").strip()
    if not platform_name and targets:
        platform_name = targets[0].platform
    if not platform_name:
        raise ProductReadContractError("INPUT_INVALID: platform_name is required.")
    if len(platforms) > 1 or (platforms and normalize_text(platform_name) not in platforms):
        raise ProductReadContractError("SINGLE_PLATFORM_REQUIRED")

    limits = _normalize_limits(payload.get("limits"))
    normalized_products = []
    for target, raw in zip(targets, raw_products, strict=True):
        normalized_product = {
            "item_id": target.item_id,
            "platform": target.platform,
            "platform_sku": target.platform_sku,
            "expected_product_name": target.expected_product_name,
            "expected_grade": target.expected_grade,
        }
        normalized_products.append(normalized_product)

    normalized = {
        "contract_version": CONTRACT_VERSION,
        "execution_mode": execution_mode,
        "read_batch_id": read_batch_id,
        "platform_name": platform_name,
        # Evidence capture is an explicit diagnostics/manual-review opt-in.
        # Section 17 makes structured accessibility-tree reads authoritative;
        # screenshots must not be an implicit completion gate.
        "capture_evidence": _normalize_optional_bool(payload.get("capture_evidence", False), "capture_evidence"),
        "products": normalized_products,
        "limits": limits,
    }
    return normalized


def _normalize_optional_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    raise ProductReadContractError(f"INPUT_INVALID: {field_name} must be a boolean.")


def _normalize_limits(raw: Any) -> dict[str, int]:
    if raw is None:
        return {"max_pages": DEFAULT_MAX_PAGES, "max_scrolls": DEFAULT_MAX_SCROLLS, "max_seconds": DEFAULT_MAX_SECONDS}
    if not isinstance(raw, Mapping):
        raise ProductReadContractError("INPUT_INVALID: limits must be an object.")
    limits = {
        "max_pages": _bounded_int(raw.get("max_pages", DEFAULT_MAX_PAGES), 1, HARD_MAX_PAGES, "max_pages"),
        "max_scrolls": _bounded_int(raw.get("max_scrolls", DEFAULT_MAX_SCROLLS), 1, HARD_MAX_SCROLLS, "max_scrolls"),
        "max_seconds": _bounded_int(raw.get("max_seconds", DEFAULT_MAX_SECONDS), 1, HARD_MAX_SECONDS, "max_seconds"),
    }
    return limits


def _bounded_int(value: Any, minimum: int, maximum: int, field_name: str) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ProductReadContractError(f"INPUT_INVALID: {field_name} must be an integer.")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ProductReadContractError(f"INPUT_INVALID: {field_name} must be an integer.") from exc
    if not minimum <= integer <= maximum:
        raise ProductReadContractError(f"{field_name.upper()}_LIMIT_EXCEEDED")
    return integer


def _check_json_size(payload: Mapping[str, Any], maximum: int, label: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) > maximum:
        raise ProductReadContractError(f"{label.upper()}_SIZE_LIMIT_EXCEEDED")


def resolve_product_match(target: ProductTarget, candidates: Iterable[ProductCandidate]) -> ProductCandidate | str:
    matches = [candidate for candidate in candidates if _candidate_matches(target, candidate)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return "AMBIGUOUS_MATCH"
    return "PRODUCT_NOT_FOUND"


def _candidate_matches(target: ProductTarget, candidate: ProductCandidate) -> bool:
    if normalize_text(candidate.platform) != normalize_text(target.platform):
        return False
    if target.platform_sku:
        return normalize_sku(candidate.platform_sku) == target.platform_sku
    return (
        normalize_text(candidate.product_name) == normalize_text(target.expected_product_name)
        and normalize_grade(candidate.grade) == normalize_grade(target.expected_grade)
    )


def stable_viewport_fingerprint(candidates: Iterable[ProductCandidate]) -> str:
    """Fingerprint identities and multiplicity only; exclude volatile UI fields."""

    counts = Counter(candidate.identity_key for candidate in candidates)
    encoded = json.dumps(sorted(counts.items()), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_has_no_progress(history: Sequence[str], *, unchanged_moves: int = 2) -> bool:
    if unchanged_moves < 1 or len(history) < unchanged_moves + 1:
        return False
    tail = history[-(unchanged_moves + 1) :]
    return len(set(tail)) == 1


def normalize_price(value: Any) -> str:
    if isinstance(value, (bool, float)) or value is None:
        raise ProductReadContractError("PRICE_PARSE_FAILED")
    try:
        price = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ProductReadContractError("PRICE_PARSE_FAILED") from exc
    if not price.is_finite() or price < 0:
        raise ProductReadContractError("PRICE_PARSE_FAILED")
    return format(price, "f")


def validate_evidence_binding(
    evidence: Iterable[Mapping[str, Any]],
    *,
    read_batch_id: str,
    item_id: str,
    execution_attempt_id: str,
) -> None:
    for item in evidence:
        if not isinstance(item, Mapping):
            raise ProductReadContractError("EVIDENCE_BINDING_FAILED")
        required = ("evidence_id", "evidence_type", "relative_path", "sha256", "read_batch_id", "item_id", "execution_attempt_id")
        if any(not str(item.get(name) or "").strip() for name in required):
            raise ProductReadContractError("EVIDENCE_BINDING_FAILED")
        if str(item.get("read_batch_id")) != read_batch_id or str(item.get("item_id")) != item_id:
            raise ProductReadContractError("EVIDENCE_BINDING_FAILED")
        if str(item.get("execution_attempt_id")) != execution_attempt_id:
            raise ProductReadContractError("EVIDENCE_BINDING_FAILED")
        relative_path = str(item.get("relative_path") or "")
        if relative_path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", relative_path) or ".." in relative_path.split("/"):
            raise ProductReadContractError("EVIDENCE_BINDING_FAILED")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(item.get("sha256"))):
            raise ProductReadContractError("EVIDENCE_BINDING_FAILED")


def aggregate_product_snapshots(
    *,
    read_batch_id: str,
    contract_version: int,
    started_at: str,
    completed_at: str,
    snapshots: Sequence[Mapping[str, Any]],
    expected_item_ids: Sequence[str] | None = None,
    check_size: bool = True,
) -> dict[str, Any]:
    if not snapshots:
        raise ProductReadContractError("INPUT_INVALID: product_snapshots must be non-empty.")
    seen: set[str] = set()
    expected = {str(item_id).strip() for item_id in expected_item_ids or () if str(item_id).strip()}
    normalized: list[dict[str, Any]] = []
    for raw in snapshots:
        item_id = str(raw.get("item_id") or "").strip()
        item_status = str(raw.get("item_status") or "").strip().upper()
        listing_status = str(raw.get("listing_status") or "").strip().upper()
        error_code = str(raw.get("error_code") or "").strip().upper() or None
        if not item_id or item_id in seen or item_status not in ITEM_STATUSES or listing_status not in LISTING_STATUSES:
            raise ProductReadContractError("RESULT_CONTRACT_INVALID")
        if error_code is not None and error_code not in ERROR_CODES:
            raise ProductReadContractError("RESULT_CONTRACT_INVALID")
        seen.add(item_id)
        normalized_item = dict(raw)
        if raw.get("price") is not None:
            normalized_item["price"] = normalize_price(raw.get("price"))
        if raw.get("inventory") is not None:
            normalized_item["inventory"] = normalize_inventory(raw.get("inventory"))
        currency = str(raw.get("currency") or "CNY").strip().upper()
        if currency != "CNY":
            raise ProductReadContractError("RESULT_CONTRACT_INVALID")
        normalized_item["currency"] = "CNY"
        normalized_item.update({"item_id": item_id, "item_status": item_status, "listing_status": listing_status, "error_code": error_code})
        normalized.append(normalized_item)
    success_count = sum(item["item_status"] == "SUCCESS" for item in normalized)
    failed_count = sum(item["item_status"] == "FAILED" for item in normalized)
    skipped_count = sum(item["item_status"] == "SKIPPED" for item in normalized)
    manual_check_count = sum(item["item_status"] == "MANUAL_CHECK_REQUIRED" for item in normalized)
    total_count = len(normalized)
    if expected and seen != expected:
        raise ProductReadContractError("RESULT_CONTRACT_INVALID")
    if success_count == total_count:
        overall_status = "COMPLETED"
    elif success_count == 0 and manual_check_count == 0 and skipped_count == 0:
        overall_status = "FAILED"
    else:
        overall_status = "PARTIAL"
    result = {
        "read_batch_id": read_batch_id,
        "contract_version": contract_version,
        "started_at": started_at,
        "completed_at": completed_at,
        "total_count": total_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "manual_check_count": manual_check_count,
        "overall_status": overall_status,
        "product_snapshots": normalized,
    }
    if check_size:
        _check_json_size(result, MAX_RESULT_BYTES, "result")
    return result


def normalize_inventory(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ProductReadContractError("INVENTORY_PARSE_FAILED")
    if isinstance(value, float) and not value.is_integer():
        raise ProductReadContractError("INVENTORY_PARSE_FAILED")
    try:
        inventory = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ProductReadContractError("INVENTORY_PARSE_FAILED") from exc
    if inventory < 0:
        raise ProductReadContractError("INVENTORY_PARSE_FAILED")
    return inventory
