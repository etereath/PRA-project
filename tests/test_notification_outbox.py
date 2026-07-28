from __future__ import annotations

import json
import tempfile
import threading
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

import pytest

from app.enums import (
    DeliveryAttemptStatus,
    NotificationOutboxStatus,
    ReviewTaskStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.exceptions import (
    NotificationChannelMismatchError,
    NotificationDeliveryError,
    NotificationIdempotencyConflictError,
    NotificationLeaseError,
)
from app.models import (
    NotificationDeliveryAttempt,
    NotificationDeliveryResult,
    NotificationOutbox,
    ReviewTask,
    Task,
    TaskStatusHistory,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION
from app.services.notification_outbox import (
    FakeSender,
    FeishuOutboxSender,
    NotificationChannelRegistry,
    NotificationOutboxService,
    NotificationOutboxWorker,
    OutboxReviewNotificationService,
    ScriptedSender,
)
from app.services import notification_outbox as notification_outbox_module
from app.services.feishu import build_feishu_signature
from app.services.runtime import ReviewTaskService


def _review(review_id: str = "REVIEW-1") -> ReviewTask:
    return ReviewTask(
        review_task_id=review_id,
        trade_date=None,
        scope_type="global",
        scope_key="test",
        dedupe_key=f"dedupe:{review_id}",
        source_task_id=None,
        review_type="manual_review",
        review_status=ReviewTaskStatus.PENDING,
        reason="需要人工确认",
        created_at=datetime(2026, 7, 17, 10, 0),
        updated_at=datetime(2026, 7, 17, 10, 0),
    )


def _notification(notification_id: str = "N-1", *, priority: int = 1) -> NotificationOutbox:
    now = datetime(2026, 7, 17, 10, 0)
    return NotificationOutbox(
        notification_id=notification_id,
        notification_key=f"test:{notification_id}:v1:fake:operator",
        notification_type="test_notification",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        priority=priority,
        payload={"message": "safe test payload"},
        created_at=now,
        updated_at=now,
    )


def _source_task(task_id: str) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=None,
        platform_name=None,
        action_type=TaskActionType.LABOR_REQUIRED,
        priority=2,
        task_status=TaskStatus.MANUAL_REVIEW,
        created_at=datetime(2026, 7, 17, 9, 0),
        origin_type=TaskOriginType.MANUAL,
        trade_date=datetime(2026, 7, 17).date(),
        scope_type="global",
        scope_key="2026-07-17",
        dedupe_key=f"source:{task_id}",
        result_message="needs review",
        required_by=datetime(2026, 7, 17, 10, 30),
    )


@pytest.fixture()
def repository():
    with tempfile.TemporaryDirectory() as directory:
        repo = SQLiteRuntimeRepository(Path(directory) / "runtime.sqlite3")
        repo.init_schema()
        yield repo


def test_schema_v6_has_outbox_and_attempt_health(repository):
    health = repository.check_schema_health()
    assert health.ok, health.summary
    assert repository.schema_versions() == list(
        range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1)
    )
    connection = repository.connect_read()
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert {"notification_outbox", "notification_delivery_attempts", "notification_logs"} <= tables


