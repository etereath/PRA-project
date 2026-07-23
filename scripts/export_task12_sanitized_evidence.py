from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_task12_sanitized_evidence import validate_bundle


DEFAULT_QUEUE_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_RUNTIME_ROOT = Path(r"D:\PRA_Runtime")
DEFAULT_OUTPUT_ROOT = Path("docs/evidence/task12")
RUNS = (
    {
        "attempt_id": "ATTEMPT-52c584afca044d79",
        "manifest": "task12_remediation_commit_20260723_01.manifest.json",
    },
    {
        "attempt_id": "ATTEMPT-0f30900b398045cc",
        "manifest": "task12_controlled_unknown_20260723_02.manifest.json",
        "reconcile_id": "RECONCILE-046a063ae885fcb4f352",
    },
)
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _redacted_path(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    name = normalized.rsplit("/", 1)[-1]
    return f"<REDACTED_PATH>/{name}" if "." in name else "<REDACTED_PATH>"


def _sanitize(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: _sanitize(item, key=name) for name, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, key=key) for item in value]
    if isinstance(value, str):
        if key in {"worker_id", "device_id", "robot_id", "computer_name"}:
            return "<REDACTED_DEVICE>"
        if WINDOWS_PATH.match(value) or value.startswith("\\\\"):
            return _redacted_path(value)
    return value


def _copy_attempt(
    source_dir: Path,
    output_dir: Path,
    attempt_id: str,
    *,
    prefix: str = "",
) -> dict[str, str]:
    sources = {
        "request": source_dir / f"{attempt_id}.request.json",
        "result": source_dir / f"{attempt_id}.result.json",
        "phase": source_dir / f"{attempt_id}.phase.json",
    }
    for path in sources.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    documents = {name: _sanitize(_read_json(path)) for name, path in sources.items()}
    request_name = f"{prefix}request.sanitized.json"
    result_name = f"{prefix}result.sanitized.json"
    phase_name = f"{prefix}phase.sanitized.json"
    request_path = output_dir / request_name
    _write_json(request_path, documents["request"])
    request_sha = _sha256(request_path)
    documents["result"]["request_file_sha256"] = request_sha
    documents["phase"]["request_file_sha256"] = request_sha
    _write_json(output_dir / result_name, documents["result"])
    _write_json(output_dir / phase_name, documents["phase"])
    return {
        f"{prefix}request": _sha256(sources["request"]),
        f"{prefix}result": _sha256(sources["result"]),
        f"{prefix}phase": _sha256(sources["phase"]),
    }


def _write_index(root: Path, reports: list[dict[str, Any]]) -> None:
    lines = [
        "# 任务12脱敏实机证据",
        "",
        "> 本索引由 `scripts/export_task12_sanitized_evidence.py` 自动生成；"
        "CI 使用 `scripts/verify_task12_sanitized_evidence.py` 复算合同、哈希绑定、"
        "计数恒等式和 UNKNOWN→RECONCILE 关系。",
        "",
        "| 运行 ID | 批次 | 状态 | 项目计数 | 对账 |",
        "|---|---|---|---:|---|",
    ]
    for report in reports:
        reconcile = report.get("reconcile")
        lines.append(
            "| [{run}]({run}/validation_report.json) | `{batch}` | `{status}` | {total} | {reconcile} |".format(
                run=report["bundle_id"],
                batch=report["batch_id"],
                status=report["batch_status"],
                total=report["counts"]["total"],
                reconcile=(
                    f"`{reconcile['execution_attempt_id']}` → `{reconcile['status']}`"
                    if reconcile
                    else "—"
                ),
            )
        )
    lines.extend(
        [
            "",
            "脱敏只替换本机/共享路径和 Worker 设备标识；商品身份、任务 ID、价格、"
            "时间、manifest、instruction hash、逐项状态与执行序号均保留。",
            "",
        ]
    )
    (root / "index.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-archive", type=Path, default=DEFAULT_QUEUE_ARCHIVE)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    for run in RUNS:
        attempt_id = str(run["attempt_id"])
        output_dir = args.output_root / attempt_id
        output_dir.mkdir(parents=True, exist_ok=True)
        original_hashes = _copy_attempt(
            args.queue_archive / attempt_id,
            output_dir,
            attempt_id,
        )
        manifest_source = args.runtime_root / str(run["manifest"])
        manifest = _read_json(manifest_source)
        _write_json(output_dir / "manifest.json", manifest)
        original_hashes["manifest"] = _sha256(manifest_source)
        receipt_source = args.queue_archive / attempt_id / f"{attempt_id}.import.ack.json"
        if receipt_source.is_file():
            _write_json(
                output_dir / "receipt.sanitized.json",
                _sanitize(_read_json(receipt_source)),
            )
            original_hashes["receipt"] = _sha256(receipt_source)
        reconcile_id = str(run.get("reconcile_id") or "")
        if reconcile_id:
            original_hashes.update(
                _copy_attempt(
                    args.queue_archive / reconcile_id,
                    output_dir,
                    reconcile_id,
                    prefix="reconcile.",
                )
            )
        report = validate_bundle(output_dir)
        report["schema_version"] = "task12-sanitized-evidence-validation-1.0"
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        report["redaction_policy"] = {
            "replaced": ["local_or_unc_paths", "worker_device_identifier"],
            "preserved": [
                "business_identity",
                "task_and_attempt_ids",
                "prices",
                "timestamps",
                "manifest_and_instruction_hashes",
                "item_statuses_and_execution_order",
            ],
        }
        report["original_archive_sha256"] = original_hashes
        _write_json(output_dir / "validation_report.json", report)
        reports.append(report)
    _write_index(args.output_root, reports)
    print(
        json.dumps(
            {"ok": True, "output_root": str(args.output_root), "bundles": len(reports)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
