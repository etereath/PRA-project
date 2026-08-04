from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from app.enums import IncidentCategory, IncidentStatus
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.incident_management import (
    IncidentDetection,
    IncidentManagementService,
    IncidentNotificationService,
)
from app.services.shadowbot_queue import ShadowBotQueueWatchdog
from app.services.shadowbot_worker_health import (
    build_shadowbot_worker_health_report,
)

HOST_HELPER_SCHEMA_VERSION = "shadowbot-host-helper-1.0"
LIFECYCLE_SCHEMA_VERSION = "shadowbot-lifecycle-state-1.0"
ALLOWED_LIFECYCLE_STATES = frozenset({"RUNNING", "STOPPED", "UNKNOWN"})
SUBMIT_RISK_PHASES = frozenset(
    {"SUBMIT_INTENT_RECORDED", "SUBMIT_CLICKED", "RESULT_WRITTEN"}
)


@dataclass(frozen=True, slots=True)
class ShadowBotHostInspection:
    configured: bool
    app_name: str
    main_window_locatable: bool = False
    app_list_locatable: bool = False
    editor_open: bool = False
    unsaved_changes: bool = False
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ShadowBotHostActionResult:
    ok: bool
    action: str
    app_name: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ShadowBotWorkerRecoveryResult:
    incident_id: str
    status: str
    action: str = ""
    host_action_performed: bool = False
    watchdog_event_count: int = 0
    working_count: int = 0
    result_count: int = 0
    stop_signal_present: bool = False
    heartbeat_fresh: bool = False
    write_unknown_risk: bool = False
    detail: str = ""


class ShadowBotHostAdapter(Protocol):
    def inspect(self, *, app_name: str) -> ShadowBotHostInspection: ...

    def perform(
        self,
        action: str,
        *,
        app_name: str,
    ) -> ShadowBotHostActionResult: ...


class UnavailableShadowBotHostAdapter:
    def inspect(self, *, app_name: str) -> ShadowBotHostInspection:
        return ShadowBotHostInspection(
            configured=False,
            app_name=app_name,
            detail="ShadowBot host helper is not configured",
        )

    def perform(
        self,
        action: str,
        *,
        app_name: str,
    ) -> ShadowBotHostActionResult:
        return ShadowBotHostActionResult(
            ok=False,
            action=action,
            app_name=app_name,
            detail="ShadowBot host helper is not configured",
        )


