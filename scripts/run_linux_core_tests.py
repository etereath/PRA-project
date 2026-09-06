from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
SHADOWBOT_TEST_PREFIX = "test_shadowbot_"


def select_linux_core_tests() -> tuple[list[Path], list[Path]]:
    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    excluded = [path for path in test_files if path.name.startswith(SHADOWBOT_TEST_PREFIX)]
    selected = [path for path in test_files if path not in excluded]
    if not selected:
        raise RuntimeError("Linux Core selected no test files")
    if not excluded:
        raise RuntimeError("Linux Core found no Windows/ShadowBot test files to exclude")
    return selected, excluded


def main() -> int:
    try:
        selected, excluded = select_linux_core_tests()
    except RuntimeError as exc:
        print(f"linux_core_test_selection=FAIL reason={exc}", file=sys.stderr)
        return 2

    print(f"linux_core_selected_files={len(selected)}", flush=True)
    print(f"linux_core_excluded_files={len(excluded)}", flush=True)
    for path in excluded:
        print(f"linux_core_excluded={path.relative_to(ROOT).as_posix()}", flush=True)

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests",
        *(f"--ignore={path.relative_to(ROOT).as_posix()}" for path in excluded),
        "-k",
        "not shadowbot",
    ]
    result = subprocess.run(command, cwd=ROOT, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
