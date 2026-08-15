from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from app.repositories.master_data_repository import RuntimeMasterDataRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.master_data_management import (
    MasterDataManagementError,
    MasterDataManagementService,
)


@pytest.fixture()
def runtime(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            UPDATE inventory_authority_state
            SET authority_mode = 'DB_AUTHORITY',
                bootstrap_snapshot_sha256 = ?,
                bootstrap_runtime_snapshot_sha256 = ?,
                bootstrap_sales_watermark_date = '2026-08-14',
                bootstrap_idempotency_key = 'synthetic-cutover',
                bootstrap_completed_at = '2026-08-15T00:00:00+00:00',
                bootstrap_completed_by = 'test', version = 1,
                updated_at = '2026-08-15T00:00:00+00:00'
            WHERE authority_key = 'REAL_INVENTORY'
            """,
            ("sha256:" + "1" * 64, "sha256:" + "2" * 64),
        )
    return repository


def test_create_product_and_zero_inventory_are_one_idempotent_transaction(
    runtime: SQLiteRuntimeRepository,
) -> None:
    service = MasterDataManagementService(runtime)
    request = dict(
        internal_sku="rose-a-50-z",
        product_name="艾莎",
        grade="A级",
        stem_length="50cm",
        unit="扎",
        base_cost=Decimal("8.50"),
        sale_enabled=True,
        remark="测试商品",
        actor="admin",
        idempotency_key="web-product:create:1",
    )

    first = service.create_product(**request)
    replay = service.create_product(**request)

    assert (first.status, first.version) == ("APPLIED", 1)
    assert (replay.status, replay.version) == ("REPLAYED", 1)
    product = RuntimeMasterDataRepository(runtime).get_product("ROSE-A-50-Z")
    assert product is not None
    assert product.product_name == "艾莎"
    assert product.current_stock == 0
    with closing(runtime.connect_read()) as connection:
        transactions = connection.execute(
            "SELECT transaction_type FROM inventory_transactions WHERE internal_sku = ?",
            ("ROSE-A-50-Z",),
        ).fetchall()
    assert [row[0] for row in transactions] == ["SKU_INITIALIZATION"]


def test_product_update_requires_current_version_and_keeps_inventory(
    runtime: SQLiteRuntimeRepository,
) -> None:
    service = MasterDataManagementService(runtime)
    service.create_product(
        internal_sku="ROSE-A-50-Z",
        product_name="艾莎",
        grade="A级",
        stem_length="50cm",
        unit="扎",
        base_cost=Decimal("8.50"),
        sale_enabled=True,
        remark="",
        actor="admin",
        idempotency_key="create",
    )

    receipt = service.update_product(
        internal_sku="ROSE-A-50-Z",
        product_name="艾莎",
        grade="A级",
        stem_length="50cm",
        unit="扎",
        base_cost=Decimal("9.00"),
        sale_enabled=False,
        remark="暂停销售",
        expected_version=1,
        actor="admin",
        idempotency_key="update-1",
    )

    assert receipt.version == 2
    product = RuntimeMasterDataRepository(runtime).get_product("ROSE-A-50-Z")
    assert product is not None
    assert product.base_cost == Decimal("9.00")
    assert product.sale_enabled is False
    assert product.current_stock == 0
    with pytest.raises(MasterDataManagementError, match="刷新"):
        service.update_product(
            internal_sku="ROSE-A-50-Z",
            product_name="艾莎",
            grade="A级",
            stem_length="50cm",
            unit="扎",
            base_cost=Decimal("10.00"),
            sale_enabled=True,
            remark="",
            expected_version=1,
            actor="admin",
            idempotency_key="stale-update",
        )


def test_mapping_create_update_disable_and_conflict(
    runtime: SQLiteRuntimeRepository,
) -> None:
    service = MasterDataManagementService(runtime)
    service.create_product(
        internal_sku="ROSE-A-50-Z",
        product_name="艾莎",
        grade="A级",
        stem_length="50cm",
        unit="扎",
        base_cost=Decimal("8.50"),
        sale_enabled=True,
        remark="",
        actor="admin",
        idempotency_key="create",
    )
    created = service.save_product_mapping(
        mapping_id="MAP-A",
        platform_name="蚂蚁花团供应商",
        platform_product_name="艾莎 20枝/扎",
        grade="A级",
        internal_sku="ROSE-A-50-Z",
        search_keyword="艾莎",
        mapping_status="VERIFIED",
        remark="",
        expected_version=0,
        actor="admin",
        idempotency_key="mapping-create",
    )
    disabled = service.save_product_mapping(
        mapping_id="MAP-A",
        platform_name="蚂蚁花团供应商",
        platform_product_name="艾莎 20枝/扎",
        grade="A级",
        internal_sku=None,
        search_keyword="艾莎",
        mapping_status="DISABLED",
        remark="暂不使用",
        expected_version=1,
        actor="admin",
        idempotency_key="mapping-disable",
    )

    assert (created.version, disabled.version) == (1, 2)
    mapping = RuntimeMasterDataRepository(runtime).list_mapping_records()[0]
    assert mapping.mapping_status == "DISABLED"
    assert mapping.internal_sku is None
    with pytest.raises(MasterDataManagementError, match="刷新"):
        service.save_product_mapping(
            mapping_id="MAP-A",
            platform_name="蚂蚁花团供应商",
            platform_product_name="艾莎 20枝/扎",
            grade="A级",
            internal_sku="ROSE-A-50-Z",
            search_keyword="艾莎",
            mapping_status="VERIFIED",
            remark="",
            expected_version=1,
            actor="admin",
            idempotency_key="mapping-stale",
        )


def test_mapping_create_without_id_replays_with_same_generated_identity(
    runtime: SQLiteRuntimeRepository,
) -> None:
    service = MasterDataManagementService(runtime)
    service.create_product(
        internal_sku="ROSE-A-50-Z",
        product_name="艾莎",
        grade="A级",
        stem_length="50cm",
        unit="扎",
        base_cost=Decimal("8.50"),
        sale_enabled=True,
        remark="",
        actor="admin",
        idempotency_key="create",
    )
    request = dict(
        mapping_id="",
        platform_name="蚂蚁花团供应商",
        platform_product_name="艾莎 20枝/扎",
        grade="A级",
        internal_sku="ROSE-A-50-Z",
        search_keyword="艾莎",
        mapping_status="VERIFIED",
        remark="",
        expected_version=0,
        actor="admin",
        idempotency_key="mapping-generated-id",
    )

    created = service.save_product_mapping(**request)
    replayed = service.save_product_mapping(**request)

    assert created.entity_id == replayed.entity_id
    assert (created.status, replayed.status) == ("APPLIED", "REPLAYED")
    assert len(RuntimeMasterDataRepository(runtime).list_mapping_records()) == 1


def test_create_product_rolls_back_if_inventory_authority_is_not_ready(
    tmp_path: Path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    service = MasterDataManagementService(runtime)

    with pytest.raises(MasterDataManagementError, match="库存尚未切换"):
        service.create_product(
            internal_sku="ROSE-A-50-Z",
            product_name="艾莎",
            grade="A级",
            stem_length="50cm",
            unit="扎",
            base_cost=Decimal("8.50"),
            sale_enabled=True,
            remark="",
            actor="admin",
            idempotency_key="create",
        )

    assert RuntimeMasterDataRepository(runtime).get_product("ROSE-A-50-Z") is None
