"""One isolated Runtime: real application, publisher, file Worker, flow and Importer.

Only xbot UI observations/clicks/window handling are replaced. No authorization,
publication, phase serialization, receipt or result-import arrow is mocked.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip('msvcrt', reason='The formal Queue Worker uses Windows process locks')

from app.enums import TaskStatus
from app.models import ShadowBotOperationLedger
from app.services.execution_authorization import ExecutionAuthorizationApplicationService
from app.services.execution_authorization import ExecutionAuthorizationConflict
from app.services.manual_task_orchestration import ManualTaskApplicationService, ManualTaskRequest
from app.services.manual_task_orchestration import ManualTaskError
from app.services.shadowbot_commit_pipeline import publish_task_commit_batch
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_queue import ShadowBotResultImporter, ShadowBotQueueWatchdog
from app.services.task_execution_coordinator import TaskExecutionCoordinator
from scripts.run_shadowbot_queue_services import run_cycle, build_execution_coordinator
import tests.test_execution_authorization as seed


@pytest.fixture
def journey(tmp_path, monkeypatch):
    # The platform adapter records observations at whole-second precision.
    monkeypatch.setattr(seed, 'NOW', datetime.now(UTC).replace(microsecond=0))
    service, runtime, _ = seed.execution_setup.__wrapped__(tmp_path)
    with runtime.connect_write() as connection:
        connection.execute("UPDATE tasks SET task_status = 'cancelled'")
    service.v4_publish = publish_task_commit_batch
    service.runner_factory = ShadowBotFileQueueRunner
    for key, value in {
        'PRA_ENV': service.execution_profile,
        'SHADOWBOT_APPLET_URI': service.applet_uri,
        'PRA_PLATFORM_MAPPINGS_WORKBOOK': str(service.platform_mappings_workbook),
        'PRA_SHADOWBOT_IDENTITY_MAPPING': str(service.shadowbot_identity_mapping),
    }.items():
        monkeypatch.setenv(key, value)
    manual = ManualTaskApplicationService(runtime, products_workbook=service.products_workbook,
        platform_mappings_workbook=service.platform_mappings_workbook, clock=service.clock)
    return types.SimpleNamespace(service=service, runtime=runtime, manual=manual, root=tmp_path)


def decide(journey, price='13', key='decision-1'):
    request = ManualTaskRequest(varieties=('艾莎',), grades=('A级',), platforms=(seed.PLATFORM,),
                                action='SET_PRICE', price_value=Decimal(price), idempotency_key=key)
    preview = journey.manual.preview(request)
    return journey.manual.create(request, expected_preview_digest=preview.preview_digest,
                                  authenticated_subject='admin').task_ids[0]


def accept(journey, task_id, key='authorize-1'):
    p = journey.service.prepare_execution(seed._admin(), [task_id], key)
    r = journey.service.submit_execution(seed._admin(), [task_id], p.confirmation_digest, key)
    assert r.execution_attempt_id == ''
    return p


def rebuild(journey):
    old = journey.service
    importer = ShadowBotResultImporter(journey.runtime, ShadowBotFileQueueRunner(old.queue_root),
                                       old.queue_root, inventory_products_path=old.products_workbook)
    coordinator = build_execution_coordinator(journey.runtime, importer,
        products=old.products_workbook, queue_dir=old.queue_root)
    coordinator.service.clock = old.clock
    return coordinator, importer, ShadowBotQueueWatchdog(old.queue_root, repository=journey.runtime)


def platform_worker(journey, monkeypatch, *, price='12.00', fail_after_click=False):
    """Load the full production flow with only its Windows/UI adapter removed."""
    source = Path('shadowbot/test2/vertical_slice_read_price.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if not (
        isinstance(n, ast.Import) and any(a.name == 'xbot' for a in n.names)
        or isinstance(n, ast.ImportFrom) and (
            str(n.module).startswith('xbot') or n.level and any(a.name == 'package' for a in n.names)))]
    flow = types.ModuleType('vertical_slice_read_price')
    flow.__dict__.update(__package__='shadowbot.test2', __file__=str(source.resolve()),
                         print=lambda *a, **k: None, sleep=lambda *a: None)
    exec(compile(tree, str(source), 'exec'), flow.__dict__)
    state = {'price': price, 'filled': '', 'writes': 0}
    def rows(window, timeout, items):
        row = {'position': 1, 'parent_index': 1, 'name': '艾莎', 'grade': 'A级',
               'price': state['price'], 'inventory': 20, 'listing_status': 'ONLINE'}
        return [row], {i['source_task_id']: row for i in items}
    def fill(window, target, *args):
        state['filled'] = target
        return target
    def click(*args):
        state['price'] = state['filled']
        state['writes'] += 1
        if fail_after_click:
            raise flow.SliceError('PLATFORM_DISCONNECTED', 'Synthetic click outcome unknown', False)
    overrides = {
        '_get_or_open_and_prepare_window': lambda *a, **k: (object(), {}),
        '_recover_login_if_needed': lambda *a, **k: None,
        '_prepare_product_list': lambda *a, **k: {},
        '_refresh_product_list': lambda *a, **k: {},
        '_commit_v4_prepare_product_list': lambda *a, **k: None,
        '_commit_v4_scan_target_rows': rows,
        '_commit_v4_prepare_first_target_for_click': lambda *a, **k: {},
        '_locate_product_row_at_position': lambda *a, **k: (1, '艾莎', 'A级', 1),
        '_locate_product_row': lambda *a, **k: (1, '艾莎', 'A级', 1),
        '_read_row_price': lambda *a, **k: state['price'],
        '_open_price_dialog': lambda *a, **k: None,
        '_read_dialog_context': lambda *a, **k: {'product_name': '艾莎', 'grade': 'A级', 'current_price': state['price']},
        '_fill_target_price': fill,
        '_confirm_price_dialog': click,
        '_wait_after_submit_price': lambda *a, **k: (state['price'], ''),
        '_result_output_path': lambda attempt: str(journey.root / (attempt + '.flow-result.json')),
        '_capture_window': lambda *a, **k: {'status': 'CAPTURED'},
    }
    flow.__dict__.update(overrides)
    monkeypatch.setitem(sys.modules, 'vertical_slice_read_price', flow)
    monkeypatch.syspath_prepend(str(Path('shadowbot/test2').resolve()))
    import shadowbot_queue_worker
    worker = shadowbot_queue_worker.QueueWorker({'queue_dir': str(journey.service.queue_root),
        'poll_seconds': .01, 'max_hours': .1, 'max_tasks': 1, 'heartbeat_seconds': .01,
        'login_auto_enabled': False})
    claimed = worker._claim_next()
    assert claimed is not None
    worker._execute_claimed(*claimed)
    result = json.loads(next(worker.results.glob('*.result.json')).read_text(encoding='utf-8'))
    return state, result


def test_human_price_normal_and_restart_journey(journey, monkeypatch):
    task = decide(journey)
    prepared = accept(journey, task)
    assert not list(journey.service.queue_root.glob('inbox/*.ready.json'))
    coordinator, importer, watchdog = rebuild(journey)
    events = run_cycle(importer, watchdog, coordinator=coordinator)
    assert events[-1]['status'] == 'TRACKING', events
    state, result = platform_worker(journey, monkeypatch)
    assert result['status'] == 'VERIFIED', result
    assert state['writes'] == 1
    coordinator, importer, watchdog = rebuild(journey)
    events = run_cycle(importer, watchdog, coordinator=coordinator)
    assert journey.runtime.get_task(task).task_status is TaskStatus.SUCCESS, events
    assert not coordinator.store.active()
    listing = journey.runtime.get_listing_status(seed.PLATFORM, '艾莎', 'A级')
    assert listing.current_price == Decimal('13')
    assert listing.price_source_attempt_id in {
        result['execution_attempt_id'], result['items'][0]['item_execution_attempt_id']}
    assert listing.price_observed_at is not None
    with journey.runtime.connect_read() as connection:
        receipt = connection.execute('SELECT * FROM shadowbot_commit_result_receipts').fetchone()
        assert receipt['batch_id'] == prepared.batch_id
        assert connection.execute('SELECT COUNT(*) FROM shadowbot_execution_attempts').fetchone()[0] == 1
    assert not run_cycle(importer, watchdog, coordinator=coordinator)


def test_late_commit_result_preserves_newer_external_price_and_its_source(journey, monkeypatch):
    task = decide(journey)
    accept(journey, task)
    coordinator, importer, watchdog = rebuild(journey)
    run_cycle(importer, watchdog, coordinator=coordinator)
    _, result = platform_worker(journey, monkeypatch)
    assert result['status'] == 'VERIFIED'
    observed = datetime.now(UTC) + timedelta(seconds=1)
    journey.runtime.apply_shadowbot_inventory_observation(platform_name=seed.PLATFORM,
        variety='艾莎', grade='A级', internal_sku='AISHA-A-50-Z', observed_price=Decimal('14'),
        platform_stock_qty=20, online_status='online', observed_at=observed,
        execution_attempt_id='newer-platform-observation')
    run_cycle(importer, watchdog, coordinator=coordinator)
    listing = journey.runtime.get_listing_status(seed.PLATFORM, '艾莎', 'A级')
    assert journey.runtime.get_task(task).task_status is TaskStatus.SUCCESS
    assert listing.current_price == Decimal('14')
    assert listing.price_observed_at == observed
    assert listing.price_source_attempt_id == 'newer-platform-observation'


def test_unconfirmed_prepared_never_executes_after_restart(journey):
    task = decide(journey)
    journey.service.prepare_execution(seed._admin(), [task], 'unconfirmed')
    coordinator, importer, watchdog = rebuild(journey)
    assert not run_cycle(importer, watchdog, coordinator=coordinator)
    assert not list(journey.service.queue_root.glob('inbox/*.ready.json'))


def test_latest_price_is_saved_and_supersedes_unpublished_authorization(journey):
    old = decide(journey)
    accept(journey, old)
    new = decide(journey, '14', 'decision-2')
    assert journey.runtime.get_task(old).task_status is TaskStatus.CANCELLED
    assert journey.runtime.get_task(new).target_price == Decimal('14')
    coordinator, importer, watchdog = rebuild(journey)
    assert not run_cycle(importer, watchdog, coordinator=coordinator)
    assert not list(journey.service.queue_root.glob('inbox/*.ready.json'))


@pytest.mark.parametrize('price,outcome', [('14', 'RECONFIRM'), ('13', 'ALREADY_APPLIED')])
def test_external_change_or_already_completed_has_no_write(journey, price, outcome):
    task = decide(journey)
    accept(journey, task)
    seed._listing(journey.runtime, 'AISHA-A-50-Z', 'A级', Decimal(price), 'online')
    coordinator, importer, watchdog = rebuild(journey)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == outcome
    assert not list(journey.service.queue_root.glob('inbox/*.ready.json'))


def test_authorization_expiry_is_terminal_before_publish(journey):
    task = decide(journey)
    accept(journey, task)
    coordinator, _, _ = rebuild(journey)
    assert coordinator.run_cycle(now=seed.NOW + timedelta(minutes=11))[-1]['status'] == 'EXPIRED'
    assert journey.runtime.get_task(task).task_status is TaskStatus.EXPIRED


def test_final_confirmation_failure_before_commit_rolls_back_audit_and_handoff(journey):
    task = decide(journey)
    p = journey.service.prepare_execution(seed._admin(), [task], 'before-commit')
    with journey.runtime.connect_write() as connection:
        connection.execute("""CREATE TRIGGER fail_auth BEFORE INSERT ON task_status_history
            WHEN NEW.reason = 'execution_submission_authorized'
            BEGIN SELECT RAISE(ABORT, 'synthetic crash before commit'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        journey.service.submit_execution(seed._admin(), [task], p.confirmation_digest, 'before-commit')
    coordinator, importer, watchdog = rebuild(journey)
    assert not coordinator.store.active()
    assert not journey.runtime.list_task_status_history(task)
    assert not run_cycle(importer, watchdog, coordinator=coordinator)
    with pytest.raises(ExecutionAuthorizationConflict):
        coordinator.service.submit_execution(seed._admin(), [task], p.confirmation_digest, 'before-commit')


def test_exit_after_acceptance_commit_replays_and_resumes_once(journey, monkeypatch):
    task = decide(journey)
    p = journey.service.prepare_execution(seed._admin(), [task], 'after-commit')
    original = journey.service.continuations.accept
    def crash(envelope, **kwargs):
        original(envelope, **kwargs)
        raise SystemExit('synthetic process exit after commit')
    monkeypatch.setattr(journey.service.continuations, 'accept', crash)
    with pytest.raises(SystemExit):
        journey.service.submit_execution(seed._admin(), [task], p.confirmation_digest, 'after-commit')
    coordinator, importer, watchdog = rebuild(journey)
    r = coordinator.service.submit_execution(seed._admin(), [task], p.confirmation_digest, 'after-commit')
    assert r.batch_id == p.batch_id
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'
    assert len(list(journey.service.queue_root.glob('inbox/*.ready.json'))) == 1
    assert len(journey.runtime.list_task_status_history(task)) == 1


def test_real_ui_lease_blocker_resumes_after_release_and_service_restart(journey, monkeypatch):
    from tests.test_shadowbot_commit_pipeline import _insert_active_automation_ui_run
    task = decide(journey)
    accept(journey, task)
    _insert_active_automation_ui_run(journey.runtime)
    coordinator, importer, watchdog = rebuild(journey)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'BLOCKED'
    assert not list(journey.service.queue_root.glob('inbox/*.ready.json'))
    with journey.runtime.connect_write() as connection:
        connection.execute("UPDATE automation_runs SET run_status = 'SUCCESS' WHERE run_id = 'UI-RUN'")
    coordinator, importer, watchdog = rebuild(journey)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'
    state, result = platform_worker(journey, monkeypatch)
    assert result['status'] == 'VERIFIED' and state['writes'] == 1
    run_cycle(importer, watchdog, coordinator=coordinator)
    assert journey.runtime.get_task(task).task_status is TaskStatus.SUCCESS


def test_published_old_decision_finishes_then_new_correction_requires_confirmation(journey, monkeypatch):
    old = decide(journey)
    accept(journey, old)
    coordinator, importer, watchdog = rebuild(journey)
    run_cycle(importer, watchdog, coordinator=coordinator)
    new = decide(journey, '14', 'new-while-queued')
    assert journey.runtime.get_task(old).task_status is TaskStatus.RUNNING
    with pytest.raises(ExecutionAuthorizationConflict, match='尚未收口'):
        journey.service.prepare_execution(seed._admin(), [new], 'correction')
    _, result = platform_worker(journey, monkeypatch)
    assert result['status'] == 'VERIFIED'
    run_cycle(importer, watchdog, coordinator=coordinator)
    # New readback is later than the fixture's initial clock.
    journey.service.clock = lambda: datetime.now(UTC)
    p = journey.service.prepare_execution(seed._admin(), [new], 'correction')
    assert journey.runtime.get_task(new).expected_old_price == Decimal('13')
    assert not list(journey.service.queue_root.glob('inbox/*.ready.json'))
    journey.service.submit_execution(seed._admin(), [new], p.confirmation_digest, 'correction')
    coordinator, importer, watchdog = rebuild(journey)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'
    state, result = platform_worker(journey, monkeypatch, price='13.00')
    assert state['writes'] == 1 and result['status'] == 'VERIFIED'
    run_cycle(importer, watchdog, coordinator=coordinator)
    assert journey.runtime.get_task(new).task_status is TaskStatus.SUCCESS


@pytest.mark.parametrize('published', [False, True])
def test_process_exit_at_publisher_boundary_never_repeats_commit(journey, monkeypatch, published):
    task = decide(journey)
    accept(journey, task)
    coordinator, importer, watchdog = rebuild(journey)
    class CrashRunner:
        def start(self, request):
            if published:
                ShadowBotFileQueueRunner(journey.service.queue_root).start(request)
            raise SystemExit('synthetic process exit at publication boundary')
    coordinator.service.runner_factory = lambda path: CrashRunner()
    with pytest.raises(SystemExit):
        run_cycle(importer, watchdog, coordinator=coordinator)
    coordinator, importer, watchdog = rebuild(journey)
    if published:
        assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'
        state, result = platform_worker(journey, monkeypatch)
        assert state['writes'] == 1 and result['status'] == 'VERIFIED'
        run_cycle(importer, watchdog, coordinator=coordinator)
        assert journey.runtime.get_task(task).task_status is TaskStatus.SUCCESS
    else:
        assert coordinator.run_cycle(now=datetime.now(UTC) + timedelta(seconds=40))[-1]['status'] == 'RECONCILING'
        new = decide(journey, '14', 'new-while-unknown')
        assert journey.runtime.get_task(new).task_status is TaskStatus.PENDING
        for _ in range(2):
            coordinator.run_cycle(now=datetime.now(UTC) + timedelta(seconds=41))
        with journey.runtime.connect_read() as connection:
            modes = [r[0] for r in connection.execute('SELECT execution_mode FROM shadowbot_execution_attempts')]
        assert sorted(modes) == ['COMMIT', 'RECONCILE']
        requests = [json.loads(p.read_text(encoding='utf-8')) for p in journey.service.queue_root.glob('inbox/*.ready.json')]
        assert len(requests) == 1 and requests[0]['execution_mode'] == 'RECONCILE'
        state, result = platform_worker(journey, monkeypatch)
        assert state['writes'] == 0 and result['status'] == 'NOT_APPLIED', result
        run_cycle(importer, watchdog, coordinator=coordinator)
        assert journey.runtime.get_task(task).task_status is TaskStatus.SKIPPED
        listing = journey.runtime.get_listing_status(seed.PLATFORM, '艾莎', 'A级')
        assert listing.price_source_attempt_id == result['execution_attempt_id']
        assert listing.current_price == Decimal('12')


def test_worker_unknown_uses_unique_reconcile_and_preserves_latest_decision(journey, monkeypatch):
    task = decide(journey)
    accept(journey, task)
    coordinator, importer, watchdog = rebuild(journey)
    run_cycle(importer, watchdog, coordinator=coordinator)
    state, result = platform_worker(journey, monkeypatch, fail_after_click=True)
    assert result['batch_status'] == 'UNKNOWN', result
    assert state['writes'] == 1
    run_cycle(importer, watchdog, coordinator=coordinator)
    new = decide(journey, '14', 'unknown-new')
    assert journey.runtime.get_task(new).task_status is TaskStatus.PENDING
    coordinator, importer, watchdog = rebuild(journey)
    for _ in range(2):
        run_cycle(importer, watchdog, coordinator=coordinator)
    with journey.runtime.connect_read() as connection:
        modes = [r[0] for r in connection.execute('SELECT execution_mode FROM shadowbot_execution_attempts')]
    assert sorted(modes) == ['COMMIT', 'RECONCILE']
    state, result = platform_worker(journey, monkeypatch, price='13.00')
    assert state['writes'] == 0 and result['status'] == 'VERIFIED', result
    run_cycle(importer, watchdog, coordinator=coordinator)
    assert journey.runtime.get_task(task).task_status is TaskStatus.SUCCESS


def test_cancel_is_durable_and_cannot_cancel_published_work(journey):
    task = decide(journey)
    accept(journey, task)
    journey.manual.cancel_price_decisions([task], authenticated_subject='admin')
    coordinator, importer, watchdog = rebuild(journey)
    assert not run_cycle(importer, watchdog, coordinator=coordinator)
    new = decide(journey, '14', 'another')
    accept(journey, new, 'another-auth')
    run_cycle(importer, watchdog, coordinator=coordinator)
    with pytest.raises(ManualTaskError, match='不能取消'):
        journey.manual.cancel_price_decisions([new], authenticated_subject='admin')


def test_pending_task_with_unresolved_operation_cannot_be_cancelled(journey):
    task = decide(journey)
    journey.runtime.insert_shadowbot_operation(ShadowBotOperationLedger(
        operation_id='legacy-active', task_id=task, platform=seed.PLATFORM,
        product_identity={}, expected_old_price=Decimal('12'), target_price=Decimal('13'),
        status='NEEDS_RECONCILIATION'))
    with pytest.raises(ManualTaskError, match='不能取消'):
        journey.manual.cancel_price_decisions([task], authenticated_subject='admin')
    assert journey.runtime.get_task(task).task_status is TaskStatus.PENDING


def test_cancelling_one_batch_item_returns_remaining_decision_to_confirmation(journey):
    request = ManualTaskRequest(varieties=('艾莎',), grades=('A级', 'B级'),
        platforms=(seed.PLATFORM,), action='SET_PRICE', price_value=Decimal('13'),
        idempotency_key='two-sku-decisions')
    preview = journey.manual.preview(request)
    tasks = journey.manual.create(request, expected_preview_digest=preview.preview_digest,
        authenticated_subject='admin').task_ids
    preparation = journey.service.prepare_execution(seed._admin(), tasks, 'two-sku-auth')
    journey.service.submit_execution(seed._admin(), tasks, preparation.confirmation_digest, 'two-sku-auth')
    journey.manual.cancel_price_decisions([tasks[0]], authenticated_subject='admin')
    assert journey.service.continuations.replay('admin', 'two-sku-auth')['outcome'] == 'RECONFIRM'
    coordinator, importer, watchdog = rebuild(journey)
    assert not run_cycle(importer, watchdog, coordinator=coordinator)
    assert journey.runtime.get_task(tasks[1]).task_status is TaskStatus.PENDING
    accept(journey, tasks[1], 'remaining-auth')
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'


@pytest.mark.parametrize('raw_price', ['NaN', '-1', 'invalid', None])
def test_invalid_or_missing_reconcile_price_cannot_refresh_platform_fact(journey, raw_price):
    _, importer, _ = rebuild(journey)
    operation = ShadowBotOperationLedger(operation_id='observed-op', task_id='observed-task',
        platform=seed.PLATFORM, product_identity={'name': '艾莎', 'grade': 'A级'},
        expected_old_price=Decimal('12'), target_price=Decimal('13'), status='NOT_APPLIED')
    before = journey.runtime.get_listing_status(seed.PLATFORM, '艾莎', 'A级')
    result = types.SimpleNamespace(business_operation_completed=False,
        execution_mode='RECONCILE', status='NOT_APPLIED', execution_attempt_id='invalid-observation',
        raw_output={'actual_price': raw_price, 'observed_at': journey.service.clock().isoformat()})
    importer.executor._update_listing_status_after_result(operation=operation, result=result)
    after = journey.runtime.get_listing_status(seed.PLATFORM, '艾莎', 'A级')
    assert (after.current_price, after.price_observed_at, after.price_source_attempt_id) == (
        before.current_price, before.price_observed_at, before.price_source_attempt_id)


def test_single_object_and_coordinator_failures_do_not_stop_other_components(journey, monkeypatch):
    from unittest.mock import Mock
    a = decide(journey)
    accept(journey, a)
    request = ManualTaskRequest(varieties=('艾莎',), grades=('B级',), platforms=(seed.PLATFORM,),
        action='SET_PRICE', price_value=Decimal('10'), idempotency_key='second-sku')
    preview = journey.manual.preview(request)
    b = journey.manual.create(request, expected_preview_digest=preview.preview_digest,
        authenticated_subject='admin').task_ids[0]
    accept(journey, b, 'second-auth')
    coordinator, importer, watchdog = rebuild(journey)
    advance = coordinator._advance
    def broken_item(row, now):
        if a in json.loads(row['envelope_json'])['task_ids']:
            raise ValueError('synthetic single object error')
        return advance(row, now)
    monkeypatch.setattr(coordinator, '_advance', broken_item)
    review = Mock()
    review.renew_overdue_manual_reviews.return_value = types.SimpleNamespace(renewed_review_tasks=0, errors=[])
    outbox = Mock()
    outbox.run_watchdog.return_value = []
    outbox.run_once.return_value = None
    result = run_cycle(importer, watchdog, review_service=review, notification_worker=outbox, coordinator=coordinator)
    assert {e['status'] for e in result} >= {'RETRY_PENDING', 'TRACKING'}
    review.renew_overdue_manual_reviews.assert_called_once()
    outbox.run_once.assert_called_once()
    monkeypatch.setattr(coordinator, 'run_cycle', Mock(side_effect=ValueError('coordinator failed')))
    result = run_cycle(importer, watchdog, review_service=review, notification_worker=outbox, coordinator=coordinator)
    assert result[-1]['error_code'] == 'COORDINATOR_FAILED'
    assert review.renew_overdue_manual_reviews.call_count == 2
    assert outbox.run_once.call_count == 2


def test_web_create_authorize_and_readback_uses_real_application(journey, monkeypatch):
    import re
    from urllib.parse import urlsplit
    from app.operations_web.app import create_application
    from app.operations_web.composition import OperationsWebPaths, OperationsWebSettings, build_container
    from tests.test_operations_web_foundation import call_app, login, header_values
    s = journey.service
    settings = OperationsWebSettings(environment='development', public_scheme='http', cookie_secure=False,
        admin_username='admin', admin_password='synthetic-local-password', shadowbot_applet_uri=s.applet_uri,
        paths=OperationsWebPaths(runtime_db=journey.runtime.db_path, products_workbook=s.products_workbook,
            price_rules_workbook=journey.root / 'price.xlsx', listing_rules_workbook=journey.root / 'listing.xlsx',
            queue_root=s.queue_root, platform_mappings_workbook=s.platform_mappings_workbook,
            shadowbot_identity_mapping=s.shadowbot_identity_mapping, backup_root=journey.root / 'backups'))
    container = build_container(settings)
    app = create_application(container)
    _, cookie = login(app, container)
    csrf = container.sessions.get(cookie).csrf_token
    def post(path, form):
        status, headers, body = call_app(app, path=path, method='POST', cookie=cookie,
                                         form={'csrf_token': csrf, **form})
        assert status == '303 See Other', body
        location = header_values(headers, 'Location')[0]
        status, _, body = call_app(app, path='/management', cookie=cookie, query=urlsplit(location).query)
        assert status == '200 OK', body
        return body
    body = post('/management/tasks/preview', {'varieties': '艾莎', 'grades': 'A级',
        'platforms': seed.PLATFORM, 'action': 'SET_PRICE', 'price_value': '13', 'idempotency_key': 'http-decision'})
    token = re.search(r'name="preview_token" value="([^"]+)"', body).group(1)
    digest = re.search(r'name="preview_digest" value="([^"]+)"', body).group(1)
    post('/management/tasks/create', {'preview_token': token, 'preview_digest': digest})
    with journey.runtime.connect_read() as connection:
        task = connection.execute("SELECT task_id FROM tasks WHERE task_status = 'pending'").fetchone()[0]
    body = post('/management/executions/prepare', {'task_ids': task, 'idempotency_key': 'http-auth'})
    digest = re.search(r'name="confirmation_digest" value="([^"]+)"', body).group(1)
    body = post('/management/executions/submit', {'task_ids': task, 'idempotency_key': 'http-auth', 'confirmation_digest': digest})
    assert '执行授权已接受' in body
    coordinator, importer, watchdog = rebuild(journey)
    assert run_cycle(importer, watchdog, coordinator=coordinator)[-1]['status'] == 'TRACKING'
    _, result = platform_worker(journey, monkeypatch)
    assert result['status'] == 'VERIFIED'
    run_cycle(importer, watchdog, coordinator=coordinator)
    # Rebuild the Web application as well: the result is a Runtime projection.
    app = create_application(container)
    status, _, body = call_app(app, path='/management/task/' + task, cookie=cookie)
    assert status == '200 OK'
    assert '执行回读价格' in body and '13.00' in body and 'RESULT-' in body


def test_v18_schema_rejects_mutated_authorization_and_detects_missing_gate(journey):
    task = decide(journey)
    accept(journey, task)
    with journey.runtime.connect_write() as connection:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            connection.execute("UPDATE execution_continuations SET envelope_json = '{}'")
        connection.execute('DROP TRIGGER execution_continuations_authorization_immutable')
    assert not journey.runtime.check_schema_health().ok
