from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from scripts.release_backup import (
    ROOT,
    ReleaseBackupError,
    _database_snapshot,
    build_release_manifest,
    create_backup,
    migrate_runtime_database,
    restore_backup,
    validate_nonsecret_config,
    verify_backup,
)


class ReleaseBackupTests(unittest.TestCase):
    def _create_runtime_db(self, root: Path) -> Path:
        path = root / "runtime" / "pra_runtime.sqlite3"
        repository = SQLiteRuntimeRepository(path)
        repository.init_schema()
        return path

    def _create_legacy_v5_db(self, root: Path) -> Path:
        path = self._create_runtime_db(root)
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE notification_delivery_attempts")
            connection.execute("DROP TABLE notification_outbox")
            connection.execute(
                "DELETE FROM runtime_schema_migrations WHERE schema_version = 6"
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.commit()
        finally:
            connection.close()
        return path

    def test_release_manifest_records_hashes_and_names_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel = root / "pra_mvp-0.1.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel bytes")
            manifest = build_release_manifest(
                git_root=ROOT,
                wheel_path=wheel,
                config_names=["CUSTOM_SETTING"],
                git_commit="eecd284c51e50f106a75c5504bab7f43afa9d632",
            )
            self.assertEqual(manifest["git_commit"], "eecd284c51e50f106a75c5504bab7f43afa9d632")
            self.assertEqual(manifest["runtime_schema_version"], 6)
            self.assertIn("CUSTOM_SETTING", manifest["configuration_item_names"])
            self.assertIn("YINGDAO_ACCESS_KEY_SECRET", manifest["configuration_item_names"])
            self.assertFalse(manifest["secret_values_included"])
            self.assertNotIn("wheel bytes", json.dumps(manifest))

    def test_backup_verify_restore_and_force_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_db = self._create_runtime_db(root)
            wheel = root / "pra_mvp.whl"
            wheel.write_bytes(b"wheel")
            input_file = root / "products.xlsx"
            input_file.write_bytes(b"xlsx placeholder")
            config_file = root / "runtime-config.json"
            config_file.write_text(
                '{"SHADOWBOT_QUEUE_DIR": "D:\\\\PRA_Runtime\\\\shadowbot_queue", '
                '"YINGDAO_ACCESS_KEY_SECRET": ""}\n',
                encoding="utf-8",
            )
            backup = create_backup(
                runtime_db=runtime_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                input_specs=[("products.xlsx", input_file)],
                config_specs=[("runtime-config.json", config_file)],
                git_root=ROOT,
                backup_id="valid-backup",
            )
            manifest = verify_backup(backup)
            self.assertEqual(manifest["backup_id"], "valid-backup")
            self.assertTrue(manifest["database_validation"]["backup_snapshot"]["schema_health"]["ok"])
            latest = json.loads((root / "backups" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["backup_id"], "valid-backup")
            self.assertFalse(any(path.name.startswith(".valid-backup.tmp-") for path in (root / "backups").iterdir()))

            restored_db = root / "restored" / "pra_runtime.sqlite3"
            result = restore_backup(
                backup_path=backup,
                runtime_db=restored_db,
                input_dir=root / "restored" / "inputs",
                config_dir=root / "restored" / "config",
            )
            self.assertEqual(result["backup_id"], "valid-backup")
            self.assertTrue(_database_snapshot(restored_db)["ok"])
            self.assertEqual(
                (root / "restored" / "inputs" / "products.xlsx").read_bytes(),
                input_file.read_bytes(),
            )
            self.assertEqual(
                (root / "restored" / "config" / "runtime-config.json").read_bytes(),
                config_file.read_bytes(),
            )
            with self.assertRaises(ReleaseBackupError):
                restore_backup(backup_path=backup, runtime_db=restored_db)
            restore_backup(backup_path=backup, runtime_db=restored_db, force=True)

    def test_v5_migration_is_copy_only_and_unlocks_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_db = self._create_legacy_v5_db(root)
            wheel = root / "pra_mvp.whl"
            wheel.write_bytes(b"wheel")
            before = _database_snapshot(legacy_db)
            self.assertEqual(before["schema_health"]["actual_version"], 5)
            self.assertIsNone(before["logical_table_counts"]["notification_outbox"])

            migrated_db = root / "migrated" / "pra_runtime.sqlite3"
            result = migrate_runtime_database(source_db=legacy_db, output_db=migrated_db)
            self.assertEqual(result["source_schema_version"], 5)
            self.assertEqual(result["target_schema_version"], 6)
            self.assertTrue(_database_snapshot(migrated_db)["ok"])
            self.assertEqual(_database_snapshot(legacy_db)["schema_health"]["actual_version"], 5)

            backup = create_backup(
                runtime_db=migrated_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                git_root=ROOT,
                backup_id="migrated-v5-backup",
            )
            self.assertEqual(verify_backup(backup)["backup_id"], "migrated-v5-backup")

    def test_secret_config_is_rejected_and_previous_latest_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_db = self._create_runtime_db(root)
            wheel = root / "pra_mvp.whl"
            wheel.write_bytes(b"wheel")
            safe_config = root / "safe.json"
            safe_config.write_text('{"TOKEN": ""}\n', encoding="utf-8")
            create_backup(
                runtime_db=runtime_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                config_specs=[("safe.json", safe_config)],
                git_root=ROOT,
                backup_id="previous-valid",
            )
            latest_before = (root / "backups" / "latest.json").read_text(encoding="utf-8")
            secret_config = root / "unsafe.json"
            secret_config.write_text('{"YINGDAO_ACCESS_KEY_SECRET": "real-secret"}\n', encoding="utf-8")
            with self.assertRaises(ReleaseBackupError):
                validate_nonsecret_config(secret_config)
            with patch("scripts.release_backup._backup_sqlite", side_effect=ReleaseBackupError("interrupted")):
                with self.assertRaises(ReleaseBackupError):
                    create_backup(
                        runtime_db=runtime_db,
                        backup_dir=root / "backups",
                        wheel_path=wheel,
                        git_root=ROOT,
                        backup_id="interrupted-backup",
                    )
            self.assertEqual(
                (root / "backups" / "latest.json").read_text(encoding="utf-8"),
                latest_before,
            )
            self.assertFalse((root / "backups" / "interrupted-backup").exists())


if __name__ == "__main__":
    unittest.main()
