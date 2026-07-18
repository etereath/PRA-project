"""Release manifest, runtime backup, restore, and rollback operations.

The command is intentionally separate from the application runtime.  It can
be used from a checked-out repository before a release is installed, while the
database copy uses SQLite's backup API so a live WAL database is not copied by
just duplicating its main file.
"""

from __future__ import annotations

import argparse
import configparser
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
from typing import Any, Iterable, NoReturn, Sequence
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
SENSITIVE_CONFIG_KEY_RE = re.compile(
    r"(?:^|[_\-.])(webhook|authorization|token|secret|password|passwd|credential|"
    r"api[_-]?key|access[_-]?key)(?:$|[_\-.])",
    re.I,
)
CONFIG_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?(?:\$env:|\$)?['\"]?(?P<key>[A-Za-z0-9_.-]+)['\"]?"
    r"\s*(?:=|:)\s*(?P<value>.*?)\s*(?:#.*)?$"
)
PLACEHOLDER_VALUES = frozenset(
    {
        "CHANGE_ME",
        "DUMMY",
        "EXAMPLE",
        "PLACEHOLDER",
        "REDACTED",
        "REPLACE_ME",
        "REPLACE-ME",
        "YOUR_VALUE",
        "YOUR-VALUE",
    }
)
TRANSACTION_FILE_GLOB = ".pra-*.transaction.json"


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
        or upper.startswith("REPLACE-")
        or upper.startswith("REPLACE_")
        or "REPLACE-ME" in upper
        or "REPLACE_ME" in upper
        or upper.startswith("YOUR-")
        or upper.startswith("YOUR_")
    )


def _is_sensitive_config_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    normalized = normalized.lower().replace("-", "_").replace(".", "_")
    if normalized.endswith(("_selector", "_field", "_name", "_param", "_type")):
        return False
    return bool(SENSITIVE_CONFIG_KEY_RE.search(normalized))


def _config_value_is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return _is_placeholder(value)
    if isinstance(value, (list, tuple)):
        return not value or all(_config_value_is_placeholder(item) for item in value)
    return False


def _raise_sensitive_config(key: str) -> NoReturn:
    raise ReleaseBackupError(
        f"configuration contains a non-placeholder secret value for {key}"
    )


def _scan_structured_config(value: Any, *, path: str = "config") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if _is_sensitive_config_key(key):
                if not _config_value_is_placeholder(child):
                    _raise_sensitive_config(child_path)
                continue
            _scan_structured_config(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_structured_config(child, path=f"{path}[{index}]")


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseBackupError(f"configuration JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _strip_assignment_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _scan_line_config(text: str, *, suffix: str) -> None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        if suffix == ".conf" and line.startswith("[") and line.endswith("]"):
            continue
        match = CONFIG_ASSIGNMENT_RE.match(line)
        if not match:
            if suffix == ".ps1" and line in {"@{", "}", "};", ")", "(", "@("}:
                continue
            raise ReleaseBackupError(f"configuration line cannot be parsed: {suffix}")
        key = match.group("key")
        if _is_sensitive_config_key(key):
            value = _strip_assignment_value(match.group("value"))
            if not _is_placeholder(value):
                _raise_sensitive_config(key)


def _scan_ini_config(text: str, path: Path) -> None:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ReleaseBackupError(f"configuration INI is invalid: {path.name}") from exc
    for section in parser.sections():
        for key, value in parser.items(section):
            if _is_sensitive_config_key(key) and not _is_placeholder(value):
                _raise_sensitive_config(f"{section}.{key}")


def _scan_yaml_fallback(text: str) -> None:
    """Conservative YAML fallback for environments without PyYAML."""

    parsed_any = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "---", "...")):
            continue
        matches = list(
            re.finditer(
                r"(?:^|[,{]\s*)(?:['\"]?)(?P<key>[A-Za-z0-9_.-]+)['\"]?\s*:\s*(?P<value>[^,}]+)",
                line,
            )
        )
        if not matches:
            if re.fullmatch(r"(?:[- ]*)[A-Za-z0-9_.-]+\s*:\s*", line):
                parsed_any = True
                continue
            raise ReleaseBackupError("configuration YAML could not be parsed")
        parsed_any = True
        for match in matches:
            if _is_sensitive_config_key(match.group("key")):
                value = _strip_assignment_value(match.group("value"))
                if not _is_placeholder(value):
                    _raise_sensitive_config(match.group("key"))
    if text.strip() and not parsed_any:
        raise ReleaseBackupError("configuration YAML could not be parsed")


