from contextlib import closing
import sqlite3

import pytest

from app.repositories import sqlite_runtime_repository as module
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


def v17_database(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / 'runtime.sqlite3')
    runtime.init_schema()
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute('DROP TABLE execution_continuations')
        connection.execute('DELETE FROM runtime_schema_migrations WHERE schema_version = 18')
        connection.execute("UPDATE inventory_authority_state SET version = 7")
    return runtime


def test_v18_migration_is_repeatable_preserves_inventory_and_does_not_adopt_tasks(tmp_path):
    runtime = v17_database(tmp_path)
    runtime.init_schema()
    runtime.init_schema()
    assert runtime.check_schema_health().ok
    with closing(runtime.connect_read()) as connection:
        assert connection.execute('SELECT COUNT(*) FROM execution_continuations').fetchone()[0] == 0
        assert connection.execute('SELECT version FROM inventory_authority_state').fetchone()[0] == 7


def test_v18_migration_failure_is_atomic(tmp_path, monkeypatch):
    runtime = v17_database(tmp_path)
    monkeypatch.setattr(module, 'SCHEMA_V18_SQL', (*module.SCHEMA_V18_SQL, 'INVALID SQL'))
    with pytest.raises(sqlite3.DatabaseError):
        runtime.init_schema()
    assert runtime.schema_versions()[-1] == 17
    with closing(runtime.connect_read()) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'execution_continuations'").fetchone() is None
