"""Offline control surface for Runtime Product/Mapping import and authority switch."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.master_data_repository import RuntimeMasterDataRepository  # noqa: E402
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository  # noqa: E402
from app.services.master_data_management import (  # noqa: E402
    CUTOVER_CONFIRMATION,
    ROLLBACK_CONFIRMATION,
    MasterDataManagementService,
)
from app.services.runtime_master_data import RuntimeMasterDataProvider  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview/import Product and Mapping data, then explicitly switch authority."
    )
    parser.add_argument("--runtime-db", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview")
    _add_import_sources(preview)

    import_command = subparsers.add_parser("import")
    _add_import_sources(import_command)
    _add_actor_and_key(import_command)
    import_command.add_argument("--expected-request-sha256", required=True)

    compare = subparsers.add_parser("shadow-compare")
    _add_import_sources(compare)

    cutover = subparsers.add_parser("cutover")
    _add_actor_and_key(cutover)
    cutover.add_argument("--expected-product-snapshot-sha256", required=True)
    cutover.add_argument("--expected-mapping-snapshot-sha256", required=True)
    cutover.add_argument("--confirmation", required=True, help=CUTOVER_CONFIRMATION)

    rollback = subparsers.add_parser("rollback")
    _add_actor_and_key(rollback)
    rollback.add_argument("--confirmation", required=True, help=ROLLBACK_CONFIRMATION)

    subparsers.add_parser("status")
    locator = subparsers.add_parser("derive-locator")
    locator.add_argument("--account-id", required=True)
    locator.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = SQLiteRuntimeRepository(args.runtime_db)
    repository.init_schema()
    service = MasterDataManagementService(repository)

    if args.command == "preview":
        result = service.preview_workbook_import(
            products_workbook=args.products,
            platform_mappings_workbook=args.platform_mappings,
            account_id_by_platform=_accounts(args.account),
        )
    elif args.command == "import":
        result = service.import_from_workbooks(
            products_workbook=args.products,
            platform_mappings_workbook=args.platform_mappings,
            account_id_by_platform=_accounts(args.account),
            expected_request_sha256=args.expected_request_sha256,
            actor=args.actor,
            idempotency_key=args.idempotency_key,
        )
    elif args.command == "shadow-compare":
        result = service.shadow_compare_workbooks(
            products_workbook=args.products,
            platform_mappings_workbook=args.platform_mappings,
            account_id_by_platform=_accounts(args.account),
        )
    elif args.command == "cutover":
        result = service.activate_runtime_authority(
            expected_product_snapshot_sha256=args.expected_product_snapshot_sha256,
            expected_mapping_snapshot_sha256=args.expected_mapping_snapshot_sha256,
            actor=args.actor,
            idempotency_key=args.idempotency_key,
            confirmation=args.confirmation,
        )
    elif args.command == "rollback":
        result = service.rollback_to_workbook_authority(
            actor=args.actor,
            idempotency_key=args.idempotency_key,
            confirmation=args.confirmation,
        )
    elif args.command == "derive-locator":
        output = RuntimeMasterDataProvider(
            repository,
            configured_account_id=args.account_id,
        ).ensure_shadowbot_locator(args.output)
        result = {"status": "DERIVED", "output": str(output.resolve())}
    else:
        result = RuntimeMasterDataRepository(repository).authority_state()
    print(json.dumps(asdict(result) if hasattr(result, "__dataclass_fields__") else result,
                     ensure_ascii=False, sort_keys=True))
    return 0


def _add_import_sources(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--products", type=Path, required=True)
    parser.add_argument("--platform-mappings", type=Path, required=True)
    parser.add_argument(
        "--account",
        action="append",
        required=True,
        metavar="PLATFORM=ACCOUNT_ID",
    )


def _add_actor_and_key(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", required=True)
    parser.add_argument("--idempotency-key", required=True)


def _accounts(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        platform, separator, account_id = value.partition("=")
        if not separator or not platform.strip() or not account_id.strip():
            raise ValueError("--account must use PLATFORM=ACCOUNT_ID")
        if platform.strip() in result:
            raise ValueError("--account contains a duplicate platform")
        result[platform.strip()] = account_id.strip()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
