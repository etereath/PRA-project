from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.exceptions import ValidationError
from app.repositories.workbook_repository import load_products
from app.shadowbot_contract_primitives import (
    canonical_positive_price,
    normalize_contract_grade as normalize_grade,
    normalize_contract_text as normalize_text,
    sha256_json,
)


CONTRACT_VERSION = 4
SCHEMA_VERSION = "shadowbot-commit-batch-request-1.1"
PROPOSAL_SCHEMA_VERSION = "shadowbot-commit-manifest-1.1"
MAX_ITEMS = 50
EXECUTION_PROFILES = frozenset({"development", "production"})
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_FORBIDDEN_POSITION_FIELDS = frozenset(
    {
        "ordinal",
        "page_position",
        "page_position_hint",
        "matched_product_position",
        "commit_batch_ordinal",
        "reuse_product_list",
        "runtime_ordinal",
        "execution_ordinal",
    }
)


def load_identity_mapping(path: Path, *, expected_platform_name: str = "") -> dict[str, dict[str, str]]:
    if Path(path).suffix.lower() in {".xlsx", ".xlsm"}:
        try:
            products = load_products(Path(path))
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise ValidationError(f"无法读取库存 SKU 映射源：{path}") from exc
        mapping = {
            product.internal_sku.upper(): {
                "expected_product_name": product.product_name,
                "expected_grade": product.grade,
            }
            for product in products
        }
        if not mapping:
            raise ValidationError("商品资料与库存录入中没有可用 SKU。")
        return mapping
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"无法读取 SKU 映射：{path}") from exc
    rows = raw.get("mappings") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValidationError("SKU 映射必须包含非空 mappings 数组。")
    mapping_platform = str(raw.get("platform_name") or "").strip()
    expected_platform = str(expected_platform_name or "").strip()
    if not mapping_platform:
        raise ValidationError("SKU 映射缺少 platform_name。")
    if expected_platform and normalize_text(mapping_platform) != normalize_text(expected_platform):
        raise ValidationError("SKU 映射平台与任务平台不一致。")
    mapping: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValidationError(f"SKU 映射第 {index} 项不是对象。")
        internal_sku = _required_text(row, "internal_sku").upper()
        if internal_sku in mapping:
            raise ValidationError(f"SKU 映射重复：{internal_sku}")
        if str(row.get("status") or "active").strip().lower() != "active":
            continue
        mapping[internal_sku] = {
            "expected_product_name": _required_text(row, "expected_product_name"),
            "expected_grade": _required_text(row, "expected_grade"),
        }
    if not mapping:
        raise ValidationError("SKU 映射中没有启用项。")
    return mapping


def build_commit_manifest(
    *,
    batch_id: str,
    task_items: list[dict[str, Any]],
    identity_mapping: dict[str, dict[str, str]],
    platform_name: str,
) -> dict[str, Any]:
    _validate_id(batch_id, "batch_id")
    normalized_platform = str(platform_name or "").strip()
    if not normalized_platform:
        raise ValidationError("platform_name 不能为空。")
    if not isinstance(task_items, list) or not task_items or len(task_items) > MAX_ITEMS:
        raise ValidationError(f"任务列表数量必须在 1 到 {MAX_ITEMS} 之间。")

    normalized_items: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    seen_skus: set[str] = set()
    seen_page_identities: set[tuple[str, str, str]] = set()
    for raw in task_items:
        if not isinstance(raw, dict):
            raise ValidationError("任务列表项目必须是对象。")
        allowed = {"source_task_id", "internal_sku", "expected_old_price", "target_price"}
        unexpected = sorted(set(raw) - allowed)
        if unexpected:
            raise ValidationError("任务项目包含非正式输入字段：" + ", ".join(unexpected))
        source_task_id = _required_text(raw, "source_task_id")
        _validate_id(source_task_id, "source_task_id")
        if source_task_id in seen_task_ids:
            raise ValidationError(f"同一批次 source_task_id 重复：{source_task_id}")
        seen_task_ids.add(source_task_id)
        internal_sku = _required_text(raw, "internal_sku").upper()
        if internal_sku in seen_skus:
            raise ValidationError(f"同一批次 internal_sku 重复：{internal_sku}")
        seen_skus.add(internal_sku)
        identity = identity_mapping.get(internal_sku)
        if not identity:
            raise ValidationError(f"SKU 未找到唯一启用映射：{internal_sku}")
        page_identity = (
            normalize_text(normalized_platform),
            normalize_text(identity.get("expected_product_name")),
            normalize_grade(identity.get("expected_grade")),
        )
        if not page_identity[1] or not page_identity[2]:
            raise ValidationError(f"SKU 页面身份映射不完整：{internal_sku}")
        if page_identity in seen_page_identities:
            raise ValidationError("多个 SKU 解析为同一平台页面身份。")
        seen_page_identities.add(page_identity)
        old_price = _price(raw.get("expected_old_price"), "expected_old_price")
        target_price = _price(raw.get("target_price"), "target_price")
        if old_price == target_price:
            raise ValidationError(f"SKU {internal_sku} 的旧价与目标价相同。")
        item = {
            "item_id": f"ITEM-{source_task_id}",
            "source_task_id": source_task_id,
            "internal_sku": internal_sku,
            "expected_product_name": str(identity["expected_product_name"]).strip(),
            "expected_grade": str(identity["expected_grade"]).strip(),
            "expected_old_price": old_price,
            "target_price": target_price,
        }
        item["item_payload_sha256"] = _sha256(_item_manifest_payload(normalized_platform, item))
        normalized_items.append(item)

    manifest = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "batch_id": batch_id,
        "platform_name": normalized_platform,
        "execution_mode": "COMMIT",
        "items": normalized_items,
    }
    manifest["manifest_sha256"] = _manifest_sha256(normalized_platform, normalized_items)
    manifest["development_confirmation_text"] = required_development_confirmation(
        batch_id, len(normalized_items)
    )
    return manifest