def test_schema_health_rejects_each_critical_v6_numeric_and_attempt_key_constraint(repository):
    with closing(sqlite3.connect(repository.db_path)) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE notification_delivery_attempts")
        connection.execute(
            """
            CREATE TABLE notification_delivery_attempts (
                delivery_attempt_id TEXT PRIMARY KEY,
                notification_id TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('STARTED', 'ACKNOWLEDGED', 'TEMP_FAILED', 'PERM_FAILED', 'UNKNOWN')),
                lease_owner_token TEXT NOT NULL,
                lease_version INTEGER NOT NULL,
                request_fingerprint TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                provider_status_code TEXT NOT NULL DEFAULT '',
                provider_message_id TEXT NOT NULL DEFAULT '',
                response_fingerprint TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(notification_id) REFERENCES notification_outbox(notification_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX ix_notification_delivery_attempts_notification_id ON notification_delivery_attempts(notification_id)"
        )
        connection.commit()
    health = repository.check_schema_health()
    assert not health.ok
    assert any("attempt_no lacks required numeric CHECK" in error for error in health.constraint_errors)
    assert any("notification_delivery_attempts.lease_version lacks required numeric CHECK" in error for error in health.constraint_errors)
    assert any("UNIQUE(notification_id, attempt_no)" in error for error in health.constraint_errors)


def test_schema_health_rejects_missing_outbox_numeric_checks(repository):
    with closing(sqlite3.connect(repository.db_path)) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE notification_delivery_attempts")
        connection.execute("DROP TABLE notification_outbox")
        connection.execute(
            """
            CREATE TABLE notification_outbox (
                notification_id TEXT PRIMARY KEY,
                notification_key TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                related_task_id TEXT,
                related_review_task_id TEXT,
                recipient_type TEXT NOT NULL,
                recipient_ref TEXT NOT NULL,
                channel TEXT NOT NULL,
                priority INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PENDING', 'LEASED', 'SENDING', 'RETRY_WAIT', 'SENT', 'UNKNOWN_DELIVERY', 'FAILED', 'EXPIRED', 'CANCELLED')),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL,
                next_attempt_at TEXT,
                deadline_at TEXT,
                lease_owner_token TEXT NOT NULL DEFAULT '',
                lease_version INTEGER NOT NULL DEFAULT 0,
                lease_expires_at TEXT,
                sent_at TEXT,
                provider_message_id TEXT NOT NULL DEFAULT '',
                last_error_code TEXT NOT NULL DEFAULT '',
                last_error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(related_task_id) REFERENCES tasks(task_id),
                FOREIGN KEY(related_review_task_id) REFERENCES review_tasks(review_task_id)
            )
            """
        )
        connection.execute("CREATE UNIQUE INDEX ux_notification_outbox_key ON notification_outbox(notification_key)")
        connection.execute(
            "CREATE INDEX ix_notification_outbox_claim ON notification_outbox(status, priority, next_attempt_at, deadline_at, created_at)"
        )
        connection.execute("CREATE INDEX ix_notification_outbox_lease_expires_at ON notification_outbox(lease_expires_at)")
        connection.execute(
            """
            CREATE TABLE notification_delivery_attempts (
                delivery_attempt_id TEXT PRIMARY KEY,
                notification_id TEXT NOT NULL,
                attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
                status TEXT NOT NULL CHECK (status IN ('STARTED', 'ACKNOWLEDGED', 'TEMP_FAILED', 'PERM_FAILED', 'UNKNOWN')),
                lease_owner_token TEXT NOT NULL,
                lease_version INTEGER NOT NULL CHECK (lease_version >= 0),
                request_fingerprint TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                provider_status_code TEXT NOT NULL DEFAULT '',
                provider_message_id TEXT NOT NULL DEFAULT '',
                response_fingerprint TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                UNIQUE(notification_id, attempt_no),
                FOREIGN KEY(notification_id) REFERENCES notification_outbox(notification_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX ix_notification_delivery_attempts_notification_id ON notification_delivery_attempts(notification_id)"
        )
        connection.commit()
    health = repository.check_schema_health()
    assert not health.ok
    assert any("notification_outbox.attempt_count lacks required numeric CHECK" in error for error in health.constraint_errors)
    assert any("notification_outbox.max_attempts lacks required numeric CHECK" in error for error in health.constraint_errors)
    assert any("notification_outbox.lease_version lacks required numeric CHECK" in error for error in health.constraint_errors)


def test_duplicate_notification_key_returns_existing_row_and_secrets_are_rejected(repository):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    first = service.enqueue(
        notification_type="test",
        notification_key="test:entity:v1:fake:operator",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
    )
    same = service.enqueue(
        notification_type="test",
        notification_key="test:entity:v1:fake:operator",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
    )
    assert same.notification_id == first.notification_id
    with pytest.raises(NotificationIdempotencyConflictError):
        service.enqueue(
            notification_type="test",
            notification_key="test:entity:v1:fake:operator",
            recipient_type="role",
            recipient_ref="operator",
            channel="fake",
            payload={"message": "different payload"},
        )
    assert len(repository.list_notification_outbox()) == 1
    with pytest.raises(ValueError, match="forbidden secret"):
        service.enqueue(
            notification_type="test",
            notification_key="test:secret:v1:fake:operator",
            recipient_type="role",
            recipient_ref="operator",
            channel="fake",
            payload={"access_token": "do-not-store"},
        )


def test_notification_key_is_unambiguous_and_channel_mismatch_never_starts_attempt(repository):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    assert service.notification_key("test", "a:b", "v1", "fake", "operator") != service.notification_key(
        "test", "a", "b:v1", "fake", "operator"
    )
    service.enqueue(
        notification_type="test",
        notification_key="mismatch-key",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
    )
    with pytest.raises(NotificationChannelMismatchError):
        service.deliver_once(ScriptedSender(), channel="fake", now=datetime(2026, 7, 17, 10, 0))
    row = repository.get_notification_outbox_by_key("mismatch-key")
    assert row is not None
    assert row.status == NotificationOutboxStatus.PENDING.value
    assert repository.list_notification_delivery_attempts(row.notification_id) == []


def test_payload_schema_rejects_nested_secret_values_and_verification_overrides(repository):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    with pytest.raises(ValueError, match="secret-like"):
        service.enqueue(
            notification_type="test",
            notification_key="secret-value",
            recipient_type="role",
            recipient_ref="operator",
            channel="fake",
            payload={"message": "Authorization: Bearer abc123"},
        )
    with pytest.raises(ValueError, match="not allowed"):
        service.enqueue_verification_intervention(
            operation_id="OP-1",
            attempt_id="ATT-1",
            recipient_type="role",
            recipient_ref="operator",
            channel="fake",
            payload={"message": "override"},
        )


def test_provider_error_persistence_is_redacted(repository):
    now = datetime(2026, 7, 17, 10, 0)
    service = NotificationOutboxService(repository, clock=lambda: now)
    notification = service.enqueue(
        notification_type="test",
        notification_key="provider-error-redaction",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
    )
    claimed = repository.claim_notification_outbox(now=now, channel="fake")[0]
    attempt = repository.begin_notification_delivery(
        claimed.notification_id,
        owner_token=claimed.lease_owner_token,
        lease_version=claimed.lease_version,
        request_fingerprint="fingerprint",
        now=now,
    )
    final = repository.complete_notification_delivery(
        notification.notification_id,
        attempt.delivery_attempt_id,
        owner_token=claimed.lease_owner_token,
        lease_version=claimed.lease_version,
        result=NotificationDeliveryResult(
            classification="TEMP_FAILED",
            error_code="TEMP_PROVIDER_FAILURE",
            error_message="Authorization: Bearer super-secret https://open.feishu.cn/hook/private",
        ),
        now=now,
    )
    assert "super-secret" not in final.last_error_message
    assert "open.feishu.cn" not in final.last_error_message
    log = repository.get_notification_log(notification.notification_id)
    assert log is not None
    assert "super-secret" not in log.error_message
    assert "open.feishu.cn" not in log.error_message
def test_channel_registry_binds_feishu_and_provider_result_is_bounded(repository, monkeypatch):
    registry = NotificationChannelRegistry()
    assert registry.build("feishu").channel == "feishu"
    assert registry.build("fake").channel == "fake"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

        def read(self):
            return b'{"code":0,"msg":"success","request_id":"req-1"}'

    monkeypatch.setattr(notification_outbox_module, "urlopen", lambda *args, **kwargs: Response())
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/hook/test")
    notification = _notification("FEISHU-1")
    notification.channel = "feishu"
    result = FeishuOutboxSender().send(
        notification,
        NotificationDeliveryAttempt(
            delivery_attempt_id="ATT-1",
            notification_id=notification.notification_id,
            attempt_no=1,
            status="STARTED",
            lease_owner_token="owner",
            lease_version=1,
            request_fingerprint="request",
            started_at=datetime(2026, 7, 17, 10, 0),
        ),
    )
    assert result.classification == "SUCCESS"
    assert result.provider_message_id == "req-1"


@pytest.mark.parametrize("channel", ["mock", "fake", "scripted"])
def test_default_worker_rejects_all_test_channels(repository, channel):
    with pytest.raises(NotificationDeliveryError, match="explicitly enabled test worker"):
        NotificationOutboxWorker.for_channel(repository, channel)


def test_feishu_outbox_uses_official_signature_vector_and_response_code_matrix(monkeypatch):
    assert build_feishu_signature("1234567890", "sign-secret") == (
        "CgzXNZVOeFF3tZW7JDVpuevxS8czITsTOclPQeDiF9c="
    )
    captured: dict[str, object] = {}
    response_body = [b'{"StatusCode":0,"StatusMessage":"success"}']

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

        def read(self):
            return response_body[0]

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr(notification_outbox_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(notification_outbox_module.time, "time", lambda: 1234567890)
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/hook/test")
    monkeypatch.setenv("FEISHU_WEBHOOK_SECRET", "sign-secret")
    notification = _notification("FEISHU-SIGNED")
    notification.channel = "feishu"
    attempt = NotificationDeliveryAttempt(
        delivery_attempt_id="ATT-SIGNED",
        notification_id=notification.notification_id,
        attempt_no=1,
        status="STARTED",
        lease_owner_token="owner",
        lease_version=1,
        request_fingerprint="request",
        started_at=datetime(2026, 7, 17, 10, 0),
    )
    success = FeishuOutboxSender().send(notification, attempt)
    assert success.classification == "SUCCESS"
    assert captured["body"]["timestamp"] == "1234567890"
    assert captured["body"]["sign"] == "CgzXNZVOeFF3tZW7JDVpuevxS8czITsTOclPQeDiF9c="

    response_body[0] = b'{"StatusCode":19001,"StatusMessage":"bad sign"}'
    failed = FeishuOutboxSender().send(notification, attempt)
    assert failed.classification == "PERM_FAILED"
    assert failed.error_code == "FEISHU_PROVIDER_REJECTED"

    response_body[0] = b'{"code":19002,"msg":"rejected"}'
    failed_code = FeishuOutboxSender().send(notification, attempt)
    assert failed_code.classification == "PERM_FAILED"

    response_body[0] = b'{"code":0,"StatusCode":19003,"StatusMessage":"rejected"}'
    conflicting = FeishuOutboxSender().send(notification, attempt)
    assert conflicting.classification == "PERM_FAILED"

    for body in (b"{}", b'{"msg":"success"}'):
        response_body[0] = body
        missing_confirmation = FeishuOutboxSender().send(notification, attempt)
        assert missing_confirmation.classification == "UNKNOWN"
        assert missing_confirmation.error_code == "FEISHU_CONFIRMATION_MISSING"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(500, "UNKNOWN"), (502, "UNKNOWN"), (429, "TEMP_FAILED")],
)
def test_feishu_http_status_classification_is_safe(monkeypatch, status_code, expected):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return status_code

        def read(self):
            return b'{"code":0}'

    monkeypatch.setattr(notification_outbox_module, "urlopen", lambda *args, **kwargs: Response())
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/hook/test")
    notification = _notification(f"FEISHU-{status_code}")
    notification.channel = "feishu"
    attempt = NotificationDeliveryAttempt(
        delivery_attempt_id=f"ATT-{status_code}",
        notification_id=notification.notification_id,
        attempt_no=1,
        status="STARTED",
        lease_owner_token="owner",
        lease_version=1,
        request_fingerprint="request",
        started_at=datetime(2026, 7, 17, 10, 0),
    )
    assert FeishuOutboxSender().send(notification, attempt).classification == expected


def test_feishu_http_error_5xx_is_unknown(monkeypatch):
    def raise_http_error(request, timeout):
        raise HTTPError(request.full_url, 500, "server error", None, None)

    monkeypatch.setattr(notification_outbox_module, "urlopen", raise_http_error)
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/hook/test")
    notification = _notification("FEISHU-HTTPERROR")
    notification.channel = "feishu"
    attempt = NotificationDeliveryAttempt(
        delivery_attempt_id="ATT-HTTPERROR",
        notification_id=notification.notification_id,
        attempt_no=1,
        status="STARTED",
        lease_owner_token="owner",
        lease_version=1,
        request_fingerprint="request",
        started_at=datetime(2026, 7, 17, 10, 0),
    )
    result = FeishuOutboxSender().send(notification, attempt)
    assert result.classification == "UNKNOWN"
    assert result.error_code == "FEISHU_UNKNOWN_DELIVERY"


def test_review_and_outbox_commit_or_rollback_together(repository):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    review = _review()
    candidate = service._candidate_review_notification(review)

    with pytest.raises(RuntimeError, match="injected"):
        repository.insert_review_task_with_notification_outbox(
            review,
            candidate,
            failure_injector=lambda stage: (_ for _ in ()).throw(RuntimeError("injected"))
            if stage == "after_outbox_insert"
            else None,
        )
    assert repository.get_review_task(review.review_task_id) is None
    assert repository.get_notification_outbox_by_key(candidate.notification_key) is None

    inserted = service.enqueue_review_task_atomically(review)
    assert inserted[:2] == (1, 1)
    assert repository.get_review_task(review.review_task_id) is not None
    assert repository.get_notification_outbox_by_key(inserted[2].notification_key) is not None


def test_duplicate_review_dedupe_returns_existing_outbox_and_repairs_projection(repository):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    first_review = _review("REVIEW-RANDOM-1")
    second_review = _review("REVIEW-RANDOM-2")
    second_review.dedupe_key = first_review.dedupe_key
    first = service.enqueue_review_task_atomically(first_review)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "DELETE FROM notification_logs WHERE notification_id = ?",
            (first[2].notification_id,),
        )
    duplicate = service.enqueue_review_task_atomically(second_review)
    assert duplicate[:2] == (0, 0)
    assert duplicate[2].notification_id == first[2].notification_id
    assert len(repository.list_notification_outbox()) == 1
    assert repository.get_notification_log(first[2].notification_id) is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "不同原因"),
        ("required_by", datetime(2026, 7, 17, 12, 0)),
        ("review_payload", {"decision": "different"}),
        ("platform_name", "different-platform"),
        ("internal_sku", "SKU-DIFFERENT"),
    ],
)
def test_duplicate_review_full_business_fingerprint_rejects_changes(repository, field, value):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    first_review = _review("REVIEW-FP-1")
    first_review.review_payload = {"decision": "same"}
    second_review = replace(first_review, review_task_id="REVIEW-FP-2")
    setattr(second_review, field, value)
    service.enqueue_review_task_atomically(first_review, channel="feishu", recipient_ref="operations")
    with pytest.raises(NotificationIdempotencyConflictError):
        service.enqueue_review_task_atomically(second_review, channel="feishu", recipient_ref="operations")