def _scan_yaml_config(text: str, path: Path) -> None:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        _scan_yaml_fallback(text)
        return

    class _UniqueKeyLoader(yaml.SafeLoader):
        pass

    def _construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ReleaseBackupError(f"configuration YAML contains duplicate key: {key}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    _UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_mapping,
    )
    try:
        payload = yaml.load(text, Loader=_UniqueKeyLoader)
    except ReleaseBackupError:
        raise
    except yaml.YAMLError as exc:
        raise ReleaseBackupError(f"configuration YAML is invalid: {path.name}") from exc
    _scan_structured_config(payload)


def _scan_config_text(path: Path, text: str) -> None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(text, object_pairs_hook=_json_object_no_duplicates)
        except json.JSONDecodeError as exc:
            raise ReleaseBackupError(f"configuration JSON is invalid: {path.name}") from exc
        _scan_structured_config(payload)
        return
    if suffix in {".ini", ".cfg"}:
        _scan_ini_config(text, path)
        return
    if suffix in {".yaml", ".yml"}:
        _scan_yaml_config(text, path)
        return
    if suffix in {".env", ".conf", ".ps1"}:
        _scan_line_config(text, suffix=suffix)
        return
    raise ReleaseBackupError(
        f"configuration format is not allowlisted; use JSON, YAML, INI, ENV, CONF, or PS1: {path.name}"
    )


