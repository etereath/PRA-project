from __future__ import annotations

import os
import sqlite3
import subprocess
import time
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
    _execute_with_sqlite_retry,
    is_sqlite_concurrency_error,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


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
    with pytest.raises(SQLiteConfigurationError):
        SQLiteConnectionConfig.from_environment(
            environment={"PRA_SQLITE_SYNCHRONOUS": "OFF"}
        )
    with pytest.raises(SQLiteConfigurationError):
        SQLiteConnectionConfig(synchronous="OFF")


def test_unc_and_device_paths_are_rejected_before_directory_creation() -> None:
    for raw_path in (r"\\server\share\runtime.sqlite3", r"\\?\C:\runtime.sqlite3"):
        with pytest.raises(SQLiteStorageLocationError):
            SQLiteConnectionFactory(raw_path).connect_write()


def test_non_memory_file_uri_cannot_bypass_local_storage_or_read_only_guards(tmp_path: Path) -> None:
    config = SQLiteConnectionConfig(uri=True, purpose="test")
    file_uri = f"file:///{(tmp_path / 'uri-bypass.sqlite3').as_posix()}"
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(file_uri, config=config).connect_read()
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(file_uri, config=config).connect_write()


def test_memory_uri_is_only_allowed_as_an_explicit_controlled_test_target() -> None:
    test_config = SQLiteConnectionConfig(uri=True, purpose="test")
    factory = SQLiteConnectionFactory("file:task8-memory?mode=memory&cache=shared", config=test_config)
    assert factory.is_memory_database
    with factory.connect_write() as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")

    runtime_config = SQLiteConnectionConfig(uri=True, purpose="runtime")
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory("file:task8-memory?mode=memory", config=runtime_config).connect_write()


@pytest.mark.parametrize(
    "uri",
    [
        "file:task8-memory?mode=memory&mode=memory",
        "file:task8-memory?MODE=MEMORY",
        "file:task8-memory?mode%3Dmemory",
        "file:task8-memory%3Fmode%3Dmemory?cache=shared",
        "file://server/share/runtime.sqlite3?mode=memory",
    ],
)
def test_memory_uri_parser_rejects_ambiguous_variants(uri: str) -> None:
    config = SQLiteConnectionConfig(uri=True, purpose="test")
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(uri, config=config).connect_write()


def test_memory_uri_parser_rejects_mode_lookalike_in_disk_filename(tmp_path: Path) -> None:
    config = SQLiteConnectionConfig(uri=True, purpose="test")
    uri = f"file:{(tmp_path / 'mode=memory.sqlite3').as_posix()}"
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(uri, config=config).connect_write()


def test_wal_reader_does_not_block_an_independent_writer(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.sqlite3"
    factory = SQLiteConnectionFactory(db_path)
    factory.initialize_database(
        lambda connection: connection.execute(
            "CREATE TABLE values_table (value INTEGER NOT NULL)"
        )
    )
    reader = factory.connect_read()
    writer = factory.connect_write()
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO values_table(value) VALUES (1)")
        writer.commit()
        assert reader.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0
        reader.rollback()
        assert reader.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 1
    finally:
        reader.close()
        writer.close()


def test_second_writer_respects_busy_timeout_and_fails_within_a_bound(tmp_path: Path) -> None:
    db_path = tmp_path / "busy.sqlite3"
    config = SQLiteConnectionConfig(busy_timeout_ms=100)
    factory = SQLiteConnectionFactory(db_path, config=config)
    factory.initialize_database(lambda connection: connection.execute("CREATE TABLE sample (value INTEGER)"))
    first = factory.connect_write()
    second = factory.connect_write()
    first.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError) as raised:
            second.execute("BEGIN IMMEDIATE")
        elapsed = time.monotonic() - started
        assert is_sqlite_concurrency_error(raised.value)
        assert elapsed < 1.0
    finally:
        first.rollback()
        first.close()
        second.close()


