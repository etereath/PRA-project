from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.inventory_repository import InventoryRepository  # noqa: E402
from app.repositories.sqlite_runtime_repository import (  # noqa: E402
    SQLiteRuntimeRepository,
)
from app.repositories.workbook_repository import load_products  # noqa: E402
from app.services.authoritative_inventory import (  # noqa: E402
    sqlite_logical_snapshot_sha256,
)
from app.services.product_mapping import (  # noqa: E402
    compile_product_mapping_workbook,
)


MANIFEST_NAME = "clean-runtime-cutover-manifest.json"
MANIFEST_VERSION = 1
ACTIVATION_CONFIRMATION = "REPLACE_TEST_RUNTIME_WITH_CLEAN_V17"
ROLLBACK_CONFIRMATION = "ROLLBACK_TO_ARCHIVED_TEST_RUNTIME"


class CleanRuntimeCutoverError(RuntimeError):
    """Raised when a clean Runtime cutover gate cannot be proven."""


def prepare_clean_runtime(
    *,
    source_runtime_db: Path,
    products_path: Path,
    platform_mappings_path: Path,
    workspace_dir: Path,
    expected_source_snapshot_sha256: str,
    expected_products_sha256: str,
    expected_platform_mappings_sha256: str,
    apply: bool,
) -> dict[str, Any]:
    source = _require_file(source_runtime_db, "旧 Runtime DB")
    products = _require_file(products_path, "商品工作簿")
    mappings = _require_file(platform_mappings_path, "平台映射工作簿")
    workspace = workspace_dir.resolve(strict=False)
    _require_safe_workspace(workspace, source)
    _require_canonical_inputs(source, products, mappings)

    source_repository = SQLiteRuntimeRepository(source)
    source_snapshot = sqlite_logical_snapshot_sha256(source_repository)
    products_sha256 = _file_sha256(products)
    mappings_sha256 = _file_sha256(mappings)
    _check_preview_or_apply_hash(
        expected_source_snapshot_sha256,
        source_snapshot,
        "旧 Runtime DB 逻辑快照",
        apply=apply,
    )
    _check_preview_or_apply_hash(
        expected_products_sha256,
        products_sha256,
        "商品工作簿",
        apply=apply,
    )
    _check_preview_or_apply_hash(
        expected_platform_mappings_sha256,
        mappings_sha256,
        "平台映射工作簿",
        apply=apply,
    )

    product_items = tuple(load_products(products))
    if not product_items:
        raise CleanRuntimeCutoverError("商品工作簿没有可保留的正式 SKU")
    if any(item.current_stock < 0 for item in product_items):
        raise CleanRuntimeCutoverError("商品工作簿包含负库存，不能准备切换")
    compiled_mappings = compile_product_mapping_workbook(mappings)
    mapping_status_counts = Counter(
        item.mapping_status.value for item in compiled_mappings.records
    )
    source_health = source_repository.check_schema_health()
    source_integrity, source_foreign_key_violations = _sqlite_integrity(source)
    preview = {
        "mode": "APPLY" if apply else "READ_ONLY_PREVIEW",
        "manifest_version": MANIFEST_VERSION,
        "source_runtime_db": str(source),
        "workspace_dir": str(workspace),
        "source_runtime_snapshot_sha256": source_snapshot,
        "source_schema_health": source_health.as_dict(),
        "source_integrity_check": source_integrity,
        "source_foreign_key_violation_count": source_foreign_key_violations,
        "products_sha256": products_sha256,
        "platform_mappings_sha256": mappings_sha256,
        "sku_count": len(product_items),
        "inventory_total": sum(item.current_stock for item in product_items),
        "mapping_status_counts": dict(sorted(mapping_status_counts.items())),
    }
    if not apply:
        return preview
    if workspace.exists() and any(workspace.iterdir()):
        raise CleanRuntimeCutoverError("切换工作目录不是空目录，拒绝覆盖已有证据")

    workspace.mkdir(parents=True, exist_ok=True)
    archive_dir = workspace / "archive"
    candidate_dir = workspace / "candidate"
    inputs_dir = workspace / "inputs"
    archive_dir.mkdir()
    candidate_dir.mkdir()
    inputs_dir.mkdir()
    archived_runtime = archive_dir / "legacy-test-runtime.sqlite3"
    candidate_runtime = candidate_dir / "pra_runtime-v17-candidate.sqlite3"
    archived_products = inputs_dir / "products.xlsx"
    archived_mappings = inputs_dir / "platform_mappings.xlsx"
    try:
        _backup_sqlite(source, archived_runtime)
        archived_snapshot = sqlite_logical_snapshot_sha256(
            SQLiteRuntimeRepository(archived_runtime)
        )
        if archived_snapshot != source_snapshot:
            raise CleanRuntimeCutoverError("旧 Runtime DB 归档逻辑快照不一致")
        shutil.copy2(products, archived_products)
        shutil.copy2(mappings, archived_mappings)
        if _file_sha256(archived_products) != products_sha256:
            raise CleanRuntimeCutoverError("商品工作簿归档哈希不一致")
        if _file_sha256(archived_mappings) != mappings_sha256:
            raise CleanRuntimeCutoverError("平台映射工作簿归档哈希不一致")

        candidate_repository = SQLiteRuntimeRepository(candidate_runtime)
        candidate_repository.init_schema()
        candidate_health = candidate_repository.check_schema_health()
        if not candidate_health.ok:
            raise CleanRuntimeCutoverError(
                "新建 v17 候选库未通过健康检查：" + candidate_health.summary
            )
        candidate_authority = InventoryRepository(
            candidate_repository
        ).get_authority_state()
        if candidate_authority.authority_mode != "PRE_CUTOVER":
            raise CleanRuntimeCutoverError("新建 v17 候选库不是 PRE_CUTOVER 状态")
        candidate_snapshot = sqlite_logical_snapshot_sha256(candidate_repository)
        manifest = {
            **preview,
            "mode": "PREPARED",
            "prepared_at": _utc_now(),
            "archive_runtime_db": str(archived_runtime),
            "archive_runtime_snapshot_sha256": archived_snapshot,
            "archived_products": str(archived_products),
            "archived_platform_mappings": str(archived_mappings),
            "candidate_runtime_db": str(candidate_runtime),
            "candidate_runtime_snapshot_sha256_at_prepare": candidate_snapshot,
            "candidate_schema_health": candidate_health.as_dict(),
            "candidate_authority_mode": candidate_authority.authority_mode,
            "preserved_products": [
                {
                    "internal_sku": item.internal_sku,
                    "product_name": item.product_name,
                    "grade": item.grade,
                    "stem_length": item.stem_length,
                    "unit": item.unit,
                    "base_cost": _decimal_text(item.base_cost),
                    "current_stock": item.current_stock,
                    "sale_enabled": item.sale_enabled,
                }
                for item in sorted(product_items, key=lambda value: value.internal_sku)
            ],
            "preserved_product_mappings": [
                {
                    "mapping_id": item.mapping_id,
                    "platform_name": item.platform_name,
                    "platform_product_name": item.platform_product_name,
                    "grade": item.grade,
                    "internal_sku": item.internal_sku,
                    "candidate_internal_sku": item.candidate_internal_sku,
                    "mapping_status": item.mapping_status.value,
                }
                for item in sorted(
                    compiled_mappings.records,
                    key=lambda value: (value.platform_name, value.mapping_id),
                )
            ],
        }
        _atomic_write_json(workspace / MANIFEST_NAME, manifest)
        return manifest
    except Exception:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        raise


