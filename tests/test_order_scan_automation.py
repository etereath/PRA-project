from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.adapters.mayi_huatuan_order import (
    MAYI_HUATUAN_PLATFORM,
    MayiHuatuanOrderReadOnlyAdapter,
    page_capture_from_json,
)
from app.automation_models import AutomationJob, AutomationRunOutcome
from app.enums import AutomationRunStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    AutomationExecutionContext,
    CHILD_ONLY,
    FULL_MARKET_SCAN,
    INTERVAL_MINUTES,
    ORDER_SCAN,
)
from app.services.operational_time import OperationalTimeService
from app.services.order_automation_runtime import (
    build_order_read_only_handlers,
)
from app.services.order_observation import OrderObservationImporter
from app.services.order_scan_automation import (
    FullMarketScanOrderDispatchHandler,
    FullMarketScanOrderCoordinator,
    OrderScanHandler,
)
from app.services.product_mapping import compile_product_mapping_rows


NOW = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "order_observation"
    / "mayi_huatuan_complete.json"
)


class Reader:
    def __init__(self) -> None:
        self.capture = page_capture_from_json(
            json.loads(FIXTURE.read_text(encoding="utf-8"))
        )
        self.platform_write_count = 0

    def read_orders_read_only(self, request):
        return self.capture


def _job(job_id: str, job_type: str, kind: str, enabled: bool):
    return AutomationJob(
        job_id=job_id,
        job_type=job_type,
        display_name=job_type,
        enabled=enabled,
        schedule_kind=kind,
        schedule_expression="60" if kind == INTERVAL_MINUTES else "-",
        priority=50,
        config={
            "platform_name": MAYI_HUATUAN_PLATFORM,
            "catchup_policy": "LATEST_ONLY",
        },
    )


def test_full_market_scan_creates_order_child_and_imports_observations(
    tmp_path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    repository = AutomationRepository(runtime)
    parent_job = repository.upsert_job(
        _job("FULL", FULL_MARKET_SCAN, INTERVAL_MINUTES, True),
        now=NOW,
    )
    repository.upsert_job(
        _job(
            "AUTOMATION-ORDER-SCAN-CHILD",
            ORDER_SCAN,
            CHILD_ONLY,
            False,
        ),
        now=NOW,
    )
    parent = repository.ensure_run(
        job=parent_job,
        scheduled_for=NOW,
        time_context=OperationalTimeService().classify(NOW),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=NOW,
    )[0]
    parent_claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="full-owner",
        now=NOW,
        lease_seconds=600,
    )
    assert parent_claim is not None
    parent_context = AutomationExecutionContext(
        claim=parent_claim,
        repository=repository,
        operational_time=OperationalTimeService(),
        clock=lambda: NOW,
        lease_seconds=600,
    )
    coordinator = FullMarketScanOrderCoordinator(
        parent_handler=lambda run, context: AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS
        )
    )
    parent_outcome = coordinator(parent, parent_context)
    child_id = str(parent_outcome.event_payload["order_scan_child_run_id"])
    assert repository.finish_run(
        parent_context.claim,
        parent_outcome,
        now=NOW + timedelta(seconds=1),
    )

    child_claim = repository.claim_run(
        run_id=child_id,
        owner_token="order-owner",
        now=NOW + timedelta(seconds=2),
        lease_seconds=600,
    )
    assert child_claim is not None
    child_context = AutomationExecutionContext(
        claim=child_claim,
        repository=repository,
        operational_time=OperationalTimeService(),
        clock=lambda: NOW + timedelta(seconds=2),
        lease_seconds=600,
    )
    reader = Reader()
    mappings = compile_product_mapping_rows(
        [
            {
                "mapping_id": "MAP-SYN-A",
                "mapping_kind": "PRODUCT",
                "platform_name": MAYI_HUATUAN_PLATFORM,
                "platform_product_name": "合成玫瑰甲",
                "grade": "A级",
                "internal_sku": "SYN-A",
                "candidate_internal_sku": "",
                "mapping_status": "VERIFIED",
            },
            {
                "mapping_id": "MAP-SYN-B",
                "mapping_kind": "PRODUCT",
                "platform_name": MAYI_HUATUAN_PLATFORM,
                "platform_product_name": "合成玫瑰乙",
                "grade": "B级",
                "internal_sku": "SYN-B",
                "candidate_internal_sku": "",
                "mapping_status": "VERIFIED",
            },
        ],
        source_workbook_sha256="b" * 64,
    )
    handler = OrderScanHandler(
        adapter=MayiHuatuanOrderReadOnlyAdapter(
            reader,
            operational_time=OperationalTimeService(),
        ),
        importer=OrderObservationImporter(
            runtime,
            operational_time=OperationalTimeService(),
            clock=lambda: NOW + timedelta(seconds=3),
        ),
        mappings_provider=lambda: mappings,
        batch_id_factory=lambda run: f"ORDER-BATCH-{run.run_id}",
    )
    child_outcome = handler(child_claim.run, child_context)

    assert child_outcome.status is AutomationRunStatus.SUCCESS
    assert (
        child_outcome.event_payload["relation_type"]
        == "ORDER_HISTORY_IMPORT"
    )
    assert reader.platform_write_count == 0
    assert repository.finish_run(
        child_context.claim,
        child_outcome,
        now=NOW + timedelta(seconds=4),
    )
    with closing(runtime.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_batches"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_items"
        ).fetchone()[0] == 3
    links = repository.list_links(parent_run_id=parent.run_id)
    assert any(
        link.child_run_id == child_id
        and link.relation_type == "ORDER_SCAN_CHILD"
        for link in links
    )
    target_events = [
        event
        for event in repository.list_events(child_id)
        if event.event_type == "ORDER_SCAN_TARGET_SELECTED"
    ]
    assert len(target_events) == 1
    assert target_events[0].payload == {
        "requested_platform_trade_date": "2026-07-31"
    }


