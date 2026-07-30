from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable

from app.automation_models import AutomationRunClaim
from app.listing_observation_identity import (
    ListingObservationSourceIdentity,
    listing_observation_source_identities,
    listing_observation_source_identity_payload,
    listing_observation_source_identity_sha256,
)
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
RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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
    mapping_version: str
    already_imported: bool


@dataclass(frozen=True, slots=True)
class _ListingSnapshotSourceValidation:
    mapping_identity_sha256: str
    source_identities: tuple[ListingObservationSourceIdentity, ...]


class ProductObservationImporter:
    """Append immutable scan facts without projecting listing state."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        *,
        mappings: CompiledProductMappings,
        operational_time: OperationalTimeService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.mappings = mappings
        self.operational_time = operational_time or OperationalTimeService()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def import_batch(
        self,
        batch: ProductObservationBatchInput,
        *,
        claim: AutomationRunClaim,
    ) -> ProductObservationImportResult:
        normalized = self._normalize_and_validate(batch)
        if claim.run.run_id != normalized.automation_run_id:
            raise ProductObservationError(
                "Automation Run claim does not match observation batch"
            )
        batch_context = self.operational_time.classify(
            normalized.scan_completed_at
        )
        batch_start_context = self.operational_time.classify(
            normalized.scan_started_at
        )
        item_contexts = tuple(
            self.operational_time.classify(item.observed_at)
            for item in normalized.items
        )
        for context in item_contexts:
            if context.time_policy_version != batch_context.time_policy_version:
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
                           platform_trade_date, time_policy_version,
                           input_manifest_sha256
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
                    or str(run["time_policy_version"])
                    != batch_start_context.time_policy_version
                ):
                    raise ProductObservationError(
                        "observation time policy does not match automation run"
                    )
                run_trade_date = str(run["platform_trade_date"])
                if (
                    batch_start_context.platform_trade_date.isoformat()
                    != run_trade_date
                    or batch_context.platform_trade_date.isoformat()
                    != run_trade_date
                    or any(
                        context.platform_trade_date.isoformat()
                        != run_trade_date
                        for context in item_contexts
                    )
                ):
                    raise ProductObservationError(
                        "observation platform_trade_date does not match "
                        "automation run"
                    )
                source_validation = None
                if normalized.scan_type == LISTING_STATUS_SCAN:
                    source_validation = _validate_listing_snapshot_source(
                        connection,
                        normalized,
                        run=run,
                    )
                    normalized = replace(
                        normalized,
                        requested_scope={
                            **normalized.requested_scope,
                            "validated_mapping_identity_sha256": (
                                source_validation.mapping_identity_sha256
                            ),
                        },
                    )

                replay = _find_existing_observation_replay(
                    connection,
                    normalized,
                    operational_time=self.operational_time,
                    source_validation=source_validation,
                )
                if replay is not None:
                    connection.commit()
                    return replay
                if str(run["run_status"]) not in ACCEPTING_RUN_STATUSES:
                    raise ProductObservationError(
                        "automation run is not accepting scan results"
                    )
                current = _as_utc(self.clock(), "clock")
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

                resolved_items = tuple(
                    self._resolve_items(
                        normalized.platform_name,
                        normalized.items,
                    )
                )
                if source_validation is not None:
                    _validate_resolved_listing_identities(
                        source_identities=(
                            source_validation.source_identities
                        ),
                        resolved_items=resolved_items,
                    )
                accepted_mapping_version = self.mappings.mapping_version
                normalized = replace(
                    normalized,
                    requested_scope={
                        **normalized.requested_scope,
                        "accepted_mapping_version": accepted_mapping_version,
                    },
                )
                content_sha256 = _result_content_sha256(
                    normalized,
                    mapping_version=accepted_mapping_version,
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
                            accepted_mapping_version,
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
            mapping_version=accepted_mapping_version,
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
                "candidate_internal_skus": (
                    resolution.candidate_internal_skus
                ),
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
    source_manifest_sha256: str,
    source_result_sha256: str,
    operational_time: OperationalTimeService | None = None,
) -> ProductObservationBatchInput:
    """Adapt one validated Task 13 two-page snapshot to the v14 input."""

    validate_listing_sync_snapshot(snapshot)
    manifest_sha256 = source_manifest_sha256.strip().lower()
    result_sha256 = source_result_sha256.strip().lower()
    if not EVIDENCE_SHA256_RE.fullmatch(manifest_sha256):
        raise ProductObservationError(
            "source_manifest_sha256 must be a prefixed SHA-256"
        )
    if not RAW_SHA256_RE.fullmatch(result_sha256):
        raise ProductObservationError(
            "source_result_sha256 must be an unprefixed SHA-256"
        )
    items, source_identities = _listing_snapshot_observation_bundle(
        snapshot_id=str(snapshot["snapshot_id"]),
        evidence_manifest_sha256=str(
            snapshot["evidence_manifest_sha256"]
        ),
        snapshot_items=tuple(dict(item) for item in snapshot["items"]),
    )
    time_service = operational_time or OperationalTimeService()
    source_trade_date = time_service.classify(
        _parse_datetime(
            snapshot["scan_completed_at"],
            "scan_completed_at",
        )
    ).platform_trade_date.isoformat()
    source_conversion_sha256 = _listing_source_conversion_sha256(
        snapshot_id=str(snapshot["snapshot_id"]),
        manifest_sha256=manifest_sha256,
        result_sha256=result_sha256,
        scan_started_at=_parse_datetime(
            snapshot["scan_started_at"],
            "scan_started_at",
        ),
        scan_completed_at=_parse_datetime(
            snapshot["scan_completed_at"],
            "scan_completed_at",
        ),
        items=items,
        source_identities=source_identities,
    )
    source_mapping_identity_sha256 = (
        listing_observation_source_identity_sha256(source_identities)
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
            "source_manifest_sha256": manifest_sha256,
            "source_result_sha256": result_sha256,
            "source_platform_trade_date": source_trade_date,
            "source_conversion_sha256": source_conversion_sha256,
            "source_mapping_identity_sha256": (
                source_mapping_identity_sha256
            ),
        },
        scope_complete=snapshot_complete,
        end_marker_verified=bool(
            snapshot["online_end_marker_verified"]
            and snapshot["waiting_end_marker_verified"]
        ),
        items=items,
        error_code=str(snapshot.get("error_code") or ""),
        error_message=(
            ""
            if snapshot_complete
            else "Task 13 listing snapshot was incomplete"
        ),
    )


def _validate_listing_snapshot_source(
    connection,
    batch: ProductObservationBatchInput,
    *,
    run,
) -> _ListingSnapshotSourceValidation:
    scope = batch.requested_scope
    snapshot_id = str(scope.get("source_snapshot_id") or "").strip()
    manifest_sha256 = str(
        scope.get("source_manifest_sha256") or ""
    ).strip().lower()
    result_sha256 = str(
        scope.get("source_result_sha256") or ""
    ).strip().lower()
    source_trade_date = str(
        scope.get("source_platform_trade_date") or ""
    ).strip()
    conversion_sha256 = str(
        scope.get("source_conversion_sha256") or ""
    ).strip().lower()
    mapping_identity_sha256 = str(
        scope.get("source_mapping_identity_sha256") or ""
    ).strip().lower()
    if (
        not snapshot_id
        or not EVIDENCE_SHA256_RE.fullmatch(manifest_sha256)
        or not RAW_SHA256_RE.fullmatch(result_sha256)
        or not EVIDENCE_SHA256_RE.fullmatch(conversion_sha256)
        or not EVIDENCE_SHA256_RE.fullmatch(mapping_identity_sha256)
    ):
        raise ProductObservationError(
            "LISTING_STATUS_SCAN requires immutable snapshot source binding"
        )
    source = connection.execute(
        """
        SELECT snapshots.*, batches.manifest_sha256,
               receipts.result_sha256
        FROM listing_sync_snapshots AS snapshots
        INNER JOIN shadowbot_listing_action_batches AS batches
            ON batches.batch_id = snapshots.batch_id
        INNER JOIN shadowbot_listing_result_receipts AS receipts
            ON receipts.result_id = snapshots.result_id
           AND receipts.batch_id = snapshots.batch_id
        WHERE snapshots.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if source is None:
        raise ProductObservationError(
            "LISTING_STATUS_SCAN source snapshot does not exist"
        )
    run_manifest = str(run["input_manifest_sha256"] or "").strip().lower()
    if (
        str(source["platform_name"]) != batch.platform_name
        or str(source["status"]) != "VERIFIED"
        or int(source["snapshot_complete"]) != 1
        or manifest_sha256 != str(source["manifest_sha256"]).lower()
        or manifest_sha256 != run_manifest
        or result_sha256 != str(source["result_sha256"]).lower()
        or source_trade_date != str(run["platform_trade_date"])
        or batch.scan_started_at
        != _parse_datetime(source["scan_started_at"], "scan_started_at")
        or batch.scan_completed_at
        != _parse_datetime(source["scan_completed_at"], "scan_completed_at")
    ):
        raise ProductObservationError(
            "LISTING_STATUS_SCAN source snapshot envelope does not match run"
        )
    source_item_rows = connection.execute(
        """
        SELECT *
        FROM listing_sync_snapshot_items
        WHERE snapshot_id = ?
        ORDER BY snapshot_item_id
        """,
        (snapshot_id,),
    ).fetchall()
    source_items = tuple(
        {
            **dict(row),
            "affected_internal_skus": json.loads(
                str(row["affected_internal_skus_json"])
            ),
            "online_row_identities": json.loads(
                str(row["online_row_identities_json"])
            ),
            "waiting_row_identities": json.loads(
                str(row["waiting_row_identities_json"])
            ),
        }
        for row in source_item_rows
    )
    expected_items, source_identities = _listing_snapshot_observation_bundle(
        snapshot_id=snapshot_id,
        evidence_manifest_sha256=str(
            source["evidence_manifest_sha256"]
        ),
        snapshot_items=source_items,
    )
    expected_conversion_sha256 = _listing_source_conversion_sha256(
        snapshot_id=snapshot_id,
        manifest_sha256=manifest_sha256,
        result_sha256=result_sha256,
        scan_started_at=batch.scan_started_at,
        scan_completed_at=batch.scan_completed_at,
        items=expected_items,
        source_identities=source_identities,
    )
    expected_mapping_identity_sha256 = (
        listing_observation_source_identity_sha256(source_identities)
    )
    if (
        conversion_sha256 != expected_conversion_sha256
        or mapping_identity_sha256 != expected_mapping_identity_sha256
        or _observation_inputs_payload(batch.items)
        != _observation_inputs_payload(expected_items)
    ):
        raise ProductObservationError(
            "LISTING_STATUS_SCAN observations are not the canonical "
            "snapshot conversion"
        )
    return _ListingSnapshotSourceValidation(
        mapping_identity_sha256=expected_mapping_identity_sha256,
        source_identities=source_identities,
    )


