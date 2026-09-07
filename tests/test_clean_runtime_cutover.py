from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import scripts.clean_runtime_cutover as cutover

from app.repositories.inventory_repository import InventoryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    load_products,
    save_table_records,
)
from app.services.authoritative_inventory import (
    InventoryApplicationService,
    sqlite_logical_snapshot_sha256,
)
from app.services.operational_time import OperationalTimeService
from scripts.clean_runtime_cutover import (
    ACTIVATION_CONFIRMATION,
    MANIFEST_NAME,
    ROLLBACK_CONFIRMATION,
    CleanRuntimeCutoverError,
    _file_sha256,
    activate_candidate,
    prepare_clean_runtime,
    rollback_activation,
    verify_candidate,
)
from tests.inventory_cutover_support import insert_cutover_order_snapshot


def _write_business_inputs(root: Path) -> tuple[Path, Path]:
    products = root / "products.xlsx"
    mappings = root / "platform_mappings.xlsx"
    save_table_records(
        "products",
        products,
        [
            {
                "internal_sku": "AISHA-A-65-Z",
                "product_name": "艾莎",
                "grade": "A",
                "stem_length": "65",
                "unit": "扎",
                "base_cost": "8",
                "current_stock": "12",
                "sale_enabled": "True",
                "last_price": "",
                "recommended_price": "",
                "remark": "正式商品",
                "feature_season": "",
                "feature_color": "",
            },
            {
                "internal_sku": "CAPPUCCINO-B-50-Z",
                "product_name": "卡布奇诺",
                "grade": "B",
                "stem_length": "50",
                "unit": "扎",
                "base_cost": "6",
                "current_stock": "7",
                "sale_enabled": "True",
                "last_price": "",
                "recommended_price": "",
                "remark": "正式商品",
                "feature_season": "",
                "feature_color": "",
            },
        ],
    )
    save_table_records(
        "platform_mappings",
        mappings,
        [
            {
                "mapping_id": "MAP-AISHA-A",
                "mapping_kind": "PRODUCT",
                "internal_sku": "",
                "candidate_internal_sku": "AISHA-A-65-Z",
                "platform_name": "测试平台",
                "platform_product_id": "",
                "platform_product_name": "艾莎 A级",
                "normalized_platform_product_name": "艾莎 A级",
                "grade": "A",
                "search_keyword": "艾莎",
                "mapping_status": "DISABLED",
                "effective_from": "",
                "effective_to": "",
                "last_verified_at": "",
                "remark": "保持停用",
            }
        ],
    )
    return products, mappings


