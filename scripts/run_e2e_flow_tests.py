from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook

from app.enums import (
    NotificationSendStatus,
    ReviewTaskStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.models import MockPlatformProductState, Task
from app.repositories.mock_platform_repository import MockPlatformRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    CAPACITY_PLAN_HEADERS,
    COLD_STORAGE_STATUS_HEADERS,
    HARVEST_FORECAST_HEADERS,
    LISTING_RULE_HEADERS,
    PRICE_RULE_HEADERS,
    PRODUCT_HEADERS,
)
from app.services.business_rule_evaluation import RUN_MODE_APPLY, RUN_MODE_DRY_RUN, BusinessRuleRunner, EvaluationContext
from app.services.mock_platform import MockPlatformExecutorService
from app.services.runtime import ReviewTaskService, RuntimeTaskService
from app.services.workflow import WorkflowInputs, generate_tasks_from_sources, source_task_status_for_review_resolution
from app.utils import utc_now


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "data" / "runtime" / "e2e_runs"
REPORT_ROOT = ROOT / "docs" / "reports"
DEFAULT_PLATFORM = "default_platform"
TRADE_DATE = date(2026, 5, 12)


@dataclass(slots=True)
class StepResult:
    name: str
    status: str
    details: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScenarioResult:
    name: str
    status: str
    steps: list[StepResult] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