def _find_existing_observation_replay(
    connection,
    batch: ProductObservationBatchInput,
    *,
    operational_time: OperationalTimeService,
    source_validation: _ListingSnapshotSourceValidation | None,
) -> ProductObservationImportResult | None:
    same_id = connection.execute(
        """
        SELECT *
        FROM product_observation_batches
        WHERE observation_batch_id = ?
        """,
        (batch.observation_batch_id,),
    ).fetchone()
    if same_id is not None:
        replay = _existing_observation_replay(
            connection,
            same_id,
            batch,
            operational_time=operational_time,
            source_validation=source_validation,
        )
        if replay is None:
            raise ProductObservationError(
                "observation_batch_id already exists with different "
                "envelope or content"
            )
        return replay

    candidates = connection.execute(
        """
        SELECT *
        FROM product_observation_batches
        WHERE automation_run_id = ?
        ORDER BY created_at, observation_batch_id
        """,
        (batch.automation_run_id,),
    ).fetchall()
    for candidate in candidates:
        replay = _existing_observation_replay(
            connection,
            candidate,
            batch,
            operational_time=operational_time,
            source_validation=source_validation,
        )
        if replay is not None:
            return replay
    return None


def _existing_observation_replay(
    connection,
    stored_batch,
    incoming: ProductObservationBatchInput,
    *,
    operational_time: OperationalTimeService,
    source_validation: _ListingSnapshotSourceValidation | None,
) -> ProductObservationImportResult | None:
    scalar_fields = {
        "automation_run_id": incoming.automation_run_id,
        "platform_name": incoming.platform_name,
        "scan_type": incoming.scan_type,
        "batch_status": incoming.batch_status,
        "scan_started_at": _datetime_text(incoming.scan_started_at),
        "scan_completed_at": _datetime_text(incoming.scan_completed_at),
        "scope_complete": int(incoming.scope_complete),
        "end_marker_verified": int(incoming.end_marker_verified),
        "time_policy_version": operational_time.classify(
            incoming.scan_completed_at
        ).time_policy_version,
        "error_code": incoming.error_code,
        "error_message": incoming.error_message,
    }
    if any(
        str(stored_batch[field]) != str(expected)
        for field, expected in scalar_fields.items()
    ):
        return None

    try:
        stored_scope = json.loads(str(stored_batch["requested_scope_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(stored_scope, dict):
        return None
    comparable_scope = dict(stored_scope)
    comparable_scope.pop("accepted_mapping_version", None)
    if comparable_scope != incoming.requested_scope:
        return None

    stored_items = connection.execute(
        """
        SELECT *
        FROM product_observation_items
        WHERE observation_batch_id = ?
        ORDER BY observation_item_id
        """,
        (stored_batch["observation_batch_id"],),
    ).fetchall()
    if _stored_observation_fact_payload(stored_items) != (
        _expected_observation_fact_payload(
            incoming.items,
            operational_time=operational_time,
        )
    ):
        return None
    stored_item_mapping_versions = {
        str(row["mapping_version"]) for row in stored_items
    }
    mapping_version = str(
        stored_scope.get("accepted_mapping_version") or ""
    ).strip()
    if not mapping_version and len(stored_item_mapping_versions) == 1:
        mapping_version = next(iter(stored_item_mapping_versions))
    if not mapping_version:
        return None
    if stored_item_mapping_versions and (
        stored_item_mapping_versions != {mapping_version}
    ):
        return None
    if source_validation is not None and not (
        _stored_listing_identities_match_source(
            stored_items,
            source_validation.source_identities,
        )
    ):
        return None

    stored_content_sha256 = str(stored_batch["content_sha256"])
    canonical_stored_batch = replace(
        incoming,
        observation_batch_id=str(stored_batch["observation_batch_id"]),
        requested_scope=stored_scope,
    )
    if (
        _result_content_sha256(
            canonical_stored_batch,
            mapping_version=mapping_version,
        )
        != stored_content_sha256
    ):
        return None
    return ProductObservationImportResult(
        observation_batch_id=str(stored_batch["observation_batch_id"]),
        content_sha256=stored_content_sha256,
        item_count=len(stored_items),
        mapping_version=mapping_version,
        already_imported=True,
    )


def _expected_observation_fact_payload(
    items: Iterable[ProductObservationInput],
    *,
    operational_time: OperationalTimeService,
) -> list[dict[str, object]]:
    payload = []
    for item in items:
        context = operational_time.classify(item.observed_at)
        payload.append(
            {
                "platform_product_name": item.platform_product_name,
                "grade": item.grade,
                "observed_at": _datetime_text(context.observed_at),
                "observed_online": item.observed_online,
                "page_identity_key": item.page_identity_key,
                "observed_price": (
                    str(item.observed_price)
                    if item.observed_price is not None
                    else None
                ),
                "observed_inventory": item.observed_inventory,
                "platform_trade_date": (
                    context.platform_trade_date.isoformat()
                ),
                "seller_operation_date": (
                    context.seller_operation_date.isoformat()
                ),
                "seller_phase": context.seller_phase.value,
                "evidence_sha256": item.evidence_sha256,
            }
        )
    return _sort_json_payload(payload)


def _stored_observation_fact_payload(
    rows: Iterable[object],
) -> list[dict[str, object]]:
    payload = [
        {
            "platform_product_name": str(row["platform_product_name"]),
            "grade": str(row["grade"]),
            "observed_at": str(row["observed_at"]),
            "observed_online": bool(row["observed_online"]),
            "page_identity_key": str(row["page_identity_key"]),
            "observed_price": (
                str(row["observed_price"])
                if row["observed_price"] is not None
                else None
            ),
            "observed_inventory": row["observed_inventory"],
            "platform_trade_date": str(row["platform_trade_date"]),
            "seller_operation_date": str(row["seller_operation_date"]),
            "seller_phase": str(row["seller_phase"]),
            "evidence_sha256": str(row["evidence_sha256"]),
        }
        for row in rows
    ]
    return _sort_json_payload(payload)


def _sort_json_payload(
    payload: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    return sorted(
        payload,
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _stored_listing_identities_match_source(
    stored_items: Iterable[object],
    source_identities: Iterable[ListingObservationSourceIdentity],
) -> bool:
    stored_items = tuple(stored_items)
    expected = {
        identity.evidence_sha256: identity for identity in source_identities
    }
    stored_by_evidence = {
        str(item["evidence_sha256"]): item for item in stored_items
    }
    if len(stored_by_evidence) != len(stored_items):
        return False
    if set(stored_by_evidence) != set(expected):
        return False
    for evidence_sha256, identity in expected.items():
        item = stored_by_evidence[evidence_sha256]
        stored_sku = str(item["internal_sku"] or "").strip() or None
        if (
            stored_sku != identity.internal_sku
            or str(item["mapping_status"]) != identity.mapping_status.value
        ):
            return False
    return True


def _listing_snapshot_observation_bundle(
    *,
    snapshot_id: str,
    evidence_manifest_sha256: str,
    snapshot_items: Iterable[dict[str, object]],
) -> tuple[
    tuple[ProductObservationInput, ...],
    tuple[ListingObservationSourceIdentity, ...],
]:
    source_items = tuple(snapshot_items)
    source_identities = listing_observation_source_identities(
        snapshot_id=snapshot_id,
        evidence_manifest_sha256=evidence_manifest_sha256,
        snapshot_items=source_items,
    )
    identity_by_item_and_page = {
        (identity.snapshot_item_id, identity.page): identity
        for identity in source_identities
    }
    items: list[ProductObservationInput] = []
    for item in source_items:
        for page, observed_online in (
            ("online", True),
            ("waiting", False),
        ):
            if int(item[f"{page}_occurrences"]) == 0:
                continue
            identity = identity_by_item_and_page[
                (str(item["snapshot_item_id"]), page)
            ]
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
                    evidence_sha256=identity.evidence_sha256,
                )
            )
    return tuple(items), source_identities


def _listing_source_conversion_sha256(
    *,
    snapshot_id: str,
    manifest_sha256: str,
    result_sha256: str,
    scan_started_at: datetime,
    scan_completed_at: datetime,
    items: Iterable[ProductObservationInput],
    source_identities: Iterable[ListingObservationSourceIdentity],
) -> str:
    payload = {
        "snapshot_id": snapshot_id,
        "manifest_sha256": manifest_sha256,
        "result_sha256": result_sha256,
        "scan_started_at": _datetime_text(scan_started_at),
        "scan_completed_at": _datetime_text(scan_completed_at),
        "items": _observation_inputs_payload(items),
        "source_mapping_identities": (
            listing_observation_source_identity_payload(source_identities)
        ),
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_resolved_listing_identities(
    *,
    source_identities: Iterable[ListingObservationSourceIdentity],
    resolved_items: Iterable[dict[str, object]],
) -> None:
    expected = {
        identity.evidence_sha256: identity for identity in source_identities
    }
    resolved_by_evidence: dict[str, dict[str, object]] = {}
    for item in resolved_items:
        evidence_sha256 = str(item["evidence_sha256"])
        if evidence_sha256 in resolved_by_evidence:
            raise ProductObservationError(
                "LISTING_STATUS_SCAN resolved evidence identity is duplicated"
            )
        resolved_by_evidence[evidence_sha256] = item
    if set(resolved_by_evidence) != set(expected):
        raise ProductObservationError(
            "LISTING_STATUS_SCAN resolved mapping identities do not match "
            "source snapshot"
        )
    for evidence_sha256, identity in expected.items():
        resolved = resolved_by_evidence[evidence_sha256]
        resolved_sku = (
            str(resolved.get("internal_sku") or "").strip() or None
        )
        resolved_candidates = tuple(
            sorted(
                {
                    str(candidate or "").strip()
                    for candidate in (
                        resolved.get("candidate_internal_skus") or ()
                    )
                    if str(candidate or "").strip()
                }
            )
        )
        if (
            str(resolved["mapping_status"])
            != identity.mapping_status.value
            or resolved_sku != identity.internal_sku
            or resolved_candidates != identity.candidate_internal_skus
        ):
            raise ProductObservationError(
                "LISTING_STATUS_SCAN mapping identity drifted from source "
                f"snapshot item {identity.snapshot_item_id}"
            )


def _observation_inputs_payload(
    items: Iterable[ProductObservationInput],
) -> list[dict[str, object]]:
    payload = [
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
        for item in items
    ]
    payload.sort(
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return payload


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
    reserved_fields = {
        "accepted_mapping_version",
        "validated_mapping_identity_sha256",
    }
    supplied_reserved_fields = sorted(reserved_fields & set(requested_scope))
    if supplied_reserved_fields:
        raise ProductObservationError(
            "requested_scope contains importer-reserved fields: "
            + ", ".join(supplied_reserved_fields)
        )
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
