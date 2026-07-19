from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import (
    EXECUTION_MODE_COMMIT,
    SIDE_EFFECT_NOT_APPLIED,
    SIDE_EFFECT_NOT_STARTED,
    SIDE_EFFECT_UNKNOWN,
    STATUS_FAILED,
    STATUS_SIDE_EFFECT_UNKNOWN,
    STATUS_START_UNKNOWN,
    ShadowBotExecutor,
    ShadowBotTaskRunner,
    shadowbot_result_contract_from_data,
)
from app.services.shadowbot_product_read import (
    MAX_RESULT_BYTES,
    aggregate_product_snapshots,
    normalize_multi_product_request,
    validate_evidence_binding,
)


SUBMIT_PHASES = {"SUBMIT_INTENT_RECORDED", "SUBMIT_CLICKED"}
PRE_SUBMIT_PHASES = {"CLAIMED", "UI_STARTED", "PRICE_VERIFIED", "TARGET_FILLED"}


@dataclass(frozen=True, slots=True)
class ShadowBotQueuePaths:
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def working(self) -> Path:
        return self.root / "working"

    @property
    def results(self) -> Path:
        return self.root / "results"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def quarantine(self) -> Path:
        return self.root / "quarantine"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def control(self) -> Path:
        return self.root / "control"

    @property
    def heartbeat(self) -> Path:
        return self.root / "heartbeat.json"

    def ensure(self) -> None:
        for path in (
            self.inbox,
            self.working,
            self.results,
            self.archive,
            self.quarantine,
            self.evidence,
            self.control,
        ):
            path.mkdir(parents=True, exist_ok=True)


