from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from app.services.operational_time import OperationalTimeService
from app.services.order_observation import (
    OrderObservationBatchInput,
    OrderObservationError,
    OrderObservationInput,
)


MAYI_HUATUAN_PLATFORM = "蚂蚁花团供应商"
MAYI_HUATUAN_ORDER_CAPABILITIES = {
    "supports_order_scan": True,
    "supports_current_trade_day": True,
    "supports_historical_trade_day": True,
}
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_ORDER_CAPTURE_ROWS = 2_000
MAX_ORDER_IDENTITY_TEXT_LENGTH = 200

CAPTURE_FIELDS = frozenset(
    {
        "selected_platform_trade_date",
        "scan_started_at",
        "scan_completed_at",
        "loading_completed",
        "scroll_completed",
        "no_more_marker_visible",
        "trusted_empty_marker_visible",
        "page_count",
        "rows",
        "unavailable_code",
        "failure_code",
        "failure_message",
    }
)
ROW_FIELDS = frozenset(
    {
        "order_created_at",
        "platform_product_name",
        "grade",
        "order_qty",
        "order_transaction_amount",
        "observed_at",
    }
)


@dataclass(frozen=True, slots=True)
class OrderReadOnlyRequest:
    execution_mode: str
    automation_run_id: str
    observation_batch_id: str
    platform_name: str
    requested_platform_trade_date: date


@dataclass(frozen=True, slots=True)
class MayiHuatuanOrderPageRow:
    order_created_at: str
    platform_product_name: str
    grade: str
    order_qty: str | int
    order_transaction_amount: str | int | Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class MayiHuatuanOrderPageCapture:
    selected_platform_trade_date: date | None
    scan_started_at: datetime
    scan_completed_at: datetime
    loading_completed: bool
    scroll_completed: bool
    no_more_marker_visible: bool
    trusted_empty_marker_visible: bool
    page_count: int
    rows: tuple[MayiHuatuanOrderPageRow, ...] = ()
    unavailable_code: str = ""
    failure_code: str = ""
    failure_message: str = ""


class MayiHuatuanOrderPageReader(Protocol):
    """UI boundary with no write operation in its callable surface."""

    def read_orders_read_only(
        self,
        request: OrderReadOnlyRequest,
    ) -> MayiHuatuanOrderPageCapture: ...


