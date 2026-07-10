from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.runtime import DEFAULT_RUNTIME_DB


SENSITIVE_ENV_NAMES = {
    "YINGDAO_ACCESS_KEY_ID",
    "YINGDAO_ACCESS_KEY_SECRET",
}

YINGDAO_REQUIRED_ENV_NAMES = [
    "YINGDAO_API_BASE_URL",
    "YINGDAO_ACCESS_KEY_ID",
    "YINGDAO_ACCESS_KEY_SECRET",
    "YINGDAO_ROBOT_UUID",
    "YINGDAO_ACCOUNT_NAME",
    "YINGDAO_ROBOT_CLIENT_GROUP_UUID",
]


def _env_status(name: str) -> dict[str, object]:
    value = os.environ.get(name, "")
    status: dict[str, object] = {
        "name": name,
        "configured": bool(value),
    }
    if value:
        status["length"] = len(value)
        if name not in SENSITIVE_ENV_NAMES:
            status["value"] = value
    return status


def build_readiness_report(runtime_db: Path) -> dict[str, object]:
    runner_type = os.environ.get("SHADOWBOT_RUNNER_TYPE", "filedrop").strip().lower() or "filedrop"

    checks: list[dict[str, object]] = []
    checks.append(
        {
            "name": "runtime_db_exists",
            "ok": runtime_db.exists(),
            "path": str(runtime_db),
        }
    )
    env: list[dict[str, object]] = [
        _env_status("SHADOWBOT_RUNNER_TYPE"),
    ]

    if runner_type in {"filequeue", "file_queue", "filedrop", "file_drop"}:
        request_dir = Path(
            os.environ.get("SHADOWBOT_QUEUE_DIR")
            or os.environ.get("SHADOWBOT_REQUEST_DIR")
            or "data/runtime/shadowbot_queue"
        )
        checks.append(
            {
                "name": "filequeue_dir_parent_exists",
                "ok": request_dir.parent.exists(),
                "path": str(request_dir),
            }
        )
        env.extend(
            [
                _env_status("SHADOWBOT_QUEUE_DIR"),
                _env_status("SHADOWBOT_EVIDENCE_DIR"),
                _env_status("SHADOWBOT_RUNNER_COMMAND"),
            ]
        )
    elif runner_type == "yingdao_openapi":
        env.extend(_env_status(name) for name in YINGDAO_REQUIRED_ENV_NAMES)
        missing = [name for name in YINGDAO_REQUIRED_ENV_NAMES if not os.environ.get(name)]
        checks.append(
            {
                "name": "yingdao_required_env_configured",
                "ok": not missing,
                "missing": missing,
            }
        )
    else:
        checks.append(
            {
                "name": "runner_type_supported",
                "ok": False,
                "value": runner_type,
            }
        )

    ready = all(bool(check.get("ok")) for check in checks)
    next_steps: list[str] = []
    if runner_type == "yingdao_openapi":
        next_steps.append("Run: python scripts/run_shadowbot_executor.py check-yingdao-app-params")
    else:
        next_steps.append("Start test2 manually, then run: python scripts/run_shadowbot_queue_services.py")

    return {
        "ok": ready,
        "runner_type": runner_type,
        "runtime_db": str(runtime_db),
        "checks": checks,
        "environment": env,
        "next_steps": next_steps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check ShadowBot real-run readiness without starting ShadowBot.")
    parser.add_argument("--runtime-db", default=str(DEFAULT_RUNTIME_DB))
    parser.add_argument("--strict", action="store_true", help="Exit with 1 when readiness checks fail.")
    args = parser.parse_args(argv)

    report = build_readiness_report(Path(args.runtime_db))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
