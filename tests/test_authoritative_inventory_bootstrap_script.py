from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from app.repositories.inventory_repository import InventoryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import save_table_records
from app.services.authoritative_inventory import sqlite_logical_snapshot_sha256


def test_cutover_script_applies_only_canonical_frozen_snapshots(
    tmp_path: Path,
) -> None:
    products_path = tmp_path / "products.xlsx"
    runtime_path = tmp_path / "runtime.sqlite3"
    backup_dir = tmp_path / "backups"
    save_table_records(
        "products",
        products_path,
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
                "remark": "合成切换测试",
                "feature_season": "",
                "feature_color": "",
            }
        ],
    )
    runtime = SQLiteRuntimeRepository(runtime_path)
    runtime.init_schema()
    products_sha256 = "sha256:" + hashlib.sha256(
        products_path.read_bytes()
    ).hexdigest()
    runtime_snapshot_sha256 = sqlite_logical_snapshot_sha256(runtime)
    environment = os.environ.copy()
    environment.update(
        {
            "PRA_PRODUCTS_WORKBOOK": str(products_path),
            "PRA_RUNTIME_DB": str(runtime_path),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/bootstrap_authoritative_inventory.py",
            "--products",
            str(products_path),
            "--runtime-db",
            str(runtime_path),
            "--apply",
            "--expected-products-sha256",
            products_sha256,
            "--expected-runtime-snapshot-sha256",
            runtime_snapshot_sha256,
            "--backup-dir",
            str(backup_dir),
            "--actor",
            "test:cutover",
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
    assert '"verified": true' in completed.stdout
    inventory = InventoryRepository(runtime)
    assert inventory.get_authority_state().authority_mode == "DB_AUTHORITY"
    assert inventory.get_balance("AISHA-A-65-Z").current_qty == 12
    assert len(tuple(backup_dir.glob("*.xlsx"))) == 1
    assert len(tuple(backup_dir.glob("*.sqlite3"))) == 1
    assert len(tuple(backup_dir.glob("*.json"))) == 1
