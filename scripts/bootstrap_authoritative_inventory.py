from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.inventory_repository import InventoryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.services.authoritative_inventory import InventoryApplicationService


def main() -> int:
    _configure_output()
    args = _parser().parse_args()
    products_path = args.products.resolve(strict=True)
    runtime_db = args.runtime_db.resolve(strict=True)
    products_sha256 = _file_sha256(products_path)
    runtime_sha256 = _file_sha256(runtime_db)
    products = load_products(products_path)
    _validate_products(products)
    repository = SQLiteRuntimeRepository(runtime_db)
    health = repository.check_schema_health()
    if not health.ok:
        raise RuntimeError(
            "Runtime Schema 未通过健康检查；请先走独立维护、备份和回读门禁："
            + health.summary
        )
    authority = InventoryRepository(repository).get_authority_state()
    preview = {
        "mode": "APPLY" if args.apply else "READ_ONLY_PREVIEW",
        "products_path": str(products_path),
        "products_sha256": products_sha256,
        "runtime_db": str(runtime_db),
        "runtime_db_sha256_before": runtime_sha256,
        "authority_mode_before": authority.authority_mode,
        "sku_count": len(products),
        "inventory_total": sum(item.current_stock for item in products),
    }
    if not args.apply:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    _require_expected_hash(
        args.expected_products_sha256,
        products_sha256,
        "商品工作簿",
    )
    _require_expected_hash(
        args.expected_runtime_db_sha256,
        runtime_sha256,
        "Runtime DB",
    )
    if args.backup_dir is None:
        raise ValueError("--apply 必须同时提供 --backup-dir")
    backup_dir = args.backup_dir.resolve(strict=False)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    products_backup = backup_dir / f"products.before-inventory-cutover.{timestamp}.xlsx"
    runtime_backup = backup_dir / f"runtime.before-inventory-cutover.{timestamp}.sqlite3"
    shutil.copy2(products_path, products_backup)
    _backup_sqlite(runtime_db, runtime_backup)
    if _file_sha256(products_backup) != products_sha256:
        raise RuntimeError("商品工作簿备份哈希不一致，已停止切换")
    _verify_sqlite_backup(runtime_backup)
    if _file_sha256(products_path) != products_sha256:
        raise RuntimeError("商品工作簿在切换前发生变化，已停止切换")

    result = InventoryApplicationService(repository).bootstrap(
        products,
        snapshot_sha256=products_sha256,
        idempotency_key=f"inventory-bootstrap:{products_sha256}",
        actor=args.actor,
    )
    balances = InventoryRepository(repository).list_balances()
    expected = {
        item.internal_sku: item.current_stock
        for item in products
    }
    actual = {
        item.internal_sku: item.current_qty
        for item in balances
    }
    if actual != expected or sum(actual.values()) != sum(expected.values()):
        raise RuntimeError("库存切换回读与工作簿冻结快照不一致")
    if _file_sha256(products_path) != products_sha256:
        raise RuntimeError("切换期间商品工作簿发生变化，必须人工复核")
    report = {
        **preview,
        "result_status": result.status,
        "authority_mode_after": result.authority_state.authority_mode,
        "balance_count_after": len(balances),
        "inventory_total_after": sum(actual.values()),
        "products_backup": str(products_backup),
        "runtime_db_backup": str(runtime_backup),
        "runtime_db_sha256_after": _file_sha256(runtime_db),
        "verified": True,
    }
    report_path = backup_dir / f"inventory-cutover-report.{timestamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="显式校验或执行 Excel 到 Runtime DB 的唯一真实库存权威切换。"
    )
    parser.add_argument("--products", required=True, type=Path)
    parser.add_argument("--runtime-db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-products-sha256", default="")
    parser.add_argument("--expected-runtime-db-sha256", default="")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--actor", default="admin:inventory-cutover")
    return parser


def _validate_products(products) -> None:
    if not products:
        raise ValueError("商品工作簿没有可切换的商品")
    skus = [str(item.internal_sku).strip() for item in products]
    if any(not sku for sku in skus) or len(skus) != len(set(skus)):
        raise ValueError("商品工作簿 internal_sku 为空或重复")
    if any(item.current_stock < 0 for item in products):
        raise ValueError("商品工作簿库存不能为负数")


def _require_expected_hash(expected: str, actual: str, label: str) -> None:
    normalized = str(expected).strip().lower()
    if not normalized:
        raise ValueError(f"--apply 必须提供 {label} 的预期 SHA-256")
    if normalized != actual:
        raise ValueError(f"{label} 当前哈希与预期不一致")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + digest


def _backup_sqlite(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source)) as source_connection, closing(
        sqlite3.connect(destination)
    ) as destination_connection:
        source_connection.backup(destination_connection, pages=1000, sleep=0.05)


def _verify_sqlite_backup(path: Path) -> None:
    uri = f"file:{path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        result = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    if result != ["ok"]:
        raise RuntimeError("Runtime DB 备份完整性检查失败，已停止切换")


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
