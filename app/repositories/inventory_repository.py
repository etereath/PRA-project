from __future__ import annotations

import json
import hashlib
from contextlib import closing
from datetime import date, datetime

from app.inventory_models import (
    InventoryAlertPolicy,
    InventoryAuthorityState,
    InventoryBalance,
    InventorySalesBaseline,
    InventoryTransaction,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


class InventoryRepository:
    """Narrow v17 persistence API; business writes stay in one caller transaction."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository

    def get_authority_state(self, *, connection=None) -> InventoryAuthorityState:
        query = (
            "SELECT * FROM inventory_authority_state "
            "WHERE authority_key = 'REAL_INVENTORY'"
        )
        if connection is not None:
            row = connection.execute(query).fetchone()
        else:
            with closing(self.runtime_repository.connect_read()) as read_connection:
                row = read_connection.execute(query).fetchone()
        if row is None:
            raise RuntimeError("库存权威状态不存在，请先执行显式 Runtime Schema 维护")
        return _row_to_authority_state(row)

    def get_balance(
        self,
        internal_sku: str,
        *,
        connection=None,
    ) -> InventoryBalance | None:
        query = "SELECT * FROM inventory_balances WHERE internal_sku = ?"
        values = (str(internal_sku).strip(),)
        if connection is not None:
            row = connection.execute(query, values).fetchone()
        else:
            with closing(self.runtime_repository.connect_read()) as read_connection:
                row = read_connection.execute(query, values).fetchone()
        return _row_to_balance(row) if row is not None else None

    def list_balances(self, *, connection=None) -> tuple[InventoryBalance, ...]:
        query = "SELECT * FROM inventory_balances ORDER BY internal_sku"
        if connection is not None:
            rows = connection.execute(query).fetchall()
        else:
            with closing(self.runtime_repository.connect_read()) as read_connection:
                rows = read_connection.execute(query).fetchall()
        return tuple(_row_to_balance(row) for row in rows)

    def get_transaction_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        connection=None,
    ) -> InventoryTransaction | None:
        query = "SELECT * FROM inventory_transactions WHERE idempotency_key = ?"
        values = (str(idempotency_key).strip(),)
        if connection is not None:
            row = connection.execute(query, values).fetchone()
        else:
            with closing(self.runtime_repository.connect_read()) as read_connection:
                row = read_connection.execute(query, values).fetchone()
        return _row_to_transaction(row) if row is not None else None

    def get_transaction(
        self,
        transaction_id: str,
    ) -> InventoryTransaction | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM inventory_transactions WHERE transaction_id = ?",
                (str(transaction_id).strip(),),
            ).fetchone()
        return _row_to_transaction(row) if row is not None else None

    def list_transactions(
        self,
        *,
        internal_sku: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[InventoryTransaction, ...]:
        normalized_limit = int(limit)
        normalized_offset = int(offset)
        if normalized_limit < 1 or normalized_limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if normalized_offset < 0:
            raise ValueError("offset must be non-negative")
        query = "SELECT * FROM inventory_transactions"
        values: list[object] = []
        if internal_sku is not None:
            query += " WHERE internal_sku = ?"
            values.append(str(internal_sku).strip())
        query += " ORDER BY recorded_at DESC, transaction_id DESC LIMIT ? OFFSET ?"
        values.extend((normalized_limit, normalized_offset))
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return tuple(_row_to_transaction(row) for row in rows)

    def get_sales_baseline(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        internal_sku: str,
        connection=None,
    ) -> InventorySalesBaseline | None:
        query = """
            SELECT *
            FROM inventory_sales_baselines
            WHERE platform_name = ?
              AND platform_trade_date = ?
              AND internal_sku = ?
        """
        values = (
            str(platform_name).strip(),
            platform_trade_date.isoformat(),
            str(internal_sku).strip(),
        )
        if connection is not None:
            row = connection.execute(query, values).fetchone()
        else:
            with closing(self.runtime_repository.connect_read()) as read_connection:
                row = read_connection.execute(query, values).fetchone()
        return _row_to_sales_baseline(row) if row is not None else None

    def get_alert_policy(
        self,
        *,
        internal_sku: str,
    ) -> InventoryAlertPolicy | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM inventory_alert_policies
                WHERE (scope_type = 'SKU' AND scope_key = ?)
                   OR (scope_type = 'DEFAULT' AND scope_key = '*')
                ORDER BY CASE scope_type WHEN 'SKU' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (str(internal_sku).strip(),),
            ).fetchone()
        return _row_to_alert_policy(row) if row is not None else None

    def list_alert_policies(self) -> tuple[InventoryAlertPolicy, ...]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM inventory_alert_policies
                ORDER BY CASE scope_type WHEN 'DEFAULT' THEN 0 ELSE 1 END,
                         scope_key
                """
            ).fetchall()
        return tuple(_row_to_alert_policy(row) for row in rows)

    def save_alert_policy(
        self,
        *,
        scope_type: str,
        scope_key: str,
        enabled: bool,
        threshold_qty: int,
        repeat_interval_minutes: int,
        updated_by: str,
        expected_version: int | None,
        updated_at: datetime,
    ) -> InventoryAlertPolicy:
        scope = str(scope_type).strip().upper()
        key = str(scope_key).strip()
        if scope not in {"DEFAULT", "SKU"}:
            raise ValueError("库存预警范围只能是 DEFAULT 或 SKU")
        if (scope == "DEFAULT" and key != "*") or (
            scope == "SKU" and (not key or key == "*")
        ):
            raise ValueError("库存预警范围键无效")
        threshold = int(threshold_qty)
        repeat = int(repeat_interval_minutes)
        if not 0 <= threshold <= 9999:
            raise ValueError("库存预警阈值必须在 0 到 9999 之间")
        if not 30 <= repeat <= 1440:
            raise ValueError("库存预警重复间隔必须在 30 到 1440 分钟之间")
        actor = str(updated_by).strip()
        if not actor:
            raise ValueError("库存预警修改人不能为空")
        with closing(self.runtime_repository.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM inventory_alert_policies "
                    "WHERE scope_type = ? AND scope_key = ?",
                    (scope, key),
                ).fetchone()
                timestamp = updated_at.isoformat(timespec="seconds")
                if row is None:
                    if expected_version not in (None, 0):
                        raise RuntimeError("库存预警方案已变化，请刷新后重试")
                    identity = f"{scope}:{key}".encode("utf-8")
                    policy_key = "INVENTORY-ALERT-" + hashlib.sha256(
                        identity
                    ).hexdigest()[:24]
                    connection.execute(
                        """
                        INSERT INTO inventory_alert_policies(
                            policy_key, scope_type, scope_key,
                            enabled, threshold_qty, repeat_interval_minutes,
                            version, updated_by, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                        """,
                        (
                            policy_key,
                            scope,
                            key,
                            int(bool(enabled)),
                            threshold,
                            repeat,
                            actor,
                            timestamp,
                            timestamp,
                        ),
                    )
                else:
                    current_version = int(row["version"])
                    if expected_version != current_version:
                        raise RuntimeError("库存预警方案已变化，请刷新后重试")
                    updated = connection.execute(
                        """
                        UPDATE inventory_alert_policies
                        SET enabled = ?, threshold_qty = ?,
                            repeat_interval_minutes = ?,
                            version = version + 1,
                            updated_by = ?, updated_at = ?
                        WHERE policy_key = ? AND version = ?
                        """,
                        (
                            int(bool(enabled)),
                            threshold,
                            repeat,
                            actor,
                            timestamp,
                            str(row["policy_key"]),
                            current_version,
                        ),
                    ).rowcount
                    if updated != 1:
                        raise RuntimeError("库存预警方案已变化，请刷新后重试")
                stored = connection.execute(
                    "SELECT * FROM inventory_alert_policies "
                    "WHERE scope_type = ? AND scope_key = ?",
                    (scope, key),
                ).fetchone()
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return _row_to_alert_policy(stored)