class E2EFlowRunner:
    def __init__(self, *, notification_mode: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = RUN_ROOT / stamp
        self.input_dir = self.run_dir / "inputs"
        self.report_path = REPORT_ROOT / f"e2e_flow_report_{stamp}.md"
        self.log_path = self.run_dir / "e2e_flow.log"
        self.results: list[ScenarioResult] = []
        self.notification_mode = notification_mode
        self.started_at = datetime.now()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.input_dir.mkdir(parents=True, exist_ok=True)
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        if notification_mode == "mock":
            os.environ["DEFAULT_NOTIFICATION_CHANNEL"] = "mock"
            os.environ.setdefault("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role")
            os.environ.setdefault("DEFAULT_NOTIFICATION_RECIPIENT", "operations")

    def run(self) -> int:
        self._log("PRA 端到端流程测试开始")
        self._log(f"run_dir={self.run_dir}")
        self._log(f"notification_channel={self.notification_channel}")

        scenarios = [
            self.scenario_listing_offline_review_execute_sync,
            self.scenario_price_rule_update_execute_sync_clean,
            self.scenario_external_price_mismatch_review,
            self.scenario_platform_stock_zero_review,
            self.scenario_capacity_shortage_notification_review,
            self.scenario_cold_storage_over_capacity_notification_review,
        ]
        for scenario in scenarios:
            self.results.append(scenario())
        self._write_report()
        failed = [result for result in self.results if result.status != "PASS"]
        self._log(f"测试完成 pass={len(self.results) - len(failed)} failed={len(failed)}")
        return 1 if failed else 0

    @property
    def notification_channel(self) -> str:
        return os.environ.get("DEFAULT_NOTIFICATION_CHANNEL", "mock")

    def scenario_listing_offline_review_execute_sync(self) -> ScenarioResult:
        name = "库存不足 → 上下架规则建议下架 → 人工复核 → Mock 平台下架 → 同步确认"
        result = ScenarioResult(name=name, status="PASS")
        env = self._new_env("listing_offline")
        try:
            products_path = env.input_dir / "products.xlsx"
            listing_rules_path = env.input_dir / "listing_rules.xlsx"
            self._write_workbook(
                products_path,
                PRODUCT_HEADERS,
                [["SKU-LOW", "艾莎", "A", "65", "扎", 10, 0, True, 14, 15, "", "", ""]],
            )
            self._write_workbook(
                listing_rules_path,
                LISTING_RULE_HEADERS,
                [["LIST-LOW", "库存不足下架", "*", "*", "*", 0, "stock_below_offline", True, 1, ""]],
            )
            env.mock_repo.upsert_product_states(
                [
                    MockPlatformProductState(
                        platform_name=DEFAULT_PLATFORM,
                        internal_sku="SKU-LOW",
                        platform_sku="MP-SKU-LOW",
                        product_name="艾莎",
                        grade="A",
                        platform_price=Decimal("18"),
                        platform_online_status="online",
                        platform_stock_qty=20,
                    )
                ]
            )
            summary = env.runner.run(
                "listing_rules",
                env.context(
                    RUN_MODE_APPLY,
                    products_path=products_path,
                    listing_rules_path=listing_rules_path,
                ),
            )
            result.steps.append(
                self._step(
                    "上下架规则生成复核",
                    summary.inserted_review_tasks_count == 1,
                    f"script_run_id={summary.script_run.script_run_id}",
                    f"inserted_reviews={summary.inserted_review_tasks_count}",
                    f"notifications={summary.inserted_notification_logs_count}",
                )
            )
            resolved_count = self._resolve_pending_reviews(env, note="E2E：同意库存不足下架")
            result.steps.append(self._step("人工复核处理", resolved_count == 1, f"resolved_reviews={resolved_count}"))

            offline_task = self._create_runtime_task(
                env,
                internal_sku="SKU-LOW",
                action_type=TaskActionType.SET_OFFLINE,
                target_status=TaskActionType.SET_OFFLINE.value,
                reason="E2E：复核后生成 Mock 平台下架任务",
            )
            execution = env.executor.execute(apply=True, task_id=offline_task.task_id)
            platform_state = env.mock_repo.get_product_state(platform_name=DEFAULT_PLATFORM, internal_sku="SKU-LOW")
            result.steps.append(
                self._step(
                    "Mock 平台下架执行",
                    execution.success_count == 1 and platform_state is not None and platform_state.platform_online_status == "offline",
                    f"task_id={offline_task.task_id}",
                    f"execution_success={execution.success_count}",
                    f"platform_status={platform_state.platform_online_status if platform_state else '-'}",
                )
            )
            sync = env.runner.run("platform_sync", env.context(RUN_MODE_DRY_RUN))
            matched = any(item.proposal_type == "skipped" and item.decision_trace.get("skip_reason") == "platform_state_matched" for item in sync.items)
            result.steps.append(
                self._step(
                    "同步确认无异常",
                    matched,
                    f"script_run_id={sync.script_run.script_run_id}",
                    f"items={[item.proposal_type for item in sync.items]}",
                )
            )
        except Exception as exc:
            self._fail_result(result, exc)
        return self._finalize_result(result)

    def scenario_price_rule_update_execute_sync_clean(self) -> ScenarioResult:
        name = "价格规则生成改价 → Mock 平台改价成功 → 同步无异常"
        result = ScenarioResult(name=name, status="PASS")
        env = self._new_env("price_update")
        try:
            products_path = env.input_dir / "products.xlsx"
            price_rules_path = env.input_dir / "price_rules.xlsx"
            listing_rules_path = env.input_dir / "listing_rules.xlsx"
            output_path = env.input_dir / "generated_tasks.xlsx"
            self._write_workbook(
                products_path,
                PRODUCT_HEADERS,
                [["SKU-PRICE", "艾莎", "A", "65", "扎", 10, 50, True, 10, 18, "", "", ""]],
            )
            self._write_workbook(
                price_rules_path,
                PRICE_RULE_HEADERS,
                [["RULE-PRICE", "艾莎 A 级改价", "艾莎", "A", "*", "fixed_markup", 8, "", "none", "", True, 1, ""]],
            )
            self._write_workbook(
                listing_rules_path,
                LISTING_RULE_HEADERS,
                [["LIST-NOOP", "不匹配的上架规则", "不存在", "*", "*", 0, "stock_below_offline", True, 1, ""]],
            )
            env.mock_repo.upsert_product_states(
                [
                    MockPlatformProductState(
                        platform_name=DEFAULT_PLATFORM,
                        internal_sku="SKU-PRICE",
                        platform_sku="MP-SKU-PRICE",
                        product_name="艾莎",
                        grade="A",
                        platform_price=Decimal("10"),
                        platform_online_status="online",
                        platform_stock_qty=40,
                    )
                ]
            )
            generated = generate_tasks_from_sources(
                WorkflowInputs(
                    products_path=products_path,
                    price_rules_path=price_rules_path,
                    listing_rules_path=listing_rules_path,
                    output_path=output_path,
                    platform_name=DEFAULT_PLATFORM,
                )
            )
            update_tasks = [task for task in generated.tasks if task.action_type == TaskActionType.UPDATE_PRICE]
            inserted = env.task_service.create_tasks(update_tasks, trade_date=TRADE_DATE)
            result.steps.append(
                self._step(
                    "价格规则生成改价任务",
                    len(update_tasks) == 1 and inserted == 1 and str(update_tasks[0].target_price) == "18.00",
                    f"generated_update_tasks={len(update_tasks)}",
                    f"target_price={update_tasks[0].target_price if update_tasks else '-'}",
                )
            )
            execution = env.executor.execute(apply=True, task_id=update_tasks[0].task_id)
            platform_state = env.mock_repo.get_product_state(platform_name=DEFAULT_PLATFORM, internal_sku="SKU-PRICE")
            result.steps.append(
                self._step(
                    "Mock 平台改价成功",
                    execution.success_count == 1
                    and platform_state is not None
                    and Decimal(str(platform_state.platform_price)) == Decimal("18"),
                    f"task_id={update_tasks[0].task_id}",
                    f"platform_price={platform_state.platform_price if platform_state else '-'}",
                )
            )
            sync = env.runner.run("platform_sync", env.context(RUN_MODE_DRY_RUN))
            matched = any(item.proposal_type == "skipped" and item.decision_trace.get("skip_reason") == "platform_state_matched" for item in sync.items)
            result.steps.append(
                self._step(
                    "同步无异常",
                    matched,
                    f"script_run_id={sync.script_run.script_run_id}",
                    f"items={[item.proposal_type for item in sync.items]}",
                )
            )
        except Exception as exc:
            self._fail_result(result, exc)
        return self._finalize_result(result)

    def scenario_external_price_mismatch_review(self) -> ScenarioResult:
        name = "Mock 平台外部改价 → platform_sync 发现差异 → 人工复核"
        result = ScenarioResult(name=name, status="PASS")
        env = self._new_env("external_price_mismatch")
        try:
            task = self._create_runtime_task(
                env,
                internal_sku="SKU-PRICE-DIFF",
                action_type=TaskActionType.UPDATE_PRICE,
                target_price=Decimal("18"),
                reason="E2E：PRA 期望平台价格为 18",
            )
            env.mock_repo.upsert_product_states(
                [
                    MockPlatformProductState(
                        platform_name=DEFAULT_PLATFORM,
                        internal_sku="SKU-PRICE-DIFF",
                        platform_sku="MP-SKU-PRICE-DIFF",
                        product_name="艾莎",
                        grade="A",
                        platform_price=Decimal("13"),
                        platform_online_status="online",
                        platform_stock_qty=30,
                    )
                ]
            )
            sync = env.runner.run("platform_sync", env.context(RUN_MODE_APPLY))
            review = self._latest_pending_review(env)
            result.steps.append(
                self._step(
                    "platform_sync 发现价格差异",
                    sync.inserted_review_tasks_count == 1 and review is not None and review.reason.find("价格") >= 0,
                    f"script_run_id={sync.script_run.script_run_id}",
                    f"source_task_id={task.task_id}",
                    f"review_id={review.review_task_id if review else '-'}",
                )
            )
            resolved = self._resolve_pending_reviews(env, note="E2E：确认价格差异，后续人工处理")
            result.steps.append(self._step("人工复核价格差异", resolved == 1, f"resolved_reviews={resolved}"))
        except Exception as exc:
            self._fail_result(result, exc)
        return self._finalize_result(result)

    def scenario_platform_stock_zero_review(self) -> ScenarioResult:
        name = "平台库存为 0 → 生成库存差异复核 → 人工确认"
        result = ScenarioResult(name=name, status="PASS")
        env = self._new_env("platform_stock_zero")
        try:
            task = self._create_runtime_task(
                env,
                internal_sku="SKU-STOCK-ZERO",
                action_type=TaskActionType.SET_ONLINE,
                target_status=TaskActionType.SET_ONLINE.value,
                reason="E2E：PRA 期望商品在线",
            )
            env.mock_repo.upsert_product_states(
                [
                    MockPlatformProductState(
                        platform_name=DEFAULT_PLATFORM,
                        internal_sku="SKU-STOCK-ZERO",
                        platform_sku="MP-SKU-STOCK-ZERO",
                        product_name="艾莎",
                        grade="B",
                        platform_price=Decimal("16"),
                        platform_online_status="online",
                        platform_stock_qty=0,
                    )
                ]
            )
            sync = env.runner.run("platform_sync", env.context(RUN_MODE_APPLY))
            review = self._latest_pending_review(env)
            result.steps.append(
                self._step(
                    "生成库存差异复核",
                    sync.inserted_review_tasks_count == 1 and review is not None and "库存" in review.reason,
                    f"script_run_id={sync.script_run.script_run_id}",
                    f"source_task_id={task.task_id}",
                    f"review_id={review.review_task_id if review else '-'}",
                )
            )
            resolved = self._resolve_pending_reviews(env, note="E2E：确认平台库存为 0")
            result.steps.append(self._step("人工确认库存差异", resolved == 1, f"resolved_reviews={resolved}"))
        except Exception as exc:
            self._fail_result(result, exc)
        return self._finalize_result(result)

    def scenario_capacity_shortage_notification_review(self) -> ScenarioResult:
        name = "产能不足 → 飞书预警 → 人工处理"
        result = ScenarioResult(name=name, status="PASS")
        env = self._new_env("capacity_shortage")
        try:
            harvest_path = env.input_dir / "harvest_forecasts.xlsx"
            capacity_path = env.input_dir / "capacity_plans.xlsx"
            self._write_workbook(
                harvest_path,
                HARVEST_FORECAST_HEADERS,
                [["HF-CAP", TRADE_DATE, TRADE_DATE, "艾莎", "A", 420, 380, 460, 0.8, "manual", datetime.now(), ""]],
            )
            self._write_workbook(
                capacity_path,
                CAPACITY_PLAN_HEADERS,
                [[TRADE_DATE, 250, 100, 0, 250, "proportional_by_forecast", True, "E2E capacity shortage"]],
            )
            summary = env.runner.run(
                "capacity_warning",
                env.context(RUN_MODE_APPLY, harvest_forecasts_path=harvest_path, capacity_plan_path=capacity_path),
            )
            notifications = env.repository.list_notification_logs()
            ok_notifications = [log for log in notifications if log.send_status == NotificationSendStatus.SUCCESS.value]
            result.steps.append(
                self._step(
                    "产能不足生成预警通知",
                    summary.inserted_review_tasks_count == 2 and len(ok_notifications) >= 1,
                    f"script_run_id={summary.script_run.script_run_id}",
                    f"reviews={summary.inserted_review_tasks_count}",
                    f"notifications={len(notifications)}",
                    f"notification_channel={self.notification_channel}",
                    f"success_notifications={len(ok_notifications)}",
                )
            )
            resolved = self._resolve_pending_reviews(env, note="E2E：产能不足预警已人工处理")
            result.steps.append(self._step("人工处理产能预警", resolved == 2, f"resolved_reviews={resolved}"))
        except Exception as exc:
            self._fail_result(result, exc)
        return self._finalize_result(result)

    def scenario_cold_storage_over_capacity_notification_review(self) -> ScenarioResult:
        name = "冷库超容 → 飞书预警 → 人工处理"
        result = ScenarioResult(name=name, status="PASS")
        env = self._new_env("cold_storage_over_capacity")
        try:
            cold_path = env.input_dir / "cold_storage_status.xlsx"
            self._write_workbook(
                cold_path,
                COLD_STORAGE_STATUS_HEADERS,
                [[TRADE_DATE, 500, 480, 80, 0, 50, 560, -60, True, "E2E cold storage over capacity"]],
            )
            summary = env.runner.run(
                "cold_storage",
                env.context(RUN_MODE_APPLY, cold_storage_status_path=cold_path),
            )
            notifications = env.repository.list_notification_logs()
            ok_notifications = [log for log in notifications if log.send_status == NotificationSendStatus.SUCCESS.value]
            result.steps.append(
                self._step(
                    "冷库超容生成预警通知",
                    summary.inserted_review_tasks_count == 1 and len(ok_notifications) >= 1,
                    f"script_run_id={summary.script_run.script_run_id}",
                    f"reviews={summary.inserted_review_tasks_count}",
                    f"notifications={len(notifications)}",
                    f"notification_channel={self.notification_channel}",
                    f"success_notifications={len(ok_notifications)}",
                )
            )
            resolved = self._resolve_pending_reviews(env, note="E2E：冷库超容预警已人工处理")
            result.steps.append(self._step("人工处理冷库预警", resolved == 1, f"resolved_reviews={resolved}"))
        except Exception as exc:
            self._fail_result(result, exc)
        return self._finalize_result(result)

    def _new_env(self, scenario_id: str) -> "ScenarioEnv":
        scenario_dir = self.run_dir / scenario_id
        input_dir = scenario_dir / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        runtime_db = scenario_dir / "runtime.sqlite3"
        mock_db = scenario_dir / "mock_platform.sqlite3"
        repository = SQLiteRuntimeRepository(runtime_db)
        repository.init_schema()
        mock_repo = MockPlatformRepository(mock_db)
        mock_repo.init_schema()
        task_service = RuntimeTaskService(repository)
        review_service = ReviewTaskService(repository, runtime_task_service=task_service)
        runner = BusinessRuleRunner(repository)
        executor = MockPlatformExecutorService(runtime_repository=repository, mock_platform_repository=mock_repo)
        return ScenarioEnv(
            scenario_id=scenario_id,
            input_dir=input_dir,
            runtime_db=runtime_db,
            mock_db=mock_db,
            repository=repository,
            mock_repo=mock_repo,
            task_service=task_service,
            review_service=review_service,
            runner=runner,
            executor=executor,
        )

    def _create_runtime_task(
        self,
        env: "ScenarioEnv",
        *,
        internal_sku: str,
        action_type: TaskActionType,
        target_price: Decimal | None = None,
        target_status: str | None = None,
        reason: str,
    ) -> Task:
        task = Task(
            task_id=uuid4().hex[:12],
            internal_sku=internal_sku,
            platform_name=DEFAULT_PLATFORM,
            action_type=action_type,
            priority=1,
            task_status=TaskStatus.PENDING,
            created_at=utc_now(),
            origin_type=TaskOriginType.MANUAL,
            target_price=target_price,
            target_status=target_status,
            result_message=reason,
            trade_date=TRADE_DATE,
            scope_type="sku",
            scope_key=internal_sku,
            dedupe_key=f"e2e|{env.scenario_id}|{internal_sku}|{action_type.value}|{target_price or target_status or 'none'}",
            decision_trace={"source": "scripts/run_e2e_flow_tests.py", "reason": reason},
        )
        inserted = env.task_service.create_tasks([task], trade_date=TRADE_DATE)
        if inserted != 1:
            raise RuntimeError(f"runtime task not inserted: {task.dedupe_key}")
        return task

    def _resolve_pending_reviews(self, env: "ScenarioEnv", *, note: str) -> int:
        pending = env.review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)
        resolved = 0
        for review in pending:
            source_task = env.task_service.get_task(review.source_task_id) if review.source_task_id else None
            source_status = source_task_status_for_review_resolution(source_task, ReviewTaskStatus.APPROVED)
            env.review_service.resolve_review_task(
                review_task_id=review.review_task_id,
                status=ReviewTaskStatus.APPROVED,
                actor="e2e_operator",
                actor_source="system_e2e",
                note=note,
                resolution_payload={"e2e": True, "scenario": env.scenario_id},
                source_task_status=source_status,
            )
            resolved += 1
        return resolved

    def _latest_pending_review(self, env: "ScenarioEnv"):
        pending = env.review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)
        return pending[-1] if pending else None

    def _step(self, name: str, ok: bool, *details: str) -> StepResult:
        status = "OK" if ok else "FAILED"
        self._log(f"[{status}] {name} {' | '.join(details)}")
        return StepResult(name=name, status=status, details=[_redact(detail) for detail in details])

    def _fail_result(self, result: ScenarioResult, exc: Exception) -> None:
        message = _redact(f"{exc.__class__.__name__}: {exc}")
        result.steps.append(StepResult(name="场景执行异常", status="FAILED", details=[message]))
        result.status = "FAILED"
        self._log(f"[FAILED] {result.name} {message}")

    def _finalize_result(self, result: ScenarioResult) -> ScenarioResult:
        if any(step.status != "OK" for step in result.steps):
            result.status = "FAILED"
        self._log(f"场景完成：{result.name} => {result.status}")
        return result

    def _write_workbook(self, path: Path, headers: list[str], rows: list[list[object]]) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "data"
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(path)
        self._log(f"写入工作簿 {path.name} headers={headers[:4]} rows={len(rows)}")

    def _write_report(self) -> None:
        lines: list[str] = [
            "# PRA 端到端流程测试报告",
            "",
            f"- 开始时间：{self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 测试目录：`{_redact(str(self.run_dir))}`",
            f"- 日志文件：`{_redact(str(self.log_path))}`",
            f"- 通知模式：`{self.notification_channel}`",
            f"- 真实飞书配置：`{'是' if _real_feishu_configured() else '否'}`",
            "",
            "## 测试结论",
            "",
        ]
        passed = sum(1 for result in self.results if result.status == "PASS")
        failed = len(self.results) - passed
        lines.append(f"- 通过场景：{passed}")
        lines.append(f"- 失败场景：{failed}")
        lines.append("")
        for result in self.results:
            lines.extend([f"## {result.name}", "", f"状态：**{result.status}**", ""])
            for step in result.steps:
                lines.append(f"- `{step.status}` {step.name}")
                for detail in step.details:
                    lines.append(f"  - {detail}")
            lines.append("")
        lines.extend(
            [
                "## 边界确认",
                "",
                "- 本脚本使用独立测试目录，不污染 `data/runtime/pra_runtime.sqlite3`。",
                "- Mock 平台状态保存到独立 `mock_platform.sqlite3`。",
                "- 未接真实销售平台、真实 RPA 或 AI Agent。",
                "- 平台库存差异只生成复核，不回写 PRA 公共库存。",
                "- 报告和日志会脱敏原始复核链接参数、webhook、secret、mobile review URL。",
                "",
            ]
        )
        self.report_path.write_text("\n".join(lines), encoding="utf-8")
        self._log(f"报告写入 {self.report_path}")

    def _log(self, message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} {_redact(message)}"
        print(line)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


