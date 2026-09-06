from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import time
from pathlib import Path

from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.operational_time_maintenance import (
    OperationalTimeMaintenanceService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="原子替换运营时间策略及其全部定时 Automation Job",
    )
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--backup-db", type=Path, required=True)
    parser.add_argument("--platform-name", required=True)
    parser.add_argument("--expected-current-policy-version", required=True)
    parser.add_argument("--successor-policy-version", required=True)
    parser.add_argument("--platform-cutoff", required=True, help="例如 19:00")
    parser.add_argument("--seller-cutoff", required=True, help="例如 21:00")
    parser.add_argument("--peak-start", required=True, help="例如 17:00")
    parser.add_argument("--created-by", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="明确执行写入；省略时只进行只读检查",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    runtime_path = args.runtime_db.resolve()
    backup_path = args.backup_db.resolve()
    if not runtime_path.is_file():
        raise SystemExit("Runtime DB 不存在。")
    if runtime_path == backup_path:
        raise SystemExit("备份路径不能与 Runtime DB 相同。")
    runtime = SQLiteRuntimeRepository(runtime_path)
    automation = AutomationRepository(runtime)
    policies = automation.load_operational_time_policies()
    open_policies = [policy for policy in policies if policy.effective_to is None]
    current_jobs = [
        job
        for job in automation.list_jobs()
        if str(job.config.get("platform_name") or "") == args.platform_name
        and str(job.config.get("time_policy_version") or "")
        == args.expected_current_policy_version
    ]
    preview = {
        "mode": "APPLY" if args.apply else "READ_ONLY",
        "runtime_db": str(runtime_path),
        "backup_db": str(backup_path),
        "current_open_policy_versions": [
            policy.policy_version for policy in open_policies
        ],
        "expected_current_policy_version": (
            args.expected_current_policy_version
        ),
        "successor_policy_version": args.successor_policy_version,
        "matched_policy_bound_job_count": len(current_jobs),
    }
    if not args.apply:
        preview["message"] = "只读检查完成；未创建备份，未修改 Runtime DB。"
        _print_json(preview)
        return 0
    if backup_path.exists():
        raise SystemExit("备份文件已存在；为避免覆盖，请使用新的备份路径。")
    _assert_runtime_maintenance_ready(runtime)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    _create_verified_backup(runtime, backup_path)
    result = OperationalTimeMaintenanceService(
        automation
    ).replace_policy_and_timed_jobs(
        platform_name=args.platform_name,
        expected_current_policy_version=args.expected_current_policy_version,
        successor_policy_version=args.successor_policy_version,
        platform_cutoff_local_time=_parse_time(
            args.platform_cutoff,
            "平台截单时间",
        ),
        seller_cutoff_local_time=_parse_time(
            args.seller_cutoff,
            "卖家截单时间",
        ),
        peak_start_local_time=_parse_time(args.peak_start, "销售高峰开始时间"),
        created_by=args.created_by,
    )
    readback = {
        job.job_type: {
            "job_id": job.job_id,
            "enabled": job.enabled,
            "schedule_expression": job.schedule_expression,
            "time_policy_version": job.config.get("time_policy_version"),
        }
        for job in result.successor_jobs
    }
    preview.update(
        {
            "backup_sha256": _sha256(backup_path),
            "effective_from": result.successor_policy.effective_from.isoformat(),
            "successor_jobs": readback,
            "message": "时间策略与全部相关定时 Job 已在同一事务切换。",
        }
    )
    _print_json(preview)
    return 0


def _create_verified_backup(
    runtime: SQLiteRuntimeRepository,
    backup_path: Path,
) -> None:
    try:
        with closing(runtime.connect_read()) as source, closing(
            sqlite3.connect(backup_path)
        ) as destination:
            source.backup(destination, pages=1000, sleep=0.05)
            destination.commit()
        with closing(sqlite3.connect(backup_path)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                raise RuntimeError("备份数据库完整性检查未通过。")
    except Exception:
        if backup_path.exists():
            backup_path.unlink()
        raise


def _assert_runtime_maintenance_ready(
    runtime: SQLiteRuntimeRepository,
) -> None:
    with closing(runtime.connect_read()) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise RuntimeError("Runtime DB 完整性检查未通过，已停止维护。")
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise RuntimeError(
                "Runtime DB 存在外键违规，必须先走独立数据维护流程。"
            )


def _parse_time(value: str, label: str) -> time:
    try:
        parsed = time.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise SystemExit(f"{label}格式无效，应使用 HH:MM。") from exc
    return parsed.replace(second=0, microsecond=0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
