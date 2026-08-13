"""Structured manual task preview and creation for the operations Web.

This module deliberately stops at Runtime Task persistence.  It never creates a
ShadowBot request, writes the file queue, or starts a Worker.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from app.enums import (
    PricingSource,
    ProductMappingStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.exceptions import ValidationError
from app.models import Product, Task
from app.repositories.automation_repository import AutomationRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.services.operational_time import OperationalTimeService
from app.services.product_mapping import (
    CompiledProductMappings,
    ProductMappingRecord,
    compile_product_mapping_workbook,
    normalize_mapping_text,
)
from app.utils import utc_now


SET_PRICE = "SET_PRICE"
CHANGE_PRICE = "CHANGE_PRICE"
SET_OFFLINE = "SET_OFFLINE"
SET_ONLINE = "SET_ONLINE"
MANUAL_ACTIONS = frozenset({SET_PRICE, CHANGE_PRICE, SET_OFFLINE, SET_ONLINE})
PRICE_FACT_MAX_AGE = timedelta(minutes=30)
TASK_LIFETIME = timedelta(minutes=30)
MAX_MANUAL_TASK_ITEMS = 50
CONTRACT_VERSION = "task13.5-7e-manual-task-1.0"


class ManualTaskError(ValidationError):
    """A stable, operator-safe manual task validation failure."""


class ManualTaskConflictError(ManualTaskError):
    """The idempotency key or preview no longer matches current facts."""


@dataclass(frozen=True, slots=True)
class ManualTaskRequest:
    varieties: tuple[str, ...]
    grades: tuple[str, ...]
    platforms: tuple[str, ...]
    action: str
    price_value: Decimal | None = None
    target_inventory: int | None = None
    excluded_item_keys: tuple[str, ...] = ()
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class ManualTaskScopeOptions:
    varieties: tuple[str, ...]
    grades: tuple[str, ...]
    platforms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ManualTaskPreviewItem:
    item_key: str
    internal_sku: str
    variety: str
    grade: str
    platform_name: str
    platform_product_name: str
    action_type: TaskActionType
    current_price: Decimal | None
    current_status: str
    real_inventory: int | None
    real_inventory_version: int | None
    base_cost: Decimal
    target_price: Decimal | None
    target_inventory: int | None
    mapping_version: str
    mapping_ids: tuple[str, ...]
    price_fact_version: str
    excluded: bool
    blockers: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.blockers


@dataclass(frozen=True, slots=True)
class ManualTaskPreview:
    request: ManualTaskRequest
    items: tuple[ManualTaskPreviewItem, ...]
    preview_digest: str
    products_sha256: str
    mapping_version: str
    generated_at: datetime
    errors: tuple[str, ...] = ()

    @property
    def included_items(self) -> tuple[ManualTaskPreviewItem, ...]:
        return tuple(item for item in self.items if not item.excluded)

    @property
    def creatable(self) -> bool:
        included = self.included_items
        return bool(included) and not self.errors and all(item.eligible for item in included)


@dataclass(frozen=True, slots=True)
class ManualTaskCreationResult:
    status: str
    task_ids: tuple[str, ...]
    preview_digest: str
    origin_ref_id: str


class ManualTaskApplicationService:
    """Expand a structured scope and atomically persist exact manual Tasks."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        clock=None,
        price_fact_max_age: timedelta = PRICE_FACT_MAX_AGE,
    ) -> None:
        self.runtime = runtime_repository
        self.products_workbook = Path(products_workbook)
        self.platform_mappings_workbook = Path(platform_mappings_workbook)
        self.clock = clock or utc_now
        self.price_fact_max_age = price_fact_max_age
        self.inventory = InventoryRepository(runtime_repository)

    def scope_options(self, *, now: datetime | None = None) -> ManualTaskScopeOptions:
        observed_at = _aware_utc(now or self.clock())
        products, _ = self._load_products_snapshot()
        mappings = self._load_mappings_snapshot()
        platforms = {
            record.platform_name
            for record in mappings.records
            if record.is_effective_at(observed_at)
            and record.mapping_status is not ProductMappingStatus.DISABLED
        }
        return ManualTaskScopeOptions(
            varieties=tuple(sorted({item.product_name for item in products})),
            grades=tuple(sorted({item.grade for item in products})),
            platforms=tuple(sorted(platforms)),
        )

    def preview(
        self,
        request: ManualTaskRequest,
        *,
        now: datetime | None = None,
    ) -> ManualTaskPreview:
        current = _aware_utc(now or self.clock())
        normalized = _normalize_request(request, require_idempotency=False)
        with closing(self.runtime.connect_read()) as connection:
            return self._preview_on_connection(connection, normalized, current)

    def create(
        self,
        request: ManualTaskRequest,
        *,
        expected_preview_digest: str,
        authenticated_subject: str,
        now: datetime | None = None,
    ) -> ManualTaskCreationResult:
        current = _aware_utc(now or self.clock())
        normalized = _normalize_request(request, require_idempotency=True)
        subject = str(authenticated_subject or "").strip()
        if not subject:
            raise ManualTaskError("登录身份不可用，不能创建任务。")
        expected = str(expected_preview_digest or "").strip()
        if not expected:
            raise ManualTaskError("缺少任务预览摘要，请重新预览。")
        request_sha256 = _request_sha256(normalized)
        origin_ref_id = _origin_ref_id(subject, normalized.idempotency_key)

        connection = self.runtime.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM tasks WHERE origin_type = 'MANUAL' AND origin_ref_id = ? "
                "ORDER BY task_id",
                (origin_ref_id,),
            ).fetchall()
            if existing:
                stored_digests = {
                    str(
                        json.loads(str(row["decision_trace_json"] or "{}"))
                        .get("manual_request_sha256")
                        or ""
                    )
                    for row in existing
                }
                if stored_digests != {request_sha256}:
                    raise ManualTaskConflictError(
                        "该幂等键已用于不同的任务请求，请使用新的幂等键。"
                    )
                connection.rollback()
                return ManualTaskCreationResult(
                    status="REPLAYED",
                    task_ids=tuple(str(row["task_id"]) for row in existing),
                    preview_digest=str(
                        json.loads(str(existing[0]["decision_trace_json"] or "{}"))
                        .get("preview_digest")
                        or expected
                    ),
                    origin_ref_id=origin_ref_id,
                )

            preview = self._preview_on_connection(connection, normalized, current)
            if preview.preview_digest != expected:
                raise ManualTaskConflictError(
                    "商品、价格、库存或映射在预览后发生变化，请重新预览。"
                )
            if not preview.creatable:
                raise ManualTaskError(_preview_failure_message(preview))

            time_context = OperationalTimeService(
                policies=AutomationRepository(self.runtime).load_operational_time_policies(
                    connection=connection
                )
            ).classify(current)
            group_id = "MANUAL-GROUP-" + preview.preview_digest.split(":", 1)[-1][:20]
            tasks = [
                self._task_from_item(
                    item,
                    preview=preview,
                    request_sha256=request_sha256,
                    origin_ref_id=origin_ref_id,
                    group_id=group_id,
                    current=current,
                    time_context=time_context,
                )
                for item in preview.included_items
            ]
            SQLiteRuntimeRepository._validate_tasks_for_insert(tasks)
            inserted = SQLiteRuntimeRepository._insert_tasks_on_connection(connection, tasks)
            if inserted != len(tasks):
                raise ManualTaskConflictError(
                    "任务身份与现有开放任务冲突，未创建任何任务。"
                )
            self._verify_workbook_hashes(
                products_sha256=preview.products_sha256,
                mapping_source_sha256=preview.mapping_version.split(":", 1)[0],
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

        return ManualTaskCreationResult(
            status="CREATED",
            task_ids=tuple(task.task_id for task in tasks),
            preview_digest=preview.preview_digest,
            origin_ref_id=origin_ref_id,
        )

    def _preview_on_connection(
        self,
        connection,
        request: ManualTaskRequest,
        current: datetime,
    ) -> ManualTaskPreview:
        products, products_sha256 = self._load_products_snapshot()
        mappings = self._load_mappings_snapshot()
        authority = self.inventory.get_authority_state(connection=connection)
        errors: list[str] = []
        if authority.authority_mode != "DB_AUTHORITY":
            errors.append("数据库真实库存尚未完成权威切换。")

        wanted_varieties = {normalize_mapping_text(value) for value in request.varieties}
        wanted_grades = {normalize_mapping_text(value) for value in request.grades}
        selected_products = [
            product
            for product in products
            if normalize_mapping_text(product.product_name) in wanted_varieties
            and normalize_mapping_text(product.grade) in wanted_grades
            and product.sale_enabled
        ]
        if not selected_products:
            errors.append("所选品种和等级没有可销售商品。")

        open_tasks = connection.execute(
            """
            SELECT internal_sku, platform_name FROM tasks
            WHERE task_status IN ('pending', 'running', 'manual_review')
              AND action_type IN ('update_price', 'set_online', 'set_offline')
            """
        ).fetchall()
        open_identities = {
            (
                str(row["internal_sku"] or "").strip().upper(),
                normalize_mapping_text(row["platform_name"]),
            )
            for row in open_tasks
        }
        exclusions = set(request.excluded_item_keys)
        items: list[ManualTaskPreviewItem] = []
        for platform in request.platforms:
            for product in selected_products:
                item = self._preview_item(
                    connection,
                    request=request,
                    product=product,
                    platform_name=platform,
                    mappings=mappings,
                    authority_mode=authority.authority_mode,
                    open_identities=open_identities,
                    current=current,
                    exclusions=exclusions,
                )
                items.append(item)

        items.sort(key=lambda item: (item.platform_name, item.variety, item.grade, item.internal_sku))
        known_keys = {item.item_key for item in items}
        unknown_exclusions = sorted(exclusions - known_keys)
        if unknown_exclusions:
            errors.append("排除项不属于当前预览，请重新选择任务范围。")
        if len(items) > MAX_MANUAL_TASK_ITEMS:
            errors.append(f"一次最多预览 {MAX_MANUAL_TASK_ITEMS} 个任务项目。")
        if not items and not errors:
            errors.append("当前范围没有可预览项目。")
        if items and all(item.excluded for item in items):
            errors.append("不能排除全部任务项目。")

        digest_payload = {
            "contract_version": CONTRACT_VERSION,
            "request": _request_payload(request),
            "products_sha256": products_sha256,
            "mapping_version": mappings.mapping_version,
            "items": [_item_payload(item) for item in items],
            "errors": errors,
        }
        return ManualTaskPreview(
            request=request,
            items=tuple(items),
            preview_digest=_sha256_json(digest_payload),
            products_sha256=products_sha256,
            mapping_version=(
                mappings.source_workbook_sha256 + ":" + mappings.mapping_version
            ),
            generated_at=current,
            errors=tuple(errors),
        )

    def _preview_item(
        self,
        connection,
        *,
        request: ManualTaskRequest,
        product: Product,
        platform_name: str,
        mappings: CompiledProductMappings,
        authority_mode: str,
        open_identities: set[tuple[str, str]],
        current: datetime,
        exclusions: set[str],
    ) -> ManualTaskPreviewItem:
        blockers: list[str] = []
        mapping_records = _mapping_records_for_product(
            mappings,
            product=product,
            platform_name=platform_name,
            observed_at=current,
        )
        verified = tuple(
            record
            for record in mapping_records
            if record.mapping_status is ProductMappingStatus.VERIFIED
            and str(record.internal_sku or "").upper() == product.internal_sku.upper()
        )
        if len(verified) != 1:
            blockers.append("平台商品映射不是唯一 VERIFIED。")
        mapping_record = verified[0] if len(verified) == 1 else None
        platform_product_name = (
            mapping_record.platform_product_name if mapping_record else product.product_name
        )
        listing = self.runtime.get_listing_status(
            platform_name,
            platform_product_name,
            product.grade,
        )
        if listing is None:
            blockers.append("缺少当前平台商品事实。")
        elif listing.internal_sku and listing.internal_sku.upper() != product.internal_sku.upper():
            blockers.append("平台事实绑定了其他内部商品。")

        balance = self.inventory.get_balance(product.internal_sku, connection=connection)
        if authority_mode == "DB_AUTHORITY" and balance is None:
            blockers.append("数据库真实库存缺少该商品余额。")

        current_price = listing.current_price if listing is not None else None
        current_status = str(listing.online_status or "").strip().lower() if listing else ""
        price_fact_at = (
            listing.price_observed_at if listing is not None else None
        )
        status_fact_at = None
        if listing is not None:
            status_fact_at = (
                listing.online_status_observed_at
                or listing.inventory_observed_at
                or listing.updated_at
            )
        action_type = _task_action_type(request.action)
        target_price = None
        target_inventory = None

        if request.action in {SET_PRICE, CHANGE_PRICE}:
            if current_status != "online":
                blockers.append("改价只允许当前上架中的商品。")
            if not _fact_is_fresh(price_fact_at, current, self.price_fact_max_age):
                blockers.append("当前价格事实缺失或已过期。")
            if current_price is None:
                blockers.append("当前价格不可用。")
            elif request.action == SET_PRICE:
                target_price = request.price_value
            elif request.price_value is not None:
                target_price = current_price + request.price_value
        elif request.action == SET_OFFLINE:
            if current_status != "online":
                blockers.append("下架只允许当前上架中的商品。")
            if not _fact_is_fresh(status_fact_at, current, self.price_fact_max_age):
                blockers.append("当前上下架状态事实缺失或已过期。")
        elif request.action == SET_ONLINE:
            if current_status != "offline":
                blockers.append("上架只允许当前待上架商品。")
            if not _fact_is_fresh(status_fact_at, current, self.price_fact_max_age):
                blockers.append("当前上下架状态事实缺失或已过期。")
            target_price = request.price_value
            target_inventory = request.target_inventory

        if target_price is not None:
            if not target_price.is_finite() or target_price <= 0:
                blockers.append("目标价格必须大于 0。")
            elif target_price < product.base_cost:
                blockers.append("目标价格不能低于商品基础成本。")
        if request.action == SET_ONLINE:
            if target_inventory is None or target_inventory < 0:
                blockers.append("上架必须填写非负平台目标库存。")
            elif balance is not None and target_inventory > balance.current_qty:
                blockers.append("平台目标库存不能超过数据库真实库存。")

        if (
            product.internal_sku.upper(),
            normalize_mapping_text(platform_name),
        ) in open_identities:
            blockers.append("该商品与平台已有开放任务。")

        item_key = _item_key(platform_name, product.internal_sku)
        observed_version = ""
        if listing is not None:
            observed_version = "|".join(
                (
                    str(listing.price_source_attempt_id or ""),
                    str(listing.inventory_source_attempt_id or ""),
                    _datetime_text(price_fact_at),
                    _datetime_text(status_fact_at),
                    _decimal_text(current_price),
                    current_status,
                )
            )
        return ManualTaskPreviewItem(
            item_key=item_key,
            internal_sku=product.internal_sku.upper(),
            variety=product.product_name,
            grade=product.grade,
            platform_name=platform_name,
            platform_product_name=platform_product_name,
            action_type=action_type,
            current_price=current_price,
            current_status=current_status,
            real_inventory=balance.current_qty if balance else None,
            real_inventory_version=balance.version if balance else None,
            base_cost=product.base_cost,
            target_price=target_price,
            target_inventory=target_inventory,
            mapping_version=mappings.mapping_version,
            mapping_ids=tuple(sorted(record.mapping_id for record in mapping_records)),
            price_fact_version=_sha256_text(observed_version) if observed_version else "",
            excluded=item_key in exclusions,
            blockers=tuple(dict.fromkeys(blockers)),
        )

    def _task_from_item(
        self,
        item: ManualTaskPreviewItem,
        *,
        preview: ManualTaskPreview,
        request_sha256: str,
        origin_ref_id: str,
        group_id: str,
        current: datetime,
        time_context,
    ) -> Task:
        task_identity = _sha256_text(
            "|".join((origin_ref_id, item.item_key, item.action_type.value))
        ).split(":", 1)[1]
        target_status = None
        if item.action_type is TaskActionType.SET_ONLINE:
            target_status = "online"
        elif item.action_type is TaskActionType.SET_OFFLINE:
            target_status = "offline"
        trace = {
            "contract_version": CONTRACT_VERSION,
            "manual_request_sha256": request_sha256,
            "preview_digest": preview.preview_digest,
            "task_group_id": group_id,
            "manual_action": preview.request.action,
            "authenticated_subject": origin_ref_id.split(":", 2)[1],
            "item_key": item.item_key,
            "mapping_version": item.mapping_version,
            "mapping_ids": list(item.mapping_ids),
            "price_fact_version": item.price_fact_version,
            "real_inventory_version": item.real_inventory_version,
            "base_cost": _decimal_text(item.base_cost),
        }
        return Task(
            task_id="TASK-MANUAL-" + task_identity[:24],
            internal_sku=item.internal_sku,
            platform_name=item.platform_name,
            action_type=item.action_type,
            priority=(3 if item.action_type is TaskActionType.SET_OFFLINE else 5),
            task_status=TaskStatus.PENDING,
            created_at=current,
            expected_old_price=(
                item.current_price
                if item.action_type is TaskActionType.UPDATE_PRICE
                else None
            ),
            target_price=item.target_price,
            target_inventory=item.target_inventory,
            target_status=target_status,
            pricing_source=(
                PricingSource.MANUAL_OVERRIDE
                if item.target_price is not None
                else None
            ),
            decision_trace=trace,
            required_by=current + TASK_LIFETIME,
            trade_date=time_context.platform_trade_date,
            origin_type=TaskOriginType.MANUAL,
            origin_ref_id=origin_ref_id,
            approval_policy="MANUAL_EXECUTION_AUTHORIZATION_REQUIRED",
            policy_version=CONTRACT_VERSION,
            platform_trade_date=time_context.platform_trade_date,
            seller_operation_date=time_context.seller_operation_date,
            seller_phase=time_context.seller_phase,
            time_policy_version=time_context.time_policy_version,
            scope_type="sku",
            scope_key=item.internal_sku,
            dedupe_key=(
                "manual|"
                + normalize_mapping_text(item.platform_name)
                + "|"
                + item.internal_sku
                + "|"
                + item.action_type.value
                + "|"
                + task_identity[:16]
            ),
            expires_at=current + TASK_LIFETIME,
            updated_at=current,
        )

    def _load_products_snapshot(self) -> tuple[list[Product], str]:
        try:
            before = self.products_workbook.read_bytes()
            products = load_products(self.products_workbook)
            after = self.products_workbook.read_bytes()
        except (OSError, UnicodeError, ValueError) as exc:
            raise ManualTaskError("商品主数据不可用。") from exc
        if before != after:
            raise ManualTaskConflictError("商品主数据读取期间发生变化，请重试。")
        return products, hashlib.sha256(before).hexdigest()

    def _load_mappings_snapshot(self) -> CompiledProductMappings:
        try:
            before = self.platform_mappings_workbook.read_bytes()
            compiled = compile_product_mapping_workbook(
                self.platform_mappings_workbook
            )
            after = self.platform_mappings_workbook.read_bytes()
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise ManualTaskError("平台商品映射不可用。") from exc
        if before != after:
            raise ManualTaskConflictError("平台商品映射读取期间发生变化，请重试。")
        return compiled

    def _verify_workbook_hashes(
        self,
        *,
        products_sha256: str,
        mapping_source_sha256: str,
    ) -> None:
        if hashlib.sha256(self.products_workbook.read_bytes()).hexdigest() != products_sha256:
            raise ManualTaskConflictError("商品主数据在任务创建期间发生变化。")
        if (
            hashlib.sha256(self.platform_mappings_workbook.read_bytes()).hexdigest()
            != mapping_source_sha256
        ):
            raise ManualTaskConflictError("平台商品映射在任务创建期间发生变化。")


def _normalize_request(
    request: ManualTaskRequest,
    *,
    require_idempotency: bool,
) -> ManualTaskRequest:
    action = str(request.action or "").strip().upper()
    if action not in MANUAL_ACTIONS:
        raise ManualTaskError("任务类型无效。")
    varieties = _normalized_values(request.varieties, "品种")
    grades = _normalized_values(request.grades, "等级")
    platforms = _normalized_values(request.platforms, "平台")
    exclusions = tuple(sorted({str(value).strip() for value in request.excluded_item_keys if str(value).strip()}))
    idempotency_key = str(request.idempotency_key or "").strip()
    if require_idempotency and not idempotency_key:
        raise ManualTaskError("缺少幂等键。")
    if len(idempotency_key) > 160:
        raise ManualTaskError("幂等键过长。")

    price_value = request.price_value
    if action in {SET_PRICE, CHANGE_PRICE, SET_ONLINE}:
        try:
            price_value = Decimal(str(price_value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ManualTaskError("该任务类型必须填写有效价格。") from exc
        if not price_value.is_finite():
            raise ManualTaskError("价格必须是有限数值。")
        price_value = price_value.quantize(Decimal("0.01"))
        if action in {SET_PRICE, SET_ONLINE} and price_value <= 0:
            raise ManualTaskError("目标价格必须大于 0。")
        if action == CHANGE_PRICE and price_value == 0:
            raise ManualTaskError("加/降价数值不能为 0。")
    elif price_value is not None:
        raise ManualTaskError("下架任务不能携带价格。")

    target_inventory = request.target_inventory
    if action == SET_ONLINE:
        if isinstance(target_inventory, bool):
            raise ManualTaskError("平台目标库存必须是非负整数。")
        try:
            target_inventory = int(target_inventory)
        except (TypeError, ValueError) as exc:
            raise ManualTaskError("上架任务必须填写平台目标库存。") from exc
        if target_inventory < 0:
            raise ManualTaskError("平台目标库存必须是非负整数。")
    elif target_inventory is not None:
        raise ManualTaskError("只有上架任务可以携带平台目标库存。")

    return ManualTaskRequest(
        varieties=varieties,
        grades=grades,
        platforms=platforms,
        action=action,
        price_value=price_value,
        target_inventory=target_inventory,
        excluded_item_keys=exclusions,
        idempotency_key=idempotency_key,
    )


def _normalized_values(values: Iterable[str], label: str) -> tuple[str, ...]:
    normalized = tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
    if not normalized:
        raise ManualTaskError(f"至少选择一个{label}。")
    if len(normalized) > 50:
        raise ManualTaskError(f"{label}选项过多。")
    return normalized


def _mapping_records_for_product(
    mappings: CompiledProductMappings,
    *,
    product: Product,
    platform_name: str,
    observed_at: datetime,
) -> tuple[ProductMappingRecord, ...]:
    platform_key = normalize_mapping_text(platform_name)
    sku = product.internal_sku.upper()
    return tuple(
        record
        for record in mappings.records
        if normalize_mapping_text(record.platform_name) == platform_key
        and record.is_effective_at(observed_at)
        and sku
        in {
            str(record.internal_sku or "").upper(),
            str(record.candidate_internal_sku or "").upper(),
        }
    )


def _task_action_type(action: str) -> TaskActionType:
    if action in {SET_PRICE, CHANGE_PRICE}:
        return TaskActionType.UPDATE_PRICE
    if action == SET_ONLINE:
        return TaskActionType.SET_ONLINE
    return TaskActionType.SET_OFFLINE


def _request_payload(request: ManualTaskRequest) -> dict[str, object]:
    return {
        "varieties": list(request.varieties),
        "grades": list(request.grades),
        "platforms": list(request.platforms),
        "action": request.action,
        "price_value": _decimal_text(request.price_value),
        "target_inventory": request.target_inventory,
        "excluded_item_keys": list(request.excluded_item_keys),
    }


def _request_sha256(request: ManualTaskRequest) -> str:
    return _sha256_json(_request_payload(request))


def _item_payload(item: ManualTaskPreviewItem) -> dict[str, object]:
    return {
        "item_key": item.item_key,
        "internal_sku": item.internal_sku,
        "variety": item.variety,
        "grade": item.grade,
        "platform_name": item.platform_name,
        "platform_product_name": item.platform_product_name,
        "action_type": item.action_type.value,
        "current_price": _decimal_text(item.current_price),
        "current_status": item.current_status,
        "real_inventory": item.real_inventory,
        "real_inventory_version": item.real_inventory_version,
        "base_cost": _decimal_text(item.base_cost),
        "target_price": _decimal_text(item.target_price),
        "target_inventory": item.target_inventory,
        "mapping_version": item.mapping_version,
        "mapping_ids": list(item.mapping_ids),
        "price_fact_version": item.price_fact_version,
        "excluded": item.excluded,
        "blockers": list(item.blockers),
    }


def _origin_ref_id(subject: str, idempotency_key: str) -> str:
    subject_hash = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
    return f"web-manual:{subject_hash}:{key_hash}"


def _item_key(platform_name: str, internal_sku: str) -> str:
    return _sha256_text(normalize_mapping_text(platform_name) + "|" + internal_sku.upper())


def _fact_is_fresh(
    observed_at: datetime | None,
    now: datetime,
    max_age: timedelta,
) -> bool:
    if observed_at is None:
        return False
    observed = _aware_utc(observed_at)
    age = now - observed
    return timedelta(0) <= age <= max_age


def _preview_failure_message(preview: ManualTaskPreview) -> str:
    if preview.errors:
        return "；".join(preview.errors)
    blocked = [
        f"{item.platform_name}/{item.variety}/{item.grade}：{'、'.join(item.blockers)}"
        for item in preview.included_items
        if item.blockers
    ]
    return "；".join(blocked[:5]) or "当前预览没有可创建项目。"


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{Decimal(value):.2f}"


def _datetime_text(value: datetime | None) -> str:
    return _aware_utc(value).isoformat() if value is not None else ""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
