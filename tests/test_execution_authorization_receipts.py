"""Receipt replay must report durable state without acquiring fresh authority."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.operations_web.presenters import _render_execution_receipt
from app.services.execution_authorization import ExecutionAuthorizationForbidden
from app.services.task_execution_coordinator import MESSAGES
from tests.test_execution_authorization import execution_setup, _admin, NOW


@pytest.mark.parametrize('outcome,closed', [
    ('ACCEPTED', False), ('BLOCKED', False), ('TRACKING', False), ('RECONCILING', False),
    ('HUMAN', False), ('RETRY_PENDING', False), ('COMPLETE', True), ('EXPIRED', True),
    ('RECONFIRM', True), ('SUPERSEDED', True), ('ALREADY_APPLIED', True), ('HUMAN_RESOLVED', True),
])
def test_replay_projects_current_state_without_new_authority(execution_setup, outcome, closed):
    service, runtime, calls = execution_setup
    ids, key = ['TASK-PRICE-A'], 'original-authority'
    preview = service.prepare_execution(_admin(), ids, key)
    receipt = service.submit_execution(_admin(), ids, preview.confirmation_digest, key)
    # Projection test: the producer transitions are covered by the journey suites.
    closed_at = (NOW + timedelta(minutes=1)).isoformat() if closed else None
    with runtime.connect_write() as connection:
        connection.execute('UPDATE execution_continuations SET outcome = ?, message = ?, closed_at = ?',
                           (outcome, MESSAGES[outcome], closed_at))
    service._preparations.clear()
    service._idempotency.clear()
    replay = service.submit_execution(_admin(), ids, preview.confirmation_digest, key,
                                      now=NOW + timedelta(days=1))
    assert replay == service.refresh_submission_result(_admin(), receipt)
    assert (replay.batch_id, replay.task_ids) == (receipt.batch_id, receipt.task_ids)
    assert (replay.outcome, replay.closed_at, replay.message) == (outcome, closed_at, MESSAGES[outcome])
    html = _render_execution_receipt(replay)
    assert MESSAGES[outcome] in html and '/management/task/TASK-PRICE-A' in html
    if closed:
        assert closed_at in html
        assert '执行授权已接受' not in html and '由执行服务继续推进' not in html
    with runtime.connect_read() as connection:
        assert connection.execute('SELECT COUNT(*) FROM execution_continuations').fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM task_status_history WHERE reason = 'execution_submission_authorized'").fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM shadowbot_execution_attempts').fetchone()[0] == 0
        assert connection.execute('SELECT closed_at FROM execution_continuations').fetchone()[0] == closed_at
    assert not calls
    for bad_ids, bad_digest in [(['TASK-OFFLINE-B'], preview.confirmation_digest), (ids, 'wrong-digest')]:
        with pytest.raises(ExecutionAuthorizationForbidden):
            service.submit_execution(_admin(), bad_ids, bad_digest, key)
    with pytest.raises(ExecutionAuthorizationForbidden):
        service.refresh_submission_result(replace(_admin(), subject='another-admin'), receipt)


def test_closed_receipt_without_legacy_message_never_claims_progress(execution_setup):
    service, runtime, _ = execution_setup
    preview = service.prepare_execution(_admin(), ['TASK-PRICE-A'], 'legacy')
    receipt = service.submit_execution(_admin(), ['TASK-PRICE-A'], preview.confirmation_digest, 'legacy')
    with runtime.connect_write() as connection:
        connection.execute('UPDATE execution_continuations SET closed_at = ?', (NOW.isoformat(),))
    refreshed = service.refresh_submission_result(_admin(), receipt)
    assert refreshed.outcome == 'CLOSED'
    assert '本次授权已结束' in _render_execution_receipt(refreshed)
    assert '继续推进' not in _render_execution_receipt(refreshed)
