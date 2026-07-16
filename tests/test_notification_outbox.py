from __future__ import annotations

import tempfile
import threading
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.enums import DeliveryAttemptStatus, NotificationOutboxStatus, ReviewTaskStatus
from app.exceptions import NotificationChannelMismatchError, NotificationIdempotencyConflictError, NotificationLeaseError
from app.models import NotificationDeliveryAttempt, NotificationDeliveryResult, NotificationOutbox, ReviewTask
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.notification_outbox import (
    FakeSender,
    FeishuOutboxSender,
    NotificationChannelRegistry,
    NotificationOutboxService,
    ScriptedSender,
)
from app.services import notification_outbox as notification_outbox_module


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


@pytest.fixture()
def repository():
    with tempfile.TemporaryDirectory() as directory:
        repo = SQLiteRuntimeRepository(Path(directory) / "runtime.sqlite3")
        repo.init_schema()
        yield repo


def test_schema_v6_has_outbox_and_attempt_health(repository):
    health = repository.check_schema_health()
    assert health.ok, health.summary
    assert repository.schema_versions() == [1, 2, 3, 4, 5, 6]
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
    second = service.deliver_once(sender, now=now + timedelta(seconds=1))
    assert second is not None
    assert second.status == NotificationOutboxStatus.SENT.value
    assert second.attempt_count == 2
    assert len(repository.list_notification_delivery_attempts(second.notification_id)) == 2
    assert repository.list_notification_delivery_attempts(second.notification_id)[-1].status == DeliveryAttemptStatus.ACKNOWLEDGED.value
    assert service.deliver_once(sender, now=now + timedelta(minutes=1)) is None


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
