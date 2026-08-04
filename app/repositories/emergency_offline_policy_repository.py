from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal

from app.models import EmergencyOfflinePolicy
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository

EMERGENCY_RATIO = Decimal("0.80")


class EmergencyOfflinePolicyRepository:
    """Persist the single-purpose v16 policy without a general rule engine."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository

    def create_draft(
        self,
        *,
        policy_version: str,
        platform_name: str,
        created_at: datetime,
    ) -> EmergencyOfflinePolicy:
        version = _require_text(policy_version, "policy_version")
        platform = _require_text(platform_name, "platform_name")
        created_text = _datetime_text(created_at, "created_at")
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = _select_policy(connection, version)
            if existing is not None:
                policy = _row_to_policy(existing)
                expected = EmergencyOfflinePolicy(
                    policy_version=version,
                    platform_name=platform,
                    emergency_ratio=EMERGENCY_RATIO,
                    approved_by=None,
                    approved_at=None,
                    created_at=created_at,
                    retired_at=None,
                )
                if policy != expected:
                    raise ValueError(
                        "policy_version already exists with different content"
                    )
                connection.rollback()
                return policy
            connection.execute(
                """
                INSERT INTO emergency_offline_policies(
                    policy_version, platform_name, emergency_ratio,
                    approved_by, approved_at, created_at, retired_at
                ) VALUES (?, ?, '0.80', NULL, NULL, ?, NULL)
                """,
                (version, platform, created_text),
            )
            policy = _row_to_policy(_select_required_policy(connection, version))
            connection.commit()
            return policy
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def approve(
        self,
        policy_version: str,
        *,
        approved_by: str,
        approved_at: datetime,
    ) -> EmergencyOfflinePolicy:
        version = _require_text(policy_version, "policy_version")
        actor = _require_text(approved_by, "approved_by")
        approved_text = _datetime_text(approved_at, "approved_at")
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = _row_to_policy(_select_required_policy(connection, version))
            if current.is_approved:
                if (
                    current.approved_by == actor
                    and current.approved_at == approved_at
                    and current.retired_at is None
                ):
                    connection.rollback()
                    return current
                raise ValueError("approved policy versions are immutable")
            active = connection.execute(
                """
                SELECT policy_version FROM emergency_offline_policies
                WHERE platform_name = ?
                  AND approved_at IS NOT NULL
                  AND retired_at IS NULL
                """,
                (current.platform_name,),
            ).fetchone()
            if active is not None:
                raise ValueError(
                    "platform already has an active policy; use replace()"
                )
            connection.execute(
                """
                UPDATE emergency_offline_policies
                SET approved_by = ?, approved_at = ?
                WHERE policy_version = ?
                """,
                (actor, approved_text, version),
            )
            approved = _row_to_policy(_select_required_policy(connection, version))
            connection.commit()
            return approved
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace(
        self,
        *,
        current_policy_version: str,
        successor_policy_version: str,
        approved_by: str,
        approved_at: datetime,
    ) -> EmergencyOfflinePolicy:
        current_version = _require_text(
            current_policy_version,
            "current_policy_version",
        )
        successor_version = _require_text(
            successor_policy_version,
            "successor_policy_version",
        )
        if successor_version == current_version:
            raise ValueError("successor policy_version must be different")
        actor = _require_text(approved_by, "approved_by")
        approved_text = _datetime_text(approved_at, "approved_at")
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = _row_to_policy(
                _select_required_policy(connection, current_version)
            )
            successor = _row_to_policy(
                _select_required_policy(connection, successor_version)
            )
            if (
                current.retired_at == approved_at
                and successor.approved_by == actor
                and successor.approved_at == approved_at
                and successor.retired_at is None
            ):
                connection.rollback()
                return successor
            if not current.is_active:
                raise ValueError("current policy is not the active policy")
            if successor.is_approved or successor.retired_at is not None:
                raise ValueError("successor policy must be an unapproved draft")
            if successor.platform_name != current.platform_name:
                raise ValueError("successor policy must target the same platform")
            connection.execute(
                """
                UPDATE emergency_offline_policies
                SET retired_at = ?
                WHERE policy_version = ?
                """,
                (approved_text, current_version),
            )
            connection.execute(
                """
                UPDATE emergency_offline_policies
                SET approved_by = ?, approved_at = ?
                WHERE policy_version = ?
                """,
                (actor, approved_text, successor_version),
            )
            replacement = _row_to_policy(
                _select_required_policy(connection, successor_version)
            )
            connection.commit()
            return replacement
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def retire(
        self,
        policy_version: str,
        *,
        retired_at: datetime,
    ) -> EmergencyOfflinePolicy:
        version = _require_text(policy_version, "policy_version")
        retired_text = _datetime_text(retired_at, "retired_at")
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = _row_to_policy(_select_required_policy(connection, version))
            if current.retired_at is not None:
                if current.retired_at == retired_at:
                    connection.rollback()
                    return current
                raise ValueError("retired policy versions are immutable")
            if not current.is_approved:
                raise ValueError("unapproved policy cannot be retired")
            connection.execute(
                """
                UPDATE emergency_offline_policies
                SET retired_at = ?
                WHERE policy_version = ?
                """,
                (retired_text, version),
            )
            retired = _row_to_policy(_select_required_policy(connection, version))
            connection.commit()
            return retired
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, policy_version: str) -> EmergencyOfflinePolicy | None:
        version = _require_text(policy_version, "policy_version")
        with closing(self.runtime_repository.connect_read()) as connection:
            row = _select_policy(connection, version)
        return _row_to_policy(row) if row is not None else None

    def get_active(self, platform_name: str) -> EmergencyOfflinePolicy | None:
        platform = _require_text(platform_name, "platform_name")
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT * FROM emergency_offline_policies
                WHERE platform_name = ?
                  AND approved_at IS NOT NULL
                  AND retired_at IS NULL
                """,
                (platform,),
            ).fetchone()
        return _row_to_policy(row) if row is not None else None

    @staticmethod
    def canonical_sha256(policy: EmergencyOfflinePolicy) -> str:
        payload = {
            "approved_at": _optional_datetime_text(policy.approved_at),
            "approved_by": policy.approved_by,
            "created_at": _datetime_text(policy.created_at, "created_at"),
            "emergency_ratio": "0.80",
            "platform_name": policy.platform_name,
            "policy_version": policy.policy_version,
            "retired_at": _optional_datetime_text(policy.retired_at),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _select_policy(
    connection: sqlite3.Connection,
    policy_version: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM emergency_offline_policies WHERE policy_version = ?",
        (policy_version,),
    ).fetchone()


def _select_required_policy(
    connection: sqlite3.Connection,
    policy_version: str,
) -> sqlite3.Row:
    row = _select_policy(connection, policy_version)
    if row is None:
        raise ValueError(f"emergency policy not found: {policy_version}")
    return row


def _row_to_policy(row: sqlite3.Row) -> EmergencyOfflinePolicy:
    return EmergencyOfflinePolicy(
        policy_version=str(row["policy_version"]),
        platform_name=str(row["platform_name"]),
        emergency_ratio=Decimal(str(row["emergency_ratio"])),
        approved_by=(
            str(row["approved_by"]) if row["approved_by"] is not None else None
        ),
        approved_at=_optional_datetime(row["approved_at"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        retired_at=_optional_datetime(row["retired_at"]),
    )


def _require_text(value: str, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _datetime_text(value: datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.isoformat()


def _optional_datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))
