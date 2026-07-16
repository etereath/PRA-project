from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from app.repositories.sqlite_connection import (
    DRIVE_REMOTE,
    SQLiteConcurrencyError,
    SQLiteConnectionConfig,
    SQLiteConnectionFactory,
    SQLiteConfigurationError,
    SQLiteDatabaseNotFoundError,
    SQLiteStorageLocationError,
    execute_with_sqlite_retry,
    is_sqlite_concurrency_error,
)


class CodedOperationalError(sqlite3.OperationalError):
    def __init__(self, message: str, code: int, name: str) -> None:
        super().__init__(message)
        self.sqlite_errorcode = code
        self.sqlite_errorname = name


def test_explicit_initialization_enables_wal_and_connection_pragmas(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    factory = SQLiteConnectionFactory(db_path)

    factory.initialize_database(
        lambda connection: connection.execute(
            "CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
    )

    with factory.connect_read() as connection:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_read_only_connection_does_not_create_or_change_database(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.sqlite3"
    with pytest.raises(SQLiteDatabaseNotFoundError):
        SQLiteConnectionFactory(missing_path).connect_read()
    assert not missing_path.exists()

    db_path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")

    with SQLiteConnectionFactory(db_path).connect_read() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"


def test_memory_database_is_explicit_test_path_without_wal_claim() -> None:
    with SQLiteConnectionFactory(":memory:").connect_write() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_invalid_environment_values_fail_closed() -> None:
    with pytest.raises(SQLiteConfigurationError):
        SQLiteConnectionConfig.from_environment(
            environment={"PRA_SQLITE_BUSY_TIMEOUT_MS": "50"}
        )


def test_unc_and_device_paths_are_rejected_before_directory_creation() -> None:
    for raw_path in (r"\\server\share\runtime.sqlite3", r"\\?\C:\runtime.sqlite3"):
        with pytest.raises(SQLiteStorageLocationError):
            SQLiteConnectionFactory(raw_path).connect_write()


@pytest.mark.skipif(os.name != "nt", reason="Windows drive classification")
def test_remote_windows_drive_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.repositories.sqlite_connection as sqlite_connection

    monkeypatch.setattr(sqlite_connection, "_get_windows_drive_type", lambda _root: DRIVE_REMOTE)
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(tmp_path / "runtime.sqlite3").connect_write()


def test_lock_retry_uses_codes_and_bounded_injected_clock() -> None:
    now = [0.0]
    sleeps: list[float] = []
    calls = [0]

    def monotonic() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    def operation() -> str:
        calls[0] += 1
        if calls[0] < 3:
            raise CodedOperationalError("数据库正忙", sqlite3.SQLITE_BUSY, "SQLITE_BUSY")
        return "ok"

    assert execute_with_sqlite_retry(
        operation,
        max_attempts=3,
        max_elapsed_ms=1000,
        base_delay_ms=10,
        monotonic=monotonic,
        sleep=sleep,
    ) == "ok"
    assert calls[0] == 3
    assert sleeps == [0.01, 0.02]


def test_lock_retry_stops_at_attempt_budget_and_preserves_code() -> None:
    calls = [0]

    def operation() -> None:
        calls[0] += 1
        raise CodedOperationalError(
            "snapshot verrouillé",
            sqlite3.SQLITE_BUSY_SNAPSHOT,
            "SQLITE_BUSY_SNAPSHOT",
        )

    with pytest.raises(SQLiteConcurrencyError) as raised:
        execute_with_sqlite_retry(
            operation,
            max_attempts=2,
            max_elapsed_ms=1000,
            base_delay_ms=1,
            sleep=lambda _seconds: None,
        )
    assert calls[0] == 2
    assert raised.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT
    assert raised.value.sqlite_errorname == "SQLITE_BUSY_SNAPSHOT"
    assert "snapshot verrouillé" not in str(raised.value)


def test_lock_classification_ignores_localized_text_without_sqlite_code() -> None:
    assert not is_sqlite_concurrency_error(sqlite3.OperationalError("database is locked"))
    assert not is_sqlite_concurrency_error(
        CodedOperationalError("database is locked", sqlite3.SQLITE_ERROR, "SQLITE_ERROR")
    )
    assert is_sqlite_concurrency_error(
        CodedOperationalError("任意文本", sqlite3.SQLITE_LOCKED, "SQLITE_LOCKED")
    )