class ShadowBotResultImporter:
    """Validate and import completed result files. It never classifies queue timeouts."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        runner: ShadowBotTaskRunner,
        queue_dir: Path,
    ) -> None:
        self.repository = repository
        self.executor = ShadowBotExecutor(repository, runner)
        self.paths = ShadowBotQueuePaths(queue_dir)
        self.paths.ensure()

    def import_available(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for result_path in sorted(self.paths.results.glob("*.result.json")):
            try:
                events.append(self.import_one(result_path))
            except OSError as exc:
                events.append(
                    {
                        "status": "RETRY_PENDING",
                        "error_code": "RESULT_IO_RETRY_PENDING",
                        "error_message": str(exc),
                        "path": str(result_path),
                    }
                )
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                quarantined = self._quarantine(result_path, exc)
                events.append(
                    {
                        "status": "QUARANTINED",
                        "error_code": "RESULT_CONTRACT_INVALID",
                        "error_message": str(exc),
                        "path": str(quarantined),
                    }
                )
        return events

    def import_one(self, result_path: Path) -> dict[str, Any]:
        result_bytes = result_path.read_bytes()
        _verify_checksum(result_path, result_bytes)
        data = json.loads(result_bytes.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise ValidationError("RESULT_CONTRACT_INVALID: result JSON must be an object.")
        execution_attempt_id = str(data.get("execution_attempt_id") or "").strip()
        if not execution_attempt_id:
            raise ValidationError("RESULT_CONTRACT_INVALID: execution_attempt_id is required.")
        attempt = self.repository.get_shadowbot_execution_attempt(execution_attempt_id)
        if attempt is None:
            raise ValidationError("RESULT_CONTRACT_INVALID: execution_attempt_id does not exist.")
        request_path = self._find_request(execution_attempt_id, attempt.queue_request_path)
        request_bytes = request_path.read_bytes()
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        _verify_checksum(request_path, request_bytes)
        if request_sha256 != attempt.request_file_sha256:
            raise ValidationError("RESULT_CONTRACT_INVALID: archived request hash does not match attempt.")
        request_data = json.loads(request_bytes.decode("utf-8-sig"))
        self._validate_v2_result(request_data, data, result_bytes)
        result_file_sha256 = hashlib.sha256(result_bytes).hexdigest()
        data.setdefault("result_id", f"RESULT-{result_file_sha256[:24]}")
        lease_required = isinstance(attempt.raw_output.get("lease"), dict)
        for lease_field in ("lease_owner_token", "lease_version"):
            if lease_required and lease_field not in data:
                raise ValidationError(f"RESULT_CONTRACT_INVALID: {lease_field} is required for leased attempt.")
            if lease_field in data and str(data.get(lease_field)) != str(request_data.get(lease_field)):
                raise ValidationError(f"RESULT_CONTRACT_INVALID: {lease_field} mismatch.")
        data["result_file_sha256"] = result_file_sha256
        contract = shadowbot_result_contract_from_data(data)
        if contract.request_file_sha256 != request_sha256:
            raise ValidationError("RESULT_CONTRACT_INVALID: result request_file_sha256 mismatch.")
        for field_name in ("operation_id", "task_id", "execution_attempt_id", "execution_mode", "instruction_hash"):
            if str(data.get(field_name) or "") != str(request_data.get(field_name) or ""):
                raise ValidationError(f"RESULT_CONTRACT_INVALID: {field_name} mismatch.")
        if attempt.ended_at is None:
            self.executor.record_result(
                contract,
                automatic_reconcile_payload=_automatic_reconcile_payload(request_data),
            )
        else:
            imported_result_id = str(attempt.raw_output.get("result_id") or "")
            imported_result_sha256 = str(attempt.raw_output.get("result_file_sha256") or "")
            if not imported_result_id or not imported_result_sha256:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: late result for terminal attempt without persisted result identity."
                )
            if imported_result_id != contract.result_id or imported_result_sha256 != result_file_sha256:
                raise ValidationError("RESULT_CONTRACT_INVALID: conflicting result evidence for completed attempt.")
        archive_dir = self._archive_attempt(contract.execution_attempt_id, request_path, result_path)
        return {
            "status": "IMPORTED" if attempt.ended_at is None else "ALREADY_IMPORTED",
            "execution_attempt_id": contract.execution_attempt_id,
            "archive_dir": str(archive_dir),
        }

    @staticmethod
    def _validate_v2_result(request: dict[str, Any], result: dict[str, Any], result_bytes: bytes) -> None:
        if request.get("contract_version") != 2:
            return
        if len(result_bytes) > MAX_RESULT_BYTES:
            raise ValidationError("RESULT_CONTRACT_INVALID: v2 result exceeds 4 MiB.")
        normalized_request = normalize_multi_product_request(request)
        if result.get("contract_version") != 2 or str(result.get("read_batch_id") or "") != normalized_request["read_batch_id"]:
            raise ValidationError("RESULT_CONTRACT_INVALID: v2 batch identity mismatch.")
        snapshots = result.get("product_snapshots")
        if result.get("status") == "READ_COMPLETED":
            if not isinstance(snapshots, list) or not snapshots:
                raise ValidationError("RESULT_CONTRACT_INVALID: READ_COMPLETED requires product_snapshots.")
            aggregate_product_snapshots(
                read_batch_id=normalized_request["read_batch_id"],
                contract_version=2,
                started_at=str(result.get("started_at") or ""),
                completed_at=str(result.get("ended_at") or result.get("completed_at") or ""),
                snapshots=snapshots,
                expected_item_ids=[product["item_id"] for product in normalized_request["products"]],
            )
            execution_attempt_id = str(result.get("execution_attempt_id") or "")
            for snapshot in snapshots:
                evidence = snapshot.get("evidence")
                if not isinstance(evidence, list) or not evidence:
                    if str(snapshot.get("error_code") or "").upper() in {"EVIDENCE_UNAVAILABLE", "EVIDENCE_BINDING_FAILED"}:
                        continue
                    raise ValidationError("RESULT_CONTRACT_INVALID: each v2 snapshot requires evidence.")
                if str(snapshot.get("error_code") or "").upper() in {"EVIDENCE_UNAVAILABLE", "EVIDENCE_BINDING_FAILED"}:
                    continue
                validate_evidence_binding(
                    evidence,
                    read_batch_id=normalized_request["read_batch_id"],
                    item_id=str(snapshot.get("item_id") or ""),
                    execution_attempt_id=execution_attempt_id,
                )

    def _find_request(self, execution_attempt_id: str, recorded_path: str) -> Path:
        candidates = [
            self.paths.working / f"{execution_attempt_id}.request.json",
            self.paths.inbox / f"{execution_attempt_id}.ready.json",
        ]
        if recorded_path:
            candidates.append(Path(recorded_path))
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise ValidationError("RESULT_CONTRACT_INVALID: source request file is missing.")

    def _archive_attempt(self, execution_attempt_id: str, request_path: Path, result_path: Path) -> Path:
        archive_dir = self.paths.archive / execution_attempt_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        related = (
            request_path,
            request_path.with_suffix(request_path.suffix + ".sha256"),
            self.paths.working / f"{execution_attempt_id}.phase.json",
            result_path,
            result_path.with_suffix(result_path.suffix + ".sha256"),
        )
        for source in related:
            destination = archive_dir / source.name
            if source.exists() and destination.exists() and source.read_bytes() != destination.read_bytes():
                raise ValidationError(
                    f"RESULT_CONTRACT_INVALID: archive evidence conflict for {source.name}."
                )
        for source in related:
            if source.exists():
                destination = archive_dir / source.name
                if destination.exists():
                    source.unlink()
                else:
                    os.replace(source, destination)
        return archive_dir

    def _quarantine(self, result_path: Path, error: Exception) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.paths.quarantine / f"{stamp}-{result_path.name}"
        os.replace(result_path, destination)
        try:
            quarantined_data = json.loads(destination.read_text(encoding="utf-8-sig"))
            execution_attempt_id = str(quarantined_data.get("execution_attempt_id") or "")
            if execution_attempt_id:
                self.repository.quarantine_shadowbot_attempt(
                    execution_attempt_id,
                    reason=type(error).__name__ + ":" + str(error),
                    now=datetime.now(UTC),
                )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        checksum = result_path.with_suffix(result_path.suffix + ".sha256")
        if checksum.exists():
            os.replace(checksum, destination.with_suffix(destination.suffix + ".sha256"))
        reason_path = destination.with_suffix(destination.suffix + ".error.json")
        _atomic_write(
            reason_path,
            _json_bytes(
                {
                    "error_code": "RESULT_CONTRACT_INVALID",
                    "error_message": str(error),
                    "quarantined_at": datetime.now(UTC).isoformat(),
                    "source_path": str(result_path),
                }
            ),
        )
        return destination


class ShadowBotLoginVerificationMonitor:
    """Observes active login-verification phases and delegates handoff creation to Executor."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        runner: ShadowBotTaskRunner,
        queue_dir: Path,
    ) -> None:
        self.executor = ShadowBotExecutor(repository, runner)
        self.paths = ShadowBotQueuePaths(queue_dir)
        self.paths.ensure()

    def inspect(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for phase_path in sorted(self.paths.working.glob("*.phase.json")):
            phase_data = _read_json_object(phase_path)
            if str(phase_data.get("phase") or "") != "LOGIN_VERIFICATION_REQUIRED":
                continue
            review_task_id = self.executor.open_login_verification_handoff(phase_data)
            events.append(
                {
                    "status": "LOGIN_VERIFICATION_HANDOFF_OPEN",
                    "execution_attempt_id": str(phase_data.get("execution_attempt_id") or ""),
                    "review_task_id": review_task_id,
                }
            )
        return events


class ShadowBotQueueWatchdog:
    """Classify stale workers and working attempts. It never imports result files."""

    def __init__(
        self,
        queue_dir: Path,
        *,
        stale_seconds: int = 30,
        repository: SQLiteRuntimeRepository | None = None,
    ) -> None:
        self.paths = ShadowBotQueuePaths(queue_dir)
        self.paths.ensure()
        self.stale_seconds = stale_seconds
        self.repository = repository
        self._last_heartbeat_alert_key = ""

    def inspect(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or datetime.now(UTC)
        heartbeat_stale = self._heartbeat_stale(current)
        events = self._inspect_inbox_integrity()
        if not heartbeat_stale:
            self._last_heartbeat_alert_key = ""
            return events
        for phase_path in sorted(self.paths.working.glob("*.phase.json")):
            phase_data = _read_json_object(phase_path)
            if not _is_timestamp_stale(phase_data.get("updated_at"), current, self.stale_seconds):
                continue
            event = self._recover_phase(phase_path, phase_data)
            if event is not None:
                events.append(event)
        phase_attempts = {path.name.removesuffix(".phase.json") for path in self.paths.working.glob("*.phase.json")}
        for request_path in sorted(self.paths.working.glob("*.request.json")):
            execution_attempt_id = request_path.name.removesuffix(".request.json")
            if execution_attempt_id in phase_attempts or not _is_file_stale(request_path, current, self.stale_seconds):
                continue
            request_data = _read_json_object(request_path)
            phase_data = {
                "request_file_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
                "side_effect_state": SIDE_EFFECT_NOT_STARTED,
            }
            events.append(self._write_recovery_result(request_data, phase_data, phase="CLAIMED"))
        if not events and self.paths.heartbeat.exists():
            heartbeat = _read_json_object(self.paths.heartbeat)
            if str(heartbeat.get("status") or "") == "RUNNING":
                alert_key = "%s|%s" % (heartbeat.get("worker_id", ""), heartbeat.get("updated_at", ""))
                if alert_key != self._last_heartbeat_alert_key:
                    events.append(
                        {
                            "status": "WARNING",
                            "error_code": "WORKER_HEARTBEAT_STALE",
                            "worker_id": str(heartbeat.get("worker_id") or ""),
                            "heartbeat_updated_at": str(heartbeat.get("updated_at") or ""),
                            "stale_seconds": self.stale_seconds,
                        }
                    )
                    self._last_heartbeat_alert_key = alert_key
        return events

    def _inspect_inbox_integrity(self) -> list[dict[str, Any]]:
        if self.repository is None:
            return []
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for request_path in sorted(self.paths.inbox.glob("*.ready.json")):
            try:
                request = _read_json_object(request_path)
                attempt_id = str(request.get("execution_attempt_id") or "").strip()
                if not attempt_id:
                    raise ValidationError("ready request has no execution_attempt_id")
                attempt = self.repository.get_shadowbot_execution_attempt(attempt_id)
                duplicate = attempt_id in seen or (self.paths.working / f"{attempt_id}.request.json").exists()
                seen.add(attempt_id)
                if attempt is None:
                    reason = "ORPHAN_READY_REQUEST"
                    target_root = self.paths.quarantine
                elif duplicate:
                    reason = "DUPLICATE_READY_REQUEST"
                    target_root = self.paths.quarantine
                else:
                    operation = self.repository.get_shadowbot_operation(attempt.operation_id)
                    if operation is not None and operation.status in {"VERIFIED", "MANUAL_HANDLED"}:
                        reason = "STALE_TERMINAL_READY_REQUEST"
                        target_root = self.paths.archive / attempt_id
                        target_root.mkdir(parents=True, exist_ok=True)
                    elif operation is not None and operation.status in {
                        "NEEDS_RECONCILIATION",
                        "MANUAL_REVIEW",
                    }:
                        reason = "FROZEN_READY_REQUEST"
                        target_root = self.paths.quarantine
                    else:
                        continue
                destination = target_root / f"{reason.lower()}-{request_path.name}"
                os.replace(request_path, destination)
                checksum = request_path.with_suffix(request_path.suffix + ".sha256")
                if checksum.exists():
                    os.replace(checksum, destination.with_suffix(destination.suffix + ".sha256"))
                events.append(
                    {
                        "status": "ARCHIVED" if target_root != self.paths.quarantine else "QUARANTINED",
                        "error_code": reason,
                        "execution_attempt_id": attempt_id,
                        "path": str(destination),
                    }
                )
            except (ValidationError, ValueError, json.JSONDecodeError):
                destination = self.paths.quarantine / f"invalid-ready-{request_path.name}"
                os.replace(request_path, destination)
                events.append(
                    {
                        "status": "QUARANTINED",
                        "error_code": "READY_CONTRACT_INVALID",
                        "path": str(destination),
                    }
                )
        return events

    def _heartbeat_stale(self, now: datetime) -> bool:
        if not self.paths.heartbeat.exists():
            return True
        heartbeat = _read_json_object(self.paths.heartbeat)
        return _is_timestamp_stale(heartbeat.get("updated_at"), now, self.stale_seconds)

    def _recover_phase(self, phase_path: Path, phase_data: dict[str, Any]) -> dict[str, Any] | None:
        execution_attempt_id = str(phase_data.get("execution_attempt_id") or "")
        if not execution_attempt_id:
            raise ValidationError(f"phase file has no execution_attempt_id: {phase_path}")
        result_path = self.paths.results / f"{execution_attempt_id}.result.json"
        if result_path.exists() or str(phase_data.get("phase") or "") == "RESULT_WRITTEN":
            return None
        request_path = self.paths.working / f"{execution_attempt_id}.request.json"
        request_data = _read_json_object(request_path)
        phase_data.setdefault("request_file_sha256", hashlib.sha256(request_path.read_bytes()).hexdigest())
        phase = str(phase_data.get("phase") or "CLAIMED")
        if phase == "VERIFIED" and isinstance(phase_data.get("result_snapshot"), dict):
            return self._write_result(dict(phase_data["result_snapshot"]), execution_attempt_id)
        return self._write_recovery_result(request_data, phase_data, phase=phase)

    def _write_recovery_result(
        self,
        request: dict[str, Any],
        phase_data: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        execution_mode = str(request.get("execution_mode") or "")
        has_submit_risk = phase in SUBMIT_PHASES or phase == "VERIFIED" or (
            execution_mode == EXECUTION_MODE_COMMIT and str(phase_data.get("side_effect_state") or "") != SIDE_EFFECT_NOT_STARTED
        )
        cleanup_confirmed = bool(phase_data.get("cleanup_confirmed", False))
        not_applied_evidence = (
            str(phase_data.get("side_effect_state") or "") == SIDE_EFFECT_NOT_APPLIED
            and bool(phase_data.get("evidence_hash"))
            and (phase != "TARGET_FILLED" or cleanup_confirmed)
        )
        if has_submit_risk:
            result_status = STATUS_SIDE_EFFECT_UNKNOWN
            side_effect_state = SIDE_EFFECT_UNKNOWN
        elif execution_mode == EXECUTION_MODE_COMMIT and not not_applied_evidence:
            result_status = STATUS_START_UNKNOWN
            side_effect_state = SIDE_EFFECT_NOT_STARTED
        else:
            result_status = STATUS_FAILED
            side_effect_state = SIDE_EFFECT_NOT_APPLIED if not_applied_evidence else SIDE_EFFECT_NOT_STARTED
        retryable = execution_mode != EXECUTION_MODE_COMMIT and phase in PRE_SUBMIT_PHASES
        result = {
            "schema_version": "shadowbot-result-1.0",
            "task_id": request.get("task_id", ""),
            "operation_id": request.get("operation_id", ""),
            "execution_attempt_id": request.get("execution_attempt_id", ""),
            "execution_mode": execution_mode,
            "instruction_hash": request.get("instruction_hash", ""),
            "request_file_sha256": phase_data.get("request_file_sha256", ""),
            "lease_owner_token": request.get("lease_owner_token", ""),
            "lease_version": request.get("lease_version", 0),
            "worker_id": phase_data.get("worker_id", ""),
            "status": result_status,
            "run_success_flag": None if result_status in {STATUS_START_UNKNOWN, STATUS_SIDE_EFFECT_UNKNOWN} else False,
            "business_operation_completed": None if result_status in {STATUS_START_UNKNOWN, STATUS_SIDE_EFFECT_UNKNOWN} else False,
            "side_effect_state": side_effect_state,
            "error_code": "SUBMIT_RESULT_UNKNOWN" if has_submit_risk else "WORKER_INTERRUPTED",
            "error_message": f"stale ShadowBot working attempt recovered at phase {phase}",
            "retryable": retryable,
            "recovered_phase": phase,
            "ended_at": datetime.now(UTC).isoformat(),
        }
        return self._write_result(result, str(request.get("execution_attempt_id") or ""))

    def _write_result(self, result: dict[str, Any], execution_attempt_id: str) -> dict[str, Any]:
        if not execution_attempt_id:
            raise ValidationError("cannot recover working attempt without execution_attempt_id.")
        if not result.get("result_id"):
            identity = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            result["result_id"] = "RESULT-" + hashlib.sha256(identity).hexdigest()[:24]
        result_path = self.paths.results / f"{execution_attempt_id}.result.json"
        content = _json_bytes(result)
        _atomic_write(result_path.with_suffix(result_path.suffix + ".sha256"), (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"))
        _atomic_write(result_path, content)
        return {
            "status": "RECOVERY_RESULT_WRITTEN",
            "execution_attempt_id": execution_attempt_id,
            "result_path": str(result_path),
        }


def _verify_checksum(path: Path, content: bytes) -> None:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if not checksum_path.exists():
        raise ValidationError(f"checksum file is missing: {checksum_path}")
    expected = checksum_path.read_text(encoding="ascii").strip().lower()
    actual = hashlib.sha256(content).hexdigest()
    if expected != actual:
        raise ValidationError(f"checksum mismatch: {path}")


def _automatic_reconcile_payload(request: dict[str, Any]) -> dict[str, Any]:
    """Carry verified execution context into an Executor-owned RECONCILE request."""
    return {
        field_name: value
        for field_name in ("evidence_share_dir", "applet_uri", "window_title")
        if (value := request.get(field_name)) not in (None, "")
    }


def _read_json_object(path: Path, *, attempts: int = 8) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(attempts, 1)):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValidationError(f"JSON file must contain an object: {path}")
            return data
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.025 * (2**attempt), 0.4))
                continue
            raise
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.025 * (2**attempt), 0.4))
                continue
            raise
    raise RuntimeError(f"cannot read queue JSON {path}: {last_error}")


def _is_timestamp_stale(value: Any, now: datetime, stale_seconds: int) -> bool:
    if not value:
        return True
    try:
        updated = datetime.fromisoformat(str(value))
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (now - updated.astimezone(UTC)).total_seconds() > stale_seconds


def _is_file_stale(path: Path, now: datetime, stale_seconds: int) -> bool:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return (now - modified).total_seconds() > stale_seconds


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the staging filename short.  A long destination plus the previous
    # ``<name>.tmp-<uuid>`` suffix can exceed Windows MAX_PATH before the
    # final replace, even though the destination itself is valid.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
