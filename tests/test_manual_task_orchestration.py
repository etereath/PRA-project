from __future__ import annotations

from dataclasses import replace
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.enums import TaskActionType, TaskOriginType, TaskStatus
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    PLATFORM_MAPPING_HEADERS,
    PRODUCT_HEADERS,
)
from app.services.manual_task_orchestration import (
    CHANGE_PRICE,
    SET_OFFLINE,
    SET_ONLINE,
    SET_PRICE,
    ManualTaskApplicationService,
    ManualTaskConflictError,
    ManualTaskError,
    ManualTaskRequest,
)


NOW = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)
PLATFORM = "蚂蚁花团供应商"


@pytest.fixture()
def manual_service(tmp_path: Path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    products = tmp_path / "products.xlsx"
    mappings = tmp_path / "platform_mappings.xlsx"
    _write_workbook(
        products,
        PRODUCT_HEADERS,
        [
            {
                "internal_sku": "AISHA-A-50-Z",
                "product_name": "艾莎",
                "grade": "A级",
                "stem_length": "50cm",
                "unit": "扎",
                "base_cost": "5.00",
                "current_stock": 72,
                "sale_enabled": True,
            },
            {
                "internal_sku": "AISHA-B-50-Z",
                "product_name": "艾莎",
                "grade": "B级",
                "stem_length": "50cm",
                "unit": "扎",
                "base_cost": "4.00",
                "current_stock": 41,
                "sale_enabled": True,
            },
        ],
    )
    _write_workbook(
        mappings,
        PLATFORM_MAPPING_HEADERS,
        [
            _mapping("MAP-A", "AISHA-A-50-Z", "艾莎", "A级"),
            _mapping("MAP-B", "AISHA-B-50-Z", "艾莎", "B级"),
        ],
    )
    with repository.connect_write() as connection:
        connection.execute(
            """
            UPDATE inventory_authority_state
            SET authority_mode = 'DB_AUTHORITY',
                bootstrap_snapshot_sha256 = ?,
                bootstrap_runtime_snapshot_sha256 = ?,
                bootstrap_sales_watermark_date = '2026-08-13',
                bootstrap_idempotency_key = 'synthetic-bootstrap',
                bootstrap_completed_at = ?,
                bootstrap_completed_by = 'test',
                version = 2, updated_at = ?
            WHERE authority_key = 'REAL_INVENTORY'
            """,
            (
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        for sku, qty in (("AISHA-A-50-Z", 72), ("AISHA-B-50-Z", 41)):
            connection.execute(
                """
                INSERT INTO inventory_balances(
                    internal_sku, current_qty, version,
                    last_transaction_id, updated_at
                ) VALUES (?, ?, 1, ?, ?)
                """,
                (sku, qty, "BOOT-" + sku, NOW.isoformat()),
            )
        connection.commit()
    _listing(repository, "AISHA-A-50-Z", "A级", Decimal("12.00"), "online")
    _listing(repository, "AISHA-B-50-Z", "B级", Decimal("9.00"), "online")
    service = ManualTaskApplicationService(
        repository,
        products_workbook=products,
        platform_mappings_workbook=mappings,
        clock=lambda: NOW,
    )
    return service, repository, products, mappings


def test_scope_options_and_multiselect_preview_use_verified_runtime_facts(
    manual_service,
) -> None:
    service, _, _, _ = manual_service

    options = service.scope_options()
    preview = service.preview(
        ManualTaskRequest(
            varieties=("艾莎",),
            grades=("A级", "B级"),
            platforms=(PLATFORM,),
            action=SET_PRICE,
            price_value=Decimal("13"),
        )
    )

    assert options.varieties == ("艾莎",)
    assert options.grades == ("A级", "B级")
    assert options.platforms == (PLATFORM,)
    assert preview.creatable is True
    assert [item.internal_sku for item in preview.items] == [
        "AISHA-A-50-Z",
        "AISHA-B-50-Z",
    ]
    assert {item.target_price for item in preview.items} == {Decimal("13.00")}
    assert all(item.mapping_ids for item in preview.items)
    assert all(item.price_fact_version.startswith("sha256:") for item in preview.items)


def test_price_preview_accepts_old_observation_but_requires_original_price(
    manual_service,
    monkeypatch,
) -> None:
    service, repository, _, _ = manual_service
    service.clock = lambda: NOW + timedelta(hours=2)
    request = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("A级",),
        platforms=(PLATFORM,),
        action=SET_PRICE,
        price_value=Decimal("13"),
    )

    stale_preview = service.preview(request)

    assert stale_preview.creatable is True
    assert stale_preview.items[0].current_price == Decimal("12.00")
    listing = repository.get_listing_status(PLATFORM, "艾莎", "A级")
    assert listing is not None
    monkeypatch.setattr(
        repository,
        "get_listing_status",
        lambda *_args, **_kwargs: replace(
            listing,
            current_price=None,
            price_observed_at=None,
            price_source_attempt_id=None,
        ),
    )

    missing_preview = service.preview(request)

    assert missing_preview.creatable is False
    assert missing_preview.items[0].blockers == (
        "缺少原价格，请先读取平台价格。",
    )


def test_negative_delta_creates_exact_manual_tasks_without_queue_side_effect(
    manual_service,
    tmp_path: Path,
) -> None:
    service, repository, _, _ = manual_service
    queue_root = tmp_path / "queue"
    request = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("A级", "B级"),
        platforms=(PLATFORM,),
        action=CHANGE_PRICE,
        price_value=Decimal("-1.50"),
        idempotency_key="manual-delta-1",
    )
    preview = service.preview(request)

    result = service.create(
        request,
        expected_preview_digest=preview.preview_digest,
        authenticated_subject="admin",
    )

    assert result.status == "CREATED"
    tasks = [repository.get_task(task_id) for task_id in result.task_ids]
    assert [item.action_type for item in tasks if item is not None] == [
        TaskActionType.UPDATE_PRICE,
        TaskActionType.UPDATE_PRICE,
    ]
    assert {
        (item.expected_old_price, item.target_price)
        for item in tasks
        if item is not None
    } == {
        (Decimal("12.00"), Decimal("10.50")),
        (Decimal("9.00"), Decimal("7.50")),
    }
    assert not queue_root.exists()


def test_exact_replay_returns_same_tasks_and_same_key_different_request_rejects(
    manual_service,
) -> None:
    service, repository, _, _ = manual_service
    first = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("A级",),
        platforms=(PLATFORM,),
        action=SET_PRICE,
        price_value=Decimal("13"),
        idempotency_key="same-key",
    )
    preview = service.preview(first)
    created = service.create(
        first,
        expected_preview_digest=preview.preview_digest,
        authenticated_subject="admin",
    )
    replayed = service.create(
        first,
        expected_preview_digest=preview.preview_digest,
        authenticated_subject="admin",
    )

    assert replayed.status == "REPLAYED"
    assert replayed.task_ids == created.task_ids
    assert len(repository.list_tasks()) == 1

    changed = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("A级",),
        platforms=(PLATFORM,),
        action=SET_PRICE,
        price_value=Decimal("14"),
        idempotency_key="same-key",
    )
    with pytest.raises(ManualTaskConflictError, match="本次提交内容与之前不同"):
        service.create(
            changed,
            expected_preview_digest=service.preview(changed).preview_digest,
            authenticated_subject="admin",
        )


