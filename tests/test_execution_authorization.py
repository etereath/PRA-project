from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from app.enums import (
    PricingSource,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.models import Task
from app.operations_web.auth import (
    Capability,
    Principal,
    PrincipalCapabilityBackend,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    PLATFORM_MAPPING_HEADERS,
    PRODUCT_HEADERS,
)
from app.services.execution_authorization import (
    ExecutionAuthorizationApplicationService,
    ExecutionAuthorizationBlocked,
    ExecutionAuthorizationConflict,
    ExecutionAuthorizationForbidden,
)
from app.services.product_mapping import compile_product_mapping_workbook
from app.services.shadowbot_commit_batch import build_commit_request
from app.services.shadowbot_listing_action_contract import (
    V5_GATE_SUMMARY_SCHEMA_VERSION,
    build_listing_action_manifest,
    build_listing_action_request,
)


NOW = datetime(2099, 8, 13, 2, 0, tzinfo=UTC)
PLATFORM = "蚂蚁花团供应商"


@pytest.fixture()
def execution_setup(tmp_path: Path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    products = tmp_path / "products.xlsx"
    mappings = tmp_path / "platform_mappings.xlsx"
    identity = tmp_path / "product_identity_mapping.json"
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
            _mapping("MAP-A", "AISHA-A-50-Z", "A级"),
            _mapping("MAP-B", "AISHA-B-50-Z", "B级"),
        ],
    )
    identity.write_text(
        json.dumps(
            {
                "schema_version": "synthetic",
                "platform_name": PLATFORM,
                "mappings": [
                    {
                        "internal_sku": "AISHA-A-50-Z",
                        "expected_product_name": "艾莎",
                        "expected_grade": "A级",
                        "status": "active",
                    },
                    {
                        "internal_sku": "AISHA-B-50-Z",
                        "expected_product_name": "艾莎",
                        "expected_grade": "B级",
                        "status": "active",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
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
                bootstrap_completed_at = ?, bootstrap_completed_by = 'test',
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
    _listing(repository, "AISHA-A-50-Z", "A级", Decimal("12"), "online")
    _listing(repository, "AISHA-B-50-Z", "B级", Decimal("9"), "online")
    mapping_version = compile_product_mapping_workbook(mappings).mapping_version
    repository.insert_tasks(
        [
            _task(
                "TASK-PRICE-A",
                "AISHA-A-50-Z",
                "A级",
                TaskActionType.UPDATE_PRICE,
                mapping_version,
                expected_old_price=Decimal("12"),
                target_price=Decimal("13"),
            ),
            _task(
                "TASK-OFFLINE-B",
                "AISHA-B-50-Z",
                "B级",
                TaskActionType.SET_OFFLINE,
                mapping_version,
                target_status="offline",
            ),
        ]
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_v4_publish(repository, runner, **kwargs):
        calls.append(("v4", kwargs))
        request = build_commit_request(
            kwargs["manifest"],
            execution_profile=kwargs["execution_profile"],
            batch_task_id="BATCHTASK-SYNTHETIC-V4",
            operation_id="OP-SYNTHETIC-V4",
            execution_attempt_id="ATTEMPT-V4",
            applet_uri=kwargs["applet_uri"],
            confirmation_text=kwargs["confirmation_text"],
            confirmed_by=kwargs["confirmed_by"],
        )
        return (
            request,
            SimpleNamespace(shadowbot_run_id="RUN-V4"),
        )

    def fake_v5_propose(repository, *, batch_id, task_ids, **kwargs):
        task_id = task_ids[0]
        manifest = build_listing_action_manifest(
            batch_id=batch_id,
            action_type="set_offline",
            task_items=[
                {
                    "source_task_id": task_id,
                    "internal_sku": "AISHA-B-50-Z",
                    "expected_old_status": "online",
                    "target_status": "offline",
                    "expires_at": (NOW + timedelta(hours=1)).isoformat(),
                }
            ],
            identity_mapping={
                "AISHA-B-50-Z": {
                    "expected_product_name": "艾莎",
                    "expected_grade": "B级",
                }
            },
            platform_name=PLATFORM,
            mapping_source_version="sha256:" + "c" * 64,
        )
        return {
            "manifest": manifest,
            "execution_profile": service.execution_profile,
            "publishable": True,
            "required_confirmation": str(
                manifest.get("development_confirmation_text") or ""
            ),
        }

    def fake_v5_publish(repository, runner, **kwargs):
        calls.append(("v5", kwargs))
        proposal = kwargs["proposal"]
        item = proposal["manifest"]["items"][0]
        gate_summary = {
            "schema_version": V5_GATE_SUMMARY_SCHEMA_VERSION,
            "gate_phase": "PRE_PUBLISH",
            "evaluated_at": NOW.isoformat(),
            "items": [
                {
                    "internal_sku": item["internal_sku"],
                    "operation_id": item["operation_id"],
                    "decision": "EXECUTE",
                    "lock_status": "ACTIVE",
                    "lock_operation_id": item["operation_id"],
                    "block_reasons": [],
                }
            ],
        }
        request = build_listing_action_request(
            proposal["manifest"],
            execution_profile=proposal["execution_profile"],
            execution_attempt_id="ATTEMPT-V5",
            applet_uri=kwargs["applet_uri"],
            gate_summary=gate_summary,
            batch_task_id="BATCH-TASK-SYNTHETIC-V5",
            batch_operation_id="BATCH-OP-SYNTHETIC-V5",
            confirmation_text=kwargs["confirmation_text"],
            confirmed_by=kwargs["confirmed_by"],
        )
        return (
            request,
            SimpleNamespace(shadowbot_run_id="RUN-V5"),
        )

    service = ExecutionAuthorizationApplicationService(
        repository,
        authorization=PrincipalCapabilityBackend(),
        products_workbook=products,
        platform_mappings_workbook=mappings,
        shadowbot_identity_mapping=identity,
        queue_root=tmp_path / "queue",
        applet_uri="weixin://launchapplet/?app_id=synthetic",
        execution_profile="development",
        clock=lambda: NOW,
        runner_factory=lambda path: SimpleNamespace(path=path),
        v4_publish=fake_v4_publish,
        v5_propose=fake_v5_propose,
        v5_publish=fake_v5_publish,
    )
    return service, repository, calls


def test_prepare_requires_submit_execution_capability(execution_setup) -> None:
    service, _, _ = execution_setup
    viewer = Principal("viewer", frozenset({Capability.MANAGE_BUSINESS}))

    with pytest.raises(ExecutionAuthorizationForbidden, match="没有提交"):
        service.prepare_execution(viewer, ["TASK-PRICE-A"], "auth-1")


def test_update_price_authorization_accepts_old_observation(
    execution_setup,
) -> None:
    service, repository, calls = execution_setup
    with repository.connect_write() as connection:
        connection.execute(
            """UPDATE listing_status
               SET price_observed_at = ?
               WHERE platform_name = ? AND internal_sku = ?""",
            ((NOW - timedelta(hours=2)).isoformat(), PLATFORM, "AISHA-A-50-Z"),
        )
        connection.commit()

    prepared = service.prepare_execution(_admin(), ["TASK-PRICE-A"], "stale-price")
    submitted = service.submit_execution(
        _admin(),
        ["TASK-PRICE-A"],
        prepared.confirmation_digest,
        "stale-price",
    )

    assert submitted.batch_id
    assert submitted.execution_attempt_id == ""
    assert not calls


def test_update_price_authorization_rejects_missing_original_price(
    execution_setup,
) -> None:
    service, repository, _ = execution_setup
    with repository.connect_write() as connection:
        connection.execute(
            "UPDATE tasks SET expected_old_price = NULL WHERE task_id = ?",
            ("TASK-PRICE-A",),
        )
        connection.commit()

    with pytest.raises(ExecutionAuthorizationConflict, match="任务缺少原价格"):
        service.prepare_execution(_admin(), ["TASK-PRICE-A"], "missing-price")


def test_v4_prepare_and_submit_bind_exact_principal_tasks_and_actor(
    execution_setup,
) -> None:
    service, _, calls = execution_setup
    admin = _admin()

    prepared = service.prepare_execution(admin, ["TASK-PRICE-A"], "auth-v4")
    submitted = service.submit_execution(
        admin,
        ["TASK-PRICE-A"],
        prepared.confirmation_digest,
        "auth-v4",
    )

    assert prepared.action_type is TaskActionType.UPDATE_PRICE
    assert submitted.execution_attempt_id == ""
    assert not calls
    from app.services.task_execution_coordinator import TaskExecutionCoordinator
    TaskExecutionCoordinator(service, executor=None).run_cycle(now=NOW)
    assert calls[0][0] == "v4"
    assert calls[0][1]["confirmed_by"] == "admin"
    assert calls[0][1]["manifest"]["items"][0]["source_task_id"] == "TASK-PRICE-A"
    replay = service.submit_execution(
            admin,
            ["TASK-PRICE-A"],
            prepared.confirmation_digest,
            "auth-v4",
        )
    assert replay.batch_id == submitted.batch_id
    assert len(calls) == 1


def test_production_publish_omits_development_confirmation_and_audits_actor(
    execution_setup,
) -> None:
    service, repository, calls = execution_setup
    service.execution_profile = "production"
    admin = _admin()

    prepared = service.prepare_execution(admin, ["TASK-PRICE-A"], "auth-production")
    service.submit_execution(
        admin,
        ["TASK-PRICE-A"],
        prepared.confirmation_digest,
        "auth-production",
    )

    assert not calls
    from app.services.task_execution_coordinator import TaskExecutionCoordinator
    TaskExecutionCoordinator(service, executor=None).run_cycle(now=NOW)
    assert calls[0][1]["confirmed_by"] == ""
    assert calls[0][1]["confirmation_text"] == ""
    assert calls[0][1]["execution_profile"] == "production"
    price_audit = repository.list_task_status_history("TASK-PRICE-A")
    assert price_audit[-1].changed_by == "admin"
    assert price_audit[-1].reason == "execution_submission_authorized"
    assert price_audit[-1].from_status is TaskStatus.PENDING
    assert price_audit[-1].to_status is TaskStatus.PENDING

    listing = service.prepare_execution(
        admin,
        ["TASK-OFFLINE-B"],
        "auth-production-listing",
    )
    service.submit_execution(
        admin,
        ["TASK-OFFLINE-B"],
        listing.confirmation_digest,
        "auth-production-listing",
    )
    assert calls[1][0] == "v5"
    assert calls[1][1]["confirmed_by"] == ""
    assert calls[1][1]["confirmation_text"] == ""
    assert calls[1][1]["proposal"]["execution_profile"] == "production"
    listing_audit = repository.list_task_status_history("TASK-OFFLINE-B")
    assert listing_audit[-1].changed_by == "admin"
    assert listing_audit[-1].reason == "execution_submission_authorized"


def test_digest_cannot_be_swapped_between_principal_or_task_batch(
    execution_setup,
) -> None:
    service, _, _ = execution_setup
    admin = _admin()
    other = Principal("other", frozenset({Capability.SUBMIT_EXECUTION}))
    prepared = service.prepare_execution(admin, ["TASK-PRICE-A"], "auth-swap")

    with pytest.raises(ExecutionAuthorizationForbidden, match="不匹配"):
        service.submit_execution(
            other,
            ["TASK-PRICE-A"],
            prepared.confirmation_digest,
            "auth-swap",
        )


def test_unrelated_execution_facts_do_not_change_price_authorization(
    execution_setup,
) -> None:
    service, repository, calls = execution_setup
    admin = _admin()
    prepared = service.prepare_execution(admin, ["TASK-PRICE-A"], "auth-drift")
    with repository.connect_write() as connection:
        connection.execute(
            "DELETE FROM inventory_balances WHERE internal_sku = ?",
            ("AISHA-A-50-Z",),
        )
        connection.execute(
            """
            UPDATE inventory_authority_state
            SET authority_mode = 'PRE_CUTOVER',
                bootstrap_snapshot_sha256 = NULL,
                bootstrap_runtime_snapshot_sha256 = NULL,
                bootstrap_sales_watermark_date = NULL,
                bootstrap_idempotency_key = NULL,
                bootstrap_completed_at = NULL,
                bootstrap_completed_by = NULL
            """
        )
        connection.execute(
            "UPDATE listing_status SET price_observed_at = ?, "
            "price_source_attempt_id = ? WHERE internal_sku = ?",
            (
                (NOW + timedelta(seconds=1)).isoformat(),
                "ATTEMPT-REFRESHED-A",
                "AISHA-A-50-Z",
            ),
        )
        connection.commit()
    workbook = load_workbook(service.platform_mappings_workbook)
    sheet = workbook["data"]
    headers = [cell.value for cell in sheet[1]]
    sheet.cell(row=3, column=headers.index("remark") + 1).value = "unrelated B edit"
    workbook.save(service.platform_mappings_workbook)

    submitted = service.submit_execution(
        admin,
        ["TASK-PRICE-A"],
        prepared.confirmation_digest,
        "auth-drift",
    )
    assert submitted.batch_id == prepared.batch_id
    assert calls == []


def test_update_price_ignores_other_action_review_but_blocks_own_review(
    execution_setup,
) -> None:
    service, repository, _ = execution_setup
    with repository.connect_write() as connection:
        connection.execute(
            """
            INSERT INTO review_tasks(
              review_task_id, scope_type, scope_key, source_task_id,
              review_type, review_status, internal_sku, platform_name,
              created_at, updated_at
            ) VALUES (?, 'task', ?, ?, 'manual_review', 'pending', ?, ?, ?, ?)
            """,
            (
                "REVIEW-OTHER-ACTION",
                "TASK-OFFLINE-B",
                "TASK-OFFLINE-B",
                "AISHA-A-50-Z",
                PLATFORM,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    prepared = service.prepare_execution(
        _admin(), ["TASK-PRICE-A"], "review-isolation"
    )
    assert prepared.action_type is TaskActionType.UPDATE_PRICE

    with repository.connect_write() as connection:
        connection.execute(
            """
            INSERT INTO review_tasks(
              review_task_id, scope_type, scope_key, source_task_id,
              review_type, review_status, internal_sku, platform_name,
              created_at, updated_at
            ) VALUES (?, 'task', ?, ?, 'manual_review', 'pending', ?, ?, ?, ?)
            """,
            (
                "REVIEW-OWN-PRICE",
                "TASK-PRICE-A",
                "TASK-PRICE-A",
                "AISHA-A-50-Z",
                PLATFORM,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    with pytest.raises(ExecutionAuthorizationBlocked):
        service.prepare_execution(_admin(), ["TASK-PRICE-A"], "review-own")


def test_relevant_sku_mapping_change_still_invalidates_authorization(
    execution_setup,
) -> None:
    service, _, calls = execution_setup
    prepared = service.prepare_execution(
        _admin(), ["TASK-PRICE-A"], "relevant-mapping"
    )
    workbook = load_workbook(service.platform_mappings_workbook)
    sheet = workbook["data"]
    headers = [cell.value for cell in sheet[1]]
    sheet.cell(row=2, column=headers.index("mapping_id") + 1).value = "MAP-A-NEW"
    workbook.save(service.platform_mappings_workbook)

    with pytest.raises(ExecutionAuthorizationConflict):
        service.submit_execution(
            _admin(),
            ["TASK-PRICE-A"],
            prepared.confirmation_digest,
            "relevant-mapping",
        )
    assert calls == []


def test_set_offline_uses_existing_v5_propose_and_publish(execution_setup) -> None:
    service, _, calls = execution_setup
    admin = _admin()

    prepared = service.prepare_execution(admin, ["TASK-OFFLINE-B"], "auth-v5")
    submitted = service.submit_execution(
        admin,
        ["TASK-OFFLINE-B"],
        prepared.confirmation_digest,
        "auth-v5",
    )

    assert prepared.action_type is TaskActionType.SET_OFFLINE
    assert submitted.execution_attempt_id == "ATTEMPT-V5"
    assert calls[0][0] == "v5"
    assert calls[0][1]["confirmed_by"] == "admin"
    assert calls[0][1]["proposal"]["manifest"]["items"][0]["source_task_id"] == (
        "TASK-OFFLINE-B"
    )


def test_prepare_idempotency_replays_same_batch_and_rejects_other_tasks(
    execution_setup,
) -> None:
    service, _, _ = execution_setup
    admin = _admin()
    first = service.prepare_execution(admin, ["TASK-PRICE-A"], "same-auth")
    replay = service.prepare_execution(admin, ["TASK-PRICE-A"], "same-auth")
    assert replay == first

    with pytest.raises(ExecutionAuthorizationConflict, match="与之前的任务不同"):
        service.prepare_execution(admin, ["TASK-OFFLINE-B"], "same-auth")


def _sales_snapshot(service, repository, *, online):
    from app.services.shadowbot_listing_sync import prepare_listing_sync_batch, import_listing_sync_result
    from app.services.shadowbot_listing_action_contract import compute_listing_result_hash
    from tests.test_shadowbot_listing_sync import _result, _item

    manifest = prepare_listing_sync_batch(repository, batch_id='BATCH-EXPOSURE-SCAN',
        platform_name=PLATFORM, mapping_path=service.shadowbot_identity_mapping)
    request = build_listing_action_request(manifest, execution_profile='production',
        execution_attempt_id='ATTEMPT-EXPOSURE-SCAN', applet_uri=service.applet_uri)
    with repository.connect_write() as connection:
        connection.execute("""UPDATE shadowbot_listing_action_batches SET status = 'QUEUED',
            instruction_hash = ?, execution_attempt_id = ? WHERE batch_id = ?""",
            (request['instruction_hash'], request['execution_attempt_id'], request['batch_id']))
    now = datetime.now(UTC).isoformat()
    result = _result(request, scan_started_at=now)
    snapshot = result['snapshot']
    for key in snapshot:
        if key.endswith('_at'):
            snapshot[key] = now
    item = _item(snapshot_id=snapshot['snapshot_id'], suffix='0001', sku='AISHA-A-50-Z',
                 name='艾莎', grade='A级', location='online_only' if online else 'waiting_only')
    prefix = 'online' if online else 'waiting'
    item.update({prefix + '_observed_at': now, prefix + '_observed_inventory': 20})
    snapshot['items'] = [item]
    result['ended_at'] = now
    result['result_payload_sha256'] = compute_listing_result_hash(result)
    import_listing_sync_result(repository, request=request, result=result,
        result_file_sha256='a' * 64, source_result_path='synthetic-exposure-scan.result.json')


@pytest.mark.parametrize('inventory_state', ['present', 'missing', 'maintenance'])
def test_human_exposure_authorization_v5_readback_preserves_physical_ledger(tmp_path, monkeypatch, inventory_state):
    import hashlib
    import sys
    from app.services.manual_task_orchestration import ManualTaskApplicationService, ManualTaskRequest
    from app.services.shadowbot_executor import ShadowBotFileQueueRunner
    from app.services.shadowbot_listing_action_pipeline import propose_listing_action_batch, publish_listing_action_batch
    from app.services.shadowbot_listing_action_contract import compute_listing_result_hash
    from app.services.shadowbot_queue import ShadowBotResultImporter
    from shadowbot.test2 import shadowbot_queue_worker
    from tests.test_shadowbot_listing_action_pipeline import _write_result

    monkeypatch.setattr(sys.modules[__name__], 'NOW', datetime.now(UTC))
    service, repository, _ = execution_setup.__wrapped__(tmp_path)
    service.clock = lambda: datetime.now(UTC)
    service.runner_factory = ShadowBotFileQueueRunner
    service.v5_propose = propose_listing_action_batch
    service.v5_publish = publish_listing_action_batch
    with repository.connect_write() as connection:
        connection.execute("UPDATE tasks SET task_status = 'cancelled'")
        connection.execute('UPDATE inventory_balances SET current_qty = 20')
        if inventory_state == 'missing':
            connection.execute('DELETE FROM inventory_balances')
        elif inventory_state == 'maintenance':
            connection.execute("""UPDATE inventory_authority_state SET authority_mode = 'PRE_CUTOVER',
                bootstrap_snapshot_sha256 = NULL, bootstrap_runtime_snapshot_sha256 = NULL,
                bootstrap_sales_watermark_date = NULL, bootstrap_idempotency_key = NULL,
                bootstrap_completed_at = NULL, bootstrap_completed_by = NULL""")
    _sales_snapshot(service, repository, online=False)
    def physical_ledger():
        with repository.connect_read() as connection:
            return {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table)]
                    for table in ('inventory_balances', 'inventory_transactions', 'inventory_authority_state')}
    before = physical_ledger()
    manual = ManualTaskApplicationService(repository, products_workbook=service.products_workbook,
        platform_mappings_workbook=service.platform_mappings_workbook, clock=service.clock)
    request = ManualTaskRequest(varieties=('艾莎',), grades=('A级',), platforms=(PLATFORM,),
        action='SET_ONLINE', price_value=Decimal('22'), target_inventory=50, idempotency_key='exposure-50')
    preview = manual.preview(request)
    assert preview.creatable
    created = manual.create(request, expected_preview_digest=preview.preview_digest, authenticated_subject='admin')
    prepared = service.prepare_execution(_admin(), created.task_ids, 'authorize-exposure-50')
    receipt = service.submit_execution(_admin(), created.task_ids, prepared.confirmation_digest, 'authorize-exposure-50')
    # The published operation still excludes a second same-SKU platform write.
    competing = replace(repository.get_task(created.task_ids[0]), task_id='TASK-COMPETING-EXPOSURE',
        task_status=TaskStatus.PENDING, dedupe_key='', origin_ref_id='synthetic:competing-exposure')
    repository.insert_tasks([competing])
    with pytest.raises(ExecutionAuthorizationBlocked, match='正在执行其他平台操作'):
        service.prepare_execution(_admin(), [competing.task_id], 'competing-exposure')
    path = service.queue_root / 'inbox' / (receipt.execution_attempt_id + '.ready.json')
    raw = path.read_bytes()
    published = json.loads(raw.decode('utf-8'))
    shadowbot_queue_worker._v5_validate_request(published)
    assert published['items'][0]['target_inventory'] == 50
    result = _write_result(published, request_file_sha256=hashlib.sha256(raw).hexdigest())
    result['items'][0].update(observed_inventory_before_action=20,
        observed_inventory_after_detail_save=50, actual_inventory=50)
    result['result_payload_sha256'] = compute_listing_result_hash(result)
    result_path = service.queue_root / 'results' / (receipt.execution_attempt_id + '.result.json')
    result_bytes = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    result_path.write_bytes(result_bytes)
    result_path.with_suffix('.json.sha256').write_text(hashlib.sha256(result_bytes).hexdigest() + '\n', encoding='ascii')
    importer = ShadowBotResultImporter(repository, ShadowBotFileQueueRunner(service.queue_root), service.queue_root)
    event = importer.import_one(result_path)
    assert event['summary']['status'] == 'VERIFIED'
    assert repository.get_task(created.task_ids[0]).task_status is TaskStatus.SUCCESS
    assert repository.get_listing_status(PLATFORM, '艾莎', 'A级').platform_stock_qty == 50
    with repository.connect_read() as connection:
        operation = connection.execute('SELECT * FROM shadowbot_operations WHERE task_id = ?', created.task_ids).fetchone()
        assert operation['target_inventory'] == 50 and operation['status'] == 'VERIFIED'
        attempt = connection.execute('SELECT * FROM shadowbot_execution_attempts WHERE operation_id = ?',
                                     (operation['operation_id'],)).fetchone()
        evidence = json.loads(attempt['raw_output_json'])
        assert evidence['observed_inventory_before_action'] == 20
        assert evidence['observed_inventory_after_detail_save'] == evidence['actual_inventory'] == 50
        assert evidence['readback_observed_at']
        assert attempt['status'] == 'VERIFIED' and attempt['ended_at']
    assert physical_ledger() == before


def test_inventory_maintenance_and_missing_balance_allow_offline_authorization(execution_setup):
    from app.services.manual_task_orchestration import ManualTaskApplicationService, ManualTaskRequest
    service, repository, calls = execution_setup
    with repository.connect_write() as connection:
        connection.execute("UPDATE tasks SET task_status = 'cancelled'")
        connection.execute('DELETE FROM inventory_balances')
        connection.execute("""UPDATE inventory_authority_state SET authority_mode = 'PRE_CUTOVER',
            bootstrap_snapshot_sha256 = NULL, bootstrap_runtime_snapshot_sha256 = NULL,
            bootstrap_sales_watermark_date = NULL, bootstrap_idempotency_key = NULL,
            bootstrap_completed_at = NULL, bootstrap_completed_by = NULL""")
    manual = ManualTaskApplicationService(repository, products_workbook=service.products_workbook,
        platform_mappings_workbook=service.platform_mappings_workbook, clock=service.clock)
    request = ManualTaskRequest(varieties=('艾莎',), grades=('B级',), platforms=(PLATFORM,),
                                action='SET_OFFLINE', idempotency_key='offline-maintenance')
    preview = manual.preview(request)
    assert preview.creatable
    created = manual.create(request, expected_preview_digest=preview.preview_digest, authenticated_subject='admin')
    prepared = service.prepare_execution(_admin(), created.task_ids, 'offline-maintenance')
    service.submit_execution(_admin(), created.task_ids, prepared.confirmation_digest, 'offline-maintenance')
    assert calls[0][0] == 'v5'


@pytest.mark.parametrize('failure', ['predecessor', 'expired_task', 'expired_authorization'])
def test_offline_authorization_safety_remains_without_balance(execution_setup, failure):
    service, repository, _ = execution_setup
    task_id = 'TASK-OFFLINE-B'
    with repository.connect_write() as connection:
        connection.execute('DELETE FROM inventory_balances')
    if failure == 'expired_authorization':
        prepared = service.prepare_execution(_admin(), [task_id], 'expiry')
        service.clock = lambda: prepared.expires_at + timedelta(seconds=1)
        with pytest.raises(ExecutionAuthorizationConflict):
            service.submit_execution(_admin(), [task_id], prepared.confirmation_digest, 'expiry')
        return
    if failure == 'predecessor':
        old = replace(repository.get_task(task_id), task_id='TASK-PREDECESSOR', dedupe_key='')
        repository.insert_tasks([old])
        with repository.connect_write() as connection:
            connection.execute('UPDATE tasks SET decision_trace_json = ? WHERE task_id = ?',
                (json.dumps({'predecessor_task_ids': [old.task_id]}), task_id))
        error, message = ExecutionAuthorizationBlocked, '尚未收口'
    else:
        with repository.connect_write() as connection:
            connection.execute('UPDATE tasks SET expires_at = ? WHERE task_id = ?',
                               ((NOW - timedelta(seconds=1)).isoformat(), task_id))
        error, message = ExecutionAuthorizationConflict, '已过期'
    with pytest.raises(error, match=message):
        service.prepare_execution(_admin(), [task_id], 'blocked')


def _admin() -> Principal:
    return Principal(
        "admin",
        frozenset({Capability.MANAGE_BUSINESS, Capability.SUBMIT_EXECUTION}),
    )


def _task(
    task_id: str,
    sku: str,
    grade: str,
    action: TaskActionType,
    mapping_version: str,
    *,
    expected_old_price: Decimal | None = None,
    target_price: Decimal | None = None,
    target_status: str | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=sku,
        platform_name=PLATFORM,
        action_type=action,
        priority=5,
        task_status=TaskStatus.PENDING,
        created_at=NOW,
        expected_old_price=expected_old_price,
        target_price=target_price,
        target_status=target_status,
        pricing_source=(PricingSource.MANUAL_OVERRIDE if target_price else None),
        decision_trace={
            "mapping_version": mapping_version,
            "mapping_ids": [
                "MAP-A" if sku == "AISHA-A-50-Z" else "MAP-B"
            ],
            "grade": grade,
        },
        required_by=NOW + timedelta(hours=1),
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id="synthetic:" + task_id,
        expires_at=NOW + timedelta(hours=1),
        updated_at=NOW,
    )


def _mapping(mapping_id: str, sku: str, grade: str):
    return {
        "mapping_id": mapping_id,
        "mapping_kind": "PRODUCT",
        "internal_sku": sku,
        "platform_name": PLATFORM,
        "platform_product_name": "艾莎",
        "grade": grade,
        "mapping_status": "VERIFIED",
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
) -> None:
    repository.apply_shadowbot_inventory_observation(
        platform_name=PLATFORM,
        variety="艾莎",
        grade=grade,
        internal_sku=sku,
        observed_price=price,
        platform_stock_qty=20,
        online_status=status,
        observed_at=NOW,
        execution_attempt_id="ATTEMPT-" + sku,
    )
