from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.shadowbot_worker_health import (
    build_shadowbot_worker_health_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check ShadowBot Worker heartbeat health"
    )
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument(
        "--expected-status", choices=("RUNNING", "STOPPED"), default="RUNNING"
    )
    parser.add_argument("--max-age-seconds", type=float, default=15.0)
    parser.add_argument("--strict", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_health_report(
        args.queue_dir,
        expected_status=args.expected_status,
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] or not args.strict else 1


def build_health_report(
    queue_dir: Path,
    *,
    expected_status: str = "RUNNING",
    max_age_seconds: float = 15.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    return build_shadowbot_worker_health_report(
        queue_dir,
        expected_status=expected_status,
        max_age_seconds=max_age_seconds,
        now=now,
    )


if __name__ == "__main__":
    raise SystemExit(main())
