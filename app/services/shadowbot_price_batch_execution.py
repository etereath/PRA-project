"""Lower one claimed task 12 item into the proven single-item ShadowBot queue."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import (
    EXECUTION_MODE_READ_ONLY,
    ShadowBotApproval,
    ShadowBotApprovedPayload,
    ShadowBotExecutionRequest,
    ShadowBotExecutor,
    ShadowBotExecutorStartResult,
    ShadowBotTaskRunner,
)
from app.services.shadowbot_price_batch import (
    BatchItemStatus,
    PriceBatchContractError,
    PriceBatchErrorCode,
    build_batch_item_approval_view,
)
from app.services.shadowbot_price_batch_orchestrator import (
    ShadowBotPriceBatchOrchestrator,
    build_persisted_batch_item_request_view,
)
from app.utils import utc_now


class ShadowBotPriceBatchExecutionService:
    """Coordinates queue publication while retaining per-item DB authority."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        runner: ShadowBotTaskRunner,
    ) -> None:
        self.repository = repository
        self.executor = ShadowBotExecutor(repository, runner)
        self.orchestrator = ShadowBotPriceBatchOrchestrator(repository)

    def start_fresh_read(
        self,
        batch_id: str,
        item_id: str,
        *,
        execution_attempt_id: str,
        runner_payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ShadowBotExecutorStartResult:
        timestamp = _aware_utc(now or utc_now())
        batch, item = self._load_running_item(batch_id, item_id)
        if item.fresh_read_result_sha256:
            raise PriceBatchContractError(
                PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH,
                "fresh read has already completed",
            )
        self.orchestrator.revalidate_item_approval(batch_id, item_id, now=timestamp)
        self.repository.reserve_shadowbot_batch_fresh_read_attempt(
            batch_id,
            item_id,
            fresh_read_attempt_id=execution_attempt_id,
            now=timestamp,
        )
        approval = self._build_approval(batch, item)
        request = ShadowBotExecutionRequest(
            operation_id=item.operation_id,
            execution_attempt_id=execution_attempt_id,
            execution_mode=EXECUTION_MODE_READ_ONLY,
            approval=approval,
            runner_payload=self._runner_payload(
                batch,
                item,
                stage="FRESH_READ",
                overrides=runner_payload,
                fresh_read_attempt_id=execution_attempt_id,
            ),
        )
        return self.executor.start_execution(request)

    def start_write(
        self,
        batch_id: str,
        item_id: str,
        *,
        execution_attempt_id: str,
        runner_payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ShadowBotExecutorStartResult:
        timestamp = _aware_utc(now or utc_now())
        batch, item = self._load_running_item(batch_id, item_id)
        if batch.execution_mode not in {"FILL_PREVIEW", "COMMIT"}:
            raise PriceBatchContractError(PriceBatchErrorCode.UNSUPPORTED_EXECUTION_MODE)
        self.orchestrator.bind_execution_attempt(
            batch_id,
            item_id,
            execution_attempt_id=execution_attempt_id,
            now=timestamp,
        )
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        assert item is not None
        approval = self._build_approval(batch, item)
        request = ShadowBotExecutionRequest(
            operation_id=item.operation_id,
            execution_attempt_id=execution_attempt_id,
            execution_mode=batch.execution_mode,
            approval=approval,
            runner_payload=self._runner_payload(
                batch,
                item,
                stage="WRITE",
                overrides=runner_payload,
            ),
        )
        return self.executor.start_execution(request)

    def start_reconcile(
        self,
        batch_id: str,
        item_id: str,
        *,
        reconcile_attempt_id: str,
        runner_payload: dict[str, Any] | None = None,
    ) -> ShadowBotExecutorStartResult:
        batch = self.repository.get_shadowbot_batch(batch_id)
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if batch is None or item is None:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        if (
            item.status != BatchItemStatus.NEEDS_RECONCILIATION.value
            or item.reconcile_attempt_id != reconcile_attempt_id
            or item.reconciliation_outcome != "PENDING"
        ):
            raise PriceBatchContractError(PriceBatchErrorCode.RECONCILIATION_CONFLICT)
        return self.executor.start_reconcile_attempt(
            operation_id=item.operation_id,
            execution_attempt_id=reconcile_attempt_id,
            runner_payload=self._runner_payload(
                batch,
                item,
                stage="RECONCILE",
                overrides=runner_payload,
            ),
        )

    def _load_running_item(self, batch_id: str, item_id: str):
        batch = self.repository.get_shadowbot_batch(batch_id)
        item = self.repository.get_shadowbot_batch_item(batch_id, item_id)
        if batch is None or item is None or item.status != BatchItemStatus.RUNNING.value:
            raise PriceBatchContractError(PriceBatchErrorCode.BATCH_ITEM_BINDING_MISMATCH)
        return batch, item

    def _build_approval(self, batch, item) -> ShadowBotApproval:
        review = self.repository.get_review_task(item.review_task_id)
        operation = self.repository.get_shadowbot_operation(item.operation_id)
        if review is None or operation is None or review.resolved_at is None:
            raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_REQUIRED)
        approval_data = dict(review.review_payload)
        approval_data.update(review.resolution_payload)
        expires_at = _parse_datetime(approval_data.get("approval_expires_at"))
        normalized_request = build_persisted_batch_item_request_view(batch, item)
        approved_payload_view = build_batch_item_approval_view(
            normalized_request,
            normalized_request["items"][0],
        )
        approved_payload = ShadowBotApprovedPayload(
            operation_id=operation.operation_id,
            task_id=operation.task_id,
            platform=operation.platform,
            product_identity=dict(operation.product_identity),
            expected_old_price=operation.expected_old_price,
            target_price=operation.target_price,
        )
        return ShadowBotApproval(
            approval_id=review.review_task_id,
            approval_status="APPROVED",
            approved_payload=approved_payload,
            approved_payload_hash=item.approved_payload_hash,
            approved_at=_aware_utc(review.resolved_at),
            expires_at=expires_at,
            approval_contract_version=3,
            approved_payload_view=approved_payload_view,
        )

    @staticmethod
    def _runner_payload(
        batch,
        item,
        *,
        stage: str,
        overrides: dict[str, Any] | None,
        fresh_read_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(overrides or {})
        payload.update(
            {
                "batch_contract_version": 3,
                "price_batch_id": batch.batch_id,
                "price_batch_item_id": item.item_id,
                "price_batch_ordinal": item.ordinal,
                "price_batch_stage": stage,
                "batch_execution_mode": batch.execution_mode,
                "normalized_request_digest": batch.normalized_request_digest,
                "source_read_batch_id": batch.source_read_batch_id,
                "source_snapshot_sha256": batch.source_snapshot_sha256,
                "source_page_context_sha256": batch.source_page_context_sha256,
                "page_identity_key": item.page_identity_key,
                "write_identity_key": item.write_identity_key,
                "fresh_read_attempt_id": fresh_read_attempt_id or item.fresh_read_attempt_id,
                "fresh_read_result_sha256": item.fresh_read_result_sha256,
                "fresh_old_price": (
                    format(item.fresh_old_price, ".2f") if item.fresh_old_price is not None else ""
                ),
                "capture_evidence": bool(batch.capture_evidence),
            }
        )
        return payload


def _parse_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_EXPIRED) from exc
    if parsed.tzinfo is None:
        raise PriceBatchContractError(PriceBatchErrorCode.APPROVAL_EXPIRED)
    return parsed.astimezone(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
