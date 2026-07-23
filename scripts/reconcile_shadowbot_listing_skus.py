from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.exceptions import ValidationError
from app.listing_identity import listing_identity_key
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_commit_batch import load_identity_mapping


def build_reconciliation_plan(
    repository: SQLiteRuntimeRepository,
    *,
    mapping_path: Path,
) -> list[dict[str, str]]:
    mapping = load_identity_mapping(mapping_path)
    raw_mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
    platform_name = str(raw_mapping.get("platform_name") or "").strip()
    changes: list[dict[str, str]] = []
    with closing(repository.connect_read()) as connection:
        for internal_sku, identity in mapping.items():
            key = listing_identity_key(
                platform_name,
                identity["expected_product_name"],
                identity["expected_grade"],
            )
            rows = connection.execute(
                """
                SELECT listing_status_id, internal_sku, platform_name, variety, grade
                FROM listing_status
                WHERE platform_name = ? AND variety = ? AND grade = ?
                """,
                key,
            ).fetchall()
            if len(rows) != 1:
                raise ValidationError(
                    f"SKU 未找到唯一平台状态身份：{internal_sku}，匹配数={len(rows)}"
                )
            row = rows[0]
            current_sku = str(row["internal_sku"] or "").strip().upper()
            if current_sku == internal_sku:
                continue
            conflicting = connection.execute(
                """
                SELECT listing_status_id FROM listing_status
                WHERE internal_sku = ? AND listing_status_id <> ?
                """,
                (internal_sku, row["listing_status_id"]),
            ).fetchall()
            if conflicting:
                raise ValidationError(f"正式 SKU 已被其他状态行占用：{internal_sku}")
            changes.append(
                {
                    "listing_status_id": str(row["listing_status_id"]),
                    "platform_name": str(row["platform_name"]),
                    "variety": str(row["variety"]),
                    "grade": str(row["grade"]),
                    "old_internal_sku": current_sku,
                    "new_internal_sku": internal_sku,
                }
            )
    return changes


def apply_reconciliation_plan(
    repository: SQLiteRuntimeRepository,
    changes: list[dict[str, str]],
) -> None:
    with closing(repository.connect_write()) as connection, connection:
        for change in changes:
            cursor = connection.execute(
                """
                UPDATE listing_status SET internal_sku = ?
                WHERE listing_status_id = ? AND UPPER(TRIM(internal_sku)) = ?
                """,
                (
                    change["new_internal_sku"],
                    change["listing_status_id"],
                    change["old_internal_sku"],
                ),
            )
            if cursor.rowcount != 1:
                raise ValidationError(
                    f"状态行在迁移期间发生变化：{change['listing_status_id']}"
                )


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="按可信页面身份统一 ShadowBot 状态表内部 SKU")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    repository = SQLiteRuntimeRepository(args.db)
    changes = build_reconciliation_plan(repository, mapping_path=args.mapping)
    if args.apply:
        apply_reconciliation_plan(repository, changes)
    print(
        json.dumps(
            {
                "mode": "APPLY" if args.apply else "DRY_RUN",
                "change_count": len(changes),
                "changes": changes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
