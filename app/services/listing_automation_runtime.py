from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.automation_models import AutomationRun, AutomationRunOutcome
from app.enums import AutomationRunStatus
from app.repositories.master_data_repository import RuntimeMasterDataError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    AutomationExecutionContext,
    AutomationHandler,
    FULL_MARKET_SCAN,
    LISTING_STATUS_SCAN,
    PRE_CUTOFF_FULL_SCAN,
)
from app.services.product_observation import (
    ProductObservationImporter,
    ProductObservationMappingContext,
    listing_snapshot_to_observation_batch,
)
from app.services.product_mapping import CompiledProductMappings
from app.services.runtime_master_data import RuntimeMasterDataProvider
from app.services.shadowbot_listing_sync import (
    fail_listing_sync_batch,
    import_listing_sync_result,
    mark_listing_sync_ack,
    prepare_listing_sync_batch,
    publish_listing_sync_batch,
)
from app.services.shadowbot_order_read import ShadowBotFileQueueOrderTransport


LISTING_STATUS_CHILD_JOB_ID = "AUTOMATION-LISTING-STATUS-SCAN-CHILD"
LISTING_STATUS_CHILD_RELATION = "LISTING_STATUS_CHILD"


class ParentAutomationHandler(Protocol):
    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome: ...


@dataclass(frozen=True, slots=True)
class ListingScanParentDispatchHandler:
    """Attach the existing child-only listing scan to one full scan."""

    parent_handler: ParentAutomationHandler | None = None
    child_job_id: str = LISTING_STATUS_CHILD_JOB_ID

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type not in {FULL_MARKET_SCAN, PRE_CUTOFF_FULL_SCAN}:
            raise ValueError(
                "listing child dispatch requires a full-scan parent"
            )
        outcome = (
            self.parent_handler(run, context)
            if self.parent_handler is not None
            else AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)
        )
        if outcome.status not in {
            AutomationRunStatus.SUCCESS,
            AutomationRunStatus.PARTIAL,
        }:
            return outcome
        child, created = context.ensure_child_run(
            child_job_id=self.child_job_id,
            relation_type=LISTING_STATUS_CHILD_RELATION,
        )
        return AutomationRunOutcome(
            status=outcome.status,
            output_manifest_sha256=outcome.output_manifest_sha256,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
            event_payload={
                **dict(outcome.event_payload),
                "listing_scan_child_run_id": child.run_id,
                "listing_scan_child_created": created,
                "listing_scan_relation": LISTING_STATUS_CHILD_RELATION,
            },
        )


