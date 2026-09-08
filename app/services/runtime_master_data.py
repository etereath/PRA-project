"""Single authority gate for Product Master and Platform Product Mapping."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.models import Product
from app.repositories.master_data_repository import (
    MasterDataAuthorityState,
    RuntimeMasterDataError,
    RuntimeMasterDataRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.services.product_mapping import (
    CompiledProductMappings,
    compile_product_mapping_rows,
    compile_product_mapping_workbook,
)


@dataclass(frozen=True, slots=True)
class RuntimeMasterDataSnapshot:
    authority_mode: str
    authority_generation: int
    account_id: str
    products: tuple[Product, ...]
    product_snapshot_sha256: str
    mappings: CompiledProductMappings
    mapping_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeProductSnapshot:
    authority_mode: str
    authority_generation: int
    products: tuple[Product, ...]
    product_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeMappingSnapshot:
    authority_mode: str
    authority_generation: int
    account_id: str
    mappings: CompiledProductMappings
    mapping_snapshot_sha256: str


class RuntimeMasterDataProvider:
    """Read both authorities under one gate; never fallback after cutover."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        configured_account_id: str = "",
        products_workbook: Path | None = None,
        platform_mappings_workbook: Path | None = None,
    ) -> None:
        self.runtime = runtime_repository
        self.repository = RuntimeMasterDataRepository(runtime_repository)
        self.configured_account_id = str(configured_account_id or "").strip()
        self.products_workbook = (
            Path(products_workbook) if products_workbook is not None else None
        )
        self.platform_mappings_workbook = (
            Path(platform_mappings_workbook)
            if platform_mappings_workbook is not None
            else None
        )

    def authority_state(self) -> MasterDataAuthorityState:
        return self.repository.authority_state()

    def snapshot(self, *, account_id: str | None = None) -> RuntimeMasterDataSnapshot:
        selected_account = str(
            self.configured_account_id if account_id is None else account_id
        ).strip()
        state = self.repository.authority_state()
        if state.authority_mode == "PRE_CUTOVER":
            return self._workbook_snapshot(state, selected_account)
        if state.authority_mode != "DB_AUTHORITY":
            raise RuntimeMasterDataError("Unsupported master-data authority mode.")
        return self._runtime_snapshot(selected_account)

    def product_cost_snapshot(
        self, internal_sku: str, *, account_id: str | None = None
    ) -> tuple[object, str]:
        snapshot = self.product_snapshot()
        sku = str(internal_sku or "").strip().upper()
        product = next(
            (item for item in snapshot.products if item.internal_sku == sku), None
        )
        if product is None:
            raise RuntimeMasterDataError("Product is not present in current authority.")
        return (
            product.base_cost,
            f"{snapshot.authority_mode.lower()}:product:"
            f"generation:{snapshot.authority_generation}:"
            f"{snapshot.product_snapshot_sha256}:{sku}",
        )

    def mapping_snapshot(
        self, *, account_id: str | None = None
    ) -> RuntimeMappingSnapshot:
        selected_account = str(
            self.configured_account_id if account_id is None else account_id
        ).strip()
        state = self.repository.authority_state()
        if state.authority_mode == "PRE_CUTOVER":
            if self.platform_mappings_workbook is None:
                raise RuntimeMasterDataError(
                    "Pre-cutover Platform Product Mapping workbook is not configured."
                )
            before = self.platform_mappings_workbook.read_bytes()
            mappings = compile_product_mapping_workbook(
                self.platform_mappings_workbook
            )
            if self.platform_mappings_workbook.read_bytes() != before:
                raise RuntimeMasterDataError(
                    "Pre-cutover Platform Product Mapping changed during snapshot read."
                )
            if selected_account:
                mappings = _scope_legacy_mappings(mappings, selected_account)
            return RuntimeMappingSnapshot(
                authority_mode=state.authority_mode,
                authority_generation=state.generation,
                account_id=selected_account,
                mappings=mappings,
                mapping_snapshot_sha256=(
                    "sha256:" + hashlib.sha256(before).hexdigest()
                ),
            )
        if state.authority_mode != "DB_AUTHORITY":
            raise RuntimeMasterDataError("Unsupported master-data authority mode.")
        if not selected_account:
            raise RuntimeMasterDataError(
                "configured target account_id is required after Product/Mapping cutover."
            )
        with self.runtime.connect_read() as connection:
            connection.execute("BEGIN")
            current = self.repository.authority_state(connection=connection)
            mappings = self.repository.compiled_mappings(
                account_id=selected_account, connection=connection
            )
            digest = self.repository.mapping_snapshot_sha256(connection=connection)
            if (
                current.authority_mode != "DB_AUTHORITY"
                or digest != current.mapping_snapshot_sha256
            ):
                raise RuntimeMasterDataError(
                    "Runtime Mapping snapshot does not match authority state."
                )
            return RuntimeMappingSnapshot(
                authority_mode=current.authority_mode,
                authority_generation=current.generation,
                account_id=selected_account,
                mappings=mappings,
                mapping_snapshot_sha256=digest,
            )

    def ensure_shadowbot_locator(self, path: Path) -> Path:
        """Materialize a generation-bound locator; it is never an authority."""

        target = Path(path)
        state = self.authority_state()
        if state.authority_mode == "PRE_CUTOVER":
            if not target.is_file():
                raise RuntimeMasterDataError(
                    "Pre-cutover ShadowBot locator is not configured."
                )
            return target
        if target.suffix.lower() != ".json":
            raise RuntimeMasterDataError(
                "Runtime-derived ShadowBot locator must use a .json target."
            )
        snapshot = self.mapping_snapshot()
        mappings: list[dict[str, str]] = []
        seen_skus: set[str] = set()
        platforms: set[str] = set()
        for record in snapshot.mappings.records:
            if record.mapping_status.value != "VERIFIED" or not record.internal_sku:
                continue
            sku = record.internal_sku.upper()
            if sku in seen_skus:
                raise RuntimeMasterDataError(
                    "Runtime mappings cannot derive one unambiguous ShadowBot locator "
                    f"for SKU {sku}."
                )
            seen_skus.add(sku)
            platforms.add(record.platform_name)
            mappings.append(
                {
                    "internal_sku": sku,
                    "expected_product_name": record.platform_product_name,
                    "expected_grade": record.grade,
                    "status": "active",
                }
            )
        if not mappings:
            raise RuntimeMasterDataError(
                "Runtime mappings contain no VERIFIED ShadowBot locator entries."
            )
        if len(platforms) != 1:
            raise RuntimeMasterDataError(
                "One derived ShadowBot locator must resolve to exactly one platform."
            )
        payload = {
            "schema_version": "runtime-derived-shadowbot-locator-v1",
            "authority": "runtime_db_derived",
            "account_id": snapshot.account_id,
            "platform_name": next(iter(platforms)),
            "authority_generation": snapshot.authority_generation,
            "mapping_snapshot_sha256": snapshot.mapping_snapshot_sha256,
            "mappings": sorted(mappings, key=lambda item: item["internal_sku"]),
        }
        payload["artifact_payload_sha256"] = "sha256:" + hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        content = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        try:
            if target.is_file() and target.read_bytes() == content:
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.{os.getpid()}.runtime-master-data.tmp"
            )
            temporary.write_bytes(content)
            os.replace(temporary, target)
        except OSError as exc:
            raise RuntimeMasterDataError(
                "Derived ShadowBot locator could not be written atomically."
            ) from exc
        return target

    def product_snapshot(self) -> RuntimeProductSnapshot:
        state = self.repository.authority_state()
        if state.authority_mode == "PRE_CUTOVER":
            if self.products_workbook is None:
                raise RuntimeMasterDataError(
                    "Pre-cutover Product workbook input is not configured."
                )
            before = self.products_workbook.read_bytes()
            products = tuple(load_products(self.products_workbook))
            if self.products_workbook.read_bytes() != before:
                raise RuntimeMasterDataError(
                    "Pre-cutover Product input changed during snapshot read."
                )
            return RuntimeProductSnapshot(
                authority_mode=state.authority_mode,
                authority_generation=state.generation,
                products=products,
                product_snapshot_sha256=(
                    "sha256:" + hashlib.sha256(before).hexdigest()
                ),
            )
        if state.authority_mode != "DB_AUTHORITY":
            raise RuntimeMasterDataError("Unsupported master-data authority mode.")
        with self.runtime.connect_read() as connection:
            connection.execute("BEGIN")
            current = self.repository.authority_state(connection=connection)
            products = self.repository.list_products(connection=connection)
            digest = self.repository.product_snapshot_sha256(connection=connection)
            if (
                current.authority_mode != "DB_AUTHORITY"
                or digest != current.product_snapshot_sha256
            ):
                raise RuntimeMasterDataError(
                    "Runtime Product snapshot does not match authority state."
                )
            return RuntimeProductSnapshot(
                authority_mode=current.authority_mode,
                authority_generation=current.generation,
                products=products,
                product_snapshot_sha256=digest,
            )

    def _runtime_snapshot(self, account_id: str) -> RuntimeMasterDataSnapshot:
        if not account_id:
            raise RuntimeMasterDataError(
                "configured target account_id is required after Product/Mapping cutover."
            )
        with self.runtime.connect_read() as connection:
            connection.execute("BEGIN")
            state = self.repository.authority_state(connection=connection)
            if state.authority_mode != "DB_AUTHORITY":
                raise RuntimeMasterDataError("Master-data authority changed during read.")
            products = self.repository.list_products(connection=connection)
            product_digest = self.repository.product_snapshot_sha256(
                connection=connection
            )
            mappings = self.repository.compiled_mappings(
                account_id=account_id, connection=connection
            )
            mapping_digest = self.repository.mapping_snapshot_sha256(
                connection=connection
            )
            if (
                product_digest != state.product_snapshot_sha256
                or mapping_digest != state.mapping_snapshot_sha256
            ):
                raise RuntimeMasterDataError(
                    "Runtime master-data snapshot does not match authority state."
                )
            return RuntimeMasterDataSnapshot(
                authority_mode=state.authority_mode,
                authority_generation=state.generation,
                account_id=account_id,
                products=products,
                product_snapshot_sha256=product_digest,
                mappings=mappings,
                mapping_snapshot_sha256=mapping_digest,
            )

    def _workbook_snapshot(
        self, state: MasterDataAuthorityState, account_id: str
    ) -> RuntimeMasterDataSnapshot:
        if self.products_workbook is None or self.platform_mappings_workbook is None:
            raise RuntimeMasterDataError(
                "Pre-cutover Product/Mapping workbook inputs are not configured."
            )
        products_before = self.products_workbook.read_bytes()
        mappings_before = self.platform_mappings_workbook.read_bytes()
        products = tuple(load_products(self.products_workbook))
        mappings = compile_product_mapping_workbook(self.platform_mappings_workbook)
        if (
            self.products_workbook.read_bytes() != products_before
            or self.platform_mappings_workbook.read_bytes() != mappings_before
        ):
            raise RuntimeMasterDataError(
                "Pre-cutover Product/Mapping input changed during snapshot read."
            )
        if account_id:
            mappings = _scope_legacy_mappings(mappings, account_id)
        return RuntimeMasterDataSnapshot(
            authority_mode=state.authority_mode,
            authority_generation=state.generation,
            account_id=account_id,
            products=products,
            product_snapshot_sha256=(
                "sha256:" + hashlib.sha256(products_before).hexdigest()
            ),
            mappings=mappings,
            mapping_snapshot_sha256=(
                "sha256:" + hashlib.sha256(mappings_before).hexdigest()
            ),
        )


def _scope_legacy_mappings(
    compiled: CompiledProductMappings, account_id: str
) -> CompiledProductMappings:
    rows = tuple(
        {
            "mapping_id": record.mapping_id,
            "platform_name": record.platform_name,
            "account_id": account_id,
            "platform_product_identity_json": (
                record.platform_product_identity_json
            ),
            "platform_product_identity_digest": (
                record.platform_product_identity_digest
            ),
            "platform_product_name": record.platform_product_name,
            "normalized_platform_product_name": (
                record.normalized_platform_product_name
            ),
            "grade": record.grade,
            "internal_sku": record.internal_sku,
            "candidate_internal_skus": list(record.candidate_internal_skus),
            "candidate_internal_sku": record.candidate_internal_sku,
            "mapping_status": record.mapping_status.value,
            "effective_from": (
                record.effective_from.isoformat() if record.effective_from else None
            ),
            "effective_to": (
                record.effective_to.isoformat() if record.effective_to else None
            ),
            "remark": record.remark,
        }
        for record in compiled.records
    )
    return compile_product_mapping_rows(
        rows,
        source_workbook_sha256=compiled.source_workbook_sha256,
    )
