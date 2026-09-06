"""P1-47-01: real unknown/reconcile, durable scan import, human closure and next write.

The v4 flow uses the existing synthetic UI adapter; the read-only v5 scan uses
a platform-result fixture. Publishers, Queue Worker and Importers remain real.
"""
from __future__ import annotations

import json
import re
import sys
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.test_human_price_journey import journey, decide, accept, rebuild, platform_worker, seed, run_cycle
from tests.test_shadowbot_listing_sync import _result, _item
from app.enums import TaskStatus, ReviewTaskStatus
from app.exceptions import ValidationError
from app.operations_web.auth import Principal, Capability
from app.services.price_execution_resolution import PriceExecutionResolutionApplicationService
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_listing_sync import prepare_listing_sync_batch, publish_listing_sync_batch
from app.services.runtime import ReviewTaskService
from app.repositories.automation_repository import AutomationRepository


def reviewer(subject='admin'):
    return Principal(subject, frozenset({Capability.HANDLE_REVIEW}))


@pytest.fixture
def unknown(journey, monkeypatch):
    old = decide(journey)
    prepared = accept(journey, old)
    coordinator, importer, watchdog = rebuild(journey)
    run_cycle(importer, watchdog, coordinator=coordinator)
    state, result = platform_worker(journey, monkeypatch, fail_after_click=True)
    assert result['batch_status'] == 'UNKNOWN' and state['writes'] == 1
    run_cycle(importer, watchdog, coordinator=coordinator)
    state, result = platform_worker(journey, monkeypatch, price='14.00')
    assert state['writes'] == 0 and result['status'] == 'SIDE_EFFECT_UNKNOWN', result
    events = run_cycle(importer, watchdog, coordinator=coordinator)
    assert events[-1]['status'] == 'HUMAN', events
    service = PriceExecutionResolutionApplicationService(journey.service)
    model = service.for_task(old)
    assert model is not None and model['review_status'] == 'pending'
    assert model['payload']['owner_subject'] == 'admin'
    assert model['payload']['execution_stopped_at']
    assert AutomationRepository(journey.runtime).active_ui_blocker() == ''
    with journey.runtime.connect_read() as connection:
        item = dict(connection.execute('SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?',
                                      (prepared.batch_id,)).fetchone())
    return types.SimpleNamespace(j=journey, old=old, batch=prepared.batch_id, item=item, service=service,
        review_id=model['review_task_id'], importer=importer, watchdog=watchdog, coordinator=coordinator)


def scan(unknown, monkeypatch, *, price='14.00', suffix='001'):
    j = unknown.j
    manifest = prepare_listing_sync_batch(j.runtime, batch_id='BATCH-PRICE-SCAN-' + suffix,
        platform_name=seed.PLATFORM, mapping_path=j.service.shadowbot_identity_mapping,
        execution_profile=j.service.execution_profile)
    request, _ = publish_listing_sync_batch(j.runtime, ShadowBotFileQueueRunner(j.service.queue_root),
        manifest=manifest, execution_profile=j.service.execution_profile, applet_uri=j.service.applet_uri)
    # A platform observation fixture crosses the same real file Worker/import boundary.
    now = datetime.now(UTC)
    result = _result(request, scan_started_at=now.isoformat())
    snapshot = result['snapshot']
    for key in list(snapshot):
        if key.endswith('_at'):
            snapshot[key] = now.isoformat()
    result['started_at'] = result['ended_at'] = now.isoformat()
    item = _item(snapshot_id=snapshot['snapshot_id'], suffix='0001',
        sku=unknown.item['internal_sku'], name=unknown.item['expected_product_name'],
        grade=unknown.item['expected_grade'], location='online_only')
    item.update(page_identity_key=unknown.item['page_identity_key'], online_observed_price=price,
                online_observed_at=now.isoformat())
    snapshot['items'] = [item]
    flow = types.ModuleType('vertical_slice_read_price')
    flow.main = lambda args: result
    monkeypatch.setitem(sys.modules, 'vertical_slice_read_price', flow)
    import shadowbot_queue_worker
    worker = shadowbot_queue_worker.QueueWorker({'queue_dir': str(j.service.queue_root),
        'poll_seconds': .01, 'max_hours': .1, 'max_tasks': 1, 'heartbeat_seconds': .01,
        'login_auto_enabled': False})
    worker._execute_claimed(*worker._claim_next())
    events = run_cycle(unknown.importer, unknown.watchdog, coordinator=unknown.coordinator)
    assert not any(e.get('status') == 'QUARANTINED' for e in events), events
    j.service.clock = lambda: datetime.now(UTC)
    model = unknown.service.for_task(unknown.old)
    assert model['evidence'], (model, events)
    return model['evidence'][0]