def _create_unhealthy_test_runtime(path: Path) -> str:
    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()
    with closing(repository.connect_write()) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO automation_run_events(
                event_id, run_id, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, '{}', ?)
            """,
            (
                "ORPHAN-EVENT",
                "DELETED-TEST-RUN",
                "TEST_CLEANUP_LEFTOVER",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
    assert not repository.check_schema_health().ok
    return sqlite_logical_snapshot_sha256(repository)


def _prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path, str]:
    source = tmp_path / "runtime" / "pra_runtime.sqlite3"
    source.parent.mkdir()
    source_snapshot = _create_unhealthy_test_runtime(source)
    products, mappings = _write_business_inputs(tmp_path)
    workspace = tmp_path / "受控切换工作区"
    monkeypatch.setenv("PRA_RUNTIME_DB", str(source))
    monkeypatch.setenv("PRA_PRODUCTS_WORKBOOK", str(products))
    monkeypatch.setenv("PRA_PLATFORM_MAPPINGS_WORKBOOK", str(mappings))
    result = prepare_clean_runtime(
        source_runtime_db=source,
        products_path=products,
        platform_mappings_path=mappings,
        workspace_dir=workspace,
        expected_source_snapshot_sha256=source_snapshot,
        expected_products_sha256=_file_sha256(products),
        expected_platform_mappings_sha256=_file_sha256(mappings),
        apply=True,
    )
    assert result["mode"] == "PREPARED"
    return source, products, mappings, workspace, source_snapshot


def _bootstrap_candidate(*, manifest_path: Path, products: Path, now: datetime) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = Path(manifest["candidate_runtime_db"])
    repository = SQLiteRuntimeRepository(candidate)
    context = OperationalTimeService().classify(now)
    batch_id = insert_cutover_order_snapshot(
        repository,
        batch_id="clean-cutover-empty-open",
        observed_at=now - timedelta(seconds=5),
        platform_trade_date=context.platform_trade_date,
        time_policy_version=context.time_policy_version,
    )
    before = sqlite_logical_snapshot_sha256(repository)
    product_items = tuple(load_products(products))
    result = InventoryApplicationService(repository, clock=lambda: now).bootstrap(
        product_items,
        snapshot_sha256=_file_sha256(products),
        runtime_snapshot_sha256=before,
        cutover_order_observation_batch_id=batch_id,
        idempotency_key=f"clean-rebuild:{_file_sha256(products)}",
        actor="test:clean-cutover",
        freeze_validator=lambda: True,
    )
    assert result.status == "APPLIED"
    return sqlite_logical_snapshot_sha256(repository)


def test_prepare_preview_is_zero_write_and_apply_archives_unhealthy_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "runtime.sqlite3"
    source_snapshot = _create_unhealthy_test_runtime(source)
    products, mappings = _write_business_inputs(tmp_path)
    workspace = tmp_path / "切换预览"
    monkeypatch.setenv("PRA_RUNTIME_DB", str(source))
    monkeypatch.setenv("PRA_PRODUCTS_WORKBOOK", str(products))
    monkeypatch.setenv("PRA_PLATFORM_MAPPINGS_WORKBOOK", str(mappings))

    preview = prepare_clean_runtime(
        source_runtime_db=source,
        products_path=products,
        platform_mappings_path=mappings,
        workspace_dir=workspace,
        expected_source_snapshot_sha256="",
        expected_products_sha256="",
        expected_platform_mappings_sha256="",
        apply=False,
    )

    assert preview["mode"] == "READ_ONLY_PREVIEW"
    assert preview["source_foreign_key_violation_count"] == 1
    assert preview["sku_count"] == 2
    assert preview["inventory_total"] == 19
    assert not workspace.exists()
    assert (
        sqlite_logical_snapshot_sha256(SQLiteRuntimeRepository(source))
        == source_snapshot
    )


def test_prepare_preview_cli_runs_as_a_direct_utf8_script(tmp_path: Path) -> None:
    source = tmp_path / "runtime.sqlite3"
    source_snapshot = _create_unhealthy_test_runtime(source)
    products, mappings = _write_business_inputs(tmp_path)
    workspace = tmp_path / "不会创建的预览目录"
    environment = os.environ.copy()
    environment.update(
        {
            "PRA_RUNTIME_DB": str(source),
            "PRA_PRODUCTS_WORKBOOK": str(products),
            "PRA_PLATFORM_MAPPINGS_WORKBOOK": str(mappings),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/clean_runtime_cutover.py",
            "prepare",
            "--source-runtime-db",
            str(source),
            "--products",
            str(products),
            "--platform-mappings",
            str(mappings),
            "--workspace-dir",
            str(workspace),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "READ_ONLY_PREVIEW"
    assert payload["source_runtime_snapshot_sha256"] == source_snapshot
    assert payload["sku_count"] == 2
    assert payload["inventory_total"] == 19
    assert not workspace.exists()


def test_candidate_must_be_bootstrapped_before_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, products, mappings, workspace, _ = _prepare(tmp_path, monkeypatch)

    with pytest.raises(CleanRuntimeCutoverError, match="尚未完成真实库存 bootstrap"):
        verify_candidate(
            manifest_path=workspace / MANIFEST_NAME,
            products_path=products,
            platform_mappings_path=mappings,
        )


def test_clean_v17_activation_and_immediate_rollback_are_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, products, mappings, workspace, source_snapshot = _prepare(
        tmp_path, monkeypatch
    )
    manifest_path = workspace / MANIFEST_NAME
    now = datetime.now(timezone.utc)
    candidate_snapshot = _bootstrap_candidate(
        manifest_path=manifest_path,
        products=products,
        now=now,
    )
    verification = verify_candidate(
        manifest_path=manifest_path,
        products_path=products,
        platform_mappings_path=mappings,
    )
    from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION
    assert verification["schema_version"] == LATEST_RUNTIME_SCHEMA_VERSION
    assert verification["authority_mode"] == "DB_AUTHORITY"
    assert verification["sku_count"] == 2
    assert verification["inventory_total"] == 19
    assert verification["candidate_runtime_snapshot_sha256"] == candidate_snapshot

    with pytest.raises(CleanRuntimeCutoverError, match="确认文本不匹配"):
        activate_candidate(
            manifest_path=manifest_path,
            source_runtime_db=source,
            products_path=products,
            platform_mappings_path=mappings,
            expected_source_snapshot_sha256=source_snapshot,
            expected_candidate_snapshot_sha256=candidate_snapshot,
            confirmation="",
            apply=True,
        )
    assert (
        sqlite_logical_snapshot_sha256(SQLiteRuntimeRepository(source))
        == source_snapshot
    )

    activated = activate_candidate(
        manifest_path=manifest_path,
        source_runtime_db=source,
        products_path=products,
        platform_mappings_path=mappings,
        expected_source_snapshot_sha256=source_snapshot,
        expected_candidate_snapshot_sha256=candidate_snapshot,
        confirmation=ACTIVATION_CONFIRMATION,
        apply=True,
    )
    assert activated["mode"] == "ACTIVATED"
    assert SQLiteRuntimeRepository(source).check_schema_health().ok
    inventory = InventoryRepository(SQLiteRuntimeRepository(source))
    assert inventory.get_authority_state().authority_mode == "DB_AUTHORITY"
    assert {
        item.internal_sku: item.current_qty for item in inventory.list_balances()
    } == {"AISHA-A-65-Z": 12, "CAPPUCCINO-B-50-Z": 7}

    rolled_back = rollback_activation(
        activation_record_path=Path(activated["activation_record"]),
        source_runtime_db=source,
        expected_current_snapshot_sha256=candidate_snapshot,
        confirmation=ROLLBACK_CONFIRMATION,
        apply=True,
    )
    assert rolled_back["mode"] == "ROLLED_BACK"
    assert (
        sqlite_logical_snapshot_sha256(SQLiteRuntimeRepository(source))
        == source_snapshot
    )
    with closing(SQLiteRuntimeRepository(source).connect_read()) as connection:
        orphan = connection.execute(
            "SELECT run_id FROM automation_run_events WHERE event_id = ?",
            ("ORPHAN-EVENT",),
        ).fetchone()
    assert orphan is not None
    assert orphan["run_id"] == "DELETED-TEST-RUN"


def test_activation_failure_restores_the_original_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, products, mappings, workspace, source_snapshot = _prepare(
        tmp_path, monkeypatch
    )
    manifest_path = workspace / MANIFEST_NAME
    candidate_snapshot = _bootstrap_candidate(
        manifest_path=manifest_path,
        products=products,
        now=datetime.now(timezone.utc),
    )

    def fail_readback(**_kwargs):
        raise CleanRuntimeCutoverError("合成激活回读失败")

    monkeypatch.setattr(cutover, "_verify_activated_runtime", fail_readback)
    with pytest.raises(CleanRuntimeCutoverError, match="合成激活回读失败"):
        activate_candidate(
            manifest_path=manifest_path,
            source_runtime_db=source,
            products_path=products,
            platform_mappings_path=mappings,
            expected_source_snapshot_sha256=source_snapshot,
            expected_candidate_snapshot_sha256=candidate_snapshot,
            confirmation=ACTIVATION_CONFIRMATION,
            apply=True,
        )

    assert (
        sqlite_logical_snapshot_sha256(SQLiteRuntimeRepository(source))
        == source_snapshot
    )
    assert not SQLiteRuntimeRepository(source).check_schema_health().ok