def verify_candidate(
    *,
    manifest_path: Path,
    products_path: Path,
    platform_mappings_path: Path,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    products = _require_file(products_path, "商品工作簿")
    mappings = _require_file(platform_mappings_path, "平台映射工作簿")
    _require_manifest_hashes(manifest, products, mappings)
    candidate = _manifest_file(manifest, "candidate_runtime_db")
    repository = SQLiteRuntimeRepository(candidate)
    health = repository.check_schema_health()
    if not health.ok:
        raise CleanRuntimeCutoverError("v17 候选库未通过健康检查：" + health.summary)
    authority = InventoryRepository(repository).get_authority_state()
    if authority.authority_mode != "DB_AUTHORITY":
        raise CleanRuntimeCutoverError("v17 候选库尚未完成真实库存 bootstrap，不能激活")
    product_items = tuple(load_products(products))
    expected_balances = {
        item.internal_sku: int(item.current_stock) for item in product_items
    }
    balance_items = InventoryRepository(repository).list_balances()
    actual_balances = {
        item.internal_sku: int(item.current_qty) for item in balance_items
    }
    if actual_balances != expected_balances:
        raise CleanRuntimeCutoverError("候选库逐 SKU 库存回读与商品冻结快照不一致")
    integrity, foreign_key_violations = _sqlite_integrity(candidate)
    if integrity.lower() != "ok" or foreign_key_violations:
        raise CleanRuntimeCutoverError("候选库 SQLite 完整性或外键检查失败")
    return {
        "status": "VERIFIED",
        "candidate_runtime_db": str(candidate),
        "candidate_runtime_snapshot_sha256": sqlite_logical_snapshot_sha256(repository),
        "schema_version": health.actual_version,
        "authority_mode": authority.authority_mode,
        "sku_count": len(actual_balances),
        "inventory_total": sum(actual_balances.values()),
        "products_sha256": _file_sha256(products),
        "platform_mappings_sha256": _file_sha256(mappings),
        "integrity_check": integrity,
        "foreign_key_violation_count": foreign_key_violations,
    }


def activate_candidate(
    *,
    manifest_path: Path,
    source_runtime_db: Path,
    products_path: Path,
    platform_mappings_path: Path,
    expected_source_snapshot_sha256: str,
    expected_candidate_snapshot_sha256: str,
    confirmation: str,
    apply: bool,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    source = _require_file(source_runtime_db, "当前 Runtime DB")
    products = _require_file(products_path, "商品工作簿")
    mappings = _require_file(platform_mappings_path, "平台映射工作簿")
    _require_canonical_inputs(source, products, mappings)
    _require_manifest_hashes(manifest, products, mappings)
    source_snapshot = sqlite_logical_snapshot_sha256(SQLiteRuntimeRepository(source))
    candidate_verification = verify_candidate(
        manifest_path=manifest_path,
        products_path=products,
        platform_mappings_path=mappings,
    )
    candidate_snapshot = str(
        candidate_verification["candidate_runtime_snapshot_sha256"]
    )
    _check_preview_or_apply_hash(
        expected_source_snapshot_sha256,
        source_snapshot,
        "激活前 Runtime DB 逻辑快照",
        apply=apply,
    )
    if source_snapshot != str(manifest["source_runtime_snapshot_sha256"]):
        raise CleanRuntimeCutoverError("旧 Runtime DB 在准备后发生变化，必须重新准备")
    _check_preview_or_apply_hash(
        expected_candidate_snapshot_sha256,
        candidate_snapshot,
        "v17 候选库逻辑快照",
        apply=apply,
    )
    preview = {
        "mode": "APPLY" if apply else "READ_ONLY_PREVIEW",
        "source_runtime_db": str(source),
        "source_runtime_snapshot_sha256": source_snapshot,
        **candidate_verification,
        "status": "READY_TO_ACTIVATE",
    }
    if not apply:
        return preview
    if confirmation != ACTIVATION_CONFIRMATION:
        raise CleanRuntimeCutoverError("激活确认文本不匹配；未替换真实 Runtime DB")

    workspace = manifest_path.resolve().parent
    if os.name == "nt" and workspace.drive.casefold() != source.drive.casefold():
        raise CleanRuntimeCutoverError(
            "激活工作目录必须与 canonical Runtime DB 位于同一磁盘"
        )
    activation_dir = workspace / "activation" / (f"{_timestamp()}-{uuid4().hex[:8]}")
    activation_dir.mkdir(parents=True, exist_ok=False)
    final_legacy_archive = activation_dir / "runtime-before-activation.sqlite3"
    staged_candidate = source.with_name(f".{source.name}.clean-v17-stage-{uuid4().hex}")
    displaced_source = activation_dir / "runtime-displaced.sqlite3"
    displaced_sidecars: list[tuple[Path, Path]] = []
    source_displaced = False
    try:
        _checkpoint_sqlite(source)
        _checkpoint_sqlite(_manifest_file(manifest, "candidate_runtime_db"))
        _backup_sqlite(source, final_legacy_archive)
        if (
            sqlite_logical_snapshot_sha256(
                SQLiteRuntimeRepository(final_legacy_archive)
            )
            != source_snapshot
        ):
            raise CleanRuntimeCutoverError("激活前最终旧库归档回读不一致")
        _backup_sqlite(
            _manifest_file(manifest, "candidate_runtime_db"),
            staged_candidate,
        )
        staged_snapshot = sqlite_logical_snapshot_sha256(
            SQLiteRuntimeRepository(staged_candidate)
        )
        if staged_snapshot != candidate_snapshot:
            raise CleanRuntimeCutoverError("候选库激活暂存副本回读不一致")
        os.replace(source, displaced_source)
        source_displaced = True
        displaced_sidecars = _displace_sidecars(source, activation_dir)
        os.replace(staged_candidate, source)
        activated_verification = _verify_activated_runtime(
            source=source,
            products=products,
            expected_snapshot=candidate_snapshot,
        )
        activation_record = {
            **preview,
            **activated_verification,
            "mode": "ACTIVATED",
            "activated_at": _utc_now(),
            "final_legacy_archive": str(final_legacy_archive),
            "displaced_source": str(displaced_source),
        }
        record_path = activation_dir / "activation-record.json"
        _atomic_write_json(record_path, activation_record)
        return {**activation_record, "activation_record": str(record_path)}
    except Exception:
        if source_displaced and displaced_source.exists():
            failed_runtime = activation_dir / "failed-activated-runtime.sqlite3"
            _remove_sidecars(source)
            if source.exists():
                os.replace(source, failed_runtime)
            os.replace(displaced_source, source)
            _restore_sidecars(displaced_sidecars)
        raise
    finally:
        staged_candidate.unlink(missing_ok=True)


def rollback_activation(
    *,
    activation_record_path: Path,
    source_runtime_db: Path,
    expected_current_snapshot_sha256: str,
    confirmation: str,
    apply: bool,
) -> dict[str, Any]:
    record = _load_json(activation_record_path)
    source = _require_file(source_runtime_db, "当前 Runtime DB")
    canonical = _canonical_path(
        "PRA_RUNTIME_DB", Path("data/runtime/pra_runtime.sqlite3")
    )
    if source != canonical:
        raise CleanRuntimeCutoverError("回滚只允许固定 PRA_RUNTIME_DB")
    current_repository = SQLiteRuntimeRepository(source)
    current_snapshot = sqlite_logical_snapshot_sha256(current_repository)
    _check_preview_or_apply_hash(
        expected_current_snapshot_sha256,
        current_snapshot,
        "回滚前 Runtime DB 逻辑快照",
        apply=apply,
    )
    if current_snapshot != str(record.get("candidate_runtime_snapshot_sha256")):
        raise CleanRuntimeCutoverError("当前 Runtime DB 已在激活后变化，拒绝回滚")
    inventory = InventoryRepository(current_repository)
    with closing(current_repository.connect_read()) as connection:
        post_bootstrap = int(
            connection.execute(
                "SELECT COUNT(*) FROM inventory_transactions "
                "WHERE transaction_type <> 'BOOTSTRAP'"
            ).fetchone()[0]
        )
    if post_bootstrap:
        raise CleanRuntimeCutoverError("激活后已有新库存流水，禁止回滚到旧测试库")
    if inventory.get_authority_state().authority_mode != "DB_AUTHORITY":
        raise CleanRuntimeCutoverError("当前 Runtime DB 库存权威状态异常，拒绝回滚")
    archive = _require_file(
        Path(str(record.get("final_legacy_archive") or "")),
        "激活前旧库归档",
    )
    preview = {
        "mode": "APPLY" if apply else "READ_ONLY_PREVIEW",
        "status": "READY_TO_ROLLBACK",
        "current_runtime_snapshot_sha256": current_snapshot,
        "legacy_archive": str(archive),
    }
    if not apply:
        return preview
    if confirmation != ROLLBACK_CONFIRMATION:
        raise CleanRuntimeCutoverError("回滚确认文本不匹配；未替换 Runtime DB")
    rollback_dir = activation_record_path.resolve().parent / "rollback"
    rollback_dir.mkdir(exist_ok=False)
    current_backup = rollback_dir / "runtime-before-rollback.sqlite3"
    staged_legacy = source.with_name(f".{source.name}.legacy-stage-{uuid4().hex}")
    displaced_current = rollback_dir / "runtime-displaced-v17.sqlite3"
    displaced_sidecars: list[tuple[Path, Path]] = []
    source_displaced = False
    try:
        _backup_sqlite(source, current_backup)
        _backup_sqlite(archive, staged_legacy)
        os.replace(source, displaced_current)
        source_displaced = True
        displaced_sidecars = _displace_sidecars(source, rollback_dir)
        os.replace(staged_legacy, source)
        restored_snapshot = sqlite_logical_snapshot_sha256(
            SQLiteRuntimeRepository(source)
        )
        expected_legacy = str(record.get("source_runtime_snapshot_sha256") or "")
        if restored_snapshot != expected_legacy:
            raise CleanRuntimeCutoverError("旧库回滚后的逻辑快照不一致")
        result = {
            **preview,
            "mode": "ROLLED_BACK",
            "rolled_back_at": _utc_now(),
            "restored_runtime_snapshot_sha256": restored_snapshot,
            "preserved_v17_runtime": str(displaced_current),
        }
        _atomic_write_json(rollback_dir / "rollback-record.json", result)
        return result
    except Exception:
        if source_displaced and displaced_current.exists():
            _remove_sidecars(source)
            if source.exists():
                source.unlink()
            os.replace(displaced_current, source)
            _restore_sidecars(displaced_sidecars)
        raise
    finally:
        staged_legacy.unlink(missing_ok=True)


def _verify_activated_runtime(
    *, source: Path, products: Path, expected_snapshot: str
) -> dict[str, Any]:
    repository = SQLiteRuntimeRepository(source)
    health = repository.check_schema_health()
    if not health.ok:
        raise CleanRuntimeCutoverError(
            "激活后的 Runtime DB 未通过健康检查：" + health.summary
        )
    snapshot = sqlite_logical_snapshot_sha256(repository)
    if snapshot != expected_snapshot:
        raise CleanRuntimeCutoverError("激活后的 Runtime DB 逻辑快照不一致")
    expected = {
        item.internal_sku: int(item.current_stock) for item in load_products(products)
    }
    actual = {
        item.internal_sku: int(item.current_qty)
        for item in InventoryRepository(repository).list_balances()
    }
    if actual != expected:
        raise CleanRuntimeCutoverError("激活后的逐 SKU 库存回读不一致")
    return {
        "activated_runtime_snapshot_sha256": snapshot,
        "activated_schema_version": health.actual_version,
        "activated_sku_count": len(actual),
        "activated_inventory_total": sum(actual.values()),
    }


def _require_manifest_hashes(
    manifest: dict[str, Any], products: Path, mappings: Path
) -> None:
    if _file_sha256(products) != str(manifest.get("products_sha256")):
        raise CleanRuntimeCutoverError("商品工作簿在准备后发生变化")
    if _file_sha256(mappings) != str(manifest.get("platform_mappings_sha256")):
        raise CleanRuntimeCutoverError("平台映射工作簿在准备后发生变化")


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest = _load_json(path)
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CleanRuntimeCutoverError("干净重建清单版本不受支持")
    if manifest.get("mode") != "PREPARED":
        raise CleanRuntimeCutoverError("干净重建清单尚未完成 PREPARED")
    return manifest


def _load_json(path: Path) -> dict[str, Any]:
    source = _require_file(path, "JSON 记录")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanRuntimeCutoverError("JSON 记录不是有效 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise CleanRuntimeCutoverError("JSON 记录根节点必须是对象")
    return payload


def _manifest_file(manifest: dict[str, Any], field: str) -> Path:
    value = str(manifest.get(field) or "").strip()
    if not value:
        raise CleanRuntimeCutoverError(f"清单缺少 {field}")
    return _require_file(Path(value), field)


def _require_canonical_inputs(source: Path, products: Path, mappings: Path) -> None:
    expected = (
        (
            source,
            _canonical_path("PRA_RUNTIME_DB", Path("data/runtime/pra_runtime.sqlite3")),
            "Runtime DB",
        ),
        (
            products,
            _canonical_path(
                "PRA_PRODUCTS_WORKBOOK", Path("data/samples/products.xlsx")
            ),
            "商品工作簿",
        ),
        (
            mappings,
            _canonical_path(
                "PRA_PLATFORM_MAPPINGS_WORKBOOK",
                Path("data/samples/platform_mappings.xlsx"),
            ),
            "平台映射工作簿",
        ),
    )
    for actual, canonical, label in expected:
        if actual != canonical:
            raise CleanRuntimeCutoverError(f"{label} 不是固定环境配置路径")


def _canonical_path(environment_name: str, default: Path) -> Path:
    configured = Path(os.getenv(environment_name, str(default)).strip())
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve(strict=False)


def _require_safe_workspace(workspace: Path, source: Path) -> None:
    if workspace == Path(workspace.anchor) or workspace == PROJECT_ROOT:
        raise CleanRuntimeCutoverError("切换工作目录不能是磁盘根目录或项目根目录")
    if workspace == source.parent or source.is_relative_to(workspace):
        raise CleanRuntimeCutoverError("切换工作目录不能包含当前 Runtime DB")


def _require_expected_hash(expected: str, actual: str, label: str) -> None:
    normalized = str(expected or "").strip().lower()
    if not normalized:
        raise CleanRuntimeCutoverError(f"必须提供 {label} 的预期 SHA-256")
    if normalized != actual:
        raise CleanRuntimeCutoverError(f"{label} 与预期 SHA-256 不一致")


def _check_preview_or_apply_hash(
    expected: str, actual: str, label: str, *, apply: bool
) -> None:
    if apply or str(expected or "").strip():
        _require_expected_hash(expected, actual, label)


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sqlite_integrity(path: Path) -> tuple[str, int]:
    uri = f"file:{path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    return (str(integrity[0]) if integrity else "", len(violations))


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=5)) as source_connection:
        with closing(sqlite3.connect(str(destination), timeout=5)) as target:
            source_connection.backup(target, pages=1000, sleep=0.05)
            target.execute("PRAGMA journal_mode = WAL")
            target.execute("PRAGMA synchronous = NORMAL")
            target.commit()
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _checkpoint_sqlite(path: Path) -> None:
    with closing(sqlite3.connect(str(path), timeout=5)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _runtime_sidecars(path: Path) -> tuple[Path, Path]:
    return (Path(str(path) + "-wal"), Path(str(path) + "-shm"))


def _displace_sidecars(
    runtime_path: Path, destination_dir: Path
) -> list[tuple[Path, Path]]:
    displaced: list[tuple[Path, Path]] = []
    try:
        for sidecar in _runtime_sidecars(runtime_path):
            if not sidecar.exists():
                continue
            destination = destination_dir / ("old-" + sidecar.name)
            os.replace(sidecar, destination)
            displaced.append((destination, sidecar))
    except Exception:
        _restore_sidecars(displaced)
        raise
    return displaced


def _restore_sidecars(displaced: list[tuple[Path, Path]]) -> None:
    for source, destination in displaced:
        if source.exists():
            os.replace(source, destination)


def _remove_sidecars(runtime_path: Path) -> None:
    for sidecar in _runtime_sidecars(runtime_path):
        sidecar.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_file(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    if not resolved.is_file():
        raise CleanRuntimeCutoverError(f"{label}不存在：{resolved}")
    return resolved


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="归档旧测试 Runtime，并受控准备、验证、激活或回滚干净 v17 Runtime。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source-runtime-db", type=Path, required=True)
    prepare.add_argument("--products", type=Path, required=True)
    prepare.add_argument("--platform-mappings", type=Path, required=True)
    prepare.add_argument("--workspace-dir", type=Path, required=True)
    prepare.add_argument("--expected-source-snapshot-sha256", default="")
    prepare.add_argument("--expected-products-sha256", default="")
    prepare.add_argument("--expected-platform-mappings-sha256", default="")
    prepare.add_argument("--apply", action="store_true")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--products", type=Path, required=True)
    verify.add_argument("--platform-mappings", type=Path, required=True)

    activate = subparsers.add_parser("activate")
    activate.add_argument("--manifest", type=Path, required=True)
    activate.add_argument("--source-runtime-db", type=Path, required=True)
    activate.add_argument("--products", type=Path, required=True)
    activate.add_argument("--platform-mappings", type=Path, required=True)
    activate.add_argument("--expected-source-snapshot-sha256", default="")
    activate.add_argument("--expected-candidate-snapshot-sha256", default="")
    activate.add_argument("--confirmation", default="")
    activate.add_argument("--apply", action="store_true")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--activation-record", type=Path, required=True)
    rollback.add_argument("--source-runtime-db", type=Path, required=True)
    rollback.add_argument("--expected-current-snapshot-sha256", default="")
    rollback.add_argument("--confirmation", default="")
    rollback.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_output()
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_clean_runtime(
                source_runtime_db=args.source_runtime_db,
                products_path=args.products,
                platform_mappings_path=args.platform_mappings,
                workspace_dir=args.workspace_dir,
                expected_source_snapshot_sha256=(args.expected_source_snapshot_sha256),
                expected_products_sha256=args.expected_products_sha256,
                expected_platform_mappings_sha256=(
                    args.expected_platform_mappings_sha256
                ),
                apply=args.apply,
            )
        elif args.command == "verify":
            result = verify_candidate(
                manifest_path=args.manifest,
                products_path=args.products,
                platform_mappings_path=args.platform_mappings,
            )
        elif args.command == "activate":
            result = activate_candidate(
                manifest_path=args.manifest,
                source_runtime_db=args.source_runtime_db,
                products_path=args.products,
                platform_mappings_path=args.platform_mappings,
                expected_source_snapshot_sha256=(args.expected_source_snapshot_sha256),
                expected_candidate_snapshot_sha256=(
                    args.expected_candidate_snapshot_sha256
                ),
                confirmation=args.confirmation,
                apply=args.apply,
            )
        else:
            result = rollback_activation(
                activation_record_path=args.activation_record,
                source_runtime_db=args.source_runtime_db,
                expected_current_snapshot_sha256=(
                    args.expected_current_snapshot_sha256
                ),
                confirmation=args.confirmation,
                apply=args.apply,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CleanRuntimeCutoverError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"clean_runtime_cutover=FAIL reason={exc}", file=sys.stderr)
        return 1


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
