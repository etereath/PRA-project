from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlencode

import pytest

from app.operations_web.app import create_application
from app.operations_web.auth import Capability, Principal
from app.operations_web.composition import (
    OperationsWebConfigurationError,
    OperationsWebContainer,
    OperationsWebPaths,
    OperationsWebSettings,
    build_container,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


class DenySystemAuthorization:
    def allows(self, principal: Principal, capability: Capability) -> bool:
        return capability != Capability.VIEW_SYSTEM


@pytest.fixture()
def operations_web(tmp_path: Path):
    runtime_db = tmp_path / "runtime.sqlite3"
    SQLiteRuntimeRepository(runtime_db).init_schema()
    products = tmp_path / "products.xlsx"
    price_rules = tmp_path / "price_rules.xlsx"
    listing_rules = tmp_path / "listing_rules.xlsx"
    products.write_bytes(b"synthetic-products")
    price_rules.write_bytes(b"synthetic-price-rules")
    listing_rules.write_bytes(b"synthetic-listing-rules")
    queue_root = tmp_path / "queue"
    for name in ("inbox", "working", "results", "archive"):
        (queue_root / name).mkdir(parents=True, exist_ok=True)
    settings = OperationsWebSettings(
        environment="development",
        public_scheme="http",
        cookie_secure=False,
        admin_username="admin",
        admin_password="synthetic-password",
        paths=OperationsWebPaths(
            runtime_db=runtime_db,
            products_workbook=products,
            price_rules_workbook=price_rules,
            listing_rules_workbook=listing_rules,
            queue_root=queue_root,
        ),
    )
    container = build_container(settings)
    return create_application(container), container, tmp_path


def call_app(
    app,
    *,
    path: str,
    method: str = "GET",
    query: str = "",
    form: dict[str, str] | None = None,
    json_body: dict[str, object] | None = None,
    cookie: str = "",
):
    encoded = (
        json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        if json_body is not None
        else urlencode(form or {}).encode("utf-8")
    )
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(encoded)),
        "CONTENT_TYPE": (
            "application/json"
            if json_body is not None
            else "application/x-www-form-urlencoded"
        ),
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_COOKIE": cookie,
        "wsgi.input": io.BytesIO(encoded),
    }
    captured: dict[str, object] = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(app(environ, start_response)).decode("utf-8")
    return str(captured["status"]), list(captured["headers"]), body


def header_values(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for key, value in headers if key.lower() == name.lower()]


def cookie_pair(set_cookie: str) -> str:
    return set_cookie.split(";", 1)[0]


def login(app, container) -> tuple[str, str]:
    status, headers, body = call_app(app, path="/login")
    assert status == "200 OK"
    preauth_cookie = cookie_pair(header_values(headers, "Set-Cookie")[0])
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None
    status, headers, _ = call_app(
        app,
        path="/login",
        method="POST",
        cookie=preauth_cookie,
        form={
            "username": container.settings.admin_username,
            "password": container.settings.admin_password,
            "csrf_token": match.group(1),
        },
    )
    assert status == "303 See Other"
    authenticated_cookie = cookie_pair(header_values(headers, "Set-Cookie")[0])
    return preauth_cookie, authenticated_cookie


def snapshot_tree(
    root: Path,
    *,
    ignore_sqlite_sidecar_mtime: bool = False,
) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        normalized_mtime = (
            0
            if ignore_sqlite_sidecar_mtime and relative.endswith(("-shm", "-wal"))
            else stat.st_mtime_ns
        )
        snapshot[relative] = (
            stat.st_size,
            normalized_mtime,
            hashlib.sha256(payload).hexdigest(),
        )
    return snapshot


@pytest.mark.parametrize(
    ("environment", "scheme", "cookie_secure", "expected"),
    [
        ("development", "http", "false", ("development", False)),
        ("production", "https", "true", ("production", True)),
    ],
)
def test_environment_and_cookie_contract_accepts_only_matching_modes(
    tmp_path: Path,
    environment: str,
    scheme: str,
    cookie_secure: str,
    expected: tuple[str, bool],
) -> None:
    settings = OperationsWebSettings.from_environment(
        {
            "PRA_ENV": environment,
            "PRA_WEB_PUBLIC_SCHEME": scheme,
            "PRA_COOKIE_SECURE": cookie_secure,
        },
        project_root=tmp_path,
    )
    assert (settings.environment, settings.cookie_secure) == expected


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {
            "PRA_ENV": "development",
            "PRA_WEB_PUBLIC_SCHEME": "http",
            "PRA_COOKIE_SECURE": "true",
        },
        {
            "PRA_ENV": "production",
            "PRA_WEB_PUBLIC_SCHEME": "http",
            "PRA_COOKIE_SECURE": "true",
        },
        {
            "PRA_ENV": "production",
            "PRA_WEB_PUBLIC_SCHEME": "https",
            "PRA_COOKIE_SECURE": "false",
        },
    ],
)
def test_environment_and_cookie_conflicts_fail_startup_in_chinese(
    tmp_path: Path,
    environment: dict[str, str],
) -> None:
    with pytest.raises(OperationsWebConfigurationError, match=r"[\u4e00-\u9fff]"):
        OperationsWebSettings.from_environment(environment, project_root=tmp_path)