@dataclass(frozen=True, slots=True)
class ListingStatusScanHandler:
    """Run existing SYNC_STATUS and append its immutable observation."""

    runtime_repository: SQLiteRuntimeRepository
    transport: ShadowBotFileQueueOrderTransport
    mapping_path: Path
    master_data: RuntimeMasterDataProvider
    execution_profile: str = "production"
    applet_uri: str = ""
    window_title: str = "蚂蚁花团供应商"

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != LISTING_STATUS_SCAN:
            raise ValueError(
                "ListingStatusScanHandler received an unexpected job type"
            )
        batch_id = f"LISTING-BATCH-{run.run_id}"
        self.transport.require_worker_ready()
        manifest = prepare_listing_sync_batch(
            self.runtime_repository,
            batch_id=batch_id,
            platform_name=run.platform_name,
            mapping_path=self.mapping_path,
            execution_profile=self.execution_profile,
            configured_account_id=self.master_data.configured_account_id,
            master_data_provider=self.master_data,
        )
        mapping_snapshot = self.master_data.mapping_snapshot()
        _validate_locator_binding(
            self.mapping_path,
            manifest=manifest,
            authority_mode=mapping_snapshot.authority_mode,
            authority_generation=mapping_snapshot.authority_generation,
            account_id=mapping_snapshot.account_id,
            platform_name=run.platform_name,
            mapping_snapshot_sha256=(
                mapping_snapshot.mapping_snapshot_sha256
            ),
            mappings=mapping_snapshot.mappings,
        )
        mapping_context = ProductObservationMappingContext(
            authority_mode=mapping_snapshot.authority_mode,
            authority_generation=mapping_snapshot.authority_generation,
            account_id=mapping_snapshot.account_id,
            mapping_snapshot_sha256=(
                mapping_snapshot.mapping_snapshot_sha256
            ),
            mapping_version=mapping_snapshot.mappings.mapping_version,
            locator_artifact_sha256=str(
                manifest["mapping_source_version"]
            ),
        )
        context.bind_input_manifest(str(manifest["manifest_sha256"]))
        self.transport.set_wait_callback(context.heartbeat)
        imported_result_id = ""
        observation_imported = False
        try:
            request, started = publish_listing_sync_batch(
                self.runtime_repository,
                self.transport.runner,
                manifest=manifest,
                execution_profile=self.execution_profile,
                applet_uri=self.applet_uri,
                execution_attempt_id=f"LISTING-READ-{uuid4().hex}",
                window_title=self.window_title,
                capture_evidence=False,
            )
            result = dict(
                self.transport.wait_for_published_result(
                    request,
                    request_file_sha256=str(
                        started.raw_output.get("request_file_sha256") or ""
                    ),
                )
            )
            result_sha256 = self.transport.last_result_file_sha256
            result_path = self.transport.last_result_path
            summary = import_listing_sync_result(
                self.runtime_repository,
                request=request,
                result=result,
                result_file_sha256=result_sha256,
                source_result_path=str(result_path or ""),
                automation_claim=context.claim,
            )
            imported_result_id = str(summary["result_id"])
            if summary["status"] != "VERIFIED":
                archive_dir, archive_error = self._archive_result()
                mark_listing_sync_ack(
                    self.runtime_repository,
                    result_id=imported_result_id,
                    written=archive_dir is not None,
                    error_message=archive_error,
                )
                return AutomationRunOutcome(
                    status=AutomationRunStatus.FAILED,
                    error_code="LISTING_STATUS_SCAN_FAILED",
                    error_message=str(
                        dict(result["snapshot"]).get("error_code") or ""
                    ),
                    event_payload={
                        "source_snapshot_id": summary["snapshot_id"],
                        "delivery_archive_healthy": archive_dir is not None,
                        "platform_write_performed": False,
                    },
                )

            observation_batch = listing_snapshot_to_observation_batch(
                dict(result["snapshot"]),
                automation_run_id=run.run_id,
                source_manifest_sha256=str(manifest["manifest_sha256"]),
                source_result_sha256=result_sha256,
                operational_time=context.operational_time,
                mapping_authority=mapping_context,
            )
            imported = ProductObservationImporter(
                self.runtime_repository,
                mappings=mapping_snapshot.mappings,
                operational_time=context.operational_time,
            ).import_batch(
                observation_batch,
                claim=context.claim,
            )
            observation_imported = True
            archive_dir, archive_error = self._archive_result()
            archive_healthy = archive_dir is not None
            mark_listing_sync_ack(
                self.runtime_repository,
                result_id=imported_result_id,
                written=archive_healthy,
                error_message=archive_error,
            )
            payload = {
                "relation_type": "PRODUCT_OBSERVATION_IMPORT",
                "observation_batch_id": imported.observation_batch_id,
                "source_snapshot_id": summary["snapshot_id"],
                "item_count": imported.item_count,
                "mapping_version": imported.mapping_version,
                "account_id": mapping_snapshot.account_id,
                "authority_generation": (
                    mapping_snapshot.authority_generation
                ),
                "mapping_snapshot_sha256": (
                    mapping_snapshot.mapping_snapshot_sha256
                ),
                "archive_dir": str(archive_dir or ""),
                "delivery_archive_healthy": archive_healthy,
                "platform_write_performed": False,
            }
            if not archive_healthy:
                return AutomationRunOutcome(
                    status=AutomationRunStatus.PARTIAL,
                    output_manifest_sha256=imported.content_sha256,
                    error_code="LISTING_EVIDENCE_ARCHIVE_FAILED",
                    error_message=archive_error,
                    event_payload=payload,
                )
            return AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS,
                output_manifest_sha256=imported.content_sha256,
                event_payload=payload,
            )
        except Exception as exc:
            if not observation_imported:
                fail_listing_sync_batch(
                    self.runtime_repository,
                    batch_id=batch_id,
                )
            archive_dir = None
            archive_error = ""
            if self.transport.last_result_path is not None:
                archive_dir, archive_error = self._archive_result()
            if imported_result_id:
                mark_listing_sync_ack(
                    self.runtime_repository,
                    result_id=imported_result_id,
                    written=archive_dir is not None,
                    error_message=archive_error or str(exc),
                )
            raise
        finally:
            self.transport.set_wait_callback(None)

    def _archive_result(self) -> tuple[Path | None, str]:
        try:
            archive_dir = self.transport.acknowledge_last_result()
            if archive_dir is None:
                return None, "result archive was not created"
            return archive_dir, ""
        except Exception as exc:
            return None, str(exc)[:1000]


