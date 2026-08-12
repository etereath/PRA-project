"""PRA 运营 Web 的 WSGI 应用骨架。"""

from __future__ import annotations

import json
import logging
import re
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from app.operations_web.auth import Capability
from app.operations_web.composition import OperationsWebContainer, build_container
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
    "/": ("今日", Capability.VIEW_TODAY),
    "/database": ("数据库", Capability.VIEW_DATABASE),
    "/management": ("业务管理", Capability.MANAGE_BUSINESS),
    "/system": ("系统", Capability.VIEW_SYSTEM),
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

        if path in {"/login", "/runtime/login"}:
            if method == "GET":
                return self._login_page(environ)
            if method == "POST":
                return self._login(environ)
            return self._method_not_allowed("GET, POST")

        if path in {"/logout", "/runtime/logout"}:
            if method != "POST":
                return self._method_not_allowed("POST")
            return self._logout(environ)

        if path.startswith("/mobile/review/"):
            return self._mobile_review_shell(method, path)

        if path in PROTECTED_ROUTES:
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
        state, cookie = self.container.sessions.issue_preauth(
            replace_session_id=previous.session_id if previous else None
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
            return Response.text("429 Too Many Requests", "登录尝试过于频繁，请稍后再试。")

        principal = self.container.credentials.authenticate(username, password)
        if principal is None:
            LOGIN_RATE_LIMITER.record_failure(username, remote_addr)
            reason = (
                "尚未配置后台登录密码。"
                if not self.container.credentials.configured
                else "账号或密码不正确。"
            )
            return self._login_page(environ, message=reason, status="401 Unauthorized")

        LOGIN_RATE_LIMITER.record_success(username, remote_addr)
        _, cookie = self.container.sessions.rotate_authenticated(
            principal=principal,
            replace_session_id=session.session_id if session else None,
        )
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
                ("Location", "/"),
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
            headers=[("Location", "/login"), ("Set-Cookie", expired_cookie)],
        )

    def _protected_page(self, environ, path: str) -> Response:
        session = self.container.sessions.get(str(environ.get("HTTP_COOKIE", "")))
        if session is None or session.principal is None:
            return Response.text(
                "303 See Other",
                "",
                headers=[("Location", "/login"), ("Cache-Control", "no-store")],
            )
        title, capability = PROTECTED_ROUTES[path]
        if not self.container.authorization.allows(session.principal, capability):
            record_security_event(
                "AUTHORIZATION_REJECTED",
                route=path,
                outcome="rejected",
                reason_code="CAPABILITY_DENIED",
                subject=session.principal.subject,
            )
            return Response.text("403 Forbidden", "当前账号没有访问该页面的权限。")

        readiness = self._runtime_readiness_message()
        body = render_template(
            "shell.html",
            page_title=html(title),
            active_today="active" if path == "/" else "",
            active_database="active" if path == "/database" else "",
            active_management="active" if path == "/management" else "",
            active_system="active" if path == "/system" else "",
            readiness=html(readiness),
            csrf_token=html(session.csrf_token),
            username=html(session.principal.subject),
        )
        return Response.text(
            "200 OK",
            body,
            content_type="text/html; charset=utf-8",
            headers=[("Cache-Control", "no-store")],
        )

    def _runtime_readiness_message(self) -> str:
        try:
            schema = self.container.runtime_repository.check_schema_health()
            if schema.ok:
                return "运行数据只读检查正常。业务页面将在后续阶段接入。"
        except Exception:
            pass
        return "运行数据当前需要维护。Web 未执行初始化、迁移或真实数据修复。"

    def _mobile_review_shell(self, method: str, path: str) -> Response:
        tail = path.removeprefix("/mobile/review/").strip("/")
        parts = tail.split("/") if tail else []
        valid_get = method == "GET" and len(parts) == 1 and bool(unquote(parts[0]).strip())
        valid_post = method == "POST" and len(parts) == 2 and parts[1] == "resolve"
        if valid_get:
            body = render_template("mobile_review_shell.html")
            return Response.text(
                "503 Service Unavailable",
                body,
                content_type="text/html; charset=utf-8",
                headers=[("Cache-Control", "no-store"), ("Retry-After", "300")],
            )
        if valid_post:
            return Response.text(
                "503 Service Unavailable",
                "手机复核写入尚未切换到新 Web，本阶段未执行任何业务操作。",
                headers=[("Cache-Control", "no-store"), ("Retry-After", "300")],
            )
        return Response.text("404 Not Found", "复核链接无效或已失效。")

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