@pytest.mark.parametrize(
    "notification_kwargs",
    [
        {"channel": "mock", "recipient_ref": "operations"},
        {"channel": "feishu", "recipient_ref": "different-recipient"},
        {"channel": "feishu", "recipient_ref": "operations", "recipient_type": "user"},
    ],
)
def test_duplicate_review_notification_intent_fingerprint_rejects_changes(repository, notification_kwargs):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    first_review = _review("REVIEW-NOTIFY-FP-1")
    second_review = replace(first_review, review_task_id="REVIEW-NOTIFY-FP-2")
    service.enqueue_review_task_atomically(first_review, channel="feishu", recipient_ref="operations")
    with pytest.raises(NotificationIdempotencyConflictError):
        service.enqueue_review_task_atomically(second_review, **notification_kwargs)


@pytest.mark.parametrize(
    "notification_kwargs",
    [
        {"event_version": "v2"},
        {"priority": 99},
        {"max_attempts": 7},
        {"deadline_at": datetime(2026, 7, 17, 13, 0)},
    ],
)
def test_duplicate_review_rejects_event_version_or_delivery_policy_changes(repository, notification_kwargs):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    first_review = _review("REVIEW-POLICY-FP-1")
    second_review = replace(first_review, review_task_id="REVIEW-POLICY-FP-2")
    service.enqueue_review_task_atomically(first_review, channel="feishu", recipient_ref="operations")
    with pytest.raises(NotificationIdempotencyConflictError):
        service.enqueue_review_task_atomically(
            second_review,
            channel="feishu",
            recipient_ref="operations",
            **notification_kwargs,
        )


