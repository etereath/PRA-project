from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.enums import IncidentCategory, IncidentStatus
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.incident_management import (
    IncidentDetection,
    IncidentManagementService,
)
from app.services.shadowbot_worker_recovery import (
    CommandShadowBotHostAdapter,
    ShadowBotHostActionResult,
    ShadowBotHostInspection,
    ShadowBotWorkerRecoveryCoordinator,
)

NOW = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def runtime_repository(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "REVIEW_TOKEN_SECRET",
        "worker-recovery-test-secret-at-least-32-bytes",
    )
    monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", "fake")
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    repository.init_schema()
    return repository


@pytest.fixture
def queue_dir(tmp_path):
    root = tmp_path / "shadowbot_queue"
    for name in (
        "inbox",
        "working",
        "results",
        "archive",
        "quarantine",
        "evidence",
        "control",
    ):
        (root / name).mkdir(parents=True)
    return root


@dataclass
class FakeHostAdapter:
    inspections: list[ShadowBotHostInspection]
    outcomes: dict[str, ShadowBotHostActionResult] = field(default_factory=dict)
    inspected: int = 0
    actions: list[str] = field(default_factory=list)

    def inspect(self, *, app_name: str) -> ShadowBotHostInspection:
        index = min(self.inspected, len(self.inspections) - 1)
        self.inspected += 1
        return self.inspections[index]

    def perform(
        self,
        action: str,
        *,
        app_name: str,
    ) -> ShadowBotHostActionResult:
        self.actions.append(action)
        return self.outcomes.get(
            action,
            ShadowBotHostActionResult(
                ok=True,
                action=action,
                app_name=app_name,
                detail="synthetic host action",
            ),
        )


class FakeWatchdog:
    def __init__(self, queue_dir: Path, *, write_result: bool = False) -> None:
        self.queue_dir = queue_dir
        self.write_result = write_result
        self.calls = 0

    def inspect(self, *, now: datetime):
        self.calls += 1
        if self.write_result:
            _write_json(
                self.queue_dir / "results" / "attempt-unknown.result.json",
                {
                    "execution_attempt_id": "attempt-unknown",
                    "status": "SIDE_EFFECT_UNKNOWN",
                },
            )
        return [{"status": "RECOVERY_RESULT_WRITTEN"}]


def _create_worker_incident(repository: SQLiteRuntimeRepository):
    return (
        IncidentManagementService(repository)
        .detect(
            IncidentDetection(
                event_key="worker-unavailable-detected",
                category=IncidentCategory.WORKER_UNAVAILABLE,
                source_type="WORKER_HEALTH",
                source_ref_id="primary-worker",
                severity="S3",
                blocks_finalization=True,
                platform_name="synthetic-platform",
                platform_trade_date=date(2026, 8, 3),
                seller_operation_date=date(2026, 8, 3),
                subject_type="worker",
                subject_key="test2",
                title="Worker 不可用",
                description="Synthetic worker recovery fixture",
                occurred_at=NOW,
                reason="heartbeat_stale",
            )
        )
        .incident
    )


def _inspection(
    *,
    main: bool = True,
    app_list: bool = True,
    editor: bool = False,
    unsaved: bool = False,
) -> ShadowBotHostInspection:
    return ShadowBotHostInspection(
        configured=True,
        app_name="test2",
        main_window_locatable=main,
        app_list_locatable=app_list,
        editor_open=editor,
        unsaved_changes=unsaved,
    )


