from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import ExecutionLog, MockPlatformProductState, Task
from app.repositories.mock_platform_repository import (
    MOCK_PLATFORM_OFFLINE,
    MOCK_PLATFORM_ONLINE,
    MockPlatformRepository,
    seed_default_mock_platform,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import RuntimeTaskService
from app.utils import utc_now


MOCK_PLATFORM_EXECUTOR_NAME = "mock_platform_executor"
EXECUTABLE_ACTIONS = {
    TaskActionType.UPDATE_PRICE,
    TaskActionType.SET_ONLINE,
    TaskActionType.SET_OFFLINE,
    TaskActionType.SYNC_STATUS,
}
MIN_MOCK_PLATFORM_PRICE = Decimal("1")


@dataclass(slots=True)
class MockPlatformExecutionItem:
    task_id: str
    action_type: str
    platform_name: str
    internal_sku: str
    would_apply: bool
    success: bool
    message: str
    before_state: dict[str, object] = field(default_factory=dict)
    after_state: dict[str, object] = field(default_factory=dict)
    error_code: str = ""


@dataclass(slots=True)
class MockPlatformExecutionSummary:
    run_mode: str
    scanned_tasks_count: int
    executable_tasks_count: int
    executed_tasks_count: int
    success_count: int
    failed_count: int
    items: list[MockPlatformExecutionItem]


class MockPlatformExecutorService:
    def __init__(
        self,
        *,
        runtime_repository: SQLiteRuntimeRepository,
        mock_platform_repository: MockPlatformRepository,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.mock_platform_repository = mock_platform_repository
        self.runtime_task_service = RuntimeTaskService(runtime_repository)

    def initialize_mock_platform(self, *, reset: bool = False) -> int:
        if reset:
            return seed_default_mock_platform(self.mock_platform_repository)
        self.mock_platform_repository.init_schema()
        return 0

    def execute(
        self,
        *,
        apply: bool = False,
        platform_name: str | None = None,
        task_id: str | None = None,
    ) -> MockPlatformExecutionSummary:
        self.runtime_repository.init_schema()
        self.mock_platform_repository.init_schema()
        tasks = self._candidate_tasks(platform_name=platform_name, task_id=task_id)
        items: list[MockPlatformExecutionItem] = []
        logs: list[ExecutionLog] = []
        success_count = 0
        failed_count = 0
        for task in tasks:
            item, log = self._execute_one(task, apply=apply)
            items.append(item)
            if item.success:
                success_count += 1
            else:
                failed_count += 1
            if log is not None:
                logs.append(log)
        if apply and logs:
            self.runtime_repository.insert_execution_logs(logs)
        return MockPlatformExecutionSummary(
            run_mode="apply" if apply else "dry-run",
            scanned_tasks_count=len(tasks),
            executable_tasks_count=len(tasks),
            executed_tasks_count=len(logs) if apply else 0,
            success_count=success_count,
            failed_count=failed_count,
            items=items,
        )

    def _candidate_tasks(self, *, platform_name: str | None, task_id: str | None) -> list[Task]:
        if task_id:
            task = self.runtime_repository.get_task(task_id)
            tasks = [task] if task is not None else []
        else:
            tasks = self.runtime_repository.list_tasks(status=TaskStatus.PENDING)
        filtered: list[Task] = []
        for task in tasks:
            if task is None:
                continue
            if task.action_type not in EXECUTABLE_ACTIONS:
                continue
            if task.task_status != TaskStatus.PENDING:
                continue
            if platform_name and task.platform_name != platform_name:
                continue
            filtered.append(task)
        return filtered

    def _execute_one(self, task: Task, *, apply: bool) -> tuple[MockPlatformExecutionItem, ExecutionLog | None]:
        start_time = utc_now()
        platform_name = task.platform_name or "default_platform"
        internal_sku = task.internal_sku or ""
        state = self.mock_platform_repository.get_product_state(
            platform_name=platform_name,
            internal_sku=internal_sku,
        )
        before = _state_snapshot(state)
        error_code = ""
        success = True
        message = ""
        after_state = before
        try:
            if not internal_sku:
                raise ValidationError("任务缺少商品 SKU，无法执行到模拟平台。")
            if state is None:
                raise ValidationError("模拟平台商品不存在。")
            if task.action_type == TaskActionType.UPDATE_PRICE:
                price = _target_price(task)
                if price < MIN_MOCK_PLATFORM_PRICE:
                    raise ValidationError("模拟平台拒绝低于最低允许值的价格。")
                after_state = before | {"platform_price": str(price)}
                if apply:
                    self.mock_platform_repository.update_price(
                        platform_name=platform_name,
                        internal_sku=internal_sku,
                        price=price,
                        updated_at=datetime.now(),
                    )
                message = f"模拟平台改价为 {price}"
            elif task.action_type == TaskActionType.SET_ONLINE:
                after_state = before | {"platform_online_status": MOCK_PLATFORM_ONLINE}
                if apply:
                    self.mock_platform_repository.update_online_status(
                        platform_name=platform_name,
                        internal_sku=internal_sku,
                        status=MOCK_PLATFORM_ONLINE,
                        updated_at=datetime.now(),
                    )
                message = "模拟平台已上架"
            elif task.action_type == TaskActionType.SET_OFFLINE:
                after_state = before | {"platform_online_status": MOCK_PLATFORM_OFFLINE}
                if apply:
                    self.mock_platform_repository.update_online_status(
                        platform_name=platform_name,
                        internal_sku=internal_sku,
                        status=MOCK_PLATFORM_OFFLINE,
                        updated_at=datetime.now(),
                    )
                message = "模拟平台已下架"
            elif task.action_type == TaskActionType.SYNC_STATUS:
                if apply:
                    self.mock_platform_repository.update_last_synced_at(
                        platform_name=platform_name,
                        internal_sku=internal_sku,
                        synced_at=datetime.now(),
                    )
                message = "已读取模拟平台状态"
            else:
                raise ValidationError("该任务类型不支持模拟平台执行。")
        except ValidationError as exc:
            success = False
            message = str(exc)
            error_code = "mock_platform_execution_failed"
            if apply and internal_sku:
                self.mock_platform_repository.record_error(
                    platform_name=platform_name,
                    internal_sku=internal_sku,
                    error=message,
                    updated_at=datetime.now(),
                )

        item = MockPlatformExecutionItem(
            task_id=task.task_id,
            action_type=task.action_type.value,
            platform_name=platform_name,
            internal_sku=internal_sku,
            would_apply=apply,
            success=success,
            message=message,
            before_state=before,
            after_state=after_state,
            error_code=error_code,
        )
        if not apply:
            return item, None

        if success:
            self.runtime_task_service.change_status(
                task_id=task.task_id,
                to_status=TaskStatus.RUNNING,
                changed_by=MOCK_PLATFORM_EXECUTOR_NAME,
                reason="mock platform execution started",
                metadata={"executor_name": MOCK_PLATFORM_EXECUTOR_NAME},
            )
            self.runtime_task_service.change_status(
                task_id=task.task_id,
                to_status=TaskStatus.SUCCESS,
                changed_by=MOCK_PLATFORM_EXECUTOR_NAME,
                reason="mock platform execution success",
                metadata={"executor_name": MOCK_PLATFORM_EXECUTOR_NAME, "mock_platform_state": after_state},
                result_message=message,
            )
        else:
            self.runtime_task_service.change_status(
                task_id=task.task_id,
                to_status=TaskStatus.RUNNING,
                changed_by=MOCK_PLATFORM_EXECUTOR_NAME,
                reason="mock platform execution started",
                metadata={"executor_name": MOCK_PLATFORM_EXECUTOR_NAME},
            )
            self.runtime_task_service.change_status(
                task_id=task.task_id,
                to_status=TaskStatus.FAILED,
                changed_by=MOCK_PLATFORM_EXECUTOR_NAME,
                reason="mock platform execution failed",
                metadata={"executor_name": MOCK_PLATFORM_EXECUTOR_NAME, "error_code": error_code},
                result_message=message,
            )
        end_time = utc_now()
        return item, ExecutionLog(
            log_id=uuid4().hex[:12],
            task_id=task.task_id,
            executor_name=MOCK_PLATFORM_EXECUTOR_NAME,
            start_time=start_time,
            end_time=end_time,
            success_flag=success,
            error_code=error_code,
            error_message="" if success else message,
            raw_output=_safe_raw_output(
                {
                    "action_type": task.action_type.value,
                    "platform_name": platform_name,
                    "internal_sku": internal_sku,
                    "message": message,
                    "before": before,
                    "after": after_state,
                }
            ),
            created_at=end_time,
        )


def _target_price(task: Task) -> Decimal:
    if task.target_price is None:
        raise ValidationError("改价任务缺少目标价格。")
    try:
        return Decimal(str(task.target_price))
    except InvalidOperation as exc:
        raise ValidationError("目标价格格式不正确。") from exc


def _state_snapshot(state: MockPlatformProductState | None) -> dict[str, object]:
    if state is None:
        return {}
    return {
        "platform_name": state.platform_name,
        "internal_sku": state.internal_sku,
        "platform_sku": state.platform_sku,
        "product_name": state.product_name,
        "grade": state.grade,
        "platform_price": str(state.platform_price) if state.platform_price is not None else None,
        "platform_online_status": state.platform_online_status,
        "platform_stock_qty": state.platform_stock_qty,
        "last_synced_at": state.last_synced_at.isoformat() if state.last_synced_at else None,
        "last_platform_update_at": state.last_platform_update_at.isoformat() if state.last_platform_update_at else None,
        "last_error": state.last_error,
    }


def _safe_raw_output(payload: dict[str, object]) -> str:
    import json

    text = json.dumps(payload, ensure_ascii=False, default=str)
    for marker in ["token=", "webhook", "secret", "mobile_review_url"]:
        text = text.replace(marker, "[redacted]")
    return text[:2000]
