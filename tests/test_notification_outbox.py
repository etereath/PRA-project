from __future__ import annotations

import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.enums import DeliveryAttemptStatus, NotificationOutboxStatus, ReviewTaskStatus
from app.exceptions import NotificationLeaseError
from app.models import NotificationOutbox, ReviewTask
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.notification_outbox import (
    FakeSender,
    NotificationOutboxService,
    ScriptedSender,
)


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
    second = service.enqueue(
        notification_type="test",
        notification_key="test:entity:v1:fake:operator",
        recipient_type="role",
        recipient_ref="operator",
        channel="fake",
        payload={"message": "different payload"},
    )
    assert second.notification_id == first.notification_id
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
    repository.insert_notification_outbox(_notification())
    claimed = repository.claim_notification_outbox(now=now, lease_seconds=10)[0]
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
