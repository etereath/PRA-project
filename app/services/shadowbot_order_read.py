from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.adapters.mayi_huatuan_order import (
    MayiHuatuanOrderPageCapture,
    OrderReadOnlyRequest,
    page_capture_from_json,
)
from app.services.order_observation import OrderObservationError
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.shadowbot_contract_primitives import (
    ORDER_SCAN_CONTRACT_VERSION,
    ORDER_SCAN_REQUEST_SCHEMA_VERSION,
    ORDER_SCAN_RESULT_SCHEMA_VERSION,
    normalize_order_scan_request,
    order_scan_instruction_hash,
)


FORBIDDEN_ORDER_RESULT_KEYS = frozenset(
    {
        "platform_order_id",
        "order_id",
        "order_line_id",
        "buyer_name",
        "buyer_phone",
        "buyer_address",
        "chat_content",
        "customer_name",
        "screenshot",
        "raw_page_text",
        "page_text",
        "province",
    }
)
FORBIDDEN_ORDER_KEY_MARKERS = (
    "订单号",
    "买家",
    "客户姓名",
    "电话",
    "地址",
    "聊天",
)
FORBIDDEN_ORDER_RESULT_KEYS_COLLAPSED = frozenset(
    name.replace("_", "") for name in FORBIDDEN_ORDER_RESULT_KEYS
)


