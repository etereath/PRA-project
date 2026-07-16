"""Shared SQLite connection, storage, and bounded-lock-retry primitives."""

from __future__ import annotations

import ctypes
import ntpath
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TypeVar


T = TypeVar("T")

DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_RETRY_MAX_ATTEMPTS = 3
DEFAULT_RETRY_MAX_ELAPSED_MS = 2_000
DEFAULT_WORKER_RETRY_MAX_ELAPSED_MS = 10_000
DEFAULT_RETRY_BASE_DELAY_MS = 25
MIN_BUSY_TIMEOUT_MS = 100
MAX_BUSY_TIMEOUT_MS = 30_000
MIN_RETRY_MAX_ATTEMPTS = 0
MAX_RETRY_MAX_ATTEMPTS = 8
MIN_RETRY_BASE_DELAY_MS = 1
MAX_RETRY_BASE_DELAY_MS = 500

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

SQLITE_CONCURRENCY_ERROR_CODES = frozenset(
    getattr(sqlite3, name)
    for name in (
        "SQLITE_BUSY",
        "SQLITE_BUSY_RECOVERY",
        "SQLITE_BUSY_SNAPSHOT",
        "SQLITE_LOCKED",
        "SQLITE_LOCKED_SHAREDCACHE",
        "SQLITE_LOCKED_VTAB",
    )
    if hasattr(sqlite3, name)
)


class SQLiteConnectionError(RuntimeError):
    """Base class for stable, non-sensitive connection failures."""


class SQLiteConfigurationError(SQLiteConnectionError, ValueError):
    """Raised when SQLite runtime configuration is outside its safe bounds."""


class SQLiteStorageLocationError(SQLiteConnectionError, ValueError):
    """Raised when a runtime database is not on an accepted local path."""


class SQLiteDatabaseNotFoundError(SQLiteConnectionError, FileNotFoundError):
    """Raised when a read-only connection targets a missing database."""


class SQLiteInitializationError(SQLiteConnectionError):
    """Raised when explicit runtime database initialization cannot establish WAL."""


@dataclass(frozen=True, slots=True)
class SQLiteOperationalHealth:
    """Non-mutating health facts for a runtime SQLite database."""

    ok: bool
    database_exists: bool
    local_storage: bool
    journal_mode: str | None
    synchronous: str | None
    foreign_keys: int | None
    busy_timeout_ms: int | None
    configured_busy_timeout_ms: int
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.ok:
            return (
                "SQLite operational health is healthy "
                f"(journal_mode={self.journal_mode}, synchronous={self.synchronous}, "
                f"foreign_keys={self.foreign_keys}, busy_timeout_ms={self.busy_timeout_ms})"
            )
        parts = []
        if not self.database_exists:
            parts.append("database file does not exist")
        if not self.local_storage:
            parts.append("database is not on an accepted local filesystem")
        if self.journal_mode != "wal":
            parts.append(f"journal_mode expected wal, actual {self.journal_mode or 'unknown'}")
        if self.synchronous != "NORMAL":
            parts.append(f"synchronous expected NORMAL, actual {self.synchronous or 'unknown'}")
        if self.foreign_keys != 1:
            parts.append("foreign_keys expected 1")
        if self.busy_timeout_ms != self.configured_busy_timeout_ms:
            parts.append(
                f"busy_timeout expected {self.configured_busy_timeout_ms}, actual {self.busy_timeout_ms}"
            )
        if self.error:
            parts.append(self.error)
        return "; ".join(parts) or "SQLite operational health is unhealthy"

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "database_exists": self.database_exists,
            "local_storage": self.local_storage,
            "journal_mode": self.journal_mode,
            "synchronous": self.synchronous,
            "foreign_keys": self.foreign_keys,
            "busy_timeout_ms": self.busy_timeout_ms,
            "configured_busy_timeout_ms": self.configured_busy_timeout_ms,
            "error": self.error,
            "summary": self.summary,
        }


class SQLiteConcurrencyError(SQLiteConnectionError):
    """Stable error returned after bounded SQLite lock retries are exhausted."""

    def __init__(
        self,
        operation_name: str,
        *,
        sqlite_errorcode: int | None,
        sqlite_errorname: str | None,
        original_error: sqlite3.OperationalError,
    ) -> None:
        self.operation_name = operation_name
        self.sqlite_errorcode = sqlite_errorcode
        self.sqlite_errorname = sqlite_errorname
        self.error_code = sqlite_errorcode
        self.error_name = sqlite_errorname
        self.original_error = original_error
        code_label = sqlite_errorname or (
            f"code {sqlite_errorcode}" if sqlite_errorcode is not None else "lock contention"
        )
        super().__init__(f"{operation_name} failed after bounded SQLite lock retries ({code_label})")


