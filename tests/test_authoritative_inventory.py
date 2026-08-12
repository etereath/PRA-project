from __future__ import annotations

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from app.inventory_models import InventoryTransaction
from app.models import Product
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.operational_incident_repository import OperationalIncidentRepository
from app.services.authoritative_inventory import (
    InventoryApplicationService,
    InventoryAuthorityError,
    InventoryConflictError,
    InventoryInsufficientError,
    InventoryProvider,
    sqlite_logical_snapshot_sha256,
)
from app.services.inventory_alert import InventoryAlertService
from app.enums import IncidentCategory
from tests.inventory_cutover_support import insert_cutover_order_snapshot


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SNAPSHOT_SHA256 = "sha256:" + "a" * 64
CUTOVER_BATCH_ID = "cutover-empty-open"


def _repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    insert_cutover_order_snapshot(
        repository,
        batch_id=CUTOVER_BATCH_ID,
        observed_at=NOW - timedelta(minutes=1),
        platform_trade_date=date(2026, 8, 13),
    )
    return repository


def _products() -> list[Product]:
    return [
        Product(
            internal_sku="AISHA-A-50-Z",
            product_name="艾莎",
            grade="A级",
            stem_length="50cm",
            unit="扎",
            base_cost=Decimal("5.00"),
            current_stock=72,
            sale_enabled=True,
        ),
        Product(
            internal_sku="AISHA-B-50-Z",
            product_name="艾莎",
            grade="B级",
            stem_length="50cm",
            unit="扎",
            base_cost=Decimal("4.00"),
            current_stock=41,
            sale_enabled=True,
        ),
    ]


def _bootstrap(repository: SQLiteRuntimeRepository) -> InventoryApplicationService:
    known_skus = {item.internal_sku for item in _products()} | {"NEW-SKU"}
    service = InventoryApplicationService(
        repository,
        clock=lambda: NOW,
        product_exists=lambda sku: sku in known_skus,
    )
    result = service.bootstrap(
        _products(),
        snapshot_sha256=SNAPSHOT_SHA256,
        runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
        cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
        idempotency_key="bootstrap:2026-08-12",
        actor="admin",
        freeze_validator=lambda: True,
    )
    assert result.status == "APPLIED"
    return service


