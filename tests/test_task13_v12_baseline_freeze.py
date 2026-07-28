from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from scripts.freeze_task13_v12_baseline import freeze_baseline


class Task13V12BaselineFreezeTests(unittest.TestCase):
    def test_historical_v12_freeze_rejects_current_v13_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime_db = root / "runtime.sqlite3"
            SQLiteRuntimeRepository(runtime_db).init_schema()

            output_dir = root / "baseline"
            with self.assertRaisesRegex(RuntimeError, "found v13"):
                freeze_baseline(runtime_db, output_dir)


if __name__ == "__main__":
    unittest.main()