@pytest.mark.parametrize(
    "failure_stage",
    ["after_business_update", "after_outbox_insert", "after_compatibility_log_insert"],
)
def test_expired_review_notification_transaction_rolls_back_every_stage(repository, monkeypatch, failure_stage):
    monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", "mock")
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    source = _source_task("TASK-EXPIRE-ROLLBACK")
    repository.insert_tasks([source])
    review = replace(
        _review("REVIEW-EXPIRE-ROLLBACK"),
        source_task_id=source.task_id,
        required_by=source.required_by,
    )
    initial = service.enqueue_review_task_atomically(review, channel="mock")[2]
    updated = replace(
        review,
        review_status=ReviewTaskStatus.EXPIRED,
        resolution_payload={"timeout": True},
        resolved_by="system",
        resolved_at=datetime(2026, 7, 17, 11, 0),
        updated_at=datetime(2026, 7, 17, 11, 0),
    )
    expired, log = service.build_expired_notification_candidate(
        updated,
        timeout_at=datetime(2026, 7, 17, 11, 0),
    )
    history = TaskStatusHistory(
        history_id=f"HISTORY-{failure_stage}",
        task_id=source.task_id,
        from_status=TaskStatus.MANUAL_REVIEW,
        to_status=TaskStatus.EXPIRED,
        changed_by="system",
        changed_at=datetime(2026, 7, 17, 11, 0),
        reason="review expired",
        metadata={"failure_stage": failure_stage},
    )
    history_before = repository.list_task_status_history(source.task_id)
    with pytest.raises(RuntimeError, match="injected"):
        repository.expire_review_task_with_notification_outbox(
            updated,
            expired,
            log,
            task_id=source.task_id,
            task_status=TaskStatus.EXPIRED,
            history=history,
            result_message="expired",
            failure_injector=lambda stage: (_ for _ in ()).throw(RuntimeError("injected"))
            if stage == failure_stage
            else None,
        )
    assert repository.get_review_task(review.review_task_id).review_status == ReviewTaskStatus.PENDING
    assert repository.get_task(source.task_id).task_status == TaskStatus.MANUAL_REVIEW
    assert repository.list_task_status_history(source.task_id) == history_before
    assert repository.get_notification_outbox(initial.notification_id).status == NotificationOutboxStatus.PENDING.value
    assert repository.get_notification_outbox(expired.notification_id) is None
    assert repository.get_notification_log(expired.notification_id) is None


