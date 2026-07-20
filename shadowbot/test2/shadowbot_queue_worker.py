from __future__ import annotations

import hashlib
import json
import msvcrt
import os
import socket
import threading
import time
import uuid
import unicodedata
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "CREDENTIAL_TARGET_MISSING",
        "CREDENTIAL_MANAGER_UNAVAILABLE",
        "CREDENTIAL_NOT_FOUND",
        "CREDENTIAL_ACCESS_DENIED",
        "CREDENTIAL_FORMAT_INVALID",
        "CREDENTIAL_READ_FAILED",
    }
)


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

TASK12_INSTRUCTION_HASH_FIELDS = (
    "batch_contract_version",
    "price_batch_id",
    "price_batch_item_id",
    "price_batch_ordinal",
    "price_batch_stage",
    "batch_execution_mode",
    "normalized_request_digest",
    "source_read_batch_id",
    "source_snapshot_sha256",
    "source_page_context_sha256",
    "page_identity_key",
    "write_identity_key",
    "fresh_read_attempt_id",
    "fresh_read_result_sha256",
    "fresh_old_price",
    "approved_payload_hash",
    "approval_id",
    "expires_at",
    "capture_evidence",
)

V2_CONTRACT_VERSION = 2
V2_DEFAULT_LIMITS = {"max_pages": 20, "max_scrolls": 100, "max_seconds": 300}
V2_HARD_LIMITS = {"max_pages": 100, "max_scrolls": 500, "max_seconds": 900}
V2_HARD_MAX_PRODUCTS = 50
V2_MAX_REQUEST_BYTES = 256 * 1024
V2_MAX_RESULT_BYTES = 4 * 1024 * 1024
_V2_WHITESPACE_RE = re.compile(r"\s+")


