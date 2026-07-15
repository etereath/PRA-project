from __future__ import annotations

import argparse
from pathlib import Path

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import create_template_workbooks
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION
from app.services.ai import MockAISuggestionProvider
from app.services.pricing import PricingService
from app.services.runtime import DEFAULT_RUNTIME_DB
from app.services.workflow import (
    ExpireReviewTasksInputs,
    ExecutionSimulationInputs,
    RuntimeDatabaseInputs,
    RuntimeReviewResolutionInputs,
    WorkflowInputs,
    expire_runtime_review_tasks,
    generate_runtime_tasks_from_sources,
    generate_tasks_from_sources,
    init_runtime_database,
    list_manual_intervention_tasks,
    list_runtime_review_tasks,
    list_runtime_task_history,
    list_runtime_tasks,
    preview_tasks_from_sources,
    resolve_runtime_review_task,
    simulate_execution_from_tasks,
    validate_sources,
)
from app.utils import parse_date, parse_datetime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PRA MVP 命令行工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    templates_parser = subparsers.add_parser("templates", help="创建 Excel 模板工作簿")
    templates_parser.add_argument("--output-dir", required=True, type=Path)

    validate_parser = subparsers.add_parser("validate", help="校验输入工作簿")
    _add_source_args(validate_parser)

    import_parser = subparsers.add_parser("import-data", help="校验并输出输入数据摘要")
    _add_source_args(import_parser)

    preview_parser = subparsers.add_parser("preview-tasks", help="预览任务但不写出文件")
    _add_source_args(preview_parser)
    preview_parser.add_argument("--platform", default="default_platform")
    preview_parser.add_argument("--use-mock-ai", action="store_true")

    generate_parser = subparsers.add_parser("generate-tasks", help="生成任务 Excel 工作簿")
    _add_source_args(generate_parser)
    generate_parser.add_argument("--output", required=True, type=Path)
    generate_parser.add_argument("--platform", default="default_platform")
    generate_parser.add_argument("--use-mock-ai", action="store_true")

    ai_parser = subparsers.add_parser("mock-ai-decision", help="预览单个 SKU 的 Mock AI 定价决策")
    _add_source_args(ai_parser)
    ai_parser.add_argument("--sku", required=True)
    ai_parser.add_argument("--platform", default="default_platform")

    execution_parser = subparsers.add_parser("simulate-execution", help="模拟执行 Excel 任务并输出执行日志")
    execution_parser.add_argument("--tasks", required=True, type=Path)
    execution_parser.add_argument("--logs-output", required=True, type=Path)
    execution_parser.add_argument("--updated-tasks-output", type=Path)
    execution_parser.add_argument("--executor-name", default="mock_executor")

    list_manual_parser = subparsers.add_parser("list-manual-tasks", help="列出旧 Excel 人工介入任务（只读兼容）")
    list_manual_parser.add_argument("--tasks", required=True, type=Path)

    resolve_manual_parser = subparsers.add_parser("resolve-manual-task", help="旧 Excel 人工介入处理入口（已弃用）")
    resolve_manual_parser.add_argument("--tasks", required=True, type=Path)
    resolve_manual_parser.add_argument("--output", required=True, type=Path)
    resolve_manual_parser.add_argument("--task-id", required=True)
    resolve_manual_parser.add_argument("--decision", required=True)
    resolve_manual_parser.add_argument("--actor", default="manual_operator")
    resolve_manual_parser.add_argument("--note", default="")

    init_runtime_parser = subparsers.add_parser("init-runtime-db", help="初始化 SQLite 运行态数据库")
    init_runtime_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)

    health_parser = subparsers.add_parser(
        "health",
        aliases=["check-runtime-health"],
        help="检查 Runtime Schema v5 健康状态",
    )
    health_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)

    generate_runtime_parser = subparsers.add_parser("generate-runtime-tasks", help="生成任务并写入 SQLite 运行态数据库")
    _add_source_args(generate_runtime_parser)
    generate_runtime_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    generate_runtime_parser.add_argument("--platform", default="default_platform")
    generate_runtime_parser.add_argument("--use-mock-ai", action="store_true")

    list_tasks_parser = subparsers.add_parser("list-tasks", help="列出 SQLite 运行态任务")
    list_tasks_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    list_tasks_parser.add_argument("--trade-date")
    list_tasks_parser.add_argument("--status")
    list_tasks_parser.add_argument("--action-type")

    history_parser = subparsers.add_parser("show-task-history", help="查看任务状态历史")
    history_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    history_parser.add_argument("--task-id", required=True)

    list_reviews_parser = subparsers.add_parser("list-review-tasks", help="列出 SQLite 人工复核任务")
    list_reviews_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    list_reviews_parser.add_argument("--trade-date")
    list_reviews_parser.add_argument("--status")

    resolve_review_parser = subparsers.add_parser("resolve-review-task", help="处理 SQLite 人工复核任务")
    resolve_review_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    resolve_review_parser.add_argument("--review-task-id", required=True)
    resolve_review_parser.add_argument("--status", required=True)
    resolve_review_parser.add_argument("--actor", default="manual_operator")
    resolve_review_parser.add_argument("--note", default="")
    resolve_review_parser.add_argument("--source-task-status")

    expire_reviews_parser = subparsers.add_parser("expire-review-tasks", help="处理超时的 SQLite pending 复核任务")
    expire_reviews_parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    expire_reviews_parser.add_argument("--apply", action="store_true")
    expire_reviews_parser.add_argument("--now")
    expire_reviews_parser.add_argument("--enable-notification", action="store_true")

    web_parser = subparsers.add_parser("serve-web", help="启动简单 Web 管理页")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", default=8765, type=int)

    return parser


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--products", required=True, type=Path)
    parser.add_argument("--price-rules", required=True, type=Path)
    parser.add_argument("--listing-rules", required=True, type=Path)
    parser.add_argument("--harvest-forecasts", type=Path)
    parser.add_argument("--price-forecasts", type=Path)
    parser.add_argument("--capacity-plan", type=Path)
    parser.add_argument("--cold-storage-status", type=Path)
    parser.add_argument("--trade-date")
    parser.add_argument("--now")
    parser.add_argument("--inventory-strategy", default="conservative_v1")


