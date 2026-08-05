from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.repositories.emergency_offline_policy_repository import (
    EmergencyOfflinePolicyRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


def _repository(
    tmp_path: Path,
) -> tuple[SQLiteRuntimeRepository, EmergencyOfflinePolicyRepository]:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    return runtime, EmergencyOfflinePolicyRepository(runtime)


def test_draft_is_not_active_and_exact_creation_replays(tmp_path: Path) -> None:
    _, policies = _repository(tmp_path)
    created_at = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)

    first = policies.create_draft(
        policy_version="POLICY-1",
        platform_name="platform",
        created_at=created_at,
    )
    replay = policies.create_draft(
        policy_version="POLICY-1",
        platform_name="platform",
        created_at=created_at,
    )

    assert first == replay
    assert first.emergency_ratio == Decimal("0.80")
    assert not first.is_approved
    assert not first.is_active
    assert policies.get_active("platform") is None
    assert len(policies.canonical_sha256(first)) == 64


def test_approved_policy_is_active_and_immutable(tmp_path: Path) -> None:
    runtime, policies = _repository(tmp_path)
    created_at = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)
    approved_at = created_at + timedelta(minutes=1)
    policies.create_draft(
        policy_version="POLICY-1",
        platform_name="platform",
        created_at=created_at,
    )

    approved = policies.approve(
        "POLICY-1",
        approved_by="admin",
        approved_at=approved_at,
    )
    replay = policies.approve(
        "POLICY-1",
        approved_by="admin",
        approved_at=approved_at,
    )

    assert approved == replay == policies.get_active("platform")
    assert approved.is_active
    with pytest.raises(ValueError, match="immutable"):
        policies.approve(
            "POLICY-1",
            approved_by="another-admin",
            approved_at=approved_at + timedelta(seconds=1),
        )
    with closing(runtime.connect_write()) as connection, connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                UPDATE emergency_offline_policies
                SET emergency_ratio = '0.80', platform_name = 'other'
                WHERE policy_version = 'POLICY-1'
                """
            )


def test_replacement_retires_current_and_approves_successor_atomically(
    tmp_path: Path,
) -> None:
    _, policies = _repository(tmp_path)
    created_at = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)
    first_approved_at = created_at + timedelta(minutes=1)
    replaced_at = created_at + timedelta(minutes=2)
    policies.create_draft(
        policy_version="POLICY-1",
        platform_name="platform",
        created_at=created_at,
    )
    policies.approve(
        "POLICY-1",
        approved_by="admin",
        approved_at=first_approved_at,
    )
    policies.create_draft(
        policy_version="POLICY-2",
        platform_name="platform",
        created_at=created_at + timedelta(seconds=1),
    )

    successor = policies.replace(
        current_policy_version="POLICY-1",
        successor_policy_version="POLICY-2",
        approved_by="admin",
        approved_at=replaced_at,
    )
    replay = policies.replace(
        current_policy_version="POLICY-1",
        successor_policy_version="POLICY-2",
        approved_by="admin",
        approved_at=replaced_at,
    )

    assert successor == replay == policies.get_active("platform")
    assert policies.get("POLICY-1").retired_at == replaced_at  # type: ignore[union-attr]
    assert policies.get("POLICY-2").is_active  # type: ignore[union-attr]


def test_approval_requires_replacement_when_platform_has_active_policy(
    tmp_path: Path,
) -> None:
    _, policies = _repository(tmp_path)
    created_at = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)
    for version in ("POLICY-1", "POLICY-2"):
        policies.create_draft(
            policy_version=version,
            platform_name="platform",
            created_at=created_at,
        )
    policies.approve(
        "POLICY-1",
        approved_by="admin",
        approved_at=created_at + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="use replace"):
        policies.approve(
            "POLICY-2",
            approved_by="admin",
            approved_at=created_at + timedelta(minutes=2),
        )


def test_retire_is_one_way_and_draft_cannot_be_retired(tmp_path: Path) -> None:
    _, policies = _repository(tmp_path)
    created_at = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)
    retired_at = created_at + timedelta(minutes=2)
    policies.create_draft(
        policy_version="POLICY-DRAFT",
        platform_name="platform",
        created_at=created_at,
    )
    with pytest.raises(ValueError, match="unapproved"):
        policies.retire("POLICY-DRAFT", retired_at=retired_at)

    policies.approve(
        "POLICY-DRAFT",
        approved_by="admin",
        approved_at=created_at + timedelta(minutes=1),
    )
    retired = policies.retire("POLICY-DRAFT", retired_at=retired_at)
    replay = policies.retire("POLICY-DRAFT", retired_at=retired_at)

    assert retired == replay
    assert not retired.is_active
    assert policies.get_active("platform") is None
