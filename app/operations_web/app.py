"""PRA 运营 Web 的 WSGI 应用骨架。"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from app.operations_web.auth import Capability, SessionCapacityError
from app.operations_web.composition import OperationsWebContainer, build_container
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
from app.services.security import LOGIN_RATE_LIMITER, record_security_event


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
    }
)

PROTECTED_ROUTES: dict[str, tuple[str, Capability]] = {
    "/today": ("今日", Capability.VIEW_TODAY),
    "/database": ("数据库", Capability.VIEW_DATABASE),
    "/management": ("业务管理", Capability.MANAGE_BUSINESS),
    "/system": ("系统", Capability.VIEW_SYSTEM),
}

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
                "数据库、工作簿和队列位置由服务启动配置固定，不能由请求覆盖。",
                headers=[("Cache-Control", "no-store")],
            )

        if path == "/static/app.css":
            if method != "GET":
                return self._method_not_allowed("GET")
            return Response.text(
                "200 OK",
                static_text("app.css"),
                content_type="text/css; charset=utf-8",
                headers=[("Cache-Control", "public, max-age=3600")],
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
            content = render_management(self.queries.management())
        elif path == "/system":
            content = render_system(self.queries.system())
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
                return Response.text("404 Not Found", "未找到该业务事实。")
            content = render_detail(detail)
        drawer = render_notification_drawer(self.queries.notification_drawer())
        body = render_template(
            "page.html",
            page_title=html(title),
            active_today="active" if path == "/today" else "",
            active_database="active" if path.startswith("/database") else "",
            active_management="active" if path.startswith("/management") else "",
            active_system="active" if path == "/system" else "",
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

    def _mobile_review(self, environ, method: str, path: str) -> Response:
        tail = path.removeprefix("/mobile/review/").strip("/")
        parts = tail.split("/") if tail else []
        valid_get = method == "GET" and len(parts) == 1 and bool(unquote(parts[0]).strip())
        valid_post = method == "POST" and len(parts) == 2 and parts[1] == "resolve"
        if valid_get:
            query = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True)
            model = self.queries.mobile_review(
                unquote(parts[0]).strip(),
                self._first(query, "token"),
            )
            body = render_template(
                "mobile_review.html",
                content=render_mobile_review(model),
            )
            return Response.text(
                model.http_status,
                body,
                content_type="text/html; charset=utf-8",
                headers=[("Cache-Control", "no-store")],
            )
        if valid_post:
            return Response.text(
                "503 Service Unavailable",
                "当前复核入口只提供查看，未执行任何业务操作。",
                headers=[("Cache-Control", "no-store"), ("Retry-After", "300")],
            )
        return Response.text("404 Not Found", "复核链接无效或已失效。")

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
