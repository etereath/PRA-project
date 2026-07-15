from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATH_PREFIXES = (
    "shadowbot/",
    "tests/",
    "data/",
    "doc/",
    "docs/",
    "scripts/",
    ".git/",
    "pra_mvp.egg-info/",
)
FORBIDDEN_FILE_NAMES = {
    "shadowbot_worker_config.json",
    "local_env.ps1",
}
WHEEL_SECRET_PATTERNS = (
    re.compile(rb"CredentialBlob", re.IGNORECASE),
    re.compile(rb"BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY"),
    re.compile(rb"(?:ghp_|github_pat_|AKIA[0-9A-Z]{16})"),
    re.compile(rb"mobile_review_url[^\r\n]{0,200}token=", re.IGNORECASE),
)


def _relative_member(name: str) -> str:
    normalized = name.replace("\\", "/").lstrip("./")
    parts = normalized.split("/", 1)
    if len(parts) == 2 and parts[0].startswith("pra_mvp-"):
        return parts[1]
    return normalized


def _path_issues(names: list[str]) -> list[str]:
    issues: list[str] = []
    for name in names:
        relative = _relative_member(name)
        if any(relative.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
            issues.append(f"forbidden path: {name}")
        if Path(relative).name in FORBIDDEN_FILE_NAMES:
            issues.append(f"forbidden file: {name}")
        if relative.lower().endswith((".sqlite3", ".db", ".pyc")):
            issues.append(f"runtime/cache artifact: {name}")
    return issues


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_wheel(path: Path) -> list[str]:
    issues: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        issues.extend(_path_issues(names))
        required = {
            "app/__init__.py",
            "app/cli.py",
            "app/runtime_schema.py",
            "app/repositories/sqlite_runtime_repository.py",
            "app/services/runtime.py",
        }
        missing = sorted(required - set(names))
        issues.extend(f"missing required core file: {name}" for name in missing)
        dist_info = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(dist_info) != 1:
            issues.append(f"expected one dist-info/METADATA, found {len(dist_info)}")
        for name in names:
            if name.endswith("/"):
                continue
            payload = archive.read(name)
            for pattern in WHEEL_SECRET_PATTERNS:
                if pattern.search(payload):
                    issues.append(f"sensitive marker in wheel member: {name}")
                    break
    return issues


def _verify_sdist(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        names = [
            name
            for name in archive.getnames()
            if "/pra_mvp.egg-info/" not in name.replace("\\", "/")
        ]
        return _path_issues(names)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit core wheel and sdist boundaries")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    wheel = args.wheel or next(iter(sorted(args.dist_dir.glob("*.whl"))), None)
    sdist = args.sdist or next(iter(sorted(args.dist_dir.glob("*.tar.gz"))), None)
    if wheel is None or sdist is None:
        print("expected one wheel and one sdist under dist/", file=sys.stderr)
        return 2

    wheel_issues = _verify_wheel(wheel)
    sdist_issues = _verify_sdist(sdist)
    print(f"wheel={wheel} sha256={_sha256(wheel)}")
    print(f"sdist={sdist} sha256={_sha256(sdist)}")
    print(f"wheel_boundary={'PASS' if not wheel_issues else 'FAIL'}")
    print(f"sdist_boundary={'PASS' if not sdist_issues else 'FAIL'}")
    for issue in [*wheel_issues, *sdist_issues]:
        print(f"- {issue}")
    return 1 if wheel_issues or sdist_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