def test_price_change_after_preview_rejects_whole_batch(manual_service) -> None:
    service, repository, _, _ = manual_service
    request = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("A级", "B级"),
        platforms=(PLATFORM,),
        action=SET_PRICE,
        price_value=Decimal("13"),
        idempotency_key="drift",
    )
    preview = service.preview(request)
    _listing(
        repository,
        "AISHA-B-50-Z",
        "B级",
        Decimal("10.00"),
        "online",
        observed_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ManualTaskConflictError, match="预览后发生变化"):
        service.create(
            request,
            expected_preview_digest=preview.preview_digest,
            authenticated_subject="admin",
            now=NOW + timedelta(seconds=2),
        )

    assert repository.list_tasks() == []


def test_offline_has_no_price_and_online_exposure_may_exceed_physical_inventory(
    manual_service,
) -> None:
    service, repository, _, _ = manual_service
    offline_request = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("A级",),
        platforms=(PLATFORM,),
        action=SET_OFFLINE,
        idempotency_key="offline",
    )
    offline_preview = service.preview(offline_request)
    offline = service.create(
        offline_request,
        expected_preview_digest=offline_preview.preview_digest,
        authenticated_subject="admin",
    )
    offline_task = repository.get_task(offline.task_ids[0])
    assert offline_task is not None
    assert offline_task.action_type is TaskActionType.SET_OFFLINE
    assert offline_task.target_price is None
    assert offline_task.target_inventory is None

    _listing(repository, "AISHA-B-50-Z", "B级", Decimal("9.00"), "offline")
    with repository.connect_write() as connection:
        connection.execute("UPDATE inventory_balances SET current_qty = 20 WHERE internal_sku = 'AISHA-B-50-Z'")

    allowed_request = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("B级",),
        platforms=(PLATFORM,),
        action=SET_ONLINE,
        price_value=Decimal("10"),
        target_inventory=50,
        idempotency_key="online",
    )
    allowed = service.preview(allowed_request)
    assert allowed.creatable and allowed.items[0].real_inventory == 20
    created = service.create(
        allowed_request,
        expected_preview_digest=allowed.preview_digest,
        authenticated_subject="admin",
    )
    task = repository.get_task(created.task_ids[0])
    assert task is not None
    assert task.action_type is TaskActionType.SET_ONLINE
    assert task.target_price == Decimal("10.00")
    assert task.target_inventory == 50


