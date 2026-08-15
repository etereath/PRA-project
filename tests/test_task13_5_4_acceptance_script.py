from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.models import Product
from app.repositories.master_data_repository import RuntimeMasterDataRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from scripts.run_task13_5_4_order_readonly_acceptance import (
    _runtime_mappings_provider,
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


def test_acceptance_runtime_mapping_provider_reads_versioned_master_data(
    tmp_path: Path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    master_data = RuntimeMasterDataRepository(runtime)
    master_data.seed(
        [
            Product(
                internal_sku="SKU-1",
                product_name="Sample",
                grade="A",
                stem_length="50cm",
                unit="bundle",
                base_cost=Decimal("1"),
                current_stock=0,
                sale_enabled=True,
                remark="",
            )
        ],
        [
            {
                "mapping_id": "MAP-1",
                "mapping_kind": "PRODUCT",
                "platform_name": "platform",
                "platform_product_id": "",
                "platform_product_name": "sample product",
                "normalized_platform_product_name": "sample product",
                "grade": "A",
                "internal_sku": "SKU-1",
                "candidate_internal_sku": None,
                "search_keyword": "",
                "mapping_status": "VERIFIED",
                "effective_from": None,
                "effective_to": None,
                "last_verified_at": None,
                "remark": "",
            }
        ],
        product_source_ref="test-products",
        product_source_sha256="sha256:" + "1" * 64,
        mapping_source_ref="test-mappings",
        mapping_source_sha256="sha256:" + "2" * 64,
        actor="test",
    )

    compiled = _runtime_mappings_provider(runtime)()
    resolution = compiled.resolve(
        platform_name="platform",
        platform_product_name="sample product",
        grade="A",
        observed_at=datetime.now(timezone.utc),
    )

    assert resolution.mapping_status.value == "VERIFIED"
    assert resolution.internal_sku == "SKU-1"


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
