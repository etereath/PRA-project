from __future__ import annotations

import argparse
from pathlib import Path

from app.exceptions import ValidationError
from app.repositories.workbook_repository import create_template_workbooks
from app.services.ai import MockAISuggestionProvider
from app.services.pricing import PricingService
from app.services.workflow import (
    ExecutionSimulationInputs,
    WorkflowInputs,
    generate_tasks_from_sources,
    preview_tasks_from_sources,
    simulate_execution_from_tasks,
    validate_sources,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PRA MVP 命令行工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    templates_parser = subparsers.add_parser("templates", help="创建模板工作簿")
    templates_parser.add_argument("--output-dir", required=True, type=Path)

    validate_parser = subparsers.add_parser("validate", help="校验输入工作簿")
    _add_source_args(validate_parser)

    import_parser = subparsers.add_parser("import-data", help="校验并输出输入数据摘要")
    _add_source_args(import_parser)

    preview_parser = subparsers.add_parser("preview-tasks", help="预览任务但不写出文件")
    _add_source_args(preview_parser)
    preview_parser.add_argument("--platform", default="default_platform")
    preview_parser.add_argument("--use-mock-ai", action="store_true")

    generate_parser = subparsers.add_parser("generate-tasks", help="生成任务工作簿")
    _add_source_args(generate_parser)
    generate_parser.add_argument("--output", required=True, type=Path)
    generate_parser.add_argument("--platform", default="default_platform")
    generate_parser.add_argument("--use-mock-ai", action="store_true")

    ai_parser = subparsers.add_parser("mock-ai-decision", help="预览单个 SKU 的 Mock AI 定价决策")
    _add_source_args(ai_parser)
    ai_parser.add_argument("--sku", required=True)
    ai_parser.add_argument("--platform", default="default_platform")

    execution_parser = subparsers.add_parser("simulate-execution", help="模拟执行任务并输出执行日志")
    execution_parser.add_argument("--tasks", required=True, type=Path)
    execution_parser.add_argument("--logs-output", required=True, type=Path)
    execution_parser.add_argument("--updated-tasks-output", type=Path)
    execution_parser.add_argument("--executor-name", default="mock_executor")

    web_parser = subparsers.add_parser("serve-web", help="启动简易 Web 管理页")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", default=8765, type=int)

    return parser


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--products", required=True, type=Path)
    parser.add_argument("--price-rules", required=True, type=Path)
    parser.add_argument("--listing-rules", required=True, type=Path)


def _workflow_inputs(args: argparse.Namespace, *, include_output: bool = False) -> WorkflowInputs:
    return WorkflowInputs(
        products_path=args.products,
        price_rules_path=args.price_rules,
        listing_rules_path=args.listing_rules,
        output_path=args.output if include_output else None,
        platform_name=getattr(args, "platform", "default_platform"),
        use_mock_ai=getattr(args, "use_mock_ai", False),
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "templates":
            paths = create_template_workbooks(args.output_dir)
            for path in paths:
                print(path)
            return 0

        if args.command == "serve-web":
            from app.web import serve

            serve(args.host, args.port)
            return 0

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
                print(
                    f"- {task.internal_sku} | {task.action_type.value} | "
                    f"status={task.task_status.value} | target_price={task.target_price or '-'}"
                )
            return 0

        if args.command == "mock-ai-decision":
            from app.repositories.workbook_repository import load_price_rules, load_products

            products = load_products(args.products)
            price_rules = load_price_rules(args.price_rules)
            product = next((item for item in products if item.internal_sku == args.sku), None)
            if product is None:
                raise ValidationError(f"未找到 SKU: {args.sku}")
            service = PricingService(ai_provider=MockAISuggestionProvider())
            decision = service.calculate(product, args.platform, price_rules)
            print(decision)
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
            print(
                f"已模拟执行 {len(summary.tasks)} 条任务，"
                f"执行日志输出到 {summary.logs_output_path}"
            )
            if summary.updated_tasks_output_path is not None:
                print(f"更新后的任务文件输出到 {summary.updated_tasks_output_path}")
            return 0

        parser.error("未知命令")
        return 2
    except ValidationError as exc:
        print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
