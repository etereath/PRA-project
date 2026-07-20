"""Task 12 serial batch orchestration above the ShadowBot executor boundary."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.models import ShadowBotBatch, ShadowBotBatchItem, ShadowBotOperationLedger
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_price_batch import (
    BATCH_TYPE,
    CONTRACT_VERSION,
    FRESH_READ_MAX_AGE_SECONDS,
    BatchItemStatus,
    BatchStatus,
    PriceBatchContractError,
    PriceBatchErrorCode,
    compute_batch_item_approved_payload_hash,
    normalize_price_batch_request,
    normalize_price_string,
)
from app.services.shadowbot_product_read import normalize_grade, normalize_sku, normalize_text
from app.utils import utc_now


_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_UNSAFE_SIDE_EFFECT_STATES = frozenset(
    {"SUBMIT_INTENT_RECORDED", "SUBMIT_CLICKED", "UNKNOWN"}
)
_TERMINAL_OPERATION_SUCCESS = frozenset({"SUCCESS", "ALREADY_APPLIED", "VERIFIED"})


class ShadowBotPriceBatchOrchestrator:
    """Fail-closed batch coordinator; it never performs a UI action itself."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def create_batch(
        self,
        payload: Mapping[str, Any],
        *,
        source_binding: Mapping[str, Any],
        created_by: str,
        now: datetime | None = None,
    ) -> ShadowBotBatch:
        timestamp = _aware_utc(now or utc_now())
        actor = str(created_by or "").strip()
        if not actor:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        self.repository.init_schema()
        normalized = normalize_price_batch_request(
            payload,
            source_binding=source_binding,
            now=timestamp,
        )
        accepted_platforms = {
            normalize_text(normalized["platform"]),
            normalize_text(source_binding.get("page_context", {}).get("platform_name")),
        }
        accepted_platforms.discard("")
        operations: list[ShadowBotOperationLedger] = []
        items: list[ShadowBotBatchItem] = []
        for raw_item in normalized["items"]:
            self._validate_approval(
                normalized,
                raw_item,
                accepted_platforms=accepted_platforms,
                now=timestamp,
            )
            operation = ShadowBotOperationLedger(
                operation_id=raw_item["operation_id"],
                task_id=raw_item["task_id"],
                platform=normalized["platform"],
                product_identity={
                    "platform_sku": raw_item["platform_sku"],
                    "expected_product_name": raw_item["expected_product_name"],
                    "expected_grade": raw_item["expected_grade"],
                    "page_identity_key": raw_item["page_identity_key"],
                    "write_identity_key": raw_item["write_identity_key"],
                    "source_read_batch_id": normalized["source_read_batch_id"],
                    "source_item_id": raw_item["source_item_id"],
                    "approved_platform_names": sorted(accepted_platforms),
                },
                expected_old_price=Decimal(raw_item["approved_expected_old_price"]),
                target_price=Decimal(raw_item["target_price"]),
                status="PENDING",
                approved_payload_hash=raw_item["approved_payload_hash"],
                write_identity_key=raw_item["write_identity_key"],
                page_identity_key=raw_item["page_identity_key"],
                created_at=timestamp,
                updated_at=timestamp,
            )
            existing = self.repository.get_shadowbot_operation(operation.operation_id)
            if existing is not None:
                self._validate_existing_operation(existing, operation)
            operations.append(operation)
            items.append(
                ShadowBotBatchItem(
                    batch_id=normalized["batch_id"],
                    item_id=raw_item["item_id"],
                    ordinal=raw_item["ordinal"],
                    source_item_id=raw_item["source_item_id"],
                    source_read_batch_id=normalized["source_read_batch_id"],
                    source_snapshot_sha256=normalized["source_snapshot_sha256"],
                    source_page_context_sha256=normalized["source_page_context_sha256"],
                    task_id=raw_item["task_id"],
                    review_task_id=raw_item["review_task_id"],
                    operation_id=raw_item["operation_id"],
                    approved_payload_hash=raw_item["approved_payload_hash"],
                    page_identity_key=raw_item["page_identity_key"],
                    write_identity_key=raw_item["write_identity_key"],
                    external_platform_sku=raw_item["platform_sku"] or None,
                    expected_product_name=raw_item["expected_product_name"],
                    expected_grade=raw_item["expected_grade"],
                    approved_expected_old_price=Decimal(raw_item["approved_expected_old_price"]),
                    target_price=Decimal(raw_item["target_price"]),
                    status=BatchItemStatus.PENDING.value,
                    updated_at=timestamp,
                )
            )
        source_observed_at = _parse_datetime(normalized["source_observed_at"])
        batch = ShadowBotBatch(
            batch_id=normalized["batch_id"],
            contract_version=CONTRACT_VERSION,
            platform=normalized["platform"],
            batch_type=BATCH_TYPE,
            execution_mode=normalized["execution_mode"],
            identity_normalization_version=normalized["identity_normalization_version"],
            normalized_request_digest=normalized["normalized_request_digest"],
            stop_policy=normalized["stop_policy"],
            source_read_batch_id=normalized["source_read_batch_id"],
            source_snapshot_sha256=normalized["source_snapshot_sha256"],
            source_page_context_sha256=normalized["source_page_context_sha256"],
            source_observed_at=source_observed_at,
            source_snapshot_max_age_seconds=normalized["source_snapshot_max_age_seconds"],
            status=BatchStatus.PENDING.value,
            created_by=actor,
            capture_evidence=normalized["capture_evidence"],
            pending_count=len(items),
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.repository.insert_shadowbot_batch(batch, items, operations=operations)
        stored = self.repository.get_shadowbot_batch(batch.batch_id)
        if stored is None:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        return stored

    def claim_next(
        self,
        batch_id: str,
        *,
        stop_requested: bool = False,
        now: datetime | None = None,
    ) -> ShadowBotBatchItem | None:
        item = self.repository.claim_next_shadowbot_batch_item(
            batch_id,
            now=_aware_utc(now or utc_now()),
            stop_requested=stop_requested,
        )
        if item is not None:
            try:
                self.revalidate_item_approval(batch_id, item.item_id, now=now)
            except PriceBatchContractError as exc:
                self.repository.complete_shadowbot_batch_item(
                    batch_id,
                    item.item_id,
                    status=BatchItemStatus.FAILED.value,
                    execution_attempt_id=f"APPROVAL-{batch_id}-{item.item_id}",
                    error_code=exc.code,
                    error_message=exc.detail or exc.code,
                    result_id=f"APPROVAL-{item.item_id}",
                    now=_aware_utc(now or utc_now()),
                )
                raise
        return item

    def revalidate_item_approval(
        self,
        batch_id: str,
        item_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        batch = self.repository.get_shadowbot_batch(batch_id)
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if batch is None or item is None:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        operation = self.repository.get_shadowbot_operation(item.operation_id)
        if operation is None:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        accepted_platforms = {
            normalize_text(value)
            for value in operation.product_identity.get("approved_platform_names", [])
            if normalize_text(value)
        }
        accepted_platforms.add(normalize_text(batch.platform))
        normalized = build_persisted_batch_item_request_view(batch, item)
        self._validate_approval(
            normalized,
            normalized["items"][0],
            accepted_platforms=accepted_platforms,
            now=_aware_utc(now or utc_now()),
        )

    def record_fresh_read(
        self,
        batch_id: str,
        item_id: str,
        *,
        fresh_read_attempt_id: str,
        result_sha256: str,
        observed_product_name: str,
        observed_grade: str,
        observed_platform_sku: Any,
        observed_price: Any,
        observed_at: datetime | str,
        now: datetime | None = None,
    ) -> ShadowBotBatchItem:
        timestamp = _aware_utc(now or utc_now())
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if item is None or item.status != BatchItemStatus.RUNNING.value:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        error: PriceBatchErrorCode | None = None
        observed_time = _parse_datetime(observed_at)
        age = (timestamp - observed_time).total_seconds()
        if age < 0 or age > FRESH_READ_MAX_AGE_SECONDS:
            error = PriceBatchErrorCode.FRESH_READ_EXPIRED
        elif (
            normalize_text(observed_product_name) != normalize_text(item.expected_product_name)
            or normalize_grade(observed_grade) != normalize_grade(item.expected_grade)
            or (
                item.external_platform_sku is not None
                and normalize_sku(observed_platform_sku) != normalize_sku(item.external_platform_sku)
            )
        ):
            error = PriceBatchErrorCode.PRODUCT_IDENTITY_MISMATCH
        try:
            observed_price_text = normalize_price_string(
                observed_price,
                error_code=PriceBatchErrorCode.CURRENT_PRICE_PARSE_FAILED,
            )
        except PriceBatchContractError:
            observed_price_text = "0.00"
            error = PriceBatchErrorCode.CURRENT_PRICE_PARSE_FAILED
        observed_decimal = Decimal(observed_price_text)
        if error is None and observed_decimal != item.approved_expected_old_price:
            error = PriceBatchErrorCode.OLD_PRICE_CHANGED
        normalized_hash = _normalize_sha256(
            result_sha256,
            error_code=PriceBatchErrorCode.RESULT_CONTRACT_INVALID,
        )
        if error is not None:
            self.repository.complete_shadowbot_batch_item(
                batch_id,
                item_id,
                status=BatchItemStatus.FAILED.value,
                execution_attempt_id=fresh_read_attempt_id,
                error_code=error.value,
                error_message=error.value,
                result_id=fresh_read_attempt_id,
                result_hash=normalized_hash,
                now=timestamp,
            )
            raise PriceBatchContractError(error)
        self.revalidate_item_approval(batch_id, item_id, now=timestamp)
        self.repository.record_shadowbot_batch_fresh_read(
            batch_id,
            item_id,
            fresh_read_attempt_id=fresh_read_attempt_id,
            fresh_read_result_sha256=normalized_hash,
            fresh_old_price=observed_decimal,
            now=timestamp,
        )
        stored = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        assert stored is not None
        return stored

    def bind_execution_attempt(
        self,
        batch_id: str,
        item_id: str,
        *,
        execution_attempt_id: str,
        run_id: str = "",
        now: datetime | None = None,
    ) -> None:
        timestamp = _aware_utc(now or utc_now())
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if item is None or item.status != BatchItemStatus.RUNNING.value:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        if (
            not item.fresh_read_attempt_id
            or item.fresh_old_price != item.approved_expected_old_price
            or item.updated_at is None
            or (timestamp - _aware_utc(item.updated_at)).total_seconds() < 0
            or (timestamp - _aware_utc(item.updated_at)).total_seconds() > FRESH_READ_MAX_AGE_SECONDS
        ):
            self.repository.complete_shadowbot_batch_item(
                batch_id,
                item_id,
                status=BatchItemStatus.FAILED.value,
                execution_attempt_id=execution_attempt_id,
                error_code=PriceBatchErrorCode.FRESH_READ_EXPIRED.value,
                error_message="fresh read is missing or older than 60 seconds",
                result_id=execution_attempt_id,
                now=timestamp,
            )
            raise PriceBatchContractError(PriceBatchErrorCode.FRESH_READ_EXPIRED)
        try:
            self.revalidate_item_approval(batch_id, item_id, now=timestamp)
        except PriceBatchContractError as exc:
            self.repository.complete_shadowbot_batch_item(
                batch_id,
                item_id,
                status=BatchItemStatus.FAILED.value,
                execution_attempt_id=execution_attempt_id,
                error_code=exc.code,
                error_message=exc.detail or exc.code,
                result_id=execution_attempt_id,
                now=timestamp,
            )
            raise
        self.repository.bind_shadowbot_batch_item_attempt(
            batch_id,
            item_id,
            execution_attempt_id=execution_attempt_id,
            run_id=run_id,
            now=timestamp,
        )

    def record_item_result(
        self,
        batch_id: str,
        item_id: str,
        *,
        status: str,
        execution_attempt_id: str,
        run_id: str = "",
        post_commit_price: Decimal | None = None,
        error_code: str = "",
        error_message: str = "",
        result_id: str = "",
        result_hash: str = "",
        stop_requested: bool = False,
        now: datetime | None = None,
    ) -> bool:
        batch = self.repository.get_shadowbot_batch(batch_id)
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if batch is None or item is None:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        timestamp = _aware_utc(now or utc_now())
        normalized_result_hash = (
            _normalize_sha256(
                result_hash,
                error_code=PriceBatchErrorCode.RESULT_CONTRACT_INVALID,
            )
            if result_hash
            else ""
        )
        invalid_code: PriceBatchErrorCode | None = None
        invalid_status = BatchItemStatus.FAILED.value
        if batch.execution_mode == "FILL_PREVIEW":
            if status == BatchItemStatus.PREVIEWED.value and (
                post_commit_price is None or post_commit_price != item.fresh_old_price
            ):
                invalid_code = PriceBatchErrorCode.PREVIEW_INPUT_MISMATCH
            elif status not in {
                BatchItemStatus.PREVIEWED.value,
                BatchItemStatus.FAILED.value,
                BatchItemStatus.SKIPPED.value,
                BatchItemStatus.CANCELLED.value,
                BatchItemStatus.NEEDS_RECONCILIATION.value,
            }:
                invalid_code = PriceBatchErrorCode.RESULT_CONTRACT_INVALID
        elif batch.execution_mode == "COMMIT":
            if status == BatchItemStatus.VERIFIED.value and post_commit_price != item.target_price:
                invalid_code = PriceBatchErrorCode.SUBMIT_RESULT_UNKNOWN
                invalid_status = BatchItemStatus.NEEDS_RECONCILIATION.value
            elif status == BatchItemStatus.PREVIEWED.value:
                invalid_code = PriceBatchErrorCode.RESULT_CONTRACT_INVALID
                invalid_status = BatchItemStatus.NEEDS_RECONCILIATION.value
        if invalid_code is not None:
            self.repository.complete_shadowbot_batch_item(
                batch_id,
                item_id,
                status=invalid_status,
                execution_attempt_id=execution_attempt_id,
                run_id=run_id,
                post_commit_price=post_commit_price,
                error_code=invalid_code.value,
                error_message=invalid_code.value,
                result_id=result_id,
                result_hash=normalized_result_hash,
                stop_requested=stop_requested,
                now=timestamp,
            )
            raise PriceBatchContractError(invalid_code)
        return self.repository.complete_shadowbot_batch_item(
            batch_id,
            item_id,
            status=status,
            execution_attempt_id=execution_attempt_id,
            run_id=run_id,
            post_commit_price=post_commit_price,
            error_code=error_code,
            error_message=error_message,
            result_id=result_id,
            result_hash=normalized_result_hash,
            stop_requested=stop_requested,
            now=timestamp,
        )

    def request_safe_stop(
        self,
        batch_id: str,
        *,
        item_id: str | None = None,
        side_effect_state: str = "NOT_STARTED",
        now: datetime | None = None,
    ) -> str:
        timestamp = _aware_utc(now or utc_now())
        if not item_id:
            self.repository.claim_next_shadowbot_batch_item(
                batch_id,
                now=timestamp,
                stop_requested=True,
            )
            return "PAUSED_BEFORE_NEXT_ITEM"
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if item is None or item.status != BatchItemStatus.RUNNING.value:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        if str(side_effect_state).upper() in _UNSAFE_SIDE_EFFECT_STATES:
            self.repository.control_shadowbot_batch(
                batch_id,
                action="PAUSE",
                actor="worker",
                reason="stop deferred until current item is read back",
                now=timestamp,
            )
            return "DEFERRED_UNTIL_RESULT"
        attempt_id = (
            item.current_execution_attempt_id
            or item.fresh_read_attempt_id
            or f"STOP-{batch_id}-{item_id}"
        )
        self.repository.complete_shadowbot_batch_item(
            batch_id,
            item_id,
            status=BatchItemStatus.FAILED.value,
            execution_attempt_id=attempt_id,
            error_code=PriceBatchErrorCode.WORKER_STOP_REQUESTED.value,
            error_message="worker stopped before submit intent",
            result_id=attempt_id,
            stop_requested=True,
            now=timestamp,
        )
        return "STOPPED_BEFORE_SUBMIT"

    def pause(self, batch_id: str, *, actor: str, reason: str = "", now: datetime | None = None) -> bool:
        return self.repository.control_shadowbot_batch(
            batch_id,
            action="PAUSE",
            actor=actor,
            reason=reason,
            now=_aware_utc(now or utc_now()),
        )

    def resume(self, batch_id: str, *, actor: str, reason: str = "", now: datetime | None = None) -> bool:
        return self.repository.control_shadowbot_batch(
            batch_id,
            action="RESUME",
            actor=actor,
            reason=reason,
            now=_aware_utc(now or utc_now()),
        )

    def cancel_pending(
        self,
        batch_id: str,
        *,
        actor: str,
        reason: str = "",
        now: datetime | None = None,
    ) -> bool:
        return self.repository.control_shadowbot_batch(
            batch_id,
            action="CANCEL_PENDING",
            actor=actor,
            reason=reason,
            now=_aware_utc(now or utc_now()),
        )

    def claim_reconcile(
        self,
        batch_id: str,
        item_id: str,
        *,
        reconcile_attempt_id: str,
        now: datetime | None = None,
    ) -> ShadowBotBatchItem:
        return self.repository.claim_shadowbot_batch_reconcile(
            batch_id,
            item_id,
            reconcile_attempt_id=reconcile_attempt_id,
            now=_aware_utc(now or utc_now()),
        )

    def complete_reconcile(
        self,
        batch_id: str,
        item_id: str,
        *,
        reconcile_attempt_id: str,
        outcome: str,
        post_commit_price: Decimal | None = None,
        error_code: str = "",
        error_message: str = "",
        now: datetime | None = None,
    ) -> bool:
        return self.repository.complete_shadowbot_batch_reconcile(
            batch_id,
            item_id,
            reconcile_attempt_id=reconcile_attempt_id,
            outcome=outcome,
            post_commit_price=post_commit_price,
            error_code=error_code,
            error_message=error_message,
            now=_aware_utc(now or utc_now()),
        )

    def recover_batch(
        self,
        batch_id: str,
        *,
        worker_stopped: bool,
        now: datetime | None = None,
    ) -> list[str]:
        """Classify interrupted items without creating or replaying any COMMIT."""

        if not worker_stopped:
            return []
        timestamp = _aware_utc(now or utc_now())
        recovered: list[str] = []
        for item in self.repository.list_shadowbot_batch_items(batch_id):
            if item.status != BatchItemStatus.RUNNING.value:
                continue
            operation = self.repository.get_shadowbot_operation(item.operation_id)
            attempt_id = item.current_execution_attempt_id or item.fresh_read_attempt_id
            attempt = (
                self.repository.get_shadowbot_execution_attempt(attempt_id)
                if attempt_id
                else None
            )
            checkpoint = self.repository.latest_shadowbot_side_effect_checkpoint(item.operation_id)
            side_effect = (
                checkpoint.side_effect_state
                if checkpoint is not None
                else (attempt.side_effect_state if attempt is not None else "NOT_STARTED")
            )
            synthetic_attempt = attempt_id or f"RECOVERY-{batch_id}-{item.item_id}"
            if operation is not None and (
                operation.status in _TERMINAL_OPERATION_SUCCESS or side_effect == "VERIFIED"
            ):
                final_status = BatchItemStatus.VERIFIED.value
                failure_code = ""
            elif operation is not None and operation.status in {"FAILED", "NOT_APPLIED"} and side_effect in {
                "NOT_STARTED",
                "NOT_APPLIED",
            }:
                final_status = BatchItemStatus.FAILED.value
                failure_code = ""
            else:
                final_status = BatchItemStatus.NEEDS_RECONCILIATION.value
                failure_code = PriceBatchErrorCode.SUBMIT_RESULT_UNKNOWN.value
            self.repository.complete_shadowbot_batch_item(
                batch_id,
                item.item_id,
                status=final_status,
                execution_attempt_id=synthetic_attempt,
                run_id=attempt.shadowbot_run_id if attempt is not None else "",
                error_code=failure_code,
                error_message="recovered without replaying COMMIT" if failure_code else "",
                result_id=f"RECOVERY-{item.item_id}",
                now=timestamp,
            )
            recovered.append(item.item_id)
        return recovered

    def _validate_approval(
        self,
        normalized: Mapping[str, Any],
        item: Mapping[str, Any],
        *,
        accepted_platforms: set[str],
        now: datetime,
    ) -> None:
        task = self.repository.get_task(str(item["task_id"]))
        review = self.repository.get_review_task(str(item["review_task_id"]))
        if task is None or review is None:
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_REQUIRED)
        if (
            task.action_type != TaskActionType.UPDATE_PRICE
            or task.task_status not in {TaskStatus.PENDING, TaskStatus.RUNNING}
            or review.review_status != ReviewTaskStatus.APPROVED
            or review.resolved_at is None
            or review.source_task_id != task.task_id
        ):
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_REQUIRED)
        if task.target_price is not None and task.target_price != Decimal(str(item["target_price"])):
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVED_PAYLOAD_HASH_MISMATCH)
        if task.internal_sku and review.internal_sku and task.internal_sku != review.internal_sku:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        for platform_name in (task.platform_name, review.platform_name):
            if platform_name and normalize_text(platform_name) not in accepted_platforms:
                raise PriceBatchContractError(PriceBatchErrorCode.SINGLE_PLATFORM_REQUIRED)
        approval_data = dict(review.review_payload)
        approval_data.update(review.resolution_payload)
        allowed_modes = approval_data.get("approved_execution_modes")
        if not isinstance(allowed_modes, list) or str(normalized["execution_mode"]) not in {
            str(value).strip().upper() for value in allowed_modes
        }:
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_MODE_NOT_ALLOWED)
        expires_at_raw = approval_data.get("approval_expires_at")
        if not expires_at_raw:
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_EXPIRED)
        if _parse_datetime(expires_at_raw) <= now:
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_EXPIRED)
        computed_hash = compute_batch_item_approved_payload_hash(normalized, item)
        if _normalize_sha256(item["approved_payload_hash"]) != computed_hash:
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVED_PAYLOAD_HASH_MISMATCH)
        stored_hash = approval_data.get("approved_payload_hash")
        if _normalize_sha256(stored_hash) != computed_hash:
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVED_PAYLOAD_HASH_MISMATCH)

    @staticmethod
    def _validate_existing_operation(
        existing: ShadowBotOperationLedger,
        expected: ShadowBotOperationLedger,
    ) -> None:
        if (
            existing.task_id != expected.task_id
            or existing.platform != expected.platform
            or existing.product_identity != expected.product_identity
            or existing.expected_old_price != expected.expected_old_price
            or existing.target_price != expected.target_price
            or existing.approved_payload_hash != expected.approved_payload_hash
            or existing.write_identity_key != expected.write_identity_key
            or existing.page_identity_key != expected.page_identity_key
        ):
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)


