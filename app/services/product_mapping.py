from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping
from uuid import uuid4

from app.enums import ProductMappingStatus
from app.exceptions import ValidationError
from app.platform_product_identity import (
    PlatformProductIdentityError,
    canonical_identity_json,
    identity_digest,
    legacy_workbook_identity,
)
from app.repositories.workbook_repository import load_table_records


MAPPING_SCHEMA_VERSION = "product-identity-mapping-2.0"


class ProductMappingError(ValidationError):
    """Raised when the operator-owned mapping source violates its contract."""


@dataclass(frozen=True, slots=True)
class ProductMappingRecord:
    mapping_id: str
    platform_name: str
    platform_product_name: str
    normalized_platform_product_name: str
    grade: str
    internal_sku: str | None
    candidate_internal_sku: str | None
    mapping_status: ProductMappingStatus
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    remark: str = ""
    account_id: str = ""
    platform_product_identity_json: str = ""
    platform_product_identity_digest: str = ""
    candidate_internal_skus: tuple[str, ...] = ()

    @property
    def identity_key(self) -> tuple[str, str, str]:
        return (
            normalize_mapping_text(self.platform_name),
            self.normalized_platform_product_name,
            normalize_mapping_text(self.grade),
        )

    @property
    def authority_identity_key(self) -> tuple[str, str, str]:
        return (
            normalize_mapping_text(self.platform_name),
            self.account_id.strip(),
            self.platform_product_identity_digest,
        )

    def is_effective_at(self, observed_at: datetime) -> bool:
        observed_utc = _as_utc(observed_at, "observed_at")
        return (
            (self.effective_from is None or self.effective_from <= observed_utc)
            and (self.effective_to is None or observed_utc < self.effective_to)
        )


@dataclass(frozen=True, slots=True)
class ProductMappingResolution:
    mapping_status: ProductMappingStatus
    internal_sku: str | None
    mapping_ids: tuple[str, ...] = ()
    candidate_internal_skus: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledProductMappings:
    records: tuple[ProductMappingRecord, ...]
    source_workbook_sha256: str
    mapping_version: str
    immutable_json: bytes

    def resolve(
        self,
        *,
        platform_name: str,
        platform_product_name: str,
        grade: str,
        observed_at: datetime,
        account_id: str = "",
        platform_product_identity: Mapping[str, object] | str | None = None,
        platform_product_identity_digest: str = "",
    ) -> ProductMappingResolution:
        identity_key = (
            normalize_mapping_text(platform_name),
            normalize_mapping_text(platform_product_name),
            normalize_mapping_text(grade),
        )
        normalized_account = str(account_id or "").strip()
        supplied_digest = str(platform_product_identity_digest or "").strip()
        if supplied_digest and platform_product_identity is not None:
            raise ProductMappingError(
                "provide platform_product_identity or its digest, not both"
            )
        if supplied_digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", supplied_digest):
            raise ProductMappingError("platform_product_identity_digest is invalid")
        product_identity_digest = supplied_digest
        if platform_product_identity is not None:
            try:
                product_identity_digest = identity_digest(platform_product_identity)
            except PlatformProductIdentityError as exc:
                raise ProductMappingError(str(exc)) from exc
        matches = tuple(
            record
            for record in self.records
            if (
                record.platform_product_identity_digest == product_identity_digest
                if product_identity_digest
                else record.identity_key == identity_key
            )
            and (not normalized_account or record.account_id == normalized_account)
            and record.is_effective_at(observed_at)
        )
        if not matches:
            return ProductMappingResolution(ProductMappingStatus.UNMAPPED, None)

        ids = tuple(sorted(record.mapping_id for record in matches))
        enabled = tuple(
            record
            for record in matches
            if record.mapping_status is not ProductMappingStatus.DISABLED
        )
        if not enabled:
            candidate_skus = tuple(
                sorted(
                    {
                        sku
                        for record in matches
                        for sku in (
                            record.internal_sku,
                            record.candidate_internal_sku,
                            *record.candidate_internal_skus,
                        )
                        if sku
                    }
                )
            )
            return ProductMappingResolution(
                ProductMappingStatus.DISABLED,
                None,
                ids,
                candidate_skus,
            )
        candidate_skus = tuple(
            sorted(
                {
                    sku
                    for record in enabled
                    for sku in (
                        record.internal_sku,
                        record.candidate_internal_sku,
                        *record.candidate_internal_skus,
                    )
                    if sku
                }
            )
        )
        if any(
            record.mapping_status is ProductMappingStatus.AMBIGUOUS
            for record in enabled
        ):
            return ProductMappingResolution(
                ProductMappingStatus.AMBIGUOUS,
                None,
                ids,
                candidate_skus,
            )

        verified_skus = {
            record.internal_sku
            for record in enabled
            if record.mapping_status is ProductMappingStatus.VERIFIED
            and record.internal_sku
        }
        if len(verified_skus) == 1 and all(
            record.mapping_status is ProductMappingStatus.VERIFIED
            for record in enabled
        ):
            return ProductMappingResolution(
                ProductMappingStatus.VERIFIED,
                next(iter(verified_skus)),
                ids,
                tuple(sorted(verified_skus)),
            )
        if len(verified_skus) > 1 or any(
            record.mapping_status is ProductMappingStatus.VERIFIED
            for record in enabled
        ):
            return ProductMappingResolution(
                ProductMappingStatus.AMBIGUOUS,
                None,
                ids,
                candidate_skus,
            )
        return ProductMappingResolution(
            ProductMappingStatus.UNMAPPED,
            None,
            ids,
            candidate_skus,
        )


