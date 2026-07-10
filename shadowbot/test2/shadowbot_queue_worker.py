from __future__ import annotations

import hashlib
import json
import msvcrt
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


INSTRUCTION_HASH_FIELDS = (
    "task_id",
    "operation_id",
    "execution_attempt_id",
    "execution_mode",
    "platform_name",
    "platform_sku",
    "product_keyword",
    "expected_product_name",
    "expected_grade",
    "expected_spec",
    "spec_verification_required",
    "expected_old_price",
    "target_price",
    "applet_uri",
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path, content, max_attempts=8):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        for attempt in range(max(int(max_attempts), 1)):
            try:
                os.replace(str(temporary), str(path))
                return
            except OSError as exc:
                retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33)
                if not retryable or attempt + 1 >= max_attempts:
                    raise
                time.sleep(min(0.05 * (2 ** attempt), 0.5))
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def _instruction_hash(payload):
    canonical = {name: payload.get(name, "") for name in INSTRUCTION_HASH_FIELDS}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_config(args):
    config_path = Path(__file__).with_name("shadowbot_worker_config.json")
    config = {}
    if config_path.exists():
        loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, dict):
            config.update(loaded)
    if args:
        try:
            config.update({key: value for key, value in args.items() if value not in (None, "")})
        except AttributeError:
            pass
    config.setdefault("queue_dir", os.environ.get("SHADOWBOT_QUEUE_DIR", r"D:\PRA_Runtime\shadowbot_queue"))
    config.setdefault("poll_seconds", int(os.environ.get("SHADOWBOT_WORKER_POLL_SECONDS", "3")))
    config.setdefault("max_hours", int(os.environ.get("SHADOWBOT_WORKER_MAX_HOURS", "8")))
    config.setdefault("max_tasks", int(os.environ.get("SHADOWBOT_WORKER_MAX_TASKS", "50")))
    config.setdefault("heartbeat_seconds", 5)
    config.setdefault("allow_fault_injection", False)
    return config


