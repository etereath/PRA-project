from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.enums import TaskActionType, TaskStatus
from app.models import MockPlatformProductState, Task
from app.repositories.mock_platform_repository import (
    MOCK_PLATFORM_OFFLINE,
    MOCK_PLATFORM_ONLINE,
    MockPlatformRepository,
    seed_default_mock_platform,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.business_rule_evaluation import RUN_MODE_APPLY, RUN_MODE_DRY_RUN, BusinessRuleRunner, EvaluationContext
from app.services.mock_platform import MockPlatformExecutorService
from app.services.runtime import RuntimeTaskService
from app.utils import utc_now


class MockPlatformSyncLabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.runtime_db = root / "runtime.sqlite3"
        self.mock_db = root / "mock_platform.sqlite3"
        self.trade_date = date(2026, 5, 9)
        self.runtime_repository = SQLiteRuntimeRepository(self.runtime_db)
        self.runtime_service = RuntimeTaskService(self.runtime_repository)
        self.runtime_service.init_schema()
        self.mock_repository = MockPlatformRepository(self.mock_db)
        self.mock_repository.init_schema()
        self.executor = MockPlatformExecutorService(
            runtime_repository=self.runtime_repository,
            mock_platform_repository=self.mock_repository,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_seed_default_mock_platform_state(self) -> None:
        inserted = seed_default_mock_platform(self.mock_repository)
        states = self.mock_repository.list_product_states()

        self.assertEqual(inserted, 3)
        self.assertEqual(len(states), 3)
        self.assertEqual(states[0].platform_name, "default_platform")

    def test_executor_dry_run_does_not_modify_platform_or_runtime(self) -> None:
        self._seed_state(price=Decimal("14"), online_status=MOCK_PLATFORM_OFFLINE)
        task = self._create_task(TaskActionType.UPDATE_PRICE, target_price=Decimal("18"))

        summary = self.executor.execute(apply=False)

        self.assertEqual(summary.run_mode, "dry-run")
        self.assertEqual(summary.success_count, 1)
        state = self.mock_repository.get_product_state(platform_name="default_platform", internal_sku="SKU-001")
        self.assertEqual(state.platform_price, Decimal("14"))
        self.assertEqual(self.runtime_repository.get_task(task.task_id).task_status, TaskStatus.PENDING)
        self.assertEqual(self.runtime_repository.list_execution_logs(), [])

    def test_executor_apply_updates_price_status_and_execution_log(self) -> None:
        self._seed_state(price=Decimal("14"), online_status=MOCK_PLATFORM_OFFLINE)
        task = self._create_task(TaskActionType.UPDATE_PRICE, target_price=Decimal("18"))

        summary = self.executor.execute(apply=True)

        self.assertEqual(summary.success_count, 1)
        state = self.mock_repository.get_product_state(platform_name="default_platform", internal_sku="SKU-001")
        self.assertEqual(state.platform_price, Decimal("18"))
        updated = self.runtime_repository.get_task(task.task_id)
        self.assertEqual(updated.task_status, TaskStatus.SUCCESS)
        logs = self.runtime_repository.list_execution_logs(task_id=task.task_id)
        self.assertEqual(len(logs), 1)
        self.assertTrue(logs[0].success_flag)

    def test_executor_apply_set_online_and_set_offline(self) -> None:
        self._seed_state(price=Decimal("14"), online_status=MOCK_PLATFORM_OFFLINE)
        online_task = self._create_task(TaskActionType.SET_ONLINE)
        self.executor.execute(apply=True, task_id=online_task.task_id)
        self.assertEqual(
            self.mock_repository.get_product_state(platform_name="default_platform", internal_sku="SKU-001").platform_online_status,
            MOCK_PLATFORM_ONLINE,
        )

        offline_task = self._create_task(TaskActionType.SET_OFFLINE)
        self.executor.execute(apply=True, task_id=offline_task.task_id)
        self.assertEqual(
            self.mock_repository.get_product_state(platform_name="default_platform", internal_sku="SKU-001").platform_online_status,
            MOCK_PLATFORM_OFFLINE,
        )

    def test_executor_apply_missing_platform_product_fails_task_and_logs_error(self) -> None:
        task = self._create_task(TaskActionType.UPDATE_PRICE, target_price=Decimal("18"))

        summary = self.executor.execute(apply=True)

        self.assertEqual(summary.failed_count, 1)
        updated = self.runtime_repository.get_task(task.task_id)
        self.assertEqual(updated.task_status, TaskStatus.FAILED)
        logs = self.runtime_repository.list_execution_logs(task_id=task.task_id)
        self.assertEqual(len(logs), 1)
        self.assertFalse(logs[0].success_flag)
        self.assertIn("模拟平台商品不存在", logs[0].error_message)

    def test_executor_apply_rejects_illegal_low_price(self) -> None:
        self._seed_state(price=Decimal("14"), online_status=MOCK_PLATFORM_OFFLINE)
        task = self._create_task(TaskActionType.UPDATE_PRICE, target_price=Decimal("0.5"))

        summary = self.executor.execute(apply=True)

        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(self.runtime_repository.get_task(task.task_id).task_status, TaskStatus.FAILED)
        self.assertEqual(
            self.mock_repository.get_product_state(platform_name="default_platform", internal_sku="SKU-001").platform_price,
            Decimal("14"),
        )

    def test_platform_sync_evaluator_detects_price_mismatch_and_apply_is_idempotent(self) -> None:
        self._seed_state(price=Decimal("14"), online_status=MOCK_PLATFORM_ONLINE, stock_qty=10)
        self._create_task(TaskActionType.UPDATE_PRICE, target_price=Decimal("18"))
        runner = BusinessRuleRunner(self.runtime_repository)

        dry_run = runner.run("platform_sync", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(dry_run.inserted_tasks_count, 0)
        items = self.runtime_repository.list_script_run_items(dry_run.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "review_task")
        self.assertEqual(items[0].decision_trace["mismatch_type"], "price_mismatch")

        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            first = runner.run("platform_sync", self._context(RUN_MODE_APPLY))
            second = runner.run("platform_sync", self._context(RUN_MODE_APPLY))

        self.assertEqual(first.inserted_review_tasks_count, 1)
        self.assertEqual(first.inserted_notification_logs_count, 1)
        self.assertEqual(second.inserted_review_tasks_count, 0)
        second_items = self.runtime_repository.list_script_run_items(second.script_run.script_run_id)
        self.assertEqual(second_items[0].item_status, "skipped")

    def test_platform_sync_evaluator_detects_listing_and_stock_mismatch(self) -> None:
        self._seed_state(price=Decimal("14"), online_status=MOCK_PLATFORM_OFFLINE, stock_qty=0)
        self._create_task(TaskActionType.SET_ONLINE)
        runner = BusinessRuleRunner(self.runtime_repository)

        summary = runner.run("platform_sync", self._context(RUN_MODE_DRY_RUN))

        items = self.runtime_repository.list_script_run_items(summary.script_run.script_run_id)
        mismatch_types = {item.decision_trace.get("mismatch_type") for item in items}
        self.assertIn("listing_status_mismatch", mismatch_types)
        self.assertIn("stock_mismatch", mismatch_types)

    def test_platform_sync_default_platform_does_not_scan_other_platform_tasks(self) -> None:
        self.mock_repository.upsert_product_states(
            [
                MockPlatformProductState(
                    platform_name="other_platform",
                    internal_sku="SKU-001",
                    platform_sku="MOCK-SKU-OTHER",
                    product_name="艾莎",
                    grade="A",
                    platform_price=Decimal("10"),
                    platform_online_status=MOCK_PLATFORM_ONLINE,
                    platform_stock_qty=10,
                    last_synced_at=datetime.now(),
                    last_platform_update_at=datetime.now(),
                )
            ]
        )
        self._create_task(TaskActionType.UPDATE_PRICE, target_price=Decimal("18"), platform_name="other_platform")
        runner = BusinessRuleRunner(self.runtime_repository)

        summary = runner.run("platform_sync", self._context(RUN_MODE_DRY_RUN))

        items = self.runtime_repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "skipped")
        self.assertEqual(items[0].decision_trace["skip_reason"], "no_platform_tasks")

    def _seed_state(
        self,
        *,
        price: Decimal,
        online_status: str,
        stock_qty: int = 10,
    ) -> None:
        now = datetime.now()
        self.mock_repository.upsert_product_states(
            [
                MockPlatformProductState(
                    platform_name="default_platform",
                    internal_sku="SKU-001",
                    platform_sku="MOCK-SKU-001",
                    product_name="艾莎",
                    grade="A",
                    platform_price=price,
                    platform_online_status=online_status,
                    platform_stock_qty=stock_qty,
                    last_synced_at=now,
                    last_platform_update_at=now,
                )
            ]
        )

    def _create_task(
        self,
        action_type: TaskActionType,
        *,
        target_price: Decimal | None = None,
        platform_name: str = "default_platform",
    ) -> Task:
        task = Task(
            task_id=f"TASK-{action_type.value}-{datetime.now().timestamp()}",
            internal_sku="SKU-001",
            platform_name=platform_name,
            action_type=action_type,
            priority=1,
            task_status=TaskStatus.PENDING,
            created_at=utc_now(),
            target_price=target_price,
            target_status=action_type.value if action_type in {TaskActionType.SET_ONLINE, TaskActionType.SET_OFFLINE} else None,
            trade_date=self.trade_date,
            scope_type="sku",
            scope_key="SKU-001",
            dedupe_key=f"test|{platform_name}|{action_type.value}|{target_price or ''}|{datetime.now().timestamp()}",
        )
        self.runtime_service.create_tasks([task], trade_date=self.trade_date)
        return task

    def _context(self, run_mode: str) -> EvaluationContext:
        return EvaluationContext(
            trade_date=self.trade_date,
            runtime_db_path=self.runtime_db,
            run_mode=run_mode,
            now=datetime(2026, 5, 8, 10, 0),
            mock_platform_db_path=self.mock_db,
            platform_name="default_platform",
            created_by="test",
        )


if __name__ == "__main__":
    unittest.main()