def _row_to_authority_state(row) -> InventoryAuthorityState:
    return InventoryAuthorityState(
        authority_mode=str(row["authority_mode"]),
        bootstrap_snapshot_sha256=_optional_text(row["bootstrap_snapshot_sha256"]),
        bootstrap_runtime_snapshot_sha256=_optional_text(
            row["bootstrap_runtime_snapshot_sha256"]
        ),
        bootstrap_sales_watermark_date=_optional_date(
            row["bootstrap_sales_watermark_date"]
        ),
        bootstrap_idempotency_key=_optional_text(row["bootstrap_idempotency_key"]),
        bootstrap_completed_at=_optional_datetime(row["bootstrap_completed_at"]),
        bootstrap_completed_by=_optional_text(row["bootstrap_completed_by"]),
        version=int(row["version"]),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _row_to_balance(row) -> InventoryBalance:
    return InventoryBalance(
        internal_sku=str(row["internal_sku"]),
        current_qty=int(row["current_qty"]),
        version=int(row["version"]),
        last_transaction_id=str(row["last_transaction_id"]),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _row_to_transaction(row) -> InventoryTransaction:
    return InventoryTransaction(
        transaction_id=str(row["transaction_id"]),
        internal_sku=str(row["internal_sku"]),
        inventory_before=int(row["inventory_before"]),
        inventory_delta=int(row["inventory_delta"]),
        inventory_after=int(row["inventory_after"]),
        transaction_type=str(row["transaction_type"]),
        source_type=str(row["source_type"]),
        source_ref_id=str(row["source_ref_id"]),
        reason=str(row["reason"]),
        actor=str(row["actor"]),
        seller_operation_date=_optional_date(row["seller_operation_date"]),
        platform_name=_optional_text(row["platform_name"]),
        platform_trade_date=_optional_date(row["platform_trade_date"]),
        supporting_refs=tuple(json.loads(str(row["supporting_refs_json"]))),
        idempotency_key=str(row["idempotency_key"]),
        request_sha256=str(row["request_sha256"]),
        balance_version_after=int(row["balance_version_after"]),
        occurred_at=_required_datetime(row["occurred_at"]),
        recorded_at=_required_datetime(row["recorded_at"]),
    )


def _row_to_sales_baseline(row) -> InventorySalesBaseline:
    return InventorySalesBaseline(
        platform_name=str(row["platform_name"]),
        platform_trade_date=date.fromisoformat(str(row["platform_trade_date"])),
        internal_sku=str(row["internal_sku"]),
        selected_fact_source=str(row["selected_fact_source"]),
        quality_level=str(row["quality_level"]),
        selected_sold_qty=int(row["selected_sold_qty"]),
        source_ref_id=str(row["source_ref_id"]),
        source_sha256=str(row["source_sha256"]),
        mapping_version=str(row["mapping_version"]),
        supporting_refs=tuple(json.loads(str(row["supporting_refs_json"]))),
        inventory_transaction_id=str(row["inventory_transaction_id"]),
        version=int(row["version"]),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _row_to_alert_policy(row) -> InventoryAlertPolicy:
    return InventoryAlertPolicy(
        policy_key=str(row["policy_key"]),
        scope_type=str(row["scope_type"]),
        scope_key=str(row["scope_key"]),
        enabled=bool(row["enabled"]),
        threshold_qty=int(row["threshold_qty"]),
        repeat_interval_minutes=int(row["repeat_interval_minutes"]),
        version=int(row["version"]),
        updated_by=str(row["updated_by"]),
        created_at=_required_datetime(row["created_at"]),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _optional_text(value) -> str | None:
    return str(value) if value not in (None, "") else None


def _required_datetime(value) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value) -> datetime | None:
    return _required_datetime(value) if value not in (None, "") else None


def _optional_date(value) -> date | None:
    return date.fromisoformat(str(value)) if value not in (None, "") else None
