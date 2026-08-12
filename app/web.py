from __future__ import annotations

import io
import ipaddress
import json
import os
import re
import sqlite3
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hmac import compare_digest
from html import escape
from http.cookies import SimpleCookie
from pathlib import Path
from secrets import token_urlsafe
from socketserver import ThreadingMixIn
from threading import Lock
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from app.enums import (
    NotificationSendStatus,
    ReviewTaskStatus,
    TaskActionType,
    TaskStatus,
)
from app.exceptions import (
    MobileReviewErrorCode,
    MobileReviewTransactionError,
    TableValidationError,
    ValidationError,
)
from app.field_labels import FIELD_LABELS, TABLE_LABELS
from app.listing_status_policy import has_current_platform_stock
from app.models import ListingStatus, NotificationLog
from app.path_policy import PathAccessPolicy, PathPolicyError
from app.repositories.mock_platform_repository import (
    DEFAULT_MOCK_PLATFORM_DB,
    MockPlatformRepository,
)
from app.repositories.sqlite_connection import (
    SQLiteConnectionError,
    SQLiteConnectionFactory,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.workbook_repository import (
    get_table_headers,
    load_table_records,
    save_table_records,
)
from app.review_policy import (
    allowed_review_statuses,
    is_execution_failure_review,
    review_action_label,
)
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION
from app.services.capacity_plan_input import (
    CapacityPlanInputError,
    apply_capacity_plan_edit,
    apply_capacity_plan_input,
    computed_capacity_from_row,
    format_capacity_number,
    load_capacity_plan_input_rows,
    persist_capacity_plan_rows,
    validate_capacity_plan_form,
)
from app.services.capacity_plan_input import (
    active_display as capacity_active_display,
)
from app.services.cold_storage_input import (
    ColdStorageInputError,
    apply_cold_storage_edit,
    apply_cold_storage_input,
    computed_projected_occupied_from_row,
    computed_remaining_capacity_from_row,
    format_cold_storage_number,
    load_cold_storage_input_rows,
    persist_cold_storage_rows,
    validate_cold_storage_form,
)
from app.services.cold_storage_input import (
    active_display as cold_storage_active_display,
)
from app.services.listing_rule_input import (
    LISTING_STRATEGY_OPTIONS,
    ListingRuleInputError,
    apply_listing_rule_edit,
    apply_listing_rule_input,
    format_listing_rule_number,
    format_listing_rule_scope,
    load_listing_rule_input_rows,
    persist_listing_rule_rows,
    validate_listing_rule_form,
)
from app.services.listing_rule_input import (
    active_display as listing_active_display,
)
from app.services.platform_mapping_input import (
    PlatformMappingInputError,
    apply_platform_input,
    ensure_platform_mappings_workbook,
    load_platform_mapping_rows,
    persist_platform_mapping_rows,
    platform_options_from_rows,
)
from app.services.price_rule_input import (
    PRICING_METHOD_OPTIONS,
    ROUNDING_RULE_OPTIONS,
    PriceRuleInputError,
    active_display,
    apply_price_rule_edit,
    apply_price_rule_input,
    format_price_rule_number,
    load_price_rule_input_rows,
    persist_price_rule_rows,
    validate_price_rule_form,
)
from app.services.product_inventory_input import (
    FOLLOW_GRADE_VALUE,
    GRADE_OPTIONS,
    GRADE_STEM_LENGTH_MAP,
    PLATFORM_OPTIONS,
    STEM_LENGTH_OPTIONS,
    UNIT_OPTIONS,
    ProductInventoryInputError,
    apply_inventory_input,
    apply_product_edit,
    extract_variety_options,
    format_product_number,
    load_product_input_rows,
    persist_product_rows,
    sale_enabled_display,
    validate_inventory_form,
    validate_product_edit_form,
)
from app.services.runtime import (
    DEFAULT_RUNTIME_DB,
    NotificationLogService,
    NotificationSenderFactory,
)
from app.services.security import LOGIN_RATE_LIMITER, record_security_event
from app.services.shadowbot_executor import (
    ShadowBotExecutor,
    build_shadowbot_task_runner_from_environment,
)
from app.services.workflow import (
    ExecutionSimulationInputs,
    ExecutionSimulationSummary,
    RuntimeReviewResolutionInputs,
    TaskGenerationSummary,
    ValidationSummary,
    WorkflowInputs,
    generate_tasks_from_selected_rule,
    generate_tasks_from_sources,
    get_mobile_review_detail,
    get_runtime_notification_log,
    get_runtime_review_task,
    get_runtime_task,
    list_manual_intervention_tasks,
    list_runtime_execution_logs,
    list_runtime_notification_logs,
    list_runtime_review_tasks,
    list_runtime_task_history,
    list_runtime_tasks,
    listing_task_override_key,
    persist_task_generation_summary,
    preview_tasks_from_selected_rule,
    preview_tasks_from_sources,
    resolve_mobile_review,
    resolve_runtime_review_task,
    simulate_execution_from_tasks,
    source_task_status_for_review_resolution,
)
from app.utils import parse_date
from app.web_styles import common_styles

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "data" / "samples" / "products.xlsx"
DEFAULT_PRICE_RULES = ROOT / "data" / "samples" / "price_rules.xlsx"
DEFAULT_PLATFORM_MAPPINGS = ROOT / "data" / "samples" / "platform_mappings.xlsx"
DEFAULT_LISTING_RULES = ROOT / "data" / "samples" / "listing_rules.xlsx"
DEFAULT_OUTPUT = ROOT / "data" / "samples" / "web_generated_tasks.xlsx"
DEFAULT_EXECUTION_LOGS = ROOT / "data" / "samples" / "web_execution_logs.xlsx"
DEFAULT_EXECUTED_TASKS = ROOT / "data" / "samples" / "web_executed_tasks.xlsx"
DEFAULT_HARVEST_FORECASTS = ROOT / "data" / "samples" / "harvest_forecasts.xlsx"
DEFAULT_PRICE_FORECASTS = ROOT / "data" / "samples" / "price_forecasts.xlsx"
DEFAULT_CAPACITY_PLANS = ROOT / "data" / "samples" / "capacity_plans.xlsx"
DEFAULT_COLD_STORAGE_STATUS = ROOT / "data" / "samples" / "cold_storage_status.xlsx"
DEFAULT_MANUAL_TASKS = ROOT / "data" / "samples" / "web_generated_tasks.xlsx"
BUSINESS_INPUT_TABS = {
    "inventory": "录入库存",
    "listing_status": "上架状态",
    "price_rules": "价格规则管理",
    "listing_rules": "上下架规则管理",
    "capacity_plans": "包装产能计划",
    "cold_storage_status": "冷库状态",
}

TABLE_OPTIONS = {
    "products": {"label": TABLE_LABELS["products"], "path": DEFAULT_PRODUCTS},
    "price_rules": {"label": TABLE_LABELS["price_rules"], "path": DEFAULT_PRICE_RULES},
    "platform_mappings": {"label": TABLE_LABELS["platform_mappings"], "path": DEFAULT_PLATFORM_MAPPINGS},
    "listing_rules": {"label": TABLE_LABELS["listing_rules"], "path": DEFAULT_LISTING_RULES},
    "harvest_forecasts": {"label": TABLE_LABELS["harvest_forecasts"], "path": DEFAULT_HARVEST_FORECASTS},
    "price_forecasts": {"label": TABLE_LABELS["price_forecasts"], "path": DEFAULT_PRICE_FORECASTS},
    "capacity_plans": {"label": TABLE_LABELS["capacity_plans"], "path": DEFAULT_CAPACITY_PLANS},
    "cold_storage_status": {"label": TABLE_LABELS["cold_storage_status"], "path": DEFAULT_COLD_STORAGE_STATUS},
}

TABLE_HEADER_LABELS = FIELD_LABELS
RUNTIME_SESSION_COOKIE = "pra_runtime_session"
RUNTIME_SESSION_TTL_SECONDS = 3600
MOBILE_RESOLUTION_PAYLOAD_MAX_BYTES = 4096
PAGE_SIZE = 50
TERMINAL_TASK_STATUS_VALUES = {"success", "skipped", "cancelled", "expired"}
_RUNTIME_SESSIONS: dict[str, dict[str, object]] = {}
LOGIN_CSRF_COOKIE = "pra_login_csrf"
LOGIN_CSRF_TTL_SECONDS = 600
LOGIN_CSRF_MAX_CONTEXTS = 4096
_LOGIN_CSRF_CONTEXTS: dict[str, tuple[str, datetime]] = {}
_LOGIN_CSRF_LOCK = Lock()
_RESPONSE_EXTRA_HEADERS: ContextVar[tuple[tuple[str, str], ...]] = ContextVar(
    "web_response_extra_headers",
    default=(),
)
_PATH_POLICY: PathAccessPolicy | None = None
_PATH_POLICY_ENV_SNAPSHOT: str | None = None
_PATH_POLICY_ERROR: PathPolicyError | None = None
_PATH_POLICY_LOCKED = False
# The WSGI environ does not expose the address the server socket is bound to.
# ``serve`` records it here so the legacy safety gate can distinguish a
# loopback-only listener from a public/non-loopback listener.
_WEB_LISTEN_HOST: str | None = None
PATH_POLICY_REQUEST_FIELDS = frozenset({
    "allowed_data_dirs",
    "data_root",
    "runtime_db_root",
})
CSRF_PROTECTED_WRITE_PATHS = frozenset({
    "/",
    "/tables",
    "/execution",
    "/manual-intervention",
    "/runtime",
    "/runtime/logout",
    "/reviews",
    "/execution-logs",
    "/business-inputs",
    "/task-generator",
    "/system/test-feishu-notification",
})
LEGACY_WEB_ROUTES = frozenset({"/", "/tables", "/execution", "/manual-intervention"})
LEGACY_FORWARDING_HEADERS = (
    "HTTP_FORWARDED",
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_REAL_IP",
)
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))

MOBILE_REVIEW_HTTP_STATUS = {
    MobileReviewErrorCode.TOKEN_NOT_FOUND.value: "403 Forbidden",
    MobileReviewErrorCode.TOKEN_REVIEW_MISMATCH.value: "403 Forbidden",
    MobileReviewErrorCode.TOKEN_EXPIRED.value: "410 Gone",
    MobileReviewErrorCode.TOKEN_REVOKED.value: "410 Gone",
    MobileReviewErrorCode.TOKEN_ALREADY_USED.value: "410 Gone",
    MobileReviewErrorCode.REVIEW_NOT_FOUND.value: "404 Not Found",
    MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND.value: "404 Not Found",
    MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED.value: "409 Conflict",
    MobileReviewErrorCode.ACTION_NOT_ALLOWED.value: "403 Forbidden",
    MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE.value: "422 Unprocessable Entity",
    MobileReviewErrorCode.INVALID_ADJUSTMENT.value: "422 Unprocessable Entity",
    MobileReviewErrorCode.CONCURRENT_UPDATE.value: "409 Conflict",
}

DISPLAY_ENUM_LABELS = {
    "task_status": {
        "pending": "待处理",
        "running": "执行中",
        "success": "已完成",
        "failed": "失败",
        "skipped": "已跳过",
        "manual_review": "等待人工确认",
        "cancelled": "已取消",
        "expired": "已过期",
    },
    "review_status": {
        "pending": "待处理",
        "approved": "已通过",
        "rejected": "已拒绝",
        "adjusted": "已调整",
        "expired": "已过期",
        "cancelled": "已取消",
    },
    "send_status": {
        "pending": "待发送",
        "success": "发送成功",
        "failed": "发送失败",
    },
    "action_type": {
        "update_price": "改价",
        "set_online": "上架",
        "set_offline": "下架",
        "sync_status": "同步状态",
        "capacity_warning": "产能预警",
        "labor_required": "临时工确认",
        "manual_price_review": "人工价格复核",
        "below_break_even_review": "低于保本价复核",
        "shortage_warning": "短缺预警",
        "cold_storage_warning": "冷库预警",
        "clearance_warning": "清库存预警",
        "manual_review": "人工复核",
    },
    "review_type": {
        "manual_review": "人工复核",
        "manual_price_review": "人工价格复核",
        "below_break_even_review": "低于保本价复核",
        "emergency_protection": "价格异常处理",
        "labor_required": "临时工确认",
        "capacity_warning": "产能预警",
        "shortage_warning": "短缺预警",
        "cold_storage_warning": "冷库预警",
        "clearance_warning": "清库存预警",
    },
    "scope_type": {
        "global": "全局事项",
        "forecast_group": "预测分组",
        "all": "全部商品",
        "grade": "按等级",
        "variety": "按品种",
        "product_name": "按品种",
        "product": "按品种",
        "sku": "单个商品",
        "platform": "单个平台",
        "task": "单个任务",
    },
    "pricing_method": {
        "fixed_markup": "固定改价",
        "percentage_markup": "百分比改价",
    },
    "rounding_rule": {
        "none": "不取整",
        "round": "四舍五入到整数",
        "ceil": "向上取整",
        "floor": "向下取整",
        "step": "按步长向上取整",
    },
    "listing_strategy": {
        "allow_online": "允许上架",
        "prohibit_online": "禁止上架（兼容旧规则）",
        "set_offline": "直接下架（set_offline）",
        "stock_below_offline": "库存低于阈值下架",
        "stock_above_online": "库存高于阈值允许上架",
    },
    "channel": {
        "mock": "模拟通知",
        "feishu": "飞书",
    },
    "recipient_type": {
        "role": "角色",
        "system": "系统",
        "user": "用户",
    },
}

UI_TEXT = {
    "site_title": "PRA \u8fd0\u884c\u6001\u8fd0\u8425\u540e\u53f0",
    "dashboard_title": "PRA 业务数据与任务生成",
    "dashboard_lede": "选择业务规则，预览并生成后续需要处理的任务。预览时会自动完成数据校验。",
    "dashboard_tab": "\u9996\u9875\u603b\u89c8",
    "tasks_tab": "\u4efb\u52a1\u4e2d\u5fc3",
    "reviews_tab": "\u590d\u6838\u4e2d\u5fc3",
    "notifications_tab": "\u901a\u77e5\u4e2d\u5fc3",
    "execution_logs_tab": "执行记录",
    "business_inputs_tab": "业务数据",
    "task_generator_tab": "生成待处理任务",
    "system_tab": "系统维护",
    "tables_tab": "Excel \u8868\u683c\u7ba1\u7406",
    "execution_tab": "\u6267\u884c\u56de\u5199",
    "manual_tab": "\u4eba\u5de5\u4ecb\u5165",
    "runtime_tab": "SQLite \u8fd0\u884c\u6001",
    "ops_dashboard_title": "PRA \u8fd0\u884c\u6001\u8fd0\u8425\u540e\u53f0",
    "ops_dashboard_lede": "首页总览用于查看今天是否有待处理复核、即将超时事项、通知失败和待执行任务。",
    "legacy_root_notice": "原 / 页面中的任务生成流程已迁移到 /task-generator。",
    "legacy_tables_notice": "\u8be5\u9875\u5df2\u5f52\u5165\u4e1a\u52a1\u8f93\u5165\uff0c\u8fd0\u884c\u6001\u6570\u636e\u8bf7\u4ece\u65b0\u5bfc\u822a\u8fdb\u5165\u3002",
    "legacy_execution_notice": "\u8be5\u9875\u7528\u4e8e mock \u6267\u884c/\u65e7\u56de\u5199\u517c\u5bb9\uff0c\u6b63\u5f0f\u67e5\u770b\u8bf7\u8fdb\u5165\u6267\u884c\u65e5\u5fd7\u3002",
    "legacy_manual_notice": "\u65e7 Excel \u4eba\u5de5\u4ecb\u5165\u53ea\u8bfb\u517c\u5bb9\uff0c\u6b63\u5f0f\u590d\u6838\u8bf7\u8fdb\u5165\u590d\u6838\u4e2d\u5fc3\u3002",
    "legacy_runtime_notice": "\u8fd0\u884c\u6001\u80fd\u529b\u5df2\u62c6\u5206\u4e3a\u4efb\u52a1\u4e2d\u5fc3\u3001\u590d\u6838\u4e2d\u5fc3\u548c\u901a\u77e5\u4e2d\u5fc3\uff0c\u8fd9\u91cc\u4fdd\u7559\u805a\u5408\u517c\u5bb9\u5165\u53e3\u3002",
    "legacy_web_disabled": "\u65e7\u7248 Web \u8def\u7531\u5f53\u524d\u5df2\u5b89\u5168\u5173\u95ed\u3002",
    "task_panel_title": "\u4efb\u52a1\u751f\u6210",
    "generation_mode": "生成模式",
    "generation_mode_batch": "批量生成（全部适用规则）",
    "generation_mode_single_rule": "单规则生成",
    "selected_rule": "选择规则",
    "selected_rule_placeholder": "请选择一条价格规则或上下架规则",
    "single_rule_hint": "单规则模式只使用所选规则；一条规则可能按匹配商品生成多条待处理任务。",
    "single_rule_validated": "所选规则校验通过，可继续预览任务。",
    "runtime_tasks_created": "已生成 {total} 条任务；其中 {inserted} 条新任务已进入任务中心，{deduplicated} 条因重复未再次入库。",
    "ops_tasks_title": "\u4efb\u52a1\u4e2d\u5fc3",
    "ops_reviews_title": "\u590d\u6838\u4e2d\u5fc3",
    "ops_notifications_title": "\u901a\u77e5\u4e2d\u5fc3",
    "ops_execution_logs_title": "执行记录",
    "ops_business_inputs_title": "业务数据",
    "ops_system_title": "系统维护",
    "ops_login_required": "这些运营页面需要先登录后才能查看。",
    "ops_empty_tasks": "当前没有待执行或待处理任务。可以先去业务数据生成任务。",
    "ops_empty_reviews": "当前没有需要人工确认的事项。",
    "ops_empty_notifications": "当前还没有飞书或系统通知记录。",
    "ops_empty_execution_logs": "当前还没有执行器回写结果；接入真实 RPA 后会在这里查看执行结果。",
    "ops_execution_logs_note": "执行记录用于查看系统或执行器实际处理后的结果；ShadowBot 记录会展示操作、尝试、模式、价格、证据和对账告警。",
    "ops_review_runtime_hint": "复核中心是 Web 人工复核主入口；旧聚合页仍可用于排障。",
    "ops_system_config_only": "系统维护用于检查飞书通知、手机复核链接、数据库和后台配置；本页只做配置与本地状态检查。",
    "ops_system_config_checks": "\u914d\u7f6e\u68c0\u67e5",
    "ops_system_db_checks": "\u8fd0\u884c\u6001\u6570\u636e\u5e93\u68c0\u67e5",
    "ops_system_runtime_counts": "\u8fd0\u884c\u6001\u8ba1\u6570",
    "ops_system_runtime_summary": "\u8fd0\u884c\u72b6\u6001\u6458\u8981",
    "ops_system_connectivity": "\u5916\u90e8\u8fde\u901a\u6027\u8fb9\u754c",
    "ops_system_module": "\u6a21\u5757",
    "ops_system_item": "\u68c0\u67e5\u9879",
    "ops_system_status": "\u72b6\u6001",
    "ops_system_value": "\u503c",
    "ops_system_recommendation": "\u5efa\u8bae",
    "ops_system_status_ok": "ok",
    "ops_system_status_info": "info",
    "ops_system_status_warning": "warning",
    "ops_system_status_error": "error",
    "ops_system_status_not_configured": "not_configured",
    "ops_system_not_verified": "\u672a\u9a8c\u8bc1",
    "ops_system_latest_schema": "最新结构版本要求",
    "ops_system_db_exists": "DB \u6587\u4ef6",
    "ops_system_db_readable": "DB \u53ef\u8bfb",
    "ops_system_table_count": "\u8868\u8ba1\u6570",
    "ops_system_external_note": "\u672c\u9875\u4e0d\u81ea\u52a8\u53d1\u9001\u98de\u4e66\u6d4b\u8bd5\u6d88\u606f\uff0c\u4e0d\u81ea\u52a8\u63a2\u6d4b cpolar \u6216 Mobile Review \u5916\u7f51\u94fe\u8def\uff1b\u771f\u5b9e\u8fde\u901a\u6027\u9700\u5355\u72ec\u624b\u52a8\u9a8c\u8bc1\u3002",
    "ops_system_test_feishu_title": "\u624b\u52a8\u6d4b\u8bd5\u98de\u4e66\u901a\u77e5",
    "ops_system_test_feishu_note": "\u8be5\u6d4b\u8bd5\u53ea\u9a8c\u8bc1 FeishuWebhookNotificationSender\u3001Webhook\u3001\u7b7e\u540d\u548c\u7f51\u7edc\uff0c\u4e0d\u521b\u5efa review_task\u3001review_token \u6216 mobile_review_url\u3002",
    "ops_system_test_feishu_button": "\u53d1\u9001\u98de\u4e66\u6d4b\u8bd5\u901a\u77e5",
    "ops_system_test_feishu_success": "\u98de\u4e66\u6d4b\u8bd5\u901a\u77e5\u53d1\u9001\u6210\u529f\u3002",
    "ops_system_test_feishu_not_feishu": "\u5f53\u524d\u901a\u77e5\u6e20\u9053\u4e0d\u662f feishu\uff0c\u4e0d\u4f1a\u53d1\u9001\u98de\u4e66\u6d4b\u8bd5\u901a\u77e5\u3002",
    "ops_system_test_feishu_failed": "\u98de\u4e66\u6d4b\u8bd5\u901a\u77e5\u53d1\u9001\u5931\u8d25\uff1a{error}",
    "ops_config_present": "\u5df2\u914d\u7f6e",
    "ops_config_missing": "\u672a\u914d\u7f6e",
    "ops_runtime_db_path": "运行数据库",
    "ops_schema_versions": "结构版本",
    "ops_link_to_tables": "\u8fdb\u5165 Excel \u8868\u683c\u7ba1\u7406",
    "ops_link_to_generator": "生成待处理任务",
    "ops_link_to_execution": "\u8fdb\u5165 mock \u6267\u884c\u517c\u5bb9\u9875",
    "ops_link_to_runtime": "\u8fdb\u5165\u65e7 /runtime \u805a\u5408\u9875",
    "ops_dashboard_pending_reviews": "\u5f85\u590d\u6838",
    "ops_dashboard_due_soon_reviews": "\u5373\u5c06\u8d85\u65f6\u590d\u6838",
    "ops_dashboard_failed_notifications": "\u5931\u8d25\u901a\u77e5",
    "ops_dashboard_pending_tasks": "\u5f85\u6267\u884c\u4efb\u52a1",
    "ops_dashboard_expired_total": "\u5df2\u8fc7\u671f",
    "ops_dashboard_expired_breakdown": "\u4efb\u52a1 {tasks}\uff0c\u590d\u6838 {reviews}",
    "ops_dashboard_view_tasks": "\u67e5\u770b\u8fc7\u671f\u4efb\u52a1",
    "ops_dashboard_view_reviews": "\u67e5\u770b\u8fc7\u671f\u590d\u6838",
    "ops_filter_all": "\u5168\u90e8",
    "ops_filter_apply": "\u7b5b\u9009",
    "ops_filter_due_soon": "\u4ec5\u5373\u5c06\u8d85\u65f6",
    "ops_task_detail_title": "\u4efb\u52a1\u8be6\u60c5",
    "ops_task_not_found": "\u672a\u627e\u5230\u5bf9\u5e94\u4efb\u52a1\u3002",
    "ops_task_related_reviews_title": "\u5173\u8054\u590d\u6838",
    "ops_task_related_notifications_title": "\u5173\u8054\u901a\u77e5",
    "ops_task_related_execution_logs_title": "\u5173\u8054\u6267\u884c\u65e5\u5fd7",
    "ops_task_no_related_reviews": "\u5f53\u524d\u4efb\u52a1\u6ca1\u6709 source_task_id \u6307\u5411\u5b83\u7684\u590d\u6838\u4efb\u52a1\u3002",
    "ops_task_no_related_notifications": "\u5f53\u524d\u4efb\u52a1\u6682\u65e0\u5173\u8054\u901a\u77e5\u3002",
    "ops_task_no_execution_logs": "\u5f53\u524d\u6682\u65e0\u6267\u884c\u65e5\u5fd7\u3002",
    "ops_task_notification_direct": "\u76f4\u63a5\u5173\u8054",
    "ops_task_notification_via_review": "\u901a\u8fc7\u590d\u6838\u5173\u8054",
    "ops_review_detail_title": "\u590d\u6838\u8be6\u60c5",
    "ops_review_handle_title": "\u5904\u7406\u590d\u6838",
    "ops_review_source_task_title": "\u6e90\u4efb\u52a1\u72b6\u6001",
    "ops_review_related_notifications_title": "\u5173\u8054\u901a\u77e5",
    "ops_review_tokens_title": "\u624b\u673a\u590d\u6838 Token \u6458\u8981",
    "ops_review_history_title": "\u72b6\u6001\u5386\u53f2",
    "ops_review_detail_link": "\u67e5\u770b\u8be6\u60c5",
    "ops_review_no_detail": "\u672a\u627e\u5230\u6307\u5b9a\u7684\u590d\u6838\u4efb\u52a1\u3002",
    "ops_review_handled_hint": "\u8be5\u590d\u6838\u4efb\u52a1\u5df2\u5904\u7406\uff0c\u4e0d\u518d\u663e\u793a\u5904\u7406\u8868\u5355\u3002",
    "ops_json_full": "\u67e5\u770b\u622a\u65ad\u540e\u7684\u5b8c\u6574 JSON",
    "ops_notification_detail_title": "\u901a\u77e5\u8be6\u60c5",
    "ops_notification_related_review_title": "\u5173\u8054\u590d\u6838",
    "ops_notification_related_task_title": "\u5173\u8054\u4efb\u52a1",
    "ops_notification_not_found": "\u672a\u627e\u5230\u5bf9\u5e94\u901a\u77e5\u3002",
    "ops_notification_view_detail": "\u67e5\u770b\u8be6\u60c5",
    "ops_notification_current_feishu_type": "\u5f53\u524d\u98de\u4e66\u6d88\u606f\u7c7b\u578b",
    "ops_notification_config_snapshot_note": "\u8be5\u503c\u6765\u81ea\u5f53\u524d\u914d\u7f6e\uff0c\u4e0d\u4ee3\u8868\u6bcf\u6761\u5386\u53f2\u901a\u77e5\u7684\u6301\u4e45\u5316\u5b57\u6bb5\u3002",
    "ops_notification_no_related_review": "\u8be5\u901a\u77e5\u672a\u5173\u8054\u590d\u6838\u4efb\u52a1\uff0c\u6216\u5173\u8054\u590d\u6838\u5df2\u4e0d\u5b58\u5728\u3002",
    "ops_notification_no_related_task": "\u8be5\u901a\u77e5\u672a\u5173\u8054\u6e90\u4efb\u52a1\uff0c\u6216\u5173\u8054\u4efb\u52a1\u5df2\u4e0d\u5b58\u5728\u3002",
    "execution_panel_title": "\u6a21\u62df\u6267\u884c\u4e0e\u56de\u5199",
    "resources_title": "\u5185\u7f6e\u8d44\u6e90",
    "products_path": "\u5546\u54c1\u8868\u8def\u5f84",
    "price_rules_path": "\u4ef7\u683c\u89c4\u5219\u8def\u5f84",
    "listing_rules_path": "\u4e0a\u4e0b\u67b6\u89c4\u5219\u8def\u5f84",
    "output_path": "\u4efb\u52a1\u8f93\u51fa\u8def\u5f84",
    "platform_name": "\u5e73\u53f0\u540d\u79f0",
    "platform_placeholder": "请选择实际执行平台",
    "task_group_id": "规则任务组 ID",
    "task_group_required_by": "任务组截止时间",
    "task_group_hint": "同一次单规则预览和确认共享组 ID、截止时间与组运行状态；商品任务 ID 仍保持唯一。",
    "inventory_strategy": "\u5e93\u5b58\u7b56\u7565",
    "inventory_strategy_conservative": "\u4fdd\u5b88\u7b56\u7565\uff08\u4f18\u5148\u63a7\u5236\u8d85\u552e\u98ce\u9669\uff09",
    "inventory_strategy_balanced": "\u5e73\u8861\u7b56\u7565\uff08\u5728\u98ce\u9669\u53ef\u63a7\u524d\u63d0\u4e0b\u63d0\u9ad8\u53ef\u552e\u91cf\uff09",
    "use_mock_ai": "\u4f7f\u7528 Mock AI \u5b9a\u4ef7\u5efa\u8bae",
    "validate_button": "\u5148\u6821\u9a8c\u6570\u636e",
    "preview_button": "\u9884\u89c8\u4efb\u52a1",
    "confirm_button": "确认生成并进入任务中心",
    "data_summary": "\u6570\u636e\u6458\u8981",
    "task_result": "\u4efb\u52a1\u7ed3\u679c",
    "output_file": "\u8f93\u51fa\u6587\u4ef6",
    "planned_output_file": "\u9884\u8ba1\u5bfc\u51fa\u6587\u4ef6",
    "no_tasks": "\u6682\u65e0\u4efb\u52a1",
    "preview_ready": "\u4efb\u52a1\u9884\u89c8\u5df2\u5b8c\u6210\uff0c\u786e\u8ba4\u65e0\u8bef\u540e\u518d\u5199\u5165 Excel \u6587\u4ef6\u3002",
    "execution_source_path": "\u4efb\u52a1\u6587\u4ef6\u8def\u5f84",
    "execution_logs_path": "\u6267\u884c\u65e5\u5fd7\u8f93\u51fa\u8def\u5f84",
    "execution_tasks_path": "\u66f4\u65b0\u540e\u4efb\u52a1\u8f93\u51fa\u8def\u5f84",
    "executor_name": "\u6267\u884c\u5668\u540d\u79f0",
    "simulate_button": "\u6a21\u62df\u6267\u884c\u5e76\u56de\u5199",
    "execution_result": "\u6267\u884c\u56de\u5199\u7ed3\u679c",
    "execution_logs_file": "\u6267\u884c\u65e5\u5fd7\u6587\u4ef6",
    "execution_updated_tasks_file": "\u66f4\u65b0\u540e\u4efb\u52a1\u6587\u4ef6",
    "table_editor_title": "Excel \u8868\u683c\u7ba1\u7406",
    "table_editor_lede": "\u5728\u8fd9\u4e00\u9875\u91cc\u6211\u4eec\u53ef\u4ee5\u76f4\u63a5\u7ef4\u62a4\u5546\u54c1\u4e3b\u8868\u3001\u4ef7\u683c\u89c4\u5219\u8868\u548c\u4e0a\u4e0b\u67b6\u89c4\u5219\u8868\u3002\u5148\u52a0\u8f7d\uff0c\u518d\u7f16\u8f91\uff0c\u6700\u540e\u4fdd\u5b58\u56de\u5bf9\u5e94\u5de5\u4f5c\u7c3f\u3002",
    "table_picker": "\u8868\u683c\u9009\u62e9",
    "table_type": "\u8868\u683c\u7c7b\u578b",
    "table_path": "\u5de5\u4f5c\u7c3f\u8def\u5f84",
    "load_button": "\u52a0\u8f7d\u8868\u683c",
    "save_button": "\u4fdd\u5b58\u5f53\u524d\u4fee\u6539",
    "table_hint": "\u8868\u683c\u4f1a\u989d\u5916\u4fdd\u7559 3 \u884c\u7a7a\u767d\u8f93\u5165\uff0c\u65b9\u4fbf\u76f4\u63a5\u8ffd\u52a0\u65b0\u8bb0\u5f55\u3002\u4fdd\u5b58\u65f6\u4f1a\u81ea\u52a8\u5ffd\u7565\u6574\u884c\u7a7a\u767d\u3002",
    "loaded_rows": "\u5df2\u52a0\u8f7d {count} \u884c\u6570\u636e\u3002",
    "saved_rows": "\u5df2\u4fdd\u5b58 {count} \u884c\u5230 {path}",
    "validated": "\u6570\u636e\u6821\u9a8c\u901a\u8fc7\uff0c\u53ef\u4ee5\u76f4\u63a5\u751f\u6210\u4efb\u52a1\u3002",
    "generated": "\u5df2\u751f\u6210 {count} \u6761\u4efb\u52a1\uff0c\u5e76\u5199\u5165 {path}",
    "previewed": "\u5df2\u9884\u89c8 {count} \u6761\u4efb\u52a1\uff0c\u5c1a\u672a\u5199\u5165 Excel \u3002",
    "execution_done": "\u5df2\u6a21\u62df\u6267\u884c {count} \u6761\u4efb\u52a1\uff0c\u65e5\u5fd7\u5df2\u5199\u51fa\u3002",
    "table_validation_summary": "\u4fdd\u5b58\u672a\u6210\u529f\uff0c\u8bf7\u5148\u4fee\u6b63\u4ee5\u4e0b\u5355\u5143\u683c\u95ee\u9898\uff1a",
    "manual_panel_title": "\u4eba\u5de5\u4ecb\u5165\u5de5\u4f5c\u53f0",
    "manual_tasks_path": "\u4efb\u52a1\u6587\u4ef6\u8def\u5f84",
    "manual_output_path": "\u56de\u5199\u8f93\u51fa\u8def\u5f84",
    "manual_actor": "\u5904\u7406\u4eba",
    "manual_note": "\u5907\u6ce8",
    "manual_load_button": "\u52a0\u8f7d\u5f85\u5904\u7406\u4efb\u52a1",
    "manual_empty": "\u5f53\u524d\u6ca1\u6709\u5f85\u4eba\u5de5\u4ecb\u5165\u4efb\u52a1\u3002",
    "manual_resolved": "\u5df2\u5904\u7406\u4efb\u52a1 {task_id} -> {status}",
    "manual_decision": "\u5904\u7406\u7ed3\u679c",
    "manual_submit": "\u63d0\u4ea4",
    "manual_readonly_notice": "\u65e7 Excel \u4eba\u5de5\u4ecb\u5165\u94fe\u8def\u5df2\u8fdb\u5165\u53ea\u8bfb\u517c\u5bb9\u72b6\u6001\uff0c\u4e0d\u518d\u5141\u8bb8\u5728\u8fd9\u91cc\u6267\u884c\u6b63\u5f0f\u5904\u7406\u3002\u8bf7\u6539\u7528 SQLite review_tasks \u6216 /runtime \u5165\u53e3\u3002",
    "runtime_panel_title": "SQLite \u8fd0\u884c\u6001\u67e5\u770b",
    "runtime_db_path": "\u8fd0\u884c\u6001\u6570\u636e\u5e93\u8def\u5f84",
    "runtime_load_button": "\u52a0\u8f7d\u8fd0\u884c\u6001\u6570\u636e",
    "runtime_login_title": "\u8fd0\u884c\u6001\u767b\u5f55",
    "runtime_login_user": "\u540e\u53f0\u8d26\u53f7",
    "runtime_login_password": "\u540e\u53f0\u5bc6\u7801",
    "runtime_login_button": "\u767b\u5f55",
    "runtime_logout_button": "\u9000\u51fa\u767b\u5f55",
    "runtime_session_user": "\u5f53\u524d\u767b\u5f55\u8eab\u4efd",
    "runtime_review_detail": "\u590d\u6838\u8be6\u60c5",
    "runtime_review_handle": "\u5904\u7406\u590d\u6838",
    "runtime_history": "\u4efb\u52a1\u72b6\u6001\u5386\u53f2",
    "runtime_history_empty": "\u6682\u65e0\u72b6\u6001\u53d8\u66f4\u5386\u53f2\u3002",
    "runtime_reviewer_code": "\u590d\u6838\u7801\uff08\u8fc7\u6e21\u5b57\u6bb5\uff09",
    "runtime_resolution_note": "\u5904\u7406\u5907\u6ce8",
    "runtime_resolution_payload": "\u7ed3\u679c JSON",
    "runtime_submit_review": "\u63d0\u4ea4\u590d\u6838\u7ed3\u679c",
    "runtime_login_required": "\u8fd0\u884c\u6001\u590d\u6838\u5904\u7406\u9700\u8981\u5148\u767b\u5f55\u3002",
    "runtime_already_handled": "\u8be5\u590d\u6838\u4efb\u52a1\u5df2\u5904\u7406\uff0c\u4e0d\u80fd\u91cd\u590d\u63d0\u4ea4\u3002",
    "runtime_review_resolved": "\u5df2\u5904\u7406\u590d\u6838\u4efb\u52a1 {review_task_id} -> {status}",
    "runtime_source_not_advanced": "\u6e90\u4efb\u52a1\u5f53\u524d\u4e0d\u662f manual_review\uff0c\u672a\u81ea\u52a8\u63a8\u52a8\u72b6\u6001\u3002",
    "runtime_adjusted_followup": "\u590d\u6838\u7ed3\u679c\u4e3a adjusted\uff0c\u539f\u4efb\u52a1\u5df2\u8df3\u8fc7\uff0c\u9700\u540e\u7eed\u751f\u6210\u65b0\u4efb\u52a1\u3002",
    "runtime_pending_only": "\u53ea\u6709 pending \u72b6\u6001\u7684\u590d\u6838\u4efb\u52a1\u53ef\u4ee5\u5904\u7406\u3002",
    "runtime_tasks": "\u8fd0\u884c\u6001\u4efb\u52a1",
    "runtime_reviews": "\u4eba\u5de5\u590d\u6838\u4efb\u52a1",
    "runtime_notifications": "\u901a\u77e5\u8bb0\u5f55",
    "runtime_task_filters": "\u4efb\u52a1\u7b5b\u9009",
    "runtime_review_filters": "\u590d\u6838\u7b5b\u9009",
    "runtime_notification_filters": "\u901a\u77e5\u7b5b\u9009",
    "runtime_filter_apply": "\u5e94\u7528\u7b5b\u9009",
    "runtime_filter_reset": "\u91cd\u7f6e",
    "runtime_notification_detail": "\u901a\u77e5\u8be6\u60c5",
    "runtime_notification_none": "\u6682\u65e0\u7b26\u5408\u6761\u4ef6\u7684\u901a\u77e5\u8bb0\u5f55\u3002",
    "runtime_history_summary": "\u72b6\u6001\u5386\u53f2\u6458\u8981",
    "mobile_review_title": "\u624b\u673a\u590d\u6838",
    "mobile_review_lede": "\u8fd9\u662f review_tasks \u7684\u8f7b\u91cf\u5904\u7406\u5165\u53e3\uff0c\u6240\u6709\u590d\u6838\u4f9d\u7136\u7531\u8fd0\u884c\u6001\u670d\u52a1\u7edf\u4e00\u5199\u5165\u3002",
    "mobile_review_invalid": "\u94fe\u63a5\u5df2\u5931\u6548\u6216\u65e0\u6743\u8bbf\u95ee\u8be5\u590d\u6838\u4efb\u52a1",
    "mobile_review_handled": "\u8be5\u590d\u6838\u5df2\u5904\u7406",
    "mobile_review_submit": "\u63d0\u4ea4\u590d\u6838",
    "mobile_review_note": "\u5904\u7406\u5907\u6ce8",
    "mobile_review_payload": "\u7ed3\u679c JSON\uff08\u53ef\u7559\u7a7a\uff09",
    "mobile_review_source_status": "\u6e90\u4efb\u52a1\u72b6\u6001",
    "mobile_review_payload_summary": "\u590d\u6838\u4e0a\u4e0b\u6587\u6458\u8981",
}


def _redact_request_log_value(value: object) -> str:
    return re.sub(
        r"([?&]token=)[^&\s]+",
        r"\1[REDACTED]",
        str(value),
        flags=re.IGNORECASE,
    )


class RedactingWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        sanitized_args = tuple(_redact_request_log_value(value) for value in args)
        super().log_message(format, *sanitized_args)


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    global _WEB_LISTEN_HOST
    _WEB_LISTEN_HOST = str(host).strip()
    _configure_path_policy()
    global _PATH_POLICY_LOCKED
    _PATH_POLICY_LOCKED = True
    print(f"{UI_TEXT['site_title']} {host}:{port}")
    with make_server(
        host,
        port,
        application,
        server_class=ThreadedWSGIServer,
        handler_class=RedactingWSGIRequestHandler,
    ) as httpd:
        httpd.serve_forever()


def application(environ, start_response):
    response_context = _RESPONSE_EXTRA_HEADERS.set(())
    try:
        return _application(environ, start_response)
    except PathPolicyError as exc:
        return _respond(
            start_response,
            "400 Bad Request",
            "text/plain; charset=utf-8",
            exc.public_message,
        )
    finally:
        _RESPONSE_EXTRA_HEADERS.reset(response_context)


def _application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")

    override_error = _request_path_policy_override(environ)
    if override_error is not None:
        return _respond(
            start_response,
            "400 Bad Request",
            "text/plain; charset=utf-8",
            override_error.public_message,
        )

    # Logout is an explicit state-changing action.  Reject every method
    # except POST before the CSRF guard or handler can touch the Session.
    if path == "/runtime/logout" and method != "POST":
        return _respond(
            start_response,
            "405 Method Not Allowed",
            "text/plain; charset=utf-8",
            "Method Not Allowed.",
        )

    csrf_failure = _csrf_request_guard(method, path, environ)
    if csrf_failure is not None:
        status, body, headers = csrf_failure
        return _respond(start_response, status, "text/plain; charset=utf-8", body, headers=headers)

    if path in LEGACY_WEB_ROUTES:
        legacy_guard = _legacy_route_guard(path, environ)
        if legacy_guard is not None:
            status, body, headers = legacy_guard
            content_type = "text/plain; charset=utf-8" if status == "403 Forbidden" else "text/html; charset=utf-8"
            return _respond(start_response, status, content_type, body, headers=headers)

    if path == "/health":
        # Health is an unauthenticated operational probe.  Keep its database
        # target fixed to the trusted process configuration; accepting a
        # request-level path would let callers make the service open arbitrary
        # local SQLite files.
        repository = SQLiteRuntimeRepository(Path(DEFAULT_RUNTIME_DB))
        schema_health = repository.check_schema_health()
        operational_health = repository.check_operational_health()
        health_ok = schema_health.ok and operational_health.ok
        status = "200 OK" if health_ok else "503 Service Unavailable"
        body = "ok" if health_ok else f"unhealthy: {schema_health.summary}; {operational_health.summary}"
        return _respond(start_response, status, "text/plain; charset=utf-8", body)
    if path == "/dashboard":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_ops_dashboard(environ))
    if path == "/tasks":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_tasks_page(environ))
    if path == "/reviews":
        result = _handle_reviews_page(method, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    if path == "/notifications":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_notifications_page(environ))
    if path == "/execution-logs":
        result = _handle_execution_logs_page(method, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    if path == "/business-inputs":
        result = _handle_business_inputs_page(method, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    if path == "/task-generator":
        result = _handle_task_generator_page(method, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    if path == "/system/test-feishu-notification":
        result = _handle_system_test_feishu_notification(method, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    if path == "/system":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_system_page(environ))
    if path == "/":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_dashboard(method, environ))
    if path == "/tables":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_tables(method, environ))
    if path == "/execution":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_execution(method, environ))
    if path == "/manual-intervention":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_manual_intervention(method, environ))
    if path.startswith("/mobile/review/"):
        result = _handle_mobile_review(method, path, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    if path == "/runtime/login":
        if method not in {"GET", "POST"}:
            return _respond(
                start_response,
                "405 Method Not Allowed",
                "text/plain; charset=utf-8",
                "Method Not Allowed.",
            )
        status, body, headers = _handle_runtime_login(environ)
        return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
    if path == "/runtime/logout":
        status, body, headers = _handle_runtime_logout(environ)
        return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
    if path == "/runtime":
        result = _handle_runtime(method, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    return _respond(start_response, "404 Not Found", "text/plain; charset=utf-8", "Not Found")


def _configure_path_policy() -> None:
    global _PATH_POLICY, _PATH_POLICY_ENV_SNAPSHOT, _PATH_POLICY_ERROR
    _PATH_POLICY_ENV_SNAPSHOT = os.environ.get("PRA_ALLOWED_DATA_DIRS", "<unset>")
    try:
        _PATH_POLICY = PathAccessPolicy.from_environment(
            default_root=ROOT / "data" / "runtime",
        )
        _PATH_POLICY_ERROR = None
    except PathPolicyError as exc:
        _PATH_POLICY = None
        _PATH_POLICY_ERROR = exc


def _get_path_policy() -> PathAccessPolicy:
    global _PATH_POLICY_ERROR
    snapshot = os.environ.get("PRA_ALLOWED_DATA_DIRS", "<unset>")
    if _PATH_POLICY is None or _PATH_POLICY_ENV_SNAPSHOT != snapshot:
        if _PATH_POLICY_LOCKED and _PATH_POLICY_ENV_SNAPSHOT != snapshot:
            raise PathPolicyError("PATH_POLICY_RESTART_REQUIRED", "允许目录配置变更后必须重启服务")
        _configure_path_policy()
    if _PATH_POLICY_ERROR is not None:
        raise _PATH_POLICY_ERROR
    assert _PATH_POLICY is not None
    return _PATH_POLICY


def _resolve_web_path(
    raw_path: str | os.PathLike[str],
    *,
    purpose: str,
    allow_create: bool = False,
) -> Path:
    try:
        return _get_path_policy().resolve(raw_path, purpose=purpose, allow_create=allow_create)
    except PathPolicyError as exc:
        record_security_event(
            "WEB_PATH_REJECTED",
            route="/web-path",
            outcome="rejected",
            reason_code=exc.code,
        )
        raise


def _read_request_body_preserving(environ) -> bytes:
    if environ.get("REQUEST_METHOD", "GET").upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return b""
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        size = 0
    stream = environ.get("wsgi.input")
    if stream is None:
        raw = b""
    else:
        raw = stream.read(size)
    environ["wsgi.input"] = io.BytesIO(raw)
    return raw


def _request_path_policy_override(environ) -> PathPolicyError | None:
    query_keys = set(_parse_query(environ))
    if query_keys & PATH_POLICY_REQUEST_FIELDS:
        return PathPolicyError(
            "PATH_CONFIG_FROM_REQUEST",
            "允许目录只能来自服务端部署配置",
        )
    raw_body = _read_request_body_preserving(environ)
    if not raw_body:
        return None
    content_type = str(environ.get("CONTENT_TYPE", "")).lower()
    body_keys: set[str] = set()
    if "json" in content_type:
        try:
            loaded = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            body_keys = set(loaded)
    else:
        try:
            body_keys = set(parse_qs(raw_body.decode("utf-8"), keep_blank_values=True))
        except UnicodeDecodeError:
            return PathPolicyError("PATH_BODY_ENCODING", "请求体编码无效")
    if body_keys & PATH_POLICY_REQUEST_FIELDS:
        return PathPolicyError(
            "PATH_CONFIG_FROM_REQUEST",
            "允许目录只能来自服务端部署配置",
        )
    return None


def _csrf_token_from_request(environ, raw_body: bytes) -> str:
    header_token = str(environ.get("HTTP_X_CSRF_TOKEN", "")).strip()
    if header_token:
        return header_token
    content_type = str(environ.get("CONTENT_TYPE", "")).lower()
    if "json" in content_type:
        try:
            loaded = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ""
        return str(loaded.get("csrf_token", "")).strip() if isinstance(loaded, dict) else ""
    parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True) if raw_body else {}
    return _first(parsed, "csrf_token", "").strip()


def _queue_response_header(name: str, value: str) -> None:
    current = _RESPONSE_EXTRA_HEADERS.get()
    _RESPONSE_EXTRA_HEADERS.set((*current, (name, value)))


def _cleanup_login_csrf_contexts(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [
        context_id
        for context_id, (_, expires_at) in _LOGIN_CSRF_CONTEXTS.items()
        if expires_at <= current
    ]
    for context_id in expired:
        _LOGIN_CSRF_CONTEXTS.pop(context_id, None)


def _issue_login_csrf_token() -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=LOGIN_CSRF_TTL_SECONDS)
    context_id = token_urlsafe(24)
    token = token_urlsafe(32)
    with _LOGIN_CSRF_LOCK:
        _cleanup_login_csrf_contexts(now)
        while len(_LOGIN_CSRF_CONTEXTS) >= LOGIN_CSRF_MAX_CONTEXTS:
            oldest_context_id = min(
                _LOGIN_CSRF_CONTEXTS,
                key=lambda item: _LOGIN_CSRF_CONTEXTS[item][1],
            )
            _LOGIN_CSRF_CONTEXTS.pop(oldest_context_id, None)
        _LOGIN_CSRF_CONTEXTS[context_id] = (token, expires_at)

    cookie = SimpleCookie()
    cookie[LOGIN_CSRF_COOKIE] = context_id
    cookie[LOGIN_CSRF_COOKIE]["path"] = "/runtime/login"
    cookie[LOGIN_CSRF_COOKIE]["httponly"] = True
    cookie[LOGIN_CSRF_COOKIE]["samesite"] = "Lax"
    cookie[LOGIN_CSRF_COOKIE]["max-age"] = str(LOGIN_CSRF_TTL_SECONDS)
    if _secure_cookie_enabled():
        cookie[LOGIN_CSRF_COOKIE]["secure"] = True
    _queue_response_header("Set-Cookie", cookie.output(header="").strip())
    return token


def _consume_login_csrf_token(environ, token: str) -> bool:
    if not token:
        return False
    cookie = SimpleCookie()
    cookie.load(environ.get("HTTP_COOKIE", ""))
    context_cookie = cookie.get(LOGIN_CSRF_COOKIE)
    if context_cookie is None:
        return False
    context_id = context_cookie.value
    now = datetime.now(timezone.utc)
    with _LOGIN_CSRF_LOCK:
        context = _LOGIN_CSRF_CONTEXTS.get(context_id)
        if context is None:
            return False
        expected_token, expires_at = context
        if expires_at <= now:
            _LOGIN_CSRF_CONTEXTS.pop(context_id, None)
            return False
        if not compare_digest(expected_token, token):
            return False
        _LOGIN_CSRF_CONTEXTS.pop(context_id, None)
        return True


def _csrf_failure_response() -> tuple[str, str, list[tuple[str, str]]]:
    return "403 Forbidden", "CSRF validation failed (CSRF_REJECTED).", []


def _csrf_request_guard(method: str, path: str, environ):
    if method not in {"POST", "PUT", "PATCH", "DELETE"} or path.startswith("/mobile/review/"):
        return None
    if path == "/runtime/login" and method != "POST":
        return None
    raw_body = _read_request_body_preserving(environ)
    if path == "/runtime/login":
        if _consume_login_csrf_token(environ, _csrf_token_from_request(environ, raw_body)):
            return None
        record_security_event(
            "CSRF_REJECTED",
            route="/runtime/login",
            outcome="rejected",
            reason_code="LOGIN_CSRF_INVALID",
        )
        return _csrf_failure_response()
    if path not in CSRF_PROTECTED_WRITE_PATHS:
        return None
    session = _get_runtime_session(environ)
    if session is None:
        return None
    expected = str(session.get("csrf_token", ""))
    provided = _csrf_token_from_request(environ, raw_body)
    if expected and provided and compare_digest(expected, provided):
        return None
    record_security_event(
        "CSRF_REJECTED",
        route=path,
        outcome="rejected",
        reason_code="SESSION_CSRF_INVALID",
        subject=str(session.get("user", "")),
    )
    return _csrf_failure_response()


def _legacy_route_guard(path: str, environ) -> tuple[str, str, list[tuple[str, str]]] | None:
    """Apply the fail-closed legacy access policy before dispatching a handler.

    Legacy routes are intentionally kept behind a single dispatcher gate.  The
    gate does not inspect forwarding headers to infer the client address; their
    presence is itself a topology anomaly in direct-loopback mode.
    """

    denial_reason = _legacy_route_denial_reason(environ)
    if denial_reason is not None:
        record_security_event(
            "LEGACY_ACCESS_REJECTED",
            route=path,
            outcome="rejected",
            reason_code="LEGACY_POLICY_DENIED",
            subject=str(environ.get("REMOTE_ADDR", "")),
        )
        return (
            "403 Forbidden",
            f"{UI_TEXT['legacy_web_disabled']} {denial_reason}",
            [],
        )

    if _get_runtime_session_user(environ) is None:
        next_path = _safe_internal_path(path) or "/"
        return _redirect_response(
            _append_query_to_path("/runtime/login", {"next": next_path})
        )
    return None


def _legacy_route_denial_reason(environ) -> str | None:
    if os.getenv("PRA_ENABLE_LEGACY_WEB", "").strip() != "1":
        return "PRA_ENABLE_LEGACY_WEB 必须显式设置为 1。"

    if os.getenv("PRA_LEGACY_ACCESS_MODE", "").strip().lower() != "direct_loopback":
        return "PRA_LEGACY_ACCESS_MODE 必须显式设置为 direct_loopback。"

    proxy_mode = os.getenv("PRA_PROXY_MODE", "").strip().lower()
    pra_env = os.getenv("PRA_ENV", "production").strip().lower() or "production"
    if not proxy_mode and pra_env == "production":
        proxy_mode = "reverse_proxy"
    if proxy_mode != "none":
        return "PRA_PROXY_MODE 必须显式设置为 none；反向代理或公网隧道模式下旧路由始终关闭。"

    forwarding_headers = [
        header
        for header in LEGACY_FORWARDING_HEADERS
        if header in environ
    ]
    if forwarding_headers:
        return "direct_loopback 模式检测到未经验证的转发头，已拒绝访问。"

    listen_host = _legacy_listen_host(environ)
    if not _is_exact_loopback_host(listen_host):
        return f"服务监听地址 {listen_host or '-'} 不是允许的 127.0.0.1/::1。"

    remote_addr = str(environ.get("REMOTE_ADDR", "")).strip()
    if not _is_exact_loopback_host(remote_addr):
        return "TCP 对端不是 127.0.0.1/::1，旧路由拒绝访问。"
    return None


def _legacy_listen_host(environ) -> str:
    # The WSGI request environment does not prove which address the listening
    # socket was bound to.  In particular, ``SERVER_ADDR`` and the legacy
    # ``PRA_LISTEN_HOST`` request override are not authoritative and must not
    # turn a wildcard/public listener into an apparent loopback-only service.
    # ``serve`` captures the startup binding in the process-level value below;
    # if no startup binding has been recorded, fail closed.
    return str(_WEB_LISTEN_HOST or "").strip()


def _is_exact_loopback_host(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.compressed in {"127.0.0.1", "::1"}


def _handle_task_generator_page(method: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    if method not in {"GET", "POST"}:
        return "405 Method Not Allowed", "Method Not Allowed.", []

    runtime_db = _runtime_db_for_request(environ)
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["task_generator_tab"],
            description=UI_TEXT["dashboard_lede"],
            active_path="/task-generator",
            runtime_db=runtime_db,
            next_path="/task-generator",
            message=UI_TEXT["ops_login_required"],
        )
    return _handle_dashboard(method, environ, session_user=session_user, runtime_db=Path(runtime_db))


def _handle_dashboard(
    method: str,
    environ,
    *,
    session_user: str | None = None,
    runtime_db: Path | None = None,
) -> str:
    params = default_dashboard_state()
    message = ""
    level = "info"
    validation_summary: ValidationSummary | None = None
    generation_summary: TaskGenerationSummary | None = None
    preview_ready = False

    if method == "POST":
        parsed = _parse_body(environ)
        params = {
            "products": _first(parsed, "products", params["products"]),
            "price_rules": _first(parsed, "price_rules", params["price_rules"]),
            "listing_rules": _first(parsed, "listing_rules", params["listing_rules"]),
            "output": _first(parsed, "output", params["output"]),
            "inventory_strategy": _first(parsed, "inventory_strategy", params["inventory_strategy"]),
            "generation_mode": _first(parsed, "generation_mode", params["generation_mode"]),
            "selected_rule": _first(parsed, "selected_rule", params["selected_rule"]),
            "task_group_id": _first(parsed, "task_group_id", params["task_group_id"]),
            "required_by": _first(parsed, "required_by", params["required_by"]),
            "use_mock_ai": "use_mock_ai" in parsed,
        }
        action = _first(parsed, "action", "preview")
        listing_task_overrides = (
            _parse_listing_task_overrides(parsed)
            if action == "confirm_generate"
            else None
        )

        try:
            products_path = _resolve_request_or_trusted_default(
                params["products"], DEFAULT_PRODUCTS, purpose="products", allow_create=False
            )
            price_rules_path = _resolve_request_or_trusted_default(
                params["price_rules"], DEFAULT_PRICE_RULES, purpose="price_rules", allow_create=False
            )
            listing_rules_path = _resolve_request_or_trusted_default(
                params["listing_rules"], DEFAULT_LISTING_RULES, purpose="listing_rules", allow_create=False
            )
            output_path = _resolve_request_or_trusted_default(
                params["output"], DEFAULT_OUTPUT, purpose="task_output", allow_create=True
            )
            resolved_platform = (
                ""
                if params["generation_mode"] == "single_rule"
                else _resolve_batch_rule_platform(price_rules_path, listing_rules_path)
            )
            workflow_inputs = WorkflowInputs(
                products_path=products_path,
                price_rules_path=price_rules_path,
                listing_rules_path=listing_rules_path,
                output_path=output_path,
                platform_name=resolved_platform,
                inventory_strategy=str(params["inventory_strategy"]),
                use_mock_ai=bool(params["use_mock_ai"]),
                runtime_db_path=runtime_db or Path(DEFAULT_RUNTIME_DB),
                platform_names=tuple(_load_task_generator_platform_options(runtime_db)),
            )
            if params["generation_mode"] == "single_rule":
                rule_type, rule_id = _parse_selected_rule(str(params["selected_rule"]))
                task_group_id = str(params["task_group_id"] or "").strip() or f"RULE-GROUP-{uuid4().hex[:12]}"
                required_by = _parse_task_group_required_by(str(params["required_by"] or ""))
                params["task_group_id"] = task_group_id
                params["required_by"] = required_by.strftime("%Y-%m-%dT%H:%M")
                if action == "preview":
                    generation_summary = preview_tasks_from_selected_rule(
                        workflow_inputs,
                        rule_type=rule_type,
                        rule_id=rule_id,
                        task_group_id=task_group_id,
                        required_by=required_by,
                    )
                    validation_summary = generation_summary.validation
                    message = UI_TEXT["previewed"].format(count=len(generation_summary.tasks))
                    level = "success"
                    preview_ready = True
                elif action == "confirm_generate":
                    generation_summary = generate_tasks_from_selected_rule(
                        workflow_inputs,
                        rule_type=rule_type,
                        rule_id=rule_id,
                        task_group_id=task_group_id,
                        required_by=required_by,
                        listing_task_overrides=listing_task_overrides,
                    )
                    validation_summary = generation_summary.validation
                    runtime_summary = persist_task_generation_summary(
                        generation_summary,
                        db_path=runtime_db or Path(DEFAULT_RUNTIME_DB),
                    )
                    message = UI_TEXT["runtime_tasks_created"].format(
                        total=len(generation_summary.tasks),
                        path=generation_summary.output_path,
                        inserted=runtime_summary.inserted_tasks_count,
                        deduplicated=len(generation_summary.tasks) - runtime_summary.inserted_tasks_count,
                    )
                    level = "success"
                else:
                    raise ValidationError("未知任务生成操作，请先预览任务")
            else:
                if action == "preview":
                    generation_summary = preview_tasks_from_sources(workflow_inputs)
                    validation_summary = generation_summary.validation
                    message = UI_TEXT["previewed"].format(count=len(generation_summary.tasks))
                    level = "success"
                    preview_ready = True
                elif action == "confirm_generate":
                    generation_summary = generate_tasks_from_sources(
                        workflow_inputs,
                        listing_task_overrides=listing_task_overrides,
                    )
                    validation_summary = generation_summary.validation
                    runtime_summary = persist_task_generation_summary(
                        generation_summary,
                        db_path=runtime_db or Path(DEFAULT_RUNTIME_DB),
                    )
                    message = UI_TEXT["runtime_tasks_created"].format(
                        total=len(generation_summary.tasks),
                        path=generation_summary.output_path,
                        inserted=runtime_summary.inserted_tasks_count,
                        deduplicated=len(generation_summary.tasks) - runtime_summary.inserted_tasks_count,
                    )
                    level = "success"
                else:
                    raise ValidationError("未知任务生成操作，请先预览任务")
        except PathPolicyError as exc:
            _redact_rejected_path_values(params, "products", "price_rules", "listing_rules", "output")
            message = exc.public_message
            level = "error"
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"

    rule_options: list[tuple[str, str]] = []
    try:
        rule_options = _load_task_generator_rule_options(params)
    except (PathPolicyError, ValidationError, FileNotFoundError) as exc:
        if not message:
            message = str(exc)
            level = "error"

    return render_dashboard_page(
        params=params,
        message=message,
        message_level=level,
        validation_summary=validation_summary,
        generation_summary=generation_summary,
        preview_ready=preview_ready,
        session_user=session_user,
        rule_options=rule_options,
    )


def _parse_selected_rule(value: str) -> tuple[str, str]:
    rule_type, separator, rule_id = value.partition(":")
    if separator != ":" or rule_type not in {"price", "listing"} or not rule_id.strip():
        raise ValidationError(UI_TEXT["selected_rule_placeholder"])
    return rule_type, rule_id.strip()


def _parse_listing_task_overrides(
    parsed: dict[str, list[str]],
) -> dict[tuple[str, str, str], tuple[str, str]]:
    raw_count = _first(parsed, "listing_override_count", "0").strip()
    try:
        count = int(raw_count)
    except ValueError as exc:
        raise ValidationError("上下架任务目标输入数量无效，请重新预览") from exc
    if count < 0 or count > 5000:
        raise ValidationError("上下架任务目标输入数量超出允许范围，请重新预览")

    overrides: dict[tuple[str, str, str], tuple[str, str]] = {}
    for index in range(count):
        raw_key = _first(parsed, f"listing_override_key_{index}", "")
        try:
            decoded_key = json.loads(raw_key)
        except json.JSONDecodeError as exc:
            raise ValidationError("上下架任务目标输入标识无效，请重新预览") from exc
        if (
            not isinstance(decoded_key, list)
            or len(decoded_key) != 3
            or any(not isinstance(value, str) for value in decoded_key)
        ):
            raise ValidationError("上下架任务目标输入标识无效，请重新预览")
        key = tuple(decoded_key)
        if key in overrides:
            raise ValidationError("上下架任务目标输入重复，请重新预览")
        overrides[key] = (
            _first(parsed, f"listing_target_price_{index}", ""),
            _first(parsed, f"listing_target_inventory_{index}", ""),
        )
    return overrides


def _resolve_batch_rule_platform(price_rules_path: Path, listing_rules_path: Path) -> str:
    platforms: set[str] = set()
    wildcard_found = False
    for table_name, path in (("price_rules", price_rules_path), ("listing_rules", listing_rules_path)):
        for row in load_table_records(table_name, path):
            active = str(row.get("active") or "").strip().lower() in {"true", "1", "yes", "是"}
            if not active:
                continue
            platform = str(row.get("platform_filter") or "").strip()
            if not platform or platform == "*":
                wildcard_found = True
            else:
                platforms.add(platform)
    if wildcard_found:
        raise ValidationError("批量生成要求所有启用规则指定具体平台；请先补全规则平台")
    if len(platforms) != 1:
        raise ValidationError("批量生成仅支持同一平台的规则；多平台请使用单规则生成")
    return next(iter(platforms))


def _parse_task_group_required_by(value: str) -> datetime:
    text = str(value or "").strip()
    current = datetime.now(DISPLAY_TIMEZONE)
    if not text:
        return current + timedelta(minutes=30)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError("任务组截止时间格式无效") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=DISPLAY_TIMEZONE)
    else:
        parsed = parsed.astimezone(DISPLAY_TIMEZONE)
    if parsed <= current:
        raise ValidationError("任务组截止时间必须晚于当前时间")
    return parsed


def _load_task_generator_rule_options(params: dict[str, str | bool]) -> list[tuple[str, str]]:
    sources = (
        (
            "price",
            "价格规则",
            "price_rules",
            _resolve_request_or_trusted_default(
                params["price_rules"], DEFAULT_PRICE_RULES, purpose="price_rules", allow_create=False
            ),
        ),
        (
            "listing",
            "上下架规则",
            "listing_rules",
            _resolve_request_or_trusted_default(
                params["listing_rules"], DEFAULT_LISTING_RULES, purpose="listing_rules", allow_create=False
            ),
        ),
    )
    options: list[tuple[str, str]] = []
    for rule_type, type_label, table_name, path in sources:
        for row in load_table_records(table_name, path):
            rule_id = str(row.get("rule_id") or "").strip()
            if not rule_id:
                continue
            rule_name = str(row.get("rule_name") or rule_id).strip()
            platform = str(row.get("platform_filter") or "").strip() or "未指定平台"
            active = str(row.get("active") or "").strip().lower() in {"true", "1", "yes", "是"}
            suffix = "" if active else "（停用）"
            options.append(
                (f"{rule_type}:{rule_id}", f"{type_label}｜{rule_name}（{rule_id}）｜平台：{platform}{suffix}")
            )
    return options


def _load_task_generator_platform_options(runtime_db: Path | None = None) -> list[str]:
    options = list(PLATFORM_OPTIONS)
    # This is an application-owned resource, not a user-supplied path.
    for row in load_table_records("platform_mappings", DEFAULT_PLATFORM_MAPPINGS):
        platform_name = str(row.get("platform_name") or "").strip()
        status = str(row.get("mapping_status") or "active").strip().lower()
        if platform_name and status == "active" and platform_name not in options:
            options.append(platform_name)
    if runtime_db is not None:
        repository = SQLiteRuntimeRepository(runtime_db)
        repository.init_schema()
        for listing_status in repository.list_listing_statuses():
            if listing_status.platform_name not in options:
                options.append(listing_status.platform_name)
    return options


def _handle_tables(method: str, environ) -> str:
    params = default_table_editor_state()
    message = ""
    level = "info"
    records: list[dict[str, object]] = []
    table_issues: list[tuple[int, str, str]] = []

    if method == "POST":
        parsed = _parse_body(environ)
        previous_table_name = _first(parsed, "previous_table_name", str(params["table_name"]))
        requested_table = _first(parsed, "table_name", str(params["table_name"]))
        table_name = requested_table if requested_table in TABLE_OPTIONS else "products"
        posted_path = _first(parsed, "table_path", str(params["table_path"]))
        params["table_name"] = table_name
        params["table_path"] = _resolve_table_path(table_name, previous_table_name, posted_path)
        action = _first(parsed, "action", "load")
        headers = get_table_headers(table_name)

        try:
            table_path = _resolve_request_or_trusted_default(
                params["table_path"],
                Path(TABLE_OPTIONS[table_name]["path"]),
                purpose=f"table:{table_name}",
                allow_create=action == "save",
            )
            params["table_path"] = str(table_path)
            if action == "save":
                records = _extract_table_rows(parsed, headers)
                save_table_records(table_name, table_path, records)
                message = UI_TEXT["saved_rows"].format(count=len(records), path=table_path)
                level = "success"

            records = load_table_records(table_name, table_path)
            if action == "load" and not message:
                message = UI_TEXT["loaded_rows"].format(count=len(records))
                level = "success"
        except PathPolicyError as exc:
            _redact_rejected_path_values(params, "table_path")
            message = exc.public_message
            level = "error"
        except TableValidationError as exc:
            message = UI_TEXT["table_validation_summary"]
            level = "error"
            table_issues = [(item.row_number, item.field_name, item.message) for item in exc.issues]
            if action == "save":
                records = _extract_table_rows(parsed, headers)
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"
            if action == "save":
                records = _extract_table_rows(parsed, headers)
    else:
        headers = get_table_headers(str(params["table_name"]))
        try:
            table_default = Path(TABLE_OPTIONS[str(params["table_name"])]["path"])
            table_path = _resolve_request_or_trusted_default(
                params["table_path"],
                table_default,
                purpose=f"table:{params['table_name']}",
                allow_create=False,
            )
            params["table_path"] = str(table_path)
            records = load_table_records(str(params["table_name"]), table_path)
        except PathPolicyError as exc:
            _redact_rejected_path_values(params, "table_path")
            message = exc.public_message
            level = "error"
            records = []
        except (ValidationError, FileNotFoundError):
            records = []

    headers = get_table_headers(str(params["table_name"]))
    return render_table_editor_page(
        params=params,
        headers=headers,
        records=records,
        message=message,
        message_level=level,
        table_issues=table_issues,
    )


def _handle_execution(method: str, environ) -> str:
    params = default_execution_state()
    message = ""
    level = "info"
    execution_summary: ExecutionSimulationSummary | None = None

    if method == "POST":
        parsed = _parse_body(environ)
        params = {
            "tasks_path": _first(parsed, "tasks_path", params["tasks_path"]),
            "logs_output": _first(parsed, "logs_output", params["logs_output"]),
            "updated_tasks_output": _first(parsed, "updated_tasks_output", params["updated_tasks_output"]),
            "executor_name": _first(parsed, "executor_name", params["executor_name"]),
        }
        try:
            tasks_path = _resolve_request_or_trusted_default(
                params["tasks_path"], DEFAULT_OUTPUT, purpose="execution_tasks", allow_create=False
            )
            logs_output_path = _resolve_request_or_trusted_default(
                params["logs_output"], DEFAULT_EXECUTION_LOGS, purpose="execution_logs", allow_create=True
            )
            updated_tasks_output_path = (
                _resolve_request_or_trusted_default(
                    params["updated_tasks_output"],
                    DEFAULT_EXECUTED_TASKS,
                    purpose="execution_updated_tasks",
                    allow_create=True,
                )
                if str(params["updated_tasks_output"]).strip()
                else None
            )
            execution_summary = simulate_execution_from_tasks(
                ExecutionSimulationInputs(
                    tasks_path=tasks_path,
                    logs_output_path=logs_output_path,
                    updated_tasks_output_path=updated_tasks_output_path,
                    executor_name=str(params["executor_name"]),
                )
            )
            message = UI_TEXT["execution_done"].format(count=len(execution_summary.tasks))
            level = "success"
        except PathPolicyError as exc:
            _redact_rejected_path_values(params, "tasks_path", "logs_output", "updated_tasks_output")
            message = exc.public_message
            level = "error"
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"

    return render_execution_page(
        params=params,
        message=message,
        message_level=level,
        execution_summary=execution_summary,
    )


def _handle_manual_intervention(method: str, environ) -> str:
    params = default_manual_state()
    message = UI_TEXT["manual_readonly_notice"]
    level = "info"
    tasks = []

    if method == "POST":
        parsed = _parse_body(environ)
        params = {
            "tasks_path": _first(parsed, "tasks_path", params["tasks_path"]),
            "output_path": _first(parsed, "output_path", params["output_path"]),
            "actor": _first(parsed, "actor", params["actor"]),
            "note": _first(parsed, "note", ""),
        }
        try:
            tasks_path = _resolve_request_or_trusted_default(
                params["tasks_path"], DEFAULT_MANUAL_TASKS, purpose="manual_tasks", allow_create=False
            )
            _resolve_request_or_trusted_default(
                params["output_path"], DEFAULT_MANUAL_TASKS, purpose="manual_output", allow_create=True
            )
            tasks = list_manual_intervention_tasks(tasks_path)
            if _first(parsed, "action", "load") == "resolve":
                message = "旧 Excel 人工介入入口已弃用，不能再执行正式处理。请改用 SQLite review_tasks 或 /runtime 入口。"
                level = "error"
            elif not tasks:
                message = UI_TEXT["manual_empty"]
                level = "info"
        except PathPolicyError as exc:
            _redact_rejected_path_values(params, "tasks_path", "output_path")
            message = exc.public_message
            level = "error"
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"

    return render_manual_intervention_page(
        params=params,
        message=message,
        message_level=level,
        tasks=tasks,
    )


def _handle_mobile_review(method: str, path: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    parsed_path = _parse_mobile_review_path(path)
    if parsed_path is None:
        return render_mobile_review_error_page("Not Found")
    review_task_id, is_resolve = parsed_path
    query = _parse_query(environ)

    if method == "GET" and not is_resolve:
        if _first(query, "resolved", "") == "1":
            return render_mobile_review_resolved_page(review_task_id)
        token = _first(query, "token", "")
        try:
            detail = get_mobile_review_detail(DEFAULT_RUNTIME_DB, review_task_id, token)
            return render_mobile_review_page(
                detail=detail,
                raw_token=token,
            )
        except (ValidationError, FileNotFoundError):
            record_security_event(
                "MOBILE_REVIEW_TOKEN_REJECTED",
                route="/mobile/review",
                outcome="rejected",
                reason_code="TOKEN_INVALID",
                subject=review_task_id,
            )
            return render_mobile_review_error_page(UI_TEXT["mobile_review_invalid"])

    if method == "POST" and is_resolve:
        parsed = _parse_body(environ)
        token = _first(parsed, "token", "")
        action = _first(parsed, "action", "")
        note = _first(parsed, "resolution_note", "")
        try:
            resolution_payload = _parse_mobile_resolution_payload(
                _first(parsed, "resolution_payload_json", "")
            )
            target_price = _first(parsed, "target_price", "").strip()
            if action == ReviewTaskStatus.ADJUSTED.value and target_price:
                resolution_payload = dict(resolution_payload or {})
                adjustment = dict(resolution_payload.get("adjustment") or {})
                adjustment["target_price"] = target_price
                resolution_payload["adjustment"] = adjustment
            resolve_mobile_review(
                DEFAULT_RUNTIME_DB,
                review_task_id,
                token,
                action,
                note=note,
                resolution_payload=resolution_payload,
                products_path=DEFAULT_PRODUCTS,
            )
            record_security_event(
                "MOBILE_REVIEW_TOKEN_CONSUMED",
                route="/mobile/review",
                outcome="accepted",
                reason_code="TOKEN_CONSUMED",
                subject=review_task_id,
            )
            return _redirect_response(
                f"/mobile/review/{review_task_id}?{urlencode({'resolved': '1'})}"
            )
        except MobileReviewTransactionError as exc:
            return _mobile_review_error_response(exc)
        except ValidationError as exc:
            record_security_event(
                "MOBILE_REVIEW_TOKEN_REJECTED",
                route="/mobile/review",
                outcome="rejected",
                reason_code="TOKEN_OR_PAYLOAD_INVALID",
                subject=review_task_id,
            )
            message = (
                str(exc)
                if str(exc) not in {UI_TEXT["mobile_review_invalid"], "链接已失效或无权访问该复核任务"}
                else UI_TEXT["mobile_review_invalid"]
            )
            return render_mobile_review_error_page(message)
        except FileNotFoundError:
            return render_mobile_review_error_page(UI_TEXT["mobile_review_invalid"])

    return render_mobile_review_error_page("Method Not Allowed")


def _parse_mobile_review_path(path: str) -> tuple[str, bool] | None:
    prefix = "/mobile/review/"
    if not path.startswith(prefix):
        return None
    tail = path[len(prefix) :].strip("/")
    if not tail:
        return None
    parts = tail.split("/")
    if len(parts) == 1:
        return (unquote(parts[0]), False)
    if len(parts) == 2 and parts[1] == "resolve":
        return (unquote(parts[0]), True)
    return None


def _handle_runtime(method: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    params = default_runtime_state()
    query = _parse_query(environ)
    params["runtime_db"] = _runtime_db_for_request(environ)
    params["review_task_id"] = _first(query, "review_task_id", "")
    params["task_id"] = _first(query, "task_id", "")
    params["notification_id"] = _first(query, "notification_id", "")
    params["task_trade_date"] = _first(query, "task_trade_date", "")
    params["task_status"] = _first(query, "task_status", "")
    params["review_trade_date"] = _first(query, "review_trade_date", "")
    params["review_status"] = _first(query, "review_status", "")
    params["notification_related_review_task_id"] = _first(query, "notification_related_review_task_id", "")
    params["notification_send_status"] = _first(query, "notification_send_status", "")
    message = _first(query, "message", "")
    level = _first(query, "level", "info" if message else "info")
    tasks = []
    reviews = []
    notifications = []
    selected_review = None
    selected_task = None
    selected_notification = None
    history = []
    session_user = _get_runtime_session_user(environ)
    filters = _runtime_filter_state(params)

    if method == "POST":
        if session_user is None:
            return _redirect_response(
                _build_runtime_url(
                    params["runtime_db"],
                    **filters,
                    message=UI_TEXT["runtime_login_required"],
                    level="error",
                )
            )
        parsed = _parse_body(environ)
        params["runtime_db"] = str(_resolve_request_or_trusted_default(
            _first(parsed, "runtime_db", params["runtime_db"]),
            Path(DEFAULT_RUNTIME_DB),
            purpose="runtime_db",
            allow_create=False,
        ))
        params["review_task_id"] = _first(parsed, "review_task_id", params["review_task_id"])
        params["task_id"] = _first(parsed, "task_id", params["task_id"])
        params["notification_id"] = _first(parsed, "notification_id", params["notification_id"])
        params["task_trade_date"] = _first(parsed, "task_trade_date", params["task_trade_date"])
        params["task_status"] = _first(parsed, "task_status", params["task_status"])
        params["review_trade_date"] = _first(parsed, "review_trade_date", params["review_trade_date"])
        params["review_status"] = _first(parsed, "review_status", params["review_status"])
        params["notification_related_review_task_id"] = _first(
            parsed,
            "notification_related_review_task_id",
            params["notification_related_review_task_id"],
        )
        params["notification_send_status"] = _first(parsed, "notification_send_status", params["notification_send_status"])
        filters = _runtime_filter_state(params)
        action = _first(parsed, "action", "load")
        if action == "resolve_review":
            try:
                db_path = Path(str(params["runtime_db"]))
                review = get_runtime_review_task(db_path, params["review_task_id"])
                if review is None:
                    raise ValidationError(f"review task not found: {params['review_task_id']}")
                if review.review_status != ReviewTaskStatus.PENDING:
                    raise ValidationError(UI_TEXT["runtime_already_handled"])

                resolution_payload = _parse_resolution_payload(
                    _first(parsed, "resolution_payload_json", "{}")
                )
                reviewer_code = _first(parsed, "reviewer_code", "").strip()
                if reviewer_code:
                    resolution_payload["reviewer_code"] = reviewer_code

                review_status = ReviewTaskStatus(_first(parsed, "review_status", ReviewTaskStatus.APPROVED.value))
                source_task_status = None
                source_followup_message = ""
                if review.source_task_id:
                    source_task = get_runtime_task(db_path, review.source_task_id)
                    source_task_status = source_task_status_for_review_resolution(source_task, review_status)
                    if source_task_status is None:
                        source_followup_message = UI_TEXT["runtime_source_not_advanced"]
                    params["task_id"] = review.source_task_id

                resolved = resolve_runtime_review_task(
                    RuntimeReviewResolutionInputs(
                        db_path=db_path,
                        review_task_id=review.review_task_id,
                        status=review_status,
                        actor=session_user,
                        actor_source="session_user",
                        note=_first(parsed, "resolution_note", ""),
                        source_task_status=source_task_status,
                        resolution_payload=resolution_payload,
                    )
                )
                success_message = UI_TEXT["runtime_review_resolved"].format(
                    review_task_id=resolved.review_task_id,
                    status=resolved.review_status.value,
                )
                if review_status == ReviewTaskStatus.ADJUSTED:
                    success_message = f"{success_message} {UI_TEXT['runtime_adjusted_followup']}"
                if source_followup_message:
                    success_message = f"{success_message} {source_followup_message}"
                return _redirect_response(
                    _build_runtime_url(
                        params["runtime_db"],
                        review_task_id=resolved.review_task_id,
                        task_id=params["task_id"],
                        notification_related_review_task_id=filters["notification_related_review_task_id"],
                        notification_send_status=filters["notification_send_status"],
                        task_trade_date=filters["task_trade_date"],
                        task_status=filters["task_status"],
                        review_trade_date=filters["review_trade_date"],
                        review_status=filters["review_status"],
                        message=success_message,
                        level="success",
                    )
                )
            except (ValidationError, FileNotFoundError) as exc:
                message = str(exc)
                level = "error"

    if session_user is not None:
        try:
            db_path = Path(str(params["runtime_db"]))
            tasks = _sort_tasks_for_display(list_runtime_tasks(
                db_path,
                trade_date=parse_date(params["task_trade_date"], "task_trade_date") if params["task_trade_date"] else None,
                status=TaskStatus(params["task_status"]) if params["task_status"] else None,
            ))
            reviews = _sort_reviews_for_display(list_runtime_review_tasks(
                db_path,
                trade_date=parse_date(params["review_trade_date"], "review_trade_date") if params["review_trade_date"] else None,
                status=ReviewTaskStatus(params["review_status"]) if params["review_status"] else None,
            ))
            notifications = _sort_notifications_for_display(list_runtime_notification_logs(
                db_path,
                related_review_task_id=params["notification_related_review_task_id"] or None,
                send_status=params["notification_send_status"] or None,
            ))
            if params["review_task_id"]:
                selected_review = get_runtime_review_task(db_path, params["review_task_id"])
                if selected_review is not None and not params["task_id"] and selected_review.source_task_id:
                    params["task_id"] = selected_review.source_task_id
            if params["task_id"]:
                selected_task = get_runtime_task(db_path, params["task_id"])
                if selected_task is not None:
                    history = list_runtime_task_history(db_path, selected_task.task_id)
            if params["notification_id"]:
                selected_notification = get_runtime_notification_log(db_path, params["notification_id"])
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"
    elif not message:
        message = UI_TEXT["runtime_login_required"]

    return render_runtime_page(
        params=params,
        message=message,
        message_level=level,
        tasks=tasks,
        reviews=reviews,
        notifications=notifications,
        session_user=session_user,
        selected_review=selected_review,
        selected_task=selected_task,
        selected_notification=selected_notification,
        task_history=history,
    )


def _handle_runtime_login(environ) -> tuple[str, str, list[tuple[str, str]]]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    query = _parse_query(environ)
    if method == "GET":
        runtime_db_value = _first(query, "runtime_db", "")
        runtime_db = str(_resolve_request_or_trusted_default(
            runtime_db_value,
            Path(DEFAULT_RUNTIME_DB),
            purpose="runtime_db",
            allow_create=False,
        ))
        login_csrf_token = _issue_login_csrf_token()
        body = render_runtime_page(
            params={
                "runtime_db": runtime_db,
                "review_task_id": "",
                "task_id": "",
                "notification_id": "",
                "task_trade_date": "",
                "task_status": "",
                "review_trade_date": "",
                "review_status": "",
                "notification_related_review_task_id": "",
                "notification_send_status": "",
            },
            message="",
            message_level="info",
            tasks=[],
            reviews=[],
            notifications=[],
            session_user=None,
            selected_review=None,
            selected_task=None,
            selected_notification=None,
            task_history=[],
            login_csrf_token=login_csrf_token,
        )
        return "200 OK", body, []

    parsed = _parse_body(environ)
    runtime_db_value = _first(parsed, "runtime_db", _first(query, "runtime_db", ""))
    runtime_db = str(_resolve_request_or_trusted_default(
        runtime_db_value,
        Path(DEFAULT_RUNTIME_DB),
        purpose="runtime_db",
        allow_create=False,
    ))
    next_path = _safe_internal_path(_first(parsed, "next", _first(query, "next", "")))
    username = _first(parsed, "username", _runtime_admin_user()).strip() or _runtime_admin_user()
    password = _first(parsed, "password", "")
    redirect_target = next_path or _build_runtime_url(runtime_db)
    if next_path:
        redirect_target = _append_query_to_path(next_path, {"runtime_db": runtime_db})

    expected_password = os.getenv("RUNTIME_ADMIN_PASSWORD", "")
    remote_addr = str(environ.get("REMOTE_ADDR", ""))
    if LOGIN_RATE_LIMITER.is_blocked(username, remote_addr):
        record_security_event(
            "LOGIN_RATE_LIMITED",
            route="/runtime/login",
            outcome="rejected",
            reason_code="RATE_LIMITED",
            subject=username,
        )
        return _runtime_login_error_response(
            runtime_db,
            "登录尝试过于频繁，请稍后再试（RATE_LIMITED）。",
            status="429 Too Many Requests",
        )
    if expected_password and username == _runtime_admin_user() and password == expected_password:
        LOGIN_RATE_LIMITER.record_success(username, remote_addr)
        record_security_event(
            "LOGIN_SUCCESS",
            route="/runtime/login",
            outcome="accepted",
            reason_code="PASSWORD_MATCH",
            subject=username,
        )
        return _redirect_response(
            redirect_target,
            headers=_session_cookie_headers(_runtime_admin_user(), runtime_db=runtime_db, environ=environ),
        )
    if _dev_mode():
        shared_password = os.getenv("RUNTIME_DEV_SHARED_PASSWORD", "")
        if shared_password and password == shared_password:
            LOGIN_RATE_LIMITER.record_success(username, remote_addr)
            record_security_event(
                "LOGIN_SUCCESS",
                route="/runtime/login",
                outcome="accepted",
                reason_code="DEV_PASSWORD_MATCH",
                subject=username,
            )
            return _redirect_response(
                redirect_target,
                headers=_session_cookie_headers("dev_shared_user", runtime_db=runtime_db, environ=environ),
            )

    message = "登录失败：账号或密码不正确。"
    if not expected_password and not (_dev_mode() and os.getenv("RUNTIME_DEV_SHARED_PASSWORD", "")):
        message = "尚未配置 RUNTIME_ADMIN_PASSWORD，无法进行运行态复核登录。"
    blocked = LOGIN_RATE_LIMITER.record_failure(username, remote_addr)
    record_security_event(
        "LOGIN_FAILED",
        route="/runtime/login",
        outcome="rejected",
        reason_code="PASSWORD_NOT_CONFIGURED" if not expected_password else "INVALID_CREDENTIALS",
        subject=username,
    )
    status = "200 OK"
    if blocked:
        record_security_event(
            "LOGIN_RATE_LIMITED",
            route="/runtime/login",
            outcome="rejected",
            reason_code="RATE_LIMITED",
            subject=username,
        )
        message = "登录尝试过于频繁，请稍后再试（RATE_LIMITED）。"
        status = "429 Too Many Requests"
    return _runtime_login_error_response(runtime_db, message, status=status)


def _runtime_login_error_response(
    runtime_db: str,
    message: str,
    *,
    status: str = "200 OK",
) -> tuple[str, str, list[tuple[str, str]]]:
    login_csrf_token = _issue_login_csrf_token()
    body = render_runtime_page(
        params={
            "runtime_db": runtime_db,
            "review_task_id": "",
            "task_id": "",
            "notification_id": "",
            "task_trade_date": "",
            "task_status": "",
            "review_trade_date": "",
            "review_status": "",
            "notification_related_review_task_id": "",
            "notification_send_status": "",
        },
        message=message,
        message_level="error",
        tasks=[],
        reviews=[],
        notifications=[],
        session_user=None,
        selected_review=None,
        selected_task=None,
        selected_notification=None,
        task_history=[],
        login_csrf_token=login_csrf_token,
    )
    return (status, body, [])


def _handle_runtime_logout(environ) -> tuple[str, str, list[tuple[str, str]]]:
    parsed = _parse_body(environ)
    runtime_db_value = _first(parsed, "runtime_db", "")
    runtime_db = str(_resolve_request_or_trusted_default(
        runtime_db_value,
        Path(DEFAULT_RUNTIME_DB),
        purpose="runtime_db",
        allow_create=False,
    ))
    next_path = _safe_internal_path(_first(parsed, "next", ""))
    cookie = SimpleCookie()
    cookie.load(environ.get("HTTP_COOKIE", ""))
    login_csrf_cookie = cookie.get(LOGIN_CSRF_COOKIE)
    if login_csrf_cookie is not None:
        with _LOGIN_CSRF_LOCK:
            _LOGIN_CSRF_CONTEXTS.pop(login_csrf_cookie.value, None)
    session_cookie = cookie.get(RUNTIME_SESSION_COOKIE)
    if session_cookie is not None:
        session = _RUNTIME_SESSIONS.pop(session_cookie.value, None)
        if session is not None:
            record_security_event(
                "SESSION_LOGOUT",
                route="/runtime/logout",
                outcome="accepted",
                reason_code="EXPLICIT_LOGOUT",
                subject=str(session.get("user", "")),
            )
    return _redirect_response(
        _append_query_to_path(
            next_path or "/runtime",
            {
                "runtime_db": runtime_db,
                "message": "已退出运行态登录。",
                "level": "success",
            },
        ),
        headers=_clear_session_cookie_headers(),
    )


def _handle_ops_dashboard(environ) -> str:
    runtime_db = _runtime_db_for_request(environ)
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_dashboard_title"],
            description=UI_TEXT["ops_dashboard_lede"],
            active_path="/dashboard",
            runtime_db=runtime_db,
            next_path=_append_query_to_path("/dashboard", {"runtime_db": runtime_db}),
            message=UI_TEXT["ops_login_required"],
        )

    tasks = list_runtime_tasks(Path(runtime_db))
    reviews = list_runtime_review_tasks(Path(runtime_db))
    notifications = list_runtime_notification_logs(Path(runtime_db))
    try:
        execution_logs = list_runtime_execution_logs(Path(runtime_db), limit=100)
    except FileNotFoundError:
        execution_logs = []
    return render_ops_dashboard_page(
        runtime_db=runtime_db,
        session_user=session_user,
        tasks=tasks,
        reviews=reviews,
        notifications=notifications,
        execution_logs=execution_logs,
        now=datetime.now(),
    )


def _handle_tasks_page(environ) -> str:
    query = _parse_query(environ)
    runtime_db = _runtime_db_for_request(environ)
    task_tab = _first(query, "task_tab", "tasks") or "tasks"
    task_status = _first(query, "task_status", "")
    trade_date_text = _first(query, "trade_date", "")
    action_type_text = _first(query, "action_type", "")
    scope_type = _first(query, "scope_type", "")
    scope_key = _first(query, "scope_key", "")
    selected_task_id = _first(query, "task_id", "")
    page = _parse_page_number(_first(query, "page", "1"))
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_tasks_title"],
            description="查看运行态任务基础列表，复杂详情和状态时间线留到后续阶段。",
            active_path="/tasks",
            runtime_db=runtime_db,
            next_path=_append_query_to_path("/tasks", {"runtime_db": runtime_db}),
            message=UI_TEXT["ops_login_required"],
        )
    if task_tab == "mock_platform":
        mock_platform_db_value = _first(query, "mock_platform_db", "")
        platform_filter = _first(query, "platform", "")
        sku_filter = _first(query, "internal_sku", "")
        mock_platform_path = (
            _resolve_web_path(mock_platform_db_value, purpose="mock_platform_db", allow_create=False)
            if mock_platform_db_value.strip()
            else _resolve_web_path(
                _trusted_project_path(DEFAULT_MOCK_PLATFORM_DB),
                purpose="mock_platform_db",
                allow_create=False,
            )
        )
        states = []
        if mock_platform_path.exists():
            try:
                mock_repository = MockPlatformRepository(mock_platform_path)
                states = mock_repository.list_product_states(
                    platform_name=platform_filter or None,
                    internal_sku=sku_filter or None,
                )
            except sqlite3.Error:
                states = []
        paged_states, pagination = _paginate_items(states, page)
        return render_tasks_mock_platform_page(
            runtime_db=runtime_db,
            session_user=session_user,
            states=paged_states,
            pagination=pagination,
            platform_filter=platform_filter,
            sku_filter=sku_filter,
        )
    if task_tab == "automation":
        db_path = Path(runtime_db)
        selected_script_run_id = _first(query, "script_run_id", "")
        if not db_path.exists():
            empty_runs, pagination = _paginate_items([], page)
            return render_tasks_automation_page(
                runtime_db=runtime_db,
                session_user=session_user,
                script_runs=empty_runs,
                pagination=pagination,
                selected_script_run=None,
                script_run_items=[],
            )
        repository = SQLiteRuntimeRepository(db_path)
        try:
            script_runs = repository.list_script_runs(limit=200)
            selected_script_run = repository.get_script_run(selected_script_run_id) if selected_script_run_id else None
            script_run_items = (
                repository.list_script_run_items(selected_script_run.script_run_id)
                if selected_script_run is not None
                else []
            )
        except sqlite3.Error:
            script_runs = []
            selected_script_run = None
            script_run_items = []
        paged_script_runs, pagination = _paginate_items(script_runs, page)
        return render_tasks_automation_page(
            runtime_db=runtime_db,
            session_user=session_user,
            script_runs=paged_script_runs,
            pagination=pagination,
            selected_script_run=selected_script_run,
            script_run_items=script_run_items,
        )
    parsed_trade_date = _parse_optional_date(trade_date_text)
    parsed_action_type = _parse_optional_task_action_type(action_type_text)
    tasks = _sort_tasks_for_display(
        list_runtime_tasks(
        Path(runtime_db),
        trade_date=parsed_trade_date,
        status=TaskStatus(task_status) if task_status else None,
        action_type=parsed_action_type,
        scope_type=scope_type or None,
        scope_key=scope_key or None,
        )
    )
    paged_tasks, pagination = _paginate_items(tasks, page)
    task_group_statuses = _build_task_group_statuses(tasks)
    selected_task = None
    task_history = []
    related_reviews = []
    related_notifications = []
    execution_logs = []
    listing_action_projections = []
    if selected_task_id:
        db_path = Path(runtime_db)
        selected_task = get_runtime_task(db_path, selected_task_id)
        if selected_task is not None:
            task_history = list_runtime_task_history(db_path, selected_task.task_id)
            related_reviews = [
                review
                for review in list_runtime_review_tasks(db_path)
                if review.source_task_id == selected_task.task_id
            ]
            direct_notifications = list_runtime_notification_logs(
                db_path,
                related_task_id=selected_task.task_id,
            )
            review_notifications = []
            seen_notification_ids = {log.notification_id for log in direct_notifications}
            for review in related_reviews:
                for log in list_runtime_notification_logs(
                    db_path,
                    related_review_task_id=review.review_task_id,
                ):
                    if log.notification_id not in seen_notification_ids:
                        review_notifications.append(log)
                        seen_notification_ids.add(log.notification_id)
            related_notifications = [
                (log, UI_TEXT["ops_task_notification_direct"]) for log in direct_notifications
            ] + [
                (log, UI_TEXT["ops_task_notification_via_review"]) for log in review_notifications
            ]
            execution_logs = list_runtime_execution_logs(db_path, task_id=selected_task.task_id)
            try:
                listing_action_projections = (
                    SQLiteRuntimeRepository(db_path)
                    .list_shadowbot_listing_action_task_projection(
                        task_id=selected_task.task_id,
                    )
                )
            except sqlite3.Error:
                listing_action_projections = []
    return render_tasks_page(
        runtime_db=runtime_db,
        session_user=session_user,
        tasks=paged_tasks,
        pagination=pagination,
        task_status=task_status,
        trade_date_filter=trade_date_text,
        action_type_filter=action_type_text,
        scope_type_filter=scope_type,
        scope_key_filter=scope_key,
        selected_task_id=selected_task_id,
        selected_task=selected_task,
        task_history=task_history,
        related_reviews=related_reviews,
        related_notifications=related_notifications,
        execution_logs=execution_logs,
        listing_action_projections=listing_action_projections,
        task_group_statuses=task_group_statuses,
    )


def _handle_reviews_page(method: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    query = _parse_query(environ)
    runtime_db = _runtime_db_for_request(environ)
    review_status = _first(query, "review_status", "")
    due_filter = _first(query, "due", "")
    selected_review_task_id = _first(query, "review_task_id", "")
    page = _parse_page_number(_first(query, "page", "1"))
    message = _first(query, "message", "")
    level = _first(query, "level", "info" if message else "info")
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_reviews_title"],
            description="复核中心是 Web 人工复核主入口。",
            active_path="/reviews",
            runtime_db=runtime_db,
            next_path=_append_query_to_path("/reviews", {"runtime_db": runtime_db}),
            message=UI_TEXT["ops_login_required"],
        )

    if method == "POST":
        parsed = _parse_body(environ)
        action = _first(parsed, "action", "")
        if action == "resolve_review":
            selected_review_task_id = _first(parsed, "review_task_id", "")
            review_status = _first(parsed, "review_status_filter", review_status)
            due_filter = _first(parsed, "due", due_filter)
            try:
                db_path = Path(runtime_db)
                review = get_runtime_review_task(db_path, selected_review_task_id)
                if review is None:
                    raise ValidationError(f"review task not found: {selected_review_task_id}")
                if review.review_status != ReviewTaskStatus.PENDING:
                    raise ValidationError(UI_TEXT["runtime_already_handled"])
                review_action = ReviewTaskStatus(_first(parsed, "review_status", ""))
                if review_action == ReviewTaskStatus.EXPIRED:
                    raise ValidationError("expired 只能由超时流程触发，不能由 Web 手动提交。")
                if review_action not in {
                    ReviewTaskStatus.APPROVED,
                    ReviewTaskStatus.REJECTED,
                    ReviewTaskStatus.ADJUSTED,
                    ReviewTaskStatus.CANCELLED,
                }:
                    raise ValidationError(f"不支持的复核动作: {review_action.value}")
                resolution_payload = _parse_mobile_resolution_payload(
                    _first(parsed, "resolution_payload_json", "")
                )
                source_task_status = None
                source_followup_message = ""
                if review.source_task_id:
                    source_task = get_runtime_task(db_path, review.source_task_id)
                    source_task_status = source_task_status_for_review_resolution(source_task, review_action)
                    if source_task_status is None:
                        source_followup_message = UI_TEXT["runtime_source_not_advanced"]
                resolved = resolve_runtime_review_task(
                    RuntimeReviewResolutionInputs(
                        db_path=db_path,
                        review_task_id=review.review_task_id,
                        status=review_action,
                        actor=session_user,
                        actor_source="session_user",
                        note=_first(parsed, "resolution_note", ""),
                        source_task_status=source_task_status,
                        resolution_payload=resolution_payload,
                    )
                )
                success_message = UI_TEXT["runtime_review_resolved"].format(
                    review_task_id=resolved.review_task_id,
                    status=resolved.review_status.value,
                )
                if review_action == ReviewTaskStatus.ADJUSTED:
                    success_message = f"{success_message} {UI_TEXT['runtime_adjusted_followup']}"
                if source_followup_message:
                    success_message = f"{success_message} {source_followup_message}"
                return _redirect_response(
                    _append_query_to_path(
                        "/reviews",
                        {
                            "review_task_id": resolved.review_task_id,
                            "review_status": review_status,
                            "due": due_filter,
                            "message": success_message,
                            "level": "success",
                        },
                    )
                )
            except (ValidationError, ValueError, FileNotFoundError) as exc:
                message = str(exc)
                level = "error"
        else:
            message = "不支持的复核操作。"
            level = "error"

    reviews = _sort_reviews_for_display(list_runtime_review_tasks(
        Path(runtime_db),
        status=ReviewTaskStatus(review_status) if review_status else None,
    ))
    if due_filter == "soon":
        now = datetime.now()
        reviews = [review for review in reviews if _is_review_due_soon(review, now)]
    paged_reviews, pagination = _paginate_items(reviews, page)
    selected_review = None
    source_task = None
    task_history = []
    related_notifications = []
    review_tokens = []
    if selected_review_task_id:
        db_path = Path(runtime_db)
        selected_review = get_runtime_review_task(db_path, selected_review_task_id)
        if selected_review is not None:
            if selected_review.source_task_id:
                source_task = get_runtime_task(db_path, selected_review.source_task_id)
                if source_task is not None:
                    task_history = list_runtime_task_history(db_path, source_task.task_id)
            related_notifications = list_runtime_notification_logs(
                db_path,
                related_review_task_id=selected_review.review_task_id,
            )
            review_tokens = SQLiteRuntimeRepository(db_path).list_review_tokens_by_review_task_id(
                selected_review.review_task_id
            )
    return render_reviews_page(
        runtime_db=runtime_db,
        session_user=session_user,
        reviews=paged_reviews,
        pagination=pagination,
        review_status=review_status,
        due_filter=due_filter,
        selected_review=selected_review,
        source_task=source_task,
        task_history=task_history,
        related_notifications=related_notifications,
        review_tokens=review_tokens,
        message=message,
        message_level=level,
    )


def _handle_notifications_page(environ) -> str:
    query = _parse_query(environ)
    runtime_db = _runtime_db_for_request(environ)
    send_status = _first(query, "send_status", "")
    related_review_task_id = _first(query, "related_review_task_id", "")
    channel = _first(query, "channel", "")
    notification_id = _first(query, "notification_id", "")
    page = _parse_page_number(_first(query, "page", "1"))
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_notifications_title"],
            description="查看 review 主流程生成的通知记录和发送状态。",
            active_path="/notifications",
            runtime_db=runtime_db,
            next_path=_append_query_to_path("/notifications", {"runtime_db": runtime_db}),
            message=UI_TEXT["ops_login_required"],
        )
    db_path = Path(runtime_db)
    notifications = _sort_notifications_for_display(list_runtime_notification_logs(
        db_path,
        related_review_task_id=related_review_task_id or None,
        send_status=send_status or None,
        channel=channel or None,
    ))
    paged_notifications, pagination = _paginate_items(notifications, page)
    selected_notification = get_runtime_notification_log(db_path, notification_id) if notification_id else None
    selected_review = None
    selected_task = None
    if selected_notification is not None:
        if selected_notification.related_review_task_id:
            selected_review = get_runtime_review_task(db_path, selected_notification.related_review_task_id)
        task_id = selected_notification.related_task_id or (
            selected_review.source_task_id if selected_review is not None else ""
        )
        if task_id:
            selected_task = get_runtime_task(db_path, task_id)
    return render_notifications_page(
        runtime_db=runtime_db,
        session_user=session_user,
        notifications=paged_notifications,
        pagination=pagination,
        send_status=send_status,
        related_review_task_id=related_review_task_id,
        channel=channel,
        notification_id=notification_id,
        selected_notification=selected_notification,
        selected_review=selected_review,
        selected_task=selected_task,
    )


def _handle_execution_logs_page(method: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    query = _parse_query(environ)
    runtime_db = _runtime_db_for_request(environ)
    page = _parse_page_number(_first(query, "page", "1"))
    message = _first(query, "message", "")
    level = _first(query, "level", "info" if message else "info")
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_execution_logs_title"],
            description="查看 SQLite 运行态执行日志；旧 /execution 保留 mock 执行兼容入口。",
            active_path="/execution-logs",
            runtime_db=runtime_db,
            next_path=_append_query_to_path("/execution-logs", {"runtime_db": runtime_db}),
            message=UI_TEXT["ops_login_required"],
        )
    if method == "POST":
        parsed = _parse_body(environ)
        action = _first(parsed, "action", "")
        operation_id = _first(parsed, "operation_id", "")
        try:
            executor = ShadowBotExecutor(
                SQLiteRuntimeRepository(Path(runtime_db)),
                build_shadowbot_task_runner_from_environment(),
            )
            if action == "start_shadowbot_reconcile":
                execution_attempt_id = _first(parsed, "execution_attempt_id", "") or f"RECONCILE-{uuid4().hex[:12]}"
                result = executor.start_reconcile_attempt(
                    operation_id=operation_id,
                    execution_attempt_id=execution_attempt_id,
                    runner_payload={"triggered_by": session_user, "triggered_from": "web_execution_logs"},
                )
                return _redirect_response(
                    _append_query_to_path(
                        "/execution-logs",
                        {
                            "runtime_db": runtime_db,
                            "message": f"已启动只读对账：{result.execution_attempt_id}",
                            "level": "success",
                        },
                    )
                )
            if action == "confirm_shadowbot_manual_handled":
                executor.confirm_manual_handled(
                    operation_id=operation_id,
                    actor=session_user,
                    note=_first(parsed, "manual_note", ""),
                )
                return _redirect_response(
                    _append_query_to_path(
                        "/execution-logs",
                        {
                            "runtime_db": runtime_db,
                            "message": f"已确认人工处理完成：{operation_id}",
                            "level": "success",
                        },
                    )
                )
            message = "不支持的 ShadowBot 操作。"
            level = "error"
        except (ValidationError, FileNotFoundError, OSError) as exc:
            message = str(exc)
            level = "error"
    try:
        execution_logs = _sort_execution_logs_for_display(list_runtime_execution_logs(Path(runtime_db)))
    except FileNotFoundError:
        execution_logs = []
    paged_logs, pagination = _paginate_items(execution_logs, page)
    return render_execution_logs_page(
        runtime_db=runtime_db,
        session_user=session_user,
        execution_logs=paged_logs,
        pagination=pagination,
        message=message,
        message_level=level,
        shadowbot_queue_status=_load_shadowbot_queue_status(),
    )


def _handle_business_inputs_page(method: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    query = _parse_query(environ)
    runtime_db = _runtime_db_for_request(environ)
    products_path = _request_or_default_path(query, "products_path", DEFAULT_PRODUCTS, purpose="products", allow_create=True)
    price_rules_path = _request_or_default_path(query, "price_rules_path", DEFAULT_PRICE_RULES, purpose="price_rules", allow_create=True)
    listing_rules_path = _request_or_default_path(query, "listing_rules_path", DEFAULT_LISTING_RULES, purpose="listing_rules", allow_create=True)
    capacity_plans_path = _request_or_default_path(query, "capacity_plans_path", DEFAULT_CAPACITY_PLANS, purpose="capacity_plans", allow_create=True)
    cold_storage_status_path = _request_or_default_path(query, "cold_storage_status_path", DEFAULT_COLD_STORAGE_STATUS, purpose="cold_storage_status", allow_create=True)
    platform_mappings_path = _request_or_default_path(query, "platform_mappings_path", DEFAULT_PLATFORM_MAPPINGS, purpose="platform_mappings", allow_create=True)
    active_input_tab = _normalize_business_input_tab(_first(query, "input_tab", "inventory"))
    message = _first(query, "message", "")
    level = _first(query, "level", "info" if message else "info")
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_business_inputs_title"],
            description="业务数据页用于维护商品资料、录入可销售库存，并生成后续待处理任务。",
            active_path="/business-inputs",
            runtime_db=runtime_db,
            next_path=_append_query_to_path(
                "/business-inputs",
                {
                    "runtime_db": runtime_db,
                    "products_path": products_path,
                    "price_rules_path": price_rules_path,
                    "listing_rules_path": listing_rules_path,
                    "capacity_plans_path": capacity_plans_path,
                    "cold_storage_status_path": cold_storage_status_path,
                    "platform_mappings_path": platform_mappings_path,
                    "input_tab": active_input_tab,
                },
            ),
            message=UI_TEXT["ops_login_required"],
        )
    if method == "POST":
        parsed = _parse_body(environ)
        products_path = str(_resolve_web_path(
            _first(parsed, "products_path", products_path), purpose="products", allow_create=True
        ))
        price_rules_path = str(_resolve_web_path(
            _first(parsed, "price_rules_path", price_rules_path), purpose="price_rules", allow_create=True
        ))
        listing_rules_path = str(_resolve_web_path(
            _first(parsed, "listing_rules_path", listing_rules_path), purpose="listing_rules", allow_create=True
        ))
        capacity_plans_path = str(_resolve_web_path(
            _first(parsed, "capacity_plans_path", capacity_plans_path), purpose="capacity_plans", allow_create=True
        ))
        cold_storage_status_path = str(_resolve_web_path(
            _first(parsed, "cold_storage_status_path", cold_storage_status_path), purpose="cold_storage_status", allow_create=True
        ))
        platform_mappings_path = str(_resolve_web_path(
            _first(parsed, "platform_mappings_path", platform_mappings_path), purpose="platform_mappings", allow_create=True
        ))
        active_input_tab = _normalize_business_input_tab(_first(parsed, "input_tab", active_input_tab))
        action = _first(parsed, "action", "")
        try:
            if action == "add_inventory":
                active_input_tab = "inventory"
                path = Path(products_path)
                rows = load_product_input_rows(path)
                form = validate_inventory_form({key: _first(parsed, key, "") for key in parsed})
                result = apply_inventory_input(
                    rows,
                    form,
                    inventory_authoritative=_inventory_db_is_authoritative(),
                )
                persist_product_rows(path, result.rows)
            elif action == "edit_product":
                active_input_tab = "inventory"
                path = Path(products_path)
                rows = load_product_input_rows(path)
                form = validate_product_edit_form({key: _first(parsed, key, "") for key in parsed})
                result = apply_product_edit(
                    rows,
                    form,
                    inventory_authoritative=_inventory_db_is_authoritative(),
                )
                persist_product_rows(path, result.rows)
            elif action == "add_price_rule":
                active_input_tab = "price_rules"
                path = Path(price_rules_path)
                rows = load_price_rule_input_rows(path)
                platform_rows, platform_warning = _load_platform_rows_for_business_inputs(platform_mappings_path)
                form = validate_price_rule_form(
                    {key: _first(parsed, key, "") for key in parsed},
                    existing_rows=rows,
                    is_edit=False,
                    allowed_varieties=extract_variety_options(load_product_input_rows(Path(products_path))),
                    allowed_platforms=platform_options_from_rows(platform_rows),
                )
                result = apply_price_rule_input(rows, form)
                if platform_warning:
                    result.message = f"{result.message} {platform_warning}"
                persist_price_rule_rows(path, result.rows)
            elif action == "edit_price_rule":
                active_input_tab = "price_rules"
                path = Path(price_rules_path)
                rows = load_price_rule_input_rows(path)
                platform_rows, platform_warning = _load_platform_rows_for_business_inputs(platform_mappings_path)
                form = validate_price_rule_form(
                    {key: _first(parsed, key, "") for key in parsed},
                    existing_rows=rows,
                    is_edit=True,
                    allowed_varieties=extract_variety_options(load_product_input_rows(Path(products_path))),
                    allowed_platforms=platform_options_from_rows(platform_rows),
                )
                result = apply_price_rule_edit(rows, form)
                if platform_warning:
                    result.message = f"{result.message} {platform_warning}"
                persist_price_rule_rows(path, result.rows)
            elif action == "add_listing_rule":
                active_input_tab = "listing_rules"
                path = Path(listing_rules_path)
                rows = load_listing_rule_input_rows(path)
                platform_rows, platform_warning = _load_platform_rows_for_business_inputs(platform_mappings_path)
                form = validate_listing_rule_form(
                    {key: _first(parsed, key, "") for key in parsed},
                    existing_rows=rows,
                    is_edit=False,
                    allowed_varieties=extract_variety_options(load_product_input_rows(Path(products_path))),
                    allowed_platforms=platform_options_from_rows(platform_rows),
                )
                result = apply_listing_rule_input(rows, form)
                if platform_warning:
                    result.message = f"{result.message} {platform_warning}"
                persist_listing_rule_rows(path, result.rows)
            elif action == "edit_listing_rule":
                active_input_tab = "listing_rules"
                path = Path(listing_rules_path)
                rows = load_listing_rule_input_rows(path)
                platform_rows, platform_warning = _load_platform_rows_for_business_inputs(platform_mappings_path)
                form = validate_listing_rule_form(
                    {key: _first(parsed, key, "") for key in parsed},
                    existing_rows=rows,
                    is_edit=True,
                    allowed_varieties=extract_variety_options(load_product_input_rows(Path(products_path))),
                    allowed_platforms=platform_options_from_rows(platform_rows),
                )
                result = apply_listing_rule_edit(rows, form)
                if platform_warning:
                    result.message = f"{result.message} {platform_warning}"
                persist_listing_rule_rows(path, result.rows)
            elif action == "add_capacity_plan":
                active_input_tab = "capacity_plans"
                path = Path(capacity_plans_path)
                rows = load_capacity_plan_input_rows(path)
                form = validate_capacity_plan_form(
                    {key: _first(parsed, key, "") for key in parsed},
                    existing_rows=rows,
                    is_edit=False,
                )
                result = apply_capacity_plan_input(rows, form)
                persist_capacity_plan_rows(path, result.rows)
            elif action == "edit_capacity_plan":
                active_input_tab = "capacity_plans"
                path = Path(capacity_plans_path)
                rows = load_capacity_plan_input_rows(path)
                form_values = {key: _first(parsed, key, "") for key in parsed}
                form = validate_capacity_plan_form(
                    form_values,
                    existing_rows=rows,
                    is_edit=True,
                )
                row_index_text = _first(parsed, "current_row_index", "")
                current_row_index = int(row_index_text) if row_index_text.isdigit() else None
                result = apply_capacity_plan_edit(rows, _first(parsed, "current_trade_date", ""), form, current_row_index)
                persist_capacity_plan_rows(path, result.rows)
            elif action == "add_cold_storage_status":
                active_input_tab = "cold_storage_status"
                path = Path(cold_storage_status_path)
                rows = load_cold_storage_input_rows(path)
                form = validate_cold_storage_form(
                    {key: _first(parsed, key, "") for key in parsed},
                    existing_rows=rows,
                    is_edit=False,
                )
                result = apply_cold_storage_input(rows, form)
                persist_cold_storage_rows(path, result.rows)
            elif action == "edit_cold_storage_status":
                active_input_tab = "cold_storage_status"
                path = Path(cold_storage_status_path)
                rows = load_cold_storage_input_rows(path)
                form_values = {key: _first(parsed, key, "") for key in parsed}
                form = validate_cold_storage_form(
                    form_values,
                    existing_rows=rows,
                    is_edit=True,
                )
                row_index_text = _first(parsed, "current_row_index", "")
                current_row_index = int(row_index_text) if row_index_text.isdigit() else None
                result = apply_cold_storage_edit(
                    rows,
                    _first(parsed, "current_trade_date", ""),
                    form,
                    current_row_index,
                )
                persist_cold_storage_rows(path, result.rows)
            elif action == "add_platform":
                active_input_tab = "inventory"
                path = Path(platform_mappings_path)
                ensure_platform_mappings_workbook(path)
                rows = load_platform_mapping_rows(path)
                result = apply_platform_input(rows, {key: _first(parsed, key, "") for key in parsed})
                persist_platform_mapping_rows(path, result.rows)
            elif action == "save_listing_status":
                active_input_tab = "listing_status"
                # Debug-only POST endpoint.  The business-inputs page does not
                # render a form for this action; normal updates come from the
                # validated ShadowBot READ_ONLY result importer.
                platform_name = _first(parsed, "platform_name", "").strip()
                variety = _first(parsed, "variety", "").strip()
                grade = _first(parsed, "grade", "").strip()
                if not platform_name or not variety or not grade:
                    raise ValidationError("平台、品种和等级不能为空")
                try:
                    current_price = Decimal(_first(parsed, "current_price", ""))
                    sold_qty = int(_first(parsed, "sold_qty", "0"))
                except (InvalidOperation, ValueError) as exc:
                    raise ValidationError("价格必须为有效数字，已销售数必须为非负整数") from exc
                if current_price < 0 or sold_qty < 0:
                    raise ValidationError("价格和已销售数不能为负数")
                repository = SQLiteRuntimeRepository(Path(runtime_db))
                repository.init_schema()
                existing = repository.get_listing_status(platform_name, variety, grade)
                repository.upsert_listing_status(
                    ListingStatus(
                        listing_status_id=existing.listing_status_id if existing else f"LISTING-{uuid4().hex[:16]}",
                        platform_name=platform_name,
                        internal_sku="",
                        variety=variety,
                        current_price=current_price,
                        grade=grade,
                        platform_stock_qty=100,
                        sold_qty=sold_qty,
                        online_status=_first(parsed, "online_status", "online"),
                        source="debug_web_request",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                result = SimpleNamespace(message="调试用上架状态已保存到 SQLite。", level="success")
            else:
                raise ProductInventoryInputError("未知操作，请重新提交。")
            return _redirect_response(
                _append_query_to_path(
                    "/business-inputs",
                    {
                        "runtime_db": runtime_db,
                        "products_path": products_path,
                        "price_rules_path": price_rules_path,
                        "listing_rules_path": listing_rules_path,
                        "capacity_plans_path": capacity_plans_path,
                        "cold_storage_status_path": cold_storage_status_path,
                        "platform_mappings_path": platform_mappings_path,
                        "input_tab": active_input_tab,
                        "message": result.message,
                        "level": result.level,
                    },
                )
            )
        except PathPolicyError as exc:
            products_path = price_rules_path = listing_rules_path = "[路径已拒绝]"
            capacity_plans_path = cold_storage_status_path = platform_mappings_path = "[路径已拒绝]"
            message = exc.public_message
            level = "error"
        except (
            ProductInventoryInputError,
            PriceRuleInputError,
            ListingRuleInputError,
            CapacityPlanInputError,
            ColdStorageInputError,
            PlatformMappingInputError,
            ValidationError,
            FileNotFoundError,
        ) as exc:
            message = str(exc)
            level = "error"

    try:
        product_rows = load_product_input_rows(Path(products_path))
    except (ValidationError, FileNotFoundError) as exc:
        product_rows = []
        message = message or str(exc)
        level = "error"
    try:
        price_rule_rows = load_price_rule_input_rows(Path(price_rules_path))
    except (ValidationError, FileNotFoundError) as exc:
        price_rule_rows = []
        message = message or str(exc)
        level = "error"
    try:
        listing_rule_rows = load_listing_rule_input_rows(Path(listing_rules_path))
    except (ValidationError, FileNotFoundError) as exc:
        listing_rule_rows = []
        message = message or str(exc)
        level = "error"
    try:
        capacity_plan_rows = load_capacity_plan_input_rows(Path(capacity_plans_path))
    except (ValidationError, FileNotFoundError) as exc:
        capacity_plan_rows = []
        message = message or str(exc)
        level = "error"
    try:
        cold_storage_rows = load_cold_storage_input_rows(Path(cold_storage_status_path))
    except (ValidationError, FileNotFoundError) as exc:
        cold_storage_rows = []
        message = message or str(exc)
        level = "error"
    platform_rows, platform_warning = _load_platform_rows_for_business_inputs(platform_mappings_path)
    repository = SQLiteRuntimeRepository(Path(runtime_db))
    repository.init_schema()
    listing_status_rows = [
        row for row in repository.list_listing_statuses() if has_current_platform_stock(row)
    ]
    if platform_warning:
        message = message or platform_warning
        level = "info" if level != "error" else level
    return render_business_inputs_page(
        runtime_db=runtime_db,
        session_user=session_user,
        products_path=products_path,
        price_rules_path=price_rules_path,
        listing_rules_path=listing_rules_path,
        capacity_plans_path=capacity_plans_path,
        cold_storage_status_path=cold_storage_status_path,
        platform_mappings_path=platform_mappings_path,
        product_rows=product_rows,
        variety_options=extract_variety_options(product_rows),
        price_rule_rows=price_rule_rows,
        listing_rule_rows=listing_rule_rows,
        capacity_plan_rows=capacity_plan_rows,
        cold_storage_rows=cold_storage_rows,
        platform_options=platform_options_from_rows(platform_rows),
        listing_status_rows=listing_status_rows,
        active_input_tab=active_input_tab,
        message=message,
        message_level=level,
    )


def _load_platform_rows_for_business_inputs(platform_mappings_path: str) -> tuple[list[dict[str, object]], str]:
    try:
        platform_path = Path(platform_mappings_path)
        platform_rows = load_platform_mapping_rows(platform_path)
    except (ValidationError, FileNotFoundError) as exc:
        return [], f"平台映射表读取失败，页面已使用默认平台列表。原因：{exc}"
    if not platform_rows:
        return [], "平台映射表为空，页面已使用默认平台列表。"
    return platform_rows, ""


def _handle_system_page(environ) -> str:
    query = _parse_query(environ)
    runtime_db = _runtime_db_for_request(environ)
    message = _first(query, "message", "")
    level = _first(query, "level", "info" if message else "info")
    session_user = _get_runtime_session_user(environ)
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_system_title"],
            description=UI_TEXT["ops_system_config_only"],
            active_path="/system",
            runtime_db=runtime_db,
            next_path=_append_query_to_path("/system", {"runtime_db": runtime_db}),
            message=UI_TEXT["ops_login_required"],
        )
    return render_system_page(runtime_db=runtime_db, session_user=session_user, message=message, level=level)


def _handle_system_test_feishu_notification(method: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    runtime_db = _runtime_db_for_request(environ)
    session_user = _get_runtime_session_user(environ)
    if method != "POST":
        return _redirect_response(
            _append_query_to_path(
                "/system",
                {
                    "runtime_db": runtime_db,
                    "message": "飞书测试通知必须通过 POST 触发。",
                    "level": "error",
                },
            )
        )
    if session_user is None:
        return _render_login_required_page(
            title=UI_TEXT["ops_system_title"],
            description=UI_TEXT["ops_system_config_only"],
            active_path="/system",
            runtime_db=runtime_db,
            next_path=_append_query_to_path("/system", {"runtime_db": runtime_db}),
            message=UI_TEXT["ops_login_required"],
        )
    success, result_message = _send_system_feishu_test_notification(Path(runtime_db), actor=session_user)
    return _redirect_response(
        _append_query_to_path(
            "/system",
            {
                "runtime_db": runtime_db,
                "message": result_message,
                "level": "success" if success else "error",
            },
        )
    )


def _resolve_table_path(table_name: str, previous_table_name: str, posted_path: str) -> str:
    previous_default = str(TABLE_OPTIONS.get(previous_table_name, TABLE_OPTIONS["products"])["path"])
    current_default = str(TABLE_OPTIONS[table_name]["path"])
    if not posted_path.strip():
        return current_default
    def normalize_path(value: str) -> str:
        return os.path.normcase(os.path.normpath(value)).replace("\\", "/").rstrip("/")

    normalized_posted = normalize_path(posted_path)
    normalized_previous_default = normalize_path(previous_default)
    previous_relative = normalize_path(os.path.relpath(previous_default, ROOT))
    is_previous_default = normalized_posted == normalized_previous_default or normalized_posted.endswith(
        "/" + previous_relative
    )
    if table_name != previous_table_name and is_previous_default:
        return current_default
    return posted_path


def default_dashboard_state() -> dict[str, str | bool]:
    return {
        "products": str(DEFAULT_PRODUCTS),
        "price_rules": str(DEFAULT_PRICE_RULES),
        "listing_rules": str(DEFAULT_LISTING_RULES),
        "output": str(DEFAULT_OUTPUT),
        "inventory_strategy": "conservative_v1",
        "generation_mode": "single_rule",
        "selected_rule": "",
        "task_group_id": "",
        "required_by": (
            datetime.now(DISPLAY_TIMEZONE) + timedelta(minutes=30)
        ).strftime("%Y-%m-%dT%H:%M"),
        "use_mock_ai": True,
    }


def default_table_editor_state() -> dict[str, str]:
    return {
        "table_name": "products",
        "table_path": str(DEFAULT_PRODUCTS),
    }


def default_execution_state() -> dict[str, str]:
    return {
        "tasks_path": str(DEFAULT_OUTPUT),
        "logs_output": str(DEFAULT_EXECUTION_LOGS),
        "updated_tasks_output": str(DEFAULT_EXECUTED_TASKS),
        "executor_name": "mock_executor",
    }


def default_manual_state() -> dict[str, str]:
    return {
        "tasks_path": str(DEFAULT_MANUAL_TASKS),
        "output_path": str(DEFAULT_MANUAL_TASKS),
        "actor": "manual_operator",
        "note": "",
    }


def default_runtime_state() -> dict[str, str]:
    return {
        "runtime_db": str(DEFAULT_RUNTIME_DB),
        "review_task_id": "",
        "task_id": "",
        "notification_id": "",
        "task_trade_date": "",
        "task_status": "",
        "review_trade_date": "",
        "review_status": "",
        "notification_related_review_task_id": "",
        "notification_send_status": "",
    }


def render_dashboard_page(
    *,
    params: dict[str, str | bool],
    message: str,
    message_level: str,
    validation_summary: ValidationSummary | None,
    generation_summary: TaskGenerationSummary | None,
    preview_ready: bool,
    session_user: str | None = None,
    rule_options: list[tuple[str, str]] | None = None,
) -> str:
    summary_html = ""
    if validation_summary is not None:
        summary_html = f"""
        <section class="panel">
          <h2>{escape(UI_TEXT["data_summary"])}</h2>
          <div class="metrics">
            <div class="metric"><span class="label">products</span><strong>{len(validation_summary.products)}</strong></div>
            <div class="metric"><span class="label">price_rules</span><strong>{validation_summary.price_rules_count}</strong></div>
            <div class="metric"><span class="label">listing_rules</span><strong>{validation_summary.listing_rules_count}</strong></div>
          </div>
        </section>
        """

    tasks_html = ""
    if generation_summary is not None:
        confirm_html = ""
        rows = []
        listing_override_index = 0
        for task in generation_summary.tasks:
            target_price_html = escape(
                str(task.target_price) if task.target_price is not None else "-"
            )
            target_inventory_html = escape(
                str(task.target_inventory)
                if task.target_inventory is not None
                else "-"
            )
            if preview_ready and task.action_type is TaskActionType.SET_ONLINE:
                listing_default_source = str(
                    task.decision_trace.get("listing_target_default_source")
                    or "platform_snapshot"
                )
                uses_platform_snapshot = listing_default_source == "platform_snapshot"
                price_default_title = (
                    "默认值来自最新平台快照价格"
                    if uses_platform_snapshot
                    else "新商品无平台快照，默认值来自商品基础成本"
                )
                inventory_default_title = (
                    "默认值来自最新平台快照库存"
                    if uses_platform_snapshot
                    else "新商品无平台快照，默认值来自商品当前库存"
                )
                override_key = json.dumps(
                    list(listing_task_override_key(task)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                price_value = (
                    format(task.target_price, ".2f")
                    if task.target_price is not None
                    else ""
                )
                inventory_value = (
                    str(task.target_inventory)
                    if task.target_inventory is not None
                    else ""
                )
                target_price_html = f"""
                  <input type="hidden" name="listing_override_key_{listing_override_index}" value="{escape(override_key)}">
                  <input
                    class="task-target-number"
                    name="listing_target_price_{listing_override_index}"
                    type="number"
                    min="0.01"
                    step="0.01"
                    required
                    value="{escape(price_value)}"
                    aria-label="{escape(str(task.internal_sku or ''))} 目标价格"
                    title="{escape(price_default_title)}"
                  >
                """
                target_inventory_html = f"""
                  <input
                    class="task-target-number"
                    name="listing_target_inventory_{listing_override_index}"
                    type="number"
                    min="0"
                    step="1"
                    required
                    value="{escape(inventory_value)}"
                    aria-label="{escape(str(task.internal_sku or ''))} 目标库存"
                    title="{escape(inventory_default_title)}"
                  >
                """
                listing_override_index += 1
            elif task.action_type is TaskActionType.SET_OFFLINE:
                target_price_html = "不适用"
                target_inventory_html = "不适用"
            rows.append(
                "<tr>"
                f"<td>{escape(str(task.internal_sku or ''))}</td>"
                f"<td>{escape(task.action_type.value)}</td>"
                f"<td>{escape(task.task_status.value)}</td>"
                f"<td>{escape(str(task.platform_name or ''))}</td>"
                f"<td>{escape(str(task.expected_old_price) if task.expected_old_price is not None else '-')}</td>"
                f"<td>{target_price_html}</td>"
                f"<td>{target_inventory_html}</td>"
                f"<td>{escape(task.pricing_source.value if task.pricing_source else '-')}</td>"
                f"<td>{escape(format_display_datetime(task.required_by))}</td>"
                f"<td>{escape(_task_price_calculation_summary(task))}</td>"
                "</tr>"
            )
        rows_html = "".join(rows) or f"<tr><td colspan='10'>{escape(UI_TEXT['no_tasks'])}</td></tr>"
        task_counts = "".join(
            f"<div class='metric'><span class='label'>{escape(name)}</span><strong>{count}</strong></div>"
            for name, count in generation_summary.task_counts.items()
        )
        ignored_candidates = generation_summary.ignored_candidates
        if ignored_candidates:
            task_counts += (
                "<div class='metric'><span class='label'>已忽略</span>"
                f"<strong>{len(ignored_candidates)}</strong></div>"
            )
        listing_state_labels = {
            "online": "上架",
            "offline": "下架",
            "missing": "无平台状态",
            "unavailable": "库存为 0（不参与）",
            "unknown": "状态未知",
        }
        ignored_rows_html = "".join(
            "<tr>"
            f"<td>{escape(candidate.internal_sku)}</td>"
            f"<td>{escape(candidate.product_name)}</td>"
            f"<td>{escape(candidate.grade or '-')}</td>"
            f"<td>{escape(candidate.platform_name)}</td>"
            f"<td>{escape(display_enum_label('action_type', candidate.action_type.value))}</td>"
            f"<td>{escape(listing_state_labels.get(candidate.current_listing_state, candidate.current_listing_state))}</td>"
            f"<td>{escape(candidate.reason)}</td>"
            "</tr>"
            for candidate in ignored_candidates
        )
        ignored_candidates_html = (
            f"""
          <div class="ignored-task-candidates">
            <h3>已忽略商品（{len(ignored_candidates)}）</h3>
            <p class="subtle">以下商品命中了所选规则，但当前平台状态不符合任务生成条件，不会写入任务中心。</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>商品</th>
                    <th>等级</th>
                    <th>平台</th>
                    <th>原任务类型</th>
                    <th>当前平台状态</th>
                    <th>忽略原因</th>
                  </tr>
                </thead>
                <tbody>{ignored_rows_html}</tbody>
              </table>
            </div>
          </div>
            """
            if ignored_candidates
            else ""
        )
        confirm_form_open = ""
        confirm_form_close = ""
        confirm_action_html = ""
        if preview_ready:
            mock_ai_hidden = "<input type='hidden' name='use_mock_ai' value='on'>" if params["use_mock_ai"] else ""
            listing_input_hint = (
                "<p class='subtle'>上架任务的目标价格和目标库存必须在创建前确认；"
                "有平台快照时默认读取同一条最新快照；新商品没有平台快照时，"
                "expected_old_price 和目标价格默认使用基础成本，目标库存默认使用商品当前库存。"
                "下架任务仅变更状态，不携带目标价格和目标库存。</p>"
                if listing_override_index
                else ""
            )
            confirm_form_open = "<form method='post'>"
            confirm_form_close = "</form>"
            confirm_html = f"""
          <div class="confirm-box">
            <p>{escape(UI_TEXT["preview_ready"])}</p>
            {listing_input_hint}
            <input type="hidden" name="inventory_strategy" value="{escape(str(params["inventory_strategy"]))}">
            <input type="hidden" name="generation_mode" value="{escape(str(params.get("generation_mode", "single_rule")))}">
            <input type="hidden" name="selected_rule" value="{escape(str(params.get("selected_rule", "")))}">
            <input type="hidden" name="task_group_id" value="{escape(str(params.get("task_group_id", "")))}">
            <input type="hidden" name="required_by" value="{escape(str(params.get("required_by", "")))}">
            <input type="hidden" name="listing_override_count" value="{listing_override_index}">
            {mock_ai_hidden}
          </div>
        """
            confirm_action_html = f"""
            <div class="actions">
              <button class="primary" type="submit" name="action" value="confirm_generate">{escape(UI_TEXT["confirm_button"])}</button>
            </div>
        """
        tasks_html = f"""
        <section class="panel">
          <h2>{escape(UI_TEXT["task_result"])}</h2>
          <div class="metrics">{task_counts}</div>
          {confirm_form_open}
          {confirm_html}
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>action</th>
                  <th>status</th>
                  <th>platform</th>
                  <th>expected_old_price</th>
                  <th>目标价格</th>
                  <th>目标库存</th>
                  <th>pricing_source</th>
                  <th>required_by</th>
                  <th>calculation</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
          </div>
          {ignored_candidates_html}
          {confirm_action_html}
          {confirm_form_close}
        </section>
        """

    checked = "checked" if params["use_mock_ai"] else ""
    inventory_strategy_options = [
        ("conservative_v1", UI_TEXT["inventory_strategy_conservative"]),
        ("balanced_v1", UI_TEXT["inventory_strategy_balanced"]),
    ]
    inventory_strategy_html = "".join(
        (
            f"<option value='{escape(value)}' {'selected' if params['inventory_strategy'] == value else ''}>"
            f"{escape(label)}</option>"
        )
        for value, label in inventory_strategy_options
    )
    generation_mode = str(params.get("generation_mode", "single_rule"))
    generation_mode_html = "".join(
        f"<option value='{value}'{' selected' if generation_mode == value else ''}>{escape(label)}</option>"
        for value, label in (
            ("batch", UI_TEXT["generation_mode_batch"]),
            ("single_rule", UI_TEXT["generation_mode_single_rule"]),
        )
    )
    selected_rule = str(params.get("selected_rule", ""))
    rule_options_html = (
        f"<option value=''>{escape(UI_TEXT['selected_rule_placeholder'])}</option>"
        + "".join(
            f"<option value='{escape(value)}'{' selected' if selected_rule == value else ''}>{escape(label)}</option>"
            for value, label in (rule_options or [])
        )
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["site_title"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell">
    {_hero(UI_TEXT["dashboard_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/task-generator")}
    {_session_toolbar("", session_user, "/task-generator")}
    {_compat_notice(UI_TEXT["legacy_root_notice"])}
    {_banner(message, message_level)}
    <div class="layout">
      <section class="panel">
        <h2>{escape(UI_TEXT["task_panel_title"])}</h2>
        <form method="post" class="grid">
          <div class="field">
            <label for="generation_mode">{escape(UI_TEXT["generation_mode"])}</label>
            <select id="generation_mode" name="generation_mode">{generation_mode_html}</select>
          </div>
          <div class="field">
            <label for="selected_rule">{escape(UI_TEXT["selected_rule"])}</label>
            <select id="selected_rule" name="selected_rule">{rule_options_html}</select>
            <p class="subtle">{escape(UI_TEXT["single_rule_hint"])}</p>
          </div>
          <div class="field">
            <label for="required_by">{escape(UI_TEXT["task_group_required_by"])}</label>
            <input id="required_by" name="required_by" type="datetime-local" value="{escape(str(params.get("required_by", "")))}">
            <p class="subtle">{escape(UI_TEXT["task_group_hint"])}</p>
          </div>
          <div class="field">
            <label for="inventory_strategy">{escape(UI_TEXT["inventory_strategy"])}</label>
            <select id="inventory_strategy" name="inventory_strategy">{inventory_strategy_html}</select>
          </div>
          <label class="checkbox">
            <input name="use_mock_ai" type="checkbox" {checked}>
            {escape(UI_TEXT["use_mock_ai"])}
          </label>
          <div class="actions">
            <button class="primary" type="submit" name="action" value="preview">{escape(UI_TEXT["preview_button"])}</button>
          </div>
        </form>
      </section>
    </div>
    {summary_html}
    {tasks_html}
  </main>
</body>
</html>
"""


def render_table_editor_page(
    *,
    params: dict[str, str],
    headers: list[str],
    records: list[dict[str, object]],
    message: str,
    message_level: str,
    table_issues: list[tuple[int, str, str]],
) -> str:
    issue_map = {(row_number - 2, field_name): detail for row_number, field_name, detail in table_issues}
    issues_html = ""
    if table_issues:
        issue_items = "".join(
            f"<li>\u7b2c {row_number} \u884c {escape(TABLE_HEADER_LABELS.get(field_name, field_name))}: {escape(detail)}</li>"
            for row_number, field_name, detail in table_issues
        )
        issues_html = f"<ul class='issue-list'>{issue_items}</ul>"

    rows = records + [{header: "" for header in headers} for _ in range(3)]
    row_html: list[str] = []
    for row_index, row in enumerate(rows):
        cells = []
        for header in headers:
            value = "" if row.get(header) is None else str(row.get(header))
            issue_detail = issue_map.get((row_index, header), "")
            issue_class = " cell-input invalid" if issue_detail else " cell-input"
            issue_note = f"<div class='cell-issue'>{escape(issue_detail)}</div>" if issue_detail else ""
            cells.append(
                f"<td><input class='{issue_class.strip()}' type='text' name='cell__{row_index}__{header}' value='{escape(value)}'>{issue_note}</td>"
            )
        row_html.append(f"<tr><td class='row-index'>{row_index + 1}</td>{''.join(cells)}</tr>")

    options_html = "".join(
        f"<option value='{name}' data-default-path='{escape(str(meta['path']))}' {'selected' if params['table_name'] == name else ''}>{escape(str(meta['label']))}</option>"
        for name, meta in TABLE_OPTIONS.items()
    )
    header_html = "".join(
        f"<th title='{escape(header)}'>{escape(TABLE_HEADER_LABELS.get(header, header))}</th>"
        for header in headers
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["tables_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["table_editor_title"], UI_TEXT["table_editor_lede"])}
    {navigation("/tables")}
    {_compat_notice(UI_TEXT["legacy_tables_notice"])}
    {_banner(message, message_level)}
    <section class="panel">
      <h2>{escape(UI_TEXT["table_picker"])}</h2>
      <form method="post" class="grid two-col">
        <input type="hidden" name="previous_table_name" value="{escape(params['table_name'])}">
        <div class="field">
          <label for="table_name">{escape(UI_TEXT["table_type"])}</label>
          <select id="table_name" name="table_name">{options_html}</select>
        </div>
        <div class="field">
          <label for="table_path">{escape(UI_TEXT["table_path"])}</label>
          <input id="table_path" name="table_path" type="text" value="{escape(params['table_path'])}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit" name="action" value="load">{escape(UI_TEXT["load_button"])}</button>
          <button class="primary" type="submit" name="action" value="save">{escape(UI_TEXT["save_button"])}</button>
        </div>
      </form>
    </section>

    <section class="panel">
      <h2>{escape(str(TABLE_OPTIONS[params['table_name']]['label']))}</h2>
      <p class="subtle">{escape(UI_TEXT["table_hint"])}</p>
      {issues_html}
      <form method="post">
        <input type="hidden" name="previous_table_name" value="{escape(params['table_name'])}">
        <input type="hidden" name="table_name" value="{escape(params['table_name'])}">
        <input type="hidden" name="table_path" value="{escape(params['table_path'])}">
        <div class="table-wrap">
          <table class="editor-table">
            <thead>
              <tr>
                <th>#</th>
                {header_html}
              </tr>
            </thead>
            <tbody>
              {''.join(row_html)}
            </tbody>
          </table>
        </div>
        <div class="actions sticky-actions">
          <button class="primary" type="submit" name="action" value="save">{escape(UI_TEXT["save_button"])}</button>
        </div>
      </form>
    </section>
  </main>
  <script>
    const tableNameSelect = document.getElementById("table_name");
    const tablePathInput = document.getElementById("table_path");
    if (tableNameSelect && tablePathInput) {{
      tableNameSelect.addEventListener("change", () => {{
        const option = tableNameSelect.options[tableNameSelect.selectedIndex];
        const defaultPath = option.getAttribute("data-default-path");
        if (defaultPath) {{
          tablePathInput.value = defaultPath;
        }}
      }});
    }}
  </script>
</body>
</html>
"""


def render_execution_page(
    *,
    params: dict[str, str],
    message: str,
    message_level: str,
    execution_summary: ExecutionSimulationSummary | None,
) -> str:
    result_html = ""
    if execution_summary is not None:
        rows_html = "".join(
            "<tr>"
            f"<td>{escape(log.task_id)}</td>"
            f"<td>{escape(log.executor_name)}</td>"
            f"<td>{escape('success' if log.success_flag else 'failed')}</td>"
            f"<td>{escape(log.raw_output)}</td>"
            "</tr>"
            for log in execution_summary.logs[:12]
        )
        result_html = f"""
        <section class="panel">
          <h2>{escape(UI_TEXT["execution_result"])}</h2>
          <div class="metrics">
            <div class="metric"><span class="label">logs</span><strong>{len(execution_summary.logs)}</strong></div>
            <div class="metric"><span class="label">success</span><strong>{execution_summary.success_count}</strong></div>
          </div>
          <p class="subtle">{escape(UI_TEXT["execution_logs_file"])}: {escape(str(execution_summary.logs_output_path))}</p>
          <p class="subtle">{escape(UI_TEXT["execution_updated_tasks_file"])}: {escape(str(execution_summary.updated_tasks_output_path or '-'))}</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>task_id</th>
                  <th>executor</th>
                  <th>result</th>
                  <th>raw_output</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
          </div>
        </section>
        """

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["execution_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell">
    {_hero(UI_TEXT["execution_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/execution")}
    {_compat_notice(UI_TEXT["legacy_execution_notice"])}
    {_banner(message, message_level)}
    <section class="panel">
      <h2>{escape(UI_TEXT["execution_panel_title"])}</h2>
      <form method="post" class="grid">
        <div class="field">
          <label for="tasks_path">{escape(UI_TEXT["execution_source_path"])}</label>
          <input id="tasks_path" name="tasks_path" type="text" value="{escape(params["tasks_path"])}">
        </div>
        <div class="field">
          <label for="logs_output">{escape(UI_TEXT["execution_logs_path"])}</label>
          <input id="logs_output" name="logs_output" type="text" value="{escape(params["logs_output"])}">
        </div>
        <div class="field">
          <label for="updated_tasks_output">{escape(UI_TEXT["execution_tasks_path"])}</label>
          <input id="updated_tasks_output" name="updated_tasks_output" type="text" value="{escape(params["updated_tasks_output"])}">
        </div>
        <div class="field">
          <label for="executor_name">{escape(UI_TEXT["executor_name"])}</label>
          <input id="executor_name" name="executor_name" type="text" value="{escape(params["executor_name"])}">
        </div>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["simulate_button"])}</button>
        </div>
      </form>
    </section>
    {result_html}
  </main>
</body>
</html>
"""


def render_manual_intervention_page(
    *,
    params: dict[str, str],
    message: str,
    message_level: str,
    tasks,
) -> str:
    rows_html = "".join(
        "<tr>"
        f"<td>{escape(task.task_id)}</td>"
        f"<td>{escape(task.internal_sku or '-')}</td>"
        f"<td>{escape(task.action_type.value)}</td>"
        f"<td>{escape(task.task_status.value)}</td>"
        f"<td>{escape(task.result_message or '-')}</td>"
        f"<td>{escape(str(task.required_by) if task.required_by is not None else '-')}</td>"
        f"<td>{escape(UI_TEXT['manual_readonly_notice'])}</td>"
        "</tr>"
        for task in tasks
    ) or f"<tr><td colspan='7'>{escape(UI_TEXT['manual_empty'])}</td></tr>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["manual_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["manual_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/manual-intervention")}
    {_compat_notice(UI_TEXT["legacy_manual_notice"])}
    {_banner(message, message_level)}
    <section class="panel">
      <h2>{escape(UI_TEXT["manual_panel_title"])}</h2>
      <p class="subtle">{escape(UI_TEXT["manual_readonly_notice"])}</p>
      <form method="post" class="grid two-col">
        <div class="field">
          <label for="tasks_path">{escape(UI_TEXT["manual_tasks_path"])}</label>
          <input id="tasks_path" name="tasks_path" type="text" value="{escape(params["tasks_path"])}">
        </div>
        <div class="field">
          <label for="output_path">{escape(UI_TEXT["manual_output_path"])}</label>
          <input id="output_path" name="output_path" type="text" value="{escape(params["output_path"])}">
        </div>
        <div class="field">
          <label for="actor">{escape(UI_TEXT["manual_actor"])}</label>
          <input id="actor" name="actor" type="text" value="{escape(params["actor"])}">
        </div>
        <div class="field">
          <label for="note">{escape(UI_TEXT["manual_note"])}</label>
          <input id="note" name="note" type="text" value="{escape(params["note"])}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit" name="action" value="load">{escape(UI_TEXT["manual_load_button"])}</button>
        </div>
      </form>
    </section>
    <section class="panel">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>task_id</th>
              <th>SKU</th>
              <th>action</th>
              <th>status</th>
              <th>message</th>
              <th>required_by</th>
              <th>mode</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>
"""


def render_mobile_review_page(*, detail, raw_token: str) -> str:
    review = detail.review_task
    source_status = (
        display_enum_label("task_status", detail.source_task.task_status.value)
        if detail.source_task is not None
        else "-"
    )
    payload_rows = _mobile_payload_summary_rows(review.review_payload)
    actions_html = "".join(
        f"<button class='primary' type='submit' name='action' value='{escape(action)}'>"
        f"{escape(_review_action_display_label(review, detail.source_task, ReviewTaskStatus(action)))}</button>"
        for action in detail.allowed_actions
    )
    if not actions_html:
        actions_html = f"<p class='subtle'>{escape(UI_TEXT['mobile_review_handled'])}</p>"
    emergency_price_html = (
        """
        <div class="field">
          <label for="target_price">改价目标（仅选择“改价到”时填写）</label>
          <input id="target_price" name="target_price" inputmode="decimal" placeholder="不得低于基础成本">
        </div>
        """
        if review.review_type == "emergency_protection"
        else ""
    )
    review_type_label = display_enum_label("review_type", review.review_type)
    if (
        review.review_type == "emergency_protection"
        and str(review.review_payload.get("severity") or "").upper() == "S4"
    ):
        review_type_label = "极端低价处理"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["mobile_review_title"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell">
    {_hero(UI_TEXT["mobile_review_title"], UI_TEXT["mobile_review_lede"])}
    <section class="panel">
      <h2>{escape(review_type_label)}</h2>
      <div class="metrics">
        <div class="metric"><span class="label">业务日期</span><strong>{escape(str(review.trade_date or "-"))}</strong></div>
        <div class="metric"><span class="label">当前状态</span><strong>{escape(display_enum_label("review_status", review.review_status.value))}</strong></div>
      </div>
      <p class="subtle">处理对象：{escape(format_object_scope(review.scope_type, review.scope_key))}</p>
      <p class="subtle">原因：{escape(review.reason or "-")}</p>
      <p class="subtle">截止时间：{escape(format_display_datetime(review.required_by))}</p>
      <p class="subtle">{escape(UI_TEXT["mobile_review_source_status"])}: {escape(source_status)}</p>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["mobile_review_payload_summary"])}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>field</th><th>value</th></tr></thead>
          <tbody>{payload_rows}</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["mobile_review_submit"])}</h2>
      <form method="post" action="/mobile/review/{escape(review.review_task_id)}/resolve" class="grid">
        <input type="hidden" name="token" value="{escape(raw_token)}">
        <div class="field">
          <label for="resolution_note">{escape(UI_TEXT["mobile_review_note"])}</label>
          <textarea id="resolution_note" name="resolution_note"></textarea>
        </div>
        {emergency_price_html}
        <div class="field">
          <label for="resolution_payload_json">{escape(UI_TEXT["mobile_review_payload"])}</label>
          <textarea id="resolution_payload_json" name="resolution_payload_json" placeholder='{{}}'></textarea>
        </div>
        <div class="actions">
          {actions_html}
        </div>
      </form>
    </section>
  </main>
</body>
</html>
"""


def render_mobile_review_error_page(message: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["mobile_review_title"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell">
    {_hero(UI_TEXT["mobile_review_title"], UI_TEXT["mobile_review_lede"])}
    {_banner(message, "error")}
  </main>
</body>
</html>
"""


def render_mobile_review_resolved_page(review_task_id: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["mobile_review_title"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell">
    {_hero(UI_TEXT["mobile_review_title"], UI_TEXT["mobile_review_lede"])}
    {_banner(UI_TEXT["mobile_review_handled"], "success")}
    <section class="panel">
      <h2>{escape(UI_TEXT["mobile_review_handled"])}</h2>
      <p class="subtle">review_task_id: {escape(review_task_id)}</p>
      <p class="subtle">该页面不会再次展示操作按钮，避免刷新或重复提交。</p>
    </section>
  </main>
</body>
</html>
"""


def render_runtime_page(
    *,
    params: dict[str, str],
    message: str,
    message_level: str,
    tasks,
    reviews,
    notifications,
    session_user: str | None,
    selected_review,
    selected_task,
    selected_notification,
    task_history,
    login_csrf_token: str | None = None,
) -> str:
    if session_user is None:
        login_csrf_token = login_csrf_token or _issue_login_csrf_token()
        login_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_login_title"])}</h2>
      <form method="post" action="/runtime/login" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="csrf_token" value="{escape(login_csrf_token)}">
        <div class="field">
          <label for="runtime_username">{escape(UI_TEXT["runtime_login_user"])}</label>
          <input id="runtime_username" name="username" type="text" value="{escape(_runtime_admin_user())}">
        </div>
        <div class="field">
          <label for="runtime_password">{escape(UI_TEXT["runtime_login_password"])}</label>
          <input id="runtime_password" name="password" type="password" value="">
        </div>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_login_button"])}</button>
        </div>
      </form>
    </section>
"""
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["runtime_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["runtime_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/runtime")}
    {_banner(message, message_level)}
    {login_panel}
  </main>
</body>
</html>
"""

    base_filters = {
        "task_trade_date": params.get("task_trade_date", ""),
        "task_status": params.get("task_status", ""),
        "review_trade_date": params.get("review_trade_date", ""),
        "review_status": params.get("review_status", ""),
        "notification_related_review_task_id": params.get("notification_related_review_task_id", ""),
        "notification_send_status": params.get("notification_send_status", ""),
    }
    task_status_options = "".join(
        f"<option value='{escape(option.value)}'{' selected' if params.get('task_status') == option.value else ''}>{escape(option.value)}</option>"
        for option in TaskStatus
    )
    review_status_options = "".join(
        f"<option value='{escape(option.value)}'{' selected' if params.get('review_status') == option.value else ''}>{escape(option.value)}</option>"
        for option in ReviewTaskStatus
    )
    notification_status_options = "".join(
        f"<option value='{escape(option.value)}'{' selected' if params.get('notification_send_status') == option.value else ''}>{escape(option.value)}</option>"
        for option in NotificationSendStatus
    )

    task_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], task_id=task.task_id, review_task_id=params.get('review_task_id', ''), notification_id=params.get('notification_id', ''), **base_filters))}'>{escape(task.task_id)}</a></td>"
        f"<td>{escape(task.trade_date.isoformat() if task.trade_date else '-')}</td>"
        f"<td>{escape(task.scope_type)}:{escape(task.scope_key)}</td>"
        f"<td>{escape(task.action_type.value)}</td>"
        f"<td>{escape(task.task_status.value)}</td>"
        f"<td>{escape(task.internal_sku or '-')}</td>"
        f"<td>{escape(task.platform_name or '-')}</td>"
        "</tr>"
        for task in tasks[:PAGE_SIZE]
    ) or "<tr><td colspan='7'>-</td></tr>"
    review_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], review_task_id=review.review_task_id, task_id=review.source_task_id or '', notification_id=params.get('notification_id', ''), **base_filters))}'>{escape(review.review_task_id)}</a></td>"
        f"<td>{escape(review.trade_date.isoformat() if review.trade_date else '-')}</td>"
        f"<td>{escape(review.scope_type)}:{escape(review.scope_key)}</td>"
        f"<td>{escape(review.review_type)}</td>"
        f"<td>{escape(review.review_status.value)}</td>"
        f"<td>{escape(review.source_task_id or '-')}</td>"
        f"<td>{escape(review.reason)}</td>"
        "</tr>"
        for review in reviews[:PAGE_SIZE]
    ) or "<tr><td colspan='7'>-</td></tr>"
    notification_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], notification_id=log.notification_id, review_task_id=params.get('review_task_id', ''), task_id=params.get('task_id', ''), **base_filters))}'>{escape(log.notification_id)}</a></td>"
        f"<td>{escape(log.related_task_id or '-')}</td>"
        f"<td>{escape(log.related_review_task_id or '-')}</td>"
        f"<td>{escape(log.recipient_type)}:{escape(log.recipient)}</td>"
        f"<td>{escape(log.channel)}</td>"
        f"<td>{escape(log.send_status)}</td>"
        f"<td>{escape(log.sent_at.isoformat() if log.sent_at else '-')}</td>"
        f"<td>{escape(log.message)}</td>"
        "</tr>"
        for log in notifications[:PAGE_SIZE]
    ) or f"<tr><td colspan='8'>{escape(UI_TEXT['runtime_notification_none'])}</td></tr>"

    selected_review_html = ""
    if selected_review is not None:
        next_source_status = _review_source_status_hint(
            selected_review,
            selected_task,
        )
        details = [
            ("review_task_id", selected_review.review_task_id),
            ("review_type", selected_review.review_type),
            ("review_status", selected_review.review_status.value),
            ("scope", f"{selected_review.scope_type}:{selected_review.scope_key}"),
            ("source_task_id", selected_review.source_task_id or "-"),
            ("required_by", selected_review.required_by.isoformat() if selected_review.required_by else "-"),
            ("reason", selected_review.reason or "-"),
            ("resolved_by", selected_review.resolved_by or "-"),
            ("resolved_at", selected_review.resolved_at.isoformat() if selected_review.resolved_at else "-"),
        ]
        detail_rows = "".join(f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>" for label, value in details)
        resolution_pre = escape(_to_pretty_json(selected_review.resolution_payload))
        review_payload_pre = escape(_to_pretty_json(selected_review.review_payload))
        related_notification_rows = "".join(
            "<tr>"
            f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], notification_id=log.notification_id, review_task_id=selected_review.review_task_id, task_id=selected_review.source_task_id or '', **base_filters))}'>{escape(log.notification_id)}</a></td>"
            f"<td>{escape(log.recipient_type)}:{escape(log.recipient)}</td>"
            f"<td>{escape(log.channel)}</td>"
            f"<td>{escape(log.send_status)}</td>"
            f"<td>{escape(log.sent_at.isoformat() if log.sent_at else '-')}</td>"
            f"<td>{escape(log.message)}</td>"
            "</tr>"
            for log in notifications
            if log.related_review_task_id == selected_review.review_task_id
        ) or "<tr><td colspan='6'>-</td></tr>"
        selected_review_html = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_detail"])}</h2>
      <div class="table-wrap"><table><tbody>{detail_rows}</tbody></table></div>
      <div class="grid two-col">
        <div class="field">
          <label>review_payload_json</label>
          <pre>{review_payload_pre}</pre>
        </div>
        <div class="field">
          <label>resolution_payload_json</label>
          <pre>{resolution_pre}</pre>
        </div>
      </div>
      <h3>{escape(UI_TEXT["runtime_notifications"])}</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>notification_id</th><th>recipient</th><th>channel</th><th>status</th><th>sent_at</th><th>message</th></tr></thead>
          <tbody>{related_notification_rows}</tbody>
        </table>
      </div>
    </section>
"""
        if selected_review.review_status == ReviewTaskStatus.PENDING:
            resolve_status_options = _review_status_options(
                selected_review,
                selected_task,
                use_display_labels=False,
            )
            hidden_filters = "".join(
                f"<input type='hidden' name='{escape(key)}' value='{escape(value)}'>"
                for key, value in base_filters.items()
            )
            selected_review_html += f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_handle"])}</h2>
      <form method="post" class="grid">
        <input type="hidden" name="action" value="resolve_review">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="review_task_id" value="{escape(selected_review.review_task_id)}">
        <input type="hidden" name="task_id" value="{escape(selected_review.source_task_id or '')}">
        <input type="hidden" name="notification_id" value="{escape(params.get("notification_id", ""))}">
        {hidden_filters}
        <div class="field">
          <label for="review_status">review_status</label>
          <select id="review_status" name="review_status">{resolve_status_options}</select>
        </div>
        <div class="field">
          <label for="reviewer_code">{escape(UI_TEXT["runtime_reviewer_code"])}</label>
          <input id="reviewer_code" name="reviewer_code" type="text" value="">
        </div>
        <div class="field">
          <label for="resolution_note">{escape(UI_TEXT["runtime_resolution_note"])}</label>
          <input id="resolution_note" name="resolution_note" type="text" value="">
        </div>
        <div class="field">
          <label for="resolution_payload_json">{escape(UI_TEXT["runtime_resolution_payload"])}</label>
          <textarea id="resolution_payload_json" name="resolution_payload_json" rows="8">{{}}</textarea>
        </div>
        <p class="subtle">来源任务后续状态：{escape(next_source_status)}</p>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_submit_review"])}</button>
        </div>
      </form>
    </section>
"""

    history_panel = ""
    if selected_task is not None:
        history_rows = "".join(
            "<tr>"
            f"<td>{escape(item.changed_at.isoformat())}</td>"
            f"<td>{escape(item.from_status.value if item.from_status else '-')}</td>"
            f"<td>{escape(item.to_status.value)}</td>"
            f"<td>{escape(item.changed_by)}</td>"
            f"<td>{escape(item.reason)}</td>"
            f"<td>{_metadata_summary_rows(item.metadata)}</td>"
            "</tr>"
            for item in task_history
        ) or f"<tr><td colspan='6'>{escape(UI_TEXT['runtime_history_empty'])}</td></tr>"
        history_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_history"])}: {escape(selected_task.task_id)}</h2>
      <p class="subtle">{escape(UI_TEXT["runtime_history_summary"])}</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>changed_at</th><th>from</th><th>to</th><th>changed_by</th><th>reason</th><th>summary</th></tr></thead>
          <tbody>{history_rows}</tbody>
        </table>
      </div>
    </section>
"""

    selected_notification_panel = ""
    if selected_notification is not None:
        notification_details = [
            ("notification_id", selected_notification.notification_id),
            ("related_task_id", selected_notification.related_task_id or "-"),
            ("related_review_task_id", selected_notification.related_review_task_id or "-"),
            ("recipient_type", selected_notification.recipient_type),
            ("recipient", selected_notification.recipient),
            ("channel", selected_notification.channel),
            ("send_status", selected_notification.send_status),
            ("sent_at", selected_notification.sent_at.isoformat() if selected_notification.sent_at else "-"),
            ("message", selected_notification.message or "-"),
            ("error_message", selected_notification.error_message or "-"),
        ]
        notification_detail_rows = "".join(
            f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>"
            for label, value in notification_details
        )
        selected_notification_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_notification_detail"])}</h2>
      <div class="table-wrap"><table><tbody>{notification_detail_rows}</tbody></table></div>
    </section>
"""

    filter_panels = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_panel_title"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <div class="field">
          <label for="runtime_db">{escape(UI_TEXT["runtime_db_path"])}</label>
          <input id="runtime_db" name="runtime_db" type="text" value="{escape(params["runtime_db"])}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_load_button"])}</button>
        </div>
      </form>
      <div class="actions">
        <span class="subtle">{escape(UI_TEXT["runtime_session_user"])}: {escape(session_user)}</span>
        <form method="post" action="/runtime/logout">
          <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_logout_button"])}</button>
        </form>
      </div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_task_filters"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <div class="field">
          <label for="task_trade_date">trade_date</label>
          <input id="task_trade_date" name="task_trade_date" type="text" value="{escape(params.get("task_trade_date", ""))}" placeholder="YYYY-MM-DD">
        </div>
        <div class="field">
          <label for="task_status">task_status</label>
          <select id="task_status" name="task_status"><option value="">-</option>{task_status_options}</select>
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_filter_apply"])}</button>
        </div>
      </form>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_filters"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="task_trade_date" value="{escape(params.get("task_trade_date", ""))}">
        <input type="hidden" name="task_status" value="{escape(params.get("task_status", ""))}">
        <div class="field">
          <label for="review_trade_date">trade_date</label>
          <input id="review_trade_date" name="review_trade_date" type="text" value="{escape(params.get("review_trade_date", ""))}" placeholder="YYYY-MM-DD">
        </div>
        <div class="field">
          <label for="review_status">review_status</label>
          <select id="review_status" name="review_status"><option value="">-</option>{review_status_options}</select>
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_filter_apply"])}</button>
        </div>
      </form>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_notification_filters"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="task_trade_date" value="{escape(params.get("task_trade_date", ""))}">
        <input type="hidden" name="task_status" value="{escape(params.get("task_status", ""))}">
        <input type="hidden" name="review_trade_date" value="{escape(params.get("review_trade_date", ""))}">
        <input type="hidden" name="review_status" value="{escape(params.get("review_status", ""))}">
        <div class="field">
          <label for="notification_related_review_task_id">related_review_task_id</label>
          <input id="notification_related_review_task_id" name="notification_related_review_task_id" type="text" value="{escape(params.get("notification_related_review_task_id", ""))}">
        </div>
        <div class="field">
          <label for="notification_send_status">send_status</label>
          <select id="notification_send_status" name="notification_send_status"><option value="">-</option>{notification_status_options}</select>
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_filter_apply"])}</button>
        </div>
      </form>
    </section>
"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["runtime_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["runtime_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/runtime")}
    {_banner(message, message_level)}
    {filter_panels}
    {selected_review_html}
    {history_panel}
    {selected_notification_panel}
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_tasks"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>task_id</th><th>trade_date</th><th>scope</th><th>action</th><th>status</th><th>SKU</th><th>platform</th></tr></thead><tbody>{task_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_reviews"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>review_task_id</th><th>trade_date</th><th>scope</th><th>type</th><th>status</th><th>source_task_id</th><th>reason</th></tr></thead><tbody>{review_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_notifications"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>notification_id</th><th>related_task_id</th><th>related_review_task_id</th><th>recipient</th><th>channel</th><th>send_status</th><th>sent_at</th><th>message</th></tr></thead><tbody>{notification_rows}</tbody></table></div>
    </section>
  </main>
</body>
</html>
"""


def render_ops_dashboard_page(
    *,
    runtime_db: str,
    session_user: str,
    tasks,
    reviews,
    notifications,
    execution_logs,
    now: datetime,
) -> str:
    metrics = _build_dashboard_metrics(tasks, reviews, notifications, now)
    cards_html = "".join(
        _render_dashboard_metric_card(runtime_db, metric)
        for metric in metrics
    )
    body = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["dashboard_tab"])}</h2>
      <p class="subtle">{escape(UI_TEXT["ops_dashboard_lede"])}</p>
      <div class="metrics">{cards_html}</div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_business_inputs_title"])}</h2>
      <div class="actions">
        <a class="nav-link utility-link" href="/task-generator">{escape(UI_TEXT["ops_link_to_generator"])}</a>
      </div>
    </section>
    """
    return _render_ops_page(
        title=UI_TEXT["ops_dashboard_title"],
        description=UI_TEXT["ops_dashboard_lede"],
        active_path="/dashboard",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
    )


def render_tasks_page(
    *,
    runtime_db: str,
    session_user: str,
    tasks,
    pagination=None,
    task_status: str = "",
    trade_date_filter: str = "",
    action_type_filter: str = "",
    scope_type_filter: str = "",
    scope_key_filter: str = "",
    selected_task_id: str = "",
    selected_task=None,
    task_history=None,
    related_reviews=None,
    related_notifications=None,
    execution_logs=None,
    listing_action_projections=None,
    task_group_statuses: dict[str, str] | None = None,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(task.trade_date.isoformat() if task.trade_date else '-')}</td>"
        f"<td>{escape(display_enum_label('action_type', task.action_type.value))}</td>"
        f"<td>{escape(format_task_object(task))}</td>"
        f"<td>{escape(task.platform_name or '-')}</td>"
        f"<td>{escape(_task_group_id(task) or '-')}</td>"
        f"<td>{escape((task_group_statuses or {}).get(_task_group_id(task), '-'))}</td>"
        f"<td>{_status_badge(task.task_status.value, 'task_status')}</td>"
        f"<td>{escape(str(task.expected_old_price) if task.expected_old_price is not None else '-')}</td>"
        f"<td>{escape(format_task_target(task))}</td>"
        f"<td>{escape(format_display_datetime(task.required_by))}</td>"
        f"<td>{escape(_task_reason_summary(task))}</td>"
        f"<td><a href='{escape(_append_query_to_path('/tasks', {'task_id': task.task_id, 'task_status': task_status, 'trade_date': trade_date_filter, 'action_type': action_type_filter, 'scope_type': scope_type_filter, 'scope_key': scope_key_filter}))}'>{escape(UI_TEXT['ops_review_detail_link'])}</a></td>"
        "</tr>"
        for task in tasks
    )
    detail_html = _render_task_center_detail(
        selected_task_id=selected_task_id,
        selected_task=selected_task,
        task_history=task_history or [],
        related_reviews=related_reviews or [],
        related_notifications=related_notifications or [],
        execution_logs=execution_logs or [],
        listing_action_projections=listing_action_projections or [],
    )
    body = _task_center_tabs("tasks") + _task_filter_panel(
        runtime_db,
        task_status,
        trade_date_filter,
        action_type_filter,
        scope_type_filter,
        scope_key_filter,
    ) + (
        _render_table_panel(
            UI_TEXT["ops_tasks_title"],
            [
                "trade_date",
                "action_type",
                "business_object",
                "platform_name",
                "task_group_id",
                "task_group_status",
                "task_status",
                "expected_old_price",
                "target_action_or_price",
                "required_by",
                "reason_summary",
                "action",
            ],
            rows,
            empty_message=UI_TEXT["ops_empty_tasks"],
        )
        if tasks
        else _render_empty_state(UI_TEXT["ops_tasks_title"], UI_TEXT["ops_empty_tasks"])
    ) + _render_pagination(
        "/tasks",
        pagination,
        {
            "task_status": task_status,
            "trade_date": trade_date_filter,
            "action_type": action_type_filter,
            "scope_type": scope_type_filter,
            "scope_key": scope_key_filter,
        },
    ) + detail_html
    return _render_ops_page(
        title=UI_TEXT["ops_tasks_title"],
        description="任务中心用于查看系统准备执行或等待处理的改价、上架、下架和运营提醒任务。",
        active_path="/tasks",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
    )


def render_tasks_automation_page(
    *,
    runtime_db: str,
    session_user: str,
    script_runs,
    pagination=None,
    selected_script_run=None,
    script_run_items=None,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(run.script_run_id)}</td>"
        f"<td>{escape(run.evaluator_name)}</td>"
        f"<td>{escape(run.description)}</td>"
        f"<td>{escape(format_display_datetime(run.started_at))}</td>"
        f"<td>{_status_badge(run.run_status, 'script_run_status')}</td>"
        f"<td>{escape(run.run_mode)}</td>"
        f"<td>{escape(str(run.summary.get('inserted_tasks_count', 0)))}</td>"
        f"<td>{escape(str(run.summary.get('inserted_review_tasks_count', 0)))}</td>"
        f"<td>{escape(str(run.summary.get('inserted_notification_logs_count', 0)))}</td>"
        f"<td>{escape(_truncate_text(_sanitize_display_text(run.error_message or '-'), 160))}</td>"
        f"<td><a href='{escape(_append_query_to_path('/tasks', {'task_tab': 'automation', 'script_run_id': run.script_run_id}))}'>查看详情</a></td>"
        "</tr>"
        for run in script_runs
    )
    body = _task_center_tabs("automation")
    body += """
    <section class="panel">
      <p class="subtle">脚本状态用于查看自动规则评估的 dry-run/apply 运行记录。第一版仅展示记录，不提供 apply 按钮；apply 只能通过命令行执行。</p>
    </section>
    """
    body += (
        _render_table_panel(
            "脚本状态",
            [
                "script_run_id",
                "evaluator_name",
                "description",
                "started_at",
                "run_status",
                "run_mode",
                "inserted_tasks_count",
                "inserted_review_tasks_count",
                "inserted_notification_logs_count",
                "error_message",
                "action",
            ],
            rows,
            empty_message="当前还没有自动规则评估运行记录。可以先通过命令行 dry-run 生成预览记录。",
        )
        if script_runs
        else _render_empty_state("脚本状态", "当前还没有自动规则评估运行记录。可以先通过命令行 dry-run 生成预览记录。")
    )
    body += _render_pagination("/tasks", pagination, {"task_tab": "automation"})
    body += _render_script_run_detail(selected_script_run, script_run_items or [])
    return _render_ops_page(
        title=UI_TEXT["ops_tasks_title"],
        description="任务中心用于查看待执行任务，也用于追踪自动规则评估脚本的运行状态。",
        active_path="/tasks",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
    )


def render_tasks_mock_platform_page(
    *,
    runtime_db: str,
    session_user: str,
    states,
    pagination=None,
    platform_filter: str = "",
    sku_filter: str = "",
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(state.platform_name)}</td>"
        f"<td>{escape(state.internal_sku)}</td>"
        f"<td>{escape(state.platform_sku)}</td>"
        f"<td>{escape(state.product_name)}</td>"
        f"<td>{escape(state.grade)}</td>"
        f"<td>{escape('-' if state.platform_price is None else str(state.platform_price))}</td>"
        f"<td>{escape(_mock_platform_status_label(state.platform_online_status))}</td>"
        f"<td>{escape(str(state.platform_stock_qty))}</td>"
        f"<td>{escape(format_display_datetime(state.last_synced_at))}</td>"
        f"<td>{escape(format_display_datetime(state.last_platform_update_at))}</td>"
        f"<td>{escape(_truncate_text(_sanitize_display_text(state.last_error or '-'), 120))}</td>"
        "</tr>"
        for state in states
    )
    body = _task_center_tabs("mock_platform")
    body += f"""
    <section class="panel">
      <p class="subtle">Mock 平台测试台只用于本地验证改价、上下架和平台状态同步链路。这里展示的是测试平台实际状态，不代表真实销售平台，也不会回写商品公共库存。</p>
      <p class="subtle">执行测试请使用命令行 <code>python scripts/run_mock_platform_executor.py --dry-run</code> 或确认后使用 <code>--apply</code>；本页第一版不提供执行按钮。</p>
      <form method="get" action="/tasks" class="grid two-col">
        <input type="hidden" name="task_tab" value="mock_platform">
        <div class="field">
          <label for="platform">{escape(_field_label("platform_name"))}</label>
          <input id="platform" name="platform" type="text" value="{escape(platform_filter)}" placeholder="default_platform">
        </div>
        <div class="field">
          <label for="internal_sku">{escape(_field_label("internal_sku"))}</label>
          <input id="internal_sku" name="internal_sku" type="text" value="{escape(sku_filter)}" placeholder="SKU-001">
        </div>
        <div class="actions"><button type="submit">筛选</button></div>
      </form>
    </section>
    """
    body += (
        _render_table_panel(
            "Mock 平台状态",
            [
                "platform_name",
                "internal_sku",
                "platform_sku",
                "product_name",
                "grade",
                "platform_price",
                "platform_online_status",
                "platform_stock_qty",
                "last_synced_at",
                "last_platform_update_at",
                "last_error",
            ],
            rows,
            empty_message="当前还没有 Mock 平台状态。可以先运行命令行初始化测试平台数据。",
        )
        if states
        else _render_empty_state("Mock 平台状态", "当前还没有 Mock 平台状态。可以先运行命令行初始化测试平台数据。")
    )
    body += _render_pagination(
        "/tasks",
        pagination,
        {
            "task_tab": "mock_platform",
            "platform": platform_filter,
            "internal_sku": sku_filter,
        },
    )
    return _render_ops_page(
        title=UI_TEXT["ops_tasks_title"],
        description="任务中心用于查看待执行任务、自动规则运行记录，也可以查看本地 Mock 平台测试台状态。",
        active_path="/tasks",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
    )


def render_reviews_page(
    *,
    runtime_db: str,
    session_user: str,
    reviews,
    pagination=None,
    review_status: str = "",
    due_filter: str = "",
    selected_review=None,
    source_task=None,
    task_history=None,
    related_notifications=None,
    review_tokens=None,
    message: str = "",
    message_level: str = "info",
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(display_enum_label('review_type', review.review_type))}</td>"
        f"<td>{escape(format_object_scope(review.scope_type, review.scope_key))}</td>"
        f"<td>{escape(_sanitize_display_text(review.reason or '-'))}</td>"
        f"<td>{escape(format_display_datetime(review.required_by))}</td>"
        f"<td>{_status_badge(review.review_status.value, 'review_status')}</td>"
        f"<td>{escape(review.source_task_id or '-')}</td>"
        f"<td>{_review_action_hint(review, review_status, due_filter)}</td>"
        "</tr>"
        for review in reviews
    )
    detail_html = _render_review_center_detail(
        runtime_db=runtime_db,
        session_user=session_user,
        selected_review=selected_review,
        source_task=source_task,
        task_history=task_history or [],
        related_notifications=related_notifications or [],
        review_tokens=review_tokens or [],
        review_status=review_status,
        due_filter=due_filter,
    )
    intro = "<p class='subtle'>复核中心用于处理需要人工确认的事项。通过这里处理后，系统会记录处理人、处理时间和后续任务状态。</p>"
    body = intro + _review_filter_panel(runtime_db, review_status, due_filter) + (
        _render_table_panel(
            UI_TEXT["ops_reviews_title"],
            [
                "review_type",
                "business_object",
                "reason",
                "required_by",
                "review_status",
                "source_task_id",
                "action",
            ],
            rows,
            empty_message=UI_TEXT["ops_empty_reviews"],
        )
        if reviews
        else _render_empty_state(UI_TEXT["ops_reviews_title"], UI_TEXT["ops_empty_reviews"])
    ) + _render_pagination(
        "/reviews",
        pagination,
        {
            "review_status": review_status,
            "due": due_filter,
        },
    ) + detail_html
    return _render_ops_page(
        title=UI_TEXT["ops_reviews_title"],
        description="复核中心用于处理需要人工确认的事项，例如产能预警、临时工确认、低于保本价复核等。",
        active_path="/reviews",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
        message=message,
        message_level=message_level,
    )


def render_notifications_page(
    *,
    runtime_db: str,
    session_user: str,
    notifications,
    pagination=None,
    send_status: str = "",
    related_review_task_id: str = "",
    channel: str = "",
    notification_id: str = "",
    selected_notification=None,
    selected_review=None,
    selected_task=None,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(format_display_datetime(log.sent_at))}</td>"
        f"<td>{escape(display_enum_label('channel', log.channel))}</td>"
        f"<td>{_status_badge(log.send_status, 'send_status')}</td>"
        f"<td>{escape(log.related_review_task_id or '-')}</td>"
        f"<td>{escape(_truncate_text(_sanitize_notification_text(log.message), 180))}</td>"
        f"<td>{escape(_truncate_text(_sanitize_notification_text(log.error_message or '-'), 180))}</td>"
        f"<td><a href='{escape(_append_query_to_path('/notifications', {'notification_id': log.notification_id, 'send_status': send_status, 'related_review_task_id': related_review_task_id, 'channel': channel}))}'>{escape(UI_TEXT['ops_notification_view_detail'])}</a></td>"
        "</tr>"
        for log in notifications
    )
    detail_html = _render_notification_center_detail(
        notification_id=notification_id,
        selected_notification=selected_notification,
        selected_review=selected_review,
        selected_task=selected_task,
    )
    body = _notification_filter_panel(
        runtime_db,
        send_status,
        related_review_task_id,
        channel,
    ) + (
        _render_table_panel(
            UI_TEXT["ops_notifications_title"],
            [
                "sent_at",
                "channel",
                "send_status",
                "related_review_task_id",
                "message",
                "error_message",
                "action",
            ],
            rows,
            empty_message=UI_TEXT["ops_empty_notifications"],
        )
        if notifications
        else _render_empty_state(UI_TEXT["ops_notifications_title"], UI_TEXT["ops_empty_notifications"])
    ) + _render_pagination(
        "/notifications",
        pagination,
        {
            "send_status": send_status,
            "related_review_task_id": related_review_task_id,
            "channel": channel,
        },
    ) + detail_html
    return _render_ops_page(
        title=UI_TEXT["ops_notifications_title"],
        description="通知中心用于查看飞书或系统通知是否发送成功，以及对应哪个复核事项。",
        active_path="/notifications",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
    )


def render_execution_logs_page(
    *,
    runtime_db: str,
    session_user: str,
    execution_logs,
    pagination=None,
    message: str = "",
    message_level: str = "info",
    shadowbot_queue_status=None,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(log.log_id)}</td>"
        f"<td>{escape(log.task_id)}</td>"
        f"<td>{escape(log.executor_name)}</td>"
        f"<td>{_status_badge('success' if log.success_flag else 'failed' if log.success_flag is False else 'pending', 'task_status')}</td>"
        f"<td>{escape(format_display_datetime(log.start_time))}</td>"
        f"<td>{escape(format_display_datetime(log.end_time))}</td>"
        f"<td>{escape(_sanitize_display_text(log.error_message or '-'))}</td>"
        f"<td>{_render_shadowbot_log_summary(log)}</td>"
        "</tr>"
        for log in execution_logs
    )
    body = _banner(message, message_level) + _render_shadowbot_queue_status(shadowbot_queue_status) + f"<p class='subtle'>{escape(UI_TEXT['ops_execution_logs_note'])}</p>" + (
        _render_table_panel(
            UI_TEXT["ops_execution_logs_title"],
            [
                "log_id",
                "task_id",
                "executor_name",
                "success_flag",
                "start_time",
                "end_time",
                "error_message",
                "shadowbot",
            ],
            rows,
            empty_message=UI_TEXT["ops_empty_execution_logs"],
        )
        if execution_logs
        else _render_empty_state(UI_TEXT["ops_execution_logs_title"], UI_TEXT["ops_empty_execution_logs"])
    ) + _render_pagination("/execution-logs", pagination, {})
    return _render_ops_page(
        title=UI_TEXT["ops_execution_logs_title"],
        description="执行记录用于查看系统或执行器实际执行后的结果。",
        active_path="/execution-logs",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
    )


def render_business_inputs_page(
    *,
    runtime_db: str,
    session_user: str,
    products_path: str,
    price_rules_path: str,
    listing_rules_path: str,
    capacity_plans_path: str,
    cold_storage_status_path: str,
    platform_mappings_path: str,
    product_rows: list[dict[str, object]],
    variety_options: list[str],
    price_rule_rows: list[dict[str, object]],
    listing_rule_rows: list[dict[str, object]],
    capacity_plan_rows: list[dict[str, object]],
    cold_storage_rows: list[dict[str, object]],
    platform_options: list[str],
    listing_status_rows: list[ListingStatus],
    active_input_tab: str = "inventory",
    message: str = "",
    message_level: str = "info",
) -> str:
    active_input_tab = _normalize_business_input_tab(active_input_tab)
    if active_input_tab == "listing_status":
        active_panel = _render_listing_status_management(listing_status_rows)
    elif active_input_tab == "price_rules":
        active_panel = _render_price_rule_management(price_rules_path, product_rows, price_rule_rows, platform_options)
    elif active_input_tab == "listing_rules":
        active_panel = _render_listing_rule_management(
            listing_rules_path,
            product_rows,
            listing_rule_rows,
            platform_options,
        )
    elif active_input_tab == "capacity_plans":
        active_panel = _render_capacity_plan_management(capacity_plans_path, capacity_plan_rows)
    elif active_input_tab == "cold_storage_status":
        active_panel = _render_cold_storage_management(cold_storage_status_path, cold_storage_rows)
    else:
        active_panel = (
            _render_inventory_input_form(products_path, platform_mappings_path, variety_options)
            + _render_product_inventory_list(products_path, product_rows, variety_options)
        )
    body = f"""
    <section class="panel">
      <h2>商品资料与库存录入</h2>
      <p class="subtle">这里用于维护商品资料并录入可销售库存。系统会优先补充同类型库存；如果没有对应商品，才会新增商品资料。保存后请重新生成运行态任务，任务中心才会使用最新数据。</p>
      <p class="subtle">初始录入库存是公共库存，不绑定特定平台；平台销售统计属于后续销售或转化记录。</p>
      <div class="actions">
        <a class="nav-link utility-link" href="/tables">{escape(UI_TEXT["ops_link_to_tables"])}</a>
        <a class="nav-link utility-link" href="{escape('/')}">{escape(UI_TEXT["ops_link_to_generator"])}</a>
      </div>
      <p class="subtle"><a href="/system">{escape(UI_TEXT["ops_system_title"])}</a></p>
    </section>
    {_render_business_input_tabs(active_input_tab, runtime_db, products_path, price_rules_path, listing_rules_path, capacity_plans_path, cold_storage_status_path, platform_mappings_path)}
    {active_panel}
    {_render_inventory_feedback_dialog(message, message_level)}
    """
    return _render_ops_page(
        title=UI_TEXT["ops_business_inputs_title"],
        description="业务数据用于维护商品资料、录入可销售库存，并生成后续待处理任务。",
        active_path="/business-inputs",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
        message=message,
        message_level=message_level,
    )


def _normalize_business_input_tab(value: str | None) -> str:
    if value in BUSINESS_INPUT_TABS:
        return str(value)
    return "inventory"


def _render_business_input_tabs(
    active_input_tab: str,
    runtime_db: str,
    products_path: str,
    price_rules_path: str,
    listing_rules_path: str,
    capacity_plans_path: str,
    cold_storage_status_path: str,
    platform_mappings_path: str,
) -> str:
    links = []
    for tab_key, label in BUSINESS_INPUT_TABS.items():
        href = _append_query_to_path(
            "/business-inputs",
            {
                "runtime_db": runtime_db,
                "products_path": products_path,
                "price_rules_path": price_rules_path,
                "listing_rules_path": listing_rules_path,
                "capacity_plans_path": capacity_plans_path,
                "cold_storage_status_path": cold_storage_status_path,
                "platform_mappings_path": platform_mappings_path,
                "input_tab": tab_key,
            },
        )
        css_class = "business-input-tab active" if tab_key == active_input_tab else "business-input-tab"
        aria_current = " aria-current=\"page\"" if tab_key == active_input_tab else ""
        links.append(f"<a class=\"{css_class}\" href=\"{escape(href)}\"{aria_current}>{escape(label)}</a>")
    return f"""
    <section class="panel business-input-tab-panel" aria-label="业务输入分页">
      <div class="business-input-tabs">
        {''.join(links)}
      </div>
      <p class="subtle">请选择当前要维护的业务输入模块。库存录入和价格规则管理会分别保存到对应表格，保存后请重新生成待处理任务。</p>
    </section>
    """


def _render_listing_status_management(
    rows: list[ListingStatus],
) -> str:
    table_rows = "".join(
        "<tr>"
        f"<td>{escape(row.platform_name)}</td>"
        f"<td>{escape(row.variety)}</td>"
        f"<td>{escape(row.grade)}</td>"
        f"<td>{escape(str(row.current_price))}</td>"
        f"<td>{row.platform_stock_qty}</td>"
        f"<td>{row.sold_qty}</td>"
        f"<td>{'上架' if row.online_status == 'online' else '下架'}</td>"
        f"<td>{escape(row.source)}</td>"
        "</tr>"
        for row in rows
    ) or '<tr><td colspan="8">暂无上架状态。请先通过 ShadowBot READ_ONLY 读取平台商品。</td></tr>'
    return f"""
    <section class="panel">
      <h2>上架状态</h2>
      <p class="subtle">本页面只读，不提供人工录入或修改。正常业务更新仅接收经过合同校验的 ShadowBot READ_ONLY 结果，并按“平台 + 品种 + 等级”写入 SQLite。</p>
    </section>
    <section class="panel table-panel">
      <h2>当前平台商品状态</h2>
      <div class="table-wrap"><table><thead><tr><th>平台</th><th>品种</th><th>等级</th><th>价格</th><th>平台库存</th><th>已销售数</th><th>状态</th><th>来源</th></tr></thead><tbody>{table_rows}</tbody></table></div>
    </section>
    """


def _render_inventory_input_form(products_path: str, platform_mappings_path: str, variety_options: list[str]) -> str:
    return f"""
    <section class="panel product-input-panel">
      <h2>录入库存</h2>
      <p class="subtle">如果已有同类型商品，本次数量会累加到当前库存。同类型商品已存在时，本次保存会补充库存，并将基础成本、是否允许销售更新为本次填写值。</p>
      <form method="post" class="inventory-form">
        <input type="hidden" name="products_path" value="{escape(products_path)}">
        <input type="hidden" name="platform_mappings_path" value="{escape(platform_mappings_path)}">
        <input type="hidden" name="input_tab" value="inventory">
        <input type="hidden" id="variety_code" name="variety_code" value="">
        <div class="inventory-row">
          <div class="field">
            <div class="field-title-row">
              <label for="product_name">品种</label>
              <button class="mini-button" type="button" id="open_new_platform_dialog">新增平台</button>
              <button class="mini-button" type="button" id="open_new_variety_dialog">新增品种</button>
            </div>
            <select id="product_name" name="product_name" required>
              <option value="">请选择品种</option>
              {_options_html(variety_options, "")}
            </select>
            <p class="help">选择鲜花品种。若列表中没有，请点击“新增品种”填写新品种名称和代码。</p>
          </div>
          <div class="field align-with-primary-control">
            <label for="grade">等级</label>
            <select id="grade" name="grade">{_options_html(GRADE_OPTIONS, "B")}</select>
            <p class="help help-placeholder">&nbsp;</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="quantity">本次入库数量</label>
            <input id="quantity" name="quantity" type="number" min="1" step="1" required>
            <p class="help">如果已有同类型商品，本次数量会累加到当前库存。</p>
          </div>
          <div class="field align-with-primary-control">
            <label for="stem_length">枝长/规格</label>
            <select id="stem_length" name="stem_length">{_options_html(STEM_LENGTH_OPTIONS, FOLLOW_GRADE_VALUE)}</select>
            <p class="help">选择“跟随等级”时，会按 {_follow_grade_rule_text()} 保存。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="unit">单位</label>
            <select id="unit" name="unit">{_options_html(UNIT_OPTIONS, "扎")}</select>
            <p class="help help-placeholder">&nbsp;</p>
          </div>
          <div class="field align-with-primary-control">
            <label for="base_cost">基础成本</label>
            <input id="base_cost" name="base_cost" type="number" min="0" step="0.01" value="6" required>
            <p class="help">用于系统判断低价风险，不会直接展示给销售平台。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="sale_enabled">是否允许销售</label>
            <select id="sale_enabled" name="sale_enabled">
              <option value="true" selected>是</option>
              <option value="false">否</option>
            </select>
            <p class="help">关闭后，系统不会为该商品生成上架或改价任务。</p>
          </div>
          <div class="field inventory-submit-field">
            <div class="submit-label-spacer" aria-hidden="true"></div>
            <div class="inventory-submit-control">
              <button class="primary" type="submit" name="action" value="add_inventory">补充库存</button>
            </div>
            <p class="help help-placeholder">&nbsp;</p>
          </div>
        </div>
      </form>
      {_render_new_platform_dialog(platform_mappings_path)}
      {_render_new_variety_dialog()}
      {_new_platform_dialog_script()}
      {_new_variety_dialog_script()}
    </section>
    """


def _render_inventory_feedback_dialog(message: str, message_level: str) -> str:
    if not message or message_level != "success":
        return ""
    safe_message = escape(message)
    alert_message = json.dumps(message, ensure_ascii=False)
    return f"""
    <dialog id="inventory_feedback_dialog" class="modal-card feedback-dialog" aria-labelledby="inventory_feedback_title">
      <form method="dialog" class="grid">
        <h3 id="inventory_feedback_title">保存成功</h3>
        <p>{safe_message}</p>
        <div class="actions">
          <button class="primary" type="submit" value="confirm">确认</button>
        </div>
      </form>
    </dialog>
    <script>
      (function () {{
        const dialog = document.getElementById("inventory_feedback_dialog");
        if (!dialog) {{
          return;
        }}
        if (typeof dialog.showModal === "function") {{
          dialog.showModal();
        }} else {{
          window.alert({alert_message});
        }}
      }})();
    </script>
    """


def _render_product_inventory_list(
    products_path: str,
    product_rows: list[dict[str, object]],
    variety_options: list[str],
) -> str:
    if not product_rows:
        return _render_empty_state("商品资料", "当前还没有商品资料。请先录入库存，系统会自动创建商品资料。")
    rows_html = "".join(_render_product_inventory_row(products_path, row, variety_options) for row in product_rows)
    return _render_table_panel(
        "商品资料",
        ["product_name", "grade", "stem_length", "unit", "base_cost", "current_stock", "sale_enabled", "internal_sku", "action"],
        rows_html,
        empty_message="当前还没有商品资料。请先录入库存，系统会自动创建商品资料。",
    )


def _render_price_rule_management(
    price_rules_path: str,
    product_rows: list[dict[str, object]],
    price_rule_rows: list[dict[str, object]],
    platform_options: list[str],
) -> str:
    return f"""
    <section class="panel product-input-panel">
      <h2>价格规则管理</h2>
      <p class="subtle">这里维护的是价格规则。保存后不会直接修改已生成任务；如需应用到任务中心，请重新生成运行态任务。</p>
      <p class="subtle">当前表单严格保存回现有价格规则表字段：适用范围、价格类型、改价值、最低价、取整方式、是否启用和优先级。不新增数据库结构。</p>
      {_render_price_rule_form(price_rules_path, product_rows, platform_options)}
    </section>
    {_render_price_rule_list(price_rules_path, product_rows, price_rule_rows, platform_options)}
    """


def _render_listing_rule_management(
    listing_rules_path: str,
    product_rows: list[dict[str, object]],
    listing_rule_rows: list[dict[str, object]],
    platform_options: list[str],
) -> str:
    return f"""
    <section class="panel product-input-panel">
      <h2>上下架规则管理</h2>
      <p class="subtle">这里维护的是上下架判断规则。保存规则不会直接操作销售平台；如需影响任务中心，请重新生成运行态任务或运行对应规则评估。</p>
      <p class="subtle">上下架规则由品种、等级、平台三个条件共同决定。“不限制”表示该维度不参与筛选。</p>
      {_render_listing_rule_form(listing_rules_path, product_rows, platform_options)}
    </section>
    {_render_listing_rule_list(listing_rules_path, product_rows, listing_rule_rows, platform_options)}
    """


def _render_capacity_plan_management(capacity_plans_path: str, capacity_plan_rows: list[dict[str, object]]) -> str:
    return f"""
    <section class="panel product-input-panel">
      <h2>包装产能计划</h2>
      <p class="subtle">这里维护每日包装能力。系统会用确认包装能力与预测采收数量对比，判断是否需要产能预警或临时工确认。保存后如需影响脚本状态和复核，请运行对应自动规则评估。</p>
      {_render_capacity_plan_form(capacity_plans_path)}
    </section>
    {_render_capacity_plan_list(capacity_plans_path, capacity_plan_rows)}
    """


def _render_capacity_plan_form(capacity_plans_path: str) -> str:
    computed_capacity = 250
    return f"""
      <form method="post" class="inventory-form">
        <input type="hidden" name="capacity_plans_path" value="{escape(capacity_plans_path)}">
        <input type="hidden" name="input_tab" value="capacity_plans">
        <input type="hidden" name="allocation_rule" value="proportional_by_forecast">
        <div class="inventory-row">
          <div class="field">
            <label for="capacity_trade_date">业务日期</label>
            <input id="capacity_trade_date" name="trade_date" type="date" required>
            <p class="help">选择这条包装产能计划对应的业务日期。</p>
          </div>
          <div class="field">
            <label for="capacity_active">是否启用</label>
            <select id="capacity_active" name="active">
              <option value="true" selected>是</option>
              <option value="false">否</option>
            </select>
            <p class="help">同一业务日期只能保留一条启用的产能计划。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="normal_packing_capacity_qty">基础包装产能</label>
            <input id="normal_packing_capacity_qty" name="normal_packing_capacity_qty" type="number" min="0" step="1" value="250" required>
            <p class="help">基地不额外安排临时工时的默认包装能力。</p>
          </div>
          <div class="field">
            <label for="confirmed_temp_worker_count">临时工人数</label>
            <input id="confirmed_temp_worker_count" name="confirmed_temp_worker_count" type="number" min="0" step="1" value="0" required>
            <p class="help">已确认会参与包装的临时工人数。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="temp_worker_capacity_qty">单人临时工产能</label>
            <input id="temp_worker_capacity_qty" name="temp_worker_capacity_qty" type="number" min="0" step="1" value="100" required>
            <p class="help">每名临时工当天预计可增加的包装能力。</p>
          </div>
          <div class="field">
            <label for="confirmed_packing_capacity_qty">确认包装能力</label>
            <input id="confirmed_packing_capacity_qty" name="confirmed_packing_capacity_qty" type="number" min="0" step="1" value="{computed_capacity}" required>
            <p class="help">默认等于基础产能 + 临时工人数 × 单人产能，也可以人工确认。</p>
          </div>
        </div>
        <div class="field">
          <label for="capacity_note">备注</label>
          <textarea id="capacity_note" name="note" rows="3"></textarea>
        </div>
        <div class="actions">
          <button class="primary" type="submit" name="action" value="add_capacity_plan">新增包装产能计划</button>
        </div>
      </form>
      {_capacity_plan_auto_capacity_script()}
    """


def _render_capacity_plan_list(capacity_plans_path: str, capacity_plan_rows: list[dict[str, object]]) -> str:
    if not capacity_plan_rows:
        return _render_empty_state("包装产能计划", "当前还没有包装产能计划。请先新增某个业务日期的包装能力。")
    rows_html = "".join(
        _render_capacity_plan_row(capacity_plans_path, row, row_index) for row_index, row in enumerate(capacity_plan_rows)
    )
    return _render_table_panel(
        "包装产能计划",
        [
            "trade_date",
            "normal_packing_capacity_qty",
            "confirmed_temp_worker_count",
            "temp_worker_capacity_qty",
            "confirmed_packing_capacity_qty",
            "active",
            "note",
            "action",
        ],
        rows_html,
        empty_message="当前还没有包装产能计划。请先新增某个业务日期的包装能力。",
    )


def _render_capacity_plan_row(capacity_plans_path: str, row: dict[str, object], row_index: int) -> str:
    trade_date = _capacity_trade_date_value(row)
    confirmed_capacity = computed_capacity_from_row(row)
    return f"""
      <tr>
        <td>{escape(trade_date or '-')}</td>
        <td>{escape(format_capacity_number(row.get('normal_packing_capacity_qty')))}</td>
        <td>{escape(format_capacity_number(row.get('confirmed_temp_worker_count')))}</td>
        <td>{escape(format_capacity_number(row.get('temp_worker_capacity_qty')))}</td>
        <td>{escape(format_capacity_number(confirmed_capacity))}</td>
        <td>{escape(capacity_active_display(row.get('active')))}</td>
        <td>{escape(str(row.get('note') or '-'))}</td>
        <td>{_render_capacity_plan_edit_form(capacity_plans_path, row, row_index)}</td>
      </tr>
    """


def _render_capacity_plan_edit_form(capacity_plans_path: str, row: dict[str, object], row_index: int) -> str:
    trade_date = _capacity_trade_date_value(row)
    active = "true" if capacity_active_display(row.get("active")) == "是" else "false"
    confirmed_capacity = computed_capacity_from_row(row)
    return f"""
      <details>
        <summary>编辑</summary>
        <form method="post" class="grid product-edit-form">
          <input type="hidden" name="capacity_plans_path" value="{escape(capacity_plans_path)}">
          <input type="hidden" name="input_tab" value="capacity_plans">
          <input type="hidden" name="current_trade_date" value="{escape(trade_date)}">
          <input type="hidden" name="current_row_index" value="{row_index}">
          <input type="hidden" name="allocation_rule" value="{escape(str(row.get('allocation_rule') or 'proportional_by_forecast'))}">
          <p class="help">保存后如需影响脚本状态和复核，请运行对应自动规则评估。</p>
          <div class="two-col">
            <div class="field">
              <label>业务日期</label>
              <input name="trade_date" type="date" value="{escape(trade_date)}" required>
            </div>
            <div class="field">
              <label>是否启用</label>
              <select name="active">
                <option value="true" {'selected' if active == 'true' else ''}>是</option>
                <option value="false" {'selected' if active == 'false' else ''}>否</option>
              </select>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>基础包装产能</label>
              <input name="normal_packing_capacity_qty" type="number" min="0" step="1" value="{escape(str(row.get('normal_packing_capacity_qty') or '250'))}" required>
            </div>
            <div class="field">
              <label>临时工人数</label>
              <input name="confirmed_temp_worker_count" type="number" min="0" step="1" value="{escape(str(row.get('confirmed_temp_worker_count') or '0'))}" required>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>单人临时工产能</label>
              <input name="temp_worker_capacity_qty" type="number" min="0" step="1" value="{escape(str(row.get('temp_worker_capacity_qty') or '100'))}" required>
            </div>
            <div class="field">
              <label>确认包装能力</label>
              <input name="confirmed_packing_capacity_qty" type="number" min="0" step="1" value="{escape(str(confirmed_capacity))}" required>
            </div>
          </div>
          <div class="field">
            <label>备注</label>
            <textarea name="note" rows="3">{escape(str(row.get('note') or ''))}</textarea>
          </div>
          <button class="primary" type="submit" name="action" value="edit_capacity_plan">保存包装产能计划</button>
        </form>
      </details>
    """


def _capacity_trade_date_value(row: dict[str, object]) -> str:
    value = row.get("trade_date")
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


def _capacity_plan_auto_capacity_script() -> str:
    return """
      <script>
        (function () {
          const form = document.currentScript && document.currentScript.previousElementSibling;
          if (!form || !form.matches("form")) {
            return;
          }
          const base = form.querySelector("#normal_packing_capacity_qty");
          const workers = form.querySelector("#confirmed_temp_worker_count");
          const workerCapacity = form.querySelector("#temp_worker_capacity_qty");
          const confirmed = form.querySelector("#confirmed_packing_capacity_qty");
          if (!base || !workers || !workerCapacity || !confirmed) {
            return;
          }
          const update = function () {
            const baseValue = Number(base.value || 0);
            const workerCount = Number(workers.value || 0);
            const workerCapacityValue = Number(workerCapacity.value || 0);
            if (document.activeElement === confirmed) {
              return;
            }
            confirmed.value = String(Math.max(0, baseValue + workerCount * workerCapacityValue));
          };
          base.addEventListener("input", update);
          workers.addEventListener("input", update);
          workerCapacity.addEventListener("input", update);
        })();
      </script>
    """


def _render_listing_rule_form(listing_rules_path: str, product_rows: list[dict[str, object]], platform_options: list[str]) -> str:
    return f"""
      <form method="post" class="inventory-form">
        <input type="hidden" name="listing_rules_path" value="{escape(listing_rules_path)}">
        <input type="hidden" name="input_tab" value="listing_rules">
        <div class="inventory-row">
          <div class="field">
            <label for="listing_rule_name">规则名称</label>
            <input id="listing_rule_name" name="rule_name" type="text" required>
            <p class="help">例如：低库存下架、B级艾莎允许上架。</p>
          </div>
          <div class="field">
            <label for="listing_rule_active">是否启用</label>
            <select id="listing_rule_active" name="active">
              <option value="true" selected>是</option>
              <option value="false">否</option>
            </select>
            <p class="help">关闭后，任务生成不会使用这条上下架规则。</p>
          </div>
        </div>
        {_render_listing_filter_picker(product_rows, platform_options, "*", "*", "*", "new")}
        <div class="inventory-row">
          <div class="field">
            <label for="listing_strategy">规则策略</label>
            <select id="listing_strategy" name="listing_strategy">{_labeled_options(LISTING_STRATEGY_OPTIONS, "stock_below_offline", "listing_strategy")}</select>
            <p class="help">“直接下架”生成 set_offline 任务；规则保存本身不会直接操作平台。</p>
          </div>
          <div class="field">
            <label for="stock_threshold">库存阈值</label>
            <input id="stock_threshold" name="stock_threshold" type="number" min="0" step="1" value="0" required>
            <p class="help">库存低于或高于阈值时触发对应策略；禁止/允许上架策略也保留该值用于说明。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="listing_priority">优先级</label>
            <input id="listing_priority" name="priority" type="number" min="0" step="1" value="10" required>
            <p class="help">数字越小越先应用；下架建议优先于上架建议。</p>
          </div>
          <div class="field">
            <label for="listing_rule_remark">备注</label>
            <textarea id="listing_rule_remark" name="remark" rows="3"></textarea>
          </div>
        </div>
        <div class="actions">
          <button class="primary" type="submit" name="action" value="add_listing_rule">新增上下架规则</button>
        </div>
      </form>
    """


def _render_listing_rule_list(
    listing_rules_path: str,
    product_rows: list[dict[str, object]],
    listing_rule_rows: list[dict[str, object]],
    platform_options: list[str],
) -> str:
    if not listing_rule_rows:
        return _render_empty_state("上下架规则", "当前还没有上下架规则。请先新增一条规则，或使用高级表格入口批量维护。")
    rows_html = "".join(
        _render_listing_rule_row(listing_rules_path, product_rows, platform_options, row) for row in listing_rule_rows
    )
    return _render_table_panel(
        "上下架规则",
        ["rule_name", "listing_rule_scope", "listing_strategy", "stock_threshold", "active", "priority", "remark", "action"],
        rows_html,
        empty_message="当前还没有上下架规则。请先新增一条规则，或使用高级表格入口批量维护。",
    )


def _render_listing_rule_row(
    listing_rules_path: str,
    product_rows: list[dict[str, object]],
    platform_options: list[str],
    row: dict[str, object],
) -> str:
    listing_strategy = str(row.get("listing_strategy") or "").strip()
    return f"""
      <tr>
        <td>{escape(str(row.get('rule_name') or '-'))}</td>
        <td>{escape(format_listing_rule_scope(row))}</td>
        <td>{escape(display_enum_label('listing_strategy', listing_strategy))}</td>
        <td>{escape(format_listing_rule_number(row.get('stock_threshold')))}</td>
        <td>{escape(listing_active_display(row.get('active')))}</td>
        <td>{escape(format_listing_rule_number(row.get('priority')))}</td>
        <td>{escape(str(row.get('remark') or '-'))}</td>
        <td>{_render_listing_rule_edit_form(listing_rules_path, product_rows, platform_options, row)}</td>
      </tr>
    """


def _render_listing_rule_edit_form(
    listing_rules_path: str,
    product_rows: list[dict[str, object]],
    platform_options: list[str],
    row: dict[str, object],
) -> str:
    rule_id = str(row.get("rule_id") or "").strip()
    active = "true" if listing_active_display(row.get("active")) == "是" else "false"
    return f"""
      <details>
        <summary>编辑</summary>
        <form method="post" class="grid product-edit-form">
          <input type="hidden" name="listing_rules_path" value="{escape(listing_rules_path)}">
          <input type="hidden" name="input_tab" value="listing_rules">
          <input type="hidden" name="rule_id" value="{escape(rule_id)}">
          <p class="help">规则编号：{escape(rule_id)}。保存后请重新生成运行态任务或运行对应规则评估。</p>
          <div class="field">
            <label>规则名称</label>
            <input name="rule_name" type="text" value="{escape(str(row.get('rule_name') or ''))}" required>
          </div>
          {_render_listing_filter_picker(product_rows, platform_options, str(row.get('variety_filter') or '*'), str(row.get('grade_filter') or '*'), str(row.get('platform_filter') or '*'), rule_id)}
          <div class="two-col">
            <div class="field">
              <label>规则策略</label>
              <select name="listing_strategy">{_labeled_options(LISTING_STRATEGY_OPTIONS, str(row.get('listing_strategy') or 'stock_below_offline'), 'listing_strategy')}</select>
            </div>
            <div class="field">
              <label>库存阈值</label>
              <input name="stock_threshold" type="number" min="0" step="1" value="{escape(str(row.get('stock_threshold') or '0'))}" required>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>是否启用</label>
              <select name="active">
                <option value="true" {'selected' if active == 'true' else ''}>是</option>
                <option value="false" {'selected' if active == 'false' else ''}>否</option>
              </select>
            </div>
            <div class="field">
              <label>优先级</label>
              <input name="priority" type="number" min="0" step="1" value="{escape(str(row.get('priority') or '10'))}" required>
            </div>
          </div>
          <div class="field">
            <label>备注</label>
            <textarea name="remark" rows="3">{escape(str(row.get('remark') or ''))}</textarea>
          </div>
          <button class="primary" type="submit" name="action" value="edit_listing_rule">保存上下架规则</button>
        </form>
      </details>
    """


def _render_price_rule_form(price_rules_path: str, product_rows: list[dict[str, object]], platform_options: list[str]) -> str:
    return f"""
      <form method="post" class="inventory-form">
        <input type="hidden" name="price_rules_path" value="{escape(price_rules_path)}">
        <input type="hidden" name="input_tab" value="price_rules">
        <div class="inventory-row">
          <div class="field">
            <label for="price_rule_name">规则名称</label>
            <input id="price_rule_name" name="rule_name" type="text" required>
            <p class="help">例如：A级百分比改价、全局最低价保护。</p>
          </div>
          <div class="field">
            <label for="price_rule_active">是否启用</label>
            <select id="price_rule_active" name="active">
              <option value="true" selected>是</option>
              <option value="false">否</option>
            </select>
            <p class="help">关闭后，任务生成不会使用这条价格规则。</p>
          </div>
        </div>
        {_render_price_filter_picker(product_rows, platform_options, "*", "*", "*", "new")}
        <div class="inventory-row">
          <div class="field">
            <label for="pricing_method">价格类型</label>
            <select id="pricing_method" name="pricing_method">{_labeled_options(PRICING_METHOD_OPTIONS, "fixed_markup", "pricing_method")}</select>
            <p class="help">固定改价按金额调整；百分比改价按当前价格比例调整。</p>
          </div>
          <div class="field">
            <label for="markup_value">改价值</label>
            <input id="markup_value" name="markup_value" type="number" step="0.01" required>
            <p class="help">输入正数表示涨价，输入负数表示降价；不能为 0。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="min_price">最低价</label>
            <input id="min_price" name="min_price" type="number" min="0" step="0.01">
            <p class="help">用于低价风险和价格底线保护，不能为负数。</p>
          </div>
          <div class="field">
            <label for="priority">优先级</label>
            <input id="priority" name="priority" type="number" min="0" step="1" value="10" required>
            <p class="help">数字越小越先应用。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="rounding_rule">取整规则</label>
            <select id="rounding_rule" name="rounding_rule">{_labeled_options(ROUNDING_RULE_OPTIONS, "none", "rounding_rule")}</select>
            <p class="help">按步长取整时，需要填写取整步长。</p>
          </div>
          <div class="field">
            <label for="rounding_step">取整步长</label>
            <input id="rounding_step" name="rounding_step" type="number" min="0" step="0.01">
            <p class="help">例如 `0.5` 表示按 0.5 元向上取整。</p>
          </div>
        </div>
        <div class="field">
          <label for="price_rule_remark">备注</label>
          <textarea id="price_rule_remark" name="remark" rows="3"></textarea>
        </div>
        <div class="actions">
          <button class="primary" type="submit" name="action" value="add_price_rule">新增价格规则</button>
        </div>
      </form>
    """


def _render_price_rule_list(
    price_rules_path: str,
    product_rows: list[dict[str, object]],
    price_rule_rows: list[dict[str, object]],
    platform_options: list[str],
) -> str:
    if not price_rule_rows:
        return _render_empty_state("价格规则", "当前还没有价格规则。请先新增一条价格规则，或使用高级表格入口批量维护。")
    rows_html = "".join(_render_price_rule_row(price_rules_path, product_rows, platform_options, row) for row in price_rule_rows)
    return _render_table_panel(
        "价格规则",
        ["rule_name", "price_rule_scope", "pricing_method", "markup_value", "min_price", "rounding_rule", "active", "priority", "action"],
        rows_html,
        empty_message="当前还没有价格规则。请先新增一条价格规则，或使用高级表格入口批量维护。",
    )


def _render_price_rule_row(
    price_rules_path: str,
    product_rows: list[dict[str, object]],
    platform_options: list[str],
    row: dict[str, object],
) -> str:
    pricing_method = str(row.get("pricing_method") or "").strip()
    rounding_rule = str(row.get("rounding_rule") or "").strip()
    return f"""
      <tr>
        <td>{escape(str(row.get('rule_name') or '-'))}</td>
        <td>{escape(format_price_rule_scope(row))}</td>
        <td>{escape(display_enum_label('pricing_method', pricing_method))}</td>
        <td>{escape(format_price_rule_number(row.get('markup_value')))}</td>
        <td>{escape(format_price_rule_number(row.get('min_price')))}</td>
        <td>{escape(display_enum_label('rounding_rule', rounding_rule))}</td>
        <td>{escape(active_display(row.get('active')))}</td>
        <td>{escape(format_price_rule_number(row.get('priority')))}</td>
        <td>{_render_price_rule_edit_form(price_rules_path, product_rows, platform_options, row)}</td>
      </tr>
    """


def _render_price_rule_edit_form(
    price_rules_path: str,
    product_rows: list[dict[str, object]],
    platform_options: list[str],
    row: dict[str, object],
) -> str:
    rule_id = str(row.get("rule_id") or "").strip()
    active = "true" if active_display(row.get("active")) == "是" else "false"
    return f"""
      <details>
        <summary>编辑</summary>
        <form method="post" class="grid product-edit-form">
          <input type="hidden" name="price_rules_path" value="{escape(price_rules_path)}">
          <input type="hidden" name="input_tab" value="price_rules">
          <input type="hidden" name="rule_id" value="{escape(rule_id)}">
          <p class="help">规则编号：{escape(rule_id)}。保存后请重新生成运行态任务，任务中心才会使用最新价格规则。</p>
          <div class="field">
            <label>规则名称</label>
            <input name="rule_name" type="text" value="{escape(str(row.get('rule_name') or ''))}" required>
          </div>
          {_render_price_filter_picker(product_rows, platform_options, str(row.get('variety_filter') or '*'), str(row.get('grade_filter') or '*'), str(row.get('platform_filter') or '*'), rule_id)}
          <div class="two-col">
            <div class="field">
              <label>价格类型</label>
              <select name="pricing_method">{_labeled_options(PRICING_METHOD_OPTIONS, str(row.get('pricing_method') or 'fixed_markup'), 'pricing_method')}</select>
            </div>
            <div class="field">
              <label>改价值</label>
              <input name="markup_value" type="number" step="0.01" value="{escape(str(row.get('markup_value') or '0'))}" required>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>最低价</label>
              <input name="min_price" type="number" min="0" step="0.01" value="{escape(str(row.get('min_price') or ''))}">
            </div>
            <div class="field">
              <label>优先级</label>
              <input name="priority" type="number" min="0" step="1" value="{escape(str(row.get('priority') or '10'))}" required>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>取整规则</label>
              <select name="rounding_rule">{_labeled_options(ROUNDING_RULE_OPTIONS, str(row.get('rounding_rule') or 'none'), 'rounding_rule')}</select>
            </div>
            <div class="field">
              <label>取整步长</label>
              <input name="rounding_step" type="number" min="0" step="0.01" value="{escape(str(row.get('rounding_step') or ''))}">
            </div>
          </div>
          <div class="field">
            <label>是否启用</label>
            <select name="active">
              <option value="true" {'selected' if active == 'true' else ''}>是</option>
              <option value="false" {'selected' if active == 'false' else ''}>否</option>
            </select>
          </div>
          <div class="field">
            <label>备注</label>
            <textarea name="remark" rows="3">{escape(str(row.get('remark') or ''))}</textarea>
          </div>
          <button class="primary" type="submit" name="action" value="edit_price_rule">保存价格规则</button>
        </form>
      </details>
    """


def _render_price_filter_picker(
    product_rows: list[dict[str, object]],
    platform_options: list[str],
    selected_variety: str,
    selected_grade: str,
    selected_platform: str,
    suffix: str,
) -> str:
    safe_suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", suffix or "new").strip("-") or "new"
    variety_options = ["*", *extract_variety_options(product_rows)]
    grade_options = ["*", *GRADE_OPTIONS]
    platform_options = ["*", *(platform_options or PLATFORM_OPTIONS)]
    return f"""
        <div class="inventory-row price-filter-picker">
          <div class="field">
            <label for="variety_filter_{escape(safe_suffix)}">品种</label>
            <select id="variety_filter_{escape(safe_suffix)}" name="variety_filter">
              {_price_filter_options(variety_options, selected_variety or "*", "全部品种")}
            </select>
            <p class="help">不限制表示该维度不参与筛选。</p>
          </div>
          <div class="field">
            <label for="grade_filter_{escape(safe_suffix)}">等级</label>
            <select id="grade_filter_{escape(safe_suffix)}" name="grade_filter">
              {_price_filter_options(grade_options, selected_grade or "*", "全部等级", grade_suffix=True)}
            </select>
            <p class="help">等级 0 会按字符串保存，不会被当成空值。</p>
          </div>
        </div>
        <div class="inventory-row price-filter-picker">
          <div class="field">
            <label for="platform_filter_{escape(safe_suffix)}">平台</label>
            <select id="platform_filter_{escape(safe_suffix)}" name="platform_filter">
              {_price_filter_options(platform_options, selected_platform or "*", "全部平台")}
            </select>
            <p class="help">平台只用于价格规则命中，不改变库存归属。</p>
          </div>
          <div class="field">
            <label>筛选说明</label>
            <p class="help">价格规则由品种、等级、平台三个条件共同决定。“不限制”表示该维度不参与筛选。例如：品种=艾莎、等级=B、平台=蚂蚁，表示只对蚂蚁平台上的 B 级艾莎生效。</p>
          </div>
        </div>
    """


def _render_listing_filter_picker(
    product_rows: list[dict[str, object]],
    platform_options: list[str],
    selected_variety: str,
    selected_grade: str,
    selected_platform: str,
    suffix: str,
) -> str:
    safe_suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", suffix or "new").strip("-") or "new"
    variety_options = ["*", *extract_variety_options(product_rows)]
    grade_options = ["*", *GRADE_OPTIONS]
    platform_options = ["*", *(platform_options or PLATFORM_OPTIONS)]
    return f"""
        <div class="inventory-row price-filter-picker">
          <div class="field">
            <label for="listing_variety_filter_{escape(safe_suffix)}">品种</label>
            <select id="listing_variety_filter_{escape(safe_suffix)}" name="variety_filter">
              {_price_filter_options(variety_options, selected_variety or "*", "全部品种")}
            </select>
            <p class="help">不限制表示该维度不参与筛选。</p>
          </div>
          <div class="field">
            <label for="listing_grade_filter_{escape(safe_suffix)}">等级</label>
            <select id="listing_grade_filter_{escape(safe_suffix)}" name="grade_filter">
              {_price_filter_options(grade_options, selected_grade or "*", "全部等级", grade_suffix=True)}
            </select>
            <p class="help">等级 0 会按字符串保存，不会被当成空值。</p>
          </div>
        </div>
        <div class="inventory-row price-filter-picker">
          <div class="field">
            <label for="listing_platform_filter_{escape(safe_suffix)}">平台</label>
            <select id="listing_platform_filter_{escape(safe_suffix)}" name="platform_filter">
              {_price_filter_options(platform_options, selected_platform or "*", "全部平台")}
            </select>
            <p class="help">平台只用于上下架规则命中，不改变库存归属。</p>
          </div>
          <div class="field">
            <label>筛选说明</label>
            <p class="help">上下架规则由品种、等级、平台三个条件共同决定。“不限制”表示该维度不参与筛选。保存规则不会直接操作平台。</p>
          </div>
        </div>
    """


def _price_filter_options(options: list[str], selected: str, wildcard_label: str, *, grade_suffix: bool = False) -> str:
    unique_options: list[str] = []
    seen: set[str] = set()
    for option in options:
        if option in seen:
            continue
        seen.add(option)
        unique_options.append(option)
    selected = selected or "*"
    html_options = []
    for option in unique_options:
        if option == "*":
            label = f"不限制（{wildcard_label}）"
        elif grade_suffix:
            label = f"{option}级"
        else:
            label = option
        html_options.append(f"<option value='{escape(option)}' {'selected' if option == selected else ''}>{escape(label)}</option>")
    return "".join(html_options)


def format_price_rule_scope(row: dict[str, object]) -> str:
    variety = str(row.get("variety_filter") or "*").strip() or "*"
    grade = str(row.get("grade_filter") or "*").strip().upper() or "*"
    platform = str(row.get("platform_filter") or "*").strip() or "*"
    if variety == "*" and grade == "*" and platform == "*":
        return "全部商品"
    variety_label = "全部品种" if variety == "*" else variety
    grade_label = "全部等级" if grade == "*" else f"{grade}级"
    platform_label = "全部平台" if platform == "*" else platform
    return f"{variety_label} / {grade_label} / {platform_label}"


def _render_product_inventory_row(products_path: str, row: dict[str, object], variety_options: list[str]) -> str:
    sku = str(row.get("internal_sku") or "")
    edit_form = _render_product_edit_form(products_path, row, variety_options)
    return (
        "<tr>"
        f"<td>{escape(str(row.get('product_name') or '-'))}</td>"
        f"<td>{escape(str(row.get('grade') or '-'))}</td>"
        f"<td>{escape(_display_stem_length(row.get('stem_length')))}</td>"
        f"<td>{escape(str(row.get('unit') or '-'))}</td>"
        f"<td>{escape(format_product_number(row.get('base_cost')))}</td>"
        f"<td>{escape(format_product_number(row.get('current_stock')))}</td>"
        f"<td>{escape(sale_enabled_display(row.get('sale_enabled')))}</td>"
        f"<td><code>{escape(sku or '-')}</code></td>"
        f"<td>{edit_form}</td>"
        "</tr>"
    )


def _render_product_edit_form(products_path: str, row: dict[str, object], variety_options: list[str]) -> str:
    sku = str(row.get("internal_sku") or "")
    sale_enabled = "true" if sale_enabled_display(row.get("sale_enabled")) == "是" else "false"
    return f"""
    <details class="inline-details">
      <summary>编辑</summary>
      {_product_variety_datalist(variety_options, suffix=sku)}
      <form method="post" class="grid two-col compact-form product-edit-form">
        <input type="hidden" name="products_path" value="{escape(products_path)}">
        <input type="hidden" name="input_tab" value="inventory">
        <input type="hidden" name="internal_sku" value="{escape(sku)}">
        <div class="field">
          <label>内部 SKU</label>
          <input type="text" value="{escape(sku)}" readonly>
          <p class="help">SKU 用于系统识别商品和关联任务，通常不建议修改。</p>
        </div>
        <div class="field">
          <label>品种</label>
          <input name="product_name" list="product_variety_options_{escape(sku)}" type="text" value="{escape(str(row.get('product_name') or ''))}" required>
        </div>
        <div class="field">
          <label>等级</label>
          <select name="grade">{_options_html(GRADE_OPTIONS, str(row.get('grade') or 'B'))}</select>
        </div>
        <div class="field">
          <label>枝长/规格</label>
          <select name="stem_length">{_options_html(STEM_LENGTH_OPTIONS, _display_stem_length(row.get('stem_length')))}</select>
        </div>
        <div class="field">
          <label>单位</label>
          <select name="unit">{_options_html(UNIT_OPTIONS, str(row.get('unit') or '扎'))}</select>
        </div>
        <div class="field">
          <label>基础成本</label>
          <input name="base_cost" type="number" min="0" step="0.01" value="{escape(format_product_number(row.get('base_cost')))}" required>
        </div>
        <div class="field">
          <label>当前库存</label>
          <input name="current_stock" type="number" min="0" step="1" value="{escape(format_product_number(row.get('current_stock')))}" required>
        </div>
        <div class="field">
          <label>是否允许销售</label>
          <select name="sale_enabled">
            <option value="true" {'selected' if sale_enabled == 'true' else ''}>是</option>
            <option value="false" {'selected' if sale_enabled == 'false' else ''}>否</option>
          </select>
        </div>
        <p class="subtle">修改品种、等级、枝长或单位可能影响后续任务生成。为避免破坏历史任务和统计，默认保留原 SKU。</p>
        <div class="actions">
          <button class="secondary" type="submit" name="action" value="edit_product">保存商品资料</button>
        </div>
      </form>
    </details>
    """


def _product_variety_datalist(variety_options: list[str], suffix: str = "") -> str:
    datalist_id = f"product_variety_options_{suffix}" if suffix else "product_variety_options"
    options = "".join(f"<option value='{escape(option)}'></option>" for option in variety_options)
    return f"<datalist id='{escape(datalist_id)}'>{options}</datalist>"


def _render_new_variety_dialog() -> str:
    return """
    <dialog id="new_variety_dialog" class="modal-card">
      <form method="dialog" class="grid">
        <h3>新增品种</h3>
        <p class="subtle">新品种需要同时维护品种名称和品种代码。品种代码用于生成 SKU，不会包含平台信息。</p>
        <div class="field">
          <label for="new_variety_name">新品种名称</label>
          <input id="new_variety_name" type="text" autocomplete="off">
        </div>
        <div class="field">
          <label for="new_variety_code">品种代码</label>
          <input id="new_variety_code" type="text" autocomplete="off" placeholder="例如 AISHA">
        </div>
        <p class="help" id="new_variety_error" role="alert"></p>
        <div class="actions">
          <button class="primary" type="button" id="confirm_new_variety">加入品种列表</button>
          <button class="secondary" type="button" id="cancel_new_variety">取消</button>
        </div>
      </form>
    </dialog>
    """


def _render_new_platform_dialog(platform_mappings_path: str) -> str:
    return f"""
    <dialog id="new_platform_dialog" class="modal-card">
      <form method="post" class="grid">
        <input type="hidden" name="platform_mappings_path" value="{escape(platform_mappings_path)}">
        <input type="hidden" name="input_tab" value="inventory">
        <h3>新增平台</h3>
        <p class="subtle">这里维护的是销售平台选项。初始库存仍是公共库存，不会因为新增平台而拆分为平台库存。</p>
        <div class="field">
          <label for="new_platform_name">平台名称</label>
          <input id="new_platform_name" name="platform_name" type="text" autocomplete="off" required>
        </div>
        <div class="actions">
          <button class="primary" type="submit" name="action" value="add_platform">保存平台</button>
          <button class="secondary" type="button" id="cancel_new_platform">取消</button>
        </div>
      </form>
    </dialog>
    """


def _new_platform_dialog_script() -> str:
    return """
    <script>
      (() => {
        const dialog = document.getElementById("new_platform_dialog");
        const openButton = document.getElementById("open_new_platform_dialog");
        const cancelButton = document.getElementById("cancel_new_platform");
        if (!dialog || !openButton) return;
        openButton.addEventListener("click", () => {
          if (typeof dialog.showModal === "function") {
            dialog.showModal();
          } else {
            dialog.setAttribute("open", "open");
          }
        });
        if (cancelButton) {
          cancelButton.addEventListener("click", () => {
            if (typeof dialog.close === "function") {
              dialog.close();
            } else {
              dialog.removeAttribute("open");
            }
          });
        }
      })();
    </script>
    """


def _render_cold_storage_management(cold_storage_status_path: str, cold_storage_rows: list[dict[str, object]]) -> str:
    return f"""
    <section class="panel product-input-panel">
      <h2>冷库状态</h2>
      <p class="subtle">这里维护每日冷库占用情况。系统会用预计占用量和剩余容量判断是否需要冷库预警或人工复核。保存后如需影响脚本状态和复核，请运行对应自动规则评估。</p>
      {_render_cold_storage_form(cold_storage_status_path)}
    </section>
    {_render_cold_storage_list(cold_storage_status_path, cold_storage_rows)}
    """


def _render_cold_storage_form(cold_storage_status_path: str) -> str:
    return f"""
      <form method="post" class="inventory-form cold-storage-form">
        <input type="hidden" name="cold_storage_status_path" value="{escape(cold_storage_status_path)}">
        <input type="hidden" name="input_tab" value="cold_storage_status">
        <div class="inventory-row">
          <div class="field">
            <label for="cold_trade_date">业务日期</label>
            <input id="cold_trade_date" name="trade_date" type="date" required>
            <p class="help">选择这条冷库状态对应的业务日期。</p>
          </div>
          <div class="field">
            <label for="cold_active">是否启用</label>
            <select id="cold_active" name="active">
              <option value="true" selected>是</option>
              <option value="false">否</option>
            </select>
            <p class="help">同一业务日期只能保留一条启用的冷库状态。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="total_capacity_qty">冷库总容量</label>
            <input id="total_capacity_qty" name="total_capacity_qty" type="number" min="1" step="1" value="500" required>
            <p class="help">全场共享冷库容量，默认 500 扎。</p>
          </div>
          <div class="field">
            <label for="current_occupied_qty">当前占用量</label>
            <input id="current_occupied_qty" name="current_occupied_qty" type="number" min="0" step="1" value="0" required>
            <p class="help">当前已经占用冷库的数量。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="expected_inbound_qty">预计入库量</label>
            <input id="expected_inbound_qty" name="expected_inbound_qty" type="number" min="0" step="1" value="0" required>
            <p class="help">预计当天还会进入冷库的数量。</p>
          </div>
          <div class="field">
            <label for="expected_outbound_qty">预计出库量</label>
            <input id="expected_outbound_qty" name="expected_outbound_qty" type="number" min="0" step="1" value="0" required>
            <p class="help">预计当天会从冷库移出的数量。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="warning_threshold_qty">预警阈值</label>
            <input id="warning_threshold_qty" name="warning_threshold_qty" type="number" min="0" step="1" value="50" required>
            <p class="help">剩余容量小于或等于该值时，系统会建议冷库预警。</p>
          </div>
          <div class="field">
            <label for="projected_occupied_qty">预计占用量</label>
            <input id="projected_occupied_qty" name="projected_occupied_qty" type="number" min="0" step="1" value="0" required>
            <p class="help">默认 = 当前占用量 + 预计入库量 - 预计出库量，可人工确认。</p>
          </div>
        </div>
        <div class="inventory-row">
          <div class="field">
            <label for="remaining_capacity_qty">剩余容量</label>
            <input id="remaining_capacity_qty" name="remaining_capacity_qty" type="number" step="1" value="500" required>
            <p class="help">默认 = 冷库总容量 - 预计占用量，可人工确认。</p>
          </div>
          <div class="field">
            <label for="cold_note">备注</label>
            <textarea id="cold_note" name="note" rows="3"></textarea>
          </div>
        </div>
        <div class="actions">
          <button class="primary" type="submit" name="action" value="add_cold_storage_status">新增冷库状态</button>
        </div>
      </form>
      {_cold_storage_auto_capacity_script()}
    """


def _render_cold_storage_list(cold_storage_status_path: str, cold_storage_rows: list[dict[str, object]]) -> str:
    if not cold_storage_rows:
        return _render_empty_state("冷库状态", "当前还没有冷库状态。请先新增某个业务日期的冷库容量与占用情况。")
    rows_html = "".join(
        _render_cold_storage_row(cold_storage_status_path, row, row_index)
        for row_index, row in enumerate(cold_storage_rows)
    )
    return _render_table_panel(
        "冷库状态",
        [
            "trade_date",
            "total_capacity_qty",
            "current_occupied_qty",
            "expected_inbound_qty",
            "expected_outbound_qty",
            "projected_occupied_qty",
            "remaining_capacity_qty",
            "warning_threshold_qty",
            "active",
            "note",
            "action",
        ],
        rows_html,
        empty_message="当前还没有冷库状态。请先新增某个业务日期的冷库容量与占用情况。",
    )


def _render_cold_storage_row(cold_storage_status_path: str, row: dict[str, object], row_index: int) -> str:
    trade_date = _cold_storage_trade_date_value(row)
    projected = computed_projected_occupied_from_row(row)
    remaining = computed_remaining_capacity_from_row(row)
    return f"""
      <tr>
        <td>{escape(trade_date or '-')}</td>
        <td>{escape(format_cold_storage_number(row.get('total_capacity_qty')))}</td>
        <td>{escape(format_cold_storage_number(row.get('current_occupied_qty')))}</td>
        <td>{escape(format_cold_storage_number(row.get('expected_inbound_qty')))}</td>
        <td>{escape(format_cold_storage_number(row.get('expected_outbound_qty')))}</td>
        <td>{escape(format_cold_storage_number(projected))}</td>
        <td>{escape(format_cold_storage_number(remaining))}</td>
        <td>{escape(format_cold_storage_number(row.get('warning_threshold_qty')))}</td>
        <td>{escape(cold_storage_active_display(row.get('active')))}</td>
        <td>{escape(str(row.get('note') or '-'))}</td>
        <td>{_render_cold_storage_edit_form(cold_storage_status_path, row, row_index)}</td>
      </tr>
    """


def _render_cold_storage_edit_form(cold_storage_status_path: str, row: dict[str, object], row_index: int) -> str:
    trade_date = _cold_storage_trade_date_value(row)
    active = "true" if cold_storage_active_display(row.get("active")) == "是" else "false"
    projected = computed_projected_occupied_from_row(row)
    remaining = computed_remaining_capacity_from_row(row)
    return f"""
      <details>
        <summary>编辑</summary>
        <form method="post" class="grid product-edit-form cold-storage-edit-form">
          <input type="hidden" name="cold_storage_status_path" value="{escape(cold_storage_status_path)}">
          <input type="hidden" name="input_tab" value="cold_storage_status">
          <input type="hidden" name="current_trade_date" value="{escape(trade_date)}">
          <input type="hidden" name="current_row_index" value="{row_index}">
          <p class="help">保存后如需影响脚本状态和复核，请运行对应自动规则评估。</p>
          <div class="two-col">
            <div class="field">
              <label>业务日期</label>
              <input name="trade_date" type="date" value="{escape(trade_date)}" required>
            </div>
            <div class="field">
              <label>是否启用</label>
              <select name="active">
                <option value="true" {'selected' if active == 'true' else ''}>是</option>
                <option value="false" {'selected' if active == 'false' else ''}>否</option>
              </select>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>冷库总容量</label>
              <input name="total_capacity_qty" type="number" min="1" step="1" value="{escape(str(row.get('total_capacity_qty') or '500'))}" required>
            </div>
            <div class="field">
              <label>当前占用量</label>
              <input name="current_occupied_qty" type="number" min="0" step="1" value="{escape(str(row.get('current_occupied_qty') or '0'))}" required>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>预计入库量</label>
              <input name="expected_inbound_qty" type="number" min="0" step="1" value="{escape(str(row.get('expected_inbound_qty') or '0'))}" required>
            </div>
            <div class="field">
              <label>预计出库量</label>
              <input name="expected_outbound_qty" type="number" min="0" step="1" value="{escape(str(row.get('expected_outbound_qty') or '0'))}" required>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>预警阈值</label>
              <input name="warning_threshold_qty" type="number" min="0" step="1" value="{escape(str(row.get('warning_threshold_qty') or '50'))}" required>
            </div>
            <div class="field">
              <label>预计占用量</label>
              <input name="projected_occupied_qty" type="number" min="0" step="1" value="{escape(str(projected))}" required>
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label>剩余容量</label>
              <input name="remaining_capacity_qty" type="number" step="1" value="{escape(str(remaining))}" required>
            </div>
            <div class="field">
              <label>备注</label>
              <textarea name="note" rows="2">{escape(str(row.get('note') or ''))}</textarea>
            </div>
          </div>
          <button class="primary" type="submit" name="action" value="edit_cold_storage_status">保存冷库状态</button>
        </form>
      </details>
    """


def _cold_storage_trade_date_value(row: dict[str, object]) -> str:
    value = row.get("trade_date")
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


def _cold_storage_auto_capacity_script() -> str:
    return """
    <script>
      (function () {
        const form = document.querySelector(".cold-storage-form");
        if (!form) {
          return;
        }
        const total = form.querySelector("#total_capacity_qty");
        const current = form.querySelector("#current_occupied_qty");
        const inbound = form.querySelector("#expected_inbound_qty");
        const outbound = form.querySelector("#expected_outbound_qty");
        const projected = form.querySelector("#projected_occupied_qty");
        const remaining = form.querySelector("#remaining_capacity_qty");
        function toNumber(input) {
          const value = Number(input.value || 0);
          return Number.isFinite(value) ? value : 0;
        }
        function update() {
          const projectedValue = Math.max(0, toNumber(current) + toNumber(inbound) - toNumber(outbound));
          projected.value = String(projectedValue);
          remaining.value = String(toNumber(total) - projectedValue);
        }
        [total, current, inbound, outbound].forEach((input) => input.addEventListener("input", update));
        update();
      })();
    </script>
    """


def _new_variety_dialog_script() -> str:
    return """
    <script>
      (() => {
        const dialog = document.getElementById("new_variety_dialog");
        const openButton = document.getElementById("open_new_variety_dialog");
        const cancelButton = document.getElementById("cancel_new_variety");
        const confirmButton = document.getElementById("confirm_new_variety");
        const nameInput = document.getElementById("new_variety_name");
        const codeInput = document.getElementById("new_variety_code");
        const error = document.getElementById("new_variety_error");
        const productSelect = document.getElementById("product_name");
        const varietyCode = document.getElementById("variety_code");
        if (!dialog || !openButton || !cancelButton || !confirmButton || !nameInput || !codeInput || !productSelect || !varietyCode) {
          return;
        }
        const openDialog = () => {
          error.textContent = "";
          nameInput.value = "";
          codeInput.value = "";
          if (typeof dialog.showModal === "function") {
            dialog.showModal();
          } else {
            dialog.setAttribute("open", "open");
          }
          nameInput.focus();
        };
        const closeDialog = () => {
          if (typeof dialog.close === "function") {
            dialog.close();
          } else {
            dialog.removeAttribute("open");
          }
        };
        openButton.addEventListener("click", openDialog);
        cancelButton.addEventListener("click", closeDialog);
        confirmButton.addEventListener("click", () => {
          const name = nameInput.value.trim();
          const code = codeInput.value.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
          if (!name) {
            error.textContent = "请输入新品种名称。";
            return;
          }
          if (!code) {
            error.textContent = "请输入品种代码，只能包含英文字母和数字。";
            return;
          }
          let option = Array.from(productSelect.options).find((item) => item.value === name);
          if (!option) {
            option = new Option(name, name);
            productSelect.add(option);
          }
          productSelect.value = name;
          varietyCode.value = code;
          closeDialog();
        });
        productSelect.addEventListener("change", () => {
          varietyCode.value = "";
        });
      })();
    </script>
    """


def _options_html(options: list[str], selected: str) -> str:
    return "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected else ''}>{escape(option)}</option>"
        for option in options
    )


def _labeled_options(options: list[str], selected: str, category: str) -> str:
    return "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected else ''}>{escape(display_enum_label(category, option))}</option>"
        for option in options
    )


def _display_stem_length(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in {"follow_grade", "fg"}:
        return FOLLOW_GRADE_VALUE
    return text or "-"


def _follow_grade_rule_text() -> str:
    return "、".join(f"{grade}={length}" for grade, length in GRADE_STEM_LENGTH_MAP.items())


def render_system_page(*, runtime_db: str, session_user: str, message: str = "", level: str = "info") -> str:
    db_path = Path(runtime_db)
    config_checks = _build_system_config_checks()
    db_checks = _build_runtime_db_checks(db_path)
    count_checks = _build_runtime_table_count_checks(db_path)
    runtime_summary = _build_system_runtime_summary(db_path)
    body = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_system_title"])}</h2>
      <p class="subtle">{escape(UI_TEXT["ops_system_config_only"])}</p>
    </section>
    {_render_system_feishu_test_panel(runtime_db)}
    {_render_system_checks_table(UI_TEXT["ops_system_config_checks"], config_checks)}
    {_render_system_checks_table(UI_TEXT["ops_system_db_checks"], db_checks)}
    {_render_system_checks_table(UI_TEXT["ops_system_runtime_counts"], count_checks)}
    {_render_system_checks_table(UI_TEXT["ops_system_runtime_summary"], runtime_summary)}
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_system_connectivity"])}</h2>
      <p class="subtle">{escape(UI_TEXT["ops_system_external_note"])}</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>{escape(UI_TEXT["ops_system_item"])}</th><th>{escape(UI_TEXT["ops_system_status"])}</th><th>{escape(UI_TEXT["ops_system_value"])}</th></tr></thead>
          <tbody>
            <tr><td>Feishu Webhook</td><td>{_system_status_badge("not_configured", UI_TEXT["ops_system_not_verified"])}</td><td>{escape(_mask_config_value("FEISHU_WEBHOOK_URL", os.getenv("FEISHU_WEBHOOK_URL", "")))}</td></tr>
            <tr><td>Mobile Review Base URL</td><td>{_system_status_badge("not_configured", UI_TEXT["ops_system_not_verified"])}</td><td>{escape(_mask_config_value("MOBILE_REVIEW_BASE_URL", os.getenv("MOBILE_REVIEW_BASE_URL", "")))}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
    """
    return _render_ops_page(
        title=UI_TEXT["ops_system_title"],
        description=UI_TEXT["ops_system_config_only"],
        active_path="/system",
        runtime_db=runtime_db,
        session_user=session_user,
        body_html=body,
        message=_sanitize_notification_text(message),
        message_level=level,
    )


def _render_login_required_page(
    *,
    title: str,
    description: str,
    active_path: str,
    runtime_db: str,
    next_path: str,
    message: str,
) -> str:
    login_csrf_token = _issue_login_csrf_token()
    login_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_login_title"])}</h2>
      <form method="post" action="/runtime/login" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(runtime_db)}">
        <input type="hidden" name="next" value="{escape(next_path)}">
        <input type="hidden" name="csrf_token" value="{escape(login_csrf_token)}">
        <div class="field">
          <label for="runtime_username">{escape(UI_TEXT["runtime_login_user"])}</label>
          <input id="runtime_username" name="username" type="text" value="{escape(_runtime_admin_user())}">
        </div>
        <div class="field">
          <label for="runtime_password">{escape(UI_TEXT["runtime_login_password"])}</label>
          <input id="runtime_password" name="password" type="password" value="">
        </div>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_login_button"])}</button>
        </div>
      </form>
    </section>
    """
    return _render_ops_page(
        title=title,
        description=description,
        active_path=active_path,
        runtime_db=runtime_db,
        session_user=None,
        body_html=login_panel,
        message=message,
        message_level="error",
    )


def _render_ops_page(
    *,
    title: str,
    description: str,
    active_path: str,
    runtime_db: str,
    session_user: str | None,
    body_html: str,
    message: str = "",
    message_level: str = "info",
) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(title, description)}
    {navigation(active_path)}
    {_session_toolbar(runtime_db, session_user, active_path)}
    {_banner(message, message_level)}
    {body_html}
  </main>
</body>
</html>
"""


def _render_table_panel(title: str, headers: list[str], rows_html: str, *, empty_message: str) -> str:
    header_html = "".join(f"<th>{escape(_field_label(name))}</th>" for name in headers)
    body_html = rows_html or f"<tr><td colspan='{len(headers)}'>{escape(empty_message)}</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(title)}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr>{header_html}</tr></thead>
          <tbody>{body_html}</tbody>
        </table>
      </div>
    </section>
    """


def _render_empty_state(title: str, message: str) -> str:
    return f"""
    <section class="panel">
      <h2>{escape(title)}</h2>
      <p class="subtle">{escape(message)}</p>
    </section>
    """


def _render_pagination(path: str, pagination: dict[str, int] | None, query_params: dict[str, str]) -> str:
    if not pagination:
        return ""
    total = pagination["total"]
    page = pagination["page"]
    total_pages = pagination["total_pages"]
    start = pagination["start"]
    end = pagination["end"]
    if total == 0:
        return ""
    summary = f"显示 {start}-{end} / {total} 条，每页最多 {PAGE_SIZE} 条"
    links = ""
    clean_params = {key: value for key, value in query_params.items() if value}
    if page > 1:
        links += (
            f"<a class='nav-link utility-link' href='{escape(_append_query_to_path(path, clean_params | {'page': str(page - 1)}))}'>上一页</a>"
        )
    if page < total_pages:
        links += (
            f"<a class='nav-link utility-link' href='{escape(_append_query_to_path(path, clean_params | {'page': str(page + 1)}))}'>下一页</a>"
        )
    return f"""
    <section class="panel pagination-panel">
      <div class="toolbar-row">
        <span class="subtle">{escape(summary)}；第 {page} / {total_pages} 页</span>
        <div class="actions">{links}</div>
      </div>
    </section>
    """


def _parse_page_number(raw: str) -> int:
    try:
        page = int(str(raw).strip() or "1")
    except ValueError:
        return 1
    return max(1, page)


def _paginate_items(items: list, page: int) -> tuple[list, dict[str, int]]:
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    resolved_page = min(max(1, page), total_pages)
    start_index = (resolved_page - 1) * PAGE_SIZE
    end_index = min(start_index + PAGE_SIZE, total)
    return items[start_index:end_index], {
        "total": total,
        "page": resolved_page,
        "total_pages": total_pages,
        "start": start_index + 1 if total else 0,
        "end": end_index,
    }


def _sort_tasks_for_display(tasks: list) -> list:
    return sorted(
        tasks,
        key=lambda task: (
            1 if task.task_status.value in TERMINAL_TASK_STATUS_VALUES else 0,
            -_datetime_sort_value(task.created_at),
            task.task_id,
        ),
    )


def _sort_reviews_for_display(reviews: list) -> list:
    return sorted(
        reviews,
        key=lambda review: (
            0 if review.review_status == ReviewTaskStatus.PENDING else 1,
            -_datetime_sort_value(review.created_at or review.resolved_at),
            review.review_task_id,
        ),
    )


def _sort_notifications_for_display(notifications: list) -> list:
    return sorted(
        notifications,
        key=lambda log: (
            -_datetime_sort_value(log.created_at or log.sent_at),
            log.notification_id,
        ),
    )


def _sort_execution_logs_for_display(logs: list) -> list:
    return sorted(
        logs,
        key=lambda log: (
            -_datetime_sort_value(log.created_at or log.start_time),
            log.log_id,
        ),
    )


def _datetime_sort_value(value) -> float:
    if not isinstance(value, datetime):
        return 0.0
    try:
        return value.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _task_center_tabs(active_tab: str) -> str:
    links = [
        ("tasks", "任务状态", "/tasks"),
        ("automation", "脚本状态", _append_query_to_path("/tasks", {"task_tab": "automation"})),
        ("mock_platform", "Mock 平台测试台", _append_query_to_path("/tasks", {"task_tab": "mock_platform"})),
    ]
    items = "".join(
        f"<a class='nav-link {'active' if tab == active_tab else ''}' href='{escape(href)}'>{escape(label)}</a>"
        for tab, label, href in links
    )
    return f"""
    <section class="panel task-center-tabs">
      <div class="actions">{items}</div>
    </section>
    """


def _mock_platform_status_label(value: str) -> str:
    labels = {
        "online": "已上架",
        "offline": "已下架",
    }
    return labels.get(value, value or "-")


def _task_filter_panel(
    runtime_db: str,
    task_status: str,
    trade_date_filter: str = "",
    action_type_filter: str = "",
    scope_type_filter: str = "",
    scope_key_filter: str = "",
) -> str:
    options = _status_options(TaskStatus, task_status, "task_status")
    action_options = _status_options(TaskActionType, action_type_filter, "action_type")
    return f"""
    <section class="panel">
      <form method="get" action="/tasks" class="grid two-col">
        <input type="hidden" name="task_tab" value="tasks">
        <div class="field">
          <label for="task_status">{escape(FIELD_LABELS.get("task_status", "task_status"))}</label>
          <select id="task_status" name="task_status">{options}</select>
        </div>
        <div class="field">
          <label for="trade_date">{escape(_field_label("trade_date"))}</label>
          <input id="trade_date" name="trade_date" type="text" value="{escape(trade_date_filter)}" placeholder="YYYY-MM-DD">
        </div>
        <div class="field">
          <label for="action_type">{escape(_field_label("action_type"))}</label>
          <select id="action_type" name="action_type">{action_options}</select>
        </div>
        <div class="field">
          <label for="scope_type">{escape(_field_label("scope_type"))}</label>
          <input id="scope_type" name="scope_type" type="text" value="{escape(scope_type_filter)}">
        </div>
        <div class="field">
          <label for="scope_key">{escape(_field_label("scope_key"))}</label>
          <input id="scope_key" name="scope_key" type="text" value="{escape(scope_key_filter)}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["ops_filter_apply"])}</button>
        </div>
      </form>
    </section>
    """


def _review_filter_panel(runtime_db: str, review_status: str, due_filter: str) -> str:
    options = _status_options(ReviewTaskStatus, review_status, "review_status")
    checked = " checked" if due_filter == "soon" else ""
    return f"""
    <section class="panel">
      <form method="get" action="/reviews" class="grid two-col">
        <div class="field">
          <label for="review_status">{escape(FIELD_LABELS.get("review_status", "review_status"))}</label>
          <select id="review_status" name="review_status">{options}</select>
        </div>
        <label class="checkbox">
          <input type="checkbox" name="due" value="soon"{checked}>
          {escape(UI_TEXT["ops_filter_due_soon"])}
        </label>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["ops_filter_apply"])}</button>
        </div>
      </form>
    </section>
    """


def _notification_filter_panel(
    runtime_db: str,
    send_status: str,
    related_review_task_id: str = "",
    channel: str = "",
) -> str:
    options = _status_options(NotificationSendStatus, send_status, "send_status")
    return f"""
    <section class="panel">
      <form method="get" action="/notifications" class="grid two-col">
        <div class="field">
          <label for="send_status">{escape(FIELD_LABELS.get("send_status", "send_status"))}</label>
          <select id="send_status" name="send_status">{options}</select>
        </div>
        <div class="field">
          <label for="related_review_task_id">{escape(_field_label("related_review_task_id"))}</label>
          <input id="related_review_task_id" name="related_review_task_id" type="text" value="{escape(related_review_task_id)}">
        </div>
        <div class="field">
          <label for="channel">{escape(_field_label("channel"))}</label>
          <input id="channel" name="channel" type="text" value="{escape(channel)}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["ops_filter_apply"])}</button>
        </div>
      </form>
    </section>
    """


def _render_notification_center_detail(
    *,
    notification_id: str,
    selected_notification,
    selected_review,
    selected_task,
) -> str:
    if not notification_id:
        return ""
    if selected_notification is None:
        return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_notification_detail_title"])}</h2>
      <p class="subtle">{escape(UI_TEXT["ops_notification_not_found"])}</p>
    </section>
    """
    notification_details = [
        ("notification_id", selected_notification.notification_id),
        ("related_review_task_id", selected_notification.related_review_task_id or "-"),
        ("related_task_id", selected_notification.related_task_id or "-"),
        ("recipient_type", selected_notification.recipient_type),
        ("recipient", selected_notification.recipient),
        ("channel", display_enum_label("channel", selected_notification.channel)),
        ("send_status", display_enum_label("send_status", selected_notification.send_status)),
        ("sent_at", format_display_datetime(selected_notification.sent_at)),
        ("created_at", format_display_datetime(selected_notification.created_at)),
        ("dedupe_key", selected_notification.dedupe_key),
        ("message", _truncate_text(_sanitize_notification_text(selected_notification.message), 4000)),
        ("error_message", _truncate_text(_sanitize_notification_text(selected_notification.error_message or "-"), 4000)),
    ]
    if selected_notification.channel == "feishu":
        notification_details.extend(
            [
                ("current_feishu_message_type", _current_feishu_message_type()),
                ("note", UI_TEXT["ops_notification_config_snapshot_note"]),
            ]
        )
    detail_rows = "".join(
        f"<tr><th>{escape(_field_label(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in notification_details
    )
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_notification_detail_title"])}</h2>
      <div class="table-wrap"><table><tbody>{detail_rows}</tbody></table></div>
    </section>
    {_render_notification_related_review_panel(selected_review, selected_notification.related_review_task_id)}
    {_render_notification_related_task_panel(selected_task, selected_notification.related_task_id)}
    """


def _render_notification_related_review_panel(selected_review, related_review_task_id: str | None) -> str:
    if selected_review is None:
        content = f"<p class='subtle'>{escape(UI_TEXT['ops_notification_no_related_review'])}</p>"
    else:
        href = _append_query_to_path("/reviews", {"review_task_id": selected_review.review_task_id})
        rows = "".join(
            f"<tr><th>{escape(_field_label(label))}</th><td>{value}</td></tr>"
            for label, value in [
                ("review_task_id", f"<a href='{escape(href)}'>{escape(selected_review.review_task_id)}</a>"),
                ("review_type", escape(display_enum_label("review_type", selected_review.review_type))),
                ("review_status", _status_badge(selected_review.review_status.value, "review_status")),
                ("trade_date", escape(selected_review.trade_date.isoformat() if selected_review.trade_date else "-")),
                ("scope", escape(format_object_scope(selected_review.scope_type, selected_review.scope_key))),
                ("reason", escape(_truncate_text(_sanitize_notification_text(selected_review.reason or "-"), 240))),
            ]
        )
        content = f"<div class='table-wrap'><table><tbody>{rows}</tbody></table></div>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_notification_related_review_title"])}</h2>
      {content}
    </section>
    """


def _render_notification_related_task_panel(selected_task, related_task_id: str | None) -> str:
    if selected_task is None:
        content = f"<p class='subtle'>{escape(UI_TEXT['ops_notification_no_related_task'])}</p>"
    else:
        rows = "".join(
            f"<tr><th>{escape(_field_label(label))}</th><td>{value}</td></tr>"
            for label, value in [
                ("task_id", escape(selected_task.task_id)),
                ("task_status", _status_badge(selected_task.task_status.value, "task_status")),
                ("action_type", escape(display_enum_label("action_type", selected_task.action_type.value))),
                ("trade_date", escape(selected_task.trade_date.isoformat() if selected_task.trade_date else "-")),
                ("scope", escape(format_object_scope(selected_task.scope_type, selected_task.scope_key))),
                ("required_by", escape(format_display_datetime(selected_task.required_by))),
            ]
        )
        content = f"<div class='table-wrap'><table><tbody>{rows}</tbody></table></div>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_notification_related_task_title"])}</h2>
      {content}
    </section>
    """


def _render_task_center_detail(
    *,
    selected_task_id: str,
    selected_task,
    task_history,
    related_reviews,
    related_notifications,
    execution_logs,
    listing_action_projections,
) -> str:
    if not selected_task_id:
        return ""
    if selected_task is None:
        return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_task_detail_title"])}</h2>
      <p class="subtle">{escape(UI_TEXT["ops_task_not_found"])}</p>
    </section>
    """
    task_details = [
        ("task_id", selected_task.task_id),
        ("trade_date", selected_task.trade_date.isoformat() if selected_task.trade_date else "-"),
        ("scope_type", display_enum_label("scope_type", selected_task.scope_type)),
        ("scope_key", selected_task.scope_key),
        ("dedupe_key", selected_task.dedupe_key),
        ("internal_sku", selected_task.internal_sku or "-"),
        ("platform_name", selected_task.platform_name or "-"),
        ("action_type", display_enum_label("action_type", selected_task.action_type.value)),
        ("task_status", display_enum_label("task_status", selected_task.task_status.value)),
        ("priority", selected_task.priority),
        ("expected_old_price", selected_task.expected_old_price if selected_task.expected_old_price is not None else "-"),
        ("target_price", selected_task.target_price if selected_task.target_price is not None else "-"),
        ("target_status", selected_task.target_status or "-"),
        ("pricing_source", selected_task.pricing_source.value if selected_task.pricing_source else "-"),
        ("scheduled_at", format_display_datetime(selected_task.scheduled_at)),
        ("expires_at", format_display_datetime(selected_task.expires_at)),
        ("required_by", format_display_datetime(selected_task.required_by)),
        ("created_at", format_display_datetime(selected_task.created_at)),
        ("updated_at", format_display_datetime(selected_task.updated_at)),
        ("result_message", _sanitize_notification_text(selected_task.result_message or "-")),
    ]
    detail_rows = "".join(
        f"<tr><th>{escape(_field_label(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in task_details
    )
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_task_detail_title"])}</h2>
      <div class="table-wrap"><table><tbody>{detail_rows}</tbody></table></div>
      {_json_preview_block("decision_trace_json", selected_task.decision_trace)}
    </section>
    {_render_review_history_panel(task_history)}
    {_render_task_related_reviews_panel(related_reviews)}
    {_render_task_related_notifications_panel(related_notifications)}
    {_render_task_listing_action_projection_panel(listing_action_projections)}
    {_render_task_execution_logs_panel(execution_logs)}
    """


def _render_task_listing_action_projection_panel(projections) -> str:
    if not projections:
        return ""

    rows = []
    for projection in projections:
        attempts = projection.get("attempts")
        normalized_attempts = (
            attempts if isinstance(attempts, list) else []
        )
        attempt_summary = "；".join(
            (
                f"{attempt.get('execution_mode') or '-'}:"
                f"{attempt.get('execution_attempt_id') or '-'}:"
                f"{attempt.get('status') or '-'}"
            )
            for attempt in normalized_attempts
            if isinstance(attempt, dict)
        ) or "-"
        reconcile_status = "；".join(
            (
                f"{attempt.get('execution_attempt_id') or '-'}:"
                f"{attempt.get('status') or '-'}"
            )
            for attempt in normalized_attempts
            if isinstance(attempt, dict)
            and str(attempt.get("execution_mode") or "").upper() == "RECONCILE"
        ) or "-"
        operation_result = str(projection.get("operation_result") or "")
        if operation_result == "VERIFIED":
            actual_status = str(projection.get("target_status") or "-")
        elif operation_result == "NOT_APPLIED":
            actual_status = str(projection.get("expected_old_status") or "-")
        elif operation_result == "NEEDS_RECONCILIATION":
            actual_status = "UNKNOWN"
        else:
            actual_status = "-"
        rows.append(
            "<tr>"
            f"<td>{escape(str(projection.get('action_type') or '-'))}</td>"
            f"<td>{escape(str(projection.get('expected_old_status') or '-'))}</td>"
            f"<td>{escape(str(projection.get('target_status') or '-'))}</td>"
            f"<td>{escape(actual_status)}</td>"
            f"<td>{escape(str(projection.get('batch_id') or '-'))}</td>"
            f"<td>{escape(str(projection.get('operation_id') or '-'))}</td>"
            f"<td>{escape(attempt_summary)}</td>"
            f"<td>{escape(str(projection.get('batch_status') or '-'))}</td>"
            f"<td>{escape(str(projection.get('operation_status') or '-'))}</td>"
            f"<td>{escape(reconcile_status)}</td>"
            f"<td>{escape(str(projection.get('readback_observed_at') or '-'))}</td>"
            f"<td>{escape(str(projection.get('error_code') or '-'))}</td>"
            "</tr>"
        )
    return f"""
    <section class="panel">
      <h2>上下架运行投影</h2>
      <p class="subtle">该面板只读展示 v5 批次、逐商品 operation、attempt、UNKNOWN 和 RECONCILE 事实；人工处理请使用关联复核任务，不在此处自动审批或重新发布。</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>动作</th><th>预期旧状态</th><th>目标状态</th><th>实际回读状态</th><th>批次 ID</th><th>operation ID</th><th>execution attempt</th><th>批次状态</th><th>operation 状态</th><th>RECONCILE</th><th>observed_at</th><th>错误代码</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    </section>
    """


def _render_script_run_detail(selected_script_run, script_run_items) -> str:
    if selected_script_run is None:
        return ""
    details = [
        ("script_run_id", selected_script_run.script_run_id),
        ("evaluator_name", selected_script_run.evaluator_name),
        ("description", selected_script_run.description),
        ("run_mode", selected_script_run.run_mode),
        ("run_status", selected_script_run.run_status),
        ("trade_date", selected_script_run.trade_date.isoformat() if selected_script_run.trade_date else "-"),
        ("started_at", format_display_datetime(selected_script_run.started_at)),
        ("finished_at", format_display_datetime(selected_script_run.finished_at)),
        ("created_by", selected_script_run.created_by),
        ("error_message", _sanitize_display_text(selected_script_run.error_message or "-")),
    ]
    detail_rows = "".join(
        f"<tr><th>{escape(_field_label(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in details
    )
    item_rows = "".join(
        "<tr>"
        f"<td>{escape(item.item_id)}</td>"
        f"<td>{escape(item.proposal_type)}</td>"
        f"<td>{escape(item.severity)}</td>"
        f"<td>{_status_badge(item.item_status, 'script_run_item_status')}</td>"
        f"<td>{escape(_truncate_text(_sanitize_display_text(item.message), 200))}</td>"
        f"<td>{escape(item.related_task_id or '-')}</td>"
        f"<td>{escape(item.related_review_task_id or '-')}</td>"
        f"<td>{escape(item.related_notification_id or '-')}</td>"
        f"<td>{escape(_truncate_text(_sanitize_display_text(item.error_message or '-'), 160))}</td>"
        "</tr>"
        for item in script_run_items
    ) or "<tr><td colspan='9'>该脚本运行暂无明细。</td></tr>"
    return f"""
    <section class="panel">
      <h2>脚本运行详情</h2>
      <div class="table-wrap"><table><tbody>{detail_rows}</tbody></table></div>
      {_json_preview_block("summary_json", selected_script_run.summary)}
    </section>
    <section class="panel">
      <h2>脚本运行明细</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>item_id</th><th>proposal_type</th><th>severity</th><th>item_status</th><th>message</th><th>related_task_id</th><th>related_review_task_id</th><th>related_notification_id</th><th>error_message</th></tr></thead>
          <tbody>{item_rows}</tbody>
        </table>
      </div>
      {_render_script_run_item_json_blocks(script_run_items)}
    </section>
    """


def _render_script_run_item_json_blocks(items) -> str:
    if not items:
        return ""
    blocks = []
    for item in items[:20]:
        blocks.append(
            f"<h3>{escape(item.item_id)}</h3>"
            f"{_json_preview_block('payload_json', item.payload)}"
            f"{_json_preview_block('decision_trace_json', item.decision_trace)}"
        )
    return "".join(blocks)


def _render_task_related_reviews_panel(related_reviews) -> str:
    rows = "".join(
        "<tr>"
        f"<td><a href='{escape(_append_query_to_path('/reviews', {'review_task_id': review.review_task_id}))}'>{escape(review.review_task_id)}</a></td>"
        f"<td>{escape(display_enum_label('review_type', review.review_type))}</td>"
        f"<td>{_status_badge(review.review_status.value, 'review_status')}</td>"
        f"<td>{escape(review.reason or '-')}</td>"
        f"<td>{escape(format_display_datetime(review.required_by))}</td>"
        "</tr>"
        for review in related_reviews[:50]
    ) or f"<tr><td colspan='5'>{escape(UI_TEXT['ops_task_no_related_reviews'])}</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_task_related_reviews_title"])}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>review_task_id</th><th>review_type</th><th>review_status</th><th>reason</th><th>required_by</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def _render_task_related_notifications_panel(related_notifications) -> str:
    rows = "".join(
        "<tr>"
        f"<td><a href='{escape(_append_query_to_path('/notifications', {'notification_id': log.notification_id}))}'>{escape(log.notification_id)}</a></td>"
        f"<td>{escape(relation_label)}</td>"
        f"<td>{escape(log.related_review_task_id or '-')}</td>"
        f"<td>{escape(display_enum_label('channel', log.channel))}</td>"
        f"<td>{_status_badge(log.send_status, 'send_status')}</td>"
        f"<td>{escape(format_display_datetime(log.sent_at))}</td>"
        f"<td>{escape(_truncate_text(_sanitize_notification_text(log.message), 180))}</td>"
        "</tr>"
        for log, relation_label in related_notifications[:50]
    ) or f"<tr><td colspan='7'>{escape(UI_TEXT['ops_task_no_related_notifications'])}</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_task_related_notifications_title"])}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>notification_id</th><th>relation</th><th>related_review_task_id</th><th>channel</th><th>send_status</th><th>sent_at</th><th>message</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def _render_task_execution_logs_panel(execution_logs) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(log.log_id)}</td>"
        f"<td>{escape(log.executor_name)}</td>"
        f"<td>{escape(str(log.success_flag))}</td>"
        f"<td>{escape(format_display_datetime(log.start_time))}</td>"
        f"<td>{escape(format_display_datetime(log.end_time))}</td>"
        f"<td>{escape(_truncate_text(_sanitize_task_sensitive_text(log.error_message or '-'), 180))}</td>"
        f"<td>{_render_shadowbot_log_summary(log)}{_json_details_block({'raw_output': _truncate_text(_sanitize_task_sensitive_text(log.raw_output or '-'), 4000)})}</td>"
        "</tr>"
        for log in execution_logs[:50]
    ) or f"<tr><td colspan='7'>{escape(UI_TEXT['ops_task_no_execution_logs'])}</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_task_related_execution_logs_title"])}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>log_id</th><th>executor_name</th><th>success_flag</th><th>start_time</th><th>end_time</th><th>error_message</th><th>raw_output</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def _render_review_center_detail(
    *,
    runtime_db: str,
    session_user: str,
    selected_review,
    source_task,
    task_history,
    related_notifications,
    review_tokens,
    review_status: str,
    due_filter: str,
) -> str:
    if selected_review is None:
        return ""
    details = [
        ("review_task_id", selected_review.review_task_id),
        ("trade_date", selected_review.trade_date.isoformat() if selected_review.trade_date else "-"),
        ("review_type", display_enum_label("review_type", selected_review.review_type)),
        ("review_status", display_enum_label("review_status", selected_review.review_status.value)),
        ("scope", format_object_scope(selected_review.scope_type, selected_review.scope_key)),
        ("source_task_id", selected_review.source_task_id or "-"),
        ("reason", selected_review.reason or "-"),
        ("required_by", format_display_datetime(selected_review.required_by)),
        ("resolved_by", selected_review.resolved_by or "-"),
        ("resolved_at", format_display_datetime(selected_review.resolved_at)),
        ("resolution_note", selected_review.resolution_note or "-"),
    ]
    detail_rows = "".join(
        f"<tr><th>{escape(_field_label(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in details
    )
    review_payload_html = _json_preview_block("review_payload_json", selected_review.review_payload)
    resolution_payload_html = _json_preview_block("resolution_payload_json", selected_review.resolution_payload)
    source_html = _render_review_source_task_panel(source_task)
    history_html = _render_review_history_panel(task_history)
    notifications_html = _render_review_notifications_panel(related_notifications)
    tokens_html = _render_review_tokens_panel(review_tokens)
    resolve_html = _render_review_resolution_form(
        selected_review=selected_review,
        source_task=source_task,
        review_status=review_status,
        due_filter=due_filter,
    )
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_review_detail_title"])}</h2>
      <div class="table-wrap"><table><tbody>{detail_rows}</tbody></table></div>
      <div class="grid two-col">
        {review_payload_html}
        {resolution_payload_html}
      </div>
    </section>
    {source_html}
    {history_html}
    {notifications_html}
    {tokens_html}
    {resolve_html}
    """


def _render_review_source_task_panel(source_task) -> str:
    if source_task is None:
        rows = "<tr><td colspan='2'>-</td></tr>"
    else:
        details = [
            ("task_id", source_task.task_id),
            ("task_status", display_enum_label("task_status", source_task.task_status.value)),
            ("action_type", display_enum_label("action_type", source_task.action_type.value)),
            ("scope", format_object_scope(source_task.scope_type, source_task.scope_key)),
            ("expected_old_price", source_task.expected_old_price if source_task.expected_old_price is not None else "-"),
            ("target_price", source_task.target_price if source_task.target_price is not None else "-"),
            ("target_status", source_task.target_status or "-"),
            ("required_by", format_display_datetime(source_task.required_by)),
            ("result_message", source_task.result_message or "-"),
        ]
        rows = "".join(
            f"<tr><th>{escape(_field_label(label))}</th><td>{escape(str(value))}</td></tr>"
            for label, value in details
        )
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_review_source_task_title"])}</h2>
      <div class="table-wrap"><table><tbody>{rows}</tbody></table></div>
    </section>
    """


def _render_review_history_panel(task_history) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(format_display_datetime(item.changed_at))}</td>"
        f"<td>{escape(display_enum_label('task_status', item.from_status.value if item.from_status else ''))}</td>"
        f"<td>{escape(display_enum_label('task_status', item.to_status.value))}</td>"
        f"<td>{escape(item.changed_by)}</td>"
        f"<td>{escape(item.reason)}</td>"
        f"<td>{_metadata_summary_rows(item.metadata)}{_json_details_block(item.metadata)}</td>"
        "</tr>"
        for item in task_history
    ) or f"<tr><td colspan='6'>{escape(UI_TEXT['runtime_history_empty'])}</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_review_history_title"])}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>changed_at</th><th>from</th><th>to</th><th>changed_by</th><th>reason</th><th>metadata_json</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def _render_review_notifications_panel(notifications) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(log.notification_id)}</td>"
        f"<td>{escape(log.related_task_id or '-')}</td>"
        f"<td>{escape(display_enum_label('channel', log.channel))}</td>"
        f"<td>{_status_badge(log.send_status, 'send_status')}</td>"
        f"<td>{escape(format_display_datetime(log.sent_at))}</td>"
        f"<td>{escape(_sanitize_display_text(log.message or '-'))}</td>"
        f"<td>{escape(_sanitize_display_text(log.error_message or '-'))}</td>"
        "</tr>"
        for log in notifications[:50]
    ) or "<tr><td colspan='7'>-</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_review_related_notifications_title"])}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>notification_id</th><th>related_task_id</th><th>channel</th><th>send_status</th><th>sent_at</th><th>message</th><th>error_message</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def _render_review_tokens_panel(tokens) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(token.token_id)}</td>"
        f"<td>{escape(token.token_subject)}</td>"
        f"<td>{escape(', '.join(token.allowed_actions))}</td>"
        f"<td>{escape(format_display_datetime(token.expires_at))}</td>"
        f"<td>{escape(format_display_datetime(token.used_at))}</td>"
        f"<td>{escape(format_display_datetime(token.revoked_at))}</td>"
        f"<td>{escape(format_display_datetime(token.last_used_at))}</td>"
        "</tr>"
        for token in tokens[:50]
    ) or "<tr><td colspan='7'>-</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_review_tokens_title"])}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>token_id</th><th>token_subject</th><th>allowed_actions</th><th>expires_at</th><th>used_at</th><th>revoked_at</th><th>last_used_at</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def _render_review_resolution_form(*, selected_review, source_task, review_status: str, due_filter: str) -> str:
    if selected_review.review_status != ReviewTaskStatus.PENDING:
        return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_review_handle_title"])}</h2>
      <p class="subtle">{escape(UI_TEXT["ops_review_handled_hint"])}</p>
    </section>
    """
    resolve_status_options = _review_status_options(
        selected_review,
        source_task,
        use_display_labels=True,
    )
    next_source_status = _review_source_status_hint(selected_review, source_task)
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_review_handle_title"])}</h2>
      <form method="post" action="/reviews" class="grid">
        <input type="hidden" name="action" value="resolve_review">
        <input type="hidden" name="review_task_id" value="{escape(selected_review.review_task_id)}">
        <input type="hidden" name="review_status_filter" value="{escape(review_status)}">
        <input type="hidden" name="due" value="{escape(due_filter)}">
        <div class="field">
          <label for="review_status">{escape(_field_label("review_status"))}</label>
          <select id="review_status" name="review_status">{resolve_status_options}</select>
        </div>
        <div class="field">
          <label for="resolution_note">{escape(UI_TEXT["runtime_resolution_note"])}</label>
          <input id="resolution_note" name="resolution_note" type="text" value="">
        </div>
        <div class="field">
          <label for="resolution_payload_json">{escape(UI_TEXT["runtime_resolution_payload"])}</label>
          <textarea id="resolution_payload_json" name="resolution_payload_json" rows="8">{{}}</textarea>
        </div>
        <p class="subtle">source_task_status: {escape(next_source_status)}</p>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_submit_review"])}</button>
        </div>
      </form>
    </section>
    """


def _review_source_status_hint(selected_review, source_task) -> str:
    if source_task is None:
        return "-"
    if source_task.task_status == TaskStatus.MANUAL_REVIEW:
        if is_execution_failure_review(selected_review, source_task):
            return "重试任务后转为待处理；取消任务后转为已取消"
        return "通过后转为待处理；拒绝或调整后跳过；取消后取消"
    if source_task.task_status == TaskStatus.PENDING and source_task.action_type in {
        TaskActionType.CAPACITY_WARNING,
        TaskActionType.LABOR_REQUIRED,
        TaskActionType.MANUAL_PRICE_REVIEW,
        TaskActionType.BELOW_BREAK_EVEN_REVIEW,
        TaskActionType.SHORTAGE_WARNING,
        TaskActionType.COLD_STORAGE_WARNING,
        TaskActionType.CLEARANCE_WARNING,
        TaskActionType.MANUAL_REVIEW,
    }:
        return "通过、拒绝或调整后跳过；取消后取消"
    return "-"


def _review_status_options(
    review,
    source_task,
    *,
    use_display_labels: bool,
) -> str:
    return "".join(
        f"<option value='{escape(status.value)}'>"
        f"{escape(_review_action_display_label(review, source_task, status) if use_display_labels else _review_action_raw_label(review, source_task, status))}"
        "</option>"
        for status in allowed_review_statuses(review, source_task)
    )


def _review_action_display_label(review, source_task, status: ReviewTaskStatus) -> str:
    business_label = review_action_label(review, source_task, status)
    if business_label:
        return business_label
    return display_enum_label("review_status", status.value)


def _review_action_raw_label(review, source_task, status: ReviewTaskStatus) -> str:
    business_label = review_action_label(review, source_task, status)
    return business_label or status.value


def _review_action_hint(review, review_status: str, due_filter: str) -> str:
    href = _append_query_to_path(
        "/reviews",
        {
            "review_task_id": review.review_task_id,
            "review_status": review_status,
            "due": due_filter,
        },
    )
    status_hint = "可处理" if review.review_status == ReviewTaskStatus.PENDING else "已处理"
    return (
        f"<a href='{escape(href)}'>{escape(UI_TEXT['ops_review_detail_link'])}</a>"
        f"<br><span class='subtle'>{escape(status_hint)}</span>"
    )


SHADOWBOT_WARNING_STATUSES = {
    "NEEDS_RECONCILIATION",
    "POST_SUBMIT_PRICE_MISMATCH",
    "OLD_PRICE_CHANGED",
    "EVIDENCE_UPLOAD_FAILED",
}


def _load_shadowbot_queue_status() -> dict[str, object]:
    queue_dir = Path(
        os.environ.get("SHADOWBOT_QUEUE_DIR")
        or os.environ.get("SHADOWBOT_REQUEST_DIR")
        or "data/runtime/shadowbot_queue"
    )
    heartbeat_path = queue_dir / "heartbeat.json"
    heartbeat = {}
    if heartbeat_path.exists():
        heartbeat = _parse_json_object(heartbeat_path.read_text(encoding="utf-8-sig"))
    phases = []
    working_dir = queue_dir / "working"
    if working_dir.exists():
        for phase_path in sorted(working_dir.glob("*.phase.json"))[:20]:
            phase = _parse_json_object(phase_path.read_text(encoding="utf-8-sig"))
            if phase:
                phases.append(phase)
    return {
        "queue_dir": str(queue_dir),
        "heartbeat": heartbeat,
        "phases": phases,
        "quarantine_count": len(list((queue_dir / "quarantine").glob("*"))) if (queue_dir / "quarantine").exists() else 0,
    }


def _render_shadowbot_queue_status(status) -> str:
    if not isinstance(status, dict):
        return ""
    heartbeat = status.get("heartbeat") if isinstance(status.get("heartbeat"), dict) else {}
    phases = status.get("phases") if isinstance(status.get("phases"), list) else []
    phase_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('execution_attempt_id') or '-'))}</td>"
        f"<td>{escape(str(item.get('execution_mode') or '-'))}</td>"
        f"<td>{escape(str(item.get('phase') or '-'))}</td>"
        f"<td>{escape(str(item.get('side_effect_state') or '-'))}</td>"
        f"<td>{escape(str(item.get('updated_at') or '-'))}</td>"
        "</tr>"
        for item in phases
        if isinstance(item, dict)
    ) or "<tr><td colspan='5'>当前没有 working attempt。</td></tr>"
    return f"""
    <section class="panel">
      <h2>ShadowBot 队列状态</h2>
      <p class="subtle">队列目录：{escape(str(status.get('queue_dir') or '-'))}</p>
      <p>Worker：{escape(str(heartbeat.get('worker_id') or '-'))} · 状态：{escape(str(heartbeat.get('status') or '未启动'))} · 心跳：{escape(str(heartbeat.get('updated_at') or '-'))} · 隔离文件：{escape(str(status.get('quarantine_count') or 0))}</p>
      <div class="table-wrap"><table><thead><tr><th>attempt</th><th>模式</th><th>phase</th><th>副作用</th><th>更新时间</th></tr></thead><tbody>{phase_rows}</tbody></table></div>
    </section>
    """


def _render_shadowbot_log_summary(log) -> str:
    if log.executor_name != "shadowbot_executor":
        return "<span class='subtle'>-</span>"
    payload = _parse_json_object(log.raw_output)
    if not payload:
        return "<span class='subtle'>ShadowBot raw_output 为空或不是 JSON</span>"

    status = str(payload.get("status") or "-")
    error_code = str(payload.get("error_code") or log.error_code or "")
    side_effect_state = str(payload.get("side_effect_state") or "-")
    warning_keys = {status, error_code, side_effect_state} & SHADOWBOT_WARNING_STATUSES
    warning_html = ""
    if warning_keys:
        warning_html = (
            "<div class='banner warning'>"
            f"{escape(' / '.join(sorted(warning_keys)))}：需要人工关注或只读对账"
            "</div>"
        )

    fields = [
        ("operation_id", payload.get("operation_id")),
        ("execution_attempt_id", payload.get("execution_attempt_id")),
        ("shadowbot_run_id", payload.get("shadowbot_run_id")),
        ("execution_mode", payload.get("execution_mode")),
        ("worker_id", payload.get("worker_id")),
        ("queue_phase", payload.get("queue_phase") or payload.get("recovered_phase")),
        ("worker_heartbeat_at", payload.get("worker_heartbeat_at")),
        ("status", status),
        ("side_effect_state", side_effect_state),
        ("old_price", payload.get("old_price") or payload.get("expected_old_price")),
        ("target_price", payload.get("target_price")),
        ("actual_price", payload.get("actual_price") or payload.get("verified_price")),
        ("evidence_status", payload.get("evidence_status")),
        ("approved_payload_hash", payload.get("approved_payload_hash")),
        ("instruction_hash", payload.get("instruction_hash")),
        ("request_file_sha256", payload.get("request_file_sha256")),
        ("queue_request_path", payload.get("queue_request_path")),
        ("quarantine_reason", payload.get("quarantine_reason")),
        ("automatic_reconcile_attempt_id", payload.get("automatic_reconcile_attempt_id")),
    ]
    v2_evidence = payload.get("evidence")
    if payload.get("contract_version") == 2:
        snapshots = payload.get("product_snapshots") if isinstance(payload.get("product_snapshots"), list) else []
        counts = {
            "total": payload.get("total_count", len(snapshots)),
            "success": payload.get("success_count", 0),
            "failed": payload.get("failed_count", 0),
            "skipped": payload.get("skipped_count", 0),
            "manual": payload.get("manual_check_count", 0),
        }
        fields = [
            ("contract_version", payload.get("contract_version")),
            ("read_batch_id", payload.get("read_batch_id")),
            ("overall_status", payload.get("overall_status")),
            ("product_counts", json.dumps(counts, ensure_ascii=False, sort_keys=True)),
        ] + fields
        v2_evidence = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            for evidence in snapshot.get("evidence") or []:
                if isinstance(evidence, dict):
                    item = dict(evidence)
                    item.setdefault("item_id", snapshot.get("item_id", ""))
                    v2_evidence.append(item)
    rows = "".join(
        "<tr>"
        f"<th>{escape(label)}</th>"
        f"<td>{escape(str(value if value not in (None, '') else '-'))}</td>"
        "</tr>"
        for label, value in fields
    )
    return (
        f"{warning_html}"
        "<details class='json-details' open>"
        "<summary>ShadowBot</summary>"
        f"<table><tbody>{rows}</tbody></table>"
        f"{_render_shadowbot_evidence(v2_evidence)}"
        f"{_render_shadowbot_manual_actions(payload)}"
        "</details>"
    )


def _render_shadowbot_evidence(evidence) -> str:
    if not isinstance(evidence, list) or not evidence:
        return "<p class='subtle'>共享截图：-</p>"
    items = []
    for item in evidence[:6]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("type") or item.get("evidence_id") or "evidence")
        uri = str(item.get("storage_uri") or item.get("local_path") or "")
        sha256 = str(item.get("sha256") or item.get("storage_sha256") or "")
        upload_status = str(item.get("upload_status") or "")
        if uri.startswith(("http://", "https://")):
            uri_html = f"<a href='{escape(uri)}' target='_blank' rel='noreferrer'>{escape(uri)}</a>"
        else:
            uri_html = f"<code>{escape(uri or '-')}</code>"
        items.append(
            "<li>"
            f"<strong>{escape(label)}</strong> {uri_html}"
            f"<br><span class='subtle'>upload_status={escape(upload_status or '-')} sha256={escape(sha256 or '-')}</span>"
            "</li>"
        )
    if not items:
        return "<p class='subtle'>共享截图：-</p>"
    return f"<div><p class='subtle'>共享截图</p><ul>{''.join(items)}</ul></div>"


def _render_shadowbot_manual_actions(payload: dict[str, object]) -> str:
    status = str(payload.get("status") or "")
    error_code = str(payload.get("error_code") or "")
    operation_id = str(payload.get("operation_id") or "")
    actions: list[str] = []
    if status == "NEEDS_RECONCILIATION":
        actions.append("启动只读对账")
    if status == "NEEDS_RECONCILIATION" or error_code in SHADOWBOT_WARNING_STATUSES:
        actions.append("确认人工处理完成")
    actions.append("查看证据")
    forms = ""
    if operation_id and status == "NEEDS_RECONCILIATION":
        forms += f"""
        <form method="post" action="/execution-logs" class="inline-form">
          <input type="hidden" name="action" value="start_shadowbot_reconcile">
          <input type="hidden" name="operation_id" value="{escape(operation_id)}">
          <button type="submit">启动只读对账</button>
        </form>
        """
    if operation_id and (status == "NEEDS_RECONCILIATION" or error_code in SHADOWBOT_WARNING_STATUSES):
        forms += f"""
        <form method="post" action="/execution-logs" class="inline-form">
          <input type="hidden" name="action" value="confirm_shadowbot_manual_handled">
          <input type="hidden" name="operation_id" value="{escape(operation_id)}">
          <input type="hidden" name="manual_note" value="confirmed from execution log">
          <button type="submit">确认人工处理完成</button>
        </form>
        """
    return (
        "<p class='subtle'>人工操作："
        + " / ".join(escape(action) for action in actions)
        + "</p>"
        + forms
    )


def _parse_json_object(raw_value: object) -> dict[str, object]:
    if isinstance(raw_value, dict):
        return raw_value
    if not raw_value:
        return {}
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_preview_block(label: str, value: object) -> str:
    rows = _json_summary_rows(value)
    return f"""
    <div class="field">
      <label>{escape(_field_label(label))}</label>
      <div class="table-wrap"><table><tbody>{rows}</tbody></table></div>
      {_json_details_block(value)}
    </div>
    """


def _json_summary_rows(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "<tr><td colspan='2'>-</td></tr>"
    rows = []
    for index, (key, item) in enumerate(value.items()):
        if index >= 8:
            rows.append("<tr><td colspan='2'>...</td></tr>")
            break
        rows.append(
            "<tr>"
            f"<th>{escape(str(key))}</th>"
            f"<td>{escape(_json_compact_value(item, 180))}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='2'>-</td></tr>"


def _json_details_block(value: object) -> str:
    json_text = _json_pretty_text(value)
    truncated_text = _truncate_text(json_text, 4000)
    return (
        "<details class='json-details'>"
        f"<summary>{escape(UI_TEXT['ops_json_full'])}</summary>"
        f"<pre>{escape(truncated_text)}</pre>"
        "</details>"
    )


def _json_pretty_text(value: object) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        return str(value)


def _json_compact_value(value: object, max_chars: int) -> str:
    if isinstance(value, dict):
        keys = ", ".join(str(key) for key in list(value.keys())[:8])
        suffix = "..." if len(value) > 8 else ""
        text = f"object({keys}{suffix})"
    elif isinstance(value, list):
        text = f"array(len={len(value)})"
    elif value is None:
        text = "-"
    else:
        text = str(value)
    return _truncate_text(text, max_chars)


def _enum_raw_value(value: object) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw)


def display_enum_label(category: str, value: object) -> str:
    raw = _enum_raw_value(value)
    if not raw:
        return "-"
    return DISPLAY_ENUM_LABELS.get(category, {}).get(raw, raw)


def display_status_label(value: object) -> str:
    raw = _enum_raw_value(value)
    for category in ("task_status", "review_status", "send_status"):
        label = DISPLAY_ENUM_LABELS.get(category, {}).get(raw)
        if label:
            return label
    return raw or "-"


def format_display_datetime(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        display_value = value
        if value.tzinfo is not None and value.utcoffset() is not None:
            display_value = value.astimezone(DISPLAY_TIMEZONE)
        return display_value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def format_object_scope(scope_type: object, scope_key: object) -> str:
    return f"{display_enum_label('scope_type', scope_type)}：{scope_key or '-'}"


def format_task_object(task) -> str:
    if getattr(task, "internal_sku", None):
        return f"单个商品：{task.internal_sku}"
    return format_object_scope(getattr(task, "scope_type", ""), getattr(task, "scope_key", ""))


def format_task_target(task) -> str:
    parts = []
    target_status = getattr(task, "target_status", None)
    if target_status:
        parts.append(str(target_status))
    target_price = getattr(task, "target_price", None)
    if target_price is not None:
        parts.append(f"目标价 {target_price}")
    return " / ".join(parts) if parts else "-"


def _task_group_id(task) -> str:
    trace = getattr(task, "decision_trace", None)
    if not isinstance(trace, dict):
        return ""
    return str(trace.get("task_group_id") or "").strip()


def _build_task_group_statuses(tasks: list) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for task in tasks:
        group_id = _task_group_id(task)
        if group_id:
            grouped.setdefault(group_id, []).append(task.task_status.value)
    statuses: dict[str, str] = {}
    terminal = {"success", "failed", "skipped", "cancelled", "expired"}
    for group_id, values in grouped.items():
        unique = set(values)
        if len(unique) == 1:
            statuses[group_id] = display_status_label(values[0])
        elif "failed" in unique:
            statuses[group_id] = "部分失败"
        elif any(value not in terminal for value in unique):
            statuses[group_id] = "处理中（部分完成）"
        else:
            statuses[group_id] = "部分完成"
    return statuses


def _task_price_calculation_summary(task) -> str:
    trace = getattr(task, "decision_trace", None)
    if not isinstance(trace, dict):
        return "-"
    steps = trace.get("rule_steps")
    if isinstance(steps, list) and steps:
        return " → ".join(str(step) for step in steps)
    return "-"


def _task_reason_summary(task) -> str:
    if getattr(task, "result_message", None):
        return _truncate_text(_sanitize_notification_text(task.result_message), 120)
    decision_trace = getattr(task, "decision_trace", None)
    if isinstance(decision_trace, dict):
        for key in ("reason", "review_reason", "message"):
            if decision_trace.get(key):
                return _truncate_text(_sanitize_notification_text(str(decision_trace[key])), 120)
        calculation = _task_price_calculation_summary(task)
        if calculation != "-":
            return _truncate_text(calculation, 120)
    return "-"


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "...（已截断）"


def _status_options(enum_type, current: str, category: str = "status") -> str:
    option_html = f"<option value=''>{escape(UI_TEXT['ops_filter_all'])}</option>"
    option_html += "".join(
        f"<option value='{escape(option.value)}'{' selected' if current == option.value else ''}>{escape(display_enum_label(category, option.value))}</option>"
        for option in enum_type
    )
    return option_html


def _session_toolbar(runtime_db: str, session_user: str | None, active_path: str) -> str:
    if session_user is None:
        return ""
    return f"""
    <section class="panel toolbar-panel">
      <div class="toolbar-row">
        <span class="subtle">{escape(UI_TEXT["runtime_session_user"])}: {escape(session_user)}</span>
        <form method="post" action="/runtime/logout">
          <input type="hidden" name="next" value="{escape(active_path)}">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_logout_button"])}</button>
        </form>
      </div>
    </section>
    """


def _status_badge(value: object, category: str = "status") -> str:
    raw = _enum_raw_value(value)
    normalized = raw.lower()
    badge_class = "status-badge"
    if normalized in {"failed", "rejected"}:
        badge_class += " status-error"
    elif normalized in {"expired", "cancelled"}:
        badge_class += " status-muted"
    elif normalized in {"pending", "manual_review", "adjusted"}:
        badge_class += " status-warn"
    elif normalized in {"success", "approved"}:
        badge_class += " status-success"
    label = display_enum_label(category, raw) if category != "status" else display_status_label(raw)
    return f"<span class='{badge_class}'>{escape(label)}</span>"


def _build_dashboard_metrics(tasks, reviews, notifications, now: datetime) -> list[dict[str, object]]:
    pending_reviews = [review for review in reviews if review.review_status == ReviewTaskStatus.PENDING]
    due_soon_reviews = [review for review in pending_reviews if _is_review_due_soon(review, now)]
    failed_notifications = [log for log in notifications if log.send_status == NotificationSendStatus.FAILED.value]
    pending_tasks = [task for task in tasks if task.task_status == TaskStatus.PENDING]
    expired_tasks = [task for task in tasks if task.task_status == TaskStatus.EXPIRED]
    expired_reviews = [review for review in reviews if review.review_status == ReviewTaskStatus.EXPIRED]
    return [
        {
            "label": UI_TEXT["ops_dashboard_pending_reviews"],
            "value": len(pending_reviews),
            "href": "/reviews",
            "params": {"review_status": ReviewTaskStatus.PENDING.value},
            "tone": "warn",
            "note": "",
        },
        {
            "label": UI_TEXT["ops_dashboard_due_soon_reviews"],
            "value": len(due_soon_reviews),
            "href": "/reviews",
            "params": {"review_status": ReviewTaskStatus.PENDING.value, "due": "soon"},
            "tone": "urgent",
            "note": "",
        },
        {
            "label": UI_TEXT["ops_dashboard_failed_notifications"],
            "value": len(failed_notifications),
            "href": "/notifications",
            "params": {"send_status": NotificationSendStatus.FAILED.value},
            "tone": "error",
            "note": "",
        },
        {
            "label": UI_TEXT["ops_dashboard_pending_tasks"],
            "value": len(pending_tasks),
            "href": "/tasks",
            "params": {"task_status": TaskStatus.PENDING.value},
            "tone": "warn",
            "note": "",
        },
        {
            "label": UI_TEXT["ops_dashboard_expired_total"],
            "value": len(expired_tasks) + len(expired_reviews),
            "href": "",
            "params": {},
            "tone": "muted",
            "note": UI_TEXT["ops_dashboard_expired_breakdown"].format(
                tasks=len(expired_tasks),
                reviews=len(expired_reviews),
            ),
            "links": [
                (
                    UI_TEXT["ops_dashboard_view_tasks"],
                    "/tasks",
                    {"task_status": TaskStatus.EXPIRED.value},
                ),
                (
                    UI_TEXT["ops_dashboard_view_reviews"],
                    "/reviews",
                    {"review_status": ReviewTaskStatus.EXPIRED.value},
                ),
            ],
        },
    ]


def _render_dashboard_metric_card(runtime_db: str, metric: dict[str, object]) -> str:
    href = str(metric.get("href", ""))
    params = dict(metric.get("params", {}))
    classes = f"metric metric-link dashboard-metric metric-{escape(str(metric.get('tone', 'neutral')))}"
    note = str(metric.get("note", ""))
    note_html = f"<span class='metric-note'>{escape(note)}</span>" if note else ""
    links = metric.get("links", [])
    if href:
        return (
            f"<a class='{classes}' href='{escape(_append_query_to_path(href, params))}'>"
            f"<span class='label'>{escape(str(metric['label']))}</span>"
            f"<strong>{escape(str(metric['value']))}</strong>"
            f"{note_html}</a>"
        )
    link_html = "".join(
        f"<a href='{escape(_append_query_to_path(path, link_params))}'>{escape(label)}</a>"
        for label, path, link_params in links
    )
    return (
        f"<div class='{classes}'>"
        f"<span class='label'>{escape(str(metric['label']))}</span>"
        f"<strong>{escape(str(metric['value']))}</strong>"
        f"{note_html}<span class='metric-links'>{link_html}</span></div>"
    )


def _is_review_due_soon(review, now: datetime) -> bool:
    if review.review_status != ReviewTaskStatus.PENDING or review.required_by is None:
        return False

    comparable_now = _datetime_in_display_timezone(now)
    comparable_deadline = _datetime_in_display_timezone(review.required_by)
    return (
        comparable_now
        <= comparable_deadline
        <= comparable_now + timedelta(hours=2)
    )


def _datetime_in_display_timezone(value: datetime) -> datetime:
    """Normalize legacy naive and timezone-aware values for safe comparison."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=DISPLAY_TIMEZONE)
    return value.astimezone(DISPLAY_TIMEZONE)


def _field_label(name: str) -> str:
    if name == "action":
        return "\u5904\u7406\u5165\u53e3"
    if name == "current_feishu_message_type":
        return UI_TEXT["ops_notification_current_feishu_type"]
    if name == "note":
        return "\u5907\u6ce8"
    return FIELD_LABELS.get(name, name)


def _sanitize_display_text(value: str) -> str:
    return _sanitize_notification_text(value)


def _sanitize_notification_text(value: str) -> str:
    text = value or ""
    text = re.sub(r"https?://[^\s\"'<>]*webhook[^\s\"'<>]*", "[webhook_redacted]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"https?://[^\s\"'<>]*open\.feishu\.cn/open-apis/bot/v2/hook[^\s\"'<>]*",
        "[webhook_redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"https?://[^\s\"'<>]*/mobile/review/[^\s\"'<>]+",
        "[mobile_review_url_redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"/mobile/review/[^\s\"'<>]+",
        "[mobile_review_url_redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"mobile_review_url\s*[:=]\s*[^\s\"'<>]+",
        "mobile_review_url=[mobile_review_url_redacted]",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"token=[^&\s\"'<>]+", "token=***", text, flags=re.IGNORECASE)


def _sanitize_task_sensitive_text(value: str) -> str:
    return _sanitize_notification_text(value).replace("token=***", "[token_redacted]")


def _current_feishu_message_type() -> str:
    configured = os.getenv("FEISHU_MESSAGE_TYPE", "post").strip().lower()
    return configured if configured in {"post", "text"} else f"{configured or 'post'} (invalid)"


def _parse_optional_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_optional_task_action_type(value: str) -> TaskActionType | None:
    text = value.strip()
    if not text:
        return None
    try:
        return TaskActionType(text)
    except ValueError:
        return None


def _render_system_feishu_test_panel(runtime_db: str) -> str:
    channel = _env_value("DEFAULT_NOTIFICATION_CHANNEL", "").lower()
    webhook_configured = bool(_env_value("FEISHU_WEBHOOK_URL"))
    disabled = channel != "feishu" or not webhook_configured
    if channel != "feishu":
        hint = UI_TEXT["ops_system_test_feishu_not_feishu"]
    elif not webhook_configured:
        hint = "FEISHU_WEBHOOK_URL 未配置，提交后无法发送测试通知。"
    else:
        hint = "将发送一条不含 token、mobile_review_url、webhook、secret 或 runtime DB 路径的系统测试消息。"
    disabled_attr = " disabled" if disabled else ""
    return f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["ops_system_test_feishu_title"])}</h2>
      <p class="subtle">{escape(UI_TEXT["ops_system_test_feishu_note"])}</p>
      <p class="subtle">{escape(hint)}</p>
      <form method="post" action="/system/test-feishu-notification" class="actions">
        <button class="primary" type="submit"{disabled_attr}>{escape(UI_TEXT["ops_system_test_feishu_button"])}</button>
      </form>
    </section>
    """


def _send_system_feishu_test_notification(db_path: Path, *, actor: str) -> tuple[bool, str]:
    channel = _env_value("DEFAULT_NOTIFICATION_CHANNEL", "mock").lower()
    if channel != "feishu":
        return False, UI_TEXT["ops_system_test_feishu_not_feishu"]

    now = datetime.now()
    message = "PRA 系统测试通知"
    log = NotificationLog(
        notification_id=uuid4().hex[:12],
        related_task_id=None,
        related_review_task_id=None,
        recipient_type="system",
        recipient="system_test",
        channel="feishu",
        sent_at=None,
        send_status=NotificationSendStatus.PENDING.value,
        dedupe_key=f"system_test:feishu:{now.strftime('%Y%m%d%H%M%S')}:{uuid4().hex[:8]}",
        message=message,
        created_at=now,
    )
    payload = _build_system_feishu_test_payload(now=now, actor=actor)
    result = NotificationSenderFactory().build("feishu").send(log, payload)
    error_message = _sanitize_notification_text(result.error_message)
    persisted_log = NotificationLog(
        notification_id=log.notification_id,
        related_task_id=None,
        related_review_task_id=None,
        recipient_type=log.recipient_type,
        recipient=log.recipient,
        channel="feishu",
        sent_at=result.sent_at,
        send_status=result.send_status,
        dedupe_key=log.dedupe_key,
        message=message,
        error_message=error_message,
        created_at=log.created_at,
    )
    try:
        NotificationLogService(SQLiteRuntimeRepository(db_path)).append(persisted_log)
    except Exception as exc:
        error_message = _sanitize_notification_text(f"{error_message}; log write failed: {type(exc).__name__}")
    if result.send_status == NotificationSendStatus.SUCCESS.value:
        return True, UI_TEXT["ops_system_test_feishu_success"]
    return False, UI_TEXT["ops_system_test_feishu_failed"].format(error=error_message or "unknown error")


def _build_system_feishu_test_payload(*, now: datetime, actor: str) -> dict[str, object]:
    triggered_at = format_display_datetime(now)
    text = "\n".join(
        [
            "PRA 系统测试通知",
            "这是由 /system 手动触发的测试消息。",
            "不关联任何复核任务，不包含手机复核链接。",
            f"触发时间：{triggered_at}",
            "当前通知模式：飞书",
        ]
    )
    return {
        "title": "PRA 系统测试通知",
        "text": text,
        "review_type": "system_test",
        "review_type_label": "系统测试",
        "trade_date": "-",
        "scope_type": "system",
        "scope_key": "manual_feishu_test",
        "required_by": "-",
        "reason": "这是由 /system 手动触发的测试消息；不关联任何复核任务，不包含手机复核链接。",
        "system_test": True,
        "triggered_at": triggered_at,
        "notification_mode": "feishu",
        "actor": actor,
    }


def _render_system_checks_table(title: str, checks: list[dict[str, str]]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(check.get('module', ''))}</td>"
        f"<td>{escape(check.get('item', ''))}</td>"
        f"<td>{_system_status_badge(check.get('status', 'not_configured'))}</td>"
        f"<td>{escape(check.get('value', ''))}</td>"
        f"<td>{escape(check.get('recommendation', ''))}</td>"
        "</tr>"
        for check in checks
    )
    if not rows:
        rows = f"<tr><td colspan='5'>{escape(UI_TEXT['ops_empty_execution_logs'])}</td></tr>"
    return f"""
    <section class="panel">
      <h2>{escape(title)}</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{escape(UI_TEXT["ops_system_module"])}</th>
              <th>{escape(UI_TEXT["ops_system_item"])}</th>
              <th>{escape(UI_TEXT["ops_system_status"])}</th>
              <th>{escape(UI_TEXT["ops_system_value"])}</th>
              <th>{escape(UI_TEXT["ops_system_recommendation"])}</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def _system_status_badge(status: str, label: str | None = None) -> str:
    normalized = status.strip().lower() or "not_configured"
    class_map = {
        "ok": "status-success",
        "info": "status-info",
        "warning": "status-warn",
        "error": "status-error",
        "not_configured": "status-muted",
    }
    display = label or UI_TEXT.get(f"ops_system_status_{normalized}", normalized)
    return f"<span class='status-badge {class_map.get(normalized, 'status-muted')}'>{escape(display)}</span>"


def _build_system_config_checks() -> list[dict[str, str]]:
    channel = _env_value("DEFAULT_NOTIFICATION_CHANNEL", "mock").lower()
    dev_mode = _env_value("DEV_MODE", "false").lower()
    message_type = _env_value("FEISHU_MESSAGE_TYPE", "post").lower()
    timeout = _env_value("FEISHU_WEBHOOK_TIMEOUT_SECONDS", "5")
    checks: list[dict[str, str]] = []

    checks.append(_system_check("后台登录", "RUNTIME_ADMIN_USER", *_check_optional_default("RUNTIME_ADMIN_USER", "admin")))
    checks.append(_system_check("后台登录", "RUNTIME_ADMIN_PASSWORD", *_check_required_secret("RUNTIME_ADMIN_PASSWORD", min_length=12)))
    checks.append(_system_check("Review Token", "REVIEW_TOKEN_SECRET", *_check_required_secret("REVIEW_TOKEN_SECRET", min_length=32)))

    if dev_mode == "true":
        dev_status = "info"
        dev_recommendation = "仅用于本地调试；公网或生产场景请使用 DEV_MODE=false。"
    elif dev_mode == "false":
        dev_status = "ok"
        dev_recommendation = "当前为非开发模式。"
    else:
        dev_status = "warning"
        dev_recommendation = "DEV_MODE 建议只使用 true 或 false。"
    checks.append(_system_check("通知渠道", "DEV_MODE", dev_status, dev_mode or "false", dev_recommendation))

    if channel == "mock":
        if dev_mode == "true":
            channel_status = "info"
            channel_recommendation = "DEV_MODE=true 时 mock 适合本地调试，不会发送真实通知。"
        else:
            channel_status = "error"
            channel_recommendation = "非开发模式禁止使用 mock；请配置真实通知渠道。"
    elif channel == "feishu":
        missing = [
            name
            for name in ("FEISHU_WEBHOOK_URL", "MOBILE_REVIEW_BASE_URL")
            if not os.getenv(name, "").strip()
        ]
        channel_status = "error" if missing else "ok"
        channel_recommendation = (
            f"缺少 {', '.join(missing)}，飞书通知或手机复核链接不可用。"
            if missing
            else "飞书通知关键配置已具备；真实连通性仍需手动验证。"
        )
    else:
        channel_status = "error"
        channel_recommendation = "DEFAULT_NOTIFICATION_CHANNEL 未配置或不受支持；通知将保持 PENDING。"
    checks.append(_system_check("通知渠道", "DEFAULT_NOTIFICATION_CHANNEL", channel_status, channel or "未配置", channel_recommendation))

    if message_type in {"post", "text"}:
        message_status = "ok"
        message_recommendation = "post 为推荐富文本；text 可作为回退。"
    else:
        message_status = "error"
        message_recommendation = "FEISHU_MESSAGE_TYPE 仅支持 post 或 text。"
    checks.append(_system_check("飞书配置", "FEISHU_MESSAGE_TYPE", message_status, message_type or "post", message_recommendation))

    checks.append(_system_check("飞书配置", "FEISHU_WEBHOOK_URL", *_check_optional_or_required_url("FEISHU_WEBHOOK_URL", required=channel == "feishu")))
    checks.append(_system_check("飞书配置", "FEISHU_WEBHOOK_SECRET", *_check_optional_secret("FEISHU_WEBHOOK_SECRET", "未开启签名时可留空；开启签名建议配置。")))
    checks.append(_system_check("飞书配置", "FEISHU_WEBHOOK_TIMEOUT_SECONDS", *_check_positive_number(timeout)))
    checks.append(_system_check("Mobile Review", "MOBILE_REVIEW_BASE_URL", *_check_optional_or_required_url("MOBILE_REVIEW_BASE_URL", required=channel == "feishu")))
    return checks


def _build_runtime_db_checks(db_path: Path) -> list[dict[str, str]]:
    versions = _safe_schema_versions(db_path)
    latest_version = max(versions) if versions else 0
    version_value = ", ".join(str(version) for version in versions) if versions else "-"
    repository = SQLiteRuntimeRepository(db_path)
    health = repository.check_schema_health()
    operational_health = repository.check_operational_health()
    return [
        _system_check(
            "运行态数据库",
            UI_TEXT["ops_runtime_db_path"],
            "ok" if str(db_path).strip() else "error",
            _runtime_db_summary(str(db_path)),
            "仅显示文件名，避免在公网页面暴露本地完整路径。",
        ),
        _system_check(
            "运行态数据库",
            UI_TEXT["ops_system_db_exists"],
            "ok" if db_path.exists() else "error",
            UI_TEXT["ops_config_present"] if db_path.exists() else UI_TEXT["ops_config_missing"],
            "DB 缺失时请使用 init-runtime-db 初始化；本页不会自动创建数据库。",
        ),
        _system_check(
            "运行态数据库",
            UI_TEXT["ops_system_db_readable"],
            "ok" if _is_runtime_db_readable(db_path) else "error",
            UI_TEXT["ops_config_present"] if _is_runtime_db_readable(db_path) else UI_TEXT["ops_config_missing"],
            "需要能够只读打开 SQLite 文件。",
        ),
        _system_check(
            "运行态数据库",
            UI_TEXT["ops_schema_versions"],
            "ok"
            if latest_version == LATEST_RUNTIME_SCHEMA_VERSION
            and versions == list(range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1))
            else "error",
            version_value,
            f"{UI_TEXT['ops_system_latest_schema']} 必须精确匹配 v{LATEST_RUNTIME_SCHEMA_VERSION}，且迁移记录连续。",
        ),
        _system_check(
            "运行态数据库",
            "schema 完整性",
            "ok" if health.ok else "error",
            f"v{LATEST_RUNTIME_SCHEMA_VERSION} 结构完整" if health.ok else f"v{LATEST_RUNTIME_SCHEMA_VERSION} 结构缺失或约束不完整",
            health.summary,
        ),
        _system_check(
            "运行态数据库",
            "SQLite operational health",
            "ok" if operational_health.ok else "error",
            operational_health.summary,
            "运行数据库必须位于本地磁盘，并使用 WAL、synchronous=NORMAL、foreign_keys=ON 和有界 busy_timeout。",
        ),
    ]


def _build_runtime_table_count_checks(db_path: Path) -> list[dict[str, str]]:
    table_labels = {
        "tasks": "tasks",
        "review_tasks": "review_tasks",
        "notification_logs": "notification_logs",
        "execution_logs": "execution_logs",
        "review_tokens": "review_tokens",
        "script_runs": "script_runs",
        "script_run_items": "script_run_items",
    }
    checks: list[dict[str, str]] = []
    for table_name, label in table_labels.items():
        count, error = _safe_table_count(db_path, table_name)
        checks.append(
            _system_check(
                UI_TEXT["ops_system_table_count"],
                label,
                "error" if error else "ok",
                str(count) if error is None else "-",
                error or "该表可读。",
            )
        )
    return checks


def _build_system_runtime_summary(db_path: Path) -> list[dict[str, str]]:
    pending_tasks, pending_tasks_error = _safe_filtered_table_count(db_path, "tasks", "task_status", TaskStatus.PENDING.value)
    expired_tasks, expired_tasks_error = _safe_filtered_table_count(db_path, "tasks", "task_status", TaskStatus.EXPIRED.value)
    pending_reviews, pending_reviews_error = _safe_filtered_table_count(
        db_path,
        "review_tasks",
        "review_status",
        ReviewTaskStatus.PENDING.value,
    )
    expired_reviews, expired_reviews_error = _safe_filtered_table_count(
        db_path,
        "review_tasks",
        "review_status",
        ReviewTaskStatus.EXPIRED.value,
    )
    failed_notifications, failed_notifications_error = _safe_filtered_table_count(
        db_path,
        "notification_logs",
        "send_status",
        NotificationSendStatus.FAILED.value,
    )
    notification_mode = _env_value("DEFAULT_NOTIFICATION_CHANNEL", "").lower()
    dev_mode = _env_value("DEV_MODE", "false").lower()
    message_type = _env_value("FEISHU_MESSAGE_TYPE", "post").lower()
    return [
        _runtime_summary_check("运行状态", UI_TEXT["ops_dashboard_pending_reviews"], pending_reviews, pending_reviews_error),
        _runtime_summary_check("运行状态", UI_TEXT["ops_dashboard_failed_notifications"], failed_notifications, failed_notifications_error),
        _runtime_summary_check(
            "运行状态",
            UI_TEXT["ops_dashboard_expired_total"],
            (expired_tasks or 0) + (expired_reviews or 0) if expired_tasks_error is None and expired_reviews_error is None else None,
            expired_tasks_error or expired_reviews_error,
            note=f"tasks={expired_tasks if expired_tasks_error is None else '-'}, reviews={expired_reviews if expired_reviews_error is None else '-'}",
        ),
        _runtime_summary_check("运行状态", UI_TEXT["ops_dashboard_pending_tasks"], pending_tasks, pending_tasks_error),
        _system_check(
            "通知模式",
            "DEFAULT_NOTIFICATION_CHANNEL",
            "ok" if notification_mode == "feishu" or (notification_mode == "mock" and dev_mode == "true") else "error",
            notification_mode or "未配置",
            "当前通知 sender 选择；未配置或生产 mock 会失败关闭。",
        ),
        _system_check("通知模式", "FEISHU_MESSAGE_TYPE", "ok" if message_type in {"post", "text"} else "error", message_type, "飞书消息展示模式；不代表历史通知持久化字段。"),
    ]


def _runtime_summary_check(
    module: str,
    item: str,
    count: int | None,
    error: str | None,
    *,
    note: str = "",
) -> dict[str, str]:
    recommendation = error or note or "来自运行态 SQLite 的只读计数。"
    return _system_check(module, item, "error" if error else "ok", str(count) if error is None else "-", recommendation)


def _system_check(module: str, item: str, status: str, value: str, recommendation: str) -> dict[str, str]:
    return {
        "module": module,
        "item": item,
        "status": status,
        "value": value,
        "recommendation": recommendation,
    }


def _env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _check_optional_default(name: str, default: str) -> tuple[str, str, str]:
    value = _env_value(name)
    if not value:
        return "warning", default, f"未配置时默认使用 {default}。"
    if _looks_placeholder(value):
        return "error", "[placeholder]", "当前值像占位符，请替换为真实配置。"
    return "ok", value, "已配置。"


def _check_required_secret(name: str, *, min_length: int) -> tuple[str, str, str]:
    value = _env_value(name)
    if not value:
        return "error", UI_TEXT["ops_config_missing"], "该配置为必填。"
    if _looks_placeholder(value):
        return "error", UI_TEXT["ops_config_present"], "当前值像占位符，请替换为真实密钥。"
    if len(value) < min_length:
        return "warning", UI_TEXT["ops_config_present"], f"建议长度不少于 {min_length} 个字符。"
    return "ok", UI_TEXT["ops_config_present"], "已配置且长度满足建议。"


def _check_optional_secret(name: str, recommendation: str) -> tuple[str, str, str]:
    value = _env_value(name)
    if not value:
        return "not_configured", UI_TEXT["ops_config_missing"], recommendation
    if _looks_placeholder(value):
        return "error", UI_TEXT["ops_config_present"], "当前值像占位符，请替换为真实配置。"
    return "ok", UI_TEXT["ops_config_present"], "已配置；页面不会显示明文。"


def _check_optional_or_required_url(name: str, *, required: bool) -> tuple[str, str, str]:
    value = _env_value(name)
    if not value:
        status = "error" if required else "not_configured"
        recommendation = "当前通知模式需要该配置。" if required else "当前模式下可留空。"
        return status, UI_TEXT["ops_config_missing"], recommendation
    if _looks_placeholder(value):
        return "error", _mask_config_value(name, value), "当前值像占位符，请替换为真实 URL。"
    return "ok", _mask_config_value(name, value), "已配置；仅展示脱敏主机名。"


def _check_positive_number(value: str) -> tuple[str, str, str]:
    try:
        numeric = float(value)
    except ValueError:
        return "error", value or "-", "必须是数字，默认建议 5 秒。"
    if numeric <= 0:
        return "error", value, "必须大于 0。"
    return "ok", value, "HTTP 超时时间配置有效。"


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    markers = (
        "replace-with",
        "your-fixed-domain",
        "replace-me",
        "your-",
        "example.com",
        "你的",
        "请换成",
        "璇锋崲",
        "浣犵殑",
    )
    return any(marker.lower() in lowered for marker in markers)


def _is_runtime_db_readable(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        connection = SQLiteConnectionFactory(db_path).connect_read()
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
        return True
    except (sqlite3.Error, SQLiteConnectionError):
        return False


def _safe_table_count(db_path: Path, table_name: str) -> tuple[int | None, str | None]:
    return _safe_count_query(db_path, f"SELECT COUNT(*) FROM {table_name}", ())


def _safe_filtered_table_count(db_path: Path, table_name: str, column_name: str, value: str) -> tuple[int | None, str | None]:
    return _safe_count_query(db_path, f"SELECT COUNT(*) FROM {table_name} WHERE {column_name} = ?", (value,))


def _safe_count_query(db_path: Path, sql: str, params: tuple[str, ...]) -> tuple[int | None, str | None]:
    if not db_path.exists():
        return None, "DB 文件不存在。"
    try:
        connection = SQLiteConnectionFactory(db_path).connect_read()
        try:
            row = connection.execute(sql, params).fetchone()
        finally:
            connection.close()
        return int(row[0]) if row else 0, None
    except (sqlite3.Error, SQLiteConnectionError) as exc:
        return None, f"查询失败：{type(exc).__name__}"


def _present_or_missing(value: str) -> str:
    return UI_TEXT["ops_config_present"] if value.strip() else UI_TEXT["ops_config_missing"]


def _mask_config_value(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return UI_TEXT["ops_config_missing"]
    if name in {"MOBILE_REVIEW_BASE_URL", "FEISHU_WEBHOOK_URL"}:
        parsed = urlparse(cleaned)
        host = parsed.netloc or parsed.path
        if host:
            return f"{UI_TEXT['ops_config_present']} ({host})"
    return UI_TEXT["ops_config_present"]


def _runtime_db_summary(runtime_db: str) -> str:
    path = Path(runtime_db)
    if runtime_db.strip():
        return f"{UI_TEXT['ops_config_present']} ({path.name or 'runtime database'})"
    return UI_TEXT["ops_config_missing"]


def _safe_schema_versions(db_path: Path) -> list[int]:
    if not db_path.exists():
        return []
    try:
        repository = SQLiteRuntimeRepository(db_path)
        return repository.schema_versions()
    except Exception:
        return []


def _compat_notice(message: str) -> str:
    return f"""
    <section class="panel notice-panel">
      <p class="subtle">{escape(message)}</p>
    </section>
    """


def navigation(active_path: str, runtime_db: str | None = None) -> str:
    normalized_active = {
        "/": "/task-generator",
        "/tables": "/business-inputs",
        "/execution": "/execution-logs",
        "/manual-intervention": "/reviews",
    }.get(active_path, active_path)
    items = [
        ("/dashboard", UI_TEXT["dashboard_tab"]),
        ("/tasks", UI_TEXT["tasks_tab"]),
        ("/reviews", UI_TEXT["reviews_tab"]),
        ("/notifications", UI_TEXT["notifications_tab"]),
        ("/execution-logs", UI_TEXT["execution_logs_tab"]),
        ("/business-inputs", UI_TEXT["business_inputs_tab"]),
        ("/task-generator", UI_TEXT["task_generator_tab"]),
    ]
    links = "".join(
        f"<a class='{'nav-link active' if normalized_active == path else 'nav-link'}' href='{escape(path)}'>{escape(label)}</a>"
        for path, label in items
    )
    return f"<nav class='nav-strip'>{links}</nav>"


def _hero(title: str, description: str) -> str:
    return (
        "<section class='hero'>"
        f"<h1>{escape(title)}</h1>"
        f"<p class='lede'>{escape(description)}</p>"
        "</section>"
    )


def _banner(message: str, level: str) -> str:
    if not message:
        return ""
    return f"<div class='banner {escape(level)}'>{escape(message)}</div>"


def _parse_query(environ) -> dict[str, list[str]]:
    return parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)


def _safe_internal_path(path: str) -> str:
    cleaned = path.strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme or parsed.netloc:
        return ""
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return ""
    query = urlencode(
        {
            key: values[-1]
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            if values
        }
    )
    if query:
        return f"{parsed.path}?{query}"
    return parsed.path


def _append_query_to_path(path: str, params: dict[str, str | None]) -> str:
    parsed = urlparse(path)
    existing = {
        key: values[-1]
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if values
    }
    for key, value in params.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            existing[key] = text
    query = urlencode(existing)
    if query:
        return f"{parsed.path}?{query}"
    return parsed.path


def _parse_body(environ) -> dict[str, list[str]]:
    size = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(size).decode("utf-8")
    return parse_qs(body)


def _runtime_admin_user() -> str:
    return os.getenv("RUNTIME_ADMIN_USER", "admin")


def _dev_mode() -> bool:
    return os.getenv("DEV_MODE", "").strip().lower() == "true"


def _session_cookie_headers(
    session_user: str,
    *,
    runtime_db: str | None = None,
    environ=None,
) -> list[tuple[str, str]]:
    _cleanup_runtime_sessions()
    if environ is not None:
        old_cookie = SimpleCookie()
        old_cookie.load(environ.get("HTTP_COOKIE", ""))
        old_session = old_cookie.get(RUNTIME_SESSION_COOKIE)
        if old_session is not None and old_session.value in _RUNTIME_SESSIONS:
            old_payload = _RUNTIME_SESSIONS.pop(old_session.value)
            record_security_event(
                "SESSION_ROTATED",
                route="/runtime/login",
                outcome="accepted",
                reason_code="LOGIN_SESSION_ROTATED",
                subject=str(old_payload.get("user", "")),
            )
    session_id = token_urlsafe(24)
    csrf_token = token_urlsafe(32)
    expires_at = datetime.now() + timedelta(seconds=RUNTIME_SESSION_TTL_SECONDS)
    _RUNTIME_SESSIONS[session_id] = {
        "user": session_user,
        "expires_at": expires_at,
        "runtime_db": runtime_db or str(DEFAULT_RUNTIME_DB),
        "csrf_token": csrf_token,
    }
    cookie = SimpleCookie()
    cookie[RUNTIME_SESSION_COOKIE] = session_id
    cookie[RUNTIME_SESSION_COOKIE]["path"] = "/"
    cookie[RUNTIME_SESSION_COOKIE]["httponly"] = True
    cookie[RUNTIME_SESSION_COOKIE]["samesite"] = "Lax"
    cookie[RUNTIME_SESSION_COOKIE]["max-age"] = str(RUNTIME_SESSION_TTL_SECONDS)
    if _secure_cookie_enabled():
        cookie[RUNTIME_SESSION_COOKIE]["secure"] = True
    csrf_cookie = SimpleCookie()
    csrf_cookie["pra_runtime_csrf"] = csrf_token
    csrf_cookie["pra_runtime_csrf"]["path"] = "/"
    csrf_cookie["pra_runtime_csrf"]["samesite"] = "Lax"
    csrf_cookie["pra_runtime_csrf"]["max-age"] = str(RUNTIME_SESSION_TTL_SECONDS)
    if _secure_cookie_enabled():
        csrf_cookie["pra_runtime_csrf"]["secure"] = True
    return [
        ("Set-Cookie", cookie.output(header="").strip()),
        ("Set-Cookie", csrf_cookie.output(header="").strip()),
    ]


def _clear_session_cookie_headers() -> list[tuple[str, str]]:
    cookie = SimpleCookie()
    cookie[RUNTIME_SESSION_COOKIE] = ""
    cookie[RUNTIME_SESSION_COOKIE]["path"] = "/"
    cookie[RUNTIME_SESSION_COOKIE]["httponly"] = True
    cookie[RUNTIME_SESSION_COOKIE]["samesite"] = "Lax"
    cookie[RUNTIME_SESSION_COOKIE]["max-age"] = "0"
    cookie[RUNTIME_SESSION_COOKIE]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if _secure_cookie_enabled():
        cookie[RUNTIME_SESSION_COOKIE]["secure"] = True
    csrf_cookie = SimpleCookie()
    csrf_cookie["pra_runtime_csrf"] = ""
    csrf_cookie["pra_runtime_csrf"]["path"] = "/"
    csrf_cookie["pra_runtime_csrf"]["samesite"] = "Lax"
    csrf_cookie["pra_runtime_csrf"]["max-age"] = "0"
    csrf_cookie["pra_runtime_csrf"]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if _secure_cookie_enabled():
        csrf_cookie["pra_runtime_csrf"]["secure"] = True
    login_csrf_cookie = SimpleCookie()
    login_csrf_cookie[LOGIN_CSRF_COOKIE] = ""
    login_csrf_cookie[LOGIN_CSRF_COOKIE]["path"] = "/runtime/login"
    login_csrf_cookie[LOGIN_CSRF_COOKIE]["httponly"] = True
    login_csrf_cookie[LOGIN_CSRF_COOKIE]["samesite"] = "Lax"
    login_csrf_cookie[LOGIN_CSRF_COOKIE]["max-age"] = "0"
    login_csrf_cookie[LOGIN_CSRF_COOKIE]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if _secure_cookie_enabled():
        login_csrf_cookie[LOGIN_CSRF_COOKIE]["secure"] = True
    return [
        ("Set-Cookie", cookie.output(header="").strip()),
        ("Set-Cookie", csrf_cookie.output(header="").strip()),
        ("Set-Cookie", login_csrf_cookie.output(header="").strip()),
    ]


def _secure_cookie_enabled() -> bool:
    pra_env = os.getenv("PRA_ENV", "production").strip().lower() or "production"
    if pra_env == "development":
        explicit = os.getenv("PRA_COOKIE_SECURE", "").strip().lower()
        if explicit in {"1", "true", "yes", "on"}:
            return True
        if explicit in {"0", "false", "no", "off"}:
            return False
        # Development HTTP must be an explicit deployment choice; without
        # one keep the production-safe Secure behavior.
        return True
    return True


def _get_runtime_session_user(environ) -> str | None:
    session = _get_runtime_session(environ)
    if session is None:
        return None
    user = session.get("user")
    return str(user) if user else None


def _inventory_db_is_authoritative() -> bool:
    """Fence workbook stock writes against the fixed canonical Runtime DB."""

    runtime_db = _trusted_default_runtime_db()
    try:
        return (
            InventoryRepository(
                SQLiteRuntimeRepository(runtime_db)
            ).get_authority_state().authority_mode
            == "DB_AUTHORITY"
        )
    except sqlite3.OperationalError as exc:
        if "no such table: inventory_authority_state" in str(exc).lower():
            return False
        raise ProductInventoryInputError(
            "无法确认库存权威状态，已拒绝修改工作簿库存。"
        ) from exc
    except (sqlite3.Error, SQLiteConnectionError, RuntimeError) as exc:
        raise ProductInventoryInputError(
            "无法确认库存权威状态，已拒绝修改工作簿库存。"
        ) from exc


def _runtime_db_for_request(environ) -> str:
    session = _get_runtime_session(environ)
    if session is not None and session.get("runtime_db"):
        return str(_resolve_web_path(str(session["runtime_db"]), purpose="runtime_db", allow_create=False))
    query = _parse_query(environ)
    requested = _first(query, "runtime_db", "")
    if requested.strip():
        return str(_resolve_request_or_trusted_default(
            requested,
            Path(DEFAULT_RUNTIME_DB),
            purpose="runtime_db",
            allow_create=False,
        ))
    return str(_resolve_web_path(
        _trusted_default_runtime_db(),
        purpose="runtime_db",
        allow_create=False,
    ))


def _trusted_default_runtime_db() -> Path:
    configured_value = os.getenv("PRA_RUNTIME_DB", "").strip()
    configured = Path(configured_value) if configured_value else Path(DEFAULT_RUNTIME_DB)
    if configured.is_absolute():
        return configured.resolve(strict=False)
    return (ROOT / configured).resolve(strict=False)


def _trusted_project_path(configured_path: Path) -> Path:
    path = Path(configured_path)
    return path.resolve(strict=False) if path.is_absolute() else (ROOT / path).resolve(strict=False)


def _request_or_default_path(
    params: dict[str, list[str]],
    key: str,
    default_path: Path,
    *,
    purpose: str,
    allow_create: bool,
) -> str:
    requested = _first(params, key, "")
    return str(_resolve_request_or_trusted_default(
        requested,
        default_path,
        purpose=purpose,
        allow_create=allow_create,
    ))


def _resolve_request_or_trusted_default(
    requested: str | os.PathLike[str],
    default_path: Path,
    *,
    purpose: str,
    allow_create: bool,
) -> Path:
    text = os.fspath(requested) if isinstance(requested, os.PathLike) else str(requested)
    if not text.strip():
        return _resolve_web_path(
            _trusted_project_path(default_path),
            purpose=purpose,
            allow_create=allow_create,
        )
    # A request-supplied spelling is never trusted merely because it names an
    # application default.  Only an omitted value may use that default.
    return _resolve_web_path(text, purpose=purpose, allow_create=allow_create)


def _redact_rejected_path_values(params: dict[str, object], *keys: str) -> None:
    for key in keys:
        if key in params:
            params[key] = "[路径已拒绝]"


def _get_runtime_session(environ) -> dict[str, object] | None:
    _cleanup_runtime_sessions()
    cookie_header = environ.get("HTTP_COOKIE", "")
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    session_cookie = cookie.get(RUNTIME_SESSION_COOKIE)
    if session_cookie is None:
        return None
    session = _RUNTIME_SESSIONS.get(session_cookie.value)
    if session is None:
        return None
    expires_at = session.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.now():
        _RUNTIME_SESSIONS.pop(session_cookie.value, None)
        return None
    return session


def _cleanup_runtime_sessions() -> None:
    now = datetime.now()
    expired = [
        session_id
        for session_id, payload in _RUNTIME_SESSIONS.items()
        if not isinstance(payload.get("expires_at"), datetime) or payload.get("expires_at") <= now
    ]
    for session_id in expired:
        _RUNTIME_SESSIONS.pop(session_id, None)


def _parse_resolution_payload(payload_text: str) -> dict[str, object]:
    raw = payload_text.strip() or "{}"
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"resolution_payload_json 不是合法 JSON: {exc.msg}") from exc
    if not isinstance(loaded, dict):
        raise ValidationError("resolution_payload_json 必须是 JSON 对象。")
    return loaded


def _parse_mobile_resolution_payload(payload_text: str) -> dict[str, object]:
    raw = payload_text.strip()
    if not raw:
        return {}
    if len(raw.encode("utf-8")) > MOBILE_RESOLUTION_PAYLOAD_MAX_BYTES:
        raise ValidationError(f"resolution_payload_json 不能超过 {MOBILE_RESOLUTION_PAYLOAD_MAX_BYTES} bytes")
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"resolution_payload_json 不是合法 JSON: {exc.msg}") from exc
    if not isinstance(loaded, dict):
        raise ValidationError("resolution_payload_json 必须是 JSON object")
    return loaded


def _mobile_payload_summary_rows(payload: object) -> str:
    safe_keys = [
        "task_group_id",
        "task_count",
        "affected_task_count",
        "affected_task_ids",
        "task_id",
        "action_type",
        "expected_old_price",
        "target_price",
        "target_status",
        "pricing_source",
        "required_by",
    ]
    if not isinstance(payload, dict):
        return "<tr><td colspan='2'>-</td></tr>"
    rows = []
    for key in safe_keys:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False)
        else:
            value_text = str(value)
        rows.append(f"<tr><td>{escape(key)}</td><td>{escape(value_text)}</td></tr>")
    return "".join(rows) or "<tr><td colspan='2'>-</td></tr>"


def _build_runtime_query(
    runtime_db: str,
    *,
    review_task_id: str = "",
    task_id: str = "",
    notification_id: str = "",
    task_trade_date: str = "",
    task_status: str = "",
    review_trade_date: str = "",
    review_status: str = "",
    notification_related_review_task_id: str = "",
    notification_send_status: str = "",
    message: str = "",
    level: str = "",
) -> str:
    params = {"runtime_db": runtime_db}
    if review_task_id:
        params["review_task_id"] = review_task_id
    if task_id:
        params["task_id"] = task_id
    if notification_id:
        params["notification_id"] = notification_id
    if task_trade_date:
        params["task_trade_date"] = task_trade_date
    if task_status:
        params["task_status"] = task_status
    if review_trade_date:
        params["review_trade_date"] = review_trade_date
    if review_status:
        params["review_status"] = review_status
    if notification_related_review_task_id:
        params["notification_related_review_task_id"] = notification_related_review_task_id
    if notification_send_status:
        params["notification_send_status"] = notification_send_status
    if message:
        params["message"] = message
    if level:
        params["level"] = level
    return urlencode(params)


def _build_runtime_url(
    runtime_db: str,
    *,
    review_task_id: str = "",
    task_id: str = "",
    notification_id: str = "",
    task_trade_date: str = "",
    task_status: str = "",
    review_trade_date: str = "",
    review_status: str = "",
    notification_related_review_task_id: str = "",
    notification_send_status: str = "",
    message: str = "",
    level: str = "",
) -> str:
    return (
        "/runtime?"
        + _build_runtime_query(
            runtime_db,
            review_task_id=review_task_id,
            task_id=task_id,
            notification_id=notification_id,
            task_trade_date=task_trade_date,
            task_status=task_status,
            review_trade_date=review_trade_date,
            review_status=review_status,
            notification_related_review_task_id=notification_related_review_task_id,
            notification_send_status=notification_send_status,
            message=message,
            level=level,
        )
    )


def _redirect_response(location: str, headers: list[tuple[str, str]] | None = None) -> tuple[str, str, list[tuple[str, str]]]:
    response_headers = [("Location", location)]
    if headers:
        response_headers.extend(headers)
    return ("303 See Other", "", response_headers)


def _mobile_review_error_response(error: MobileReviewTransactionError) -> tuple[str, str, list[tuple[str, str]]]:
    status = MOBILE_REVIEW_HTTP_STATUS.get(error.code, "409 Conflict")
    record_security_event(
        "MOBILE_REVIEW_TOKEN_REJECTED",
        route="/mobile/review",
        outcome="rejected",
        reason_code=str(error.code),
    )
    return status, render_mobile_review_error_page(UI_TEXT["mobile_review_invalid"]), []


def _to_pretty_json(value: object) -> str:
    if not value:
        return "{}"
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _metadata_summary_rows(metadata: object) -> str:
    if not isinstance(metadata, dict) or not metadata:
        return "-"
    parts: list[str] = []
    for key in sorted(metadata.keys()):
        value = metadata[key]
        if isinstance(value, dict):
            value_text = ", ".join(
                f"{sub_key}={_json_compact_value(value[sub_key], 80)}" for sub_key in sorted(value.keys())
            )
        else:
            value_text = _json_compact_value(value, 120)
        parts.append(f"{key}: {value_text}")
    return "<br>".join(escape(part) for part in parts)


def _runtime_filter_state(params: dict[str, str]) -> dict[str, str]:
    return {
        "task_trade_date": params.get("task_trade_date", ""),
        "task_status": params.get("task_status", ""),
        "review_trade_date": params.get("review_trade_date", ""),
        "review_status": params.get("review_status", ""),
        "notification_related_review_task_id": params.get("notification_related_review_task_id", ""),
        "notification_send_status": params.get("notification_send_status", ""),
    }


def _extract_table_rows(parsed: dict[str, list[str]], headers: list[str]) -> list[dict[str, object]]:
    row_indexes: set[int] = set()
    for key in parsed:
        if not key.startswith("cell__"):
            continue
        parts = key.split("__", 2)
        if len(parts) != 3:
            continue
        try:
            row_indexes.add(int(parts[1]))
        except ValueError:
            continue

    rows: list[dict[str, object]] = []
    for row_index in sorted(row_indexes):
        row = {header: _first(parsed, f"cell__{row_index}__{header}", "") for header in headers}
        if any(str(value).strip() for value in row.values()):
            rows.append(row)
    return rows


def _respond(start_response, status: str, content_type: str, body: str, *, headers: list[tuple[str, str]] | None = None):
    payload = body.encode("utf-8")
    response_headers = [("Content-Type", content_type), ("Content-Length", str(len(payload)))]
    response_headers.extend(_RESPONSE_EXTRA_HEADERS.get())
    if headers:
        response_headers.extend(headers)
    start_response(status, response_headers)
    return [payload]


def _first(values: dict[str, list[str]], key: str, default: str) -> str:
    return values.get(key, [default])[0]
