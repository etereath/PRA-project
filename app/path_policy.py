"""Fail-closed policy for paths selected by Web requests.

The Web layer may accept a file *candidate* from a request, but the request
cannot define the allowed roots.  Roots come only from deployment
configuration and are canonicalized once before candidate checks.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.exceptions import ValidationError


class PathPolicyError(ValidationError):
    """Safe, stable error raised when a path is outside the Web policy."""

    def __init__(self, code: str, message: str = "请求的文件路径不符合安全策略。") -> None:
        self.code = code
        self.public_message = f"{message}（{code}）。"
        super().__init__(self.public_message)


def _is_windows() -> bool:
    return os.name == "nt"


def _reject_windows_special_path(raw: str) -> None:
    if not _is_windows():
        return
    normalized = raw.replace("/", "\\")
    if normalized.startswith(("\\\\", "\\?\\", "\\.\\", "\\??\\")):
        raise PathPolicyError("PATH_SPECIAL_NAMESPACE", "不允许 UNC、设备或特殊命名空间路径")
    for component in re.split(r"[\\/]+", raw):
        if component in {"", ".", ".."}:
            continue
        if component.endswith((".", " ")):
            raise PathPolicyError("PATH_AMBIGUOUS_COMPONENT", "不允许带尾随点或空格的路径组件")


def _lexical_absolute(path: Path) -> Path:
    """Return an absolute path without resolving symlinks or junctions."""

    return Path(os.path.abspath(os.fspath(path)))


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class PathAccessPolicy:
    """Canonical allowed roots and path-level containment checks."""

    allowed_roots: tuple[Path, ...]

    @classmethod
    def from_environment(cls, *, default_root: Path) -> "PathAccessPolicy":
        configured = os.environ.get("PRA_ALLOWED_DATA_DIRS")
        if configured is None:
            raw_roots = [os.fspath(default_root)]
        else:
            # os.pathsep is ';' on Windows and ':' on POSIX.  An explicitly
            # empty item is invalid rather than silently broadening access.
            raw_roots = configured.split(os.pathsep)
            if not raw_roots or any(not item.strip() for item in raw_roots):
                raise PathPolicyError("PATH_ALLOWLIST_INVALID", "PRA_ALLOWED_DATA_DIRS 含有空目录")

        roots: list[Path] = []
        for raw_root in raw_roots:
            root_text = raw_root.strip()
            _reject_windows_special_path(root_text)
            root = Path(root_text)
            if not root.is_absolute():
                raise PathPolicyError("PATH_ALLOWLIST_RELATIVE", "PRA_ALLOWED_DATA_DIRS 只能包含绝对目录")
            try:
                resolved = root.resolve(strict=True)
            except (OSError, RuntimeError):
                raise PathPolicyError("PATH_ALLOWLIST_UNRESOLVABLE", "PRA_ALLOWED_DATA_DIRS 包含不可解析目录")
            if not resolved.is_dir():
                raise PathPolicyError("PATH_ALLOWLIST_NOT_DIRECTORY", "PRA_ALLOWED_DATA_DIRS 只能包含目录")
            if resolved not in roots:
                roots.append(resolved)

        if not roots:
            raise PathPolicyError("PATH_ALLOWLIST_EMPTY", "PRA_ALLOWED_DATA_DIRS 未提供有效目录")
        return cls(tuple(roots))

    def _check_lexical_root(self, candidate: Path) -> None:
        lexical = _lexical_absolute(candidate)
        if any(_is_within(lexical, root) for root in self.allowed_roots):
            return
        raise PathPolicyError("PATH_OUTSIDE_ALLOWLIST", "请求的路径不在允许目录内")

    def _check_resolved_root(self, candidate: Path) -> Path:
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            raise PathPolicyError("PATH_UNRESOLVABLE", "请求的路径无法安全规范化")
        if any(_is_within(resolved, root) for root in self.allowed_roots):
            return resolved
        raise PathPolicyError("PATH_SYMLINK_ESCAPE", "请求的路径解析后超出允许目录")

    def resolve(self, raw_path: str | os.PathLike[str], *, purpose: str, allow_create: bool = False) -> Path:
        """Resolve a request candidate and prove it remains within a root.

        ``allow_create`` permits a missing final file only when its nearest
        existing parent and the final resolved parent are both inside an
        allowlisted root.  The lexical check intentionally runs before
        symlink resolution so an outside symlink pointing back into an
        allowlisted root cannot be used as an alternate spelling.
        """

        del purpose  # The caller uses the purpose for audit context.
        if not isinstance(raw_path, (str, os.PathLike)):
            raise PathPolicyError("PATH_INVALID_TYPE", "请求的路径格式无效")
        raw = os.fspath(raw_path)
        if not isinstance(raw, str) or not raw.strip():
            raise PathPolicyError("PATH_EMPTY", "请求的路径不能为空")
        _reject_windows_special_path(raw)
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise PathPolicyError("PATH_RELATIVE", "请求的路径必须是绝对路径")

        # Reject path traversal and alternate spellings before resolving links.
        self._check_lexical_root(candidate)

        if candidate.exists():
            resolved = self._check_resolved_root(candidate)
            try:
                strict_resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                raise PathPolicyError("PATH_UNRESOLVABLE", "请求的路径无法安全规范化")
            if strict_resolved != resolved:
                raise PathPolicyError("PATH_UNRESOLVABLE", "请求的路径无法安全规范化")
            return strict_resolved

        if not allow_create:
            # Missing files are still safe to identify when their parent is
            # allowlisted; the business layer decides whether they may exist.
            # This supports deterministic, non-leaking not-found errors.
            pass

        existing_parent = candidate.parent
        while not existing_parent.exists() and existing_parent != existing_parent.parent:
            existing_parent = existing_parent.parent
        if not existing_parent.exists():
            raise PathPolicyError("PATH_PARENT_MISSING", "请求路径的父目录不存在")

        # Check both the lexical parent and its resolved target.  This catches
        # junction/symlink escapes for a not-yet-created final file.
        self._check_lexical_root(existing_parent)
        resolved_parent = self._check_resolved_root(existing_parent)
        resolved_candidate = self._check_resolved_root(candidate)
        if not _is_within(resolved_candidate.parent, resolved_parent) and not any(
            _is_within(resolved_candidate.parent, root) for root in self.allowed_roots
        ):
            raise PathPolicyError("PATH_PARENT_ESCAPE", "请求路径的最终父目录超出允许目录")
        return resolved_candidate