@pytest.mark.parametrize("configured_channel", [None, "mock", "fake"])
def test_business_creation_never_auto_runs_fake_sender(repository, monkeypatch, configured_channel):
    if configured_channel is None:
        monkeypatch.delenv("DEFAULT_NOTIFICATION_CHANNEL", raising=False)
    else:
        monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", configured_channel)
    monkeypatch.setattr(FakeSender, "send", lambda *args, **kwargs: pytest.fail("FakeSender must not run"))
    result = OutboxReviewNotificationService(repository).create_review_task_atomically(
        _review(f"REVIEW-NO-FAKE-{configured_channel}")
    )
    assert result[2].status == NotificationOutboxStatus.PENDING.value
    assert result[2].channel == (configured_channel or "unconfigured")


def test_compatibility_projection_insert_failure_rolls_back_review_and_outbox(repository):
    service = NotificationOutboxService(repository, clock=lambda: datetime(2026, 7, 17, 10, 0))
    review = _review("REVIEW-COMPAT-ROLLBACK")
    candidate = service._candidate_review_notification(review)
    compatibility_log = service._compatibility_log(candidate)
    with pytest.raises(RuntimeError, match="compatibility failure"):
        repository.insert_review_task_with_notification_outbox(
            review,
            candidate,
            compatibility_log=compatibility_log,
            failure_injector=lambda stage: (_ for _ in ()).throw(RuntimeError("compatibility failure"))
            if stage == "after_compatibility_log_insert"
            else None,
        )
    assert repository.get_review_task(review.review_task_id) is None
    assert repository.get_notification_outbox(candidate.notification_id) is None
    assert repository.get_notification_log(candidate.notification_id) is None


def test_only_one_worker_claims_a_notification(repository):
    repository.insert_notification_outbox(_notification())
    results: list[list[NotificationOutbox]] = []
    barrier = threading.Barrier(2)

    def claim() -> None:
        barrier.wait()
        results.append(
            repository.claim_notification_outbox(
                now=datetime(2026, 7, 17, 10, 0), lease_seconds=30
            )
        )

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(len(result) for result in results) == [0, 1]
    claimed = next(result[0] for result in results if result)
    assert claimed.status == NotificationOutboxStatus.LEASED.value
    assert claimed.lease_version == 1


