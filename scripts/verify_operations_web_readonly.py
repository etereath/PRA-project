"""Verify new Operations Web GET routes against one fixed Runtime DB without writes."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.operations_web.app import create_application  # noqa: E402
from app.operations_web.auth import ALL_ADMIN_CAPABILITIES, Principal  # noqa: E402
from app.operations_web.composition import (  # noqa: E402
    OperationsWebPaths,
    OperationsWebSettings,
    build_container,
)


GET_ROUTES = (
    "/today",
    "/database",
    "/database/project",
    "/database/quality",
    "/management",
    "/system",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Operations Web GET routes with a fixed read-only Runtime DB."
    )
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--products", type=Path, required=True)
    parser.add_argument("--price-rules", type=Path, required=True)
    parser.add_argument("--listing-rules", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--platform-mappings", type=Path)
    parser.add_argument("--identity-mapping", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--automation-heartbeat", type=Path)
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_database(path: Path) -> dict[str, dict[str, object] | None]:
    targets = (
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
        Path(str(path) + "-journal"),
    )
    snapshot: dict[str, dict[str, object] | None] = {}
    for index, target in enumerate(targets):
        label = ("database", "wal", "shm", "journal")[index]
        if not target.is_file():
            snapshot[label] = None
            continue
        stat = target.stat()
        snapshot[label] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": _sha256(target),
        }
    return snapshot


def _same_content(
    left: dict[str, object] | None,
    right: dict[str, object] | None,
) -> bool:
    if left is None or right is None:
        return left is right
    return left.get("size") == right.get("size") and left.get("sha256") == right.get(
        "sha256"
    )


def _call_get(application, *, path: str, cookie: str) -> str:
    captured: dict[str, object] = {}
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": "0",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_COOKIE": cookie,
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(application(environ, start_response))
    body.decode("utf-8")
    return str(captured.get("status") or "")


def main() -> int:
    args = build_parser().parse_args()
    runtime_db = args.runtime_db.resolve(strict=True)
    fixed_inputs = (args.products, args.price_rules, args.listing_rules)
    for item in fixed_inputs:
        item.resolve(strict=True)
    queue_dir = args.queue_dir.resolve(strict=True)
    initial = _snapshot_database(runtime_db)

    settings = OperationsWebSettings(
        environment="development",
        public_scheme="http",
        cookie_secure=False,
        admin_username="readonly-acceptance",
        admin_password="<runtime-only>",
        paths=OperationsWebPaths(
            runtime_db=runtime_db,
            products_workbook=args.products.resolve(strict=True),
            price_rules_workbook=args.price_rules.resolve(strict=True),
            listing_rules_workbook=args.listing_rules.resolve(strict=True),
            queue_root=queue_dir,
            platform_mappings_workbook=(
                args.platform_mappings.resolve(strict=True)
                if args.platform_mappings is not None
                else None
            ),
            shadowbot_identity_mapping=(
                args.identity_mapping.resolve(strict=True)
                if args.identity_mapping is not None
                else None
            ),
            backup_root=(
                args.backup_dir.resolve(strict=False)
                if args.backup_dir is not None
                else None
            ),
            automation_heartbeat=(
                args.automation_heartbeat.resolve(strict=False)
                if args.automation_heartbeat is not None
                else None
            ),
        ),
    )
    container = build_container(settings)
    application = create_application(container)
    _, cookie = container.sessions.rotate_authenticated(
        principal=Principal(
            "readonly-acceptance",
            ALL_ADMIN_CAPABILITIES,
        ),
        replace_session_id=None,
    )
    warmup_status = _call_get(application, path="/health", cookie="")
    warmed = _snapshot_database(runtime_db)
    statuses = {
        route: _call_get(application, path=route, cookie=cookie)
        for route in GET_ROUTES
    }
    after = _snapshot_database(runtime_db)
    database_unchanged = initial["database"] == after["database"]
    wal_content_unchanged = _same_content(initial["wal"], after["wal"])
    sidecar_content_unchanged_after_warmup = all(
        _same_content(warmed[name], after[name])
        for name in ("wal", "shm", "journal")
    )
    passed = (
        database_unchanged
        and wal_content_unchanged
        and sidecar_content_unchanged_after_warmup
        and warmup_status in {"200 OK", "503 Service Unavailable"}
        and all(status == "200 OK" for status in statuses.values())
    )
    print(
        json.dumps(
            {
                "schema_version": "operations-web-readonly-acceptance-1.0",
                "passed": passed,
                "database_unchanged": database_unchanged,
                "wal_content_unchanged": wal_content_unchanged,
                "sqlite_sidecar_warmup_changed": initial != warmed,
                "sidecar_content_unchanged_after_warmup": (
                    sidecar_content_unchanged_after_warmup
                ),
                "warmup_status": warmup_status,
                "runtime_health_available": warmup_status == "200 OK",
                "route_statuses": statuses,
                "platform_write_performed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
