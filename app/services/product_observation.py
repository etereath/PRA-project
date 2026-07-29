from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.automation_models import AutomationRunClaim
from app.repositories.automation_repository import (
    AutomationLeaseLostError,
    validate_live_automation_claim_in_transaction,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.shadowbot_contract_primitives import canonical_positive_price
from app.services.operational_time import OperationalTimeService
from app.services.product_mapping import CompiledProductMappings
from app.services.shadowbot_listing_action_contract import (
    validate_listing_sync_snapshot,
)


ONLINE_PULSE = "ONLINE_PULSE"
LISTING_STATUS_SCAN = "LISTING_STATUS_SCAN"
PRODUCT_OBSERVATION_INPUT_SCHEMA_VERSION = "product-observation-input-1.0"
ALLOWED_SCAN_TYPES = frozenset({ONLINE_PULSE, LISTING_STATUS_SCAN})
ALLOWED_BATCH_STATUSES = frozenset(
    {"ACCEPTED", "PARTIAL", "UNAVAILABLE", "FAILED"}
)
ACCEPTING_RUN_STATUSES = frozenset({"RUNNING"})
EVIDENCE_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ProductObservationError(ValueError):
    """Raised when a scan observation violates the frozen input contract."""


@dataclass(frozen=True, slots=True)
class ProductObservationInput:
    platform_product_name: str
    grade: str
    observed_at: datetime
    observed_online: bool
    page_identity_key: str
    observed_price: Decimal | None = None
    observed_inventory: int | None = None
    evidence_sha256: str = ""


@dataclass(frozen=True, slots=True)
class ProductObservationBatchInput:
    observation_batch_id: str
    automation_run_id: str
    platform_name: str
    scan_type: str
    batch_status: str
    scan_started_at: datetime
    scan_completed_at: datetime
    requested_scope: dict[str, object]
    scope_complete: bool
    end_marker_verified: bool
    items: tuple[ProductObservationInput, ...] = ()
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class ProductObservationImportResult:
    observation_batch_id: str
    content_sha256: str
    item_count: int
    already_imported: bool


class ProductObservationImporter:
    """Append immutable scan facts without projecting listing state."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        *,
        mappings: CompiledProductMappings,
        operational_time: OperationalTimeService | None = None,
    ) -> None:
        self.repository = repository
        self.mappings = mappings
        self.operational_time = operational_time or OperationalTimeService()

    def import_batch(
        self,
        batch: ProductObservationBatchInput,
        *,
        claim: AutomationRunClaim,
        now: datetime,
    ) -> ProductObservationImportResult:
        normalized = self._normalize_and_validate(batch)
        current = _as_utc(now, "now")
        if claim.run.run_id != normalized.automation_run_id:
            raise ProductObservationError(
                "Automation Run claim does not match observation batch"
            )
        content_sha256 = _result_content_sha256(
            normalized,
            mapping_version=self.mappings.mapping_version,
        )
        batch_context = self.operational_time.classify(
            normalized.scan_completed_at
        )
        resolved_items = tuple(
            self._resolve_items(
                normalized.platform_name,
                normalized.items,
            )
        )
        for item in resolved_items:
            if item["time_policy_version"] != batch_context.time_policy_version:
                raise ProductObservationError(
                    "all observations in one batch must use the same "
                    "operational time policy"
                )

        with closing(self.repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                run = connection.execute(
                    """
                    SELECT job_type, run_status, platform_name,
                           time_policy_version
                    FROM automation_runs
                    WHERE run_id = ?
                    """,
                    (normalized.automation_run_id,),
                ).fetchone()
                if run is None:
                    raise ProductObservationError(
                        "automation_run_id does not exist"
                    )
                if str(run["platform_name"]) != normalized.platform_name:
                    raise ProductObservationError(
                        "observation platform does not match automation run"
                    )
                if str(run["job_type"]) != normalized.scan_type:
                    raise ProductObservationError(
                        "scan_type does not match automation run job_type"
                    )
                if (
                    str(run["time_policy_version"])
                    != batch_context.time_policy_version
                ):
                    raise ProductObservationError(
                        "observation time policy does not match automation run"
                    )

                existing = connection.execute(
                    """
                    SELECT automation_run_id, platform_name, scan_type,
                           content_sha256
                    FROM product_observation_batches
                    WHERE observation_batch_id = ?
                    """,
                    (normalized.observation_batch_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["automation_run_id"])
                        != normalized.automation_run_id
                        or str(existing["platform_name"])
                        != normalized.platform_name
                        or str(existing["scan_type"])
                        != normalized.scan_type
                        or str(existing["content_sha256"])
                        != content_sha256
                    ):
                        raise ProductObservationError(
                            "observation_batch_id already exists with "
                            "different envelope or content"
                        )
                    item_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*)
                            FROM product_observation_items
                            WHERE observation_batch_id = ?
                            """,
                            (normalized.observation_batch_id,),
                        ).fetchone()[0]
                    )
                    connection.commit()
                    return ProductObservationImportResult(
                        observation_batch_id=normalized.observation_batch_id,
                        content_sha256=content_sha256,
                        item_count=item_count,
                        already_imported=True,
                    )

                duplicate = connection.execute(
                    """
                    SELECT observation_batch_id
                    FROM product_observation_batches
                    WHERE automation_run_id = ?
                      AND content_sha256 = ?
                    ORDER BY created_at, observation_batch_id
                    LIMIT 1
                    """,
                    (normalized.automation_run_id, content_sha256),
                ).fetchone()
                if duplicate is not None:
                    canonical_batch_id = str(
                        duplicate["observation_batch_id"]
                    )
                    item_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*)
                            FROM product_observation_items
                            WHERE observation_batch_id = ?
                            """,
                            (canonical_batch_id,),
                        ).fetchone()[0]
                    )
                    connection.commit()
                    return ProductObservationImportResult(
                        observation_batch_id=canonical_batch_id,
                        content_sha256=content_sha256,
                        item_count=item_count,
                        already_imported=True,
                    )
                if str(run["run_status"]) not in ACCEPTING_RUN_STATUSES:
                    raise ProductObservationError(
                        "automation run is not accepting scan results"
                    )
                try:
                    validate_live_automation_claim_in_transaction(
                        connection,
                        claim,
                        now=current,
                    )
                except AutomationLeaseLostError as exc:
                    raise ProductObservationError(
                        "automation run lease is not live"
                    ) from exc
                conflicting_run_batch = connection.execute(
                    """
                    SELECT observation_batch_id
                    FROM product_observation_batches
                    WHERE automation_run_id = ?
                    ORDER BY created_at, observation_batch_id
                    LIMIT 1
                    """,
                    (normalized.automation_run_id,),
                ).fetchone()
                if conflicting_run_batch is not None:
                    raise ProductObservationError(
                        "automation run already has different observation "
                        "content"
                    )

                connection.execute(
                    """
                    INSERT INTO product_observation_batches(
                        observation_batch_id, automation_run_id,
                        platform_name, scan_type, batch_status,
                        scan_started_at, scan_completed_at,
                        requested_scope_json, scope_complete,
                        end_marker_verified, content_sha256,
                        time_policy_version, error_code, error_message,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized.observation_batch_id,
                        normalized.automation_run_id,
                        normalized.platform_name,
                        normalized.scan_type,
                        normalized.batch_status,
                        _datetime_text(normalized.scan_started_at),
                        _datetime_text(normalized.scan_completed_at),
                        json.dumps(
                            normalized.requested_scope,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        int(normalized.scope_complete),
                        int(normalized.end_marker_verified),
                        content_sha256,
                        batch_context.time_policy_version,
                        normalized.error_code,
                        normalized.error_message,
                        _datetime_text(current),
                    ),
                )
                for index, item in enumerate(resolved_items):
                    item_id = _observation_item_id(
                        normalized.observation_batch_id,
                        index,
                        item,
                    )
                    connection.execute(
                        """
                        INSERT INTO product_observation_items(
                            observation_item_id, observation_batch_id,
                            internal_sku, platform_product_name, grade,
                            observed_price, observed_inventory,
                            observed_online, observed_at,
                            platform_trade_date, seller_operation_date,
                            seller_phase, page_identity_key,
                            mapping_status, mapping_version,
                            evidence_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            normalized.observation_batch_id,
                            item["internal_sku"],
                            item["platform_product_name"],
                            item["grade"],
                            item["observed_price"],
                            item["observed_inventory"],
                            int(item["observed_online"]),
                            item["observed_at"],
                            item["platform_trade_date"],
                            item["seller_operation_date"],
                            item["seller_phase"],
                            item["page_identity_key"],
                            item["mapping_status"],
                            self.mappings.mapping_version,
                            item["evidence_sha256"],
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return ProductObservationImportResult(
            observation_batch_id=normalized.observation_batch_id,
            content_sha256=content_sha256,
            item_count=len(resolved_items),
            already_imported=False,
        )

    def _normalize_and_validate(
        self,
        batch: ProductObservationBatchInput,
    ) -> ProductObservationBatchInput:
        if not batch.observation_batch_id.strip():
            raise ProductObservationError(
                "observation_batch_id must not be blank"
            )
        if not batch.automation_run_id.strip():
            raise ProductObservationError(
                "automation_run_id must not be blank"
            )
        if not batch.platform_name.strip():
            raise ProductObservationError("platform_name must not be blank")
        scan_type = batch.scan_type.strip().upper()
        if scan_type not in ALLOWED_SCAN_TYPES:
            raise ProductObservationError(
                f"unsupported scan_type '{batch.scan_type}'"
            )
        batch_status = batch.batch_status.strip().upper()
        if batch_status not in ALLOWED_BATCH_STATUSES:
            raise ProductObservationError(
                f"unsupported batch_status '{batch.batch_status}'"
            )
        started_at = _as_utc(batch.scan_started_at, "scan_started_at")
        completed_at = _as_utc(
            batch.scan_completed_at,
            "scan_completed_at",
        )
        if completed_at < started_at:
            raise ProductObservationError(
                "scan_completed_at must not be earlier than scan_started_at"
            )
        error_code = batch.error_code.strip()
        error_message = batch.error_message.strip()
        _validate_batch_status(
            batch_status,
            scope_complete=bool(batch.scope_complete),
            end_marker_verified=bool(batch.end_marker_verified),
            has_items=bool(batch.items),
            error_code=error_code,
            error_message=error_message,
        )
        requested_scope = _normalize_requested_scope(
            scan_type,
            batch.requested_scope,
        )
        if scan_type == ONLINE_PULSE:
            if any(not item.observed_online for item in batch.items):
                raise ProductObservationError(
                    "ONLINE_PULSE accepts online positive observations only"
                )
        normalized_items = tuple(
            self._normalize_item(item)
            for item in batch.items
        )
        for item in normalized_items:
            if not started_at <= item.observed_at <= completed_at:
                raise ProductObservationError(
                    "item observed_at must fall within the scan interval"
                )
            if batch_status in {"ACCEPTED", "PARTIAL"} and (
                not EVIDENCE_SHA256_RE.fullmatch(item.evidence_sha256)
            ):
                raise ProductObservationError(
                    "accepted observations require evidence_sha256 in "
                    "sha256:<64 lowercase hex> format"
                )
        return ProductObservationBatchInput(
            observation_batch_id=batch.observation_batch_id.strip(),
            automation_run_id=batch.automation_run_id.strip(),
            platform_name=batch.platform_name.strip(),
            scan_type=scan_type,
            batch_status=batch_status,
            scan_started_at=started_at,
            scan_completed_at=completed_at,
            requested_scope=requested_scope,
            scope_complete=bool(batch.scope_complete),
            end_marker_verified=bool(batch.end_marker_verified),
            items=normalized_items,
            error_code=error_code,
            error_message=error_message,
        )

    def _normalize_item(
        self,
        item: ProductObservationInput,
    ) -> ProductObservationInput:
        name = item.platform_product_name.strip()
        grade = item.grade.strip()
        page_identity_key = item.page_identity_key.strip()
        if not name or not grade or not page_identity_key:
            raise ProductObservationError(
                "product name, grade and page_identity_key are required"
            )
        if item.observed_inventory is not None and item.observed_inventory < 0:
            raise ProductObservationError(
                "observed_inventory must not be negative"
            )
        observed_price = None
        if item.observed_price is not None:
            try:
                observed_price = Decimal(
                    canonical_positive_price(
                        item.observed_price,
                        require_canonical=True,
                        reject_float=True,
                    )
                )
            except (InvalidOperation, ValueError) as exc:
                raise ProductObservationError(
                    "observed_price must be a canonical positive decimal"
                ) from exc
        return ProductObservationInput(
            platform_product_name=name,
            grade=grade,
            observed_at=_as_utc(item.observed_at, "observed_at"),
            observed_online=bool(item.observed_online),
            page_identity_key=page_identity_key,
            observed_price=observed_price,
            observed_inventory=item.observed_inventory,
            evidence_sha256=item.evidence_sha256.strip(),
        )

    def _resolve_items(
        self,
        platform_name: str,
        items: Iterable[ProductObservationInput],
    ) -> Iterable[dict[str, object]]:
        for item in items:
            context = self.operational_time.classify(item.observed_at)
            resolution = self.mappings.resolve(
                platform_name=platform_name,
                platform_product_name=item.platform_product_name,
                grade=item.grade,
                observed_at=item.observed_at,
            )
            yield {
                "internal_sku": resolution.internal_sku,
                "platform_product_name": item.platform_product_name,
                "grade": item.grade,
                "observed_price": (
                    str(item.observed_price)
                    if item.observed_price is not None
                    else None
                ),
                "observed_inventory": item.observed_inventory,
                "observed_online": item.observed_online,
                "observed_at": _datetime_text(context.observed_at),
                "platform_trade_date": context.platform_trade_date.isoformat(),
                "seller_operation_date": (
                    context.seller_operation_date.isoformat()
                ),
                "seller_phase": context.seller_phase.value,
                "time_policy_version": context.time_policy_version,
                "page_identity_key": item.page_identity_key,
                "mapping_status": resolution.mapping_status.value,
                "evidence_sha256": item.evidence_sha256,
            }


def product_observation_batch_from_payload(
    payload: dict[str, object],
) -> ProductObservationBatchInput:
    """Parse the strict JSON boundary used by future scanner adapters."""

    if not isinstance(payload, dict):
        raise ProductObservationError(
            "product observation payload must be an object"
        )
    allowed = {
        "schema_version",
        "observation_batch_id",
        "automation_run_id",
        "platform_name",
        "scan_type",
        "batch_status",
        "scan_started_at",
        "scan_completed_at",
        "requested_scope",
        "scope_complete",
        "end_marker_verified",
        "items",
        "error_code",
        "error_message",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ProductObservationError(
            "unexpected product observation fields: "
            + ", ".join(unexpected)
        )
    if (
        payload.get("schema_version")
        != PRODUCT_OBSERVATION_INPUT_SCHEMA_VERSION
    ):
        raise ProductObservationError(
            "unsupported product observation schema_version"
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ProductObservationError("items must be an array")
    requested_scope = payload.get("requested_scope")
    if not isinstance(requested_scope, dict):
        raise ProductObservationError(
            "requested_scope must be an object"
        )
    if type(payload.get("scope_complete")) is not bool:
        raise ProductObservationError("scope_complete must be a boolean")
    if type(payload.get("end_marker_verified")) is not bool:
        raise ProductObservationError(
            "end_marker_verified must be a boolean"
        )
    return ProductObservationBatchInput(
        observation_batch_id=str(
            payload.get("observation_batch_id") or ""
        ),
        automation_run_id=str(payload.get("automation_run_id") or ""),
        platform_name=str(payload.get("platform_name") or ""),
        scan_type=str(payload.get("scan_type") or ""),
        batch_status=str(payload.get("batch_status") or ""),
        scan_started_at=_parse_datetime(
            payload.get("scan_started_at"),
            "scan_started_at",
        ),
        scan_completed_at=_parse_datetime(
            payload.get("scan_completed_at"),
            "scan_completed_at",
        ),
        requested_scope=dict(requested_scope),
        scope_complete=payload["scope_complete"],
        end_marker_verified=payload["end_marker_verified"],
        items=tuple(
            _observation_item_from_payload(item, index)
            for index, item in enumerate(raw_items, start=1)
        ),
        error_code=str(payload.get("error_code") or ""),
        error_message=str(payload.get("error_message") or ""),
    )


def listing_snapshot_to_observation_batch(
    snapshot: dict[str, object],
    *,
    automation_run_id: str,
) -> ProductObservationBatchInput:
    """Adapt one validated Task 13 two-page snapshot to the v14 input."""

    validate_listing_sync_snapshot(snapshot)
    items: list[ProductObservationInput] = []
    for raw_item in snapshot["items"]:
        item = dict(raw_item)
        for page, observed_online in (
            ("online", True),
            ("waiting", False),
        ):
            if int(item[f"{page}_occurrences"]) == 0:
                continue
            evidence_payload = {
                "snapshot_id": snapshot["snapshot_id"],
                "snapshot_item_id": item["snapshot_item_id"],
                "page": page,
                "row_identities": item[f"{page}_row_identities"],
                "evidence_manifest_sha256": snapshot[
                    "evidence_manifest_sha256"
                ],
            }
            evidence_sha256 = "sha256:" + hashlib.sha256(
                json.dumps(
                    evidence_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            items.append(
                ProductObservationInput(
                    platform_product_name=str(item["product_name"]),
                    grade=str(item["grade"]),
                    observed_at=_parse_datetime(
                        item[f"{page}_observed_at"],
                        f"{page}_observed_at",
                    ),
                    observed_online=observed_online,
                    page_identity_key=str(item["page_identity_key"]),
                    observed_price=Decimal(
                        str(item[f"{page}_observed_price"])
                    ),
                    observed_inventory=int(
                        item[f"{page}_observed_inventory"]
                    ),
                    evidence_sha256=evidence_sha256,
                )
            )

    snapshot_complete = bool(snapshot["snapshot_complete"])
    return ProductObservationBatchInput(
        observation_batch_id=(
            f"product-observation-{snapshot['snapshot_id']}"
        ),
        automation_run_id=automation_run_id,
        platform_name=str(snapshot["platform_name"]),
        scan_type=LISTING_STATUS_SCAN,
        batch_status="ACCEPTED" if snapshot_complete else "FAILED",
        scan_started_at=_parse_datetime(
            snapshot["scan_started_at"],
            "scan_started_at",
        ),
        scan_completed_at=_parse_datetime(
            snapshot["scan_completed_at"],
            "scan_completed_at",
        ),
        requested_scope={
            "child_type": LISTING_STATUS_SCAN,
            "pages": ["online", "waiting"],
            "source_snapshot_id": snapshot["snapshot_id"],
        },
        scope_complete=snapshot_complete,
        end_marker_verified=bool(
            snapshot["online_end_marker_verified"]
            and snapshot["waiting_end_marker_verified"]
        ),
        items=tuple(items),
        error_code=str(snapshot.get("error_code") or ""),
        error_message=(
            ""
            if snapshot_complete
            else "Task 13 listing snapshot was incomplete"
        ),
    )


def _result_content_sha256(
    batch: ProductObservationBatchInput,
    *,
    mapping_version: str,
) -> str:
    items = [
        {
            "platform_product_name": item.platform_product_name,
            "grade": item.grade,
            "observed_at": _datetime_text(item.observed_at),
            "observed_online": item.observed_online,
            "page_identity_key": item.page_identity_key,
            "observed_price": (
                str(item.observed_price)
                if item.observed_price is not None
                else None
            ),
            "observed_inventory": item.observed_inventory,
            "evidence_sha256": item.evidence_sha256,
        }
        for item in batch.items
    ]
    items.sort(
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    payload = {
        "platform_name": batch.platform_name,
        "mapping_version": mapping_version,
        "scan_type": batch.scan_type,
        "batch_status": batch.batch_status,
        "scan_started_at": _datetime_text(batch.scan_started_at),
        "scan_completed_at": _datetime_text(batch.scan_completed_at),
        "requested_scope": batch.requested_scope,
        "scope_complete": batch.scope_complete,
        "end_marker_verified": batch.end_marker_verified,
        "error_code": batch.error_code,
        "error_message": batch.error_message,
        "items": items,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_batch_status(
    batch_status: str,
    *,
    scope_complete: bool,
    end_marker_verified: bool,
    has_items: bool,
    error_code: str,
    error_message: str,
) -> None:
    if batch_status == "ACCEPTED":
        if not scope_complete or not end_marker_verified:
            raise ProductObservationError(
                "ACCEPTED batches must prove scope completeness and end marker"
            )
        if error_code or error_message:
            raise ProductObservationError(
                "ACCEPTED batches must not contain error fields"
            )
        return
    if batch_status == "PARTIAL":
        if not error_code:
            raise ProductObservationError(
                "PARTIAL batches must provide error_code"
            )
        return
    if has_items:
        raise ProductObservationError(
            f"{batch_status} batches must not contain observations"
        )
    if scope_complete:
        raise ProductObservationError(
            f"{batch_status} batches must not mark scope_complete"
        )
    if not error_code:
        raise ProductObservationError(
            f"{batch_status} batches must provide error_code"
        )


def _normalize_requested_scope(
    scan_type: str,
    requested_scope: dict[str, object],
) -> dict[str, object]:
    pages = requested_scope.get("pages")
    if not isinstance(pages, list) or any(
        not isinstance(page, str) for page in pages
    ):
        raise ProductObservationError(
            "requested_scope.pages must be an array of page names"
        )
    if scan_type == ONLINE_PULSE and pages != ["online"]:
        raise ProductObservationError(
            "ONLINE_PULSE requested_scope.pages must be exactly [online]"
        )
    if scan_type == LISTING_STATUS_SCAN and (
        len(pages) != 2 or set(pages) != {"online", "waiting"}
    ):
        raise ProductObservationError(
            "LISTING_STATUS_SCAN must cover online and waiting exactly once"
        )
    normalized_scope = dict(requested_scope)
    normalized_scope["pages"] = (
        ["online"]
        if scan_type == ONLINE_PULSE
        else ["online", "waiting"]
    )
    return normalized_scope


def _observation_item_id(
    batch_id: str,
    index: int,
    item: dict[str, object],
) -> str:
    payload = json.dumps(
        {"batch_id": batch_id, "index": index, "item": item},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"product-observation-{hashlib.sha256(payload).hexdigest()[:24]}"


def _observation_item_from_payload(
    value: object,
    row_number: int,
) -> ProductObservationInput:
    if not isinstance(value, dict):
        raise ProductObservationError(
            f"items[{row_number}] must be an object"
        )
    allowed = {
        "platform_product_name",
        "grade",
        "observed_at",
        "observed_online",
        "page_identity_key",
        "observed_price",
        "observed_inventory",
        "evidence_sha256",
    }
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ProductObservationError(
            f"items[{row_number}] has unexpected fields: "
            + ", ".join(unexpected)
        )
    if type(value.get("observed_online")) is not bool:
        raise ProductObservationError(
            f"items[{row_number}].observed_online must be a boolean"
        )
    observed_price = value.get("observed_price")
    if isinstance(observed_price, (bool, float)):
        raise ProductObservationError(
            f"items[{row_number}].observed_price must use an exact string"
        )
    try:
        parsed_price = (
            Decimal(str(observed_price))
            if observed_price not in (None, "")
            else None
        )
    except (InvalidOperation, ValueError) as exc:
        raise ProductObservationError(
            f"items[{row_number}].observed_price is invalid"
        ) from exc
    inventory = value.get("observed_inventory")
    if inventory is not None and type(inventory) is not int:
        raise ProductObservationError(
            f"items[{row_number}].observed_inventory must be an integer"
        )
    return ProductObservationInput(
        platform_product_name=str(
            value.get("platform_product_name") or ""
        ),
        grade=str(value.get("grade") or ""),
        observed_at=_parse_datetime(
            value.get("observed_at"),
            f"items[{row_number}].observed_at",
        ),
        observed_online=value["observed_online"],
        page_identity_key=str(value.get("page_identity_key") or ""),
        observed_price=parsed_price,
        observed_inventory=inventory,
        evidence_sha256=str(value.get("evidence_sha256") or ""),
    )


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProductObservationError(
            f"{field_name} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ProductObservationError(
                f"{field_name} must be an ISO datetime"
            ) from exc
    return _as_utc(parsed, field_name)


def _datetime_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
