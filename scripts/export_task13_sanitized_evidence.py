from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.shadowbot_listing_action_contract import (  # noqa: E402
    compute_listing_phase_hash,
    compute_listing_result_hash,
)
from scripts.verify_task13_sanitized_evidence import validate_bundle  # noqa: E402


DEFAULT_QUEUE_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_RUNTIME_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("shadowbot/test2/product_identity_mapping.json")
DEFAULT_OUTPUT_ROOT = Path("docs/evidence/task13")
ATTEMPT_ID = "ATTEMPT-T13-KEYBOARD-SYNC-20260725-04"
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


def _sha256_json(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def _load_receipt(runtime_db: Path, attempt_id: str) -> dict[str, Any]:
    with sqlite3.connect(runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT *
            FROM shadowbot_listing_result_receipts
            WHERE execution_attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"listing result receipt is missing: {attempt_id}")
    return dict(row)


def _load_database_projection(
    runtime_db: Path,
    *,
    snapshot_id: str,
) -> dict[str, Any]:
    with sqlite3.connect(runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        snapshot = connection.execute(
            "SELECT * FROM listing_sync_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise ValueError(f"listing snapshot is missing: {snapshot_id}")
        locations = {
            str(row["listing_location"]): int(row["item_count"])
            for row in connection.execute(
                """
                SELECT listing_location, COUNT(*) AS item_count
                FROM listing_sync_snapshot_items
                WHERE snapshot_id = ?
                GROUP BY listing_location
                ORDER BY listing_location
                """,
                (snapshot_id,),
            )
        }
        item_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM listing_sync_snapshot_items
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()[0]
        )
        projected_skus = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT internal_sku
                FROM listing_status
                WHERE online_status_source_id = ?
                ORDER BY internal_sku
                """,
                (snapshot_id,),
            )
        ]
        anomaly_rows = list(
            connection.execute(
                """
                SELECT reason_code, review_task_id
                FROM listing_anomaly_cases
                WHERE snapshot_id = ? AND cleared_at IS NULL
                ORDER BY anomaly_case_id
                """,
                (snapshot_id,),
            )
        )
        review_ids = sorted(
            {
                str(row["review_task_id"])
                for row in anomaly_rows
                if row["review_task_id"] not in (None, "")
            }
        )
        if review_ids:
            placeholders = ",".join("?" for _ in review_ids)
            review_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM review_tasks WHERE review_task_id IN ({placeholders})",
                    review_ids,
                ).fetchone()[0]
            )
            notification_count = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM notification_outbox
                    WHERE related_review_task_id IN ({placeholders})
                    """,
                    review_ids,
                ).fetchone()[0]
            )
        else:
            review_count = 0
            notification_count = 0
    return {
        "schema_version": "task13-sync-status-database-backread-1.0",
        "snapshot_id": snapshot_id,
        "snapshot_status": str(snapshot["status"]),
        "snapshot_complete": bool(snapshot["snapshot_complete"]),
        "snapshot_item_count": item_count,
        "location_counts": dict(sorted(locations.items())),
        "projected_listing_status_count": len(projected_skus),
        "projected_internal_skus": projected_skus,
        "open_anomaly_count": len(anomaly_rows),
        "anomaly_reason_counts": dict(
            sorted(Counter(str(row["reason_code"]) for row in anomaly_rows).items())
        ),
        "related_review_count": review_count,
        "related_notification_count": notification_count,
        "count_equations": {
            "snapshot_items": "location_counts 之和 = snapshot_item_count",
            "review_chain": "open_anomaly_count = related_review_count = related_notification_count",
        },
    }