def build_commit_request(
    manifest: dict[str, Any],
    *,
    execution_profile: str,
    batch_task_id: str,
    operation_id: str,
    execution_attempt_id: str,
    applet_uri: str,
    confirmation_text: str = "",
    confirmed_by: str = "",
    window_title: str = "微信",
    capture_evidence: bool = False,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    validate_manifest(manifest)
    profile = str(execution_profile or "").strip().lower()
    if profile not in EXECUTION_PROFILES:
        raise ValidationError("execution_profile 必须是 development 或 production。")
    for value, name in (
        (batch_task_id, "batch_task_id"),
        (operation_id, "operation_id"),
        (execution_attempt_id, "execution_attempt_id"),
    ):
        _validate_id(value, name)

    development_confirmation: dict[str, str] | None = None
    if profile == "development":
        required = required_development_confirmation(str(manifest["batch_id"]), len(manifest["items"]))
        if str(confirmation_text or "").strip() != required:
            raise ValidationError("开发阶段确认文本与固定商品清单不匹配，未创建 COMMIT。")
        if not str(confirmed_by or "").strip():
            raise ValidationError("开发阶段 confirmed_by 不能为空。")
        development_confirmation = {
            "confirmed_by": str(confirmed_by).strip(),
            "confirmation_text": required,
        }
    elif str(confirmation_text or "").strip() or str(confirmed_by or "").strip():
        raise ValidationError("正式运行合同不得携带开发阶段人工确认字段。")

    now = datetime.now(timezone.utc)
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "execution_profile": profile,
        "task_id": batch_task_id,
        "operation_id": operation_id,
        "execution_attempt_id": execution_attempt_id,
        "execution_mode": "COMMIT",
        "batch_id": manifest["batch_id"],
        "platform_name": manifest["platform_name"],
        "items": [dict(item) for item in manifest["items"]],
        "manifest_sha256": manifest["manifest_sha256"],
        "applet_uri": str(applet_uri or "").strip(),
        "window_title": str(window_title or "微信").strip(),
        "capture_evidence": bool(capture_evidence),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=max(1, int(ttl_minutes)))).isoformat(),
    }
    if development_confirmation is not None:
        development_confirmation["confirmed_at"] = now.isoformat()
        request["development_confirmation"] = development_confirmation
    request["instruction_hash"] = compute_instruction_hash(request)
    validate_request(request)
    return request


def compute_instruction_hash(request: dict[str, Any]) -> str:
    canonical = {
        "schema_version": request.get("schema_version"),
        "contract_version": request.get("contract_version"),
        "execution_profile": request.get("execution_profile"),
        "task_id": request.get("task_id"),
        "operation_id": request.get("operation_id"),
        "execution_attempt_id": request.get("execution_attempt_id"),
        "execution_mode": request.get("execution_mode"),
        "batch_id": request.get("batch_id"),
        "platform_name": request.get("platform_name"),
        "items": request.get("items"),
        "manifest_sha256": request.get("manifest_sha256"),
        "development_confirmation": request.get("development_confirmation"),
        "applet_uri": request.get("applet_uri"),
        "window_title": request.get("window_title"),
        "capture_evidence": request.get("capture_evidence"),
        "created_at": request.get("created_at"),
        "expires_at": request.get("expires_at"),
    }
    return _sha256(canonical)


