from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import Product
from app.repositories.master_data_repository import RuntimeMasterDataRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION


def test_runtime_schema_gate_never_creates_missing_database(tmp_path) -> None:
    database = tmp_path / "missing.sqlite3"
    runtime = SQLiteRuntimeRepository(database)

    with pytest.raises(RuntimeError, match="requires healthy Runtime Schema v18"):
        runtime.require_current_schema(operation_name="synthetic service")

    assert not database.exists()


def test_v18_schema_has_runtime_product_and_mapping_authority(tmp_path) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()

    assert LATEST_RUNTIME_SCHEMA_VERSION == 18
    health = runtime.check_schema_health()
    assert health.ok, health.summary
    with closing(runtime.connect_read()) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"product_catalog", "platform_product_mappings"} <= tables
        assert tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations "
                "ORDER BY schema_version"
            ).fetchall()
        ) == tuple(range(1, 19))


def test_runtime_master_data_seed_and_readback_do_not_need_workbooks(tmp_path) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    catalog = RuntimeMasterDataRepository(runtime)
    product = Product(
        internal_sku="AISHA-A-50-Z",
        product_name="艾莎",
        grade="A级",
        stem_length="50cm",
        unit="扎",
        base_cost=Decimal("5.00"),
        current_stock=72,
        sale_enabled=True,
    )
    result = catalog.seed(
        [product],
        [
            {
                "mapping_id": "PLATFORM-01",
                "mapping_kind": "PLATFORM",
                "platform_name": "蚂蚁花团供应商",
                "mapping_status": "ACTIVE",
            },
            {
                "mapping_id": "MAP-AISHA-A",
                "mapping_kind": "PRODUCT",
                "platform_name": "蚂蚁花团供应商",
                "platform_product_name": "艾莎",
                "normalized_platform_product_name": "艾莎",
                "grade": "A级",
                "internal_sku": "AISHA-A-50-Z",
                "mapping_status": "VERIFIED",
            },
        ],
        product_source_ref="one-time-import:products",
        product_source_sha256="sha256:" + "a" * 64,
        mapping_source_ref="one-time-import:mappings",
        mapping_source_sha256="sha256:" + "b" * 64,
        actor="test",
    )

    assert result["product_count"] == 1
    assert result["mapping_count"] == 2
    assert result["product_snapshot_sha256"].startswith("sha256:")
    products = catalog.list_products()
    assert len(products) == 1
    assert products[0].internal_sku == "AISHA-A-50-Z"
    assert products[0].current_stock == 0
    compiled = catalog.compiled_mappings()
    assert compiled.mapping_version
    resolution = compiled.resolve(
        platform_name="蚂蚁花团供应商",
        platform_product_name="艾莎",
        grade="A级",
        observed_at=datetime.now(timezone.utc),
    )
    assert resolution.internal_sku == "AISHA-A-50-Z"