def request_for(unknown, evidence, *, conclusion='STOP_OLD_DECISION'):
    return dict(review_id=unknown.review_id, evidence_id=evidence['snapshot_item_id'],
        evidence_digest=evidence['digest'], conclusion=conclusion, idempotency_key='human-close-001',
        note='已核对平台当前价格，终止旧决定。')


def web(j):
    from app.operations_web.app import create_application
    from app.operations_web.composition import OperationsWebPaths, OperationsWebSettings, build_container
    from tests.test_operations_web_foundation import login
    s = j.service
    container = build_container(OperationsWebSettings(environment='development', public_scheme='http', cookie_secure=False,
        admin_username='admin', admin_password='synthetic-local-password', shadowbot_applet_uri=s.applet_uri,
        paths=OperationsWebPaths(runtime_db=j.runtime.db_path, products_workbook=s.products_workbook,
            price_rules_workbook=j.root / 'price.xlsx', listing_rules_workbook=j.root / 'listing.xlsx',
            queue_root=s.queue_root, platform_mappings_workbook=s.platform_mappings_workbook,
            shadowbot_identity_mapping=s.shadowbot_identity_mapping, backup_root=j.root / 'backups')))
    app = create_application(container)
    _, cookie = login(app, container)
    return app, container, cookie


def test_web_human_closure_restart_and_new_authorized_price(unknown, monkeypatch):
    from tests.test_operations_web_foundation import call_app, header_values
    u, j = unknown, unknown.j
    new = decide(j, '15', 'correction-during-unknown')
    with pytest.raises(Exception, match='尚未收口'):
        j.service.prepare_execution(seed._admin(), [new], 'new-auth')
    evidence = scan(u, monkeypatch)
    app, container, cookie = web(j)
    status, _, body = call_app(app, path='/management/task/' + u.old, cookie=cookie)
    assert status == '200 OK' and '按所选证据终止旧决定' in body
    # The browser submits only the evidence reference/digest rendered by the server.
    form = {key: re.search('name="' + key + '" value="([^"]+)"', body).group(1)
            for key in ('review_id', 'evidence_id', 'evidence_digest', 'idempotency_key')}
    form.update(csrf_token=container.sessions.get(cookie).csrf_token, conclusion='STOP_OLD_DECISION', note='人工核验完成')
    for _ in range(2):
        status, headers, _ = call_app(app, path='/management/price-resolutions/resolve', method='POST', cookie=cookie, form=form)
        assert status == '303 See Other'
        assert 'error' not in header_values(headers, 'Location')[0]
    coordinator, importer, watchdog = rebuild(j)
    assert not coordinator.store.active()
    assert not run_cycle(importer, watchdog, coordinator=coordinator)
    assert j.runtime.get_task(u.old).task_status is TaskStatus.SKIPPED
    with j.runtime.connect_read() as connection:
        op = connection.execute('SELECT * FROM shadowbot_operations WHERE operation_id = ?', (u.item['operation_id'],)).fetchone()
        assert op['status'] == op['resolution_status'] == 'MANUAL_HANDLED'
        assert op['resolved_by'] == 'admin'
        assert connection.execute('SELECT status FROM shadowbot_write_locks').fetchone()[0] == 'RELEASED'
        assert connection.execute('SELECT status FROM shadowbot_commit_batches WHERE batch_id = ?', (u.batch,)).fetchone()[0] == 'UNKNOWN'
        history = connection.execute("SELECT * FROM task_status_history WHERE reason = 'price_execution_human_resolved'").fetchall()
        assert len(history) == 1
        record = json.loads(history[0]['metadata_json'])
        assert record['historical_side_effect'] == 'UNKNOWN'
        assert record['evidence']['digest'] == evidence['digest']
        assert sorted(r[0] for r in connection.execute('SELECT execution_mode FROM shadowbot_execution_attempts')) == ['COMMIT', 'RECONCILE']
    app, container, cookie = web(j)
    status, _, body = call_app(app, path='/management/task/' + u.old, cookie=cookie)
    assert status == '200 OK' and '人工处置已记录' in body and '人工核验完成' in body
    j.service.clock = lambda: datetime.now(UTC)
    accept(j, new, 'new-auth')
    coordinator, importer, watchdog = rebuild(j)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'
    state, result = platform_worker(j, monkeypatch, price='14.00')
    assert state['writes'] == 1 and result['status'] == 'VERIFIED'
    run_cycle(importer, watchdog, coordinator=coordinator)
    assert j.runtime.get_task(new).task_status is TaskStatus.SUCCESS
    assert j.runtime.get_listing_status(seed.PLATFORM, '艾莎', 'A级').current_price == Decimal('15')


