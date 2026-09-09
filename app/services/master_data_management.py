"""Controlled import, cutover, and versioned Runtime master-data writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4

from app.models import Product
from app.repositories.master_data_repository import (
    RuntimeMasterDataError,
    RuntimeMasterDataRepository,
    canonical_identity_json,
    identity_digest,
    utc_text,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.services.product_mapping import (
    ProductMappingError,
    ProductMappingRecord,
    compile_product_mapping_rows,
    compile_product_mapping_workbook,
    normalize_mapping_text,
)


CUTOVER_CONFIRMATION = "CUTOVER PRODUCT_MAPPING TO RUNTIME DB"
ROLLBACK_CONFIRMATION = "ROLLBACK PRODUCT_MAPPING TO WORKBOOK"


@dataclass(frozen=True, slots=True)
class MasterDataImportPreview:
    product_count: int
    mapping_count: int
    account_ids: tuple[str, ...]
    products_workbook_sha256: str
    mappings_workbook_sha256: str
    request_sha256: str


@dataclass(frozen=True, slots=True)
class MasterDataShadowComparison:
    matched: bool
    product_count: int
    mapping_count: int
    differences: tuple[str, ...]
    authority_generation: int
    product_snapshot_sha256: str
    mapping_snapshot_sha256: str
    products_workbook_sha256: str
    mappings_workbook_sha256: str
    account_bindings_sha256: str
    receipt_sha256: str
    event_sequence: int


@dataclass(frozen=True, slots=True)
class MasterDataWriteReceipt:
    status: str
    entity_type: str
    entity_id: str
    version: int
    authority_generation: int
    product_snapshot_sha256: str
    mapping_snapshot_sha256: str


class MasterDataManagementService:
    """Only writer for v19 Product/Mapping authority and cutover state."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime = runtime_repository
        self.repository = RuntimeMasterDataRepository(runtime_repository)

    def preview_workbook_import(
        self,
        *,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        account_id_by_platform: Mapping[str, str],
    ) -> MasterDataImportPreview:
        products, mappings, products_sha, mappings_sha = _read_workbooks(
            products_workbook,
            platform_mappings_workbook,
            account_id_by_platform,
        )
        payload = _import_payload(
            products, mappings, products_sha, mappings_sha, account_id_by_platform
        )
        return MasterDataImportPreview(
            product_count=len(products),
            mapping_count=len(mappings),
            account_ids=tuple(sorted(set(account_id_by_platform.values()))),
            products_workbook_sha256=products_sha,
            mappings_workbook_sha256=mappings_sha,
            request_sha256=_payload_sha256(payload),
        )

    def import_from_workbooks(
        self,
        *,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        account_id_by_platform: Mapping[str, str],
        expected_request_sha256: str,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        return self._write_workbook_candidate(
            products_workbook=products_workbook,
            platform_mappings_workbook=platform_mappings_workbook,
            account_id_by_platform=account_id_by_platform,
            expected_request_sha256=expected_request_sha256,
            actor=actor,
            idempotency_key=idempotency_key,
            refresh=False,
        )

    def preview_candidate_refresh(
        self,
        *,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        account_id_by_platform: Mapping[str, str],
    ) -> MasterDataImportPreview:
        products, mappings, products_sha, mappings_sha = _read_workbooks(
            products_workbook,
            platform_mappings_workbook,
            account_id_by_platform,
        )
        payload = _import_payload(
            products,
            mappings,
            products_sha,
            mappings_sha,
            account_id_by_platform,
            operation="CANDIDATE_REFRESH",
        )
        return MasterDataImportPreview(
            product_count=len(products),
            mapping_count=len(mappings),
            account_ids=tuple(sorted(set(account_id_by_platform.values()))),
            products_workbook_sha256=products_sha,
            mappings_workbook_sha256=mappings_sha,
            request_sha256=_payload_sha256(payload),
        )

    def refresh_candidate_from_workbooks(
        self,
        *,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        account_id_by_platform: Mapping[str, str],
        expected_request_sha256: str,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        return self._write_workbook_candidate(
            products_workbook=products_workbook,
            platform_mappings_workbook=platform_mappings_workbook,
            account_id_by_platform=account_id_by_platform,
            expected_request_sha256=expected_request_sha256,
            actor=actor,
            idempotency_key=idempotency_key,
            refresh=True,
        )

    def _write_workbook_candidate(
        self,
        *,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        account_id_by_platform: Mapping[str, str],
        expected_request_sha256: str,
        actor: str,
        idempotency_key: str,
        refresh: bool,
    ) -> MasterDataWriteReceipt:
        health = self.runtime.check_schema_health()
        if not health.ok:
            raise RuntimeMasterDataError(
                "Runtime schema is not current for Product/Mapping import: "
                + health.summary
            )
        normalized_actor = _required_text(actor, "actor")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        products, mappings, products_sha, mappings_sha = _read_workbooks(
            products_workbook,
            platform_mappings_workbook,
            account_id_by_platform,
        )
        operation = "CANDIDATE_REFRESH" if refresh else "IMPORT"
        payload = _import_payload(
            products,
            mappings,
            products_sha,
            mappings_sha,
            account_id_by_platform,
            operation=operation,
        )
        request_sha = _payload_sha256(payload)
        if request_sha != str(expected_request_sha256 or "").strip():
            raise RuntimeMasterDataError("Import preview digest changed; preview again.")
        with self.runtime.connect_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _event_replay(
                connection, normalized_actor, normalized_key, request_sha
            )
            if replay:
                connection.rollback()
                return self._receipt("REPLAYED", operation, normalized_key, 1)
            state = self.repository.authority_state(connection=connection)
            if state.authority_mode != "PRE_CUTOVER":
                raise RuntimeMasterDataError(
                    "Workbook candidate writes require PRE_CUTOVER authority."
                )
            has_products = connection.execute(
                "SELECT 1 FROM product_catalog LIMIT 1"
            ).fetchone() is not None
            has_mappings = connection.execute(
                "SELECT 1 FROM platform_product_mappings LIMIT 1"
            ).fetchone() is not None
            if refresh:
                if state.generation < 1 or not has_products or not has_mappings:
                    raise RuntimeMasterDataError(
                        "Candidate refresh requires an existing PRE_CUTOVER candidate."
                    )
                connection.execute("DELETE FROM platform_product_mappings")
                connection.execute("DELETE FROM product_catalog")
                generation = state.generation + 1
            else:
                if state.generation != 0 or has_products or has_mappings:
                    raise RuntimeMasterDataError(
                        "Initial workbook import requires an empty PRE_CUTOVER candidate."
                    )
                generation = 1
            now = utc_text()
            for product in products:
                _insert_product(
                    connection,
                    product,
                    source_type=operation,
                    source_ref="products-workbook:" + products_sha,
                    source_sha256=products_sha,
                    version=1,
                    generation=generation,
                    now=now,
                )
            product_skus = {item.internal_sku for item in products}
            for mapping in mappings:
                _validate_mapping_skus(mapping, product_skus)
                _insert_mapping(
                    connection,
                    mapping,
                    source_type=operation,
                    source_ref="platform-mappings-workbook:" + mappings_sha,
                    source_sha256=mappings_sha,
                    version=1,
                    generation=generation,
                    now=now,
                )
            product_digest = self.repository.product_snapshot_sha256(
                connection=connection
            )
            mapping_digest = self.repository.mapping_snapshot_sha256(
                connection=connection
            )
            connection.execute(
                "UPDATE master_data_authority_state SET generation = ?, "
                "product_snapshot_sha256 = ?, mapping_snapshot_sha256 = ?, "
                "updated_at = ? WHERE authority_key = 'PRODUCT_MAPPING'",
                (generation, product_digest, mapping_digest, now),
            )
            _insert_event(
                connection,
                event_type=operation,
                generation=generation,
                actor=normalized_actor,
                idempotency_key=normalized_key,
                request_sha256=request_sha,
                source_ref=(
                    "controlled-candidate-refresh"
                    if refresh
                    else "controlled-workbook-import"
                ),
                payload=payload,
                created_at=now,
            )
            connection.commit()
        return MasterDataWriteReceipt(
            "APPLIED",
            operation,
            normalized_key,
            1,
            generation,
            product_digest,
            mapping_digest,
        )

    def shadow_compare_workbooks(
        self,
        *,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        account_id_by_platform: Mapping[str, str],
        actor: str,
        idempotency_key: str,
    ) -> MasterDataShadowComparison:
        """Compare business semantics without treating workbook inventory as Product."""

        normalized_actor = _required_text(actor, "actor")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        products, mappings, products_sha, mappings_sha = _read_workbooks(
            products_workbook,
            platform_mappings_workbook,
            account_id_by_platform,
        )
        expected_products = {
            _product_business_tuple(product) for product in products
        }
        expected_mappings = {
            _mapping_business_tuple(record) for record in mappings
        }
        account_bindings = _normalized_account_bindings(account_id_by_platform)
        account_bindings_sha = _payload_sha256(account_bindings)
        with self.runtime.connect_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self.repository.authority_state(connection=connection)
            if state.authority_mode != "PRE_CUTOVER" or state.generation < 1:
                raise RuntimeMasterDataError(
                    "Shadow compare requires an imported PRE_CUTOVER candidate."
                )
            actual_products = {
                _product_business_tuple(product)
                for product in self.repository.list_products(connection=connection)
            }
            actual_mappings = {
                _mapping_business_tuple(record)
                for record in self.repository.list_mapping_records(
                    connection=connection
                )
            }
            product_digest = self.repository.product_snapshot_sha256(
                connection=connection
            )
            mapping_digest = self.repository.mapping_snapshot_sha256(
                connection=connection
            )
            differences: list[str] = []
            if expected_products != actual_products:
                differences.append("PRODUCT_SEMANTICS_DIFFER")
            if expected_mappings != actual_mappings:
                differences.append("MAPPING_SEMANTICS_DIFFER")
            payload = {
                "operation": "SHADOW_COMPARE",
                "matched": not differences,
                "differences": differences,
                "authority_generation": state.generation,
                "product_snapshot_sha256": product_digest,
                "mapping_snapshot_sha256": mapping_digest,
                "products_workbook_sha256": products_sha,
                "mappings_workbook_sha256": mappings_sha,
                "account_bindings": account_bindings,
                "account_bindings_sha256": account_bindings_sha,
            }
            receipt_sha = _payload_sha256(payload)
            replay = _event_replay(
                connection, normalized_actor, normalized_key, receipt_sha
            )
            if replay:
                row = connection.execute(
                    "SELECT event_sequence FROM master_data_authority_events "
                    "WHERE actor = ? AND idempotency_key = ?",
                    (normalized_actor, normalized_key),
                ).fetchone()
                event_sequence = int(row["event_sequence"])
                connection.rollback()
            else:
                event_sequence = _insert_event(
                    connection,
                    event_type="SHADOW_COMPARE",
                    generation=state.generation,
                    actor=normalized_actor,
                    idempotency_key=normalized_key,
                    request_sha256=receipt_sha,
                    source_ref="controlled-workbook-shadow-compare",
                    payload=payload,
                    created_at=utc_text(),
                )
                connection.commit()
        return MasterDataShadowComparison(
            matched=not differences,
            product_count=len(actual_products),
            mapping_count=len(actual_mappings),
            differences=tuple(differences),
            authority_generation=state.generation,
            product_snapshot_sha256=product_digest,
            mapping_snapshot_sha256=mapping_digest,
            products_workbook_sha256=products_sha,
            mappings_workbook_sha256=mappings_sha,
            account_bindings_sha256=account_bindings_sha,
            receipt_sha256=receipt_sha,
            event_sequence=event_sequence,
        )

    def activate_runtime_authority(
        self,
        *,
        expected_product_snapshot_sha256: str,
        expected_mapping_snapshot_sha256: str,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        account_id_by_platform: Mapping[str, str],
        expected_shadow_compare_receipt_sha256: str,
        actor: str,
        idempotency_key: str,
        confirmation: str,
    ) -> MasterDataWriteReceipt:
        if confirmation != CUTOVER_CONFIRMATION:
            raise RuntimeMasterDataError("Explicit Runtime authority confirmation is missing.")
        normalized_actor = _required_text(actor, "actor")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        _, _, products_sha, mappings_sha = _read_workbooks(
            products_workbook,
            platform_mappings_workbook,
            account_id_by_platform,
        )
        account_bindings = _normalized_account_bindings(account_id_by_platform)
        receipt_sha = _required_text(
            expected_shadow_compare_receipt_sha256,
            "expected_shadow_compare_receipt_sha256",
        )
        payload = {
            "operation": "CUTOVER",
            "product_snapshot_sha256": expected_product_snapshot_sha256,
            "mapping_snapshot_sha256": expected_mapping_snapshot_sha256,
            "shadow_compare_receipt_sha256": receipt_sha,
            "products_workbook_sha256": products_sha,
            "mappings_workbook_sha256": mappings_sha,
            "account_bindings": account_bindings,
        }
        request_sha = _payload_sha256(payload)
        with self.runtime.connect_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _event_replay(
                connection, normalized_actor, normalized_key, request_sha
            )
            if replay:
                state = self.repository.authority_state(connection=connection)
                if state.authority_mode != "DB_AUTHORITY":
                    raise RuntimeMasterDataError(
                        "Cutover idempotency replay no longer matches current authority."
                    )
                connection.rollback()
                return self._receipt("REPLAYED", "AUTHORITY", "PRODUCT_MAPPING", 1)
            state = self.repository.authority_state(connection=connection)
            if state.authority_mode != "PRE_CUTOVER" or state.generation < 1:
                raise RuntimeMasterDataError("Runtime candidate is not ready for cutover.")
            product_digest = self.repository.product_snapshot_sha256(
                connection=connection
            )
            mapping_digest = self.repository.mapping_snapshot_sha256(
                connection=connection
            )
            if (
                product_digest != expected_product_snapshot_sha256
                or mapping_digest != expected_mapping_snapshot_sha256
                or product_digest != state.product_snapshot_sha256
                or mapping_digest != state.mapping_snapshot_sha256
            ):
                raise RuntimeMasterDataError("Runtime candidate changed after preview.")
            if not connection.execute("SELECT 1 FROM product_catalog LIMIT 1").fetchone():
                raise RuntimeMasterDataError("Product candidate must not be empty.")
            if not connection.execute(
                "SELECT 1 FROM platform_product_mappings LIMIT 1"
            ).fetchone():
                raise RuntimeMasterDataError("Mapping candidate must not be empty.")
            compare_row = connection.execute(
                "SELECT event_sequence, authority_generation, payload_json "
                "FROM master_data_authority_events "
                "WHERE event_type = 'SHADOW_COMPARE' AND request_sha256 = ? "
                "ORDER BY event_sequence DESC LIMIT 1",
                (receipt_sha,),
            ).fetchone()
            if compare_row is None:
                raise RuntimeMasterDataError(
                    "Cutover requires a successful shadow-compare receipt."
                )
            try:
                compare_payload = json.loads(str(compare_row["payload_json"]))
            except json.JSONDecodeError as exc:
                raise RuntimeMasterDataError(
                    "Shadow-compare receipt payload is invalid."
                ) from exc
            last_rollback = connection.execute(
                "SELECT max(event_sequence) AS event_sequence "
                "FROM master_data_authority_events WHERE event_type = 'ROLLBACK'"
            ).fetchone()
            last_rollback_sequence = int(last_rollback["event_sequence"] or 0)
            expected_compare = {
                "operation": "SHADOW_COMPARE",
                "matched": True,
                "differences": [],
                "authority_generation": state.generation,
                "product_snapshot_sha256": product_digest,
                "mapping_snapshot_sha256": mapping_digest,
                "products_workbook_sha256": products_sha,
                "mappings_workbook_sha256": mappings_sha,
                "account_bindings": account_bindings,
            }
            if (
                int(compare_row["authority_generation"]) != state.generation
                or int(compare_row["event_sequence"]) <= last_rollback_sequence
                or _payload_sha256(compare_payload) != receipt_sha
                or any(
                    compare_payload.get(key) != value
                    for key, value in expected_compare.items()
                )
            ):
                raise RuntimeMasterDataError(
                    "Shadow-compare receipt is stale or does not match current sources."
                )
            now = utc_text()
            is_recutover = connection.execute(
                "SELECT 1 FROM master_data_authority_events "
                "WHERE event_type = 'ROLLBACK' LIMIT 1"
            ).fetchone() is not None
            event_sequence = _insert_event(
                connection,
                event_type="RECUTOVER" if is_recutover else "CUTOVER",
                generation=state.generation,
                actor=normalized_actor,
                idempotency_key=normalized_key,
                request_sha256=request_sha,
                source_ref="explicit-authority-switch",
                payload=payload,
                created_at=now,
            )
            connection.execute(
                "UPDATE master_data_authority_state SET authority_mode = 'DB_AUTHORITY', "
                "cutover_generation = ?, cutover_event_sequence = ?, cutover_at = ?, "
                "cutover_by = ?, updated_at = ? "
                "WHERE authority_key = 'PRODUCT_MAPPING'",
                (state.generation, event_sequence, now, normalized_actor, now),
            )
            connection.commit()
        return self._receipt("APPLIED", "AUTHORITY", "PRODUCT_MAPPING", 1)

    def rollback_to_workbook_authority(
        self,
        *,
        actor: str,
        idempotency_key: str,
        confirmation: str,
    ) -> MasterDataWriteReceipt:
        if confirmation != ROLLBACK_CONFIRMATION:
            raise RuntimeMasterDataError("Explicit workbook rollback confirmation is missing.")
        normalized_actor = _required_text(actor, "actor")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        payload = {"operation": "ROLLBACK_TO_WORKBOOK"}
        request_sha = _payload_sha256(payload)
        with self.runtime.connect_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _event_replay(
                connection, normalized_actor, normalized_key, request_sha
            )
            if replay:
                state = self.repository.authority_state(connection=connection)
                if state.authority_mode != "PRE_CUTOVER":
                    raise RuntimeMasterDataError(
                        "Rollback idempotency replay no longer matches current authority."
                    )
                connection.rollback()
                return self._receipt("REPLAYED", "AUTHORITY", "PRODUCT_MAPPING", 1)
            state = self.repository.authority_state(connection=connection)
            if (
                state.authority_mode != "DB_AUTHORITY"
                or state.cutover_event_sequence is None
            ):
                raise RuntimeMasterDataError("Runtime authority is not active.")
            irreversible = connection.execute(
                "SELECT event_type FROM master_data_authority_events "
                "WHERE event_sequence > ? AND event_type IN "
                "('PRODUCT_MUTATION', 'MAPPING_MUTATION', 'PLATFORM_SIDE_EFFECT') "
                "ORDER BY event_sequence LIMIT 1",
                (state.cutover_event_sequence,),
            ).fetchone()
            if irreversible is not None:
                raise RuntimeMasterDataError(
                    "Rollback is unsafe after authoritative mutation or platform side effect; "
                    "use forward correction or explicit re-cutover."
                )
            now = utc_text()
            _insert_event(
                connection,
                event_type="ROLLBACK",
                generation=state.generation,
                actor=normalized_actor,
                idempotency_key=normalized_key,
                request_sha256=request_sha,
                source_ref="explicit-authority-rollback",
                payload=payload,
                created_at=now,
            )
            connection.execute(
                "UPDATE master_data_authority_state SET authority_mode = 'PRE_CUTOVER', "
                "cutover_generation = NULL, cutover_event_sequence = NULL, "
                "cutover_at = NULL, cutover_by = '', updated_at = ? "
                "WHERE authority_key = 'PRODUCT_MAPPING'",
                (now,),
            )
            connection.commit()
        return self._receipt("APPLIED", "AUTHORITY", "PRODUCT_MAPPING", 1)

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
        product = _validated_product(
            internal_sku=internal_sku,
            product_name=product_name,
            grade=grade,
            stem_length=stem_length,
            unit=unit,
            base_cost=base_cost,
            sale_enabled=sale_enabled,
            remark=remark,
        )
        payload = {"operation": "CREATE_PRODUCT", "product": _product_payload(product)}
        return self._write_product(
            product=product,
            expected_version=None,
            payload=payload,
            actor=actor,
            idempotency_key=idempotency_key,
        )

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
        product = _validated_product(
            internal_sku=internal_sku,
            product_name=product_name,
            grade=grade,
            stem_length=stem_length,
            unit=unit,
            base_cost=base_cost,
            sale_enabled=sale_enabled,
            remark=remark,
        )
        payload = {
            "operation": "UPDATE_PRODUCT",
            "expected_version": int(expected_version),
            "product": _product_payload(product),
        }
        return self._write_product(
            product=product,
            expected_version=int(expected_version),
            payload=payload,
            actor=actor,
            idempotency_key=idempotency_key,
        )

    def create_mapping(
        self,
        *,
        mapping_id: str,
        platform_name: str,
        account_id: str,
        platform_product_identity: Mapping[str, object],
        platform_product_name: str,
        grade: str,
        mapping_status: str,
        internal_sku: str | None,
        candidate_internal_skus: Sequence[str] = (),
        effective_from: str | None = None,
        effective_to: str | None = None,
        remark: str = "",
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        row = _mapping_input_row(
            mapping_id=mapping_id,
            platform_name=platform_name,
            account_id=account_id,
            platform_product_identity=platform_product_identity,
            platform_product_name=platform_product_name,
            grade=grade,
            mapping_status=mapping_status,
            internal_sku=internal_sku,
            candidate_internal_skus=candidate_internal_skus,
            effective_from=effective_from,
            effective_to=effective_to,
            remark=remark,
        )
        return self._write_mapping(
            row=row,
            expected_version=None,
            actor=actor,
            idempotency_key=idempotency_key,
        )

    def update_mapping(
        self,
        *,
        mapping_id: str,
        platform_name: str,
        account_id: str,
        platform_product_identity: Mapping[str, object],
        platform_product_name: str,
        grade: str,
        mapping_status: str,
        internal_sku: str | None,
        candidate_internal_skus: Sequence[str] = (),
        effective_from: str | None = None,
        effective_to: str | None = None,
        remark: str = "",
        expected_version: int,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        row = _mapping_input_row(
            mapping_id=mapping_id,
            platform_name=platform_name,
            account_id=account_id,
            platform_product_identity=platform_product_identity,
            platform_product_name=platform_product_name,
            grade=grade,
            mapping_status=mapping_status,
            internal_sku=internal_sku,
            candidate_internal_skus=candidate_internal_skus,
            effective_from=effective_from,
            effective_to=effective_to,
            remark=remark,
        )
        return self._write_mapping(
            row=row,
            expected_version=int(expected_version),
            actor=actor,
            idempotency_key=idempotency_key,
        )

    def mark_platform_side_effect(
        self,
        *,
        operation_id: str,
        authority_generation: int,
        mapping_snapshot_sha256: str,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        normalized_actor = _required_text(actor, "actor")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        payload = {
            "operation": "PLATFORM_SIDE_EFFECT",
            "operation_id": _required_text(operation_id, "operation_id"),
            "authority_generation": int(authority_generation),
            "mapping_snapshot_sha256": _required_text(
                mapping_snapshot_sha256, "mapping_snapshot_sha256"
            ),
        }
        request_sha = _payload_sha256(payload)
        with self.runtime.connect_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _event_replay(
                connection, normalized_actor, normalized_key, request_sha
            )
            if replay:
                connection.rollback()
                return self._receipt(
                    "REPLAYED", "PLATFORM_SIDE_EFFECT", payload["operation_id"], 1
                )
            state = self.repository.authority_state(connection=connection)
            if (
                state.authority_mode != "DB_AUTHORITY"
                or state.generation != int(authority_generation)
                or state.mapping_snapshot_sha256 != mapping_snapshot_sha256
            ):
                raise RuntimeMasterDataError(
                    "Platform side effect is not bound to current Runtime mapping authority."
                )
            _insert_event(
                connection,
                event_type="PLATFORM_SIDE_EFFECT",
                generation=state.generation,
                actor=normalized_actor,
                idempotency_key=normalized_key,
                request_sha256=request_sha,
                source_ref="platform-operation:" + payload["operation_id"],
                payload=payload,
                created_at=utc_text(),
            )
            connection.commit()
        return self._receipt(
            "APPLIED", "PLATFORM_SIDE_EFFECT", payload["operation_id"], 1
        )

    def _write_product(
        self,
        *,
        product: Product,
        expected_version: int | None,
        payload: Mapping[str, object],
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        normalized_actor = _required_text(actor, "actor")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        request_sha = _payload_sha256(payload)
        with self.runtime.connect_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _event_replay(
                connection, normalized_actor, normalized_key, request_sha
            )
            if replay:
                connection.rollback()
                record = self.repository.get_product_record(product.internal_sku)
                version = record.version if record else 0
                return self._receipt(
                    "REPLAYED", "PRODUCT", product.internal_sku, version
                )
            state = self.repository.authority_state(connection=connection)
            if state.authority_mode != "DB_AUTHORITY":
                raise RuntimeMasterDataError(
                    "Versioned Product writes require active Runtime authority."
                )
            generation = state.generation + 1
            now = utc_text()
            source_ref = f"operator:{normalized_actor}:{normalized_key}"
            if expected_version is None:
                if connection.execute(
                    "SELECT 1 FROM product_catalog WHERE internal_sku = ?",
                    (product.internal_sku,),
                ).fetchone():
                    raise RuntimeMasterDataError("Product already exists.")
                version = 1
                _insert_product(
                    connection,
                    product,
                    source_type="WEB_MANAGEMENT",
                    source_ref=source_ref,
                    source_sha256=request_sha,
                    version=version,
                    generation=generation,
                    now=now,
                )
            else:
                version = expected_version + 1
                changed = connection.execute(
                    """
                    UPDATE product_catalog SET product_name = ?, grade = ?,
                        stem_length = ?, unit = ?, base_cost = ?, sale_enabled = ?,
                        remark = ?, source_type = 'WEB_MANAGEMENT', source_ref = ?,
                        source_sha256 = ?, version = ?, authority_generation = ?,
                        updated_at = ?
                    WHERE internal_sku = ? AND version = ?
                    """,
                    (
                        product.product_name,
                        product.grade,
                        product.stem_length,
                        product.unit,
                        _decimal_text(product.base_cost),
                        int(product.sale_enabled),
                        product.remark,
                        source_ref,
                        request_sha,
                        version,
                        generation,
                        now,
                        product.internal_sku,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise RuntimeMasterDataError(
                        "Product version changed; refresh before retrying."
                    )
            product_digest = self.repository.product_snapshot_sha256(
                connection=connection
            )
            connection.execute(
                "UPDATE master_data_authority_state SET generation = ?, "
                "product_snapshot_sha256 = ?, updated_at = ? "
                "WHERE authority_key = 'PRODUCT_MAPPING'",
                (generation, product_digest, now),
            )
            _insert_event(
                connection,
                event_type="PRODUCT_MUTATION",
                generation=generation,
                actor=normalized_actor,
                idempotency_key=normalized_key,
                request_sha256=request_sha,
                source_ref=source_ref,
                payload=payload,
                created_at=now,
            )
            connection.commit()
        return self._receipt("APPLIED", "PRODUCT", product.internal_sku, version)

    def _write_mapping(
        self,
        *,
        row: Mapping[str, object],
        expected_version: int | None,
        actor: str,
        idempotency_key: str,
    ) -> MasterDataWriteReceipt:
        normalized_actor = _required_text(actor, "actor")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        payload = {
            "operation": "CREATE_MAPPING" if expected_version is None else "UPDATE_MAPPING",
            "expected_version": expected_version,
            "mapping": dict(row),
        }
        request_sha = _payload_sha256(payload)
        mapping_id = str(row["mapping_id"])
        with self.runtime.connect_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _event_replay(
                connection, normalized_actor, normalized_key, request_sha
            )
            if replay:
                connection.rollback()
                current = next(
                    (
                        item
                        for item in self.repository.list_mapping_records()
                        if item.mapping_id == mapping_id
                    ),
                    None,
                )
                return self._receipt(
                    "REPLAYED", "MAPPING", mapping_id, current.version if current else 0
                )
            state = self.repository.authority_state(connection=connection)
            if state.authority_mode != "DB_AUTHORITY":
                raise RuntimeMasterDataError(
                    "Versioned Mapping writes require active Runtime authority."
                )
            existing = self.repository.list_mapping_records(connection=connection)
            existing_rows = [_stored_mapping_row(item) for item in existing]
            matching = next((item for item in existing if item.mapping_id == mapping_id), None)
            if expected_version is None:
                if matching is not None:
                    raise RuntimeMasterDataError("Mapping already exists.")
                candidate_rows = [*existing_rows, dict(row)]
                version = 1
            else:
                if matching is None or matching.version != expected_version:
                    raise RuntimeMasterDataError(
                        "Mapping version changed; refresh before retrying."
                    )
                candidate_rows = [
                    dict(row) if item["mapping_id"] == mapping_id else item
                    for item in existing_rows
                ]
                version = expected_version + 1
            try:
                compiled = compile_product_mapping_rows(
                    candidate_rows,
                    source_workbook_sha256=request_sha.removeprefix("sha256:"),
                )
            except ProductMappingError as exc:
                raise RuntimeMasterDataError(str(exc)) from exc
            candidate = next(
                item for item in compiled.records if item.mapping_id == mapping_id
            )
            product_skus = {
                item.product.internal_sku
                for item in self.repository.list_product_records(connection=connection)
            }
            _validate_mapping_skus(candidate, product_skus)
            generation = state.generation + 1
            now = utc_text()
            source_ref = f"operator:{normalized_actor}:{normalized_key}"
            if expected_version is None:
                _insert_mapping(
                    connection,
                    candidate,
                    source_type="WEB_MANAGEMENT",
                    source_ref=source_ref,
                    source_sha256=request_sha,
                    version=version,
                    generation=generation,
                    now=now,
                )
            else:
                changed = connection.execute(
                    """
                    UPDATE platform_product_mappings SET platform_name = ?,
                        account_id = ?, platform_product_identity_json = ?,
                        platform_product_identity_digest = ?,
                        platform_product_name = ?, normalized_platform_product_name = ?,
                        grade = ?, internal_sku = ?, candidate_internal_skus_json = ?,
                        mapping_status = ?, effective_from = ?, effective_to = ?,
                        last_verified_at = ?, remark = ?, source_type = 'WEB_MANAGEMENT',
                        source_ref = ?, source_sha256 = ?, version = ?,
                        authority_generation = ?, updated_at = ?
                    WHERE mapping_id = ? AND version = ?
                    """,
                    (
                        candidate.platform_name,
                        candidate.account_id,
                        candidate.platform_product_identity_json,
                        candidate.platform_product_identity_digest,
                        candidate.platform_product_name,
                        candidate.normalized_platform_product_name,
                        candidate.grade,
                        candidate.internal_sku,
                        json.dumps(list(candidate.candidate_internal_skus), separators=(",", ":")),
                        candidate.mapping_status.value,
                        candidate.effective_from.isoformat() if candidate.effective_from else None,
                        candidate.effective_to.isoformat() if candidate.effective_to else None,
                        now if candidate.mapping_status.value == "VERIFIED" else None,
                        candidate.remark,
                        source_ref,
                        request_sha,
                        version,
                        generation,
                        now,
                        mapping_id,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise RuntimeMasterDataError(
                        "Mapping version changed; refresh before retrying."
                    )
            mapping_digest = self.repository.mapping_snapshot_sha256(
                connection=connection
            )
            connection.execute(
                "UPDATE master_data_authority_state SET generation = ?, "
                "mapping_snapshot_sha256 = ?, updated_at = ? "
                "WHERE authority_key = 'PRODUCT_MAPPING'",
                (generation, mapping_digest, now),
            )
            _insert_event(
                connection,
                event_type="MAPPING_MUTATION",
                generation=generation,
                actor=normalized_actor,
                idempotency_key=normalized_key,
                request_sha256=request_sha,
                source_ref=source_ref,
                payload=payload,
                created_at=now,
            )
            connection.commit()
        return self._receipt("APPLIED", "MAPPING", mapping_id, version)

    def _receipt(
        self, status: str, entity_type: str, entity_id: str, version: int
    ) -> MasterDataWriteReceipt:
        state = self.repository.authority_state()
        return MasterDataWriteReceipt(
            status=status,
            entity_type=entity_type,
            entity_id=entity_id,
            version=version,
            authority_generation=state.generation,
            product_snapshot_sha256=state.product_snapshot_sha256,
            mapping_snapshot_sha256=state.mapping_snapshot_sha256,
        )


def _mapping_input_row(
    *,
    mapping_id: str,
    platform_name: str,
    account_id: str,
    platform_product_identity: Mapping[str, object],
    platform_product_name: str,
    grade: str,
    mapping_status: str,
    internal_sku: str | None,
    candidate_internal_skus: Sequence[str],
    effective_from: str | None,
    effective_to: str | None,
    remark: str,
) -> dict[str, object]:
    identity_json = canonical_identity_json(platform_product_identity)
    return {
        "mapping_id": _required_text(mapping_id, "mapping_id"),
        "mapping_kind": "PRODUCT",
        "platform_name": _required_text(platform_name, "platform_name"),
        "account_id": _required_text(account_id, "account_id"),
        "platform_product_identity_json": identity_json,
        "platform_product_identity_digest": identity_digest(identity_json),
        "platform_product_name": _required_text(
            platform_product_name, "platform_product_name"
        ),
        "normalized_platform_product_name": normalize_mapping_text(
            platform_product_name
        ),
        "grade": _required_text(grade, "grade"),
        "internal_sku": str(internal_sku or "").strip().upper() or None,
        "candidate_internal_skus": sorted(
            {
                str(item).strip().upper()
                for item in candidate_internal_skus
                if str(item).strip()
            }
        ),
        "mapping_status": _required_text(mapping_status, "mapping_status").upper(),
        "effective_from": str(effective_from or "").strip() or None,
        "effective_to": str(effective_to or "").strip() or None,
        "remark": str(remark or "").strip(),
    }


def _stored_mapping_row(record) -> dict[str, object]:
    return {
        "mapping_id": record.mapping_id,
        "mapping_kind": "PRODUCT",
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


def _product_business_tuple(product: Product) -> tuple[object, ...]:
    return (
        product.internal_sku,
        product.product_name,
        product.grade,
        product.stem_length,
        product.unit,
        Decimal(product.base_cost),
        bool(product.sale_enabled),
        product.remark,
    )


def _mapping_business_tuple(record) -> tuple[object, ...]:
    return (
        record.mapping_id,
        record.platform_name,
        record.account_id,
        record.platform_product_identity_json,
        record.platform_product_name,
        record.normalized_platform_product_name,
        record.grade,
        record.internal_sku,
        tuple(record.candidate_internal_skus),
        (
            record.mapping_status.value
            if hasattr(record.mapping_status, "value")
            else str(record.mapping_status)
        ),
        (
            record.effective_from.isoformat()
            if hasattr(record.effective_from, "isoformat")
            else record.effective_from
        ),
        (
            record.effective_to.isoformat()
            if hasattr(record.effective_to, "isoformat")
            else record.effective_to
        ),
        record.remark,
    )


def _read_workbooks(
    products_workbook: Path,
    platform_mappings_workbook: Path,
    account_id_by_platform: Mapping[str, str],
) -> tuple[tuple[Product, ...], tuple[ProductMappingRecord, ...], str, str]:
    product_path = Path(products_workbook)
    mapping_path = Path(platform_mappings_workbook)
    product_bytes = product_path.read_bytes()
    mapping_bytes = mapping_path.read_bytes()
    products = tuple(load_products(product_path))
    compiled = compile_product_mapping_workbook(mapping_path)
    if product_path.read_bytes() != product_bytes or mapping_path.read_bytes() != mapping_bytes:
        raise RuntimeMasterDataError("Workbook source changed during import preview.")
    if not products:
        raise RuntimeMasterDataError("Product import candidate must not be empty.")
    if not compiled.records:
        raise RuntimeMasterDataError("Mapping import candidate must not be empty.")
    normalized_accounts = {
        normalize_mapping_text(platform): _required_text(account, "account_id")
        for platform, account in account_id_by_platform.items()
    }
    mapping_rows: list[dict[str, object]] = []
    for record in compiled.records:
        account_id = normalized_accounts.get(normalize_mapping_text(record.platform_name))
        if not account_id:
            raise RuntimeMasterDataError(
                f"Configured account_id is missing for platform {record.platform_name!r}."
            )
        mapping_rows.append(
            {
                "mapping_id": record.mapping_id,
                "platform_name": record.platform_name,
                "account_id": account_id,
                "platform_product_identity_json": record.platform_product_identity_json,
                "platform_product_identity_digest": record.platform_product_identity_digest,
                "platform_product_name": record.platform_product_name,
                "normalized_platform_product_name": record.normalized_platform_product_name,
                "grade": record.grade,
                "internal_sku": record.internal_sku,
                "candidate_internal_sku": record.candidate_internal_sku,
                "candidate_internal_skus": list(record.candidate_internal_skus),
                "mapping_status": record.mapping_status.value,
                "effective_from": (
                    record.effective_from.isoformat() if record.effective_from else None
                ),
                "effective_to": (
                    record.effective_to.isoformat() if record.effective_to else None
                ),
                "remark": record.remark,
            }
        )
    scoped = compile_product_mapping_rows(
        mapping_rows,
        source_workbook_sha256=hashlib.sha256(mapping_bytes).hexdigest(),
    )
    return (
        products,
        scoped.records,
        "sha256:" + hashlib.sha256(product_bytes).hexdigest(),
        "sha256:" + hashlib.sha256(mapping_bytes).hexdigest(),
    )


def _import_payload(
    products: Sequence[Product],
    mappings: Sequence[ProductMappingRecord],
    products_sha: str,
    mappings_sha: str,
    account_id_by_platform: Mapping[str, str],
    *,
    operation: str = "IMPORT",
) -> dict[str, object]:
    return {
        "operation": operation,
        "product_count": len(products),
        "mapping_count": len(mappings),
        "products_workbook_sha256": products_sha,
        "mappings_workbook_sha256": mappings_sha,
        "account_bindings": _normalized_account_bindings(account_id_by_platform),
    }


def _normalized_account_bindings(
    account_id_by_platform: Mapping[str, str],
) -> list[list[str]]:
    return [
        [str(platform).strip(), str(account).strip()]
        for platform, account in sorted(
            account_id_by_platform.items(),
            key=lambda item: (str(item[0]).strip(), str(item[1]).strip()),
        )
    ]


def _insert_product(
    connection,
    product: Product,
    *,
    source_type: str,
    source_ref: str,
    source_sha256: str,
    version: int,
    generation: int,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO product_catalog(
            internal_sku, product_name, grade, stem_length, unit, base_cost,
            sale_enabled, remark, source_type, source_ref, source_sha256,
            version, authority_generation, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product.internal_sku,
            product.product_name,
            product.grade,
            product.stem_length,
            product.unit,
            _decimal_text(product.base_cost),
            int(product.sale_enabled),
            product.remark,
            source_type,
            source_ref,
            source_sha256,
            version,
            generation,
            now,
            now,
        ),
    )


def _insert_mapping(
    connection,
    mapping: ProductMappingRecord,
    *,
    source_type: str,
    source_ref: str,
    source_sha256: str,
    version: int,
    generation: int,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO platform_product_mappings(
            mapping_id, platform_name, account_id,
            platform_product_identity_json, platform_product_identity_digest,
            platform_product_name, normalized_platform_product_name, grade,
            internal_sku, candidate_internal_skus_json, mapping_status,
            effective_from, effective_to, last_verified_at, remark,
            source_type, source_ref, source_sha256, version,
            authority_generation, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mapping.mapping_id,
            mapping.platform_name,
            mapping.account_id,
            mapping.platform_product_identity_json,
            mapping.platform_product_identity_digest,
            mapping.platform_product_name,
            mapping.normalized_platform_product_name,
            mapping.grade,
            mapping.internal_sku,
            json.dumps(list(mapping.candidate_internal_skus), separators=(",", ":")),
            mapping.mapping_status.value,
            mapping.effective_from.isoformat() if mapping.effective_from else None,
            mapping.effective_to.isoformat() if mapping.effective_to else None,
            None,
            mapping.remark,
            source_type,
            source_ref,
            source_sha256,
            version,
            generation,
            now,
            now,
        ),
    )


def _validate_mapping_skus(
    mapping: ProductMappingRecord, product_skus: set[str]
) -> None:
    referenced = {
        value
        for value in (
            mapping.internal_sku,
            mapping.candidate_internal_sku,
            *mapping.candidate_internal_skus,
        )
        if value
    }
    missing = sorted(referenced - product_skus)
    if missing:
        raise RuntimeMasterDataError(
            f"Mapping {mapping.mapping_id} references unknown SKU(s): {', '.join(missing)}"
        )


def _insert_event(
    connection,
    *,
    event_type: str,
    generation: int,
    actor: str,
    idempotency_key: str,
    request_sha256: str,
    source_ref: str,
    payload: Mapping[str, object],
    created_at: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO master_data_authority_events(
            event_id, event_type, authority_generation, actor,
            idempotency_key, request_sha256, source_ref, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "MDE-" + uuid4().hex,
            event_type,
            generation,
            actor,
            idempotency_key,
            request_sha256,
            source_ref,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def _event_replay(connection, actor: str, key: str, request_sha256: str) -> bool:
    row = connection.execute(
        "SELECT request_sha256 FROM master_data_authority_events "
        "WHERE actor = ? AND idempotency_key = ?",
        (actor, key),
    ).fetchone()
    if row is None:
        return False
    if str(row["request_sha256"]) != request_sha256:
        raise RuntimeMasterDataError(
            "Idempotency key was already used for a different master-data request."
        )
    return True


def _validated_product(
    *,
    internal_sku: str,
    product_name: str,
    grade: str,
    stem_length: str,
    unit: str,
    base_cost: Decimal,
    sale_enabled: bool,
    remark: str,
) -> Product:
    try:
        cost = Decimal(str(base_cost))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeMasterDataError("base_cost must be numeric.") from exc
    if not cost.is_finite() or cost <= 0:
        raise RuntimeMasterDataError("base_cost must be positive.")
    return Product(
        internal_sku=_required_text(internal_sku, "internal_sku").upper(),
        product_name=_required_text(product_name, "product_name"),
        grade=_required_text(grade, "grade"),
        stem_length=_required_text(stem_length, "stem_length"),
        unit=_required_text(unit, "unit"),
        base_cost=cost,
        current_stock=None,
        sale_enabled=bool(sale_enabled),
        remark=str(remark or "").strip(),
        metadata={"inventory_status": "NOT_INITIALIZED"},
    )


def _product_payload(product: Product) -> dict[str, object]:
    return {
        "internal_sku": product.internal_sku,
        "product_name": product.product_name,
        "grade": product.grade,
        "stem_length": product.stem_length,
        "unit": product.unit,
        "base_cost": _decimal_text(product.base_cost),
        "sale_enabled": product.sale_enabled,
        "remark": product.remark,
    }


def _decimal_text(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


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
