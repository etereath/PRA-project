"""PRA 运营 Web 的 WSGI 应用骨架。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from http.cookies import SimpleCookie
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from socketserver import ThreadingMixIn
from threading import Lock
from urllib.parse import parse_qs, quote, unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from app.operations_web.auth import Capability, SessionCapacityError
from app.operations_web.composition import (
    PROJECT_ROOT,
    OperationsWebContainer,
    build_container,
)
from app.operations_web.presenters import (
    render_database,
    render_detail,
    render_management,
    render_mobile_review,
    render_notification_drawer,
    render_system,
    render_today,
)
from app.operations_web.queries import OperationsQueryService
from app.operations_web.rendering import html, render_template, static_text
from app.operations_web.security import Response, internal_error_response
from app.services.authoritative_inventory import (
    InventoryApplicationService,
    InventoryAuthorityError,
    InventoryConflictError,
    InventoryInsufficientError,
)
from app.services.inventory_alert import InventoryAlertService
from app.services.execution_authorization import (
    ExecutionAuthorizationApplicationService,
    ExecutionAuthorizationError,
)
from app.repositories.automation_repository import AutomationRepository
from app.repositories.inventory_repository import InventoryRepository
from app.services.automation_configuration import (
    AutomationConfigurationApplicationService,
    AutomationConfigurationError,
)
from app.services.manual_task_orchestration import (
    ManualTaskApplicationService,
    ManualTaskError,
    ManualTaskRequest,
)
from app.services.review_resolution import (
    ReviewResolutionApplicationService,
    ReviewResolutionError,
)
from app.services.operations_maintenance import (
    MaintenanceReceipt,
    OperationsMaintenanceApplicationService,
    OperationsMaintenanceError,
)
from app.services.security import LOGIN_RATE_LIMITER, record_security_event
from app.exceptions import MobileReviewTransactionError
from app.mobile_review_http import mobile_review_http_status
from app.services.workflow import resolve_mobile_review


LOGGER = logging.getLogger("app.operations_web")
MAX_REQUEST_BODY_BYTES = 64 * 1024
DEPENDENCY_OVERRIDE_FIELDS = frozenset(
    {
        "runtime_db",
        "products_path",
        "products_workbook",
        "price_rules_path",
        "price_rules_workbook",
        "listing_rules_path",
        "listing_rules_workbook",
        "queue_dir",
        "queue_root",
        "platform_mappings",
        "platform_mappings_path",
        "platform_mappings_workbook",
        "shadowbot_identity_mapping",
        "identity_mapping_path",
        "backup_dir",
        "backup_root",
        "automation_heartbeat",
    }
)

PROTECTED_ROUTES: dict[str, tuple[str, Capability]] = {
    "/today": ("今日", Capability.VIEW_TODAY),
    "/database": ("数据库", Capability.VIEW_DATABASE),
    "/management": ("业务管理", Capability.MANAGE_BUSINESS),
    "/system": ("系统", Capability.VIEW_SYSTEM),
    "/system/notifications": ("通知通路", Capability.SYSTEM_ADMIN),
    "/system/data": ("数据与备份", Capability.SYSTEM_ADMIN),
    "/system/diagnostics": ("高级诊断", Capability.SYSTEM_ADMIN),
}

EPHEMERAL_CONTROL_TTL = timedelta(minutes=15)
MAX_EPHEMERAL_CONTROL_ITEMS = 1024


@dataclass(frozen=True, slots=True)
class _EphemeralControlItem:
    subject: str
    value: object
    expires_at: datetime


class _EphemeralControlStore:
    """Bounded PRG handoff only; it is never a business authority."""

    def __init__(self) -> None:
        self._items: dict[str, _EphemeralControlItem] = {}
        self._lock = Lock()

    def put(self, subject: str, value: object) -> str:
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._purge(now)
            if len(self._items) >= MAX_EPHEMERAL_CONTROL_ITEMS:
                oldest = min(self._items, key=lambda key: self._items[key].expires_at)
                self._items.pop(oldest, None)
            self._items[token] = _EphemeralControlItem(
                subject=subject,
                value=value,
                expires_at=now + EPHEMERAL_CONTROL_TTL,
            )
        return token

    def get(self, token: str, subject: str) -> object | None:
        now = datetime.now(UTC)
        with self._lock:
            self._purge(now)
            item = self._items.get(str(token or ""))
            if item is None or item.subject != subject:
                return None
            return item.value

    def _purge(self, now: datetime) -> None:
        for token in [
            key for key, item in self._items.items() if item.expires_at <= now
        ]:
            self._items.pop(token, None)

DATABASE_SECTION_ROUTES = {
    "/database": "business",
    "/database/project": "project",
    "/database/sales-analysis": "sales-analysis",
    "/database/dictionary": "dictionary",
    "/database/quality": "quality",
}
DETAIL_ROUTE_PATTERN = re.compile(
    r"^/(database|management)/(product|sales|settlement|task|review|run|execution)/([^/]+)$"
)
DETAIL_ROUTE_OWNERS = {
    "product": "database",
    "sales": "database",
    "settlement": "database",
    "run": "database",
    "execution": "database",
    "task": "management",
    "review": "management",
}


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class RedactingRequestHandler(WSGIRequestHandler):
    """请求日志不记录 query，避免 Mobile Review token 泄露。"""

    def log_message(self, format: str, *args: object) -> None:
        sanitized = tuple(
            re.sub(r"(\s/[^\s?]*)\?[^\s]*(\sHTTP/)", r"\1?[query-redacted]\2", str(value))
            for value in args
        )
        super().log_message(format, *sanitized)


class OperationsWebApplication:
    def __init__(self, container: OperationsWebContainer) -> None:
        self.container = container
        self.queries = OperationsQueryService(
            container.runtime_repository,
            container.settings.paths,
        )
        inventory_alerts = InventoryAlertService(container.runtime_repository)
        self.inventory_application = InventoryApplicationService(
            container.runtime_repository,
            alert_evaluator=inventory_alerts.evaluate_transaction,
        )
        platform_mappings = (
            container.settings.paths.platform_mappings_workbook
            or (PROJECT_ROOT / "data/samples/platform_mappings.xlsx")
        )
        identity_mapping = (
            container.settings.paths.shadowbot_identity_mapping
            or (PROJECT_ROOT / "shadowbot/test2/product_identity_mapping.json")
        )
        self.manual_tasks = ManualTaskApplicationService(
            container.runtime_repository,
            products_workbook=container.settings.paths.products_workbook,
            platform_mappings_workbook=platform_mappings,
        )
        self.execution_authorization = ExecutionAuthorizationApplicationService(
            container.runtime_repository,
            authorization=container.authorization,
            products_workbook=container.settings.paths.products_workbook,
            platform_mappings_workbook=platform_mappings,
            shadowbot_identity_mapping=identity_mapping,
            queue_root=container.settings.paths.queue_root,
            applet_uri=container.settings.shadowbot_applet_uri,
            execution_profile=container.settings.environment,
        )
        self.review_resolution = ReviewResolutionApplicationService(
            container.runtime_repository,
            container.authorization,
            products_path=container.settings.paths.products_workbook,
        )
        self.automation_configuration = AutomationConfigurationApplicationService(
            AutomationRepository(container.runtime_repository),
            InventoryRepository(container.runtime_repository),
            container.authorization,
        )
        self.maintenance = OperationsMaintenanceApplicationService(
            container.runtime_repository,
            container.authorization,
            queue_root=container.settings.paths.queue_root,
            platform_name=container.settings.platform_name,
            notification_channel=container.settings.notification_channel,
            automation_heartbeat=container.settings.paths.automation_heartbeat,
        )
        self.control_store = _EphemeralControlStore()

    def __call__(self, environ, start_response):
        try:
            response = self.dispatch(environ)
        except Exception:
            response, reference = internal_error_response()
            LOGGER.exception("运营 Web 请求失败 error_reference=%s", reference)
        return response.wsgi(start_response)

    def dispatch(self, environ) -> Response:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))

        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                content_length = int(environ.get("CONTENT_LENGTH") or 0)
            except (TypeError, ValueError):
                return Response.text("400 Bad Request", "请求长度无效。")
            if content_length < 0 or content_length > MAX_REQUEST_BODY_BYTES:
                return Response.text("413 Content Too Large", "请求内容过大。")

        if self._contains_dependency_override(environ, method):
            return Response.text(
                "400 Bad Request",
                "该请求包含不允许修改的系统设置。",
                headers=[("Cache-Control", "no-store")],
            )

        if path == "/static/app.css":
            if method != "GET":
                return self._method_not_allowed("GET")
            return Response.text(
                "200 OK",
                static_text("app.css"),
                content_type="text/css; charset=utf-8",
                headers=[("Cache-Control", "no-cache")],
            )

        if path == "/static/app.js":
            if method != "GET":
                return self._method_not_allowed("GET")
            return Response.text(
                "200 OK",
                static_text("app.js"),
                content_type="application/javascript; charset=utf-8",
                headers=[("Cache-Control", "no-cache")],
            )

        if path == "/health":
            if method != "GET":
                return self._method_not_allowed("GET")
            return self._health()

        if path == "/":
            if method != "GET":
                return self._method_not_allowed("GET")
            return Response.text(
                "303 See Other",
                "",
                headers=[("Location", "/today"), ("Cache-Control", "no-store")],
            )

        if path == "/login":
            if method == "GET":
                return self._login_page(environ)
            if method == "POST":
                return self._login(environ)
            return self._method_not_allowed("GET, POST")

        if path == "/logout":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._logout(environ)

        if path.startswith("/mobile/review/"):
            return self._mobile_review(environ, method, path)

        if path == "/management/inventory-adjustments":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._inventory_adjustment(environ)

        if path == "/management/tasks/preview":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._manual_task_preview(environ)

        if path == "/management/tasks/create":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._manual_task_create(environ)

        if path == '/management/tasks/cancel-price':
            if method != 'POST':
                return self._method_not_allowed('POST')
            return self._cancel_price_decisions(environ)

        if path == "/management/executions/prepare":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._execution_prepare(environ)

        if path == "/management/executions/submit":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._execution_submit(environ)

        if path == "/management/reviews/resolve":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._review_resolve(environ)

        if path == "/management/automation/configure":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._automation_configure(environ)

        if path == "/management/automation/inventory-alert":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._inventory_alert_configure(environ)

        if path == "/management/automation/rerun":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._automation_rerun(environ)

        if path == "/system/worker-recovery":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._maintenance_request(environ, "WORKER_RECOVERY")

        if path == "/system/notifications/test":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._maintenance_request(environ, "NOTIFICATION_TEST")

        if path == "/system/backups":
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._maintenance_request(environ, "RELEASE_BACKUP")

        if self._is_protected_route(path):
            if method != "GET":
                return self._method_not_allowed("GET")
            return self._protected_page(environ, path)

        return Response.text("404 Not Found", "未找到该页面。")

    def _health(self) -> Response:
        try:
            schema = self.container.runtime_repository.check_schema_health()
            operational = self.container.runtime_repository.check_operational_health()
            if schema.ok and operational.ok:
                return Response.text("200 OK", "ok", headers=[("Cache-Control", "no-store")])
            detail = f"{schema.summary}; {operational.summary}"
        except Exception as exc:
            detail = type(exc).__name__
        return Response.text(
            "503 Service Unavailable",
            "unhealthy: " + detail,
            headers=[("Cache-Control", "no-store")],
        )

    def _login_page(self, environ, *, message: str = "", status: str = "200 OK") -> Response:
        previous = self.container.sessions.get(str(environ.get("HTTP_COOKIE", "")))
        if previous is not None and previous.principal is not None:
            return Response.text(
                "303 See Other",
                "",
                headers=[("Location", "/today"), ("Cache-Control", "no-store")],
            )
        try:
            state, cookie = self.container.sessions.issue_preauth(
                replace_session_id=previous.session_id if previous else None
            )
        except SessionCapacityError:
            return Response.text(
                "503 Service Unavailable",
                "登录会话容量已满，请稍后重试。",
                headers=[("Cache-Control", "no-store"), ("Retry-After", "30")],
            )
        body = render_template(
            "login.html",
            message=html(message),
            csrf_token=html(state.csrf_token),
            username=html(self.container.settings.admin_username),
        )
        return Response.text(
            status,
            body,
            content_type="text/html; charset=utf-8",
            headers=[("Set-Cookie", cookie), ("Cache-Control", "no-store")],
        )

    def _login(self, environ) -> Response:
        body = self._parse_form(environ)
        session = self.container.sessions.get(str(environ.get("HTTP_COOKIE", "")))
        if not self.container.sessions.csrf_matches(session, self._first(body, "csrf_token")):
            record_security_event(
                "CSRF_REJECTED",
                route="/login",
                outcome="rejected",
                reason_code="LOGIN_CSRF_INVALID",
            )
            return Response.text("403 Forbidden", "登录请求校验失败，请刷新页面后重试。")

        username = self._first(body, "username").strip()
        password = self._first(body, "password")
        remote_addr = str(environ.get("REMOTE_ADDR", ""))
        if LOGIN_RATE_LIMITER.is_blocked(username, remote_addr):
            record_security_event(
                "LOGIN_RATE_LIMITED",
                route="/login",
                outcome="rejected",
                reason_code="RATE_LIMITED",
                subject=username,
            )
            return Response.text("429 Too Many Requests", "登录尝试过于频繁，请稍后再试。")

        principal = self.container.credentials.authenticate(username, password)
        if principal is None:
            blocked = LOGIN_RATE_LIMITER.record_failure(username, remote_addr)
            record_security_event(
                "LOGIN_FAILED",
                route="/login",
                outcome="rejected",
                reason_code=(
                    "PASSWORD_NOT_CONFIGURED"
                    if not self.container.credentials.configured
                    else "INVALID_CREDENTIALS"
                ),
                subject=username,
            )
            reason = (
                "尚未配置后台登录密码。"
                if not self.container.credentials.configured
                else "账号或密码不正确。"
            )
            status = "401 Unauthorized"
            if blocked:
                record_security_event(
                    "LOGIN_RATE_LIMITED",
                    route="/login",
                    outcome="rejected",
                    reason_code="RATE_LIMITED",
                    subject=username,
                )
                reason = "登录尝试过于频繁，请稍后再试。"
                status = "429 Too Many Requests"
            return self._login_page(environ, message=reason, status=status)

        try:
            _, cookie = self.container.sessions.rotate_authenticated(
                principal=principal,
                replace_session_id=session.session_id if session else None,
            )
        except SessionCapacityError:
            return Response.text(
                "503 Service Unavailable",
                "登录会话容量已满，请稍后重试。",
                headers=[("Cache-Control", "no-store"), ("Retry-After", "30")],
            )
        LOGIN_RATE_LIMITER.record_success(username, remote_addr)
        record_security_event(
            "LOGIN_SUCCESS",
            route="/login",
            outcome="accepted",
            reason_code="PASSWORD_MATCH",
            subject=principal.subject,
        )
        return Response.text(
            "303 See Other",
            "",
            headers=[
                ("Location", "/today"),
                ("Set-Cookie", cookie),
                ("Cache-Control", "no-store"),
            ],
        )

    def _logout(self, environ) -> Response:
        body = self._parse_form(environ)
        cookie_header = str(environ.get("HTTP_COOKIE", ""))
        session = self.container.sessions.get(cookie_header)
        if session is None or session.principal is None:
            return Response.text("401 Unauthorized", "登录已失效，请重新登录。")
        if not self.container.sessions.csrf_matches(session, self._first(body, "csrf_token")):
            record_security_event(
                "CSRF_REJECTED",
                route="/logout",
                outcome="rejected",
                reason_code="SESSION_CSRF_INVALID",
                subject=session.principal.subject,
            )
            return Response.text("403 Forbidden", "退出请求校验失败。")
        expired_cookie = self.container.sessions.clear(cookie_header)
        return Response.text(
            "303 See Other",
            "",
            headers=[
                ("Location", "/login"),
                ("Set-Cookie", expired_cookie),
                ("Cache-Control", "no-store"),
            ],
        )

    def _protected_page(self, environ, path: str) -> Response:
        session = self.container.sessions.get(str(environ.get("HTTP_COOKIE", "")))
        if session is None or session.principal is None:
            return Response.text(
                "303 See Other",
                "",
                headers=[("Location", "/login"), ("Cache-Control", "no-store")],
            )
        title, capability = self._protected_route_contract(path)
        if not self.container.authorization.allows(session.principal, capability):
            record_security_event(
                "AUTHORIZATION_REJECTED",
                route=path,
                outcome="rejected",
                reason_code="CAPABILITY_DENIED",
                subject=session.principal.subject,
            )
            return Response.text("403 Forbidden", "当前账号没有访问该页面的权限。")

        query = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True)
        content: str
        if path == "/today":
            content = render_today(self.queries.today())
        elif path in DATABASE_SECTION_ROUTES:
            content = render_database(
                self.queries.database(
                    section=DATABASE_SECTION_ROUTES[path],
                    dataset=self._first(query, "dataset"),
                    page=self._page_number(self._first(query, "page")),
                    trade_date=self._optional_date(self._first(query, "trade_date")),
                    platform_name=self._first(query, "platform").strip(),
                )
            )
        elif path == "/management":
            subject = session.principal.subject
            task_preview_token = self._first(query, "task_preview")
            task_preview_value = self.control_store.get(
                task_preview_token,
                subject,
            )
            task_preview = (
                task_preview_value
                if hasattr(task_preview_value, "preview_digest")
                else None
            )
            task_receipt_value = self.control_store.get(
                self._first(query, "task_receipt"),
                subject,
            )
            execution_preparation_value = self.control_store.get(
                self._first(query, "execution_preview"),
                subject,
            )
            execution_receipt_value = self.control_store.get(
                self._first(query, "execution_receipt"),
                subject,
            )
            review_receipt_value = self.control_store.get(
                self._first(query, "review_receipt"),
                subject,
            )
            automation_receipt_value = self.control_store.get(
                self._first(query, "automation_receipt"),
                subject,
            )
            try:
                task_scope_options = self.manual_tasks.scope_options()
            except Exception:
                task_scope_options = None
            content = render_management(
                self.queries.management(
                    inventory_transaction_id=self._first(
                        query,
                        "inventory_transaction",
                    ),
                    inventory_error_code=self._first(
                        query,
                        "inventory_error",
                    ),
                ),
                csrf_token=session.csrf_token,
                task_scope_options=task_scope_options,
                task_preview=task_preview,
                task_preview_token=task_preview_token if task_preview is not None else "",
                task_receipt=(
                    task_receipt_value
                    if isinstance(task_receipt_value, tuple)
                    else ()
                ),
                task_error=self._control_message(query, "task_error", subject),
                execution_preparation=(
                    execution_preparation_value
                    if hasattr(execution_preparation_value, "confirmation_digest")
                    else None
                ),
                execution_receipt=(
                    execution_receipt_value
                    if isinstance(execution_receipt_value, tuple)
                    and len(execution_receipt_value) == 2
                    else None
                ),
                execution_error=self._control_message(
                    query,
                    "execution_error",
                    subject,
                ),
                review_receipt=(
                    review_receipt_value
                    if isinstance(review_receipt_value, tuple)
                    and len(review_receipt_value) == 3
                    else None
                ),
                review_error=self._control_message(
                    query,
                    "review_error",
                    subject,
                ),
                automation_receipt=(
                    automation_receipt_value
                    if isinstance(automation_receipt_value, str)
                    else ""
                ),
                automation_error=self._control_message(
                    query,
                    "automation_error",
                    subject,
                ),
            )
        elif path.startswith("/system"):
            receipt_value = self.control_store.get(
                self._first(query, "maintenance_receipt"),
                session.principal.subject,
            )
            error_value = self.control_store.get(
                self._first(query, "maintenance_error"),
                session.principal.subject,
            )
            content = render_system(
                self.queries.system(),
                csrf_token=session.csrf_token,
                section={
                    "/system": "status",
                    "/system/notifications": "notifications",
                    "/system/data": "data",
                    "/system/diagnostics": "diagnostics",
                }.get(path, "status"),
                can_admin=self.container.authorization.allows(
                    session.principal,
                    Capability.SYSTEM_ADMIN,
                ),
                maintenance_receipt=(
                    receipt_value
                    if isinstance(receipt_value, MaintenanceReceipt)
                    else None
                ),
                maintenance_error=(
                    error_value if isinstance(error_value, str) else ""
                ),
                idempotency_keys={
                    "worker": secrets.token_urlsafe(18),
                    "notification": secrets.token_urlsafe(18),
                    "backup": secrets.token_urlsafe(18),
                },
            )
        else:
            match = DETAIL_ROUTE_PATTERN.fullmatch(path)
            if match is None or DETAIL_ROUTE_OWNERS.get(match.group(2)) != match.group(1):
                return Response.text("404 Not Found", "未找到该页面。")
            detail = self.queries.detail(
                match.group(2),
                unquote(match.group(3)),
                context={
                    key: self._first(query, key)
                    for key in ("source", "dataset", "trade_date", "platform")
                },
            )
            if detail is None:
                return Response.text("404 Not Found", "未找到该记录。")
            content = render_detail(detail)
        drawer = render_notification_drawer(self.queries.notification_drawer())
        body = render_template(
            "page.html",
            page_title=html(title),
            active_today="active" if path == "/today" else "",
            active_database="active" if path.startswith("/database") else "",
            active_management="active" if path.startswith("/management") else "",
            active_system="active" if path.startswith("/system") else "",
            content=content,
            notification_drawer=drawer,
            csrf_token=html(session.csrf_token),
            username=html(session.principal.subject),
        )
        return Response.text(
            "200 OK",
            body,
            content_type="text/html; charset=utf-8",
            headers=[("Cache-Control", "no-store")],
        )

    def _maintenance_request(self, environ, intent_type: str) -> Response:
        session = self.container.sessions.get(str(environ.get("HTTP_COOKIE", "")))
        if session is None or session.principal is None:
            return Response.text(
                "303 See Other",
                "",
                headers=[("Location", "/login"), ("Cache-Control", "no-store")],
            )
        if not self.container.authorization.allows(
            session.principal,
            Capability.SYSTEM_ADMIN,
        ):
            record_security_event(
                "AUTHORIZATION_REJECTED",
                route=str(environ.get("PATH_INFO", "")),
                outcome="rejected",
                reason_code="SYSTEM_ADMIN_REQUIRED",
                subject=session.principal.subject,
            )
            return Response.text("403 Forbidden", "当前账号没有系统维护权限。")
        form = self._parse_form(environ)
        if not self.container.sessions.csrf_matches(
            session,
            self._first(form, "csrf_token"),
        ):
            record_security_event(
                "CSRF_REJECTED",
                route=str(environ.get("PATH_INFO", "")),
                outcome="rejected",
                reason_code="SESSION_CSRF_INVALID",
                subject=session.principal.subject,
            )
            return Response.text("403 Forbidden", "系统维护请求校验失败，请刷新页面后重试。")

        redirect_path = {
            "WORKER_RECOVERY": "/system",
            "NOTIFICATION_TEST": "/system/notifications",
            "RELEASE_BACKUP": "/system/data",
        }.get(intent_type, "/system")
        try:
            idempotency_key = self._first(form, "idempotency_key")
            if intent_type == "WORKER_RECOVERY":
                receipt = self.maintenance.request_worker_recovery(
                    session.principal,
                    idempotency_key=idempotency_key,
                )
            elif intent_type == "NOTIFICATION_TEST":
                receipt = self.maintenance.request_notification_test(
                    session.principal,
                    idempotency_key=idempotency_key,
                )
            elif intent_type == "RELEASE_BACKUP":
                if self._first(form, "confirmation") != "CREATE_BACKUP":
                    raise OperationsMaintenanceError("请确认后再创建数据备份。")
                receipt = self.maintenance.request_backup(
                    session.principal,
                    idempotency_key=idempotency_key,
                )
            else:
                return Response.text("404 Not Found", "未知的系统维护操作。")
            token = self.control_store.put(session.principal.subject, receipt)
            location = f"{redirect_path}?maintenance_receipt={quote(token, safe='')}"
        except (OperationsMaintenanceError, ValueError) as exc:
            token = self.control_store.put(
                session.principal.subject,
                str(exc) or "系统维护请求未受理。",
            )
            location = f"{redirect_path}?maintenance_error={quote(token, safe='')}"
        except Exception:
            token = self.control_store.put(
                session.principal.subject,
                "系统维护请求失败，本次没有执行任何维护操作。",
            )
            location = f"{redirect_path}?maintenance_error={quote(token, safe='')}"
        return Response.text(
            "303 See Other",
            "",
            headers=[("Location", location), ("Cache-Control", "no-store")],
        )

    def _inventory_adjustment(self, environ) -> Response:
        session = self.container.sessions.get(str(environ.get("HTTP_COOKIE", "")))
        if session is None or session.principal is None:
            return Response.text(
                "303 See Other",
                "",
                headers=[("Location", "/login"), ("Cache-Control", "no-store")],
            )
        if not self.container.authorization.allows(
            session.principal,
            Capability.MANAGE_BUSINESS,
        ):
            return Response.text("403 Forbidden", "当前账号没有库存调整权限。")
        form = self._parse_form(environ)
        if not self.container.sessions.csrf_matches(
            session,
            self._first(form, "csrf_token"),
        ):
            record_security_event(
                "CSRF_REJECTED",
                route="/management/inventory-adjustments",
                outcome="rejected",
                reason_code="SESSION_CSRF_INVALID",
                subject=session.principal.subject,
            )
            return Response.text("403 Forbidden", "库存调整请求校验失败，请刷新页面后重试。")
        try:
            result = self.inventory_application.adjust(
                internal_sku=self._first(form, "internal_sku"),
                inventory_delta=int(self._first(form, "inventory_delta")),
                source_type=self._first(form, "source_type"),
                reason=self._first(form, "reason"),
                actor=session.principal.subject,
                idempotency_key=self._first(form, "idempotency_key"),
                expected_version=int(self._first(form, "expected_version")),
            )
        except (ValueError, InventoryInsufficientError):
            return self._inventory_error_redirect("INVALID_ADJUSTMENT")
        except InventoryConflictError:
            return self._inventory_error_redirect("INVENTORY_CONFLICT")
        except InventoryAuthorityError:
            return self._inventory_error_redirect("INVENTORY_UNAVAILABLE")
        except Exception:
            return self._inventory_error_redirect("INVENTORY_WRITE_FAILED")
        if result.transaction is None:
            return self._inventory_error_redirect("INVENTORY_WRITE_FAILED")
        location = "/management?inventory_transaction=" + quote(
            result.transaction.transaction_id,
            safe="",
        )
        return Response.text(
            "303 See Other",
            "",
            headers=[("Location", location), ("Cache-Control", "no-store")],
        )

    def _manual_task_preview(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/tasks/preview",
            capability=Capability.MANAGE_BUSINESS,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        try:
            request = self._manual_task_request(form)
            preview = self.manual_tasks.preview(request)
            token = self.control_store.put(session.principal.subject, preview)
            return self._management_redirect("task_preview", token)
        except (ManualTaskError, InvalidOperation, ValueError) as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "task_error",
                str(exc) or "任务预览失败。",
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "task_error",
                "任务资料暂不可用，未创建任何任务。",
            )

    def _manual_task_create(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/tasks/create",
            capability=Capability.MANAGE_BUSINESS,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        preview_token = self._first(form, "preview_token")
        preview = self.control_store.get(preview_token, session.principal.subject)
        if preview is None or not hasattr(preview, "request"):
            return self._control_error_redirect(
                session.principal.subject,
                "task_error",
                "任务预览已失效，请重新预览。",
            )
        try:
            result = self.manual_tasks.create(
                preview.request,
                expected_preview_digest=self._first(form, "preview_digest"),
                authenticated_subject=session.principal.subject,
            )
        except ManualTaskError as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "task_error",
                str(exc),
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "task_error",
                "任务创建失败，本次没有创建任何任务。",
            )
        token = self.control_store.put(
            session.principal.subject,
            result.task_ids,
        )
        return self._management_redirect("task_receipt", token)

    def _cancel_price_decisions(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ, route='/management/tasks/cancel-price', capability=Capability.MANAGE_BUSINESS,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        try:
            self.manual_tasks.cancel_price_decisions(self._many(form, 'task_ids'),
                authenticated_subject=session.principal.subject)
        except ManualTaskError as exc:
            return self._control_error_redirect(session.principal.subject, 'execution_error', str(exc))
        return Response.text('303 See Other', '', headers=[
            ('Location', '/management#tasks'), ('Cache-Control', 'no-store'),
        ])

    def _execution_prepare(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/executions/prepare",
            capability=Capability.SUBMIT_EXECUTION,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        try:
            preparation = self.execution_authorization.prepare_execution(
                session.principal,
                self._many(form, "task_ids"),
                self._first(form, "idempotency_key"),
            )
        except ExecutionAuthorizationError as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "execution_error",
                str(exc),
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "execution_error",
                "执行预览失败，本次没有发送任何平台任务。",
            )
        token = self.control_store.put(session.principal.subject, preparation)
        return self._management_redirect("execution_preview", token)

    def _execution_submit(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/executions/submit",
            capability=Capability.SUBMIT_EXECUTION,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        try:
            result = self.execution_authorization.submit_execution(
                session.principal,
                self._many(form, "task_ids"),
                self._first(form, "confirmation_digest"),
                self._first(form, "idempotency_key"),
            )
        except ExecutionAuthorizationError as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "execution_error",
                str(exc),
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "execution_error",
                "执行提交失败；请先检查系统状态，再重新预览后提交。",
            )
        token = self.control_store.put(
            session.principal.subject,
            (result.batch_id, result.execution_attempt_id),
        )
        return self._management_redirect("execution_receipt", token)

    def _review_resolve(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/reviews/resolve",
            capability=Capability.HANDLE_REVIEW,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        try:
            result = self.review_resolution.resolve(
                session.principal,
                review_task_id=self._first(form, "review_task_id"),
                action=self._first(form, "action"),
                target_price=self._first(form, "target_price"),
                note=self._first(form, "note"),
            )
        except ReviewResolutionError as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "review_error",
                str(exc),
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "review_error",
                "复核提交失败，未执行任何业务操作，也未保存部分结果。",
            )
        token = self.control_store.put(
            session.principal.subject,
            (
                result.review_task_id,
                result.review_status,
                result.created_task_id,
            ),
        )
        return self._management_redirect("review_receipt", token)

    def _automation_configure(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/automation/configure",
            capability=Capability.MANAGE_BUSINESS,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        interval_raw = self._first(form, "interval_minutes").strip()
        offset_raw = self._first(form, "offset_minutes").strip()
        try:
            job = self.automation_configuration.configure_job(
                session.principal,
                job_id=self._first(form, "job_id"),
                enabled=self._first(form, "enabled").lower() == "true",
                interval_minutes=int(interval_raw) if interval_raw else None,
                offset_minutes=int(offset_raw) if offset_raw else None,
                source_allowlist=tuple(self._many(form, "source_allowlist")),
            )
        except (AutomationConfigurationError, ValueError) as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "automation_error",
                str(exc),
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "automation_error",
                "自动化方案保存失败，旧方案保持不变。",
            )
        token = self.control_store.put(
            session.principal.subject,
            f"{job.display_name}已{'启用' if job.enabled else '停用'}。",
        )
        return self._management_redirect("automation_receipt", token)

    def _inventory_alert_configure(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/automation/inventory-alert",
            capability=Capability.MANAGE_BUSINESS,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        try:
            policy = self.automation_configuration.configure_inventory_alert(
                session.principal,
                scope_type=self._first(form, "scope_type"),
                scope_key=self._first(form, "scope_key"),
                enabled=self._first(form, "enabled").lower() == "true",
                threshold_qty=int(self._first(form, "threshold_qty")),
                repeat_interval_minutes=int(
                    self._first(form, "repeat_interval_minutes")
                ),
                expected_version=int(self._first(form, "expected_version")),
            )
        except (AutomationConfigurationError, ValueError) as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "automation_error",
                str(exc),
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "automation_error",
                "库存预警保存失败，原方案保持不变。",
            )
        scope = "全部商品" if policy.scope_type == "DEFAULT" else policy.scope_key
        token = self.control_store.put(
            session.principal.subject,
            f"{scope}库存预警已保存：阈值 {policy.threshold_qty} 扎",
        )
        return self._management_redirect("automation_receipt", token)

    def _automation_rerun(self, environ) -> Response:
        session, form, denied = self._management_write_context(
            environ,
            route="/management/automation/rerun",
            capability=Capability.MANAGE_BUSINESS,
        )
        if denied is not None:
            return denied
        assert session is not None and session.principal is not None
        try:
            run, created = self.automation_configuration.schedule_rerun(
                session.principal,
                job_id=self._first(form, "job_id"),
                target_trade_date=date.fromisoformat(
                    self._first(form, "target_trade_date")
                ),
                idempotency_key=self._first(form, "idempotency_key"),
            )
        except (AutomationConfigurationError, ValueError) as exc:
            return self._control_error_redirect(
                session.principal.subject,
                "automation_error",
                str(exc),
            )
        except Exception:
            return self._control_error_redirect(
                session.principal.subject,
                "automation_error",
                "补跑任务创建失败，本次没有开始运行。",
            )
        token = self.control_store.put(
            session.principal.subject,
            (
                "补跑任务已创建，等待后台执行"
                if created
                else "相同补跑已存在，未重复创建"
            ),
        )
        return self._management_redirect("automation_receipt", token)

    def _management_write_context(
        self,
        environ,
        *,
        route: str,
        capability: Capability,
    ):
        session = self.container.sessions.get(str(environ.get("HTTP_COOKIE", "")))
        if session is None or session.principal is None:
            return None, {}, Response.text(
                "303 See Other",
                "",
                headers=[("Location", "/login"), ("Cache-Control", "no-store")],
            )
        if not self.container.authorization.allows(session.principal, capability):
            return session, {}, Response.text(
                "403 Forbidden",
                "当前账号没有执行该操作的权限。",
            )
        form = self._parse_form(environ)
        if not self.container.sessions.csrf_matches(
            session,
            self._first(form, "csrf_token"),
        ):
            record_security_event(
                "CSRF_REJECTED",
                route=route,
                outcome="rejected",
                reason_code="SESSION_CSRF_INVALID",
                subject=session.principal.subject,
            )
            return session, form, Response.text(
                "403 Forbidden",
                "请求校验失败，请刷新页面后重试。",
            )
        return session, form, None

    def _manual_task_request(self, form: dict[str, list[str]]) -> ManualTaskRequest:
        price_raw = self._first(form, "price_value").strip()
        inventory_raw = self._first(form, "target_inventory").strip()
        return ManualTaskRequest(
            varieties=tuple(self._many(form, "varieties")),
            grades=tuple(self._many(form, "grades")),
            platforms=tuple(self._many(form, "platforms")),
            action=self._first(form, "action"),
            price_value=(Decimal(price_raw) if price_raw else None),
            target_inventory=(int(inventory_raw) if inventory_raw else None),
            excluded_item_keys=tuple(
                self._many(form, "excluded_item_keys")
            ),
            idempotency_key=self._first(form, "idempotency_key"),
        )

    def _control_error_redirect(
        self,
        subject: str,
        field: str,
        message: str,
    ) -> Response:
        token = self.control_store.put(subject, str(message)[:500])
        return self._management_redirect(field, token)

    def _control_message(
        self,
        query: dict[str, list[str]],
        field: str,
        subject: str,
    ) -> str:
        value = self.control_store.get(self._first(query, field), subject)
        return value if isinstance(value, str) else ""

    @staticmethod
    def _management_redirect(field: str, token: str) -> Response:
        return Response.text(
            "303 See Other",
            "",
            headers=[
                ("Location", "/management?" + field + "=" + quote(token, safe="")),
                ("Cache-Control", "no-store"),
            ],
        )

    @staticmethod
    def _inventory_error_redirect(error_code: str) -> Response:
        return Response.text(
            "303 See Other",
            "",
            headers=[
                ("Location", "/management?inventory_error=" + quote(error_code)),
                ("Cache-Control", "no-store"),
            ],
        )

    def _mobile_review(self, environ, method: str, path: str) -> Response:
        tail = path.removeprefix("/mobile/review/").strip("/")
        parts = tail.split("/") if tail else []
        valid_get = method == "GET" and len(parts) == 1 and bool(unquote(parts[0]).strip())
        valid_post = method == "POST" and len(parts) == 2 and parts[1] == "resolve"
        if valid_get:
            query = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True)
            review_task_id = unquote(parts[0]).strip()
            raw_token = self._first(query, "token") or self._mobile_review_cookie(
                environ, review_task_id
            )
            model = self.queries.mobile_review(
                review_task_id,
                raw_token,
            )
            body = render_template(
                "mobile_review.html",
                content=render_mobile_review(model),
            )
            headers = [("Cache-Control", "no-store")]
            if raw_token and model.action_options:
                headers.append(
                    (
                        "Set-Cookie",
                        self._mobile_review_cookie_header(review_task_id, raw_token),
                    )
                )
            return Response.text(
                model.http_status,
                body,
                content_type="text/html; charset=utf-8",
                headers=headers,
            )
        if valid_post:
            review_task_id = unquote(parts[0]).strip()
            form = self._parse_form(environ)
            query = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True)
            raw_token = (
                self._first(query, "token")
                or self._mobile_review_cookie(environ, review_task_id)
                or self._first(form, "token")
            )
            action = self._first(form, "action").strip()
            target_price = self._first(form, "target_price").strip()
            resolution_payload = None
            if action == "adjusted":
                resolution_payload = {
                    "adjustment": {"target_price": target_price}
                }
            try:
                resolve_mobile_review(
                    self.container.settings.paths.runtime_db,
                    review_task_id,
                    raw_token,
                    action,
                    note=self._first(form, "note"),
                    resolution_payload=resolution_payload,
                    products_path=self.container.settings.paths.products_workbook,
                )
            except MobileReviewTransactionError as exc:
                return Response.text(
                    mobile_review_http_status(exc.code),
                    "复核未提交：" + str(exc),
                    headers=[("Cache-Control", "no-store")],
                )
            except Exception:
                return Response.text(
                    "503 Service Unavailable",
                    "复核提交失败，未执行任何业务操作，也未保存部分结果，请稍后重试。",
                    headers=[("Cache-Control", "no-store")],
                )
            location = "/mobile/review/" + quote(review_task_id, safe="") + "?result=completed"
            return Response.text(
                "303 See Other",
                "",
                headers=[("Location", location), ("Cache-Control", "no-store")],
            )
        return Response.text("404 Not Found", "复核链接无效或已失效。")

    @staticmethod
    def _mobile_review_cookie_name(review_task_id: str) -> str:
        digest = hashlib.sha256(review_task_id.encode("utf-8")).hexdigest()[:16]
        return "pra_mobile_review_" + digest

    def _mobile_review_cookie(self, environ, review_task_id: str) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(str(environ.get("HTTP_COOKIE", "")))
        except Exception:
            return ""
        morsel = cookie.get(self._mobile_review_cookie_name(review_task_id))
        return "" if morsel is None else morsel.value

    def _mobile_review_cookie_header(self, review_task_id: str, raw_token: str) -> str:
        name = self._mobile_review_cookie_name(review_task_id)
        path = "/mobile/review/" + quote(review_task_id, safe="")
        attributes = [
            f"{name}={raw_token}",
            f"Path={path}",
            "Max-Age=3600",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if self.container.settings.cookie_secure:
            attributes.append("Secure")
        return "; ".join(attributes)

    @staticmethod
    def _is_protected_route(path: str) -> bool:
        if path in PROTECTED_ROUTES or path in DATABASE_SECTION_ROUTES:
            return True
        match = DETAIL_ROUTE_PATTERN.fullmatch(path)
        return bool(
            match is not None
            and DETAIL_ROUTE_OWNERS.get(match.group(2)) == match.group(1)
        )

    @staticmethod
    def _protected_route_contract(path: str) -> tuple[str, Capability]:
        if path in PROTECTED_ROUTES:
            return PROTECTED_ROUTES[path]
        if path in DATABASE_SECTION_ROUTES or path.startswith("/database/"):
            return "数据库", Capability.VIEW_DATABASE
        if path.startswith("/management/"):
            return "业务管理", Capability.MANAGE_BUSINESS
        return "系统", Capability.VIEW_SYSTEM

    @staticmethod
    def _page_number(raw: str) -> int:
        try:
            value = int(raw or "1")
        except ValueError:
            return 1
        return min(max(value, 1), 100000)

    @staticmethod
    def _optional_date(raw: str) -> date | None:
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def _contains_dependency_override(self, environ, method: str) -> bool:
        query = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True)
        if set(query) & DEPENDENCY_OVERRIDE_FIELDS:
            return True
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return False
        body = self._read_body(environ)
        content_type = str(environ.get("CONTENT_TYPE", "")).lower()
        if "json" in content_type:
            try:
                parsed_json = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return False
            return bool(
                isinstance(parsed_json, dict)
                and set(parsed_json) & DEPENDENCY_OVERRIDE_FIELDS
            )
        parsed_form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        return bool(set(parsed_form) & DEPENDENCY_OVERRIDE_FIELDS)

    @staticmethod
    def _read_body(environ) -> bytes:
        cached = environ.get("pra.operations_web.body")
        if isinstance(cached, bytes):
            return cached
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        stream = environ.get("wsgi.input")
        body = stream.read(length) if stream is not None and length > 0 else b""
        environ["pra.operations_web.body"] = body
        return body

    def _parse_form(self, environ) -> dict[str, list[str]]:
        raw = self._read_body(environ)
        try:
            return parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return {}

    @staticmethod
    def _first(values: dict[str, list[str]], name: str) -> str:
        return values.get(name, [""])[0]

    @staticmethod
    def _many(values: dict[str, list[str]], name: str) -> list[str]:
        return [str(value) for value in values.get(name, [])]

    @staticmethod
    def _method_not_allowed(allow: str) -> Response:
        return Response.text(
            "405 Method Not Allowed",
            "该页面不支持此操作。",
            headers=[("Allow", allow)],
        )


def create_application(container: OperationsWebContainer | None = None) -> OperationsWebApplication:
    return OperationsWebApplication(container or build_container())


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    app = create_application()
    print(
        f"PRA 运营 Web 监听 {host}:{port}；"
        f"声明的对外协议为 {app.container.settings.public_scheme}。"
    )
    with make_server(
        host,
        port,
        app,
        server_class=ThreadedWSGIServer,
        handler_class=RedactingRequestHandler,
    ) as server:
        server.serve_forever()