@dataclass(frozen=True, slots=True)
class SQLiteConnectionConfig:
    """Validated connection and retry settings for one SQLite purpose."""

    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
    synchronous: str = "NORMAL"
    uri: bool = False
    read_only: bool = False
    purpose: str = "runtime"
    retry_max_attempts: int = DEFAULT_RETRY_MAX_ATTEMPTS
    retry_max_elapsed_ms: int | None = None
    retry_base_delay_ms: int = DEFAULT_RETRY_BASE_DELAY_MS

    def __post_init__(self) -> None:
        if not MIN_BUSY_TIMEOUT_MS <= self.busy_timeout_ms <= MAX_BUSY_TIMEOUT_MS:
            raise SQLiteConfigurationError(
                f"busy_timeout_ms must be between {MIN_BUSY_TIMEOUT_MS} and {MAX_BUSY_TIMEOUT_MS}"
            )
        if not MIN_RETRY_MAX_ATTEMPTS <= self.retry_max_attempts <= MAX_RETRY_MAX_ATTEMPTS:
            raise SQLiteConfigurationError(
                f"retry_max_attempts must be between {MIN_RETRY_MAX_ATTEMPTS} and {MAX_RETRY_MAX_ATTEMPTS}"
            )
        if not MIN_RETRY_BASE_DELAY_MS <= self.retry_base_delay_ms <= MAX_RETRY_BASE_DELAY_MS:
            raise SQLiteConfigurationError(
                f"retry_base_delay_ms must be between {MIN_RETRY_BASE_DELAY_MS} and {MAX_RETRY_BASE_DELAY_MS}"
            )
        synchronous = str(self.synchronous).strip().upper()
        if synchronous != "NORMAL":
            raise SQLiteConfigurationError("synchronous is fixed to NORMAL for runtime SQLite")
        object.__setattr__(self, "synchronous", synchronous)
        if self.retry_max_elapsed_ms is None:
            purpose = self.purpose.strip().lower()
            default_elapsed = (
                DEFAULT_WORKER_RETRY_MAX_ELAPSED_MS
                if purpose in {"worker", "background", "shadowbot"}
                else DEFAULT_RETRY_MAX_ELAPSED_MS
            )
            object.__setattr__(self, "retry_max_elapsed_ms", default_elapsed)
        elif not 0 <= self.retry_max_elapsed_ms <= DEFAULT_WORKER_RETRY_MAX_ELAPSED_MS:
            raise SQLiteConfigurationError(
                f"retry_max_elapsed_ms must be between 0 and {DEFAULT_WORKER_RETRY_MAX_ELAPSED_MS}"
            )

    @classmethod
    def from_environment(
        cls,
        *,
        purpose: str = "runtime",
        environment: Mapping[str, str] | None = None,
        read_only: bool = False,
    ) -> "SQLiteConnectionConfig":
        values = os.environ if environment is None else environment
        worker_purpose = purpose.strip().lower() in {"worker", "background", "shadowbot"}
        elapsed_default = (
            DEFAULT_WORKER_RETRY_MAX_ELAPSED_MS
            if worker_purpose
            else DEFAULT_RETRY_MAX_ELAPSED_MS
        )
        return cls(
            busy_timeout_ms=_read_bounded_int(
                values, "PRA_SQLITE_BUSY_TIMEOUT_MS", DEFAULT_BUSY_TIMEOUT_MS,
                MIN_BUSY_TIMEOUT_MS, MAX_BUSY_TIMEOUT_MS,
            ),
            synchronous=values.get("PRA_SQLITE_SYNCHRONOUS", "NORMAL"),
            uri=False,
            read_only=read_only,
            purpose=purpose,
            retry_max_attempts=_read_bounded_int(
                values, "PRA_SQLITE_RETRY_MAX_ATTEMPTS", DEFAULT_RETRY_MAX_ATTEMPTS,
                MIN_RETRY_MAX_ATTEMPTS, MAX_RETRY_MAX_ATTEMPTS,
            ),
            retry_max_elapsed_ms=_read_bounded_int(
                values, "PRA_SQLITE_RETRY_MAX_ELAPSED_MS", elapsed_default,
                0, DEFAULT_WORKER_RETRY_MAX_ELAPSED_MS,
            ),
            retry_base_delay_ms=_read_bounded_int(
                values, "PRA_SQLITE_RETRY_BASE_DELAY_MS", DEFAULT_RETRY_BASE_DELAY_MS,
                MIN_RETRY_BASE_DELAY_MS, MAX_RETRY_BASE_DELAY_MS,
            ),
        )


