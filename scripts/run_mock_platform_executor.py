from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repositories.mock_platform_repository import DEFAULT_MOCK_PLATFORM_DB, MockPlatformRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.mock_platform import MockPlatformExecutorService
from app.services.runtime import DEFAULT_RUNTIME_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PRA Mock Platform Executor")
    parser.add_argument("--dry-run", action="store_true", help="只预览将执行的任务，不修改任何状态")
    parser.add_argument("--apply", action="store_true", help="实际修改 mock platform 状态并写 execution_logs")
    parser.add_argument("--runtime-db", default=str(DEFAULT_RUNTIME_DB), help="PRA 运行态 SQLite 数据库路径")
    parser.add_argument("--mock-platform-db", default=str(DEFAULT_MOCK_PLATFORM_DB), help="Mock 平台 SQLite 数据库路径")
    parser.add_argument("--platform", default="", help="只执行指定平台的任务")
    parser.add_argument("--task-id", default="", help="只执行指定 task_id")
    parser.add_argument("--init", action="store_true", help="初始化 mock platform schema")
    parser.add_argument("--reset-sample", action="store_true", help="重置并写入默认 mock platform 样例数据")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        print("错误：--dry-run 与 --apply 不能同时使用。", file=sys.stderr)
        return 2

    runtime_repository = SQLiteRuntimeRepository(Path(args.runtime_db))
    mock_repository = MockPlatformRepository(Path(args.mock_platform_db))
    service = MockPlatformExecutorService(
        runtime_repository=runtime_repository,
        mock_platform_repository=mock_repository,
    )
    if args.reset_sample:
        inserted = service.initialize_mock_platform(reset=True)
        print(f"Mock 平台样例数据已重置：{inserted} 条。")
        return 0
    if args.init:
        service.initialize_mock_platform(reset=False)
        print(f"Mock 平台数据库已初始化：{Path(args.mock_platform_db)}")
        return 0

    apply = bool(args.apply)
    summary = service.execute(
        apply=apply,
        platform_name=args.platform.strip() or None,
        task_id=args.task_id.strip() or None,
    )
    print("Mock Platform Executor 完成：")
    print(f"- run_mode={summary.run_mode}")
    print(f"- scanned_tasks={summary.scanned_tasks_count}")
    print(f"- executable_tasks={summary.executable_tasks_count}")
    print(f"- executed_tasks={summary.executed_tasks_count}")
    print(f"- success={summary.success_count}")
    print(f"- failed={summary.failed_count}")
    for item in summary.items[:20]:
        status = "OK" if item.success else "FAILED"
        print(
            f"  [{status}] task_id={item.task_id} action={item.action_type} "
            f"platform={item.platform_name} sku={item.internal_sku} message={item.message}"
        )
    if len(summary.items) > 20:
        print(f"  ... 还有 {len(summary.items) - 20} 条未展示")
    return 0 if summary.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
