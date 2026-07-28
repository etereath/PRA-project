from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA_VERSION = 12
BASELINE_TABLES = (
    "shadowbot_operations",
    "shadowbot_execution_attempts",
    "shadowbot_side_effect_checkpoints",
    "shadowbot_commit_batches",
    "shadowbot_commit_batch_items",
    "shadowbot_write_locks",
    "shadowbot_commit_result_receipts",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a read-only Task 13 pre-migration snapshot of the Runtime Schema v12 database."
    )
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def canonical_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, float):
        return {"float_repr": repr(value)}
    return value


def table_digest(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    table_info = connection.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    if not table_info:
        raise RuntimeError(f"required v12 baseline table is missing: {table}")
    columns = [str(row[1]) for row in table_info]
    primary_key_columns = [
        str(row[1])
        for row in sorted(
            (row for row in table_info if int(row[5]) > 0),
            key=lambda row: int(row[5]),
        )
    ]
    order_columns = primary_key_columns + [
        column for column in columns if column not in primary_key_columns
    ]
    select_columns = ", ".join(quote_identifier(column) for column in columns)
    order_clause = ", ".join(quote_identifier(column) for column in order_columns)
    rows = connection.execute(
        f"SELECT {select_columns} FROM {quote_identifier(table)} ORDER BY {order_clause}"
    )

    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {"table": table, "columns": columns},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    row_count = 0
    for row in rows:
        digest.update(b"\n")
        digest.update(
            json.dumps(
                [canonical_cell(value) for value in row],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        row_count += 1
    return {
        "columns": columns,
        "primary_key_columns": primary_key_columns,
        "row_count": row_count,
        "normalized_sha256": digest.hexdigest(),
    }


def database_facts(connection: sqlite3.Connection) -> dict[str, Any]:
    versions = [
        int(row[0])
        for row in connection.execute(
            "SELECT schema_version FROM runtime_schema_migrations ORDER BY schema_version"
        )
    ]
    actual_version = max(versions) if versions else 0
    if actual_version != EXPECTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"expected Runtime Schema v{EXPECTED_SCHEMA_VERSION}, found v{actual_version}"
        )
    integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    if integrity_rows != ["ok"]:
        raise RuntimeError(f"SQLite integrity_check failed: {integrity_rows}")
    foreign_key_rows = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    if foreign_key_rows:
        raise RuntimeError(f"SQLite foreign_key_check failed: {foreign_key_rows}")
    return {
        "schema_versions": versions,
        "integrity_check": integrity_rows,
        "foreign_key_violation_count": 0,
        "tables": {
            table: table_digest(connection, table)
            for table in BASELINE_TABLES
        },
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def freeze_baseline(runtime_db: Path, output_dir: Path) -> dict[str, Any]:
    source_path = runtime_db.expanduser().resolve()
    target_dir = output_dir.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"runtime database does not exist: {source_path}")
    target_dir.mkdir(parents=True, exist_ok=False)

    backup_path = target_dir / "pra_runtime_v12_baseline.sqlite3"
    temporary_backup = backup_path.with_suffix(".sqlite3.tmp")
    source_uri = source_path.as_uri() + "?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    try:
        source.execute("PRAGMA query_only = ON")
        source.execute("BEGIN")
        source_facts = database_facts(source)
        destination = sqlite3.connect(temporary_backup)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
        source.rollback()
    finally:
        source.close()

    backup = sqlite3.connect(temporary_backup)
    try:
        backup_facts = database_facts(backup)
    finally:
        backup.close()
    if source_facts != backup_facts:
        raise RuntimeError("source snapshot and backup normalized facts do not match")
    os.replace(temporary_backup, backup_path)

    manifest = {
        "schema_version": "task13-v12-baseline-manifest-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime_schema_version": EXPECTED_SCHEMA_VERSION,
        "source_runtime_db": str(source_path),
        "backup_path": str(backup_path),
        "backup_size_bytes": backup_path.stat().st_size,
        "backup_sha256": sha256_file(backup_path),
        "source_snapshot_matches_backup": True,
        "database_facts": backup_facts,
    }
    manifest_path = target_dir / "baseline_manifest.json"
    atomic_write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path)
    manifest["manifest_sha256"] = sha256_file(manifest_path)
    return manifest


def main() -> int:
    args = build_parser().parse_args()
    manifest = freeze_baseline(args.runtime_db, args.output_dir)
    print(
        json.dumps(
            {
                "backup_path": manifest["backup_path"],
                "backup_sha256": manifest["backup_sha256"],
                "manifest_path": manifest["manifest_path"],
                "manifest_sha256": manifest["manifest_sha256"],
                "source_snapshot_matches_backup": manifest["source_snapshot_matches_backup"],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
