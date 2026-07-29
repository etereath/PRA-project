from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from app.automation_models import (
    AutomationJob,
    AutomationRunClaim,
    AutomationRunOutcome,
)
from app.enums import AutomationRunStatus, ReviewTaskStatus
from app.exceptions import ValidationError
from app.models import ListingStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.operational_time import OperationalTimeService
from app.services.shadowbot_listing_action_contract import (
    build_listing_action_request,
    compute_listing_result_hash,
)
from app.services.shadowbot_listing_sync import (
    import_listing_sync_result,
    prepare_listing_sync_batch,
    render_listing_sync_markdown,
)
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_queue import ShadowBotResultImporter
from app.services.workflow import _load_latest_platform_observations
from shadowbot.test2 import shadowbot_queue_worker


PLATFORM = "蚂蚁花团供应商"


def _mapping_file(path: Path) -> Path:
    payload = {
        "schema_version": "shadowbot-product-identity-mapping-1.0",
        "platform_name": PLATFORM,
        "mappings": [
            {
                "internal_sku": sku,
                "expected_product_name": name,
                "expected_grade": grade,
                "status": "active",
            }
            for sku, name, grade in (
                ("SKU-ONLINE-001", "艾莎", "B级"),
                ("SKU-WAITING-001", "艾莎", "C级"),
                ("SKU-BOTH-00001", "卡布奇诺", "B级"),
                ("SKU-NEITHER-01", "卡布奇诺", "C级"),
                ("SKU-DUPLICATE1", "艾莎", "D级"),
            )
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    repository.init_schema()
    for index, (sku, name, grade, status) in enumerate(
        (
            ("SKU-ONLINE-001", "艾莎", "B级", "offline"),
            ("SKU-WAITING-001", "艾莎", "C级", "online"),
            ("SKU-BOTH-00001", "卡布奇诺", "B级", "offline"),
            ("SKU-NEITHER-01", "卡布奇诺", "C级", "online"),
            ("SKU-DUPLICATE1", "艾莎", "D级", "online"),
        ),
        start=1,
    ):
        repository.upsert_listing_status(
            ListingStatus(
                listing_status_id=f"LISTING-SYNC-{index:02d}",
                platform_name=PLATFORM,
                internal_sku=sku,
                variety=name,
                grade=grade,
                current_price=Decimal("20.00") + index,
                platform_stock_qty=77,
                online_status=status,
                updated_at=datetime.now(UTC),
            )
        )
    return repository


def _request(
    repository: SQLiteRuntimeRepository,
    tmp_path: Path,
    *,
    batch_id: str,
    attempt_id: str,
) -> dict:
    manifest = prepare_listing_sync_batch(
        repository,
        batch_id=batch_id,
        platform_name=PLATFORM,
        mapping_path=_mapping_file(tmp_path / f"{batch_id}.mapping.json"),
    )
    request = build_listing_action_request(
        manifest,
        execution_profile="production",
        execution_attempt_id=attempt_id,
        applet_uri="weixin://launchapplet/test",
        window_title=PLATFORM,
    )
    with repository.connect_write() as connection, connection:
        connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET instruction_hash = ?, execution_attempt_id = ?,
                status = 'QUEUED'
            WHERE batch_id = ?
            """,
            (request["instruction_hash"], attempt_id, batch_id),
        )
    return request


def _item(
    *,
    snapshot_id: str,
    suffix: str,
    sku: str | None,
    name: str,
    grade: str,
    location: str,
) -> dict:
    occurrences = {
        "online_only": (1, 0, False),
        "waiting_only": (0, 1, False),
        "both": (1, 1, False),
        "neither": (0, 0, False),
        "ambiguous": (2, 0, True),
    }
    online_count, waiting_count, ambiguous = occurrences[location]
    return {
        "snapshot_item_id": f"{snapshot_id}-ITEM-{suffix}",
        "internal_sku": sku,
        "product_name": name,
        "grade": grade,
        "page_identity_key": f"platform|name:{name}|grade:{grade}",
        "affected_internal_skus": [sku] if sku else [],
        "online_occurrences": online_count,
        "waiting_occurrences": waiting_count,
        "mapping_ambiguous": ambiguous,
        "listing_location": location,
        "online_row_identities": [
            f"online:parent-index:{1 + 16 * index}"
            for index in range(online_count)
        ],
        "waiting_row_identities": [
            f"waiting:parent-index:{1 + 16 * index}"
            for index in range(waiting_count)
        ],
        "online_observed_price": "21.00" if online_count else None,
        "waiting_observed_price": "21.00" if waiting_count else None,
        "online_observed_inventory": 9 if online_count else None,
        "waiting_observed_inventory": 8 if waiting_count else None,
        "diagnostic_code": {
            "both": "PRESENT_IN_BOTH_LISTS",
            "neither": "ABSENT_FROM_BOTH_LISTS",
            "ambiguous": "DUPLICATE_PAGE_IDENTITY",
        }.get(location, ""),
        "online_observed_at": (
            "2026-07-25T03:00:01+00:00" if online_count else None
        ),
        "waiting_observed_at": (
            "2026-07-25T03:00:02+00:00" if waiting_count else None
        ),
    }


def _result(
    request: dict,
    *,
    complete: bool = True,
    locations: dict[str, str] | None = None,
    scan_started_at: str = "2026-07-25T03:00:00+00:00",
) -> dict:
    snapshot_id = "SNAPSHOT-" + str(request["batch_id"])[6:]
    result_id = "RESULT-" + str(request["batch_id"])[6:]
    locations = locations or {
        "SKU-ONLINE-001": "online_only",
        "SKU-WAITING-001": "waiting_only",
        "SKU-BOTH-00001": "both",
        "SKU-NEITHER-01": "neither",
        "SKU-DUPLICATE1": "ambiguous",
    }
    identities = {
        "SKU-ONLINE-001": ("艾莎", "B级"),
        "SKU-WAITING-001": ("艾莎", "C级"),
        "SKU-BOTH-00001": ("卡布奇诺", "B级"),
        "SKU-NEITHER-01": ("卡布奇诺", "C级"),
        "SKU-DUPLICATE1": ("艾莎", "D级"),
    }
    items = (
        [
            _item(
                snapshot_id=snapshot_id,
                suffix=f"{index:04d}",
                sku=sku,
                name=identities[sku][0],
                grade=identities[sku][1],
                location=locations[sku],
            )
            for index, sku in enumerate(locations, start=1)
        ]
        if complete
        else []
    )
    snapshot = {
        "schema_version": "shadowbot-listing-sync-snapshot-1.0",
        "snapshot_id": snapshot_id,
        "platform_name": PLATFORM,
        "execution_attempt_id": request["execution_attempt_id"],
        "mapping_source_version": request["mapping_source_version"],
        "result_id": result_id,
        "scan_started_at": scan_started_at,
        "scan_completed_at": "2026-07-25T03:00:03+00:00",
        "online_scan_started_at": scan_started_at,
        "online_scan_completed_at": "2026-07-25T03:00:01+00:00",
        "waiting_scan_started_at": "2026-07-25T03:00:01+00:00",
        "waiting_scan_completed_at": "2026-07-25T03:00:03+00:00",
        "online_scan_complete": complete,
        "waiting_scan_complete": complete,
        "online_end_marker_verified": complete,
        "waiting_end_marker_verified": complete,
        "snapshot_complete": complete,
        "instruction_hash": request["instruction_hash"],
        "status": "VERIFIED" if complete else "FAILED",
        "error_code": "" if complete else "END_MARKER_NOT_VERIFIED",
        "evidence_manifest_sha256": "sha256:" + "a" * 64,
        "items": items,
    }
    result = {
        "schema_version": "shadowbot-listing-action-batch-result-1.0",
        "contract_version": 5,
        "action_type": "sync_status",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": "READ_ONLY",
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": "sha256:" + "b" * 64,
        "result_id": result_id,
        "started_at": scan_started_at,
        "ended_at": "2026-07-25T03:00:03+00:00",
        "snapshot": snapshot,
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    return result


def _bind_automation_listing_run(
    repository: SQLiteRuntimeRepository,
    request: dict,
) -> tuple[AutomationRepository, AutomationRunClaim]:
    automation = AutomationRepository(repository)
    now = datetime.now(UTC)
    scheduled_for = datetime(2026, 7, 25, 3, 0, tzinfo=UTC)
    suffix = str(request["batch_id"])[-8:]
    parent_job = AutomationJob(
        job_id=f"FULL-{suffix}",
        job_type="FULL_MARKET_SCAN",
        display_name="完整扫描测试",
        enabled=True,
        schedule_kind="INTERVAL_MINUTES",
        schedule_expression="60",
        priority=50,
        config={
            "platform_name": PLATFORM,
            "catchup_policy": "LATEST_ONLY",
        },
    )
    child_job = AutomationJob(
        job_id=f"LISTING-{suffix}",
        job_type="LISTING_STATUS_SCAN",
        display_name="商品状态扫描测试",
        enabled=False,
        schedule_kind="CHILD_ONLY",
        schedule_expression="-",
        priority=50,
        config={
            "platform_name": PLATFORM,
            "catchup_policy": "LATEST_ONLY",
        },
    )
    automation.upsert_job(parent_job, now=now)
    automation.upsert_job(child_job, now=now)
    parent_run = automation.ensure_run(
        job=parent_job,
        scheduled_for=scheduled_for,
        time_context=OperationalTimeService().classify(scheduled_for),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=now,
    )[0]
    parent_claim = automation.claim_run(
        run_id=parent_run.run_id,
        owner_token=f"parent-{suffix}",
        now=now,
        lease_seconds=3600,
    )
    assert parent_claim is not None
    child_run, _ = automation.ensure_child_run_fenced(
        parent_claim,
        child_job,
        relation_type="LISTING_STATUS_CHILD",
        now=now,
    )
    assert automation.finish_run(
        parent_claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now,
    )
    child_claim = automation.claim_run(
        run_id=child_run.run_id,
        owner_token=f"listing-{suffix}",
        now=now,
        lease_seconds=3600,
    )
    assert child_claim is not None
    automation.bind_run_input_manifest(
        child_claim,
        manifest_sha256=str(request["manifest_sha256"]),
        now=now,
    )
    return automation, child_claim


def test_complete_sync_projects_status_price_and_inventory(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0001",
        attempt_id="ATTEMPT-SYNC-0001",
    )
    result = _result(request)

    summary = import_listing_sync_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="c" * 64,
        source_result_path="result.json",
    )

    assert summary["status"] == "VERIFIED"
    assert summary["projected_count"] == 4
    assert summary["anomaly_count"] == 3
    assert summary["review_created_count"] == 3
    assert summary["notification_created_count"] == 3
    statuses = {
        status.internal_sku: status
        for status in repository.list_listing_statuses(platform_name=PLATFORM)
    }
    assert statuses["SKU-ONLINE-001"].online_status == "online"
    assert statuses["SKU-WAITING-001"].online_status == "offline"
    assert statuses["SKU-BOTH-00001"].online_status == "online"
    assert statuses["SKU-NEITHER-01"].online_status == "offline"
    assert statuses["SKU-DUPLICATE1"].online_status == "online"
    assert statuses["SKU-ONLINE-001"].current_price == Decimal("21.00")
    assert statuses["SKU-ONLINE-001"].platform_stock_qty == 9
    assert statuses["SKU-WAITING-001"].current_price == Decimal("21.00")
    assert statuses["SKU-WAITING-001"].platform_stock_qty == 8
    assert statuses["SKU-BOTH-00001"].platform_stock_qty == 9
    assert statuses["SKU-NEITHER-01"].platform_stock_qty == 77
    assert statuses["SKU-ONLINE-001"].price_source == "shadowbot_sync_status"
    assert (
        statuses["SKU-ONLINE-001"].price_source_attempt_id
        == request["execution_attempt_id"]
    )
    observations = _load_latest_platform_observations(
        repository.db_path,
        PLATFORM,
    )
    assert observations is not None
    assert observations[(PLATFORM, "艾莎", "B")] == (
        Decimal("21.00"),
        9,
    )
    assert observations[(PLATFORM, "艾莎", "C")] == (
        Decimal("21.00"),
        8,
    )
    assert len(repository.list_review_tasks()) == 3
    assert len(repository.list_notification_outbox()) == 3
    report = render_listing_sync_markdown(
        request=request,
        result=result,
        summary=summary,
    )
    assert "结果：成功" in report
    assert "最新页面观察值已投影" in report
    assert "SKU-ONLINE-001" in report


def test_automation_sync_requires_bound_live_claim_and_allows_noop_replay(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-AUTO-0001",
        attempt_id="ATTEMPT-AUTO-0001",
    )
    result = _result(request)
    _, claim = _bind_automation_listing_run(repository, request)

    with pytest.raises(
        ValidationError,
        match="必须携带当前 Run claim",
    ):
        import_listing_sync_result(
            repository,
            request=request,
            result=result,
            result_file_sha256="d" * 64,
            source_result_path="result.json",
        )

    summary = import_listing_sync_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="d" * 64,
        source_result_path="result.json",
        automation_claim=claim,
    )
    replay = import_listing_sync_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="d" * 64,
        source_result_path="result.json",
    )

    assert summary["status"] == "VERIFIED"
    assert replay["already_imported"] is True


def test_reclaimed_automation_owner_cannot_project_authoritative_status(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-AUTO-0002",
        attempt_id="ATTEMPT-AUTO-0002",
    )
    result = _result(request)
    _, old_claim = _bind_automation_listing_run(repository, request)
    baseline_statuses = {
        item.internal_sku: item.online_status
        for item in repository.list_listing_statuses(platform_name=PLATFORM)
    }
    with repository.connect_write() as connection, connection:
        connection.execute(
            """
            UPDATE automation_runs
            SET lease_owner = 'replacement-owner',
                lease_version = lease_version + 1,
                lease_expires_at = ?
            WHERE run_id = ?
            """,
            (
                (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                old_claim.run.run_id,
            ),
        )

    with pytest.raises(
        ValidationError,
        match="已过期或被其他实例回收",
    ):
        import_listing_sync_result(
            repository,
            request=request,
            result=result,
            result_file_sha256="e" * 64,
            source_result_path="late-result.json",
            automation_claim=old_claim,
        )

    assert {
        item.internal_sku: item.online_status
        for item in repository.list_listing_statuses(platform_name=PLATFORM)
    } == baseline_statuses
    with repository.connect_read() as connection:
        for table_name in (
            "shadowbot_listing_result_receipts",
            "listing_sync_snapshots",
            "review_tasks",
            "notification_outbox",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0] == 0


def test_failed_snapshot_is_recorded_without_projection(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0002",
        attempt_id="ATTEMPT-SYNC-0002",
    )
    before = {
        row.internal_sku: row.online_status
        for row in repository.list_listing_statuses(platform_name=PLATFORM)
    }
    result = _result(request, complete=False)

    summary = import_listing_sync_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="d" * 64,
        source_result_path="failed.result.json",
    )

    assert summary["status"] == "FAILED"
    assert summary["projected_count"] == 0
    assert repository.list_review_tasks() == []
    assert repository.list_notification_outbox() == []
    after = {
        row.internal_sku: row.online_status
        for row in repository.list_listing_statuses(platform_name=PLATFORM)
    }
    assert after == before
    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM listing_sync_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM listing_sync_snapshot_items"
        ).fetchone()[0] == 0


def test_sync_creates_missing_mapped_listing_with_latest_observation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with repository.connect_write() as connection, connection:
        connection.execute(
            "DELETE FROM listing_status WHERE internal_sku = 'SKU-WAITING-001'"
        )
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-NEW-ROW",
        attempt_id="ATTEMPT-SYNC-NEW-ROW",
    )
    result = _result(
        request,
        locations={"SKU-WAITING-001": "waiting_only"},
    )

    summary = import_listing_sync_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="f" * 64,
        source_result_path="new-row.result.json",
    )

    assert summary["projected_count"] == 1
    assert summary["items"][0]["projection_action"] == "CREATED"
    statuses = {
        status.internal_sku: status
        for status in repository.list_listing_statuses(platform_name=PLATFORM)
    }
    created = statuses["SKU-WAITING-001"]
    assert created.online_status == "offline"
    assert created.current_price == Decimal("21.00")
    assert created.platform_stock_qty == 8
    assert created.inventory_source == "shadowbot_sync_status"
    assert created.price_source == "shadowbot_sync_status"
    assert created.source == "shadowbot_sync_status"


def test_sync_import_rolls_back_every_projection_on_failure(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0003",
        attempt_id="ATTEMPT-SYNC-0003",
    )
    result = _result(request)
    before = {
        row.internal_sku: row.online_status
        for row in repository.list_listing_statuses(platform_name=PLATFORM)
    }

    with pytest.raises(RuntimeError, match="injected"):
        import_listing_sync_result(
            repository,
            request=request,
            result=result,
            result_file_sha256="e" * 64,
            source_result_path="result.json",
            failure_injector=lambda point: (
                (_ for _ in ()).throw(RuntimeError("injected"))
                if point == "after_status_projection"
                else None
            ),
        )

    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_listing_result_receipts"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM listing_sync_snapshots"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM listing_anomaly_cases"
        ).fetchone()[0] == 0
    after = {
        row.internal_sku: row.online_status
        for row in repository.list_listing_statuses(platform_name=PLATFORM)
    }
    assert after == before


def test_new_complete_snapshot_auto_clears_listing_anomaly_review(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    first_request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0004",
        attempt_id="ATTEMPT-SYNC-0004",
    )
    first_result = _result(first_request)
    first_summary = import_listing_sync_result(
        repository,
        request=first_request,
        result=first_result,
        result_file_sha256="1" * 64,
        source_result_path="first.result.json",
    )
    assert first_summary["anomaly_count"] == 3

    second_request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0005",
        attempt_id="ATTEMPT-SYNC-0005",
    )
    second_result = _result(
        second_request,
        locations={
            "SKU-ONLINE-001": "online_only",
            "SKU-WAITING-001": "waiting_only",
            "SKU-BOTH-00001": "online_only",
            "SKU-NEITHER-01": "waiting_only",
            "SKU-DUPLICATE1": "online_only",
        },
    )
    second_summary = import_listing_sync_result(
        repository,
        request=second_request,
        result=second_result,
        result_file_sha256="2" * 64,
        source_result_path="second.result.json",
    )

    assert second_summary["anomaly_count"] == 0
    assert second_summary["review_cleared_count"] == 3
    assert second_summary["notification_cancelled_count"] == 3
    reviews = repository.list_review_tasks()
    assert {review.review_status for review in reviews} == {
        ReviewTaskStatus.CANCELLED
    }
    assert {
        row.status for row in repository.list_notification_outbox()
    } == {"CANCELLED"}


def test_snapshot_older_than_listing_change_is_rejected_atomically(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with repository.connect_write() as connection, connection:
        connection.execute(
            """
            UPDATE listing_status
            SET last_listing_change_at = ?
            WHERE internal_sku = 'SKU-ONLINE-001'
            """,
            ((datetime.now(UTC) + timedelta(hours=1)).isoformat(),),
        )
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0006",
        attempt_id="ATTEMPT-SYNC-0006",
    )
    result = _result(request)

    with pytest.raises(ValidationError, match="LISTING_SYNC_SNAPSHOT_STALE"):
        import_listing_sync_result(
            repository,
            request=request,
            result=result,
            result_file_sha256="3" * 64,
            source_result_path="stale.result.json",
        )

    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM listing_sync_snapshots"
        ).fetchone()[0] == 0


def test_file_queue_import_writes_human_report_and_ack(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0007",
        attempt_id="ATTEMPT-SYNC-0007",
    )
    queue_dir = tmp_path / "queue"
    runner = ShadowBotFileQueueRunner(queue_dir)
    runner.start(request)
    attempt_id = request["execution_attempt_id"]
    inbox_request = queue_dir / "inbox" / f"{attempt_id}.ready.json"
    inbox_checksum = inbox_request.with_suffix(
        inbox_request.suffix + ".sha256"
    )
    working_request = queue_dir / "working" / f"{attempt_id}.request.json"
    working_checksum = working_request.with_suffix(
        working_request.suffix + ".sha256"
    )
    inbox_request.replace(working_request)
    inbox_checksum.replace(working_checksum)
    request_file_sha256 = hashlib.sha256(
        working_request.read_bytes()
    ).hexdigest()
    result = _result(request)
    result["request_file_sha256"] = "sha256:" + request_file_sha256
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    result_bytes = (
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=str,
        )
        + "\n"
    ).encode("utf-8")
    result_path = queue_dir / "results" / f"{attempt_id}.result.json"
    result_path.write_bytes(result_bytes)
    result_path.with_suffix(result_path.suffix + ".sha256").write_text(
        hashlib.sha256(result_bytes).hexdigest() + "\n",
        encoding="ascii",
    )

    events = ShadowBotResultImporter(
        repository,
        runner,
        queue_dir,
    ).import_available()

    assert len(events) == 1
    assert events[0]["status"] == "IMPORTED"
    report_path = Path(events[0]["report_path"])
    ack_path = Path(events[0]["ack_path"])
    assert report_path.read_text(encoding="utf-8").startswith(
        "# 平台商品状态同步报告"
    )
    ack = json.loads(ack_path.read_text(encoding="utf-8"))
    assert ack["status"] == "VERIFIED"
    with repository.connect_read() as connection:
        receipt = connection.execute(
            """
            SELECT ack_state FROM shadowbot_listing_result_receipts
            WHERE result_id = ?
            """,
            (result["result_id"],),
        ).fetchone()
    assert receipt["ack_state"] == "WRITTEN"


def test_worker_v5_request_and_failed_result_match_core_contract(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-0008",
        attempt_id="ATTEMPT-SYNC-0008",
    )

    shadowbot_queue_worker._v5_validate_sync_request(request)
    failed = shadowbot_queue_worker._v5_failed_sync_result(
        request,
        "a" * 64,
        worker_id="WORKER-T13-001",
        error_code="END_MARKER_NOT_VERIFIED",
        error_message="待上架页面未确认结束标记",
    )

    from app.services.shadowbot_listing_action_contract import (
        validate_listing_action_result,
    )

    validate_listing_action_result(
        failed,
        request=request,
        request_file_sha256="sha256:" + "a" * 64,
    )
    assert failed["snapshot"]["snapshot_complete"] is False
    assert failed["snapshot"]["items"] == []
