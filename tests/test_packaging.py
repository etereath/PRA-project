from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.sync_shadowbot_test2 import sync
from scripts.verify_packaging import (
    _is_placeholder,
    _scan_path,
    _verify_sdist,
    _verify_wheel,
)
from scripts.verify_packaging import (
    main as verify_packaging_main,
)
from scripts.verify_shadowbot_deployment import verify_shadowbot_deployment

ROOT = Path(__file__).resolve().parents[1]


def _write_fake_wheel(path: Path, *, extra_member: str | None = None, extra_payload: bytes = b"") -> None:
    dist_info = "pra_mvp-0.1.0.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("app/__init__.py", b"\"\"\"core\"\"\"\n")
        archive.writestr("app/cli.py", b"def main(): pass\n")
        archive.writestr("app/runtime_schema.py", b"LATEST_RUNTIME_SCHEMA_VERSION = 5\n")
        archive.writestr("app/repositories/sqlite_runtime_repository.py", b"class Repo: pass\n")
        archive.writestr("app/services/runtime.py", b"class Runtime: pass\n")
        archive.writestr("app/operations_web/app.py", b"def create_application(): pass\n")
        archive.writestr("app/operations_web/templates/login.html", b"<!doctype html>\n")
        archive.writestr("app/operations_web/templates/page.html", b"<!doctype html>\n")
        archive.writestr("app/operations_web/templates/shell.html", b"<!doctype html>\n")
        archive.writestr("app/operations_web/templates/mobile_review.html", b"<!doctype html>\n")
        archive.writestr("app/operations_web/templates/mobile_review_shell.html", b"<!doctype html>\n")
        archive.writestr("app/operations_web/static/app.css", b":root {}\n")
        archive.writestr("app/operations_web/static/app.js", b'"use strict";\n')
        archive.writestr(f"{dist_info}/METADATA", b"Metadata-Version: 2.1\n")
        archive.writestr(f"{dist_info}/WHEEL", b"Wheel-Version: 1.0\n")
        archive.writestr(f"{dist_info}/RECORD", b"")
        archive.writestr(f"{dist_info}/entry_points.txt", b"")
        archive.writestr(f"{dist_info}/top_level.txt", b"app\n")
        if extra_member:
            archive.writestr(extra_member, extra_payload)


def _write_fake_sdist(
    path: Path,
    *,
    extra_member: str | None = None,
    extra_payload: bytes = b"",
    extra_directory: str | None = None,
) -> None:
    root = "pra_mvp-0.1.0"
    files = {
        f"{root}/MANIFEST.in": b"recursive-include app *.py\n",
        f"{root}/PKG-INFO": b"Metadata-Version: 2.1\n",
        f"{root}/README.md": b"# PRA\n",
        f"{root}/pyproject.toml": b"[project]\nname = 'pra-mvp'\n",
        f"{root}/setup.cfg": b"[egg_info]\n",
        f"{root}/app/__init__.py": b"\"\"\"core\"\"\"\n",
        f"{root}/pra_mvp.egg-info/SOURCES.txt": b"app/__init__.py\n",
    }
    if extra_member:
        files[f"{root}/{extra_member}"] = extra_payload
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        if extra_directory:
            info = tarfile.TarInfo(f"{root}/{extra_directory}")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)