def test_old_owner_is_fenced_before_delivery(repository):
    repository.insert_notification_outbox(_notification())
    claimed = repository.claim_notification_outbox(
        now=datetime(2026, 7, 17, 10, 0), lease_seconds=30
    )[0]
    assert not repository.renew_notification_outbox_lease(
        claimed.notification_id,
        owner_token="old-owner",
        lease_version=claimed.lease_version,
        now=datetime(2026, 7, 17, 10, 1),
    )
    with pytest.raises(NotificationLeaseError):
        repository.begin_notification_delivery(
            claimed.notification_id,
            owner_token="old-owner",
            lease_version=claimed.lease_version,
            request_fingerprint="fingerprint",
            now=datetime(2026, 7, 17, 10, 0),
        )


def test_success_is_sent_once_and_temporary_failure_is_bounded(repository):
    now = datetime(2026, 7, 17, 10, 0)
    current = [now]
    repository._clock = lambda: current[0]
    service = NotificationOutboxService(repository, clock=lambda: now)
    service.enqueue(
        notification_type="test",
        notification_key="test:retry:v1:scripted:operator",
        recipient_type="role",
        recipient_ref="operator",
        channel="scripted",
        payload={"message": "safe"},
        max_attempts=2,
    )
    sender = ScriptedSender(["temporary_reject", "success"])
    first = service.deliver_once(sender, now=now)
    assert first is not None
    assert first.status == NotificationOutboxStatus.RETRY_WAIT.value
    assert first.next_attempt_at == now + timedelta(seconds=1)
    current[0] = now + timedelta(seconds=1)
    second = service.deliver_once(sender)
    assert second is not None
    assert second.status == NotificationOutboxStatus.SENT.value
    assert second.attempt_count == 2
    assert len(repository.list_notification_delivery_attempts(second.notification_id)) == 2
    assert repository.list_notification_delivery_attempts(second.notification_id)[-1].status == DeliveryAttemptStatus.ACKNOWLEDGED.value
    current[0] = now + timedelta(minutes=1)
    assert service.deliver_once(sender) is None


def test_unknown_delivery_is_terminal_for_normal_worker(repository):
    now = datetime(2026, 7, 17, 10, 0)
    service = NotificationOutboxService(repository, clock=lambda: now)
    service.enqueue(
        notification_type="test",
        notification_key="test:unknown:v1:scripted:operator",
        recipient_type="role",
        recipient_ref="operator",
        channel="scripted",
        payload={"message": "safe"},
    )
    final = service.deliver_once(ScriptedSender(["timeout_after_bytes_sent"]), now=now)
    assert final is not None
    assert final.status == NotificationOutboxStatus.UNKNOWN_DELIVERY.value
    assert repository.list_notification_delivery_attempts(final.notification_id)[0].status == DeliveryAttemptStatus.UNKNOWN.value
    assert service.deliver_once(FakeSender(), now=now + timedelta(hours=1)) is None


def test_watchdog_fences_expired_sending_as_unknown(repository):
    now = datetime(2026, 7, 17, 10, 0)
    service = NotificationOutboxService(repository, clock=lambda: now)
    notification = service.enqueue(
        notification_type="test",
        notification_key="watchdog-compat",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
    )
    claimed = repository.claim_notification_outbox(now=now, lease_seconds=10, channel="fake")[0]
    repository.begin_notification_delivery(
        claimed.notification_id,
        owner_token=claimed.lease_owner_token,
        lease_version=claimed.lease_version,
        request_fingerprint="fingerprint",
        now=now,
    )
    recovered = repository.recover_expired_notification_leases(now=now + timedelta(seconds=11))
    assert recovered[0].status == NotificationOutboxStatus.UNKNOWN_DELIVERY.value
    assert repository.list_notification_delivery_attempts(claimed.notification_id)[0].status == DeliveryAttemptStatus.UNKNOWN.value
    compatibility = repository.get_notification_log(notification.notification_id)
    assert compatibility is not None
    assert compatibility.send_status == "failed"


def test_late_writeback_after_lease_or_deadline_is_fenced_for_watchdog(repository):
    now = datetime(2026, 7, 17, 10, 0)
    service = NotificationOutboxService(repository, clock=lambda: now)
    notification = service.enqueue(
        notification_type="test",
        notification_key="late-writeback",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
        deadline_at=now + timedelta(seconds=5),
    )
    claimed = repository.claim_notification_outbox(now=now, lease_seconds=30, channel="fake")[0]
    attempt = repository.begin_notification_delivery(
        claimed.notification_id,
        owner_token=claimed.lease_owner_token,
        lease_version=claimed.lease_version,
        request_fingerprint="fingerprint",
        now=now,
    )
    with pytest.raises(Exception, match="expired before writeback"):
        repository.complete_notification_delivery(
            notification.notification_id,
            attempt.delivery_attempt_id,
            owner_token=claimed.lease_owner_token,
            lease_version=claimed.lease_version,
            result=NotificationDeliveryResult(classification="SUCCESS"),
            now=now + timedelta(seconds=6),
        )
    assert repository.get_notification_outbox(notification.notification_id).status == NotificationOutboxStatus.SENDING.value
    recovered = repository.recover_expired_notification_leases(now=now + timedelta(seconds=6))
    assert recovered[0].status == NotificationOutboxStatus.UNKNOWN_DELIVERY.value