def validate_nonsecret_config(path: Path) -> None:
    """Reject secret-bearing configuration before it enters a backup.

    JSON is scanned structurally; line-oriented formats support quoted and
    unquoted assignments. Unknown formats are rejected by default so a new
    configuration syntax cannot silently bypass the allowlist.
    """

    if SECRET_CONFIG_NAME_RE.search(path.name):
        raise ReleaseBackupError(f"secret-like configuration filename is not allowed: {path.name}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReleaseBackupError(f"configuration must be UTF-8 text: {path.name}") from exc
    _scan_config_text(path, text)


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
    transaction_stage: Path | None = None
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

        transaction_stage = output.parent / f".{output.name}.pra-{uuid4().hex}.stage"
        shutil.copy2(staged_database, transaction_stage)
        _fsync_file(transaction_stage)
        _apply_file_transaction(
            target_runtime=output,
            staged_files=[
                {
                    "staging": transaction_stage,
                    "target": output,
                    "role": "runtime_database",
                    "sha256": sha256_file(transaction_stage),
                }
            ],
            expected_runtime_snapshot=target_snapshot,
            force=force,
            operation="migrate",
            transaction_id=uuid4().hex,
        )
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
        if transaction_stage is not None and _path_exists(transaction_stage):
            _remove_path(transaction_stage)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _file_records_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ReleaseBackupError("backup manifest has no file records")
    return records


def _release_artifact_record(
    manifest: dict[str, Any], records: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    artifact_records = [record for record in records if record.get("role") == "release_artifact"]
    if len(artifact_records) != 1:
        raise ReleaseBackupError("backup manifest must contain exactly one release wheel artifact")
    release_manifest = manifest.get("release_manifest")
    wheel = release_manifest.get("wheel") if isinstance(release_manifest, dict) else None
    if not isinstance(wheel, dict):
        raise ReleaseBackupError("release manifest has no wheel metadata")
    record = artifact_records[0]
    expected_path = f"artifacts/{wheel.get('file_name')}"
    if record.get("path") != expected_path:
        raise ReleaseBackupError("release wheel artifact path does not match release manifest")
    try:
        record_size = int(record.get("size_bytes", -1))
        wheel_size = int(wheel.get("size_bytes", -1))
    except (TypeError, ValueError) as exc:
        raise ReleaseBackupError("release wheel artifact metadata is invalid") from exc
    if record.get("sha256") != wheel.get("sha256") or record_size != wheel_size:
        raise ReleaseBackupError("release wheel artifact does not match release manifest")
    return record


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
    artifact_record = _release_artifact_record(manifest, records)
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
    artifact_path = _safe_backup_path(root, str(artifact_record["path"]))
    if not artifact_path.is_file() or artifact_path.suffix.lower() != ".whl":
        raise ReleaseBackupError("backup release artifact is not a wheel file")

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
    wheel_source = _require_file(wheel_path, "wheel")
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
            wheel_path=wheel_source,
            config_names=config_names,
        )
        wheel_name = str(release_manifest["wheel"]["file_name"])
        files.append(
            _copy_file_record(
                wheel_source,
                temporary_dir / "artifacts" / wheel_name,
                role="release_artifact",
                relative_path=f"artifacts/{wheel_name}",
            )
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


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        pass


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif _path_exists(path):
        path.unlink()


def _assert_sqlite_exclusive(path: Path) -> None:
    """Prove that the runtime DB can acquire an exclusive write lock."""

    try:
        connection = sqlite3.connect(str(path), timeout=1)
        try:
            connection.execute("PRAGMA busy_timeout = 1000")
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("ROLLBACK")
        finally:
            connection.close()
    except sqlite3.OperationalError as exc:
        raise ReleaseBackupError(
            "runtime database is locked; stop PRA services and close database consumers before restore"
        ) from exc
    except sqlite3.Error as exc:
        raise ReleaseBackupError("runtime database cannot be opened exclusively") from exc


def _assert_target_file_ready(path: Path, label: str) -> None:
    if path.is_dir():
        raise ReleaseBackupError(f"{label} is a directory, expected a file: {path}")
    if not _path_exists(path):
        return
    try:
        with path.open("r+b"):
            pass
    except PermissionError as exc:
        raise ReleaseBackupError(
            f"{label} is locked or read-only; close Excel and other consumers before restore: {path}"
        ) from exc
    except OSError as exc:
        raise ReleaseBackupError(f"{label} cannot be opened for replacement: {path}") from exc


def _transaction_log_path(parent: Path, transaction_id: str) -> Path:
    return parent / f".pra-{transaction_id}.transaction.json"


def _write_transaction(path: Path, transaction: dict[str, Any]) -> None:
    _atomic_write_json(path, transaction)


def _cleanup_transaction(transaction_path: Path, transaction: dict[str, Any]) -> None:
    paths: list[Path] = []
    for entry in transaction.get("entries", []):
        if not isinstance(entry, dict):
            continue
        for field in ("staging", "reserved"):
            value = entry.get(field)
            if value:
                paths.append(Path(str(value)))
    snapshot = transaction.get("pre_rollback_snapshot")
    if snapshot:
        paths.append(Path(str(snapshot)))
    for path in paths:
        try:
            _remove_path(path)
        except OSError:
            pass
    for value in transaction.get("reserve_dirs", []):
        reserve_dir = Path(str(value))
        try:
            if reserve_dir.is_dir() and not any(reserve_dir.iterdir()):
                reserve_dir.rmdir()
        except OSError:
            pass
    try:
        if transaction_path.exists():
            transaction_path.unlink()
    except OSError:
        pass


def _rollback_transaction(transaction_path: Path, transaction: dict[str, Any]) -> None:
    transaction["status"] = "rolling_back"
    _write_transaction(transaction_path, transaction)
    transaction_id = str(transaction.get("transaction_id", uuid4().hex))
    for entry in reversed(transaction.get("entries", [])):
        if not isinstance(entry, dict):
            continue
        target = Path(str(entry["target"]))
        reserved_value = entry.get("reserved")
        reserved = Path(str(reserved_value)) if reserved_value else None
        original_exists = bool(entry.get("original_exists"))
        try:
            if reserved is not None and _path_exists(reserved):
                displaced = target.parent / f".{target.name}.pra-{transaction_id}.recovery"
                if _path_exists(displaced):
                    _remove_path(displaced)
                if _path_exists(target):
                    os.replace(target, displaced)
                os.replace(reserved, target)
                if _path_exists(displaced):
                    _remove_path(displaced)
            elif not original_exists and _path_exists(target):
                _remove_path(target)
            elif original_exists and not _path_exists(target):
                raise ReleaseBackupError(f"rollback could not find original target: {target}")
            entry["state"] = "restored"
            _write_transaction(transaction_path, transaction)
        except (OSError, ReleaseBackupError) as exc:
            transaction["status"] = "rollback_failed"
            transaction["error"] = str(exc)
            _write_transaction(transaction_path, transaction)
            raise ReleaseBackupError(
                f"automatic restore rollback failed; transaction log retained: {transaction_path}"
            ) from exc
    transaction["status"] = "rolled_back"
    _write_transaction(transaction_path, transaction)
    _cleanup_transaction(transaction_path, transaction)


def _recover_pending_transactions(parent: Path) -> None:
    for transaction_path in sorted(parent.glob(TRANSACTION_FILE_GLOB)):
        transaction = _read_json(transaction_path)
        status = str(transaction.get("status", ""))
        if status == "committed":
            _cleanup_transaction(transaction_path, transaction)
            continue
        _rollback_transaction(transaction_path, transaction)


def _prepare_rollback_snapshot(
    *, target_runtime: Path, reserve_dir: Path | None
) -> tuple[dict[str, Any] | None, Path | None]:
    if not _path_exists(target_runtime):
        return None, None
    _assert_sqlite_exclusive(target_runtime)
    current_snapshot = _database_snapshot(target_runtime)
    if not current_snapshot["ok"]:
        raise ReleaseBackupError(
            "current runtime database failed health validation; rollback aborted before replacement"
        )
    if reserve_dir is None:
        raise ReleaseBackupError("rollback reserve directory is unavailable")
    snapshot_path = reserve_dir / "pre-rollback.sqlite3"
    _backup_sqlite(target_runtime, snapshot_path)
    snapshot = _database_snapshot(snapshot_path)
    if (
        not snapshot["ok"]
        or snapshot["logical_table_counts"] != current_snapshot["logical_table_counts"]
    ):
        raise ReleaseBackupError("pre-rollback snapshot failed validation")
    return current_snapshot, snapshot_path


def _preflight_targets(
    *,
    targets: Sequence[tuple[Path, Path, str]],
    force: bool,
) -> None:
    seen_targets: set[Path] = set()
    for source, target, role in targets:
        if not source.is_file():
            raise ReleaseBackupError(f"staged restore source does not exist: {source}")
        target = target.resolve()
        if target in seen_targets:
            raise ReleaseBackupError(f"restore target is listed more than once: {target}")
        seen_targets.add(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force:
            raise ReleaseBackupError(f"{role} target exists; pass --force to replace it: {target}")
        if target.exists():
            _assert_target_file_ready(target, f"{role} target")
        try:
            usage = shutil.disk_usage(target.parent)
        except OSError as exc:
            raise ReleaseBackupError(f"cannot inspect free space for restore target: {target.parent}") from exc
        required = max(1024 * 1024, source.stat().st_size)
        if usage.free < required:
            raise ReleaseBackupError(f"insufficient free space for restore target: {target}")


def _stage_source_file(source: Path, target: Path, transaction_id: str) -> Path:
    staged = target.parent / f".{target.name}.pra-{transaction_id}.stage"
    if _path_exists(staged):
        raise ReleaseBackupError(f"restore staging path already exists: {staged}")
    try:
        shutil.copy2(source, staged)
        _fsync_file(staged)
    except PermissionError as exc:
        raise ReleaseBackupError(
            f"cannot stage restore file; target directory may be locked: {target.parent}"
        ) from exc
    return staged


def _apply_file_transaction(
    *,
    target_runtime: Path,
    staged_files: Sequence[dict[str, Any]],
    expected_runtime_snapshot: dict[str, Any],
    force: bool,
    operation: str,
    transaction_id: str,
) -> dict[str, Any]:
    target_runtime = target_runtime.resolve()
    target_runtime.parent.mkdir(parents=True, exist_ok=True)
    _recover_pending_transactions(target_runtime.parent)

    targets = [
        (Path(str(item["staging"])), Path(str(item["target"])).resolve(), str(item["role"]))
        for item in staged_files
    ]
    sidecar_targets: list[tuple[Path, Path, str]] = []
    for sidecar in (Path(str(target_runtime) + "-wal"), Path(str(target_runtime) + "-shm")):
        if sidecar.exists() and not target_runtime.exists():
            raise ReleaseBackupError(f"runtime sidecar exists without a database: {sidecar}")
        if sidecar.exists():
            sidecar_targets.append((sidecar, sidecar, "runtime_sidecar"))
    all_targets = targets + sidecar_targets
    _preflight_targets(targets=all_targets, force=force)

    reserve_dirs: dict[Path, Path] = {}
    pre_snapshot: Path | None = None
    try:
        for _, target, _ in all_targets:
            reserve_dir = reserve_dirs.setdefault(
                target.parent,
                target.parent / f".pra-{transaction_id}-reserve",
            )
            reserve_dir.mkdir(parents=True, exist_ok=True)
        runtime_reserve_dir = reserve_dirs[target_runtime.parent]
        current_snapshot, pre_snapshot = _prepare_rollback_snapshot(
            target_runtime=target_runtime,
            reserve_dir=runtime_reserve_dir,
        )
    except Exception:
        for reserve_dir in reserve_dirs.values():
            shutil.rmtree(reserve_dir, ignore_errors=True)
        raise

    entries: list[dict[str, Any]] = []
    staged_by_target = {
        Path(str(item["target"])).resolve(): item
        for item in staged_files
    }
    for _, target, role in all_targets:
        item = staged_by_target.get(target)
        entries.append(
            {
                "target": str(target),
                "staging": str(item["staging"]) if item is not None else None,
                "reserved": str(reserve_dirs[target.parent] / target.name),
                "original_exists": _path_exists(target),
                "role": role,
                "state": "prepared",
            }
        )
    transaction = {
        "transaction_version": 1,
        "transaction_id": transaction_id,
        "operation": operation,
        "status": "prepared",
        "target_runtime": str(target_runtime),
        "pre_rollback_snapshot": str(pre_snapshot) if pre_snapshot is not None else None,
        "reserve_dirs": [str(path) for path in reserve_dirs.values()],
        "entries": entries,
        "created_at_utc": _utc_now(),
    }
    transaction_path = _transaction_log_path(target_runtime.parent, transaction_id)
    _write_transaction(transaction_path, transaction)
    try:
        transaction["status"] = "committing"
        _write_transaction(transaction_path, transaction)
        for entry in entries:
            target = Path(entry["target"])
            reserved = Path(entry["reserved"])
            if _path_exists(target):
                os.replace(target, reserved)
                entry["state"] = "reserved"
                _write_transaction(transaction_path, transaction)
        for entry in entries:
            staging = entry.get("staging")
            if not staging:
                continue
            os.replace(Path(str(staging)), Path(entry["target"]))
            entry["state"] = "replaced"
            _write_transaction(transaction_path, transaction)

        restored_snapshot = _database_snapshot(target_runtime)
        if (
            not restored_snapshot["ok"]
            or restored_snapshot["logical_table_counts"]
            != expected_runtime_snapshot["logical_table_counts"]
        ):
            raise ReleaseBackupError("restored runtime database failed final health validation")
        for item in staged_files:
            target = Path(str(item["target"])).resolve()
            if sha256_file(target) != str(item["sha256"]):
                raise ReleaseBackupError(f"restored file hash mismatch: {target}")

        transaction["status"] = "committed"
        _write_transaction(transaction_path, transaction)
        restored_files = [str(Path(str(item["target"])).resolve()) for item in staged_files]
        _cleanup_transaction(transaction_path, transaction)
        _fsync_directory(target_runtime.parent)
        return {
            "runtime_snapshot": restored_snapshot,
            "current_snapshot": current_snapshot,
            "restored_files": restored_files,
        }
    except Exception as exc:
        try:
            _rollback_transaction(transaction_path, transaction)
        except ReleaseBackupError as rollback_exc:
            raise rollback_exc from exc
        raise


def restore_backup(
    *,
    backup_path: Path,
    runtime_db: Path,
    input_dir: Path | None = None,
    config_dir: Path | None = None,
    artifact_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Restore a verified backup through a recoverable multi-file transaction."""

    backup_root = _require_directory(backup_path, "backup")
    manifest = verify_backup(backup_root)
    target_runtime = runtime_db.expanduser().resolve()
    _assert_not_inside(target_runtime, backup_root, "runtime database target")
    target_inputs = input_dir.expanduser().resolve() if input_dir is not None else None
    target_config = config_dir.expanduser().resolve() if config_dir is not None else None
    target_artifacts = artifact_dir.expanduser().resolve() if artifact_dir is not None else None
    for target, label in (
        (target_inputs, "input target"),
        (target_config, "config target"),
        (target_artifacts, "artifact target"),
    ):
        if target is not None:
            _assert_not_inside(target, backup_root, label)

    records = _file_records_from_manifest(manifest)
    runtime_records = [record for record in records if record.get("role") == "runtime_database"]
    if len(runtime_records) != 1:
        raise ReleaseBackupError("backup manifest must contain exactly one runtime database")
    input_records = [record for record in records if record.get("role") == "business_input"]
    config_records = [record for record in records if record.get("role") == "operations_config"]
    artifact_records = [record for record in records if record.get("role") == "release_artifact"]
    if target_artifacts is not None and not artifact_records:
        raise ReleaseBackupError("backup has no release artifact to restore")
    targets: list[tuple[Path, Path, str]] = [
        (
            _safe_backup_path(backup_root, str(runtime_records[0]["path"])),
            target_runtime,
            "runtime database",
        )
    ]
    if target_inputs is not None:
        for record in input_records:
            source = _safe_backup_path(backup_root, str(record["path"]))
            target = target_inputs / Path(str(record["path"])).name
            targets.append((source, target, "input"))
    if target_config is not None:
        for record in config_records:
            source = _safe_backup_path(backup_root, str(record["path"]))
            target = target_config / Path(str(record["path"])).name
            targets.append((source, target, "config"))
    if target_artifacts is not None:
        for record in artifact_records:
            source = _safe_backup_path(backup_root, str(record["path"]))
            target = target_artifacts / Path(str(record["path"])).name
            targets.append((source, target, "release artifact"))

    transaction_id = uuid4().hex
    staged_files: list[dict[str, Any]] = []
    try:
        _preflight_targets(targets=targets, force=force)
        for source, target, role in targets:
            staged = _stage_source_file(source, target, transaction_id)
            staged_files.append(
                {
                    "staging": staged,
                    "target": target,
                    "role": role,
                    "sha256": sha256_file(source),
                }
            )
        staged_runtime = Path(str(staged_files[0]["staging"]))
        staged_snapshot = _database_snapshot(staged_runtime)
        expected_snapshot = manifest["database_validation"]["backup_snapshot"]
        if (
            not staged_snapshot["ok"]
            or staged_snapshot["logical_table_counts"] != expected_snapshot["logical_table_counts"]
        ):
            raise ReleaseBackupError("staged restore database failed validation")
        result = _apply_file_transaction(
            target_runtime=target_runtime,
            staged_files=staged_files,
            expected_runtime_snapshot=expected_snapshot,
            force=force,
            operation="restore" if not force else "rollback",
            transaction_id=transaction_id,
        )
        return {
            "backup_id": manifest["backup_id"],
            "runtime_db": str(target_runtime),
            "restored_files": result["restored_files"],
            "force": force,
            "database_validation": result["runtime_snapshot"],
        }
    except PermissionError as exc:
        raise ReleaseBackupError(
            "restore target is locked; stop PRA services and close Excel before retrying"
        ) from exc
    except Exception:
        transaction_path = _transaction_log_path(target_runtime.parent, transaction_id)
        if not transaction_path.exists():
            for item in staged_files:
                staged = Path(str(item["staging"]))
                if _path_exists(staged):
                    _remove_path(staged)
        raise


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
        restore_parser.add_argument("--artifact-dir", type=Path)
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
                artifact_dir=args.artifact_dir,
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
                artifact_dir=args.artifact_dir,
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