def test_operational_health_reports_non_wal_without_mutating_it(tmp_path: Path) -> None:
    db_path = tmp_path / "health.sqlite3"
    repository = SQLiteRuntimeRepository(db_path)
    repository.init_schema()
    healthy = repository.check_operational_health()
    assert healthy.ok
    assert healthy.journal_mode == "wal"
    assert healthy.synchronous == "NORMAL"

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() == "delete"

    unhealthy = repository.check_operational_health()
    assert not unhealthy.ok
    assert unhealthy.journal_mode == "delete"
    assert "journal_mode expected wal" in unhealthy.summary
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"


@pytest.mark.skipif(os.name != "nt", reason="Windows drive classification")
def test_remote_windows_drive_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.repositories.sqlite_connection as sqlite_connection

    monkeypatch.setattr(sqlite_connection, "_get_windows_drive_type", lambda _root: DRIVE_REMOTE)
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(tmp_path / "runtime.sqlite3").connect_write()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point validation")
def test_reparse_point_to_remote_target_is_rejected(tmp_path: Path) -> None:
    link_path = tmp_path / "remote-link.sqlite3"
    try:
        link_path.symlink_to(r"\\server\share\runtime.sqlite3")
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(link_path).connect_write()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction validation")
def test_real_junction_to_local_target_is_resolved_before_drive_check(tmp_path: Path) -> None:
    target_dir = tmp_path / "junction-target"
    target_dir.mkdir()
    junction = tmp_path / "runtime-junction"
    result = subprocess.run(
        ["cmd.exe", "/c", f'mklink /J "{junction}" "{target_dir}"'],
        capture_output=True,
    )
    if result.returncode != 0 or not junction.is_dir():
        pytest.skip("junction creation unavailable")

    db_path = junction / "runtime.sqlite3"
    with SQLiteConnectionFactory(db_path).connect_write() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
    assert (target_dir / "runtime.sqlite3").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction validation")
def test_real_junction_final_target_still_applies_remote_drive_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app.repositories.sqlite_connection as sqlite_connection

    target_dir = tmp_path / "junction-target"
    target_dir.mkdir()
    junction = tmp_path / "remote-classified-junction"
    result = subprocess.run(
        ["cmd.exe", "/c", f'mklink /J "{junction}" "{target_dir}"'],
        capture_output=True,
    )
    if result.returncode != 0 or not junction.is_dir():
        pytest.skip("junction creation unavailable")

    monkeypatch.setattr(sqlite_connection, "_get_windows_drive_type", lambda _root: DRIVE_REMOTE)
    with pytest.raises(SQLiteStorageLocationError):
        SQLiteConnectionFactory(junction / "runtime.sqlite3").connect_write()


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

    assert _execute_with_sqlite_retry(
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
        _execute_with_sqlite_retry(
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


def test_lock_retry_jitter_is_capped_by_elapsed_budget() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def monotonic() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    def operation() -> None:
        raise CodedOperationalError("busy", sqlite3.SQLITE_BUSY, "SQLITE_BUSY")

    with pytest.raises(SQLiteConcurrencyError):
        _execute_with_sqlite_retry(
            operation,
            max_attempts=8,
            max_elapsed_ms=25,
            base_delay_ms=10,
            monotonic=monotonic,
            sleep=sleep,
            jitter=lambda delay: delay * 10,
        )
    assert sleeps == [0.025]


def test_lock_classification_ignores_localized_text_without_sqlite_code() -> None:
    assert not is_sqlite_concurrency_error(sqlite3.OperationalError("database is locked"))
    assert not is_sqlite_concurrency_error(
        CodedOperationalError("database is locked", sqlite3.SQLITE_ERROR, "SQLITE_ERROR")
    )
    assert is_sqlite_concurrency_error(
        CodedOperationalError("任意文本", sqlite3.SQLITE_LOCKED, "SQLITE_LOCKED")
    )
