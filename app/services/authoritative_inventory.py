from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Mapping
from uuid import uuid4

from app.enums import DataQualityLevel, FactSource
from app.inventory_models import (
    InventoryAuthorityState,
    InventoryBalance,
    InventoryBootstrapResult,
    InventorySalesBatchResult,
    InventoryTransaction,
    InventoryWriteResult,
)
from app.models import Product
from app.repositories.automation_repository import AutomationRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.operational_summary_repository import OperationalSummaryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.trade_day_settlement import TradeDaySettlementService
from app.services.operational_time import OperationalTimeService
from app.utils import utc_now


class InventoryAuthorityError(RuntimeError):
    pass


class InventoryConflictError(RuntimeError):
    pass


class InventoryInsufficientError(RuntimeError):
    pass


MANUAL_INVENTORY_SOURCE_TYPES = frozenset(
    {
        "NEW_FLOWER_INBOUND",
        "MANUAL_STOCKTAKE",
        "LOSS_ADJUSTMENT",
        "RECONCILIATION_CORRECTION",
    }
)
CUTOVER_ORDER_SNAPSHOT_MAX_AGE = timedelta(minutes=10)


def sqlite_logical_snapshot_sha256(
    runtime_repository: SQLiteRuntimeRepository,
    *,
    connection=None,
) -> str:
    """Hash a complete logical SQLite snapshot, including committed WAL state."""

    def _digest(active_connection) -> str:
        digest = hashlib.sha256()
        for statement in active_connection.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
        return "sha256:" + digest.hexdigest()

    if connection is not None:
        return _digest(connection)
    with closing(runtime_repository.connect_read()) as read_connection:
        try:
            read_connection.execute("BEGIN")
            return _digest(read_connection)
        finally:
            if read_connection.in_transaction:
                read_connection.rollback()


class InventoryProvider:
    """Return workbook stock before cutover and DB stock after cutover."""

    def __init__(self, repository: InventoryRepository) -> None:
        self.repository = repository

    def hydrate_products(self, products: Iterable[Product]) -> list[Product]:
        source = list(products)
        state = self.repository.get_authority_state()
        if state.authority_mode == "PRE_CUTOVER":
            return source
        balances = {
            balance.internal_sku: balance
            for balance in self.repository.list_balances()
        }
        missing = sorted(
            product.internal_sku
            for product in source
            if product.internal_sku not in balances
        )
        if missing:
            raise InventoryAuthorityError(
                "数据库库存已成为权威，但以下商品缺少库存余额："
                + "、".join(missing)
            )
        return [
            replace(
                product,
                current_stock=balances[product.internal_sku].current_qty,
            )
            for product in source
        ]