def test_formal_full_market_dispatch_schedules_order_child_without_scan_fact(
    tmp_path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    repository = AutomationRepository(runtime)
    parent_job = repository.upsert_job(
        _job("FULL", FULL_MARKET_SCAN, INTERVAL_MINUTES, True),
        now=NOW,
    )
    repository.upsert_job(
        _job(
            "AUTOMATION-ORDER-SCAN-CHILD",
            ORDER_SCAN,
            CHILD_ONLY,
            False,
        ),
        now=NOW,
    )
    parent = repository.ensure_run(
        job=parent_job,
        scheduled_for=NOW,
        time_context=OperationalTimeService().classify(NOW),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=NOW,
    )[0]
    claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="dispatch-owner",
        now=NOW,
        lease_seconds=600,
    )
    assert claim is not None
    context = AutomationExecutionContext(
        claim=claim,
        repository=repository,
        operational_time=OperationalTimeService(),
        clock=lambda: NOW,
        lease_seconds=600,
    )

    outcome = FullMarketScanOrderDispatchHandler()(claim.run, context)

    assert outcome.status is AutomationRunStatus.SUCCESS
    assert outcome.event_payload["coordination_only"] is True
    assert "output_manifest_sha256" not in outcome.event_payload
    child = repository.get_run(
        str(outcome.event_payload["order_scan_child_run_id"])
    )
    assert child is not None
    assert child.job_type == ORDER_SCAN


def test_formal_runtime_composition_registers_only_read_only_order_chain(
    tmp_path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()

    handlers = build_order_read_only_handlers(
        runtime_repository=runtime,
        queue_dir=tmp_path / "queue",
        mapping_workbook=tmp_path / "platform_mappings.xlsx",
    )

    assert set(handlers) == {FULL_MARKET_SCAN, ORDER_SCAN}
    assert isinstance(
        handlers[FULL_MARKET_SCAN],
        FullMarketScanOrderDispatchHandler,
    )
    assert isinstance(handlers[ORDER_SCAN], OrderScanHandler)
