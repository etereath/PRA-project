from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from scripts.verify_shadowbot_filequeue_acceptance import verify_acceptance


def _write_json(path, value):
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.write_bytes(content)
    path.with_suffix(path.suffix + ".sha256").write_text(
        hashlib.sha256(content).hexdigest() + "\n",
        encoding="ascii",
    )
    return hashlib.sha256(content).hexdigest()


def test_v2_read_only_acceptance_uses_snapshot_price_and_inventory(tmp_path):
    attempt_id = "ATTEMPT-T11-V2-PRICE-001"
    queue_dir = tmp_path / "queue"
    archive_dir = queue_dir / "archive" / attempt_id
    for directory in ("inbox", "working", "results", "quarantine"):
        (queue_dir / directory).mkdir(parents=True)
    archive_dir.mkdir(parents=True)

    instruction_hash = "sha256:" + ("a" * 64)
    request = {
        "contract_version": 2,
        "task_id": "TASK-11",
        "operation_id": "OP-T11-V2-PRICE-001",
        "execution_attempt_id": attempt_id,
        "execution_mode": "READ_ONLY",
        "instruction_hash": instruction_hash,
        "capture_evidence": False,
        "products": [{
            "item_id": "ITEM-AISHA-C",
            "expected_product_name": "艾莎",
            "expected_grade": "C级",
        }],
    }
    request_path = archive_dir / f"{attempt_id}.request.json"
    request_hash = _write_json(request_path, request)
    result = {
        "contract_version": 2,
        "task_id": request["task_id"],
        "operation_id": request["operation_id"],
        "execution_attempt_id": attempt_id,
        "execution_mode": "READ_ONLY",
        "instruction_hash": instruction_hash,
        "request_file_sha256": request_hash,
        "status": "READ_COMPLETED",
        "run_success_flag": True,
        "business_operation_completed": False,
        "side_effect_state": "NOT_STARTED",
        "total_count": 1,
        "processed_count": 1,
        "success_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
        "manual_check_count": 0,
        "product_snapshots": [{
            "item_id": "ITEM-AISHA-C",
            "item_status": "SUCCESS",
            "listing_status": "ONLINE",
            "price": "19.00",
            "inventory": 8,
            "evidence": [],
        }],
    }
    _write_json(archive_dir / f"{attempt_id}.result.json", result)
    _write_json(
        archive_dir / f"{attempt_id}.phase.json",
        {"phase": "RESULT_WRITTEN", "worker_id": "TEST-WORKER"},
    )

    attempt = SimpleNamespace(
        execution_mode="READ_ONLY",
        ended_at="2026-07-20T00:00:00Z",
        instruction_hash=instruction_hash,
        request_file_sha256=request_hash,
        queue_request_path=str(request_path),
        status="READ_COMPLETED",
    )

    class FakeRepository:
        def __init__(self, _path):
            pass

        def init_schema(self):
            pass

        def get_shadowbot_execution_attempt(self, _attempt_id):
            return attempt

        def list_execution_logs(self, *, task_id):
            return [SimpleNamespace(raw_output=f"completed {attempt_id}")]

    with patch("scripts.verify_shadowbot_filequeue_acceptance.SQLiteRuntimeRepository", FakeRepository):
        report = verify_acceptance(
            runtime_db=tmp_path / "runtime.sqlite3",
            queue_dir=queue_dir,
            execution_attempt_id=attempt_id,
            execution_mode="READ_ONLY",
        )

    assert report["ok"], report["failed_checks"]
    check_names = {item["name"] for item in report["checks"]}
    assert "v2_product_snapshots_present" in check_names
    assert "v2_success_prices_recorded" in check_names
    assert "v2_success_inventory_recorded" in check_names
    assert "v2_count_identity" in check_names
