from __future__ import annotations

import sqlite3
import tempfile
import unittest
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.enums import (
    NotificationOutboxStatus,
    NotificationSendStatus,
    ReviewTaskStatus,
    TaskActionType,
    TaskStatus,
)
from app.models import NotificationLog, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import (
    FeishuWebhookNotificationSender,
    MockNotificationSender,
    NotificationLogService,
    NotificationSenderFactory,
    ReviewNotificationService,
    ReviewTaskService,
    ReviewTokenService,
    RuntimeTaskService,
    _build_feishu_sign,
)


def _runtime_task(
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.PENDING,
    action_type: TaskActionType = TaskActionType.CAPACITY_WARNING,
    required_by: datetime | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=None,
        platform_name=None,
        action_type=action_type,
        priority=2,
        task_status=status,
        created_at=datetime(2026, 5, 4, 9, 0),
        trade_date=date(2026, 5, 4),
        scope_type="global",
        scope_key="2026-05-04",
        dedupe_key=f"2026-05-04|global|2026-05-04|{action_type.value}|{task_id}",
        decision_trace={"reason": "runtime"},
        result_message="needs manual review",
        required_by=required_by,
    )


class RuntimePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runtime.sqlite3"
        self.repository = SQLiteRuntimeRepository(self.db_path)
        self.task_service = RuntimeTaskService(self.repository)
        self.task_service.init_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_initializes_version_and_partial_unique_index(self) -> None:
        self.task_service.init_schema()
        self.assertEqual(self.repository.schema_versions(), [1, 2, 3, 4, 5, 6, 7])
        connection = sqlite3.connect(self.db_path)
        try:
            indexes = connection.execute("PRAGMA index_list(tasks)").fetchall()
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        finally:
            connection.close()
        self.assertTrue(any(row[1] == "ux_tasks_open_dedupe" and row[4] for row in indexes))
        self.assertIn(("review_tokens",), tables)
        self.assertIn(("script_runs",), tables)
        self.assertIn(("script_run_items",), tables)

    def test_schema_migrates_v1_database_to_latest(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_runtime.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE runtime_schema_migrations (
                    schema_version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                INSERT INTO runtime_schema_migrations(schema_version, applied_at, note)
                VALUES (1, '2026-05-04T09:00:00', 'initial runtime schema')
                """
            )
            connection.commit()
        finally:
            connection.close()

        repository = SQLiteRuntimeRepository(legacy_path)
        repository.init_schema()
        self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5, 6, 7])
        connection = sqlite3.connect(legacy_path)
        try:
            token_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'review_tokens'"
            ).fetchone()
            script_run_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'script_runs'"
            ).fetchone()
            shadowbot_operation_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'shadowbot_operations'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(token_table)
        self.assertIsNotNone(script_run_table)
        self.assertIsNotNone(shadowbot_operation_table)

    def test_dedupe_key_only_blocks_open_tasks(self) -> None:
        first = _runtime_task("TASK-1")
        duplicate = _runtime_task("TASK-2")
        duplicate.dedupe_key = first.dedupe_key
        self.assertEqual(self.task_service.create_tasks([first]), 1)
        self.assertEqual(self.task_service.create_tasks([duplicate]), 0)

        self.task_service.change_status(
            task_id="TASK-1",
            to_status=TaskStatus.CANCELLED,
            changed_by="test",
            reason="close old task",
        )
        self.assertEqual(self.task_service.create_tasks([duplicate]), 1)
        self.assertEqual(len(self.task_service.list_tasks()), 2)

    def test_status_transition_records_history_and_rejects_invalid_transition(self) -> None:
        self.task_service.create_tasks([_runtime_task("TASK-1")])
        self.task_service.change_status(
            task_id="TASK-1",
            to_status=TaskStatus.RUNNING,
            changed_by="worker",
            reason="start",
            metadata={"batch": "B1"},
        )
        history = self.task_service.list_status_history("TASK-1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].from_status, TaskStatus.PENDING)
        self.assertEqual(history[0].to_status, TaskStatus.RUNNING)
        self.assertEqual(history[0].metadata["batch"], "B1")

        with self.assertRaises(Exception):
            self.task_service.change_status(
                task_id="TASK-1",
                to_status=TaskStatus.PENDING,
                changed_by="worker",
                reason="invalid",
            )

    def test_mock_notification_sender_returns_structured_result(self) -> None:
        sender = MockNotificationSender()
        result = sender.send(
            NotificationLog(
                notification_id="N-1",
                related_task_id=None,
                related_review_task_id=None,
                recipient_type="role",
                recipient="operations",
                channel="mock",
                sent_at=None,
                send_status=NotificationSendStatus.PENDING.value,
                dedupe_key="mock|1",
                message="mock message",
            )
        )
        self.assertEqual(result.send_status, NotificationSendStatus.SUCCESS.value)
        self.assertIsNotNone(result.sent_at)
        self.assertEqual(result.raw_response_json, {"mock": True})

    def test_notification_sender_factory_selects_feishu_and_unknown_fails(self) -> None:
        factory = NotificationSenderFactory()
        self.assertIsInstance(factory.build("feishu"), FeishuWebhookNotificationSender)

        sender = factory.build("unknown-channel")
        result = sender.send(
            NotificationLog(
                notification_id="N-1",
                related_task_id=None,
                related_review_task_id=None,
                recipient_type="role",
                recipient="operations",
                channel="unknown-channel",
                sent_at=None,
                send_status=NotificationSendStatus.PENDING.value,
                dedupe_key="unknown|1",
                message="unknown message",
            )
        )
        self.assertEqual(result.send_status, NotificationSendStatus.FAILED.value)
        self.assertIn("unsupported notification channel", result.error_message)

    def test_feishu_sender_requires_webhook_url(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = FeishuWebhookNotificationSender().send(
                NotificationLog(
                    notification_id="N-1",
                    related_task_id=None,
                    related_review_task_id=None,
                    recipient_type="role",
                    recipient="operations",
                    channel="feishu",
                    sent_at=None,
                    send_status=NotificationSendStatus.PENDING.value,
                    dedupe_key="feishu|1",
                    message="feishu message",
                )
            )
        self.assertEqual(result.send_status, NotificationSendStatus.FAILED.value)
        self.assertIn("FEISHU_WEBHOOK_URL", result.error_message)

    def test_feishu_sender_signs_request_and_returns_success(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def getcode(self):
                return 200

            def read(self):
                return b'{"code":0,"msg":"success","request_id":"REQ-1"}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch.dict(
            "os.environ",
            {
                "FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook",
                "FEISHU_WEBHOOK_SECRET": "sign-secret",
                "FEISHU_WEBHOOK_TIMEOUT_SECONDS": "7",
                "FEISHU_MESSAGE_TYPE": "text",
            },
            clear=False,
        ), patch("app.services.runtime.time.time", return_value=1234567890), patch(
            "app.services.runtime.urlopen",
            side_effect=fake_urlopen,
        ):
            result = FeishuWebhookNotificationSender().send(
                NotificationLog(
                    notification_id="N-1",
                    related_task_id=None,
                    related_review_task_id=None,
                    recipient_type="role",
                    recipient="operations",
                    channel="feishu",
                    sent_at=None,
                    send_status=NotificationSendStatus.PENDING.value,
                    dedupe_key="feishu|1",
                    message="log summary",
                ),
                {"text": "review message with mobile url"},
            )

        self.assertEqual(result.send_status, NotificationSendStatus.SUCCESS.value)
        self.assertIsNotNone(result.sent_at)
        self.assertEqual(result.provider_message_id, "REQ-1")
        self.assertEqual(captured["timeout"], 7.0)
        self.assertEqual(captured["body"]["timestamp"], "1234567890")
        self.assertEqual(captured["body"]["sign"], _build_feishu_sign("1234567890", "sign-secret"))
        self.assertEqual(captured["body"]["content"]["text"], "review message with mobile url")

    def test_feishu_sender_builds_post_message_by_default(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def getcode(self):
                return 200

            def read(self):
                return b'{"code":0,"msg":"success"}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        log = NotificationLog(
            notification_id="N-1",
            related_task_id=None,
            related_review_task_id=None,
            recipient_type="role",
            recipient="operations",
            channel="feishu",
            sent_at=None,
            send_status=NotificationSendStatus.PENDING.value,
            dedupe_key="feishu|1",
            message="log summary",
        )
        payload = {
            "review_type": "manual_review",
            "review_type_label": "人工复核",
            "trade_date": "2026-05-05",
            "scope_type": "global",
            "scope_key": "mobile_review_test",
            "required_by": "2026-05-06 09:01",
            "reason": "mobile review test task",
            "mobile_review_url": "https://pra.example/mobile/review/R-1?token=secret",
        }
        with patch.dict(
            "os.environ",
            {
                "FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook",
                "FEISHU_MESSAGE_TYPE": "",
            },
            clear=False,
        ), patch("app.services.runtime.urlopen", side_effect=fake_urlopen):
            result = FeishuWebhookNotificationSender().send(log, payload)

        self.assertEqual(result.send_status, NotificationSendStatus.SUCCESS.value)
        body = captured["body"]
        self.assertEqual(body["msg_type"], "post")
        post = body["content"]["post"]["zh_cn"]
        self.assertEqual(post["title"], "PRA 复核通知")
        flattened = [part for row in post["content"] for part in row]
        self.assertIn({"tag": "a", "text": "👉 点击处理复核", "href": payload["mobile_review_url"]}, flattened)
        self.assertTrue(any("人工复核" in part.get("text", "") for part in flattened))

    def test_feishu_sender_uses_specialized_shadowbot_login_handoff_layout(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def getcode(self):
                return 200

            def read(self):
                return b'{"code":0,"msg":"success"}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        log = NotificationLog(
            notification_id="N-shadowbot-login",
            related_task_id="TASK-1",
            related_review_task_id="LOGIN-VERIFY-1",
            recipient_type="role",
            recipient="operations",
            channel="feishu",
            sent_at=None,
            send_status=NotificationSendStatus.PENDING.value,
            dedupe_key="shadowbot-login|1",
            message="ShadowBot 登录验证码人工接管",
        )
        payload = {
            "notification_kind": "shadowbot_login_verification",
            "title": "ShadowBot 登录验证码人工接管",
            "platform_name": "蚂蚁花团供应商",
            "execution_attempt_id": "ATTEMPT-LOGIN-1",
            "required_by": "2026-07-12T12:00+08:00",
            "action": "请在已打开的桌面端微信小程序中完成手机验证码；完成后 Worker 将继续原任务。",
        }
        with patch.dict(
            "os.environ",
            {"FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook", "FEISHU_MESSAGE_TYPE": "post"},
            clear=False,
        ), patch("app.services.runtime.urlopen", side_effect=fake_urlopen):
            result = FeishuWebhookNotificationSender().send(log, payload)

        self.assertEqual(result.send_status, NotificationSendStatus.SUCCESS.value)
        post = captured["body"]["content"]["post"]["zh_cn"]
        self.assertEqual(post["title"], "ShadowBot 登录验证码人工接管")
        lines = [part["text"] for row in post["content"] for part in row]
        self.assertIn("平台：蚂蚁花团供应商", lines)
        self.assertIn("执行尝试：ATTEMPT-LOGIN-1", lines)
        self.assertIn("截止时间：2026-07-12T12:00+08:00", lines)
        self.assertFalse(any(line.startswith("业务日期：") for line in lines))
        self.assertFalse(any(line.startswith("处理对象：") for line in lines))
        self.assertFalse(any(line.startswith("原因：") for line in lines))

    def test_feishu_sender_failed_response_and_http_exception(self) -> None:
        class FailedResponse:
            def getcode(self):
                return 200

            def read(self):
                return b'{"code":19001,"msg":"bad sign"}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        log = NotificationLog(
            notification_id="N-1",
            related_task_id=None,
            related_review_task_id=None,
            recipient_type="role",
            recipient="operations",
            channel="feishu",
            sent_at=None,
            send_status=NotificationSendStatus.PENDING.value,
            dedupe_key="feishu|1",
            message="summary",
        )
        with patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook"}, clear=False), patch(
            "app.services.runtime.urlopen",
            return_value=FailedResponse(),
        ):
            failed_response = FeishuWebhookNotificationSender().send(log, {"text": "message"})
        with patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook"}, clear=False), patch(
            "app.services.runtime.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            failed_exception = FeishuWebhookNotificationSender().send(log, {"text": "message"})

        self.assertEqual(failed_response.send_status, NotificationSendStatus.FAILED.value)
        self.assertEqual(failed_response.raw_response_json["code"], 19001)
        self.assertIn("bad sign", failed_response.error_message)
        self.assertEqual(failed_exception.send_status, NotificationSendStatus.FAILED.value)
        self.assertIn("TimeoutError", failed_exception.error_message)

    def test_feishu_sender_rejects_unknown_message_type(self) -> None:
        log = NotificationLog(
            notification_id="N-1",
            related_task_id=None,
            related_review_task_id=None,
            recipient_type="role",
            recipient="operations",
            channel="feishu",
            sent_at=None,
            send_status=NotificationSendStatus.PENDING.value,
            dedupe_key="feishu|1",
            message="summary",
        )
        with patch.dict(
            "os.environ",
            {
                "FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook",
                "FEISHU_MESSAGE_TYPE": "card",
            },
            clear=False,
        ), patch("app.services.runtime.urlopen") as mocked_urlopen:
            result = FeishuWebhookNotificationSender().send(log, {"text": "message"})

        self.assertEqual(result.send_status, NotificationSendStatus.FAILED.value)
        self.assertIn("FEISHU_MESSAGE_TYPE", result.error_message)
        mocked_urlopen.assert_not_called()

    def test_review_task_creation_also_creates_initial_notification_log(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)

        with patch.dict(
            "os.environ",
            {
                "DEFAULT_NOTIFICATION_RECIPIENT_TYPE": "role",
                "DEFAULT_NOTIFICATION_RECIPIENT": "operations",
                "DEFAULT_NOTIFICATION_CHANNEL": "mock",
            },
            clear=False,
        ):
            summary = review_service.create_from_tasks([source])

        self.assertEqual(summary.inserted_review_tasks_count, 1)
        self.assertEqual(summary.inserted_notification_logs_count, 1)
        self.assertEqual(summary.notification_errors, [])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        logs = NotificationLogService(self.repository).list_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].related_review_task_id, review.review_task_id)
        self.assertEqual(logs[0].related_task_id, source.task_id)
        self.assertEqual(logs[0].send_status, NotificationSendStatus.PENDING.value)
        self.assertIsNone(logs[0].sent_at)
        self.assertIn("产能预警", logs[0].message)
        self.assertNotIn("decision_trace", logs[0].message)

    def test_repeated_review_business_event_returns_existing_without_notification_error(self) -> None:
        source = _runtime_task("TASK-REPEATED-REVIEW")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        with patch.dict("os.environ", {"DEFAULT_NOTIFICATION_CHANNEL": "FeIsHu"}, clear=False):
            first = review_service.create_from_tasks([source])
            second = review_service.create_from_tasks([source])
        self.assertEqual(first.inserted_review_tasks_count, 1)
        self.assertEqual(second.inserted_review_tasks_count, 0)
        self.assertEqual(second.notification_errors, [])
        self.assertEqual(len(self.repository.list_review_tasks()), 1)
        self.assertEqual(len(self.repository.list_notification_outbox()), 1)
        self.assertEqual(self.repository.list_notification_outbox()[0].channel, "feishu")
        self.assertEqual(len(self.repository.list_notification_logs()), 1)

    def test_feishu_notification_flow_sends_url_but_does_not_persist_raw_token(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        with patch.dict(
            "os.environ",
            {
                "DEFAULT_NOTIFICATION_CHANNEL": "feishu",
                "FEISHU_MESSAGE_TYPE": "post",
                "FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook",
                "REVIEW_TOKEN_SECRET": "test-secret",
                "MOBILE_REVIEW_BASE_URL": "https://pra.example",
            },
            clear=False,
        ), patch("app.services.runtime.urlopen") as mocked_urlopen:
            summary = review_service.create_from_tasks([source])

        self.assertEqual(summary.inserted_review_tasks_count, 1)
        self.assertEqual(summary.inserted_notification_logs_count, 1)
        mocked_urlopen.assert_not_called()
        logs = NotificationLogService(self.repository).list_logs()
        self.assertEqual(logs[0].channel, "feishu")
        self.assertEqual(logs[0].send_status, NotificationSendStatus.PENDING.value)
        self.assertNotIn("token=", logs[0].message)
        outbox = self.repository.list_notification_outbox(related_review_task_id=logs[0].related_review_task_id)[0]
        self.assertEqual(outbox.status, "PENDING")
        self.assertNotIn("token", json.dumps(outbox.payload).lower())

    def test_feishu_notification_flow_can_send_text_message(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        with patch.dict(
            "os.environ",
            {
                "DEFAULT_NOTIFICATION_CHANNEL": "feishu",
                "FEISHU_MESSAGE_TYPE": "text",
                "FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook",
                "REVIEW_TOKEN_SECRET": "test-secret",
                "MOBILE_REVIEW_BASE_URL": "https://pra.example",
            },
            clear=False,
        ), patch("app.services.runtime.urlopen") as mocked_urlopen:
            summary = review_service.create_from_tasks([source])

        self.assertEqual(summary.inserted_review_tasks_count, 1)
        self.assertEqual(summary.inserted_notification_logs_count, 1)
        mocked_urlopen.assert_not_called()
        outbox = self.repository.list_notification_outbox()[0]
        self.assertEqual(outbox.channel, "feishu")
        self.assertEqual(outbox.status, "PENDING")

    def test_feishu_notification_fails_when_review_url_cannot_be_created(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        with patch.dict(
            "os.environ",
            {
                "DEFAULT_NOTIFICATION_CHANNEL": "feishu",
                "FEISHU_WEBHOOK_URL": "https://open.feishu.test/webhook",
            },
            clear=True,
        ), patch("app.services.runtime.urlopen") as mocked_urlopen:
            summary = review_service.create_from_tasks([source])

        self.assertEqual(summary.inserted_review_tasks_count, 1)
        self.assertEqual(summary.inserted_notification_logs_count, 1)
        mocked_urlopen.assert_not_called()
        logs = NotificationLogService(self.repository).list_logs()
        self.assertEqual(logs[0].send_status, NotificationSendStatus.PENDING.value)
        self.assertEqual(logs[0].error_message, "")

    def test_notification_failure_does_not_block_review_creation(self) -> None:
        class FailingReviewNotificationService(ReviewNotificationService):
            def create_initial_notification(self, review_task):
                raise RuntimeError("mock notification failure")

        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(
            self.repository,
            runtime_task_service=self.task_service,
            notification_service=FailingReviewNotificationService(self.repository),
        )
        summary = review_service.create_from_tasks([source])
        self.assertEqual(summary.inserted_review_tasks_count, 1)
        self.assertEqual(summary.inserted_notification_logs_count, 0)
        self.assertEqual(len(summary.notification_errors), 1)
        self.assertEqual(len(review_service.list_review_tasks()), 1)
        self.assertEqual(len(NotificationLogService(self.repository).list_logs()), 0)

    def test_notification_log_dedupes_by_dedupe_key(self) -> None:
        service = NotificationLogService(self.repository)
        log = NotificationLog(
            notification_id="N-1",
            related_task_id=None,
            related_review_task_id=None,
            recipient_type="role",
            recipient="operator",
            channel="mock",
            sent_at=datetime(2026, 5, 4, 10, 0),
            send_status=NotificationSendStatus.SUCCESS.value,
            dedupe_key="notice|2026-05-04|operator",
            message="capacity warning",
        )
        duplicate = NotificationLog(
            notification_id="N-2",
            related_task_id=None,
            related_review_task_id=None,
            recipient_type="role",
            recipient="operator",
            channel="mock",
            sent_at=datetime(2026, 5, 4, 10, 1),
            send_status=NotificationSendStatus.SUCCESS.value,
            dedupe_key="notice|2026-05-04|operator",
            message="capacity warning again",
        )
        self.assertEqual(service.append(log), 1)
        self.assertEqual(self.repository.insert_notification_logs([duplicate]), 0)
        self.assertEqual(len(service.list_logs()), 1)

    def test_review_task_can_resolve_and_update_source_through_runtime_service(self) -> None:
        source = _runtime_task("TASK-1", status=TaskStatus.MANUAL_REVIEW)
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        summary = review_service.create_from_tasks([source])
        self.assertEqual(summary.inserted_review_tasks_count, 1)

        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        resolved = review_service.resolve_review_task(
            review_task_id=review.review_task_id,
            status=ReviewTaskStatus.REJECTED,
            actor="alice",
            note="not enough capacity",
            source_task_status=TaskStatus.CANCELLED,
        )
        self.assertEqual(resolved.review_status, ReviewTaskStatus.REJECTED)
        task = self.task_service.list_tasks()[0]
        self.assertEqual(task.task_status, TaskStatus.CANCELLED)

    def test_review_task_can_close_pending_operational_source_task(self) -> None:
        source = _runtime_task(
            "TASK-OPS-1",
            status=TaskStatus.PENDING,
            action_type=TaskActionType.LABOR_REQUIRED,
        )
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])

        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        review_service.resolve_review_task(
            review_task_id=review.review_task_id,
            status=ReviewTaskStatus.APPROVED,
            actor="alice",
            actor_source="session_user",
            source_task_status=TaskStatus.SKIPPED,
        )

        task = self.task_service.get_task(source.task_id)
        history = self.task_service.list_status_history(source.task_id)
        self.assertEqual(task.task_status, TaskStatus.SKIPPED)
        self.assertEqual(history[-1].from_status, TaskStatus.PENDING)
        self.assertEqual(history[-1].to_status, TaskStatus.SKIPPED)

    def test_review_task_requires_pending_and_writes_metadata_to_history(self) -> None:
        source = _runtime_task("TASK-1", status=TaskStatus.MANUAL_REVIEW)
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])

        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        review_service.resolve_review_task(
            review_task_id=review.review_task_id,
            status=ReviewTaskStatus.APPROVED,
            actor="alice",
            actor_source="session_user",
            note="approved in web",
            resolution_payload={"reviewer_code": "R-001", "adjustment": {"target_price": "8.8"}},
            source_task_status=TaskStatus.PENDING,
        )
        history = self.task_service.list_status_history("TASK-1")
        self.assertEqual(history[-1].metadata["review_task_id"], review.review_task_id)
        self.assertEqual(history[-1].metadata["actor_source"], "session_user")
        self.assertTrue(history[-1].metadata["resolution_payload_summary"]["reviewer_code_present"])

        with self.assertRaises(Exception):
            review_service.resolve_review_task(
                review_task_id=review.review_task_id,
                status=ReviewTaskStatus.REJECTED,
                actor="bob",
            )

    def test_expire_pending_review_tasks_supports_dry_run_and_apply(self) -> None:
        source = _runtime_task(
            "TASK-1",
            status=TaskStatus.MANUAL_REVIEW,
            action_type=TaskActionType.LABOR_REQUIRED,
            required_by=datetime(2026, 5, 4, 8, 0),
        )
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]

        dry_run = review_service.expire_pending_review_tasks(
            now=datetime(2026, 5, 4, 9, 30),
            apply=False,
        )
        self.assertEqual(dry_run.scanned_review_tasks, 1)
        self.assertEqual(dry_run.expired_review_tasks, 1)
        self.assertEqual(review_service.get_review_task(review.review_task_id).review_status, ReviewTaskStatus.PENDING)
        self.assertEqual(self.task_service.get_task(source.task_id).task_status, TaskStatus.MANUAL_REVIEW)

        applied = review_service.expire_pending_review_tasks(
            now=datetime(2026, 5, 4, 9, 30),
            apply=True,
            enable_notification=True,
        )
        self.assertEqual(applied.expired_review_tasks, 1)
        self.assertEqual(applied.expired_source_tasks, 1)
        updated_review = review_service.get_review_task(review.review_task_id)
        updated_task = self.task_service.get_task(source.task_id)
        self.assertEqual(updated_review.review_status, ReviewTaskStatus.EXPIRED)
        self.assertEqual(updated_task.task_status, TaskStatus.EXPIRED)
        history = self.task_service.list_status_history(source.task_id)
        self.assertEqual(history[-1].metadata["timeout_policy"], "uniform_conservative_v1")
        self.assertTrue(history[-1].metadata["fallback_to_safe_default"])
        self.assertEqual(history[-1].metadata["confirmed_temp_worker_count"], 0)
        self.assertEqual(history[-1].metadata["confirmed_packing_capacity_qty"], 250)
        self.assertEqual(applied.notification_logs_created, 1)
        outboxes = self.repository.list_notification_outbox(related_review_task_id=review.review_task_id)
        self.assertEqual(
            sorted(row.status for row in outboxes),
            [NotificationOutboxStatus.CANCELLED.value, NotificationOutboxStatus.PENDING.value],
        )
        self.assertEqual(
            [row.notification_type for row in outboxes if row.status == NotificationOutboxStatus.PENDING.value],
            ["review_expired"],
        )

    def test_expire_pending_review_tasks_skips_non_manual_review_source(self) -> None:
        source = _runtime_task(
            "TASK-1",
            status=TaskStatus.PENDING,
            action_type=TaskActionType.CAPACITY_WARNING,
            required_by=datetime(2026, 5, 4, 8, 0),
        )
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])

        summary = review_service.expire_pending_review_tasks(
            now=datetime(2026, 5, 4, 9, 0),
            apply=True,
        )
        self.assertEqual(summary.expired_review_tasks, 1)
        self.assertEqual(summary.expired_source_tasks, 0)
        self.assertEqual(summary.skipped_source_tasks, 1)
        self.assertEqual(self.task_service.get_task(source.task_id).task_status, TaskStatus.PENDING)

    def test_review_token_create_uses_hmac_hash_and_does_not_store_raw_token(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        token_service = ReviewTokenService(self.repository)

        with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "test-secret"}, clear=False):
            result = token_service.create_token(review.review_task_id, token_subject="operations")

        self.assertNotEqual(result.review_token.token_hash, result.raw_token)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT token_hash FROM review_tokens WHERE token_id = ?",
                (result.review_token.token_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], result.review_token.token_hash)
        self.assertNotEqual(row[0], result.raw_token)
        self.assertTrue(result.mobile_review_url.startswith("/mobile/review/"))

    def test_review_token_requires_secret_and_pending_review_task(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        token_service = ReviewTokenService(self.repository)

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(Exception):
                token_service.create_token(review.review_task_id)

        review_service.resolve_review_task(
            review_task_id=review.review_task_id,
            status=ReviewTaskStatus.CANCELLED,
            actor="alice",
        )
        with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "test-secret"}, clear=False):
            with self.assertRaises(Exception):
                token_service.create_token(review.review_task_id)

    def test_review_token_default_expiry_uses_required_by_or_twenty_four_hours(self) -> None:
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 5, 4, 10, 0)

        token_service = ReviewTokenService(self.repository)
        with patch("app.services.runtime.datetime", FixedDateTime), patch.dict(
            "os.environ",
            {"REVIEW_TOKEN_SECRET": "test-secret"},
            clear=False,
        ):
            source_early = _runtime_task("TASK-1", required_by=FixedDateTime(2026, 5, 4, 12, 0))
            source_late = _runtime_task("TASK-2", required_by=FixedDateTime(2026, 5, 6, 12, 0))
            source_empty = _runtime_task("TASK-3")
            self.task_service.create_tasks([source_early, source_late, source_empty])
            review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
            review_service.create_from_tasks([source_early, source_late, source_empty])
            reviews = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)
            by_source = {review.source_task_id: review for review in reviews}

            early = token_service.create_token(by_source["TASK-1"].review_task_id)
            late = token_service.create_token(by_source["TASK-2"].review_task_id)
            empty = token_service.create_token(by_source["TASK-3"].review_task_id)

        self.assertEqual(early.review_token.expires_at, datetime(2026, 5, 4, 12, 0))
        self.assertEqual(late.review_token.expires_at, datetime(2026, 5, 5, 10, 0))
        self.assertEqual(empty.review_token.expires_at, datetime(2026, 5, 5, 10, 0))

    def test_review_token_validation_failure_modes_and_url_base(self) -> None:
        future = datetime.now() + timedelta(hours=2)
        source = _runtime_task("TASK-1", required_by=future)
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        token_service = ReviewTokenService(self.repository)

        with patch.dict(
            "os.environ",
            {"REVIEW_TOKEN_SECRET": "test-secret", "MOBILE_REVIEW_BASE_URL": "https://example.test/app/"},
            clear=False,
        ):
            result = token_service.create_token(review.review_task_id, allowed_actions=["approved"])
            valid = token_service.validate_token(review.review_task_id, result.raw_token, "approved")
            wrong_action = token_service.validate_token(review.review_task_id, result.raw_token, "rejected")
            wrong_review = token_service.validate_token("missing-review", result.raw_token, "approved")

        self.assertTrue(valid.is_valid)
        self.assertEqual(valid.token_subject, "operations")
        self.assertFalse(wrong_action.is_valid)
        self.assertEqual(wrong_action.failure_reason, "action not allowed by token")
        self.assertFalse(wrong_review.is_valid)
        self.assertEqual(result.mobile_review_url.count("//"), 1)
        self.assertTrue(result.mobile_review_url.startswith("https://example.test/app/mobile/review/"))

    def test_review_token_usage_and_revoke_invalidate_token(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        token_service = ReviewTokenService(self.repository)

        with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "test-secret"}, clear=False):
            result = token_service.create_token(review.review_task_id)
            accessed = token_service.record_detail_access(result.review_token.token_id)
            used = token_service.record_resolve_usage(result.review_token.token_id)
            validation = token_service.validate_token(review.review_task_id, result.raw_token, "approved")

        self.assertIsNotNone(accessed.last_used_at)
        self.assertIsNotNone(used.used_at)
        self.assertIsNotNone(used.last_used_at)
        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.failure_reason, "token already used")
        with self.assertRaises(Exception):
            token_service.record_resolve_usage(result.review_token.token_id)

        with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "test-secret"}, clear=False):
            second = token_service.create_token(review.review_task_id)
            token_service.revoke_token(second.review_token.token_id)
            revoked = token_service.validate_token(review.review_task_id, second.raw_token, "approved")
        self.assertFalse(revoked.is_valid)
        self.assertEqual(revoked.failure_reason, "token revoked")

    def test_review_token_bulk_revoke_and_non_pending_review_invalid(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        token_service = ReviewTokenService(self.repository)

        with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "test-secret"}, clear=False):
            first = token_service.create_token(review.review_task_id)
            second = token_service.create_token(review.review_task_id)
            revoked_count = token_service.revoke_tokens_for_review_task(review.review_task_id)
            first_validation = token_service.validate_token(review.review_task_id, first.raw_token, "approved")
            second_validation = token_service.validate_token(review.review_task_id, second.raw_token, "approved")
        self.assertEqual(revoked_count, 2)
        self.assertEqual(first_validation.failure_reason, "token revoked")
        self.assertEqual(second_validation.failure_reason, "token revoked")

        with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "test-secret"}, clear=False):
            third = token_service.create_token(review.review_task_id)
            review_service.resolve_review_task(
                review_task_id=review.review_task_id,
                status=ReviewTaskStatus.APPROVED,
                actor="alice",
            )
            validation = token_service.validate_token(review.review_task_id, third.raw_token, "approved")
        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.failure_reason, "review task is not pending")


if __name__ == "__main__":
    unittest.main()