class MayiHuatuanOrderReadOnlyAdapter:
    """Validate a bounded order-page read without persisting page secrets."""

    def __init__(
        self,
        reader: MayiHuatuanOrderPageReader,
        *,
        operational_time: OperationalTimeService,
    ) -> None:
        self.reader = reader
        self.operational_time = operational_time

    @property
    def capabilities(self) -> Mapping[str, bool]:
        return dict(MAYI_HUATUAN_ORDER_CAPABILITIES)

    def scan(
        self,
        *,
        observation_batch_id: str,
        automation_run_id: str,
        platform_name: str,
        requested_platform_trade_date: date,
    ) -> OrderObservationBatchInput:
        if platform_name != MAYI_HUATUAN_PLATFORM:
            raise OrderObservationError(
                "Mayi Huatuan adapter is bound to a different platform"
            )
        request = OrderReadOnlyRequest(
            execution_mode="READ_ONLY",
            automation_run_id=automation_run_id,
            observation_batch_id=observation_batch_id,
            platform_name=platform_name,
            requested_platform_trade_date=requested_platform_trade_date,
        )
        capture = self.reader.read_orders_read_only(request)
        return self.from_capture(
            capture,
            observation_batch_id=observation_batch_id,
            automation_run_id=automation_run_id,
            platform_name=platform_name,
            requested_platform_trade_date=requested_platform_trade_date,
        )

    def from_capture(
        self,
        capture: MayiHuatuanOrderPageCapture,
        *,
        observation_batch_id: str,
        automation_run_id: str,
        platform_name: str,
        requested_platform_trade_date: date,
    ) -> OrderObservationBatchInput:
        started = _aware_utc(capture.scan_started_at, "scan_started_at")
        completed = _aware_utc(
            capture.scan_completed_at,
            "scan_completed_at",
        )
        if completed < started:
            raise OrderObservationError(
                "order-page capture completed before it started"
            )
        current_trade_date = self.operational_time.classify(
            completed
        ).platform_trade_date
        if requested_platform_trade_date > current_trade_date:
            return self._batch(
                observation_batch_id=observation_batch_id,
                automation_run_id=automation_run_id,
                platform_name=platform_name,
                requested_platform_trade_date=(
                    requested_platform_trade_date
                ),
                trade_day_status="OPEN",
                capture=capture,
                capability_result="UNAVAILABLE",
                batch_status="UNAVAILABLE",
                scope_complete=False,
                end_marker_verified=False,
                end_marker_kind="",
                items=(),
                error_code="ORDER_TRADE_DATE_NOT_OPEN",
                error_message="requested platform trade date is in the future",
            )
        trade_day_status = (
            "OPEN"
            if requested_platform_trade_date == current_trade_date
            else "CLOSED"
        )
        if capture.unavailable_code:
            return self._batch(
                observation_batch_id=observation_batch_id,
                automation_run_id=automation_run_id,
                platform_name=platform_name,
                requested_platform_trade_date=(
                    requested_platform_trade_date
                ),
                trade_day_status=trade_day_status,
                capture=capture,
                capability_result="UNAVAILABLE",
                batch_status="UNAVAILABLE",
                scope_complete=False,
                end_marker_verified=False,
                end_marker_kind="",
                items=(),
                error_code=_error_code(capture.unavailable_code),
                error_message=capture.failure_message,
            )
        if not capture.loading_completed:
            return self._batch(
                observation_batch_id=observation_batch_id,
                automation_run_id=automation_run_id,
                platform_name=platform_name,
                requested_platform_trade_date=(
                    requested_platform_trade_date
                ),
                trade_day_status=trade_day_status,
                capture=capture,
                capability_result="FAILED",
                batch_status="FAILED",
                scope_complete=False,
                end_marker_verified=False,
                end_marker_kind="",
                items=(),
                error_code=(
                    _error_code(capture.failure_code)
                    or "ORDER_PAGE_NOT_LOADED"
                ),
                error_message=capture.failure_message,
            )
        if (
            capture.selected_platform_trade_date
            != requested_platform_trade_date
        ):
            return self._batch(
                observation_batch_id=observation_batch_id,
                automation_run_id=automation_run_id,
                platform_name=platform_name,
                requested_platform_trade_date=(
                    requested_platform_trade_date
                ),
                trade_day_status=trade_day_status,
                capture=capture,
                capability_result="FAILED",
                batch_status="FAILED",
                scope_complete=False,
                end_marker_verified=False,
                end_marker_kind="",
                items=(),
                error_code="ORDER_DATE_MISMATCH",
                error_message="selected order date differs from requested date",
            )

        parsed: list[OrderObservationInput] = []
        parse_error = ""
        for row in capture.rows:
            try:
                item = _parse_row(
                    row,
                    operational_time=self.operational_time,
                    requested_platform_trade_date=(
                        requested_platform_trade_date
                    ),
                    scan_started_at=started,
                    scan_completed_at=completed,
                )
            except OrderObservationError as exc:
                parse_error = str(exc)
                break
            parsed.append(item)

        if parse_error:
            return self._batch(
                observation_batch_id=observation_batch_id,
                automation_run_id=automation_run_id,
                platform_name=platform_name,
                requested_platform_trade_date=(
                    requested_platform_trade_date
                ),
                trade_day_status=trade_day_status,
                capture=capture,
                capability_result="FAILED",
                batch_status="PARTIAL" if parsed else "FAILED",
                scope_complete=False,
                end_marker_verified=False,
                end_marker_kind="",
                items=tuple(parsed),
                error_code="ORDER_ROW_PARSE_FAILED",
                error_message=parse_error,
            )

        if not parsed:
            if capture.trusted_empty_marker_visible:
                return self._batch(
                    observation_batch_id=observation_batch_id,
                    automation_run_id=automation_run_id,
                    platform_name=platform_name,
                    requested_platform_trade_date=(
                        requested_platform_trade_date
                    ),
                    trade_day_status=trade_day_status,
                    capture=capture,
                    capability_result="SUCCEEDED",
                    batch_status="ACCEPTED",
                    scope_complete=True,
                    end_marker_verified=True,
                    end_marker_kind="TRUSTED_EMPTY",
                    items=(),
                )
            return self._batch(
                observation_batch_id=observation_batch_id,
                automation_run_id=automation_run_id,
                platform_name=platform_name,
                requested_platform_trade_date=(
                    requested_platform_trade_date
                ),
                trade_day_status=trade_day_status,
                capture=capture,
                capability_result="FAILED",
                batch_status="FAILED",
                scope_complete=False,
                end_marker_verified=False,
                end_marker_kind="",
                items=(),
                error_code="ORDER_EMPTY_NOT_VERIFIED",
                error_message=(
                    "zero rows were returned without the trusted empty marker"
                ),
            )

        complete = (
            capture.scroll_completed
            and capture.no_more_marker_visible
            and not capture.failure_code
        )
        return self._batch(
            observation_batch_id=observation_batch_id,
            automation_run_id=automation_run_id,
            platform_name=platform_name,
            requested_platform_trade_date=requested_platform_trade_date,
            trade_day_status=trade_day_status,
            capture=capture,
            capability_result="SUCCEEDED" if complete else "FAILED",
            batch_status="ACCEPTED" if complete else "PARTIAL",
            scope_complete=complete,
            end_marker_verified=complete,
            end_marker_kind="NO_MORE" if complete else "",
            items=tuple(parsed),
            error_code=(
                ""
                if complete
                else _error_code(capture.failure_code)
                or "ORDER_SCROLL_INCOMPLETE"
            ),
            error_message=(
                "" if complete else capture.failure_message
                or "order scroll or no-more marker was not verified"
            ),
        )

    def _batch(
        self,
        *,
        observation_batch_id: str,
        automation_run_id: str,
        platform_name: str,
        requested_platform_trade_date: date,
        trade_day_status: str,
        capture: MayiHuatuanOrderPageCapture,
        capability_result: str,
        batch_status: str,
        scope_complete: bool,
        end_marker_verified: bool,
        end_marker_kind: str,
        items: tuple[OrderObservationInput, ...],
        error_code: str = "",
        error_message: str = "",
    ) -> OrderObservationBatchInput:
        return OrderObservationBatchInput(
            observation_batch_id=observation_batch_id,
            automation_run_id=automation_run_id,
            platform_name=platform_name,
            requested_platform_trade_date=(
                requested_platform_trade_date
            ),
            trade_day_status=trade_day_status,
            capability_result=capability_result,
            batch_status=batch_status,
            scan_started_at=capture.scan_started_at,
            scan_completed_at=capture.scan_completed_at,
            scope_complete=scope_complete,
            end_marker_verified=end_marker_verified,
            end_marker_kind=end_marker_kind,
            page_count=capture.page_count,
            adapter_capabilities=self.capabilities,
            items=items,
            error_code=error_code,
            error_message=error_message,
        )