@pytest.mark.parametrize('action', [SET_PRICE, SET_OFFLINE, SET_ONLINE])
@pytest.mark.parametrize('change', ['maintenance', 'missing_balance'])
def test_physical_inventory_changes_do_not_invalidate_sales_preview(manual_service, action, change):
    service, repository, _, _ = manual_service
    if action == SET_ONLINE:
        _listing(repository, 'AISHA-A-50-Z', 'A级', Decimal('12.00'), 'offline')
    request = ManualTaskRequest(
        varieties=('艾莎',), grades=('A级',), platforms=(PLATFORM,), action=action,
        price_value=None if action == SET_OFFLINE else Decimal('13'),
        target_inventory=100 if action == SET_ONLINE else None, idempotency_key='stock-independent',
    )
    before = service.preview(request)
    assert before.creatable
    with repository.connect_write() as connection:
        if change == 'maintenance':
            connection.execute("""UPDATE inventory_authority_state SET authority_mode = 'PRE_CUTOVER',
                bootstrap_snapshot_sha256 = NULL, bootstrap_runtime_snapshot_sha256 = NULL,
                bootstrap_sales_watermark_date = NULL, bootstrap_idempotency_key = NULL,
                bootstrap_completed_at = NULL, bootstrap_completed_by = NULL""")
            connection.execute('UPDATE inventory_balances SET current_qty = 1, version = version + 1')
        else:
            connection.execute('DELETE FROM inventory_balances')
    after = service.preview(request)
    assert after.creatable and after.preview_digest == before.preview_digest
    created = service.create(request, expected_preview_digest=before.preview_digest, authenticated_subject='admin')
    assert repository.get_task(created.task_ids[0]).task_status is TaskStatus.PENDING


@pytest.mark.parametrize('failure', ['wrong_status', 'stale_status', 'below_cost', 'negative_exposure'])
def test_online_safety_gates_remain_without_physical_inventory(manual_service, failure):
    service, repository, _, _ = manual_service
    with repository.connect_write() as connection:
        connection.execute('DELETE FROM inventory_balances')
    if failure != 'wrong_status':
        _listing(repository, 'AISHA-A-50-Z', 'A级', Decimal('12.00'), 'offline')
    if failure == 'stale_status':
        service.clock = lambda: NOW + timedelta(days=1)
    request = ManualTaskRequest(varieties=('艾莎',), grades=('A级',), platforms=(PLATFORM,),
        action=SET_ONLINE, price_value=Decimal('4') if failure == 'below_cost' else Decimal('13'),
        target_inventory=-1 if failure == 'negative_exposure' else 100, idempotency_key='unsafe')
    if failure == 'negative_exposure':
        with pytest.raises(ManualTaskError):
            service.preview(request)
    else:
        preview = service.preview(request)
        assert not preview.creatable
        expected = {'wrong_status': '待上架', 'stale_status': '已过期', 'below_cost': '基础成本'}[failure]
        assert expected in ''.join(preview.items[0].blockers)


def test_low_price_mapping_failure_and_open_task_conflict_are_explicit(
    manual_service,
) -> None:
    service, _, _, mappings = manual_service
    low = service.preview(
        ManualTaskRequest(
            varieties=("艾莎",),
            grades=("A级",),
            platforms=(PLATFORM,),
            action=SET_PRICE,
            price_value=Decimal("4.99"),
        )
    )
    assert "不能低于商品基础成本" in "".join(low.items[0].blockers)

    _write_workbook(
        mappings,
        PLATFORM_MAPPING_HEADERS,
        [_mapping("MAP-B", "AISHA-B-50-Z", "艾莎", "B级")],
    )
    unmapped = service.preview(
        ManualTaskRequest(
            varieties=("艾莎",),
            grades=("A级",),
            platforms=(PLATFORM,),
            action=SET_PRICE,
            price_value=Decimal("13"),
        )
    )
    assert "对应关系未确认或存在重复" in "".join(unmapped.items[0].blockers)


