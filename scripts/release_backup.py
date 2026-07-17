"""Release manifest, runtime backup, restore, and rollback operations.

The command is intentionally separate from the application runtime.  It can
be used from a checked-out repository before a release is installed, while the
database copy uses SQLite's backup API so a live WAL database is not copied by
just duplicating its main file.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository  # noqa: E402
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION, REQUIRED_RUNTIME_TABLES  # noqa: E402


class ReleaseBackupError(RuntimeError):
    """Raised when a release or backup safety invariant is not satisfied."""


DEFAULT_CONFIGURATION_ITEM_NAMES = (
    "PRA_ALLOWED_DATA_DIRS",
    "PRA_SQLITE_BUSY_TIMEOUT_MS",
    "PRA_SQLITE_SYNCHRONOUS",
    "PRA_SQLITE_RETRY_MAX_ATTEMPTS",
    "PRA_SQLITE_RETRY_MAX_ELAPSED_MS",
    "PRA_SQLITE_RETRY_BASE_DELAY_MS",
    "DEFAULT_NOTIFICATION_CHANNEL",
    "DEV_MODE",
    "SHADOWBOT_QUEUE_DIR",
    "SHADOWBOT_REQUEST_DIR",
    "SHADOWBOT_RUNNER_TYPE",
    "SHADOWBOT_RUNNER_COMMAND",
    "SHADOWBOT_EVIDENCE_DIR",
    "SHADOWBOT_APPLET_URI",
    "YINGDAO_API_BASE_URL",
    "YINGDAO_ACCESS_KEY_ID",
    "YINGDAO_ACCESS_KEY_SECRET",
    "YINGDAO_ROBOT_UUID",
    "YINGDAO_ACCOUNT_NAME",
    "YINGDAO_ROBOT_CLIENT_GROUP_UUID",
    "YINGDAO_REQUEST_PARAM_NAME",
    "YINGDAO_INCLUDE_FLAT_PARAMS",
    "YINGDAO_WAIT_TIMEOUT_SECONDS",
    "YINGDAO_RUN_TIMEOUT_SECONDS",
    "YINGDAO_PRIORITY",
)

LOGICAL_TABLES = (
    "notification_outbox",
    "notification_delivery_attempts",
    "review_tasks",
    "review_tokens",
    "shadowbot_operations",
    "shadowbot_execution_attempts",
    "execution_logs",
)

SECRET_CONFIG_NAME_RE = re.compile(r"(?:^|[._-])(env|secret|password|passwd)(?:$|[._-])", re.I)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)(?:['\"]?)(?P<key>[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|"
    r"access[_-]?key|credential[_-]?blob|credentialblob)[A-Za-z0-9_.-]*)['\"]?"
    r"(?:\s*[:=])\s*['\"](?P<value>[^'\"]+)['\"]"
)
PLACEHOLDER_VALUES = frozenset(
    {
        "CHANGE_ME",
        "DUMMY",
        "EXAMPLE",
        "PLACEHOLDER",
        "REDACTED",
        "REPLACE_ME",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ReleaseBackupError(f"{label} does not exist or is not a file: {path}")
    return path


def _require_directory(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ReleaseBackupError(f"{label} does not exist or is not a directory: {path}")
    return path


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.tmp-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability; Windows may not expose directory FDs."""

    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _git_commit(git_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseBackupError("unable to resolve the release Git commit") from exc
    commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit):
        raise ReleaseBackupError("git rev-parse returned an invalid commit")
    return commit


def _normalise_config_names(extra_names: Iterable[str] = ()) -> list[str]:
    names = {name.strip() for name in DEFAULT_CONFIGURATION_ITEM_NAMES}
    names.update(name.strip() for name in extra_names if name and name.strip())
    if any("=" in name or "\n" in name or "\r" in name for name in names):
        raise ReleaseBackupError("configuration item names must be names only, not key/value pairs")
    return sorted(names)


