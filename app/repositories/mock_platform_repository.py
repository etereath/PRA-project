from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from app.models import MockPlatformProductState
from app.repositories.sqlite_connection import SQLiteConnectionFactory
from app.utils import serialize_decimal


DEFAULT_MOCK_PLATFORM_DB = Path("data/runtime/mock_platform.sqlite3")
MOCK_PLATFORM_ONLINE = "online"
MOCK_PLATFORM_OFFLINE = "offline"

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS mock_platform_products (
        platform_name TEXT NOT NULL,
        internal_sku TEXT NOT NULL,
        platform_sku TEXT NOT NULL,
        product_name TEXT NOT NULL DEFAULT '',
        grade TEXT NOT NULL DEFAULT '',
        platform_price TEXT,
        platform_online_status TEXT NOT NULL,
        platform_stock_qty INTEGER NOT NULL DEFAULT 0,
        last_synced_at TEXT,
        last_platform_update_at TEXT,
        last_error TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(platform_name, internal_sku)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_mock_platform_products_platform
    ON mock_platform_products(platform_name)
    """,
]


class MockPlatformRepository:
    def __init__(self, db_path: Path = DEFAULT_MOCK_PLATFORM_DB) -> None:
        self.db_path = Path(db_path)
        self.connection_factory = SQLiteConnectionFactory(self.db_path, config=None)

    def connect(self) -> sqlite3.Connection:
        return self.connection_factory.connect_write()

    def init_schema(self) -> None:
        def initialize_schema(connection: sqlite3.Connection) -> None:
            for statement in SCHEMA_SQL:
                connection.execute(statement)

        self.connection_factory.initialize_database(initialize_schema)

    def reset(self) -> None:
        self.init_schema()
        with closing(self.connect()) as connection, connection:
            connection.execute("DELETE FROM mock_platform_products")

    def upsert_product_states(self, states: Iterable[MockPlatformProductState]) -> int:
        rows = [_state_to_row(state) for state in states]
        if not rows:
            return 0
        self.init_schema()
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT INTO mock_platform_products(
                    platform_name, internal_sku, platform_sku, product_name, grade,
                    platform_price, platform_online_status, platform_stock_qty,
                    last_synced_at, last_platform_update_at, last_error
                )
                VALUES(
                    :platform_name, :internal_sku, :platform_sku, :product_name, :grade,
                    :platform_price, :platform_online_status, :platform_stock_qty,
                    :last_synced_at, :last_platform_update_at, :last_error
                )
                ON CONFLICT(platform_name, internal_sku) DO UPDATE SET
                    platform_sku = excluded.platform_sku,
                    product_name = excluded.product_name,
                    grade = excluded.grade,
                    platform_price = excluded.platform_price,
                    platform_online_status = excluded.platform_online_status,
                    platform_stock_qty = excluded.platform_stock_qty,
                    last_synced_at = excluded.last_synced_at,
                    last_platform_update_at = excluded.last_platform_update_at,
                    last_error = excluded.last_error
                """,
                rows,
            )
            return connection.total_changes - before

    def list_product_states(
        self,
        *,
        platform_name: str | None = None,
        internal_sku: str | None = None,
    ) -> list[MockPlatformProductState]:
        self.init_schema()
        query = "SELECT * FROM mock_platform_products"
        clauses: list[str] = []
        params: list[str] = []
        if platform_name:
            clauses.append("platform_name = ?")
            params.append(platform_name)
        if internal_sku:
            clauses.append("internal_sku = ?")
            params.append(internal_sku)
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY platform_name ASC, internal_sku ASC"
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_state(row) for row in rows]

    def get_product_state(self, *, platform_name: str, internal_sku: str) -> MockPlatformProductState | None:
        self.init_schema()
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM mock_platform_products
                WHERE platform_name = ? AND internal_sku = ?
                """,
                (platform_name, internal_sku),
            ).fetchone()
        return _row_to_state(row) if row is not None else None

    def update_price(self, *, platform_name: str, internal_sku: str, price: Decimal, updated_at: datetime) -> None:
        self._update_fields(
            platform_name=platform_name,
            internal_sku=internal_sku,
            fields={
                "platform_price": serialize_decimal(price),
                "last_platform_update_at": _datetime_to_text(updated_at),
                "last_error": "",
            },
        )

    def update_online_status(
        self,
        *,
        platform_name: str,
        internal_sku: str,
        status: str,
        updated_at: datetime,
    ) -> None:
        self._update_fields(
            platform_name=platform_name,
            internal_sku=internal_sku,
            fields={
                "platform_online_status": status,
                "last_platform_update_at": _datetime_to_text(updated_at),
                "last_error": "",
            },
        )

    def update_stock(self, *, platform_name: str, internal_sku: str, stock_qty: int, updated_at: datetime) -> None:
        self._update_fields(
            platform_name=platform_name,
            internal_sku=internal_sku,
            fields={
                "platform_stock_qty": stock_qty,
                "last_platform_update_at": _datetime_to_text(updated_at),
                "last_error": "",
            },
        )

    def update_last_synced_at(self, *, platform_name: str, internal_sku: str, synced_at: datetime) -> None:
        self._update_fields(
            platform_name=platform_name,
            internal_sku=internal_sku,
            fields={"last_synced_at": _datetime_to_text(synced_at)},
        )

    def record_error(self, *, platform_name: str, internal_sku: str, error: str, updated_at: datetime) -> None:
        self._update_fields(
            platform_name=platform_name,
            internal_sku=internal_sku,
            fields={
                "last_error": error[:500],
                "last_platform_update_at": _datetime_to_text(updated_at),
            },
        )

    def _update_fields(self, *, platform_name: str, internal_sku: str, fields: dict[str, object]) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = [str(value) if value is not None else None for value in fields.values()]
        params.extend([platform_name, internal_sku])
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"""
                UPDATE mock_platform_products
                SET {assignments}
                WHERE platform_name = ? AND internal_sku = ?
                """,
                params,
            )


def seed_default_mock_platform(repository: MockPlatformRepository) -> int:
    now = datetime.now()
    repository.reset()
    return repository.upsert_product_states(
        [
            MockPlatformProductState(
                platform_name="default_platform",
                internal_sku="SKU-001",
                platform_sku="MOCK-SKU-001",
                product_name="艾莎",
                grade="A",
                platform_price=Decimal("14"),
                platform_online_status=MOCK_PLATFORM_ONLINE,
                platform_stock_qty=50,
                last_synced_at=now,
                last_platform_update_at=now,
            ),
            MockPlatformProductState(
                platform_name="default_platform",
                internal_sku="SKU-002",
                platform_sku="MOCK-SKU-002",
                product_name="艾莎",
                grade="B",
                platform_price=Decimal("13"),
                platform_online_status=MOCK_PLATFORM_OFFLINE,
                platform_stock_qty=0,
                last_synced_at=now,
                last_platform_update_at=now,
            ),
            MockPlatformProductState(
                platform_name="default_platform",
                internal_sku="SKU-003",
                platform_sku="MOCK-SKU-003",
                product_name="卡布奇诺",
                grade="A",
                platform_price=Decimal("12"),
                platform_online_status=MOCK_PLATFORM_OFFLINE,
                platform_stock_qty=12,
                last_synced_at=now,
                last_platform_update_at=now,
            ),
        ]
    )


def _state_to_row(state: MockPlatformProductState) -> dict[str, object]:
    return {
        "platform_name": state.platform_name,
        "internal_sku": state.internal_sku,
        "platform_sku": state.platform_sku,
        "product_name": state.product_name,
        "grade": state.grade,
        "platform_price": serialize_decimal(state.platform_price),
        "platform_online_status": state.platform_online_status,
        "platform_stock_qty": state.platform_stock_qty,
        "last_synced_at": _datetime_to_text(state.last_synced_at),
        "last_platform_update_at": _datetime_to_text(state.last_platform_update_at),
        "last_error": state.last_error,
    }


def _row_to_state(row: sqlite3.Row) -> MockPlatformProductState:
    price_raw = row["platform_price"]
    return MockPlatformProductState(
        platform_name=str(row["platform_name"]),
        internal_sku=str(row["internal_sku"]),
        platform_sku=str(row["platform_sku"]),
        product_name=str(row["product_name"] or ""),
        grade=str(row["grade"] or ""),
        platform_price=Decimal(str(price_raw)) if price_raw not in (None, "") else None,
        platform_online_status=str(row["platform_online_status"]),
        platform_stock_qty=int(row["platform_stock_qty"] or 0),
        last_synced_at=_datetime_from_text(row["last_synced_at"]),
        last_platform_update_at=_datetime_from_text(row["last_platform_update_at"]),
        last_error=str(row["last_error"] or ""),
    )


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_from_text(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value))
