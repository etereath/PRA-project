from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_price_batch_report import (
    build_price_batch_acceptance,
    write_price_batch_reports,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Task 12 JSON acceptance and human-readable Markdown reports."
    )
    parser.add_argument("--db", required=True, type=Path, help="Runtime SQLite database")
    parser.add_argument("--batch-id", required=True, help="Task 12 batch identifier")
    parser.add_argument("--queue-dir", type=Path, help="Queue root for archive verification")
    parser.add_argument("--json-output", required=True, type=Path, help="UTF-8 JSON output")
    parser.add_argument("--markdown-output", required=True, type=Path, help="UTF-8 Markdown output")
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    repository = SQLiteRuntimeRepository(args.db)
    payload = build_price_batch_acceptance(
        repository,
        args.batch_id,
        queue_dir=args.queue_dir,
    )
    json_path, markdown_path = write_price_batch_reports(
        payload,
        json_path=args.json_output,
        markdown_path=args.markdown_output,
    )
    print(
        json.dumps(
            {
                "overall_status": payload["overall_status"],
                "json_path": str(json_path),
                "markdown_path": str(markdown_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["validation"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
