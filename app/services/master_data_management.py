from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from app.models import Product
from app.repositories.master_data_repository import (
    PlatformMappingRecord,
    ProductMasterRecord,
    RuntimeMasterDataError,
    RuntimeMasterDataRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.authoritative_inventory import InventoryApplicationService
from app.services.product_mapping import normalize_mapping_text


class MasterDataManagementError(ValueError):
    """The requested master-data write is invalid or stale."""


@dataclass(frozen=True, slots=True)
class MasterDataWriteReceipt:
    entity_type: str
    entity_id: str
    status: str
    version: int


class MasterDataManagementService:
    """Versioned operator writes for Runtime product and mapping authority."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime = runtime_repository
        self.master_data = RuntimeMasterDataRepository(runtime_repository)
        self.inventory = InventoryApplicationService(runtime_repository)

    def create_product(
        self,
        *,
        internal_sku: str,
        product_name: str,
        grade: str,
        stem_length: str,
        unit: str,
        base_cost: Decimal,
        sale_enabled: bool,
        remark: str,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        self.runtime.require_current_schema(operation_name="新增商品")
        sku = _required_text(internal_sku, "商品 SKU").upper()
        normalized_cost = _positive_decimal(base_cost, "基础成本")
        product = Product(
            internal_sku=sku,
            product_name=_required_text(product_name, "商品名称"),
            grade=_required_text(grade, "等级"),
            stem_length=_required_text(stem_length, "规格"),
            unit=_required_text(unit, "单位"),
            base_cost=normalized_cost,
            current_stock=0,
            sale_enabled=bool(sale_enabled),
            remark=str(remark or "").strip(),
        )
        source_ref = _source_ref(actor, idempotency_key)
        digest = _payload_sha256(
            {
                "operation": "CREATE_PRODUCT",
                "product": _product_payload(product),
            }
        )
        with closing(self.runtime.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                replay = _current_product_replay(
                    connection,
                    sku=sku,
                    source_ref=source_ref,
                    source_sha256=digest,
                )
                if replay is not None:
                    connection.rollback()
                    return MasterDataWriteReceipt("PRODUCT", sku, "REPLAYED", replay)
                record = self.master_data.insert_product(
                    product,
                    source_ref=source_ref,
                    source_sha256=digest,
                    connection=connection,
                )
                self.inventory.initialize_sku(
                    internal_sku=sku,
                    actor=_required_text(actor, "操作人"),
                    idempotency_key=f"{idempotency_key}:inventory",
                    connection=connection,
                )
                connection.commit()
            except Exception as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise _as_management_error(exc) from exc
        return MasterDataWriteReceipt("PRODUCT", sku, "APPLIED", record.version)

    def update_product(
        self,
        *,
        internal_sku: str,
        product_name: str,
        grade: str,
        stem_length: str,
        unit: str,
        base_cost: Decimal,
        sale_enabled: bool,
        remark: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        self.runtime.require_current_schema(operation_name="修改商品资料")
        sku = _required_text(internal_sku, "商品 SKU").upper()
        current = _current_product_record(self.master_data, sku)
        product = Product(
            internal_sku=sku,
            product_name=_required_text(product_name, "商品名称"),
            grade=_required_text(grade, "等级"),
            stem_length=_required_text(stem_length, "规格"),
            unit=_required_text(unit, "单位"),
            base_cost=_positive_decimal(base_cost, "基础成本"),
            current_stock=current.product.current_stock,
            sale_enabled=bool(sale_enabled),
            remark=str(remark or "").strip(),
        )
        source_ref = _source_ref(actor, idempotency_key)
        digest = _payload_sha256(
            {
                "operation": "UPDATE_PRODUCT",
                "expected_version": int(expected_version),
                "product": _product_payload(product),
            }
        )
        with closing(self.runtime.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                replay = _current_product_replay(
                    connection,
                    sku=sku,
                    source_ref=source_ref,
                    source_sha256=digest,
                )
                if replay is not None:
                    connection.rollback()
                    return MasterDataWriteReceipt("PRODUCT", sku, "REPLAYED", replay)
                record = self.master_data.update_product(
                    product,
                    expected_version=int(expected_version),
                    source_ref=source_ref,
                    source_sha256=digest,
                    connection=connection,
                )
                connection.commit()
            except Exception as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise _as_management_error(exc) from exc
        return MasterDataWriteReceipt("PRODUCT", sku, "APPLIED", record.version)

    def save_product_mapping(
        self,
        *,
        mapping_id: str,
        platform_name: str,
        platform_product_name: str,
        grade: str,
        internal_sku: str | None,
        search_keyword: str,
        mapping_status: str,
        remark: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        self.runtime.require_current_schema(operation_name="维护商品对应关系")
        normalized_status = _required_text(mapping_status, "对应状态").upper()
        if normalized_status not in {"VERIFIED", "UNMAPPED", "AMBIGUOUS", "DISABLED"}:
            raise MasterDataManagementError("对应状态无效。")
        sku = str(internal_sku or "").strip().upper() or None
        if normalized_status == "VERIFIED" and not sku:
            raise MasterDataManagementError("已确认的对应关系必须选择内部商品。")
        if sku and self.master_data.get_product(sku) is None:
            raise MasterDataManagementError("选择的内部商品不存在。")
        source_ref = _source_ref(actor, idempotency_key)
        normalized_id = str(mapping_id or "").strip() or _mapping_id_from_source_ref(
            source_ref
        )
        now_text = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row: dict[str, object] = {
            "mapping_id": normalized_id,
            "mapping_kind": "PRODUCT",
            "platform_name": _required_text(platform_name, "平台"),
            "platform_product_id": "",
            "platform_product_name": _required_text(
                platform_product_name,
                "平台商品名称",
            ),
            "normalized_platform_product_name": normalize_mapping_text(
                platform_product_name
            ),
            "grade": _required_text(grade, "等级"),
            "internal_sku": sku if normalized_status == "VERIFIED" else None,
            "candidate_internal_sku": (
                sku if normalized_status == "AMBIGUOUS" else None
            ),
            "search_keyword": str(search_keyword or "").strip(),
            "mapping_status": normalized_status,
            "effective_from": None,
            "effective_to": None,
            "last_verified_at": now_text if normalized_status == "VERIFIED" else None,
            "remark": str(remark or "").strip(),
        }
        digest = _payload_sha256(
            {
                "operation": "SAVE_PRODUCT_MAPPING",
                "expected_version": int(expected_version),
                "mapping": row,
            }
        )
        with closing(self.runtime.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                replay = _current_mapping_replay(
                    connection,
                    mapping_id=normalized_id,
                    source_ref=source_ref,
                    source_sha256=digest,
                )
                if replay is not None:
                    connection.rollback()
                    return MasterDataWriteReceipt(
                        "PRODUCT_MAPPING",
                        normalized_id,
                        "REPLAYED",
                        replay,
                    )
                record: PlatformMappingRecord
                if int(expected_version) == 0:
                    record = self.master_data.insert_mapping(
                        row,
                        source_ref=source_ref,
                        source_sha256=digest,
                        connection=connection,
                    )
                else:
                    record = self.master_data.update_mapping(
                        row,
                        expected_version=int(expected_version),
                        source_ref=source_ref,
                        source_sha256=digest,
                        connection=connection,
                    )
                connection.commit()
            except Exception as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise _as_management_error(exc) from exc
        return MasterDataWriteReceipt(
            "PRODUCT_MAPPING",
            normalized_id,
            "APPLIED",
            record.version,
        )


def _current_product_record(
    repository: RuntimeMasterDataRepository,
    sku: str,
) -> ProductMasterRecord:
    record = next(
        (item for item in repository.list_product_records() if item.product.internal_sku == sku),
        None,
    )
    if record is None:
        raise MasterDataManagementError("商品不存在。")
    return record


def _current_product_replay(connection, *, sku: str, source_ref: str, source_sha256: str) -> int | None:
    row = connection.execute(
        "SELECT source_ref, source_sha256, version FROM product_catalog WHERE internal_sku = ?",
        (sku,),
    ).fetchone()
    if row is None:
        return None
    if str(row["source_ref"]) == source_ref:
        if str(row["source_sha256"]) != source_sha256:
            raise MasterDataManagementError("重复请求编号对应了不同的商品内容。")
        return int(row["version"])
    return None


def _current_mapping_replay(
    connection,
    *,
    mapping_id: str,
    source_ref: str,
    source_sha256: str,
) -> int | None:
    row = connection.execute(
        "SELECT source_ref, source_sha256, version FROM platform_product_mappings WHERE mapping_id = ?",
        (mapping_id,),
    ).fetchone()
    if row is None:
        return None
    if str(row["source_ref"]) == source_ref:
        if str(row["source_sha256"]) != source_sha256:
            raise MasterDataManagementError("重复请求编号对应了不同的商品对应关系。")
        return int(row["version"])
    return None


def _product_payload(product: Product) -> Mapping[str, object]:
    return {
        "internal_sku": product.internal_sku,
        "product_name": product.product_name,
        "grade": product.grade,
        "stem_length": product.stem_length,
        "unit": product.unit,
        "base_cost": format(product.base_cost, "f"),
        "sale_enabled": product.sale_enabled,
        "remark": product.remark,
    }


def _payload_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _source_ref(actor: str, idempotency_key: str) -> str:
    return (
        _required_text(idempotency_key, "请求编号")
        + ";actor="
        + _required_text(actor, "操作人")
    )


def _mapping_id_from_source_ref(source_ref: str) -> str:
    """Derive a stable identifier so a retried create cannot duplicate a mapping."""

    digest = hashlib.sha256(source_ref.encode("utf-8")).hexdigest().upper()
    return f"MAP-{digest[:24]}"


def _required_text(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MasterDataManagementError(f"{label}不能为空。")
    return normalized


def _positive_decimal(value: object, label: str) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MasterDataManagementError(f"{label}必须是有效数字。") from exc
    if not normalized.is_finite() or normalized <= 0:
        raise MasterDataManagementError(f"{label}必须大于 0。")
    return normalized


def _as_management_error(exc: Exception) -> MasterDataManagementError:
    if isinstance(exc, MasterDataManagementError):
        return exc
    if isinstance(exc, RuntimeMasterDataError):
        return MasterDataManagementError(str(exc))
    return MasterDataManagementError(str(exc) or "商品资料未保存。")