def test_exclusion_allows_valid_subset_but_unknown_exclusion_rejects(
    manual_service,
) -> None:
    service, _, _, _ = manual_service
    base = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("A级", "B级"),
        platforms=(PLATFORM,),
        action=SET_PRICE,
        price_value=Decimal("13"),
    )
    first = service.preview(base)
    excluded = service.preview(
        replace(base, excluded_item_keys=(first.items[0].item_key,))
    )
    assert excluded.creatable is True
    assert len(excluded.included_items) == 1

    unknown = service.preview(
        replace(base, excluded_item_keys=("sha256:" + "0" * 64,))
    )
    assert unknown.creatable is False
    assert "排除项不属于当前预览" in "".join(unknown.errors)


def _decision(service, *, action=SET_PRICE, key='old', grade='A级'):
    request = ManualTaskRequest(
        varieties=('艾莎',), grades=(grade,), platforms=(PLATFORM,),
        action=action, price_value=Decimal('13') if action != SET_OFFLINE else None,
        target_inventory=20 if action == SET_ONLINE else None, idempotency_key=key,
    )
    preview = service.preview(request)
    result = service.create(request, expected_preview_digest=preview.preview_digest,
                            authenticated_subject='admin')
    return result.task_ids[0]


@pytest.mark.parametrize('old_action,new_action,superseded', [
    (SET_PRICE, SET_OFFLINE, True), (SET_OFFLINE, SET_PRICE, False),
    (SET_ONLINE, SET_PRICE, False), (SET_ONLINE, SET_OFFLINE, True),
])
def test_sales_decision_supersedes_unpublished_other_action_atomically(
    manual_service, old_action, new_action, superseded,
):
    service, repository, _, _ = manual_service
    if old_action == SET_ONLINE:
        _listing(repository, 'AISHA-A-50-Z', 'A级', Decimal('12.00'), 'offline')
    old = _decision(service, action=old_action)
    _listing(repository, 'AISHA-A-50-Z', 'A级', Decimal('12.00'), 'online')
    new = _decision(service, action=new_action, key='new')
    assert repository.get_task(old).task_status is (
        TaskStatus.CANCELLED if superseded else TaskStatus.PENDING
    )
    assert repository.get_task(new).task_status is TaskStatus.PENDING
    assert repository.get_task(new).decision_trace['predecessor_task_ids'] == ([] if superseded else [old])
    assert _decision(service, action=new_action, key='new') == new
    with repository.connect_read() as connection:
        history = connection.execute('SELECT * FROM task_status_history WHERE task_id = ?', (old,)).fetchall()
        if superseded:
            assert len(history) == 1 and history[0]['to_status'] == 'cancelled'
        else:
            assert not history
        assert connection.execute('SELECT COUNT(*) FROM shadowbot_execution_attempts').fetchone()[0] == 0


@pytest.mark.parametrize('origin,ref,superseded', [
    ('AUTOMATION', 'task-generation:test', True),
    ('SYSTEM_EMERGENCY', 'emergency:test', False),
    ('MANUAL', 'incident-review:test', False),
])
def test_human_offline_supersedes_only_normal_automation_price(manual_service, origin, ref, superseded):
    service, repository, _, _ = manual_service
    old = 'TASK-ORIGIN-FIXTURE'
    task = Task(
        task_id=old, internal_sku='AISHA-A-50-Z', platform_name=PLATFORM,
        action_type=(TaskActionType.SET_OFFLINE if origin == 'SYSTEM_EMERGENCY' else TaskActionType.UPDATE_PRICE),
        priority=1, task_status=TaskStatus.PENDING, created_at=NOW,
        origin_type=TaskOriginType(origin), origin_ref_id=ref,
    )
    if origin == 'SYSTEM_EMERGENCY':
        # Seed an existing emergency record; this test does not authorize or
        # exercise emergency creation, which has its own dedicated service.
        with repository.connect_write() as connection:
            repository._insert_tasks_on_connection(connection, [task])
    else:
        repository.insert_tasks([task])
    new = _decision(service, action=SET_OFFLINE, key='new')
    assert repository.get_task(old).task_status is (
        TaskStatus.CANCELLED if superseded else TaskStatus.PENDING
    )
    assert repository.get_task(new).decision_trace['predecessor_task_ids'] == ([] if superseded else [old])
    with repository.connect_read() as connection:
        assert connection.execute('SELECT COUNT(*) FROM shadowbot_execution_attempts').fetchone()[0] == 0


