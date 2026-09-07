"""Real Web/Coordinator/publisher boundaries for an externally satisfied decision."""

import json
import re
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.services.execution_authorization import ExecutionAuthorizationConflict
from tests.test_human_price_journey import journey, decide, accept, rebuild, run_cycle, seed
from tests.test_operations_web_foundation import call_app, header_values
from tests.test_price_execution_resolution import web


def observe(j, price='13'):
    seed._listing(j.runtime, 'AISHA-A-50-Z', 'A级', Decimal(price), 'online')


def assert_no_write(j):
    assert not list(j.service.queue_root.glob('inbox/*.ready.json'))
    with j.runtime.connect_read() as connection:
        for table in ('shadowbot_execution_attempts', 'shadowbot_operations', 'shadowbot_write_locks'):
            assert connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0


def prepare(j, task, key='no-write'):
    p = j.service.prepare_execution(seed._admin(), [task], key)
    assert p.resolution_only
    return p


def submit(j, task, p, key='no-write'):
    return j.service.submit_execution(seed._admin(), [task], p.confirmation_digest, key)


def test_web_confirm_external_target_closes_after_restart_and_refreshes_receipt(journey, monkeypatch):
    j = journey
    task = decide(j)
    observe(j)
    app, container, cookie = web(j)
    csrf = container.sessions.get(cookie).csrf_token

    def post(path, form):
        status, headers, body = call_app(app, path=path, method='POST', cookie=cookie,
                                        form={'csrf_token': csrf, **form})
        assert status == '303 See Other', body
        query = urlsplit(header_values(headers, 'Location')[0]).query
        status, _, body = call_app(app, path='/management', cookie=cookie, query=query)
        assert status == '200 OK', body
        return query, body

    _, body = post('/management/executions/prepare', {'task_ids': task, 'idempotency_key': 'http-close'})
    assert '无需改价' in body and '确认结束本次决定' in body
    assert not j.service.continuations.active()
    assert_no_write(j)
    digest = re.search(r'name="confirmation_digest" value="([^"]+)"', body).group(1)
    form = {'task_ids': task, 'idempotency_key': 'http-close', 'confirmation_digest': digest}
    receipt_query, body = post('/management/executions/submit', form)
    assert '结束决定的确认已保存' in body
    row = j.service.continuations.active()[0]
    envelope = json.loads(row['envelope_json'])
    assert envelope['resolution_only'] is True
    assert j.runtime.get_task(task).task_status is TaskStatus.PENDING
    # Even calling the formal publisher directly cannot turn this into write authority.
    with pytest.raises(ValidationError, match='Resolution-only'):
        j.service.v4_publish(j.runtime, j.service.runner_factory(j.service.queue_root),
            manifest=envelope['manifest'], execution_profile=j.service.execution_profile,
            applet_uri=j.service.applet_uri,
            confirmation_text=envelope['manifest']['development_confirmation_text'],
            confirmed_by='admin', authorization_batch_id=row['batch_id'])
    assert_no_write(j)
    coordinator, importer, watchdog = rebuild(j)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'ALREADY_APPLIED'
    assert j.runtime.get_task(task).task_status is TaskStatus.SKIPPED
    assert not coordinator.store.active()
    assert_no_write(j)
    with j.runtime.connect_read() as connection:
        history = connection.execute('SELECT reason, metadata_json FROM task_status_history WHERE task_id = ?', (task,)).fetchall()
        authorized = [json.loads(h['metadata_json']) for h in history if h['reason'] == 'execution_submission_authorized']
        evidence = [json.loads(h['metadata_json']) for h in history if h['reason'] == 'ALREADY_APPLIED']
        assert len(authorized) == 1 and authorized[0]['resolution_only'] is True
        assert len(evidence) == 1
        assert evidence[0]['platform_observation'][0]['listing_price_source_attempt_id']
        assert evidence[0]['platform_observation'][0]['listing_price_observed_at']
    # The same receipt URL must read the latest durable state, not its cached ACCEPTED value.
    status, _, body = call_app(app, path='/management', cookie=cookie, query=receipt_query)
    assert status == '200 OK' and '目标已满足，决定已结束' in body
    assert '执行授权已接受' not in body and '结束时间' in body
    # A restarted Web can replay the old POST without its original preview cache.
    app, container, cookie = web(j)
    csrf = container.sessions.get(cookie).csrf_token
    _, body = post('/management/executions/submit', form)
    assert '目标已满足，决定已结束' in body and '执行授权已接受' not in body
    assert not run_cycle(importer, watchdog, coordinator=coordinator)
    assert_no_write(j)
    query, _ = post('/management/executions/submit', form)

    def unavailable(*args):
        raise RuntimeError('synthetic receipt read failure')

    monkeypatch.setattr(app.execution_authorization, 'refresh_submission_result', unavailable)
    status, _, body = call_app(app, path='/management', cookie=cookie, query=query)
    assert status == '200 OK' and '执行状态暂不可用' in body
    assert '执行授权已接受' not in body and '由执行服务继续推进' not in body