def test_service_re_reads_time_after_slow_sender_and_watchdog_after_lock_wait(repository):
    now = datetime(2026, 7, 17, 10, 0)
    current = [now]
    repository._clock = lambda: current[0]
    service = NotificationOutboxService(repository, lease_seconds=5)
    notification = service.enqueue(
        notification_type="test",
        notification_key="service-fresh-time",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
        deadline_at=now + timedelta(seconds=30),
    )

    class SlowSender(FakeSender):
        def send(self, notification, attempt):
            current[0] = now + timedelta(seconds=6)
            return super().send(notification, attempt)

    with pytest.raises(NotificationDeliveryError, match="expired before writeback"):
        service.deliver_once(SlowSender(), lease_seconds=5)
    assert repository.get_notification_outbox(notification.notification_id).status == "SENDING"
    assert service.watchdog()[0].status == "UNKNOWN_DELIVERY"

    leased = service.enqueue(
        notification_type="test",
        notification_key="watchdog-lock-time",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "safe"},
    )
    current[0] = now
    repository.claim_notification_outbox(lease_seconds=5, channel="fake")
    blocker = repository.connect_write()
    blocker.execute("BEGIN IMMEDIATE")
    started = threading.Event()
    recovered: list[NotificationOutbox] = []

    def run_watchdog():
        started.set()
        recovered.extend(service.watchdog())

    thread = threading.Thread(target=run_watchdog)
    thread.start()
    assert started.wait(timeout=2)
    current[0] = now + timedelta(seconds=6)
    blocker.commit()
    blocker.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert any(row.notification_id == leased.notification_id for row in recovered)


def test_verification_intervention_has_priority_and_short_deadline(repository):
    now = datetime(2026, 7, 17, 10, 0)
    service = NotificationOutboxService(repository, clock=lambda: now)
    service.enqueue(
        notification_type="ordinary",
        notification_key="ordinary:1:v1:fake:operator",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        priority=1,
    )
    verification = service.enqueue_verification_intervention(
        operation_id="OP-1",
        attempt_id="ATT-1",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        now=now,
    )
    claimed = repository.claim_notification_outbox(now=now, lease_seconds=30)[0]
    assert claimed.notification_id == verification.notification_id
    assert claimed.priority == 100
    assert claimed.deadline_at == now + timedelta(seconds=300)


@pytest.mark.parametrize("ttl_seconds", [-1, 60, 601, 3600])
def test_atomic_verification_review_rejects_deadline_outside_short_ttl(repository, ttl_seconds):
    now = datetime(2026, 7, 17, 10, 0)
    service = NotificationOutboxService(repository, clock=lambda: now)
    review = _review(f"VERIFY-{ttl_seconds}")
    review.required_by = now + timedelta(seconds=ttl_seconds)
    with pytest.raises(ValueError, match="between 120 and 600 seconds"):
        service.enqueue_verification_review_task_atomically(
            review,
            operation_id="OP-1",
            attempt_id=f"ATT-{ttl_seconds}",
            recipient_type="role",
            recipient_ref="operator",
            channel="fake",
        )


def test_atomic_verification_review_accepts_five_minute_ttl(repository):
    now = datetime(2026, 7, 17, 10, 0)
    service = NotificationOutboxService(repository, clock=lambda: now)
    review = _review("VERIFY-300")
    review.required_by = now + timedelta(seconds=300)
    inserted = service.enqueue_verification_review_task_atomically(
        review,
        operation_id="OP-1",
        attempt_id="ATT-300",
        recipient_type="role",
        recipient_ref="operator",
        channel="FeIsHu",
    )
    assert inserted[:2] == (1, 1)
    assert inserted[2].channel == "feishu"


def test_review_channel_is_normalized_before_key_and_worker_claim(repository, monkeypatch):
    monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", "FeIsHu")
    adapter = OutboxReviewNotificationService(repository)
    review = _review("REVIEW-CASE-CHANNEL")
    inserted = adapter.create_review_task_atomically(review)
    outbox = inserted[2]
    assert outbox.channel == "feishu"
    expected_key = adapter.outbox_service.notification_key(
        "mobile_review_required",
        review.review_task_id,
        "v1",
        "feishu",
        "operations",
    )
    assert outbox.notification_key == expected_key
    registry = NotificationChannelRegistry({"feishu": lambda: FakeSender(channel="feishu")})
    repository._clock = lambda: datetime(2026, 7, 17, 10, 0)
    worker = NotificationOutboxWorker.for_channel(repository, "FEISHU", registry=registry)
    assert worker.run_once().status == "SENT"


