from __future__ import annotations

from decimal import Decimal
import json

import pytest

from app.repositories.master_data_repository import (
    RuntimeMasterDataError,
    RuntimeMasterDataRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import save_table_records
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION
from app.services.master_data_management import (
    CUTOVER_CONFIRMATION,
    ROLLBACK_CONFIRMATION,
    MasterDataManagementService,
)
from app.services.runtime_master_data import RuntimeMasterDataProvider


PLATFORM = "测试平台"
ACCOUNT = "account-main"


def _sources(tmp_path):
    products = tmp_path / "products.xlsx"
    mappings = tmp_path / "platform_mappings.xlsx"
    save_table_records(
        "products",
        products,
        [
            {
                "internal_sku": "SKU-001",
                "product_name": "艾莎",
                "grade": "A级",
                "stem_length": "60cm",
                "unit": "扎",
                "base_cost": "10.50",
                "current_stock": "7",
                "sale_enabled": "true",
                "last_price": "",
                "recommended_price": "",
                "remark": "",
                "feature_season": "",
                "feature_color": "",
            }
        ],
    )
    save_table_records(
        "platform_mappings",
        mappings,
        [
            {
                "mapping_id": "MAP-001",
                "mapping_kind": "PRODUCT",
                "platform_name": PLATFORM,
                "platform_product_id": "platform-product-1",
                "platform_product_name": "艾莎",
                "normalized_platform_product_name": "",
                "grade": "A级",
                "internal_sku": "SKU-001",
                "candidate_internal_sku": "",
                "search_keyword": "艾莎",
                "mapping_status": "VERIFIED",
                "effective_from": "",
                "effective_to": "",
                "last_verified_at": "",
                "remark": "",
            }
        ],
    )
    return products, mappings


def _import_candidate(runtime, products, mappings):
    service = MasterDataManagementService(runtime)
    preview = service.preview_workbook_import(
        products_workbook=products,
        platform_mappings_workbook=mappings,
        account_id_by_platform={PLATFORM: ACCOUNT},
    )
    receipt = service.import_from_workbooks(
        products_workbook=products,
        platform_mappings_workbook=mappings,
        account_id_by_platform={PLATFORM: ACCOUNT},
        expected_request_sha256=preview.request_sha256,
        actor="tester",
        idempotency_key="import-1",
    )
    return service, preview, receipt


def test_v19_additive_schema_preserves_v18_history_and_starts_pre_cutover(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    with runtime.connect_write() as connection:
        connection.execute(
            "INSERT INTO tasks(task_id, scope_type, scope_key, action_type, priority, "
            "task_status, created_at, updated_at) VALUES "
            "('TASK-OLD', 'sku', 'SKU-OLD', 'update_price', 1, 'pending', ?, ?)",
            ("2026-09-09T00:00:00+00:00", "2026-09-09T00:00:00+00:00"),
        )
    runtime.init_schema()

    assert LATEST_RUNTIME_SCHEMA_VERSION == 19
    assert runtime.schema_versions() == list(range(1, 20))
    assert runtime.check_schema_health().ok
    with runtime.connect_read() as connection:
        assert connection.execute(
            "SELECT task_id FROM tasks WHERE task_id = 'TASK-OLD'"
        ).fetchone()[0] == "TASK-OLD"
    state = RuntimeMasterDataRepository(runtime).authority_state()
    assert state.authority_mode == "PRE_CUTOVER"
    assert state.generation == 0


def test_import_keeps_inventory_uninitialized_and_mapping_account_scoped(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, preview, receipt = _import_candidate(runtime, products, mappings)

    assert preview.product_count == 1
    assert preview.mapping_count == 1
    assert receipt.status == "APPLIED"
    assert receipt.authority_generation == 1
    comparison = service.shadow_compare_workbooks(
        products_workbook=products,
        platform_mappings_workbook=mappings,
        account_id_by_platform={PLATFORM: ACCOUNT},
    )
    assert comparison.matched
    assert comparison.differences == ()
    repository = RuntimeMasterDataRepository(runtime)
    record = repository.get_product_record("SKU-001")
    assert record is not None
    assert record.inventory_status == "NOT_INITIALIZED"
    assert record.product.current_stock is None
    assert repository.compiled_mappings(account_id="another-account").records == ()
    compiled = repository.compiled_mappings(account_id=ACCOUNT)
    assert compiled.records[0].account_id == ACCOUNT
    assert compiled.records[0].platform_product_identity_digest.startswith("sha256:")
    with runtime.connect_read() as connection:
        assert connection.execute("SELECT count(*) FROM inventory_balances").fetchone()[0] == 0


def test_cutover_disables_workbook_fallback_and_binds_runtime_digests(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, _, imported = _import_candidate(runtime, products, mappings)
    service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="cutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )
    products.unlink()
    mappings.unlink()

    provider = RuntimeMasterDataProvider(
        runtime,
        configured_account_id=ACCOUNT,
        products_workbook=products,
        platform_mappings_workbook=mappings,
    )
    snapshot = provider.snapshot()
    assert snapshot.authority_mode == "DB_AUTHORITY"
    assert snapshot.authority_generation == 1
    assert snapshot.products[0].internal_sku == "SKU-001"
    assert snapshot.mappings.records[0].account_id == ACCOUNT
    assert snapshot.product_snapshot_sha256 == imported.product_snapshot_sha256
    assert snapshot.mapping_snapshot_sha256 == imported.mapping_snapshot_sha256


def test_rollback_is_blocked_after_authoritative_product_mutation(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, _, imported = _import_candidate(runtime, products, mappings)
    service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="cutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )
    created = service.create_product(
        internal_sku="SKU-NEW",
        product_name="卡布奇诺",
        grade="B级",
        stem_length="60cm",
        unit="扎",
        base_cost=Decimal("8.20"),
        sale_enabled=True,
        remark="",
        actor="operator",
        idempotency_key="product-create-1",
    )
    assert created.authority_generation == 2
    assert RuntimeMasterDataRepository(runtime).get_product_record(
        "SKU-NEW"
    ).inventory_status == "NOT_INITIALIZED"
    with pytest.raises(RuntimeMasterDataError, match="forward correction"):
        service.rollback_to_workbook_authority(
            actor="owner",
            idempotency_key="rollback-1",
            confirmation=ROLLBACK_CONFIRMATION,
        )


def test_safe_rollback_before_new_mutation_is_explicit_and_idempotent(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, _, imported = _import_candidate(runtime, products, mappings)
    service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="cutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )
    first = service.rollback_to_workbook_authority(
        actor="owner",
        idempotency_key="rollback-1",
        confirmation=ROLLBACK_CONFIRMATION,
    )
    replay = service.rollback_to_workbook_authority(
        actor="owner",
        idempotency_key="rollback-1",
        confirmation=ROLLBACK_CONFIRMATION,
    )
    assert first.status == "APPLIED"
    assert replay.status == "REPLAYED"
    assert RuntimeMasterDataRepository(runtime).authority_state().authority_mode == "PRE_CUTOVER"
    recutover = service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="recutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )
    assert recutover.status == "APPLIED"
    with pytest.raises(RuntimeMasterDataError, match="no longer matches"):
        service.rollback_to_workbook_authority(
            actor="owner",
            idempotency_key="rollback-1",
            confirmation=ROLLBACK_CONFIRMATION,
        )


def test_mapping_write_is_account_scoped_and_rejects_overlapping_conflict(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, _, imported = _import_candidate(runtime, products, mappings)
    service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="cutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )
    service.create_product(
        internal_sku="SKU-002",
        product_name="艾莎",
        grade="B级",
        stem_length="60cm",
        unit="扎",
        base_cost=Decimal("11.00"),
        sale_enabled=True,
        remark="",
        actor="operator",
        idempotency_key="product-create-2",
    )
    identity = {
        "platform_product_id": "platform-product-1",
        "platform_product_name": "艾莎",
        "grade": "A级",
    }
    other_account = service.create_mapping(
        mapping_id="MAP-OTHER-ACCOUNT",
        platform_name=PLATFORM,
        account_id="account-other",
        platform_product_identity=identity,
        platform_product_name="艾莎",
        grade="A级",
        mapping_status="VERIFIED",
        internal_sku="SKU-002",
        actor="operator",
        idempotency_key="mapping-create-other-account",
    )
    assert other_account.status == "APPLIED"
    with pytest.raises(RuntimeMasterDataError, match="multiple SKUs"):
        service.create_mapping(
            mapping_id="MAP-CONFLICT",
            platform_name=PLATFORM,
            account_id=ACCOUNT,
            platform_product_identity=identity,
            platform_product_name="艾莎",
            grade="A级",
            mapping_status="VERIFIED",
            internal_sku="SKU-002",
            actor="operator",
            idempotency_key="mapping-create-conflict",
        )


def test_runtime_mapping_derives_generation_bound_shadowbot_locator(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, _, imported = _import_candidate(runtime, products, mappings)
    service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="cutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )
    locator = tmp_path / "runtime" / "shadowbot-locator.json"
    provider = RuntimeMasterDataProvider(
        runtime,
        configured_account_id=ACCOUNT,
    )

    provider.ensure_shadowbot_locator(locator)
    first = locator.read_bytes()
    provider.ensure_shadowbot_locator(locator)
    payload = json.loads(first.decode("utf-8"))

    assert locator.read_bytes() == first
    assert payload["authority"] == "runtime_db_derived"
    assert payload["platform_name"] == PLATFORM
    assert payload["account_id"] == ACCOUNT
    assert payload["authority_generation"] == 1
    assert payload["mapping_snapshot_sha256"] == imported.mapping_snapshot_sha256
    assert payload["artifact_payload_sha256"].startswith("sha256:")
    assert payload["mappings"] == [
        {
            "expected_grade": "A级",
            "expected_product_name": "艾莎",
            "internal_sku": "SKU-001",
            "status": "active",
        }
    ]


def test_product_update_requires_expected_version_and_keeps_inventory_separate(
    tmp_path,
):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, _, imported = _import_candidate(runtime, products, mappings)
    service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="cutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )

    receipt = service.update_product(
        internal_sku="SKU-001",
        product_name="艾莎",
        grade="A级",
        stem_length="60cm",
        unit="扎",
        base_cost=Decimal("10.80"),
        sale_enabled=True,
        remark="cost correction",
        expected_version=1,
        actor="operator",
        idempotency_key="product-update-1",
    )
    with pytest.raises(RuntimeMasterDataError, match="version changed"):
        service.update_product(
            internal_sku="SKU-001",
            product_name="艾莎",
            grade="A级",
            stem_length="60cm",
            unit="扎",
            base_cost=Decimal("11.00"),
            sale_enabled=True,
            remark="stale update",
            expected_version=1,
            actor="operator",
            idempotency_key="product-update-stale",
        )

    record = RuntimeMasterDataRepository(runtime).get_product_record("SKU-001")
    assert receipt.version == 2
    assert record is not None
    assert record.version == 2
    assert record.product.base_cost == Decimal("10.8")
    assert record.inventory_status == "NOT_INITIALIZED"


def test_platform_side_effect_boundary_blocks_workbook_rollback(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    products, mappings = _sources(tmp_path)
    service, _, imported = _import_candidate(runtime, products, mappings)
    service.activate_runtime_authority(
        expected_product_snapshot_sha256=imported.product_snapshot_sha256,
        expected_mapping_snapshot_sha256=imported.mapping_snapshot_sha256,
        actor="owner",
        idempotency_key="cutover-1",
        confirmation=CUTOVER_CONFIRMATION,
    )
    state = RuntimeMasterDataRepository(runtime).authority_state()

    service.mark_platform_side_effect(
        operation_id="OP-PLATFORM-1",
        authority_generation=state.generation,
        mapping_snapshot_sha256=state.mapping_snapshot_sha256,
        actor="shadowbot_executor",
        idempotency_key="shadowbot-start:attempt-1",
    )

    with pytest.raises(RuntimeMasterDataError, match="forward correction"):
        service.rollback_to_workbook_authority(
            actor="owner",
            idempotency_key="rollback-after-write",
            confirmation=ROLLBACK_CONFIRMATION,
        )
