from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "pra_mvp"
REQUIRED_WHEEL_MEMBERS = {
    "app/__init__.py",
    "app/cli.py",
    "app/runtime_schema.py",
    "app/repositories/sqlite_runtime_repository.py",
    "app/services/runtime.py",
    "app/operations_web/app.py",
    "app/operations_web/templates/login.html",
    "app/operations_web/templates/page.html",
    "app/operations_web/templates/shell.html",
    "app/operations_web/templates/mobile_review.html",
    "app/operations_web/templates/mobile_review_shell.html",
    "app/operations_web/static/app.css",
    "app/operations_web/static/app.js",
}
FORBIDDEN_LEGACY_WEB_MEMBERS = {
    "app/web.py",
    "app/web_styles.py",
}
ALLOWED_WHEEL_METADATA = frozenset(
    {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt"}
)
REQUIRED_WHEEL_METADATA = frozenset({"METADATA", "WHEEL", "RECORD"})
ALLOWED_SDIST_ROOT_FILES = frozenset(
    {"MANIFEST.in", "PKG-INFO", "README.md", "pyproject.toml", "setup.cfg"}
)
ALLOWED_SDIST_EGG_INFO_FILES = frozenset({"SOURCES.txt"})
OPERATIONS_WEB_RESOURCE_PATTERN = re.compile(
    r"^app/operations_web/(?:templates/[^/]+\.html|static/[^/]+\.(?:css|js))$"
)
SKIPPED_SCAN_DIRECTORIES = frozenset({".git", ".hg", ".svn", "__pycache__"})
SENSITIVE_MARKER_PATTERNS = (
    re.compile(rb"(?:ghp_|github_pat_|AKIA[0-9A-Z]{16})"),
    re.compile(rb"BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY"),
    re.compile(rb"mobile_review_url[^\r\n]{0,200}token=", re.IGNORECASE),
)
CREDENTIAL_VALUE_PATTERN = re.compile(
    rb"(?ix)"
    rb"(?P<key>password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    rb"username|user[_-]?name|account|login_credential_target|"
    rb"credential[_-]?blob|credentialblob)"
    rb"\s*(?:[\"']?\s*)[:=]\s*[\"'](?P<value>[^\"']+)[\"']"
)
PLACEHOLDER_VALUES = frozenset(
    {
        "CHANGE_ME",
        "DUMMY",
        "EXAMPLE",
        "PLACEHOLDER",
        "REDACTED",
        "REPLACE_ME",
        "REPLACE-ME",
        "YOUR_VALUE",
        "YOUR-VALUE",
    }
)
PLACEHOLDER_MARKER_RE = re.compile(r"<[A-Za-z0-9][A-Za-z0-9_.:-]*>")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_placeholder(value: str) -> bool:
    normalized = value.strip()
    upper = normalized.upper()
    return (
        not normalized
        or upper in PLACEHOLDER_VALUES
        or bool(PLACEHOLDER_MARKER_RE.fullmatch(normalized))
    )


def _scan_payload(label: str, payload: bytes) -> list[str]:
    issues: list[str] = []
    for pattern in SENSITIVE_MARKER_PATTERNS:
        if pattern.search(payload):
            issues.append(f"{label}: sensitive marker")
    if label.lower().endswith(".py"):
        try:
            tree = ast.parse(payload.decode("utf-8"), filename=label)
        except (SyntaxError, UnicodeDecodeError):
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for key_node, value_node in zip(node.keys, node.values):
                        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                            continue
                        if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
                            continue
                        normalized_name = key_node.value.lower().replace("-", "_")
                        if normalized_name in {
                            "password",
                            "passwd",
                            "secret",
                            "token",
                            "api_key",
                            "access_key",
                            "username",
                            "user_name",
                            "account",
                            "login_credential_target",
                            "credential_blob",
                            "credentialblob",
                        } and not _is_placeholder(value_node.value):
                            issues.append(
                                f"{label}: non-placeholder credential value for field {key_node.value}"
                            )
                    continue
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                else:
                    continue
                value_node = node.value
                if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
                    continue
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    normalized_name = target.id.lower().replace("-", "_")
                    if normalized_name in {
                        "password",
                        "passwd",
                        "secret",
                        "token",
                        "api_key",
                        "access_key",
                        "username",
                        "user_name",
                        "account",
                        "login_credential_target",
                        "credential_blob",
                        "credentialblob",
                    } and not _is_placeholder(value_node.value):
                        issues.append(f"{label}: non-placeholder credential value for {target.id}")
        return issues
    for match in CREDENTIAL_VALUE_PATTERN.finditer(payload):
        value = match.group("value").decode("utf-8", errors="replace")
        if not _is_placeholder(value):
            key = match.group("key").decode("ascii", errors="replace")
            issues.append(f"{label}: non-placeholder credential value for {key}")
    return issues


def _iter_scan_files(path: Path):
    if path.is_file():
        yield path
        return
    for candidate in path.rglob("*"):
        if any(part in SKIPPED_SCAN_DIRECTORIES for part in candidate.parts):
            continue
        if candidate.is_file():
            yield candidate


def _scan_path(path: Path) -> list[str]:
    if not path.exists():
        return [f"scan path does not exist: {path}"]
    issues: list[str] = []
    for candidate in _iter_scan_files(path):
        try:
            payload = candidate.read_bytes()
        except OSError as exc:
            issues.append(f"unable to read scan path {candidate}: {exc}")
            continue
        issues.extend(_scan_payload(str(candidate), payload))
    return issues


def _relative_sdist_member(name: str, root: str) -> str:
    normalized = name.replace("\\", "/").lstrip("./")
    if normalized == root:
        return ""
    prefix = f"{root}/"
    return normalized[len(prefix) :] if normalized.startswith(prefix) else normalized


def _verify_wheel(path: Path) -> list[str]:
    issues: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        dist_info_roots = {
            name.split("/", 1)[0]
            for name in names
            if "/" in name and name.split("/", 1)[0].endswith(".dist-info")
        }
        if len(dist_info_roots) != 1:
            issues.append(f"expected one dist-info root, found {sorted(dist_info_roots)}")
            dist_info_root = None
        else:
            dist_info_root = next(iter(dist_info_roots))
            if not re.fullmatch(rf"{re.escape(PACKAGE_NAME)}-[^/]+\.dist-info", dist_info_root):
                issues.append(f"unexpected dist-info root: {dist_info_root}")

        for name in names:
            if name.endswith("/"):
                allowed_directory = name == "app/" or name.startswith("app/")
                if dist_info_root is not None:
                    allowed_directory = allowed_directory or name == f"{dist_info_root}/"
                if not allowed_directory:
                    issues.append(f"wheel member outside strict allowlist: {name}")
                continue
            if name.startswith("app/"):
                if not name.endswith(".py") and not OPERATIONS_WEB_RESOURCE_PATTERN.fullmatch(name):
                    issues.append(f"wheel app member is not declared Python package data: {name}")
                continue
            if dist_info_root is not None and name.startswith(f"{dist_info_root}/"):
                metadata_name = name.split("/", 1)[1]
                if metadata_name not in ALLOWED_WHEEL_METADATA:
                    issues.append(f"wheel metadata outside strict allowlist: {name}")
                continue
            issues.append(f"wheel member outside strict allowlist: {name}")

            issues.extend(_scan_payload(f"wheel:{name}", archive.read(name)))

        missing = sorted(REQUIRED_WHEEL_MEMBERS - set(names))
        issues.extend(f"missing required core file: {name}" for name in missing)
        present_legacy_web = sorted(FORBIDDEN_LEGACY_WEB_MEMBERS & set(names))
        issues.extend(
            f"legacy Web member must not be packaged: {name}"
            for name in present_legacy_web
        )
        if dist_info_root is not None:
            metadata_members = {
                name.split("/", 1)[1]
                for name in names
                if name.startswith(f"{dist_info_root}/") and "/" in name
            }
            missing_metadata = sorted(REQUIRED_WHEEL_METADATA - metadata_members)
            issues.extend(f"missing required wheel metadata: {name}" for name in missing_metadata)
        for name in names:
            if not name.endswith("/"):
                issues.extend(_scan_payload(f"wheel:{name}", archive.read(name)))
    return sorted(set(issues))


def _verify_sdist(path: Path) -> list[str]:
    issues: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        roots = {name.replace("\\", "/").split("/", 1)[0] for name in names if name}
        if len(roots) != 1:
            issues.append(f"expected one sdist root, found {sorted(roots)}")
            return issues
        root = next(iter(roots))
        if not root.startswith(f"{PACKAGE_NAME}-"):
            issues.append(f"unexpected sdist root: {root}")
        for name in names:
            relative = _relative_sdist_member(name, root)
            member = archive.getmember(name)
            if not relative:
                continue
            if member.isdir():
                allowed_directory = (
                    relative == "app"
                    or relative.startswith("app/")
                    or relative == f"{PACKAGE_NAME}.egg-info"
                )
                if not allowed_directory:
                    issues.append(f"sdist directory outside strict allowlist: {name}")
                continue
            if relative in ALLOWED_SDIST_ROOT_FILES:
                pass
            elif relative.startswith("app/"):
                if not relative.endswith(".py") and not OPERATIONS_WEB_RESOURCE_PATTERN.fullmatch(relative):
                    issues.append(f"sdist app member is not declared Python package data: {name}")
                if relative in FORBIDDEN_LEGACY_WEB_MEMBERS:
                    issues.append(f"legacy Web member must not be packaged: {name}")
            elif relative.startswith(f"{PACKAGE_NAME}.egg-info/"):
                metadata_name = relative.split("/", 1)[1]
                if metadata_name not in ALLOWED_SDIST_EGG_INFO_FILES:
                    issues.append(f"sdist metadata outside strict allowlist: {name}")
            else:
                issues.append(f"sdist member outside strict allowlist: {name}")
            extracted = archive.extractfile(member)
            if extracted is not None:
                issues.extend(_scan_payload(f"sdist:{name}", extracted.read()))
    return sorted(set(issues))


def _select_artifact(
    explicit_path: Path | None,
    dist_dir: Path,
    pattern: str,
    label: str,
) -> tuple[Path | None, list[str]]:
    if explicit_path is not None:
        if not explicit_path.is_file():
            return None, [f"{label} does not exist: {explicit_path}"]
        return explicit_path, []
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        return None, [f"expected exactly one {label} under {dist_dir}, found {len(matches)}"]
    return matches[0], []


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit core wheel, sdist, and deployment boundaries")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--scan-dir",
        action="append",
        type=Path,
        default=[],
        help="Recursively scan source, deployment, result, evidence, or log directories",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    wheel, wheel_selection_issues = _select_artifact(args.wheel, args.dist_dir, "*.whl", "wheel")
    sdist, sdist_selection_issues = _select_artifact(args.sdist, args.dist_dir, "*.tar.gz", "sdist")
    selection_issues = [*wheel_selection_issues, *sdist_selection_issues]
    if selection_issues:
        for issue in selection_issues:
            print(f"- {issue}", file=sys.stderr)
        return 2
    assert wheel is not None
    assert sdist is not None

    wheel_issues = _verify_wheel(wheel)
    sdist_issues = _verify_sdist(sdist)
    scan_issues = [issue for path in args.scan_dir for issue in _scan_path(path)]
    print(f"wheel={wheel} sha256={_sha256(wheel)}")
    print(f"sdist={sdist} sha256={_sha256(sdist)}")
    print(f"wheel_boundary={'PASS' if not wheel_issues else 'FAIL'}")
    print(f"sdist_boundary={'PASS' if not sdist_issues else 'FAIL'}")
    print(f"secret_scan={'PASS' if not scan_issues else 'FAIL'}")
    for issue in [*wheel_issues, *sdist_issues, *scan_issues]:
        print(f"- {issue}")
    return 1 if wheel_issues or sdist_issues or scan_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
