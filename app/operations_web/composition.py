"""运营 Web 的单一 Composition Root。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.operations_web.auth import (
    AuthorizationBackend,
    EnvironmentCredentialBackend,
    PrincipalCapabilityBackend,
    SessionManager,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import DEFAULT_RUNTIME_DB


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class OperationsWebConfigurationError(ValueError):
    """启动配置不满足运营 Web 安全约束。"""


@dataclass(frozen=True, slots=True)
class OperationsWebPaths:
    runtime_db: Path
    products_workbook: Path
    price_rules_workbook: Path
    listing_rules_workbook: Path
    queue_root: Path
    platform_mappings_workbook: Path | None = None
    shadowbot_identity_mapping: Path | None = None


@dataclass(frozen=True, slots=True)
class OperationsWebSettings:
    environment: str
    public_scheme: str
    cookie_secure: bool
    admin_username: str
    admin_password: str = field(repr=False)
    paths: OperationsWebPaths
    shadowbot_applet_uri: str = ""

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: Path = PROJECT_ROOT,
    ) -> "OperationsWebSettings":
        source = MappingProxyType(dict(os.environ if environ is None else environ))
        environment = source.get("PRA_ENV", "").strip().lower()
        if environment not in {"development", "production"}:
            raise OperationsWebConfigurationError(
                "PRA_ENV 必须显式设置为 development 或 production。"
            )

        public_scheme = source.get("PRA_WEB_PUBLIC_SCHEME", "").strip().lower()
        if public_scheme not in {"http", "https"}:
            raise OperationsWebConfigurationError(
                "PRA_WEB_PUBLIC_SCHEME 必须显式设置为 http 或 https。"
            )

        cookie_secure = _parse_required_bool(source, "PRA_COOKIE_SECURE")
        if environment == "development" and (public_scheme != "http" or cookie_secure):
            raise OperationsWebConfigurationError(
                "开发环境必须使用 HTTP 和非 Secure Cookie：请设置 "
                "PRA_ENV=development、PRA_WEB_PUBLIC_SCHEME=http、PRA_COOKIE_SECURE=false。"
            )
        if environment == "production" and (public_scheme != "https" or not cookie_secure):
            raise OperationsWebConfigurationError(
                "生产环境必须使用 HTTPS 和 Secure Cookie：请设置 "
                "PRA_ENV=production、PRA_WEB_PUBLIC_SCHEME=https、PRA_COOKIE_SECURE=true。"
            )

        root = project_root.resolve(strict=False)
        paths = OperationsWebPaths(
            runtime_db=_fixed_path(source, "PRA_RUNTIME_DB", DEFAULT_RUNTIME_DB, root),
            products_workbook=_fixed_path(
                source, "PRA_PRODUCTS_WORKBOOK", Path("data/samples/products.xlsx"), root
            ),
            price_rules_workbook=_fixed_path(
                source, "PRA_PRICE_RULES_WORKBOOK", Path("data/samples/price_rules.xlsx"), root
            ),
            listing_rules_workbook=_fixed_path(
                source, "PRA_LISTING_RULES_WORKBOOK", Path("data/samples/listing_rules.xlsx"), root
            ),
            queue_root=_fixed_path(
                source, "SHADOWBOT_QUEUE_DIR", Path("data/runtime/shadowbot_queue"), root
            ),
            platform_mappings_workbook=_fixed_path(
                source,
                "PRA_PLATFORM_MAPPINGS_WORKBOOK",
                Path("data/samples/platform_mappings.xlsx"),
                root,
            ),
            shadowbot_identity_mapping=_fixed_path(
                source,
                "PRA_SHADOWBOT_IDENTITY_MAPPING",
                Path("shadowbot/test2/product_identity_mapping.json"),
                root,
            ),
        )
        return cls(
            environment=environment,
            public_scheme=public_scheme,
            cookie_secure=cookie_secure,
            admin_username=source.get("RUNTIME_ADMIN_USER", "admin").strip() or "admin",
            admin_password=source.get("RUNTIME_ADMIN_PASSWORD", ""),
            paths=paths,
            shadowbot_applet_uri=source.get("SHADOWBOT_APPLET_URI", "").strip(),
        )


@dataclass(frozen=True, slots=True)
class OperationsWebContainer:
    settings: OperationsWebSettings
    runtime_repository: SQLiteRuntimeRepository
    credentials: EnvironmentCredentialBackend
    authorization: AuthorizationBackend
    sessions: SessionManager


def build_container(
    settings: OperationsWebSettings | None = None,
    *,
    authorization: AuthorizationBackend | None = None,
) -> OperationsWebContainer:
    fixed = settings or OperationsWebSettings.from_environment()
    return OperationsWebContainer(
        settings=fixed,
        runtime_repository=SQLiteRuntimeRepository(fixed.paths.runtime_db),
        credentials=EnvironmentCredentialBackend(
            username=fixed.admin_username,
            password=fixed.admin_password,
        ),
        authorization=authorization or PrincipalCapabilityBackend(),
        sessions=SessionManager(cookie_secure=fixed.cookie_secure),
    )


def _parse_required_bool(source: Mapping[str, str], name: str) -> bool:
    raw = source.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise OperationsWebConfigurationError(f"{name} 必须显式设置为 true 或 false。")


def _fixed_path(
    source: Mapping[str, str],
    name: str,
    default: Path,
    project_root: Path,
) -> Path:
    configured = source.get(name, "").strip()
    candidate = Path(configured).expanduser() if configured else Path(default)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve(strict=False)
