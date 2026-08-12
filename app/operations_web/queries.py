"""Task 13.5-7C 运营事实查询。

本模块只组合既有权威 Repository 和工作簿读取器，不创建 Schema、不修复数据，也不
调用 Queue、Worker、Importer 或平台 Adapter。
"""

from __future__ import annotations

import json
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
    TaskStatus,
)
from app.models import Product, ReviewTask, Task
from app.operations_web.composition import OperationsWebPaths
from app.operations_web.read_models import (
    ComponentReadModel,
    DatabaseReadModel,
    DetailFieldReadModel,
    DetailReadModel,
    ManagementReadModel,
    MetricReadModel,
    MobileReviewReadModel,
    NotificationDrawerReadModel,
    NotificationItemReadModel,
    ReadState,
    StateReadModel,
    SystemReadModel,
    TableReadModel,
    TodayReadModel,
)
from app.repositories.automation_repository import AutomationRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.review_policy import allowed_review_statuses, review_action_label
from app.services.operational_time import OperationalTimeContext, OperationalTimeService
from app.services.authoritative_inventory import InventoryProvider
from app.services.notification_outbox import (
    NOTIFICATION_TYPE_TITLES,
    REVIEW_TYPE_LABELS,
)
from app.services.runtime import ReviewTokenService


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 25

