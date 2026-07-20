"""Pure task 12 price-batch contracts and deterministic identity helpers.

This module deliberately has no database, ShadowBot, or web dependencies.  It
is the shared fail-closed boundary for contract_version=3 before any UI starts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping, Sequence

from app.exceptions import ValidationError
from app.services.shadowbot_product_read import normalize_grade, normalize_sku, normalize_text


CONTRACT_VERSION = 3
READ_CONTRACT_VERSION = 2
BATCH_TYPE = "SERIAL_PRICE_UPDATE"
EXECUTION_MODES = frozenset({"FILL_PREVIEW", "COMMIT"})
STOP_POLICIES = frozenset({"PAUSE_ON_UNCERTAIN", "CONTINUE_ON_FAILED"})
DEFAULT_MAX_ITEMS = 5
HARD_MAX_ITEMS = 20
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
SOURCE_SNAPSHOT_MAX_AGE_SECONDS = 300
FRESH_READ_MAX_AGE_SECONDS = 60
IDENTITY_NORMALIZATION_VERSION = "task11-v1"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_HASH_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_PRICE_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_CNY_QUANTUM = Decimal("0.01")
_MAX_SAFE_INTEGER = 2**53 - 1


class PriceBatchErrorCode(StrEnum):
    PRICE_BATCH_ID_CONFLICT = "PRICE_BATCH_ID_CONFLICT"
    UNSUPPORTED_CONTRACT_VERSION = "UNSUPPORTED_CONTRACT_VERSION"
    SINGLE_PLATFORM_REQUIRED = "SINGLE_PLATFORM_REQUIRED"
    UNSUPPORTED_EXECUTION_MODE = "UNSUPPORTED_EXECUTION_MODE"
    UNSUPPORTED_STOP_POLICY = "UNSUPPORTED_STOP_POLICY"
    EMPTY_BATCH = "EMPTY_BATCH"
    BATCH_CAPACITY_EXCEEDED = "BATCH_CAPACITY_EXCEEDED"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
    DUPLICATE_ITEM_ID = "DUPLICATE_ITEM_ID"
    DUPLICATE_OPERATION_ID = "DUPLICATE_OPERATION_ID"
    DUPLICATE_WRITE_IDENTITY = "DUPLICATE_WRITE_IDENTITY"
    DUPLICATE_PAGE_IDENTITY = "DUPLICATE_PAGE_IDENTITY"
    INVALID_ORDINAL = "INVALID_ORDINAL"
    INVALID_PRICE_TYPE = "INVALID_PRICE_TYPE"
    TARGET_PRICE_INVALID = "TARGET_PRICE_INVALID"
    SOURCE_REQUEST_NOT_FOUND = "SOURCE_REQUEST_NOT_FOUND"
    SOURCE_RESULT_NOT_FOUND = "SOURCE_RESULT_NOT_FOUND"
    SOURCE_SIDECAR_NOT_FOUND = "SOURCE_SIDECAR_NOT_FOUND"
    SOURCE_ARCHIVE_NOT_VERIFIED = "SOURCE_ARCHIVE_NOT_VERIFIED"
    SOURCE_BATCH_ID_MISMATCH = "SOURCE_BATCH_ID_MISMATCH"
    SOURCE_ITEM_NOT_FOUND = "SOURCE_ITEM_NOT_FOUND"
    SOURCE_ITEM_IDENTITY_MISMATCH = "SOURCE_ITEM_IDENTITY_MISMATCH"
    SOURCE_SNAPSHOT_HASH_MISMATCH = "SOURCE_SNAPSHOT_HASH_MISMATCH"
    SOURCE_PAGE_CONTEXT_HASH_MISMATCH = "SOURCE_PAGE_CONTEXT_HASH_MISMATCH"
    SOURCE_SNAPSHOT_EXPIRED = "SOURCE_SNAPSHOT_EXPIRED"
    SOURCE_CONTEXT_UNAVAILABLE = "SOURCE_CONTEXT_UNAVAILABLE"
    NORMALIZED_REQUEST_DIGEST_MISMATCH = "NORMALIZED_REQUEST_DIGEST_MISMATCH"
    BATCH_ITEM_BINDING_MISMATCH = "BATCH_ITEM_BINDING_MISMATCH"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVED_PAYLOAD_HASH_MISMATCH = "APPROVED_PAYLOAD_HASH_MISMATCH"
    APPROVAL_MODE_NOT_ALLOWED = "APPROVAL_MODE_NOT_ALLOWED"
    WRITE_LOCK_CONFLICT = "WRITE_LOCK_CONFLICT"
    LIST_NOT_LOADED = "LIST_NOT_LOADED"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    PRODUCT_IDENTITY_MISMATCH = "PRODUCT_IDENTITY_MISMATCH"
    CURRENT_PRICE_PARSE_FAILED = "CURRENT_PRICE_PARSE_FAILED"
    FRESH_READ_EXPIRED = "FRESH_READ_EXPIRED"
    OLD_PRICE_CHANGED = "OLD_PRICE_CHANGED"
    PREVIEW_INPUT_MISMATCH = "PREVIEW_INPUT_MISMATCH"
    SUBMIT_RESULT_UNKNOWN = "SUBMIT_RESULT_UNKNOWN"
    WORKER_STOP_REQUESTED = "WORKER_STOP_REQUESTED"
    BATCH_PAUSED = "BATCH_PAUSED"
    BATCH_CANCELLED = "BATCH_CANCELLED"
    RESULT_CONTRACT_INVALID = "RESULT_CONTRACT_INVALID"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILIATION_CONFLICT = "RECONCILIATION_CONFLICT"


PRICE_BATCH_ERROR_CODES = frozenset(code.value for code in PriceBatchErrorCode)


class BatchStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BatchItemStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    PREVIEWED = "PREVIEWED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"


WRITE_LOCK_STATES = frozenset(
    {
        "PENDING",
        "STARTING",
        "RUNNING",
        "SUBMIT_INTENT_RECORDED",
        "SUBMIT_CLICKED",
        "UNKNOWN",
        "NEEDS_RECONCILIATION",
    }
)

PROCESSED_ITEM_STATUSES = frozenset(
    {
        BatchItemStatus.PREVIEWED.value,
        BatchItemStatus.VERIFIED.value,
        BatchItemStatus.FAILED.value,
        BatchItemStatus.SKIPPED.value,
        BatchItemStatus.CANCELLED.value,
        BatchItemStatus.NEEDS_RECONCILIATION.value,
    }
)


class PriceBatchContractError(ValidationError):
    """Stable fail-closed task 12 contract failure."""

    def __init__(self, code: PriceBatchErrorCode | str, detail: str = ""):
        self.code = code.value if isinstance(code, PriceBatchErrorCode) else str(code)
        if self.code not in PRICE_BATCH_ERROR_CODES:
            raise ValueError(f"unfrozen task 12 error code: {self.code}")
        self.detail = detail
        super().__init__(self.code if not detail else f"{self.code}: {detail}")


def _fail(code: PriceBatchErrorCode, detail: str = "") -> None:
    raise PriceBatchContractError(code, detail)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the task 12 JCS profile.

    Task 12 execution data rejects binary floats and limits integers to the
    interoperable IEEE-754 range.  Within that profile this implements RFC
    8785 key ordering and compact UTF-8 JSON serialization.
    """

    def encode(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int):
            if abs(item) > _MAX_SAFE_INTEGER:
                raise TypeError("JCS integer is outside the interoperable range")
            return str(item)
        if isinstance(item, float):
            raise TypeError("binary floats are not allowed in task 12 canonical JSON")
        if isinstance(item, str):
            _reject_unpaired_surrogates(item)
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, (list, tuple)):
            return "[" + ",".join(encode(child) for child in item) + "]"
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("JCS object keys must be strings")
            keys = sorted(item, key=lambda key: key.encode("utf-16-be"))
            return "{" + ",".join(f"{encode(key)}:{encode(item[key])}" for key in keys) + "}"
        raise TypeError(f"unsupported task 12 canonical JSON type: {type(item).__name__}")

    return encode(value).encode("utf-8")


