"""运营 Web 的认证、Session 与 capability 边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hmac import compare_digest
from http.cookies import SimpleCookie
from secrets import token_urlsafe
from threading import Lock
from typing import Protocol


SESSION_COOKIE_NAME = "pra_operations_session"
SESSION_TTL_SECONDS = 3600
MAX_SESSIONS = 4096


class Capability(StrEnum):
    VIEW_TODAY = "VIEW_TODAY"
    VIEW_DATABASE = "VIEW_DATABASE"
    MANAGE_BUSINESS = "MANAGE_BUSINESS"
    VIEW_SYSTEM = "VIEW_SYSTEM"
    HANDLE_REVIEW = "HANDLE_REVIEW"
    SUBMIT_EXECUTION = "SUBMIT_EXECUTION"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


ALL_ADMIN_CAPABILITIES = frozenset(Capability)


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    capabilities: frozenset[Capability]


class AuthorizationBackend(Protocol):
    def allows(self, principal: Principal, capability: Capability) -> bool: ...


class PrincipalCapabilityBackend:
    """默认后端：权限只来自启动时构造的已认证主体。"""

    def allows(self, principal: Principal, capability: Capability) -> bool:
        return capability in principal.capabilities


class EnvironmentCredentialBackend:
    """固定读取启动配置，不在请求期间重新解释环境变量。"""

    def __init__(self, *, username: str, password: str) -> None:
        self._username = username
        self._password = password

    @property
    def configured(self) -> bool:
        return bool(self._password)

    def authenticate(self, username: str, password: str) -> Principal | None:
        if not self._password:
            return None
        if not compare_digest(username, self._username):
            return None
        if not compare_digest(password, self._password):
            return None
        return Principal(subject=self._username, capabilities=ALL_ADMIN_CAPABILITIES)


@dataclass(slots=True)
class SessionState:
    session_id: str
    csrf_token: str
    expires_at: datetime
    principal: Principal | None = None


class SessionManager:
    """有界内存 Session；不会写 Runtime DB、工作簿或队列。"""

    def __init__(self, *, cookie_secure: bool, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self._cookie_secure = cookie_secure
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, SessionState] = {}
        self._lock = Lock()

    def issue_preauth(
        self,
        *,
        replace_session_id: str | None = None,
    ) -> tuple[SessionState, str]:
        return self._issue(principal=None, replace_session_id=replace_session_id)

    def rotate_authenticated(
        self,
        *,
        principal: Principal,
        replace_session_id: str | None,
    ) -> tuple[SessionState, str]:
        return self._issue(principal=principal, replace_session_id=replace_session_id)

    def get(self, cookie_header: str) -> SessionState | None:
        session_id = self.session_id_from_cookie(cookie_header)
        if not session_id:
            return None
        now = datetime.now(timezone.utc)
        with self._lock:
            self._cleanup_locked(now)
            return self._sessions.get(session_id)

    def clear(self, cookie_header: str) -> str:
        session_id = self.session_id_from_cookie(cookie_header)
        if session_id:
            with self._lock:
                self._sessions.pop(session_id, None)
        return self._cookie_header("", max_age=0, expires="Thu, 01 Jan 1970 00:00:00 GMT")

    @staticmethod
    def session_id_from_cookie(cookie_header: str) -> str | None:
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel is not None else None

    @staticmethod
    def csrf_matches(session: SessionState | None, supplied: str) -> bool:
        return bool(session and supplied and compare_digest(session.csrf_token, supplied))

    def _issue(
        self,
        *,
        principal: Principal | None,
        replace_session_id: str | None,
    ) -> tuple[SessionState, str]:
        now = datetime.now(timezone.utc)
        state = SessionState(
            session_id=token_urlsafe(32),
            csrf_token=token_urlsafe(32),
            expires_at=now + timedelta(seconds=self._ttl_seconds),
            principal=principal,
        )
        with self._lock:
            self._cleanup_locked(now)
            if replace_session_id:
                self._sessions.pop(replace_session_id, None)
            while len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda key: self._sessions[key].expires_at)
                self._sessions.pop(oldest, None)
            self._sessions[state.session_id] = state
        return state, self._cookie_header(state.session_id, max_age=self._ttl_seconds)

    def _cleanup_locked(self, now: datetime) -> None:
        for session_id, state in list(self._sessions.items()):
            if state.expires_at <= now:
                self._sessions.pop(session_id, None)

    def _cookie_header(self, value: str, *, max_age: int, expires: str | None = None) -> str:
        cookie = SimpleCookie()
        cookie[SESSION_COOKIE_NAME] = value
        cookie[SESSION_COOKIE_NAME]["path"] = "/"
        cookie[SESSION_COOKIE_NAME]["httponly"] = True
        cookie[SESSION_COOKIE_NAME]["samesite"] = "Lax"
        cookie[SESSION_COOKIE_NAME]["max-age"] = str(max_age)
        if expires is not None:
            cookie[SESSION_COOKIE_NAME]["expires"] = expires
        if self._cookie_secure:
            cookie[SESSION_COOKIE_NAME]["secure"] = True
        return cookie.output(header="").strip()
