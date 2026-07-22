from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
import scripts.release_backup as release_backup
from scripts.release_backup import (
    ROOT,
    ReleaseBackupError,
    _assert_target_file_ready,
    _database_snapshot,
    build_release_manifest,
    create_backup,
    migrate_runtime_database,
    restore_backup,
    sha256_file,
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
                "DELETE FROM runtime_schema_migrations WHERE schema_version >= 6"
            )
            connection.execute("DROP TABLE listing_status")
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
            self.assertEqual(manifest["runtime_schema_version"], 11)
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
            artifact = root / "backups" / "valid-backup" / "artifacts" / wheel.name
            self.assertEqual(artifact.read_bytes(), wheel.read_bytes())
            self.assertEqual(sha256_file(artifact), manifest["release_manifest"]["wheel"]["sha256"])
            latest = json.loads((root / "backups" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["backup_id"], "valid-backup")
            self.assertFalse(any(path.name.startswith(".valid-backup.tmp-") for path in (root / "backups").iterdir()))

            restored_db = root / "restored" / "pra_runtime.sqlite3"
            result = restore_backup(
                backup_path=backup,
                runtime_db=restored_db,
                input_dir=root / "restored" / "inputs",
                config_dir=root / "restored" / "config",
                artifact_dir=root / "restored" / "artifacts",
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
            self.assertEqual(
                (root / "restored" / "artifacts" / wheel.name).read_bytes(),
                wheel.read_bytes(),
            )
            with self.assertRaises(ReleaseBackupError):
                restore_backup(backup_path=backup, runtime_db=restored_db)
            restore_backup(backup_path=backup, runtime_db=restored_db, force=True)
            artifact.write_bytes(b"tampered-wheel")
            with self.assertRaises(ReleaseBackupError):
                verify_backup(backup)

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
            self.assertEqual(result["target_schema_version"], 11)
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

    def test_secret_scanner_rejects_real_values_in_supported_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = {
                "webhook.json": '{"FEISHU_WEBHOOK_URL": "https://example.test/hook/real"}\n',
                "authorization.yaml": "Authorization: Bearer real-token\n",
                "nested.yaml": "service:\n  webhookUrl: https://example.test/hook/real\n",
                "inline.yml": "headers: {Authorization: Bearer real-token}\n",
                "runtime.ini": "[service]\napi_key = real-key\n",
                "runtime.env": "YINGDAO_ACCESS_KEY_SECRET=real-secret\n",
                "camel.env": "credentialBlob=real-blob\n",
                "uppercase.env": "FEISHUWEBHOOKURL=https://example.test/hook/real\n",
                "angle-bracket.yaml": "Authorization: Bearer <real-token>\n",
                "angle-suffix.yaml": "Authorization: <real-token>-suffix\n",
                "example-prefix.yaml": "Authorization: EXAMPLE_real-token\n",
                "replace-suffix.yaml": "Authorization: REPLACE_ME_real-token\n",
                "your-suffix.yaml": "Authorization: YOUR_VALUE_real-token\n",
                "runtime.ps1": '$env:FEISHU_WEBHOOK_URL = "https://example.test/hook/real"\n',
                "camel.ps1": '$webhookUrl = "https://example.test/hook/real"\n',
                "powershell-map.ps1": '$config = @{\n  authorizationHeader = "Bearer real-token"\n}\n',
                "malformed.env": "this is not an assignment\n",
                "duplicate.json": '{"safe": "one", "safe": "two"}\n',
                "duplicate.yaml": "safe: one\nsafe: two\n",
            }
            for filename, content in cases.items():
                path = root / filename
                path.write_text(content, encoding="utf-8")
                with self.subTest(filename=filename):
                    with self.assertRaises(ReleaseBackupError) as raised:
                        validate_nonsecret_config(path)
                    self.assertNotIn("real", str(raised.exception))

            safe = root / "safe.json"
            safe.write_text(
                '{"queue_dir": "D:\\\\PRA_Runtime\\\\queue", '
                '"login_password_selector": "登录页_密码输入框", '
                '"YINGDAO_ACCESS_KEY_SECRET": "", '
                '"Authorization": "<runtime-only>"}\n',
                encoding="utf-8",
            )
            validate_nonsecret_config(safe)

            self.assertTrue(release_backup._is_placeholder(" CHANGE_ME "))
            self.assertTrue(release_backup._is_placeholder("<runtime-only>"))
            for value in (
                "Bearer <runtime-only>",
                "EXAMPLE_real-token",
                "REPLACE_ME_real-token",
                "YOUR_VALUE_real-token",
                "<one><two>",
            ):
                with self.subTest(placeholder_value=value):
                    self.assertFalse(release_backup._is_placeholder(value))

            unknown = root / "config.txt"
            unknown.write_text("Authorization=real-secret\n", encoding="utf-8")
            with self.assertRaises(ReleaseBackupError):
                validate_nonsecret_config(unknown)

            with patch.dict(sys.modules, {"yaml": None}):
                for filename, content in {
                    "fallback-inline.yml": "headers: {Authorization: Bearer real-token}\n",
                    "fallback-nested.yaml": "service:\n  webhookUrl: https://example.test/hook/real\n",
                    "fallback-sensitive-parent.yaml": "Authorization:\n  value: Bearer real-token\n",
                    "fallback-webhook-parent.yaml": "webhookUrl:\n  endpoint: https://example.test/hook/real\n",
                    "fallback-uppercase.yaml": "FEISHUWEBHOOKURL: https://example.test/hook/real\n",
                    "fallback-duplicate.yaml": "safe: one\nsafe: two\n",
                }.items():
                    path = root / filename
                    path.write_text(content, encoding="utf-8")
                    with self.subTest(filename=filename, parser="fallback"):
                        with self.assertRaises(ReleaseBackupError):
                            validate_nonsecret_config(path)

    def _insert_task(self, path: Path, task_id: str) -> None:
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_type, scope_key, action_type, priority, task_status,
                    created_at, updated_at
                ) VALUES (?, 'sku', ?, 'update_price', 1, 'pending', '2026-07-19T00:00:00Z', '2026-07-19T00:00:00Z')
                """,
                (task_id, task_id),
            )
            connection.commit()
        finally:
            connection.close()

    def test_rollback_failure_restores_wal_and_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_db = self._create_runtime_db(root / "source")
            wheel = root / "release.whl"
            wheel.write_bytes(b"release-wheel")
            input_file = root / "source-input.xlsx"
            input_file.write_bytes(b"new-input")
            config_file = root / "source-config.json"
            config_file.write_text('{"queue_dir": "new"}\n', encoding="utf-8")
            backup = create_backup(
                runtime_db=source_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                input_specs=[("products.xlsx", input_file)],
                config_specs=[("runtime.json", config_file)],
                git_root=ROOT,
                backup_id="rollback-fixture",
            )

            target_db = root / "target" / "pra_runtime.sqlite3"
            target_db.parent.mkdir(parents=True)
            SQLiteRuntimeRepository(target_db).init_schema()
            self._insert_task(target_db, "old-wal-task")
            old_input_dir = root / "target-inputs"
            old_config_dir = root / "target-config"
            old_input_dir.mkdir()
            old_config_dir.mkdir()
            (old_input_dir / "products.xlsx").write_bytes(b"old-input")
            (old_config_dir / "runtime.json").write_bytes(b'{"queue_dir": "old"}\n')
            before = _database_snapshot(target_db)
            self.assertEqual(before["logical_table_counts"]["review_tasks"], 0)
            self.assertEqual(before["logical_table_counts"]["execution_logs"], 0)
            self.assertEqual(before["logical_table_counts"]["notification_outbox"], 0)

            real_replace = os.replace

            def fail_database_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if Path(source).suffix == ".stage" and Path(target) == target_db:
                    raise PermissionError("injected database replacement failure")
                real_replace(source, target)

            with patch("scripts.release_backup.os.replace", side_effect=fail_database_replace):
                with self.assertRaises(ReleaseBackupError):
                    restore_backup(
                        backup_path=backup,
                        runtime_db=target_db,
                        input_dir=old_input_dir,
                        config_dir=old_config_dir,
                        force=True,
                    )

            self.assertTrue(_database_snapshot(target_db)["ok"])
            connection = sqlite3.connect(str(target_db))
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            finally:
                connection.close()
            self.assertEqual((old_input_dir / "products.xlsx").read_bytes(), b"old-input")
            self.assertEqual((old_config_dir / "runtime.json").read_bytes(), b'{"queue_dir": "old"}\n')
            self.assertFalse(list(target_db.parent.glob(".pra-*.transaction.json")))

    def test_rollback_restores_commit_that_exists_only_in_wal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_db = self._create_runtime_db(root / "source")
            wheel = root / "release.whl"
            wheel.write_bytes(b"release-wheel")
            backup = create_backup(
                runtime_db=source_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                git_root=ROOT,
                backup_id="wal-only-fixture",
            )

            target_db = root / "target" / "pra_runtime.sqlite3"
            SQLiteRuntimeRepository(target_db).init_schema()
            connection = sqlite3.connect(str(target_db))
            reader = None
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA wal_autocheckpoint = 0")
                connection.execute(
                    """
                    INSERT INTO tasks(
                        task_id, scope_type, scope_key, action_type, priority, task_status,
                        created_at, updated_at
                    ) VALUES ('wal-only-task', 'sku', 'wal-only-task', 'update_price', 1, 'pending',
                              '2026-07-19T00:00:00Z', '2026-07-19T00:00:00Z')
                    """
                )
                connection.commit()
                reader = sqlite3.connect(str(target_db))
                reader.execute("BEGIN")
                reader.execute("SELECT COUNT(*) FROM tasks").fetchone()
            finally:
                connection.close()

            wal_path = Path(str(target_db) + "-wal")
            self.assertTrue(wal_path.is_file())
            self.assertGreater(wal_path.stat().st_size, 0)
            main_only = root / "main-only.sqlite3"
            shutil.copy2(target_db, main_only)
            main_connection = sqlite3.connect(str(main_only))
            try:
                self.assertEqual(
                    main_connection.execute(
                        "SELECT COUNT(*) FROM tasks WHERE task_id = 'wal-only-task'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                main_connection.close()

            saved_wal = root / "saved-wal"
            saved_shm = root / "saved-shm"
            shutil.copy2(wal_path, saved_wal)
            shutil.copy2(Path(str(target_db) + "-shm"), saved_shm)
            reader_closed = {"value": False}

            def restore_sidecars() -> None:
                shutil.copy2(saved_wal, wal_path)
                shutil.copy2(saved_shm, Path(str(target_db) + "-shm"))

            original_snapshot = release_backup._database_snapshot
            original_backup = release_backup._backup_sqlite

            def snapshot_with_wal(path: Path) -> dict[str, object]:
                result = original_snapshot(path)
                if Path(path).resolve() == target_db.resolve() and not reader_closed["value"]:
                    reader.close()
                    reader_closed["value"] = True
                    restore_sidecars()
                return result

            def backup_with_wal(source: Path, destination: Path) -> None:
                original_backup(source, destination)
                if Path(source).resolve() == target_db.resolve():
                    restore_sidecars()

            real_replace = os.replace

            def fail_database_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if Path(source).suffix == ".stage" and Path(target) == target_db:
                    raise PermissionError("injected WAL-only replacement failure")
                real_replace(source, target)

            with patch(
                "scripts.release_backup._database_snapshot",
                side_effect=snapshot_with_wal,
            ), patch(
                "scripts.release_backup._backup_sqlite",
                side_effect=backup_with_wal,
            ), patch("scripts.release_backup.os.replace", side_effect=fail_database_replace):
                with self.assertRaises(ReleaseBackupError):
                    restore_backup(backup_path=backup, runtime_db=target_db, force=True)

            self.assertTrue(wal_path.is_file())
            self.assertGreater(wal_path.stat().st_size, 0)
            restored_connection = sqlite3.connect(str(target_db))
            try:
                self.assertEqual(
                    restored_connection.execute(
                        "SELECT COUNT(*) FROM tasks WHERE task_id = 'wal-only-task'"
                    ).fetchone()[0],
                    1,
                )
                self.assertTrue(wal_path.is_file())
                self.assertGreater(wal_path.stat().st_size, 0)
            finally:
                restored_connection.close()

    def test_rollback_after_database_replace_failure_restores_excel_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_db = self._create_runtime_db(root / "source")
            wheel = root / "release.whl"
            wheel.write_bytes(b"release-wheel")
            input_file = root / "source-input.xlsx"
            input_file.write_bytes(b"new-input")
            config_file = root / "source-config.json"
            config_file.write_text('{"queue_dir": "new"}\n', encoding="utf-8")
            backup = create_backup(
                runtime_db=source_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                input_specs=[("products.xlsx", input_file)],
                config_specs=[("runtime.json", config_file)],
                git_root=ROOT,
                backup_id="excel-failure-fixture",
            )
            target_db = root / "target" / "pra_runtime.sqlite3"
            SQLiteRuntimeRepository(target_db).init_schema()
            old_input_dir = root / "target-inputs"
            old_config_dir = root / "target-config"
            old_input_dir.mkdir()
            old_config_dir.mkdir()
            (old_input_dir / "products.xlsx").write_bytes(b"old-input")
            (old_config_dir / "runtime.json").write_bytes(b'{"queue_dir": "old"}\n')
            input_target = old_input_dir / "products.xlsx"
            real_replace = os.replace

            def fail_input_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if Path(source).suffix == ".stage" and Path(target) == input_target:
                    raise PermissionError("injected Excel replacement failure")
                real_replace(source, target)

            with patch("scripts.release_backup.os.replace", side_effect=fail_input_replace):
                with self.assertRaises(ReleaseBackupError):
                    restore_backup(
                        backup_path=backup,
                        runtime_db=target_db,
                        input_dir=old_input_dir,
                        config_dir=old_config_dir,
                        force=True,
                    )
            self.assertTrue(_database_snapshot(target_db)["ok"])
            self.assertEqual((old_input_dir / "products.xlsx").read_bytes(), b"old-input")
            self.assertEqual((old_config_dir / "runtime.json").read_bytes(), b'{"queue_dir": "old"}\n')
            self.assertFalse(list(target_db.parent.glob(".pra-*.transaction.json")))

    def test_locked_excel_preflight_rejects_without_partial_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_db = self._create_runtime_db(root / "source")
            wheel = root / "release.whl"
            wheel.write_bytes(b"release-wheel")
            input_file = root / "source-input.xlsx"
            input_file.write_bytes(b"new-input")
            backup = create_backup(
                runtime_db=source_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                input_specs=[("products.xlsx", input_file)],
                git_root=ROOT,
                backup_id="locked-excel-fixture",
            )
            target_db = root / "target" / "pra_runtime.sqlite3"
            input_dir = root / "target-inputs"
            input_dir.mkdir()
            input_target = input_dir / "products.xlsx"
            input_target.write_bytes(b"old-input")
            def fail_locked_excel(path: Path, label: str) -> None:
                if path == input_target:
                    raise ReleaseBackupError("Excel target is locked")
                _assert_target_file_ready(path, label)

            with patch("scripts.release_backup._assert_target_file_ready", side_effect=fail_locked_excel):
                with self.assertRaisesRegex(ReleaseBackupError, "Excel target is locked"):
                    restore_backup(
                        backup_path=backup,
                        runtime_db=target_db,
                        input_dir=input_dir,
                        force=True,
                    )
            self.assertFalse(target_db.exists())
            self.assertEqual(input_target.read_bytes(), b"old-input")
            self.assertFalse(list(target_db.parent.glob(".pra-*.transaction.json")))

    def test_transaction_log_is_recovered_on_next_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_db = self._create_runtime_db(root / "source")
            wheel = root / "release.whl"
            wheel.write_bytes(b"release-wheel")
            backup = create_backup(
                runtime_db=source_db,
                backup_dir=root / "backups",
                wheel_path=wheel,
                git_root=ROOT,
                backup_id="recovery-fixture",
            )
            target_db = root / "target" / "pra_runtime.sqlite3"
            SQLiteRuntimeRepository(target_db).init_schema()
            self._insert_task(target_db, "old-task")
            real_replace = os.replace
            interrupted = {"raised": False}

            def interrupt_once(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if not interrupted["raised"] and Path(source).suffix == ".stage" and Path(target) == target_db:
                    interrupted["raised"] = True
                    raise KeyboardInterrupt("injected process interruption")
                real_replace(source, target)

            with patch("scripts.release_backup.os.replace", side_effect=interrupt_once):
                with self.assertRaises(KeyboardInterrupt):
                    restore_backup(backup_path=backup, runtime_db=target_db, force=True)
            self.assertTrue(list(target_db.parent.glob(".pra-*.transaction.json")))

            restore_backup(backup_path=backup, runtime_db=target_db, force=True)
            self.assertTrue(_database_snapshot(target_db)["ok"])
            self.assertFalse(list(target_db.parent.glob(".pra-*.transaction.json")))

    def test_migrate_force_failure_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_db = self._create_legacy_v5_db(root / "legacy")
            output_db = root / "output" / "pra_runtime.sqlite3"
            migrate_runtime_database(source_db=legacy_db, output_db=output_db)
            before_hash = sha256_file(output_db)
            wheel = root / "release.whl"
            wheel.write_bytes(b"wheel")

            real_replace = os.replace

            def fail_migrate_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if Path(source).suffix == ".stage" and Path(target) == output_db:
                    raise PermissionError("injected migration replacement failure")
                real_replace(source, target)

            with patch("scripts.release_backup.os.replace", side_effect=fail_migrate_replace):
                with self.assertRaises(ReleaseBackupError):
                    migrate_runtime_database(source_db=legacy_db, output_db=output_db, force=True)
            self.assertEqual(sha256_file(output_db), before_hash)
            self.assertTrue(_database_snapshot(output_db)["ok"])


if __name__ == "__main__":
    unittest.main()
