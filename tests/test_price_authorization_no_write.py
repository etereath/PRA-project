"""Real Web/Coordinator/publisher boundaries for an externally satisfied decision."""

import json
import re
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from app.enums import TaskActionType, TaskStatus
from app.models import ShadowBotOperationLedger
from app.services.execution_authorization import ExecutionAuthorizationConflict
from app.services.execution_authorization import ExecutionAuthorizationBlocked
from tests.test_human_price_journey import (
    accept,
    decide,
    rebuild,
    run_cycle,
    seed,
)
from tests.test_operations_web_foundation import call_app, header_values
from tests.test_price_execution_resolution import web


pytest_plugins = ("tests.test_human_price_journey",)

def observe(j, price='13'):
    seed._listing(j.runtime, 'AISHA-A-50-Z', 'A级', Decimal(price), 'online')


def assert_no_write(j):
    assert not list(j.service.queue_root.glob('inbox/*.ready.json'))
    with j.runtime.connect_read() as connection:
        for table in ('shadowbot_execution_attempts', 'shadowbot_operations', 'shadowbot_write_locks'):
            assert connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM shadowbot_commit_batches').fetchone()[0] == 0


def qualified_target(j, price='13'):
    listing = j.runtime.get_listing_status(seed.PLATFORM, '艾莎', 'A级')
    values = {
        'operating_fact_qualified': True,
        'observed_online': True,
        'observed_price': f'{Decimal(price):.2f}',
        'source_execution_attempt_id': listing.price_source_attempt_id,
        'observed_at': listing.price_observed_at.isoformat(),
        'platform_product_identity_digest': '',
        'qualification_sha256': 'sha256:' + 'a' * 64,
    }
    return SimpleNamespace(**values, as_dict=lambda: dict(values))


def prepare(j, task, key='no-write'):
    j.service.listing_quality.latest = lambda **kwargs: qualified_target(j)
    p = j.service.prepare_execution(seed._admin(), [task], key)
    assert p.already_applied
    return p


def submit(j, task, p, key='no-write'):
    return j.service.submit_execution(seed._admin(), [task], p.confirmation_digest, key)


def test_web_confirm_external_target_closes_after_restart_and_refreshes_receipt(journey, monkeypatch):
    j = journey
    task = decide(j)
    observe(j)
    app, container, cookie = web(j)
    app.execution_authorization.listing_quality.latest = (
        lambda **kwargs: qualified_target(j)
    )
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
    assert '目标已由最新合格平台事实满足' in body
    assert not app.execution_authorization.continuations.active()
    assert j.runtime.get_task(task).task_status is TaskStatus.SKIPPED
    assert_no_write(j)
    with j.runtime.connect_read() as connection:
        history = connection.execute('SELECT reason, metadata_json FROM task_status_history WHERE task_id = ?', (task,)).fetchall()
        evidence = [json.loads(h['metadata_json']) for h in history if h['reason'] == 'ALREADY_APPLIED']
        assert len(evidence) == 1
        assert evidence[0]['platform_observation']['qualification']['qualification_sha256']
        assert not connection.execute('SELECT 1 FROM execution_continuations').fetchone()
    # The same receipt URL must read the latest durable state, not its cached ACCEPTED value.
    status, _, body = call_app(app, path='/management', cookie=cookie, query=receipt_query)
    assert status == '200 OK' and '目标已满足，决定已结束' in body
    assert '执行授权已接受' not in body and '结束时间' in body
    # A restarted Web can replay the old POST without its original preview cache.
    app, container, cookie = web(j)
    csrf = container.sessions.get(cookie).csrf_token
    _, body = post('/management/executions/submit', form)
    assert '目标已满足，决定已结束' in body and '执行授权已接受' not in body
    assert_no_write(j)
    query, _ = post('/management/executions/submit', form)

    def unavailable(*args):
        raise RuntimeError('synthetic receipt read failure')

    monkeypatch.setattr(app.execution_authorization, 'refresh_submission_result', unavailable)
    status, _, body = call_app(app, path='/management', cookie=cookie, query=query)
    assert status == '200 OK' and '执行状态暂不可用' in body
    assert '执行授权已接受' not in body and '由执行服务继续推进' not in body


