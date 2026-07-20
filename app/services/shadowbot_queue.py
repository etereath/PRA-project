from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
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
    STATUS_NOT_APPLIED,
    STATUS_PREVIEW_COMPLETED,
    STATUS_READ_COMPLETED,
    STATUS_SIDE_EFFECT_UNKNOWN,
    STATUS_START_FAILED,
    STATUS_START_UNKNOWN,
    STATUS_VERIFIED,
    TASK12_INSTRUCTION_HASH_FIELDS,
    ShadowBotExecutor,
    ShadowBotTaskRunner,
    shadowbot_result_contract_from_data,
)
from app.services.shadowbot_price_batch import (
    MAX_RESULT_BYTES as PRICE_BATCH_MAX_RESULT_BYTES,
    PRICE_BATCH_ERROR_CODES,
    BatchItemStatus,
    PriceBatchContractError,
    PriceBatchErrorCode,
    normalize_price_string,
    aggregate_batch_counts,
)
from app.services.shadowbot_price_batch_orchestrator import ShadowBotPriceBatchOrchestrator
from app.services.shadowbot_product_read import (
    MAX_RESULT_BYTES,
    aggregate_product_snapshots,
    normalize_grade,
    normalize_multi_product_request,
    normalize_text,
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
        task12_context = self._validate_task12_result(
            request_data,
            data,
            result_bytes,
            execution_attempt_id=execution_attempt_id,
        )
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
        task12_projection = None
        if task12_context is not None:
            task12_projection = self._project_task12_result(
                task12_context,
                contract,
                result_file_sha256=result_file_sha256,
            )
        archive_dir = self._archive_attempt(contract.execution_attempt_id, request_path, result_path)
        event = {
            "status": "IMPORTED" if attempt.ended_at is None else "ALREADY_IMPORTED",
            "execution_attempt_id": contract.execution_attempt_id,
            "archive_dir": str(archive_dir),
        }
        if task12_projection is not None:
            event["price_batch"] = task12_projection
        return event

    def _validate_task12_result(
        self,
        request: dict[str, Any],
        result: dict[str, Any],
        result_bytes: bytes,
        *,
        execution_attempt_id: str,
    ) -> dict[str, Any] | None:
        if request.get("batch_contract_version") != 3:
            return None
        if len(result_bytes) > PRICE_BATCH_MAX_RESULT_BYTES:
            raise ValidationError("RESULT_TOO_LARGE: task 12 result exceeds 4 MiB.")
        if result.get("batch_contract_version") != 3:
            raise ValidationError("RESULT_CONTRACT_INVALID: task 12 contract version mismatch.")
        for name in TASK12_INSTRUCTION_HASH_FIELDS:
            if result.get(name) != request.get(name):
                raise ValidationError(f"RESULT_CONTRACT_INVALID: task 12 {name} mismatch.")
        for name in ("run_success_flag", "business_operation_completed"):
            value = result.get(name)
            if value is not None and not isinstance(value, bool):
                raise ValidationError(f"RESULT_CONTRACT_INVALID: {name} must be boolean or null.")
        if not isinstance(result.get("retryable"), bool):
            raise ValidationError("RESULT_CONTRACT_INVALID: retryable must be boolean.")

        batch_id = str(request.get("price_batch_id") or "").strip()
        item_id = str(request.get("price_batch_item_id") or "").strip()
        stage = str(request.get("price_batch_stage") or "").strip().upper()
        batch = self.repository.get_shadowbot_batch(batch_id)
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if batch is None or item is None:
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH: batch item does not exist.")
        expected_attempt_id = {
            "FRESH_READ": item.fresh_read_attempt_id,
            "WRITE": item.current_execution_attempt_id,
            "RECONCILE": item.reconcile_attempt_id,
        }.get(stage)
        if not expected_attempt_id or expected_attempt_id != execution_attempt_id:
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH: attempt is not bound to this item stage.")
        expected_batch = {
            "batch_execution_mode": batch.execution_mode,
            "normalized_request_digest": batch.normalized_request_digest,
            "source_read_batch_id": batch.source_read_batch_id,
            "source_snapshot_sha256": batch.source_snapshot_sha256,
            "source_page_context_sha256": batch.source_page_context_sha256,
            "capture_evidence": batch.capture_evidence,
        }
        expected_item = {
            "price_batch_ordinal": item.ordinal,
            "page_identity_key": item.page_identity_key,
            "write_identity_key": item.write_identity_key,
            "approved_payload_hash": item.approved_payload_hash,
        }
        for name, expected in {**expected_batch, **expected_item}.items():
            if request.get(name) != expected:
                raise ValidationError(f"BATCH_ITEM_BINDING_MISMATCH: persisted {name} differs.")
        if str(request.get("operation_id") or "") != item.operation_id:
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH: operation_id differs.")
        if str(request.get("task_id") or "") != item.task_id:
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH: task_id differs.")
        if stage != "RECONCILE" and str(request.get("approval_id") or "") != item.review_task_id:
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH: approval_id differs.")
        return {
            "batch": batch,
            "item": item,
            "request": request,
            "result": result,
            "stage": stage,
        }

    def _project_task12_result(
        self,
        context: dict[str, Any],
        contract,
        *,
        result_file_sha256: str,
    ) -> dict[str, Any]:
        batch = context["batch"]
        item = context["item"]
        result = context["result"]
        stage = context["stage"]
        orchestrator = ShadowBotPriceBatchOrchestrator(self.repository)
        result_hash = "sha256:" + result_file_sha256
        actual_price = _task12_optional_price(result.get("actual_price"))
        if contract.status in {STATUS_READ_COMPLETED, STATUS_PREVIEW_COMPLETED, STATUS_VERIFIED, STATUS_NOT_APPLIED}:
            if actual_price is None:
                raise ValidationError("RESULT_CONTRACT_INVALID: successful task 12 readback requires actual_price.")
            if contract.status != STATUS_NOT_APPLIED and str(result.get("error_code") or "").strip():
                raise ValidationError("RESULT_CONTRACT_INVALID: successful task 12 result cannot carry error_code.")
            if (
                normalize_text(result.get("product_name")) != normalize_text(item.expected_product_name)
                or normalize_grade(result.get("grade")) != normalize_grade(item.expected_grade)
            ):
                raise ValidationError("PRODUCT_IDENTITY_MISMATCH: result identity differs from batch item.")
        normalized_error, adapter_error = _task12_batch_error(
            str(result.get("error_code") or ""),
            side_effect_state=contract.side_effect_state,
        )
        error_message = str(result.get("error_message") or "").strip()
        if adapter_error and adapter_error != normalized_error:
            error_message = (f"adapter_error_code={adapter_error}; {error_message}").strip("; ")
        try:
            if stage == "FRESH_READ":
                if contract.status == STATUS_READ_COMPLETED:
                    orchestrator.record_fresh_read(
                        batch.batch_id,
                        item.item_id,
                        fresh_read_attempt_id=contract.execution_attempt_id,
                        result_sha256=result_hash,
                        observed_product_name=result.get("product_name"),
                        observed_grade=result.get("grade"),
                        observed_platform_sku=result.get("platform_sku"),
                        observed_price=result.get("actual_price"),
                        observed_at=result.get("observed_at"),
                    )
                else:
                    orchestrator.record_item_result(
                        batch.batch_id,
                        item.item_id,
                        status=BatchItemStatus.FAILED.value,
                        execution_attempt_id=contract.execution_attempt_id,
                        run_id=_attempt_run_id(self.repository, contract.execution_attempt_id),
                        error_code=normalized_error or PriceBatchErrorCode.PLATFORM_EXECUTION_FAILED.value,
                        error_message=error_message,
                        result_id=contract.result_id,
                        result_hash=result_hash,
                        stop_requested=normalized_error == PriceBatchErrorCode.WORKER_STOP_REQUESTED.value,
                    )
            elif stage == "WRITE":
                if contract.status == STATUS_PREVIEW_COMPLETED:
                    item_status = BatchItemStatus.PREVIEWED.value
                elif contract.status == STATUS_VERIFIED:
                    item_status = BatchItemStatus.VERIFIED.value
                elif contract.status in {STATUS_SIDE_EFFECT_UNKNOWN, STATUS_START_UNKNOWN} or contract.side_effect_state in {
                    "SUBMIT_INTENT_RECORDED",
                    "SUBMIT_CLICKED",
                    "UNKNOWN",
                }:
                    item_status = BatchItemStatus.NEEDS_RECONCILIATION.value
                    normalized_error = PriceBatchErrorCode.SUBMIT_RESULT_UNKNOWN.value
                elif contract.status in {STATUS_FAILED, STATUS_NOT_APPLIED, STATUS_START_FAILED}:
                    item_status = BatchItemStatus.FAILED.value
                else:
                    raise ValidationError("RESULT_CONTRACT_INVALID: unsupported task 12 write result status.")
                orchestrator.record_item_result(
                    batch.batch_id,
                    item.item_id,
                    status=item_status,
                    execution_attempt_id=contract.execution_attempt_id,
                    run_id=_attempt_run_id(self.repository, contract.execution_attempt_id),
                    post_commit_price=actual_price,
                    error_code=normalized_error,
                    error_message=error_message,
                    result_id=contract.result_id,
                    result_hash=result_hash,
                    stop_requested=normalized_error == PriceBatchErrorCode.WORKER_STOP_REQUESTED.value,
                )
            elif stage == "RECONCILE":
                if contract.status == STATUS_VERIFIED and actual_price == item.target_price:
                    outcome = "VERIFIED"
                elif (
                    contract.status in {STATUS_NOT_APPLIED, STATUS_FAILED}
                    and contract.side_effect_state == "NOT_APPLIED"
                    and actual_price in {item.fresh_old_price, item.approved_expected_old_price}
                ):
                    outcome = "NOT_APPLIED"
                else:
                    outcome = "UNCERTAIN"
                    normalized_error = PriceBatchErrorCode.SUBMIT_RESULT_UNKNOWN.value
                orchestrator.complete_reconcile(
                    batch.batch_id,
                    item.item_id,
                    reconcile_attempt_id=contract.execution_attempt_id,
                    outcome=outcome,
                    post_commit_price=actual_price,
                    error_code=normalized_error,
                    error_message=error_message,
                )
            else:
                raise ValidationError("RESULT_CONTRACT_INVALID: unsupported task 12 stage.")
        except PriceBatchContractError as exc:
            stored_after_error = self.repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
            if stored_after_error is None or stored_after_error.status == BatchItemStatus.RUNNING.value:
                raise
            normalized_error = stored_after_error.error_code or exc.code

        stored_batch = self.repository.get_shadowbot_batch(batch.batch_id)
        stored_item = self.repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
        assert stored_batch is not None and stored_item is not None
        counts = _validate_persisted_batch_counts(self.repository, batch.batch_id)
        return {
            "batch_id": batch.batch_id,
            "item_id": item.item_id,
            "stage": stage,
            "item_status": stored_item.status,
            "batch_status": stored_batch.status,
            "error_code": stored_item.error_code or normalized_error,
            "counts": counts,
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
                # Section 17: screenshots/evidence are diagnostic opt-ins, not
                # a production success prerequisite.  Preserve strict binding
                # validation whenever evidence is actually supplied.
                if evidence is None or evidence == []:
                    continue
                if not isinstance(evidence, list):
                    raise ValidationError("RESULT_CONTRACT_INVALID: evidence must be an array when present.")
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


_TASK12_ERROR_ALIASES = {
    "DUPLICATE_TARGET_IDENTITY": PriceBatchErrorCode.AMBIGUOUS_MATCH.value,
    "PRODUCT_MATCH_AMBIGUOUS": PriceBatchErrorCode.AMBIGUOUS_MATCH.value,
    "OLD_PRICE_PARSE_FAILED": PriceBatchErrorCode.CURRENT_PRICE_PARSE_FAILED.value,
    "FINAL_SAVE_NOT_FOUND": PriceBatchErrorCode.SUBMIT_RESULT_UNKNOWN.value,
    "POST_SUBMIT_PRICE_MISMATCH": PriceBatchErrorCode.SUBMIT_RESULT_UNKNOWN.value,
}

_TASK12_LIST_LOAD_ERRORS = frozenset(
    {
        "APPLET_URI_INVALID",
        "APPLET_URI_MISSING",
        "APPLET_URI_OPEN_FAILED",
        "ELEMENT_NOT_FOUND",
        "PRODUCT_LIST_REFRESH_FAILED",
        "SELECTOR_BUILD_FAILED",
        "WINDOW_NOT_AVAILABLE",
    }
)


def _task12_optional_price(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    return Decimal(
        normalize_price_string(
            value,
            error_code=PriceBatchErrorCode.CURRENT_PRICE_PARSE_FAILED,
        )
    )


def _task12_batch_error(raw_error: str, *, side_effect_state: str) -> tuple[str, str]:
    adapter_error = str(raw_error or "").strip().upper()
    if not adapter_error:
        return "", ""
    if adapter_error in PRICE_BATCH_ERROR_CODES:
        return adapter_error, adapter_error
    if adapter_error in _TASK12_ERROR_ALIASES:
        return _TASK12_ERROR_ALIASES[adapter_error], adapter_error
    if adapter_error in _TASK12_LIST_LOAD_ERRORS:
        return PriceBatchErrorCode.LIST_NOT_LOADED.value, adapter_error
    if side_effect_state in {"SUBMIT_INTENT_RECORDED", "SUBMIT_CLICKED", "UNKNOWN"}:
        return PriceBatchErrorCode.SUBMIT_RESULT_UNKNOWN.value, adapter_error
    return PriceBatchErrorCode.PLATFORM_EXECUTION_FAILED.value, adapter_error


def _attempt_run_id(repository: SQLiteRuntimeRepository, execution_attempt_id: str) -> str:
    attempt = repository.get_shadowbot_execution_attempt(execution_attempt_id)
    return attempt.shadowbot_run_id if attempt is not None else ""


def _validate_persisted_batch_counts(
    repository: SQLiteRuntimeRepository,
    batch_id: str,
) -> dict[str, int]:
    batch = repository.get_shadowbot_batch(batch_id)
    items = repository.list_shadowbot_batch_items(batch_id)
    if batch is None:
        raise ValidationError("BATCH_ITEM_BINDING_MISMATCH: batch disappeared during import.")
    counts = aggregate_batch_counts([item.status for item in items])
    for name, expected in counts.items():
        if name == "total_count":
            actual = len(items)
        else:
            actual = getattr(batch, name)
        if actual != expected:
            raise ValidationError(
                f"RESULT_CONTRACT_INVALID: persisted batch count mismatch for {name}."
            )
    return counts


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
        if request.get("contract_version") == 2:
            products = request.get("products")
            product_count = len(products) if isinstance(products, list) else 0
            result.update(
                {
                    "schema_version": "shadowbot-result-2.0",
                    "contract_version": 2,
                    "read_batch_id": request.get("read_batch_id", ""),
                    "platform_name": request.get("platform_name", ""),
                    "product_snapshots": [],
                    "total_count": product_count,
                    "success_count": 0,
                    "failed_count": product_count,
                    "skipped_count": 0,
                    "manual_check_count": 0,
                    "overall_status": "FAILED",
                }
            )
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
