from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.business_rule_evaluation import (
    DEFAULT_CAPACITY_PLANS,
    DEFAULT_COLD_STORAGE_STATUS,
    DEFAULT_HARVEST_FORECASTS,
    DEFAULT_LISTING_RULES,
    DEFAULT_PRODUCTS,
    RUN_MODE_APPLY,
    RUN_MODE_DRY_RUN,
    BusinessRuleRunner,
    EvaluationContext,
)
from app.repositories.mock_platform_repository import DEFAULT_MOCK_PLATFORM_DB
from app.services.runtime import DEFAULT_RUNTIME_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PRA 自动规则评估脚本")
    parser.add_argument("--list", action="store_true", help="列出可用 evaluator")
    parser.add_argument("--evaluator", default="", help="要运行的 evaluator，例如 capacity_warning")
    parser.add_argument("--trade-date", default="", help="交易日，格式 YYYY-MM-DD；默认明天")
    parser.add_argument("--dry-run", action="store_true", help="只生成预览记录，不写业务任务")
    parser.add_argument("--apply", action="store_true", help="写入业务任务、复核和通知")
    parser.add_argument("--runtime-db", default=str(DEFAULT_RUNTIME_DB), help="运行态 SQLite 数据库路径")
    parser.add_argument("--harvest-forecasts", default=str(DEFAULT_HARVEST_FORECASTS), help="产量预测 Excel 路径")
    parser.add_argument("--capacity-plans", default=str(DEFAULT_CAPACITY_PLANS), help="包装产能计划 Excel 路径")
    parser.add_argument("--cold-storage-status", default=str(DEFAULT_COLD_STORAGE_STATUS), help="冷库状态 Excel 路径")
    parser.add_argument("--mock-platform-db", default=str(DEFAULT_MOCK_PLATFORM_DB), help="Mock 平台 SQLite 数据库路径")
    parser.add_argument("--products", default=str(DEFAULT_PRODUCTS), help="商品资料 Excel 路径")
    parser.add_argument("--listing-rules", default=str(DEFAULT_LISTING_RULES), help="上下架规则 Excel 路径")
    parser.add_argument("--platform", default="default_platform", help="评估使用的平台名称")
    parser.add_argument("--created-by", default="cli:evaluate_business_rules", help="运行记录操作者")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repository = SQLiteRuntimeRepository(Path(args.runtime_db))
    repository.init_schema()
    runner = BusinessRuleRunner(repository)

    if args.list:
        print("可用自动规则 evaluator：")
        for evaluator in runner.list_evaluators():
            print(f"- {evaluator.evaluator_id}: {evaluator.evaluator_name} - {evaluator.description}")
        return 0

    if not args.evaluator:
        print("错误：请使用 --evaluator 指定要运行的 evaluator，或使用 --list 查看清单。", file=sys.stderr)
        return 2
    if args.apply and args.dry_run:
        print("错误：--dry-run 与 --apply 不能同时使用。", file=sys.stderr)
        return 2

    try:
        trade_date = _parse_trade_date(args.trade_date)
        run_mode = RUN_MODE_APPLY if args.apply else RUN_MODE_DRY_RUN
        summary = runner.run(
            args.evaluator,
            EvaluationContext(
                trade_date=trade_date,
                runtime_db_path=Path(args.runtime_db),
                run_mode=run_mode,
                now=datetime.now(),
                harvest_forecasts_path=Path(args.harvest_forecasts),
                capacity_plan_path=Path(args.capacity_plans),
                cold_storage_status_path=Path(args.cold_storage_status),
                mock_platform_db_path=Path(args.mock_platform_db),
                products_path=Path(args.products),
                listing_rules_path=Path(args.listing_rules),
                platform_name=args.platform,
                created_by=args.created_by,
            ),
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(f"自动规则评估失败：{exc}", file=sys.stderr)
        return 1

    print("自动规则评估完成：")
    print(f"- script_run_id={summary.script_run.script_run_id}")
    print(f"- evaluator={summary.script_run.evaluator_id}")
    print(f"- run_mode={summary.script_run.run_mode}")
    print(f"- run_status={summary.script_run.run_status}")
    print(f"- proposals={summary.proposals_count}")
    print(f"- inserted_tasks={summary.inserted_tasks_count}")
    print(f"- inserted_review_tasks={summary.inserted_review_tasks_count}")
    print(f"- inserted_notification_logs={summary.inserted_notification_logs_count}")
    if summary.warnings:
        print("- warnings=" + " | ".join(summary.warnings[:3]))
    if summary.errors:
        print("- errors=" + " | ".join(summary.errors[:3]))
    return 0 if summary.script_run.run_status == "success" else 1


def _parse_trade_date(raw: str) -> date:
    if raw.strip():
        return date.fromisoformat(raw.strip())
    return (datetime.now() + timedelta(days=1)).date()


if __name__ == "__main__":
    raise SystemExit(main())
