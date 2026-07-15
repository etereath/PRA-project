from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("shadowbot/test2/shadowbot_worker_config.json", gitignore)


if __name__ == "__main__":
    unittest.main()
