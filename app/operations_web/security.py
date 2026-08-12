"""运营 Web 的 HTTP 安全响应和错误边界。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import uuid4


LOGGER = logging.getLogger("app.operations_web")

SECURITY_HEADERS = (
    ("Content-Security-Policy", "default-src 'self'; style-src 'self'; img-src 'self'; "
     "script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; "
     "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
)


@dataclass(slots=True)
class Response:
    status: str
    body: bytes
    content_type: str
    headers: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def text(
        cls,
        status: str,
        body: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
        headers: list[tuple[str, str]] | None = None,
    ) -> "Response":
        return cls(status, body.encode("utf-8"), content_type, list(headers or ()))

    def wsgi(self, start_response):
        headers = [
            ("Content-Type", self.content_type),
            ("Content-Length", str(len(self.body))),
            *SECURITY_HEADERS,
            *self.headers,
        ]
        start_response(self.status, headers)
        return [self.body]


def internal_error_response() -> tuple[Response, str]:
    reference = uuid4().hex[:12]
    response = Response.text(
        "500 Internal Server Error",
        "系统暂时无法完成请求。请稍后重试，并向管理员提供错误编号：" + reference,
        headers=[("Cache-Control", "no-store")],
    )
    return response, reference