def test_composition_root_resolves_fixed_paths_once(tmp_path: Path) -> None:
    settings = OperationsWebSettings.from_environment(
        {
            "PRA_ENV": "development",
            "PRA_WEB_PUBLIC_SCHEME": "http",
            "PRA_COOKIE_SECURE": "false",
            "PRA_RUNTIME_DB": "fixed/runtime.sqlite3",
            "SHADOWBOT_QUEUE_DIR": "fixed/queue",
        },
        project_root=tmp_path,
    )
    assert settings.paths.runtime_db == (tmp_path / "fixed/runtime.sqlite3").resolve()
    assert settings.paths.queue_root == (tmp_path / "fixed/queue").resolve()
    assert settings.paths.runtime_db.is_absolute()


def test_all_get_routes_are_zero_write_and_never_initialize_schema(operations_web, monkeypatch) -> None:
    app, container, root = operations_web
    _, authenticated_cookie = login(app, container)
    # WAL 运行库在首次只读连接时可能由 SQLite 建立共享内存侧车；先把这一
    # SQLite 连接基础设施纳入基线，门禁随后比较主库、侧车、工作簿和队列。
    assert call_app(app, path="/health")[0] == "200 OK"
    before = snapshot_tree(root, ignore_sqlite_sidecar_mtime=True)

    def forbidden_init_schema(*_args, **_kwargs):
        raise AssertionError("GET 不得调用 init_schema")

    monkeypatch.setattr(SQLiteRuntimeRepository, "init_schema", forbidden_init_schema)
    requests = [
        ("/health", ""),
        ("/login", ""),
        ("/", authenticated_cookie),
        ("/database", authenticated_cookie),
        ("/management", authenticated_cookie),
        ("/system", authenticated_cookie),
        ("/mobile/review/REVIEW-SYNTHETIC", ""),
        ("/static/app.css", ""),
    ]
    for path, cookie in requests:
        status, _, _ = call_app(
            app,
            path=path,
            query="token=synthetic-secret" if path.startswith("/mobile/") else "",
            cookie=cookie,
        )
        assert status.split()[0] in {"200", "503"}
    assert snapshot_tree(root, ignore_sqlite_sidecar_mtime=True) == before


def test_health_does_not_create_missing_runtime_database(tmp_path: Path) -> None:
    missing_db = tmp_path / "missing" / "runtime.sqlite3"
    paths = OperationsWebPaths(
        runtime_db=missing_db,
        products_workbook=tmp_path / "products.xlsx",
        price_rules_workbook=tmp_path / "price.xlsx",
        listing_rules_workbook=tmp_path / "listing.xlsx",
        queue_root=tmp_path / "queue",
    )
    settings = OperationsWebSettings(
        environment="development",
        public_scheme="http",
        cookie_secure=False,
        admin_username="admin",
        admin_password="secret",
        paths=paths,
    )
    app = create_application(build_container(settings))
    status, _, body = call_app(app, path="/health")
    assert status == "503 Service Unavailable"
    assert body.startswith("unhealthy:")
    assert not missing_db.exists()
    assert not missing_db.parent.exists()


def test_dependency_paths_cannot_be_selected_by_request(operations_web) -> None:
    app, _, _ = operations_web
    status, _, body = call_app(
        app,
        path="/health",
        query=urlencode({"runtime_db": "C:/untrusted.sqlite3"}),
    )
    assert status == "400 Bad Request"
    assert "启动配置固定" in body
    status, _, body = call_app(
        app,
        path="/management",
        method="POST",
        json_body={"queue_root": "C:/untrusted-queue"},
    )
    assert status == "400 Bad Request"
    assert "启动配置固定" in body


def test_request_body_has_a_fixed_upper_bound(operations_web) -> None:
    app, _, _ = operations_web
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/login",
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(64 * 1024 + 1),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_COOKIE": "",
        "wsgi.input": io.BytesIO(b""),
    }
    captured = {}
    payload = b"".join(app(environ, lambda status, headers: captured.update(status=status)))
    assert captured["status"] == "413 Content Too Large"
    assert payload.decode("utf-8") == "请求内容过大。"


