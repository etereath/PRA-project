from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.automation_models import AutomationRun, AutomationRunOutcome
from app.enums import AutomationRunStatus
from app.repositories.master_data_repository import RuntimeMasterDataRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    AutomationExecutionContext,
    AutomationHandler,
    FULL_MARKET_SCAN,
    LISTING_STATUS_SCAN,
    ONLINE_PULSE,
    PRE_CUTOFF_FULL_SCAN,
)
from app.services.product_observation import (
    ProductObservationImporter,
    listing_online_snapshot_to_observation_batch,
    listing_snapshot_to_observation_batch,
)
from app.services.product_mapping import CompiledProductMappings
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
    """Add the existing child-only listing scan to a scheduled full scan."""

    parent_handler: ParentAutomationHandler | None = None
    child_job_id: str = LISTING_STATUS_CHILD_JOB_ID

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type not in {FULL_MARKET_SCAN, PRE_CUTOFF_FULL_SCAN}:
            raise ValueError(
                "listing child dispatch requires a scheduled full-scan parent"
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
    """Run Task 13 SYNC_STATUS and import its immutable v14 observation."""

    runtime_repository: SQLiteRuntimeRepository
    transport: ShadowBotFileQueueOrderTransport
    mapping_path: Path
    mappings_provider: Callable[[], CompiledProductMappings]
    execution_profile: str = "production"
    applet_uri: str = ""
    window_title: str = "蚂蚁花团供应商"
    job_type: str = LISTING_STATUS_SCAN
    scan_scope: str = "online_and_waiting"
    batch_id_factory: Callable[[AutomationRun], str] = (
        lambda run: f"LISTING-BATCH-{run.run_id}"
    )
    attempt_id_factory: Callable[[], str] = (
        lambda: f"LISTING-READ-{uuid4().hex}"
    )

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != self.job_type:
            raise ValueError(
                "ListingStatusScanHandler received an unexpected job type"
            )
        self.transport.require_worker_ready()
        manifest = prepare_listing_sync_batch(
            self.runtime_repository,
            batch_id=self.batch_id_factory(run),
            platform_name=run.platform_name,
            mapping_path=self.mapping_path,
            execution_profile=self.execution_profile,
            scan_scope=self.scan_scope,
        )
        context.bind_input_manifest(str(manifest["manifest_sha256"]))
        self.transport.set_wait_callback(context.heartbeat)
        imported_result_id = ""
        try:
            request, started = publish_listing_sync_batch(
                self.runtime_repository,
                self.transport.runner,
                manifest=manifest,
                execution_profile=self.execution_profile,
                applet_uri=self.applet_uri,
                execution_attempt_id=self.attempt_id_factory(),
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
                archive_dir = self.transport.acknowledge_last_result()
                mark_listing_sync_ack(
                    self.runtime_repository,
                    result_id=imported_result_id,
                    written=archive_dir is not None,
                    error_message=(
                        "" if archive_dir is not None else "归档目录未创建"
                    ),
                )
                return AutomationRunOutcome(
                    status=AutomationRunStatus.FAILED,
                    error_code="LISTING_STATUS_SCAN_FAILED",
                    error_message="",
                    event_payload={
                        "source_snapshot_id": summary["snapshot_id"],
                        "platform_write_performed": False,
                    },
                )

            observation_adapter = (
                listing_online_snapshot_to_observation_batch
                if self.job_type == ONLINE_PULSE
                else listing_snapshot_to_observation_batch
            )
            observation_batch = observation_adapter(
                dict(result["snapshot"]),
                automation_run_id=run.run_id,
                source_manifest_sha256=str(manifest["manifest_sha256"]),
                source_result_sha256=result_sha256,
                operational_time=context.operational_time,
            )
            imported = ProductObservationImporter(
                self.runtime_repository,
                mappings=self.mappings_provider(),
                operational_time=context.operational_time,
            ).import_batch(
                observation_batch,
                claim=context.claim,
            )
            archive_dir = self.transport.acknowledge_last_result()
            if archive_dir is None:
                raise RuntimeError("商品状态扫描结果没有完成归档")
            mark_listing_sync_ack(
                self.runtime_repository,
                result_id=imported_result_id,
                written=True,
            )
            return AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS,
                output_manifest_sha256=imported.content_sha256,
                event_payload={
                    "relation_type": "PRODUCT_OBSERVATION_IMPORT",
                    "observation_batch_id": imported.observation_batch_id,
                    "source_snapshot_id": summary["snapshot_id"],
                    "item_count": imported.item_count,
                    "mapping_version": imported.mapping_version,
                    "archive_dir": str(archive_dir),
                    "platform_write_performed": False,
                },
            )
        except Exception as exc:
            archive_dir = None
            archive_error = ""
            fail_listing_sync_batch(
                self.runtime_repository,
                batch_id=str(manifest["batch_id"]),
            )
            if self.transport.last_result_path is not None:
                try:
                    archive_dir = self.transport.acknowledge_last_result()
                except Exception as archive_exc:
                    archive_error = str(archive_exc)
            if imported_result_id:
                mark_listing_sync_ack(
                    self.runtime_repository,
                    result_id=imported_result_id,
                    written=archive_dir is not None,
                    error_message=archive_error or str(exc),
                )
            if archive_error:
                raise RuntimeError(
                    "商品状态扫描失败，且结果归档未完成："
                    + archive_error
                ) from exc
            raise
        finally:
            self.transport.set_wait_callback(None)


def build_listing_read_only_handlers(
    *,
    runtime_repository: SQLiteRuntimeRepository,
    queue_dir: Path,
    mapping_path: Path,
    full_market_parent_handler: ParentAutomationHandler | None = None,
    execution_profile: str = "production",
    applet_uri: str = "",
    timeout_seconds: float = 330.0,
) -> Mapping[str, AutomationHandler]:
    """Compose the formal two-page product scan from existing Task 13 assets."""

    transport = ShadowBotFileQueueOrderTransport(
        Path(queue_dir),
        timeout_seconds=timeout_seconds,
    )
    master_data = RuntimeMasterDataRepository(runtime_repository)
    listing_handler = ListingStatusScanHandler(
        runtime_repository=runtime_repository,
        transport=transport,
        mapping_path=Path(mapping_path),
        mappings_provider=master_data.compiled_mappings,
        execution_profile=execution_profile,
        applet_uri=applet_uri,
    )
    online_pulse_handler = ListingStatusScanHandler(
        runtime_repository=runtime_repository,
        transport=transport,
        mapping_path=Path(mapping_path),
        mappings_provider=master_data.compiled_mappings,
        execution_profile=execution_profile,
        applet_uri=applet_uri,
        job_type=ONLINE_PULSE,
        scan_scope="online",
    )
    return {
        FULL_MARKET_SCAN: ListingScanParentDispatchHandler(
            parent_handler=full_market_parent_handler,
        ),
        PRE_CUTOFF_FULL_SCAN: ListingScanParentDispatchHandler(),
        LISTING_STATUS_SCAN: listing_handler,
        ONLINE_PULSE: online_pulse_handler,
    }