class QueueWorker:
    def __init__(self, config):
        self.root = Path(str(config["queue_dir"]))
        self.poll_seconds = max(float(config["poll_seconds"]), 0.2)
        self.max_hours = max(float(config["max_hours"]), 0.1)
        self.max_tasks = max(int(config["max_tasks"]), 1)
        self.heartbeat_seconds = max(float(config["heartbeat_seconds"]), 1.0)
        self.worker_id = socket.gethostname()
        self.inbox = self.root / "inbox"
        self.working = self.root / "working"
        self.results = self.root / "results"
        self.archive = self.root / "archive"
        self.quarantine = self.root / "quarantine"
        self.evidence = self.root / "evidence"
        self.control = self.root / "control"
        self.heartbeat = self.root / "heartbeat.json"
        self.heartbeat_error_log = self.control / "heartbeat_errors.jsonl"
        self.stop_signal = self.control / "stop.signal"
        self.allow_fault_injection = bool(config.get("allow_fault_injection", False))
        self._stop_heartbeat = threading.Event()
        self._heartbeat_write_failures = 0
        self._heartbeat_consecutive_failures = 0
        self._heartbeat_last_error = ""
        self._heartbeat_last_error_at = ""
        self._heartbeat_thread_restarts = 0
        for path in (self.inbox, self.working, self.results, self.archive, self.quarantine, self.evidence, self.control):
            path.mkdir(parents=True, exist_ok=True)

    def run(self):
        started_at = datetime.now(timezone.utc)
        processed = 0
        lock_path = self.control / "worker.lock"
        with lock_path.open("a+b") as lock_file:
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return {"status": "ALREADY_RUNNING", "worker_id": self.worker_id}
            heartbeat_thread = self._start_heartbeat_thread()
            try:
                while processed < self.max_tasks:
                    if not heartbeat_thread.is_alive() and not self._stop_heartbeat.is_set():
                        self._heartbeat_thread_restarts += 1
                        heartbeat_thread = self._start_heartbeat_thread()
                    if datetime.now(timezone.utc) - started_at >= timedelta(hours=self.max_hours):
                        break
                    if self.stop_signal.exists() and not list(self.working.glob("*.request.json")):
                        break
                    if list(self.working.glob("*.request.json")):
                        time.sleep(self.poll_seconds)
                        continue
                    claimed = self._claim_next()
                    if claimed is None:
                        time.sleep(self.poll_seconds)
                        continue
                    self._execute_claimed(*claimed)
                    processed += 1
            finally:
                self._stop_heartbeat.set()
                heartbeat_thread.join(timeout=self.heartbeat_seconds + 1)
                try:
                    self._write_heartbeat("STOPPED", processed)
                except Exception as exc:
                    self._record_heartbeat_error(exc)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        return {"status": "STOPPED", "worker_id": self.worker_id, "processed": processed}

    def _start_heartbeat_thread(self):
        thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name="shadowbot-heartbeat")
        thread.start()
        return thread

    def _heartbeat_loop(self):
        while not self._stop_heartbeat.is_set():
            try:
                self._write_heartbeat("RUNNING", None)
            except Exception as exc:
                self._record_heartbeat_error(exc)
            self._stop_heartbeat.wait(self.heartbeat_seconds)

    def _write_heartbeat(self, status, processed):
        payload = {
            "worker_id": self.worker_id,
            "status": status,
            "processed": processed,
            "updated_at": _now_iso(),
            "heartbeat_write_failures": self._heartbeat_write_failures,
            "heartbeat_consecutive_failures": self._heartbeat_consecutive_failures,
            "heartbeat_last_error": self._heartbeat_last_error,
            "heartbeat_last_error_at": self._heartbeat_last_error_at,
            "heartbeat_thread_restarts": self._heartbeat_thread_restarts,
        }
        _atomic_write(self.heartbeat, _json_bytes(payload))
        self._heartbeat_consecutive_failures = 0

    def _record_heartbeat_error(self, exc):
        self._heartbeat_write_failures += 1
        self._heartbeat_consecutive_failures += 1
        self._heartbeat_last_error = "%s: %s" % (type(exc).__name__, str(exc))
        self._heartbeat_last_error_at = _now_iso()
        event = {
            "worker_id": self.worker_id,
            "error": self._heartbeat_last_error,
            "occurred_at": self._heartbeat_last_error_at,
            "write_failures": self._heartbeat_write_failures,
            "consecutive_failures": self._heartbeat_consecutive_failures,
        }
        try:
            with self.heartbeat_error_log.open("a", encoding="utf-8") as file_obj:
                file_obj.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                file_obj.flush()
        except Exception:
            pass

    def _claim_next(self):
        for request_path in sorted(self.inbox.glob("*.ready.json")):
            checksum_path = request_path.with_suffix(request_path.suffix + ".sha256")
            request = None
            request_sha256 = ""
            try:
                request_bytes = request_path.read_bytes()
                request_sha256 = hashlib.sha256(request_bytes).hexdigest()
                if not checksum_path.exists() or checksum_path.read_text(encoding="ascii").strip().lower() != request_sha256:
                    raise ValueError("request checksum mismatch")
                request = json.loads(request_bytes.decode("utf-8-sig"))
                self._validate_request(request)
                attempt_id = str(request["execution_attempt_id"])
                working_request = self.working / (attempt_id + ".request.json")
                working_checksum = working_request.with_suffix(working_request.suffix + ".sha256")
                os.replace(str(request_path), str(working_request))
                os.replace(str(checksum_path), str(working_checksum))
                phase_path = self.working / (attempt_id + ".phase.json")
                self._write_phase(request, phase_path, "CLAIMED", "NOT_STARTED", request_sha256)
                return request, request_sha256, working_request, phase_path
            except Exception as exc:
                if str(exc) == "request expired" and isinstance(request, dict) and request_sha256:
                    self._write_rejected_request_result(
                        request,
                        request_sha256,
                        request_path,
                        checksum_path,
                        error_code="REQUEST_EXPIRED",
                        error_message=str(exc),
                    )
                else:
                    self._quarantine_request(request_path, checksum_path, str(exc))
        return None

    def _validate_request(self, request):
        required = (
            "task_id",
            "operation_id",
            "execution_attempt_id",
            "execution_mode",
            "platform_name",
            "platform_sku",
            "product_keyword",
            "expected_product_name",
            "expected_grade",
            "expected_old_price",
            "target_price",
            "instruction_hash",
            "expires_at",
        )
        missing = [name for name in required if not str(request.get(name) or "").strip()]
        if missing:
            raise ValueError("missing request fields: " + ", ".join(missing))
        if str(request.get("fault_injection") or "").strip() and not self.allow_fault_injection:
            raise ValueError("UNSAFE_TEST_PARAMETER_REJECTED")
        if bool(request.get("spec_verification_required")):
            raise ValueError("current platform adapter cannot verify expected_spec")
        if request["instruction_hash"] != _instruction_hash(request):
            raise ValueError("instruction_hash mismatch")
        expires_at = datetime.fromisoformat(str(request["expires_at"]))
        if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("request expired")

    def _execute_claimed(self, request, request_sha256, working_request, phase_path):
        attempt_id = str(request["execution_attempt_id"])
        request = dict(request)
        request.update(
            {
                "request_file_sha256": request_sha256,
                "worker_id": self.worker_id,
                "_phase_file_path": str(phase_path),
                "_stop_signal_path": str(self.stop_signal),
            }
        )
        try:
            if __package__:
                from . import vertical_slice_read_price
            else:
                import vertical_slice_read_price

            raw_result = vertical_slice_read_price.main({"request_json": json.dumps(request, ensure_ascii=False)})
            result = json.loads(raw_result) if isinstance(raw_result, str) else dict(raw_result)
        except Exception as exc:
            result = {
                "status": "FAILED",
                "run_success_flag": False,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": "WORKER_EXECUTION_FAILED",
                "error_message": str(exc),
                "retryable": False,
            }
        result.update(
            {
                "schema_version": "shadowbot-result-1.0",
                "task_id": request["task_id"],
                "operation_id": request["operation_id"],
                "execution_attempt_id": attempt_id,
                "execution_mode": request["execution_mode"],
                "instruction_hash": request["instruction_hash"],
                "request_file_sha256": request_sha256,
                "worker_id": self.worker_id,
                "queue_phase": "RESULT_WRITTEN",
                "worker_heartbeat_at": _now_iso(),
            }
        )
        result_path = self.results / (attempt_id + ".result.json")
        content = _json_bytes(result)
        _atomic_write(result_path.with_suffix(result_path.suffix + ".sha256"), (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"))
        _atomic_write(result_path, content)
        self._write_phase(request, phase_path, "RESULT_WRITTEN", str(result.get("side_effect_state") or "NOT_STARTED"), request_sha256)

    def _write_phase(self, request, phase_path, phase, side_effect_state, request_sha256):
        payload = {
            "task_id": request.get("task_id", ""),
            "operation_id": request.get("operation_id", ""),
            "execution_attempt_id": request.get("execution_attempt_id", ""),
            "execution_mode": request.get("execution_mode", ""),
            "phase": phase,
            "side_effect_state": side_effect_state,
            "request_file_sha256": request_sha256,
            "instruction_hash": request.get("instruction_hash", ""),
            "worker_id": self.worker_id,
            "updated_at": _now_iso(),
        }
        _atomic_write(phase_path, _json_bytes(payload))

    def _write_rejected_request_result(
        self,
        request,
        request_sha256,
        request_path,
        checksum_path,
        error_code,
        error_message,
    ):
        attempt_id = str(request["execution_attempt_id"])
        working_request = self.working / (attempt_id + ".request.json")
        working_checksum = working_request.with_suffix(working_request.suffix + ".sha256")
        os.replace(str(request_path), str(working_request))
        os.replace(str(checksum_path), str(working_checksum))
        phase_path = self.working / (attempt_id + ".phase.json")
        result = {
            "schema_version": "shadowbot-result-1.0",
            "task_id": request["task_id"],
            "operation_id": request["operation_id"],
            "execution_attempt_id": attempt_id,
            "execution_mode": request["execution_mode"],
            "instruction_hash": request["instruction_hash"],
            "request_file_sha256": request_sha256,
            "worker_id": self.worker_id,
            "status": "FAILED",
            "run_success_flag": False,
            "business_operation_completed": False,
            "side_effect_state": "NOT_STARTED",
            "error_code": error_code,
            "error_message": error_message,
            "retryable": False,
            "queue_phase": "RESULT_WRITTEN",
            "worker_heartbeat_at": _now_iso(),
        }
        result_path = self.results / (attempt_id + ".result.json")
        content = _json_bytes(result)
        _atomic_write(
            result_path.with_suffix(result_path.suffix + ".sha256"),
            (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"),
        )
        _atomic_write(result_path, content)
        self._write_phase(request, phase_path, "RESULT_WRITTEN", "NOT_STARTED", request_sha256)

    def _quarantine_request(self, request_path, checksum_path, error):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if request_path.exists():
            os.replace(str(request_path), str(self.quarantine / (stamp + "-" + request_path.name)))
        if checksum_path.exists():
            os.replace(str(checksum_path), str(self.quarantine / (stamp + "-" + checksum_path.name)))
        error_path = self.quarantine / (stamp + "-request-error.json")
        _atomic_write(error_path, _json_bytes({"error_code": "INPUT_INVALID", "error_message": error}))


def main(args):
    result = QueueWorker(_load_config(args)).run()
    result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if args is not None:
        try:
            args["result_json"] = result_json
        except Exception:
            pass
    print(result_json)
    return result_json