def test_v17_starts_pre_cutover_and_provider_uses_workbook_stock(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    state = InventoryRepository(repository).get_authority_state()
    hydrated = InventoryProvider(InventoryRepository(repository)).hydrate_products(
        _products()
    )

    assert state.authority_mode == "PRE_CUTOVER"
    assert [item.current_stock for item in hydrated] == [72, 41]


def test_bootstrap_is_atomic_switch_with_immutable_ledger_and_exact_replay(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)

    replay = service.bootstrap(
        _products(),
        snapshot_sha256=SNAPSHOT_SHA256,
        runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
        cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
        idempotency_key="bootstrap:2026-08-12",
        actor="admin",
        freeze_validator=lambda: True,
    )
    inventory = InventoryRepository(repository)

    assert replay.status == "REPLAYED"
    assert inventory.get_authority_state().authority_mode == "DB_AUTHORITY"
    assert [(item.internal_sku, item.current_qty) for item in inventory.list_balances()] == [
        ("AISHA-A-50-Z", 72),
        ("AISHA-B-50-Z", 41),
    ]
    transactions = inventory.list_transactions()
    assert len(transactions) == 2
    assert {item.transaction_type for item in transactions} == {"BOOTSTRAP"}
    assert all(
        any(
            ref.startswith(
                f"ORDER_OBSERVATION_BATCH:{CUTOVER_BATCH_ID}:sha256:"
            )
            for ref in item.supporting_refs
        )
        for item in transactions
    )
    with closing(repository.connect_write()) as connection, pytest.raises(
        Exception,
        match="append-only",
    ):
        connection.execute(
            "UPDATE inventory_transactions SET reason = 'changed'"
        )


def test_bootstrap_conflict_does_not_replace_authoritative_balances(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)
    changed = _products()
    changed[0].current_stock = 99

    with pytest.raises(InventoryConflictError):
        service.bootstrap(
            changed,
            snapshot_sha256="sha256:" + "b" * 64,
            runtime_snapshot_sha256=(
                InventoryRepository(repository)
                .get_authority_state()
                .bootstrap_runtime_snapshot_sha256
            ),
            cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
            idempotency_key="bootstrap:other",
            actor="admin",
            freeze_validator=lambda: True,
        )

    assert InventoryRepository(repository).get_balance("AISHA-A-50-Z").current_qty == 72


def test_bootstrap_same_key_and_hash_with_different_content_is_not_a_replay(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)
    changed = _products()
    changed[0].current_stock = 99

    with pytest.raises(InventoryConflictError, match="重放内容"):
        service.bootstrap(
            changed,
            snapshot_sha256=SNAPSHOT_SHA256,
            runtime_snapshot_sha256=(
                InventoryRepository(repository)
                .get_authority_state()
                .bootstrap_runtime_snapshot_sha256
            ),
            cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
            idempotency_key="bootstrap:2026-08-12",
            actor="admin",
            freeze_validator=lambda: True,
        )

    assert InventoryRepository(repository).get_balance(
        "AISHA-A-50-Z"
    ).current_qty == 72


@pytest.mark.parametrize("with_baseline", [False, True])
def test_bootstrap_rejects_nonempty_inventory_tables_and_stays_pre_cutover(
    tmp_path: Path,
    with_baseline: bool,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO inventory_transactions(
                transaction_id, internal_sku,
                inventory_before, inventory_delta, inventory_after,
                transaction_type, source_type, source_ref_id,
                reason, actor, supporting_refs_json,
                idempotency_key, request_sha256,
                balance_version_after, occurred_at, recorded_at
            ) VALUES (
                'ORPHAN-TX', 'AISHA-A-50-Z', 0, 0, 0,
                'SALES_BASELINE_SYNC', 'TEST', 'TEST-1',
                '切换前残留', 'test', '[]',
                'orphan:1', ?, 1, ?, ?
            )
            """,
            ("sha256:" + "e" * 64, NOW.isoformat(), NOW.isoformat()),
        )
        if with_baseline:
            connection.execute(
                """
                INSERT INTO inventory_sales_baselines(
                    platform_name, platform_trade_date, internal_sku,
                    selected_fact_source, quality_level, selected_sold_qty,
                    source_ref_id, source_sha256, mapping_version,
                    supporting_refs_json, inventory_transaction_id,
                    version, updated_at
                ) VALUES (
                    'platform', '2026-08-11', 'AISHA-A-50-Z',
                    'ORDER_OBSERVED', 'ORDER_COMPLETE', 3,
                    'SUMMARY-OLD', ?, 'mapping-v1', '[]',
                    'ORPHAN-TX', 1, ?
                )
                """,
                ("sha256:" + "d" * 64, NOW.isoformat()),
            )
    service = InventoryApplicationService(repository, clock=lambda: NOW)

    with pytest.raises(InventoryConflictError, match="三表"):
        service.bootstrap(
            _products(),
            snapshot_sha256=SNAPSHOT_SHA256,
            runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
            cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
            idempotency_key=f"bootstrap:nonempty:{with_baseline}",
            actor="admin",
            freeze_validator=lambda: True,
        )

    assert (
        InventoryRepository(repository).get_authority_state().authority_mode
        == "PRE_CUTOVER"
    )


def test_bootstrap_freeze_failure_rolls_back_and_stays_pre_cutover(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    freeze_checks = iter((True, False))

    with pytest.raises(InventoryConflictError, match="发生变化"):
        InventoryApplicationService(repository, clock=lambda: NOW).bootstrap(
            _products(),
            snapshot_sha256=SNAPSHOT_SHA256,
            runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
            cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
            idempotency_key="bootstrap:freeze-failed",
            actor="admin",
            freeze_validator=lambda: next(freeze_checks),
        )

    inventory = InventoryRepository(repository)
    assert inventory.get_authority_state().authority_mode == "PRE_CUTOVER"
    assert inventory.list_balances() == ()
    assert inventory.list_transactions() == ()


def test_bootstrap_rejects_runtime_change_after_logical_snapshot(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    frozen_snapshot = sqlite_logical_snapshot_sha256(repository)
    inventory = InventoryRepository(repository)
    policy = inventory.get_alert_policy(internal_sku="AISHA-A-50-Z")
    inventory.save_alert_policy(
        scope_type="DEFAULT",
        scope_key="*",
        enabled=True,
        threshold_qty=10,
        repeat_interval_minutes=60,
        updated_by="admin",
        expected_version=policy.version,
        updated_at=NOW,
    )

    with pytest.raises(InventoryConflictError, match="逻辑快照"):
        InventoryApplicationService(repository, clock=lambda: NOW).bootstrap(
            _products(),
            snapshot_sha256=SNAPSHOT_SHA256,
            runtime_snapshot_sha256=frozen_snapshot,
            cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
            idempotency_key="bootstrap:stale-runtime-snapshot",
            actor="admin",
            freeze_validator=lambda: True,
        )

    assert inventory.get_authority_state().authority_mode == "PRE_CUTOVER"
    assert inventory.list_balances() == ()


def test_bootstrap_requires_workbook_freeze_validator(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="冻结校验器"):
        InventoryApplicationService(repository, clock=lambda: NOW).bootstrap(
            _products(),
            snapshot_sha256=SNAPSHOT_SHA256,
            runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
            cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
            idempotency_key="bootstrap:missing-freeze-validator",
            actor="admin",
        )

    inventory = InventoryRepository(repository)
    assert inventory.get_authority_state().authority_mode == "PRE_CUTOVER"
    assert inventory.list_balances() == ()
    assert inventory.list_transactions() == ()


def test_bootstrap_rejects_nonempty_open_cutover_snapshot(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    insert_cutover_order_snapshot(
        repository,
        batch_id="cutover-open-with-orders",
        observed_at=NOW - timedelta(seconds=30),
        platform_trade_date=date(2026, 8, 13),
        order_quantities=(6,),
    )
    batch_id = insert_cutover_order_snapshot(
        repository,
        batch_id="cutover-latest-empty-after-orders",
        observed_at=NOW,
        platform_trade_date=date(2026, 8, 13),
    )

    with pytest.raises(InventoryConflictError, match="曾经观察到订单"):
        InventoryApplicationService(repository, clock=lambda: NOW).bootstrap(
            _products(),
            snapshot_sha256=SNAPSHOT_SHA256,
            runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
            cutover_order_observation_batch_id=batch_id,
            idempotency_key="bootstrap:open-orders-refused",
            actor="admin",
            freeze_validator=lambda: True,
        )

    inventory = InventoryRepository(repository)
    assert inventory.get_authority_state().authority_mode == "PRE_CUTOVER"
    assert inventory.list_balances() == ()
    assert inventory.list_transactions() == ()


def test_bootstrap_uses_runtime_operational_time_policy_version(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.replace_current_operational_time_policy(
        expected_current_policy_version="CN_SINGLE_PLATFORM_2026_V1",
        successor_policy_version="CN_SINGLE_PLATFORM_2026_V2",
        effective_from=NOW - timedelta(hours=1),
        platform_cutoff_local_time="21:00:00",
        seller_cutoff_local_time="22:00:00",
        peak_start_local_time="20:00:00",
        created_by="test",
    )
    batch_id = insert_cutover_order_snapshot(
        repository,
        batch_id="cutover-empty-open-v2",
        observed_at=NOW - timedelta(minutes=1),
        platform_trade_date=date(2026, 8, 12),
        time_policy_version="CN_SINGLE_PLATFORM_2026_V2",
    )

    result = InventoryApplicationService(
        repository,
        clock=lambda: NOW,
    ).bootstrap(
        _products(),
        snapshot_sha256=SNAPSHOT_SHA256,
        runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
        cutover_order_observation_batch_id=batch_id,
        idempotency_key="bootstrap:runtime-policy-v2",
        actor="admin",
        freeze_validator=lambda: True,
    )

    assert result.authority_state.bootstrap_sales_watermark_date == date(
        2026,
        8,
        12,
    )
    assert result.status == "APPLIED"


def test_manual_signed_delta_has_defaults_and_optimistic_lock(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)

    result = service.adjust(
        internal_sku="AISHA-A-50-Z",
        inventory_delta=8,
        actor="operator",
        idempotency_key="manual:1",
        expected_version=1,
    )
    replay = service.adjust(
        internal_sku="AISHA-A-50-Z",
        inventory_delta=8,
        actor="operator",
        idempotency_key="manual:1",
        expected_version=1,
    )

    assert result.status == "APPLIED"
    assert result.transaction.transaction_type == "MANUAL_INBOUND"
    assert result.transaction.source_type == "NEW_FLOWER_INBOUND"
    assert result.transaction.reason == "新花入库"
    assert result.balance.current_qty == 80
    assert replay.status == "REPLAYED"
    with pytest.raises(InventoryConflictError):
        service.adjust(
            internal_sku="AISHA-A-50-Z",
            inventory_delta=-1,
            source_type="MANUAL_STOCKTAKE",
            reason="人工盘点修正",
            actor="operator",
            idempotency_key="manual:2",
            expected_version=1,
        )


def test_manual_adjustment_rejects_negative_balance_and_rolls_back(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)

    with pytest.raises(InventoryInsufficientError):
        service.adjust(
            internal_sku="AISHA-B-50-Z",
            inventory_delta=-42,
            source_type="MANUAL_STOCKTAKE",
            reason="人工盘点修正",
            actor="operator",
            idempotency_key="manual:negative",
        )

    inventory = InventoryRepository(repository)
    assert inventory.get_balance("AISHA-B-50-Z").current_qty == 41
    assert inventory.get_transaction_by_idempotency_key("manual:negative") is None


def test_manual_adjustment_rejects_unstructured_source(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)

    with pytest.raises(ValueError, match="允许范围"):
        service.adjust(
            internal_sku="AISHA-A-50-Z",
            inventory_delta=1,
            source_type="ARBITRARY_CLIENT_VALUE",
            reason="客户端伪造来源",
            actor="operator",
            idempotency_key="manual:bad-source",
        )

    assert InventoryRepository(repository).get_balance(
        "AISHA-A-50-Z"
    ).current_qty == 72


@pytest.mark.parametrize(
    ("source_type", "inventory_delta"),
    [
        ("NEW_FLOWER_INBOUND", -1),
        ("NEW_FLOWER_INBOUND", 0),
        ("LOSS_ADJUSTMENT", 1),
        ("LOSS_ADJUSTMENT", 0),
    ],
)
def test_manual_adjustment_rejects_source_direction_mismatch(
    tmp_path: Path,
    source_type: str,
    inventory_delta: int,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)

    with pytest.raises(ValueError, match="必须|不能为 0"):
        service.adjust(
            internal_sku="AISHA-A-50-Z",
            inventory_delta=inventory_delta,
            source_type=source_type,
            reason="方向不一致",
            actor="operator",
            idempotency_key=f"manual:direction:{source_type}",
        )

    assert InventoryRepository(repository).get_balance(
        "AISHA-A-50-Z"
    ).current_qty == 72


def test_db_authority_never_falls_back_when_a_balance_is_missing(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _bootstrap(repository)
    products = _products()
    products.append(
        Product(
            internal_sku="NEW-SKU",
            product_name="新品种",
            grade="A级",
            stem_length="50cm",
            unit="扎",
            base_cost=Decimal("6.00"),
            current_stock=20,
            sale_enabled=True,
        )
    )

    with pytest.raises(InventoryAuthorityError, match="NEW-SKU"):
        InventoryProvider(InventoryRepository(repository)).hydrate_products(products)


def test_new_sku_requires_explicit_zero_balance_before_inbound(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)

    initialized = service.initialize_sku(
        internal_sku="NEW-SKU",
        actor="admin",
        idempotency_key="sku-init:NEW-SKU",
    )
    replay = service.initialize_sku(
        internal_sku="NEW-SKU",
        actor="admin",
        idempotency_key="sku-init:NEW-SKU",
    )
    inbound = service.adjust(
        internal_sku="NEW-SKU",
        inventory_delta=20,
        actor="operator",
        idempotency_key="manual:new-sku:inbound",
        expected_version=1,
    )

    assert initialized.transaction.transaction_type == "SKU_INITIALIZATION"
    assert initialized.balance.current_qty == 0
    assert replay.status == "REPLAYED"
    assert inbound.balance.current_qty == 20


def test_new_sku_initialization_rejects_orphan_metadata(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = _bootstrap(repository)

    with pytest.raises(InventoryAuthorityError, match="商品主数据"):
        service.initialize_sku(
            internal_sku="ORPHAN-SKU",
            actor="admin",
            idempotency_key="sku-init:orphan",
        )

    assert InventoryRepository(repository).get_balance("ORPHAN-SKU") is None


def test_inventory_transaction_model_keeps_trade_day_dimensions() -> None:
    transaction = InventoryTransaction(
        transaction_id="INV-TXN-1",
        internal_sku="AISHA-A-50-Z",
        inventory_before=72,
        inventory_delta=-5,
        inventory_after=67,
        transaction_type="SALES_DEDUCTION",
        source_type="PLATFORM_TRADE_DAY_SUMMARY",
        source_ref_id="SUMMARY-1",
        reason="销售扣减",
        actor="pipeline",
        seller_operation_date=date(2026, 8, 13),
        platform_name="platform",
        platform_trade_date=date(2026, 8, 12),
        supporting_refs=("ORDER:1:sha256:x",),
        idempotency_key="inventory-sales:1",
        request_sha256="sha256:" + "f" * 64,
        balance_version_after=2,
        occurred_at=NOW,
        recorded_at=NOW,
    )

    assert transaction.platform_trade_date == date(2026, 8, 12)
    assert transaction.seller_operation_date == date(2026, 8, 13)


def test_inventory_alert_reuses_incident_outbox_repeat_and_recovery(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    inventory = InventoryRepository(repository)
    default_policy = inventory.get_alert_policy(internal_sku="AISHA-A-50-Z")
    assert default_policy is not None and not default_policy.enabled
    inventory.save_alert_policy(
        scope_type="DEFAULT",
        scope_key="*",
        enabled=True,
        threshold_qty=10,
        repeat_interval_minutes=30,
        updated_by="admin",
        expected_version=default_policy.version,
        updated_at=NOW,
    )
    alerts = InventoryAlertService(repository)
    service = InventoryApplicationService(
        repository,
        clock=lambda: NOW,
        alert_evaluator=alerts.evaluate_transaction,
    )
    service.bootstrap(
        [replace_product(_products()[0], current_stock=12)],
        snapshot_sha256=SNAPSHOT_SHA256,
        runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(repository),
        cutover_order_observation_batch_id=CUTOVER_BATCH_ID,
        idempotency_key="bootstrap:alert",
        actor="admin",
        freeze_validator=lambda: True,
    )

    detected = service.adjust(
        internal_sku="AISHA-A-50-Z",
        inventory_delta=-3,
        source_type="MANUAL_STOCKTAKE",
        reason="测试低库存",
        actor="operator",
        idempotency_key="alert:detected",
        occurred_at=NOW + timedelta(minutes=1),
    )
    assert detected.reason == ""
    active = OperationalIncidentRepository(repository).list_active(
        category=IncidentCategory.INVENTORY_ANOMALY
    )
    assert len(active) == 1
    assert active[0].subject_key == "AISHA-A-50-Z"
    assert len(repository.list_notification_outbox()) == 1

    service.adjust(
        internal_sku="AISHA-A-50-Z",
        inventory_delta=-1,
        source_type="MANUAL_STOCKTAKE",
        reason="仍然偏低",
        actor="operator",
        idempotency_key="alert:suppressed",
        occurred_at=NOW + timedelta(minutes=5),
    )
    assert len(repository.list_notification_outbox()) == 1

    service.adjust(
        internal_sku="AISHA-A-50-Z",
        inventory_delta=-1,
        source_type="MANUAL_STOCKTAKE",
        reason="重复提醒",
        actor="operator",
        idempotency_key="alert:repeat",
        occurred_at=NOW + timedelta(minutes=35),
    )
    assert len(repository.list_notification_outbox()) == 2

    service.adjust(
        internal_sku="AISHA-A-50-Z",
        inventory_delta=5,
        source_type="NEW_FLOWER_INBOUND",
        reason="新花入库",
        actor="operator",
        idempotency_key="alert:recovered",
        occurred_at=NOW + timedelta(minutes=40),
    )
    assert OperationalIncidentRepository(repository).list_active(
        category=IncidentCategory.INVENTORY_ANOMALY
    ) == []
    assert len(repository.list_notification_outbox()) == 3


def test_concurrent_first_low_inventory_alert_enqueues_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    inventory = InventoryRepository(repository)
    policy = inventory.get_alert_policy(internal_sku="AISHA-A-50-Z")
    inventory.save_alert_policy(
        scope_type="DEFAULT",
        scope_key="*",
        enabled=True,
        threshold_qty=10,
        repeat_interval_minutes=30,
        updated_by="admin",
        expected_version=policy.version,
        updated_at=NOW,
    )
    alerts = InventoryAlertService(repository)
    original_list_active = alerts.incidents.list_active
    barrier = Barrier(2)

    def _concurrent_empty_read(*, category):
        rows = original_list_active(category=category)
        barrier.wait(timeout=5)
        return rows

    monkeypatch.setattr(alerts.incidents, "list_active", _concurrent_empty_read)
    transactions = tuple(
        InventoryTransaction(
            transaction_id=f"INV-CONCURRENT-{index}",
            internal_sku="AISHA-A-50-Z",
            inventory_before=11,
            inventory_delta=-2,
            inventory_after=9,
            transaction_type="MANUAL_ADJUSTMENT",
            source_type="MANUAL_STOCKTAKE",
            source_ref_id=f"concurrent:{index}",
            reason="并发首次越界",
            actor="operator",
            seller_operation_date=None,
            platform_name=None,
            platform_trade_date=None,
            supporting_refs=(),
            idempotency_key=f"concurrent:{index}",
            request_sha256="sha256:" + str(index) * 64,
            balance_version_after=index + 2,
            occurred_at=NOW,
            recorded_at=NOW,
        )
        for index in (1, 2)
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(alerts.evaluate_transaction, transactions))

    assert {item.status for item in results} == {"DETECTED", "REPEATED"}
    assert len({item.incident_id for item in results}) == 1
    assert len(repository.list_notification_outbox()) == 1


@pytest.mark.parametrize(
    ("threshold", "repeat"),
    [(-1, 60), (10000, 60), (10, 29), (10, 1441)],
)
def test_inventory_alert_policy_rejects_out_of_range_values(
    tmp_path: Path,
    threshold: int,
    repeat: int,
) -> None:
    repository = _repository(tmp_path)
    inventory = InventoryRepository(repository)

    with pytest.raises(ValueError):
        inventory.save_alert_policy(
            scope_type="SKU",
            scope_key="AISHA-A-50-Z",
            enabled=True,
            threshold_qty=threshold,
            repeat_interval_minutes=repeat,
            updated_by="admin",
            expected_version=0,
            updated_at=NOW,
        )


def replace_product(product: Product, *, current_stock: int) -> Product:
    return Product(
        internal_sku=product.internal_sku,
        product_name=product.product_name,
        grade=product.grade,
        stem_length=product.stem_length,
        unit=product.unit,
        base_cost=product.base_cost,
        current_stock=current_stock,
        sale_enabled=product.sale_enabled,
        remark=product.remark,
    )
