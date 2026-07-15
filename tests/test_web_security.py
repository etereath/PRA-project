from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from unittest.mock import patch

from app.path_policy import PathAccessPolicy, PathPolicyError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.security import (
    LoginRateLimiter,
    clear_security_audit_events,
    list_security_audit_events,
    record_security_event,
)
from app.web import (
    DEFAULT_PRODUCTS,
    _LOGIN_CSRF_CONTEXTS,
    _LOGIN_CSRF_LOCK,
    _RUNTIME_SESSIONS,
    _resolve_request_or_trusted_default,
    application,
)


class PathPolicyTests(unittest.TestCase):
    def test_candidate_requires_absolute_path_and_stays_inside_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            root.mkdir()
            (root / "safe.txt").write_text("ok", encoding="utf-8")
            policy = PathAccessPolicy((root.resolve(),))

            resolved = policy.resolve(root / "safe.txt", purpose="test")
            self.assertEqual(resolved, (root / "safe.txt").resolve())

            with self.assertRaisesRegex(PathPolicyError, "PATH_RELATIVE"):
                policy.resolve("safe.txt", purpose="test")
            with self.assertRaisesRegex(PathPolicyError, "PATH_TRAVERSAL_COMPONENT"):
                policy.resolve(root / "nested" / ".." / "safe.txt", purpose="test")
            with self.assertRaisesRegex(PathPolicyError, "PATH_OUTSIDE_ALLOWLIST"):
                policy.resolve(Path(temp_dir) / "data_evil" / "file.txt", purpose="test")

    def test_environment_roots_use_windows_separator_and_reject_invalid_items(self) -> None:
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "one"
            second = Path(temp_dir) / "two"
            first.mkdir()
            second.mkdir()
            with patch.dict("os.environ", {"PRA_ALLOWED_DATA_DIRS": os.pathsep.join((str(first), str(second)))}, clear=False):
                policy = PathAccessPolicy.from_environment(default_root=first)
            self.assertEqual(policy.allowed_roots, (first.resolve(), second.resolve()))

            with patch.dict("os.environ", {"PRA_ALLOWED_DATA_DIRS": str(first) + os.pathsep}, clear=False):
                with self.assertRaisesRegex(PathPolicyError, "PATH_ALLOWLIST_INVALID"):
                    PathAccessPolicy.from_environment(default_root=first)


class WebSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        _RUNTIME_SESSIONS.clear()
        with _LOGIN_CSRF_LOCK:
            _LOGIN_CSRF_CONTEXTS.clear()
        clear_security_audit_events()

    def _call(
        self,
        *,
        path: str,
        method: str = "GET",
        query: str = "",
        body: str = "",
        cookie: str = "",
        remote: str = "127.0.0.1",
        content_type: str = "application/x-www-form-urlencoded",
        csrf_header: str = "",
        forwarded_for: str = "",
    ):
        captured: dict[str, object] = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        payload = body.encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": content_type,
            "REMOTE_ADDR": remote,
            "wsgi.input": io.BytesIO(payload),
        }
        if cookie:
            environ["HTTP_COOKIE"] = cookie
        if csrf_header:
            environ["HTTP_X_CSRF_TOKEN"] = csrf_header
        if forwarded_for:
            environ["HTTP_X_FORWARDED_FOR"] = forwarded_for
        response = application(environ, start_response)
        headers = list(captured["headers"])
        return str(captured["status"]), headers, b"".join(response).decode("utf-8")

    @staticmethod
    def _header(headers: list[tuple[str, str]], name: str) -> str:
        for key, value in headers:
            if key.lower() == name.lower():
                return value
        return ""

    def test_login_csrf_session_rotation_and_secure_cookie(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "runtime.sqlite3"
            SQLiteRuntimeRepository(db_path).init_schema()
            with patch.dict(
                "os.environ",
                {
                    "PRA_ALLOWED_DATA_DIRS": str(root),
                    "RUNTIME_ADMIN_PASSWORD": "secret-password",
                    "PRA_ENV": "production",
                },
                clear=False,
            ):
                status, login_headers, login_page = self._call(
                    path="/runtime/login",
                    query=urlencode({"runtime_db": str(db_path)}),
                )
                self.assertEqual(status, "200 OK")
                csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
                preauth_cookie = self._header(login_headers, "Set-Cookie").split(";", 1)[0]
                status, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=preauth_cookie,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret-password",
                            "csrf_token": csrf_token,
                        }
                    ),
                )
            self.assertEqual(status, "303 See Other")
            session_cookie = self._header(headers, "Set-Cookie")
            self.assertIn("Secure", session_cookie)
            self.assertIn("HttpOnly", session_cookie)
            csrf_cookie = [value for key, value in headers if key == "Set-Cookie"][1]
            self.assertIn("pra_runtime_csrf=", csrf_cookie)

    def test_session_write_requires_csrf_and_request_cannot_define_allowlist(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "runtime.sqlite3"
            SQLiteRuntimeRepository(db_path).init_schema()
            with patch.dict(
                "os.environ",
                {"PRA_ALLOWED_DATA_DIRS": str(root), "RUNTIME_ADMIN_PASSWORD": "secret"},
                clear=False,
            ):
                _, login_headers, login_page = self._call(
                    path="/runtime/login",
                    query=urlencode({"runtime_db": str(db_path)}),
                )
                login_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
                preauth_cookie = self._header(login_headers, "Set-Cookie").split(";", 1)[0]
                _, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=preauth_cookie,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": login_token,
                        }
                    ),
                )
                session_cookie = self._header(headers, "Set-Cookie").split(";", 1)[0]
                session_id = session_cookie.split("=", 1)[1]
                session_token = str(_RUNTIME_SESSIONS[session_id]["csrf_token"])

                status, _, body = self._call(
                    path="/runtime",
                    method="POST",
                    body=urlencode({"runtime_db": str(db_path), "action": "load"}),
                    cookie=session_cookie,
                )
                self.assertEqual(status, "403 Forbidden")
                self.assertIn("CSRF_REJECTED", body)

                for method in ("PUT", "PATCH", "DELETE"):
                    status, _, body = self._call(
                        path="/runtime",
                        method=method,
                        body=urlencode({"runtime_db": str(db_path), "action": "load"}),
                        cookie=session_cookie,
                    )
                    self.assertEqual(status, "403 Forbidden")
                    self.assertIn("CSRF_REJECTED", body)

                status, _, _ = self._call(
                    path="/runtime",
                    method="POST",
                    body=urlencode(
                        {"runtime_db": str(db_path), "action": "load", "csrf_token": session_token}
                    ),
                    cookie=session_cookie,
                )
                self.assertEqual(status, "200 OK")

                status, _, body = self._call(
                    path="/runtime",
                    query=urlencode({"runtime_db": str(root.parent / "attacker.sqlite3")}),
                    cookie=session_cookie,
                )
                self.assertEqual(status, "200 OK")
                self.assertNotIn("attacker.sqlite3", body)

                status, _, body = self._call(
                    path="/runtime",
                    query=urlencode({"allowed_data_dirs": str(root.parent)}),
                    cookie=session_cookie,
                )
                self.assertEqual(status, "400 Bad Request")
                self.assertIn("PATH_CONFIG_FROM_REQUEST", body)

    def test_login_rate_limit_is_bounded_and_audited_without_secrets(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "runtime.sqlite3"
            SQLiteRuntimeRepository(db_path).init_schema()
            with patch.dict(
                "os.environ",
                {
                    "PRA_ALLOWED_DATA_DIRS": str(root),
                    "RUNTIME_ADMIN_PASSWORD": "secret-password",
                    "RUNTIME_LOGIN_RATE_LIMIT_MAX_ATTEMPTS": "2",
                    "RUNTIME_LOGIN_RATE_LIMIT_COOLDOWN_SECONDS": "60",
                },
                clear=False,
            ):
                for index in range(2):
                    _, login_headers, login_page = self._call(
                        path="/runtime/login",
                        query=urlencode({"runtime_db": str(db_path)}),
                        remote="198.51.100.10",
                        forwarded_for=f"203.0.113.{index + 1}",
                    )
                    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
                    preauth_cookie = self._header(login_headers, "Set-Cookie").split(";", 1)[0]
                    status, _, body = self._call(
                        path="/runtime/login",
                        method="POST",
                        cookie=preauth_cookie,
                        remote="198.51.100.10",
                        body=urlencode(
                            {
                                "runtime_db": str(db_path),
                                "username": "admin",
                                "password": "wrong-password",
                                "csrf_token": token,
                            }
                        ),
                    )
                self.assertEqual(status, "429 Too Many Requests")
                self.assertIn("RATE_LIMITED", body)

            events = list_security_audit_events()
            event_types = {str(event["event_type"]) for event in events}
            self.assertIn("LOGIN_FAILED", event_types)
            self.assertIn("LOGIN_RATE_LIMITED", event_types)
            serialized = str(events)
            self.assertNotIn("wrong-password", serialized)
            self.assertNotIn("secret-password", serialized)

    def _login_context(self, db_path: Path, *, remote: str = "127.0.0.1") -> tuple[str, str]:
        status, headers, page = self._call(
            path="/runtime/login",
            query=urlencode({"runtime_db": str(db_path)}),
            remote=remote,
        )
        self.assertEqual(status, "200 OK")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        cookie = self._header(headers, "Set-Cookie").split(";", 1)[0]
        return token, cookie

    def test_default_paths_missing_targets_and_symlink_parents_fail_closed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_root = root / "runtime"
            samples_root = root / "samples"
            runtime_root.mkdir()
            samples_root.mkdir()
            sample_file = samples_root / "products.xlsx"
            sample_file.write_text("sample", encoding="utf-8")

            with patch.dict("os.environ", {"PRA_ALLOWED_DATA_DIRS": str(runtime_root)}, clear=False):
                with self.assertRaisesRegex(PathPolicyError, "PATH_OUTSIDE_ALLOWLIST"):
                    _resolve_request_or_trusted_default(
                        "",
                        sample_file,
                        purpose="products",
                        allow_create=False,
                    )
                missing = runtime_root / "missing.sqlite3"
                policy = PathAccessPolicy((runtime_root.resolve(),))
                with self.assertRaisesRegex(PathPolicyError, "PATH_NOT_FOUND"):
                    policy.resolve(missing, purpose="runtime_db", allow_create=False)
                self.assertFalse(missing.exists())
                self.assertEqual(
                    policy.resolve(missing, purpose="runtime_db", allow_create=True),
                    missing,
                )

            with patch.dict("os.environ", {"PRA_ALLOWED_DATA_DIRS": str(samples_root)}, clear=False):
                self.assertEqual(
                    _resolve_request_or_trusted_default(
                        "",
                        sample_file,
                        purpose="products",
                        allow_create=False,
                    ),
                    sample_file.resolve(),
                )

            policy = PathAccessPolicy((runtime_root.resolve(),))
            with self.assertRaisesRegex(PathPolicyError, "PATH_TRAVERSAL_COMPONENT"):
                policy.resolve(str(runtime_root) + r"\nested\..\missing.xlsx", purpose="excel")
            with self.assertRaisesRegex(PathPolicyError, "PATH_TRAVERSAL_COMPONENT"):
                policy.resolve(str(runtime_root / "%2e%2e" / "missing.xlsx"), purpose="excel")
            if os.name == "nt":
                for special_path in (
                    r"\\server\share\file.xlsx",
                    r"\\?\C:\data\file.xlsx",
                    r"\\.\PIPE\name",
                ):
                    with self.assertRaisesRegex(PathPolicyError, "PATH_SPECIAL_NAMESPACE"):
                        policy.resolve(special_path, purpose="excel")
                with self.assertRaisesRegex(PathPolicyError, "PATH_AMBIGUOUS_COMPONENT"):
                    policy.resolve(str(runtime_root) + r"\safe.xlsx.", purpose="excel")
                with self.assertRaisesRegex(PathPolicyError, "PATH_AMBIGUOUS_COMPONENT"):
                    policy.resolve(str(runtime_root) + r"\safe.xlsx ", purpose="excel")
                with self.assertRaisesRegex(PathPolicyError, "PATH_OUTSIDE_ALLOWLIST"):
                    policy.resolve(r"Z:\other\file.xlsx", purpose="excel")

            outside = root / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            link = runtime_root / "outside_link"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError as exc:
                if os.name != "nt":
                    self.skipTest(f"symlink creation unavailable: {exc}")
                junction = subprocess.run(
                    ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if junction.returncode != 0:
                    self.skipTest(f"symlink/junction creation unavailable: {junction.stdout or junction.stderr}")
            with self.assertRaisesRegex(PathPolicyError, "PATH_SYMLINK_ESCAPE"):
                policy.resolve(link / "missing.txt", purpose="excel", allow_create=True)

    def test_login_get_is_read_only_and_pre_auth_context_is_bound(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "runtime.sqlite3"
            SQLiteRuntimeRepository(db_path).init_schema()
            with patch.dict(
                "os.environ",
                {"PRA_ALLOWED_DATA_DIRS": str(root), "RUNTIME_ADMIN_PASSWORD": "secret"},
                clear=False,
            ):
                for _ in range(12):
                    status, _, _ = self._call(
                        path="/runtime/login",
                        query=urlencode({"runtime_db": str(db_path)}),
                    )
                    self.assertEqual(status, "200 OK")
                self.assertNotIn("LOGIN_FAILED", {event["event_type"] for event in list_security_audit_events()})
                with patch("app.web.LOGIN_CSRF_MAX_CONTEXTS", 3):
                    for _ in range(10):
                        self._call(
                            path="/runtime/login",
                            query=urlencode({"runtime_db": str(db_path)}),
                        )
                with _LOGIN_CSRF_LOCK:
                    self.assertLessEqual(len(_LOGIN_CSRF_CONTEXTS), 3)

                token_a, cookie_a = self._login_context(db_path, remote="198.51.100.20")
                token_b, cookie_b = self._login_context(db_path, remote="198.51.100.20")
                invalid_status, _, invalid_body = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=cookie_b,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": token_a,
                        }
                    ),
                    remote="198.51.100.20",
                )
                self.assertEqual(invalid_status, "403 Forbidden")
                self.assertIn("CSRF_REJECTED", invalid_body)

                valid_status, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=cookie_a,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": token_a,
                        }
                    ),
                    remote="198.51.100.20",
                )
                self.assertEqual(valid_status, "303 See Other")
                session_cookie = self._header(headers, "Set-Cookie").split(";", 1)[0]

                replay_status, _, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=cookie_a,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": token_a,
                        }
                    ),
                    remote="198.51.100.20",
                )
                self.assertEqual(replay_status, "403 Forbidden")

                query_token, query_cookie = self._login_context(db_path)
                query_status, _, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    query=urlencode({"runtime_db": str(db_path), "csrf_token": query_token}),
                    cookie=query_cookie,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                        }
                    ),
                )
                self.assertEqual(query_status, "403 Forbidden")

                method_status, _, _ = self._call(
                    path="/runtime/login",
                    method="PUT",
                    cookie=session_cookie,
                    body="",
                )
                self.assertEqual(method_status, "405 Method Not Allowed")

    def test_session_csrf_accepts_json_and_header_but_not_query(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "runtime.sqlite3"
            SQLiteRuntimeRepository(db_path).init_schema()
            with patch.dict(
                "os.environ",
                {"PRA_ALLOWED_DATA_DIRS": str(root), "RUNTIME_ADMIN_PASSWORD": "secret"},
                clear=False,
            ):
                login_token, preauth_cookie = self._login_context(db_path)
                _, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=preauth_cookie,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": login_token,
                        }
                    ),
                )
                session_cookie = self._header(headers, "Set-Cookie").split(";", 1)[0]
                session_id = session_cookie.split("=", 1)[1]
                session_token = str(_RUNTIME_SESSIONS[session_id]["csrf_token"])

                json_status, _, _ = self._call(
                    path="/runtime",
                    method="POST",
                    cookie=session_cookie,
                    content_type="application/json",
                    body=json.dumps(
                        {"runtime_db": str(db_path), "action": "load", "csrf_token": session_token}
                    ),
                )
                self.assertNotEqual(json_status, "403 Forbidden")

                header_status, _, _ = self._call(
                    path="/runtime",
                    method="POST",
                    cookie=session_cookie,
                    csrf_header=session_token,
                    body=urlencode({"runtime_db": str(db_path), "action": "load"}),
                )
                self.assertNotEqual(header_status, "403 Forbidden")

                wrong_status, _, _ = self._call(
                    path="/runtime",
                    method="POST",
                    cookie=session_cookie,
                    body=urlencode(
                        {"runtime_db": str(db_path), "action": "load", "csrf_token": "wrong-token"}
                    ),
                )
                self.assertEqual(wrong_status, "403 Forbidden")

                query_status, _, _ = self._call(
                    path="/runtime",
                    method="POST",
                    query=urlencode({"csrf_token": session_token}),
                    cookie=session_cookie,
                    body=urlencode({"runtime_db": str(db_path), "action": "load"}),
                )
                self.assertEqual(query_status, "403 Forbidden")

    def test_session_rotation_logout_and_cookie_matrix(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "runtime.sqlite3"
            SQLiteRuntimeRepository(db_path).init_schema()
            with patch.dict(
                "os.environ",
                {
                    "PRA_ALLOWED_DATA_DIRS": str(root),
                    "RUNTIME_ADMIN_PASSWORD": "secret",
                    "PRA_ENV": "production",
                },
                clear=False,
            ):
                login_token, preauth_cookie = self._login_context(db_path)
                _, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=preauth_cookie,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": login_token,
                        }
                    ),
                )
                old_session_cookie = self._header(headers, "Set-Cookie").split(";", 1)[0]
                old_session_id = old_session_cookie.split("=", 1)[1]
                old_csrf_token = str(_RUNTIME_SESSIONS[old_session_id]["csrf_token"])
                self.assertIn("Secure", self._header(headers, "Set-Cookie"))
                self.assertIn("HttpOnly", self._header(headers, "Set-Cookie"))
                self.assertIn("SameSite=Lax", self._header(headers, "Set-Cookie"))

                _, refresh_headers, refresh_page = self._call(
                    path="/runtime/login",
                    query=urlencode({"runtime_db": str(db_path)}),
                    cookie=old_session_cookie,
                )
                refresh_token = re.search(r'name="csrf_token" value="([^"]+)"', refresh_page).group(1)
                refresh_cookie = self._header(refresh_headers, "Set-Cookie").split(";", 1)[0]
                _, rotated_headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=f"{old_session_cookie}; {refresh_cookie}",
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": refresh_token,
                        }
                    ),
                )
                rotated_session_cookie = self._header(rotated_headers, "Set-Cookie").split(";", 1)[0]
                rotated_session_id = rotated_session_cookie.split("=", 1)[1]
                self.assertNotIn(old_session_id, _RUNTIME_SESSIONS)
                self.assertNotEqual(old_session_id, rotated_session_id)

                rotated_csrf_token = str(_RUNTIME_SESSIONS[rotated_session_id]["csrf_token"])
                logout_status, logout_headers, _ = self._call(
                    path="/runtime/logout",
                    method="POST",
                    cookie=rotated_session_cookie,
                    body=urlencode(
                        {"runtime_db": str(db_path), "csrf_token": rotated_csrf_token}
                    ),
                )
                self.assertEqual(logout_status, "303 See Other")
                self.assertNotIn(rotated_session_id, _RUNTIME_SESSIONS)
                cleared = [value for key, value in logout_headers if key == "Set-Cookie"]
                self.assertTrue(cleared)
                self.assertTrue(all("Max-Age=0" in value and "SameSite=Lax" in value for value in cleared))
                old_status, _, old_body = self._call(
                    path="/runtime",
                    query=urlencode({"runtime_db": str(db_path)}),
                    cookie=rotated_session_cookie,
                )
                self.assertEqual(old_status, "200 OK")
                self.assertIn("需要先登录", old_body)
                self.assertNotEqual(old_csrf_token, rotated_csrf_token)

            _RUNTIME_SESSIONS.clear()
            with _LOGIN_CSRF_LOCK:
                _LOGIN_CSRF_CONTEXTS.clear()
            with patch.dict(
                "os.environ",
                {
                    "PRA_ALLOWED_DATA_DIRS": str(root),
                    "RUNTIME_ADMIN_PASSWORD": "secret",
                    "PRA_ENV": "development",
                    "PRA_COOKIE_SECURE": "false",
                },
                clear=False,
            ):
                login_token, preauth_cookie = self._login_context(db_path)
                _, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=preauth_cookie,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": login_token,
                        }
                    ),
                )
                session_cookie = self._header(headers, "Set-Cookie")
                self.assertNotIn("Secure", session_cookie)
                self.assertIn("HttpOnly", session_cookie)
                self.assertIn("SameSite=Lax", session_cookie)

    def test_login_rate_limiter_window_and_forwarded_headers(self) -> None:
        limiter = LoginRateLimiter()
        with patch.dict(
            "os.environ",
            {
                "RUNTIME_LOGIN_RATE_LIMIT_MAX_ATTEMPTS": "1",
                "RUNTIME_LOGIN_RATE_LIMIT_WINDOW_SECONDS": "10",
                "RUNTIME_LOGIN_RATE_LIMIT_COOLDOWN_SECONDS": "5",
            },
            clear=False,
        ):
            self.assertTrue(limiter.record_failure("Admin", "127.0.0.1", now=0.0))
            self.assertTrue(limiter.is_blocked("admin", "127.0.0.1", now=4.0))
            self.assertFalse(limiter.is_blocked("admin", "127.0.0.1", now=11.0))
            self.assertFalse(limiter.is_blocked("other", "127.0.0.1", now=4.0))

    def test_high_frequency_security_audit_logging_is_throttled(self) -> None:
        with patch("app.services.security._LOGGER.info") as logger:
            for _ in range(100):
                record_security_event(
                    "CSRF_REJECTED",
                    route="/runtime",
                    outcome="rejected",
                    reason_code="SESSION_CSRF_INVALID",
                    subject="admin",
                )
        self.assertLessEqual(logger.call_count, 5)
        self.assertLessEqual(len(list_security_audit_events()), 5)

    def test_tasks_automation_get_does_not_initialize_database(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "empty.sqlite3"
            db_path.touch()
            before = db_path.read_bytes()
            with patch.dict(
                "os.environ",
                {"PRA_ALLOWED_DATA_DIRS": str(root), "RUNTIME_ADMIN_PASSWORD": "secret"},
                clear=False,
            ):
                login_token, preauth_cookie = self._login_context(db_path)
                _, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
                    cookie=preauth_cookie,
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "username": "admin",
                            "password": "secret",
                            "csrf_token": login_token,
                        }
                    ),
                )
                session_cookie = self._header(headers, "Set-Cookie").split(";", 1)[0]
                status, _, _ = self._call(
                    path="/tasks",
                    query=urlencode({"runtime_db": str(db_path), "task_tab": "automation"}),
                    cookie=session_cookie,
                )
            self.assertEqual(status, "200 OK")
            self.assertEqual(db_path.read_bytes(), before)
            connection = sqlite3.connect(db_path)
            try:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(tables, [])
