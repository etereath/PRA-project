from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.operations_web.auth import (
    Capability,
    Principal,
    PrincipalCapabilityBackend,
)
from app.repositories.automation_repository import AutomationRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    DAILY_TASK_GENERATION,
    FULL_MARKET_SCAN,
    LISTING_STATUS_SCAN,
    ONLINE_PULSE,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    REVIEW_TIMEOUT_MAINTENANCE,
    ensure_default_automation_jobs,
)
from app.services.automation_configuration import (
    AutomationConfigurationApplicationService,
    AutomationConfigurationError,
)


NOW = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)
PLATFORM = "synthetic-platform"


@pytest.fixture()
def configured(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    automation = AutomationRepository(runtime)
    ensure_default_automation_jobs(automation, platform_name=PLATFORM, now=NOW)
    service = AutomationConfigurationApplicationService(
        automation,
        InventoryRepository(runtime),
        PrincipalCapabilityBackend(),
        clock=lambda: NOW,
    )
    principal = Principal(
        "operator",
        frozenset({Capability.MANAGE_BUSINESS}),
    )
    return runtime, automation, service, principal


def _job_by_type(automation: AutomationRepository, job_type: str):
    return next(job for job in automation.list_jobs() if job.job_type == job_type)


def test_interval_change_creates_deterministic_version_and_disables_old(configured):
    _, automation, service, principal = configured
    original = _job_by_type(automation, ONLINE_PULSE)

    changed = service.configure_job(
        principal,
        job_id=original.job_id,
        enabled=True,
        interval_minutes=15,
    )
    replay = service.configure_job(
        principal,
        job_id=original.job_id,
        enabled=True,
        interval_minutes=15,
    )

    assert changed.job_id == replay.job_id
    assert changed.job_id != original.job_id
    assert changed.schedule_expression == "15"
    assert changed.config["updated_by"] == "operator"
    assert automation.get_job(original.job_id).enabled is False
    enabled = [
        job
        for job in automation.list_jobs(enabled_only=True)
        if job.job_type == ONLINE_PULSE
    ]
    assert [job.job_id for job in enabled] == [changed.job_id]


@pytest.mark.parametrize("minutes", [5, 31, 12])
def test_online_pulse_rejects_values_outside_allowlist(configured, minutes):
    _, automation, service, principal = configured
    job = _job_by_type(automation, ONLINE_PULSE)
    with pytest.raises(AutomationConfigurationError):
        service.configure_job(
            principal,
            job_id=job.job_id,
            enabled=True,
            interval_minutes=minutes,
        )


def test_full_scan_offset_and_child_configuration_are_fixed(configured):
    _, automation, service, principal = configured
    full = _job_by_type(automation, FULL_MARKET_SCAN)
    with pytest.raises(AutomationConfigurationError, match="固定为 10"):
        service.configure_job(
            principal,
            job_id=full.job_id,
            enabled=True,
            interval_minutes=90,
            offset_minutes=15,
        )


def test_review_timeout_and_daily_generation_use_bounded_fields(configured):
    _, automation, service, principal = configured
    review_job = _job_by_type(automation, REVIEW_TIMEOUT_MAINTENANCE)
    review_changed = service.configure_job(
        principal,
        job_id=review_job.job_id,
        enabled=True,
        interval_minutes=15,
    )
    assert review_changed.schedule_expression == "15"

    daily_job = _job_by_type(automation, DAILY_TASK_GENERATION)
    daily_changed = service.configure_job(
        principal,
        job_id=daily_job.job_id,
        enabled=True,
        offset_minutes=20,
        source_allowlist=("LISTING_RULES",),
    )
    assert daily_changed.schedule_expression == "20:25"
    assert daily_changed.config["source_allowlist"] == [
        "PRODUCTS",
        "LISTING_RULES",
    ]
    with pytest.raises(AutomationConfigurationError, match="固定白名单"):
        service.configure_job(
            principal,
            job_id=daily_changed.job_id,
            enabled=True,
            offset_minutes=20,
            source_allowlist=("ARBITRARY_SCRIPT",),
        )
    child = _job_by_type(automation, LISTING_STATUS_SCAN)
    with pytest.raises(AutomationConfigurationError, match="不能独立配置"):
        service.configure_job(
            principal,
            job_id=child.job_id,
            enabled=True,
        )


def test_inventory_alert_uses_existing_versioned_policy(configured):
    _, _, service, principal = configured
    current = service.inventory.get_alert_policy(internal_sku="SKU-SYNTHETIC")
    assert current is not None

    updated = service.configure_inventory_alert(
        principal,
        scope_type="DEFAULT",
        scope_key="*",
        enabled=True,
        threshold_qty=20,
        repeat_interval_minutes=60,
        expected_version=current.version,
    )

    assert updated.threshold_qty == 20
    assert updated.repeat_interval_minutes == 60
    assert updated.version == current.version + 1
    assert updated.updated_by == "operator"


def test_settlement_rerun_is_explicit_and_idempotent(configured):
    _, automation, service, principal = configured
    job = _job_by_type(automation, PLATFORM_TRADE_DAY_SETTLEMENT)

    first, created = service.schedule_rerun(
        principal,
        job_id=job.job_id,
        target_trade_date=date(2026, 8, 10),
        idempotency_key="web-rerun-synthetic-001",
    )
    replay, replay_created = service.schedule_rerun(
        principal,
        job_id=job.job_id,
        target_trade_date=date(2026, 8, 10),
        idempotency_key="web-rerun-synthetic-001",
    )

    assert created is True
    assert replay_created is False
    assert replay.run_id == first.run_id
    assert first.platform_trade_date == date(2026, 8, 10)
    assert first.seller_operation_date == date(2026, 8, 10)
    with pytest.raises(AutomationConfigurationError, match="different"):
        service.schedule_rerun(
            principal,
            job_id=job.job_id,
            target_trade_date=date(2026, 8, 11),
            idempotency_key="web-rerun-synthetic-001",
        )


def test_service_enforces_business_management_capability(configured):
    _, automation, service, _ = configured
    job = _job_by_type(automation, ONLINE_PULSE)
    with pytest.raises(AutomationConfigurationError, match="没有自动化方案管理权限"):
        service.configure_job(
            Principal("viewer", frozenset()),
            job_id=job.job_id,
            enabled=True,
            interval_minutes=10,
        )
