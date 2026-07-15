from __future__ import annotations

import io
import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from unittest.mock import patch

from app.path_policy import PathAccessPolicy, PathPolicyError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.security import clear_security_audit_events, list_security_audit_events
from app.web import _RUNTIME_SESSIONS, application


class PathPolicyTests(unittest.TestCase):
    def test_candidate_requires_absolute_path_and_stays_inside_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            root.mkdir()
            (root / "safe.txt").write_text("ok", encoding="utf-8")
            policy = PathAccessPolicy((root.resolve(),))

            resolved = policy.resolve(root / "nested" / ".." / "safe.txt", purpose="test")
            self.assertEqual(resolved, (root / "safe.txt").resolve())

            with self.assertRaisesRegex(PathPolicyError, "PATH_RELATIVE"):
                policy.resolve("safe.txt", purpose="test")
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
        clear_security_audit_events()

    def _call(self, *, path: str, method: str = "GET", query: str = "", body: str = "", cookie: str = "", remote: str = "127.0.0.1"):
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
            "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "REMOTE_ADDR": remote,
            "wsgi.input": io.BytesIO(payload),
        }
        if cookie:
            environ["HTTP_COOKIE"] = cookie
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
                status, _, login_page = self._call(path="/runtime/login")
                self.assertEqual(status, "200 OK")
                csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
                status, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
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
                _, _, login_page = self._call(path="/runtime/login")
                login_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
                _, headers, _ = self._call(
                    path="/runtime/login",
                    method="POST",
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
                for _ in range(2):
                    _, _, login_page = self._call(path="/runtime/login", remote="198.51.100.10")
                    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
                    status, _, body = self._call(
                        path="/runtime/login",
                        method="POST",
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