def page_capture_from_json(
    payload: Mapping[str, Any],
) -> MayiHuatuanOrderPageCapture:
    """Load a synthetic/transport capture while rejecting PII-shaped extras."""

    if not isinstance(payload, Mapping):
        raise OrderObservationError("order-page capture must be an object")
    unexpected = set(payload).difference(CAPTURE_FIELDS)
    if unexpected:
        raise OrderObservationError(
            "order-page capture contains forbidden or unknown fields: "
            + ", ".join(sorted(str(name) for name in unexpected))
        )
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise OrderObservationError("order-page rows must be an array")
    if len(raw_rows) > MAX_ORDER_CAPTURE_ROWS:
        raise OrderObservationError(
            "order-page rows exceed the contract limit"
        )
    rows: list[MayiHuatuanOrderPageRow] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise OrderObservationError("each order-page row must be an object")
        unexpected_row = set(raw).difference(ROW_FIELDS)
        if unexpected_row:
            raise OrderObservationError(
                "order-page row contains forbidden or unknown fields: "
                + ", ".join(sorted(str(name) for name in unexpected_row))
            )
        product_name = str(raw.get("platform_product_name") or "")
        grade = str(raw.get("grade") or "")
        if (
            len(product_name) > MAX_ORDER_IDENTITY_TEXT_LENGTH
            or len(grade) > MAX_ORDER_IDENTITY_TEXT_LENGTH
        ):
            raise OrderObservationError(
                "order-page product identity text exceeds the limit"
            )
        rows.append(
            MayiHuatuanOrderPageRow(
                order_created_at=str(raw.get("order_created_at") or ""),
                platform_product_name=product_name,
                grade=grade,
                order_qty=raw.get("order_qty", ""),
                order_transaction_amount=raw.get(
                    "order_transaction_amount",
                    "",
                ),
                observed_at=_parse_aware_datetime(
                    raw.get("observed_at"),
                    "observed_at",
                ),
            )
        )
    selected = payload.get("selected_platform_trade_date")
    return MayiHuatuanOrderPageCapture(
        selected_platform_trade_date=(
            date.fromisoformat(str(selected)) if selected else None
        ),
        scan_started_at=_parse_aware_datetime(
            payload.get("scan_started_at"),
            "scan_started_at",
        ),
        scan_completed_at=_parse_aware_datetime(
            payload.get("scan_completed_at"),
            "scan_completed_at",
        ),
        loading_completed=_required_bool(
            payload,
            "loading_completed",
        ),
        scroll_completed=_required_bool(payload, "scroll_completed"),
        no_more_marker_visible=_required_bool(
            payload,
            "no_more_marker_visible",
        ),
        trusted_empty_marker_visible=_required_bool(
            payload,
            "trusted_empty_marker_visible",
        ),
        page_count=_non_negative_int(payload.get("page_count")),
        rows=tuple(rows),
        unavailable_code=_error_code(payload.get("unavailable_code")),
        failure_code=_error_code(payload.get("failure_code")),
        failure_message=str(payload.get("failure_message") or "")[:512],
    )