def test_login_rotates_session_and_cookie_mode_is_explicit(operations_web) -> None:
    app, container, _ = operations_web
    status, headers, body = call_app(app, path="/login")
    assert status == "200 OK"
    preauth_header = header_values(headers, "Set-Cookie")[0]
    assert "HttpOnly" in preauth_header
    assert "SameSite=Lax" in preauth_header
    assert "Secure" not in preauth_header
    preauth_cookie = cookie_pair(preauth_header)
    token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
    status, headers, _ = call_app(
        app,
        path="/login",
        method="POST",
        cookie=preauth_cookie,
        form={"username": "admin", "password": "synthetic-password", "csrf_token": token},
    )
    assert status == "303 See Other"
    authenticated_cookie = cookie_pair(header_values(headers, "Set-Cookie")[0])
    assert authenticated_cookie != preauth_cookie
    assert container.sessions.get(preauth_cookie) is None
    assert container.sessions.get(authenticated_cookie).principal.subject == "admin"


def test_production_session_cookie_is_secure(tmp_path: Path) -> None:
    settings = OperationsWebSettings.from_environment(
        {
            "PRA_ENV": "production",
            "PRA_WEB_PUBLIC_SCHEME": "https",
            "PRA_COOKIE_SECURE": "true",
            "RUNTIME_ADMIN_PASSWORD": "synthetic",
        },
        project_root=tmp_path,
    )
    app = create_application(build_container(settings))
    _, headers, _ = call_app(app, path="/login")
    assert "Secure" in header_values(headers, "Set-Cookie")[0]


def test_csrf_and_logout_are_fail_closed(operations_web) -> None:
    app, container, _ = operations_web
    preauth, authenticated = login(app, container)
    assert preauth != authenticated
    status, _, _ = call_app(app, path="/logout", method="GET", cookie=authenticated)
    assert status == "405 Method Not Allowed"
    status, _, _ = call_app(
        app,
        path="/logout",
        method="POST",
        cookie=authenticated,
        form={"csrf_token": "invalid"},
    )
    assert status == "403 Forbidden"
    session = container.sessions.get(authenticated)
    status, headers, _ = call_app(
        app,
        path="/logout",
        method="POST",
        cookie=authenticated,
        form={"csrf_token": session.csrf_token},
    )
    assert status == "303 See Other"
    assert "Max-Age=0" in header_values(headers, "Set-Cookie")[0]
    assert container.sessions.get(authenticated) is None


def test_capability_backend_denial_is_enforced_by_route(operations_web) -> None:
    _, original, _ = operations_web
    denied = OperationsWebContainer(
        settings=original.settings,
        runtime_repository=original.runtime_repository,
        credentials=original.credentials,
        authorization=DenySystemAuthorization(),
        sessions=original.sessions,
    )
    app = create_application(denied)
    _, authenticated = login(app, denied)
    status, _, body = call_app(app, path="/system", cookie=authenticated)
    assert status == "403 Forbidden"
    assert "没有访问" in body


def test_security_headers_are_applied_to_html_health_errors_and_static(operations_web) -> None:
    app, _, _ = operations_web
    for path in ("/login", "/health", "/not-found", "/static/app.css"):
        _, headers, _ = call_app(app, path=path)
        header_map = {key: value for key, value in headers}
        assert "default-src 'self'" in header_map["Content-Security-Policy"]
        assert header_map["X-Content-Type-Options"] == "nosniff"
        assert header_map["X-Frame-Options"] == "DENY"
        assert header_map["Referrer-Policy"] == "no-referrer"


def test_mobile_review_protocol_shell_is_read_only_and_does_not_echo_secrets(operations_web) -> None:
    app, _, root = operations_web
    before = snapshot_tree(root)
    status, _, body = call_app(
        app,
        path="/mobile/review/REVIEW-SENSITIVE-ID",
        query="token=never-echo-this-token",
    )
    assert status == "503 Service Unavailable"
    assert "原手机复核链接协议" in body
    assert "REVIEW-SENSITIVE-ID" not in body
    assert "never-echo-this-token" not in body
    status, _, body = call_app(
        app,
        path="/mobile/review/REVIEW-SENSITIVE-ID/resolve",
        method="POST",
        form={"token": "never-echo-this-token", "action": "approved"},
    )
    assert status == "503 Service Unavailable"
    assert "未执行任何业务操作" in body
    assert snapshot_tree(root) == before


def test_business_posts_are_not_available_in_7b(operations_web) -> None:
    app, container, _ = operations_web
    _, authenticated = login(app, container)
    for path in ("/", "/database", "/management", "/system"):
        status, headers, _ = call_app(app, path=path, method="POST", cookie=authenticated)
        assert status == "405 Method Not Allowed"
        assert header_values(headers, "Allow") == ["GET"]


def test_templates_and_styles_use_only_local_assets() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "operations_web"
    for path in [*root.joinpath("templates").glob("*.html"), *root.joinpath("static").glob("*.css")]:
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text
        assert "https://" not in text
        assert "cdn" not in text.lower()