def build_persisted_batch_item_request_view(
    batch: ShadowBotBatch,
    item: ShadowBotBatchItem,
) -> dict[str, Any]:
    raw_item = {
        "item_id": item.item_id,
        "ordinal": item.ordinal,
        "source_item_id": item.source_item_id,
        "task_id": item.task_id,
        "review_task_id": item.review_task_id,
        "operation_id": item.operation_id,
        "approved_payload_hash": item.approved_payload_hash,
        "platform_sku": item.external_platform_sku,
        "expected_product_name": item.expected_product_name,
        "expected_grade": item.expected_grade,
        "normalized_product_name": normalize_text(item.expected_product_name),
        "normalized_grade": normalize_grade(item.expected_grade),
        "page_identity_key": item.page_identity_key,
        "write_identity_key": item.write_identity_key,
        "approved_expected_old_price": format(item.approved_expected_old_price, ".2f"),
        "target_price": format(item.target_price, ".2f"),
    }
    return {
        "contract_version": batch.contract_version,
        "batch_id": batch.batch_id,
        "platform": batch.platform,
        "batch_type": batch.batch_type,
        "execution_mode": batch.execution_mode,
        "stop_policy": batch.stop_policy,
        "identity_normalization_version": batch.identity_normalization_version,
        "source_read_batch_id": batch.source_read_batch_id,
        "source_snapshot_sha256": batch.source_snapshot_sha256,
        "source_page_context_sha256": batch.source_page_context_sha256,
        "source_observed_at": batch.source_observed_at.isoformat(),
        "source_snapshot_max_age_seconds": batch.source_snapshot_max_age_seconds,
        "items": [raw_item],
    }


def _normalize_sha256(
    value: Any,
    *,
    error_code: PriceBatchErrorCode = PriceBatchErrorCode.APPROVED_PAYLOAD_HASH_MISMATCH,
) -> str:
    match = _SHA256_RE.fullmatch(str(value or "").strip())
    if not match:
        raise PriceBatchContractError(error_code)
    return "sha256:" + match.group(1).lower()


def _parse_datetime(value: datetime | str | Any) -> datetime:
    if isinstance(value, datetime):
        return _aware_utc(value)
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PriceBatchContractError(PriceBatchErrorCode.RESULT_CONTRACT_INVALID) from exc
    if parsed.tzinfo is None:
        raise PriceBatchContractError(PriceBatchErrorCode.RESULT_CONTRACT_INVALID)
    return parsed.astimezone(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
