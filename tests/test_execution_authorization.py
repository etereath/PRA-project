from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

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
    assert submitted.execution_attempt_id == "ATTEMPT-V4"
    assert calls[0][0] == "v4"
    assert calls[0][1]["confirmed_by"] == "admin"
    assert calls[0][1]["manifest"]["items"][0]["source_task_id"] == "TASK-PRICE-A"
    with pytest.raises(ExecutionAuthorizationConflict, match="不能重复"):
        service.submit_execution(
            admin,
            ["TASK-PRICE-A"],
            prepared.confirmation_digest,
            "auth-v4",
        )


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


def test_inventory_change_after_prepare_invalidates_whole_batch(
    execution_setup,
) -> None:
    service, repository, calls = execution_setup
    admin = _admin()
    prepared = service.prepare_execution(admin, ["TASK-PRICE-A"], "auth-drift")
    with repository.connect_write() as connection:
        connection.execute(
            "UPDATE inventory_balances SET current_qty = 71, version = 2 WHERE internal_sku = ?",
            ("AISHA-A-50-Z",),
        )
        connection.commit()

    with pytest.raises(ExecutionAuthorizationConflict, match="发生变化"):
        service.submit_execution(
            admin,
            ["TASK-PRICE-A"],
            prepared.confirmation_digest,
            "auth-drift",
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
        decision_trace={"mapping_version": mapping_version, "grade": grade},
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