@pytest.mark.parametrize('new_price', ['12', '14'])
def test_close_confirmation_cannot_authorize_a_later_price_change(journey, new_price):
    j = journey
    task = decide(j)
    observe(j)
    p = prepare(j, task)
    observe(j, new_price)
    with pytest.raises(ExecutionAuthorizationConflict):
        submit(j, task, p)
    assert not j.service.continuations.active()
    assert j.runtime.get_task(task).task_status is TaskStatus.PENDING
    assert_no_write(j)


@pytest.mark.parametrize('missing_source', [False, True])
def test_unqualified_target_price_uses_live_execution_instead_of_blocking(journey, missing_source):
    j = journey
    task = decide(j)
    observe(j)
    with j.runtime.connect_write() as connection:
        if missing_source:
            connection.execute("UPDATE listing_status SET price_source_attempt_id = ''")
        else:
            connection.execute('UPDATE listing_status SET price_observed_at = ?',
                               ((seed.NOW - timedelta(days=1)).isoformat(),))
    prepared = j.service.prepare_execution(seed._admin(), [task], 'unqualified-target')
    assert prepared.already_applied is False
    submitted = j.service.submit_execution(
        seed._admin(), [task], prepared.confirmation_digest, 'unqualified-target')
    assert submitted.batch_id
    continuation = j.service.continuations.active()[0]
    assert 'resolution_only' not in json.loads(continuation['envelope_json'])
    assert not list(j.service.queue_root.glob('inbox/*.ready.json'))


def test_mixed_satisfaction_requires_explicit_separate_selection(journey):
    j = journey
    task = decide(j)
    mapping_version = j.runtime.get_task(task).decision_trace['mapping_version']
    j.runtime.insert_tasks([seed._task('TASK-PRICE-B', 'AISHA-B-50-Z', 'B级', TaskActionType.UPDATE_PRICE,
        mapping_version, expected_old_price=Decimal('9'), target_price=Decimal('10'))])
    observe(j)
    j.service.listing_quality.latest = lambda **kwargs: (
        qualified_target(j) if kwargs['internal_sku'] == 'AISHA-A-50-Z'
        else SimpleNamespace(operating_fact_qualified=False)
    )
    with pytest.raises(ExecutionAuthorizationConflict):
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
    prepared = prepare(j, task)
    original_close = j.service._close_already_applied

    def raced_close(**kwargs):
        if change_status:
            with j.runtime.connect_write() as connection:
                connection.execute("UPDATE listing_status SET online_status = 'offline'")
        else:
            observe(j, '12')
        return original_close(**kwargs)

    monkeypatch.setattr(j.service, '_close_already_applied', raced_close)
    with pytest.raises(ExecutionAuthorizationConflict):
        submit(j, task, prepared)
    assert j.runtime.get_task(task).task_status is TaskStatus.PENDING
    assert not j.service.continuations.active()
    assert_no_write(j)


def test_local_close_rereads_active_write_responsibility(journey):
    j = journey
    task = decide(j)
    observe(j)
    prepared = prepare(j, task)
    j.runtime.insert_shadowbot_operation(
        ShadowBotOperationLedger(
            operation_id='OP-RACED-ACTIVE-WRITE',
            task_id=task,
            platform=seed.PLATFORM,
            product_identity={'internal_sku': 'AISHA-A-50-Z'},
            expected_old_price=Decimal('12'),
            target_price=Decimal('13'),
            status='RUNNING',
        )
    )

    with pytest.raises(ExecutionAuthorizationBlocked):
        submit(j, task, prepared)

    assert j.runtime.get_task(task).task_status is TaskStatus.PENDING
    assert not j.service.continuations.active()
    with j.runtime.connect_read() as connection:
        assert not connection.execute(
            'SELECT 1 FROM shadowbot_commit_batches'
        ).fetchone()


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