def _read_bounded_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise SQLiteConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise SQLiteConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


class SQLiteConnectionFactory:
    """Create configured runtime connections without implicit schema mutation."""

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        config: SQLiteConnectionConfig | None = None,
    ) -> None:
        self.config = config or SQLiteConnectionConfig.from_environment()
        raw_path = os.fspath(db_path)
        self._is_memory = raw_path == ":memory:"
        self._is_uri = raw_path.startswith("file:")
        lowered_path = raw_path.lower()
        self._is_memory_uri = self._is_uri and (
            "mode=memory" in lowered_path or lowered_path.startswith("file::memory:")
        )
        if self._is_memory or self._is_uri:
            self.db_path: Path | str = raw_path
        else:
            self.db_path = Path(raw_path).expanduser()

    @property
    def is_memory_database(self) -> bool:
        return self._is_memory or self._is_memory_uri

    def connect(self) -> sqlite3.Connection:
        """Connect according to the configured read-only mode."""

        return self.connect_read() if self.config.read_only else self.connect_write()

    def connect_read(self) -> sqlite3.Connection:
        """Open an existing database without creating files or changing journal mode."""

        if self._is_memory:
            return self._open_connection(":memory:", uri=False)
        if self._is_uri:
            self._validate_uri_target()
            return self._open_connection(str(self.db_path), uri=True)
        assert isinstance(self.db_path, Path)
        self.validate_storage_location()
        if not self.db_path.exists():
            raise SQLiteDatabaseNotFoundError("SQLite database file does not exist")
        return self._open_connection(self.read_only_uri(self.db_path), uri=True)

    def connect_write(self) -> sqlite3.Connection:
        """Open a writable database without schema or journal-mode side effects."""

        if self._is_memory:
            return self._open_connection(":memory:", uri=False)
        if self._is_uri:
            self._validate_uri_target()
            if self.config.read_only:
                raise SQLiteConfigurationError("read-only SQLite configuration cannot open a writable connection")
            return self._open_connection(str(self.db_path), uri=True)
        assert isinstance(self.db_path, Path)
        self.validate_storage_location()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return self._open_connection(str(self.db_path), uri=False)

    def initialize_database(
        self,
        schema_initializer: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        """Explicitly establish WAL and optionally initialize the schema."""

        if self.config.read_only:
            raise SQLiteInitializationError("read-only SQLite configuration cannot initialize a database")
        connection = self.connect_write()
        try:
            if not self._is_memory and not self._is_memory_uri:
                row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                journal_mode = str(row[0]).lower() if row else ""
                if journal_mode != "wal":
                    raise SQLiteInitializationError("SQLite WAL initialization was not confirmed")
            connection.execute(f"PRAGMA synchronous = {self.config.synchronous}")
            if schema_initializer is not None:
                schema_initializer(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def validate_storage_location(self) -> None:
        """Fail closed for UNC/device paths and remote Windows drives."""

        if self._is_memory:
            return
        if self._is_uri:
            self._validate_uri_target()
            return
        assert isinstance(self.db_path, Path)
        _validate_local_storage_path(self.db_path)

    def _validate_uri_target(self) -> None:
        if not self.config.uri:
            raise SQLiteConfigurationError("SQLite URIs require uri=True")
        if not self._is_memory_uri or self.config.purpose.strip().lower() not in {"test", "testing"}:
            raise SQLiteStorageLocationError(
                "file: SQLite URIs are restricted to controlled mode=memory tests"
            )

    @staticmethod
    def read_only_uri(db_path: str | os.PathLike[str]) -> str:
        return f"{Path(db_path).resolve().as_uri()}?mode=ro"

    def _open_connection(self, target: str, *, uri: bool) -> sqlite3.Connection:
        connection = sqlite3.connect(target, uri=uri, timeout=0.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
            if not foreign_keys or int(foreign_keys[0]) != 1:
                raise SQLiteConnectionError("SQLite foreign_keys could not be enabled")
            connection.execute(f"PRAGMA busy_timeout = {self.config.busy_timeout_ms}")
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
            if not busy_timeout or int(busy_timeout[0]) != self.config.busy_timeout_ms:
                raise SQLiteConnectionError("SQLite busy_timeout could not be configured")
            connection.execute(f"PRAGMA synchronous = {self.config.synchronous}")
            return connection
        except Exception:
            connection.close()
            raise


def _validate_local_storage_path(db_path: Path) -> None:
    raw = os.fspath(db_path)
    normalized = raw.replace("/", "\\")
    if _is_non_local_path_syntax(normalized):
        raise SQLiteStorageLocationError("SQLite runtime database must use a local filesystem path")

    if os.name == "nt":
        for candidate in _existing_path_chain(Path(raw)):
            try:
                if not candidate.is_symlink():
                    continue
                link_target = os.readlink(candidate)
            except OSError:
                raise SQLiteStorageLocationError("Unable to verify SQLite reparse-point target") from None
            if _is_non_local_path_syntax(os.fspath(link_target).replace("/", "\\")):
                raise SQLiteStorageLocationError("SQLite runtime database must resolve to a local disk")

    resolved = Path(os.path.realpath(raw))
    if os.name == "nt":
        for candidate in (Path(raw), resolved):
            drive, _ = ntpath.splitdrive(os.fspath(candidate))
            if not drive:
                continue
            drive_type = _get_windows_drive_type(f"{drive}\\")
            if drive_type in {DRIVE_REMOTE, DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR, DRIVE_CDROM}:
                raise SQLiteStorageLocationError("SQLite runtime database must be on a local disk")


def _existing_path_chain(path: Path):
    current = Path(os.path.abspath(path))
    chain = []
    while True:
        if current.exists() or current.is_symlink():
            chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    return reversed(chain)


def _is_non_local_path_syntax(path: str) -> bool:
    return path.startswith("\\\\") or path.startswith("\\?\\") or path.startswith("\\.\\")


def _get_windows_drive_type(root: str) -> int | None:
    if os.name != "nt":
        return None
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except (AttributeError, OSError):
        raise SQLiteStorageLocationError("Unable to verify the SQLite drive type") from None


def is_sqlite_concurrency_error(error: BaseException) -> bool:
    """Classify only SQLite error codes/names, never localized exception text."""

    if not isinstance(error, sqlite3.OperationalError):
        return False
    error_code = getattr(error, "sqlite_errorcode", None)
    error_name = getattr(error, "sqlite_errorname", "")
    if error_code in SQLITE_CONCURRENCY_ERROR_CODES:
        return True
    return bool(
        isinstance(error_name, str)
        and (error_name.startswith("SQLITE_BUSY") or error_name.startswith("SQLITE_LOCKED"))
    )


def _execute_with_sqlite_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_RETRY_MAX_ATTEMPTS,
    max_elapsed_ms: int = DEFAULT_RETRY_MAX_ELAPSED_MS,
    base_delay_ms: int = DEFAULT_RETRY_BASE_DELAY_MS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float], float] | None = None,
    operation_name: str = "SQLite operation",
) -> T:
    """Retry only coded SQLite lock errors under both count and time budgets."""

    if not MIN_RETRY_MAX_ATTEMPTS <= max_attempts <= MAX_RETRY_MAX_ATTEMPTS:
        raise SQLiteConfigurationError("max_attempts is outside the allowed range")
    if not 0 <= max_elapsed_ms <= DEFAULT_WORKER_RETRY_MAX_ELAPSED_MS:
        raise SQLiteConfigurationError("max_elapsed_ms is outside the allowed range")
    if not MIN_RETRY_BASE_DELAY_MS <= base_delay_ms <= MAX_RETRY_BASE_DELAY_MS:
        raise SQLiteConfigurationError("base_delay_ms is outside the allowed range")

    started_at = monotonic()
    attempt = 0
    last_error: sqlite3.OperationalError | None = None
    while True:
        if last_error is not None and (monotonic() - started_at) * 1000 >= max_elapsed_ms:
            raise SQLiteConcurrencyError(
                operation_name,
                sqlite_errorcode=getattr(last_error, "sqlite_errorcode", None),
                sqlite_errorname=getattr(last_error, "sqlite_errorname", None),
                original_error=last_error,
            ) from last_error
        attempt += 1
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_sqlite_concurrency_error(exc):
                raise
            last_error = exc
            elapsed_ms = max(0.0, (monotonic() - started_at) * 1000)
            remaining_ms = max_elapsed_ms - elapsed_ms
            if attempt >= max(1, max_attempts) or remaining_ms <= 0:
                raise SQLiteConcurrencyError(
                    operation_name,
                    sqlite_errorcode=getattr(exc, "sqlite_errorcode", None),
                    sqlite_errorname=getattr(exc, "sqlite_errorname", None),
                    original_error=exc,
                ) from exc
            delay_seconds = min(
                base_delay_ms * (2 ** (attempt - 1)) / 1000,
                remaining_ms / 1000,
            )
            if jitter is not None:
                delay_seconds = min(
                    max(0.0, float(jitter(delay_seconds))),
                    remaining_ms / 1000,
                )
            if delay_seconds > 0:
                sleep(delay_seconds)
