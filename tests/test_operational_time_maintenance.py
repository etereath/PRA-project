from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timezone

import pytest

from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    DAILY_TASK_GENERATION,
    FULL_MARKET_SCAN,
    ONLINE_PULSE,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    POST_CUTOFF_PULSE,
    PRE_CUTOFF_FULL_SCAN,
    SALES_PLAN_INPUT_BUILD,
    ensure_default_automation_jobs,
)
from app.services.operational_time_maintenance import (
    OperationalTimeMaintenanceError,
    OperationalTimeMaintenanceService,
)
from scripts.replace_operational_time_policy import main as maintenance_main


PLATFORM = "synthetic-platform"
NOW = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
V1 = "CN_SINGLE_PLATFORM_2026_V1"
V2 = "CN_SINGLE_PLATFORM_2026_V2"


def _configured(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    automation = AutomationRepository(runtime)
    jobs = ensure_default_automation_jobs(
        automation,
        platform_name=PLATFORM,
        now=NOW,
    )
    return runtime, automation, {job.job_type: job for job in jobs}


def _replace(automation: AutomationRepository, **kwargs):
    return OperationalTimeMaintenanceService(
        automation,
        clock=lambda: NOW,
    ).replace_policy_and_timed_jobs(
        platform_name=PLATFORM,
        expected_current_policy_version=V1,
        successor_policy_version=V2,
        platform_cutoff_local_time=time(19, 0),
        seller_cutoff_local_time=time(21, 0),
        peak_start_local_time=time(17, 0),
        created_by="admin:maintenance-test",
        **kwargs,
    )


def test_policy_and_all_timed_jobs_switch_in_one_maintenance(tmp_path):
    _, automation, original = _configured(tmp_path)
    plan = original[SALES_PLAN_INPUT_BUILD]
    daily = original[DAILY_TASK_GENERATION]
    plan = automation.replace_job_version(
        previous_job_id=plan.job_id,
        successor=replace(
            plan,
            job_id="AUTOMATION-SALES-PLAN-INPUT-CUSTOM",
            config={**plan.config, "settlement_offset_minutes": 15},
            schedule_expression="20:15",
        ),
        now=NOW,
    )
    automation.replace_job_version(
        previous_job_id=daily.job_id,
        successor=replace(
            daily,
            job_id="AUTOMATION-DAILY-TASK-GENERATION-CUSTOM",
            config={
                **daily.config,
                "plan_input_offset_minutes": 20,
                "sales_plan_input_offset_minutes": 15,
                "source_allowlist": ["PRODUCTS", "LISTING_RULES"],
            },
            schedule_expression="20:35",
        ),
        now=NOW,
    )
    disabled_post = automation.replace_job_version(
        previous_job_id=original[POST_CUTOFF_PULSE].job_id,
        successor=replace(
            original[POST_CUTOFF_PULSE],
            job_id="AUTOMATION-POST-CUTOFF-PULSE-DISABLED",
            enabled=False,
            config={
                **original[POST_CUTOFF_PULSE].config,
                "configuration_version": "sha256:disabled-post",
                "effective_at": NOW.isoformat(),
            },
        ),
        now=NOW,
    )

    result = _replace(automation)

    assert result.previous_policy_version == V1
    assert result.successor_policy.policy_version == V2
    by_type = {job.job_type: job for job in result.successor_jobs}
    assert by_type[PRE_CUTOFF_FULL_SCAN].schedule_expression == "18:55"
    assert by_type[POST_CUTOFF_PULSE].schedule_expression == "19:05"
    assert by_type[PLATFORM_TRADE_DAY_SETTLEMENT].schedule_expression == "21:00"
    assert by_type[SALES_PLAN_INPUT_BUILD].schedule_expression == "21:15"
    assert by_type[DAILY_TASK_GENERATION].schedule_expression == "21:35"
    assert by_type[POST_CUTOFF_PULSE].enabled is False
    assert automation.get_job(disabled_post.job_id).enabled is False
    assert by_type[DAILY_TASK_GENERATION].config["source_allowlist"] == [
        "PRODUCTS",
        "LISTING_RULES",
    ]
    assert by_type[DAILY_TASK_GENERATION].config[
        "upstream_configuration_version"
    ] == by_type[SALES_PLAN_INPUT_BUILD].config["configuration_version"]
    assert {
        job.config["time_policy_version"] for job in result.successor_jobs
    } == {V2}
    assert {
        job.config["updated_by"] for job in result.successor_jobs
    } == {"admin:maintenance-test"}
    assert automation.get_job(original[PRE_CUTOFF_FULL_SCAN].job_id).enabled is False
    assert automation.get_job(original[SALES_PLAN_INPUT_BUILD].job_id).enabled is False
    assert automation.get_job(original[DAILY_TASK_GENERATION].job_id).enabled is False
    assert len(
        [
            job
            for job in automation.list_jobs(enabled_only=True)
            if job.job_type == PRE_CUTOFF_FULL_SCAN
        ]
    ) == 1
    assert automation.get_job(original[ONLINE_PULSE].job_id).enabled is True
    assert automation.get_job(original[FULL_MARKET_SCAN].job_id).enabled is True
    policies = automation.load_operational_time_policies()
    assert [(policy.policy_version, policy.effective_to) for policy in policies] == [
        (V1, NOW),
        (V2, None),
    ]


def test_job_failure_rolls_back_policy_and_every_successor(tmp_path):
    runtime, automation, original = _configured(tmp_path)
    with runtime.connect_write() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_policy_successor_job
            BEFORE INSERT ON automation_jobs
            WHEN NEW.job_type = 'DAILY_TASK_GENERATION'
             AND NEW.job_id <> 'AUTOMATION-DAILY-TASK-GENERATION'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic coordinated failure');
            END
            """
        )
        connection.commit()

    with pytest.raises(
        OperationalTimeMaintenanceError,
        match="synthetic coordinated failure",
    ):
        _replace(automation)

    policies = automation.load_operational_time_policies()
    assert [(policy.policy_version, policy.effective_to) for policy in policies] == [
        (V1, None)
    ]
    for job_type in (
        PRE_CUTOFF_FULL_SCAN,
        POST_CUTOFF_PULSE,
        PLATFORM_TRADE_DAY_SETTLEMENT,
        SALES_PLAN_INPUT_BUILD,
        DAILY_TASK_GENERATION,
    ):
        assert automation.get_job(original[job_type].job_id).enabled is True
        assert [
            job.job_id
            for job in automation.list_jobs()
            if job.job_type == job_type
        ] == [original[job_type].job_id]


def test_stale_policy_expectation_fails_without_job_changes(tmp_path):
    _, automation, original = _configured(tmp_path)
    service = OperationalTimeMaintenanceService(
        automation,
        clock=lambda: NOW,
    )

    with pytest.raises(
        OperationalTimeMaintenanceError,
        match="current operational time policy changed",
    ):
        service.replace_policy_and_timed_jobs(
            platform_name=PLATFORM,
            expected_current_policy_version="STALE-V0",
            successor_policy_version=V2,
            platform_cutoff_local_time=time(19, 0),
            seller_cutoff_local_time=time(21, 0),
            peak_start_local_time=time(17, 0),
            created_by="admin:maintenance-test",
        )

    assert automation.load_operational_time_policies()[0].effective_to is None
    assert automation.get_job(original[PRE_CUTOFF_FULL_SCAN].job_id).enabled is True


def test_policy_job_drift_fails_closed_before_the_transaction(tmp_path):
    _, automation, original = _configured(tmp_path)
    drifted = original[PLATFORM_TRADE_DAY_SETTLEMENT]
    automation.upsert_job(
        replace(
            drifted,
            config={**drifted.config, "time_policy_version": "DRIFTED"},
        ),
        now=NOW,
    )

    with pytest.raises(
        OperationalTimeMaintenanceError,
        match="not aligned with the current time policy",
    ):
        _replace(automation)

    assert automation.load_operational_time_policies()[0].effective_to is None


def test_admin_script_creates_backup_and_reads_back_coordinated_switch(
    tmp_path,
    capsys,
):
    runtime, _, _ = _configured(tmp_path)
    backup_path = tmp_path / "backups" / "before-time-policy-v2.sqlite3"

    exit_code = maintenance_main(
        [
            "--runtime-db",
            str(runtime.db_path),
            "--backup-db",
            str(backup_path),
            "--platform-name",
            PLATFORM,
            "--expected-current-policy-version",
            V1,
            "--successor-policy-version",
            V2,
            "--platform-cutoff",
            "19:00",
            "--seller-cutoff",
            "21:00",
            "--peak-start",
            "17:00",
            "--created-by",
            "admin:script-test",
            "--apply",
        ]
    )

    assert exit_code == 0
    assert backup_path.is_file()
    output = capsys.readouterr().out
    assert "同一事务切换" in output
    automation = AutomationRepository(runtime)
    assert [
        policy.policy_version
        for policy in automation.load_operational_time_policies()
        if policy.effective_to is None
    ] == [V2]
    assert {
        job.config.get("time_policy_version")
        for job in automation.list_jobs()
        if job.enabled and job.job_type in {
            PRE_CUTOFF_FULL_SCAN,
            POST_CUTOFF_PULSE,
            PLATFORM_TRADE_DAY_SETTLEMENT,
            SALES_PLAN_INPUT_BUILD,
            DAILY_TASK_GENERATION,
        }
    } == {V2}
