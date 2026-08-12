"""Authenticated Web Review resolution over the established atomic paths."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.exceptions import MobileReviewTransactionError, ValidationError
from app.models import ReviewTaskStatus
from app.operations_web.auth import (
    AuthorizationBackend,
    Capability,
    Principal,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.review_policy import allowed_review_statuses
from app.services.runtime import ReviewTaskService
from app.services.workflow import _read_authoritative_product_cost_snapshot


class ReviewResolutionError(ValueError):
    """The authenticated Review request cannot be committed safely."""


@dataclass(frozen=True, slots=True)
class ReviewResolutionResult:
    review_task_id: str
    review_status: str
    created_task_id: str = ""


class ReviewResolutionApplicationService:
    """Resolve desktop Reviews without introducing a second state machine."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        authorization: AuthorizationBackend,
        *,
        products_path: Path,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.products_path = products_path

    def resolve(
        self,
        principal: Principal,
        *,
        review_task_id: str,
        action: str,
        target_price: str = "",
        note: str = "",
    ) -> ReviewResolutionResult:
        if not self.authorization.allows(principal, Capability.HANDLE_REVIEW):
            raise ReviewResolutionError("当前账号没有人工复核权限。")
        clean_id = review_task_id.strip()
        if not clean_id:
            raise ReviewResolutionError("请选择需要处理的复核任务。")
        try:
            status = ReviewTaskStatus(action.strip())
        except ValueError as exc:
            raise ReviewResolutionError("该复核处理方式不可用。") from exc

        review = self.repository.get_review_task(clean_id)
        if review is None:
            raise ReviewResolutionError("复核任务不存在或已失效。")
        if review.review_status is not ReviewTaskStatus.PENDING:
            raise ReviewResolutionError("复核任务已被处理，请刷新页面。")
        source_task = (
            self.repository.get_task(review.source_task_id)
            if review.source_task_id
            else None
        )
        if status not in allowed_review_statuses(review, source_task):
            raise ReviewResolutionError("该处理方式不适用于当前复核任务。")

        resolution_payload: dict[str, object] | None = None
        if status is ReviewTaskStatus.ADJUSTED:
            try:
                parsed_price = Decimal(target_price.strip())
            except (InvalidOperation, ValueError) as exc:
                raise ReviewResolutionError("请输入有效的目标价格。") from exc
            if not parsed_price.is_finite() or parsed_price <= 0:
                raise ReviewResolutionError("目标价格必须大于 0。")
            resolution_payload = {"adjustment": {"target_price": parsed_price}}

        try:
            if review.review_type == "emergency_protection":
                base_cost = None
                base_cost_source_ref = ""
                if status in {
                    ReviewTaskStatus.ADJUSTED,
                    ReviewTaskStatus.APPROVED,
                }:
                    base_cost, base_cost_source_ref = (
                        _read_authoritative_product_cost_snapshot(
                            self.products_path,
                            internal_sku=str(review.internal_sku or ""),
                        )
                    )
                def verifier() -> tuple[Decimal, str]:
                    return _read_authoritative_product_cost_snapshot(
                        self.products_path,
                        internal_sku=str(review.internal_sku or ""),
                    )
                resolved, created_task = (
                    self.repository.resolve_authenticated_incident_review_atomic(
                        review_task_id=clean_id,
                        status=status,
                        actor_source="authenticated_web",
                        actor=principal.subject,
                        note=note.strip(),
                        resolution_payload=resolution_payload,
                        emergency_base_cost=base_cost,
                        emergency_base_cost_source_ref=base_cost_source_ref,
                        emergency_product_snapshot_verifier=verifier,
                    )
                )
                return ReviewResolutionResult(
                    review_task_id=resolved.review_task_id,
                    review_status=resolved.review_status.value,
                    created_task_id=(
                        created_task.task_id if created_task is not None else ""
                    ),
                )

            resolved = ReviewTaskService(self.repository).resolve_review_task(
                review_task_id=clean_id,
                status=status,
                actor=principal.subject,
                actor_source="authenticated_web",
                note=note.strip(),
                resolution_payload=resolution_payload,
            )
            return ReviewResolutionResult(
                review_task_id=resolved.review_task_id,
                review_status=resolved.review_status.value,
            )
        except (MobileReviewTransactionError, ValidationError) as exc:
            raise ReviewResolutionError(str(exc)) from exc