@dataclass(slots=True)
class ScenarioEnv:
    scenario_id: str
    input_dir: Path
    runtime_db: Path
    mock_db: Path
    repository: SQLiteRuntimeRepository
    mock_repo: MockPlatformRepository
    task_service: RuntimeTaskService
    review_service: ReviewTaskService
    runner: BusinessRuleRunner
    executor: MockPlatformExecutorService

    def context(self, run_mode: str, **overrides) -> EvaluationContext:
        return EvaluationContext(
            trade_date=TRADE_DATE,
            runtime_db_path=self.runtime_db,
            run_mode=run_mode,
            now=datetime.now(),
            mock_platform_db_path=self.mock_db,
            platform_name=DEFAULT_PLATFORM,
            created_by="e2e",
            **overrides,
        )


def _redact(value: str) -> str:
    text = str(value)
    text = re.sub(r"token=[^\s)&]+", "token=***", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://[^\s\"'<>]*open\.feishu\.cn[^\s\"'<>]*", "[webhook_redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://[^\s\"'<>]*/mobile/review/[^\s\"'<>]+", "[mobile_review_url_redacted]", text, flags=re.IGNORECASE)
    for key in ["REVIEW_TOKEN_SECRET", "FEISHU_WEBHOOK_SECRET", "RUNTIME_ADMIN_PASSWORD"]:
        text = text.replace(key, "[secret_key_redacted]")
    return text


def _real_feishu_configured() -> bool:
    return (
        os.environ.get("DEFAULT_NOTIFICATION_CHANNEL") == "feishu"
        and bool(os.environ.get("FEISHU_WEBHOOK_URL"))
        and bool(os.environ.get("REVIEW_TOKEN_SECRET"))
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 PRA 主控流程端到端测试。")
    parser.add_argument(
        "--notification-mode",
        choices=["mock", "env"],
        default="mock",
        help="mock=强制模拟通知；env=使用当前环境变量，可真实发送飞书。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = E2EFlowRunner(notification_mode=args.notification_mode)
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