def build_release_manifest(
    *,
    git_root: Path,
    wheel_path: Path,
    config_names: Iterable[str] = (),
    git_commit: str | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a value-free release manifest for a wheel and repository commit."""

    wheel = _require_file(wheel_path, "wheel")
    return {
        "manifest_version": 1,
        "project": "PRA",
        "generated_at_utc": generated_at_utc or _utc_now(),
        "git_commit": git_commit or _git_commit(git_root.resolve()),
        "wheel": {
            "file_name": wheel.name,
            "sha256": sha256_file(wheel),
            "size_bytes": wheel.stat().st_size,
        },
        "runtime_schema_version": LATEST_RUNTIME_SCHEMA_VERSION,
        "configuration_item_names": _normalise_config_names(config_names),
        "secret_values_included": False,
    }


def write_release_manifest(
    *,
    output: Path,
    git_root: Path,
    wheel_path: Path,
    config_names: Iterable[str] = (),
) -> dict[str, Any]:
    manifest = build_release_manifest(
        git_root=git_root,
        wheel_path=wheel_path,
        config_names=config_names,
    )
    _atomic_write_json(output.resolve(), manifest)
    return manifest


def _is_placeholder(value: str) -> bool:
    normalized = value.strip()
    upper = normalized.upper()
    return (
        not normalized
        or upper in PLACEHOLDER_VALUES
        or ("<" in normalized and ">" in normalized)
        or upper.startswith("EXAMPLE_")
    )


def _is_sensitive_config_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized.endswith(("_selector", "_field", "_name", "_url", "_path", "_dir", "_param", "_type")):
        return False
    return bool(
        re.search(
            r"(?:^|_)(?:password|passwd|secret|token|api_key|access_key|credential_blob|credentialblob)(?:_|$)",
            normalized,
        )
    )


def validate_nonsecret_config(path: Path) -> None:
    """Reject obvious secret-bearing configuration before it enters a backup."""

    if SECRET_CONFIG_NAME_RE.search(path.name):
        raise ReleaseBackupError(f"secret-like configuration filename is not allowed: {path.name}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReleaseBackupError(f"configuration must be UTF-8 text: {path.name}") from exc
    for match in SECRET_ASSIGNMENT_RE.finditer(text):
        if not _is_sensitive_config_key(match.group("key")):
            continue
        value = match.group("value")
        if not _is_placeholder(value):
            raise ReleaseBackupError(
                f"configuration contains a non-placeholder secret value for {match.group('key')}"
            )


def _destination_name(raw: str, source: Path) -> str:
    value = raw.strip()
    if not value:
        return source.name
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or value in {"manifest.json", "SHA256SUMS.txt", "release-manifest.json"}
    ):
        raise ReleaseBackupError(f"backup destination name must be a plain filename: {value}")
    return value


def parse_source_specs(raw_values: Iterable[str]) -> list[tuple[str, Path]]:
    """Parse ``path`` or ``destination_name=path`` command-line values."""

    specs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = str(raw)
        if "=" in value and not re.match(r"^[A-Za-z]:[\\/].*", value):
            destination, source_value = value.split("=", 1)
        else:
            destination, source_value = "", value
        source = _require_file(source_value, "source file")
        name = _destination_name(destination, source)
        if name in seen:
            raise ReleaseBackupError(f"duplicate backup destination name: {name}")
        seen.add(name)
        specs.append((name, source))
    return specs


def _copy_file_record(source: Path, destination: Path, *, role: str, relative_path: str) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "path": relative_path.replace("\\", "/"),
        "role": role,
        "size_bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def _database_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReleaseBackupError(f"runtime SQLite database does not exist: {path}")
    repository = SQLiteRuntimeRepository(path)
    schema_health = repository.check_schema_health()
    operational_health = repository.check_operational_health()
    integrity_check = ""
    foreign_key_violations: list[list[Any]] = []
    table_counts: dict[str, int | None] = {}
    try:
        with closing(sqlite3.connect(str(path), timeout=5)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            row = connection.execute("PRAGMA integrity_check").fetchone()
            integrity_check = str(row[0]) if row else ""
            foreign_key_violations = [list(item) for item in connection.execute("PRAGMA foreign_key_check")]
            existing_tables = {
                str(item[0])
                for item in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in sorted(REQUIRED_RUNTIME_TABLES):
                if table not in existing_tables:
                    table_counts[table] = None
                    continue
                row = connection.execute(
                    "SELECT COUNT(*) FROM " + table
                ).fetchone()
                table_counts[table] = int(row[0]) if row else 0
    except sqlite3.Error as exc:
        raise ReleaseBackupError(f"runtime SQLite validation failed: {type(exc).__name__}") from exc
    logical_table_counts = {table: table_counts[table] for table in LOGICAL_TABLES}
    ok = bool(
        schema_health.ok
        and operational_health.ok
        and integrity_check.lower() == "ok"
        and not foreign_key_violations
    )
    return {
        "ok": ok,
        "schema_health": schema_health.as_dict(),
        "operational_health": operational_health.as_dict(),
        "integrity_check": integrity_check,
        "foreign_key_violations": foreign_key_violations,
        "table_counts": table_counts,
        "logical_table_counts": logical_table_counts,
    }


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source), timeout=5)
    destination_connection = sqlite3.connect(str(destination), timeout=5)
    try:
        source_connection.backup(destination_connection, pages=1000, sleep=0.05)
        destination_connection.execute("PRAGMA journal_mode = WAL")
        destination_connection.execute("PRAGMA synchronous = NORMAL")
        destination_connection.commit()
        destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        destination_connection.rollback()
        raise ReleaseBackupError(f"SQLite backup failed: {type(exc).__name__}") from exc
    finally:
        destination_connection.close()
        source_connection.close()


def _checkpoint_sqlite(path: Path) -> None:
    try:
        with closing(sqlite3.connect(str(path), timeout=5)) as connection, connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        raise ReleaseBackupError(f"SQLite checkpoint failed: {type(exc).__name__}") from exc


def migrate_runtime_database(
    *,
    source_db: Path,
    output_db: Path,
    force: bool = False,
) -> dict[str, Any]:
    """Upgrade a runtime database in a verified copy without mutating the source."""

    source = _require_file(source_db, "source runtime database")
    output = output_db.expanduser().resolve()
    if source == output:
        raise ReleaseBackupError("migration output must be different from the source database")
    if output.exists() and not force:
        raise ReleaseBackupError(f"migration output exists; pass --force to replace it: {output}")

    source_snapshot = _database_snapshot(source)
    if str(source_snapshot.get("integrity_check", "")).lower() != "ok":
        raise ReleaseBackupError("source runtime database failed SQLite integrity check")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.migrate-", dir=output.parent))
    staged_database = staging / output.name
    try:
        _backup_sqlite(source, staged_database)
        SQLiteRuntimeRepository(staged_database).init_schema()
        _checkpoint_sqlite(staged_database)
        target_snapshot = _database_snapshot(staged_database)
        if not target_snapshot["ok"]:
            raise ReleaseBackupError("migrated runtime database failed v6 health validation")

        source_counts = source_snapshot["logical_table_counts"]
        target_counts = target_snapshot["logical_table_counts"]
        preserved_tables = [
            table
            for table, count in source_counts.items()
            if count is not None and target_counts.get(table) == count
        ]
        if len(preserved_tables) != sum(count is not None for count in source_counts.values()):
            raise ReleaseBackupError("migration changed a pre-existing logical table row count")

        if force:
            for sidecar in (Path(str(output) + "-wal"), Path(str(output) + "-shm")):
                if sidecar.exists():
                    sidecar.unlink()
        os.replace(staged_database, output)
        _fsync_directory(output.parent)
        return {
            "source_db": str(source),
            "output_db": str(output),
            "source_schema_version": source_snapshot["schema_health"].get("actual_version"),
            "target_schema_version": target_snapshot["schema_health"].get("actual_version"),
            "preserved_logical_tables": preserved_tables,
            "source_snapshot": source_snapshot,
            "target_snapshot": target_snapshot,
        }
    except PermissionError as exc:
        raise ReleaseBackupError(
            "migration target is locked; stop PRA services and close database consumers before retrying"
        ) from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _file_records_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ReleaseBackupError("backup manifest has no file records")
    return records


def _safe_backup_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ReleaseBackupError(f"backup manifest path escapes backup root: {relative_path}") from exc
    return candidate


def _write_checksums(root: Path, relative_paths: Iterable[str]) -> None:
    lines = []
    for relative_path in sorted(set(relative_paths)):
        path = _safe_backup_path(root, relative_path)
        if not path.is_file():
            raise ReleaseBackupError(f"cannot checksum missing backup file: {relative_path}")
        normalized_path = relative_path.replace("\\", "/")
        lines.append(f"{sha256_file(path)}  {normalized_path}")
    _atomic_write_text(root / "SHA256SUMS.txt", "\n".join(lines) + "\n", encoding="ascii")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseBackupError(f"invalid JSON file: {path}") from exc
    if not isinstance(payload, dict):
        raise ReleaseBackupError(f"JSON root must be an object: {path}")
    return payload


def verify_backup(backup_path: Path) -> dict[str, Any]:
    """Verify checksums, release metadata, SQLite health, and logical row counts."""

    root = _require_directory(backup_path, "backup")
    manifest_path = root / "manifest.json"
    checksums_path = root / "SHA256SUMS.txt"
    release_manifest_path = root / "release-manifest.json"
    manifest = _read_json(manifest_path)
    release_manifest = _read_json(release_manifest_path)
    if manifest.get("release_manifest") != release_manifest:
        raise ReleaseBackupError("release manifest does not match backup manifest")

    checksum_entries: dict[str, str] = {}
    try:
        checksum_lines = checksums_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseBackupError("SHA256SUMS.txt is missing or is not ASCII") from exc
    for line in checksum_lines:
        if not line.strip():
            continue
        try:
            digest, relative_path = line.split("  ", 1)
        except ValueError as exc:
            raise ReleaseBackupError(f"invalid checksum line: {line}") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseBackupError(f"invalid SHA-256 digest in checksum file: {relative_path}")
        if relative_path in checksum_entries:
            raise ReleaseBackupError(f"duplicate checksum path: {relative_path}")
        checksum_entries[relative_path] = digest

    records = _file_records_from_manifest(manifest)
    record_paths = {str(record.get("path")) for record in records}
    expected_paths = record_paths | {"manifest.json"}
    if set(checksum_entries) != expected_paths:
        raise ReleaseBackupError(
            "checksum file does not cover exactly the manifest files and manifest.json"
        )
    for relative_path, expected_digest in checksum_entries.items():
        path = _safe_backup_path(root, relative_path)
        if not path.is_file() or sha256_file(path) != expected_digest:
            raise ReleaseBackupError(f"checksum mismatch: {relative_path}")
    for record in records:
        relative_path = str(record.get("path"))
        path = _safe_backup_path(root, relative_path)
        if int(record.get("size_bytes", -1)) != path.stat().st_size:
            raise ReleaseBackupError(f"size mismatch: {relative_path}")
        if str(record.get("sha256")) != sha256_file(path):
            raise ReleaseBackupError(f"manifest hash mismatch: {relative_path}")

    database_path = _safe_backup_path(root, "runtime/pra_runtime.sqlite3")
    database_snapshot = _database_snapshot(database_path)
    if not database_snapshot["ok"]:
        raise ReleaseBackupError("backup runtime database failed schema/health validation")
    expected_snapshot = manifest.get("database_validation", {}).get("backup_snapshot")
    if not isinstance(expected_snapshot, dict):
        raise ReleaseBackupError("backup manifest has no database validation snapshot")
    if database_snapshot["logical_table_counts"] != expected_snapshot.get("logical_table_counts"):
        raise ReleaseBackupError("backup logical table counts do not match the manifest")
    return manifest


def _new_backup_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]


def create_backup(
    *,
    runtime_db: Path,
    backup_dir: Path,
    wheel_path: Path,
    input_specs: Sequence[tuple[str, Path]] = (),
    config_specs: Sequence[tuple[str, Path]] = (),
    git_root: Path = ROOT,
    config_names: Iterable[str] = (),
    backup_id: str | None = None,
) -> Path:
    """Create and atomically publish a fully verified backup directory."""

    runtime_source = _require_file(runtime_db, "runtime database")
    source_snapshot = _database_snapshot(runtime_source)
    if not source_snapshot["ok"]:
        summary = source_snapshot["schema_health"].get("summary", "unknown health failure")
        raise ReleaseBackupError(f"source runtime database is not healthy; backup aborted: {summary}")
    for _, source in config_specs:
        validate_nonsecret_config(source)

    root = backup_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected_backup_id = backup_id or _new_backup_id()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", selected_backup_id):
        raise ReleaseBackupError("backup_id contains unsupported characters")
    final_dir = root / selected_backup_id
    if final_dir.exists():
        raise ReleaseBackupError(f"backup destination already exists: {final_dir}")
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{selected_backup_id}.tmp-", dir=root))
    try:
        files: list[dict[str, Any]] = []
        runtime_destination = temporary_dir / "runtime" / "pra_runtime.sqlite3"
        _backup_sqlite(runtime_source, runtime_destination)
        backup_snapshot = _database_snapshot(runtime_destination)
        if not backup_snapshot["ok"]:
            raise ReleaseBackupError("copied runtime database failed schema/health validation")
        if backup_snapshot["logical_table_counts"] != source_snapshot["logical_table_counts"]:
            raise ReleaseBackupError("copied runtime database logical table counts changed")
        files.append(
            {
                "path": "runtime/pra_runtime.sqlite3",
                "role": "runtime_database",
                "size_bytes": runtime_destination.stat().st_size,
                "sha256": sha256_file(runtime_destination),
            }
        )

        for name, source in input_specs:
            files.append(
                _copy_file_record(
                    source,
                    temporary_dir / "inputs" / name,
                    role="business_input",
                    relative_path=f"inputs/{name}",
                )
            )
        for name, source in config_specs:
            files.append(
                _copy_file_record(
                    source,
                    temporary_dir / "config" / name,
                    role="operations_config",
                    relative_path=f"config/{name}",
                )
            )

        release_manifest = build_release_manifest(
            git_root=git_root,
            wheel_path=wheel_path,
            config_names=config_names,
        )
        release_manifest_path = temporary_dir / "release-manifest.json"
        _atomic_write_json(release_manifest_path, release_manifest)
        files.append(
            {
                "path": "release-manifest.json",
                "role": "release_manifest",
                "size_bytes": release_manifest_path.stat().st_size,
                "sha256": sha256_file(release_manifest_path),
            }
        )

        manifest = {
            "manifest_version": 1,
            "backup_id": selected_backup_id,
            "created_at_utc": _utc_now(),
            "release_manifest": release_manifest,
            "files": files,
            "database_validation": {
                "source_snapshot": source_snapshot,
                "backup_snapshot": backup_snapshot,
                "logical_table_counts_match": True,
            },
            "secret_values_included": False,
        }
        manifest_path = temporary_dir / "manifest.json"
        _atomic_write_json(manifest_path, manifest)
        _write_checksums(temporary_dir, [record["path"] for record in files] + ["manifest.json"])
        verify_backup(temporary_dir)

        os.replace(temporary_dir, final_dir)
        _fsync_directory(root)
        _atomic_write_json(
            root / "latest.json",
            {
                "backup_id": selected_backup_id,
                "updated_at_utc": _utc_now(),
            },
        )
        return final_dir
    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def _assert_not_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return
    raise ReleaseBackupError(f"{label} must not be inside the backup being restored")


def restore_backup(
    *,
    backup_path: Path,
    runtime_db: Path,
    input_dir: Path | None = None,
    config_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Restore a verified backup; existing targets require explicit force."""

    backup_root = _require_directory(backup_path, "backup")
    manifest = verify_backup(backup_root)
    target_runtime = runtime_db.expanduser().resolve()
    _assert_not_inside(target_runtime, backup_root, "runtime database target")
    target_inputs = input_dir.expanduser().resolve() if input_dir is not None else None
    target_config = config_dir.expanduser().resolve() if config_dir is not None else None
    for target, label in ((target_inputs, "input target"), (target_config, "config target")):
        if target is not None:
            _assert_not_inside(target, backup_root, label)

    records = _file_records_from_manifest(manifest)
    input_records = [record for record in records if record.get("role") == "business_input"]
    config_records = [record for record in records if record.get("role") == "operations_config"]
    targets: list[tuple[Path, Path, str]] = []
    if target_runtime.exists() and not force:
        raise ReleaseBackupError(f"runtime target exists; pass --force to replace it: {target_runtime}")
    for sidecar in (
        Path(str(target_runtime) + "-wal"),
        Path(str(target_runtime) + "-shm"),
    ):
        if sidecar.exists() and not force:
            raise ReleaseBackupError(f"runtime sidecar exists; stop the service or pass --force: {sidecar}")
    if target_inputs is not None:
        for record in input_records:
            source = _safe_backup_path(backup_root, str(record["path"]))
            target = target_inputs / Path(str(record["path"])).name
            if target.exists() and not force:
                raise ReleaseBackupError(f"input target exists; pass --force to replace it: {target}")
            targets.append((source, target, "input"))
    if target_config is not None:
        for record in config_records:
            source = _safe_backup_path(backup_root, str(record["path"]))
            target = target_config / Path(str(record["path"])).name
            if target.exists() and not force:
                raise ReleaseBackupError(f"config target exists; pass --force to replace it: {target}")
            targets.append((source, target, "config"))

    target_runtime.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pra-restore-", dir=target_runtime.parent))
    try:
        staged_runtime = staging / target_runtime.name
        shutil.copy2(_safe_backup_path(backup_root, "runtime/pra_runtime.sqlite3"), staged_runtime)
        staged_snapshot = _database_snapshot(staged_runtime)
        expected_snapshot = manifest["database_validation"]["backup_snapshot"]
        if (
            not staged_snapshot["ok"]
            or staged_snapshot["logical_table_counts"] != expected_snapshot["logical_table_counts"]
        ):
            raise ReleaseBackupError("staged restore database failed validation")

        staged_targets: list[tuple[Path, Path, str]] = []
        for index, (source, target, role) in enumerate(targets):
            staged_file = staging / f"file-{index}-{target.name}"
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged_file)
            staged_targets.append((staged_file, target, role))

        if force:
            for sidecar in (
                Path(str(target_runtime) + "-wal"),
                Path(str(target_runtime) + "-shm"),
            ):
                if sidecar.exists():
                    sidecar.unlink()
        os.replace(staged_runtime, target_runtime)
        restored_files = [str(target_runtime)]
        for staged_file, target, role in staged_targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_file, target)
            restored_files.append(str(target))
        _fsync_directory(target_runtime.parent)
        return {
            "backup_id": manifest["backup_id"],
            "runtime_db": str(target_runtime),
            "restored_files": restored_files,
            "force": force,
            "database_validation": staged_snapshot,
        }
    except PermissionError as exc:
        raise ReleaseBackupError(
            "restore target is locked; stop PRA services and close Excel before retrying"
        ) from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PRA release manifest and runtime backup tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest", help="write a value-free release manifest")
    manifest_parser.add_argument("--output", required=True, type=Path)
    manifest_parser.add_argument("--wheel", required=True, type=Path)
    manifest_parser.add_argument("--git-root", type=Path, default=ROOT)
    manifest_parser.add_argument("--config-name", action="append", default=[])

    backup_parser = subparsers.add_parser("backup", help="create and atomically publish a verified backup")
    backup_parser.add_argument("--runtime-db", required=True, type=Path)
    backup_parser.add_argument("--backup-dir", required=True, type=Path)
    backup_parser.add_argument("--wheel", required=True, type=Path)
    backup_parser.add_argument("--input", dest="inputs", action="append", default=[])
    backup_parser.add_argument("--config", dest="configs", action="append", default=[])
    backup_parser.add_argument("--git-root", type=Path, default=ROOT)
    backup_parser.add_argument("--config-name", action="append", default=[])
    backup_parser.add_argument("--backup-id")

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="upgrade a runtime database in a verified copy without changing the source",
    )
    migrate_parser.add_argument("--source-db", required=True, type=Path)
    migrate_parser.add_argument("--output-db", required=True, type=Path)
    migrate_parser.add_argument("--force", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="verify a published backup")
    verify_parser.add_argument("--backup", required=True, type=Path)

    for command, help_text in (
        ("restore", "restore a verified backup without replacing existing targets by default"),
        ("rollback", "restore a verified backup as an explicit rollback operation"),
    ):
        restore_parser = subparsers.add_parser(command, help=help_text)
        restore_parser.add_argument("--backup", required=True, type=Path)
        restore_parser.add_argument("--runtime-db", required=True, type=Path)
        restore_parser.add_argument("--input-dir", type=Path)
        restore_parser.add_argument("--config-dir", type=Path)
        if command == "restore":
            restore_parser.add_argument("--force", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "manifest":
            manifest = write_release_manifest(
                output=args.output,
                git_root=args.git_root,
                wheel_path=args.wheel,
                config_names=args.config_name,
            )
            print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "backup":
            path = create_backup(
                runtime_db=args.runtime_db,
                backup_dir=args.backup_dir,
                wheel_path=args.wheel,
                input_specs=parse_source_specs(args.inputs),
                config_specs=parse_source_specs(args.configs),
                git_root=args.git_root,
                config_names=args.config_name,
                backup_id=args.backup_id,
            )
            print(f"backup=PASS path={path}")
            return 0
        if args.command == "migrate":
            result = migrate_runtime_database(
                source_db=args.source_db,
                output_db=args.output_db,
                force=args.force,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "verify":
            manifest = verify_backup(args.backup)
            print(f"backup_verify=PASS backup_id={manifest['backup_id']}")
            return 0
        if args.command == "restore":
            result = restore_backup(
                backup_path=args.backup,
                runtime_db=args.runtime_db,
                input_dir=args.input_dir,
                config_dir=args.config_dir,
                force=args.force,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "rollback":
            result = restore_backup(
                backup_path=args.backup,
                runtime_db=args.runtime_db,
                input_dir=args.input_dir,
                config_dir=args.config_dir,
                force=True,
            )
            print(json.dumps({**result, "operation": "rollback"}, ensure_ascii=False, sort_keys=True))
            return 0
        return 2
    except (ReleaseBackupError, OSError, sqlite3.Error) as exc:
        print(f"release_backup=FAIL reason={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
