from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping

from app.automation_models import AutomationRunClaim
from app.enums import ProductMappingStatus, SellerPhase
from app.repositories.automation_repository import (
    validate_live_automation_claim_in_transaction,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.operational_time import OperationalTimeService
from app.services.product_mapping import (
    CompiledProductMappings,
    normalize_mapping_text,
)


ORDER_OBSERVATION_CONTRACT_VERSION = "order-observation-1.0"
ORDER_IDENTITY_FINGERPRINT_VERSION = "order-identity-fingerprint-1.0"
RAW_ORDER_OBSERVATION_VERSION = "raw-order-observation-1.0"

CAPABILITY_RESULTS = frozenset(
    {"SUCCEEDED", "UNSUPPORTED", "UNAVAILABLE", "FAILED"}
)
BATCH_STATUSES = frozenset(
    {"ACCEPTED", "PARTIAL", "UNAVAILABLE", "FAILED"}
)
TRADE_DAY_STATUSES = frozenset({"OPEN", "CLOSED"})
ALLOWED_ORDER_PARENT_TYPES = frozenset(
    {"FULL_MARKET_SCAN", "PRE_CUTOFF_FULL_SCAN"}
)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class OrderObservationError(ValueError):
    """Raised when an order snapshot cannot be accepted as immutable fact."""


@dataclass(frozen=True, slots=True)
class OrderObservationInput:
    order_created_at: datetime
    platform_product_name: str
    grade: str
    order_qty: int
    order_transaction_amount: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class OrderObservationBatchInput:
    observation_batch_id: str
    automation_run_id: str
    platform_name: str
    requested_platform_trade_date: date
    trade_day_status: str
    capability_result: str
    batch_status: str
    scan_started_at: datetime
    scan_completed_at: datetime
    scope_complete: bool
    end_marker_verified: bool
    end_marker_kind: str
    page_count: int
    adapter_capabilities: Mapping[str, bool]
    items: tuple[OrderObservationInput, ...] = ()
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class ImportedOrderObservation:
    observation_item_id: str
    platform_name: str
    platform_trade_date: date
    trade_day_status: str
    order_identity_fingerprint: str
    occurrence_no: int
    order_created_at: datetime
    platform_product_name: str
    grade: str
    internal_sku: str | None
    mapping_status: ProductMappingStatus
    mapping_version: str
    order_qty: int
    order_transaction_amount: Decimal
    observed_at: datetime
    seller_operation_date: date
    seller_phase: SellerPhase
    raw_observation_sha256: str


@dataclass(frozen=True, slots=True)
class OrderObservationImportResult:
    observation_batch_id: str
    automation_run_id: str
    platform_name: str
    requested_platform_trade_date: date
    trade_day_status: str
    capability_result: str
    batch_status: str
    content_sha256: str
    item_count: int
    transaction_amount_total: Decimal
    occurrence_counts: Mapping[str, int]
    mapping_version: str
    replayed: bool
    items: tuple[ImportedOrderObservation, ...] = ()


class OrderObservationImporter:
    """Atomically map and persist one fenced ORDER_SCAN result."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        operational_time: OperationalTimeService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.operational_time = operational_time
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def import_batch(
        self,
        batch: OrderObservationBatchInput,
        *,
        mappings: CompiledProductMappings,
        claim: AutomationRunClaim,
    ) -> OrderObservationImportResult:
        normalized = normalize_order_observation_batch(batch)
        raw_items = canonical_order_items(normalized)
        content_sha256 = order_batch_content_sha256(normalized, raw_items)

        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _as_utc(self.clock(), "clock")
                self._validate_run_binding(
                    connection,
                    normalized,
                    claim=claim,
                )
                replay = self._existing_replay(
                    connection,
                    normalized,
                    content_sha256=content_sha256,
                )
                if replay is not None:
                    connection.commit()
                    return replay

                validate_live_automation_claim_in_transaction(
                    connection,
                    claim,
                    now=current,
                )
                imported_items = self._map_items(
                    normalized,
                    raw_items,
                    mappings=mappings,
                    expected_time_policy_version=(
                        claim.run.time_policy_version
                    ),
                )
                stored_status = normalized.batch_status
                if (
                    stored_status == "ACCEPTED"
                    and any(
                        item.mapping_status is not ProductMappingStatus.VERIFIED
                        for item in imported_items
                    )
                ):
                    stored_status = "PARTIAL"
                requested_range_json = _canonical_json(
                    {
                        "contract_version": (
                            ORDER_OBSERVATION_CONTRACT_VERSION
                        ),
                        "requested_platform_trade_date": (
                            normalized.requested_platform_trade_date.isoformat()
                        ),
                        "actual_platform_trade_date": (
                            normalized.requested_platform_trade_date.isoformat()
                        ),
                        "adapter_capabilities": dict(
                            sorted(normalized.adapter_capabilities.items())
                        ),
                        "page_count": normalized.page_count,
                        "source_row_count": len(raw_items),
                        "end_marker_kind": normalized.end_marker_kind,
                        "source_batch_status": normalized.batch_status,
                        "accepted_mapping_version": (
                            mappings.mapping_version if raw_items else ""
                        ),
                    }
                )
                connection.execute(
                    """
                    INSERT INTO order_observation_batches(
                        observation_batch_id, automation_run_id,
                        platform_name, requested_platform_trade_date,
                        trade_day_status, capability_result, batch_status,
                        scan_started_at, scan_completed_at,
                        requested_range_json, scope_complete,
                        end_marker_verified, content_sha256,
                        time_policy_version, error_code, error_message,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized.observation_batch_id,
                        normalized.automation_run_id,
                        normalized.platform_name,
                        normalized.requested_platform_trade_date.isoformat(),
                        normalized.trade_day_status,
                        normalized.capability_result,
                        stored_status,
                        _datetime_text(normalized.scan_started_at),
                        _datetime_text(normalized.scan_completed_at),
                        requested_range_json,
                        int(normalized.scope_complete),
                        int(normalized.end_marker_verified),
                        content_sha256,
                        claim.run.time_policy_version,
                        normalized.error_code,
                        normalized.error_message,
                        _datetime_text(current),
                    ),
                )
                for item in imported_items:
                    self._insert_item(
                        connection,
                        normalized.observation_batch_id,
                        item,
                    )
                result = _result_from_values(
                    normalized,
                    content_sha256=content_sha256,
                    batch_status=stored_status,
                    mapping_version=(
                        mappings.mapping_version if raw_items else ""
                    ),
                    imported_items=imported_items,
                    replayed=False,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_item(
        connection: sqlite3.Connection,
        observation_batch_id: str,
        item: ImportedOrderObservation,
    ) -> None:
        connection.execute(
            """
            INSERT INTO order_observation_items(
                observation_item_id, observation_batch_id,
                platform_name, platform_trade_date, trade_day_status,
                order_identity_fingerprint, occurrence_no,
                order_created_at, platform_product_name, grade,
                internal_sku, mapping_status, mapping_version,
                order_qty, order_transaction_amount, observed_at,
                seller_operation_date, seller_phase,
                raw_observation_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.observation_item_id,
                observation_batch_id,
                item.platform_name,
                item.platform_trade_date.isoformat(),
                item.trade_day_status,
                item.order_identity_fingerprint,
                item.occurrence_no,
                _datetime_text(item.order_created_at),
                item.platform_product_name,
                item.grade,
                item.internal_sku,
                item.mapping_status.value,
                item.mapping_version,
                item.order_qty,
                _decimal_text(item.order_transaction_amount),
                _datetime_text(item.observed_at),
                item.seller_operation_date.isoformat(),
                item.seller_phase.value,
                item.raw_observation_sha256,
            ),
        )

    def _map_items(
        self,
        batch: OrderObservationBatchInput,
        raw_items: tuple[dict[str, Any], ...],
        *,
        mappings: CompiledProductMappings,
        expected_time_policy_version: str,
    ) -> tuple[ImportedOrderObservation, ...]:
        occurrences: Counter[str] = Counter()
        imported: list[ImportedOrderObservation] = []
        for raw in raw_items:
            fingerprint = order_identity_fingerprint(
                platform_name=batch.platform_name,
                platform_trade_date=batch.requested_platform_trade_date,
                order_created_at=raw["order_created_at"],
                platform_product_name=raw["platform_product_name"],
                grade=raw["grade"],
            )
            occurrences[fingerprint] += 1
            occurrence_no = occurrences[fingerprint]
            resolution = mappings.resolve(
                platform_name=batch.platform_name,
                platform_product_name=raw["platform_product_name"],
                grade=raw["grade"],
                observed_at=raw["observed_at"],
            )
            operational = self.operational_time.classify(raw["observed_at"])
            if (
                operational.time_policy_version
                != expected_time_policy_version
            ):
                raise OrderObservationError(
                    "order observation time policy does not match its run"
                )
            order_time = self.operational_time.classify(
                raw["order_created_at"]
            )
            if (
                order_time.platform_trade_date
                != batch.requested_platform_trade_date
            ):
                raise OrderObservationError(
                    "order_created_at belongs to a different platform "
                    "trade date"
                )
            raw_sha256 = raw_observation_sha256(
                platform_name=batch.platform_name,
                platform_trade_date=batch.requested_platform_trade_date,
                trade_day_status=batch.trade_day_status,
                order_created_at=raw["order_created_at"],
                platform_product_name=raw["platform_product_name"],
                grade=raw["grade"],
                order_qty=raw["order_qty"],
                order_transaction_amount=raw[
                    "order_transaction_amount"
                ],
                observed_at=raw["observed_at"],
            )
            item_id = _stable_id(
                "ORDER-ITEM",
                batch.observation_batch_id,
                fingerprint,
                str(occurrence_no),
            )
            imported.append(
                ImportedOrderObservation(
                    observation_item_id=item_id,
                    platform_name=batch.platform_name,
                    platform_trade_date=(
                        batch.requested_platform_trade_date
                    ),
                    trade_day_status=batch.trade_day_status,
                    order_identity_fingerprint=fingerprint,
                    occurrence_no=occurrence_no,
                    order_created_at=raw["order_created_at"],
                    platform_product_name=raw["platform_product_name"],
                    grade=raw["grade"],
                    internal_sku=resolution.internal_sku,
                    mapping_status=resolution.mapping_status,
                    mapping_version=mappings.mapping_version,
                    order_qty=raw["order_qty"],
                    order_transaction_amount=raw[
                        "order_transaction_amount"
                    ],
                    observed_at=raw["observed_at"],
                    seller_operation_date=(
                        operational.seller_operation_date
                    ),
                    seller_phase=operational.seller_phase,
                    raw_observation_sha256=raw_sha256,
                )
            )
        return tuple(imported)

    @staticmethod
    def _validate_run_binding(
        connection: sqlite3.Connection,
        batch: OrderObservationBatchInput,
        *,
        claim: AutomationRunClaim,
    ) -> None:
        if claim.run.run_id != batch.automation_run_id:
            raise OrderObservationError(
                "order observation is bound to the wrong Automation Run"
            )
        row = connection.execute(
            """
            SELECT run_id, job_type, platform_name, platform_trade_date,
                   time_policy_version
            FROM automation_runs
            WHERE run_id = ?
            """,
            (batch.automation_run_id,),
        ).fetchone()
        if row is None:
            raise OrderObservationError("Automation Run does not exist")
        if str(row["job_type"]) != "ORDER_SCAN":
            raise OrderObservationError(
                "order observation requires an ORDER_SCAN run"
            )
        if str(row["platform_name"]) != batch.platform_name:
            raise OrderObservationError(
                "order observation platform does not match its run"
            )
        run_trade_date = date.fromisoformat(str(row["platform_trade_date"]))
        if batch.requested_platform_trade_date > run_trade_date:
            raise OrderObservationError(
                "order observation cannot query a future platform trade date"
            )
        expected_status = (
            "OPEN"
            if batch.requested_platform_trade_date == run_trade_date
            else "CLOSED"
        )
        if batch.trade_day_status != expected_status:
            raise OrderObservationError(
                "order observation trade_day_status does not match the run"
            )
        if str(row["time_policy_version"]) != claim.run.time_policy_version:
            raise OrderObservationError(
                "order observation time policy does not match its run"
            )
        parent_rows = connection.execute(
            """
            SELECT parent.job_type, parent.platform_name
            FROM automation_run_links AS link
            INNER JOIN automation_runs AS parent
                ON parent.run_id = link.parent_run_id
            WHERE link.child_run_id = ?
              AND link.relation_type = 'ORDER_SCAN_CHILD'
            """,
            (batch.automation_run_id,),
        ).fetchall()
        if len(parent_rows) != 1:
            raise OrderObservationError(
                "ORDER_SCAN run must have exactly one legal parent"
            )
        parent = parent_rows[0]
        if str(parent["job_type"]) not in ALLOWED_ORDER_PARENT_TYPES:
            raise OrderObservationError(
                "ORDER_SCAN parent job type is not supported"
            )
        if str(parent["platform_name"]) != batch.platform_name:
            raise OrderObservationError(
                "ORDER_SCAN parent platform does not match"
            )

    @staticmethod
    def _existing_replay(
        connection: sqlite3.Connection,
        batch: OrderObservationBatchInput,
        *,
        content_sha256: str,
    ) -> OrderObservationImportResult | None:
        by_id = connection.execute(
            """
            SELECT *
            FROM order_observation_batches
            WHERE observation_batch_id = ?
            """,
            (batch.observation_batch_id,),
        ).fetchone()
        if by_id is not None:
            if (
                str(by_id["automation_run_id"]) != batch.automation_run_id
                or str(by_id["content_sha256"]) != content_sha256
            ):
                raise OrderObservationError(
                    "observation_batch_id already has different content"
                )
            return _result_from_database(connection, by_id, replayed=True)

        by_run = connection.execute(
            """
            SELECT *
            FROM order_observation_batches
            WHERE automation_run_id = ?
            ORDER BY created_at, observation_batch_id
            """,
            (batch.automation_run_id,),
        ).fetchall()
        if not by_run:
            return None
        exact = [
            row
            for row in by_run
            if str(row["content_sha256"]) == content_sha256
        ]
        if len(exact) == 1:
            return _result_from_database(
                connection,
                exact[0],
                replayed=True,
            )
        raise OrderObservationError(
            "ORDER_SCAN run already owns different observation content"
        )


def normalize_order_observation_batch(
    batch: OrderObservationBatchInput,
) -> OrderObservationBatchInput:
    batch_id = str(batch.observation_batch_id or "").strip()
    run_id = str(batch.automation_run_id or "").strip()
    platform = str(batch.platform_name or "").strip()
    if not batch_id or not run_id or not platform:
        raise OrderObservationError(
            "batch id, Automation Run id and platform are required"
        )
    capability = str(batch.capability_result or "").strip().upper()
    status = str(batch.batch_status or "").strip().upper()
    trade_day_status = str(batch.trade_day_status or "").strip().upper()
    if capability not in CAPABILITY_RESULTS:
        raise OrderObservationError("invalid capability_result")
    if status not in BATCH_STATUSES:
        raise OrderObservationError("invalid batch_status")
    if trade_day_status not in TRADE_DAY_STATUSES:
        raise OrderObservationError("invalid trade_day_status")
    started = _as_utc(batch.scan_started_at, "scan_started_at")
    completed = _as_utc(batch.scan_completed_at, "scan_completed_at")
    if completed < started:
        raise OrderObservationError(
            "scan_completed_at must not precede scan_started_at"
        )
    if isinstance(batch.scope_complete, bool) is False:
        raise OrderObservationError("scope_complete must be a boolean")
    if isinstance(batch.end_marker_verified, bool) is False:
        raise OrderObservationError(
            "end_marker_verified must be a boolean"
        )
    if (
        isinstance(batch.page_count, bool)
        or not isinstance(batch.page_count, int)
        or batch.page_count < 0
    ):
        raise OrderObservationError("page_count must be a non-negative integer")
    capabilities = {
        name: bool(batch.adapter_capabilities.get(name))
        for name in (
            "supports_order_scan",
            "supports_current_trade_day",
            "supports_historical_trade_day",
        )
    }
    _validate_batch_state(
        capability,
        status,
        scope_complete=batch.scope_complete,
        end_marker_verified=batch.end_marker_verified,
        end_marker_kind=batch.end_marker_kind,
        items=batch.items,
    )
    normalized_items = tuple(
        _normalize_order_input(
            item,
            requested_trade_date=batch.requested_platform_trade_date,
            scan_started_at=started,
            scan_completed_at=completed,
        )
        for item in batch.items
    )
    error_code = str(batch.error_code or "").strip().upper()
    error_message = _safe_error_message(batch.error_message)
    if capability == "SUCCEEDED" and status == "ACCEPTED":
        error_code = ""
        error_message = ""
    elif not error_code and capability != "SUCCEEDED":
        raise OrderObservationError(
            "non-success capability_result requires error_code"
        )
    return OrderObservationBatchInput(
        observation_batch_id=batch_id,
        automation_run_id=run_id,
        platform_name=platform,
        requested_platform_trade_date=batch.requested_platform_trade_date,
        trade_day_status=trade_day_status,
        capability_result=capability,
        batch_status=status,
        scan_started_at=started,
        scan_completed_at=completed,
        scope_complete=batch.scope_complete,
        end_marker_verified=batch.end_marker_verified,
        end_marker_kind=str(batch.end_marker_kind or "").strip().upper(),
        page_count=int(batch.page_count),
        adapter_capabilities=capabilities,
        items=normalized_items,
        error_code=error_code,
        error_message=error_message,
    )


def canonical_order_items(
    batch: OrderObservationBatchInput,
) -> tuple[dict[str, Any], ...]:
    items = [
        {
            "order_created_at": _as_utc(
                item.order_created_at,
                "order_created_at",
            ),
            "platform_product_name": item.platform_product_name,
            "grade": item.grade,
            "order_qty": item.order_qty,
            "order_transaction_amount": item.order_transaction_amount,
            "observed_at": _as_utc(item.observed_at, "observed_at"),
        }
        for item in batch.items
    ]
    items.sort(
        key=lambda item: (
            -item["order_created_at"].timestamp(),
            normalize_mapping_text(item["platform_product_name"]),
            normalize_mapping_text(item["grade"]),
            item["order_qty"],
            _decimal_text(item["order_transaction_amount"]),
            item["observed_at"].timestamp(),
        )
    )
    return tuple(items)


def order_identity_fingerprint(
    *,
    platform_name: str,
    platform_trade_date: date,
    order_created_at: datetime,
    platform_product_name: str,
    grade: str,
) -> str:
    payload = {
        "fingerprint_version": ORDER_IDENTITY_FINGERPRINT_VERSION,
        "platform_name": normalize_mapping_text(platform_name),
        "platform_trade_date": platform_trade_date.isoformat(),
        "order_created_at": _datetime_text(
            _as_utc(order_created_at, "order_created_at")
        ),
        "platform_product_name": normalize_mapping_text(
            platform_product_name
        ),
        "grade": normalize_mapping_text(grade),
    }
    return _sha256_payload(payload)


def raw_observation_sha256(
    *,
    platform_name: str,
    platform_trade_date: date,
    trade_day_status: str,
    order_created_at: datetime,
    platform_product_name: str,
    grade: str,
    order_qty: int,
    order_transaction_amount: Decimal,
    observed_at: datetime,
) -> str:
    payload = {
        "raw_observation_version": RAW_ORDER_OBSERVATION_VERSION,
        "platform_name": str(platform_name).strip(),
        "platform_trade_date": platform_trade_date.isoformat(),
        "trade_day_status": trade_day_status,
        "order_created_at": _datetime_text(
            _as_utc(order_created_at, "order_created_at")
        ),
        "platform_product_name": str(platform_product_name).strip(),
        "grade": str(grade).strip(),
        "order_qty": int(order_qty),
        "order_transaction_amount": _decimal_text(
            order_transaction_amount
        ),
        "observed_at": _datetime_text(
            _as_utc(observed_at, "observed_at")
        ),
    }
    return _sha256_payload(payload)


def order_batch_content_sha256(
    batch: OrderObservationBatchInput,
    items: Iterable[Mapping[str, Any]] | None = None,
) -> str:
    source_items = tuple(items or canonical_order_items(batch))
    payload = {
        "contract_version": ORDER_OBSERVATION_CONTRACT_VERSION,
        "automation_run_id": batch.automation_run_id,
        "platform_name": batch.platform_name,
        "requested_platform_trade_date": (
            batch.requested_platform_trade_date.isoformat()
        ),
        "trade_day_status": batch.trade_day_status,
        "capability_result": batch.capability_result,
        "batch_status": batch.batch_status,
        "scan_started_at": _datetime_text(batch.scan_started_at),
        "scan_completed_at": _datetime_text(batch.scan_completed_at),
        "scope_complete": batch.scope_complete,
        "end_marker_verified": batch.end_marker_verified,
        "end_marker_kind": batch.end_marker_kind,
        "page_count": batch.page_count,
        "adapter_capabilities": dict(
            sorted(batch.adapter_capabilities.items())
        ),
        "items": [
            {
                "order_created_at": _datetime_text(
                    item["order_created_at"]
                ),
                "platform_product_name": item["platform_product_name"],
                "grade": item["grade"],
                "order_qty": item["order_qty"],
                "order_transaction_amount": _decimal_text(
                    item["order_transaction_amount"]
                ),
                "observed_at": _datetime_text(item["observed_at"]),
            }
            for item in source_items
        ],
        "error_code": batch.error_code,
        "error_message": batch.error_message,
    }
    return _sha256_payload(payload)


def _normalize_order_input(
    item: OrderObservationInput,
    *,
    requested_trade_date: date,
    scan_started_at: datetime,
    scan_completed_at: datetime,
) -> OrderObservationInput:
    product_name = str(item.platform_product_name or "").strip()
    grade = str(item.grade or "").strip()
    if not product_name or not grade:
        raise OrderObservationError(
            "platform product name and grade are required"
        )
    created_at = _as_utc(item.order_created_at, "order_created_at")
    observed_at = _as_utc(item.observed_at, "observed_at")
    if not scan_started_at <= observed_at <= scan_completed_at:
        raise OrderObservationError(
            "item observed_at must be within its scan interval"
        )
    if isinstance(item.order_qty, bool) or int(item.order_qty) <= 0:
        raise OrderObservationError("order_qty must be a positive integer")
    amount = _decimal(item.order_transaction_amount)
    return OrderObservationInput(
        order_created_at=created_at,
        platform_product_name=product_name,
        grade=grade,
        order_qty=int(item.order_qty),
        order_transaction_amount=amount,
        observed_at=observed_at,
    )


def _validate_batch_state(
    capability: str,
    status: str,
    *,
    scope_complete: bool,
    end_marker_verified: bool,
    end_marker_kind: str,
    items: tuple[OrderObservationInput, ...],
) -> None:
    allowed = {
        ("SUCCEEDED", "ACCEPTED"),
        ("SUCCEEDED", "PARTIAL"),
        ("UNSUPPORTED", "UNAVAILABLE"),
        ("UNAVAILABLE", "UNAVAILABLE"),
        ("FAILED", "FAILED"),
        ("FAILED", "PARTIAL"),
    }
    if (capability, status) not in allowed:
        raise OrderObservationError(
            "capability_result and batch_status are inconsistent"
        )
    if status == "ACCEPTED":
        if not scope_complete or not end_marker_verified:
            raise OrderObservationError(
                "ACCEPTED requires complete scope and a verified end marker"
            )
        marker = str(end_marker_kind or "").strip().upper()
        if not items and marker != "TRUSTED_EMPTY":
            raise OrderObservationError(
                "an accepted empty page requires TRUSTED_EMPTY"
            )
        if items and marker != "NO_MORE":
            raise OrderObservationError(
                "an accepted data page requires NO_MORE"
            )
    if status in {"UNAVAILABLE", "FAILED"} and items:
        raise OrderObservationError(
            f"{status} batches must not contain order items"
        )


def _result_from_database(
    connection: sqlite3.Connection,
    batch_row: sqlite3.Row,
    *,
    replayed: bool,
) -> OrderObservationImportResult:
    item_rows = connection.execute(
        """
        SELECT *
        FROM order_observation_items
        WHERE observation_batch_id = ?
        ORDER BY order_created_at DESC,
                 order_identity_fingerprint,
                 occurrence_no
        """,
        (str(batch_row["observation_batch_id"]),),
    ).fetchall()
    items = tuple(_item_from_row(row) for row in item_rows)
    requested_range = json.loads(str(batch_row["requested_range_json"]))
    mapping_version = str(
        requested_range.get("accepted_mapping_version") or ""
    )
    batch = OrderObservationBatchInput(
        observation_batch_id=str(batch_row["observation_batch_id"]),
        automation_run_id=str(batch_row["automation_run_id"]),
        platform_name=str(batch_row["platform_name"]),
        requested_platform_trade_date=date.fromisoformat(
            str(batch_row["requested_platform_trade_date"])
        ),
        trade_day_status=str(batch_row["trade_day_status"]),
        capability_result=str(batch_row["capability_result"]),
        batch_status=str(batch_row["batch_status"]),
        scan_started_at=_datetime(str(batch_row["scan_started_at"])),
        scan_completed_at=_datetime(str(batch_row["scan_completed_at"])),
        scope_complete=bool(batch_row["scope_complete"]),
        end_marker_verified=bool(batch_row["end_marker_verified"]),
        end_marker_kind=str(requested_range.get("end_marker_kind") or ""),
        page_count=int(requested_range.get("page_count") or 0),
        adapter_capabilities=dict(
            requested_range.get("adapter_capabilities") or {}
        ),
        error_code=str(batch_row["error_code"]),
        error_message=str(batch_row["error_message"]),
    )
    return _result_from_values(
        batch,
        content_sha256=str(batch_row["content_sha256"]),
        batch_status=str(batch_row["batch_status"]),
        mapping_version=mapping_version,
        imported_items=items,
        replayed=replayed,
    )


def _result_from_values(
    batch: OrderObservationBatchInput,
    *,
    content_sha256: str,
    batch_status: str,
    mapping_version: str,
    imported_items: tuple[ImportedOrderObservation, ...],
    replayed: bool,
) -> OrderObservationImportResult:
    occurrence_counts = Counter(
        item.order_identity_fingerprint for item in imported_items
    )
    return OrderObservationImportResult(
        observation_batch_id=batch.observation_batch_id,
        automation_run_id=batch.automation_run_id,
        platform_name=batch.platform_name,
        requested_platform_trade_date=(
            batch.requested_platform_trade_date
        ),
        trade_day_status=batch.trade_day_status,
        capability_result=batch.capability_result,
        batch_status=batch_status,
        content_sha256=content_sha256,
        item_count=len(imported_items),
        transaction_amount_total=sum(
            (
                item.order_transaction_amount
                for item in imported_items
            ),
            Decimal("0"),
        ),
        occurrence_counts=dict(sorted(occurrence_counts.items())),
        mapping_version=mapping_version,
        replayed=replayed,
        items=imported_items,
    )


def _item_from_row(row: sqlite3.Row) -> ImportedOrderObservation:
    return ImportedOrderObservation(
        observation_item_id=str(row["observation_item_id"]),
        platform_name=str(row["platform_name"]),
        platform_trade_date=date.fromisoformat(
            str(row["platform_trade_date"])
        ),
        trade_day_status=str(row["trade_day_status"]),
        order_identity_fingerprint=str(
            row["order_identity_fingerprint"]
        ),
        occurrence_no=int(row["occurrence_no"]),
        order_created_at=_datetime(str(row["order_created_at"])),
        platform_product_name=str(row["platform_product_name"]),
        grade=str(row["grade"]),
        internal_sku=(
            str(row["internal_sku"])
            if row["internal_sku"] is not None
            else None
        ),
        mapping_status=ProductMappingStatus(str(row["mapping_status"])),
        mapping_version=str(row["mapping_version"]),
        order_qty=int(row["order_qty"]),
        order_transaction_amount=_decimal(
            row["order_transaction_amount"]
        ),
        observed_at=_datetime(str(row["observed_at"])),
        seller_operation_date=date.fromisoformat(
            str(row["seller_operation_date"])
        ),
        seller_phase=SellerPhase(str(row["seller_phase"])),
        raw_observation_sha256=str(row["raw_observation_sha256"]),
    )


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise OrderObservationError(
            "order_transaction_amount must use exact decimal input"
        )
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise OrderObservationError(
            "order_transaction_amount is invalid"
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise OrderObservationError(
            "order_transaction_amount must be finite and non-negative"
        )
    return amount


def _decimal_text(value: Decimal) -> str:
    amount = _decimal(value)
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OrderObservationError(
            f"{field_name} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return _as_utc(value, "datetime").isoformat()


def _datetime(value: str) -> datetime:
    try:
        return _as_utc(datetime.fromisoformat(value), "datetime")
    except ValueError as exc:
        raise OrderObservationError("stored datetime is invalid") from exc


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    encoded = _canonical_json(payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    encoded = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _safe_error_message(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[A-Za-z]:\\[^\s]+", "<local-path>", text)
    return text[:512]
