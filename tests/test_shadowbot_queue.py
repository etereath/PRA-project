from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from app.exceptions import ValidationError
from app.models import ListingStatus
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import save_table_records
from app.services.shadowbot_executor import (
    EXECUTION_MODE_COMMIT,
    EXECUTION_MODE_READ_ONLY,
    SIDE_EFFECT_UNKNOWN,
    STATUS_NEEDS_RECONCILIATION,
    ShadowBotFileQueueRunner,
    compute_instruction_hash,
)
from app.services.shadowbot_product_read import compute_multi_product_instruction_hash
from app.services.shadowbot_queue import (
    ShadowBotLoginVerificationMonitor,
    ShadowBotQueueWatchdog,
    ShadowBotResultImporter,
    _read_json_object,
)
from scripts.run_shadowbot_queue_services import main as run_queue_services_main, run_cycle
from scripts.prepare_shadowbot_e2e_chain import prepare_shadowbot_chain_from_args
from scripts.prepare_shadowbot_commit_acceptance import prepare_commit_acceptance_from_args
from scripts.check_shadowbot_worker_health import build_health_report
from scripts.verify_shadowbot_filequeue_acceptance import verify_acceptance


class ShadowBotQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "test_runtime_tmp" / "shadowbot_queue_tests" / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.queue_dir = self.root / "queue"
        self.db_path = self.root / "runtime.sqlite3"

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def test_file_queue_publishes_request_and_checksum_atomically(self) -> None:
        runner = ShadowBotFileQueueRunner(self.queue_dir)
        payload = self._queue_payload("ATTEMPT-QUEUE-1")

        result = runner.start(payload)

        request_path = self.queue_dir / "inbox" / "ATTEMPT-QUEUE-1.ready.json"
        checksum_path = request_path.with_suffix(request_path.suffix + ".sha256")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(result.shadowbot_run_id, "filequeue:ATTEMPT-QUEUE-1")
        self.assertEqual(request["instruction_hash"], compute_instruction_hash(request))
        self.assertEqual(checksum_path.read_text(encoding="ascii").strip(), hashlib.sha256(request_path.read_bytes()).hexdigest())
        self.assertEqual(list((self.queue_dir / "inbox").glob("*.tmp-*")), [])

    def test_file_queue_publishes_v2_multi_product_request_with_v2_hash(self) -> None:
        runner = ShadowBotFileQueueRunner(self.queue_dir)
        payload = {
            "schema_version": "shadowbot-request-2.0",
            "contract_version": 2,
            "task_id": "TASK-READ-11",
            "operation_id": "READ-READ-BATCH-TEST-001",
            "execution_attempt_id": "ATTEMPT-READ-11",
            "execution_mode": "READ_ONLY",
            "platform_name": "ant_flower_wechat",
            "read_batch_id": "READ-BATCH-TEST-001",
            "products": [
                {
                    "item_id": "ITEM-001",
                    "platform": "ant_flower_wechat",
                    "platform_sku": "SKU-001",
                    "expected_product_name": "A",
                    "expected_grade": "B",
                }
            ],
            "limits": {"max_pages": 2, "max_scrolls": 3, "max_seconds": 4},
            "applet_uri": "",
            "window_title": "",
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        }
        payload["instruction_hash"] = compute_multi_product_instruction_hash(payload)

        result = runner.start(payload)

        request_path = self.queue_dir / "inbox" / "ATTEMPT-READ-11.ready.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(result.shadowbot_run_id, "filequeue:ATTEMPT-READ-11")
        self.assertEqual(request["contract_version"], 2)
        self.assertEqual(request["instruction_hash"], compute_multi_product_instruction_hash(request))
        self.assertEqual(request["products"][0]["item_id"], "ITEM-001")

    def test_v2_read_result_persists_bound_inventory_observation(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()
        repository.upsert_listing_status(
            ListingStatus(
                listing_status_id="LISTING-001",
                platform_name="ant_flower_wechat",
                internal_sku="SKU-INTERNAL-001",
                variety="A",
                current_price=Decimal("19.00"),
                grade="B",
            )
        )
        importer = ShadowBotResultImporter(repository, Mock(), self.queue_dir)
        completed_at = datetime(2026, 7, 21, 10, 30, tzinfo=UTC)
        request = {
            "contract_version": 2,
            "execution_mode": "READ_ONLY",
            "read_batch_id": "READ-BATCH-INVENTORY-001",
            "products": [{
                "item_id": "ITEM-001",
                "platform": "ant_flower_wechat",
                "expected_product_name": "A",
                "expected_grade": "B",
            }],
        }
        result = {
            "contract_version": 2,
            "read_batch_id": "READ-BATCH-INVENTORY-001",
            "execution_attempt_id": "ATTEMPT-INVENTORY-001",
            "status": "READ_COMPLETED",
            "started_at": (completed_at - timedelta(seconds=10)).isoformat(),
            "completed_at": completed_at.isoformat(),
            "product_snapshots": [{
                "item_id": "ITEM-001",
                "item_status": "SUCCESS",
                "listing_status": "ONLINE",
                "price": "19.00",
                "inventory": 27,
                "error_code": None,
                "evidence": [],
            }],
        }

        ShadowBotResultImporter._validate_v2_result(
            request,
            result,
            json.dumps(result, ensure_ascii=False).encode("utf-8"),
        )
        events = importer._import_v2_inventory_observations(request, result)

        self.assertEqual(events[0]["status"], "UPDATED")
        status = repository.get_listing_status("ant_flower_wechat", "A", "B级")
        assert status is not None
        self.assertEqual(status.platform_stock_qty, 27)
        self.assertEqual(status.inventory_observed_at, completed_at)
        self.assertEqual(status.inventory_source_attempt_id, "ATTEMPT-INVENTORY-001")

    def test_v2_complete_page_snapshot_warns_unmapped_and_zeros_absent_existing_listing(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()
        repository.upsert_listing_status(
            ListingStatus(
                listing_status_id="LISTING-A",
                platform_name="ant_flower_wechat",
                internal_sku="AISHA-B-60-Z",
                variety="Aisha",
                current_price=Decimal("26.40"),
                grade="B",
                platform_stock_qty=8,
                online_status="online",
            )
        )
        repository.upsert_listing_status(
            ListingStatus(
                listing_status_id="LISTING-001",
                platform_name="ant_flower_wechat",
                internal_sku="SKU-INTERNAL-001",
                variety="Cappuccino",
                current_price=Decimal("46.40"),
                grade="B",
                platform_stock_qty=12,
                online_status="online",
            )
        )
        importer = ShadowBotResultImporter(repository, Mock(), self.queue_dir)
        completed_at = datetime(2026, 7, 22, 7, 32, 55, tzinfo=UTC)
        request = {
            "contract_version": 2,
            "execution_mode": "READ_ONLY",
            "read_batch_id": "READ-BATCH-NOT-ONLINE-001",
            "platform_name": "ant_flower_wechat",
            "products": [
                {
                    "item_id": "ITEM-A",
                    "platform": "ant_flower_wechat",
                    "expected_product_name": "Aisha",
                    "expected_grade": "B",
                },
                {
                    "item_id": "ITEM-CAPPUCCINO-B",
                    "platform": "ant_flower_wechat",
                    "expected_product_name": "Cappuccino",
                    "expected_grade": "B",
                },
            ],
        }
        result = {
            "contract_version": 2,
            "read_batch_id": "READ-BATCH-NOT-ONLINE-001",
            "execution_attempt_id": "ATTEMPT-NOT-ONLINE-001",
            "status": "READ_COMPLETED",
            "active_listing_filter": "ONLINE",
            "started_at": (completed_at - timedelta(seconds=10)).isoformat(),
            "completed_at": completed_at.isoformat(),
            "warnings": [{
                "warning_code": "UNMAPPED_PRODUCT_DISCOVERED",
                "item_id": "UNMAPPED-E",
                "platform_name": "ant_flower_wechat",
                "product_name": "Cappuccino",
                "grade": "E",
                "row_identity": "parent-index:17",
            }],
            "product_snapshots": [
                {
                    "item_id": "ITEM-A",
                    "item_status": "SUCCESS",
                    "mapping_status": "MAPPED",
                    "listing_status": "ONLINE",
                    "price": "26.40",
                    "inventory": 3,
                    "error_code": None,
                    "evidence": [],
                },
                {
                    "item_id": "UNMAPPED-E",
                    "platform": "ant_flower_wechat",
                    "product_name": "Cappuccino",
                    "grade": "E",
                    "item_status": "SUCCESS",
                    "mapping_status": "UNMAPPED",
                    "warning_code": "UNMAPPED_PRODUCT_DISCOVERED",
                    "listing_status": "ONLINE",
                    "price": "18.80",
                    "inventory": 2,
                    "row_identity": "parent-index:17",
                    "error_code": None,
                    "evidence": [],
                },
            ],
        }

        ShadowBotResultImporter._validate_v2_result(
            request,
            result,
            json.dumps(result, ensure_ascii=False).encode("utf-8"),
        )
        events = importer._import_v2_inventory_observations(request, result)

        self.assertEqual(events[0]["status"], "UPDATED")
        warning = next(event for event in events if event.get("warning_code"))
        self.assertEqual(warning["warning_code"], "UNMAPPED_PRODUCT_DISCOVERED")
        self.assertEqual(warning["current_price"], "18.80")
        absent = next(event for event in events if event.get("inference_basis"))
        self.assertEqual(absent["inference_basis"], "ABSENT_FROM_COMPLETE_ONLINE_SNAPSHOT")
        status = repository.get_listing_status("ant_flower_wechat", "Cappuccino", "B")
        assert status is not None
        self.assertEqual(status.platform_stock_qty, 0)
        self.assertEqual(status.current_price, Decimal("46.40"))
        self.assertEqual(status.online_status, "online")
        self.assertEqual(status.source, "shadowbot_read_not_in_online")
        self.assertEqual(status.inventory_source_attempt_id, "ATTEMPT-NOT-ONLINE-001")

    def test_v2_unmapped_worker_row_is_promoted_from_inventory_and_created_online(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()
        products_path = self.root / "products.xlsx"
        save_table_records(
            "products",
            products_path,
            [
                {
                    "internal_sku": "CAPPUCCINO-E-45-Z",
                    "product_name": "卡布奇诺",
                    "grade": "E",
                    "stem_length": "45",
                    "unit": "扎",
                    "base_cost": "10.00",
                    "current_stock": 1,
                    "sale_enabled": True,
                }
            ],
        )
        importer = ShadowBotResultImporter(
            repository,
            Mock(),
            self.queue_dir,
            inventory_products_path=products_path,
        )
        completed_at = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
        request = {
            "contract_version": 2,
            "execution_mode": "READ_ONLY",
            "read_batch_id": "READ-BATCH-INVENTORY-PROMOTE-001",
            "platform_name": "蚂蚁花团供应商",
            "products": [],
        }
        result = {
            "contract_version": 2,
            "read_batch_id": request["read_batch_id"],
            "execution_attempt_id": "ATTEMPT-INVENTORY-PROMOTE-001",
            "status": "READ_COMPLETED",
            "active_listing_filter": "ONLINE",
            "started_at": (completed_at - timedelta(seconds=10)).isoformat(),
            "completed_at": completed_at.isoformat(),
            "warnings": [
                {
                    "warning_code": "UNMAPPED_PRODUCT_DISCOVERED",
                    "item_id": "UNMAPPED-E",
                    "platform_name": "蚂蚁花团供应商",
                    "product_name": "卡布奇诺",
                    "grade": "E级",
                    "row_identity": "parent-index:1",
                }
            ],
            "product_snapshots": [
                {
                    "item_id": "UNMAPPED-E",
                    "platform": "蚂蚁花团供应商",
                    "product_name": "卡布奇诺",
                    "grade": "E级",
                    "item_status": "SUCCESS",
                    "mapping_status": "UNMAPPED",
                    "warning_code": "UNMAPPED_PRODUCT_DISCOVERED",
                    "listing_status": "ONLINE",
                    "price": "12.00",
                    "inventory": 1,
                    "row_identity": "parent-index:1",
                    "error_code": None,
                    "evidence": [],
                }
            ],
        }

        events = importer._import_v2_inventory_observations(request, result)

        self.assertFalse(any(event.get("warning_code") == "UNMAPPED_PRODUCT_DISCOVERED" for event in events))
        self.assertEqual(events[0]["status"], "CREATED")
        self.assertEqual(events[0]["internal_sku"], "CAPPUCCINO-E-45-Z")
        status = repository.get_listing_status("蚂蚁花团供应商", "卡布奇诺", "E")
        assert status is not None
        self.assertEqual(status.internal_sku, "CAPPUCCINO-E-45-Z")
        self.assertEqual(status.current_price, Decimal("12.00"))
        self.assertEqual(status.platform_stock_qty, 1)
        self.assertEqual(status.online_status, "online")

    def test_file_queue_rejects_fault_injection_and_unsupported_spec_verification(self) -> None:
        runner = ShadowBotFileQueueRunner(self.queue_dir)
        with self.assertRaisesRegex(ValidationError, "UNSAFE_TEST_PARAMETER_REJECTED"):
            runner.start(self._queue_payload("ATTEMPT-FAULT", fault_injection="AFTER_SUBMIT_CLICK_UNKNOWN"))
        with self.assertRaisesRegex(ValidationError, "cannot verify expected_spec"):
            runner.start(self._queue_payload("ATTEMPT-SPEC", spec_verification_required=True))

    def test_result_importer_validates_and_archives_complete_result(self) -> None:
        prepared, repository, runner, request, request_path = self._prepare_attempt("SUCCESS")
        result_path = self._write_result(request, request_path, status="VERIFIED", side_effect_state="VERIFIED")
        importer = ShadowBotResultImporter(repository, runner, self.queue_dir)

        event = importer.import_one(result_path)

        attempt = repository.get_shadowbot_execution_attempt(prepared.execution_attempt_id)
        operation = repository.get_shadowbot_operation(prepared.operation_id)
        self.assertEqual(event["status"], "IMPORTED")
        self.assertIsNotNone(attempt.ended_at)
        self.assertEqual(operation.status, "VERIFIED")
        self.assertTrue((self.queue_dir / "archive" / prepared.execution_attempt_id).exists())

    def test_result_importer_quarantines_contract_mismatch(self) -> None:
        _, repository, runner, request, request_path = self._prepare_attempt("MISMATCH")
        result_path = self._write_result(request, request_path, status="VERIFIED", side_effect_state="VERIFIED")
        data = json.loads(result_path.read_text(encoding="utf-8"))
        data["operation_id"] = "OP-TAMPERED"
        self._publish_json(result_path, data)
        importer = ShadowBotResultImporter(repository, runner, self.queue_dir)

        events = importer.import_available()

        self.assertEqual(events[0]["status"], "QUARANTINED")
        self.assertEqual(events[0]["error_code"], "RESULT_CONTRACT_INVALID")
        self.assertTrue(list((self.queue_dir / "quarantine").glob("*.result.json")))
        reason_paths = list((self.queue_dir / "quarantine").glob("*.result.json.error.json"))
        self.assertEqual(len(reason_paths), 1)
        reason = json.loads(reason_paths[0].read_text(encoding="utf-8"))
        self.assertEqual(reason["error_code"], "RESULT_CONTRACT_INVALID")
        self.assertIn("operation_id mismatch", reason["error_message"])

    def test_login_verification_monitor_creates_one_review_and_notification(self) -> None:
        prepared, repository, runner, request, _ = self._prepare_attempt("LOGIN-HANDOFF")
        phase_path = self.queue_dir / "working" / f"{prepared.execution_attempt_id}.phase.json"
        phase_path.write_text(
            json.dumps(
                {
                    "task_id": request["task_id"],
                    "operation_id": request["operation_id"],
                    "execution_attempt_id": prepared.execution_attempt_id,
                    "execution_mode": request["execution_mode"],
                    "phase": "LOGIN_VERIFICATION_REQUIRED",
                    "side_effect_state": "NOT_STARTED",
                    "login": {
                        "verification_detected_at": "2026-07-11T12:00:00+08:00",
                        "verification_deadline_at": "2026-07-11T12:05:00+08:00",
                        "verification_markers": ["验证码"],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monitor = ShadowBotLoginVerificationMonitor(repository, runner, self.queue_dir)

        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            first_events = monitor.inspect()
            second_events = monitor.inspect()

        review_task_id = first_events[0]["review_task_id"]
        review = repository.get_review_task(review_task_id)
        notifications = repository.list_notification_logs(related_review_task_id=review_task_id)
        self.assertEqual(first_events[0]["status"], "LOGIN_VERIFICATION_HANDOFF_OPEN")
        self.assertEqual(second_events[0]["review_task_id"], review_task_id)
        self.assertEqual(review.review_status.value, "pending")
        self.assertEqual(len(notifications), 1)
        self.assertNotIn("password", json.dumps(review.review_payload).lower())

    def test_result_importer_retries_transient_io_error_without_quarantine(self) -> None:
        _, repository, runner, request, request_path = self._prepare_attempt("IO-RETRY")
        result_path = self._write_result(
            request,
            request_path,
            status="VERIFIED",
            side_effect_state="VERIFIED",
        )
        importer = ShadowBotResultImporter(repository, runner, self.queue_dir)

        with patch.object(
            importer,
            "import_one",
            side_effect=PermissionError(5, "access denied", str(result_path)),
        ):
            events = importer.import_available()

        self.assertEqual(events[0]["status"], "RETRY_PENDING")
        self.assertEqual(events[0]["error_code"], "RESULT_IO_RETRY_PENDING")
        self.assertTrue(result_path.exists())
        self.assertFalse(list((self.queue_dir / "quarantine").glob("*.result.json")))

    def test_terminal_result_reimport_repairs_interrupted_task_projection(self) -> None:
        prepared, repository, runner, request, request_path = self._prepare_attempt(
            "REPROJECT"
        )
        result_path = self._write_result(
            request,
            request_path,
            status="VERIFIED",
            side_effect_state="VERIFIED",
        )
        importer = ShadowBotResultImporter(repository, runner, self.queue_dir)

        with patch.object(
            importer.executor,
            "_update_task_after_result",
            side_effect=ValidationError("simulated projection interruption"),
        ):
            with self.assertRaisesRegex(
                ValidationError,
                "simulated projection interruption",
            ):
                importer.import_one(result_path)

        interrupted_attempt = repository.get_shadowbot_execution_attempt(
            prepared.execution_attempt_id
        )
        interrupted_task = repository.get_task(request["task_id"])
        assert interrupted_attempt is not None
        assert interrupted_task is not None
        self.assertIsNotNone(interrupted_attempt.ended_at)
        self.assertNotEqual(interrupted_task.task_status.value, "success")

        event = importer.import_one(result_path)

        repaired_task = repository.get_task(request["task_id"])
        assert repaired_task is not None
        self.assertEqual(event["status"], "ALREADY_IMPORTED")
        self.assertEqual(repaired_task.task_status.value, "success")
        self.assertFalse(result_path.exists())

    def test_queue_json_reader_retries_windows_file_collision(self) -> None:
        path = self.root / "heartbeat.json"
        path.write_text('{"status":"RUNNING"}', encoding="utf-8")
        original_read_text = Path.read_text
        calls = 0

        def flaky_read_text(target, *args, **kwargs):
            nonlocal calls
            if target == path and calls < 2:
                calls += 1
                raise PermissionError(13, "permission denied", str(target))
            return original_read_text(target, *args, **kwargs)

        with patch.object(Path, "read_text", flaky_read_text):
            data = _read_json_object(path)

        self.assertEqual(data["status"], "RUNNING")
        self.assertEqual(calls, 2)

    def test_queue_service_cycle_survives_watchdog_io_error(self) -> None:
        importer = Mock()
        importer.import_available.return_value = []
        watchdog = Mock()
        watchdog.inspect.side_effect = PermissionError(13, "permission denied", "heartbeat.json")

        events = run_cycle(importer, watchdog)

        self.assertEqual(events[0]["status"], "RETRY_PENDING")
        self.assertEqual(events[0]["error_code"], "WATCHDOG_INSPECTION_FAILED")

    def test_queue_services_cli_sets_queue_env_for_executor_runner(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "scripts.run_shadowbot_queue_services.ShadowBotQueuePaths"
        ) as paths_class, patch(
            "scripts.run_shadowbot_queue_services.SQLiteRuntimeRepository"
        ) as repository_class, patch(
            "scripts.run_shadowbot_queue_services.build_shadowbot_task_runner_from_environment"
        ) as build_runner, patch(
            "scripts.run_shadowbot_queue_services.ShadowBotResultImporter"
        ), patch(
            "scripts.run_shadowbot_queue_services.ShadowBotQueueWatchdog"
        ), patch(
            "scripts.run_shadowbot_queue_services.run_cycle",
            return_value=[{"status": "IMPORTED", "observed_at": datetime(2026, 7, 23, tzinfo=UTC)}],
        ), patch(
            "scripts.run_shadowbot_queue_services.msvcrt.locking"
        ), patch.object(
            sys,
            "argv",
            [
                "run_shadowbot_queue_services.py",
                "--runtime-db",
                str(self.db_path),
                "--queue-dir",
                str(self.queue_dir),
                "--once",
            ],
        ):
            paths = Mock()
            paths.control = self.queue_dir / "control"
            paths.ensure.side_effect = lambda: paths.control.mkdir(parents=True, exist_ok=True)
            paths_class.return_value = paths
            repository_class.return_value.init_schema.return_value = None

            result = run_queue_services_main()
            self.assertEqual(os.environ["SHADOWBOT_QUEUE_DIR"], str(self.queue_dir))
            self.assertEqual(os.environ["SHADOWBOT_REQUEST_DIR"], str(self.queue_dir))
            self.assertEqual(result, 0)
            build_runner.assert_called_once()

    def test_unknown_result_creates_one_executor_owned_reconcile_attempt(self) -> None:
        prepared, repository, runner, request, request_path = self._prepare_attempt("UNKNOWN")
        result_path = self._write_result(
            request,
            request_path,
            status=STATUS_NEEDS_RECONCILIATION,
            side_effect_state=SIDE_EFFECT_UNKNOWN,
        )
        importer = ShadowBotResultImporter(repository, runner, self.queue_dir)

        importer.import_one(result_path)
        digest = hashlib.sha256(prepared.execution_attempt_id.encode("utf-8")).hexdigest()[:20]
        reconcile_id = f"RECONCILE-{digest}"
        reconcile = repository.get_shadowbot_execution_attempt(reconcile_id)

        self.assertIsNotNone(reconcile)
        self.assertEqual(reconcile.execution_mode, "RECONCILE")
        self.assertTrue((self.queue_dir / "inbox" / f"{reconcile_id}.ready.json").exists())
        self.assertEqual(len(list((self.queue_dir / "inbox").glob(f"{reconcile_id}.ready.json"))), 1)
        reconcile_request = json.loads(
            (self.queue_dir / "inbox" / f"{reconcile_id}.ready.json").read_text(encoding="utf-8")
        )
        self.assertEqual(reconcile_request["evidence_share_dir"], r"\\TEST-HOST\pra-evidence")

    def test_watchdog_only_writes_recovery_result_then_importer_creates_reconcile(self) -> None:
        prepared, repository, runner, request, request_path = self._prepare_attempt("STALE")
        phase_path = self.queue_dir / "working" / f"{prepared.execution_attempt_id}.phase.json"
        old_time = datetime.now(UTC) - timedelta(minutes=5)
        phase = {
            "task_id": request["task_id"],
            "operation_id": request["operation_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "execution_mode": EXECUTION_MODE_COMMIT,
            "phase": "SUBMIT_CLICKED",
            "side_effect_state": "SUBMIT_CLICKED",
            "request_file_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "instruction_hash": request["instruction_hash"],
            "worker_id": "TEST-WORKER",
            "updated_at": old_time.isoformat(),
        }
        phase_path.write_text(json.dumps(phase), encoding="utf-8")
        heartbeat = {"worker_id": "TEST-WORKER", "status": "RUNNING", "updated_at": old_time.isoformat()}
        (self.queue_dir / "heartbeat.json").write_text(json.dumps(heartbeat), encoding="utf-8")
        watchdog = ShadowBotQueueWatchdog(self.queue_dir, stale_seconds=30)

        events = watchdog.inspect()

        self.assertEqual(events[0]["status"], "RECOVERY_RESULT_WRITTEN")
        self.assertIsNone(repository.get_shadowbot_execution_attempt(self._reconcile_id(prepared.execution_attempt_id)))
        importer = ShadowBotResultImporter(repository, runner, self.queue_dir)
        importer.import_available()
        self.assertIsNotNone(repository.get_shadowbot_execution_attempt(self._reconcile_id(prepared.execution_attempt_id)))

    def test_watchdog_reports_stale_running_heartbeat_once_when_queue_is_idle(self) -> None:
        old_time = datetime.now(UTC) - timedelta(minutes=5)
        heartbeat = {
            "worker_id": "STALE-WORKER",
            "status": "RUNNING",
            "updated_at": old_time.isoformat(),
        }
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        (self.queue_dir / "heartbeat.json").write_text(json.dumps(heartbeat), encoding="utf-8")
        watchdog = ShadowBotQueueWatchdog(self.queue_dir, stale_seconds=30)

        first = watchdog.inspect()
        second = watchdog.inspect()

        self.assertEqual(first[0]["status"], "WARNING")
        self.assertEqual(first[0]["error_code"], "WORKER_HEARTBEAT_STALE")
        self.assertEqual(first[0]["worker_id"], "STALE-WORKER")
        self.assertEqual(second, [])

    def test_worker_health_report_rejects_stale_or_failed_heartbeat(self) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        heartbeat = {
            "worker_id": "HEALTH-WORKER",
            "status": "RUNNING",
            "updated_at": (now - timedelta(minutes=1)).isoformat(),
            "heartbeat_write_failures": 1,
            "heartbeat_consecutive_failures": 1,
            "heartbeat_last_error": "PermissionError: locked",
            "heartbeat_thread_restarts": 0,
        }
        (self.queue_dir / "heartbeat.json").write_text(json.dumps(heartbeat), encoding="utf-8")

        report = build_health_report(self.queue_dir, now=now, max_age_seconds=15)

        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["heartbeat_fresh"])
        self.assertFalse(report["checks"]["no_consecutive_write_failures"])
        self.assertEqual(report["heartbeat_write_failures"], 1)

    def test_bounded_worker_claims_executes_and_writes_result_with_heartbeat(self) -> None:
        runner = ShadowBotFileQueueRunner(self.queue_dir)
        runner.start(self._queue_payload("ATTEMPT-WORKER-1"))
        shadowbot_source = Path.cwd() / "shadowbot" / "test2"
        sys.path.insert(0, str(shadowbot_source))
        try:
            import shadowbot_queue_worker

            fake_vertical = types.SimpleNamespace(
                main=lambda args: json.dumps(
                    {
                        "status": "VERIFIED",
                        "run_success_flag": True,
                        "business_operation_completed": True,
                        "side_effect_state": "VERIFIED",
                        "retryable": False,
                    }
                )
            )
            with patch.dict(sys.modules, {"vertical_slice_read_price": fake_vertical}):
                result = shadowbot_queue_worker.QueueWorker(
                    {
                        "queue_dir": str(self.queue_dir),
                        "poll_seconds": 0.01,
                        "max_hours": 1,
                        "max_tasks": 1,
                        "heartbeat_seconds": 0.01,
                    }
                ).run()
        finally:
            sys.path.remove(str(shadowbot_source))

        result_path = self.queue_dir / "results" / "ATTEMPT-WORKER-1.result.json"
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
        heartbeat = json.loads((self.queue_dir / "heartbeat.json").read_text(encoding="utf-8"))
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result_data["queue_phase"], "RESULT_WRITTEN")
        self.assertEqual(heartbeat["status"], "STOPPED")

    def test_bounded_worker_accepts_v2_multi_product_request(self) -> None:
        runner = ShadowBotFileQueueRunner(self.queue_dir)
        payload = {
            "schema_version": "shadowbot-request-2.0",
            "contract_version": 2,
            "task_id": "TASK-READ-11",
            "operation_id": "READ-READ-BATCH-WORKER-001",
            "execution_attempt_id": "ATTEMPT-READ-WORKER-1",
            "execution_mode": "READ_ONLY",
            "platform_name": "ant_flower_wechat",
            "read_batch_id": "READ-BATCH-WORKER-001",
            "products": [{
                "item_id": "ITEM-001",
                "platform": "ant_flower_wechat",
                "platform_sku": "SKU-001",
                "expected_product_name": "A",
                "expected_grade": "B",
            }],
            "limits": {"max_pages": 2, "max_scrolls": 3, "max_seconds": 4},
            "applet_uri": "",
            "window_title": "",
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        }
        payload["instruction_hash"] = compute_multi_product_instruction_hash(payload)
        runner.start(payload)
        shadowbot_source = Path.cwd() / "shadowbot" / "test2"
        sys.path.insert(0, str(shadowbot_source))
        try:
            import shadowbot_queue_worker

            fake_vertical = types.SimpleNamespace(
                main=lambda args: json.dumps({
                    "status": "READ_COMPLETED",
                    "contract_version": 2,
                    "read_batch_id": "READ-BATCH-WORKER-001",
                    "overall_status": "COMPLETED",
                    "total_count": 1,
                    "success_count": 1,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "manual_check_count": 0,
                    "product_snapshots": [],
                    "run_success_flag": True,
                    "business_operation_completed": False,
                    "side_effect_state": "NOT_STARTED",
                    "retryable": False,
                })
            )
            with patch.dict(sys.modules, {"vertical_slice_read_price": fake_vertical}):
                run_result = shadowbot_queue_worker.QueueWorker({
                    "queue_dir": str(self.queue_dir),
                    "poll_seconds": 0.01,
                    "max_hours": 1,
                    "max_tasks": 1,
                    "heartbeat_seconds": 0.01,
                }).run()
        finally:
            sys.path.remove(str(shadowbot_source))

        result_data = json.loads((self.queue_dir / "results" / "ATTEMPT-READ-WORKER-1.result.json").read_text(encoding="utf-8"))
        self.assertEqual(run_result["processed"], 1)
        self.assertEqual(result_data["contract_version"], 2)
        self.assertEqual(result_data["read_batch_id"], "READ-BATCH-WORKER-001")
        self.assertEqual(result_data["instruction_hash"], payload["instruction_hash"])

    def test_v2_result_validation_checks_snapshot_identity_and_evidence_binding(self) -> None:
        request = {
            "contract_version": 2,
            "execution_mode": "READ_ONLY",
            "read_batch_id": "READ-BATCH-VALIDATE-001",
            "products": [{
                "item_id": "ITEM-001",
                "platform": "ant_flower_wechat",
                "platform_sku": "SKU-001",
                "expected_product_name": "A",
                "expected_grade": "B",
            }],
        }
        result = {
            "contract_version": 2,
            "read_batch_id": request["read_batch_id"],
            "execution_attempt_id": "ATTEMPT-VALIDATE-1",
            "status": "READ_COMPLETED",
            "started_at": datetime.now(UTC).isoformat(),
            "ended_at": datetime.now(UTC).isoformat(),
            "product_snapshots": [{
                "item_id": "ITEM-001",
                "item_status": "SUCCESS",
                "listing_status": "ONLINE",
                "error_code": None,
                "evidence": [{
                    "evidence_id": "EVD-001",
                    "evidence_type": "PRODUCT_READ",
                    "relative_path": "READ-BATCH-VALIDATE-001/ITEM-001.png",
                    "sha256": "a" * 64,
                    "read_batch_id": request["read_batch_id"],
                    "item_id": "ITEM-001",
                    "execution_attempt_id": "ATTEMPT-VALIDATE-1",
                }],
            }],
        }
        ShadowBotResultImporter._validate_v2_result(
            request,
            result,
            json.dumps(result, ensure_ascii=False).encode("utf-8"),
        )
        result["product_snapshots"][0]["evidence"] = []
        # Section 17 makes screenshot/evidence capture an explicit opt-in;
        # structured READ_ONLY results remain valid without it.
        ShadowBotResultImporter._validate_v2_result(
            request,
            result,
            json.dumps(result, ensure_ascii=False).encode("utf-8"),
        )
        result["product_snapshots"][0]["evidence"] = {"not": "an array"}
        with self.assertRaisesRegex(ValidationError, "evidence must be an array"):
            ShadowBotResultImporter._validate_v2_result(
                request,
                result,
                json.dumps(result, ensure_ascii=False).encode("utf-8"),
            )

    def test_atomic_write_retries_windows_file_sharing_collision(self) -> None:
        shadowbot_source = Path.cwd() / "shadowbot" / "test2"
        sys.path.insert(0, str(shadowbot_source))
        try:
            import shadowbot_queue_worker

            target = self.root / "atomic.json"
            original_replace = shadowbot_queue_worker.os.replace
            calls = 0

            def flaky_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError(13, "file is temporarily locked")
                return original_replace(source, destination)

            with patch.object(shadowbot_queue_worker.os, "replace", side_effect=flaky_replace):
                shadowbot_queue_worker._atomic_write(target, b"ok", max_attempts=3)
        finally:
            sys.path.remove(str(shadowbot_source))

        self.assertEqual(target.read_bytes(), b"ok")
        self.assertEqual(calls, 2)
        self.assertEqual(list(self.root.glob("atomic.json.tmp-*")), [])

    def test_heartbeat_loop_survives_write_failure_and_records_recovery(self) -> None:
        shadowbot_source = Path.cwd() / "shadowbot" / "test2"
        sys.path.insert(0, str(shadowbot_source))
        try:
            import shadowbot_queue_worker

            worker = shadowbot_queue_worker.QueueWorker(
                {
                    "queue_dir": str(self.queue_dir),
                    "poll_seconds": 0.01,
                    "max_hours": 1,
                    "max_tasks": 1,
                    "heartbeat_seconds": 1,
                }
            )
            worker.heartbeat_seconds = 0.01
            original_write = worker._write_heartbeat
            calls = 0

            def flaky_heartbeat(status, processed):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError(13, "heartbeat is temporarily locked")
                original_write(status, processed)
                worker._stop_heartbeat.set()

            with patch.object(worker, "_write_heartbeat", side_effect=flaky_heartbeat):
                worker._heartbeat_loop()
        finally:
            sys.path.remove(str(shadowbot_source))

        heartbeat = json.loads((self.queue_dir / "heartbeat.json").read_text(encoding="utf-8"))
        self.assertEqual(calls, 2)
        self.assertEqual(heartbeat["heartbeat_write_failures"], 1)
        self.assertIn("PermissionError", heartbeat["heartbeat_last_error"])
        self.assertTrue((self.queue_dir / "control" / "heartbeat_errors.jsonl").exists())

    def test_worker_writes_importable_failure_for_expired_registered_request(self) -> None:
        runner = ShadowBotFileQueueRunner(self.queue_dir)
        runner.start(self._queue_payload("ATTEMPT-EXPIRED-1"))
        request_path = self.queue_dir / "inbox" / "ATTEMPT-EXPIRED-1.ready.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        self._publish_json(request_path, request)
        shadowbot_source = Path.cwd() / "shadowbot" / "test2"
        sys.path.insert(0, str(shadowbot_source))
        try:
            import shadowbot_queue_worker

            worker = shadowbot_queue_worker.QueueWorker(
                {
                    "queue_dir": str(self.queue_dir),
                    "poll_seconds": 0.01,
                    "max_hours": 1,
                    "max_tasks": 1,
                    "heartbeat_seconds": 1,
                }
            )
            claimed = worker._claim_next()
        finally:
            sys.path.remove(str(shadowbot_source))

        result_path = self.queue_dir / "results" / "ATTEMPT-EXPIRED-1.result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertIsNone(claimed)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_code"], "REQUEST_EXPIRED")
        self.assertEqual(result["side_effect_state"], "NOT_STARTED")
        self.assertTrue((self.queue_dir / "working" / "ATTEMPT-EXPIRED-1.request.json").exists())
        self.assertFalse(list((self.queue_dir / "quarantine").glob("*ATTEMPT-EXPIRED-1*")))

    def test_acceptance_verifier_checks_archive_database_phase_and_shared_evidence(self) -> None:
        prepared, repository, runner, request, request_path = self._prepare_attempt(
            "ACCEPT",
            execution_mode=EXECUTION_MODE_READ_ONLY,
        )
        evidence_path = self.root / "shared-evidence.png"
        evidence_path.write_bytes(b"png-evidence")
        evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        phase = {
            "execution_attempt_id": prepared.execution_attempt_id,
            "phase": "RESULT_WRITTEN",
            "worker_id": "TEST-WORKER",
        }
        (self.queue_dir / "working" / f"{prepared.execution_attempt_id}.phase.json").write_text(
            json.dumps(phase),
            encoding="utf-8",
        )
        result = {
            "schema_version": "shadowbot-result-1.0",
            "task_id": request["task_id"],
            "operation_id": request["operation_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "execution_mode": request["execution_mode"],
            "instruction_hash": request["instruction_hash"],
            "request_file_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "lease_owner_token": request["lease_owner_token"],
            "lease_version": request["lease_version"],
            "worker_id": "TEST-WORKER",
            "status": "READ_COMPLETED",
            "run_success_flag": True,
            "business_operation_completed": False,
            "side_effect_state": "NOT_STARTED",
            "old_price": "19.00",
            "actual_price": "19.00",
            "error_code": "",
            "retryable": False,
            "evidence": [
                {
                    "type": "READ_OLD_PRICE",
                    "sha256": evidence_hash,
                    "storage_sha256": evidence_hash,
                    "storage_path": str(evidence_path),
                    "captured_at": datetime.now(UTC).isoformat(),
                    "upload_status": "SUCCESS",
                    "hash_verified": True,
                }
            ],
        }
        result_path = self.queue_dir / "results" / f"{prepared.execution_attempt_id}.result.json"
        self._publish_json(result_path, result)
        ShadowBotResultImporter(repository, runner, self.queue_dir).import_one(result_path)

        report = verify_acceptance(
            runtime_db=self.db_path,
            queue_dir=self.queue_dir,
            execution_attempt_id=prepared.execution_attempt_id,
            execution_mode=EXECUTION_MODE_READ_ONLY,
        )

        self.assertTrue(report["ok"], report["failed_checks"])

        preflight_args = argparse.Namespace(
            runtime_db=self.db_path,
            queue_dir=self.queue_dir,
            source_read_attempt_id=prepared.execution_attempt_id,
            target_price="19.50",
            confirmed_by="acceptance-tester",
            confirmation_text="",
            max_read_age_minutes=10,
            task_id="",
            approval_id="",
            operation_id="",
            execution_attempt_id="",
            start=False,
        )
        preflight = prepare_commit_acceptance_from_args(preflight_args)
        self.assertEqual(preflight["required_confirmation_text"], "COMMIT C级艾莎 19.00 -> 19.50")
        preflight_args.start = True
        preflight_args.confirmation_text = "COMMIT wrong product"
        with self.assertRaisesRegex(ValidationError, "confirmation text mismatch"):
            prepare_commit_acceptance_from_args(preflight_args)

    def test_acceptance_verifier_supports_pre_submit_stop_profile(self) -> None:
        prepared, repository, runner, request, request_path = self._prepare_attempt(
            "STOP",
            execution_mode=EXECUTION_MODE_READ_ONLY,
        )
        phase = {
            "execution_attempt_id": prepared.execution_attempt_id,
            "phase": "RESULT_WRITTEN",
            "worker_id": "TEST-WORKER",
        }
        (self.queue_dir / "working" / f"{prepared.execution_attempt_id}.phase.json").write_text(
            json.dumps(phase),
            encoding="utf-8",
        )
        result = {
            "schema_version": "shadowbot-result-1.0",
            "task_id": request["task_id"],
            "operation_id": request["operation_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "execution_mode": request["execution_mode"],
            "instruction_hash": request["instruction_hash"],
            "request_file_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "lease_owner_token": request["lease_owner_token"],
            "lease_version": request["lease_version"],
            "worker_id": "TEST-WORKER",
            "status": "FAILED",
            "run_success_flag": False,
            "business_operation_completed": False,
            "side_effect_state": "NOT_STARTED",
            "error_code": "WORKER_STOP_REQUESTED",
            "retryable": True,
        }
        result_path = self.queue_dir / "results" / f"{prepared.execution_attempt_id}.result.json"
        self._publish_json(result_path, result)
        ShadowBotResultImporter(repository, runner, self.queue_dir).import_one(result_path)

        report = verify_acceptance(
            runtime_db=self.db_path,
            queue_dir=self.queue_dir,
            execution_attempt_id=prepared.execution_attempt_id,
            execution_mode=EXECUTION_MODE_READ_ONLY,
            profile="PRE_SUBMIT_STOP",
        )

        self.assertTrue(report["ok"], report["failed_checks"])
        self.assertEqual(report["profile"], "PRE_SUBMIT_STOP")

    def _prepare_attempt(self, suffix: str, *, execution_mode: str = EXECUTION_MODE_COMMIT):
        task_id = f"TASK-QUEUE-{suffix}"
        attempt_id = f"ATTEMPT-QUEUE-{suffix}"
        with patch.dict(
            os.environ,
            {
                "SHADOWBOT_RUNNER_TYPE": "filequeue",
                "SHADOWBOT_QUEUE_DIR": str(self.queue_dir),
                "SHADOWBOT_EVIDENCE_DIR": r"\\TEST-HOST\pra-evidence",
            },
            clear=False,
        ):
            prepared = prepare_shadowbot_chain_from_args(
                argparse.Namespace(
                    runtime_db=self.db_path,
                    platform="蚂蚁花团供应商",
                    sku="SKU-AISHA-C",
                    product_name="艾莎",
                    grade="C级",
                    expected_old_price="19.00",
                    target_price="19.50",
                    task_id=task_id,
                    approval_id=f"REVIEW-QUEUE-{suffix}",
                    operation_id=f"OP-QUEUE-{suffix}",
                    execution_attempt_id=attempt_id,
                    trade_date="2026-06-30",
                    approved_by="queue-test",
                    approval_ttl_minutes=60,
                    execution_mode=execution_mode,
                    start=True,
                )
            )
        repository = SQLiteRuntimeRepository(self.db_path)
        runner = ShadowBotFileQueueRunner(self.queue_dir)
        inbox_request = self.queue_dir / "inbox" / f"{attempt_id}.ready.json"
        inbox_checksum = inbox_request.with_suffix(inbox_request.suffix + ".sha256")
        working_request = self.queue_dir / "working" / f"{attempt_id}.request.json"
        working_checksum = working_request.with_suffix(working_request.suffix + ".sha256")
        working_request.parent.mkdir(parents=True, exist_ok=True)
        os.replace(inbox_request, working_request)
        os.replace(inbox_checksum, working_checksum)
        request = json.loads(working_request.read_text(encoding="utf-8"))
        return prepared, repository, runner, request, working_request

    def _write_result(self, request, request_path, *, status: str, side_effect_state: str) -> Path:
        needs_reconcile = status == STATUS_NEEDS_RECONCILIATION
        result = {
            "schema_version": "shadowbot-result-1.0",
            "task_id": request["task_id"],
            "operation_id": request["operation_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "execution_mode": request["execution_mode"],
            "instruction_hash": request["instruction_hash"],
            "request_file_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "lease_owner_token": request["lease_owner_token"],
            "lease_version": request["lease_version"],
            "worker_id": "TEST-WORKER",
            "status": status,
            "run_success_flag": None if needs_reconcile else True,
            "business_operation_completed": None if needs_reconcile else True,
            "side_effect_state": side_effect_state,
            "error_code": "SUBMIT_RESULT_UNKNOWN" if needs_reconcile else "",
            "retryable": False,
        }
        result_path = self.queue_dir / "results" / f"{request['execution_attempt_id']}.result.json"
        self._publish_json(result_path, result)
        return result_path

    def _publish_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        path.write_bytes(content)
        path.with_suffix(path.suffix + ".sha256").write_text(hashlib.sha256(content).hexdigest() + "\n", encoding="ascii")

    def _queue_payload(self, attempt_id: str, **overrides):
        payload = {
            "schema_version": "shadowbot-request-1.0",
            "task_id": "TASK-QUEUE-1",
            "operation_id": "OP-QUEUE-1",
            "execution_attempt_id": attempt_id,
            "execution_mode": EXECUTION_MODE_COMMIT,
            "platform_name": "蚂蚁花团供应商",
            "platform_sku": "SKU-AISHA-C",
            "product_keyword": "C级艾莎",
            "expected_product_name": "艾莎",
            "expected_grade": "C级",
            "expected_spec": "",
            "spec_verification_required": False,
            "expected_old_price": "19.00",
            "target_price": "19.50",
            "approved_payload_hash": "approved-hash",
            "approval_id": "REVIEW-1",
            "applet_uri": "",
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        }
        payload.update(overrides)
        payload["instruction_hash"] = compute_instruction_hash(payload)
        return payload

    @staticmethod
    def _reconcile_id(source_attempt_id: str) -> str:
        return "RECONCILE-" + hashlib.sha256(source_attempt_id.encode("utf-8")).hexdigest()[:20]


if __name__ == "__main__":
    unittest.main()
