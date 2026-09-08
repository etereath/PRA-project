"""Runtime Product/Mapping authority and its additive v19 schema.

The repository deliberately keeps Product Master separate from the inventory
ledger.  A missing inventory balance is returned as ``NOT_INITIALIZED`` and is
never converted to a real zero balance.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Mapping

from app.models import Product
from app.services.product_mapping import (
    CompiledProductMappings,
    compile_product_mapping_rows,
)

if TYPE_CHECKING:
    from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


SCHEMA_V19_SQL = (
    """
    CREATE TABLE IF NOT EXISTS master_data_authority_state (
        authority_key TEXT PRIMARY KEY CHECK (authority_key = 'PRODUCT_MAPPING'),
        authority_mode TEXT NOT NULL CHECK (
            authority_mode IN ('PRE_CUTOVER', 'DB_AUTHORITY')
        ),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        product_snapshot_sha256 TEXT NOT NULL DEFAULT '',
        mapping_snapshot_sha256 TEXT NOT NULL DEFAULT '',
        cutover_generation INTEGER,
        cutover_event_sequence INTEGER,
        cutover_at TEXT,
        cutover_by TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        CHECK (
            (authority_mode = 'PRE_CUTOVER')
            OR (
                cutover_generation IS NOT NULL
                AND cutover_event_sequence IS NOT NULL
                AND cutover_at IS NOT NULL
                AND trim(cutover_by) <> ''
                AND product_snapshot_sha256 GLOB 'sha256:*'
                AND mapping_snapshot_sha256 GLOB 'sha256:*'
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS product_catalog (
        internal_sku TEXT PRIMARY KEY CHECK (trim(internal_sku) <> ''),
        product_name TEXT NOT NULL CHECK (trim(product_name) <> ''),
        grade TEXT NOT NULL CHECK (trim(grade) <> ''),
        stem_length TEXT NOT NULL CHECK (trim(stem_length) <> ''),
        unit TEXT NOT NULL CHECK (trim(unit) <> ''),
        base_cost TEXT NOT NULL CHECK (trim(base_cost) <> ''),
        sale_enabled INTEGER NOT NULL CHECK (sale_enabled IN (0, 1)),
        remark TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL CHECK (trim(source_type) <> ''),
        source_ref TEXT NOT NULL CHECK (trim(source_ref) <> ''),
        source_sha256 TEXT NOT NULL CHECK (source_sha256 GLOB 'sha256:*'),
        version INTEGER NOT NULL CHECK (version >= 1),
        authority_generation INTEGER NOT NULL CHECK (authority_generation >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_product_catalog_scope
    ON product_catalog(product_name, grade, sale_enabled, internal_sku)
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_product_mappings (
        mapping_id TEXT PRIMARY KEY CHECK (trim(mapping_id) <> ''),
        platform_name TEXT NOT NULL CHECK (trim(platform_name) <> ''),
        account_id TEXT NOT NULL CHECK (trim(account_id) <> ''),
        platform_product_identity_json TEXT NOT NULL
            CHECK (json_valid(platform_product_identity_json)),
        platform_product_identity_digest TEXT NOT NULL
            CHECK (platform_product_identity_digest GLOB 'sha256:*'),
        platform_product_name TEXT NOT NULL DEFAULT '',
        normalized_platform_product_name TEXT NOT NULL DEFAULT '',
        grade TEXT NOT NULL DEFAULT '',
        internal_sku TEXT,
        candidate_internal_skus_json TEXT NOT NULL DEFAULT '[]'
            CHECK (json_valid(candidate_internal_skus_json)),
        mapping_status TEXT NOT NULL CHECK (
            mapping_status IN ('VERIFIED', 'UNMAPPED', 'AMBIGUOUS', 'DISABLED')
        ),
        effective_from TEXT,
        effective_to TEXT,
        last_verified_at TEXT,
        remark TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL CHECK (trim(source_type) <> ''),
        source_ref TEXT NOT NULL CHECK (trim(source_ref) <> ''),
        source_sha256 TEXT NOT NULL CHECK (source_sha256 GLOB 'sha256:*'),
        version INTEGER NOT NULL CHECK (version >= 1),
        authority_generation INTEGER NOT NULL CHECK (authority_generation >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (internal_sku) REFERENCES product_catalog(internal_sku),
        CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from),
        CHECK (
            (mapping_status = 'VERIFIED'
                AND internal_sku IS NOT NULL
                AND json_array_length(candidate_internal_skus_json) = 0)
            OR
            (mapping_status = 'AMBIGUOUS'
                AND internal_sku IS NULL
                AND json_array_length(candidate_internal_skus_json) > 0)
            OR
            (mapping_status IN ('UNMAPPED', 'DISABLED')
                AND internal_sku IS NULL)
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_platform_product_mappings_identity
    ON platform_product_mappings(
        platform_name, account_id, platform_product_identity_digest,
        mapping_status, effective_from, effective_to
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_platform_product_mappings_sku
    ON platform_product_mappings(
        internal_sku, platform_name, account_id, mapping_status
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS master_data_authority_events (
        event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE CHECK (trim(event_id) <> ''),
        event_type TEXT NOT NULL CHECK (event_type IN (
            'IMPORT', 'CUTOVER', 'PRODUCT_MUTATION', 'MAPPING_MUTATION',
            'PLATFORM_SIDE_EFFECT', 'ROLLBACK', 'RECUTOVER'
        )),
        authority_generation INTEGER NOT NULL CHECK (authority_generation >= 1),
        actor TEXT NOT NULL CHECK (trim(actor) <> ''),
        idempotency_key TEXT NOT NULL CHECK (trim(idempotency_key) <> ''),
        request_sha256 TEXT NOT NULL CHECK (request_sha256 GLOB 'sha256:*'),
        source_ref TEXT NOT NULL CHECK (trim(source_ref) <> ''),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        UNIQUE(actor, idempotency_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_master_data_authority_events_generation
    ON master_data_authority_events(authority_generation, event_type, created_at)
    """,
    """
    CREATE TRIGGER IF NOT EXISTS master_data_authority_events_no_update
    BEFORE UPDATE ON master_data_authority_events
    BEGIN SELECT RAISE(ABORT, 'master data authority event is immutable'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS master_data_authority_events_no_delete
    BEFORE DELETE ON master_data_authority_events
    BEGIN SELECT RAISE(ABORT, 'master data authority event is immutable'); END
    """,
)


class RuntimeMasterDataError(ValueError):
    """Raised when Runtime master data cannot be trusted or changed safely."""


@dataclass(frozen=True, slots=True)
class MasterDataAuthorityState:
    authority_mode: str
    generation: int
    product_snapshot_sha256: str
    mapping_snapshot_sha256: str
    cutover_generation: int | None
    cutover_event_sequence: int | None
    cutover_at: str | None
    cutover_by: str


@dataclass(frozen=True, slots=True)
class ProductMasterRecord:
    product: Product
    version: int
    authority_generation: int
    inventory_status: str


@dataclass(frozen=True, slots=True)
class PlatformMappingRecord:
    mapping_id: str
    platform_name: str
    account_id: str
    platform_product_identity_json: str
    platform_product_identity_digest: str
    platform_product_name: str
    normalized_platform_product_name: str
    grade: str
    internal_sku: str | None
    candidate_internal_skus: tuple[str, ...]
    mapping_status: str
    effective_from: str | None
    effective_to: str | None
    last_verified_at: str | None
    remark: str
    version: int
    authority_generation: int


class RuntimeMasterDataRepository:
    """Narrow, account-aware Product/Mapping access over the Runtime DB."""

    def __init__(self, runtime_repository: "SQLiteRuntimeRepository") -> None:
        self.runtime = runtime_repository

    def authority_state(self, *, connection=None) -> MasterDataAuthorityState:
        query = (
            "SELECT authority_mode, generation, product_snapshot_sha256, "
            "mapping_snapshot_sha256, cutover_generation, "
            "cutover_event_sequence, cutover_at, cutover_by "
            "FROM master_data_authority_state WHERE authority_key = 'PRODUCT_MAPPING'"
        )
        if connection is not None:
            row = connection.execute(query).fetchone()
        else:
            with closing(self.runtime.connect_read()) as opened:
                row = opened.execute(query).fetchone()
        if row is None:
            raise RuntimeMasterDataError("Runtime master-data authority state is missing.")
        return MasterDataAuthorityState(
            authority_mode=str(row["authority_mode"]),
            generation=int(row["generation"]),
            product_snapshot_sha256=str(row["product_snapshot_sha256"] or ""),
            mapping_snapshot_sha256=str(row["mapping_snapshot_sha256"] or ""),
            cutover_generation=(
                int(row["cutover_generation"])
                if row["cutover_generation"] is not None
                else None
            ),
            cutover_event_sequence=(
                int(row["cutover_event_sequence"])
                if row["cutover_event_sequence"] is not None
                else None
            ),
            cutover_at=(str(row["cutover_at"]) if row["cutover_at"] else None),
            cutover_by=str(row["cutover_by"] or ""),
        )

    def list_product_records(self, *, connection=None) -> tuple[ProductMasterRecord, ...]:
        query = """
            SELECT p.internal_sku, p.product_name, p.grade, p.stem_length,
                   p.unit, p.base_cost, p.sale_enabled, p.remark, p.version,
                   p.authority_generation, b.current_qty
            FROM product_catalog AS p
            LEFT JOIN inventory_balances AS b ON b.internal_sku = p.internal_sku
            ORDER BY p.internal_sku
        """
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime.connect_read()) as opened:
                rows = opened.execute(query).fetchall()
        records: list[ProductMasterRecord] = []
        for row in rows:
            initialized = row["current_qty"] is not None
            product = Product(
                internal_sku=str(row["internal_sku"]),
                product_name=str(row["product_name"]),
                grade=str(row["grade"]),
                stem_length=str(row["stem_length"]),
                unit=str(row["unit"]),
                base_cost=Decimal(str(row["base_cost"])),
                current_stock=(int(row["current_qty"]) if initialized else None),
                sale_enabled=bool(row["sale_enabled"]),
                remark=str(row["remark"] or ""),
                metadata={"inventory_status": "INITIALIZED" if initialized else "NOT_INITIALIZED"},
            )
            records.append(
                ProductMasterRecord(
                    product=product,
                    version=int(row["version"]),
                    authority_generation=int(row["authority_generation"]),
                    inventory_status=("INITIALIZED" if initialized else "NOT_INITIALIZED"),
                )
            )
        return tuple(records)

    def list_products(self, *, connection=None) -> tuple[Product, ...]:
        return tuple(
            record.product for record in self.list_product_records(connection=connection)
        )

    def get_product_record(
        self, internal_sku: str, *, connection=None
    ) -> ProductMasterRecord | None:
        sku = str(internal_sku or "").strip().upper()
        return next(
            (
                record
                for record in self.list_product_records(connection=connection)
                if record.product.internal_sku == sku
            ),
            None,
        )

    def list_mapping_records(self, *, connection=None) -> tuple[PlatformMappingRecord, ...]:
        query = """
            SELECT mapping_id, platform_name, account_id,
                   platform_product_identity_json,
                   platform_product_identity_digest, platform_product_name,
                   normalized_platform_product_name, grade, internal_sku,
                   candidate_internal_skus_json, mapping_status,
                   effective_from, effective_to, last_verified_at, remark,
                   version, authority_generation
            FROM platform_product_mappings
            ORDER BY platform_name, account_id,
                     platform_product_identity_digest, effective_from, mapping_id
        """
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime.connect_read()) as opened:
                rows = opened.execute(query).fetchall()
        return tuple(
            PlatformMappingRecord(
                mapping_id=str(row["mapping_id"]),
                platform_name=str(row["platform_name"]),
                account_id=str(row["account_id"]),
                platform_product_identity_json=str(row["platform_product_identity_json"]),
                platform_product_identity_digest=str(row["platform_product_identity_digest"]),
                platform_product_name=str(row["platform_product_name"] or ""),
                normalized_platform_product_name=str(
                    row["normalized_platform_product_name"] or ""
                ),
                grade=str(row["grade"] or ""),
                internal_sku=(str(row["internal_sku"]) if row["internal_sku"] else None),
                candidate_internal_skus=tuple(
                    str(value) for value in json.loads(row["candidate_internal_skus_json"])
                ),
                mapping_status=str(row["mapping_status"]),
                effective_from=(str(row["effective_from"]) if row["effective_from"] else None),
                effective_to=(str(row["effective_to"]) if row["effective_to"] else None),
                last_verified_at=(
                    str(row["last_verified_at"]) if row["last_verified_at"] else None
                ),
                remark=str(row["remark"] or ""),
                version=int(row["version"]),
                authority_generation=int(row["authority_generation"]),
            )
            for row in rows
        )

    def compiled_mappings(
        self, *, account_id: str | None = None, connection=None
    ) -> CompiledProductMappings:
        records = self.list_mapping_records(connection=connection)
        if account_id is not None:
            normalized_account = _required_text(account_id, "account_id")
            records = tuple(
                record for record in records if record.account_id == normalized_account
            )
        rows = tuple(
            {
                "mapping_id": record.mapping_id,
                "platform_name": record.platform_name,
                "account_id": record.account_id,
                "platform_product_identity_json": record.platform_product_identity_json,
                "platform_product_identity_digest": record.platform_product_identity_digest,
                "platform_product_name": record.platform_product_name,
                "normalized_platform_product_name": record.normalized_platform_product_name,
                "grade": record.grade,
                "internal_sku": record.internal_sku,
                "candidate_internal_skus": list(record.candidate_internal_skus),
                "mapping_status": record.mapping_status,
                "effective_from": record.effective_from,
                "effective_to": record.effective_to,
                "last_verified_at": record.last_verified_at,
                "remark": record.remark,
            }
            for record in records
        )
        source_digest = _payload_sha256(rows)
        return compile_product_mapping_rows(
            rows,
            source_workbook_sha256=source_digest.removeprefix("sha256:"),
        )

    def product_snapshot_sha256(self, *, connection=None) -> str:
        query = """
            SELECT internal_sku, product_name, grade, stem_length, unit,
                   base_cost, sale_enabled, remark, version,
                   authority_generation
            FROM product_catalog ORDER BY internal_sku
        """
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime.connect_read()) as opened:
                rows = opened.execute(query).fetchall()
        return _rows_sha256(rows)

    def mapping_snapshot_sha256(self, *, connection=None) -> str:
        query = """
            SELECT mapping_id, platform_name, account_id,
                   platform_product_identity_json,
                   platform_product_identity_digest, platform_product_name,
                   normalized_platform_product_name, grade, internal_sku,
                   candidate_internal_skus_json, mapping_status,
                   effective_from, effective_to, last_verified_at, remark,
                   version, authority_generation
            FROM platform_product_mappings
            ORDER BY platform_name, account_id,
                     platform_product_identity_digest, effective_from, mapping_id
        """
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime.connect_read()) as opened:
                rows = opened.execute(query).fetchall()
        return _rows_sha256(rows)

    def product_cost_snapshot(
        self, internal_sku: str, *, connection=None
    ) -> tuple[Decimal, str]:
        if connection is None:
            with closing(self.runtime.connect_read()) as opened:
                opened.execute("BEGIN")
                return self.product_cost_snapshot(internal_sku, connection=opened)
        state = self.authority_state(connection=connection)
        record = self.get_product_record(internal_sku, connection=connection)
        if record is None:
            raise RuntimeMasterDataError("Product is not present in Runtime master data.")
        digest = self.product_snapshot_sha256(connection=connection)
        return (
            record.product.base_cost,
            "runtime-db:product_catalog:"
            + f"generation:{state.generation}:{digest}:{record.product.internal_sku}",
        )


def canonical_identity_json(value: Mapping[str, object] | str) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeMasterDataError(
                "platform_product_identity_json must be canonical JSON."
            ) from exc
    else:
        parsed = dict(value)
    if not isinstance(parsed, dict) or not parsed:
        raise RuntimeMasterDataError("platform_product_identity must be a non-empty object.")
    normalized = {
        str(key).strip(): str(item).strip()
        for key, item in parsed.items()
        if str(key).strip() and str(item).strip()
    }
    if not normalized:
        raise RuntimeMasterDataError("platform_product_identity has no usable fields.")
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def identity_digest(value: Mapping[str, object] | str) -> str:
    canonical = canonical_identity_json(value)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rows_sha256(rows: Iterable[object]) -> str:
    payload = tuple(
        {key: row[key] for key in row.keys()}  # type: ignore[index, union-attr]
        for row in rows
    )
    return _payload_sha256(payload)


def _payload_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeMasterDataError(f"{field_name} is required.")
    return text


def utc_text(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise RuntimeMasterDataError("timestamp must be timezone-aware.")
    return current.astimezone(timezone.utc).isoformat(timespec="seconds")