def _write_index(root: Path, report: dict[str, Any]) -> None:
    sync_reports: list[dict[str, Any]] = []
    for validation_path in sorted(
        root.glob("ATTEMPT-*/validation_report.json")
    ):
        try:
            candidate = _read_json(validation_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        if candidate.get("snapshot_id"):
            sync_reports.append(candidate)
    lines = [
        "# 任务13脱敏实机证据",
        "",
        "> 本索引由 `scripts/export_task13_sanitized_evidence.py` 自动生成；"
        "CI 分别使用 Task13 的 SYNC_STATUS、单商品往返、多商品成功、"
        "串行 UNKNOWN、UNKNOWN→RECONCILE（VERIFIED/NOT_APPLIED）、"
        "ALREADY_APPLIED 和批次预检零写"
        "校验器复算 v5 合同、"
        "request/result/phase/receipt/ACK 绑定、页面事实、0 写点击、"
        "数据库账本和计数恒等式。",
        "",
        "| 运行 ID | 批次 | 结果 | 上架中/待上架观察 | 快照项 | 数据库投影/异常 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for sync_report in sync_reports:
        lines.append(
            (
                "| [{attempt}]({attempt}/validation_report.json) | "
                "`{batch}` | `{status}` | {online}/{waiting} | {items} | "
                "{projected}/{anomalies} |"
            ).format(
                attempt=sync_report["execution_attempt_id"],
                batch=sync_report["batch_id"],
                status=sync_report["status"],
                online=sync_report["online_observations"],
                waiting=sync_report["waiting_observations"],
                items=sync_report["snapshot_item_count"],
                projected=sync_report["database_counts"][
                    "projected_listing_status_count"
                ],
                anomalies=sync_report["database_counts"][
                    "open_anomaly_count"
                ],
            )
        )
    lines.extend(
        [
            "",
            "## 单商品状态往返",
            "",
            "| 证据包 | SKU | 上架批次 | 下架批次 | 后置快照 | 结果 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for validation_path in sorted(
        root.glob("ROUND-TRIP-*/validation_report.json")
    ):
        try:
            round_trip = _read_json(validation_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        bundle = validation_path.parent.name
        lines.append(
            (
                "| [{bundle}]({bundle}/validation_report.json) | `{sku}` | "
                "`{online}` | `{offline}` | `{snapshot}` | `{status}` |"
            ).format(
                bundle=bundle,
                sku=round_trip["internal_sku"],
                online=round_trip["online_batch_id"],
                offline=round_trip["offline_batch_id"],
                snapshot=round_trip["post_sync_snapshot_id"],
                status=round_trip["status"],
            )
        )
    lines.extend(
        [
            "",
            "## 多商品正常上架与下架",
            "",
            "| 证据包 | SKU | 上架批次 | 下架批次 | 结果 |",
            "|---|---|---|---|---|",
        ]
    )
    for validation_path in sorted(
        root.glob("MULTI-SUCCESS-*/validation_report.json")
    ):
        try:
            multi = _read_json(validation_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        bundle = validation_path.parent.name
        lines.append(
            (
                "| [{bundle}]({bundle}/validation_report.json) | `{skus}` | "
                "`{online}` | `{offline}` | `{status}` |"
            ).format(
                bundle=bundle,
                skus=", ".join(multi["internal_skus"]),
                online=multi["set_online_batch_id"],
                offline=multi["set_offline_batch_id"],
                status=multi["status"],
            )
        )
    lines.extend(
        [
            "",
            "## 受控 UNKNOWN 与唯一自动对账",
            "",
            "| 证据包 | SKU | 批次 | COMMIT 运行 ID | RECONCILE 运行 ID | 最终结果 |",
            "|---|---|---|---|---|---|",
        ]
    )
    recovery_paths = sorted(
        [
            *root.glob(
                "UNKNOWN-RECONCILE-*/validation_report.json"
            ),
            *root.glob(
                "UNKNOWN-NOT-APPLIED-*/validation_report.json"
            ),
        ]
    )
    for validation_path in recovery_paths:
        try:
            recovery = _read_json(validation_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        bundle = validation_path.parent.name
        lines.append(
            (
                "| [{bundle}]({bundle}/validation_report.json) | `{sku}` | "
                "`{batch}` | `{commit}` | `{reconcile}` | `{status}` |"
            ).format(
                bundle=bundle,
                sku=recovery["internal_sku"],
                batch=recovery["batch_id"],
                commit=recovery["commit_execution_attempt_id"],
                reconcile=recovery["reconcile_execution_attempt_id"],
                status=recovery["status"],
            )
        )
    lines.extend(
        [
            "",
            "## 严格串行：成功、UNKNOWN、后续停止",
            "",
            "| 证据包 | 成功 SKU | UNKNOWN SKU | 未尝试 SKU | 批次 | 结果 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for validation_path in sorted(
        root.glob("SERIAL-UNKNOWN-*/validation_report.json")
    ):
        try:
            serial = _read_json(validation_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        bundle = validation_path.parent.name
        lines.append(
            (
                "| [{bundle}]({bundle}/validation_report.json) | `{verified}` | "
                "`{unknown}` | `{stopped}` | `{batch}` | `{status}` |"
            ).format(
                bundle=bundle,
                verified=serial["verified_internal_sku"],
                unknown=serial["unknown_internal_sku"],
                stopped=serial["not_attempted_internal_sku"],
                batch=serial["commit_batch_id"],
                status=serial["status"],
            )
        )
    lines.extend(
        [
            "",
            "## 幂等 ALREADY_APPLIED",
            "",
            "| 证据包 | SKU | 批次 | 运行 ID | 写点击 | 结果 |",
            "|---|---|---|---|---:|---|",
        ]
    )
    for validation_path in sorted(
        root.glob("ALREADY-APPLIED-*/validation_report.json")
    ):
        try:
            idempotent = _read_json(validation_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        bundle = validation_path.parent.name
        lines.append(
            (
                "| [{bundle}]({bundle}/validation_report.json) | `{sku}` | "
                "`{batch}` | `{attempt}` | 0 | `{result}` |"
            ).format(
                bundle=bundle,
                sku=idempotent["internal_sku"],
                batch=idempotent["batch_id"],
                attempt=idempotent["execution_attempt_id"],
                result=idempotent["operation_result"],
            )
        )
    lines.extend(
        [
            "",
            "## 批次预检不一致与整批零写",
            "",
            "| 证据包 | 正常 SKU | 不一致 SKU | 批次 | 运行 ID | 写点击 | 结果 |",
            "|---|---|---|---|---|---:|---|",
        ]
    )
    for validation_path in sorted(
        root.glob("PREFLIGHT-ZERO-WRITE-*/validation_report.json")
    ):
        try:
            preflight = _read_json(validation_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        bundle = validation_path.parent.name
        lines.append(
            (
                "| [{bundle}]({bundle}/validation_report.json) | `{normal}` | "
                "`{mismatch}` | `{batch}` | `{attempt}` | {clicks} | "
                "`{result}` |"
            ).format(
                bundle=bundle,
                normal=preflight["normal_internal_sku"],
                mismatch=preflight["mismatch_internal_sku"],
                batch=preflight["batch_id"],
                attempt=preflight["execution_attempt_id"],
                clicks=preflight["write_click_count"],
                result=preflight["batch_result"],
            )
        )
    lines.extend(
        [
        "",
        "脱敏仅替换本机路径和 Worker 设备标识；商品身份、价格、库存、时间、"
        "批次/运行/快照 ID、页面位置、异常分类和数据库计数均保留。",
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
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--attempt-id", default=ATTEMPT_ID)
    args = parser.parse_args()

    attempt_id = str(args.attempt_id)
    source_dir = args.queue_archive / attempt_id
    output_dir = args.output_root / attempt_id
    output_dir.mkdir(parents=True, exist_ok=True)
    request_source = source_dir / f"{attempt_id}.request.json"
    result_source = source_dir / f"{attempt_id}.result.json"
    phase_source = source_dir / f"{attempt_id}.phase.json"
    ack_source = source_dir / f"{attempt_id}.import.ack.json"
    report_source = source_dir / f"{attempt_id}.sync-report.md"
    for path in (
        request_source,
        result_source,
        phase_source,
        ack_source,
        report_source,
        args.mapping,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    request = _sanitize(_read_json(request_source))
    request_path = output_dir / "request.sanitized.json"
    _write_json(request_path, request)
    request_sha = "sha256:" + _sha256(request_path)

    result = _sanitize(_read_json(result_source))
    result["request_file_sha256"] = request_sha
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    result_path = output_dir / "result.sanitized.json"
    _write_json(result_path, result)
    result_file_sha = _sha256(result_path)

    phase = _sanitize(_read_json(phase_source))
    phase["request_file_sha256"] = request_sha
    phase["phase_snapshot_sha256"] = compute_listing_phase_hash(phase)
    _write_json(output_dir / "phase.sanitized.json", phase)

    receipt_original = _load_receipt(args.runtime_db, attempt_id)
    receipt = _sanitize(receipt_original)
    receipt["result_sha256"] = result_file_sha
    _write_json(output_dir / "receipt.sanitized.json", receipt)

    ack = _sanitize(_read_json(ack_source))
    ack["result_file_sha256"] = result_file_sha
    _write_json(output_dir / "ack.sanitized.json", ack)

    mapping_bytes = args.mapping.read_bytes()
    mapping_bytes.decode("utf-8-sig")
    (output_dir / "product_identity_mapping.json").write_bytes(mapping_bytes)

    snapshot = result["snapshot"]
    database_projection = _load_database_projection(
        args.runtime_db,
        snapshot_id=str(snapshot["snapshot_id"]),
    )
    _write_json(
        output_dir / "database_projection.sanitized.json",
        database_projection,
    )

    report_text = report_source.read_text(encoding="utf-8-sig")
    report_text = report_text.replace(str(args.queue_archive), "<REDACTED_PATH>")
    (output_dir / "sync-report.sanitized.md").write_text(
        report_text,
        encoding="utf-8",
        newline="\n",
    )

    items = snapshot["items"]
    locations = Counter(str(item["listing_location"]) for item in items)
    evidence_manifest = {
        "schema_version": "task13-sync-status-evidence-manifest-1.0",
        "execution_attempt_id": attempt_id,
        "batch_id": result["batch_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "result_id": result["result_id"],
        "expected": {
            "status": "VERIFIED",
            "online_observations": sum(
                int(item["online_occurrences"]) for item in items
            ),
            "waiting_observations": sum(
                int(item["waiting_occurrences"]) for item in items
            ),
            "snapshot_item_count": len(items),
            "location_counts": dict(sorted(locations.items())),
            "projected_listing_status_count": database_projection[
                "projected_listing_status_count"
            ],
            "open_anomaly_count": database_projection["open_anomaly_count"],
            "related_review_count": database_projection["related_review_count"],
            "related_notification_count": database_projection[
                "related_notification_count"
            ],
        },
        "original_archive_sha256": {
            "request": _sha256(request_source),
            "result": _sha256(result_source),
            "phase": _sha256(phase_source),
            "ack": _sha256(ack_source),
            "report": _sha256(report_source),
            "mapping": _sha256(args.mapping),
            "receipt_canonical_json": _sha256_json(receipt_original),
        },
    }
    _write_json(output_dir / "evidence_manifest.json", evidence_manifest)

    validation_report = validate_bundle(output_dir)
    validation_report["schema_version"] = (
        "task13-sanitized-evidence-validation-1.0"
    )
    validation_report["generated_at"] = datetime.now(timezone.utc).isoformat()
    validation_report["redaction_policy"] = {
        "replaced": ["local_or_unc_paths", "worker_device_identifier"],
        "preserved": [
            "business_identity",
            "prices_and_inventory",
            "task_and_attempt_ids",
            "timestamps",
            "page_location_and_anomaly_facts",
            "database_backread_counts",
        ],
    }
    _write_json(output_dir / "validation_report.json", validation_report)
    _write_index(args.output_root, validation_report)
    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": str(output_dir),
                "execution_attempt_id": attempt_id,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