@pytest.mark.parametrize('new_price', ['12', '14'])
@pytest.mark.parametrize('accepted', [False, True])
def test_close_confirmation_cannot_authorize_a_later_price_change(journey, new_price, accepted):
    j = journey
    task = decide(j)
    observe(j)
    p = prepare(j, task)
    if accepted:
        submit(j, task, p)
    observe(j, new_price)
    if accepted:
        coordinator, importer, watchdog = rebuild(j)
        assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'RECONFIRM'
        assert not coordinator.store.active()
    else:
        with pytest.raises(ExecutionAuthorizationConflict):
            submit(j, task, p)
        assert not j.service.continuations.active()
    assert j.runtime.get_task(task).task_status is TaskStatus.PENDING
    assert_no_write(j)


@pytest.mark.parametrize('missing_source', [False, True])
def test_already_target_still_requires_fresh_sourced_observation(journey, missing_source):
    j = journey
    task = decide(j)
    observe(j)
    with j.runtime.connect_write() as connection:
        if missing_source:
            connection.execute("UPDATE listing_status SET price_source_attempt_id = ''")
        else:
            connection.execute('UPDATE listing_status SET price_observed_at = ?',
                               ((seed.NOW - timedelta(days=1)).isoformat(),))
    with pytest.raises(ExecutionAuthorizationConflict):
        prepare(j, task)
    assert_no_write(j)


def test_mixed_satisfaction_requires_explicit_separate_selection(journey):
    j = journey
    task = decide(j)
    mapping_version = j.runtime.get_task(task).decision_trace['mapping_version']
    j.runtime.insert_tasks([seed._task('TASK-PRICE-B', 'AISHA-B-50-Z', 'B级', TaskActionType.UPDATE_PRICE,
        mapping_version, expected_old_price=Decimal('9'), target_price=Decimal('10'))])
    observe(j)
    with pytest.raises(ExecutionAuthorizationConflict, match='仅部分达到目标价'):
        j.service.prepare_execution(seed._admin(), [task, 'TASK-PRICE-B'], 'mixed')
    assert j.runtime.get_task(task).task_status is TaskStatus.PENDING
    assert j.runtime.get_task('TASK-PRICE-B').task_status is TaskStatus.PENDING
    assert not j.service.continuations.active()
    assert_no_write(j)


@pytest.mark.parametrize('change_status', [False, True])
def test_observation_race_cannot_commit_false_no_write_closure(journey, monkeypatch, change_status):
    j = journey
    task = decide(j)
    observe(j)
    submit(j, task, prepare(j, task))
    coordinator, importer, watchdog = rebuild(j)
    original_note = coordinator.store.note

    def raced_note(batch, outcome, message, **kwargs):
        if outcome == 'ALREADY_APPLIED':
            if change_status:
                with j.runtime.connect_write() as connection:
                    connection.execute("UPDATE listing_status SET online_status = 'offline'")
            else:
                observe(j, '12')
        return original_note(batch, outcome, message, **kwargs)

    monkeypatch.setattr(coordinator.store, 'note', raced_note)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'RETRY_PENDING'
    assert j.runtime.get_task(task).task_status is TaskStatus.PENDING
    assert coordinator.store.active()
    monkeypatch.setattr(coordinator.store, 'note', original_note)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'RECONFIRM'
    assert not coordinator.store.active()
    assert_no_write(j)


def test_matching_target_cannot_bypass_published_predecessor(journey):
    j = journey
    old = decide(j)
    accept(j, old)
    coordinator, importer, watchdog = rebuild(j)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'
    new = decide(j, '14', 'new-decision')
    observe(j, '14')
    with pytest.raises(ExecutionAuthorizationConflict, match='尚未收口'):
        j.service.prepare_execution(seed._admin(), [new], 'new-confirmation')
    assert j.runtime.get_task(new).task_status is TaskStatus.PENDING
    assert len(coordinator.store.active()) == 1
    assert len(list(j.service.queue_root.glob('inbox/*.ready.json'))) == 1