QUALITY_LABELS = {
    "ORDER_COMPLETE": "完整订单事实",
    "ORDER_PARTIAL": "部分订单事实",
    "SCAN_ESTIMATED_HIGH": "高可信扫描估算",
    "SCAN_ESTIMATED_MEDIUM": "中等可信扫描估算",
    "SCAN_ESTIMATED_LOW": "低可信扫描估算",
    "UNAVAILABLE": "不可用",
}
SOURCE_LABELS = {
    "ORDER_OBSERVED": "订单事实",
    "SCAN_ESTIMATED": "扫描估算",
}
SUMMARY_STATUS_LABELS = {
    "PROVISIONAL": "初步结算",
    "OBSERVED": "已观察",
    "RECONCILED": "已对账",
    "FINAL": "最终结算",
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
    "LISTING_STATUS_SCAN": "商品状态扫描",
    "ORDER_SCAN": "订单扫描",
    "INCIDENT_NOTIFICATION_MAINTENANCE": "异常通知维护",
    "DAILY_TASK_GENERATION": "每日任务生成",
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
        self.inventory_provider = InventoryProvider(self.inventory)
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
                        detail="Web 只报告当前状态，不会在读取时修复数据库。",
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
                        "来自当前真实库存权威；仅汇总允许销售的商品",
                        inventory_state,
                    ),
                ),
                products=_state_table(
                    "today-products",
                    "品种销售与库存",
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
                "来自当前真实库存权威；仅汇总允许销售的商品",
                inventory_state,
            ),
        )
        product_table = self._today_product_table(
            context.platform_trade_date,
            products,
            products_error,
        )
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
            "销售分析只展示既有确定性事实；Agent 预测、经营建议和人工质量评价尚未接入。"
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
        inventory_receipt = None
        inventory_error = _inventory_error_state(inventory_error_code)
        try:
            authority = self.inventory.get_authority_state()
            if authority.authority_mode != "DB_AUTHORITY":
                inventory_state = StateReadModel(
                    ReadState.UNAVAILABLE,
                    "库存尚未切换",
                    "完成受控 bootstrap 与回读前，Web 不接受数据库库存调整。",
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
                    "数据库库存为唯一权威",
                    "人工调整会同时写入当前余额和不可变流水。",
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
                "页面未修改库存，请联系管理员检查 Runtime Schema。",
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
        )

    def system(self) -> SystemReadModel:
        now = self._now()
        checked_at = _datetime(now)
        components: list[ComponentReadModel] = []
        try:
            schema = self.runtime.check_schema_health()
            operational = self.runtime.check_operational_health()
            if schema.ok and operational.ok:
                state = StateReadModel(ReadState.READY, "正常", "只读连接与 Schema 检查通过")
            else:
                state = StateReadModel(
                    ReadState.UNAVAILABLE,
                    "需要维护",
                    "读取路径不会自动初始化、迁移或修复数据库",
                )
        except Exception:
            state = StateReadModel(
                ReadState.FAILED,
                "检查失败",
                "数据库状态暂时无法读取，请联系管理员检查。",
            )
        components.append(ComponentReadModel("Runtime 数据库", state, checked_at))

        workbook_paths = (
            self.paths.products_workbook,
            self.paths.price_rules_workbook,
            self.paths.listing_rules_workbook,
        )
        missing = sum(1 for item in workbook_paths if not item.is_file())
        workbook_state = (
            StateReadModel(ReadState.READY, "可读取", "3 份固定工作簿均存在")
            if missing == 0
            else StateReadModel(
                ReadState.UNAVAILABLE,
                "资料不完整",
                f"{missing} 份固定工作簿缺失",
            )
        )
        components.append(ComponentReadModel("业务工作簿", workbook_state, checked_at))

        queue_state = self._queue_state()
        components.append(ComponentReadModel("执行队列", queue_state, checked_at))
        worker_state = self._worker_state(now)
        components.append(ComponentReadModel("ShadowBot Worker", worker_state, checked_at))

        states = {item.state.state for item in components}
        if ReadState.FAILED in states:
            overall = StateReadModel(ReadState.FAILED, "部分组件检查失败", "请进入后续维护流程排查")
        elif ReadState.UNAVAILABLE in states or ReadState.STALE in states:
            overall = StateReadModel(ReadState.INCOMPLETE, "部分组件需要处理", "状态页只报告事实，不执行恢复")
        else:
            overall = StateReadModel(ReadState.READY, "运行状态正常", "后台组件生命周期与 Web 相互独立")
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
                subtitle="权威事实读取失败",
                state=StateReadModel(
                    ReadState.FAILED,
                    "读取失败",
                    "页面未修改任何数据，请稍后重试或联系管理员。",
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
                    "读取失败且未修改复核状态，请稍后重试。",
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
            return MobileReviewReadModel(
                state=StateReadModel(
                    ReadState.READY,
                    "等待处理",
                    "当前页面只读展示；处理按钮将在后续接入既有安全处置流程。",
                ),
                review_title=_review_type_label(review.review_type),
                reason=review.reason or "需要人工确认",
                scope=_review_scope(review),
                deadline=_datetime(review.required_by),
                allowed_actions=tuple(actions),
                http_status="200 OK",
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
                    "该复核已有正式结果，重复打开不会再次执行操作。",
                ),
                review_title=_review_type_label(review.review_type),
                reason=review.resolution_note or review.reason,
                scope=_review_scope(review),
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
                "当前映射事实用于采集与日结，但尚无独立只读目录；不从观察行反推主数据。",
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
            return _failed_table("products", "商品与真实库存")
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
            title="商品与真实库存",
            columns=("商品", "等级", "规格", "真实库存", "销售状态"),
            rows=rows,
            row_urls=urls,
            page=page,
            has_next=has_next,
            base_path="/database",
            query={"dataset": "products"},
            state=_rows_state(rows, "产品工作簿中没有商品"),
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
                "观察时间",
                "上架状态",
            ),
            rows=rows,
            page=page,
            has_next=has_next,
            base_path="/database",
            query={"dataset": "prices", "platform": platform_name},
            state=_rows_state(rows, "当前没有平台价格观察"),
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
            columns=("交易日", "平台", "日状态", "订单数", "销量", "成交金额", "质量", "观察时间"),
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
                _review_scope(item),
                REVIEW_STATUS_LABELS.get(item.review_status.value, "状态未知"),
                _datetime(item.required_by),
                item.reason or "—",
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
            ("交易日", "PRA 平台交易日", "18:00 至次日 18:00"),
            ("卖家作业日", "PRA 卖家作业日", "20:00 至次日 20:00"),
            ("销量", "页面观察到的订单数量或合格扫描估算", "订单事实与估算不得相加"),
            ("成交金额", "页面展示成交金额的合计", "不代表卖家实收、退款净额或财务到账"),
            ("数据库库存", "目前还有多少花可以销售", "当前来自产品库存资料；切换库存台账后以数据库为准"),
            ("平台库存", "客户在该平台最多可购买的数量", "不等于数据库库存"),
            ("OPEN", "当前开放交易日快照", "不能当作完整闭市事实或 FINAL"),
            ("FINAL", "通过质量、覆盖和对账门禁的最终结算", "不会仅因到达 20:00 自动产生"),
        )
        return TableReadModel(
            dataset="fields",
            title="字段说明",
            columns=("名称", "定义", "边界"),
            rows=values,
            state=StateReadModel(ReadState.READY, "可用", "使用当前冻结业务口径"),
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
        rows: list[tuple[str, ...]] = []
        urls: list[str] = []
        for product in products[:DEFAULT_PAGE_SIZE]:
            summary = by_sku.get(product.internal_sku)
            sold = summary.sold_qty if summary else None
            amount = summary.transaction_amount_total if summary else None
            average = (
                amount / Decimal(sold)
                if amount is not None and sold not in (None, 0)
                else (Decimal("0") if amount == 0 and sold == 0 else None)
            )
            rows.append(
                (
                    product.product_name,
                    product.grade,
                    _qty(sold),
                    _money(average),
                    _money(amount),
                    _qty(product.current_stock),
                    QUALITY_LABELS.get(summary.quality_level.value, "质量未知")
                    if summary
                    else "尚无销售事实",
                )
            )
            urls.append(
                f"/database/product/{quote(product.internal_sku, safe='')}?source=today"
            )
        return TableReadModel(
            dataset="today-products",
            title="品种销售与库存",
            columns=("商品", "等级", "今日已售", "成交均价", "销售额", "真实库存", "数据状态"),
            rows=tuple(rows),
            row_urls=tuple(urls),
            state=_rows_state(tuple(rows), "产品工作簿中没有商品"),
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
            DetailFieldReadModel("真实库存", _qty(product.current_stock)),
            DetailFieldReadModel("基础成本", _money(product.base_cost)),
            DetailFieldReadModel("销售状态", "可销售" if product.sale_enabled else "停止销售"),
            DetailFieldReadModel("库存来源", "当前真实库存权威"),
        ]
        related = tuple(
            (
                f"{item.platform_name} · {_money(item.current_price)} · {_listing_status_label(item.online_status)}",
                "/database?" + urlencode({"dataset": "prices", "platform": item.platform_name}),
            )
            for item in listings
        )
        return DetailReadModel(
            title=f"{product.product_name} · {product.grade}",
            subtitle="商品与库存详情",
            state=StateReadModel(ReadState.READY, "可用", "只读展示"),
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
            title=f"{snapshot.platform_trade_date.isoformat()} 销售观察",
            subtitle=f"{snapshot.platform_name} · {_trade_day_status_label(snapshot.trade_day_status)}",
            state=state,
            fields=(
                DetailFieldReadModel("订单数", str(len(snapshot.items))),
                DetailFieldReadModel("销量", _qty(qty)),
                DetailFieldReadModel("成交金额", _money(amount)),
                DetailFieldReadModel("范围完整", "是" if snapshot.scope_complete else "否"),
                DetailFieldReadModel("尾部已确认", "是" if snapshot.end_marker_verified else "否"),
                DetailFieldReadModel("观察时间", _datetime(snapshot.scan_completed_at)),
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
            version_identity = "当前权威版本"
        else:
            current_version = (
                f"当前权威版本为 v{current.version_no}。"
                if current is not None
                else "当前权威版本暂不可读。"
            )
            state = StateReadModel(
                ReadState.STALE,
                "历史版本 · 已被取代",
                current_version,
            )
            version_identity = "历史版本，已被取代"
        return DetailReadModel(
            title=f"{summary.platform_trade_date.isoformat()} 交易日结算",
            subtitle=f"{summary.platform_name} · {_scope_label(summary.scope_type, summary.scope_key)}",
            state=state,
            fields=(
                DetailFieldReadModel("版本", f"v{summary.version_no}"),
                DetailFieldReadModel("版本身份", version_identity),
                DetailFieldReadModel(
                    "版本关系",
                    "基于上一版本重新结算"
                    if summary.supersedes_summary_id
                    else "首个版本",
                ),
                DetailFieldReadModel("销量", _qty(summary.sold_qty)),
                DetailFieldReadModel("成交金额", _money(summary.transaction_amount_total)),
                DetailFieldReadModel("事实来源", SOURCE_LABELS.get(summary.fact_source.value, "来源未知") if summary.fact_source else "不可用"),
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
        return DetailReadModel("任务详情", "正式任务事实", state, fields)

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
                "只读展示正式复核结果",
            ),
            fields=(
                DetailFieldReadModel("范围", _review_scope(item)),
                DetailFieldReadModel("原因", item.reason or "—"),
                DetailFieldReadModel("处理期限", _datetime(item.required_by)),
                DetailFieldReadModel("处理结果", item.resolution_note or "—"),
                DetailFieldReadModel("处理时间", _datetime(item.resolved_at)),
            ),
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
            detail=item.reason or "需要人工确认",
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
            return StateReadModel(ReadState.FAILED, "销售事实读取失败", "未将失败显示为 0")
        if not summaries:
            return StateReadModel(ReadState.UNAVAILABLE, "尚无可用销售事实", "缺失不显示为 0")
        available = [
            item
            for item in summaries
            if item.quality_level is not DataQualityLevel.UNAVAILABLE
            and item.sold_qty is not None
        ]
        if not available:
            return StateReadModel(ReadState.UNAVAILABLE, "销售事实不可用", "当前没有合格订单或扫描估算")
        if len(available) != len(summaries) or any(
            item.quality_level
            in {
                DataQualityLevel.ORDER_PARTIAL,
                DataQualityLevel.SCAN_ESTIMATED_MEDIUM,
                DataQualityLevel.SCAN_ESTIMATED_LOW,
            }
            for item in available
        ):
            return StateReadModel(ReadState.INCOMPLETE, "部分销售事实可用", "页面保留质量差异，不与完整事实混算")
        latest = max(item.updated_at for item in available)
        if sum(item.sold_qty or 0 for item in available) == 0:
            return StateReadModel(
                ReadState.TRUSTWORTHY_ZERO,
                "当前确认无销量",
                f"来源完整，0 是可信事实；最近更新于 {_datetime(latest)}",
            )
        quality = _joined_labels(
            QUALITY_LABELS.get(item.quality_level.value, "质量未知")
            for item in available
        )
        return StateReadModel(
            ReadState.READY,
            "销售事实可用",
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
            products = load_products(self.paths.products_workbook)
            return self.inventory_provider.hydrate_products(products), ""
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
                    "交易日时间策略读取失败",
                    "未使用代码默认时间推断交易日，也未查询当前交易日事实。",
                ),
            )
        if not policies:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.UNAVAILABLE,
                    "交易日时间策略不可用",
                    "Runtime 中没有版本化时间策略，未推断默认交易日。",
                ),
            )
        try:
            service = OperationalTimeService(policies=policies)
        except ValueError:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.FAILED,
                    "交易日时间策略配置无效",
                    "Runtime 时间策略无法建立唯一版本序列，未查询当前交易日事实。",
                ),
            )
        try:
            context = service.classify(now)
        except ValueError:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.UNAVAILABLE,
                    "当前没有唯一有效的交易日时间策略",
                    "未使用代码默认时间推断交易日，也未查询当前交易日事实。",
                ),
            )
        except Exception:
            return _TimeContextReadResult(
                context=None,
                state=StateReadModel(
                    ReadState.FAILED,
                    "交易日时间策略计算失败",
                    "未使用代码默认时间推断交易日，也未查询当前交易日事实。",
                ),
            )
        return _TimeContextReadResult(
            context=context,
            state=StateReadModel(
                ReadState.READY,
                "交易日时间策略可用",
                f"使用版本 {context.time_policy_version}",
            ),
        )

    def _queue_state(self) -> StateReadModel:
        root = self.paths.queue_root
        if not root.is_dir():
            return StateReadModel(ReadState.UNAVAILABLE, "队列目录不存在", "Web 不会自动创建队列")
        counts: dict[str, int] = {}
        try:
            for name in ("inbox", "working", "results", "archive"):
                path = root / name
                if not path.is_dir():
                    return StateReadModel(ReadState.UNAVAILABLE, "队列结构不完整", f"缺少 {name} 目录")
                counts[name] = sum(1 for item in path.iterdir() if item.is_file())
        except OSError:
            return StateReadModel(ReadState.FAILED, "队列检查失败", "请联系管理员检查队列目录。")
        active = counts["inbox"] + counts["working"] + counts["results"]
        return StateReadModel(ReadState.READY, "可读取", f"当前待处理/处理中/待导入共 {active} 项")

    def _worker_state(self, now: datetime) -> StateReadModel:
        heartbeat = self.paths.queue_root / "heartbeat.json"
        if not heartbeat.is_file():
            return StateReadModel(ReadState.UNAVAILABLE, "没有 Worker 心跳", "状态页不会启动 Worker")
        try:
            payload = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            status = str(payload.get("status") or "UNKNOWN").upper()
            updated_raw = payload.get("updated_at") or payload.get("heartbeat_at")
            updated = (
                datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
                if updated_raw
                else None
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return StateReadModel(ReadState.FAILED, "心跳无法读取", "请按既有 Worker 恢复程序处理。")
        if updated is None or _age(now, updated) > timedelta(seconds=30):
            return StateReadModel(ReadState.STALE, "心跳已过期", "需要按既有 Worker 恢复程序处理")
        if status == "RUNNING":
            return StateReadModel(ReadState.READY, "运行中", f"最近心跳 {_datetime(updated)}")
        if status == "STOPPED":
            return StateReadModel(ReadState.UNAVAILABLE, "已停止", f"最近心跳 {_datetime(updated)}")
        return StateReadModel(ReadState.INCOMPLETE, "状态未知", f"最近心跳 {_datetime(updated)}")

    def _now(self) -> datetime:
        value = self.now_provider()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _visible(values: Iterable, page_size: int):
    items = tuple(values)
    return items[:page_size], len(items) > page_size


def _rows_state(rows: tuple[tuple[str, ...], ...], empty_detail: str) -> StateReadModel:
    if rows:
        return StateReadModel(ReadState.READY, "可用", f"本页 {len(rows)} 条")
    return StateReadModel(ReadState.EMPTY, "没有记录", empty_detail)


def _snapshot_rows_state(values: Iterable) -> StateReadModel:
    items = tuple(values)
    if not items:
        return StateReadModel(ReadState.EMPTY, "没有销售观察", "没有把空列表解释为零销量")
    complete = [item for item in items if item.scope_complete and item.end_marker_verified]
    if not complete:
        return StateReadModel(ReadState.INCOMPLETE, "观察不完整", "滚动范围或尾部确认未通过")
    if any(not item.scope_complete or not item.end_marker_verified for item in items):
        return StateReadModel(ReadState.INCOMPLETE, "部分观察可用", "完整与不完整批次分开保留")
    if all(len(item.items) == 0 for item in complete):
        return StateReadModel(ReadState.TRUSTWORTHY_ZERO, "可信空页", "范围完整且尾部已确认")
    return StateReadModel(ReadState.READY, "观察完整", "订单页面范围与尾部已确认")


def _unavailable_table(dataset: str, title: str, detail: str) -> TableReadModel:
    return TableReadModel(
        dataset=dataset,
        title=title,
        columns=(),
        rows=(),
        state=StateReadModel(ReadState.UNAVAILABLE, "尚未建立权威数据源", detail),
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
        state=StateReadModel(ReadState.FAILED, "读取失败", "页面未修改任何数据，请稍后重试或联系管理员。"),
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


def _inventory_product_label(product: Product | None) -> str:
    if product is None:
        return "商品资料缺失"
    return " · ".join(
        item
        for item in (product.product_name, product.grade, product.stem_length)
        if item
    )


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
    return {"OPEN": "开放快照", "CLOSED": "已截单"}.get(value, "状态未知")


def _snapshot_quality(item) -> str:
    if item.scope_complete and item.end_marker_verified:
        return "完整" if item.items else "可信空页"
    return "不完整"


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
    return f"{severity} · 已出现 {count} 次"


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
        **REVIEW_TYPE_LABELS,
        "incident_emergency": "紧急情况复核",
        "inventory_shortage": "库存偏低复核",
        "mapping": "商品映射复核",
        "execution_failure": "执行失败复核",
    }
    return labels.get(str(value).lower(), "人工复核")


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