def normalize_mapping_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


def compile_product_mapping_workbook(path: Path) -> CompiledProductMappings:
    source_path = Path(path)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    rows = load_table_records("platform_mappings", source_path)
    return compile_product_mapping_rows(
        rows,
        source_workbook_sha256=source_sha256,
    )


def compile_product_mapping_rows(
    rows: Iterable[dict[str, object]],
    *,
    source_workbook_sha256: str,
) -> CompiledProductMappings:
    records: list[ProductMappingRecord] = []
    for row_number, row in enumerate(rows, start=2):
        mapping_kind = str(row.get("mapping_kind") or "").strip().upper()
        has_product_identity = any(
            str(row.get(field) or "").strip()
            for field in (
                "platform_product_name",
                "grade",
                "internal_sku",
                "candidate_internal_sku",
            )
        )
        if mapping_kind == "PLATFORM" or (
            not mapping_kind and not has_product_identity
        ):
            continue
        if mapping_kind not in {"", "PRODUCT"}:
            raise ProductMappingError(
                f"platform_mappings row {row_number}: "
                f"unsupported mapping_kind '{mapping_kind}'"
            )
        records.append(_record_from_row(row, row_number))

    _validate_records(records)
    canonical_records = [
        _record_payload(record)
        for record in sorted(
            records,
            key=lambda record: (
                record.authority_identity_key,
                record.effective_from or datetime.min.replace(
                    tzinfo=timezone.utc
                ),
                record.mapping_id,
            ),
        )
    ]
    envelope = {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "source_workbook_sha256": source_workbook_sha256,
        "records": canonical_records,
    }
    immutable_json = (
        json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    mapping_version = hashlib.sha256(immutable_json).hexdigest()
    return CompiledProductMappings(
        records=tuple(records),
        source_workbook_sha256=source_workbook_sha256,
        mapping_version=mapping_version,
        immutable_json=immutable_json,
    )


def write_immutable_product_mapping(
    compiled: CompiledProductMappings,
    output_path: Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("wb") as handle:
            handle.write(compiled.immutable_json)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return path


def _record_from_row(
    row: dict[str, object],
    row_number: int,
) -> ProductMappingRecord:
    mapping_id = _required_text(row, "mapping_id", row_number)
    platform_name = _required_text(row, "platform_name", row_number)
    platform_product_name = _required_text(
        row,
        "platform_product_name",
        row_number,
    )
    grade = _required_text(row, "grade", row_number)
    normalized_name = normalize_mapping_text(
        row.get("normalized_platform_product_name")
        or platform_product_name
    )
    try:
        status = ProductMappingStatus(
            str(row.get("mapping_status") or "").strip().upper()
        )
    except ValueError as exc:
        raise ProductMappingError(
            f"platform_mappings row {row_number}: mapping_status must be "
            "VERIFIED, UNMAPPED, AMBIGUOUS or DISABLED"
        ) from exc
    internal_sku = str(row.get("internal_sku") or "").strip() or None
    candidate_internal_sku = (
        str(row.get("candidate_internal_sku") or "").strip() or None
    )
    candidate_internal_skus = _candidate_skus(row, candidate_internal_sku)
    if status is ProductMappingStatus.VERIFIED and internal_sku is None:
        raise ProductMappingError(
            f"platform_mappings row {row_number}: "
            "VERIFIED requires internal_sku"
        )
    if status is not ProductMappingStatus.VERIFIED and internal_sku is not None:
        raise ProductMappingError(
            f"platform_mappings row {row_number}: "
            f"{status.value} must not set internal_sku"
        )
    if (
        status is ProductMappingStatus.VERIFIED
        and candidate_internal_skus
    ):
        raise ProductMappingError(
            f"platform_mappings row {row_number}: "
            "VERIFIED must not set candidate_internal_skus"
        )
    effective_from = _optional_datetime(
        row.get("effective_from"),
        "effective_from",
        row_number,
    )
    effective_to = _optional_datetime(
        row.get("effective_to"),
        "effective_to",
        row_number,
    )
    if (
        effective_from is not None
        and effective_to is not None
        and effective_to <= effective_from
    ):
        raise ProductMappingError(
            f"platform_mappings row {row_number}: effective_to must be "
            "later than effective_from"
        )
    account_id = str(row.get("account_id") or "").strip()
    identity_input = row.get("platform_product_identity_json")
    if not identity_input:
        identity_input = legacy_workbook_identity(
            platform_product_id=row.get("platform_product_id"),
            normalized_platform_product_name=normalized_name,
            normalized_grade=normalize_mapping_text(grade),
        )
    try:
        identity_json = canonical_identity_json(identity_input)
        calculated_identity_digest = identity_digest(identity_json)
    except PlatformProductIdentityError as exc:
        raise ProductMappingError(
            f"platform_mappings row {row_number}: {exc}"
        ) from exc
    supplied_identity_digest = str(
        row.get("platform_product_identity_digest") or ""
    ).strip()
    if supplied_identity_digest and supplied_identity_digest != calculated_identity_digest:
        raise ProductMappingError(
            f"platform_mappings row {row_number}: platform identity digest mismatch"
        )
    return ProductMappingRecord(
        mapping_id=mapping_id,
        platform_name=platform_name,
        platform_product_name=platform_product_name,
        normalized_platform_product_name=normalized_name,
        grade=grade,
        internal_sku=internal_sku,
        candidate_internal_sku=candidate_internal_sku,
        mapping_status=status,
        effective_from=effective_from,
        effective_to=effective_to,
        remark=str(row.get("remark") or "").strip(),
        account_id=account_id,
        platform_product_identity_json=identity_json,
        platform_product_identity_digest=calculated_identity_digest,
        candidate_internal_skus=candidate_internal_skus,
    )


def _validate_records(records: list[ProductMappingRecord]) -> None:
    seen_ids: set[str] = set()
    for record in records:
        if record.mapping_id in seen_ids:
            raise ProductMappingError(
                f"duplicate product mapping_id '{record.mapping_id}'"
            )
        seen_ids.add(record.mapping_id)

    verified = [
        record
        for record in records
        if record.mapping_status is ProductMappingStatus.VERIFIED
    ]
    for index, left in enumerate(verified):
        for right in verified[index + 1 :]:
            if (
                left.authority_identity_key == right.authority_identity_key
                and left.internal_sku != right.internal_sku
                and _ranges_overlap(left, right)
            ):
                raise ProductMappingError(
                    "overlapping effective ranges map identity "
                    f"{left.identity_key!r} to multiple SKUs: "
                    f"{left.mapping_id}, {right.mapping_id}"
                )


def _ranges_overlap(
    left: ProductMappingRecord,
    right: ProductMappingRecord,
) -> bool:
    left_start = left.effective_from or datetime.min.replace(
        tzinfo=timezone.utc
    )
    right_start = right.effective_from or datetime.min.replace(
        tzinfo=timezone.utc
    )
    left_end = left.effective_to or datetime.max.replace(tzinfo=timezone.utc)
    right_end = right.effective_to or datetime.max.replace(tzinfo=timezone.utc)
    return left_start < right_end and right_start < left_end


def _record_payload(record: ProductMappingRecord) -> dict[str, object]:
    return {
        "mapping_id": record.mapping_id,
        "platform_name": record.platform_name,
        "platform_product_name": record.platform_product_name,
        "normalized_platform_product_name": (
            record.normalized_platform_product_name
        ),
        "grade": record.grade,
        "internal_sku": record.internal_sku,
        "candidate_internal_sku": record.candidate_internal_sku,
        "mapping_status": record.mapping_status.value,
        "effective_from": _datetime_text(record.effective_from),
        "effective_to": _datetime_text(record.effective_to),
        "remark": record.remark,
        "account_id": record.account_id,
        "platform_product_identity_json": record.platform_product_identity_json,
        "platform_product_identity_digest": record.platform_product_identity_digest,
        "candidate_internal_skus": list(record.candidate_internal_skus),
    }


def _candidate_skus(
    row: Mapping[str, object], legacy_candidate: str | None
) -> tuple[str, ...]:
    value = row.get("candidate_internal_skus")
    if value is None:
        value = row.get("candidate_internal_skus_json")
    if value in (None, ""):
        return (legacy_candidate,) if legacy_candidate else ()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in value.split(",") if item.strip()]
    else:
        parsed = value
    if not isinstance(parsed, (list, tuple)):
        raise ProductMappingError("candidate_internal_skus must be a list")
    return tuple(
        sorted({str(item).strip().upper() for item in parsed if str(item).strip()})
    )


def _required_text(
    row: dict[str, object],
    field_name: str,
    row_number: int,
) -> str:
    value = str(row.get(field_name) or "").strip()
    if not value:
        raise ProductMappingError(
            f"platform_mappings row {row_number}: {field_name} is required"
        )
    return value


def _optional_datetime(
    value: object,
    field_name: str,
    row_number: int,
) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise ProductMappingError(
                f"platform_mappings row {row_number}: "
                f"{field_name} must be an ISO datetime"
            ) from exc
    try:
        return _as_utc(parsed, field_name)
    except ValueError as exc:
        raise ProductMappingError(
            f"platform_mappings row {row_number}: {exc}"
        ) from exc


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
