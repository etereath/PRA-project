from __future__ import annotations

import os
import sys


PLACEHOLDER_MARKERS = (
    "replace-with",
    "your-fixed-domain",
    "replace-me",
    "请换成",
    "你的",
)


def main() -> int:
    issues: list[str] = []
    warnings: list[str] = []

    channel = _env("DEFAULT_NOTIFICATION_CHANNEL", "mock").lower()
    dev_mode = _env("DEV_MODE", "false").lower()

    _require("REVIEW_TOKEN_SECRET", issues)
    _require("RUNTIME_ADMIN_PASSWORD", issues)
    _warn_if_missing("RUNTIME_ADMIN_USER", warnings, default="admin")

    secret = _env("REVIEW_TOKEN_SECRET")
    if secret and len(secret) < 32:
        issues.append("REVIEW_TOKEN_SECRET should be at least 32 characters.")

    admin_password = _env("RUNTIME_ADMIN_PASSWORD")
    if admin_password and len(admin_password) < 12:
        warnings.append("RUNTIME_ADMIN_PASSWORD is shorter than 12 characters.")

    if dev_mode == "true":
        warnings.append("DEV_MODE=true is for local debugging only.")
    elif dev_mode != "false":
        warnings.append("DEV_MODE should usually be 'false' or 'true'.")

    if channel == "feishu":
        _require("FEISHU_WEBHOOK_URL", issues)
        _require("MOBILE_REVIEW_BASE_URL", issues)
        message_type = _env("FEISHU_MESSAGE_TYPE", "post").lower()
        if message_type not in {"post", "text"}:
            issues.append("FEISHU_MESSAGE_TYPE must be 'post' or 'text'.")
        _warn_if_missing("FEISHU_WEBHOOK_SECRET", warnings, default="optional if Feishu signing is disabled")
        timeout = _env("FEISHU_WEBHOOK_TIMEOUT_SECONDS", "5")
        try:
            if float(timeout) <= 0:
                issues.append("FEISHU_WEBHOOK_TIMEOUT_SECONDS must be greater than 0.")
        except ValueError:
            issues.append("FEISHU_WEBHOOK_TIMEOUT_SECONDS must be a number.")
    elif channel == "mock":
        warnings.append("DEFAULT_NOTIFICATION_CHANNEL=mock will not send real notifications.")
    else:
        issues.append(f"Unsupported DEFAULT_NOTIFICATION_CHANNEL: {channel}")

    for name in _known_secret_names():
        value = _env(name)
        if value and _looks_placeholder(value):
            issues.append(f"{name} still looks like a placeholder.")

    _print_result(issues, warnings)
    return 1 if issues else 0


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _require(name: str, issues: list[str]) -> None:
    if not _env(name):
        issues.append(f"{name} is required.")


def _warn_if_missing(name: str, warnings: list[str], *, default: str) -> None:
    if not _env(name):
        warnings.append(f"{name} is not set; default/meaning: {default}.")


def _known_secret_names() -> tuple[str, ...]:
    return (
        "REVIEW_TOKEN_SECRET",
        "RUNTIME_ADMIN_PASSWORD",
        "FEISHU_WEBHOOK_URL",
        "FEISHU_WEBHOOK_SECRET",
        "FEISHU_MESSAGE_TYPE",
        "MOBILE_REVIEW_BASE_URL",
    )


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker.lower() in lowered for marker in PLACEHOLDER_MARKERS)


def _print_result(issues: list[str], warnings: list[str]) -> None:
    if warnings:
        print("Runtime environment warnings:")
        for warning in warnings:
            print(f"- {warning}")
        print()

    if issues:
        print("Runtime environment check failed:")
        for issue in issues:
            print(f"- {issue}")
        return

    print("Runtime environment check passed.")


if __name__ == "__main__":
    sys.exit(main())
