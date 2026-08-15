from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from app.models import Product
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.product_mapping import (
    CompiledProductMappings,
    compile_product_mapping_rows,
)


class RuntimeMasterDataError(ValueError):
    """Raised when Runtime master data cannot be trusted or seeded safely."""


class RuntimeMasterDataRepository:
    """Narrow Runtime DB authority for products and platform mappings."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime = runtime_repository

    def list_products(self, *, connection=None) -> tuple[Product, ...]:
        query = """
            SELECT product.internal_sku, product.product_name, product.grade,
                   product.stem_length, product.unit, product.base_cost,
                   product.sale_enabled, product.remark,
                   COALESCE(balance.current_qty, 0) AS current_stock
            FROM product_catalog AS product
            LEFT JOIN inventory_balances AS balance
              ON balance.internal_sku = product.internal_sku
            ORDER BY product.internal_sku
        """
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime.connect_read()) as opened:
                rows = opened.execute(query).fetchall()
        return tuple(_row_to_product(row) for row in rows)

    def get_product(self, internal_sku: str, *, connection=None) -> Product | None:
        sku = str(internal_sku or "").strip().upper()
        if not sku:
            return None
        query = """
            SELECT product.internal_sku, product.product_name, product.grade,
                   product.stem_length, product.unit, product.base_cost,
                   product.sale_enabled, product.remark,
                   COALESCE(balance.current_qty, 0) AS current_stock
            FROM product_catalog AS product
            LEFT JOIN inventory_balances AS balance
              ON balance.internal_sku = product.internal_sku
            WHERE product.internal_sku = ?
        """
        if connection is not None:
            row = connection.execute(query, (sku,)).fetchone()
        else:
            with closing(self.runtime.connect_read()) as opened:
                row = opened.execute(query, (sku,)).fetchone()
        return _row_to_product(row) if row is not None else None

    def list_mapping_rows(self, *, connection=None) -> tuple[dict[str, object], ...]:
        query = """
            SELECT mapping_id, mapping_kind, internal_sku,
                   candidate_internal_sku, platform_name,
                   platform_product_id, platform_product_name,
                   normalized_platform_product_name, grade, search_keyword,
                   mapping_status, effective_from, effective_to,
                   last_verified_at, remark
            FROM platform_product_mappings
            ORDER BY platform_name, mapping_kind, mapping_id
        """
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime.connect_read()) as opened:
                rows = opened.execute(query).fetchall()
        return tuple({key: row[key] for key in row.keys()} for row in rows)

    def compiled_mappings(self, *, connection=None) -> CompiledProductMappings:
        rows = self.list_mapping_rows(connection=connection)
        source_sha256 = _payload_sha256(rows).split(":", 1)[1]
        return compile_product_mapping_rows(
            rows,
            source_workbook_sha256=source_sha256,
        )

    def product_snapshot_sha256(self, *, connection=None) -> str:
        query = """
            SELECT internal_sku, product_name, grade, stem_length, unit,
                   base_cost, sale_enabled, remark, version
            FROM product_catalog
            ORDER BY internal_sku
        """
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime.connect_read()) as opened:
                rows = opened.execute(query).fetchall()
        return _payload_sha256(
            tuple({key: row[key] for key in row.keys()} for row in rows)
        )

    def mapping_snapshot_sha256(self, *, connection=None) -> str:
        return _payload_sha256(self.list_mapping_rows(connection=connection))

    def product_cost_snapshot(
        self,
        internal_sku: str,
        *,
        connection=None,
    ) -> tuple[Decimal, str]:
        product = self.get_product(internal_sku, connection=connection)
        if product is None:
            raise RuntimeMasterDataError("Product is not present in Runtime master data.")
        snapshot_sha256 = self.product_snapshot_sha256(connection=connection)
        return (
            product.base_cost,
            f"runtime-db:product_catalog:{snapshot_sha256}:{product.internal_sku}",
        )

    def seed(
        self,
        products: Sequence[Product],
        mapping_rows: Iterable[Mapping[str, object]],
        *,
        product_source_ref: str,
        product_source_sha256: str,
        mapping_source_ref: str,
        mapping_source_sha256: str,
        actor: str,
    ) -> dict[str, object]:
        normalized_products = _validated_products(products)
        normalized_mappings = tuple(_normalized_mapping_row(row) for row in mapping_rows)
        compile_product_mapping_rows(
            normalized_mappings,
            source_workbook_sha256=_required_sha256(
                mapping_source_sha256,
                "mapping_source_sha256",
            ).split(":", 1)[1],
        )
        product_source = _required_text(product_source_ref, "product_source_ref")
        mapping_source = _required_text(mapping_source_ref, "mapping_source_ref")
        product_sha = _required_sha256(product_source_sha256, "product_source_sha256")
        mapping_sha = _required_sha256(mapping_source_sha256, "mapping_source_sha256")
        normalized_actor = _required_text(actor, "actor")
        now_text = datetime.now(timezone.utc).isoformat(timespec="seconds")

        connection = self.runtime.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = int(
                connection.execute("SELECT COUNT(*) FROM product_catalog").fetchone()[0]
            ) + int(
                connection.execute(
                    "SELECT COUNT(*) FROM platform_product_mappings"
                ).fetchone()[0]
            )
            if existing:
                raise RuntimeMasterDataError(
                    "Runtime master data is not empty; seed is single-use."
                )
            connection.executemany(
                """
                INSERT INTO product_catalog(
                    internal_sku, product_name, grade, stem_length, unit,
                    base_cost, sale_enabled, remark,
                    source_type, source_ref, source_sha256,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ONE_TIME_IMPORT', ?, ?, 1, ?, ?)
                """,
                [
                    (
                        item.internal_sku.upper(),
                        item.product_name,
                        item.grade,
                        item.stem_length,
                        item.unit,
                        _decimal_text(item.base_cost),
                        int(item.sale_enabled),
                        item.remark,
                        f"{product_source};actor={normalized_actor}",
                        product_sha,
                        now_text,
                        now_text,
                    )
                    for item in normalized_products
                ],
            )
            connection.executemany(
                """
                INSERT INTO platform_product_mappings(
                    mapping_id, mapping_kind, platform_name,
                    platform_product_id, platform_product_name,
                    normalized_platform_product_name, grade,
                    internal_sku, candidate_internal_sku, search_keyword,
                    mapping_status, effective_from, effective_to,
                    last_verified_at, remark,
                    source_type, source_ref, source_sha256,
                    version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'ONE_TIME_IMPORT', ?, ?, 1, ?, ?
                )
                """,
                [
                    (
                        row["mapping_id"],
                        row["mapping_kind"],
                        row["platform_name"],
                        row["platform_product_id"],
                        row["platform_product_name"],
                        row["normalized_platform_product_name"],
                        row["grade"],
                        row["internal_sku"],
                        row["candidate_internal_sku"],
                        row["search_keyword"],
                        row["mapping_status"],
                        row["effective_from"],
                        row["effective_to"],
                        row["last_verified_at"],
                        row["remark"],
                        f"{mapping_source};actor={normalized_actor}",
                        mapping_sha,
                        now_text,
                        now_text,
                    )
                    for row in normalized_mappings
                ],
            )
            product_snapshot = self.product_snapshot_sha256(connection=connection)
            mapping_snapshot = self.mapping_snapshot_sha256(connection=connection)
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return {
            "product_count": len(normalized_products),
            "mapping_count": len(normalized_mappings),
            "product_snapshot_sha256": product_snapshot,
            "mapping_snapshot_sha256": mapping_snapshot,
        }


def _row_to_product(row) -> Product:
    try:
        base_cost = Decimal(str(row["base_cost"]))
    except (InvalidOperation, TypeError) as exc:
        raise RuntimeMasterDataError("Stored product base_cost is invalid.") from exc
    return Product(
        internal_sku=str(row["internal_sku"]),
        product_name=str(row["product_name"]),
        grade=str(row["grade"]),
        stem_length=str(row["stem_length"]),
        unit=str(row["unit"]),
        base_cost=base_cost,
        current_stock=int(row["current_stock"]),
        sale_enabled=bool(row["sale_enabled"]),
        remark=str(row["remark"] or ""),
    )


def _validated_products(products: Sequence[Product]) -> tuple[Product, ...]:
    normalized = tuple(products)
    if not normalized:
        raise RuntimeMasterDataError("At least one product is required.")
    seen: set[str] = set()
    for item in normalized:
        sku = str(item.internal_sku or "").strip().upper()
        if not sku or sku in seen:
            raise RuntimeMasterDataError("Product SKU is blank or duplicated.")
        seen.add(sku)
        if not all(
            str(value or "").strip()
            for value in (
                item.product_name,
                item.grade,
                item.stem_length,
                item.unit,
            )
        ):
            raise RuntimeMasterDataError(f"Product metadata is incomplete: {sku}")
        if not item.base_cost.is_finite() or item.base_cost < 0:
            raise RuntimeMasterDataError(f"Product base_cost is invalid: {sku}")
    return normalized


def _normalized_mapping_row(row: Mapping[str, object]) -> dict[str, object]:
    kind = str(row.get("mapping_kind") or "PRODUCT").strip().upper()
    status = str(row.get("mapping_status") or "").strip().upper()
    return {
        "mapping_id": _required_text(row.get("mapping_id"), "mapping_id"),
        "mapping_kind": kind,
        "platform_name": _required_text(row.get("platform_name"), "platform_name"),
        "platform_product_id": str(row.get("platform_product_id") or "").strip(),
        "platform_product_name": str(row.get("platform_product_name") or "").strip(),
        "normalized_platform_product_name": str(
            row.get("normalized_platform_product_name") or ""
        ).strip(),
        "grade": str(row.get("grade") or "").strip(),
        "internal_sku": str(row.get("internal_sku") or "").strip().upper() or None,
        "candidate_internal_sku": (
            str(row.get("candidate_internal_sku") or "").strip().upper() or None
        ),
        "search_keyword": str(row.get("search_keyword") or "").strip(),
        "mapping_status": status,
        "effective_from": str(row.get("effective_from") or "").strip() or None,
        "effective_to": str(row.get("effective_to") or "").strip() or None,
        "last_verified_at": str(row.get("last_verified_at") or "").strip() or None,
        "remark": str(row.get("remark") or ""),
    }


def _payload_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeMasterDataError(f"{field} is required.")
    return text


def _required_sha256(value: object, field: str) -> str:
    text = _required_text(value, field).lower()
    if not text.startswith("sha256:") or len(text) != 71:
        raise RuntimeMasterDataError(f"{field} must be sha256:<64 hex>.")
    try:
        int(text.split(":", 1)[1], 16)
    except ValueError as exc:
        raise RuntimeMasterDataError(f"{field} must be sha256:<64 hex>.") from exc
    return text


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")