class ShadowBotOrderTransport(Protocol):
    """Execute one v6 READ_ONLY request and return its structured result."""

    def execute_order_read(
        self,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ShadowBotOrderReaderConfig:
    window_title: str = "蚂蚁花团供应商"
    applet_uri: str = ""
    element_timeout_seconds: int = 15
    applet_launch_timeout_seconds: int = 20
    max_rows: int = 500
    max_scrolls: int = 100
    max_seconds: int = 300
    request_ttl_seconds: int = 600


class ShadowBotOrderPageReader:
    """Concrete page-reader boundary backed by the existing ShadowBot host."""

    def __init__(
        self,
        transport: ShadowBotOrderTransport,
        *,
        config: ShadowBotOrderReaderConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        attempt_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.transport = transport
        self.config = config or ShadowBotOrderReaderConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.attempt_id_factory = attempt_id_factory or (
            lambda: f"ORDER-READ-{uuid4().hex}"
        )

    def set_wait_callback(
        self,
        callback: Callable[[], bool] | None,
    ) -> None:
        setter = getattr(self.transport, "set_wait_callback", None)
        if callable(setter):
            setter(callback)

    def acknowledge_last_result(self) -> None:
        acknowledge = getattr(
            self.transport,
            "acknowledge_last_result",
            None,
        )
        if callable(acknowledge):
            acknowledge()

    def read_orders_read_only(
        self,
        request: OrderReadOnlyRequest,
    ) -> MayiHuatuanOrderPageCapture:
        if request.execution_mode != "READ_ONLY":
            raise OrderObservationError("order transport is READ_ONLY only")
        now = _as_utc(self.clock())
        payload = {
            "schema_version": ORDER_SCAN_REQUEST_SCHEMA_VERSION,
            "contract_version": ORDER_SCAN_CONTRACT_VERSION,
            "execution_mode": "READ_ONLY",
            "automation_run_id": request.automation_run_id,
            "observation_batch_id": request.observation_batch_id,
            "execution_attempt_id": self.attempt_id_factory(),
            "platform_name": request.platform_name,
            "requested_platform_trade_date": (
                request.requested_platform_trade_date.isoformat()
            ),
            "window_title": self.config.window_title,
            "applet_uri": self.config.applet_uri,
            "element_timeout_seconds": (
                self.config.element_timeout_seconds
            ),
            "applet_launch_timeout_seconds": (
                self.config.applet_launch_timeout_seconds
            ),
            "limits": {
                "max_rows": self.config.max_rows,
                "max_scrolls": self.config.max_scrolls,
                "max_seconds": self.config.max_seconds,
            },
            "created_at": now.isoformat(),
            "expires_at": (
                now + timedelta(seconds=self.config.request_ttl_seconds)
            ).isoformat(),
        }
        normalized = normalize_order_scan_request(payload)
        payload.update(normalized)
        payload["instruction_hash"] = order_scan_instruction_hash(payload)
        result = self.transport.execute_order_read(payload)
        return validate_order_scan_result(payload, result)


def validate_order_scan_result(
    request: Mapping[str, Any],
    result: Mapping[str, Any],
) -> MayiHuatuanOrderPageCapture:
    if not isinstance(result, Mapping):
        raise OrderObservationError("order scan result must be an object")
    _reject_forbidden_keys(result)
    if (
        result.get("schema_version") != ORDER_SCAN_RESULT_SCHEMA_VERSION
        or result.get("contract_version") != ORDER_SCAN_CONTRACT_VERSION
    ):
        raise OrderObservationError("order scan result schema is invalid")
    for field in (
        "execution_attempt_id",
        "automation_run_id",
        "observation_batch_id",
        "platform_name",
        "requested_platform_trade_date",
        "instruction_hash",
    ):
        if str(result.get(field) or "") != str(request.get(field) or ""):
            raise OrderObservationError(
                f"order scan result {field} does not match its request"
            )
    if str(result.get("execution_mode") or "").upper() != "READ_ONLY":
        raise OrderObservationError("order scan result is not READ_ONLY")
    if bool(result.get("business_operation_completed")):
        raise OrderObservationError(
            "order scan result reported a platform write side effect"
        )
    if str(result.get("side_effect_state") or "") != "NOT_STARTED":
        raise OrderObservationError(
            "order scan result side_effect_state must be NOT_STARTED"
        )
    capture = result.get("capture")
    if not isinstance(capture, Mapping):
        raise OrderObservationError("order scan result has no capture")
    return page_capture_from_json(capture)


class ShadowBotFileQueueOrderTransport:
    """Synchronous v6 transport over the existing single-worker file queue."""

    def __init__(
        self,
        queue_dir: Path,
        *,
        timeout_seconds: float = 330.0,
        poll_interval_seconds: float = 0.25,
        require_fresh_heartbeat: bool = True,
        heartbeat_max_age_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.queue_dir = Path(queue_dir)
        self.runner = ShadowBotFileQueueRunner(self.queue_dir)
        self.timeout_seconds = float(timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.require_fresh_heartbeat = bool(require_fresh_heartbeat)
        self.heartbeat_max_age_seconds = float(
            heartbeat_max_age_seconds
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.sleeper = sleeper or time.sleep
        self._wait_callback: Callable[[], bool] | None = None
        self._last_attempt_id = ""
        self._last_request_sha256 = ""

    def set_wait_callback(
        self,
        callback: Callable[[], bool] | None,
    ) -> None:
        self._wait_callback = callback

    def execute_order_read(
        self,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        request_data = dict(request)
        if self.require_fresh_heartbeat:
            self._require_running_worker()
        attempt_id = str(
            request_data.get("execution_attempt_id") or ""
        )
        started = self.runner.start(request_data)
        self._last_attempt_id = attempt_id
        self._last_request_sha256 = str(
            started.raw_output.get("request_file_sha256") or ""
        )
        result_path = (
            self.queue_dir
            / "results"
            / f"{attempt_id}.result.json"
        )
        deadline = self.monotonic() + self.timeout_seconds
        next_heartbeat = self.monotonic() + 5.0
        while self.monotonic() < deadline:
            if result_path.exists():
                result = self._read_checked_result(result_path)
                expected_request_sha256 = (
                    "sha256:" + self._last_request_sha256
                )
                if (
                    str(result.get("request_file_sha256") or "")
                    != expected_request_sha256
                ):
                    raise OrderObservationError(
                        "ShadowBot order result is bound to a different "
                        "request file"
                    )
                return result
            if (
                self._wait_callback is not None
                and self.monotonic() >= next_heartbeat
            ):
                if not self._wait_callback():
                    raise OrderObservationError(
                        "automation lease was lost while waiting for "
                        "the order scan"
                    )
                next_heartbeat = self.monotonic() + 5.0
            self.sleeper(self.poll_interval_seconds)
        raise OrderObservationError(
            "ShadowBot order scan timed out without a result"
        )

    def acknowledge_last_result(self) -> None:
        if not self._last_attempt_id:
            return
        attempt_id = self._last_attempt_id
        self.runner.archive_attempt_artifacts(attempt_id)
        self._last_attempt_id = ""
        self._last_request_sha256 = ""

    def _require_running_worker(self) -> None:
        heartbeat_path = self.queue_dir / "heartbeat.json"
        if not heartbeat_path.exists():
            raise OrderObservationError(
                "ShadowBot worker heartbeat is unavailable"
            )
        try:
            payload = json.loads(
                heartbeat_path.read_bytes().decode("utf-8-sig")
            )
            if not isinstance(payload, dict):
                raise ValueError("heartbeat must be an object")
            updated_at = datetime.fromisoformat(
                str(payload.get("updated_at") or "").replace(
                    "Z",
                    "+00:00",
                )
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise OrderObservationError(
                "ShadowBot worker heartbeat is invalid"
            ) from exc
        if (
            str(payload.get("status") or "") != "RUNNING"
            or updated_at.tzinfo is None
        ):
            raise OrderObservationError(
                "ShadowBot worker is not in RUNNING state"
            )
        age = (
            _as_utc(self.clock()) - updated_at.astimezone(timezone.utc)
        ).total_seconds()
        if age < -5 or age > self.heartbeat_max_age_seconds:
            raise OrderObservationError(
                "ShadowBot worker heartbeat is stale"
            )

    @staticmethod
    def _read_checked_result(
        result_path: Path,
    ) -> Mapping[str, Any]:
        result_bytes = result_path.read_bytes()
        if len(result_bytes) > 4 * 1024 * 1024:
            raise OrderObservationError(
                "ShadowBot order result exceeds the size limit"
            )
        checksum_path = result_path.with_suffix(
            result_path.suffix + ".sha256"
        )
        try:
            expected = checksum_path.read_text(
                encoding="ascii"
            ).strip()
        except OSError as exc:
            raise OrderObservationError(
                "ShadowBot order result checksum is unavailable"
            ) from exc
        actual = hashlib.sha256(result_bytes).hexdigest()
        if expected.casefold() != actual:
            raise OrderObservationError(
                "ShadowBot order result checksum does not match"
            )
        try:
            payload = json.loads(result_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OrderObservationError(
                "ShadowBot order result JSON is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise OrderObservationError(
                "ShadowBot order result must be an object"
            )
        return payload


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(
                r"[^a-z0-9_]",
                "_",
                str(key).strip().casefold(),
            )
            collapsed = normalized.replace("_", "")
            raw_key = str(key).strip()
            if (
                normalized in FORBIDDEN_ORDER_RESULT_KEYS
                or collapsed in FORBIDDEN_ORDER_RESULT_KEYS_COLLAPSED
                or any(
                    marker in raw_key
                    for marker in FORBIDDEN_ORDER_KEY_MARKERS
                )
            ):
                raise OrderObservationError(
                    "order scan result contains a forbidden identity or PII "
                    f"field: {key}"
                )
            _reject_forbidden_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_forbidden_keys(item)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OrderObservationError("order reader clock must be timezone-aware")
    return value.astimezone(timezone.utc)