def _v2_normalize_text(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return _V2_WHITESPACE_RE.sub(" ", value).strip().casefold()


def _v2_normalize_sku(value):
    value = _v2_normalize_text(value)
    return value.upper() if value else None


def _v2_normalize_request(request):
    if request.get("contract_version") != V2_CONTRACT_VERSION:
        raise ValueError("UNKNOWN_CONTRACT_VERSION")
    if str(request.get("execution_mode") or "").strip().upper() != "READ_ONLY":
        raise ValueError("READ_ONLY_REQUIRED")
    read_batch_id = str(request.get("read_batch_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", read_batch_id):
        raise ValueError("invalid read_batch_id")
    products = request.get("products")
    if not isinstance(products, list) or not products or len(products) > V2_HARD_MAX_PRODUCTS:
        raise ValueError("PRODUCT_COUNT_LIMIT_EXCEEDED")
    identities = set()
    item_ids = set()
    platforms = set()
    normalized_products = []
    for product in products:
        if not isinstance(product, dict):
            raise ValueError("invalid product target")
        item_id = str(product.get("item_id") or "").strip()
        platform = str(product.get("platform") or "").strip()
        name = str(product.get("expected_product_name") or "").strip()
        grade = str(product.get("expected_grade") or "").strip()
        if not item_id or not platform or not name or not grade or item_id in item_ids:
            raise ValueError("invalid or duplicate product target")
        sku = _v2_normalize_sku(product.get("platform_sku"))
        identity = (
            f"{_v2_normalize_text(platform)}|sku:{sku}"
            if sku
            else f"{_v2_normalize_text(platform)}|name:{_v2_normalize_text(name)}|grade:{_v2_normalize_text(grade).upper()}"
        )
        if identity in identities:
            raise ValueError("DUPLICATE_TARGET_IDENTITY")
        item_ids.add(item_id)
        identities.add(identity)
        platforms.add(_v2_normalize_text(platform))
        normalized_products.append(
            {
                "item_id": item_id,
                "platform": platform,
                "platform_sku": sku,
                "expected_product_name": name,
                "expected_grade": grade,
            }
        )
    if len(platforms) != 1:
        raise ValueError("SINGLE_PLATFORM_REQUIRED")
    platform_name = str(request.get("platform_name") or "").strip()
    if platform_name and _v2_normalize_text(platform_name) not in platforms:
        raise ValueError("SINGLE_PLATFORM_REQUIRED")
    raw_limits = request.get("limits") or {}
    if not isinstance(raw_limits, dict):
        raise ValueError("invalid limits")
    limits = {}
    for name, default in V2_DEFAULT_LIMITS.items():
        raw_value = raw_limits.get(name, default)
        if isinstance(raw_value, bool) or (isinstance(raw_value, float) and not raw_value.is_integer()):
            raise ValueError(f"{name} must be an integer")
        value = int(raw_value)
        if not 1 <= value <= V2_HARD_LIMITS[name]:
            raise ValueError(f"{name.upper()}_LIMIT_EXCEEDED")
        limits[name] = value
    return {
        "contract_version": V2_CONTRACT_VERSION,
        "execution_mode": "READ_ONLY",
        "read_batch_id": read_batch_id,
        "capture_evidence": _as_bool(request.get("capture_evidence", False)),
        "products": normalized_products,
        "limits": limits,
    }


def _v2_instruction_hash(request):
    canonical = {
        "task_id": str(request.get("task_id") or ""),
        "operation_id": str(request.get("operation_id") or ""),
        "execution_attempt_id": str(request.get("execution_attempt_id") or ""),
        "request": _v2_normalize_request(request),
        "applet_uri": str(request.get("applet_uri") or ""),
        "window_title": str(request.get("window_title") or ""),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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
    fields = INSTRUCTION_HASH_FIELDS
    if payload.get("batch_contract_version") == 3:
        fields = INSTRUCTION_HASH_FIELDS + TASK12_INSTRUCTION_HASH_FIELDS
    canonical = {name: payload.get(name, "") for name in fields}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _task12_binding(payload):
    if payload.get("batch_contract_version") != 3:
        return {}
    return {
        name: payload.get(name, "")
        for name in TASK12_INSTRUCTION_HASH_FIELDS
    }


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


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
    config.setdefault("login_auto_enabled", True)
    # Credential targets are machine-local identifiers.  Keep the repository
    # default empty so a deployment must configure its own target explicitly.
    config.setdefault("login_credential_target", "")
    config.setdefault("login_employee_mode_required", True)
    config.setdefault("login_employee_mode_selector", "登录页_员工按钮")
    config.setdefault("login_employee_mode_wait_seconds", 1)
    config.setdefault("login_account_selector", "登录页_账号输入框")
    config.setdefault("login_password_selector", "登录页_密码输入框")
    config.setdefault("login_submit_selector", "登录页_登录按钮")
    config.setdefault("login_verification_wait_seconds", 600)
    config.setdefault("login_post_submit_wait_seconds", 8)
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
        self.login_config = {
            "auto_enabled": _as_bool(config.get("login_auto_enabled", True)),
            "employee_mode_required": _as_bool(config.get("login_employee_mode_required", True)),
            "employee_mode_selector": str(config.get("login_employee_mode_selector") or "").strip(),
            "employee_mode_wait_seconds": max(float(config.get("login_employee_mode_wait_seconds", 1)), 0.0),
            "account_selector": str(config.get("login_account_selector") or "").strip(),
            "password_selector": str(config.get("login_password_selector") or "").strip(),
            "submit_selector": str(config.get("login_submit_selector") or "").strip(),
            "verification_wait_seconds": max(float(config.get("login_verification_wait_seconds", 600)), 1.0),
            "post_submit_wait_seconds": max(float(config.get("login_post_submit_wait_seconds", 8)), 1.0),
        }
        self.login_credential_target = str(config.get("login_credential_target") or "").strip()
        self.credential_provider_error_code = ""
        self.credential_provider = self._build_credential_provider()
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
        if request.get("contract_version") == V2_CONTRACT_VERSION:
            self._validate_multi_product_request(request)
            return
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
        if request.get("batch_contract_version") == 3:
            self._validate_task12_item_request(request)
        expires_at = datetime.fromisoformat(str(request["expires_at"]))
        if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("request expired")

    def _validate_task12_item_request(self, request):
        if len(_json_bytes(request)) > V2_MAX_REQUEST_BYTES:
            raise ValueError("REQUEST_SIZE_LIMIT_EXCEEDED")
        required = (
            "price_batch_id",
            "price_batch_item_id",
            "price_batch_ordinal",
            "price_batch_stage",
            "batch_execution_mode",
            "normalized_request_digest",
            "source_read_batch_id",
            "source_snapshot_sha256",
            "source_page_context_sha256",
            "page_identity_key",
            "write_identity_key",
        )
        stage = str(request.get("price_batch_stage") or "").strip().upper()
        batch_mode = str(request.get("batch_execution_mode") or "").strip().upper()
        if stage == "FRESH_READ" or batch_mode == "FILL_PREVIEW":
            required += ("fresh_read_attempt_id",)
        missing = [name for name in required if not str(request.get(name) or "").strip()]
        if request.get("batch_contract_version") == 3:
            missing = [name for name in missing if name != "platform_sku"]
        if missing:
            raise ValueError("missing task 12 item fields: " + ", ".join(missing))
        ordinal = request.get("price_batch_ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("INVALID_ORDINAL")
        for name in (
            "normalized_request_digest",
            "source_snapshot_sha256",
            "source_page_context_sha256",
            "page_identity_key",
            "write_identity_key",
            "approved_payload_hash",
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(request.get(name) or "")):
                raise ValueError("invalid task 12 hash: " + name)
        if not isinstance(request.get("capture_evidence"), bool):
            raise ValueError("capture_evidence must be boolean")
        mode = str(request.get("execution_mode") or "").strip().upper()
        if stage != "RECONCILE" and not str(request.get("approval_id") or "").strip():
            raise ValueError("task 12 approval_id is required")
        if batch_mode not in ("FILL_PREVIEW", "COMMIT"):
            raise ValueError("UNSUPPORTED_EXECUTION_MODE")
        if stage == "FRESH_READ":
            if mode != "READ_ONLY" or request.get("fresh_read_attempt_id") != request.get("execution_attempt_id"):
                raise ValueError("BATCH_ITEM_BINDING_MISMATCH")
            if str(request.get("fresh_read_result_sha256") or "") or str(request.get("fresh_old_price") or ""):
                raise ValueError("BATCH_ITEM_BINDING_MISMATCH")
        elif stage == "WRITE":
            if mode != batch_mode:
                raise ValueError("BATCH_ITEM_BINDING_MISMATCH")
            fresh_values = (
                str(request.get("fresh_read_attempt_id") or ""),
                str(request.get("fresh_read_result_sha256") or ""),
                str(request.get("fresh_old_price") or ""),
            )
            if batch_mode == "FILL_PREVIEW" or any(fresh_values):
                if not all(fresh_values) or not re.fullmatch(r"sha256:[0-9a-f]{64}", fresh_values[1]):
                    raise ValueError("BATCH_ITEM_BINDING_MISMATCH")
                if fresh_values[2] != str(request.get("expected_old_price") or ""):
                    raise ValueError("OLD_PRICE_CHANGED")
        elif stage == "RECONCILE":
            if mode != "RECONCILE":
                raise ValueError("BATCH_ITEM_BINDING_MISMATCH")
        else:
            raise ValueError("BATCH_ITEM_BINDING_MISMATCH")

    def _validate_multi_product_request(self, request):
        if len(_json_bytes(request)) > V2_MAX_REQUEST_BYTES:
            raise ValueError("REQUEST_SIZE_LIMIT_EXCEEDED")
        _v2_normalize_request(request)
        required = ("task_id", "operation_id", "execution_attempt_id", "created_at", "expires_at", "instruction_hash")
        missing = [name for name in required if not str(request.get(name) or "").strip()]
        if missing:
            raise ValueError("missing v2 request fields: " + ", ".join(missing))
        if request["instruction_hash"] != _v2_instruction_hash(request):
            raise ValueError("multi-product instruction_hash mismatch")
        expires_at = datetime.fromisoformat(str(request["expires_at"]))
        if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("request expired")

    def _execute_claimed(self, request, request_sha256, working_request, phase_path):
        attempt_id = str(request["execution_attempt_id"])
        runtime_request = dict(request)
        runtime_request.update(
            {
                "request_file_sha256": request_sha256,
                "lease_owner_token": request.get("lease_owner_token", ""),
                "lease_version": request.get("lease_version", 0),
                "worker_id": self.worker_id,
                "_phase_file_path": str(phase_path),
                "_stop_signal_path": str(self.stop_signal),
                "_provider_error_code": self.credential_provider_error_code,
            }
        )
        # Keep runtime-only objects (provider/config) out of request_json.  The
        # two underscore-prefixed paths are safe control metadata consumed by
        # the flow for phase writes and stop checks.
        runtime_request = {
            key: value
            for key, value in runtime_request.items()
            if not key.startswith("_")
            or key in {"_phase_file_path", "_stop_signal_path", "_provider_error_code"}
        }
        try:
            if __package__:
                from . import vertical_slice_read_price
            else:
                import vertical_slice_read_price

            raw_result = vertical_slice_read_price.main(
                {
                    "request_json": json.dumps(runtime_request, ensure_ascii=False),
                    "_credential_provider": self.credential_provider,
                    "_login_config": self.login_config,
                }
            )
            result = json.loads(raw_result) if isinstance(raw_result, str) else dict(raw_result)
        except Exception as exc:
            result = {
                "status": "FAILED",
                "run_success_flag": False,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": "BATCH_STOPPED" if request.get("contract_version") == V2_CONTRACT_VERSION else "WORKER_EXECUTION_FAILED",
                # A lower-level UI exception can echo the text that was passed
                # to a credential field. Keep queue results free of secrets.
                "error_message": "worker execution failed: " + type(exc).__name__,
                "retryable": False,
            }
        provider_error_code = str(result.get("provider_error_code") or "").strip()
        if provider_error_code in SAFE_PROVIDER_ERROR_CODES:
            result["provider_error_code"] = provider_error_code
        else:
            result.pop("provider_error_code", None)
        result.update(
            {
                "schema_version": "shadowbot-result-2.0" if request.get("contract_version") == V2_CONTRACT_VERSION else "shadowbot-result-1.0",
                "task_id": request["task_id"],
                "operation_id": request["operation_id"],
                "execution_attempt_id": attempt_id,
                "execution_mode": request["execution_mode"],
                "instruction_hash": request["instruction_hash"],
                "request_file_sha256": request_sha256,
                "worker_id": self.worker_id,
                "queue_phase": "RESULT_WRITTEN",
                "worker_heartbeat_at": _now_iso(),
                **_task12_binding(request),
            }
        )
        if request.get("contract_version") == V2_CONTRACT_VERSION:
            result.setdefault("contract_version", V2_CONTRACT_VERSION)
            result.setdefault("read_batch_id", request.get("read_batch_id", ""))
            result.setdefault("total_count", len(request.get("products") or []))
        if not result.get("result_id"):
            result_identity = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            result["result_id"] = "RESULT-" + hashlib.sha256(result_identity).hexdigest()[:24]
        result_path = self.results / (attempt_id + ".result.json")
        content = _json_bytes(result)
        if request.get("contract_version") == V2_CONTRACT_VERSION and len(content) > V2_MAX_RESULT_BYTES:
            result = {
                "schema_version": "shadowbot-result-2.0",
                "contract_version": V2_CONTRACT_VERSION,
                "task_id": request["task_id"],
                "operation_id": request["operation_id"],
                "execution_attempt_id": attempt_id,
                "execution_mode": request["execution_mode"],
                "read_batch_id": request.get("read_batch_id", ""),
                "instruction_hash": request.get("instruction_hash", ""),
                "request_file_sha256": request_sha256,
                "worker_id": self.worker_id,
                "queue_phase": "RESULT_WRITTEN",
                "worker_heartbeat_at": _now_iso(),
                "status": "FAILED",
                "run_success_flag": False,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": "BATCH_STOPPED",
                "error_message": "result exceeds 4 MiB contract limit",
                "retryable": False,
                "product_snapshots": [],
            }
            result["result_id"] = "RESULT-" + hashlib.sha256(_json_bytes(result)).hexdigest()[:24]
            content = _json_bytes(result)
        if request.get("batch_contract_version") == 3 and len(content) > V2_MAX_RESULT_BYTES:
            result = {
                "schema_version": "shadowbot-result-1.0",
                "task_id": request["task_id"],
                "operation_id": request["operation_id"],
                "execution_attempt_id": attempt_id,
                "execution_mode": request["execution_mode"],
                "instruction_hash": request.get("instruction_hash", ""),
                "request_file_sha256": request_sha256,
                "worker_id": self.worker_id,
                "queue_phase": "RESULT_WRITTEN",
                "worker_heartbeat_at": _now_iso(),
                "status": "FAILED",
                "run_success_flag": False,
                "business_operation_completed": False,
                "side_effect_state": "NOT_STARTED",
                "error_code": "RESULT_TOO_LARGE",
                "error_message": "result exceeds 4 MiB contract limit",
                "retryable": False,
                **_task12_binding(request),
            }
            result["result_id"] = "RESULT-" + hashlib.sha256(_json_bytes(result)).hexdigest()[:24]
            content = _json_bytes(result)
        _atomic_write(result_path.with_suffix(result_path.suffix + ".sha256"), (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"))
        _atomic_write(result_path, content)
        self._write_phase(request, phase_path, "RESULT_WRITTEN", str(result.get("side_effect_state") or "NOT_STARTED"), request_sha256)

    def _build_credential_provider(self):
        try:
            if __package__:
                from .shadowbot_credentials import WindowsCredentialManagerProvider
            else:
                from shadowbot_credentials import WindowsCredentialManagerProvider
            return WindowsCredentialManagerProvider(self.login_credential_target)
        except Exception as exc:
            provider_error_code = str(getattr(exc, "error_code", "") or "").strip()
            if provider_error_code in SAFE_PROVIDER_ERROR_CODES:
                self.credential_provider_error_code = provider_error_code
            return None

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
            "lease_owner_token": request.get("lease_owner_token", ""),
            "lease_version": request.get("lease_version", 0),
            "updated_at": _now_iso(),
            **_task12_binding(request),
        }
        if request.get("contract_version") == V2_CONTRACT_VERSION:
            payload.update(
                {
                    "contract_version": V2_CONTRACT_VERSION,
                    "read_batch_id": request.get("read_batch_id", ""),
                    "total_count": len(request.get("products") or []),
                }
            )
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
            "lease_owner_token": request.get("lease_owner_token", ""),
            "lease_version": request.get("lease_version", 0),
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
            **_task12_binding(request),
        }
        result_identity = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        result["result_id"] = "RESULT-" + hashlib.sha256(result_identity).hexdigest()[:24]
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
