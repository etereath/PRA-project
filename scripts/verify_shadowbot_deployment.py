from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sync_shadowbot_test2 import validate_shadowbot_app_dir


DEPLOYED_SOURCE_FILES = (
    "module1.py",
    "shadowbot_credentials.py",
    "shadowbot_contract_primitives.py",
    "shadowbot_queue_worker.py",
    "vertical_slice_read_price.py",
)
REQUIRED_DEPLOYED_FILES = (*DEPLOYED_SOURCE_FILES, "shadowbot_worker_config.json")


def verify_shadowbot_deployment(app_dir: Path) -> list[str]:
    issues: list[str] = []
    try:
        validate_shadowbot_app_dir(app_dir)
    except (FileNotFoundError, ValueError) as exc:
        return [str(exc)]

    missing = [name for name in REQUIRED_DEPLOYED_FILES if not (app_dir / name).is_file()]
    issues.extend(f"missing deployed file: {app_dir / name}" for name in missing)
    for name in DEPLOYED_SOURCE_FILES:
        path = app_dir / name
        if not path.is_file():
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            issues.append(f"invalid deployed Python source {path}: {exc}")
    return issues


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a ShadowBot host application and synchronized source")
    parser.add_argument("--app-dir", type=Path, help="Existing ShadowBot xbot_robot application directory")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    app_dir = args.app_dir or (Path(os.environ["SHADOWBOT_APP_DIR"]) if os.environ.get("SHADOWBOT_APP_DIR") else None)
    if app_dir is None:
        raise SystemExit("--app-dir or SHADOWBOT_APP_DIR is required")
    issues = verify_shadowbot_deployment(app_dir)
    if issues:
        for issue in issues:
            print(f"- {issue}")
        print("shadowbot_deployment=FAIL")
        return 1
    print(f"shadowbot_deployment=PASS app_dir={app_dir}")
    print("runtime_boundary=ShadowBot host xbot/package.py/selectorsV2.xml; core wheel not required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
