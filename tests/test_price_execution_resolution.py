"""P1-47-01: real unknown/reconcile, durable scan import, human closure and next write.

The v4 flow uses the existing synthetic UI adapter; the read-only v5 scan uses
a platform-result fixture. Publishers, Queue Worker and Importers remain real.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
import types
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.test_human_price_journey import (
    journey as _journey,
    decide,
    accept,
    rebuild,
    platform_worker,
    seed,
    run_cycle,
)
from tests.test_shadowbot_listing_sync import _result, _item
from app.enums import TaskStatus
from app.exceptions import ValidationError
from app.operations_web.auth import Principal, Capability
from app.services.price_execution_resolution import PriceExecutionResolutionApplicationService
from app.services.listing_scan_quality import ListingScanQuality
from app.services.shadowbot_executor import (
    ShadowBotFileQueueRunner,
    ShadowBotStartBoundaryError,
)
from app.services.shadowbot_queue import _try_worker_stop_lock
from app.services.shadowbot_listing_sync import prepare_listing_sync_batch, publish_listing_sync_batch
from app.shadowbot_contract_primitives import queue_stop_fence_sha256
from app.services.runtime import ReviewTaskService
from app.repositories.automation_repository import AutomationRepository


def reviewer(subject='admin'):
    return Principal(subject, frozenset({Capability.HANDLE_REVIEW}))


@pytest.fixture(name='journey')
def _local_journey(tmp_path, monkeypatch):
    return _journey.__wrapped__(tmp_path, monkeypatch)


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
    assert model['payload']['historical_side_effect'] == 'UNKNOWN'
    assert model['payload']['execution_stopped_at']
    assert AutomationRepository(journey.runtime).active_ui_blocker() == ''
    with journey.runtime.connect_read() as connection:
        item = dict(connection.execute('SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?',
                                      (prepared.batch_id,)).fetchone())
    return types.SimpleNamespace(j=journey, old=old, batch=prepared.batch_id, item=item, service=service,
        review_id=model['review_task_id'], importer=importer, watchdog=watchdog, coordinator=coordinator)


@pytest.fixture
def start_failed_unknown(journey, monkeypatch):
    """UNKNOWN whose unique RECONCILE failed at the explicit pre-publish boundary."""
    old = decide(journey)
    prepared = accept(journey, old)
    coordinator, importer, watchdog = rebuild(journey)
    run_cycle(importer, watchdog, coordinator=coordinator)
    state, result = platform_worker(journey, monkeypatch, fail_after_click=True)
    assert result['batch_status'] == 'UNKNOWN' and state['writes'] == 1

    original_start = ShadowBotFileQueueRunner.start

    def fail_reconcile_before_publish(runner, payload):
        if payload.get('execution_mode') == 'RECONCILE':
            raise ShadowBotStartBoundaryError(
                'synthetic reconcile start failure',
                published=False,
            )
        return original_start(runner, payload)

    monkeypatch.setattr(
        ShadowBotFileQueueRunner,
        'start',
        fail_reconcile_before_publish,
    )
    events = run_cycle(importer, watchdog, coordinator=coordinator)
    assert events[-1]['status'] == 'HUMAN', events
    service = PriceExecutionResolutionApplicationService(journey.service)
    model = service.for_task(old)
    assert model is not None and model['review_status'] == 'pending'
    with journey.runtime.connect_read() as connection:
        item = dict(connection.execute(
            'SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?',
            (prepared.batch_id,),
        ).fetchone())
        reconcile = dict(connection.execute(
            "SELECT * FROM shadowbot_execution_attempts WHERE execution_mode = 'RECONCILE'",
        ).fetchone())
    raw = json.loads(reconcile['raw_output_json'])
    assert reconcile['status'] == 'START_FAILED'
    assert raw['published'] is False
    assert not reconcile['request_file_sha256']
    assert not reconcile['queue_request_path']
    return types.SimpleNamespace(
        j=journey,
        old=old,
        batch=prepared.batch_id,
        item=item,
        service=service,
        review_id=model['review_task_id'],
        importer=importer,
        watchdog=watchdog,
        coordinator=coordinator,
        reconcile=reconcile,
        reconcile_raw=raw,
    )


@pytest.fixture
def published_unknown(journey, monkeypatch):
    """UNKNOWN whose RECONCILE request was published but start outcome was lost."""
    old = decide(journey)
    prepared = accept(journey, old)
    coordinator, importer, watchdog = rebuild(journey)
    run_cycle(importer, watchdog, coordinator=coordinator)
    state, result = platform_worker(journey, monkeypatch, fail_after_click=True)
    assert result['batch_status'] == 'UNKNOWN' and state['writes'] == 1

    original_start = ShadowBotFileQueueRunner.start

    def fail_reconcile_after_publish(runner, payload):
        started = original_start(runner, payload)
        if payload.get('execution_mode') == 'RECONCILE':
            raise ShadowBotStartBoundaryError(
                'synthetic reconcile response lost after publication',
                published=True,
                raw_output=started.raw_output,
            )
        return started

    monkeypatch.setattr(
        ShadowBotFileQueueRunner,
        'start',
        fail_reconcile_after_publish,
    )
    events = importer.import_available()
    assert events and events[0]['status'] == 'IMPORTED', events
    with journey.runtime.connect_read() as connection:
        item = dict(connection.execute(
            'SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?',
            (prepared.batch_id,),
        ).fetchone())
        reconcile = dict(connection.execute(
            "SELECT * FROM shadowbot_execution_attempts WHERE execution_mode = 'RECONCILE'",
        ).fetchone())
    raw = json.loads(reconcile['raw_output_json'])
    request_path = journey.service.queue_root / 'inbox' / (
        reconcile['execution_attempt_id'] + '.ready.json'
    )
    assert reconcile['status'] == 'START_UNKNOWN'
    assert reconcile['ended_at'] and raw['published'] is True
    assert raw['lease']['active'] is False
    assert request_path.exists()
    return types.SimpleNamespace(
        j=journey,
        old=old,
        batch=prepared.batch_id,
        item=item,
        importer=importer,
        watchdog=watchdog,
        coordinator=coordinator,
        reconcile=reconcile,
        request_path=request_path,
    )


def scan(unknown, monkeypatch, *, price='14.00', suffix='001'):
    j = unknown.j
    manifest = prepare_listing_sync_batch(j.runtime, batch_id='BATCH-PRICE-SCAN-' + suffix,
        platform_name=seed.PLATFORM, mapping_path=j.service.shadowbot_identity_mapping,
        execution_profile=j.service.execution_profile)
    request, _ = publish_listing_sync_batch(j.runtime, ShadowBotFileQueueRunner(j.service.queue_root),
        manifest=manifest, execution_profile=j.service.execution_profile, applet_uri=j.service.applet_uri)
    # A platform observation fixture crosses the same real file Worker/import boundary.
    # Whole-second observations must be strictly later than the stopped execution.
    stopped = unknown.service.for_task(unknown.old)['payload']['execution_stopped_at']
    not_before = datetime.fromisoformat(stopped).replace(microsecond=0) + timedelta(seconds=1)
    delay = (not_before - datetime.now(UTC)).total_seconds()
    if delay > 0:
        time.sleep(delay)
    now = datetime.now(UTC).replace(microsecond=0)
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
    quality = ListingScanQuality(
        schema_version='listing-scan-qualification-2.0',
        operating_fact_qualified=True,
        fact_reason_codes=(),
        delivery_archive_healthy=True,
        delivery_reason_codes=(),
        platform_name=seed.PLATFORM,
        internal_sku=unknown.item['internal_sku'],
        platform_product_identity_digest='sha256:' + '1' * 64,
        authority_mode='DB_AUTHORITY',
        authority_generation=1,
        mapping_snapshot_sha256='sha256:' + '2' * 64,
        mapping_version='test-mapping',
        provider='SHADOWBOT_SYNC_STATUS',
        observation_type='LISTING_STATUS_SCAN',
        source_run_id='run-listing-scan-' + suffix,
        observation_batch_id='OBS-BATCH-' + suffix,
        source_snapshot_id=snapshot['snapshot_id'],
        source_execution_attempt_id=result['execution_attempt_id'],
        source_manifest_sha256='sha256:' + '3' * 64,
        source_result_sha256='4' * 64,
        observation_content_sha256='5' * 64,
        qualification_sha256='sha256:' + suffix.zfill(64),
        observed_at=now.isoformat(),
        scan_completed_at=now.isoformat(),
        fresh_until=(now + timedelta(minutes=30)).isoformat(),
        evaluated_at=datetime.now(UTC).isoformat(),
        scope_complete=True,
        end_marker_verified=True,
        observed_online=True,
        observed_price=price,
        observed_inventory=20,
    )
    unknown.service.quality = types.SimpleNamespace(
        latest=lambda **kwargs: quality
    )
    model = unknown.service.for_task(unknown.old)
    assert model['evidence'], (model, events)
    return model['evidence']


def request_for(unknown, evidence=None):
    return dict(review_id=unknown.review_id, idempotency_key='human-close-001',
        note='已核对平台当前价格，终止旧决定。')


def web(j, quality=None):
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
    if quality is not None:
        app.price_resolution.quality = quality
    _, cookie = login(app, container)
    return app, container, cookie


def test_web_human_closure_restart_and_new_authorized_price(unknown, monkeypatch):
    from tests.test_operations_web_foundation import call_app, header_values
    u, j = unknown, unknown.j
    new = decide(j, '15', 'correction-during-unknown')
    with pytest.raises(Exception, match='尚未收口'):
        j.service.prepare_execution(seed._admin(), [new], 'new-auth')
    evidence = scan(u, monkeypatch)
    app, container, cookie = web(j, u.service.quality)
    status, _, body = call_app(app, path='/management/task/' + u.old, cookie=cookie)
    assert status == '200 OK' and '零平台写终止旧 one-shot' in body
    # The browser submits no evidence selector or recovery classification.
    form = {key: re.search('name="' + key + '" value="([^"]+)"', body).group(1)
            for key in ('review_id', 'idempotency_key')}
    form.update(csrf_token=container.sessions.get(cookie).csrf_token, note='人工核验完成')
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
        assert record['evidence']['qualification_sha256'] == evidence['qualification_sha256']
        assert sorted(r[0] for r in connection.execute('SELECT execution_mode FROM shadowbot_execution_attempts')) == ['COMMIT', 'RECONCILE']
    app, container, cookie = web(j, u.service.quality)
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


def test_historical_result_is_not_classified_from_current_price(unknown, monkeypatch):
    evidence = scan(unknown, monkeypatch, price='13.00')
    result = unknown.service.resolve(reviewer(), **request_for(unknown, evidence))
    assert result['historical_side_effect'] == 'UNKNOWN'
    assert result['conclusion'] == 'OLD_ONE_SHOT_TERMINATED'
    assert unknown.j.runtime.get_task(unknown.old).task_status is TaskStatus.SKIPPED


def test_explicit_not_published_boundary_allows_incomplete_reconcile_but_timeout_does_not(
    start_failed_unknown,
    monkeypatch,
):
    u = start_failed_unknown
    timeout_raw = dict(u.reconcile_raw)
    timeout_raw.pop('published')
    timeout_raw['lease'] = {
        **timeout_raw['lease'],
        'active': False,
        'expired_at': u.reconcile['ended_at'],
    }
    with u.j.runtime.connect_write() as connection:
        connection.execute(
            "UPDATE shadowbot_execution_attempts SET status = 'START_UNKNOWN', "
            "raw_output_json = ? WHERE execution_attempt_id = ?",
            (json.dumps(timeout_raw), u.reconcile['execution_attempt_id']),
        )
    model = u.service.for_task(u.old)
    assert model['evidence'] is None
    assert model['evidence_error']
    with pytest.raises(ValidationError):
        u.service.resolve(reviewer(), **request_for(u))
    assert_open(u)

    with u.j.runtime.connect_write() as connection:
        connection.execute(
            "UPDATE shadowbot_execution_attempts SET status = 'START_FAILED', "
            "raw_output_json = ? WHERE execution_attempt_id = ?",
            (json.dumps(u.reconcile_raw), u.reconcile['execution_attempt_id']),
        )
    evidence = scan(u, monkeypatch, price='13.00')
    result = u.service.resolve(reviewer(), **request_for(u, evidence))
    proofs = {
        item['execution_mode']: item['proof_type']
        for item in result['stop_proof']['attempts']
    }
    assert proofs == {
        'COMMIT': 'IMPORTED_RESULT',
        'RECONCILE': 'NOT_PUBLISHED',
    }
    assert result['historical_side_effect'] == 'UNKNOWN'
    assert u.j.runtime.get_task(u.old).task_status is TaskStatus.SKIPPED


def test_published_reconcile_is_durably_fenced_before_web_atomic_close(
    published_unknown,
    monkeypatch,
):
    from tests.test_operations_web_foundation import call_app, header_values
    import shadowbot_queue_worker

    u = published_unknown
    queue_root = u.j.service.queue_root
    marker = queue_root / 'control' / 'request_fences' / (
        u.reconcile['execution_attempt_id'] + '.fence.json'
    )

    # A terminal-looking DB row is not enough while its lease is active.
    original_raw = json.loads(u.reconcile['raw_output_json'])
    active_raw = json.loads(u.reconcile['raw_output_json'])
    active_raw['lease']['active'] = True
    with u.j.runtime.connect_write() as connection:
        connection.execute(
            'UPDATE shadowbot_execution_attempts SET raw_output_json = ? '
            'WHERE execution_attempt_id = ?',
            (json.dumps(active_raw), u.reconcile['execution_attempt_id']),
        )
    assert not any(
        event.get('status') == 'REQUEST_FENCED'
        for event in u.watchdog.inspect()
    )
    assert u.request_path.exists() and not marker.exists()
    with u.j.runtime.connect_write() as connection:
        connection.execute(
            'UPDATE shadowbot_execution_attempts SET raw_output_json = ? '
            'WHERE execution_attempt_id = ?',
            (json.dumps(original_raw), u.reconcile['execution_attempt_id']),
        )

    # The Watchdog cannot declare the old request stopped while a Worker owns
    # the same process-wide lock used for the entire claim/execute lifetime.
    with _try_worker_stop_lock(queue_root / 'control' / 'worker.lock') as locked:
        assert locked
        assert not any(
            event.get('status') == 'REQUEST_FENCED'
            for event in u.watchdog.inspect()
        )
        assert u.request_path.exists() and not marker.exists()

    # Simulate the crash window after durable marker + quarantine but before
    # the DB attempt receives the proof.  A later pass must resume from marker.
    record_fence = u.j.runtime.record_shadowbot_queue_stop_fence
    monkeypatch.setattr(
        u.j.runtime,
        'record_shadowbot_queue_stop_fence',
        lambda *args, **kwargs: False,
    )
    events = u.watchdog.inspect()
    assert any(
        event.get('error_code') == 'QUEUE_STOP_FENCE_PERSIST_FAILED'
        for event in events
    ), events
    assert marker.exists() and not u.request_path.exists()
    with u.j.runtime.connect_read() as connection:
        raw = json.loads(connection.execute(
            'SELECT raw_output_json FROM shadowbot_execution_attempts '
            'WHERE execution_attempt_id = ?',
            (u.reconcile['execution_attempt_id'],),
        ).fetchone()[0])
    assert 'queue_stop_fence' not in raw

    monkeypatch.setattr(
        u.j.runtime,
        'record_shadowbot_queue_stop_fence',
        record_fence,
    )
    events = u.watchdog.inspect()
    assert any(event.get('status') == 'REQUEST_FENCED' for event in events), events
    proof = json.loads(marker.read_text(encoding='utf-8'))
    assert proof['execution_attempt_id'] == u.reconcile['execution_attempt_id']
    assert proof['operation_id'] == u.item['operation_id']
    assert proof['source_execution_attempt_id'] == u.item['item_execution_attempt_id']
    assert proof['instruction_hash'] == u.reconcile['instruction_hash']
    assert proof['worker_lock_acquired'] is True
    with u.j.runtime.connect_read() as connection:
        persisted_raw = json.loads(connection.execute(
            'SELECT raw_output_json FROM shadowbot_execution_attempts '
            'WHERE execution_attempt_id = ?',
            (u.reconcile['execution_attempt_id'],),
        ).fetchone()[0])
    assert persisted_raw['queue_stop_fence'] == proof

    # Reappearing byte-identical work remains unexecutable: the Worker checks
    # the attempt-bound durable marker before moving a ready request to working.
    quarantined = queue_root / 'quarantine' / 'request_fences' / u.reconcile['execution_attempt_id']
    archived_request = quarantined / u.request_path.name
    archived_checksum = archived_request.with_suffix(archived_request.suffix + '.sha256')
    shutil.copy2(archived_request, u.request_path)
    shutil.copy2(
        archived_checksum,
        u.request_path.with_suffix(u.request_path.suffix + '.sha256'),
    )
    worker = shadowbot_queue_worker.QueueWorker({
        'queue_dir': str(queue_root),
        'poll_seconds': .01,
        'max_hours': .1,
        'max_tasks': 1,
        'heartbeat_seconds': .01,
        'login_auto_enabled': False,
    })
    assert worker._claim_next() is None
    assert not u.request_path.exists()
    errors = list((queue_root / 'quarantine').glob('*-request-error.json'))
    assert errors
    assert json.loads(errors[-1].read_text(encoding='utf-8'))['error_message'] == (
        'REQUEST_DURABLY_FENCED'
    )

    events = run_cycle(u.importer, u.watchdog, coordinator=u.coordinator)
    assert events[-1]['status'] == 'HUMAN', events
    u.service = PriceExecutionResolutionApplicationService(u.j.service)
    model = u.service.for_task(u.old)
    assert model is not None and model['review_status'] == 'pending'
    u.review_id = model['review_task_id']
    model_proofs = {
        item['execution_mode']: item['proof_type']
        for item in model['payload']['stop_proof']['attempts']
    }
    assert model_proofs['RECONCILE'] == 'QUEUE_REQUEST_QUARANTINED'
    assert datetime.fromisoformat(model['payload']['execution_stopped_at']) >= (
        datetime.fromisoformat(proof['fenced_at'])
    )

    # Even a self-consistent digest cannot transplant this proof to a different
    # source attempt; the human-close service binds it back to this UNKNOWN.
    tampered_raw = dict(persisted_raw)
    tampered_proof = dict(proof)
    tampered_proof['source_execution_attempt_id'] = 'different-attempt'
    tampered_proof['proof_sha256'] = queue_stop_fence_sha256(tampered_proof)
    tampered_raw['queue_stop_fence'] = tampered_proof
    with u.j.runtime.connect_write() as connection:
        connection.execute(
            'UPDATE shadowbot_execution_attempts SET raw_output_json = ? '
            'WHERE execution_attempt_id = ?',
            (json.dumps(tampered_raw), u.reconcile['execution_attempt_id']),
        )
    assert u.service.for_task(u.old)['evidence_error']
    with u.j.runtime.connect_write() as connection:
        connection.execute(
            'UPDATE shadowbot_execution_attempts SET raw_output_json = ? '
            'WHERE execution_attempt_id = ?',
            (json.dumps(persisted_raw), u.reconcile['execution_attempt_id']),
        )

    evidence = scan(u, monkeypatch)
    app, container, cookie = web(u.j, u.service.quality)
    status, _, body = call_app(
        app,
        path='/management/task/' + u.old,
        cookie=cookie,
    )
    assert status == '200 OK' and 'one-shot' in body
    form = {
        key: re.search('name="' + key + '" value="([^"]+)"', body).group(1)
        for key in ('review_id', 'idempotency_key')
    }
    form.update(
        csrf_token=container.sessions.get(cookie).csrf_token,
        note='published reconcile queue fence verified',
    )

    with u.j.runtime.connect_write() as connection:
        connection.execute(
            "CREATE TRIGGER fail_fenced_price_close BEFORE UPDATE OF closed_at "
            "ON execution_continuations BEGIN SELECT RAISE(ABORT, "
            "'synthetic fenced close failure'); END"
        )
    status, headers, _ = call_app(
        app,
        path='/management/price-resolutions/resolve',
        method='POST',
        cookie=cookie,
        form=form,
    )
    assert status == '303 See Other'
    assert 'error' in header_values(headers, 'Location')[0]
    assert_open(u)
    with u.j.runtime.connect_write() as connection:
        connection.execute('DROP TRIGGER fail_fenced_price_close')

    status, headers, _ = call_app(
        app,
        path='/management/price-resolutions/resolve',
        method='POST',
        cookie=cookie,
        form=form,
    )
    assert status == '303 See Other'
    assert 'error' not in header_values(headers, 'Location')[0]
    assert u.j.runtime.get_task(u.old).task_status is TaskStatus.SKIPPED
    with u.j.runtime.connect_read() as connection:
        history = connection.execute(
            "SELECT metadata_json FROM task_status_history "
            "WHERE reason = 'price_execution_human_resolved'",
        ).fetchone()
        operation = connection.execute(
            'SELECT * FROM shadowbot_operations WHERE operation_id = ?',
            (u.item['operation_id'],),
        ).fetchone()
        assert operation['status'] == operation['resolution_status'] == 'MANUAL_HANDLED'
        assert connection.execute(
            'SELECT status FROM shadowbot_commit_batches WHERE batch_id = ?',
            (u.batch,),
        ).fetchone()[0] == 'UNKNOWN'
    resolution = json.loads(history[0])
    proofs = {
        item['execution_mode']: item['proof_type']
        for item in resolution['stop_proof']['attempts']
    }
    assert proofs == {
        'COMMIT': 'IMPORTED_RESULT',
        'RECONCILE': 'QUEUE_REQUEST_QUARANTINED',
    }
    assert resolution['historical_side_effect'] == 'UNKNOWN'
    assert resolution['evidence']['qualification_sha256'] == evidence['qualification_sha256']


def assert_open(u):
    assert u.j.runtime.get_task(u.old).task_status is TaskStatus.MANUAL_REVIEW
    assert u.service.for_task(u.old)['review_status'] == 'pending'
    assert u.coordinator.store.active()
    with u.j.runtime.connect_read() as c:
        assert c.execute('SELECT status FROM shadowbot_write_locks WHERE operation_id = ?',
            (u.item['operation_id'],)).fetchone()[0] == 'REVIEW_BLOCKED'
        assert c.execute("SELECT COUNT(*) FROM task_status_history WHERE reason = 'price_execution_human_resolved'").fetchone()[0] == 0


def test_permissions_qualification_boundary_idempotency_and_atomic_rollback(unknown, monkeypatch):
    u = unknown
    evidence = scan(u, monkeypatch)
    request = request_for(u, evidence)
    for actor, changes, match in [
        (seed._admin(), {}, '没有人工复核权限'),
        (reviewer(), {'idempotency_key': ''}, '幂等键'),
    ]:
        with pytest.raises(ValidationError, match=match):
            u.service.resolve(actor, **{**request, **changes})
        assert_open(u)
    good = u.service.quality
    qualified = good.latest()
    u.service.quality = types.SimpleNamespace(
        latest=lambda **kwargs: replace(
            qualified,
            operating_fact_qualified=False,
            fact_reason_codes=('OBSERVATION_STALE',),
        )
    )
    with pytest.raises(ValidationError, match='OBSERVATION_STALE'):
        u.service.resolve(reviewer(), **request)
    u.service.quality = good
    with u.j.runtime.connect_write() as c:
        c.execute("UPDATE shadowbot_execution_attempts SET status = 'RUNNING' WHERE execution_mode = 'RECONCILE'")
    with pytest.raises(ValidationError, match='仍在运行'):
        u.service.resolve(reviewer(), **request)
    with u.j.runtime.connect_write() as c:
        c.execute("UPDATE shadowbot_execution_attempts SET status = 'SIDE_EFFECT_UNKNOWN' WHERE execution_mode = 'RECONCILE'")
    boundary = u.service.for_task(u.old)['payload']['execution_stopped_at']
    u.service.quality = types.SimpleNamespace(
        latest=lambda **kwargs: replace(
            qualified,
            observed_at=boundary,
            scan_completed_at=boundary,
        )
    )
    with pytest.raises(ValidationError, match='停止边界'):
        u.service.resolve(reviewer(), **request)
    u.service.quality = good
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
    resolved = u.service.resolve(reviewer(), **request_for(u, newer))
    assert resolved['evidence']['qualification_sha256'] == newer['qualification_sha256']
    with pytest.raises(ValidationError, match='原处置不一致'):
        u.service.resolve(reviewer(), **{**request, 'idempotency_key': 'different'})


def test_any_authorized_admin_can_terminate_without_claim_reminders_or_old_runtime_config(unknown, monkeypatch):
    u = unknown
    review = u.j.runtime.get_review_task(u.review_id)
    assert review.required_by is None
    reminders = ReviewTaskService(u.j.runtime).renew_overdue_manual_reviews(
        now=datetime.now(UTC) + timedelta(days=1)
    )
    assert reminders.renewed_review_tasks == 0
    evidence = scan(u, monkeypatch)
    u.j.service.execution_profile = 'replacement-profile'
    u.j.service.applet_uri = ''
    u.j.service.queue_root = u.j.root / 'replacement-queue'
    result = u.service.resolve(reviewer('backup'), **request_for(u, evidence))
    assert result['principal_subject'] == 'backup'
    with u.j.runtime.connect_read() as c:
        assert sorted(r[0] for r in c.execute('SELECT execution_mode FROM shadowbot_execution_attempts')) == ['COMMIT', 'RECONCILE']
        assert c.execute("SELECT COUNT(*) FROM task_status_history WHERE reason = 'price_execution_review_claimed'").fetchone()[0] == 0


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
    app, container, cookie = web(j, u.service.quality)
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
