from __future__ import annotations

import argparse
import json
from pathlib import Path

from verify_task13_unknown_reconcile_evidence import (
    validate_unknown_reconcile_bundle,
)


DEFAULT_BUNDLE = Path(
    "docs/evidence/task13/"
    "UNKNOWN-NOT-APPLIED-AISHA-B-60-Z-20260727"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    report = validate_unknown_reconcile_bundle(
        args.bundle,
        expected_final_operation_result="NOT_APPLIED",
    )
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