class PackagingTests(unittest.TestCase):
    def test_setuptools_discovers_all_core_subpackages_only(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            config = tomllib.load(handle)
        find_config = config["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(find_config["include"], ["app*"])
        self.assertIn("tests*", find_config["exclude"])
        self.assertIn("shadowbot*", find_config["exclude"])

    def test_core_console_scripts_are_defined(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            config = tomllib.load(handle)
        scripts = config["project"]["scripts"]
        self.assertEqual(scripts["pra"], "app.cli:main")
        self.assertEqual(scripts["pra-mvp"], "app.cli:main")

    def test_operations_web_resources_are_declared_as_package_data(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            config = tomllib.load(handle)
        package_data = config["tool"]["setuptools"]["package-data"]
        self.assertEqual(
            package_data["app.operations_web"],
            ["templates/*.html", "static/*.css", "static/*.js"],
        )

    def test_shadowbot_deployment_inputs_are_separate_and_tracked(self) -> None:
        shadowbot_dir = ROOT / "shadowbot" / "test2"
        for name in (
            "module1.py",
            "shadowbot_credentials.py",
            "shadowbot_queue_worker.py",
            "vertical_slice_read_price.py",
            "shadowbot_worker_config.example.json",
        ):
            self.assertTrue((shadowbot_dir / name).is_file(), name)
        self.assertTrue((ROOT / "app" / "shadowbot_contract_primitives.py").is_file())
        self.assertTrue((ROOT / "app" / "emergency_offline_fence.py").is_file())
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("shadowbot/test2/shadowbot_worker_config.json", gitignore)
        self.assertIn("*.egg-info/", gitignore)

    def test_generated_egg_info_is_not_tracked(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "pra_mvp.egg-info"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, "")

    def test_strict_allowlist_rejects_extra_wheel_and_sdist_members(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wheel = root / "bad.whl"
            sdist = root / "bad.tar.gz"
            payload = b'{"account": "alice", "password": "hunter2"}\n'
            _write_fake_wheel(wheel, extra_member="evidence/session.json", extra_payload=payload)
            _write_fake_sdist(sdist, extra_member="customer_export.xlsx", extra_payload=payload)
            wheel_issues = _verify_wheel(wheel)
            sdist_issues = _verify_sdist(sdist)
            self.assertTrue(any("evidence/session.json" in issue for issue in wheel_issues))
            self.assertTrue(any("customer_export.xlsx" in issue for issue in sdist_issues))
            self.assertTrue(any("credential value" in issue for issue in wheel_issues))
            self.assertTrue(any("credential value" in issue for issue in sdist_issues))

            empty_wheel = root / "empty-directory.whl"
            empty_sdist = root / "empty-directory.tar.gz"
            _write_fake_wheel(empty_wheel, extra_member="evidence/")
            _write_fake_sdist(empty_sdist, extra_directory="evidence")
            self.assertTrue(any("evidence" in issue for issue in _verify_wheel(empty_wheel)))
            self.assertTrue(any("evidence" in issue for issue in _verify_sdist(empty_sdist)))

    def test_release_artifacts_reject_deleted_legacy_web_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wheel = root / "legacy-web.whl"
            sdist = root / "legacy-web.tar.gz"
            _write_fake_wheel(
                wheel,
                extra_member="app/web.py",
                extra_payload=b"def legacy_web(): pass\n",
            )
            _write_fake_sdist(
                sdist,
                extra_member="app/web_styles.py",
                extra_payload=b"def legacy_styles(): pass\n",
            )

            self.assertTrue(
                any("legacy Web member" in issue for issue in _verify_wheel(wheel))
            )
            self.assertTrue(
                any("legacy Web member" in issue for issue in _verify_sdist(sdist))
            )

    def test_secret_scan_distinguishes_safe_provider_fields_from_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            safe = root / "provider.py"
            safe.write_text(
                "CredentialBlob is a Windows API field name; password: str is runtime-only.\n",
                encoding="utf-8",
            )
            self.assertEqual(_scan_path(root), [])
            safe.write_text(
                "provider = {'CredentialBlob': None, 'password': '<runtime-only>'}\n",
                encoding="utf-8",
            )
            self.assertEqual(_scan_path(root), [])
            self.assertTrue(_is_placeholder("<runtime-only>"))
            for value in (
                "Bearer <runtime-only>",
                "EXAMPLE_real-token",
                "REPLACE_ME_real-token",
            ):
                with self.subTest(placeholder_value=value):
                    self.assertFalse(_is_placeholder(value))
            unsafe = root / "result.json"
            unsafe.write_text('{"account": "alice", "password": "hunter2"}\n', encoding="utf-8")
            issues = _scan_path(root)
            self.assertTrue(any("credential value" in issue for issue in issues))
            unsafe_py = root / "result.py"
            unsafe_py.write_text("result = {'account': 'alice', 'password': 'hunter2'}\n", encoding="utf-8")
            issues = _scan_path(root)
            self.assertTrue(any("result.py" in issue and "credential value" in issue for issue in issues))

    def test_multiple_dist_artifacts_require_explicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir)
            for name in ("one.whl", "two.whl", "one.tar.gz", "two.tar.gz"):
                (dist_dir / name).write_bytes(b"placeholder")
            with patch.object(sys, "argv", ["verify_packaging", "--dist-dir", str(dist_dir)]):
                self.assertEqual(verify_packaging_main(), 2)

    def test_shadowbot_sync_requires_real_host_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            with self.assertRaises(ValueError):
                sync(app_dir, check_only=True)

            (app_dir / "package.py").write_text("def selector(name): return name\n", encoding="utf-8")
            (app_dir / "selectorsV2.xml").write_text("<selectors />\n", encoding="utf-8")
            records = sync(app_dir, check_only=False)
            self.assertEqual(
                [record["status"] for record in records[:7]],
                ["SYNCED"] * 7,
            )
            self.assertEqual(records[7]["status"], "CREATED")
            self.assertEqual(verify_shadowbot_deployment(app_dir), [])

            (app_dir / "package.py").unlink()
            self.assertTrue(verify_shadowbot_deployment(app_dir))


if __name__ == "__main__":
    unittest.main()