def build_listing_read_only_handlers(
    *,
    runtime_repository: SQLiteRuntimeRepository,
    queue_dir: Path,
    locator_path: Path,
    configured_account_id: str,
    platform_mappings_workbook: Path | None = None,
    full_market_parent_handler: ParentAutomationHandler | None = None,
    pre_cutoff_parent_handler: ParentAutomationHandler | None = None,
    execution_profile: str = "production",
    applet_uri: str = "",
    timeout_seconds: float = 330.0,
) -> Mapping[str, AutomationHandler]:
    """Compose listing READ_ONLY handlers from existing production assets."""

    transport = ShadowBotFileQueueOrderTransport(
        Path(queue_dir),
        timeout_seconds=timeout_seconds,
    )
    master_data = RuntimeMasterDataProvider(
        runtime_repository,
        configured_account_id=configured_account_id,
        platform_mappings_workbook=(
            Path(platform_mappings_workbook)
            if platform_mappings_workbook is not None
            else None
        ),
    )
    return {
        FULL_MARKET_SCAN: ListingScanParentDispatchHandler(
            parent_handler=full_market_parent_handler,
        ),
        PRE_CUTOFF_FULL_SCAN: ListingScanParentDispatchHandler(
            parent_handler=pre_cutoff_parent_handler,
        ),
        LISTING_STATUS_SCAN: ListingStatusScanHandler(
            runtime_repository=runtime_repository,
            transport=transport,
            mapping_path=Path(locator_path),
            master_data=master_data,
            execution_profile=execution_profile,
            applet_uri=applet_uri,
        ),
    }


def _validate_locator_binding(
    path: Path,
    *,
    manifest: dict[str, object],
    authority_mode: str,
    authority_generation: int,
    account_id: str,
    platform_name: str,
    mapping_snapshot_sha256: str,
    mappings: CompiledProductMappings,
) -> None:
    from app.services.shadowbot_listing_sync import mapping_source_version

    if mapping_source_version(path) != str(
        manifest.get("mapping_source_version") or ""
    ):
        raise RuntimeMasterDataError(
            "ShadowBot locator changed after manifest construction."
        )
    if authority_mode != "DB_AUTHORITY":
        return
    try:
        payload = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeMasterDataError(
            "Runtime-derived ShadowBot locator is unreadable."
        ) from exc
    if not isinstance(payload, dict) or (
        payload.get("schema_version")
        != "runtime-derived-shadowbot-locator-v1"
        or payload.get("authority") != "runtime_db_derived"
        or payload.get("authority_generation") != authority_generation
        or str(payload.get("account_id") or "") != account_id
        or str(payload.get("platform_name") or "") != platform_name
        or str(payload.get("mapping_snapshot_sha256") or "")
        != mapping_snapshot_sha256
    ):
        raise RuntimeMasterDataError(
            "Runtime-derived ShadowBot locator does not match current authority."
        )
    supplied_payload_sha256 = str(
        payload.get("artifact_payload_sha256") or ""
    )
    unsigned_payload = dict(payload)
    unsigned_payload.pop("artifact_payload_sha256", None)
    expected_payload_sha256 = "sha256:" + hashlib.sha256(
        json.dumps(
            unsigned_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    expected_mappings = sorted(
        (
            {
                "internal_sku": str(record.internal_sku).upper(),
                "expected_product_name": record.platform_product_name,
                "expected_grade": record.grade,
                "platform_product_identity_json": (
                    record.platform_product_identity_json
                ),
                "platform_product_identity_digest": (
                    record.platform_product_identity_digest
                ),
                "status": "active",
            }
            for record in mappings.records
            if record.mapping_status.value == "VERIFIED"
            and record.internal_sku
        ),
        key=lambda item: item["internal_sku"],
    )
    if (
        supplied_payload_sha256 != expected_payload_sha256
        or payload.get("mappings") != expected_mappings
    ):
        raise RuntimeMasterDataError(
            "Runtime-derived ShadowBot locator content does not match authority."
        )