def validate_request(
    request: dict[str, Any],
    *,
    check_expiry: bool = True,
) -> None:
    if not isinstance(request, dict):
        raise ValidationError("COMMIT_BATCH_SCHEMA_INVALID")
    if request.get("contract_version") != CONTRACT_VERSION:
        raise ValidationError("UNKNOWN_CONTRACT_VERSION")
    if request.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("COMMIT_BATCH_SCHEMA_INVALID")
    if str(request.get("execution_mode") or "").upper() != "COMMIT":
        raise ValidationError("COMMIT_REQUIRED")
    profile = str(request.get("execution_profile") or "").strip().lower()
    if profile not in EXECUTION_PROFILES:
        raise ValidationError("INVALID_EXECUTION_PROFILE")
    allowed_request_fields = {
        "schema_version",
        "contract_version",
        "execution_profile",
        "task_id",
        "operation_id",
        "execution_attempt_id",
        "execution_mode",
        "batch_id",
        "platform_name",
        "items",
        "manifest_sha256",
        "development_confirmation",
        "applet_uri",
        "window_title",
        "capture_evidence",
        "created_at",
        "expires_at",
        "instruction_hash",
    }
    unexpected = sorted(set(request) - allowed_request_fields)
    if unexpected:
        raise ValidationError("COMMIT 请求包含非合同字段：" + ", ".join(unexpected))
    forbidden = sorted(_FORBIDDEN_POSITION_FIELDS.intersection(request))
    if forbidden:
        raise ValidationError("正式 COMMIT 合同禁止页面位置字段：" + ", ".join(forbidden))
    for name in ("task_id", "operation_id", "execution_attempt_id", "batch_id"):
        _validate_id(str(request.get(name) or ""), name)
    platform_name = _required_text(request, "platform_name")
    items = _validate_items(platform_name, request.get("items"))
    supplied_manifest_hash = str(request.get("manifest_sha256") or "")
    if not _SHA256_RE.fullmatch(supplied_manifest_hash):
        raise ValidationError("COMMIT 批次 manifest_sha256 无效。")
    if supplied_manifest_hash != _manifest_sha256(platform_name, items):
        raise ValidationError("COMMIT 批次清单哈希不匹配。")
    confirmation = request.get("development_confirmation")
    if profile == "development":
        if not isinstance(confirmation, dict):
            raise ValidationError("开发阶段 COMMIT 缺少人工确认。")
        allowed_confirmation_fields = {"confirmed_by", "confirmed_at", "confirmation_text"}
        if set(confirmation) - allowed_confirmation_fields:
            raise ValidationError("开发阶段确认包含非合同字段。")
        for name in allowed_confirmation_fields:
            _required_text(confirmation, name)
        if confirmation["confirmation_text"] != required_development_confirmation(
            str(request["batch_id"]), len(items)
        ):
            raise ValidationError("开发阶段确认文本不匹配。")
    elif confirmation is not None:
        raise ValidationError("正式运行合同不得携带开发阶段人工确认。")
    if not isinstance(request.get("capture_evidence"), bool):
        raise ValidationError("capture_evidence 必须是布尔值。")
    for name in ("created_at", "expires_at"):
        try:
            parsed = datetime.fromisoformat(str(request.get(name) or ""))
        except ValueError as exc:
            raise ValidationError(f"{name} 必须是 ISO-8601 时间。") from exc
        if parsed.tzinfo is None:
            raise ValidationError(f"{name} 必须包含时区。")
    if check_expiry and datetime.fromisoformat(str(request["expires_at"])).astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise ValidationError("COMMIT 批次合同已过期。")
    if request.get("instruction_hash") != compute_instruction_hash(request):
        raise ValidationError("COMMIT 批次 instruction_hash 不匹配。")


def validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PROPOSAL_SCHEMA_VERSION:
        raise ValidationError("COMMIT 清单格式无效。")
    allowed = {
        "schema_version",
        "batch_id",
        "platform_name",
        "execution_mode",
        "items",
        "manifest_sha256",
        "development_confirmation_text",
    }
    unexpected = sorted(set(manifest) - allowed)
    if unexpected:
        raise ValidationError("COMMIT 清单包含非合同字段：" + ", ".join(unexpected))
    _validate_id(str(manifest.get("batch_id") or ""), "batch_id")
    if str(manifest.get("execution_mode") or "").upper() != "COMMIT":
        raise ValidationError("COMMIT_REQUIRED")
    platform_name = _required_text(manifest, "platform_name")
    items = _validate_items(platform_name, manifest.get("items"))
    if manifest.get("manifest_sha256") != _manifest_sha256(platform_name, items):
        raise ValidationError("COMMIT 清单哈希不匹配。")
    if manifest.get("development_confirmation_text") != required_development_confirmation(
        str(manifest["batch_id"]), len(items)
    ):
        raise ValidationError("开发阶段确认提示不匹配。")