class CommandShadowBotHostAdapter:
    """Strict JSON boundary for one separately reviewed Windows host helper."""

    ALLOWED_ACTIONS = frozenset(
        {"inspect", "start_test2", "restart_shadowbot", "send_quit_hotkey"}
    )

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not command:
            raise ValueError("ShadowBot host helper command must not be empty")
        executable = Path(command[0])
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError(
                "ShadowBot host helper executable must be an existing absolute path"
            )
        self.command = tuple(command)
        self.timeout_seconds = max(float(timeout_seconds), 1.0)

    @classmethod
    def from_environment(cls) -> ShadowBotHostAdapter:
        raw = os.environ.get("SHADOWBOT_HOST_HELPER_COMMAND_JSON", "").strip()
        if not raw:
            if os.name != "nt":
                return UnavailableShadowBotHostAdapter()
            powershell = (
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            helper = (
                Path(__file__).resolve().parents[2]
                / "scripts"
                / "shadowbot_windows_host_helper.ps1"
            )
            if not powershell.is_file() or not helper.is_file():
                return UnavailableShadowBotHostAdapter()
            return cls(
                (
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                ),
                timeout_seconds=float(
                    os.environ.get("SHADOWBOT_HOST_HELPER_TIMEOUT_SECONDS", "30")
                ),
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "SHADOWBOT_HOST_HELPER_COMMAND_JSON must be a JSON array"
            ) from exc
        if not isinstance(parsed, list) or not all(
            isinstance(item, str) and item.strip() for item in parsed
        ):
            raise ValueError(
                "SHADOWBOT_HOST_HELPER_COMMAND_JSON must contain non-empty strings"
            )
        return cls(
            tuple(parsed),
            timeout_seconds=float(
                os.environ.get("SHADOWBOT_HOST_HELPER_TIMEOUT_SECONDS", "30")
            ),
        )

    def inspect(self, *, app_name: str) -> ShadowBotHostInspection:
        payload = self._invoke("inspect", app_name=app_name)
        return ShadowBotHostInspection(
            configured=bool(payload.get("ok")),
            app_name=app_name,
            main_window_locatable=bool(payload.get("main_window_locatable")),
            app_list_locatable=bool(payload.get("app_list_locatable")),
            editor_open=bool(payload.get("editor_open")),
            unsaved_changes=bool(payload.get("unsaved_changes")),
            detail=str(payload.get("detail") or ""),
        )

    def perform(
        self,
        action: str,
        *,
        app_name: str,
    ) -> ShadowBotHostActionResult:
        if action == "inspect":
            raise ValueError("Use inspect() for the inspect action")
        payload = self._invoke(action, app_name=app_name)
        if action == "restart_shadowbot" and not bool(
            payload.get("process_paths_verified")
        ):
            raise RuntimeError(
                "ShadowBot restart helper did not prove process path verification"
            )
        return ShadowBotHostActionResult(
            ok=bool(payload.get("ok")),
            action=action,
            app_name=app_name,
            detail=str(payload.get("detail") or ""),
        )

    def _invoke(self, action: str, *, app_name: str) -> dict[str, Any]:
        if action not in self.ALLOWED_ACTIONS:
            raise ValueError(f"unsupported ShadowBot host helper action: {action}")
        request = {
            "schema_version": HOST_HELPER_SCHEMA_VERSION,
            "action": action,
            "app_name": app_name,
        }
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(  # noqa: S603
            list(self.command),
            input=json.dumps(request, ensure_ascii=False, sort_keys=True),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
            env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "ShadowBot host helper failed with a non-zero exit status"
            )
        if len(completed.stdout.encode("utf-8")) > 65_536:
            raise RuntimeError("ShadowBot host helper response is too large")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ShadowBot host helper returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("ShadowBot host helper response must be an object")
        if (
            payload.get("schema_version") != HOST_HELPER_SCHEMA_VERSION
            or payload.get("action") != action
            or payload.get("app_name") != app_name
        ):
            raise RuntimeError("ShadowBot host helper response binding failed")
        return payload


class ShadowBotLifecycleStore:
    def __init__(self, path: Path, *, app_name: str = "test2") -> None:
        self.path = Path(path)
        self.app_name = app_name

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "schema_version": LIFECYCLE_SCHEMA_VERSION,
                "app_name": self.app_name,
                "recorded_state": "UNKNOWN",
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("ShadowBot lifecycle state is unreadable") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("ShadowBot lifecycle state must be an object")
        if payload.get("recorded_state") not in ALLOWED_LIFECYCLE_STATES:
            raise RuntimeError("ShadowBot lifecycle recorded_state is invalid")
        if str(payload.get("app_name") or "") != self.app_name:
            raise RuntimeError("ShadowBot lifecycle app_name does not match")
        return payload

    def write_verified_state(
        self,
        *,
        recorded_state: str,
        now: datetime,
        heartbeat: dict[str, Any],
        window_state: str,
        reason: str,
        new_worker_started_at: datetime | None = None,
    ) -> dict[str, Any]:
        if recorded_state not in ALLOWED_LIFECYCLE_STATES:
            raise ValueError("invalid ShadowBot lifecycle state")
        previous = self.read()
        processed_count = heartbeat.get("processed_count")
        if processed_count is None:
            processed_count = heartbeat.get("processed")
        if processed_count is None:
            processed_count = (
                0
                if new_worker_started_at is not None
                else previous.get("worker_processed_count") or 0
            )
        payload = {
            "schema_version": LIFECYCLE_SCHEMA_VERSION,
            "app_name": self.app_name,
            "recorded_state": recorded_state,
            "worker_started_at": str(
                _datetime_text(new_worker_started_at)
                if new_worker_started_at is not None
                else heartbeat.get("started_at")
                or heartbeat.get("worker_started_at")
                or previous.get("worker_started_at")
                or ""
            ),
            "worker_processed_count": int(processed_count),
            "last_used_at": str(previous.get("last_used_at") or ""),
            "last_execution_attempt_id": str(
                previous.get("last_execution_attempt_id") or ""
            ),
            "shadowbot_window_state": window_state,
            "updated_at": _datetime_text(now),
            "reason": reason,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    payload,
                    stream,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return payload


class ShadowBotWorkerRecoveryCoordinator:
    """One fail-closed host recovery controller for WORKER_UNAVAILABLE Incidents."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        queue_dir: Path,
        host_adapter: ShadowBotHostAdapter | None = None,
        lifecycle_path: Path | None = None,
        app_name: str = "test2",
        enabled: bool = False,
        heartbeat_max_age_seconds: float = 30.0,
        restart_login_delay_seconds: int = 20,
        recovery_deadline_seconds: int = 120,
        queue_watchdog: Any | None = None,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.queue_dir = Path(queue_dir)
        self.host_adapter = host_adapter or UnavailableShadowBotHostAdapter()
        self.app_name = app_name
        self.enabled = bool(enabled)
        self.heartbeat_max_age_seconds = float(heartbeat_max_age_seconds)
        self.restart_login_delay_seconds = max(int(restart_login_delay_seconds), 20)
        self.recovery_deadline_seconds = max(int(recovery_deadline_seconds), 30)
        self.incidents = IncidentManagementService(runtime_repository)
        self.incident_repository = OperationalIncidentRepository(runtime_repository)
        self.notifications = IncidentNotificationService(runtime_repository)
        self.lifecycle = ShadowBotLifecycleStore(
            lifecycle_path
            or self.queue_dir / "control" / "shadowbot_lifecycle_state.json",
            app_name=app_name,
        )
        self.watchdog = queue_watchdog or ShadowBotQueueWatchdog(
            self.queue_dir,
            stale_seconds=int(self.heartbeat_max_age_seconds),
            repository=runtime_repository,
        )

    def recover(
        self,
        incident_id: str,
        *,
        now: datetime,
    ) -> ShadowBotWorkerRecoveryResult:
        current = _as_utc(now)
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        if incident.category is not IncidentCategory.WORKER_UNAVAILABLE:
            raise ValueError("Worker recovery only accepts WORKER_UNAVAILABLE Incident")
        if not self.enabled:
            return ShadowBotWorkerRecoveryResult(
                incident_id=incident_id,
                status="HOST_RECOVERY_DISABLED",
                detail="PRA_ENABLE_SHADOWBOT_HOST_RECOVERY is false",
            )

        snapshot = self._snapshot(current)
        attempt = self._attempt_state(incident_id, incident.occurrence_count)
        if attempt["terminal_status"] == "SUCCESS":
            return self._result(incident_id, "RECOVERY_ALREADY_SUCCEEDED", snapshot)
        if attempt["terminal_status"] == "FAILED":
            return self._result(incident_id, "RECOVERY_ALREADY_FAILED", snapshot)
        if snapshot["result_count"]:
            return self._result(
                incident_id,
                "WAITING_RESULT_IMPORT",
                snapshot,
                write_unknown_risk=bool(snapshot["write_unknown_risk"]),
            )
        watchdog_events: list[dict[str, Any]] = []
        if snapshot["working_count"]:
            if snapshot["write_unknown_risk"]:
                self._record_write_unknown_incidents(now=current)
            if snapshot["heartbeat_fresh"]:
                return self._result(
                    incident_id,
                    "WAITING_ACTIVE_WORKING",
                    snapshot,
                    write_unknown_risk=bool(snapshot["write_unknown_risk"]),
                )
            try:
                watchdog_events = self.watchdog.inspect(now=current)
            except Exception:
                return self._result(
                    incident_id,
                    "WATCHDOG_RECOVERY_FAILED",
                    snapshot,
                    detail="Queue Watchdog could not inspect stale working artifacts",
                )
            recovery_event_count = sum(
                str(event.get("status") or "") == "RECOVERY_RESULT_WRITTEN"
                for event in watchdog_events
            )
            if recovery_event_count:
                self._record_watchdog_recovery(
                    incident_id,
                    incident.occurrence_count,
                    now=current,
                    event_count=recovery_event_count,
                    write_unknown_risk=bool(snapshot["write_unknown_risk"]),
                )
            snapshot = self._snapshot(current)
            if snapshot["result_count"]:
                return self._result(
                    incident_id,
                    "WAITING_RESULT_IMPORT",
                    snapshot,
                    watchdog_event_count=len(watchdog_events),
                    write_unknown_risk=bool(snapshot["write_unknown_risk"]),
                )
            return self._result(
                incident_id,
                "WAITING_ACTIVE_WORKING",
                snapshot,
                watchdog_event_count=len(watchdog_events),
                write_unknown_risk=bool(snapshot["write_unknown_risk"]),
            )

        if snapshot["heartbeat_fresh"]:
            return self._complete_success(
                incident_id,
                now=current,
                heartbeat=snapshot["heartbeat"],
                action="HEARTBEAT_ALREADY_RESTORED",
                host_action_performed=False,
            )
        if attempt["action"] == "restart_shadowbot":
            return self._continue_after_restart(
                incident_id,
                incident.occurrence_count,
                now=current,
                snapshot=snapshot,
                attempt=attempt,
            )
        if attempt["action"] == "start_test2":
            deadline = _required_event_datetime(attempt["deadline_at"])
            if current < deadline:
                return self._result(
                    incident_id,
                    "WAITING_RUNNING_HEARTBEAT",
                    snapshot,
                    action="start_test2",
                )
            return self._complete_failure(
                incident_id,
                incident.occurrence_count,
                now=current,
                action="start_test2",
                detail="Worker did not publish a fresh RUNNING heartbeat before deadline",
                snapshot=snapshot,
            )
        if attempt["action"] == "send_quit_hotkey":
            return self._continue_after_quit_hotkey(
                incident_id,
                incident.occurrence_count,
                now=current,
                snapshot=snapshot,
                attempt=attempt,
            )

        if snapshot["stop_signal_present"]:
            if snapshot["heartbeat_status"] == "RUNNING":
                if self._watchdog_recovery_recorded(
                    incident_id,
                    incident.occurrence_count,
                ):
                    inspection = self.host_adapter.inspect(app_name=self.app_name)
                    if not inspection.configured:
                        return self._result(
                            incident_id,
                            "HOST_ADAPTER_UNAVAILABLE",
                            snapshot,
                            detail=inspection.detail,
                        )
                    if inspection.editor_open or inspection.unsaved_changes:
                        return self._result(
                            incident_id,
                            "BLOCKED_UNSAVED_EDITOR",
                            snapshot,
                        )
                    claim = self._claim_attempt(
                        incident_id,
                        incident.occurrence_count,
                        now=current,
                    )
                    if claim.replayed:
                        return self._result(
                            incident_id,
                            "RECOVERY_ATTEMPT_ALREADY_CLAIMED",
                            snapshot,
                        )
                    return self._request_host_action(
                        incident_id,
                        incident.occurrence_count,
                        now=current,
                        action="send_quit_hotkey",
                        snapshot=snapshot,
                    )
                return self._result(
                    incident_id,
                    "WAITING_STOP_SIGNAL",
                    snapshot,
                    detail="Worker has not completed the existing stop request",
                )
            stop_path = self.queue_dir / "control" / "stop.signal"
            stop_path.unlink(missing_ok=True)
            snapshot = self._snapshot(current)

        inspection = self.host_adapter.inspect(app_name=self.app_name)
        if not inspection.configured:
            return self._result(
                incident_id,
                "HOST_ADAPTER_UNAVAILABLE",
                snapshot,
                detail=inspection.detail,
            )
        if inspection.editor_open or inspection.unsaved_changes:
            return self._result(
                incident_id,
                "BLOCKED_UNSAVED_EDITOR",
                snapshot,
                detail="ShadowBot editor state must be closed and saved before recovery",
            )

        claim = self._claim_attempt(
            incident_id,
            incident.occurrence_count,
            now=current,
        )
        if claim.replayed:
            return self._result(
                incident_id,
                "RECOVERY_ATTEMPT_ALREADY_CLAIMED",
                snapshot,
            )
        if inspection.app_list_locatable:
            return self._request_host_action(
                incident_id,
                incident.occurrence_count,
                now=current,
                action="start_test2",
                snapshot=snapshot,
            )
        if inspection.main_window_locatable:
            return self._complete_failure(
                incident_id,
                incident.occurrence_count,
                now=current,
                action="inspect",
                detail="ShadowBot main window is visible but the app list is not safely locatable",
                snapshot=snapshot,
            )
        return self._request_host_action(
            incident_id,
            incident.occurrence_count,
            now=current,
            action="restart_shadowbot",
            snapshot=snapshot,
        )

    def _continue_after_quit_hotkey(
        self,
        incident_id: str,
        occurrence_count: int,
        *,
        now: datetime,
        snapshot: dict[str, Any],
        attempt: dict[str, Any],
    ) -> ShadowBotWorkerRecoveryResult:
        deadline = _required_event_datetime(attempt["deadline_at"])
        if snapshot["heartbeat_status"] == "RUNNING" and now < deadline:
            return self._result(
                incident_id,
                "WAITING_QUIT_HOTKEY",
                snapshot,
                action="send_quit_hotkey",
            )
        inspection = self.host_adapter.inspect(app_name=self.app_name)
        if inspection.editor_open or inspection.unsaved_changes:
            return self._complete_failure(
                incident_id,
                occurrence_count,
                now=now,
                action="send_quit_hotkey",
                detail="ShadowBot editor state blocks recovery after quit hotkey",
                snapshot=snapshot,
            )
        if snapshot["heartbeat_status"] == "RUNNING":
            if inspection.main_window_locatable:
                return self._complete_failure(
                    incident_id,
                    occurrence_count,
                    now=now,
                    action="send_quit_hotkey",
                    detail="Runner did not stop and the main window remains locatable",
                    snapshot=snapshot,
                )
            return self._request_host_action(
                incident_id,
                occurrence_count,
                now=now,
                action="restart_shadowbot",
                snapshot=snapshot,
            )
        stop_path = self.queue_dir / "control" / "stop.signal"
        stop_path.unlink(missing_ok=True)
        if inspection.app_list_locatable:
            return self._request_host_action(
                incident_id,
                occurrence_count,
                now=now,
                action="start_test2",
                snapshot=self._snapshot(now),
            )
        if inspection.main_window_locatable:
            return self._complete_failure(
                incident_id,
                occurrence_count,
                now=now,
                action="send_quit_hotkey",
                detail="ShadowBot app list is not safely locatable after Worker stop",
                snapshot=snapshot,
            )
        return self._request_host_action(
            incident_id,
            occurrence_count,
            now=now,
            action="restart_shadowbot",
            snapshot=self._snapshot(now),
        )

    def _continue_after_restart(
        self,
        incident_id: str,
        occurrence_count: int,
        *,
        now: datetime,
        snapshot: dict[str, Any],
        attempt: dict[str, Any],
    ) -> ShadowBotWorkerRecoveryResult:
        deadline = _required_event_datetime(attempt["deadline_at"])
        not_before = _required_event_datetime(attempt["next_action_not_before"])
        if now >= deadline:
            return self._complete_failure(
                incident_id,
                occurrence_count,
                now=now,
                action="restart_shadowbot",
                detail="ShadowBot host did not become ready before deadline",
                snapshot=snapshot,
            )
        if now < not_before:
            return self._result(
                incident_id,
                "WAITING_SHADOWBOT_LOGIN",
                snapshot,
                action="restart_shadowbot",
            )
        inspection = self.host_adapter.inspect(app_name=self.app_name)
        if inspection.editor_open or inspection.unsaved_changes:
            return self._complete_failure(
                incident_id,
                occurrence_count,
                now=now,
                action="restart_shadowbot",
                detail="ShadowBot editor opened during host recovery",
                snapshot=snapshot,
            )
        if not inspection.app_list_locatable:
            return self._result(
                incident_id,
                "WAITING_SHADOWBOT_APP_LIST",
                snapshot,
                action="restart_shadowbot",
            )
        return self._request_host_action(
            incident_id,
            occurrence_count,
            now=now,
            action="start_test2",
            snapshot=snapshot,
        )

    def _request_host_action(
        self,
        incident_id: str,
        occurrence_count: int,
        *,
        now: datetime,
        action: str,
        snapshot: dict[str, Any],
    ) -> ShadowBotWorkerRecoveryResult:
        try:
            outcome = self.host_adapter.perform(action, app_name=self.app_name)
        except Exception:
            return self._complete_failure(
                incident_id,
                occurrence_count,
                now=now,
                action=action,
                detail="ShadowBot host adapter action failed",
                snapshot=snapshot,
            )
        if not outcome.ok:
            return self._complete_failure(
                incident_id,
                occurrence_count,
                now=now,
                action=action,
                detail=outcome.detail or "ShadowBot host action was rejected",
                snapshot=snapshot,
            )
        deadline = now + timedelta(seconds=self.recovery_deadline_seconds)
        next_action_not_before = (
            now + timedelta(seconds=self.restart_login_delay_seconds)
            if action == "restart_shadowbot"
            else now
        )
        self.incidents.record_recovery(
            incident_id,
            event_key=self._event_key(
                incident_id,
                occurrence_count,
                f"{action}-requested",
            ),
            occurred_at=now,
            source_type="WORKER_RECOVERY",
            source_ref_id=self.app_name,
            payload={
                "phase": "HOST_ACTION_REQUESTED",
                "action": action,
                "deadline_at": _datetime_text(deadline),
                "next_action_not_before": _datetime_text(next_action_not_before),
            },
        )
        return self._result(
            incident_id,
            {
                "restart_shadowbot": "SHADOWBOT_RESTART_REQUESTED",
                "start_test2": "WORKER_START_REQUESTED",
                "send_quit_hotkey": "QUIT_HOTKEY_REQUESTED",
            }[action],
            snapshot,
            action=action,
            host_action_performed=True,
            detail=outcome.detail,
        )

    def _claim_attempt(
        self,
        incident_id: str,
        occurrence_count: int,
        *,
        now: datetime,
    ):
        return self.incidents.record_recovery(
            incident_id,
            event_key=self._event_key(
                incident_id,
                occurrence_count,
                "attempt-started",
            ),
            occurred_at=now,
            source_type="WORKER_RECOVERY",
            source_ref_id=self.app_name,
            payload={
                "phase": "ATTEMPT_STARTED",
                "occurrence_count": occurrence_count,
                "queue_clean": True,
            },
        )

    def _record_watchdog_recovery(
        self,
        incident_id: str,
        occurrence_count: int,
        *,
        now: datetime,
        event_count: int,
        write_unknown_risk: bool,
    ) -> None:
        self.incidents.record_recovery(
            incident_id,
            event_key=self._event_key(
                incident_id,
                occurrence_count,
                "queue-watchdog-recovered",
            ),
            occurred_at=now,
            source_type="WORKER_RECOVERY",
            source_ref_id=self.app_name,
            payload={
                "phase": "QUEUE_WATCHDOG_RECOVERED",
                "event_count": event_count,
                "write_unknown_risk": write_unknown_risk,
            },
        )

    def _watchdog_recovery_recorded(
        self,
        incident_id: str,
        occurrence_count: int,
    ) -> bool:
        key = self._event_key(
            incident_id,
            occurrence_count,
            "queue-watchdog-recovered",
        )
        return any(
            event.event_key == key for event in self.incidents.list_events(incident_id)
        )

    def _complete_success(
        self,
        incident_id: str,
        *,
        now: datetime,
        heartbeat: dict[str, Any],
        action: str,
        host_action_performed: bool,
    ) -> ShadowBotWorkerRecoveryResult:
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        occurrence_count = incident.occurrence_count
        attempt = self._attempt_state(incident_id, occurrence_count)
        new_worker_started_at = (
            _required_event_datetime(attempt["action_requested_at"])
            if attempt["action"] == "start_test2" and attempt["action_requested_at"]
            else None
        )
        self.lifecycle.write_verified_state(
            recorded_state="RUNNING",
            now=now,
            heartbeat=heartbeat,
            window_state="RUNNING_VERIFIED",
            reason="WORKER_RECOVERY_SUCCEEDED",
            new_worker_started_at=new_worker_started_at,
        )
        recovery = self.incidents.record_recovery(
            incident_id,
            event_key=self._event_key(
                incident_id,
                occurrence_count,
                "succeeded",
            ),
            occurred_at=now,
            source_type="WORKER_RECOVERY",
            source_ref_id=self.app_name,
            payload={"phase": "SUCCEEDED", "action": action},
        )
        current = recovery.incident
        if current.incident_status not in {
            IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED,
        }:
            self.incidents.transition(
                incident_id,
                to_status=IncidentStatus.RESOLVED,
                event_key=self._event_key(
                    incident_id,
                    occurrence_count,
                    "resolved",
                ),
                occurred_at=now,
                source_type="WORKER_RECOVERY",
                source_ref_id=self.app_name,
                reason="fresh RUNNING heartbeat verified",
            )
        self.notifications.enqueue_status_notification(
            incident_id,
            notification_kind="worker_recovered",
            source_event_key=recovery.event.event_key,
            message="执行端已恢复",
        )
        return ShadowBotWorkerRecoveryResult(
            incident_id=incident_id,
            status="WORKER_RECOVERED",
            action=action,
            host_action_performed=host_action_performed,
            heartbeat_fresh=True,
        )

    def _complete_failure(
        self,
        incident_id: str,
        occurrence_count: int,
        *,
        now: datetime,
        action: str,
        detail: str,
        snapshot: dict[str, Any],
    ) -> ShadowBotWorkerRecoveryResult:
        recovery = self.incidents.record_recovery(
            incident_id,
            event_key=self._event_key(
                incident_id,
                occurrence_count,
                "failed",
            ),
            occurred_at=now,
            source_type="WORKER_RECOVERY",
            source_ref_id=self.app_name,
            payload={"phase": "FAILED", "action": action, "detail": detail},
        )
        self.notifications.enqueue_status_notification(
            incident_id,
            notification_kind="worker_recovery_failed",
            source_event_key=recovery.event.event_key,
            message="自动恢复未成功，请人工处理",
        )
        return self._result(
            incident_id,
            "WORKER_RECOVERY_FAILED",
            snapshot,
            action=action,
            host_action_performed=True,
            detail=detail,
        )

    def _snapshot(self, now: datetime) -> dict[str, Any]:
        health = build_shadowbot_worker_health_report(
            self.queue_dir,
            expected_status="RUNNING",
            max_age_seconds=self.heartbeat_max_age_seconds,
            now=now,
        )
        working = sorted((self.queue_dir / "working").glob("*.json"))
        results = sorted((self.queue_dir / "results").glob("*.result.json"))
        write_unknown_risk = any(
            _phase_has_write_unknown_risk(path) for path in working
        )
        return {
            "heartbeat": health,
            "heartbeat_fresh": bool(health.get("ok")),
            "heartbeat_status": str(health.get("status") or ""),
            "working_count": len(working),
            "result_count": len(results),
            "stop_signal_present": (
                self.queue_dir / "control" / "stop.signal"
            ).exists(),
            "write_unknown_risk": write_unknown_risk,
        }

    def _record_write_unknown_incidents(self, *, now: datetime) -> None:
        for phase_path in sorted((self.queue_dir / "working").glob("*.phase.json")):
            if not _phase_has_write_unknown_risk(phase_path):
                continue
            phase = _read_optional_json(phase_path)
            attempt_id = str(
                phase.get("execution_attempt_id")
                or phase_path.name.removesuffix(".phase.json")
            )
            request = _read_optional_json(
                self.queue_dir / "working" / f"{attempt_id}.request.json"
            )
            operation_id = str(
                phase.get("operation_id") or request.get("operation_id") or attempt_id
            )
            platform_name = str(
                request.get("platform_name")
                or request.get("platform")
                or phase.get("platform_name")
                or ""
            )
            phase_name = str(phase.get("phase") or "UNKNOWN")
            occurred_at = _optional_datetime(phase.get("updated_at")) or now
            self.incidents.detect(
                IncidentDetection(
                    event_key=(
                        f"write-unknown:{attempt_id}:{phase_name}:worker-interrupted"
                    ),
                    category=IncidentCategory.WRITE_UNKNOWN,
                    source_type="SHADOWBOT_PHASE",
                    source_ref_id=attempt_id,
                    severity="S4",
                    blocks_finalization=True,
                    platform_name=platform_name or None,
                    platform_trade_date=_optional_date(
                        request.get("platform_trade_date")
                    ),
                    seller_operation_date=_optional_date(
                        request.get("seller_operation_date")
                    ),
                    subject_type="shadowbot_operation",
                    subject_key=operation_id,
                    title="平台写入结果待对账",
                    description=(
                        "Worker 在可能产生平台副作用的阶段失去可信心跳；"
                        "必须由既有 Importer 和唯一 RECONCILE 收敛。"
                    ),
                    occurred_at=occurred_at,
                    reason="worker_interrupted_after_submit_intent",
                    payload={
                        "execution_attempt_id": attempt_id,
                        "phase": phase_name,
                        "side_effect_state": str(phase.get("side_effect_state") or ""),
                    },
                )
            )

    def _attempt_state(
        self,
        incident_id: str,
        occurrence_count: int,
    ) -> dict[str, Any]:
        prefix = f"worker-recovery:{incident_id}:occurrence:{occurrence_count}:"
        state: dict[str, Any] = {
            "terminal_status": "",
            "action": "",
            "deadline_at": "",
            "next_action_not_before": "",
            "action_requested_at": "",
        }
        for event in self.incidents.list_events(incident_id):
            if not event.event_key.startswith(prefix):
                continue
            payload = event.event_payload.get("record")
            if not isinstance(payload, dict):
                continue
            phase = str(payload.get("phase") or "")
            if phase == "SUCCEEDED":
                state["terminal_status"] = "SUCCESS"
            elif phase == "FAILED":
                state["terminal_status"] = "FAILED"
            elif phase == "HOST_ACTION_REQUESTED":
                state.update(
                    {
                        "action": str(payload.get("action") or ""),
                        "deadline_at": str(payload.get("deadline_at") or ""),
                        "next_action_not_before": str(
                            payload.get("next_action_not_before") or ""
                        ),
                        "action_requested_at": _datetime_text(event.occurred_at),
                    }
                )
        return state

    @staticmethod
    def _event_key(incident_id: str, occurrence_count: int, suffix: str) -> str:
        return f"worker-recovery:{incident_id}:occurrence:{occurrence_count}:{suffix}"

    @staticmethod
    def _result(
        incident_id: str,
        status: str,
        snapshot: dict[str, Any],
        *,
        action: str = "",
        host_action_performed: bool = False,
        watchdog_event_count: int = 0,
        write_unknown_risk: bool = False,
        detail: str = "",
    ) -> ShadowBotWorkerRecoveryResult:
        return ShadowBotWorkerRecoveryResult(
            incident_id=incident_id,
            status=status,
            action=action,
            host_action_performed=host_action_performed,
            watchdog_event_count=watchdog_event_count,
            working_count=int(snapshot["working_count"]),
            result_count=int(snapshot["result_count"]),
            stop_signal_present=bool(snapshot["stop_signal_present"]),
            heartbeat_fresh=bool(snapshot["heartbeat_fresh"]),
            write_unknown_risk=write_unknown_risk,
            detail=detail,
        )


def build_worker_recovery_coordinator_from_environment(
    runtime_repository: SQLiteRuntimeRepository,
    *,
    queue_dir: Path,
) -> ShadowBotWorkerRecoveryCoordinator:
    enabled = os.environ.get("PRA_ENABLE_SHADOWBOT_HOST_RECOVERY", "").strip().lower()
    is_enabled = enabled in {"1", "true", "yes"}
    return ShadowBotWorkerRecoveryCoordinator(
        runtime_repository,
        queue_dir=queue_dir,
        host_adapter=(
            CommandShadowBotHostAdapter.from_environment()
            if is_enabled
            else UnavailableShadowBotHostAdapter()
        ),
        enabled=is_enabled,
    )


def _phase_has_write_unknown_risk(path: Path) -> bool:
    if not path.name.endswith(".phase.json"):
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return (
        str(payload.get("phase") or "") in SUBMIT_RISK_PHASES
        or str(payload.get("side_effect_state") or "") == "UNKNOWN"
    )


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _optional_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _required_event_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("Worker recovery event has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("Worker recovery event timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Worker recovery time must be timezone-aware")
    return value.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    return _as_utc(value).isoformat()