def test_feishu_worker_sends_ephemeral_mobile_review_link_without_persisting_token(
    repository,
    monkeypatch,
):
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

        def read(self):
            return b'{"code":0,"msg":"success","request_id":"req-mobile"}'

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr(notification_outbox_module, "urlopen", fake_urlopen)
    monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", "feishu")
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/hook/test")
    monkeypatch.setenv("FEISHU_MESSAGE_TYPE", "post")
    monkeypatch.setenv("REVIEW_TOKEN_SECRET", "test-review-token-secret")
    monkeypatch.setenv("MOBILE_REVIEW_BASE_URL", "https://pra.example")
    adapter = OutboxReviewNotificationService(repository)
    review = _review("REVIEW-MOBILE-LINK")
    inserted = adapter.create_review_task_atomically(review)

    delivered = NotificationOutboxWorker.for_channel(
        repository,
        "feishu",
    ).run_once()

    assert delivered is not None and delivered.status == "SENT"
    serialized_body = json.dumps(captured["body"], ensure_ascii=False)
    assert (
        "https://pra.example/mobile/review/REVIEW-MOBILE-LINK?token="
        in serialized_body
    )
    persisted = repository.get_notification_outbox(inserted[2].notification_id)
    assert persisted is not None
    assert "token" not in json.dumps(persisted.payload).lower()
    log = repository.get_notification_log(persisted.notification_id)
    assert log is not None and "token=" not in log.message
    tokens = repository.list_review_tokens_by_review_task_id(
        review.review_task_id
    )
    assert len(tokens) == 1
    assert tokens[0].revoked_at is None


def test_review_notification_formats_aware_deadline_as_beijing_time(
    repository,
    monkeypatch,
):
    monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", "feishu")
    review = _review("REVIEW-BEIJING-DEADLINE")
    review.required_by = datetime(2026, 7, 25, 23, 29, tzinfo=UTC)

    outbox = OutboxReviewNotificationService(
        repository
    ).create_review_task_atomically(review)[2]

    assert "复核截止：2026-07-26 07:29（北京时间）" in outbox.payload[
        "message"
    ]
    assert "+00:00" not in outbox.payload["message"]


def test_execution_failure_review_notification_names_retry_and_cancel_results(
    repository,
    monkeypatch,
):
    monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", "feishu")
    review = _review("REVIEW-EXECUTION-RESULTS")
    review.review_payload = {
        "review_subject": "task_group",
        "task_group_id": "RULE-GROUP-NOTIFY-001",
        "affected_task_count": 2,
        "action_type": TaskActionType.UPDATE_PRICE.value,
        "task_status": TaskStatus.MANUAL_REVIEW.value,
        "items": [
            {"task_id": "TASK-A", "internal_sku": "SKU-A"},
            {"task_id": "TASK-B", "internal_sku": "SKU-B"},
        ],
    }
    review.scope_type = "task_group"
    review.scope_key = "RULE-GROUP-NOTIFY-001"

    outbox = OutboxReviewNotificationService(
        repository
    ).create_review_task_atomically(review)[2]

    assert "可选结果：重试任务 / 取消任务" in outbox.payload["message"]
    assert "任务组：RULE-GROUP-NOTIFY-001（2 条待复核任务）" in outbox.payload[
        "message"
    ]
    assert "商品：SKU-A、SKU-B" in outbox.payload["message"]


@pytest.mark.parametrize("lease_before_resolve", [False, True])
def test_resolving_review_cancels_pre_send_outbox_and_projection(repository, lease_before_resolve):
    now = datetime(2026, 7, 17, 10, 0)
    repository._clock = lambda: now
    outbox_service = NotificationOutboxService(repository, clock=lambda: now)
    review = _review(f"RESOLVE-{lease_before_resolve}")
    created = outbox_service.enqueue_review_task_atomically(review)[2]
    if lease_before_resolve:
        repository.claim_notification_outbox(channel="fake")
    ReviewTaskService(repository).resolve_review_task(
        review_task_id=review.review_task_id,
        status=ReviewTaskStatus.CANCELLED,
        actor="test",
    )
    assert repository.get_notification_outbox(created.notification_id).status == "CANCELLED"
    assert repository.get_notification_log(created.notification_id).send_status == "failed"


def test_resolving_review_does_not_cancel_sending_outbox(repository):
    now = datetime(2026, 7, 17, 10, 0)
    current = [now]
    repository._clock = lambda: current[0]
    service = NotificationOutboxService(repository, clock=lambda: now)
    review = _review("RESOLVE-SENDING")
    created = service.enqueue_review_task_atomically(review)[2]
    claimed = repository.claim_notification_outbox(channel="fake", lease_seconds=5)[0]
    repository.begin_notification_delivery(
        claimed.notification_id,
        owner_token=claimed.lease_owner_token,
        lease_version=claimed.lease_version,
        request_fingerprint="fingerprint",
    )
    ReviewTaskService(repository).resolve_review_task(
        review_task_id=review.review_task_id,
        status=ReviewTaskStatus.CANCELLED,
        actor="test",
    )
    assert repository.get_notification_outbox(created.notification_id).status == "SENDING"
    current[0] = now + timedelta(seconds=6)
    assert service.watchdog()[0].status == "UNKNOWN_DELIVERY"
