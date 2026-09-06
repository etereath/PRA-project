from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.enums import TaskActionType
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


def test_offline_has_no_price_and_online_requires_price_and_safe_inventory(
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
    online_request = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("B级",),
        platforms=(PLATFORM,),
        action=SET_ONLINE,
        price_value=Decimal("10"),
        target_inventory=42,
    )
    blocked = service.preview(online_request)
    assert blocked.creatable is False
    assert "不能超过数据库库存" in "".join(blocked.items[0].blockers)

    allowed_request = ManualTaskRequest(
        varieties=("艾莎",),
        grades=("B级",),
        platforms=(PLATFORM,),
        action=SET_ONLINE,
        price_value=Decimal("10"),
        target_inventory=40,
        idempotency_key="online",
    )
    allowed = service.preview(allowed_request)
    created = service.create(
        allowed_request,
        expected_preview_digest=allowed.preview_digest,
        authenticated_subject="admin",
    )
    task = repository.get_task(created.task_ids[0])
    assert task is not None
    assert task.action_type is TaskActionType.SET_ONLINE
    assert task.target_price == Decimal("10.00")
    assert task.target_inventory == 40


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
