"""Task 13.5-7C 运营事实查询。

本模块只组合既有权威 Repository，不创建 Schema、不修复数据，也不
调用 Queue、Worker、Importer 或平台 Adapter。
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlencode

from app.enums import (
    DataQualityLevel,
    ReviewTaskStatus,
    SellerPhase,
    TaskActionType,
    TaskStatus,
)
from app.models import Product, ReviewTask, Task
from app.operations_web.composition import OperationsWebPaths
from app.operations_web.read_models import (
    AutomationControlReadModel,
    ComponentReadModel,
    DatabaseReadModel,
    DetailFieldReadModel,
    DetailReadModel,
    InventoryAlertControlReadModel,
    ManagementReadModel,
    MetricReadModel,
    MobileOperationConfirmationReadModel,
    MobileReviewReadModel,
    NotificationDrawerReadModel,
    NotificationItemReadModel,
    OperationResolutionControlReadModel,
    ReadState,
    ReviewActionReadModel,
    ReviewControlReadModel,
    ProductMappingControlReadModel,
    ProductMasterControlReadModel,
    StateReadModel,
    SystemReadModel,
    TableReadModel,
    TaskQueueReadModel,
    TodayReadModel,
    VarietyInventoryControlReadModel,
)
from app.repositories.automation_repository import AutomationRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.master_data_repository import RuntimeMasterDataRepository
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.review_display import (
    REVIEW_TYPE_DISPLAY_LABELS,
    review_reason_display,
    review_scope_display,
)
from app.review_policy import (
    allowed_review_statuses,
    is_execution_failure_review,
    review_action_label,
)
from app.services.automation import (
    DAILY_TASK_GENERATION,
    FULL_MARKET_SCAN,
    ONLINE_PULSE,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    REVIEW_TIMEOUT_MAINTENANCE,
    SALES_PLAN_INPUT_BUILD,
)
from app.services.automation_configuration import CONFIGURABLE_JOB_TYPES
from app.services.authoritative_inventory import summarize_inventory_by_variety
from app.services.operational_time import OperationalTimeContext, OperationalTimeService
from app.services.notification_outbox import (
    NOTIFICATION_TYPE_TITLES,
)
from app.services.operations_automation import validate_rule_workbooks
from app.services.runtime import ReviewTokenService
from app.services.shadowbot_worker_health import (
    build_shadowbot_worker_health_report,
)


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 25
LOGGER = logging.getLogger("app.operations_web.queries")

QUALITY_LABELS = {
    "ORDER_COMPLETE": "订单数据完整",
    "ORDER_PARTIAL": "订单数据不完整",
    "SCAN_ESTIMATED_HIGH": "库存变化估算（高可信）",
    "SCAN_ESTIMATED_MEDIUM": "库存变化估算（中等可信）",
    "SCAN_ESTIMATED_LOW": "库存变化估算（低可信）",
    "UNAVAILABLE": "不可用",
}
SOURCE_LABELS = {
    "ORDER_OBSERVED": "订单记录",
    "SCAN_ESTIMATED": "库存变化估算",
}
SUMMARY_STATUS_LABELS = {
    "PROVISIONAL": "初步结算",
    "OBSERVED": "已记录",
    "RECONCILED": "已对账",
    "FINAL": "已确认结算",
}
TASK_STATUS_LABELS = {
    "pending": "待处理",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
    "skipped": "已跳过",
    "manual_review": "需人工复核",
    "cancelled": "已取消",
    "expired": "已过期",
}
ACTION_LABELS = {
    "update_price": "调整价格",
    "set_online": "上架",
    "set_offline": "下架",
    "sync_status": "同步状态",
}
REVIEW_STATUS_LABELS = {
    "pending": "待复核",
    "approved": "已批准",
    "rejected": "人工处理",
    "adjusted": "已调整",
    "expired": "已过期",
    "cancelled": "已取消",
}
PHASE_LABELS = {
    SellerPhase.NORMAL_SALES: "正常销售时段",
    SellerPhase.PEAK_SALES: "销售高峰时段",
    SellerPhase.DELIVERY_OVERLAP: "截单后交接时段",
}
AUTOMATION_JOB_LABELS = {
    "ONLINE_PULSE": "上架商品快速扫描",
    "FULL_MARKET_SCAN": "完整市场扫描",
    "PRE_CUTOFF_FULL_SCAN": "截单前完整扫描",
    "POST_CUTOFF_PULSE": "截单后状态扫描",
    "PLATFORM_TRADE_DAY_SETTLEMENT": "交易日结算",
    "SALES_PLAN_INPUT_BUILD": "销售计划数据整理",
    "REVIEW_TIMEOUT_MAINTENANCE": "人工复核超时维护",
    "DAILY_TASK_GENERATION": "每日任务生成",
    "LISTING_STATUS_SCAN": "商品状态扫描",
    "ORDER_SCAN": "订单扫描",
    "INCIDENT_NOTIFICATION_MAINTENANCE": "异常通知维护",
}

BUSINESS_DATASETS = (
    ("sales", "销售与订单"),
    ("products", "商品与库存"),
    ("prices", "平台价格"),
    ("settlements", "交易日结算"),
    ("varieties", "品种销售结构"),
    ("inventory-adjustments", "库存调整流水"),
    ("mappings", "商品映射"),
    ("history", "历史经营快照"),
)
PROJECT_DATASETS = (
    ("tasks", "任务"),
    ("reviews", "复核"),
    ("runs", "自动化运行"),
    ("incidents", "异常"),
    ("executions", "执行记录"),
    ("notifications", "通知"),
)
ANALYSIS_DATASETS = (
    ("overview", "销售总览"),
    ("variety", "按品种"),
    ("grade", "按等级"),
    ("time", "按销售时段"),
)


@dataclass(frozen=True, slots=True)
class _TimeContextReadResult:
    context: OperationalTimeContext | None
    state: StateReadModel


@dataclass(frozen=True, slots=True)
class _TaskQueueEntry:
    source_key: str
    source_label: str
    stage_key: str
    stage_label: str
    title: str
    scope: str
    platform_name: str
    detail: str
    occurred_at: datetime
    url: str = ""
    dispatch_lane: int = 2
    task_id: str = ""
    cancellable: bool = False
    operation_resolution: OperationResolutionControlReadModel | None = None


class OperationsQueryService:
    """只读 Composition Service；所有页面共享同一固定依赖集合。"""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        paths: OperationsWebPaths,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime = runtime_repository
        self.paths = paths
        self.automation = AutomationRepository(runtime_repository)
        self.incidents = OperationalIncidentRepository(runtime_repository)
        self.summaries = OperationalSummaryRepository(runtime_repository)
        self.inventory = InventoryRepository(runtime_repository)
        self.master_data = RuntimeMasterDataRepository(runtime_repository)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def notification_drawer(self) -> NotificationDrawerReadModel:
        items: list[NotificationItemReadModel] = []
        review_count = 0
        incident_count = 0
        system_impact = 0
        try:
            review_count = self.runtime.count_review_tasks(
                status=ReviewTaskStatus.PENDING
            )
            reviews = self.runtime.list_review_tasks(
                status=ReviewTaskStatus.PENDING,
                limit=3,
            )
            items.extend(self._review_notification(item) for item in reviews)
        except Exception:
            system_impact += 1
        try:
            incident_count = self.incidents.count_active()
            active = self.incidents.list_active(limit=3)
            items.extend(
                NotificationItemReadModel(
                    title=item.title or "运营异常待处理",
                    detail=_incident_detail(item.severity, item.occurrence_count),
                    severity=item.severity,
                    url="/database/project?dataset=incidents",
                )
                for item in active
            )
        except Exception:
            system_impact += 1
        try:
            schema = self.runtime.check_schema_health()
            operational = self.runtime.check_operational_health()
            if not schema.ok or not operational.ok:
                system_impact += 1
                items.append(
                    NotificationItemReadModel(
                        title="运行数据需要维护",
                        detail="业务数据暂时无法正常读取，请联系管理员处理。",
                        severity="S2",
                        url="/system",
                    )
                )
        except Exception:
            system_impact += 1
        return NotificationDrawerReadModel(
            total=review_count + incident_count + system_impact,
            items=tuple(items[:6]),
        )

    def today(self) -> TodayReadModel:
        now = self._now()
        time_result = self._time_context(now)
        products, products_error = self._load_products()
        inventory_value = (
            sum(item.current_stock for item in products if item.sale_enabled)
            if products
            else None
        )
        inventory_state = (
            ReadState.FAILED
            if products_error
            else (ReadState.READY if products else ReadState.UNAVAILABLE)
        )
        drawer = self.notification_drawer()
        if time_result.context is None:
            unavailable_metrics = tuple(
                MetricReadModel(
                    label,
                    "—",
                    time_result.state.detail,
                    time_result.state.state,
                )
                for label in ("今日已售", "成交金额", "成交均价")
            )
            return TodayReadModel(
                platform_trade_date="不可用",
                observed_at=_datetime(now),
                trade_day_status="UNAVAILABLE",
                phase_label="销售时段不可用",
                state=time_result.state,
                metrics=unavailable_metrics
                + (
                    MetricReadModel(
                        "当前可售库存",
                        _qty(inventory_value),
                        "仅统计当前允许销售的商品",
                        inventory_state,
                    ),
                ),
                products=_state_table(
                    "today-products",
                    "品种销售与库存",
                    time_result.state,
                ),
                platform_limits=_state_table(
                    "today-platform-limits",
                    "平台可购上限",
                    time_result.state,
                ),
                todo_items=drawer.items,
                timeline=(),
            )

        context = time_result.context
        platform_summaries = ()
        summary_error = ""
        if self._schema_is_ready():
            try:
                platform_summaries = self.summaries.list_summaries_page(
                    platform_trade_date=context.platform_trade_date,
                    scope_type="PLATFORM",
                    current_only=True,
                    limit=101,
                )
            except Exception as exc:
                summary_error = type(exc).__name__
        else:
            summary_error = "RUNTIME_SCHEMA_UNAVAILABLE"

        sales_state = self._summary_state(platform_summaries, summary_error)
        valid_sold = [item.sold_qty for item in platform_summaries if item.sold_qty is not None]
        valid_amount = [
            item.transaction_amount_total
            for item in platform_summaries
            if item.transaction_amount_total is not None
        ]
        sold_qty = sum(valid_sold) if valid_sold else None
        amount = sum(valid_amount, Decimal("0")) if valid_amount else None
        average = (
            amount / Decimal(sold_qty)
            if amount is not None and sold_qty not in (None, 0)
            else (Decimal("0") if amount == 0 and sold_qty == 0 else None)
        )
        source_note = _joined_labels(
            SOURCE_LABELS.get(item.fact_source.value, "来源未知")
            for item in platform_summaries
            if item.fact_source is not None
        )
        metrics = (
            MetricReadModel("今日已售", _qty(sold_qty), source_note, sales_state.state),
            MetricReadModel("成交金额", _money(amount), source_note, sales_state.state),
            MetricReadModel("成交均价", _money(average), "按成交金额 ÷ 销量", sales_state.state),
            MetricReadModel(
                "当前可售库存",
                _qty(inventory_value),
                "仅统计当前允许销售的商品",
                inventory_state,
            ),
        )
        product_table = self._today_product_table(
            context.platform_trade_date,
            products,
            products_error,
        )
        platform_limits = self._today_platform_limit_table(products_error)
        timeline = self._today_timeline(context.platform_trade_date)
        observed = max(
            (item.updated_at for item in platform_summaries),
            default=now,
        )
        return TodayReadModel(
            platform_trade_date=context.platform_trade_date.isoformat(),
            observed_at=_datetime(observed),
            trade_day_status="OPEN",
            phase_label=PHASE_LABELS.get(context.seller_phase, "销售时段待确认"),
            state=sales_state,
            metrics=metrics,
            products=product_table,
            platform_limits=platform_limits,
            todo_items=drawer.items,
            timeline=timeline,
        )

    def database(
        self,
        *,
        section: str,
        dataset: str,
        page: int,
        trade_date: date | None,
        platform_name: str,
    ) -> DatabaseReadModel:
        time_result = self._time_context(self._now())
        selected_date = trade_date or (
            time_result.context.platform_trade_date
            if time_result.context is not None
            else None
        )
        normalized_page = max(1, int(page))
        if section == "project":
            options = PROJECT_DATASETS
            selected = dataset if dataset in dict(options) else "tasks"
            table = self._project_table(selected, normalized_page, selected_date)
            title = "项目运行数据"
            route = "/database/project"
        elif section == "sales-analysis":
            options = ANALYSIS_DATASETS
            selected = dataset if dataset in dict(options) else "overview"
            table = (
                self._analysis_table(
                    selected,
                    normalized_page,
                    selected_date,
                    platform_name,
                )
                if selected_date is not None
                else _state_table(selected, dict(options)[selected], time_result.state)
            )
            title = "销售分析"
            route = "/database/sales-analysis"
        elif section == "dictionary":
            options = (("fields", "字段说明"),)
            selected = "fields"
            table = self._dictionary_table(normalized_page)
            title = "字段说明"
            route = "/database/dictionary"
        elif section == "quality":
            options = (("freshness", "质量与新鲜度"),)
            selected = "freshness"
            table = (
                self._quality_table(
                    normalized_page,
                    selected_date,
                    platform_name,
                )
                if selected_date is not None
                else _state_table("freshness", "质量与新鲜度", time_result.state)
            )
            title = "质量与新鲜度"
            route = "/database/quality"
        else:
            options = BUSINESS_DATASETS
            selected = dataset if dataset in dict(options) else "sales"
            table = (
                self._business_table(
                    selected,
                    normalized_page,
                    selected_date,
                    platform_name,
                )
                if selected_date is not None
                or selected not in {"sales", "settlements", "varieties"}
                else _state_table(selected, dict(options)[selected], time_result.state)
            )
            title = "业务数据"
            route = "/database"
        option_models = tuple(
            (
                key,
                label,
                route
                + "?"
                + urlencode({
                    "dataset": key,
                    **(
                        {"trade_date": selected_date.isoformat()}
                        if selected_date is not None
                        else {}
                    ),
                    "platform": platform_name,
                }),
            )
            for key, label in options
        )
        platform_options = tuple(
            dict.fromkeys(
                ([platform_name] if platform_name else [])
                + list(self._platform_options(selected_date))
            )
        )
        notice = (
            "销售分析功能正在完善；当前展示已经确认的销售汇总。"
            if section == "sales-analysis"
            else ""
        )
        if selected_date is None:
            time_notice = f"{time_result.state.title}：{time_result.state.detail}"
            notice = f"{notice} {time_notice}".strip()
        return DatabaseReadModel(
            section=section,
            section_title=title,
            dataset_options=option_models,
            selected_dataset=selected,
            trade_date=selected_date.isoformat() if selected_date is not None else "",
            platform_name=platform_name,
            platform_options=platform_options,
            filter_action=route,
            show_business_filters=section in {"business", "sales-analysis", "quality"},
            table=table,
            notice=notice,
        )

    def management(
        self,
        *,
        inventory_transaction_id: str = "",
        inventory_error_code: str = "",
    ) -> ManagementReadModel:
        pending_tasks = self._task_table(page=1, pending_only=True, page_size=6)
        pending_reviews = self._review_table(page=1, pending_only=True, page_size=6)
        runs = self._run_table(page=1, page_size=6)
        inventory_options: tuple[tuple[str, str, int, int], ...] = ()
        inventory_variety_summaries: tuple[VarietyInventoryControlReadModel, ...] = ()
        inventory_receipt = None
        inventory_error = _inventory_error_state(inventory_error_code)
        try:
            pending_task_options = tuple(
                (
                    item.task_id,
                    " · ".join(
                        filter(
                            None,
                            (
                                _task_action_label(item.action_type.value),
                                item.internal_sku or "",
                                item.platform_name or "",
                            ),
                        )
                    ),
                )
                for item in self.runtime.list_tasks(
                    status=TaskStatus.PENDING,
                    limit=50,
                )
                if item.action_type
                in {
                    TaskActionType.UPDATE_PRICE,
                    TaskActionType.SET_ONLINE,
                    TaskActionType.SET_OFFLINE,
                }
            )
        except Exception:
            pending_task_options = ()
        try:
            pending_review_options = []
            for review in self.runtime.list_review_tasks(
                status=ReviewTaskStatus.PENDING,
                limit=50,
            ):
                source_task = (
                    self.runtime.get_task(review.source_task_id)
                    if review.source_task_id
                    else None
                )
                actions = []
                for status in allowed_review_statuses(review, source_task):
                    label = review_action_label(review, source_task, status)
                    if label:
                        actions.append(
                            ReviewActionReadModel(
                                value=status.value,
                                label=label,
                                requires_target_price=(
                                    status is ReviewTaskStatus.ADJUSTED
                                ),
                            )
                        )
                if actions:
                    pending_review_options.append(
                        ReviewControlReadModel(
                            review_task_id=review.review_task_id,
                            title=_review_type_label(review.review_type),
                            scope=self._review_scope(review),
                            reason=self._review_reason(review),
                            actions=tuple(actions),
                        )
                    )
            pending_review_options = tuple(pending_review_options)
        except Exception:
            pending_review_options = ()
        try:
            selected_jobs = {}
            for job in self.automation.list_jobs():
                if job.job_type not in CONFIGURABLE_JOB_TYPES:
                    continue
                previous = selected_jobs.get(job.job_type)
                job_key = (
                    int(job.enabled),
                    job.updated_at or datetime.min.replace(tzinfo=timezone.utc),
                    job.job_id,
                )
                previous_key = (
                    (
                        int(previous.enabled),
                        previous.updated_at
                        or datetime.min.replace(tzinfo=timezone.utc),
                        previous.job_id,
                    )
                    if previous is not None
                    else None
                )
                if previous_key is None or job_key > previous_key:
                    selected_jobs[job.job_type] = job
            automation_options = []
            for job in sorted(
                selected_jobs.values(),
                key=lambda item: (item.priority, item.job_type),
            ):
                interval = None
                if job.job_type in {
                    ONLINE_PULSE,
                    FULL_MARKET_SCAN,
                    REVIEW_TIMEOUT_MAINTENANCE,
                }:
                    try:
                        interval = int(job.schedule_expression)
                    except ValueError:
                        interval = None
                offset = (
                    int(job.config.get("settlement_offset_minutes") or 5)
                    if job.job_type == SALES_PLAN_INPUT_BUILD
                    else (
                        int(job.config.get("plan_input_offset_minutes") or 5)
                        if job.job_type == DAILY_TASK_GENERATION
                        else None
                    )
                )
                automation_options.append(
                    AutomationControlReadModel(
                        job_id=job.job_id,
                        job_type=job.job_type,
                        title=_automation_job_label(job.job_type),
                        enabled=job.enabled,
                        schedule=job.schedule_expression,
                        interval_minutes=interval,
                        offset_minutes=offset,
                        can_edit_interval=job.job_type
                        in {
                            ONLINE_PULSE,
                            FULL_MARKET_SCAN,
                            REVIEW_TIMEOUT_MAINTENANCE,
                        },
                        can_edit_offset=job.job_type
                        in {SALES_PLAN_INPUT_BUILD, DAILY_TASK_GENERATION},
                        can_rerun=job.job_type
                        in {
                            PLATFORM_TRADE_DAY_SETTLEMENT,
                            SALES_PLAN_INPUT_BUILD,
                        },
                        enabled_sources=tuple(
                            str(value)
                            for value in job.config.get("source_allowlist", ())
                            if str(value) != "PRODUCTS"
                        ),
                    )
                )
            automation_options = tuple(automation_options)
        except Exception:
            LOGGER.exception("读取自动化配置失败")
            automation_options = ()
        try:
            inventory_alert_options = tuple(
                InventoryAlertControlReadModel(
                    scope_type=item.scope_type,
                    scope_key=item.scope_key,
                    enabled=item.enabled,
                    threshold_qty=item.threshold_qty,
                    repeat_interval_minutes=item.repeat_interval_minutes,
                    version=item.version,
                )
                for item in self.inventory.list_alert_policies()
                if item.scope_type == "DEFAULT"
            )
        except Exception:
            inventory_alert_options = ()
        try:
            product_master_options = tuple(
                ProductMasterControlReadModel(
                    internal_sku=item.product.internal_sku,
                    product_name=item.product.product_name,
                    grade=item.product.grade,
                    stem_length=item.product.stem_length,
                    unit=item.product.unit,
                    base_cost=format(item.product.base_cost, "f"),
                    sale_enabled=item.product.sale_enabled,
                    remark=item.product.remark,
                    current_stock=item.product.current_stock,
                    version=item.version,
                )
                for item in self.master_data.list_product_records()
            )
            product_mapping_options = tuple(
                ProductMappingControlReadModel(
                    mapping_id=item.mapping_id,
                    platform_name=item.platform_name,
                    platform_product_name=item.platform_product_name,
                    grade=item.grade,
                    internal_sku=item.internal_sku or "",
                    search_keyword=item.search_keyword,
                    mapping_status=item.mapping_status,
                    remark=item.remark,
                    version=item.version,
                )
                for item in self.master_data.list_mapping_records()
                if item.mapping_kind == "PRODUCT"
            )
        except Exception:
            product_master_options = ()
            product_mapping_options = ()
        try:
            authority = self.inventory.get_authority_state()
            if authority.authority_mode != "DB_AUTHORITY":
                inventory_state = StateReadModel(
                    ReadState.UNAVAILABLE,
                    "库存调整暂不可用",
                    "请联系管理员完成数据库库存初始化。",
                )
            else:
                products, product_error = self._load_products()
                if product_error:
                    raise RuntimeError(product_error)
                product_by_sku = {item.internal_sku: item for item in products}
                balances = self.inventory.list_balances()
                inventory_options = tuple(
                    (
                        item.internal_sku,
                        _inventory_product_label(product_by_sku.get(item.internal_sku)),
                        item.current_qty,
                        item.version,
                    )
                    for item in balances
                    if item.internal_sku in product_by_sku
                )
                inventory_state = StateReadModel(
                    ReadState.READY,
                    "数据库库存已启用",
                    "库存按品种汇总、按等级记录，剩余数量自动延续。",
                )
                policy = self.inventory.get_default_alert_policy()
                threshold = policy.threshold_qty if policy is not None else 0
                enabled = bool(policy is not None and policy.enabled)
                inventory_variety_summaries = tuple(
                    VarietyInventoryControlReadModel(
                        variety=item.variety,
                        current_qty=item.current_qty,
                        grade_summary=_grade_inventory_summary(item.grade_quantities),
                        safety_margin_qty=threshold,
                        margin_gap=item.current_qty - threshold,
                        alert_enabled=enabled,
                    )
                    for item in summarize_inventory_by_variety(products)
                )
                if inventory_transaction_id:
                    transaction = self.inventory.get_transaction(
                        inventory_transaction_id
                    )
                    if transaction is not None:
                        inventory_receipt = (
                            transaction.internal_sku,
                            _qty(transaction.inventory_before),
                            _signed_qty(transaction.inventory_delta),
                            _qty(transaction.inventory_after),
                        )
        except Exception:
            inventory_state = StateReadModel(
                ReadState.FAILED,
                "库存服务暂不可用",
                "本次没有修改库存，请联系管理员检查库存数据。",
            )
        return ManagementReadModel(
            pending_tasks=pending_tasks,
            pending_reviews=pending_reviews,
            automation_runs=runs,
            inventory_state=inventory_state,
            inventory_options=inventory_options,
            inventory_receipt=inventory_receipt,
            inventory_error=inventory_error,
            inventory_idempotency_key=(
                "web-inventory:" + secrets.token_urlsafe(18)
            ),
            pending_task_options=pending_task_options,
            pending_review_options=pending_review_options,
            automation_options=automation_options,
            inventory_alert_options=inventory_alert_options,
            inventory_variety_summaries=inventory_variety_summaries,
            product_master_options=product_master_options,
            product_mapping_options=product_mapping_options,
            task_idempotency_key="web-task:" + secrets.token_urlsafe(18),
            execution_idempotency_key=(
                "web-execution:" + secrets.token_urlsafe(18)
            ),
            automation_rerun_idempotency_key=(
                "web-automation-rerun:" + secrets.token_urlsafe(18)
            ),
            master_data_idempotency_key=(
                "web-master-data:" + secrets.token_urlsafe(18)
            ),
        )

    def task_queue(
        self,
        *,
        source: str = "all",
        stage: str = "all",
        page: int = 1,
    ) -> TaskQueueReadModel:
        """Combine durable tasks with the active file queue without mutating either."""

        selected_source = source if source in {"all", "manual", "automation", "emergency"} else "all"
        selected_stage = stage if stage in {"all", "pending", "queued", "running", "results", "attention"} else "all"
        current_page = max(1, page)
        now = self._now()
        entries: list[_TaskQueueEntry] = []
        tasks_by_id: dict[str, Task] = {}
        task_read_failed = False
        task_results_truncated = False
        products, _ = self._load_products()
        product_labels = {
            item.internal_sku: _inventory_product_label(item) for item in products
        }

        for status in (
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.MANUAL_REVIEW,
            TaskStatus.FAILED,
        ):
            try:
                values = (
                    self.runtime.list_tasks(status=status, limit=501)
                    if status in {TaskStatus.PENDING, TaskStatus.RUNNING}
                    else self.runtime.list_task_history_page(status=status, limit=501)
                )
            except Exception:
                LOGGER.exception("读取当前任务队列失败")
                task_read_failed = True
                continue
            if len(values) > 500:
                task_results_truncated = True
            for item in values[:500]:
                tasks_by_id[item.task_id] = item

        queue_entries, represented_task_ids, queue_read_errors = _active_queue_entries(
            self.paths.queue_root,
            now=now,
            get_task=lambda task_id: tasks_by_id.get(task_id)
            or self.runtime.get_task(task_id),
            product_labels=product_labels,
        )
        entries.extend(queue_entries)
        unresolved_entries, unresolved_task_ids, unresolved_read_failed = (
            self._unresolved_operation_queue_entries(
                product_labels=product_labels,
                excluded_task_ids=represented_task_ids,
            )
        )
        entries.extend(unresolved_entries)
        represented_task_ids.update(unresolved_task_ids)
        queue_read_errors += int(unresolved_read_failed)
        for task_id, item in tasks_by_id.items():
            if task_id in represented_task_ids:
                continue
            entries.append(_task_queue_entry(item, product_labels=product_labels))

        entries.sort(key=_task_queue_sort_key)
        filtered = [
            item
            for item in entries
            if (selected_source == "all" or item.source_key == selected_source)
            and (selected_stage == "all" or item.stage_key == selected_stage)
        ]
        start = (current_page - 1) * DEFAULT_PAGE_SIZE
        visible, has_next = _visible(filtered[start : start + DEFAULT_PAGE_SIZE + 1], DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                item.title,
                item.scope,
                item.source_label,
                item.platform_name or "—",
                item.stage_label,
                _queue_wait_label(now, item.occurred_at),
                item.detail,
            )
            for item in visible
        )
        row_urls = tuple(item.url for item in visible)
        cancellable_task_ids = tuple(
            item.task_id if item.cancellable else "" for item in visible
        )
        operation_resolution_options = tuple(
            item.operation_resolution for item in visible
        )
        stage_counts = {
            key: sum(item.stage_key == key for item in entries)
            for key in ("pending", "queued", "running", "results", "attention")
        }
        metrics = (
            MetricReadModel("待发送", f"{stage_counts['pending']} 项", "已经创建，尚未进入执行队列"),
            MetricReadModel("排队中", f"{stage_counts['queued']} 项", "等待影刀执行端领取"),
            MetricReadModel("执行中", f"{stage_counts['running']} 项", "影刀正在处理"),
            MetricReadModel("等待回收", f"{stage_counts['results']} 项", "执行结果等待写回数据库"),
            MetricReadModel(
                "需要处理",
                f"{stage_counts['attention']} 项",
                "失败或需要人工确认",
                ReadState.INCOMPLETE if stage_counts["attention"] else ReadState.READY,
            ),
        )
        if task_read_failed:
            overall = StateReadModel(
                ReadState.FAILED,
                "任务队列读取失败",
                "部分业务任务暂时无法显示，请联系管理员检查业务数据库。",
            )
        elif queue_read_errors or task_results_truncated:
            overall = StateReadModel(
                ReadState.INCOMPLETE,
                "部分队列状态需要检查",
                "当前列表仍可查看，但可能有少量任务没有完整显示。",
            )
        elif stage_counts["attention"]:
            overall = StateReadModel(
                ReadState.INCOMPLETE,
                "有任务需要处理",
                f"当前有 {stage_counts['attention']} 项失败或需要人工确认的任务。",
            )
        elif entries:
            overall = StateReadModel(
                ReadState.READY,
                "任务队列运行正常",
                "当前任务的发送、执行和结果回收状态均可查看。",
            )
        else:
            overall = StateReadModel(
                ReadState.TRUSTWORTHY_ZERO,
                "当前没有任务排队",
                "没有等待发送、正在执行或等待回收的任务。",
            )
        checked_at = _datetime(now)
        components = (
            ComponentReadModel("任务传递", self._queue_state(), checked_at),
            ComponentReadModel("影刀执行端", self._worker_state(now), checked_at),
            ComponentReadModel("执行结果回收", self._importer_state(now), checked_at),
        )
        table_state = (
            StateReadModel(ReadState.READY, "当前队列", f"当前显示 {len(rows)} 项任务")
            if rows
            else StateReadModel(ReadState.EMPTY, "没有符合条件的任务", "可以调整筛选条件查看其他任务。")
        )
        table = self._table(
            dataset="task-queue",
            title="任务队列",
            columns=("任务", "商品或范围", "来源", "平台", "当前阶段", "已等待", "说明"),
            rows=rows,
            row_urls=row_urls,
            state=table_state,
            page=current_page,
            has_next=has_next,
            base_path="/management/queue",
            query={"source": selected_source, "stage": selected_stage},
        )
        return TaskQueueReadModel(
            overall=overall,
            metrics=metrics,
            components=components,
            table=table,
            cancellable_task_ids=cancellable_task_ids,
            operation_resolution_options=operation_resolution_options,
            selected_source=selected_source,
            selected_stage=selected_stage,
        )

    def _unresolved_operation_queue_entries(
        self,
        *,
        product_labels: dict[str, str],
        excluded_task_ids: set[str],
    ) -> tuple[list[_TaskQueueEntry], set[str], bool]:
        """Expose durable write-lock owners even if their Task was later cancelled."""

        try:
            with self.runtime.connect_read() as connection:
                rows = connection.execute(
                    """
                    SELECT lock.status AS lock_status,
                           lock.updated_at AS lock_updated_at,
                           operation.operation_id,
                           operation.created_at AS operation_created_at,
                           operation.task_id,
                           operation.platform,
                           operation.product_identity_json,
                           operation.action_type
                    FROM shadowbot_write_locks AS lock
                    JOIN shadowbot_operations AS operation
                      ON operation.operation_id = lock.operation_id
                    WHERE lock.status IN ('ACTIVE', 'UNKNOWN', 'REVIEW_BLOCKED')
                    ORDER BY lock.updated_at DESC, operation.operation_id DESC
                    """
                ).fetchall()
        except Exception:
            LOGGER.exception("读取未完成平台操作失败")
            return [], set(), True

        entries: list[_TaskQueueEntry] = []
        represented: set[str] = set()
        for row in rows:
            task_id = str(row["task_id"] or "").strip()
            if task_id and task_id in excluded_task_ids:
                continue
            identity = _json_object(row["product_identity_json"])
            sku = str(identity.get("internal_sku") or "").strip().upper()
            task = self.runtime.get_task(task_id) if task_id else None
            if task is not None:
                source_key, source_label, dispatch_lane = _task_queue_source(task)
            else:
                source_key, source_label, dispatch_lane = "manual", "人工任务", 1
            lock_status = str(row["lock_status"] or "").strip().upper()
            action_type = str(row["action_type"] or "").strip()
            action_label = ACTION_LABELS.get(action_type, "平台操作")
            operation_at = (
                _queue_datetime(row["operation_created_at"])
                or _queue_datetime(row["lock_updated_at"])
                or self._now()
            )
            operation_time = _queue_operation_time_label(operation_at)
            if lock_status == "ACTIVE":
                stage_key = "running"
                stage_label = "执行中"
                detail = (
                    f"{operation_time} 的{action_label}操作仍在执行，"
                    "当前阻塞该商品的新任务。"
                )
            elif lock_status == "REVIEW_BLOCKED":
                stage_key = "attention"
                stage_label = "未完成"
                detail = (
                    f"{operation_time} 的{action_label}操作等待人工确认平台状态，"
                    "当前阻塞该商品的新任务。"
                )
            else:
                stage_key = "attention"
                stage_label = "未完成"
                detail = (
                    f"{operation_time} 的{action_label}操作尚未确认结果，"
                    "当前阻塞该商品的新任务。"
                )
            scope = product_labels.get(sku, sku or "相关商品")
            entries.append(
                _TaskQueueEntry(
                    source_key=source_key,
                    source_label=source_label,
                    stage_key=stage_key,
                    stage_label=stage_label,
                    title=action_label,
                    scope=scope,
                    platform_name=str(row["platform"] or ""),
                    detail=detail,
                    occurred_at=_queue_datetime(row["lock_updated_at"]) or self._now(),
                    url=(
                        f"/management/task/{quote(task_id, safe='')}"
                        if task_id
                        else ""
                    ),
                    dispatch_lane=dispatch_lane,
                    task_id=task_id,
                    cancellable=False,
                    operation_resolution=(
                        _operation_resolution_control(
                            operation_id=str(row["operation_id"] or ""),
                            action_type=action_type,
                            action_label=action_label,
                            scope=scope,
                            operation_time=operation_time,
                        )
                        if lock_status in {"UNKNOWN", "REVIEW_BLOCKED"}
                        else None
                    ),
                )
            )
            if task_id:
                represented.add(task_id)
        return entries, represented, False

    def active_queue_task_ids(self) -> tuple[frozenset[str], bool]:
        """Return task identities present in the live file queue and read completeness."""

        task_ids: set[str] = set()
        complete = True
        locations = (
            (self.paths.queue_root / "inbox", "*.ready.json"),
            (self.paths.queue_root / "working", "*.request.json"),
            (self.paths.queue_root / "results", "*.result.json"),
        )
        for directory, pattern in locations:
            if not directory.is_dir():
                complete = False
                continue
            try:
                paths = sorted(directory.glob(pattern), key=_queue_artifact_sort_key)
            except OSError:
                complete = False
                continue
            if len(paths) > 100:
                complete = False
            for path in paths[:100]:
                try:
                    task_ids.update(_queue_task_ids(_read_queue_artifact(path)))
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    complete = False
        return frozenset(task_ids), complete

    def system(self) -> SystemReadModel:
        now = self._now()
        checked_at = _datetime(now)
        components: list[ComponentReadModel] = [
            ComponentReadModel(
                "运营页面",
                StateReadModel(ReadState.READY, "运行中", "页面可以正常访问"),
                checked_at,
            )
        ]
        try:
            schema = self.runtime.check_schema_health()
            operational = self.runtime.check_operational_health()
            if schema.ok and operational.ok:
                state = StateReadModel(ReadState.READY, "正常", "业务数据可以正常读取")
            else:
                state = StateReadModel(
                    ReadState.UNAVAILABLE,
                    "需要维护",
                    "业务数据暂时无法正常读取，请联系管理员处理",
                )
        except Exception:
            state = StateReadModel(
                ReadState.FAILED,
                "检查失败",
                "数据库状态暂时无法读取，请联系管理员检查。",
            )
        components.append(ComponentReadModel("业务数据库", state, checked_at))

        try:
            validate_rule_workbooks(
                price_rules_path=self.paths.price_rules_workbook,
                listing_rules_path=self.paths.listing_rules_workbook,
            )
            workbook_state = StateReadModel(
                ReadState.READY,
                "资料可用",
                "价格和上下架规则均已通过检查",
            )
        except Exception:
            workbook_state = StateReadModel(
                ReadState.UNAVAILABLE,
                "规则资料需要处理",
                "规则文件缺失或内容不符合要求，请联系管理员处理",
            )
        components.append(ComponentReadModel("规则资料", workbook_state, checked_at))

        components.append(
            ComponentReadModel(
                "自动任务服务",
                self._automation_state(now),
                checked_at,
            )
        )

        queue_state = self._queue_state()
        components.append(ComponentReadModel("平台任务传递", queue_state, checked_at))
        worker_state = self._worker_state(now)
        components.append(ComponentReadModel("影刀执行端", worker_state, checked_at))
        components.append(
            ComponentReadModel(
                "执行结果回收",
                self._importer_state(now),
                checked_at,
            )
        )
        components.append(
            ComponentReadModel(
                "通知发送",
                self._notification_state(now),
                checked_at,
            )
        )
        components.append(
            ComponentReadModel(
                "数据备份",
                self._backup_state(),
                checked_at,
            )
        )

        states = {item.state.state for item in components}
        if ReadState.FAILED in states:
            overall = StateReadModel(ReadState.FAILED, "部分服务检查失败", "请根据下方提示逐项处理")
        elif ReadState.UNAVAILABLE in states or ReadState.STALE in states:
            overall = StateReadModel(ReadState.INCOMPLETE, "部分服务需要处理", "请查看业务影响并按提示处理")
        else:
            overall = StateReadModel(ReadState.READY, "运行状态正常", "各项服务均可正常使用")
        return SystemReadModel(overall=overall, components=tuple(components))

    def detail(
        self,
        kind: str,
        entity_id: str,
        *,
        context: dict[str, str] | None = None,
    ) -> DetailReadModel | None:
        clean_id = str(entity_id).strip()
        if not clean_id:
            return None
        try:
            detail = None
            if kind == "product":
                detail = self._product_detail(clean_id)
            elif kind == "sales":
                detail = self._sales_detail(clean_id)
            elif kind == "settlement":
                detail = self._settlement_detail(clean_id)
            elif kind == "task":
                detail = self._task_detail(clean_id)
            elif kind == "review":
                detail = self._review_detail(clean_id)
            elif kind == "run":
                detail = self._run_detail(clean_id)
            elif kind == "execution":
                detail = self._execution_detail(clean_id)
            if detail is not None:
                back_url, back_label = _detail_back_link(kind, context or {})
                return replace(detail, back_url=back_url, back_label=back_label)
        except Exception:
            return DetailReadModel(
                title="详情暂不可用",
                subtitle="数据暂时无法读取",
                state=StateReadModel(
                    ReadState.FAILED,
                    "读取失败",
                    "请稍后重试；如持续失败，请联系管理员。",
                ),
                fields=(),
            )
        return None

    def mobile_review(
        self,
        review_task_id: str,
        raw_token: str,
    ) -> MobileReviewReadModel:
        try:
            validation = ReviewTokenService(self.runtime).validate_token(
                review_task_id,
                raw_token,
                action=None,
                now=self._now(),
            )
        except Exception:
            return MobileReviewReadModel(
                state=StateReadModel(
                    ReadState.FAILED,
                    "复核入口暂不可用",
                    "请稍后重试，本次没有提交任何处理结果。",
                ),
                review_title="人工复核",
                reason="",
                scope="",
                deadline="",
                allowed_actions=(),
                http_status="503 Service Unavailable",
            )

        review = validation.review_task
        token = validation.review_token
        reason = str(validation.failure_reason or "")
        if validation.is_valid and review is not None and token is not None:
            source_task = (
                self.runtime.get_task(review.source_task_id)
                if review.source_task_id
                else None
            )
            policy = {
                item.value for item in allowed_review_statuses(review, source_task)
            }
            actions = []
            action_options = []
            for item in token.allowed_actions:
                if item not in policy:
                    continue
                try:
                    status = ReviewTaskStatus(item)
                except ValueError:
                    continue
                label = review_action_label(review, source_task, status)
                if label:
                    actions.append(label)
                    action_options.append((status.value, label))
            operation_confirmations = self._mobile_operation_confirmations(
                review,
                token.allowed_actions,
            )
            if operation_confirmations:
                actions = []
                action_options = []
            state_detail = (
                "请先在平台查看商品的实际状态，再逐项确认；确认后不会再次启动平台读取。"
                if operation_confirmations
                else "请选择处理方式；提交后结果会立即保存，请勿重复提交。"
            )
            return MobileReviewReadModel(
                state=StateReadModel(
                    ReadState.READY,
                    "等待处理",
                    state_detail,
                ),
                review_title=_mobile_review_title(review),
                reason=self._review_reason(review),
                scope=self._review_scope(review),
                deadline=_datetime(review.required_by),
                allowed_actions=tuple(actions),
                http_status="200 OK",
                review_task_id=review_task_id,
                action_options=tuple(action_options),
                operation_confirmations=operation_confirmations,
            )
        if review is not None and (
            review.review_status is not ReviewTaskStatus.PENDING
            or "already used" in reason
            or "not pending" in reason
        ):
            return MobileReviewReadModel(
                state=StateReadModel(
                    ReadState.EMPTY,
                    "已经处理",
                    "该事项已经处理，无需重复提交。",
                ),
                review_title=_mobile_review_title(review),
                reason=review.resolution_note or self._review_reason(review),
                scope=self._review_scope(review),
                deadline=_datetime(review.resolved_at),
                allowed_actions=(),
                http_status="200 OK",
            )
        if "expired" in reason:
            title = "链接已过期"
            status = "410 Gone"
        elif "revoked" in reason:
            title = "链接已失效"
            status = "410 Gone"
        else:
            title = "链接无效"
            status = "404 Not Found"
        return MobileReviewReadModel(
            state=StateReadModel(ReadState.UNAVAILABLE, title, "请从最新飞书通知重新进入。"),
            review_title="人工复核",
            reason="",
            scope="",
            deadline="",
            allowed_actions=(),
            http_status=status,
        )

    def _mobile_operation_confirmations(
        self,
        review: ReviewTask,
        token_actions: list[str],
    ) -> tuple[MobileOperationConfirmationReadModel, ...]:
        source_task = (
            self.runtime.get_task(review.source_task_id)
            if review.source_task_id
            else None
        )
        if not is_execution_failure_review(review, source_task):
            return ()
        if not {"approved", "cancelled"}.intersection(token_actions):
            return ()
        rows = self.runtime.list_confirmable_shadowbot_operations_for_review(
            review.review_task_id
        )
        if not rows:
            return ()
        products, _ = self._load_products()
        product_labels = {
            product.internal_sku.upper(): _inventory_product_label(product)
            for product in products
        }
        controls: list[MobileOperationConfirmationReadModel] = []
        for row in rows:
            action_type = str(row.get("action_type") or "").strip()
            sku = str(row.get("internal_sku") or "").strip().upper()
            product_label = product_labels.get(sku, sku or "相关商品")
            action_label = ACTION_LABELS.get(action_type, "平台操作")
            applied_label, not_applied_label = _operation_confirmation_labels(
                action_type
            )
            controls.append(
                MobileOperationConfirmationReadModel(
                    operation_id=str(row.get("operation_id") or ""),
                    title=f"{product_label} · {action_label}",
                    detail=_mobile_operation_target_detail(row),
                    applied_label=applied_label,
                    not_applied_label=not_applied_label,
                )
            )
        return tuple(controls)

    def _business_table(
        self,
        dataset: str,
        page: int,
        trade_date: date | None,
        platform_name: str,
    ) -> TableReadModel:
        if dataset == "products":
            return self._products_table(page)
        if dataset == "prices":
            return self._prices_table(page, platform_name)
        if dataset == "settlements":
            if trade_date is None:
                return _unavailable_table(dataset, "交易日结算", "当前交易日不可用，请显式选择历史交易日。")
            return self._summary_table(
                dataset,
                "交易日结算",
                page,
                trade_date,
                platform_name,
                scope_type="PLATFORM",
                current_only=True,
                detail_kind="settlement",
            )
        if dataset == "varieties":
            if trade_date is None:
                return _unavailable_table(dataset, "品种销售结构", "当前交易日不可用，请显式选择历史交易日。")
            return self._summary_table(
                dataset,
                "品种销售结构",
                page,
                trade_date,
                platform_name,
                scope_type="VARIETY",
                current_only=True,
                detail_kind="settlement",
            )
        if dataset == "inventory-adjustments":
            return self._inventory_transactions_table(page)
        if dataset == "mappings":
            return _unavailable_table(
                dataset,
                "商品映射",
                "商品映射目录暂不可用，请联系管理员维护商品与平台的对应关系。",
            )
        if dataset == "history":
            return self._summary_table(
                dataset,
                "历史经营快照",
                page,
                None,
                platform_name,
                scope_type="PLATFORM",
                current_only=True,
                detail_kind="settlement",
            )
        if trade_date is None:
            return _unavailable_table(dataset, "销售与订单", "当前交易日不可用，请显式选择历史交易日。")
        return self._sales_table(page, trade_date, platform_name)

    def _project_table(
        self,
        dataset: str,
        page: int,
        trade_date: date | None,
    ) -> TableReadModel:
        if dataset == "reviews":
            return self._review_table(page=page)
        if dataset == "runs":
            return self._run_table(page=page)
        if dataset == "incidents":
            return self._incident_table(page)
        if dataset == "executions":
            return self._execution_table(page)
        if dataset == "notifications":
            return self._notification_table(page)
        return self._task_table(page=page)

    def _analysis_table(
        self,
        dataset: str,
        page: int,
        trade_date: date,
        platform_name: str,
    ) -> TableReadModel:
        scope = {
            "overview": "PLATFORM",
            "variety": "VARIETY",
            "grade": "GRADE",
            "time": "TIME_BUCKET",
        }[dataset]
        title = dict(ANALYSIS_DATASETS)[dataset]
        return self._summary_table(
            dataset,
            title,
            page,
            trade_date,
            platform_name,
            scope_type=scope,
            current_only=True,
            detail_kind="settlement",
        )

    def _products_table(self, page: int) -> TableReadModel:
        products, error = self._load_products()
        if error:
            return _failed_table("products", "商品与数据库库存")
        start = (page - 1) * DEFAULT_PAGE_SIZE
        selected = products[start : start + DEFAULT_PAGE_SIZE + 1]
        visible, has_next = _visible(selected, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                item.product_name,
                item.grade,
                item.stem_length,
                _qty(item.current_stock),
                "可销售" if item.sale_enabled else "停止销售",
            )
            for item in visible
        )
        urls = tuple(
            f"/database/product/{quote(item.internal_sku, safe='')}?source=business&dataset=products"
            for item in visible
        )
        return self._table(
            dataset="products",
            title="商品与数据库库存",
            columns=("商品", "等级", "规格", "数据库库存", "销售状态"),
            rows=rows,
            row_urls=urls,
            page=page,
            has_next=has_next,
            base_path="/database",
            query={"dataset": "products"},
            state=_rows_state(rows, "当前没有商品资料"),
        )

    def _inventory_transactions_table(self, page: int) -> TableReadModel:
        try:
            values = self.inventory.list_transactions(
                limit=DEFAULT_PAGE_SIZE + 1,
                offset=(page - 1) * DEFAULT_PAGE_SIZE,
            )
        except Exception:
            return _failed_table("inventory-adjustments", "库存调整流水")
        visible, has_next = _visible(values, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                item.internal_sku,
                _inventory_transaction_label(item.transaction_type),
                _signed_qty(item.inventory_delta),
                _qty(item.inventory_before),
                _qty(item.inventory_after),
                item.reason,
                _datetime(item.recorded_at),
            )
            for item in visible
        )
        return self._table(
            dataset="inventory-adjustments",
            title="库存调整流水",
            columns=("商品编码", "类型", "调整值", "调整前", "调整后", "原因", "记录时间"),
            rows=rows,
            page=page,
            has_next=has_next,
            base_path="/database",
            query={"dataset": "inventory-adjustments"},
            state=_rows_state(rows, "当前没有库存调整记录"),
        )

    def _prices_table(self, page: int, platform_name: str) -> TableReadModel:
        try:
            values = self.runtime.list_listing_statuses(
                platform_name=platform_name or None,
                limit=DEFAULT_PAGE_SIZE + 1,
                offset=(page - 1) * DEFAULT_PAGE_SIZE,
            )
        except Exception:
            return _failed_table("prices", "平台价格")
        visible, has_next = _visible(values, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                item.variety,
                item.grade,
                item.platform_name,
                _money(item.current_price),
                _qty(item.platform_stock_qty),
                _datetime(item.price_observed_at or item.updated_at),
                _listing_status_label(item.online_status),
            )
            for item in visible
        )
        return self._table(
            dataset="prices",
            title="平台价格",
            columns=(
                "品种",
                "等级",
                "平台",
                "当前售价",
                "平台可购上限",
                "更新时间",
                "上架状态",
            ),
            rows=rows,
            page=page,
            has_next=has_next,
            base_path="/database",
            query={"dataset": "prices", "platform": platform_name},
            state=_rows_state(rows, "当前没有平台价格记录"),
        )

    def _sales_table(self, page: int, trade_date: date, platform_name: str) -> TableReadModel:
        try:
            values = self.summaries.list_order_snapshots_page(
                platform_name=platform_name or None,
                platform_trade_date=trade_date,
                limit=DEFAULT_PAGE_SIZE + 1,
                offset=(page - 1) * DEFAULT_PAGE_SIZE,
            )
        except Exception:
            return _failed_table("sales", "销售与订单")
        visible, has_next = _visible(values, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                item.platform_trade_date.isoformat(),
                item.platform_name,
                _trade_day_status_label(item.trade_day_status),
                str(len(item.items)),
                _qty(sum(row.order_qty for row in item.items)),
                _money(sum((row.order_transaction_amount for row in item.items), Decimal("0"))),
                _snapshot_quality(item),
                _datetime(item.scan_completed_at),
            )
            for item in visible
        )
        detail_query = urlencode(
            {
                "source": "business",
                "dataset": "sales",
                "trade_date": trade_date.isoformat(),
                "platform": platform_name,
            }
        )
        urls = tuple(
            f"/database/sales/{quote(item.observation_batch_id, safe='')}?{detail_query}"
            for item in visible
        )
        state = _snapshot_rows_state(visible)
        return self._table(
            dataset="sales",
            title="销售与订单",
            columns=("交易日", "平台", "销售日状态", "订单数", "销量", "成交金额", "数据情况", "更新时间"),
            rows=rows,
            row_urls=urls,
            page=page,
            has_next=has_next,
            base_path="/database",
            query={
                "dataset": "sales",
                "trade_date": trade_date.isoformat(),
                "platform": platform_name,
            },
            state=state,
        )

    def _summary_table(
        self,
        dataset: str,
        title: str,
        page: int,
        trade_date: date | None,
        platform_name: str,
        *,
        scope_type: str,
        current_only: bool | None,
        detail_kind: str,
    ) -> TableReadModel:
        try:
            values = self.summaries.list_summaries_page(
                platform_name=platform_name or None,
                platform_trade_date=trade_date,
                scope_type=scope_type,
                current_only=current_only,
                limit=DEFAULT_PAGE_SIZE + 1,
                offset=(page - 1) * DEFAULT_PAGE_SIZE,
            )
        except Exception:
            return _failed_table(dataset, title)
        visible, has_next = _visible(values, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                item.platform_trade_date.isoformat(),
                item.platform_name,
                _scope_label(item.scope_type, item.scope_key),
                _qty(item.sold_qty),
                _money(item.transaction_amount_total),
                QUALITY_LABELS.get(item.quality_level.value, "质量未知"),
                SUMMARY_STATUS_LABELS.get(item.summary_status.value, "状态未知"),
                _datetime(item.updated_at),
            )
            for item in visible
        )
        base_path = "/database/sales-analysis" if dataset in dict(ANALYSIS_DATASETS) else "/database"
        query = {"dataset": dataset, "platform": platform_name}
        if trade_date is not None:
            query["trade_date"] = trade_date.isoformat()
        detail_query = urlencode(
            {
                "source": "sales-analysis" if dataset in dict(ANALYSIS_DATASETS) else "business",
                **query,
            }
        )
        urls = tuple(
            f"/database/{detail_kind}/{quote(item.summary_id, safe='')}?{detail_query}"
            for item in visible
        )
        return self._table(
            dataset=dataset,
            title=title,
            columns=("交易日", "平台", "范围", "销量", "成交金额", "数据质量", "结算状态", "更新时间"),
            rows=rows,
            row_urls=urls,
            page=page,
            has_next=has_next,
            base_path=base_path,
            query=query,
            state=self._summary_state(visible, ""),
        )

    def _task_table(
        self,
        *,
        page: int,
        pending_only: bool = False,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> TableReadModel:
        try:
            if pending_only:
                values = self.runtime.list_tasks(
                    status=TaskStatus.PENDING,
                    limit=page_size + 1,
                    offset=(page - 1) * page_size,
                )
            else:
                values = self.runtime.list_task_history_page(
                    limit=page_size + 1,
                    offset=(page - 1) * page_size,
                )
        except Exception:
            return _failed_table("tasks", "任务", page_size=page_size)
        visible, has_next = _visible(values, page_size)
        rows = tuple(
            (
                ACTION_LABELS.get(item.action_type.value, "其他任务"),
                item.internal_sku or "全局",
                item.platform_name or "—",
                TASK_STATUS_LABELS.get(item.task_status.value, "状态未知"),
                _datetime(item.created_at),
            )
            for item in visible
        )
        urls = tuple(
            f"/management/task/{quote(item.task_id, safe='')}?source=project&dataset=tasks"
            for item in visible
        )
        return self._table(
            dataset="tasks",
            title="当前任务" if pending_only else "任务",
            columns=("任务", "商品", "平台", "状态", "创建时间"),
            rows=rows,
            row_urls=urls,
            page=page,
            page_size=page_size,
            has_next=has_next,
            base_path="/database/project",
            query={"dataset": "tasks"},
            state=_rows_state(rows, "当前没有任务"),
        )

    def _review_table(
        self,
        *,
        page: int,
        pending_only: bool = False,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> TableReadModel:
        try:
            if pending_only:
                values = self.runtime.list_review_tasks(
                    status=ReviewTaskStatus.PENDING,
                    limit=page_size + 1,
                    offset=(page - 1) * page_size,
                )
            else:
                values = self.runtime.list_review_history_page(
                    limit=page_size + 1,
                    offset=(page - 1) * page_size,
                )
        except Exception:
            return _failed_table("reviews", "复核", page_size=page_size)
        visible, has_next = _visible(values, page_size)
        rows = tuple(
            (
                _review_type_label(item.review_type),
                self._review_scope(item),
                REVIEW_STATUS_LABELS.get(item.review_status.value, "状态未知"),
                _datetime(item.required_by),
                self._review_reason(item),
            )
            for item in visible
        )
        urls = tuple(
            f"/management/review/{quote(item.review_task_id, safe='')}?source=project&dataset=reviews"
            for item in visible
        )
        return self._table(
            dataset="reviews",
            title="人工复核",
            columns=("复核类型", "范围", "状态", "处理期限", "原因"),
            rows=rows,
            row_urls=urls,
            page=page,
            page_size=page_size,
            has_next=has_next,
            base_path="/database/project",
            query={"dataset": "reviews"},
            state=_rows_state(rows, "当前没有复核事项"),
        )

    def _run_table(self, *, page: int, page_size: int = DEFAULT_PAGE_SIZE) -> TableReadModel:
        try:
            values = self.automation.list_runs(
                limit=page_size + 1,
                offset=(page - 1) * page_size,
            )
        except Exception:
            return _failed_table("runs", "自动化运行", page_size=page_size)
        visible, has_next = _visible(values, page_size)
        rows = tuple(
            (
                _automation_job_label(item.job_type),
                item.platform_name,
                item.platform_trade_date.isoformat(),
                _automation_status_label(item.run_status.value),
                _datetime(item.scheduled_for),
            )
            for item in visible
        )
        urls = tuple(
            f"/database/run/{quote(item.run_id, safe='')}?source=project&dataset=runs"
            for item in visible
        )
        return self._table(
            dataset="runs",
            title="自动化运行",
            columns=("方案", "平台", "交易日", "状态", "计划时间"),
            rows=rows,
            row_urls=urls,
            page=page,
            page_size=page_size,
            has_next=has_next,
            base_path="/database/project",
            query={"dataset": "runs"},
            state=_rows_state(rows, "当前没有自动化运行记录"),
        )

    def _incident_table(self, page: int) -> TableReadModel:
        try:
            values = self.incidents.list_history_page(
                limit=DEFAULT_PAGE_SIZE + 1,
                offset=(page - 1) * DEFAULT_PAGE_SIZE,
            )
        except Exception:
            return _failed_table("incidents", "异常")
        visible, has_next = _visible(values, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                item.title or "运营异常",
                item.severity,
                _incident_status_label(item.incident_status.value),
                item.platform_name or "—",
                str(item.occurrence_count),
                _datetime(item.last_detected_at),
            )
            for item in visible
        )
        return self._table(
            dataset="incidents",
            title="异常",
            columns=("异常", "等级", "状态", "平台", "发生次数", "最近发生"),
            rows=rows,
            page=page,
            has_next=has_next,
            base_path="/database/project",
            query={"dataset": "incidents"},
            state=_rows_state(rows, "当前没有异常记录"),
        )

    def _execution_table(self, page: int) -> TableReadModel:
        try:
            values = self.runtime.list_execution_logs(
                limit=DEFAULT_PAGE_SIZE + 1,
                offset=(page - 1) * DEFAULT_PAGE_SIZE,
            )
        except Exception:
            return _failed_table("executions", "执行记录")
        visible, has_next = _visible(values, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                _executor_label(item.executor_name),
                _execution_status(item.success_flag),
                _execution_result_detail(item.success_flag),
                _datetime(item.start_time),
                _datetime(item.end_time),
            )
            for item in visible
        )
        urls = tuple(
            f"/database/execution/{quote(item.log_id, safe='')}?source=project&dataset=executions"
            for item in visible
        )
        return self._table(
            dataset="executions",
            title="执行记录",
            columns=("执行端", "结果", "说明", "开始时间", "结束时间"),
            rows=rows,
            row_urls=urls,
            page=page,
            has_next=has_next,
            base_path="/database/project",
            query={"dataset": "executions"},
            state=_rows_state(rows, "当前没有执行记录"),
        )

    def _notification_table(self, page: int) -> TableReadModel:
        try:
            values = self.runtime.list_notification_outbox(
                limit=DEFAULT_PAGE_SIZE + 1,
                offset=(page - 1) * DEFAULT_PAGE_SIZE,
            )
        except Exception:
            return _failed_table("notifications", "通知")
        visible, has_next = _visible(values, DEFAULT_PAGE_SIZE)
        rows = tuple(
            (
                _notification_type_label(item.notification_type),
                _notification_channel_label(item.channel),
                _notification_status_label(item.status),
                _datetime(item.created_at),
                _datetime(item.sent_at),
            )
            for item in visible
        )
        return self._table(
            dataset="notifications",
            title="通知",
            columns=("通知类型", "通道", "状态", "创建时间", "发送时间"),
            rows=rows,
            page=page,
            has_next=has_next,
            base_path="/database/project",
            query={"dataset": "notifications"},
            state=_rows_state(rows, "当前没有通知记录"),
        )

    def _dictionary_table(self, page: int) -> TableReadModel:
        values = (
            ("交易日", "平台销售使用的日期", "18:00 至次日 18:00"),
            ("卖家作业日", "日结和次日计划使用的日期", "20:00 至次日 20:00"),
            ("销量", "订单数量或符合条件的库存变化估算", "同一笔销量只计算一次"),
            ("成交金额", "页面展示成交金额的合计", "不代表卖家实收、退款净额或财务到账"),
            ("数据库库存", "目前还有多少花可以销售", "由入库、销售扣减和人工调整共同更新"),
            ("平台库存", "客户在该平台最多可购买的数量", "不等于数据库库存"),
            ("营业中", "当前销售日仍在进行", "显示截至最近更新时间的数据"),
            ("已确认结算", "数据完整且核对通过后的结算", "20:00 后先生成初步结算，核对完成后确认"),
        )
        return TableReadModel(
            dataset="fields",
            title="字段说明",
            columns=("名称", "定义", "边界"),
            rows=values,
            state=StateReadModel(ReadState.READY, "可用", "使用当前业务口径"),
            page=page,
            page_size=DEFAULT_PAGE_SIZE,
        )

    def _quality_table(self, page: int, trade_date: date, platform_name: str) -> TableReadModel:
        return self._summary_table(
            "freshness",
            "质量与新鲜度",
            page,
            trade_date,
            platform_name,
            scope_type="PLATFORM",
            current_only=True,
            detail_kind="settlement",
        )

    def _today_product_table(
        self,
        trade_date: date,
        products: list[Product],
        products_error: str,
    ) -> TableReadModel:
        if products_error:
            return _failed_table("today-products", "品种销售与库存")
        try:
            sales = self.summaries.list_summaries_page(
                platform_trade_date=trade_date,
                scope_type="SKU",
                current_only=True,
                limit=max(1, len(products) + 1),
            )
        except Exception:
            sales = ()
        by_sku = {item.scope_key: item for item in sales}
        policy = self.inventory.get_default_alert_policy()
        safety_margin = policy.threshold_qty if policy is not None else 0
        alert_enabled = bool(policy is not None and policy.enabled)
        rows: list[tuple[str, ...]] = []
        urls: list[str] = []
        for inventory in summarize_inventory_by_variety(products)[:DEFAULT_PAGE_SIZE]:
            variety_sales = [
                by_sku[sku]
                for sku in inventory.internal_skus
                if sku in by_sku
            ]
            sold_values = [
                item.sold_qty for item in variety_sales if item.sold_qty is not None
            ]
            amount_values = [
                item.transaction_amount_total
                for item in variety_sales
                if item.transaction_amount_total is not None
            ]
            sold = sum(sold_values) if sold_values else None
            amount = sum(amount_values, Decimal("0")) if amount_values else None
            average = (
                amount / Decimal(sold)
                if amount is not None and sold not in (None, 0)
                else (Decimal("0") if amount == 0 and sold == 0 else None)
            )
            rows.append(
                (
                    inventory.variety,
                    _grade_inventory_summary(inventory.grade_quantities),
                    _qty(sold),
                    _money(average),
                    _money(amount),
                    _qty(inventory.current_qty),
                    _inventory_margin_label(
                        inventory.current_qty,
                        safety_margin,
                        enabled=alert_enabled,
                    ),
                    _joined_labels(
                        QUALITY_LABELS.get(item.quality_level.value, "质量未知")
                        for item in variety_sales
                    )
                    if variety_sales
                    else "销售数据待更新",
                )
            )
            urls.append(
                f"/database/product/{quote(inventory.internal_skus[0], safe='')}?source=today"
            )
        return TableReadModel(
            dataset="today-products",
            title="品种销售与库存",
            columns=(
                "品种",
                "等级库存",
                "今日已售",
                "成交均价",
                "销售额",
                "真实库存",
                "安全余量",
                "更新情况",
            ),
            rows=tuple(rows),
            row_urls=tuple(urls),
            state=_rows_state(tuple(rows), "当前没有商品资料"),
            page_size=DEFAULT_PAGE_SIZE,
        )

    def _today_platform_limit_table(self, products_error: str) -> TableReadModel:
        if products_error:
            return _failed_table("today-platform-limits", "平台可购上限")
        try:
            values = self.runtime.list_listing_statuses(limit=101)
        except Exception:
            return _failed_table("today-platform-limits", "平台可购上限")
        rows = tuple(
            (
                item.platform_name,
                item.variety,
                item.grade,
                _qty(item.platform_stock_qty),
                _listing_status_label(item.online_status),
                _datetime(item.inventory_observed_at or item.updated_at),
            )
            for item in values[:DEFAULT_PAGE_SIZE]
        )
        return TableReadModel(
            dataset="today-platform-limits",
            title="平台可购上限",
            columns=("平台", "品种", "等级", "可购上限", "上架状态", "更新时间"),
            rows=rows,
            state=_rows_state(rows, "当前没有平台库存扫描数据"),
            page_size=DEFAULT_PAGE_SIZE,
        )

    def _today_timeline(self, trade_date: date) -> tuple[tuple[str, str, str], ...]:
        values: list[tuple[datetime, str, str]] = []
        try:
            runs = self.automation.list_runs(limit=8)
            values.extend(
                (
                    item.updated_at or item.scheduled_for,
                    _automation_job_label(item.job_type),
                    _automation_status_label(item.run_status.value),
                )
                for item in runs
                if item.platform_trade_date == trade_date
            )
        except Exception:
            pass
        try:
            logs = self.runtime.list_execution_logs(limit=8)
            values.extend(
                (
                    item.end_time or item.start_time,
                    "平台执行",
                    _execution_status(item.success_flag),
                )
                for item in logs
            )
        except Exception:
            pass
        values.sort(key=lambda item: _sortable_datetime(item[0]), reverse=True)
        return tuple((_datetime(moment), title, detail) for moment, title, detail in values[:8])

    def _product_detail(self, internal_sku: str) -> DetailReadModel | None:
        products, error = self._load_products()
        if error:
            raise RuntimeError(error)
        product = next((item for item in products if item.internal_sku == internal_sku), None)
        if product is None:
            return None
        listings = self.runtime.list_listing_statuses(internal_sku=internal_sku, limit=25)
        fields = [
            DetailFieldReadModel("商品", product.product_name),
            DetailFieldReadModel("等级", product.grade),
            DetailFieldReadModel("规格", product.stem_length),
            DetailFieldReadModel("数据库库存", _qty(product.current_stock)),
            DetailFieldReadModel("基础成本", _money(product.base_cost)),
            DetailFieldReadModel("销售状态", "可销售" if product.sale_enabled else "停止销售"),
            DetailFieldReadModel("库存来源", "数据库库存"),
        ]
        related = tuple(
            (
                f"{item.platform_name} · {_money(item.current_price)} · "
                f"平台可购上限 {_qty(item.platform_stock_qty)} · "
                f"{_listing_status_label(item.online_status)}",
                "/database?" + urlencode({"dataset": "prices", "platform": item.platform_name}),
            )
            for item in listings
        )
        return DetailReadModel(
            title=f"{product.product_name} · {product.grade}",
            subtitle="商品与库存详情",
            state=StateReadModel(ReadState.READY, "可用", "商品资料已更新"),
            fields=tuple(fields),
            related=related,
        )

    def _sales_detail(self, batch_id: str) -> DetailReadModel | None:
        snapshot = self.summaries.get_order_snapshot(batch_id)
        if snapshot is None:
            return None
        qty = sum(item.order_qty for item in snapshot.items)
        amount = sum((item.order_transaction_amount for item in snapshot.items), Decimal("0"))
        state = _snapshot_rows_state((snapshot,))
        return DetailReadModel(
            title=f"{snapshot.platform_trade_date.isoformat()} 销售数据",
            subtitle=f"{snapshot.platform_name} · {_trade_day_status_label(snapshot.trade_day_status)}",
            state=state,
            fields=(
                DetailFieldReadModel("订单数", str(len(snapshot.items))),
                DetailFieldReadModel("销量", _qty(qty)),
                DetailFieldReadModel("成交金额", _money(amount)),
                DetailFieldReadModel("采集是否完整", "是" if snapshot.scope_complete else "否"),
                DetailFieldReadModel("是否读完全部订单", "是" if snapshot.end_marker_verified else "否"),
                DetailFieldReadModel("更新时间", _datetime(snapshot.scan_completed_at)),
            ),
        )

    def _settlement_detail(self, summary_id: str) -> DetailReadModel | None:
        summary = self.summaries.get_summary(summary_id)
        if summary is None:
            return None
        current = (
            summary
            if summary.is_current
            else self.summaries.get_current_summary(summary.summary_series_id)
        )
        if summary.is_current:
            state = self._summary_state((summary,), "")
            version_identity = "当前版本"
        else:
            current_version = (
                f"当前版本为 v{current.version_no}。"
                if current is not None
                else "当前版本暂不可读。"
            )
            state = StateReadModel(
                ReadState.STALE,
                "历史版本 · 已更新",
                current_version,
            )
            version_identity = "历史版本，已更新"
        return DetailReadModel(
            title=f"{summary.platform_trade_date.isoformat()} 交易日结算",
            subtitle=f"{summary.platform_name} · {_scope_label(summary.scope_type, summary.scope_key)}",
            state=state,
            fields=(
                DetailFieldReadModel("版本", f"v{summary.version_no}"),
                DetailFieldReadModel("版本状态", version_identity),
                DetailFieldReadModel(
                    "版本关系",
                    "基于上一版本重新结算"
                    if summary.supersedes_summary_id
                    else "首个版本",
                ),
                DetailFieldReadModel("销量", _qty(summary.sold_qty)),
                DetailFieldReadModel("成交金额", _money(summary.transaction_amount_total)),
                DetailFieldReadModel("数据来源", SOURCE_LABELS.get(summary.fact_source.value, "来源未知") if summary.fact_source else "不可用"),
                DetailFieldReadModel("数据质量", QUALITY_LABELS.get(summary.quality_level.value, "质量未知")),
                DetailFieldReadModel("结算状态", SUMMARY_STATUS_LABELS.get(summary.summary_status.value, "状态未知")),
                DetailFieldReadModel("质量说明", summary.quality_reason or "—"),
                DetailFieldReadModel("更新时间", _datetime(summary.updated_at)),
            ),
        )

    def _task_detail(self, task_id: str) -> DetailReadModel | None:
        item = self.runtime.get_task(task_id)
        if item is None:
            return None
        fields = (
            DetailFieldReadModel("任务", ACTION_LABELS.get(item.action_type.value, "其他任务")),
            DetailFieldReadModel("商品", item.internal_sku or "全局"),
            DetailFieldReadModel("平台", item.platform_name or "—"),
            DetailFieldReadModel("状态", TASK_STATUS_LABELS.get(item.task_status.value, "状态未知")),
            DetailFieldReadModel("来源", _origin_label(item.origin_type.value)),
            DetailFieldReadModel("目标价格", _money(item.target_price)),
            DetailFieldReadModel("平台目标库存", _qty(item.target_inventory)),
            DetailFieldReadModel("创建时间", _datetime(item.created_at)),
            DetailFieldReadModel("结果", _task_result_detail(item)),
        )
        state = _task_state(item)
        return DetailReadModel("任务详情", "任务信息", state, fields)

    def _review_detail(self, review_task_id: str) -> DetailReadModel | None:
        item = self.runtime.get_review_task(review_task_id)
        if item is None:
            return None
        return DetailReadModel(
            title="人工复核详情",
            subtitle=_review_type_label(item.review_type),
            state=StateReadModel(
                ReadState.READY if item.review_status is ReviewTaskStatus.PENDING else ReadState.EMPTY,
                REVIEW_STATUS_LABELS.get(item.review_status.value, "状态未知"),
                "查看已保存的复核结果",
            ),
            fields=(
                DetailFieldReadModel("范围", self._review_scope(item)),
                DetailFieldReadModel("原因", self._review_reason(item)),
                DetailFieldReadModel("处理期限", _datetime(item.required_by)),
                DetailFieldReadModel("处理结果", item.resolution_note or "—"),
                DetailFieldReadModel("处理时间", _datetime(item.resolved_at)),
            ),
        )

    def _review_reason(self, item: ReviewTask) -> str:
        return review_reason_display(item)

    def _review_scope(self, item: ReviewTask) -> str:
        payload = item.review_payload if isinstance(item.review_payload, dict) else {}
        affected_ids = payload.get("affected_task_ids")
        if not isinstance(affected_ids, list):
            affected_ids = []
        task_ids = [str(value).strip() for value in affected_ids if str(value).strip()]
        if not task_ids and item.source_task_id:
            task_ids = [item.source_task_id]

        labels: list[str] = []
        for task_id in task_ids:
            task = self.runtime.get_task(task_id)
            if task is None or not task.internal_sku:
                continue
            product = self.master_data.get_product(task.internal_sku)
            label = (
                f"{product.product_name} {product.grade}".strip()
                if product is not None
                else task.internal_sku
            )
            if label and label not in labels:
                labels.append(label)
        product_label = ""
        if item.internal_sku:
            product = self.master_data.get_product(item.internal_sku)
            if product is not None:
                product_label = f"{product.product_name} {product.grade}".strip()
        return review_scope_display(
            item,
            product_label=product_label,
            affected_product_labels=tuple(labels),
        )

    def _run_detail(self, run_id: str) -> DetailReadModel | None:
        item = self.automation.get_run(run_id)
        if item is None:
            return None
        status = _automation_status_label(item.run_status.value)
        return DetailReadModel(
            title="自动化运行详情",
            subtitle=_automation_job_label(item.job_type),
            state=StateReadModel(_run_state(item.run_status.value), status, _automation_result_detail(item.run_status.value)),
            fields=(
                DetailFieldReadModel("平台", item.platform_name),
                DetailFieldReadModel("交易日", item.platform_trade_date.isoformat()),
                DetailFieldReadModel("卖家作业日", item.seller_operation_date.isoformat()),
                DetailFieldReadModel("计划时间", _datetime(item.scheduled_for)),
                DetailFieldReadModel("开始时间", _datetime(item.started_at)),
                DetailFieldReadModel("完成时间", _datetime(item.finished_at)),
                DetailFieldReadModel("结果", _automation_result_detail(item.run_status.value)),
            ),
        )

    def _execution_detail(self, log_id: str) -> DetailReadModel | None:
        item = self.runtime.get_execution_log(log_id)
        if item is None:
            return None
        return DetailReadModel(
            title="执行记录详情",
            subtitle=_executor_label(item.executor_name),
            state=StateReadModel(
                ReadState.READY if item.success_flag is True else (ReadState.FAILED if item.success_flag is False else ReadState.INCOMPLETE),
                _execution_status(item.success_flag),
                _execution_result_detail(item.success_flag),
            ),
            fields=(
                DetailFieldReadModel("执行结果", _execution_status(item.success_flag)),
                DetailFieldReadModel("开始时间", _datetime(item.start_time)),
                DetailFieldReadModel("结束时间", _datetime(item.end_time)),
                DetailFieldReadModel("结果说明", _execution_result_detail(item.success_flag)),
            ),
            related=(("查看关联任务", f"/management/task/{quote(item.task_id, safe='')}"),),
        )

    def _review_notification(self, item: ReviewTask) -> NotificationItemReadModel:
        return NotificationItemReadModel(
            title=_review_type_label(item.review_type),
            detail=self._review_reason(item),
            severity="S2",
            url=f"/management/review/{quote(item.review_task_id, safe='')}",
        )

    def _summary_state(
        self,
        values: Iterable,
        error: str,
    ) -> StateReadModel:
        summaries = tuple(values)
        if error:
            return StateReadModel(ReadState.FAILED, "销售数据读取失败", "请稍后重试；如持续失败，请联系管理员。")
        if not summaries:
            return StateReadModel(ReadState.UNAVAILABLE, "销售数据待更新", "尚未取得当前销售日的可用数据。")
        available = [
            item
            for item in summaries
            if item.quality_level is not DataQualityLevel.UNAVAILABLE
            and item.sold_qty is not None
        ]
        if not available:
            return StateReadModel(ReadState.UNAVAILABLE, "销售数据暂不可用", "当前没有完整订单或可用的库存变化估算。")
        if len(available) != len(summaries) or any(
            item.quality_level
            in {
                DataQualityLevel.ORDER_PARTIAL,
                DataQualityLevel.SCAN_ESTIMATED_MEDIUM,
                DataQualityLevel.SCAN_ESTIMATED_LOW,
            }
            for item in available
        ):
            return StateReadModel(ReadState.INCOMPLETE, "部分销售数据可用", "完整数据仍在补充，当前结果仅供参考。")
        latest = max(item.updated_at for item in available)
        if sum(item.sold_qty or 0 for item in available) == 0:
            return StateReadModel(
                ReadState.TRUSTWORTHY_ZERO,
                "已确认暂无销量",
                f"订单采集完整；最近更新于 {_datetime(latest)}",
            )
        quality = _joined_labels(
            QUALITY_LABELS.get(item.quality_level.value, "质量未知")
            for item in available
        )
        return StateReadModel(
            ReadState.READY,
            "销售数据已更新",
            f"{quality}；最近更新于 {_datetime(latest)}",
        )

    def _table(
        self,
        *,
        dataset: str,
        title: str,
        columns: tuple[str, ...],
        rows: tuple[tuple[str, ...], ...],
        state: StateReadModel,
        page: int,
        has_next: bool,
        base_path: str,
        query: dict[str, str],
        row_urls: tuple[str, ...] = (),
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> TableReadModel:
        previous_url = ""
        next_url = ""
        if page > 1:
            previous_url = base_path + "?" + urlencode({**query, "page": page - 1})
        if has_next:
            next_url = base_path + "?" + urlencode({**query, "page": page + 1})
        return TableReadModel(
            dataset=dataset,
            title=title,
            columns=columns,
            rows=rows,
            row_urls=row_urls,
            state=state,
            page=page,
            page_size=page_size,
            has_previous=page > 1,
            has_next=has_next,
            previous_url=previous_url,
            next_url=next_url,
        )

    def _load_products(self) -> tuple[list[Product], str]:
        try:
            return list(self.master_data.list_products()), ""
        except Exception as exc:
            return [], type(exc).__name__

    def _platform_options(self, trade_date: date | None) -> tuple[str, ...]:
        names: list[str] = []
        try:
            names.extend(
                item.platform_name
                for item in self.runtime.list_listing_statuses(limit=101)
                if item.platform_name
            )
        except Exception:
            pass
        if trade_date is not None:
            try:
                names.extend(
                    item.platform_name
                    for item in self.summaries.list_summaries_page(
                        platform_trade_date=trade_date,
                        current_only=True,
                        limit=101,
                    )
                    if item.platform_name
                )
            except Exception:
                pass
        return tuple(dict.fromkeys(names))

    def _schema_is_ready(self) -> bool:
        try:
            return bool(self.runtime.check_schema_health().ok)
        except Exception:
            return False

    def _time_context(self, now: datetime) -> _TimeContextReadResult:
        try:
            policies = self.automation.load_operational_time_policies()
        except Exception:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.FAILED,
                    "销售日期读取失败",
                    "当前无法确定销售日期，请联系管理员检查时间设置。",
                ),
            )
        if not policies:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.UNAVAILABLE,
                    "销售日期暂不可用",
                    "当前没有可用的销售日期设置，请联系管理员。",
                ),
            )
        try:
            service = OperationalTimeService(policies=policies)
        except ValueError:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.FAILED,
                    "销售日期设置有误",
                    "当前销售日期设置存在冲突，请联系管理员。",
                ),
            )
        try:
            context = service.classify(now)
        except ValueError:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.UNAVAILABLE,
                    "当前销售日期暂不可用",
                    "当前无法确定唯一的销售日期，请联系管理员。",
                ),
            )
        except Exception:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.FAILED,
                    "销售日期计算失败",
                    "当前无法确定销售日期，请联系管理员检查时间设置。",
                ),
            )
        return _TimeContextReadResult(
            context=context,
            state=StateReadModel(
                ReadState.READY,
                "销售日期已确定",
                "已按当前营业时间设置计算。",
            ),
        )

    def _queue_state(self) -> StateReadModel:
        root = self.paths.queue_root
        if not root.is_dir():
            return StateReadModel(ReadState.UNAVAILABLE, "任务传递暂不可用", "平台任务暂时不能发送，请联系管理员")
        counts: dict[str, int] = {}
        try:
            patterns = {
                "inbox": "*.ready.json",
                "working": "*.request.json",
                "results": "*.result.json",
                "archive": "*",
            }
            for name, pattern in patterns.items():
                path = root / name
                if not path.is_dir():
                    return StateReadModel(ReadState.UNAVAILABLE, "任务传递暂不可用", "平台任务暂时不能发送，请联系管理员")
                counts[name] = sum(1 for item in path.glob(pattern) if item.is_file())
        except OSError:
            return StateReadModel(ReadState.FAILED, "任务传递检查失败", "请联系管理员检查平台任务传递服务。")
        active = counts["inbox"] + counts["working"] + counts["results"]
        return StateReadModel(ReadState.READY, "运行正常", f"当前共有 {active} 项任务正在传递或等待回收")

    def _worker_state(self, now: datetime) -> StateReadModel:
        try:
            report = build_shadowbot_worker_health_report(
                self.paths.queue_root,
                expected_status="RUNNING",
                max_age_seconds=30,
                now=now,
            )
        except Exception:
            return StateReadModel(ReadState.FAILED, "执行端状态无法读取", "请使用上方恢复按钮检查影刀执行端。")
        if report.get("ok") is True:
            updated = _queue_datetime(report.get("updated_at"))
            return StateReadModel(
                ReadState.READY,
                "运行中",
                f"最近更新于 {_datetime(updated) if updated is not None else '刚刚'}",
            )
        error_code = str(report.get("error_code") or "")
        if error_code == "WORKER_HEARTBEAT_MISSING":
            return StateReadModel(
                ReadState.UNAVAILABLE,
                "执行端未运行",
                "平台任务暂时不会执行，请使用恢复按钮检查",
            )
        status = str(report.get("status") or "UNKNOWN").upper()
        checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
        if not checks.get("heartbeat_fresh", False):
            return StateReadModel(
                ReadState.STALE,
                "执行端响应超时",
                "平台任务可能暂停，请使用恢复按钮检查",
            )
        if status == "RUNNING":
            return StateReadModel(ReadState.INCOMPLETE, "执行端需要检查", "平台任务可能受到影响，请使用恢复按钮检查")
        if status == "STOPPED":
            return StateReadModel(ReadState.UNAVAILABLE, "已停止", "平台任务不会执行，请使用恢复按钮启动")
        return StateReadModel(ReadState.INCOMPLETE, "状态未知", "请使用恢复按钮检查影刀执行端")

    def _automation_state(self, now: datetime) -> StateReadModel:
        heartbeat = self.paths.automation_heartbeat
        if heartbeat is None or not heartbeat.is_file():
            return StateReadModel(ReadState.UNAVAILABLE, "自动任务未运行", "定时扫描、日结和任务生成可能不会自动执行")
        try:
            payload = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            status = str(payload.get("status") or "UNKNOWN").upper()
            updated_raw = payload.get("last_cycle_at") or payload.get("updated_at")
            updated = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return StateReadModel(ReadState.FAILED, "自动任务状态无法读取", "请联系管理员检查自动任务服务。")
        if _age(now, updated) > timedelta(seconds=30):
            return StateReadModel(ReadState.STALE, "自动任务响应超时", f"最近运行于 {_datetime(updated)}")
        if status != "RUNNING":
            return StateReadModel(ReadState.UNAVAILABLE, "已停止", f"最近运行于 {_datetime(updated)}")
        return StateReadModel(ReadState.READY, "运行中", f"最近运行于 {_datetime(updated)}")

    def _queue_services_state(self, now: datetime) -> StateReadModel:
        heartbeat = (
            self.paths.queue_root
            / "control"
            / "pra_queue_services_heartbeat.json"
        )
        if not heartbeat.is_file():
            return StateReadModel(
                ReadState.UNAVAILABLE,
                "结果回收和通知发送未运行",
                "执行结果回收和通知发送可能延迟",
            )
        try:
            payload = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            if payload.get("schema_version") != "queue-services-heartbeat-1.0":
                raise ValueError("unsupported queue service heartbeat")
            if payload.get("service") != "shadowbot_queue_services":
                raise ValueError("unexpected queue service identity")
            status = str(payload.get("status") or "UNKNOWN").upper()
            updated = datetime.fromisoformat(
                str(payload.get("updated_at") or "").replace("Z", "+00:00")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return StateReadModel(
                ReadState.FAILED,
                "结果回收和通知状态无法读取",
                "请联系管理员检查结果回收和通知发送服务。",
            )
        if _age(now, updated) > timedelta(seconds=30):
            return StateReadModel(
                ReadState.STALE,
                "结果回收和通知响应超时",
                f"最近运行于 {_datetime(updated)}",
            )
        if status != "RUNNING":
            return StateReadModel(
                ReadState.UNAVAILABLE,
                "结果回收和通知已停止",
                f"最近运行于 {_datetime(updated)}",
            )
        return StateReadModel(
            ReadState.READY,
            "结果回收和通知运行中",
            f"最近运行于 {_datetime(updated)}",
        )

    def _importer_state(self, now: datetime) -> StateReadModel:
        service_state = self._queue_services_state(now)
        if service_state.state is not ReadState.READY:
            return service_state
        results = self.paths.queue_root / "results"
        if not results.is_dir():
            return StateReadModel(ReadState.UNAVAILABLE, "结果回收暂不可用", "平台执行结果可能无法及时更新")
        try:
            pending = sum(1 for item in results.iterdir() if item.is_file())
        except OSError:
            return StateReadModel(ReadState.FAILED, "结果回收检查失败", "请联系管理员检查执行结果回收服务。")
        if pending:
            return StateReadModel(ReadState.INCOMPLETE, "等待回收", f"当前有 {pending} 项执行结果等待回收")
        return StateReadModel(ReadState.READY, "运行正常", "当前没有等待回收的执行结果")

    def _notification_state(self, now: datetime) -> StateReadModel:
        service_state = self._queue_services_state(now)
        if service_state.state is not ReadState.READY:
            return service_state
        try:
            values = self.runtime.list_notification_outbox(limit=100, offset=0)
        except Exception:
            return StateReadModel(ReadState.FAILED, "通知状态读取失败", "请联系管理员检查通知发送服务。")
        failed = sum(str(item.status).upper() == "FAILED" for item in values)
        pending = sum(str(item.status).upper() in {"PENDING", "IN_FLIGHT"} for item in values)
        if failed:
            return StateReadModel(ReadState.FAILED, "存在发送失败", f"最近记录中有 {failed} 条通知发送失败")
        if pending:
            return StateReadModel(ReadState.INCOMPLETE, "等待发送", f"当前有 {pending} 条通知等待发送")
        return StateReadModel(ReadState.READY, "运行正常", "当前没有等待发送的通知")

    def _backup_state(self) -> StateReadModel:
        root = self.paths.backup_root
        latest = root / "latest.json" if root is not None else None
        if latest is None or not latest.is_file():
            return StateReadModel(ReadState.UNAVAILABLE, "尚无备份", "建议创建第一份数据备份")
        try:
            pointer = json.loads(latest.read_text(encoding="utf-8-sig"))
            backup_id = str(pointer.get("backup_id") or "").strip()
            manifest_path = root / backup_id / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            verified = (
                backup_id
                and str(manifest.get("backup_id") or "") == backup_id
                and manifest.get("secret_values_included") is False
                and manifest.get("database_validation", {}).get(
                    "logical_table_counts_match"
                )
                is True
            )
            created_at = str(manifest.get("created_at_utc") or "")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return StateReadModel(ReadState.FAILED, "备份记录无法读取", "请联系管理员检查最近一次备份。")
        if not verified:
            return StateReadModel(ReadState.FAILED, "最近备份不可用", "请联系管理员重新检查或创建备份。")
        return StateReadModel(ReadState.READY, "最近备份可用", created_at or "备份检查通过")

    def _now(self) -> datetime:
        value = self.now_provider()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _visible(values: Iterable, page_size: int):
    items = tuple(values)
    return items[:page_size], len(items) > page_size


def _json_object(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _task_queue_entry(
    item: Task,
    *,
    product_labels: dict[str, str],
) -> _TaskQueueEntry:
    source_key, source_label, dispatch_lane = _task_queue_source(item)
    manual_review_detail = (
        "自动核对后仍无法确认执行结果，请人工处理"
        if "RECONCILE 仍无法确认" in str(item.result_message or "")
        else "复核完成前不会继续执行"
    )
    stage_key, stage_label, detail = {
        TaskStatus.PENDING: ("pending", "待发送", "等待选择并发送执行"),
        TaskStatus.RUNNING: ("running", "执行中", "执行端已领取，正在等待最新进度"),
        TaskStatus.MANUAL_REVIEW: (
            "attention",
            "等待人工复核",
            manual_review_detail,
        ),
        TaskStatus.FAILED: (
            "attention",
            "执行失败",
            "任务已停止，当前不会自动重试",
        ),
    }.get(item.task_status, ("attention", "需要处理", "任务当前不会继续执行"))
    return _TaskQueueEntry(
        source_key=source_key,
        source_label=source_label,
        stage_key=stage_key,
        stage_label=stage_label,
        title=ACTION_LABELS.get(item.action_type.value, "其他任务"),
        scope=product_labels.get(item.internal_sku or "", item.internal_sku or "全部商品"),
        platform_name=item.platform_name or "",
        detail=detail,
        occurred_at=item.updated_at or item.created_at,
        url=f"/management/task/{quote(item.task_id, safe='')}",
        dispatch_lane=dispatch_lane,
        task_id=item.task_id,
        cancellable=item.task_status in {TaskStatus.PENDING, TaskStatus.FAILED},
    )


def _task_queue_source(item: Task) -> tuple[str, str, int]:
    origin = item.origin_type.value
    if origin == "SYSTEM_EMERGENCY":
        return "emergency", "紧急保护", 1
    if origin == "MANUAL":
        lane = 0 if str(item.origin_ref_id or "").startswith("incident-review:") else 2
        label = "人工复核" if lane == 0 else "人工任务"
        return "manual", label, lane
    if origin == "AUTOMATION":
        return "automation", "自动任务", 2
    return "other", "历史任务", 2


def _active_queue_entries(
    queue_root: Path,
    *,
    now: datetime,
    get_task: Callable[[str], Task | None],
    product_labels: dict[str, str],
) -> tuple[list[_TaskQueueEntry], set[str], int]:
    entries: list[_TaskQueueEntry] = []
    represented_task_ids: set[str] = set()
    errors = 0
    locations = (
        ("queued", "排队中", queue_root / "inbox", "*.ready.json"),
        ("running", "执行中", queue_root / "working", "*.request.json"),
        ("results", "等待回收", queue_root / "results", "*.result.json"),
    )
    for stage_key, default_stage_label, directory, pattern in locations:
        if not directory.is_dir():
            errors += 1
            continue
        try:
            paths = sorted(directory.glob(pattern), key=_queue_artifact_sort_key)
        except OSError:
            errors += 1
            continue
        if len(paths) > 100:
            errors += 1
        for path in paths[:100]:
            try:
                payload = _read_queue_artifact(path)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                errors += 1
                entries.append(
                    _TaskQueueEntry(
                        source_key="other",
                        source_label="系统任务",
                        stage_key="attention",
                        stage_label="队列记录需要检查",
                        title="平台任务",
                        scope="范围暂不可用",
                        platform_name="",
                        detail="请联系管理员检查任务传递记录",
                        occurred_at=_queue_file_time(path, now),
                    )
                )
                continue
            phase = {}
            if stage_key == "running":
                attempt_id = str(payload.get("execution_attempt_id") or "").strip()
                if attempt_id:
                    try:
                        phase = _read_optional_queue_artifact(
                            directory / f"{attempt_id}.phase.json"
                        )
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        errors += 1
            stage_label = (
                _queue_phase_label(str(phase.get("phase") or ""))
                if stage_key == "running"
                else default_stage_label
            )
            occurred_at = _queue_artifact_time(payload, phase, path, now)
            task_ids = _queue_task_ids(payload)
            represented_task_ids.update(task_ids)
            resolved_tasks: list[Task] = []
            for task_id in task_ids:
                try:
                    task = get_task(task_id)
                except Exception:
                    errors += 1
                    task = None
                if task is not None:
                    resolved_tasks.append(task)
            if resolved_tasks:
                for task in resolved_tasks:
                    source_key, source_label, dispatch_lane = _task_queue_source(task)
                    entries.append(
                        _TaskQueueEntry(
                            source_key=source_key,
                            source_label=source_label,
                            stage_key=stage_key,
                            stage_label=stage_label,
                            title=ACTION_LABELS.get(task.action_type.value, "其他任务"),
                            scope=product_labels.get(
                                task.internal_sku or "",
                                task.internal_sku
                                or _queue_payload_scope(payload, product_labels),
                            ),
                            platform_name=task.platform_name
                            or str(payload.get("platform_name") or payload.get("platform") or ""),
                            detail=_queue_stage_detail(stage_key, stage_label),
                            occurred_at=occurred_at,
                            url=f"/management/task/{quote(task.task_id, safe='')}",
                            dispatch_lane=dispatch_lane,
                            task_id=task.task_id,
                            cancellable=False,
                        )
                    )
                continue
            automation_run_id = str(payload.get("automation_run_id") or "").strip()
            entries.append(
                _TaskQueueEntry(
                    source_key="automation" if automation_run_id or str(payload.get("execution_mode") or "").upper() == "READ_ONLY" else "other",
                    source_label="自动任务" if automation_run_id or str(payload.get("execution_mode") or "").upper() == "READ_ONLY" else "系统任务",
                    stage_key=stage_key,
                    stage_label=stage_label,
                    title=_queue_payload_title(payload),
                    scope=_queue_payload_scope(payload, product_labels),
                    platform_name=str(payload.get("platform_name") or payload.get("platform") or ""),
                    detail=_queue_stage_detail(stage_key, stage_label),
                    occurred_at=occurred_at,
                    url=(
                        f"/database/run/{quote(automation_run_id, safe='')}"
                        if automation_run_id
                        else ""
                    ),
                )
            )
    return entries, represented_task_ids, errors


def _read_queue_artifact(path: Path) -> dict[str, object]:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("queue artifact exceeds the read-only display limit")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise TypeError("queue artifact must be an object")
    return payload


def _read_optional_queue_artifact(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    return _read_queue_artifact(path)


def _queue_task_ids(payload: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            value = str(item.get("source_task_id") or item.get("task_id") or "").strip()
            if value:
                values.append(value)
    top_level = str(payload.get("task_id") or "").strip()
    if top_level:
        values.append(top_level)
    return tuple(dict.fromkeys(values))


def _queue_payload_title(payload: dict[str, object]) -> str:
    action = str(payload.get("action_type") or "").lower()
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    schema = str(payload.get("schema_version") or "").lower()
    if "order" in schema:
        return "订单扫描"
    if "listing" in schema or "product" in schema:
        return "商品状态扫描"
    if str(payload.get("execution_mode") or "").upper() == "READ_ONLY":
        return "平台数据读取"
    return "平台任务"


def _queue_payload_scope(
    payload: dict[str, object],
    product_labels: dict[str, str],
) -> str:
    items = payload.get("items")
    if isinstance(items, list) and items:
        sku_values = tuple(
            dict.fromkeys(
                str(item.get("internal_sku") or "").strip()
                for item in items
                if isinstance(item, dict) and str(item.get("internal_sku") or "").strip()
            )
        )
        if len(sku_values) == 1:
            return product_labels.get(sku_values[0], sku_values[0])
        if sku_values:
            return f"{len(sku_values)} 项商品"
    trade_date = str(
        payload.get("requested_platform_trade_date")
        or payload.get("platform_trade_date")
        or ""
    ).strip()
    return f"销售日 {trade_date}" if trade_date else "平台范围"


def _queue_phase_label(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        return "执行中"
    if normalized == "LOGIN_VERIFICATION_REQUIRED":
        return "等待登录验证"
    if "LOGIN" in normalized:
        return "登录处理中"
    if "NAVIGAT" in normalized or "OPEN" in normalized:
        return "正在打开业务页面"
    if "READ" in normalized or "SCAN" in normalized or "SCROLL" in normalized:
        return "正在读取平台数据"
    if "SUBMIT" in normalized or "CLICK" in normalized or "SAVE" in normalized:
        return "正在执行平台操作"
    if "RESULT" in normalized or "COMPLETE" in normalized:
        return "正在生成执行结果"
    return "执行中"


def _queue_stage_detail(stage_key: str, stage_label: str) -> str:
    if stage_key == "queued":
        return "等待执行端领取"
    if stage_key == "results":
        return "结果已返回，等待写入业务记录"
    if stage_label == "等待登录验证":
        return "需要在平台完成登录验证后继续"
    return "执行端正在处理"


def _queue_artifact_time(
    payload: dict[str, object],
    phase: dict[str, object],
    path: Path,
    now: datetime,
) -> datetime:
    for value in (
        phase.get("updated_at"),
        payload.get("updated_at"),
        payload.get("started_at"),
        payload.get("created_at"),
        payload.get("ended_at"),
    ):
        parsed = _queue_datetime(value)
        if parsed is not None:
            return parsed
    return _queue_file_time(path, now)


def _queue_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _queue_operation_time_label(value: datetime) -> str:
    local = value.astimezone(timezone(timedelta(hours=8)))
    return f"{local.month}月{local.day}日 {local:%H:%M}"


def _operation_resolution_control(
    *,
    operation_id: str,
    action_type: str,
    action_label: str,
    scope: str,
    operation_time: str,
) -> OperationResolutionControlReadModel:
    applied_label, not_applied_label = _operation_confirmation_labels(action_type)
    return OperationResolutionControlReadModel(
        operation_id=operation_id,
        action_label=action_label,
        scope=scope,
        prompt=(
            f"请根据你在平台上看到的实际情况，确认 {operation_time} 的"
            f"{action_label}结果。保存后会直接解除该商品的任务阻塞，不会再启动平台读取。"
        ),
        applied_label=applied_label,
        not_applied_label=not_applied_label,
    )


def _operation_confirmation_labels(action_type: str) -> tuple[str, str]:
    if action_type == "set_online":
        return "已确认商品已上架", "已确认商品未上架"
    if action_type == "set_offline":
        return "已确认商品已下架", "已确认商品仍在上架"
    return "已确认目标价格已生效", "已确认目标价格未生效"


def _mobile_operation_target_detail(row: dict[str, object]) -> str:
    action_type = str(row.get("action_type") or "").strip()
    target_price = str(row.get("target_price") or "").strip()
    target_inventory = row.get("target_inventory")
    if action_type == "set_online":
        target_parts = []
        if target_price:
            target_parts.append(f"售价 ¥{target_price}")
        if target_inventory is not None and str(target_inventory).strip():
            target_parts.append(f"平台库存 {target_inventory} 扎")
        suffix = "，目标为" + "、".join(target_parts) if target_parts else ""
        return "请查看平台当前状态并确认商品是否已上架" + suffix + "。"
    if action_type == "set_offline":
        return "请查看平台当前状态并确认商品是否已下架。"
    if target_price:
        return f"请查看平台当前价格并确认是否已调整为 ¥{target_price}。"
    return "请查看平台当前状态并确认本次操作是否已生效。"


def _queue_file_time(path: Path, now: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return now


def _queue_artifact_sort_key(path: Path) -> tuple[float, str]:
    try:
        return path.stat().st_mtime, path.name
    except OSError:
        return 0.0, path.name


def _task_queue_sort_key(item: _TaskQueueEntry) -> tuple[object, ...]:
    stage_rank = {
        "running": 0,
        "results": 1,
        "queued": 2,
        "pending": 3,
        "attention": 4,
    }.get(item.stage_key, 5)
    moment = _sortable_datetime(item.occurred_at)
    if item.stage_key == "attention":
        return item.dispatch_lane, stage_rank, -moment.timestamp(), item.title, item.scope
    return item.dispatch_lane, stage_rank, moment.timestamp(), item.title, item.scope


def _queue_wait_label(now: datetime, occurred_at: datetime) -> str:
    elapsed = max(timedelta(0), _age(now, occurred_at))
    seconds = int(elapsed.total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 86400} 天"


def _rows_state(rows: tuple[tuple[str, ...], ...], empty_detail: str) -> StateReadModel:
    if rows:
        return StateReadModel(ReadState.READY, "可用", f"当前显示 {len(rows)} 条")
    return StateReadModel(ReadState.EMPTY, "没有记录", empty_detail)


def _snapshot_rows_state(values: Iterable) -> StateReadModel:
    items = tuple(values)
    if not items:
        return StateReadModel(ReadState.EMPTY, "销售数据待更新", "尚未取得当前销售日的完整订单数据")
    complete = [item for item in items if item.scope_complete and item.end_marker_verified]
    if not complete:
        return StateReadModel(ReadState.INCOMPLETE, "订单采集未完成", "尚未读完当前销售日的全部订单")
    if any(not item.scope_complete or not item.end_marker_verified for item in items):
        return StateReadModel(ReadState.INCOMPLETE, "部分订单数据可用", "仍有部分订单数据等待补充")
    if all(len(item.items) == 0 for item in complete):
        return StateReadModel(ReadState.TRUSTWORTHY_ZERO, "已确认暂无订单", "当前销售日的订单已全部采集")
    return StateReadModel(ReadState.READY, "订单数据完整", "当前销售日的订单已全部采集")


def _unavailable_table(dataset: str, title: str, detail: str) -> TableReadModel:
    return TableReadModel(
        dataset=dataset,
        title=title,
        columns=(),
        rows=(),
        state=StateReadModel(ReadState.UNAVAILABLE, "数据暂不可用", detail),
    )


def _state_table(
    dataset: str,
    title: str,
    state: StateReadModel,
) -> TableReadModel:
    return TableReadModel(
        dataset=dataset,
        title=title,
        columns=(),
        rows=(),
        state=state,
    )


def _failed_table(
    dataset: str,
    title: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> TableReadModel:
    return TableReadModel(
        dataset=dataset,
        title=title,
        columns=(),
        rows=(),
        state=StateReadModel(ReadState.FAILED, "读取失败", "请稍后重试；如持续失败，请联系管理员。"),
        page_size=page_size,
    )


def _qty(value: int | None) -> str:
    return "—" if value is None else f"{value} 扎"


def _signed_qty(value: int) -> str:
    return f"{value:+d} 扎"


def _inventory_transaction_label(value: str) -> str:
    return {
        "BOOTSTRAP": "期初库存",
        "SKU_INITIALIZATION": "新增商品初始化",
        "MANUAL_INBOUND": "新花入库",
        "MANUAL_ADJUSTMENT": "人工修正",
        "SALES_DEDUCTION": "销售扣减",
        "SALES_RESTORE": "销售恢复",
        "RECONCILIATION": "对账修正",
    }.get(value, "库存变动")


def _task_action_label(value: str) -> str:
    return {
        "update_price": "改价",
        "set_online": "上架",
        "set_offline": "下架",
    }.get(str(value), str(value))


def _inventory_product_label(product: Product | None) -> str:
    if product is None:
        return "商品资料缺失"
    return " · ".join(
        item
        for item in (product.product_name, product.grade, product.stem_length)
        if item
    )


def _grade_inventory_summary(values: tuple[tuple[str, int], ...]) -> str:
    return " / ".join(f"{grade} {_qty(qty)}" for grade, qty in values)


def _inventory_margin_label(current_qty: int, threshold_qty: int, *, enabled: bool) -> str:
    if not enabled:
        return "预警未启用"
    gap = current_qty - threshold_qty
    if gap > 0:
        return f"高于 {gap} 扎"
    if gap == 0:
        return "已到安全余量"
    return f"低于 {-gap} 扎"


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"¥{value.quantize(Decimal('0.01'))}"


def _datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is not None and value.utcoffset() is not None:
        value = value.astimezone(timezone(timedelta(hours=8)))
    return value.strftime("%Y-%m-%d %H:%M")


def _age(now: datetime, value: datetime) -> timedelta:
    reference = value
    if reference.tzinfo is None or reference.utcoffset() is None:
        reference = reference.replace(tzinfo=now.tzinfo or timezone.utc)
    return now - reference.astimezone(now.tzinfo or timezone.utc)


def _sortable_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _joined_labels(values: Iterable[str]) -> str:
    return " / ".join(dict.fromkeys(value for value in values if value)) or "—"


def _scope_label(scope_type: str, scope_key: str) -> str:
    labels = {
        "PLATFORM": "平台合计",
        "VARIETY": "品种",
        "GRADE": "等级",
        "SKU": "商品",
        "TIME_BUCKET": "销售时段",
    }
    label = labels.get(scope_type, "其他范围")
    return f"{label} · {scope_key}" if scope_key else label


def _trade_day_status_label(value: str) -> str:
    return {"OPEN": "营业中", "CLOSED": "已截单"}.get(value, "状态未知")


def _snapshot_quality(item) -> str:
    if item.scope_complete and item.end_marker_verified:
        return "完整" if item.items else "已确认无订单"
    return "采集未完成"


def _listing_status_label(value: str) -> str:
    return {"online": "上架中", "offline": "已下架"}.get(str(value).lower(), "状态未知")


def _automation_job_label(value: str) -> str:
    return AUTOMATION_JOB_LABELS.get(str(value).upper(), "其他自动化方案")


def _automation_status_label(value: str) -> str:
    return {
        "QUEUED": "等待运行",
        "RUNNING": "运行中",
        "SUCCESS": "成功",
        "PARTIAL": "部分完成",
        "FAILED": "失败",
        "MISSED": "已错过",
        "CANCELLED": "已取消",
    }.get(value, "状态未知")


def _run_state(value: str) -> ReadState:
    if value == "SUCCESS":
        return ReadState.READY
    if value in {"FAILED", "MISSED"}:
        return ReadState.FAILED
    if value == "PARTIAL":
        return ReadState.INCOMPLETE
    return ReadState.EMPTY


def _execution_status(value: bool | None) -> str:
    return "成功" if value is True else ("失败" if value is False else "结果未知")


def _incident_status_label(value: str) -> str:
    return {
        "OPEN": "待处理",
        "WAITING_HUMAN": "等待人工",
        "AUTO_PROTECTING": "保护处理中",
        "RESOLVED": "已解决",
        "CLOSED": "已关闭",
    }.get(value, "状态未知")


def _incident_detail(severity: str, count: int) -> str:
    level = {
        "S0": "记录",
        "S1": "提醒",
        "S2": "需要关注",
        "S3": "高风险",
        "S4": "紧急",
    }.get(str(severity).upper(), "需要关注")
    return f"{level} · 已出现 {count} 次"


def _notification_type_label(value: str) -> str:
    return NOTIFICATION_TYPE_TITLES.get(str(value), "其他通知")


def _notification_channel_label(value: str) -> str:
    return {
        "feishu": "飞书",
        "mock": "模拟通知",
        "fake": "模拟通知",
        "scripted": "模拟通知",
        "unconfigured": "未配置",
    }.get(str(value).lower(), "其他通道")


def _executor_label(value: str) -> str:
    normalized = str(value).lower()
    if "shadowbot" in normalized or "影刀" in normalized:
        return "影刀执行端"
    if "mock" in normalized or "fake" in normalized:
        return "模拟执行端"
    return "执行端"


def _notification_status_label(value: str) -> str:
    return {
        "PENDING": "待发送",
        "LEASED": "已领取",
        "SENDING": "发送中",
        "RETRY_WAIT": "等待重试",
        "SENT": "已发送",
        "UNKNOWN_DELIVERY": "发送结果未知",
        "FAILED": "发送失败",
        "EXPIRED": "已过期",
        "CANCELLED": "已取消",
    }.get(value, "状态未知")


def _origin_label(value: str) -> str:
    return {
        "MANUAL": "人工创建",
        "AUTOMATION": "自动化生成",
        "SYSTEM_EMERGENCY": "系统紧急保护",
        "LEGACY": "历史来源",
    }.get(value, "来源未知")


def _review_type_label(value: str) -> str:
    labels = {
        **REVIEW_TYPE_DISPLAY_LABELS,
        "incident_emergency": "紧急情况复核",
        "inventory_shortage": "库存偏低复核",
        "mapping": "商品映射复核",
        "execution_failure": "执行失败复核",
    }
    return labels.get(str(value).lower(), "人工确认")


def _mobile_review_title(item: ReviewTask) -> str:
    if (
        item.review_type == "emergency_protection"
        and str(item.review_payload.get("severity") or "").upper() == "S4"
    ):
        return "极端低价处理"
    return _review_type_label(item.review_type)


def _review_scope(item: ReviewTask) -> str:
    parts = [item.internal_sku or item.scope_key or "全局"]
    if item.platform_name:
        parts.append(item.platform_name)
    return " · ".join(parts)


def _task_state(item: Task) -> StateReadModel:
    value = item.task_status.value
    if value == "success":
        state = ReadState.READY
    elif value == "failed":
        state = ReadState.FAILED
    elif value in {"cancelled", "expired", "skipped"}:
        state = ReadState.EMPTY
    else:
        state = ReadState.INCOMPLETE
    return StateReadModel(state, TASK_STATUS_LABELS.get(value, "状态未知"), _task_result_detail(item))


def _task_result_detail(item: Task) -> str:
    value = item.task_status.value
    if value == "success":
        return "任务已完成"
    if value == "failed":
        return "任务执行失败，技术原因请由管理员在系统诊断中查看。"
    if value in {"cancelled", "expired", "skipped"}:
        return TASK_STATUS_LABELS.get(value, "任务未执行")
    return "任务正在等待或处理中"


def _automation_result_detail(status: str) -> str:
    if status == "SUCCESS":
        return "自动化运行已完成"
    if status in {"FAILED", "MISSED"}:
        return "自动化运行未完成，技术原因请由管理员在系统诊断中查看。"
    if status == "PARTIAL":
        return "自动化运行部分完成"
    return "自动化运行状态已记录"


def _execution_result_detail(success_flag: bool | None) -> str:
    if success_flag is True:
        return "执行成功"
    if success_flag is False:
        return "执行失败，技术原因请由管理员在系统诊断中查看。"
    return "执行结果尚未确认"


def _inventory_error_state(error_code: str) -> StateReadModel | None:
    messages = {
        "INVALID_ADJUSTMENT": "调整值、来源或调整后库存不符合要求，请检查后重试。",
        "INVENTORY_CONFLICT": "库存已变化，请刷新页面后重新确认。",
        "INVENTORY_UNAVAILABLE": "数据库库存服务暂不可用，本次没有修改库存。",
        "INVENTORY_WRITE_FAILED": "库存流水回读失败，本次结果未确认，请联系管理员。",
    }
    message = messages.get(str(error_code).strip().upper())
    if message is None:
        return None
    return StateReadModel(ReadState.FAILED, "库存调整未完成", message)


def _detail_back_link(kind: str, context: dict[str, str]) -> tuple[str, str]:
    source = str(context.get("source") or "").strip()
    allowed_query = {
        key: str(context.get(key) or "").strip()
        for key in ("dataset", "trade_date", "platform")
        if str(context.get(key) or "").strip()
    }
    if source == "today":
        return "/today", "返回今日"
    if source == "sales-analysis":
        return (
            "/database/sales-analysis"
            + ("?" + urlencode(allowed_query) if allowed_query else ""),
            "返回销售分析",
        )
    if source == "project":
        return (
            "/database/project"
            + ("?" + urlencode(allowed_query) if allowed_query else ""),
            "返回项目运行数据",
        )
    if source == "business":
        return (
            "/database" + ("?" + urlencode(allowed_query) if allowed_query else ""),
            "返回业务数据",
        )
    if kind in {"task", "review"}:
        return "/management", "返回业务管理"
    return "/database", "返回数据库"
