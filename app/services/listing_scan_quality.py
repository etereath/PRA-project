from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from app.enums import ProductMappingStatus
from app.repositories.master_data_repository import RuntimeMasterDataError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.product_observation import (
    ProductObservationBatchInput,
    ProductObservationInput,
    _result_content_sha256,
)
from app.services.runtime_master_data import (
    RuntimeMappingSnapshot,
    RuntimeMasterDataProvider,
)


LISTING_SCAN_QUALIFICATION_SCHEMA_VERSION = (
    "listing-scan-qualification-1.0"
)
LISTING_SCAN_PROVIDER = "SHADOWBOT_SYNC_STATUS"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ListingScanQuality:
    """Structured fact qualification with independent delivery health."""

    schema_version: str
    operating_fact_qualified: bool
    fact_reason_codes: tuple[str, ...]
    delivery_archive_healthy: bool
    delivery_reason_codes: tuple[str, ...]
    platform_name: str
    account_id: str
    internal_sku: str
    platform_product_identity_digest: str
    authority_mode: str
    authority_generation: int
    mapping_snapshot_sha256: str
    mapping_version: str
    provider: str
    observation_type: str
    source_run_id: str
    observation_batch_id: str
    source_snapshot_id: str
    source_manifest_sha256: str
    source_result_sha256: str
    observation_content_sha256: str
    qualification_sha256: str
    observed_at: str
    scan_completed_at: str
    fresh_until: str
    scope_complete: bool
    end_marker_verified: bool
    observed_online: bool | None = None
    observed_price: str | None = None
    observed_inventory: int | None = None
    active_session_verified: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Candidate:
    quality: ListingScanQuality
    completed_at: datetime
    conflict_identity: tuple[str, ...]


