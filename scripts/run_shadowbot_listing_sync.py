from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository  # noqa: E402
from app.services.shadowbot_executor import ShadowBotFileQueueRunner  # noqa: E402
from app.services.shadowbot_listing_sync import (  # noqa: E402
    prepare_listing_sync_batch,
    publish_listing_sync_batch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and publish one Task 13 independent SYNC_STATUS request."
    )
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--platform-name", default="蚂蚁花团供应商")
    parser.add_argument(
        "--applet-uri",
        default=os.environ.get("SHADOWBOT_APPLET_URI", ""),
    )
    parser.add_argument(
        "--execution-profile",
        choices=("development", "production"),
        default="production",
    )
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--execution-attempt-id", default="")
    parser.add_argument("--capture-evidence", action="store_true")
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = build_parser().parse_args()
    if not str(args.applet_uri or "").strip():
        raise ValueError("发布 SYNC_STATUS 时必须提供 --applet-uri。")
    repository = SQLiteRuntimeRepository(args.runtime_db)
    health = repository.check_schema_health()
    if not health.ok or health.actual_version != 13:
        raise RuntimeError("Runtime Schema 必须已健康升级到 v13。")
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    batch_id = args.batch_id or "BATCH-SYNC-" + suffix
    manifest = prepare_listing_sync_batch(
        repository,
        batch_id=batch_id,
        platform_name=args.platform_name,
        mapping_path=args.mapping,
        execution_profile=args.execution_profile,
    )
    output: dict[str, object] = {
        "status": "PREPARED",
        "batch_id": batch_id,
        "manifest": manifest,
    }
    runner = ShadowBotFileQueueRunner(args.queue_dir)
    request, started = publish_listing_sync_batch(
        repository,
        runner,
        manifest=manifest,
        execution_profile=args.execution_profile,
        applet_uri=args.applet_uri,
        execution_attempt_id=args.execution_attempt_id or None,
        capture_evidence=args.capture_evidence,
    )
    output.update(
        {
            "status": "QUEUED",
            "execution_attempt_id": request["execution_attempt_id"],
            "queue_request_path": started.raw_output.get(
                "queue_request_path", ""
            ),
            "request_file_sha256": started.raw_output.get(
                "request_file_sha256", ""
            ),
        }
    )
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
