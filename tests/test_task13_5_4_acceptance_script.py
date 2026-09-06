from __future__ import annotations

import json
from datetime import date

from scripts.run_task13_5_4_order_readonly_acceptance import (
    _watchdog_validated_request,
    acceptance_gate_passed,
)


def _result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "batch_status": "PARTIAL",
        "capability_result": "SUCCEEDED",
        "scope_complete": True,
        "end_marker_verified": True,
        "result_imported": True,
        "result_archived": True,
        "queue_counts": {"inbox": 0, "working": 0, "results": 0},
        "platform_write_operations": 0,
        "watchdog_validation_required": True,
        "watchdog_validated": True,
    }
    result.update(overrides)
    return result


def test_acceptance_gate_allows_complete_unmapped_snapshot() -> None:
    assert acceptance_gate_passed(_result())


def test_acceptance_gate_rejects_missing_watchdog_validation() -> None:
    assert not acceptance_gate_passed(_result(watchdog_validated=False))


def test_watchdog_audit_must_match_attempt_run_and_target(tmp_path) -> None:
    audit_log = tmp_path / "watchdog.log"
    audit_log.write_text(
        json.dumps(
            {
                "status": "READY_REQUEST_VALIDATED",
                "execution_attempt_id": "ORDER-READ-1",
                "automation_run_id": "AUTO-RUN-1",
                "requested_platform_trade_date": "2026-07-10",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert _watchdog_validated_request(
        audit_log,
        execution_attempt_id="ORDER-READ-1",
        automation_run_id="AUTO-RUN-1",
        target_trade_date=date(2026, 7, 10),
    )
    assert not _watchdog_validated_request(
        audit_log,
        execution_attempt_id="ORDER-READ-1",
        automation_run_id="AUTO-RUN-1",
        target_trade_date=date(2026, 7, 11),
    )
