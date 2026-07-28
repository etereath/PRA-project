from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from app.enums import TaskActionType, TaskOriginType, TaskStatus
from app.exceptions import ValidationError
from app.models import Task
from app.repositories.workbook_repository import (
    TASK_HEADERS,
    export_tasks,
    load_tasks,
)
from app.services.inventory_planning import InventoryPlanningService
from app.services.manual_intervention import ManualInterventionService
from app.services.workflow import ManualInterventionInputs, list_manual_intervention_tasks, resolve_manual_intervention_task


def _manual_task(task_id: str = "TASK-001") -> Task:
    return Task(
        task_id=task_id,
        internal_sku="SKU-001",
        platform_name="default_platform",
        action_type=TaskActionType.MANUAL_PRICE_REVIEW,
        priority=3,
        task_status=TaskStatus.MANUAL_REVIEW,
        created_at=datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc),
        origin_type=TaskOriginType.MANUAL,
        target_price=Decimal("12.00"),
        result_message="needs review",
    )


class ManualAndStrategyTests(unittest.TestCase):
    def test_legacy_task_workbook_does_not_guess_manual_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "legacy_tasks.xlsx"
            export_tasks(path, [_manual_task("TASK-LEGACY")])
            workbook = load_workbook(path)
            sheet = workbook.active
            origin_column = TASK_HEADERS.index("origin_type") + 1
            for _ in range(4):
                sheet.delete_cols(origin_column)
            workbook.save(path)

            loaded = load_tasks(path)

            self.assertEqual(len(loaded), 1)
            self.assertIs(loaded[0].origin_type, TaskOriginType.LEGACY)
            self.assertIsNone(loaded[0].origin_ref_id)

    def test_inventory_strategy_can_switch(self) -> None:
        conservative = InventoryPlanningService(strategy_name="conservative_v1").build_inventory_plan(
            product=type("ProductLike", (), {"product_name": "rose", "grade": "A", "current_stock": 20})(),
            forecast=type("ForecastLike", (), {"predicted_harvest_qty": 80, "forecast_group_key": "rose::A"})(),
            trade_date=datetime(2026, 5, 4).date(),
            allocated_packing_capacity_qty=120,
            reserved_qty=0,
            safety_buffer_qty=0,
            field_buffer_qty=50,
        )
        balanced = InventoryPlanningService(strategy_name="balanced_v1").build_inventory_plan(
            product=type("ProductLike", (), {"product_name": "rose", "grade": "A", "current_stock": 20})(),
            forecast=type("ForecastLike", (), {"predicted_harvest_qty": 80, "forecast_group_key": "rose::A"})(),
            trade_date=datetime(2026, 5, 4).date(),
            allocated_packing_capacity_qty=120,
            reserved_qty=0,
            safety_buffer_qty=0,
            field_buffer_qty=50,
        )
        self.assertEqual(conservative.committable_qty, 100)
        self.assertEqual(balanced.committable_qty, 120)
        self.assertEqual(conservative.decision_trace["inventory_strategy"], "conservative_v1")
        self.assertEqual(balanced.decision_trace["inventory_strategy"], "balanced_v1")

    def test_manual_intervention_service_filters_and_resolves(self) -> None:
        service = ManualInterventionService()
        tasks = [
            _manual_task(),
            Task(
                task_id="TASK-002",
                internal_sku="SKU-002",
                platform_name="default_platform",
                action_type=TaskActionType.UPDATE_PRICE,
                priority=1,
                task_status=TaskStatus.PENDING,
                created_at=datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc),
                origin_type=TaskOriginType.MANUAL,
            ),
        ]
        open_tasks = service.list_open_tasks(tasks)
        self.assertEqual(len(open_tasks), 1)

        updated = service.resolve_task(
            tasks,
            task_id="TASK-001",
            decision="approve",
            actor="alice",
            note="looks good",
            resolved_at=datetime(2026, 5, 3, 13, 0),
        )
        resolved = next(task for task in updated if task.task_id == "TASK-001")
        self.assertEqual(resolved.task_status, TaskStatus.SUCCESS)
        self.assertIn("alice", resolved.result_message)
        self.assertEqual(resolved.decision_trace["manual_intervention"]["decision"], "approve")

    def test_manual_intervention_workflow_is_deprecated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "tasks.xlsx"
            output = Path(tmpdir) / "resolved_tasks.xlsx"
            export_tasks(source, [_manual_task("TASK-100")])
            round_tripped = load_tasks(source)
            self.assertEqual(
                round_tripped[0].origin_type,
                TaskOriginType.MANUAL,
            )

            open_tasks = list_manual_intervention_tasks(source)
            self.assertEqual(len(open_tasks), 1)

            with self.assertRaises(ValidationError):
                resolve_manual_intervention_task(
                    ManualInterventionInputs(
                        tasks_path=source,
                        output_path=output,
                        task_id="TASK-100",
                        decision="acknowledge",
                        actor="operator",
                        note="noted",
                    )
                )


if __name__ == "__main__":
    unittest.main()
