from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

from app.adapters.mayi_huatuan_order import MayiHuatuanOrderReadOnlyAdapter
from app.automation_models import AutomationRun
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.services.automation import (
    AutomationHandler,
    FULL_MARKET_SCAN,
    ORDER_SCAN,
)
from app.services.operational_time import OperationalTimeService
from app.services.order_observation import OrderObservationImporter
from app.services.order_scan_automation import (
    FullMarketScanOrderDispatchHandler,
    OrderScanHandler,
)
from app.services.product_mapping import compile_product_mapping_workbook
from app.services.shadowbot_order_read import (
    ShadowBotFileQueueOrderTransport,
    ShadowBotOrderPageReader,
)
from app.services.trade_day_settlement import TradeDaySettlementService


def build_order_read_only_handlers(
    *,
    runtime_repository: SQLiteRuntimeRepository,
    queue_dir: Path,
    mapping_workbook: Path,
    operational_time: OperationalTimeService | None = None,
    timeout_seconds: float = 330.0,
    target_trade_date: Callable[[AutomationRun], date] | None = None,
) -> Mapping[str, AutomationHandler]:
    """Compose the production read-only order chain with zero write handlers."""

    time_service = operational_time or OperationalTimeService()
    transport = ShadowBotFileQueueOrderTransport(
        Path(queue_dir),
        timeout_seconds=timeout_seconds,
    )
    reader = ShadowBotOrderPageReader(transport)
    settlement_service = TradeDaySettlementService(
        OperationalSummaryRepository(runtime_repository)
    )
    order_handler = OrderScanHandler(
        adapter=MayiHuatuanOrderReadOnlyAdapter(
            reader,
            operational_time=time_service,
        ),
        importer=OrderObservationImporter(
            runtime_repository,
            operational_time=time_service,
        ),
        mappings_provider=lambda: compile_product_mapping_workbook(
            Path(mapping_workbook)
        ),
        batch_id_factory=lambda run: f"ORDER-BATCH-{run.run_id}",
        target_trade_date=(
            target_trade_date
            if target_trade_date is not None
            else lambda run: run.platform_trade_date
        ),
        post_import_refresh=settlement_service.refresh_after_order_import,
    )
    return {
        FULL_MARKET_SCAN: FullMarketScanOrderDispatchHandler(),
        ORDER_SCAN: order_handler,
    }
