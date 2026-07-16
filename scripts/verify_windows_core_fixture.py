from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "scripts" / "sync_shadowbot_test2.py"
DEPLOYMENT_SCRIPT = ROOT / "scripts" / "verify_shadowbot_deployment.py"


def _sanitize(text: str, fixture_root: Path) -> str:
    sanitized = re.sub(re.escape(str(fixture_root)), "<fixture-root>", text, flags=re.IGNORECASE)
    return re.sub(re.escape(str(ROOT)), "<repository-root>", sanitized, flags=re.IGNORECASE)


def _run_expected(
    label: str,
    command: Sequence[str],
    *,
    expected_exit: int,
    fixture_root: Path,
) -> None:
    env = os.environ.copy()
    env.pop("SHADOWBOT_APP_DIR", None)
    env.update({"PRA_ENV": "test", "PYTHONUTF8": "1"})
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != expected_exit:
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        if output:
            print(_sanitize(output, fixture_root), file=sys.stderr)
        raise RuntimeError(f"{label}: expected exit {expected_exit}, got {result.returncode}")
    print(f"{label}=PASS expected_exit={expected_exit}")


def verify_windows_fixture() -> None:
    if os.name != "nt":
        raise RuntimeError("Windows Core fixture validation must run on Windows")
    with tempfile.TemporaryDirectory(prefix="pra-windows-core-") as temp_dir:
        fixture_root = Path(temp_dir).resolve()
        valid_app = fixture_root / "中文路径宿主"
        valid_app.mkdir()
        (valid_app / "package.py").write_text("def selector(name):\n    return name\n", encoding="utf-8")
        (valid_app / "selectorsV2.xml").write_text("<selectors />\n", encoding="utf-8")

        sync_command = [sys.executable, str(SYNC_SCRIPT), "--app-dir", str(valid_app)]
        check_command = [*sync_command, "--check"]
        deploy_command = [sys.executable, str(DEPLOYMENT_SCRIPT), "--app-dir", str(valid_app)]

        _run_expected("shadowbot_fixture_sync", sync_command, expected_exit=0, fixture_root=fixture_root)
        _run_expected("shadowbot_fixture_check", check_command, expected_exit=0, fixture_root=fixture_root)
        _run_expected("shadowbot_fixture_deployment", deploy_command, expected_exit=0, fixture_root=fixture_root)

        with (valid_app / "module1.py").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n# intentional CI hash drift\n")
        _run_expected("shadowbot_hash_drift", check_command, expected_exit=1, fixture_root=fixture_root)
        _run_expected("shadowbot_fixture_resync", sync_command, expected_exit=0, fixture_root=fixture_root)

        (valid_app / "package.py").unlink()
        _run_expected("shadowbot_missing_host_file", deploy_command, expected_exit=1, fixture_root=fixture_root)

        empty_app = fixture_root / "empty-host"
        empty_app.mkdir()
        _run_expected(
            "shadowbot_empty_host",
            [sys.executable, str(DEPLOYMENT_SCRIPT), "--app-dir", str(empty_app)],
            expected_exit=1,
            fixture_root=fixture_root,
        )


def main() -> int:
    try:
        verify_windows_fixture()
    except (OSError, RuntimeError) as exc:
        print(f"windows_core_fixture=FAIL reason={exc}", file=sys.stderr)
        return 1
    print("windows_core_fixture=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