def _workflow_inputs(args: argparse.Namespace, *, include_output: bool = False) -> WorkflowInputs:
    return WorkflowInputs(
        products_path=args.products,
        price_rules_path=args.price_rules,
        listing_rules_path=args.listing_rules,
        output_path=args.output if include_output else None,
        platform_name=getattr(args, "platform", "default_platform"),
        use_mock_ai=getattr(args, "use_mock_ai", False),
        harvest_forecasts_path=getattr(args, "harvest_forecasts", None),
        price_forecasts_path=getattr(args, "price_forecasts", None),
        capacity_plan_path=getattr(args, "capacity_plan", None),
        cold_storage_status_path=getattr(args, "cold_storage_status", None),
        trade_date=parse_date(args.trade_date, "trade_date") if getattr(args, "trade_date", None) else None,
        now=parse_datetime(args.now, "now") if getattr(args, "now", None) else None,
        inventory_strategy=getattr(args, "inventory_strategy", "conservative_v1"),
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "templates":
            for path in create_template_workbooks(args.output_dir):
                print(path)
            return 0

        if args.command == "serve-web":
            from app.web import serve

            serve(args.host, args.port)
            return 0

        if args.command in {"health", "check-runtime-health"}:
            repository = SQLiteRuntimeRepository(args.runtime_db)
            health = repository.check_schema_health()
            print(
                f"runtime health: ok={health.ok} "
                f"schema_versions={repository.schema_versions()} "
                f"summary={health.summary}"
            )
            return 0 if health.ok else 1

        if args.command in {"validate", "import-data"}:
            summary = validate_sources(_workflow_inputs(args))
            print(
                f"校验通过：products={len(summary.products)} "
                f"price_rules={summary.price_rules_count} "
                f"listing_rules={summary.listing_rules_count}"
            )
            return 0

        if args.command == "preview-tasks":
            summary = preview_tasks_from_sources(_workflow_inputs(args))
            print(f"任务预览完成：共 {len(summary.tasks)} 条")
            for task in summary.tasks:
                print(_format_task(task))
            return 0

        if args.command == "mock-ai-decision":
            from app.repositories.workbook_repository import load_price_rules, load_products

            products = load_products(args.products)
            price_rules = load_price_rules(args.price_rules)
            product = next((item for item in products if item.internal_sku == args.sku), None)
            if product is None:
                raise ValidationError(f"未找到 SKU: {args.sku}")
            service = PricingService(ai_provider=MockAISuggestionProvider())
            print(service.calculate(product, args.platform, price_rules))
            return 0

        if args.command == "generate-tasks":
            summary = generate_tasks_from_sources(_workflow_inputs(args, include_output=True))
            print(f"已生成 {len(summary.tasks)} 条任务 -> {args.output}")
            return 0

        if args.command == "simulate-execution":
            summary = simulate_execution_from_tasks(
                ExecutionSimulationInputs(
                    tasks_path=args.tasks,
                    logs_output_path=args.logs_output,
                    updated_tasks_output_path=args.updated_tasks_output,
                    executor_name=args.executor_name,
                )
            )
            print(f"已模拟执行 {len(summary.tasks)} 条任务，执行日志输出到 {summary.logs_output_path}")
            if summary.updated_tasks_output_path is not None:
                print(f"更新后的任务文件输出到 {summary.updated_tasks_output_path}")
            return 0

        if args.command == "list-manual-tasks":
            tasks = list_manual_intervention_tasks(args.tasks)
            print("兼容只读入口：旧 Excel 人工介入链路已弃用，请改用 SQLite review_tasks 或 Web /runtime 入口处理。")
            print(f"待人工介入任务：共 {len(tasks)} 条")
            for task in tasks:
                print(_format_task(task))
            return 0

        if args.command == "resolve-manual-task":
            print("旧 Excel 人工介入入口已弃用，不能再执行正式处理。请改用 SQLite review_tasks 或 Web /runtime 入口。")
            return 1

        if args.command == "init-runtime-db":
            versions = init_runtime_database(RuntimeDatabaseInputs(db_path=args.runtime_db))
            print(
                f"运行态数据库已初始化：{args.runtime_db}，schema_versions={versions}，"
                f"latest_runtime_schema_version={LATEST_RUNTIME_SCHEMA_VERSION}"
            )
            return 0

        if args.command == "generate-runtime-tasks":
            summary = generate_runtime_tasks_from_sources(_workflow_inputs(args), db_path=args.runtime_db)
            print(
                f"运行态任务生成完成：planned={len(summary.tasks)} "
                f"inserted_tasks={summary.inserted_tasks_count} "
                f"inserted_review_tasks={summary.inserted_review_tasks_count} "
                f"inserted_notification_logs={summary.inserted_notification_logs_count} "
                f"db={summary.db_path}"
            )
            if summary.notification_errors:
                print("notification_errors:")
                for error in summary.notification_errors:
                    print(f"- {error}")
            return 0

        if args.command == "list-tasks":
            status = TaskStatus(args.status) if args.status else None
            action_type = TaskActionType(args.action_type) if args.action_type else None
            trade_date = parse_date(args.trade_date, "trade_date") if args.trade_date else None
            tasks = list_runtime_tasks(args.runtime_db, trade_date=trade_date, status=status, action_type=action_type)
            print(f"运行态任务：共 {len(tasks)} 条")
            for task in tasks:
                print(_format_task(task))
            return 0

        if args.command == "show-task-history":
            history = list_runtime_task_history(args.runtime_db, args.task_id)
            print(f"任务状态历史：共 {len(history)} 条")
            for item in history:
                from_status = item.from_status.value if item.from_status else "-"
                print(f"- {item.changed_at.isoformat()} | {from_status} -> {item.to_status.value} | {item.changed_by} | {item.reason}")
            return 0

        if args.command == "list-review-tasks":
            status = ReviewTaskStatus(args.status) if args.status else None
            trade_date = parse_date(args.trade_date, "trade_date") if args.trade_date else None
            reviews = list_runtime_review_tasks(args.runtime_db, trade_date=trade_date, status=status)
            print(f"人工复核任务：共 {len(reviews)} 条")
            for review in reviews:
                print(
                    f"- {review.review_task_id} | {review.review_type} | {review.review_status.value} | "
                    f"scope={review.scope_type}:{review.scope_key} | source={review.source_task_id or '-'} | {review.reason}"
                )
            return 0

        if args.command == "resolve-review-task":
            source_status = TaskStatus(args.source_task_status) if args.source_task_status else None
            review = resolve_runtime_review_task(
                RuntimeReviewResolutionInputs(
                    db_path=args.runtime_db,
                    review_task_id=args.review_task_id,
                    status=ReviewTaskStatus(args.status),
                    actor=args.actor,
                    note=args.note,
                    source_task_status=source_status,
                )
            )
            print(f"已处理复核任务 {review.review_task_id} -> {review.review_status.value}")
            return 0

        if args.command == "expire-review-tasks":
            summary = expire_runtime_review_tasks(
                ExpireReviewTasksInputs(
                    db_path=args.runtime_db,
                    apply=bool(args.apply),
                    now=parse_datetime(args.now, "now") if getattr(args, "now", None) else None,
                    enable_notification=bool(args.enable_notification),
                )
            )
            mode = "apply" if args.apply else "dry-run"
            print(
                f"review timeout scan completed: mode={mode} "
                f"scanned_review_tasks={summary.scanned_review_tasks} "
                f"expired_review_tasks={summary.expired_review_tasks} "
                f"expired_source_tasks={summary.expired_source_tasks} "
                f"skipped_source_tasks={summary.skipped_source_tasks} "
                f"notification_logs_created={summary.notification_logs_created}"
            )
            if not args.apply:
                print("dry-run preview only: no review task or source task status was changed.")
            if summary.errors:
                print("errors:")
                for error in summary.errors:
                    print(f"- {error}")
            return 0

        parser.error("未知命令")
        return 2
    except ValidationError as exc:
        print(f"错误：{exc}")
        return 1


def _format_task(task) -> str:
    return (
        f"- {task.task_id} | {task.internal_sku or '-'} | {task.action_type.value} | "
        f"status={task.task_status.value} | target_price={task.target_price or '-'} | "
        f"scope={task.scope_type}:{task.scope_key}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