def _reject_unpaired_surrogates(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise TypeError("unpaired UTF-16 surrogate is not valid JCS input")


def sha256_jcs(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_price_string(value: Any, *, error_code: PriceBatchErrorCode) -> str:
    if not isinstance(value, str):
        _fail(PriceBatchErrorCode.INVALID_PRICE_TYPE)
    raw = value.strip()
    if not _PRICE_RE.fullmatch(raw):
        _fail(error_code)
    try:
        price = Decimal(raw)
    except InvalidOperation:
        _fail(error_code)
    if not price.is_finite() or price < 0:
        _fail(error_code)
    normalized = price.quantize(_CNY_QUANTUM)
    if normalized != price:
        _fail(error_code)
    return format(normalized, ".2f")


def build_page_identity_key(*, platform: str, product_name: str, grade: str) -> str:
    return sha256_jcs(
        {
            "v": 1,
            "platform": normalize_text(platform),
            "normalized_product_name": normalize_text(product_name),
            "normalized_grade": normalize_grade(grade),
        }
    )


def build_write_identity_key(*, platform: str, platform_sku: Any, page_identity_key: str) -> str:
    return sha256_jcs(
        {
            "v": 1,
            "platform": normalize_text(platform),
            "platform_sku": normalize_sku(platform_sku),
            "page_identity_key": _normalize_sha256(page_identity_key, PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH),
        }
    )


def build_task11_source_binding(
    request: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None,
    archive_manifest: Mapping[str, Any] | None,
    *,
    platform_key: str,
    accepted_platform_names: Sequence[str],
    now: datetime | None = None,
    max_age_seconds: int = SOURCE_SNAPSHOT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Build a trusted task 11 source view from request, result, and sidecar."""

    if request is None:
        _fail(PriceBatchErrorCode.SOURCE_REQUEST_NOT_FOUND)
    if result is None:
        _fail(PriceBatchErrorCode.SOURCE_RESULT_NOT_FOUND)
    if archive_manifest is None:
        _fail(PriceBatchErrorCode.SOURCE_SIDECAR_NOT_FOUND)
    if archive_manifest.get("archive_verified") is not True:
        _fail(PriceBatchErrorCode.SOURCE_ARCHIVE_NOT_VERIFIED)
    if request.get("contract_version") != READ_CONTRACT_VERSION or result.get("contract_version") != READ_CONTRACT_VERSION:
        _fail(PriceBatchErrorCode.UNSUPPORTED_CONTRACT_VERSION)

    request_batch_id = _required_id(request.get("read_batch_id"), PriceBatchErrorCode.SOURCE_BATCH_ID_MISMATCH)
    result_batch_id = _required_id(result.get("read_batch_id"), PriceBatchErrorCode.SOURCE_BATCH_ID_MISMATCH)
    if request_batch_id != result_batch_id:
        _fail(PriceBatchErrorCode.SOURCE_BATCH_ID_MISMATCH)

    request_sha = _normalize_sha256(
        archive_manifest.get("request_file_sha256"), PriceBatchErrorCode.SOURCE_SIDECAR_NOT_FOUND
    )
    result_sha = _normalize_sha256(
        archive_manifest.get("result_file_sha256"), PriceBatchErrorCode.SOURCE_SIDECAR_NOT_FOUND
    )
    result_request_sha = _normalize_sha256(
        result.get("request_file_sha256"), PriceBatchErrorCode.SOURCE_ARCHIVE_NOT_VERIFIED
    )
    if request_sha != result_request_sha:
        _fail(PriceBatchErrorCode.SOURCE_ARCHIVE_NOT_VERIFIED, "request checksum mismatch")

    stable_platform = normalize_text(platform_key)
    accepted_names = {normalize_text(value) for value in accepted_platform_names if normalize_text(value)}
    if not stable_platform or not accepted_names:
        _fail(PriceBatchErrorCode.SOURCE_CONTEXT_UNAVAILABLE)
    platform_name = str(request.get("platform_name") or "").strip()
    result_platform_name = str(result.get("platform_name") or "").strip()
    applet_uri = str(request.get("applet_uri") or "").strip()
    window_title = str(request.get("window_title") or "").strip()
    if not platform_name or not result_platform_name or not applet_uri or not window_title:
        _fail(PriceBatchErrorCode.SOURCE_CONTEXT_UNAVAILABLE)
    if normalize_text(platform_name) != normalize_text(result_platform_name):
        _fail(PriceBatchErrorCode.SINGLE_PLATFORM_REQUIRED)

    raw_request_products = request.get("products")
    raw_snapshots = result.get("product_snapshots")
    if not _is_array(raw_request_products) or not _is_array(raw_snapshots) or not raw_snapshots:
        _fail(PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND)
    if any(not isinstance(item, Mapping) for item in raw_request_products):
        _fail(PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND)
    request_item_ids = [_required_id(item.get("item_id"), PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND) for item in raw_request_products]
    request_platform_names = {
        normalize_text(item.get("platform")) for item in raw_request_products if normalize_text(item.get("platform"))
    }
    if len(request_platform_names) != 1:
        _fail(PriceBatchErrorCode.SINGLE_PLATFORM_REQUIRED)
    observed_platform_name = next(iter(request_platform_names))
    if observed_platform_name not in accepted_names or observed_platform_name != normalize_text(platform_name):
        _fail(PriceBatchErrorCode.SINGLE_PLATFORM_REQUIRED)
    snapshots: list[dict[str, Any]] = []
    snapshot_item_ids: list[str] = []
    observed_values: list[datetime] = []
    for raw in raw_snapshots:
        if not isinstance(raw, Mapping):
            _fail(PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND)
        item_id = _required_id(raw.get("item_id"), PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND)
        if item_id in snapshot_item_ids:
            _fail(PriceBatchErrorCode.DUPLICATE_ITEM_ID)
        product_name = str(raw.get("product_name") or "").strip()
        grade = str(raw.get("grade") or "").strip()
        listing_status = str(raw.get("listing_status") or "").strip().upper()
        observed_at = _parse_datetime(raw.get("observed_at"), PriceBatchErrorCode.SOURCE_CONTEXT_UNAVAILABLE)
        if not product_name or not grade or listing_status != "ONLINE":
            _fail(PriceBatchErrorCode.SOURCE_ITEM_IDENTITY_MISMATCH)
        price = normalize_price_string(raw.get("price"), error_code=PriceBatchErrorCode.CURRENT_PRICE_PARSE_FAILED)
        inventory = _normalize_inventory(raw.get("inventory"))
        snapshot_platform_name = str(raw.get("platform") or "").strip()
        if normalize_text(snapshot_platform_name) != observed_platform_name:
            _fail(PriceBatchErrorCode.SINGLE_PLATFORM_REQUIRED)
        snapshots.append(
            {
                "item_id": item_id,
                "product_name": product_name,
                "grade": grade,
                "price": price,
                "inventory": inventory,
                "listing_status": "ONLINE",
                "observed_at": _format_datetime(observed_at),
            }
        )
        snapshot_item_ids.append(item_id)
        observed_values.append(observed_at)
    if request_item_ids != snapshot_item_ids:
        _fail(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH, "task 11 request/result item order differs")

    page_context = {
        "v": 1,
        "platform": stable_platform,
        "platform_name": normalize_text(platform_name),
        "applet_identity_sha256": sha256_text(applet_uri),
        "window_title": normalize_text(window_title),
        "page_name": "商品管理/上架中",
        "listing_status_filter": "ONLINE",
        "read_contract_version": READ_CONTRACT_VERSION,
    }
    count_fields = {
        key: _nonnegative_int(result.get(key), PriceBatchErrorCode.SOURCE_ARCHIVE_NOT_VERIFIED)
        for key in ("total_count", "success_count", "failed_count", "skipped_count", "manual_check_count")
    }
    if count_fields["total_count"] != len(snapshots):
        _fail(PriceBatchErrorCode.SOURCE_ARCHIVE_NOT_VERIFIED, "task 11 total_count mismatch")
    business_snapshot_view = {
        "contract_version": READ_CONTRACT_VERSION,
        "read_batch_id": result_batch_id,
        "platform": stable_platform,
        "page_context": page_context,
        "counts": count_fields,
        "items": snapshots,
    }
    source_observed_at = min(observed_values)
    _validate_age(
        observed_at=source_observed_at,
        now=now,
        max_age_seconds=max_age_seconds,
        error_code=PriceBatchErrorCode.SOURCE_SNAPSHOT_EXPIRED,
    )
    return {
        "source_read_batch_id": result_batch_id,
        "source_snapshot_sha256": sha256_jcs(business_snapshot_view),
        "source_page_context_sha256": sha256_jcs(page_context),
        "source_observed_at": _format_datetime(source_observed_at),
        "source_snapshot_max_age_seconds": max_age_seconds,
        "source_request_file_sha256": request_sha,
        "source_result_file_sha256": result_sha,
        "platform": stable_platform,
        "page_context": page_context,
        "business_snapshot_view": business_snapshot_view,
        "source_items": {item["item_id"]: item for item in snapshots},
    }


def normalize_price_batch_request(
    payload: Mapping[str, Any],
    *,
    source_binding: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    check_size: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        _fail(PriceBatchErrorCode.RESULT_CONTRACT_INVALID, "request must be an object")
    if check_size and _json_size(payload) > MAX_REQUEST_BYTES:
        _fail(PriceBatchErrorCode.REQUEST_TOO_LARGE)
    if payload.get("contract_version") != CONTRACT_VERSION:
        _fail(PriceBatchErrorCode.UNSUPPORTED_CONTRACT_VERSION)
    batch_id = _required_id(payload.get("batch_id"), PriceBatchErrorCode.PRICE_BATCH_ID_CONFLICT)
    platform = str(payload.get("platform") or "").strip()
    if not platform:
        _fail(PriceBatchErrorCode.SINGLE_PLATFORM_REQUIRED)
    if str(payload.get("batch_type") or "").strip().upper() != BATCH_TYPE:
        _fail(PriceBatchErrorCode.RESULT_CONTRACT_INVALID, "unsupported batch_type")
    execution_mode = str(payload.get("execution_mode") or "").strip().upper()
    if execution_mode not in EXECUTION_MODES:
        _fail(PriceBatchErrorCode.UNSUPPORTED_EXECUTION_MODE)
    stop_policy = str(payload.get("stop_policy") or "PAUSE_ON_UNCERTAIN").strip().upper()
    if stop_policy not in STOP_POLICIES:
        _fail(PriceBatchErrorCode.UNSUPPORTED_STOP_POLICY)
    capture_evidence = _strict_bool(payload.get("capture_evidence", False))

    configured_limit = _bounded_int(max_items, 1, HARD_MAX_ITEMS, PriceBatchErrorCode.BATCH_CAPACITY_EXCEEDED)
    raw_items = payload.get("items")
    if not _is_array(raw_items) or not raw_items:
        _fail(PriceBatchErrorCode.EMPTY_BATCH)
    if len(raw_items) > configured_limit:
        _fail(PriceBatchErrorCode.BATCH_CAPACITY_EXCEEDED)

    source_read_batch_id = _required_id(
        payload.get("source_read_batch_id"), PriceBatchErrorCode.SOURCE_BATCH_ID_MISMATCH
    )
    source_snapshot_sha256 = _normalize_sha256(
        payload.get("source_snapshot_sha256"), PriceBatchErrorCode.SOURCE_SNAPSHOT_HASH_MISMATCH
    )
    source_page_context_sha256 = _normalize_sha256(
        payload.get("source_page_context_sha256"), PriceBatchErrorCode.SOURCE_PAGE_CONTEXT_HASH_MISMATCH
    )
    source_observed_at = _parse_datetime(
        payload.get("source_observed_at"), PriceBatchErrorCode.SOURCE_CONTEXT_UNAVAILABLE
    )
    source_max_age = _bounded_int(
        payload.get("source_snapshot_max_age_seconds"),
        SOURCE_SNAPSHOT_MAX_AGE_SECONDS,
        SOURCE_SNAPSHOT_MAX_AGE_SECONDS,
        PriceBatchErrorCode.SOURCE_SNAPSHOT_EXPIRED,
    )
    _validate_age(
        observed_at=source_observed_at,
        now=now,
        max_age_seconds=source_max_age,
        error_code=PriceBatchErrorCode.SOURCE_SNAPSHOT_EXPIRED,
    )

    item_ids: set[str] = set()
    operation_ids: set[str] = set()
    page_identities: set[str] = set()
    write_identities: set[str] = set()
    items: list[dict[str, Any]] = []
    for expected_ordinal, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, Mapping):
            _fail(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        ordinal = raw.get("ordinal")
        if isinstance(ordinal, bool) or ordinal != expected_ordinal:
            _fail(PriceBatchErrorCode.INVALID_ORDINAL)
        item_id = _required_id(raw.get("item_id"), PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        operation_id = _required_id(raw.get("operation_id"), PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        if item_id in item_ids:
            _fail(PriceBatchErrorCode.DUPLICATE_ITEM_ID)
        if operation_id in operation_ids:
            _fail(PriceBatchErrorCode.DUPLICATE_OPERATION_ID)
        item_ids.add(item_id)
        operation_ids.add(operation_id)
        source_item_id = _required_id(raw.get("source_item_id"), PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND)
        task_id = _required_id(raw.get("task_id"), PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        review_task_id = _required_id(raw.get("review_task_id"), PriceBatchErrorCode.APPROVAL_REQUIRED)
        approved_payload_hash = _normalize_sha256(
            raw.get("approved_payload_hash"), PriceBatchErrorCode.APPROVED_PAYLOAD_HASH_MISMATCH
        )
        product_name = str(raw.get("expected_product_name") or "").strip()
        grade = str(raw.get("expected_grade") or "").strip()
        if not product_name or not grade:
            _fail(PriceBatchErrorCode.PRODUCT_IDENTITY_MISMATCH)
        platform_sku = normalize_sku(raw.get("platform_sku"))
        expected_old_price = normalize_price_string(
            raw.get("approved_expected_old_price"), error_code=PriceBatchErrorCode.CURRENT_PRICE_PARSE_FAILED
        )
        target_price = normalize_price_string(
            raw.get("target_price"), error_code=PriceBatchErrorCode.TARGET_PRICE_INVALID
        )
        page_identity_key = build_page_identity_key(
            platform=platform, product_name=product_name, grade=grade
        )
        write_identity_key = build_write_identity_key(
            platform=platform, platform_sku=platform_sku, page_identity_key=page_identity_key
        )
        _verify_optional_hash(
            raw.get("page_identity_key"), page_identity_key, PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH
        )
        _verify_optional_hash(
            raw.get("write_identity_key"), write_identity_key, PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH
        )
        if page_identity_key in page_identities:
            _fail(PriceBatchErrorCode.DUPLICATE_PAGE_IDENTITY)
        if write_identity_key in write_identities:
            _fail(PriceBatchErrorCode.DUPLICATE_WRITE_IDENTITY)
        page_identities.add(page_identity_key)
        write_identities.add(write_identity_key)
        items.append(
            {
                "item_id": item_id,
                "ordinal": ordinal,
                "source_item_id": source_item_id,
                "task_id": task_id,
                "review_task_id": review_task_id,
                "operation_id": operation_id,
                "approved_payload_hash": approved_payload_hash,
                "platform_sku": platform_sku,
                "expected_product_name": product_name,
                "expected_grade": grade,
                "normalized_product_name": normalize_text(product_name),
                "normalized_grade": normalize_grade(grade),
                "page_identity_key": page_identity_key,
                "write_identity_key": write_identity_key,
                "approved_expected_old_price": expected_old_price,
                "target_price": target_price,
            }
        )

    normalized = {
        "contract_version": CONTRACT_VERSION,
        "batch_id": batch_id,
        "platform": normalize_text(platform),
        "batch_type": BATCH_TYPE,
        "execution_mode": execution_mode,
        "stop_policy": stop_policy,
        "capture_evidence": capture_evidence,
        "identity_normalization_version": IDENTITY_NORMALIZATION_VERSION,
        "source_read_batch_id": source_read_batch_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "source_page_context_sha256": source_page_context_sha256,
        "source_observed_at": _format_datetime(source_observed_at),
        "source_snapshot_max_age_seconds": source_max_age,
        "items": items,
    }
    normalized["normalized_request_digest"] = compute_normalized_request_digest(normalized)
    provided_digest = payload.get("normalized_request_digest")
    if provided_digest is not None:
        expected_digest = _normalize_sha256(
            provided_digest, PriceBatchErrorCode.NORMALIZED_REQUEST_DIGEST_MISMATCH
        )
        if expected_digest != normalized["normalized_request_digest"]:
            _fail(PriceBatchErrorCode.NORMALIZED_REQUEST_DIGEST_MISMATCH)
    if source_binding is not None:
        validate_source_binding(normalized, source_binding, now=now)
    return normalized


def compute_normalized_request_digest(normalized_request: Mapping[str, Any]) -> str:
    digest_view = {
        key: value
        for key, value in normalized_request.items()
        if key not in {"batch_id", "normalized_request_digest", "created_at", "transport_path"}
    }
    return sha256_jcs(digest_view)


def validate_source_binding(
    normalized_request: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    if normalized_request.get("source_read_batch_id") != source_binding.get("source_read_batch_id"):
        _fail(PriceBatchErrorCode.SOURCE_BATCH_ID_MISMATCH)
    if normalized_request.get("source_snapshot_sha256") != source_binding.get("source_snapshot_sha256"):
        _fail(PriceBatchErrorCode.SOURCE_SNAPSHOT_HASH_MISMATCH)
    if normalized_request.get("source_page_context_sha256") != source_binding.get("source_page_context_sha256"):
        _fail(PriceBatchErrorCode.SOURCE_PAGE_CONTEXT_HASH_MISMATCH)
    if normalize_text(normalized_request.get("platform")) != normalize_text(source_binding.get("platform")):
        _fail(PriceBatchErrorCode.SINGLE_PLATFORM_REQUIRED)
    observed_at = _parse_datetime(
        source_binding.get("source_observed_at"), PriceBatchErrorCode.SOURCE_CONTEXT_UNAVAILABLE
    )
    _validate_age(
        observed_at=observed_at,
        now=now,
        max_age_seconds=int(normalized_request["source_snapshot_max_age_seconds"]),
        error_code=PriceBatchErrorCode.SOURCE_SNAPSHOT_EXPIRED,
    )
    source_items = source_binding.get("source_items")
    if not isinstance(source_items, Mapping):
        _fail(PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND)
    for item in normalized_request["items"]:
        source_item = source_items.get(item["source_item_id"])
        if not isinstance(source_item, Mapping):
            _fail(PriceBatchErrorCode.SOURCE_ITEM_NOT_FOUND)
        if (
            normalize_text(source_item.get("product_name")) != item["normalized_product_name"]
            or normalize_grade(source_item.get("grade")) != item["normalized_grade"]
            or str(source_item.get("listing_status") or "").upper() != "ONLINE"
        ):
            _fail(PriceBatchErrorCode.SOURCE_ITEM_IDENTITY_MISMATCH)


def aggregate_batch_counts(item_statuses: Sequence[str]) -> dict[str, int]:
    counts = {
        "pending_count": 0,
        "ready_count": 0,
        "running_count": 0,
        "previewed_count": 0,
        "verified_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "cancelled_count": 0,
        "needs_reconciliation_count": 0,
    }
    for raw_status in item_statuses:
        status = str(raw_status or "").strip().upper()
        try:
            BatchItemStatus(status)
        except ValueError:
            _fail(PriceBatchErrorCode.RESULT_CONTRACT_INVALID, f"unknown item status {status}")
        counts[status.casefold() + "_count"] += 1
    counts["processed_count"] = sum(
        counts[status.casefold() + "_count"] for status in PROCESSED_ITEM_STATUSES
    )
    counts["total_count"] = (
        counts["pending_count"]
        + counts["ready_count"]
        + counts["running_count"]
        + counts["processed_count"]
    )
    return counts


def _verify_optional_hash(value: Any, expected: str, error_code: PriceBatchErrorCode) -> None:
    if value is not None and _normalize_sha256(value, error_code) != expected:
        _fail(error_code)


def _normalize_sha256(value: Any, error_code: PriceBatchErrorCode) -> str:
    match = _HASH_RE.fullmatch(str(value or "").strip())
    if not match:
        _fail(error_code)
    return "sha256:" + match.group(1).lower()


def _required_id(value: Any, error_code: PriceBatchErrorCode) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(normalized):
        _fail(error_code)
    return normalized


def _strict_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        _fail(PriceBatchErrorCode.RESULT_CONTRACT_INVALID, "capture_evidence must be boolean")
    return value


def _bounded_int(value: Any, minimum: int, maximum: int, error_code: PriceBatchErrorCode) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(error_code)
    return value


def _nonnegative_int(value: Any, error_code: PriceBatchErrorCode) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(error_code)
    return value


def _normalize_inventory(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        _fail(PriceBatchErrorCode.SOURCE_ITEM_IDENTITY_MISMATCH)
    try:
        inventory = int(str(value).strip())
    except (TypeError, ValueError):
        _fail(PriceBatchErrorCode.SOURCE_ITEM_IDENTITY_MISMATCH)
    if inventory < 0 or str(inventory) != str(value).strip():
        _fail(PriceBatchErrorCode.SOURCE_ITEM_IDENTITY_MISMATCH)
    return inventory


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _parse_datetime(value: Any, error_code: PriceBatchErrorCode) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        _fail(error_code)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _fail(error_code)
    if parsed.tzinfo is None:
        _fail(error_code)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_age(
    *,
    observed_at: datetime,
    now: datetime | None,
    max_age_seconds: int,
    error_code: PriceBatchErrorCode,
) -> None:
    if now is None:
        return
    normalized_now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age = (normalized_now - observed_at).total_seconds()
    if age < 0 or age > max_age_seconds:
        _fail(error_code)


def _json_size(payload: Mapping[str, Any]) -> int:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        _fail(PriceBatchErrorCode.RESULT_CONTRACT_INVALID, "request is not JSON serializable")
    return len(encoded)