def required_development_confirmation(batch_id: str, item_count: int) -> str:
    return f"确认授权批次 {batch_id} 以上{item_count}项真实COMMIT"


def _validate_items(platform_name: str, raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > MAX_ITEMS:
        raise ValidationError("COMMIT 批次 items 数量无效。")
    allowed_item_fields = {
        "item_id",
        "source_task_id",
        "internal_sku",
        "expected_product_name",
        "expected_grade",
        "expected_old_price",
        "target_price",
        "item_payload_sha256",
    }
    seen_item_ids: set[str] = set()
    seen_task_ids: set[str] = set()
    seen_skus: set[str] = set()
    seen_identities: set[tuple[str, str, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValidationError("COMMIT 批次 item 必须是对象。")
        forbidden = sorted(_FORBIDDEN_POSITION_FIELDS.intersection(item))
        if forbidden:
            raise ValidationError("COMMIT item 禁止页面位置或顺序字段：" + ", ".join(forbidden))
        unexpected = sorted(set(item) - allowed_item_fields)
        if unexpected:
            raise ValidationError("COMMIT item 包含非合同字段：" + ", ".join(unexpected))
        for name in (
            "item_id",
            "source_task_id",
            "internal_sku",
            "expected_product_name",
            "expected_grade",
        ):
            _required_text(item, name)
        _validate_id(str(item["item_id"]), "item_id")
        _validate_id(str(item["source_task_id"]), "source_task_id")
        item_id = str(item["item_id"])
        task_id = str(item["source_task_id"])
        internal_sku = str(item["internal_sku"]).strip().upper()
        identity = (
            normalize_text(platform_name),
            normalize_text(item["expected_product_name"]),
            normalize_grade(item["expected_grade"]),
        )
        if item_id in seen_item_ids or task_id in seen_task_ids or internal_sku in seen_skus:
            raise ValidationError("COMMIT 批次存在重复 item、源任务或 SKU。")
        if identity in seen_identities:
            raise ValidationError("COMMIT 批次存在重复页面身份。")
        seen_item_ids.add(item_id)
        seen_task_ids.add(task_id)
        seen_skus.add(internal_sku)
        seen_identities.add(identity)
        _price(item.get("expected_old_price"), "expected_old_price")
        _price(item.get("target_price"), "target_price")
        supplied_hash = str(item.get("item_payload_sha256") or "")
        expected_hash = _sha256(_item_manifest_payload(platform_name, item))
        if not _SHA256_RE.fullmatch(supplied_hash) or supplied_hash != expected_hash:
            raise ValidationError("COMMIT item payload 哈希不匹配。")
    return raw_items


def _item_manifest_payload(platform_name: str, item: dict[str, Any]) -> dict[str, str]:
    return {
        "platform_name": str(platform_name or "").strip(),
        "source_task_id": str(item.get("source_task_id") or "").strip(),
        "internal_sku": str(item.get("internal_sku") or "").strip().upper(),
        "expected_old_price": _price(item.get("expected_old_price"), "expected_old_price"),
        "target_price": _price(item.get("target_price"), "target_price"),
    }


def _manifest_sha256(platform_name: str, items: list[dict[str, Any]]) -> str:
    canonical_items = sorted(
        (_item_manifest_payload(platform_name, item) for item in items),
        key=lambda item: (item["source_task_id"], item["internal_sku"]),
    )
    return _sha256({"items": canonical_items})


def _price(value: Any, name: str) -> str:
    try:
        return canonical_positive_price(value)
    except ValueError as exc:
        raise ValidationError(f"{name} 必须是有效价格。") from exc


def _required_text(payload: dict[str, Any], name: str) -> str:
    value = str(payload.get(name) or "").strip()
    if not value:
        raise ValidationError(f"{name} 不能为空。")
    return value


def _validate_id(value: str, name: str) -> None:
    if not _ID_RE.fullmatch(str(value or "")):
        raise ValidationError(f"{name} 格式无效。")


def _sha256(payload: Any) -> str:
    return sha256_json(payload)