def test_supersession_history_failure_rolls_back_new_and_old_decisions(manual_service):
    service, repository, _, _ = manual_service
    old = _decision(service)
    with repository.connect_write() as connection:
        connection.execute("""CREATE TRIGGER fail_supersession BEFORE INSERT ON task_status_history
            BEGIN SELECT RAISE(ABORT, 'synthetic history failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic history failure'):
        _decision(service, action=SET_OFFLINE, key='new')
    assert repository.get_task(old).task_status is TaskStatus.PENDING
    assert [t.task_id for t in repository.list_tasks()] == [old]


def test_expiry_history_failure_rolls_back_task_transition(manual_service):
    from app.services.runtime import RuntimeTaskService

    service, repository, _, _ = manual_service
    old = _decision(service)
    with repository.connect_write() as connection:
        connection.execute('UPDATE tasks SET expires_at = ? WHERE task_id = ?',
                           ((NOW - timedelta(seconds=1)).isoformat(), old))
        connection.execute("""CREATE TRIGGER fail_expiry BEFORE INSERT ON task_status_history
            BEGIN SELECT RAISE(ABORT, 'synthetic expiry history failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic expiry history failure'):
        RuntimeTaskService(repository).expire_overdue_pending_tasks(now=NOW)
    assert repository.get_task(old).task_status is TaskStatus.PENDING


@pytest.mark.parametrize('terminal', ['success', 'skipped', 'cancelled', 'expired'])
def test_terminal_history_leaves_current_queue_and_does_not_block_new_decision(manual_service, terminal):
    from app.operations_web.composition import OperationsWebPaths
    from app.operations_web.queries import OperationsQueryService
    from app.services.price_decisions import unresolved_predecessors
    from app.services.runtime import RuntimeTaskService

    service, repository, products, mappings = manual_service
    old = _decision(service)
    runtime = RuntimeTaskService(repository)
    if terminal == 'expired':
        with repository.connect_write() as connection:
            connection.execute('UPDATE tasks SET expires_at = ? WHERE task_id = ?',
                               ((NOW - timedelta(seconds=1)).isoformat(), old))
    else:
        if terminal == 'success':
            runtime.change_status(task_id=old, to_status=TaskStatus.RUNNING, changed_by='test')
        runtime.change_status(task_id=old, to_status=TaskStatus(terminal), changed_by='test')
    query = OperationsQueryService(repository, OperationsWebPaths(
        runtime_db=repository.db_path, products_workbook=products,
        platform_mappings_workbook=mappings, price_rules_workbook=products,
        listing_rules_workbook=products, queue_root=products.parent / 'queue',
    ), now_provider=lambda: NOW)
    assert not query.management().pending_task_options
    assert repository.get_task(old).task_status is TaskStatus(terminal)
    assert runtime.list_status_history(old)[-1].to_status is TaskStatus(terminal)
    assert runtime.expire_overdue_pending_tasks(now=NOW) == 0
    new = _decision(service, action=SET_OFFLINE, key='new')
    with repository.connect_read() as connection:
        assert not unresolved_predecessors(connection, new)


def _mapping(mapping_id: str, sku: str, product_name: str, grade: str):
    return {
        "mapping_id": mapping_id,
        "mapping_kind": "PRODUCT",
        "internal_sku": sku,
        "platform_name": PLATFORM,
        "platform_product_name": product_name,
        "grade": grade,
        "mapping_status": "VERIFIED",
        "remark": "合成测试映射",
    }


def _write_workbook(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    workbook.save(path)


def _listing(
    repository: SQLiteRuntimeRepository,
    sku: str,
    grade: str,
    price: Decimal,
    status: str,
    *,
    observed_at: datetime = NOW,
) -> None:
    repository.apply_shadowbot_inventory_observation(
        platform_name=PLATFORM,
        variety="艾莎",
        grade=grade,
        internal_sku=sku,
        observed_price=price,
        platform_stock_qty=20,
        online_status=status,
        observed_at=observed_at,
        execution_attempt_id="ATTEMPT-" + sku + "-" + str(price),
    )
