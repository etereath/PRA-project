from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.mayi_huatuan_order import (
    MAYI_HUATUAN_PLATFORM,
    MayiHuatuanOrderPageCapture,
    MayiHuatuanOrderReadOnlyAdapter,
    OrderReadOnlyRequest,
    page_capture_from_json,
)
from app.services.operational_time import OperationalTimeService
from app.services.order_observation import OrderObservationError


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "order_observation"
    / "mayi_huatuan_complete.json"
)


class RecordingReadOnlyReader:
    def __init__(self, capture: MayiHuatuanOrderPageCapture) -> None:
        self.capture = capture
        self.requests: list[OrderReadOnlyRequest] = []
        self.platform_write_count = 0

    def read_orders_read_only(
        self,
        request: OrderReadOnlyRequest,
    ) -> MayiHuatuanOrderPageCapture:
        self.requests.append(request)
        return self.capture


def _capture() -> MayiHuatuanOrderPageCapture:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return page_capture_from_json(payload)


def _adapter(capture: MayiHuatuanOrderPageCapture | None = None):
    reader = RecordingReadOnlyReader(capture or _capture())
    return (
        MayiHuatuanOrderReadOnlyAdapter(
            reader,
            operational_time=OperationalTimeService(),
        ),
        reader,
    )


def _scan(
    adapter: MayiHuatuanOrderReadOnlyAdapter,
    *,
    target: date = date(2026, 7, 31),
):
    return adapter.scan(
        observation_batch_id="ORDER-BATCH-ADAPTER",
        automation_run_id="ORDER-RUN-ADAPTER",
        platform_name=MAYI_HUATUAN_PLATFORM,
        requested_platform_trade_date=target,
    )


def test_current_trade_day_is_open_complete_and_read_only() -> None:
    adapter, reader = _adapter()

    batch = _scan(adapter)

    assert batch.trade_day_status == "OPEN"
    assert batch.capability_result == "SUCCEEDED"
    assert batch.batch_status == "ACCEPTED"
    assert batch.scope_complete is True
    assert batch.end_marker_verified is True
    assert batch.end_marker_kind == "NO_MORE"
    assert len(batch.items) == 3
    assert batch.items[0].order_transaction_amount == Decimal("12.34")
    assert reader.requests == [
        OrderReadOnlyRequest(
            execution_mode="READ_ONLY",
            automation_run_id="ORDER-RUN-ADAPTER",
            observation_batch_id="ORDER-BATCH-ADAPTER",
            platform_name=MAYI_HUATUAN_PLATFORM,
            requested_platform_trade_date=date(2026, 7, 31),
        )
    ]
    assert reader.platform_write_count == 0


def test_historical_trade_day_is_closed() -> None:
    historical = replace(
        _capture(),
        selected_platform_trade_date=date(2026, 7, 30),
        rows=tuple(
            replace(
                row,
                order_created_at="2026-07-30 17:01:02",
            )
            for row in _capture().rows
        ),
    )
    adapter, _ = _adapter(historical)

    batch = _scan(adapter, target=date(2026, 7, 30))

    assert batch.trade_day_status == "CLOSED"
    assert batch.batch_status == "ACCEPTED"


def test_trusted_empty_page_is_success_not_unavailable() -> None:
    empty = replace(
        _capture(),
        rows=(),
        page_count=1,
        scroll_completed=True,
        no_more_marker_visible=False,
        trusted_empty_marker_visible=True,
    )
    adapter, _ = _adapter(empty)

    batch = _scan(adapter)

    assert batch.capability_result == "SUCCEEDED"
    assert batch.batch_status == "ACCEPTED"
    assert batch.end_marker_kind == "TRUSTED_EMPTY"
    assert batch.items == ()


@pytest.mark.parametrize(
    ("capture", "error_code"),
    [
        (
            replace(
                _capture(),
                scroll_completed=False,
                no_more_marker_visible=False,
            ),
            "ORDER_SCROLL_INCOMPLETE",
        ),
        (
            replace(
                _capture(),
                selected_platform_trade_date=date(2026, 7, 30),
            ),
            "ORDER_DATE_MISMATCH",
        ),
    ],
)
def test_incomplete_or_wrong_date_is_never_accepted(
    capture: MayiHuatuanOrderPageCapture,
    error_code: str,
) -> None:
    adapter, _ = _adapter(capture)

    batch = _scan(adapter)

    assert batch.batch_status in {"PARTIAL", "FAILED"}
    assert batch.scope_complete is False
    assert batch.end_marker_verified is False
    assert batch.error_code == error_code


def test_unavailable_is_distinct_from_empty_and_failed() -> None:
    unavailable = replace(
        _capture(),
        rows=(),
        unavailable_code="ORDER_DATE_UNAVAILABLE",
        failure_message="requested date is outside the accessible range",
    )
    adapter, _ = _adapter(unavailable)

    batch = _scan(adapter)

    assert batch.capability_result == "UNAVAILABLE"
    assert batch.batch_status == "UNAVAILABLE"
    assert batch.items == ()


def test_page_failure_precedes_date_mismatch_classification() -> None:
    failed = replace(
        _capture(),
        selected_platform_trade_date=None,
        loading_completed=False,
        rows=(),
        failure_code="ORDER_DATE_VALUE_NOT_FOUND",
        failure_message="synthetic date selection failure",
    )
    adapter, _ = _adapter(failed)

    batch = _scan(adapter)

    assert batch.capability_result == "FAILED"
    assert batch.batch_status == "FAILED"
    assert batch.error_code == "ORDER_DATE_VALUE_NOT_FOUND"


def test_fixture_loader_rejects_order_ids_and_pii_fields() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["rows"][0]["platform_order_id"] = "forbidden"

    with pytest.raises(OrderObservationError, match="forbidden"):
        page_capture_from_json(payload)


def test_order_created_at_must_match_selected_trade_day() -> None:
    bad_row = replace(
        _capture().rows[0],
        order_created_at="2026-07-30 17:01:02",
    )
    capture = replace(_capture(), rows=(bad_row,))
    adapter, _ = _adapter(capture)

    batch = _scan(adapter)

    assert batch.batch_status == "FAILED"
    assert batch.error_code == "ORDER_ROW_PARSE_FAILED"


def test_observed_at_is_preserved_per_item() -> None:
    adapter, _ = _adapter()

    batch = _scan(adapter)

    assert [item.observed_at for item in batch.items] == [
        datetime(2026, 7, 31, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 31, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 31, 9, 2, tzinfo=timezone.utc),
    ]