def test_target_met_stops_old_without_rewriting_unknown(unknown, monkeypatch):
    evidence = scan(unknown, monkeypatch, price='13.00')
    result = unknown.service.resolve(reviewer(), **request_for(unknown, evidence, conclusion='CURRENT_TARGET_MET'))
    assert result['historical_side_effect'] == 'UNKNOWN'
    assert unknown.j.runtime.get_task(unknown.old).task_status is TaskStatus.SKIPPED


def assert_open(u):
    assert u.j.runtime.get_task(u.old).task_status is TaskStatus.MANUAL_REVIEW
    assert u.service.for_task(u.old)['review_status'] == 'pending'
    assert u.coordinator.store.active()
    with u.j.runtime.connect_read() as c:
        assert c.execute('SELECT status FROM shadowbot_write_locks WHERE operation_id = ?',
            (u.item['operation_id'],)).fetchone()[0] == 'REVIEW_BLOCKED'
        assert c.execute("SELECT COUNT(*) FROM task_status_history WHERE reason = 'price_execution_human_resolved'").fetchone()[0] == 0


def test_evidence_permissions_scope_freshness_and_atomic_rollback(unknown, monkeypatch):
    u = unknown
    evidence = scan(u, monkeypatch)
    request = request_for(u, evidence)
    for actor, changes, match in [
        (seed._admin(), {}, '没有人工复核权限'),
        (reviewer('backup'), {}, '接手'),
        (reviewer(), {'evidence_id': ''}, '备注不能代替证据'),
        (reviewer(), {'evidence_id': 'unknown-evidence'}, '身份不匹配'),
        (reviewer(), {'evidence_digest': 'wrong-digest'}, '已变化'),
        (reviewer(), {'conclusion': 'CURRENT_TARGET_MET'}, '价格不一致'),
    ]:
        with pytest.raises(ValidationError, match=match):
            u.service.resolve(actor, **{**request, **changes})
        assert_open(u)
    with u.j.runtime.connect_write() as c:
        c.execute("UPDATE shadowbot_execution_attempts SET status = 'RUNNING' WHERE execution_mode = 'RECONCILE'")
    with pytest.raises(ValidationError, match='仍在运行'):
        u.service.resolve(reviewer(), **request)
    with u.j.runtime.connect_write() as c:
        c.execute("UPDATE shadowbot_execution_attempts SET status = 'SIDE_EFFECT_UNKNOWN' WHERE execution_mode = 'RECONCILE'")
    u.j.service.clock = lambda: datetime.now(UTC) + timedelta(minutes=31)
    with pytest.raises(ValidationError, match='新鲜平台核验'):
        u.service.resolve(reviewer(), **request)
    u.j.service.clock = lambda: datetime.now(UTC)
    assert_open(u)
    with u.j.runtime.connect_write() as c:
        c.execute("CREATE TRIGGER fail_price_close BEFORE UPDATE OF closed_at ON execution_continuations "
                  "BEGIN SELECT RAISE(ABORT, 'synthetic close failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match='synthetic close failure'):
        u.service.resolve(reviewer(), **request)
    assert_open(u)
    with u.j.runtime.connect_write() as c:
        c.execute('DROP TRIGGER fail_price_close')
    newer = scan(u, monkeypatch, price='13.00', suffix='002')
    with pytest.raises(ValidationError, match='更新的平台事实'):
        u.service.resolve(reviewer(), **request)
    u.service.resolve(reviewer(), **request_for(u, newer, conclusion='CURRENT_TARGET_MET'))
    with pytest.raises(ValidationError, match='原处置不一致'):
        u.service.resolve(reviewer(), **request)


def test_timeout_escalation_and_explicit_takeover_do_not_close_or_retry(unknown, monkeypatch):
    from app.review_policy import allowed_review_statuses
    u = unknown
    service = ReviewTaskService(u.j.runtime)
    review = u.j.runtime.get_review_task(u.review_id)
    assert not allowed_review_statuses(review, u.j.runtime.get_task(u.old))
    for status in (ReviewTaskStatus.APPROVED, ReviewTaskStatus.CANCELLED, ReviewTaskStatus.EXPIRED):
        with pytest.raises(ValidationError, match='平台核验记录'):
            service.resolve_review_task(review_task_id=u.review_id, status=status, actor='admin')
        with pytest.raises(ValidationError, match='平台核验记录'):
            u.j.runtime.resolve_authenticated_review_atomic(review_task_id=u.review_id, status=status,
                actor='admin', actor_source='authenticated_web')
    from app.services.notification_outbox import NotificationOutboxService, FeishuOutboxSender
    outbox = NotificationOutboxService(u.j.runtime)
    candidate, _ = outbox.build_review_notification_candidate(review, event_version='test-no-token', channel='feishu')
    monkeypatch.delenv('REVIEW_TOKEN_SECRET', raising=False)
    delivery, token_id = outbox._prepare_delivery_notification(candidate, FeishuOutboxSender())
    assert not token_id and 'mobile_review_url' not in delivery.payload
    assert service.expire_pending_review_tasks(now=review.required_by + timedelta(seconds=1), apply=True).expired_review_tasks == 0
    monkeypatch.setenv('PRA_PRICE_REVIEW_ESCALATION_SUBJECT', 'backup')
    for count in (1, 2):
        review = u.j.runtime.get_review_task(u.review_id)
        now = review.required_by + timedelta(seconds=1)
        summary = service.renew_overdue_manual_reviews(now=now)
        assert not summary.errors and summary.renewed_review_tasks == 1, summary
        assert service.renew_overdue_manual_reviews(now=now).renewed_review_tasks == 0
        updated = u.j.runtime.get_review_task(u.review_id)
        assert updated.review_payload['reminder_count'] == count
        assert updated.review_payload['owner_subject'] == 'admin'
        assert_open(u)
    assert updated.review_payload['escalation_subject'] == 'backup'
    u.service.claim(reviewer('backup'), review_id=u.review_id)
    assert u.j.runtime.get_review_task(u.review_id).review_payload['owner_subject'] == 'backup'
    assert_open(u)
    with u.j.runtime.connect_read() as c:
        assert c.execute('SELECT COUNT(*) FROM notification_outbox WHERE related_review_task_id = ?',
                         (u.review_id,)).fetchone()[0] == 3
        assert sorted(r[0] for r in c.execute('SELECT execution_mode FROM shadowbot_execution_attempts')) == ['COMMIT', 'RECONCILE']
        claim = c.execute("SELECT metadata_json FROM task_status_history WHERE reason = 'price_execution_review_claimed'").fetchone()
        assert json.loads(claim[0])['previous_owner'] == 'admin'


def test_duplicate_receipts_do_not_reopen_human_resolution(unknown, monkeypatch):
    import shutil
    u = unknown
    evidence = scan(u, monkeypatch)
    u.service.resolve(reviewer(), **request_for(u, evidence))
    paths = u.importer.paths
    for archive in paths.archive.iterdir():
        results = list(archive.glob('*.result.json'))
        if results and (archive.name.startswith('RECONCILE-') or
                        json.loads(results[0].read_text(encoding='utf-8')).get('contract_version') == 4):
            for source in archive.glob('*.request.json*'):
                shutil.copy2(source, paths.working / source.name)
            for source in archive.glob('*.result.json*'):
                shutil.copy2(source, paths.results / source.name)
    events = u.importer.import_available()
    assert events and not any(e.get('status') == 'QUARANTINED' for e in events), events
    assert u.j.runtime.get_task(u.old).task_status is TaskStatus.SKIPPED
    with u.j.runtime.connect_read() as c:
        assert u.service.is_resolved(c, u.item['operation_id'])


def test_web_csrf_generic_review_and_other_sku_remain_independent(unknown, monkeypatch):
    from tests.test_operations_web_foundation import call_app, header_values
    from app.services.manual_task_orchestration import ManualTaskRequest
    u, j = unknown, unknown.j
    evidence = scan(u, monkeypatch)
    app, container, cookie = web(j)
    status, _, body = call_app(app, path='/management', cookie=cookie)
    assert status == '200 OK' and '打开人工核验' in body
    form = request_for(u, evidence)
    status, _, _ = call_app(app, path='/management/price-resolutions/resolve', method='POST', cookie=cookie, form=form)
    assert status.startswith('403')
    assert_open(u)
    status, headers, _ = call_app(app, path='/management/reviews/resolve', method='POST', cookie=cookie,
        form={'csrf_token': container.sessions.get(cookie).csrf_token, 'review_task_id': u.review_id, 'action': 'cancelled'})
    assert status == '303 See Other' and 'review_error' in header_values(headers, 'Location')[0]
    request = ManualTaskRequest(varieties=('艾莎',), grades=('B级',), platforms=(seed.PLATFORM,),
        action='SET_PRICE', price_value=Decimal('10'), idempotency_key='other-sku')
    preview = j.manual.preview(request)
    task = j.manual.create(request, expected_preview_digest=preview.preview_digest, authenticated_subject='admin').task_ids[0]
    accept(j, task, 'other-sku-auth')
    coordinator, importer, watchdog = rebuild(j)
    assert {e['status'] for e in run_cycle(importer, watchdog, coordinator=coordinator)} >= {'HUMAN', 'TRACKING'}
    assert_open(u)
