"""Small, bounded security primitives for the Web session boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone


_LOGGER = logging.getLogger("app.security")
_AUDIT_EVENTS: deque[dict[str, object]] = deque(maxlen=1000)
_AUDIT_LOCK = threading.Lock()


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def record_security_event(
    event_type: str,
    *,
    route: str,
    outcome: str,
    reason_code: str,
    subject: str = "",
) -> None:
    """Record only bounded, non-secret security metadata."""

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "subject_hash": _stable_hash(subject) if subject else None,
        "route": route,
        "outcome": outcome,
        "reason_code": reason_code,
    }
    with _AUDIT_LOCK:
        _AUDIT_EVENTS.append(event)
    _LOGGER.info("security_event=%s", json.dumps(event, ensure_ascii=False, sort_keys=True))


def list_security_audit_events() -> list[dict[str, object]]:
    with _AUDIT_LOCK:
        return [dict(event) for event in _AUDIT_EVENTS]


def clear_security_audit_events() -> None:
    with _AUDIT_LOCK:
        _AUDIT_EVENTS.clear()


@dataclass(slots=True)
class _RateLimitEntry:
    failures: deque[float]
    blocked_until: float = 0.0
    touched_at: float = 0.0


class LoginRateLimiter:
    """Bounded in-memory failure limiter keyed by account and TCP peer."""

    def __init__(self) -> None:
        self._entries: dict[str, _RateLimitEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    def _config(self) -> tuple[int, float, float, int]:
        max_attempts = self._positive_int(
            "RUNTIME_LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 5, minimum=1, maximum=100
        )
        window = float(
            self._positive_int("RUNTIME_LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300, minimum=1, maximum=86400)
        )
        cooldown = float(
            self._positive_int("RUNTIME_LOGIN_RATE_LIMIT_COOLDOWN_SECONDS", 900, minimum=1, maximum=86400)
        )
        max_keys = self._positive_int("RUNTIME_LOGIN_RATE_LIMIT_MAX_KEYS", 4096, minimum=16, maximum=100000)
        return max_attempts, window, cooldown, max_keys

    def _key(self, username: str, remote_addr: str) -> str:
        normalized_user = " ".join(username.strip().casefold().split())
        normalized_peer = remote_addr.strip()
        return f"{_stable_hash(normalized_user)}:{_stable_hash(normalized_peer)}"

    def is_blocked(self, username: str, remote_addr: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        max_attempts, window, _, _ = self._config()
        key = self._key(username, remote_addr)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            entry.touched_at = current
            entry.failures = deque(value for value in entry.failures if current - value < window)
            if current >= entry.blocked_until:
                entry.blocked_until = 0.0
            return bool(entry.blocked_until > current or len(entry.failures) >= max_attempts)

    def record_failure(self, username: str, remote_addr: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        max_attempts, window, cooldown, max_keys = self._config()
        key = self._key(username, remote_addr)
        with self._lock:
            if key not in self._entries and len(self._entries) >= max_keys:
                oldest_key = min(self._entries, key=lambda item: self._entries[item].touched_at)
                self._entries.pop(oldest_key, None)
            entry = self._entries.setdefault(key, _RateLimitEntry(deque()))
            entry.touched_at = current
            entry.failures = deque(value for value in entry.failures if current - value < window)
            entry.failures.append(current)
            if len(entry.failures) >= max_attempts:
                entry.blocked_until = max(entry.blocked_until, current + cooldown)
            return entry.blocked_until > current

    def record_success(self, username: str, remote_addr: str) -> None:
        key = self._key(username, remote_addr)
        with self._lock:
            # Clear only the exact account/peer bucket.  A success cannot
            # erase another principal's failures.
            self._entries.pop(key, None)


LOGIN_RATE_LIMITER = LoginRateLimiter()