def _parse_row(
    row: MayiHuatuanOrderPageRow,
    *,
    operational_time: OperationalTimeService,
    requested_platform_trade_date: date,
    scan_started_at: datetime,
    scan_completed_at: datetime,
) -> OrderObservationInput:
    product_name = str(row.platform_product_name or "").strip()
    grade = str(row.grade or "").strip()
    if not product_name or not grade:
        raise OrderObservationError(
            "order row is missing product name or grade"
        )
    created_at = _parse_platform_local_datetime(row.order_created_at)
    if (
        operational_time.classify(created_at).platform_trade_date
        != requested_platform_trade_date
    ):
        raise OrderObservationError(
            "order row timestamp belongs to a different platform trade date"
        )
    observed_at = _aware_utc(row.observed_at, "observed_at")
    if not scan_started_at <= observed_at <= scan_completed_at:
        raise OrderObservationError(
            "order row observed_at is outside the scan interval"
        )
    qty = _positive_int(row.order_qty)
    amount = _non_negative_decimal(row.order_transaction_amount)
    return OrderObservationInput(
        order_created_at=created_at,
        platform_product_name=product_name,
        grade=grade,
        order_qty=qty,
        order_transaction_amount=amount,
        observed_at=observed_at,
    )


def _parse_platform_local_datetime(value: object) -> datetime:
    text = str(value or "").strip()
    match = re.fullmatch(
        r"(?:下单时间[:：]\s*)?(\d{4}-\d{2}-\d{2} "
        r"\d{2}:\d{2}:\d{2})",
        text,
    )
    if not match:
        raise OrderObservationError(
            "order_created_at must include local date and seconds"
        )
    try:
        local = datetime.strptime(
            match.group(1),
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=LOCAL_TIMEZONE)
    except ValueError as exc:
        raise OrderObservationError("order_created_at is invalid") from exc
    return local.astimezone(timezone.utc)


def _parse_aware_datetime(value: object, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise OrderObservationError(f"{field_name} is invalid") from exc
    return _aware_utc(parsed, field_name)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OrderObservationError(
            f"{field_name} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise OrderObservationError("order_qty must be a positive integer")
    try:
        qty = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise OrderObservationError(
            "order_qty must be a positive integer"
        ) from exc
    if str(value).strip() != str(qty) or qty <= 0:
        raise OrderObservationError("order_qty must be a positive integer")
    return qty


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        raise OrderObservationError("page_count must be an integer")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise OrderObservationError("page_count must be an integer") from exc
    if number < 0:
        raise OrderObservationError("page_count must be non-negative")
    return number


def _non_negative_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float)):
        raise OrderObservationError(
            "order_transaction_amount must use an exact decimal"
        )
    text = str(value or "").strip()
    text = re.sub(r"^(?:成交金额|合计)[:：]?\s*", "", text)
    text = text.removeprefix("￥").strip()
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise OrderObservationError(
            "order_transaction_amount is invalid"
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise OrderObservationError(
            "order_transaction_amount must be finite and non-negative"
        )
    return amount


def _required_bool(payload: Mapping[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise OrderObservationError(f"{name} must be a boolean")
    return value


def _error_code(value: object) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", str(value or "").strip().upper())[
        :64
    ]
