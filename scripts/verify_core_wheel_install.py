from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the final core wheel outside the repository and verify imports, CLI, schema, and health"
    )
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    return parser


def _select_wheel(explicit_wheel: Path | None, dist_dir: Path) -> Path:
    if explicit_wheel is not None:
        wheel = explicit_wheel.resolve()
        if not wheel.is_file():
            raise ValueError("explicit wheel does not exist")
        return wheel
    wheels = sorted(dist_dir.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected exactly one wheel, found {len(wheels)}")
    return wheels[0]


def _venv_executable(venv_dir: Path, name: str) -> Path:
    if os.name == "nt":
        suffix = ".exe" if name != "python" else ".exe"
        return venv_dir / "Scripts" / f"{name}{suffix}"
    return venv_dir / "bin" / name


def _sanitize(text: str, isolated_root: Path) -> str:
    sanitized = re.sub(re.escape(str(isolated_root)), "<isolated-root>", text, flags=re.IGNORECASE)
    sanitized = re.sub(re.escape(str(ROOT)), "<repository-root>", sanitized, flags=re.IGNORECASE)
    return sanitized


def _run_checked(
    label: str,
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    isolated_root: Path,
) -> str:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    safe_output = _sanitize(output, isolated_root)
    if result.returncode != 0:
        if safe_output:
            print(safe_output, file=sys.stderr)
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")
    print(f"{label}=PASS")
    if safe_output:
        print(safe_output)
    return output


def verify_core_wheel(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pra-core-wheel-") as temp_dir:
        isolated_root = Path(temp_dir).resolve()
        venv_dir = isolated_root / "venv"
        runtime_db = isolated_root / "runtime.sqlite3"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        python = _venv_executable(venv_dir, "python")
        cli = _venv_executable(venv_dir, "pra-mvp")
        if not python.is_file():
            raise RuntimeError("isolated virtual environment did not create the expected Python executable")

        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.update({"PRA_ENV": "test", "PYTHONUTF8": "1"})

        _run_checked(
            "wheel_install",
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", str(wheel)],
            cwd=isolated_root,
            env=env,
            isolated_root=isolated_root,
        )
        if not cli.is_file():
            raise RuntimeError("wheel installation did not create the pra-mvp executable")
        _run_checked(
            "core_imports",
            [
                str(python),
                "-c",
                (
                    "import app, app.repositories.sqlite_runtime_repository, "
                    "app.services.runtime, app.runtime_schema"
                ),
            ],
            cwd=isolated_root,
            env=env,
            isolated_root=isolated_root,
        )
        _run_checked(
            "repository_path_isolation",
            [
                str(python),
                "-c",
                (
                    "import pathlib, sys; "
                    "repo = pathlib.Path(sys.argv[1]).resolve(); "
                    "paths = [pathlib.Path(p or '.').resolve() for p in sys.path]; "
                    "assert pathlib.Path.cwd().resolve() != repo; "
                    "assert repo not in paths"
                ),
                str(ROOT),
            ],
            cwd=isolated_root,
            env=env,
            isolated_root=isolated_root,
        )
        _run_checked(
            "cli_help",
            [str(cli), "--help"],
            cwd=isolated_root,
            env=env,
            isolated_root=isolated_root,
        )
        init_output = _run_checked(
            "schema_init",
            [str(cli), "init-runtime-db", "--runtime-db", str(runtime_db)],
            cwd=isolated_root,
            env=env,
            isolated_root=isolated_root,
        )
        expected_versions = "schema_versions=" + str(
            list(range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1))
        )
        if expected_versions not in init_output:
            raise RuntimeError(
                "schema initialization did not report the exact latest migration sequence"
            )
        health_output = _run_checked(
            "schema_health",
            [str(cli), "health", "--runtime-db", str(runtime_db)],
            cwd=isolated_root,
            env=env,
            isolated_root=isolated_root,
        )
        if "ok=True" not in health_output or expected_versions not in health_output:
            raise RuntimeError(
                "health output did not confirm exact latest Runtime Schema health"
            )


def main() -> int:
    args = _build_parser().parse_args()
    try:
        wheel = _select_wheel(args.wheel, args.dist_dir)
        verify_core_wheel(wheel)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"core_wheel_install=FAIL reason={exc}", file=sys.stderr)
        return 1
    print("core_wheel_install=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
