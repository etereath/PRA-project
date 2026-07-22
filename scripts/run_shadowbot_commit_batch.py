from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_commit_pipeline import (
    import_task_commit_result,
    prepare_task_commit_batch,
    publish_task_commit_batch,
)
from app.services.shadowbot_executor import ShadowBotFileQueueRunner


DEFAULT_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("data/samples/products.xlsx")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 根节点必须是对象：{path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="任务中心到 ShadowBot contract v4 的单批次 COMMIT 入口")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="生成不可执行清单并写入 PREPARED 账本")
    prepare.add_argument("--task-id", action="append", required=True)
    prepare.add_argument("--batch-id", required=True)
    prepare.add_argument("--profile", choices=("development", "production"), required=True)
    prepare.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    prepare.add_argument("--output", type=Path, required=True)

    publish = subparsers.add_parser("publish", help="将已准备清单一次性投递到文件队列")
    publish.add_argument("--manifest", type=Path, required=True)
    publish.add_argument("--profile", choices=("development", "production"), required=True)
    publish.add_argument("--queue-dir", type=Path, default=Path(os.environ.get("SHADOWBOT_QUEUE_DIR", r"D:\PRA_Runtime\shadowbot_queue")))
    publish.add_argument("--applet-uri", required=True)
    publish.add_argument("--confirmation-text", default="")
    publish.add_argument("--confirmed-by", default="")
    publish.add_argument("--request-output", type=Path)

    run = subparsers.add_parser("production-run", help="正式模式：从 pending 任务创建并仅投递一次")
    run.add_argument("--task-id", action="append", required=True)
    run.add_argument("--batch-id", required=True)
    run.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    run.add_argument("--queue-dir", type=Path, default=Path(os.environ.get("SHADOWBOT_QUEUE_DIR", r"D:\PRA_Runtime\shadowbot_queue")))
    run.add_argument("--applet-uri", required=True)
    run.add_argument("--manifest-output", type=Path, required=True)
    run.add_argument("--request-output", type=Path)

    import_result = subparsers.add_parser("import-result", help="校验并原子回写一个 v4 结果")
    import_result.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    args = _parser().parse_args(argv)
    repository = SQLiteRuntimeRepository(args.db)
    repository.init_schema()

    if args.command == "prepare":
        manifest = prepare_task_commit_batch(
            repository,
            task_ids=args.task_id,
            mapping_path=args.mapping,
            batch_id=args.batch_id,
            execution_profile=args.profile,
        )
        _write_json(args.output, manifest)
        print(json.dumps({"batch_id": manifest["batch_id"], "status": "PREPARED", "manifest": str(args.output)}, ensure_ascii=False))
        return 0

    if args.command == "publish":
        manifest = _read_json(args.manifest)
        request, start = publish_task_commit_batch(
            repository,
            ShadowBotFileQueueRunner(args.queue_dir),
            manifest=manifest,
            execution_profile=args.profile,
            applet_uri=args.applet_uri,
            confirmation_text=args.confirmation_text,
            confirmed_by=args.confirmed_by,
        )
        if args.request_output:
            _write_json(args.request_output, request)
        print(json.dumps({"batch_id": request["batch_id"], "execution_attempt_id": request["execution_attempt_id"], "run_id": start.shadowbot_run_id}, ensure_ascii=False))
        return 0

    if args.command == "production-run":
        manifest = prepare_task_commit_batch(
            repository,
            task_ids=args.task_id,
            mapping_path=args.mapping,
            batch_id=args.batch_id,
            execution_profile="production",
        )
        _write_json(args.manifest_output, manifest)
        request, start = publish_task_commit_batch(
            repository,
            ShadowBotFileQueueRunner(args.queue_dir),
            manifest=manifest,
            execution_profile="production",
            applet_uri=args.applet_uri,
        )
        if args.request_output:
            _write_json(args.request_output, request)
        print(json.dumps({"batch_id": request["batch_id"], "execution_attempt_id": request["execution_attempt_id"], "run_id": start.shadowbot_run_id}, ensure_ascii=False))
        return 0

    counts = import_task_commit_result(repository, _read_json(args.result))
    print(json.dumps({"status": "IMPORTED", "counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