def _write_heartbeat(
    queue_dir: Path,
    *,
    status: str,
    updated_at: datetime,
    processed: int = 0,
) -> None:
    _write_json(
        queue_dir / "heartbeat.json",
        {
            "worker_id": "synthetic-worker",
            "status": status,
            "updated_at": updated_at.isoformat(),
            "started_at": (updated_at - timedelta(minutes=5)).isoformat(),
            "processed": processed,
            "heartbeat_consecutive_failures": 0,
            "heartbeat_write_failures": 0,
            "heartbeat_thread_restarts": 0,
        },
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _coordinator(
    repository: SQLiteRuntimeRepository,
    queue_dir: Path,
    host: FakeHostAdapter,
    *,
    enabled: bool = True,
    watchdog=None,
) -> ShadowBotWorkerRecoveryCoordinator:
    return ShadowBotWorkerRecoveryCoordinator(
        repository,
        queue_dir=queue_dir,
        host_adapter=host,
        enabled=enabled,
        queue_watchdog=watchdog,
    )


def test_host_recovery_is_disabled_without_explicit_feature_flag(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    host = FakeHostAdapter([_inspection()])

    result = _coordinator(
        runtime_repository,
        queue_dir,
        host,
        enabled=False,
    ).recover(incident.incident_id, now=NOW)

    assert result.status == "HOST_RECOVERY_DISABLED"
    assert host.inspected == 0
    assert host.actions == []


def test_fresh_running_heartbeat_resolves_without_host_action(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    _write_heartbeat(queue_dir, status="RUNNING", updated_at=NOW, processed=4)
    host = FakeHostAdapter([_inspection()])
    coordinator = _coordinator(runtime_repository, queue_dir, host)

    result = coordinator.recover(incident.incident_id, now=NOW)
    replay = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=1),
    )

    assert result.status == "WORKER_RECOVERED"
    assert result.host_action_performed is False
    assert replay.status == "RECOVERY_ALREADY_SUCCEEDED"
    assert host.actions == []
    current = IncidentManagementService(runtime_repository).get(incident.incident_id)
    assert current is not None
    assert current.incident_status is IncidentStatus.RESOLVED
    lifecycle = json.loads(
        (queue_dir / "control" / "shadowbot_lifecycle_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert lifecycle["recorded_state"] == "RUNNING"
    assert lifecycle["worker_processed_count"] == 4
    notifications = runtime_repository.list_notification_outbox()
    assert [item.notification_type for item in notifications] == ["worker_recovered"]


@pytest.mark.parametrize(
    ("artifact", "expected_status"),
    [
        ("result", "WAITING_RESULT_IMPORT"),
        ("working", "WAITING_ACTIVE_WORKING"),
    ],
)
def test_active_queue_artifacts_block_host_actions(
    runtime_repository,
    queue_dir,
    artifact,
    expected_status,
):
    incident = _create_worker_incident(runtime_repository)
    _write_heartbeat(queue_dir, status="RUNNING", updated_at=NOW)
    if artifact == "result":
        _write_json(
            queue_dir / "results" / "attempt.result.json",
            {"status": "FAILED"},
        )
    else:
        _write_json(
            queue_dir / "working" / "attempt.phase.json",
            {
                "execution_attempt_id": "attempt",
                "phase": "UI_STARTED",
                "updated_at": NOW.isoformat(),
            },
        )
    host = FakeHostAdapter([_inspection()])

    result = _coordinator(runtime_repository, queue_dir, host).recover(
        incident.incident_id,
        now=NOW,
    )

    assert result.status == expected_status
    assert host.actions == []


def test_submit_risk_uses_existing_watchdog_and_creates_write_unknown_incident(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    _write_heartbeat(
        queue_dir,
        status="RUNNING",
        updated_at=NOW - timedelta(minutes=2),
    )
    _write_json(
        queue_dir / "working" / "attempt-unknown.phase.json",
        {
            "execution_attempt_id": "attempt-unknown",
            "operation_id": "operation-unknown",
            "phase": "SUBMIT_CLICKED",
            "side_effect_state": "UNKNOWN",
            "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        },
    )
    _write_json(
        queue_dir / "working" / "attempt-unknown.request.json",
        {
            "execution_attempt_id": "attempt-unknown",
            "operation_id": "operation-unknown",
            "platform_name": "synthetic-platform",
            "platform_trade_date": "2026-08-03",
            "seller_operation_date": "2026-08-03",
        },
    )
    watchdog = FakeWatchdog(queue_dir, write_result=True)
    host = FakeHostAdapter([_inspection()])

    result = _coordinator(
        runtime_repository,
        queue_dir,
        host,
        watchdog=watchdog,
    ).recover(incident.incident_id, now=NOW)

    assert result.status == "WAITING_RESULT_IMPORT"
    assert result.write_unknown_risk is True
    assert result.watchdog_event_count == 1
    assert watchdog.calls == 1
    assert host.actions == []
    write_unknown = OperationalIncidentRepository(runtime_repository).list_active(
        category=IncidentCategory.WRITE_UNKNOWN
    )
    assert len(write_unknown) == 1
    assert write_unknown[0].subject_key == "operation-unknown"


def test_stopped_clean_worker_starts_once_then_waits_for_fresh_heartbeat(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    _write_heartbeat(queue_dir, status="STOPPED", updated_at=NOW)
    host = FakeHostAdapter([_inspection()])
    coordinator = _coordinator(runtime_repository, queue_dir, host)

    started = coordinator.recover(incident.incident_id, now=NOW)
    waiting = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=5),
    )
    _write_heartbeat(
        queue_dir,
        status="RUNNING",
        updated_at=NOW + timedelta(seconds=6),
    )
    completed = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=6),
    )

    assert started.status == "WORKER_START_REQUESTED"
    assert waiting.status == "WAITING_RUNNING_HEARTBEAT"
    assert completed.status == "WORKER_RECOVERED"
    assert host.actions == ["start_test2"]


def test_missing_host_restarts_waits_twenty_seconds_then_starts_test2(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    host = FakeHostAdapter(
        [
            _inspection(main=False, app_list=False),
            _inspection(main=True, app_list=True),
        ]
    )
    coordinator = _coordinator(runtime_repository, queue_dir, host)

    restarted = coordinator.recover(incident.incident_id, now=NOW)
    early = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=19),
    )
    started = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=20),
    )
    _write_heartbeat(
        queue_dir,
        status="RUNNING",
        updated_at=NOW + timedelta(seconds=21),
    )
    completed = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=21),
    )

    assert restarted.status == "SHADOWBOT_RESTART_REQUESTED"
    assert early.status == "WAITING_SHADOWBOT_LOGIN"
    assert started.status == "WORKER_START_REQUESTED"
    assert completed.status == "WORKER_RECOVERED"
    assert host.actions == ["restart_shadowbot", "start_test2"]
    lifecycle = json.loads(
        (queue_dir / "control" / "shadowbot_lifecycle_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert lifecycle["worker_started_at"] == (NOW + timedelta(seconds=20)).isoformat()
    assert lifecycle["worker_processed_count"] == 0


def test_unsaved_editor_blocks_recovery_before_attempt_claim(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    host = FakeHostAdapter([_inspection(editor=True, unsaved=True)])

    result = _coordinator(runtime_repository, queue_dir, host).recover(
        incident.incident_id,
        now=NOW,
    )

    assert result.status == "BLOCKED_UNSAVED_EDITOR"
    assert host.actions == []
    recovery_events = [
        event
        for event in IncidentManagementService(runtime_repository).list_events(
            incident.incident_id
        )
        if event.source_type == "WORKER_RECOVERY"
    ]
    assert recovery_events == []


def test_failed_host_action_is_terminal_and_not_repeated(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    host = FakeHostAdapter(
        [_inspection()],
        outcomes={
            "start_test2": ShadowBotHostActionResult(
                ok=False,
                action="start_test2",
                app_name="test2",
                detail="synthetic start failure",
            )
        },
    )
    coordinator = _coordinator(runtime_repository, queue_dir, host)

    failed = coordinator.recover(incident.incident_id, now=NOW)
    replay = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=1),
    )

    assert failed.status == "WORKER_RECOVERY_FAILED"
    assert replay.status == "RECOVERY_ALREADY_FAILED"
    assert host.actions == ["start_test2"]
    assert [
        item.notification_type for item in runtime_repository.list_notification_outbox()
    ] == ["worker_recovery_failed"]


def test_stale_stop_signal_is_removed_only_after_queue_is_clean_and_stopped(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    _write_heartbeat(queue_dir, status="STOPPED", updated_at=NOW)
    stop_path = queue_dir / "control" / "stop.signal"
    stop_path.write_text("stop\n", encoding="ascii", newline="\n")
    host = FakeHostAdapter([_inspection()])

    result = _coordinator(runtime_repository, queue_dir, host).recover(
        incident.incident_id,
        now=NOW,
    )

    assert result.status == "WORKER_START_REQUESTED"
    assert stop_path.exists() is False
    assert host.actions == ["start_test2"]


def test_stuck_stop_after_watchdog_recovery_uses_quit_hotkey_then_starts(
    runtime_repository,
    queue_dir,
):
    incident = _create_worker_incident(runtime_repository)
    _write_heartbeat(
        queue_dir,
        status="RUNNING",
        updated_at=NOW - timedelta(minutes=2),
    )
    _write_json(
        queue_dir / "working" / "attempt-stuck.phase.json",
        {
            "execution_attempt_id": "attempt-stuck",
            "phase": "UI_STARTED",
            "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        },
    )
    stop_path = queue_dir / "control" / "stop.signal"
    stop_path.write_text("stop\n", encoding="ascii", newline="\n")
    watchdog = FakeWatchdog(queue_dir, write_result=True)
    host = FakeHostAdapter([_inspection(), _inspection()])
    coordinator = _coordinator(
        runtime_repository,
        queue_dir,
        host,
        watchdog=watchdog,
    )

    waiting_import = coordinator.recover(incident.incident_id, now=NOW)
    for path in (queue_dir / "working").glob("*"):
        path.unlink()
    for path in (queue_dir / "results").glob("*"):
        path.unlink()
    quit_requested = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=1),
    )
    _write_heartbeat(
        queue_dir,
        status="STOPPED",
        updated_at=NOW + timedelta(seconds=2),
    )
    start_requested = coordinator.recover(
        incident.incident_id,
        now=NOW + timedelta(seconds=2),
    )

    assert waiting_import.status == "WAITING_RESULT_IMPORT"
    assert quit_requested.status == "QUIT_HOTKEY_REQUESTED"
    assert start_requested.status == "WORKER_START_REQUESTED"
    assert stop_path.exists() is False
    assert host.actions == ["send_quit_hotkey", "start_test2"]


def test_command_host_adapter_uses_strict_json_contract(tmp_path):
    helper = tmp_path / "host_helper.py"
    helper.write_text(
        "import json,sys\n"
        "request=json.loads(sys.stdin.read())\n"
        "response=dict(request)\n"
        "response.update({'ok': True, 'main_window_locatable': True, "
        "'app_list_locatable': True, 'editor_open': False, "
        "'unsaved_changes': False, 'process_paths_verified': True})\n"
        "sys.stdout.write(json.dumps(response))\n",
        encoding="utf-8",
        newline="\n",
    )
    adapter = CommandShadowBotHostAdapter(
        (str(Path(sys.executable).resolve()), str(helper.resolve()))
    )

    inspection = adapter.inspect(app_name="test2")
    restarted = adapter.perform("restart_shadowbot", app_name="test2")

    assert inspection.configured is True
    assert inspection.app_list_locatable is True
    assert restarted.ok is True
    assert restarted.action == "restart_shadowbot"


@pytest.mark.skipif(os.name != "nt", reason="bundled helper is Windows-only")
def test_bundled_windows_host_helper_is_ascii_and_configured(monkeypatch):
    monkeypatch.delenv("SHADOWBOT_HOST_HELPER_COMMAND_JSON", raising=False)

    adapter = CommandShadowBotHostAdapter.from_environment()
    assert isinstance(adapter, CommandShadowBotHostAdapter)
    helper = Path(adapter.command[-1])
    assert helper.name == "shadowbot_windows_host_helper.ps1"
    assert helper.is_file()
    helper.read_bytes().decode("ascii")
