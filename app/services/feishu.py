from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping


def build_feishu_signature(timestamp: str, secret: str) -> str:
    """Build the Feishu custom-bot signature using the official algorithm."""

    signing_key = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(signing_key, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def is_feishu_success_response(response: Mapping[str, object]) -> bool:
    """Accept only an explicit Feishu success acknowledgement."""

    codes = [response[key] for key in ("code", "StatusCode") if key in response]
    return bool(codes) and all(code in (0, "0") for code in codes)


def has_feishu_confirmation_code(response: Mapping[str, object]) -> bool:
    """Return whether the provider supplied any authoritative result code."""

    return any(key in response for key in ("code", "StatusCode"))