class ListingScanQualityService:
    """Select the newest unique qualified listing fact for one SKU."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        *,
        configured_account_id: str,
        max_age: timedelta = timedelta(minutes=30),
        clock: Callable[[], datetime] | None = None,
        master_data: RuntimeMasterDataProvider | None = None,
    ) -> None:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        self.repository = repository
        self.configured_account_id = str(configured_account_id or "").strip()
        self.max_age = max_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.master_data = master_data or RuntimeMasterDataProvider(
            repository,
            configured_account_id=self.configured_account_id,
        )

    def latest(
        self,
        *,
        platform_name: str,
        internal_sku: str,
    ) -> ListingScanQuality:
        platform = str(platform_name or "").strip()
        sku = str(internal_sku or "").strip().upper()
        account_id = self.configured_account_id
        if not platform or not account_id or not sku:
            return _empty_quality(
                platform_name=platform,
                account_id=account_id,
                internal_sku=sku,
                fact_reason_codes=("INVALID_QUALIFICATION_SCOPE",),
            )
        now = _as_utc(self.clock())
        try:
            mapping_snapshot = self.master_data.mapping_snapshot(
                account_id=account_id
            )
        except (RuntimeMasterDataError, ValueError):
            return _empty_quality(
                platform_name=platform,
                account_id=account_id,
                internal_sku=sku,
                fact_reason_codes=("MAPPING_AUTHORITY_UNAVAILABLE",),
            )
        current_records = tuple(
            record
            for record in mapping_snapshot.mappings.records
            if record.platform_name == platform
            and record.account_id == account_id
            and record.internal_sku == sku
            and record.mapping_status is ProductMappingStatus.VERIFIED
            and record.is_effective_at(now)
        )
        identity_digests = {
            record.platform_product_identity_digest
            for record in current_records
            if SHA256_RE.fullmatch(
                record.platform_product_identity_digest
            )
        }
        if not current_records or len(identity_digests) != 1:
            return _empty_quality(
                platform_name=platform,
                account_id=account_id,
                internal_sku=sku,
                mapping_snapshot=mapping_snapshot,
                fact_reason_codes=("CURRENT_MAPPING_NOT_UNIQUE",),
            )
        identity_digest = next(iter(identity_digests))
        current_mapping_ids = tuple(
            sorted(record.mapping_id for record in current_records)
        )

        with closing(self.repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT batches.*, items.*, runs.run_status,
                       runs.input_manifest_sha256
                FROM product_observation_batches AS batches
                INNER JOIN product_observation_items AS items
                  ON items.observation_batch_id = batches.observation_batch_id
                INNER JOIN automation_runs AS runs
                  ON runs.run_id = batches.automation_run_id
                WHERE batches.platform_name = ?
                  AND batches.scan_type = 'LISTING_STATUS_SCAN'
                  AND items.internal_sku = ?
                ORDER BY batches.scan_completed_at DESC,
                         batches.observation_batch_id DESC,
                         items.observation_item_id
                """,
                (platform, sku),
            ).fetchall()
            grouped: dict[str, list[object]] = defaultdict(list)
            for row in rows:
                grouped[str(row["observation_batch_id"])].append(row)
            evaluated = tuple(
                self._evaluate_candidate(
                    connection,
                    rows=batch_rows,
                    now=now,
                    mapping_snapshot=mapping_snapshot,
                    identity_digest=identity_digest,
                    current_mapping_ids=current_mapping_ids,
                )
                for batch_rows in grouped.values()
            )
            source_failure = self._latest_source_failure(
                connection,
                platform_name=platform,
                account_id=account_id,
                internal_sku=sku,
                now=now,
                mapping_snapshot=mapping_snapshot,
                identity_digest=identity_digest,
            )

        qualified = tuple(
            candidate
            for candidate in evaluated
            if candidate.quality.operating_fact_qualified
        )
        if not qualified:
            diagnostics = (
                (*evaluated, source_failure)
                if source_failure is not None
                else evaluated
            )
            if diagnostics:
                return max(
                    diagnostics,
                    key=lambda candidate: candidate.completed_at,
                ).quality
            return _empty_quality(
                platform_name=platform,
                account_id=account_id,
                internal_sku=sku,
                mapping_snapshot=mapping_snapshot,
                platform_product_identity_digest=identity_digest,
                fact_reason_codes=("NO_OBSERVATION_CANDIDATE",),
            )
        newest_at = max(candidate.completed_at for candidate in qualified)
        newest = tuple(
            candidate
            for candidate in qualified
            if candidate.completed_at == newest_at
        )
        if len({candidate.conflict_identity for candidate in newest}) > 1:
            return _empty_quality(
                platform_name=platform,
                account_id=account_id,
                internal_sku=sku,
                mapping_snapshot=mapping_snapshot,
                platform_product_identity_digest=identity_digest,
                fact_reason_codes=("AMBIGUOUS_CURRENT_CANDIDATES",),
            )
        return newest[0].quality

    def _latest_source_failure(
        self,
        connection,
        *,
        platform_name: str,
        account_id: str,
        internal_sku: str,
        now: datetime,
        mapping_snapshot: RuntimeMappingSnapshot,
        identity_digest: str,
    ) -> _Candidate | None:
        source = connection.execute(
            """
            SELECT snapshots.*, batches.manifest_sha256,
                   receipts.result_sha256, receipts.ack_state,
                   runs.run_id
            FROM listing_sync_snapshots AS snapshots
            INNER JOIN shadowbot_listing_action_batches AS batches
              ON batches.batch_id = snapshots.batch_id
            INNER JOIN shadowbot_listing_result_receipts AS receipts
              ON receipts.result_id = snapshots.result_id
             AND receipts.batch_id = snapshots.batch_id
            LEFT JOIN automation_runs AS runs
              ON runs.input_manifest_sha256 = batches.manifest_sha256
             AND runs.job_type = 'LISTING_STATUS_SCAN'
            WHERE snapshots.platform_name = ?
              AND (
                  snapshots.snapshot_complete = 0
                  OR snapshots.status <> 'VERIFIED'
              )
            ORDER BY snapshots.scan_completed_at DESC,
                     snapshots.snapshot_id DESC
            LIMIT 1
            """,
            (platform_name,),
        ).fetchone()
        if source is None:
            return None
        completed_at = _parse_utc(str(source["scan_completed_at"]))
        fresh_until = completed_at + self.max_age
        scope_complete = bool(source["snapshot_complete"])
        end_marker_verified = bool(
            source["online_end_marker_verified"]
            and source["waiting_end_marker_verified"]
        )
        fact_reasons: list[str] = []
        if not scope_complete:
            fact_reasons.append("SOURCE_SCAN_INCOMPLETE")
        if not end_marker_verified:
            fact_reasons.append("END_MARKER_NOT_VERIFIED")
        if str(source["status"]) != "VERIFIED":
            fact_reasons.append("SOURCE_SNAPSHOT_NOT_VERIFIED")
        if completed_at > now:
            fact_reasons.append("OBSERVATION_FROM_FUTURE")
        if now > fresh_until:
            fact_reasons.append("OBSERVATION_STALE")
        ack_state = str(source["ack_state"] or "")
        delivery_reasons = (
            ()
            if ack_state == "WRITTEN"
            else (f"EVIDENCE_ACK_{ack_state or 'UNKNOWN'}",)
        )
        quality = _with_qualification_sha256(
            ListingScanQuality(
                schema_version=LISTING_SCAN_QUALIFICATION_SCHEMA_VERSION,
                operating_fact_qualified=False,
                fact_reason_codes=tuple(dict.fromkeys(fact_reasons)),
                delivery_archive_healthy=not delivery_reasons,
                delivery_reason_codes=delivery_reasons,
                platform_name=platform_name,
                account_id=account_id,
                internal_sku=internal_sku,
                platform_product_identity_digest=identity_digest,
                authority_mode=mapping_snapshot.authority_mode,
                authority_generation=mapping_snapshot.authority_generation,
                mapping_snapshot_sha256=(
                    mapping_snapshot.mapping_snapshot_sha256
                ),
                mapping_version=mapping_snapshot.mappings.mapping_version,
                provider=LISTING_SCAN_PROVIDER,
                observation_type="LISTING_STATUS_SCAN",
                source_run_id=str(source["run_id"] or ""),
                observation_batch_id="",
                source_snapshot_id=str(source["snapshot_id"]),
                source_manifest_sha256=str(source["manifest_sha256"]),
                source_result_sha256=str(source["result_sha256"]),
                observation_content_sha256="",
                qualification_sha256="",
                observed_at="",
                scan_completed_at=_datetime_text(completed_at),
                fresh_until=_datetime_text(fresh_until),
                scope_complete=scope_complete,
                end_marker_verified=end_marker_verified,
            )
        )
        return _Candidate(
            quality=quality,
            completed_at=completed_at,
            conflict_identity=(
                quality.source_snapshot_id,
                quality.source_result_sha256,
            ),
        )

    def _evaluate_candidate(
        self,
        connection,
        *,
        rows: list[object],
        now: datetime,
        mapping_snapshot: RuntimeMappingSnapshot,
        identity_digest: str,
        current_mapping_ids: tuple[str, ...],
    ) -> _Candidate:
        row = rows[0]
        fact_reasons: list[str] = []
        if len(rows) != 1:
            fact_reasons.append("SKU_ITEM_NOT_UNIQUE")
        if str(row["batch_status"]) != "ACCEPTED":
            fact_reasons.append("BATCH_NOT_ACCEPTED")
        if int(row["scope_complete"]) != 1:
            fact_reasons.append("SCOPE_INCOMPLETE")
        if int(row["end_marker_verified"]) != 1:
            fact_reasons.append("END_MARKER_NOT_VERIFIED")
        completed_at = _parse_utc(str(row["scan_completed_at"]))
        observed_at = _parse_utc(str(row["observed_at"]))
        fresh_until = completed_at + self.max_age
        if completed_at > now or observed_at > now:
            fact_reasons.append("OBSERVATION_FROM_FUTURE")
        if now > fresh_until:
            fact_reasons.append("OBSERVATION_STALE")

        try:
            scope = json.loads(str(row["requested_scope_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            scope = {}
            fact_reasons.append("OBSERVATION_SCOPE_INVALID")
        if not isinstance(scope, dict):
            scope = {}
            fact_reasons.append("OBSERVATION_SCOPE_INVALID")
        stored_items = connection.execute(
            """
            SELECT *
            FROM product_observation_items
            WHERE observation_batch_id = ?
            ORDER BY observation_item_id
            """,
            (row["observation_batch_id"],),
        ).fetchall()
        mapping_version = str(
            scope.get("accepted_mapping_version") or ""
        )
        try:
            expected_content_sha256 = _result_content_sha256(
                ProductObservationBatchInput(
                    observation_batch_id=str(row["observation_batch_id"]),
                    automation_run_id=str(row["automation_run_id"]),
                    platform_name=str(row["platform_name"]),
                    scan_type=str(row["scan_type"]),
                    batch_status=str(row["batch_status"]),
                    scan_started_at=_parse_utc(str(row["scan_started_at"])),
                    scan_completed_at=completed_at,
                    requested_scope=scope,
                    scope_complete=bool(row["scope_complete"]),
                    end_marker_verified=bool(row["end_marker_verified"]),
                    items=tuple(
                        ProductObservationInput(
                            platform_product_name=str(
                                item["platform_product_name"]
                            ),
                            grade=str(item["grade"]),
                            observed_at=_parse_utc(str(item["observed_at"])),
                            observed_online=bool(item["observed_online"]),
                            page_identity_key=str(item["page_identity_key"]),
                            observed_price=(
                                Decimal(str(item["observed_price"]))
                                if item["observed_price"] is not None
                                else None
                            ),
                            observed_inventory=item["observed_inventory"],
                            evidence_sha256=str(item["evidence_sha256"]),
                        )
                        for item in stored_items
                    ),
                    error_code=str(row["error_code"]),
                    error_message=str(row["error_message"]),
                ),
                mapping_version=mapping_version,
            )
        except (ValueError, TypeError):
            expected_content_sha256 = ""
        if (
            not RAW_SHA256_RE.fullmatch(str(row["content_sha256"]))
            or expected_content_sha256 != str(row["content_sha256"])
        ):
            fact_reasons.append("OBSERVATION_CONTENT_MISMATCH")
        mapping_context = scope.get("mapping_authority")
        if not isinstance(mapping_context, dict) or (
            str(mapping_context.get("authority_mode") or "")
            != mapping_snapshot.authority_mode
            or mapping_context.get("authority_generation")
            != mapping_snapshot.authority_generation
            or str(mapping_context.get("account_id") or "")
            != mapping_snapshot.account_id
            or str(mapping_context.get("mapping_snapshot_sha256") or "")
            != mapping_snapshot.mapping_snapshot_sha256
            or str(mapping_context.get("mapping_version") or "")
            != mapping_snapshot.mappings.mapping_version
            or not SHA256_RE.fullmatch(
                str(mapping_context.get("locator_artifact_sha256") or "")
            )
        ):
            fact_reasons.append("MAPPING_AUTHORITY_MISMATCH")
        if mapping_version != (
            mapping_snapshot.mappings.mapping_version
        ):
            fact_reasons.append("MAPPING_VERSION_MISMATCH")

        bindings = scope.get("accepted_identity_bindings")
        matching_bindings = (
            [
                binding
                for binding in bindings
                if isinstance(binding, dict)
                and str(binding.get("evidence_sha256") or "")
                == str(row["evidence_sha256"])
                and str(binding.get("page_identity_key") or "")
                == str(row["page_identity_key"])
            ]
            if isinstance(bindings, list)
            else []
        )
        if len(matching_bindings) != 1:
            fact_reasons.append("IDENTITY_BINDING_MISSING")
        else:
            binding = matching_bindings[0]
            binding_mapping_ids = binding.get("mapping_ids")
            normalized_mapping_ids = (
                tuple(sorted(str(value) for value in binding_mapping_ids))
                if isinstance(binding_mapping_ids, list)
                else ()
            )
            if (
                str(binding.get("account_id") or "")
                != mapping_snapshot.account_id
                or str(binding.get("internal_sku") or "").upper()
                != str(row["internal_sku"]).upper()
                or str(
                    binding.get("platform_product_identity_digest") or ""
                )
                != identity_digest
                or normalized_mapping_ids != current_mapping_ids
            ):
                fact_reasons.append("PRODUCT_IDENTITY_MISMATCH")

        source_snapshot_id = str(scope.get("source_snapshot_id") or "")
        source_manifest_sha256 = str(
            scope.get("source_manifest_sha256") or ""
        )
        source_result_sha256 = str(
            scope.get("source_result_sha256") or ""
        )
        source = connection.execute(
            """
            SELECT snapshots.status AS snapshot_status,
                   snapshots.snapshot_complete,
                   snapshots.scan_completed_at AS source_completed_at,
                   batches.status AS source_batch_status,
                   batches.manifest_sha256,
                   receipts.result_sha256,
                   receipts.ack_state
            FROM listing_sync_snapshots AS snapshots
            INNER JOIN shadowbot_listing_action_batches AS batches
              ON batches.batch_id = snapshots.batch_id
            INNER JOIN shadowbot_listing_result_receipts AS receipts
              ON receipts.result_id = snapshots.result_id
             AND receipts.batch_id = snapshots.batch_id
            WHERE snapshots.snapshot_id = ?
            """,
            (source_snapshot_id,),
        ).fetchone()
        if source is None:
            fact_reasons.append("SOURCE_EVIDENCE_MISSING")
            ack_state = "MISSING"
        else:
            ack_state = str(source["ack_state"] or "")
            if (
                str(source["snapshot_status"]) != "VERIFIED"
                or int(source["snapshot_complete"]) != 1
                or str(source["source_batch_status"]) != "VERIFIED"
                or str(source["manifest_sha256"])
                != source_manifest_sha256
                or str(source["result_sha256"])
                != source_result_sha256
                or str(row["input_manifest_sha256"])
                != source_manifest_sha256
                or str(source["source_completed_at"])
                != str(row["scan_completed_at"])
                or not SHA256_RE.fullmatch(source_manifest_sha256)
                or not RAW_SHA256_RE.fullmatch(source_result_sha256)
            ):
                fact_reasons.append("SOURCE_INTEGRITY_MISMATCH")

        delivery_reasons = (
            ()
            if ack_state == "WRITTEN"
            else (f"EVIDENCE_ACK_{ack_state or 'UNKNOWN'}",)
        )
        quality = ListingScanQuality(
            schema_version=LISTING_SCAN_QUALIFICATION_SCHEMA_VERSION,
            operating_fact_qualified=not fact_reasons,
            fact_reason_codes=tuple(dict.fromkeys(fact_reasons)),
            delivery_archive_healthy=not delivery_reasons,
            delivery_reason_codes=delivery_reasons,
            platform_name=str(row["platform_name"]),
            account_id=mapping_snapshot.account_id,
            internal_sku=str(row["internal_sku"]),
            platform_product_identity_digest=identity_digest,
            authority_mode=mapping_snapshot.authority_mode,
            authority_generation=mapping_snapshot.authority_generation,
            mapping_snapshot_sha256=(
                mapping_snapshot.mapping_snapshot_sha256
            ),
            mapping_version=mapping_snapshot.mappings.mapping_version,
            provider=LISTING_SCAN_PROVIDER,
            observation_type="LISTING_STATUS_SCAN",
            source_run_id=str(row["automation_run_id"]),
            observation_batch_id=str(row["observation_batch_id"]),
            source_snapshot_id=source_snapshot_id,
            source_manifest_sha256=source_manifest_sha256,
            source_result_sha256=source_result_sha256,
            observation_content_sha256=str(row["content_sha256"]),
            qualification_sha256="",
            observed_at=_datetime_text(observed_at),
            scan_completed_at=_datetime_text(completed_at),
            fresh_until=_datetime_text(fresh_until),
            scope_complete=bool(row["scope_complete"]),
            end_marker_verified=bool(row["end_marker_verified"]),
            observed_online=bool(row["observed_online"]),
            observed_price=(
                str(row["observed_price"])
                if row["observed_price"] is not None
                else None
            ),
            observed_inventory=row["observed_inventory"],
        )
        quality = _with_qualification_sha256(quality)
        return _Candidate(
            quality=quality,
            completed_at=completed_at,
            conflict_identity=(
                quality.source_snapshot_id,
                quality.source_result_sha256,
                quality.observation_content_sha256,
            ),
        )


def _empty_quality(
    *,
    platform_name: str,
    account_id: str,
    internal_sku: str,
    fact_reason_codes: tuple[str, ...],
    mapping_snapshot: RuntimeMappingSnapshot | None = None,
    platform_product_identity_digest: str = "",
) -> ListingScanQuality:
    quality = ListingScanQuality(
        schema_version=LISTING_SCAN_QUALIFICATION_SCHEMA_VERSION,
        operating_fact_qualified=False,
        fact_reason_codes=fact_reason_codes,
        delivery_archive_healthy=False,
        delivery_reason_codes=("NO_SELECTED_DELIVERY_EVIDENCE",),
        platform_name=platform_name,
        account_id=account_id,
        internal_sku=internal_sku,
        platform_product_identity_digest=(
            platform_product_identity_digest
        ),
        authority_mode=(
            mapping_snapshot.authority_mode if mapping_snapshot else ""
        ),
        authority_generation=(
            mapping_snapshot.authority_generation if mapping_snapshot else 0
        ),
        mapping_snapshot_sha256=(
            mapping_snapshot.mapping_snapshot_sha256
            if mapping_snapshot
            else ""
        ),
        mapping_version=(
            mapping_snapshot.mappings.mapping_version
            if mapping_snapshot
            else ""
        ),
        provider=LISTING_SCAN_PROVIDER,
        observation_type="LISTING_STATUS_SCAN",
        source_run_id="",
        observation_batch_id="",
        source_snapshot_id="",
        source_manifest_sha256="",
        source_result_sha256="",
        observation_content_sha256="",
        qualification_sha256="",
        observed_at="",
        scan_completed_at="",
        fresh_until="",
        scope_complete=False,
        end_marker_verified=False,
    )
    return _with_qualification_sha256(quality)


def _with_qualification_sha256(
    quality: ListingScanQuality,
) -> ListingScanQuality:
    payload = quality.as_dict()
    payload.pop("qualification_sha256", None)
    digest = "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return replace(quality, qualification_sha256=digest)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("qualification clock and timestamps must be aware")
    return value.astimezone(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return _as_utc(value).isoformat()