class InventoryApplicationService:
    """Atomic bootstrap and signed inventory adjustments."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        alert_evaluator: Callable[[InventoryTransaction], object] | None = None,
        product_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.repository = InventoryRepository(runtime_repository)
        self.clock = clock or utc_now
        self.alert_evaluator = alert_evaluator
        self.product_exists = product_exists
        self.summaries = OperationalSummaryRepository(runtime_repository)
        self.settlement = TradeDaySettlementService(self.summaries)

    def bootstrap(
        self,
        products: Iterable[Product],
        *,
        snapshot_sha256: str,
        runtime_snapshot_sha256: str,
        cutover_order_observation_batch_id: str,
        idempotency_key: str,
        actor: str,
        freeze_validator: Callable[[], bool] | None = None,
    ) -> InventoryBootstrapResult:
        normalized_products = tuple(sorted(products, key=lambda item: item.internal_sku))
        if not normalized_products:
            raise ValueError("库存切换至少需要一个商品")
        if len({item.internal_sku for item in normalized_products}) != len(
            normalized_products
        ):
            raise ValueError("库存切换商品编码不能重复")
        _require_prefixed_sha256(snapshot_sha256, "snapshot_sha256")
        _require_prefixed_sha256(
            runtime_snapshot_sha256,
            "runtime_snapshot_sha256",
        )
        if freeze_validator is None:
            raise ValueError("库存切换必须提供工作簿冻结校验器")
        cutover_batch_id = _required_text(
            cutover_order_observation_batch_id,
            "cutover_order_observation_batch_id",
        )
        key = _required_text(idempotency_key, "idempotency_key")
        normalized_actor = _required_text(actor, "actor")
        payload = {
            "actor": normalized_actor,
            "products": [
                {"internal_sku": item.internal_sku, "current_qty": item.current_stock}
                for item in normalized_products
            ],
            "snapshot_sha256": snapshot_sha256,
            "runtime_snapshot_sha256": runtime_snapshot_sha256,
            "cutover_order_observation_batch_id": cutover_batch_id,
        }
        request_sha256 = _sha256_payload(payload)
        now = self.clock()
        with closing(self.runtime_repository.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self.repository.get_authority_state(connection=connection)
                if state.authority_mode == "DB_AUTHORITY":
                    if (
                        state.bootstrap_idempotency_key == key
                        and state.bootstrap_snapshot_sha256 == snapshot_sha256
                    ):
                        bootstrap_rows = connection.execute(
                            """
                            SELECT transaction_id, internal_sku,
                                   inventory_after, request_sha256
                            FROM inventory_transactions
                            WHERE transaction_type = 'BOOTSTRAP'
                            ORDER BY internal_sku
                            """
                        ).fetchall()
                        stored_products = tuple(
                            (str(row["internal_sku"]), int(row["inventory_after"]))
                            for row in bootstrap_rows
                        )
                        requested_products = tuple(
                            (item.internal_sku, int(item.current_stock))
                            for item in normalized_products
                        )
                        stored_request_hashes = {
                            str(row["request_sha256"])
                            for row in bootstrap_rows
                        }
                        replay_request_sha256 = _sha256_payload(
                            {
                                **payload,
                                "runtime_snapshot_sha256": (
                                    state.bootstrap_runtime_snapshot_sha256
                                ),
                            }
                        )
                        if (
                            stored_products != requested_products
                            or stored_request_hashes != {replay_request_sha256}
                        ):
                            raise InventoryConflictError(
                                "库存权威切换重放内容与原始请求不一致"
                            )
                        connection.rollback()
                        return InventoryBootstrapResult(
                            status="REPLAYED",
                            authority_state=state,
                            balance_count=len(bootstrap_rows),
                            sales_baseline_count=int(
                                connection.execute(
                                    "SELECT COUNT(*) FROM inventory_sales_baselines"
                                ).fetchone()[0]
                            ),
                            transaction_ids=tuple(
                                str(row["transaction_id"])
                                for row in bootstrap_rows
                            ),
                        )
                    raise InventoryConflictError("真实库存权威切换只能成功执行一次")
                if (
                    sqlite_logical_snapshot_sha256(
                        self.runtime_repository,
                        connection=connection,
                    )
                    != runtime_snapshot_sha256
                ):
                    raise InventoryConflictError(
                        "Runtime DB 逻辑快照在切换前发生变化"
                    )
                if not freeze_validator():
                    raise InventoryConflictError("商品工作簿冻结校验失败")
                policies = AutomationRepository(
                    self.runtime_repository
                ).load_operational_time_policies(connection=connection)
                time_context = OperationalTimeService(
                    policies=policies
                ).classify(now)
                watermark_date = time_context.platform_trade_date
                cutover_snapshot = _require_trusted_empty_cutover_snapshot(
                    self.summaries,
                    connection=connection,
                    observation_batch_id=cutover_batch_id,
                    platform_trade_date=watermark_date,
                    time_policy_version=time_context.time_policy_version,
                    cutover_at=now,
                )
                cutover_order_ref = (
                    "ORDER_OBSERVATION_BATCH:"
                    f"{cutover_snapshot.observation_batch_id}:"
                    f"{cutover_snapshot.content_sha256}"
                )
                nonempty_tables = tuple(
                    table_name
                    for table_name in (
                        "inventory_balances",
                        "inventory_transactions",
                        "inventory_sales_baselines",
                    )
                    if int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table_name}"
                        ).fetchone()[0]
                    )
                    > 0
                )
                if nonempty_tables:
                    raise InventoryConflictError(
                        "切换前库存三表必须同时为空：" + "、".join(nonempty_tables)
                    )
                transaction_ids: list[str] = []
                transaction_by_sku: dict[str, str] = {}
                for product in normalized_products:
                    if product.current_stock < 0:
                        raise ValueError("初始库存不能为负数")
                    transaction_id = f"INV-BOOT-{uuid4().hex}"
                    transaction_ids.append(transaction_id)
                    transaction_by_sku[product.internal_sku] = transaction_id
                    transaction_key = f"{key}:sku:{product.internal_sku}"
                    connection.execute(
                        """
                        INSERT INTO inventory_transactions(
                            transaction_id, internal_sku,
                            inventory_before, inventory_delta, inventory_after,
                            transaction_type, source_type, source_ref_id,
                            reason, actor, seller_operation_date,
                            platform_name, platform_trade_date,
                            supporting_refs_json, idempotency_key,
                            request_sha256, balance_version_after,
                            occurred_at, recorded_at
                        ) VALUES (
                            ?, ?, 0, ?, ?, 'BOOTSTRAP',
                            'WORKBOOK_SNAPSHOT', ?, ?, ?,
                            NULL, NULL, NULL, ?, ?, ?, 1, ?, ?
                        )
                        """,
                        (
                            transaction_id,
                            product.internal_sku,
                            product.current_stock,
                            product.current_stock,
                            snapshot_sha256,
                            "从经哈希确认的商品主数据一次性切换",
                            normalized_actor,
                            json.dumps(
                                [cutover_order_ref],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            transaction_key,
                            request_sha256,
                            _datetime_to_text(now),
                            _datetime_to_text(now),
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO inventory_balances(
                            internal_sku, current_qty, version,
                            last_transaction_id, updated_at
                        ) VALUES (?, ?, 1, ?, ?)
                        """,
                        (
                            product.internal_sku,
                            product.current_stock,
                            transaction_id,
                            _datetime_to_text(now),
                        ),
                    )
                baseline_count = 0
                known_skus = set(transaction_by_sku)
                for summary in self.summaries.list_all_current_sku_summaries(
                    connection=connection
                ):
                    if summary.platform_trade_date > watermark_date:
                        raise InventoryAuthorityError(
                            "切换快照中存在未来交易日销售事实"
                        )
                    if summary.platform_trade_date == watermark_date:
                        if summary.platform_name != cutover_snapshot.platform_name:
                            raise InventoryAuthorityError(
                                "当前交易日存在未被切换订单快照覆盖的平台"
                            )
                        continue
                    selection = self.settlement.select_evidence(
                        platform_name=summary.platform_name,
                        platform_trade_date=summary.platform_trade_date,
                        scope_type="SKU",
                        scope_key=summary.scope_key,
                        time_policy_version=summary.time_policy_version,
                        connection=connection,
                    ).selection
                    if not _eligible_inventory_fact(summary, selection):
                        continue
                    if summary.scope_key not in known_skus:
                        raise InventoryAuthorityError(
                            f"切换水位中的商品 {summary.scope_key} 不在商品冻结快照"
                        )
                    _upsert_sales_baseline(
                        connection,
                        summary=summary,
                        selection=selection,
                        inventory_transaction_id=transaction_by_sku[
                            summary.scope_key
                        ],
                        updated_at=now,
                        existing_version=None,
                    )
                    baseline_count += 1
                if not freeze_validator():
                    raise InventoryConflictError("商品工作簿在切换事务内发生变化")
                expected_balances = tuple(
                    (item.internal_sku, int(item.current_stock))
                    for item in normalized_products
                )
                actual_balances = tuple(
                    (str(row[0]), int(row[1]))
                    for row in connection.execute(
                        "SELECT internal_sku, current_qty FROM inventory_balances "
                        "ORDER BY internal_sku"
                    ).fetchall()
                )
                if actual_balances != expected_balances:
                    raise InventoryConflictError("库存切换事务内余额回读不一致")
                if int(
                    connection.execute(
                        "SELECT COUNT(*) FROM inventory_transactions "
                        "WHERE transaction_type = 'BOOTSTRAP'"
                    ).fetchone()[0]
                ) != len(normalized_products):
                    raise InventoryConflictError("库存切换事务内流水回读不一致")
                if int(
                    connection.execute(
                        "SELECT COUNT(*) FROM inventory_sales_baselines"
                    ).fetchone()[0]
                ) != baseline_count:
                    raise InventoryConflictError("库存切换事务内销售水位回读不一致")
                updated = connection.execute(
                    """
                    UPDATE inventory_authority_state
                    SET authority_mode = 'DB_AUTHORITY',
                        bootstrap_snapshot_sha256 = ?,
                        bootstrap_runtime_snapshot_sha256 = ?,
                        bootstrap_sales_watermark_date = ?,
                        bootstrap_idempotency_key = ?,
                        bootstrap_completed_at = ?,
                        bootstrap_completed_by = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE authority_key = 'REAL_INVENTORY'
                      AND authority_mode = 'PRE_CUTOVER'
                      AND version = 0
                    """,
                    (
                        snapshot_sha256,
                        runtime_snapshot_sha256,
                        watermark_date.isoformat(),
                        key,
                        _datetime_to_text(now),
                        normalized_actor,
                        _datetime_to_text(now),
                    ),
                ).rowcount
                if updated != 1:
                    raise InventoryConflictError("库存权威状态在切换时发生并发变化")
                stored_state = self.repository.get_authority_state(connection=connection)
                if (
                    stored_state.authority_mode != "DB_AUTHORITY"
                    or stored_state.bootstrap_runtime_snapshot_sha256
                    != runtime_snapshot_sha256
                    or stored_state.bootstrap_sales_watermark_date != watermark_date
                ):
                    raise InventoryConflictError("库存权威状态事务内回读不一致")
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return InventoryBootstrapResult(
            status="APPLIED",
            authority_state=self.repository.get_authority_state(),
            balance_count=len(normalized_products),
            sales_baseline_count=baseline_count,
            transaction_ids=tuple(transaction_ids),
        )

    def initialize_sku(
        self,
        *,
        internal_sku: str,
        actor: str,
        idempotency_key: str,
        reason: str = "新增商品建立零库存余额",
    ) -> InventoryWriteResult:
        """Create the required zero balance after new product metadata is saved."""

        sku = _required_text(internal_sku, "internal_sku")
        normalized_actor = _required_text(actor, "actor")
        key = _required_text(idempotency_key, "idempotency_key")
        normalized_reason = _required_text(reason, "reason")
        request_sha256 = _sha256_payload(
            {
                "actor": normalized_actor,
                "internal_sku": sku,
                "reason": normalized_reason,
            }
        )
        now = self.clock()
        with closing(self.runtime_repository.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self.repository.get_authority_state(connection=connection)
                if state.authority_mode != "DB_AUTHORITY":
                    raise InventoryAuthorityError("库存尚未切换为数据库权威")
                if self.product_exists is None or not self.product_exists(sku):
                    raise InventoryAuthorityError(
                        f"商品 {sku} 尚未保存到固定商品主数据"
                    )
                existing = self.repository.get_transaction_by_idempotency_key(
                    key,
                    connection=connection,
                )
                if existing is not None:
                    if existing.request_sha256 != request_sha256:
                        raise InventoryConflictError("幂等键已被不同库存请求使用")
                    balance = self.repository.get_balance(sku, connection=connection)
                    connection.rollback()
                    return InventoryWriteResult("REPLAYED", existing, balance)
                if self.repository.get_balance(sku, connection=connection) is not None:
                    raise InventoryConflictError(f"商品 {sku} 已存在权威库存余额")
                transaction = _append_transaction(
                    connection,
                    internal_sku=sku,
                    inventory_before=0,
                    inventory_delta=0,
                    transaction_type="SKU_INITIALIZATION",
                    source_type="PRODUCT_METADATA",
                    source_ref_id=sku,
                    reason=normalized_reason,
                    actor=normalized_actor,
                    supporting_refs=(),
                    idempotency_key=key,
                    request_sha256=request_sha256,
                    balance_version_after=1,
                    occurred_at=now,
                    seller_operation_date=None,
                    platform_name=None,
                    platform_trade_date=None,
                )
                connection.execute(
                    """
                    INSERT INTO inventory_balances(
                        internal_sku, current_qty, version,
                        last_transaction_id, updated_at
                    ) VALUES (?, 0, 1, ?, ?)
                    """,
                    (sku, transaction.transaction_id, _datetime_to_text(now)),
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return InventoryWriteResult(
            "APPLIED",
            transaction,
            self.repository.get_balance(sku),
        )

    def adjust(
        self,
        *,
        internal_sku: str,
        inventory_delta: int,
        source_type: str = "NEW_FLOWER_INBOUND",
        reason: str = "新花入库",
        actor: str,
        idempotency_key: str,
        expected_version: int | None = None,
        occurred_at: datetime | None = None,
    ) -> InventoryWriteResult:
        sku = _required_text(internal_sku, "internal_sku")
        delta = int(inventory_delta)
        if delta == 0:
            raise ValueError("库存调整值不能为 0")
        normalized_source = _required_text(source_type, "source_type")
        if normalized_source not in MANUAL_INVENTORY_SOURCE_TYPES:
            raise ValueError("库存调整来源不在允许范围内")
        if normalized_source == "NEW_FLOWER_INBOUND" and delta <= 0:
            raise ValueError("新花入库的库存调整值必须大于 0")
        if normalized_source == "LOSS_ADJUSTMENT" and delta >= 0:
            raise ValueError("损耗修正的库存调整值必须小于 0")
        normalized_reason = _required_text(reason, "reason")
        normalized_actor = _required_text(actor, "actor")
        key = _required_text(idempotency_key, "idempotency_key")
        now = occurred_at or self.clock()
        request_sha256 = _sha256_payload(
            {
                "actor": normalized_actor,
                "delta": delta,
                "expected_version": expected_version,
                "internal_sku": sku,
                "reason": normalized_reason,
                "source_type": normalized_source,
            }
        )
        with closing(self.runtime_repository.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self.repository.get_authority_state(connection=connection)
                if state.authority_mode != "DB_AUTHORITY":
                    raise InventoryAuthorityError("库存尚未切换为数据库权威")
                existing = self.repository.get_transaction_by_idempotency_key(
                    key,
                    connection=connection,
                )
                if existing is not None:
                    if existing.request_sha256 != request_sha256:
                        raise InventoryConflictError("幂等键已被不同库存请求使用")
                    balance = self.repository.get_balance(sku, connection=connection)
                    connection.rollback()
                    return InventoryWriteResult("REPLAYED", existing, balance)
                balance = self.repository.get_balance(sku, connection=connection)
                if balance is None:
                    raise InventoryAuthorityError(f"商品 {sku} 没有权威库存余额")
                if expected_version is not None and balance.version != int(expected_version):
                    raise InventoryConflictError("库存已变化，请刷新后重新确认")
                after = balance.current_qty + delta
                if after < 0:
                    raise InventoryInsufficientError("调整后的真实库存不能为负数")
                transaction_type = (
                    "MANUAL_INBOUND"
                    if normalized_source == "NEW_FLOWER_INBOUND" and delta > 0
                    else "MANUAL_ADJUSTMENT"
                )
                transaction = _append_transaction(
                    connection,
                    internal_sku=sku,
                    inventory_before=balance.current_qty,
                    inventory_delta=delta,
                    transaction_type=transaction_type,
                    source_type=normalized_source,
                    source_ref_id=key,
                    reason=normalized_reason,
                    actor=normalized_actor,
                    supporting_refs=(),
                    idempotency_key=key,
                    request_sha256=request_sha256,
                    balance_version_after=balance.version + 1,
                    occurred_at=now,
                    seller_operation_date=None,
                    platform_name=None,
                    platform_trade_date=None,
                )
                _update_balance(connection, transaction)
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        alert_reason = ""
        if self.alert_evaluator is not None:
            try:
                self.alert_evaluator(transaction)
            except Exception as exc:
                alert_reason = f"ALERT_EVALUATION_FAILED:{type(exc).__name__}"
        return InventoryWriteResult(
            "APPLIED",
            transaction,
            self.repository.get_balance(sku),
            alert_reason,
        )


class InventorySalesApplicationService:
    """Apply only current eligible SKU settlement facts as baseline differences."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        alert_evaluator: Callable[[InventoryTransaction], object] | None = None,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.inventory = InventoryRepository(runtime_repository)
        self.summaries = OperationalSummaryRepository(runtime_repository)
        self.settlement = TradeDaySettlementService(self.summaries)
        self.clock = clock or utc_now
        self.alert_evaluator = alert_evaluator

    def apply_current_sku_summaries(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        actor: str = "settlement_inventory_pipeline",
    ) -> InventorySalesBatchResult:
        platform = _required_text(platform_name, "platform_name")
        normalized_actor = _required_text(actor, "actor")
        now = self.clock()
        applied: list[str] = []
        replayed = 0
        skipped: list[str] = []
        with closing(self.runtime_repository.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self.inventory.get_authority_state(connection=connection)
                if state.authority_mode != "DB_AUTHORITY":
                    connection.rollback()
                    return InventorySalesBatchResult(
                        "SKIPPED_PRE_CUTOVER", 0, 0, 0, (), ("库存尚未切换",)
                    )
                sku_summaries = tuple(
                    summary
                    for summary in self.summaries.list_current_summaries(
                        platform_name=platform,
                        platform_trade_date=platform_trade_date,
                        connection=connection,
                    )
                    if summary.scope_type == "SKU"
                )
                for summary in sku_summaries:
                    selection = self.settlement.select_evidence(
                        platform_name=platform,
                        platform_trade_date=platform_trade_date,
                        scope_type="SKU",
                        scope_key=summary.scope_key,
                        time_policy_version=summary.time_policy_version,
                        connection=connection,
                    ).selection
                    if not _eligible_inventory_fact(summary, selection):
                        skipped.append(
                            f"{summary.scope_key}:结算事实不满足库存扣减资格"
                        )
                        continue
                    baseline = self.inventory.get_sales_baseline(
                        platform_name=platform,
                        platform_trade_date=platform_trade_date,
                        internal_sku=summary.scope_key,
                        connection=connection,
                    )
                    before_sold = baseline.selected_sold_qty if baseline else 0
                    selected_sold = int(summary.sold_qty)
                    delta_sold = selected_sold - before_sold
                    if (
                        baseline is not None
                        and baseline.selected_fact_source == FactSource.SCAN_ESTIMATED.value
                        and summary.fact_source is FactSource.SCAN_ESTIMATED
                        and delta_sold < 0
                    ):
                        skipped.append(
                            f"{summary.scope_key}:估算回落不自动回补库存"
                        )
                        continue
                    if (
                        baseline is not None
                        and delta_sold == 0
                        and _baseline_matches_summary(baseline, summary)
                    ):
                        replayed += 1
                        continue
                    idempotency_key = (
                        "inventory-sales:"
                        f"{platform}:{platform_trade_date.isoformat()}:"
                        f"{summary.scope_key}:{summary.input_manifest_sha256}"
                    )
                    request_sha256 = _sha256_payload(
                        {
                            "fact_source": summary.fact_source.value,
                            "input_manifest_sha256": summary.input_manifest_sha256,
                            "internal_sku": summary.scope_key,
                            "mapping_version": summary.mapping_version,
                            "platform_name": platform,
                            "platform_trade_date": platform_trade_date.isoformat(),
                            "selected_sold_qty": selected_sold,
                        }
                    )
                    existing = self.inventory.get_transaction_by_idempotency_key(
                        idempotency_key,
                        connection=connection,
                    )
                    if existing is not None:
                        if existing.request_sha256 != request_sha256:
                            raise InventoryConflictError(
                                "销售结算幂等键与已记录内容冲突"
                            )
                        replayed += 1
                        continue
                    balance = self.inventory.get_balance(
                        summary.scope_key,
                        connection=connection,
                    )
                    if balance is None:
                        raise InventoryAuthorityError(
                            f"商品 {summary.scope_key} 缺少权威库存余额"
                        )
                    historical_baseline_only = (
                        state.bootstrap_sales_watermark_date is not None
                        and platform_trade_date
                        < state.bootstrap_sales_watermark_date
                    )
                    baseline_only = historical_baseline_only or delta_sold == 0
                    inventory_delta = 0 if baseline_only else -delta_sold
                    if balance.current_qty + inventory_delta < 0:
                        raise InventoryInsufficientError(
                            f"商品 {summary.scope_key} 的销售扣减超过真实库存"
                        )
                    supporting_refs = tuple(
                        f"{input_type}:{input_ref_id}:{input_sha256}"
                        for input_type, input_ref_id, input_sha256 in selection.input_refs
                    )
                    transaction = _append_transaction(
                        connection,
                        internal_sku=summary.scope_key,
                        inventory_before=balance.current_qty,
                        inventory_delta=inventory_delta,
                        transaction_type=(
                            "SALES_BASELINE_SYNC"
                            if baseline_only
                            else (
                                "SALES_DEDUCTION"
                                if inventory_delta <= 0
                                else "SALES_RESTORE"
                            )
                        ),
                        source_type="PLATFORM_TRADE_DAY_SUMMARY",
                        source_ref_id=summary.summary_id,
                        reason=(
                            (
                                "回补切换水位前销售基准，不改变当前真实库存"
                                if historical_baseline_only
                                else "销售权威来源更新但累计销量不变，只同步基准"
                            )
                            if baseline_only
                            else "按当前权威销售事实与已应用基准的差额更新真实库存"
                        ),
                        actor=normalized_actor,
                        supporting_refs=supporting_refs,
                        idempotency_key=idempotency_key,
                        request_sha256=request_sha256,
                        balance_version_after=(
                            balance.version
                            if baseline_only
                            else balance.version + 1
                        ),
                        occurred_at=now,
                        seller_operation_date=summary.seller_operation_date,
                        platform_name=platform,
                        platform_trade_date=platform_trade_date,
                    )
                    if not baseline_only:
                        _update_balance(connection, transaction)
                    _upsert_sales_baseline(
                        connection,
                        summary=summary,
                        selection=selection,
                        inventory_transaction_id=transaction.transaction_id,
                        updated_at=now,
                        existing_version=(baseline.version if baseline else None),
                    )
                    applied.append(transaction.transaction_id)
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        if self.alert_evaluator is not None:
            for transaction_id in applied:
                transaction = self.inventory.get_transaction(transaction_id)
                if transaction is None:
                    skipped.append(
                        f"{transaction_id}:库存预警找不到已提交流水"
                    )
                    continue
                try:
                    self.alert_evaluator(transaction)
                except Exception as exc:
                    skipped.append(
                        f"{transaction.internal_sku}:库存预警失败:{type(exc).__name__}"
                    )
        return InventorySalesBatchResult(
            status="APPLIED" if applied else "NO_CHANGE",
            applied_sku_count=len(applied),
            replayed_sku_count=replayed,
            skipped_sku_count=len(skipped),
            transaction_ids=tuple(applied),
            reasons=tuple(skipped),
        )


def _require_trusted_empty_cutover_snapshot(
    repository: OperationalSummaryRepository,
    *,
    connection,
    observation_batch_id: str,
    platform_trade_date: date,
    time_policy_version: str,
    cutover_at: datetime,
):
    snapshot = repository.get_order_snapshot(
        observation_batch_id,
        connection=connection,
    )
    if snapshot is None:
        raise InventoryConflictError("库存切换绑定的订单观察批次不存在")
    if snapshot.platform_trade_date != platform_trade_date:
        raise InventoryConflictError("库存切换订单快照与当前 PRA 交易日不一致")
    if snapshot.time_policy_version != time_policy_version:
        raise InventoryConflictError("库存切换订单快照与当前交易日策略版本不一致")
    if (
        snapshot.trade_day_status != "OPEN"
        or snapshot.capability_result != "SUCCEEDED"
        or snapshot.batch_status != "ACCEPTED"
        or snapshot.source_batch_status != "ACCEPTED"
        or not snapshot.scope_complete
        or not snapshot.end_marker_verified
    ):
        raise InventoryConflictError("库存切换必须绑定可信完整的 OPEN 订单空快照")
    if snapshot.items:
        raise InventoryConflictError(
            "当前 PRA 交易日已经出现订单，保守库存切换门禁拒绝执行"
        )
    observed_order_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM order_observation_items AS item
            INNER JOIN order_observation_batches AS batch
                ON batch.observation_batch_id = item.observation_batch_id
            WHERE batch.platform_name = ?
              AND batch.requested_platform_trade_date = ?
            """,
            (
                snapshot.platform_name,
                platform_trade_date.isoformat(),
            ),
        ).fetchone()[0]
    )
    if observed_order_count:
        raise InventoryConflictError(
            "当前 PRA 交易日曾经观察到订单，保守库存切换门禁拒绝执行"
        )
    age = cutover_at - snapshot.scan_completed_at
    if age < timedelta(0) or age > CUTOVER_ORDER_SNAPSHOT_MAX_AGE:
        raise InventoryConflictError("库存切换订单空快照已过期或时间晚于切换时刻")
    snapshots = repository.list_order_snapshots(
        platform_name=snapshot.platform_name,
        platform_trade_date=platform_trade_date,
        connection=connection,
    )
    latest = max(
        snapshots,
        key=lambda item: (
            item.scan_completed_at,
            item.observation_batch_id,
        ),
        default=None,
    )
    if latest is None or latest.observation_batch_id != observation_batch_id:
        raise InventoryConflictError("库存切换必须绑定当前交易日最新订单快照")
    platforms = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT platform_name
            FROM order_observation_batches
            WHERE requested_platform_trade_date = ?
            """,
            (platform_trade_date.isoformat(),),
        ).fetchall()
    }
    if platforms != {snapshot.platform_name}:
        raise InventoryConflictError("库存切换窗口只允许一个已完整覆盖的平台")
    return snapshot


def _eligible_inventory_fact(summary, selection) -> bool:
    return bool(
        summary.sold_qty is not None
        and selection.sold_qty == summary.sold_qty
        and selection.fact_source is summary.fact_source
        and selection.quality_level is summary.quality_level
        and selection.mapping_version == summary.mapping_version
        and (
            (
                summary.fact_source is FactSource.ORDER_OBSERVED
                and summary.quality_level is DataQualityLevel.ORDER_COMPLETE
            )
            or (
                summary.fact_source is FactSource.SCAN_ESTIMATED
                and summary.quality_level
                is DataQualityLevel.SCAN_ESTIMATED_HIGH
            )
        )
    )


def _baseline_matches_summary(baseline, summary) -> bool:
    return bool(
        baseline.selected_fact_source == summary.fact_source.value
        and baseline.quality_level == summary.quality_level.value
        and baseline.selected_sold_qty == int(summary.sold_qty)
        and baseline.source_ref_id == summary.summary_id
        and baseline.source_sha256 == summary.input_manifest_sha256
        and baseline.mapping_version == summary.mapping_version
    )


def _upsert_sales_baseline(
    connection,
    *,
    summary,
    selection,
    inventory_transaction_id: str,
    updated_at: datetime,
    existing_version: int | None,
) -> None:
    supporting_refs = tuple(
        f"{input_type}:{input_ref_id}:{input_sha256}"
        for input_type, input_ref_id, input_sha256 in selection.input_refs
    )
    connection.execute(
        """
        INSERT INTO inventory_sales_baselines(
            platform_name, platform_trade_date, internal_sku,
            selected_fact_source, quality_level, selected_sold_qty,
            source_ref_id, source_sha256, mapping_version,
            supporting_refs_json, inventory_transaction_id,
            version, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform_name, platform_trade_date, internal_sku)
        DO UPDATE SET
            selected_fact_source = excluded.selected_fact_source,
            quality_level = excluded.quality_level,
            selected_sold_qty = excluded.selected_sold_qty,
            source_ref_id = excluded.source_ref_id,
            source_sha256 = excluded.source_sha256,
            mapping_version = excluded.mapping_version,
            supporting_refs_json = excluded.supporting_refs_json,
            inventory_transaction_id = excluded.inventory_transaction_id,
            version = inventory_sales_baselines.version + 1,
            updated_at = excluded.updated_at
        """,
        (
            summary.platform_name,
            summary.platform_trade_date.isoformat(),
            summary.scope_key,
            summary.fact_source.value,
            summary.quality_level.value,
            int(summary.sold_qty),
            summary.summary_id,
            summary.input_manifest_sha256,
            summary.mapping_version,
            json.dumps(
                supporting_refs,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            inventory_transaction_id,
            1 if existing_version is None else existing_version + 1,
            _datetime_to_text(updated_at),
        ),
    )


def _append_transaction(
    connection,
    *,
    internal_sku: str,
    inventory_before: int,
    inventory_delta: int,
    transaction_type: str,
    source_type: str,
    source_ref_id: str,
    reason: str,
    actor: str,
    supporting_refs: tuple[str, ...],
    idempotency_key: str,
    request_sha256: str,
    balance_version_after: int,
    occurred_at: datetime,
    seller_operation_date: date | None,
    platform_name: str | None,
    platform_trade_date: date | None,
) -> InventoryTransaction:
    transaction_id = f"INV-TXN-{uuid4().hex}"
    after = int(inventory_before) + int(inventory_delta)
    recorded_at = occurred_at
    connection.execute(
        """
        INSERT INTO inventory_transactions(
            transaction_id, internal_sku,
            inventory_before, inventory_delta, inventory_after,
            transaction_type, source_type, source_ref_id,
            reason, actor, seller_operation_date,
            platform_name, platform_trade_date,
            supporting_refs_json, idempotency_key,
            request_sha256, balance_version_after,
            occurred_at, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            internal_sku,
            inventory_before,
            inventory_delta,
            after,
            transaction_type,
            source_type,
            source_ref_id,
            reason,
            actor,
            seller_operation_date.isoformat() if seller_operation_date else None,
            platform_name,
            platform_trade_date.isoformat() if platform_trade_date else None,
            json.dumps(supporting_refs, ensure_ascii=False, separators=(",", ":")),
            idempotency_key,
            request_sha256,
            balance_version_after,
            _datetime_to_text(occurred_at),
            _datetime_to_text(recorded_at),
        ),
    )
    return InventoryTransaction(
        transaction_id=transaction_id,
        internal_sku=internal_sku,
        inventory_before=inventory_before,
        inventory_delta=inventory_delta,
        inventory_after=after,
        transaction_type=transaction_type,
        source_type=source_type,
        source_ref_id=source_ref_id,
        reason=reason,
        actor=actor,
        seller_operation_date=seller_operation_date,
        platform_name=platform_name,
        platform_trade_date=platform_trade_date,
        supporting_refs=supporting_refs,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        balance_version_after=balance_version_after,
        occurred_at=occurred_at,
        recorded_at=recorded_at,
    )


def _update_balance(connection, transaction: InventoryTransaction) -> None:
    updated = connection.execute(
        """
        UPDATE inventory_balances
        SET current_qty = ?, version = ?,
            last_transaction_id = ?, updated_at = ?
        WHERE internal_sku = ?
          AND version = ?
          AND current_qty = ?
        """,
        (
            transaction.inventory_after,
            transaction.balance_version_after,
            transaction.transaction_id,
            _datetime_to_text(transaction.recorded_at),
            transaction.internal_sku,
            transaction.balance_version_after - 1,
            transaction.inventory_before,
        ),
    ).rowcount
    if updated != 1:
        raise InventoryConflictError("库存余额在写入时发生并发变化")


def _sha256_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_prefixed_sha256(value: str, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if len(normalized) != 71 or not normalized.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a prefixed sha256")
    try:
        int(normalized[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a prefixed sha256") from exc
    return normalized


def _required_text(value: object, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _datetime_to_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds")
