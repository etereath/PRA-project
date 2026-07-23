from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.exceptions import ValidationError
from app.listing_identity import listing_identity_key
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.services.shadowbot_executor import (
    EXECUTION_MODE_COMMIT,
    SIDE_EFFECT_NOT_APPLIED,
    SIDE_EFFECT_NOT_STARTED,
    SIDE_EFFECT_UNKNOWN,
    STATUS_FAILED,
    STATUS_SIDE_EFFECT_UNKNOWN,
    STATUS_START_UNKNOWN,
    ShadowBotExecutor,
    ShadowBotTaskRunner,
    shadowbot_result_contract_from_data,
)
from app.services.shadowbot_product_read import (
    DEFAULT_INVENTORY_PRODUCTS_PATH,
    MAX_RESULT_BYTES,
    aggregate_product_snapshots,
    normalize_multi_product_request,
    validate_evidence_binding,
)
from app.services.shadowbot_commit_batch import (
    CONTRACT_VERSION as COMMIT_BATCH_CONTRACT_VERSION,
    validate_request as validate_commit_batch_request,
)


SUBMIT_PHASES = {"SUBMIT_INTENT_RECORDED", "SUBMIT_CLICKED"}
PRE_SUBMIT_PHASES = {"CLAIMED", "UI_STARTED", "PRICE_VERIFIED", "TARGET_FILLED"}


def _parse_shadowbot_observed_at(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValidationError("RESULT_CONTRACT_INVALID: inventory observation time is required.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("RESULT_CONTRACT_INVALID: inventory observation time is invalid.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ShadowBotQueuePaths:
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def working(self) -> Path:
        return self.root / "working"

    @property
    def results(self) -> Path:
        return self.root / "results"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def quarantine(self) -> Path:
        return self.root / "quarantine"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def control(self) -> Path:
        return self.root / "control"

    @property
    def heartbeat(self) -> Path:
        return self.root / "heartbeat.json"

    def ensure(self) -> None:
        for path in (
            self.inbox,
            self.working,
            self.results,
            self.archive,
            self.quarantine,
            self.evidence,
            self.control,
        ):
            path.mkdir(parents=True, exist_ok=True)


class ShadowBotResultImporter:
    """Validate and import completed result files. It never classifies queue timeouts."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        runner: ShadowBotTaskRunner,
        queue_dir: Path,
        *,
        inventory_products_path: Path | None = None,
    ) -> None:
        self.repository = repository
        self.executor = ShadowBotExecutor(repository, runner)
        self.paths = ShadowBotQueuePaths(queue_dir)
        self.inventory_products_path = Path(
            inventory_products_path
            or os.environ.get("PRA_PRODUCTS_PATH")
            or DEFAULT_INVENTORY_PRODUCTS_PATH
        )
        self.paths.ensure()

    def import_available(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for result_path in sorted(self.paths.results.glob("*.result.json")):
            try:
                events.append(self.import_one(result_path))
            except OSError as exc:
                events.append(
                    {
                        "status": "RETRY_PENDING",
                        "error_code": "RESULT_IO_RETRY_PENDING",
                        "error_message": str(exc),
                        "path": str(result_path),
                    }
                )
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                quarantined = self._quarantine(result_path, exc)
                events.append(
                    {
                        "status": "QUARANTINED",
                        "error_code": "RESULT_CONTRACT_INVALID",
                        "error_message": str(exc),
                        "path": str(quarantined),
                    }
                )
        return events

    def import_one(self, result_path: Path) -> dict[str, Any]:
        result_bytes = result_path.read_bytes()
        _verify_checksum(result_path, result_bytes)
        data = json.loads(result_bytes.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise ValidationError("RESULT_CONTRACT_INVALID: result JSON must be an object.")
        execution_attempt_id = str(data.get("execution_attempt_id") or "").strip()
        if not execution_attempt_id:
            raise ValidationError("RESULT_CONTRACT_INVALID: execution_attempt_id is required.")
        if data.get("contract_version") == COMMIT_BATCH_CONTRACT_VERSION:
            return self._import_v4_commit_result(
                result_path,
                data=data,
                result_bytes=result_bytes,
            )
        attempt = self.repository.get_shadowbot_execution_attempt(execution_attempt_id)
        if attempt is None:
            raise ValidationError("RESULT_CONTRACT_INVALID: execution_attempt_id does not exist.")
        request_path = self._find_request(execution_attempt_id, attempt.queue_request_path)
        request_bytes = request_path.read_bytes()
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        _verify_checksum(request_path, request_bytes)
        if request_sha256 != attempt.request_file_sha256:
            raise ValidationError("RESULT_CONTRACT_INVALID: archived request hash does not match attempt.")
        request_data = json.loads(request_bytes.decode("utf-8-sig"))
        self._validate_v2_result(request_data, data, result_bytes)
        result_file_sha256 = hashlib.sha256(result_bytes).hexdigest()
        data.setdefault("result_id", f"RESULT-{result_file_sha256[:24]}")
        lease_required = isinstance(attempt.raw_output.get("lease"), dict)
        for lease_field in ("lease_owner_token", "lease_version"):
            if lease_required and lease_field not in data:
                raise ValidationError(f"RESULT_CONTRACT_INVALID: {lease_field} is required for leased attempt.")
            if lease_field in data and str(data.get(lease_field)) != str(request_data.get(lease_field)):
                raise ValidationError(f"RESULT_CONTRACT_INVALID: {lease_field} mismatch.")
        data["result_file_sha256"] = result_file_sha256
        contract = shadowbot_result_contract_from_data(data)
        if contract.request_file_sha256 != request_sha256:
            raise ValidationError("RESULT_CONTRACT_INVALID: result request_file_sha256 mismatch.")
        for field_name in ("operation_id", "task_id", "execution_attempt_id", "execution_mode", "instruction_hash"):
            if str(data.get(field_name) or "") != str(request_data.get(field_name) or ""):
                raise ValidationError(f"RESULT_CONTRACT_INVALID: {field_name} mismatch.")
        if attempt.ended_at is None:
            self.executor.record_result(
                contract,
                automatic_reconcile_payload=_automatic_reconcile_payload(request_data),
            )
        else:
            imported_result_id = str(attempt.raw_output.get("result_id") or "")
            imported_result_sha256 = str(attempt.raw_output.get("result_file_sha256") or "")
            if not imported_result_id or not imported_result_sha256:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: late result for terminal attempt without persisted result identity."
                )
            if imported_result_id != contract.result_id or imported_result_sha256 != result_file_sha256:
                raise ValidationError("RESULT_CONTRACT_INVALID: conflicting result evidence for completed attempt.")
            self.executor.reproject_terminal_result(contract)
        inventory_events = self._import_v2_inventory_observations(request_data, data)
        archive_dir = self._archive_attempt(contract.execution_attempt_id, request_path, result_path)
        return {
            "status": "IMPORTED" if attempt.ended_at is None else "ALREADY_IMPORTED",
            "execution_attempt_id": contract.execution_attempt_id,
            "archive_dir": str(archive_dir),
            "inventory_events": inventory_events,
        }

    def _import_v4_commit_result(
        self,
        result_path: Path,
        *,
        data: dict[str, Any],
        result_bytes: bytes,
    ) -> dict[str, Any]:
        """Import one formal v4 batch without a legacy per-item attempt row."""

        execution_attempt_id = str(data.get("execution_attempt_id") or "").strip()
        request_path = self._find_v4_request(execution_attempt_id)
        request_bytes = request_path.read_bytes()
        _verify_checksum(request_path, request_bytes)
        request_file_sha256 = hashlib.sha256(request_bytes).hexdigest()
        request_data = json.loads(request_bytes.decode("utf-8-sig"))
        if not isinstance(request_data, dict):
            raise ValidationError("RESULT_CONTRACT_INVALID: v4 request JSON must be an object.")
        # The Worker validates expiry before claiming. Import can legitimately
        # happen after that deadline, so replay every immutable contract check
        # except the wall-clock expiry check.
        validate_commit_batch_request(request_data, check_expiry=False)
        for field_name in (
            "task_id",
            "operation_id",
            "execution_attempt_id",
            "execution_mode",
            "batch_id",
            "manifest_sha256",
            "instruction_hash",
        ):
            if str(data.get(field_name) or "") != str(request_data.get(field_name) or ""):
                raise ValidationError(
                    f"RESULT_CONTRACT_INVALID: v4 {field_name} mismatch."
                )
        if str(data.get("request_file_sha256") or "") != request_file_sha256:
            raise ValidationError(
                "RESULT_CONTRACT_INVALID: v4 request_file_sha256 mismatch."
            )
        result_file_sha256 = hashlib.sha256(result_bytes).hexdigest()
        data.setdefault("result_id", f"RESULT-{result_file_sha256[:24]}")
        data["result_file_sha256"] = result_file_sha256
        from app.services.shadowbot_commit_pipeline import import_task_commit_result

        with self.repository.connect_read() as connection:
            batch_row = connection.execute(
                "SELECT result_id FROM shadowbot_commit_batches WHERE batch_id = ?",
                (str(data.get("batch_id") or ""),),
            ).fetchone()
        already_imported = bool(
            batch_row is not None
            and str(batch_row["result_id"] or "") == str(data["result_id"])
        )
        listing_observations = self._normalize_v4_page_snapshot(request_data, data)
        counts = import_task_commit_result(
            self.repository,
            data,
            listing_observations=listing_observations,
            result_file_sha256=result_file_sha256,
            source_result_path=str(result_path),
        )
        reconcile_events: list[dict[str, Any]] = []
        for item in data.get("items") or []:
            if str(item.get("status") or "").upper() != "UNKNOWN":
                continue
            try:
                reconcile = self.executor.ensure_reconcile_attempt(
                    operation_id=str(item.get("operation_id") or ""),
                    source_execution_attempt_id=str(
                        item.get("item_execution_attempt_id") or ""
                    ),
                    runner_payload={
                        key: request_data[key]
                        for key in ("applet_uri", "window_title")
                        if request_data.get(key)
                    },
                )
                reconcile_events.append(
                    {
                        "operation_id": str(item.get("operation_id") or ""),
                        "execution_attempt_id": (
                            reconcile.execution_attempt_id
                            if reconcile is not None
                            else ""
                        ),
                        "status": (
                            reconcile.status if reconcile is not None else "NOT_REQUIRED"
                        ),
                    }
                )
            except (ValidationError, OSError) as exc:
                reconcile_events.append(
                    {
                        "operation_id": str(item.get("operation_id") or ""),
                        "execution_attempt_id": "",
                        "status": "RETRY_PENDING",
                        "error_message": str(exc),
                    }
                )
        archive_dir = self.paths.archive / execution_attempt_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        ack_path = archive_dir / f"{execution_attempt_id}.import.ack.json"
        try:
            _atomic_write(
                ack_path,
                _json_bytes(
                    {
                        "schema_version": "shadowbot-commit-import-ack-1.0",
                        "result_id": data["result_id"],
                        "batch_id": data["batch_id"],
                        "execution_attempt_id": execution_attempt_id,
                        "result_file_sha256": result_file_sha256,
                        "accepted_at": datetime.now(UTC).isoformat(),
                    }
                ),
            )
        except OSError as exc:
            with self.repository.connect_write() as connection, connection:
                connection.execute(
                    """
                    UPDATE shadowbot_commit_result_receipts
                    SET ack_state = 'FAILED', ack_updated_at = ?,
                        last_projection_error = ?
                    WHERE result_id = ?
                    """,
                    (datetime.now(UTC).isoformat(), str(exc), data["result_id"]),
                )
            return {
                "status": "IMPORTED_ACK_PENDING",
                "contract_version": COMMIT_BATCH_CONTRACT_VERSION,
                "batch_id": str(data.get("batch_id") or ""),
                "execution_attempt_id": execution_attempt_id,
                "archive_dir": str(archive_dir),
                "counts": counts,
                "listing_status_events": listing_observations,
                "reconcile_events": reconcile_events,
            }
        archive_dir = self._archive_attempt(
            execution_attempt_id,
            request_path,
            result_path,
        )
        with self.repository.connect_write() as connection, connection:
            connection.execute(
                """
                UPDATE shadowbot_commit_result_receipts
                SET ack_state = 'WRITTEN', ack_updated_at = ?,
                    last_projection_error = ''
                WHERE result_id = ?
                """,
                (datetime.now(UTC).isoformat(), data["result_id"]),
            )
        return {
            "status": "ALREADY_IMPORTED" if already_imported else "IMPORTED",
            "contract_version": COMMIT_BATCH_CONTRACT_VERSION,
            "batch_id": str(data.get("batch_id") or ""),
            "execution_attempt_id": execution_attempt_id,
            "archive_dir": str(archive_dir),
            "counts": counts,
            "listing_status_events": listing_observations,
            "reconcile_events": reconcile_events,
        }

    def _find_v4_request(self, execution_attempt_id: str) -> Path:
        candidates = (
            self.paths.working / f"{execution_attempt_id}.request.json",
            self.paths.inbox / f"{execution_attempt_id}.ready.json",
            self.paths.archive / execution_attempt_id / f"{execution_attempt_id}.request.json",
            self.paths.archive / execution_attempt_id / f"{execution_attempt_id}.ready.json",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise ValidationError("RESULT_CONTRACT_INVALID: v4 source request file is missing.")

    def _normalize_v4_page_snapshot(
        self,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Validate and normalize every observed product without mutating the database."""

        snapshot = result.get("page_snapshot")
        if snapshot is None:
            # Compatibility for v4 results written before page-wide feedback
            # became part of the Worker result.
            return []
        if not isinstance(snapshot, dict):
            raise ValidationError("RESULT_CONTRACT_INVALID: v4 page_snapshot must be an object.")
        platform_name = str(snapshot.get("platform_name") or "").strip()
        if platform_name != str(request.get("platform_name") or "").strip():
            raise ValidationError(
                "RESULT_CONTRACT_INVALID: v4 page_snapshot platform mismatch."
            )
        products = snapshot.get("products")
        if not isinstance(products, list) or not products:
            raise ValidationError(
                "RESULT_CONTRACT_INVALID: v4 page_snapshot products must be non-empty."
            )
        if int(snapshot.get("total_count") or -1) != len(products):
            raise ValidationError(
                "RESULT_CONTRACT_INVALID: v4 page_snapshot total_count mismatch."
            )
        execution_attempt_id = str(result.get("execution_attempt_id") or "").strip()
        seen_positions: set[int] = set()
        seen_identities: set[tuple[str, str, str]] = set()
        observations: list[dict[str, Any]] = []
        for product in products:
            if not isinstance(product, dict):
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: v4 page_snapshot product must be an object."
                )
            try:
                position = int(product.get("position"))
                inventory = int(product.get("inventory"))
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: v4 page_snapshot position/inventory invalid."
                ) from exc
            if position < 1 or position in seen_positions or inventory < 0:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: v4 page_snapshot position/inventory invalid."
                )
            seen_positions.add(position)
            variety = str(product.get("product_name") or "").strip()
            grade = str(product.get("grade") or "").strip()
            identity = listing_identity_key(platform_name, variety, grade)
            if identity in seen_identities:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: v4 page_snapshot identity is not unique."
                )
            seen_identities.add(identity)
            existing = self.repository.get_listing_status(*identity)
            price_status = str(product.get("price_status") or "").upper()
            if price_status not in {
                "OBSERVED_AT_PREFLIGHT",
                "VERIFIED_AFTER_COMMIT",
                "UNKNOWN_AFTER_SUBMIT",
            }:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: v4 page_snapshot price_status invalid."
                )
            raw_price = product.get("price")
            if price_status == "UNKNOWN_AFTER_SUBMIT":
                if existing is None:
                    raise ValidationError(
                        "RESULT_CONTRACT_INVALID: unknown v4 price has no existing listing status."
                    )
                observed_price = None
            else:
                try:
                    observed_price = Decimal(str(raw_price))
                except (ArithmeticError, ValueError) as exc:
                    raise ValidationError(
                        "RESULT_CONTRACT_INVALID: v4 page_snapshot price invalid."
                    ) from exc
            listing_value = str(product.get("listing_status") or "UNKNOWN").upper()
            if listing_value not in {"ONLINE", "OFFLINE", "UNKNOWN"}:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: v4 page_snapshot listing_status invalid."
                )
            if listing_value == "UNKNOWN" and existing is None:
                raise ValidationError(
                    "RESULT_CONTRACT_INVALID: unknown v4 listing status has no existing row."
                )
            online_status = (
                existing.online_status
                if listing_value == "UNKNOWN" and existing is not None
                else "online"
                if listing_value == "ONLINE"
                else "offline"
            )
            observations.append(
                {
                    "position": position,
                    "identity": identity,
                    "observed_price": observed_price,
                    "preserve_existing_price": price_status
                    == "UNKNOWN_AFTER_SUBMIT",
                    "inventory": inventory,
                    "online_status": online_status,
                    "preserve_existing_online_status": listing_value == "UNKNOWN",
                    "observed_at": _parse_shadowbot_observed_at(
                        product.get("observed_at") or snapshot.get("captured_at")
                    ),
                    "execution_attempt_id": execution_attempt_id,
                }
            )
        return observations

    def _import_v2_inventory_observations(
        self,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if request.get("contract_version") != 2 or result.get("status") != "READ_COMPLETED":
            return []
        normalized_request = normalize_multi_product_request(request)
        aggregated = aggregate_product_snapshots(
            read_batch_id=normalized_request["read_batch_id"],
            contract_version=2,
            started_at=str(result.get("started_at") or ""),
            completed_at=str(result.get("ended_at") or result.get("completed_at") or ""),
            snapshots=result.get("product_snapshots") or [],
            expected_item_ids=(
                None
                if normalized_request["execution_mode"] == "READ_ONLY"
                else [product["item_id"] for product in normalized_request["products"]]
            ),
        )
        observed_at = _parse_shadowbot_observed_at(
            result.get("ended_at") or result.get("completed_at")
        )
        execution_attempt_id = str(result.get("execution_attempt_id") or "").strip()
        request_by_item = {product["item_id"]: product for product in normalized_request["products"]}
        inventory_by_identity: dict[tuple[str, str, str], Any] = {}
        ambiguous_inventory_identities: set[tuple[str, str, str]] = set()
        try:
            inventory_products = load_products(self.inventory_products_path)
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise ValidationError(
                f"INVENTORY_MAPPING_SOURCE_INVALID: {self.inventory_products_path}"
            ) from exc
        for product in inventory_products:
            identity = listing_identity_key(
                normalized_request["platform_name"],
                product.product_name,
                product.grade,
            )
            if identity in inventory_by_identity:
                ambiguous_inventory_identities.add(identity)
                continue
            inventory_by_identity[identity] = product
        active_listing_filter = str(result.get("active_listing_filter") or "").strip().upper()
        events: list[dict[str, Any]] = []
        observed_mapped_identities: set[tuple[str, str, str]] = set()
        for snapshot in aggregated["product_snapshots"]:
            item_id = str(snapshot.get("item_id") or "")
            target = request_by_item.get(item_id)
            mapping_status = str(snapshot.get("mapping_status") or "MAPPED").strip().upper()
            snapshot_platform = str(
                snapshot.get("platform") or normalized_request["platform_name"]
            ).strip()
            snapshot_variety = str(snapshot.get("product_name") or "").strip()
            snapshot_grade = str(snapshot.get("grade") or "").strip()
            snapshot_identity = listing_identity_key(
                snapshot_platform,
                snapshot_variety,
                snapshot_grade,
            )
            inventory_product = inventory_by_identity.get(snapshot_identity)
            if target is None or mapping_status == "UNMAPPED":
                if snapshot_identity in ambiguous_inventory_identities:
                    events.append(
                        {
                            "item_id": item_id,
                            "warning_code": "INVENTORY_PRODUCT_MAPPING_AMBIGUOUS",
                            "platform_name": snapshot_platform,
                            "variety": snapshot_variety,
                            "grade": snapshot_grade,
                            "status": "WARNING",
                        }
                    )
                    continue
                if inventory_product is not None:
                    target = {
                        "item_id": item_id,
                        "platform": snapshot_platform,
                        "expected_product_name": inventory_product.product_name,
                        "expected_grade": inventory_product.grade,
                        "internal_sku": inventory_product.internal_sku,
                    }
                    mapping_status = "MAPPED"
            if target is None or mapping_status == "UNMAPPED":
                events.append(
                    {
                        "item_id": item_id,
                        "warning_code": "UNMAPPED_PRODUCT_DISCOVERED",
                        "platform_name": str(snapshot.get("platform") or normalized_request["platform_name"]),
                        "variety": str(snapshot.get("product_name") or ""),
                        "grade": str(snapshot.get("grade") or ""),
                        "current_price": str(snapshot.get("price") or ""),
                        "platform_stock_qty": snapshot.get("inventory"),
                        "row_identity": str(snapshot.get("row_identity") or ""),
                        "status": "WARNING",
                    }
                )
                continue
            platform_name = str(target.get("platform") or normalized_request["platform_name"]).strip()
            variety = str(target.get("expected_product_name") or "").strip()
            grade = str(target.get("expected_grade") or "").strip()
            internal_sku = str(
                target.get("internal_sku")
                or (inventory_product.internal_sku if inventory_product is not None else "")
            ).strip()
            identity = listing_identity_key(platform_name, variety, grade)
            observed_mapped_identities.add(identity)
            existing = self.repository.get_listing_status(platform_name, variety, grade)
            if snapshot.get("item_status") != "SUCCESS" or snapshot.get("inventory") is None:
                continue
            raw_price = snapshot.get("price")
            if existing is None and raw_price in (None, ""):
                events.append({"item_id": item_id, "status": "SKIPPED_PRICE_MISSING_FOR_NEW_STATUS"})
                continue
            listing_value = str(snapshot.get("listing_status") or "").upper()
            if existing is None and listing_value == "UNKNOWN":
                events.append({"item_id": item_id, "status": "SKIPPED_LISTING_STATUS_UNKNOWN"})
                continue
            online_status = (
                existing.online_status
                if existing is not None
                else ("online" if listing_value == "ONLINE" else "offline")
            )
            update_status = self.repository.apply_shadowbot_inventory_observation(
                platform_name=platform_name,
                variety=variety,
                grade=grade,
                internal_sku=internal_sku,
                observed_price=(
                    Decimal(str(raw_price))
                    if raw_price not in (None, "")
                    else existing.current_price
                ),
                platform_stock_qty=int(snapshot["inventory"]),
                online_status=online_status,
                observed_at=observed_at,
                execution_attempt_id=execution_attempt_id,
            )
            events.append(
                {
                    "item_id": item_id,
                    "platform_name": platform_name,
                    "variety": variety,
                    "grade": grade,
                    "internal_sku": internal_sku,
                    "platform_stock_qty": int(snapshot["inventory"]),
                    "status": update_status,
                }
            )
        if (
            normalized_request["execution_mode"] == "READ_ONLY"
            and active_listing_filter == "ONLINE"
            and aggregated["overall_status"] == "COMPLETED"
        ):
            for existing in self.repository.list_listing_statuses(
                platform_name=normalized_request["platform_name"]
            ):
                identity = listing_identity_key(
                    existing.platform_name,
                    existing.variety,
                    existing.grade,
                )
                if identity in observed_mapped_identities:
                    continue
                update_status = self.repository.apply_shadowbot_inventory_observation(
                    platform_name=existing.platform_name,
                    variety=existing.variety,
                    grade=existing.grade,
                    observed_price=existing.current_price,
                    platform_stock_qty=0,
                    online_status=existing.online_status,
                    observed_at=observed_at,
                    execution_attempt_id=execution_attempt_id,
                    source="shadowbot_read_not_in_online",
                )
                events.append(
                    {
                        "item_id": "",
                        "platform_name": existing.platform_name,
                        "variety": existing.variety,
                        "grade": existing.grade,
                        "platform_stock_qty": 0,
                        "inference_basis": "ABSENT_FROM_COMPLETE_ONLINE_SNAPSHOT",
                        "status": update_status,
                    }
                )
        return events

    @staticmethod
    def _validate_v2_result(request: dict[str, Any], result: dict[str, Any], result_bytes: bytes) -> None:
        if request.get("contract_version") != 2:
            return
        if len(result_bytes) > MAX_RESULT_BYTES:
            raise ValidationError("RESULT_CONTRACT_INVALID: v2 result exceeds 4 MiB.")
        normalized_request = normalize_multi_product_request(request)
        if result.get("contract_version") != 2 or str(result.get("read_batch_id") or "") != normalized_request["read_batch_id"]:
            raise ValidationError("RESULT_CONTRACT_INVALID: v2 batch identity mismatch.")
        snapshots = result.get("product_snapshots")
        if result.get("status") == "READ_COMPLETED":
            if not isinstance(snapshots, list) or not snapshots:
                raise ValidationError("RESULT_CONTRACT_INVALID: READ_COMPLETED requires product_snapshots.")
            aggregate_product_snapshots(
                read_batch_id=normalized_request["read_batch_id"],
                contract_version=2,
                started_at=str(result.get("started_at") or ""),
                completed_at=str(result.get("ended_at") or result.get("completed_at") or ""),
                snapshots=snapshots,
                expected_item_ids=(
                    None
                    if normalized_request["execution_mode"] == "READ_ONLY"
                    else [product["item_id"] for product in normalized_request["products"]]
                ),
            )
            warnings = result.get("warnings", [])
            if not isinstance(warnings, list):
                raise ValidationError("RESULT_CONTRACT_INVALID: warnings must be an array.")
            for warning in warnings:
                if not isinstance(warning, dict) or str(warning.get("warning_code") or "") != "UNMAPPED_PRODUCT_DISCOVERED":
                    raise ValidationError("RESULT_CONTRACT_INVALID: warning is invalid.")
                if any(
                    not str(warning.get(field) or "").strip()
                    for field in ("item_id", "platform_name", "product_name", "grade", "row_identity")
                ):
                    raise ValidationError("RESULT_CONTRACT_INVALID: unmapped warning identity is incomplete.")
            execution_attempt_id = str(result.get("execution_attempt_id") or "")
            for snapshot in snapshots:
                evidence = snapshot.get("evidence")
                # Section 17: screenshots/evidence are diagnostic opt-ins, not
                # a production success prerequisite.  Preserve strict binding
                # validation whenever evidence is actually supplied.
                if evidence is None or evidence == []:
                    continue
                if not isinstance(evidence, list):
                    raise ValidationError("RESULT_CONTRACT_INVALID: evidence must be an array when present.")
                if str(snapshot.get("error_code") or "").upper() in {"EVIDENCE_UNAVAILABLE", "EVIDENCE_BINDING_FAILED"}:
                    continue
                validate_evidence_binding(
                    evidence,
                    read_batch_id=normalized_request["read_batch_id"],
                    item_id=str(snapshot.get("item_id") or ""),
                    execution_attempt_id=execution_attempt_id,
                )
    def _find_request(self, execution_attempt_id: str, recorded_path: str) -> Path:
        candidates = [
            self.paths.working / f"{execution_attempt_id}.request.json",
            self.paths.inbox / f"{execution_attempt_id}.ready.json",
        ]
        if recorded_path:
            candidates.append(Path(recorded_path))
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise ValidationError("RESULT_CONTRACT_INVALID: source request file is missing.")

    def _archive_attempt(self, execution_attempt_id: str, request_path: Path, result_path: Path) -> Path:
        archive_dir = self.paths.archive / execution_attempt_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        related = (
            request_path,
            request_path.with_suffix(request_path.suffix + ".sha256"),
            self.paths.working / f"{execution_attempt_id}.phase.json",
            result_path,
            result_path.with_suffix(result_path.suffix + ".sha256"),
        )
        for source in related:
            destination = archive_dir / source.name
            if source.resolve() == destination.resolve():
                continue
            if source.exists() and destination.exists() and source.read_bytes() != destination.read_bytes():
                raise ValidationError(
                    f"RESULT_CONTRACT_INVALID: archive evidence conflict for {source.name}."
                )
        for source in related:
            if source.exists():
                destination = archive_dir / source.name
                if source.resolve() == destination.resolve():
                    continue
                if destination.exists():
                    source.unlink()
                else:
                    os.replace(source, destination)
        return archive_dir

    def _quarantine(self, result_path: Path, error: Exception) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.paths.quarantine / f"{stamp}-{result_path.name}"
        os.replace(result_path, destination)
        try:
            quarantined_data = json.loads(destination.read_text(encoding="utf-8-sig"))
            execution_attempt_id = str(quarantined_data.get("execution_attempt_id") or "")
            if execution_attempt_id:
                self.repository.quarantine_shadowbot_attempt(
                    execution_attempt_id,
                    reason=type(error).__name__ + ":" + str(error),
                    now=datetime.now(UTC),
                )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        checksum = result_path.with_suffix(result_path.suffix + ".sha256")
        if checksum.exists():
            os.replace(checksum, destination.with_suffix(destination.suffix + ".sha256"))
        reason_path = destination.with_suffix(destination.suffix + ".error.json")
        _atomic_write(
            reason_path,
            _json_bytes(
                {
                    "error_code": "RESULT_CONTRACT_INVALID",
                    "error_message": str(error),
                    "quarantined_at": datetime.now(UTC).isoformat(),
                    "source_path": str(result_path),
                }
            ),
        )
        return destination


class ShadowBotLoginVerificationMonitor:
    """Observes active login-verification phases and delegates handoff creation to Executor."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        runner: ShadowBotTaskRunner,
        queue_dir: Path,
    ) -> None:
        self.executor = ShadowBotExecutor(repository, runner)
        self.paths = ShadowBotQueuePaths(queue_dir)
        self.paths.ensure()

    def inspect(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for phase_path in sorted(self.paths.working.glob("*.phase.json")):
            phase_data = _read_json_object(phase_path)
            if str(phase_data.get("phase") or "") != "LOGIN_VERIFICATION_REQUIRED":
                continue
            review_task_id = self.executor.open_login_verification_handoff(phase_data)
            events.append(
                {
                    "status": "LOGIN_VERIFICATION_HANDOFF_OPEN",
                    "execution_attempt_id": str(phase_data.get("execution_attempt_id") or ""),
                    "review_task_id": review_task_id,
                }
            )
        return events


class ShadowBotQueueWatchdog:
    """Classify stale workers and working attempts. It never imports result files."""

    def __init__(
        self,
        queue_dir: Path,
        *,
        stale_seconds: int = 30,
        repository: SQLiteRuntimeRepository | None = None,
    ) -> None:
        self.paths = ShadowBotQueuePaths(queue_dir)
        self.paths.ensure()
        self.stale_seconds = stale_seconds
        self.repository = repository
        self._last_heartbeat_alert_key = ""

    def inspect(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or datetime.now(UTC)
        heartbeat_stale = self._heartbeat_stale(current)
        events = self._inspect_inbox_integrity()
        if not heartbeat_stale:
            self._last_heartbeat_alert_key = ""
            return events
        for phase_path in sorted(self.paths.working.glob("*.phase.json")):
            phase_data = _read_json_object(phase_path)
            if not _is_timestamp_stale(phase_data.get("updated_at"), current, self.stale_seconds):
                continue
            event = self._recover_phase(phase_path, phase_data)
            if event is not None:
                events.append(event)
        phase_attempts = {path.name.removesuffix(".phase.json") for path in self.paths.working.glob("*.phase.json")}
        for request_path in sorted(self.paths.working.glob("*.request.json")):
            execution_attempt_id = request_path.name.removesuffix(".request.json")
            if execution_attempt_id in phase_attempts or not _is_file_stale(request_path, current, self.stale_seconds):
                continue
            request_data = _read_json_object(request_path)
            phase_data = {
                "request_file_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
                "side_effect_state": SIDE_EFFECT_NOT_STARTED,
            }
            events.append(self._write_recovery_result(request_data, phase_data, phase="CLAIMED"))
        if not events and self.paths.heartbeat.exists():
            heartbeat = _read_json_object(self.paths.heartbeat)
            if str(heartbeat.get("status") or "") == "RUNNING":
                alert_key = "%s|%s" % (heartbeat.get("worker_id", ""), heartbeat.get("updated_at", ""))
                if alert_key != self._last_heartbeat_alert_key:
                    events.append(
                        {
                            "status": "WARNING",
                            "error_code": "WORKER_HEARTBEAT_STALE",
                            "worker_id": str(heartbeat.get("worker_id") or ""),
                            "heartbeat_updated_at": str(heartbeat.get("updated_at") or ""),
                            "stale_seconds": self.stale_seconds,
                        }
                    )
                    self._last_heartbeat_alert_key = alert_key
        return events

    def _inspect_inbox_integrity(self) -> list[dict[str, Any]]:
        if self.repository is None:
            return []
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for request_path in sorted(self.paths.inbox.glob("*.ready.json")):
            try:
                request = _read_json_object(request_path)
                attempt_id = str(request.get("execution_attempt_id") or "").strip()
                if not attempt_id:
                    raise ValidationError("ready request has no execution_attempt_id")
                duplicate = attempt_id in seen or (self.paths.working / f"{attempt_id}.request.json").exists()
                seen.add(attempt_id)
                if duplicate:
                    reason = "DUPLICATE_READY_REQUEST"
                    target_root = self.paths.quarantine
                elif request.get("contract_version") == COMMIT_BATCH_CONTRACT_VERSION:
                    validate_commit_batch_request(request, check_expiry=False)
                    with self.repository.connect_read() as connection:
                        batch = connection.execute(
                            """
                            SELECT status, execution_attempt_id, instruction_hash,
                                   manifest_sha256
                            FROM shadowbot_commit_batches
                            WHERE batch_id = ?
                            """,
                            (str(request.get("batch_id") or ""),),
                        ).fetchone()
                    if (
                        batch is None
                        or str(batch["execution_attempt_id"] or "") != attempt_id
                        or str(batch["instruction_hash"] or "")
                        != str(request.get("instruction_hash") or "")
                        or str(batch["manifest_sha256"] or "")
                        != str(request.get("manifest_sha256") or "")
                    ):
                        reason = "ORPHAN_READY_REQUEST"
                        target_root = self.paths.quarantine
                    elif str(batch["status"] or "") in {
                        "VERIFIED",
                        "PARTIAL",
                        "FAILED",
                    }:
                        reason = "STALE_TERMINAL_READY_REQUEST"
                        target_root = self.paths.archive / attempt_id
                        target_root.mkdir(parents=True, exist_ok=True)
                    elif str(batch["status"] or "") == "UNKNOWN":
                        reason = "FROZEN_READY_REQUEST"
                        target_root = self.paths.quarantine
                    elif str(batch["status"] or "") in {
                        "PUBLISHING",
                        "QUEUED",
                        "RUNNING",
                    }:
                        continue
                    else:
                        reason = "ORPHAN_READY_REQUEST"
                        target_root = self.paths.quarantine
                else:
                    attempt = self.repository.get_shadowbot_execution_attempt(attempt_id)
                    if attempt is None:
                        reason = "ORPHAN_READY_REQUEST"
                        target_root = self.paths.quarantine
                    else:
                        operation = self.repository.get_shadowbot_operation(attempt.operation_id)
                        if operation is not None and operation.status in {"VERIFIED", "MANUAL_HANDLED"}:
                            reason = "STALE_TERMINAL_READY_REQUEST"
                            target_root = self.paths.archive / attempt_id
                            target_root.mkdir(parents=True, exist_ok=True)
                        elif operation is not None and operation.status in {
                            "NEEDS_RECONCILIATION",
                            "MANUAL_REVIEW",
                        }:
                            reason = "FROZEN_READY_REQUEST"
                            target_root = self.paths.quarantine
                        else:
                            continue
                destination = target_root / f"{reason.lower()}-{request_path.name}"
                os.replace(request_path, destination)
                checksum = request_path.with_suffix(request_path.suffix + ".sha256")
                if checksum.exists():
                    os.replace(checksum, destination.with_suffix(destination.suffix + ".sha256"))
                events.append(
                    {
                        "status": "ARCHIVED" if target_root != self.paths.quarantine else "QUARANTINED",
                        "error_code": reason,
                        "execution_attempt_id": attempt_id,
                        "path": str(destination),
                    }
                )
            except (ValidationError, ValueError, json.JSONDecodeError):
                destination = self.paths.quarantine / f"invalid-ready-{request_path.name}"
                os.replace(request_path, destination)
                events.append(
                    {
                        "status": "QUARANTINED",
                        "error_code": "READY_CONTRACT_INVALID",
                        "path": str(destination),
                    }
                )
        return events

    def _heartbeat_stale(self, now: datetime) -> bool:
        if not self.paths.heartbeat.exists():
            return True
        heartbeat = _read_json_object(self.paths.heartbeat)
        return _is_timestamp_stale(heartbeat.get("updated_at"), now, self.stale_seconds)

    def _recover_phase(self, phase_path: Path, phase_data: dict[str, Any]) -> dict[str, Any] | None:
        execution_attempt_id = str(phase_data.get("execution_attempt_id") or "")
        if not execution_attempt_id:
            raise ValidationError(f"phase file has no execution_attempt_id: {phase_path}")
        result_path = self.paths.results / f"{execution_attempt_id}.result.json"
        if result_path.exists() or str(phase_data.get("phase") or "") == "RESULT_WRITTEN":
            return None
        request_path = self.paths.working / f"{execution_attempt_id}.request.json"
        request_data = _read_json_object(request_path)
        phase_data.setdefault("request_file_sha256", hashlib.sha256(request_path.read_bytes()).hexdigest())
        phase = str(phase_data.get("phase") or "CLAIMED")
        if request_data.get("contract_version") == COMMIT_BATCH_CONTRACT_VERSION:
            return self._write_v4_recovery_result(request_data, phase_data, phase=phase)
        if phase == "VERIFIED" and isinstance(phase_data.get("result_snapshot"), dict):
            return self._write_result(dict(phase_data["result_snapshot"]), execution_attempt_id)
        return self._write_recovery_result(request_data, phase_data, phase=phase)

    def _write_recovery_result(
        self,
        request: dict[str, Any],
        phase_data: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        if request.get("contract_version") == COMMIT_BATCH_CONTRACT_VERSION:
            return self._write_v4_recovery_result(request, phase_data, phase=phase)
        execution_mode = str(request.get("execution_mode") or "")
        has_submit_risk = phase in SUBMIT_PHASES or phase == "VERIFIED" or (
            execution_mode == EXECUTION_MODE_COMMIT and str(phase_data.get("side_effect_state") or "") != SIDE_EFFECT_NOT_STARTED
        )
        cleanup_confirmed = bool(phase_data.get("cleanup_confirmed", False))
        not_applied_evidence = (
            str(phase_data.get("side_effect_state") or "") == SIDE_EFFECT_NOT_APPLIED
            and bool(phase_data.get("evidence_hash"))
            and (phase != "TARGET_FILLED" or cleanup_confirmed)
        )
        if has_submit_risk:
            result_status = STATUS_SIDE_EFFECT_UNKNOWN
            side_effect_state = SIDE_EFFECT_UNKNOWN
        elif execution_mode == EXECUTION_MODE_COMMIT and not not_applied_evidence:
            result_status = STATUS_START_UNKNOWN
            side_effect_state = SIDE_EFFECT_NOT_STARTED
        else:
            result_status = STATUS_FAILED
            side_effect_state = SIDE_EFFECT_NOT_APPLIED if not_applied_evidence else SIDE_EFFECT_NOT_STARTED
        retryable = execution_mode != EXECUTION_MODE_COMMIT and phase in PRE_SUBMIT_PHASES
        result = {
            "schema_version": "shadowbot-result-1.0",
            "task_id": request.get("task_id", ""),
            "operation_id": request.get("operation_id", ""),
            "execution_attempt_id": request.get("execution_attempt_id", ""),
            "execution_mode": execution_mode,
            "instruction_hash": request.get("instruction_hash", ""),
            "request_file_sha256": phase_data.get("request_file_sha256", ""),
            "lease_owner_token": request.get("lease_owner_token", ""),
            "lease_version": request.get("lease_version", 0),
            "worker_id": phase_data.get("worker_id", ""),
            "status": result_status,
            "run_success_flag": None if result_status in {STATUS_START_UNKNOWN, STATUS_SIDE_EFFECT_UNKNOWN} else False,
            "business_operation_completed": None if result_status in {STATUS_START_UNKNOWN, STATUS_SIDE_EFFECT_UNKNOWN} else False,
            "side_effect_state": side_effect_state,
            "error_code": "SUBMIT_RESULT_UNKNOWN" if has_submit_risk else "WORKER_INTERRUPTED",
            "error_message": f"stale ShadowBot working attempt recovered at phase {phase}",
            "retryable": retryable,
            "recovered_phase": phase,
            "ended_at": datetime.now(UTC).isoformat(),
        }
        return self._write_result(result, str(request.get("execution_attempt_id") or ""))

    def _write_v4_recovery_result(
        self,
        request: dict[str, Any],
        phase_data: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        """Recover a stale v4 batch without losing completed-item boundaries."""

        validate_commit_batch_request(request, check_expiry=False)
        request_items = [dict(item) for item in request.get("items") or []]
        items = [self._v4_recovery_item(item) for item in request_items]
        by_task_id = {str(item["source_task_id"]): item for item in items}

        snapshot = phase_data.get("batch_result_snapshot")
        if not isinstance(snapshot, dict):
            candidate = phase_data.get("result_snapshot")
            if isinstance(candidate, dict) and candidate.get("contract_version") == COMMIT_BATCH_CONTRACT_VERSION:
                snapshot = candidate
        if isinstance(snapshot, dict):
            for snapshot_item in snapshot.get("items") or []:
                if not isinstance(snapshot_item, dict):
                    continue
                target = by_task_id.get(str(snapshot_item.get("source_task_id") or ""))
                if target is None:
                    continue
                for name in (
                    "preflight_row",
                    "preflight_price",
                    "execution_ordinal",
                    "submit_attempted",
                    "side_effect_state",
                    "preflight_observed_at",
                    "submit_intent_at",
                    "submit_clicked_at",
                    "readback_observed_at",
                    "actual_price",
                    "status",
                    "error_code",
                    "error_message",
                ):
                    if name in snapshot_item:
                        target[name] = snapshot_item[name]
        page_snapshot = None
        if isinstance(snapshot, dict) and isinstance(snapshot.get("page_snapshot"), dict):
            page_snapshot = json.loads(
                json.dumps(snapshot["page_snapshot"], ensure_ascii=False)
            )

        current_task_id = str(phase_data.get("current_source_task_id") or "").strip()
        current = by_task_id.get(current_task_id)
        if current is not None:
            if not current.get("execution_ordinal") and phase_data.get("execution_ordinal"):
                current["execution_ordinal"] = int(phase_data["execution_ordinal"])
            child_snapshot = phase_data.get("result_snapshot")
            item_phase = phase_data.get("item_phase")
            if isinstance(item_phase, dict):
                for name in (
                    "side_effect_state",
                    "submit_intent_at",
                    "submit_clicked_at",
                    "readback_observed_at",
                ):
                    if item_phase.get(name) not in (None, ""):
                        current[name] = item_phase[name]
            child_verified = (
                phase == "VERIFIED"
                and isinstance(child_snapshot, dict)
                and str(child_snapshot.get("status") or "").upper() == "VERIFIED"
                and str(child_snapshot.get("side_effect_state") or "").upper() == "VERIFIED"
                and str(child_snapshot.get("actual_price") or "") == str(current["target_price"])
            )
            side_effect = str(phase_data.get("side_effect_state") or SIDE_EFFECT_NOT_STARTED).upper()
            has_submit_risk = phase in SUBMIT_PHASES or phase == "VERIFIED" or side_effect != SIDE_EFFECT_NOT_STARTED
            if child_verified:
                current.update(
                    {
                        "submit_attempted": True,
                        "side_effect_state": "VERIFIED",
                        "actual_price": child_snapshot.get("actual_price"),
                        "submit_intent_at": child_snapshot.get("submit_intent_at"),
                        "submit_clicked_at": child_snapshot.get("submit_clicked_at"),
                        "readback_observed_at": child_snapshot.get("readback_observed_at"),
                        "status": "VERIFIED",
                        "error_code": "",
                        "error_message": "",
                    }
                )
            elif has_submit_risk:
                current.update(
                    {
                        "submit_attempted": True,
                        "side_effect_state": "UNKNOWN",
                        "submit_intent_at": current.get("submit_intent_at")
                        or (
                            phase_data.get("updated_at")
                            if phase == "SUBMIT_INTENT_RECORDED"
                            else None
                        ),
                        "submit_clicked_at": current.get("submit_clicked_at")
                        or (
                            phase_data.get("updated_at")
                            if phase in {"SUBMIT_CLICKED", "VERIFIED"}
                            else None
                        ),
                        "status": "UNKNOWN",
                        "error_code": "SUBMIT_RESULT_UNKNOWN",
                        "error_message": f"stale ShadowBot v4 batch recovered at phase {phase}",
                    }
                )
            if isinstance(page_snapshot, dict):
                for product in page_snapshot.get("products") or []:
                    identity_matches = (
                        str(product.get("product_name") or "").strip()
                        == str(current.get("expected_product_name") or "").strip()
                        and str(product.get("grade") or "").strip()
                        == str(current.get("expected_grade") or "").strip()
                    )
                    if not identity_matches:
                        continue
                    if child_verified:
                        product["price"] = str(current.get("actual_price") or "")
                        product["price_status"] = "VERIFIED_AFTER_COMMIT"
                    elif has_submit_risk:
                        product["price_status"] = "UNKNOWN_AFTER_SUBMIT"
                    break

        counts = self._v4_recovery_counts(items)
        if counts["verified"] == counts["total"]:
            batch_status = "VERIFIED"
        elif counts["verified"]:
            batch_status = "PARTIAL"
        elif counts["unknown"]:
            batch_status = "UNKNOWN"
        else:
            batch_status = "FAILED"
        result = {
            "schema_version": "shadowbot-commit-batch-result-1.1",
            "contract_version": COMMIT_BATCH_CONTRACT_VERSION,
            "task_id": request.get("task_id", ""),
            "operation_id": request.get("operation_id", ""),
            "execution_attempt_id": request.get("execution_attempt_id", ""),
            "execution_mode": "COMMIT",
            "batch_id": request.get("batch_id", ""),
            "manifest_sha256": request.get("manifest_sha256", ""),
            "instruction_hash": request.get("instruction_hash", ""),
            "request_file_sha256": phase_data.get("request_file_sha256", ""),
            "worker_id": phase_data.get("worker_id", ""),
            "batch_status": batch_status,
            "status": batch_status,
            "run_success_flag": batch_status == "VERIFIED",
            "business_operation_completed": counts["attempted"] > 0,
            "side_effect_state": (
                SIDE_EFFECT_UNKNOWN
                if counts["unknown"]
                else "VERIFIED"
                if counts["verified"]
                else SIDE_EFFECT_NOT_STARTED
            ),
            "error_code": "SUBMIT_RESULT_UNKNOWN" if counts["unknown"] else "WORKER_INTERRUPTED",
            "error_message": f"stale ShadowBot v4 batch recovered at phase {phase}",
            "retryable": False,
            "recovered_phase": phase,
            "items": items,
            "counts": counts,
            "ended_at": datetime.now(UTC).isoformat(),
        }
        if isinstance(page_snapshot, dict):
            result["page_snapshot"] = page_snapshot
        return self._write_result(result, str(request.get("execution_attempt_id") or ""))

    @staticmethod
    def _v4_recovery_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            **item,
            "preflight_row": None,
            "preflight_price": None,
            "execution_ordinal": None,
            "submit_attempted": False,
            "side_effect_state": "NOT_STARTED",
            "preflight_observed_at": None,
            "submit_intent_at": None,
            "submit_clicked_at": None,
            "readback_observed_at": None,
            "actual_price": None,
            "status": "NOT_ATTEMPTED",
            "error_code": "",
            "error_message": "",
        }

    @staticmethod
    def _v4_recovery_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "total": len(items),
            "attempted": 0,
            "verified": 0,
            "not_applied": 0,
            "failed": 0,
            "unknown": 0,
            "not_attempted": 0,
        }
        for item in items:
            if item.get("submit_attempted") is True:
                counts["attempted"] += 1
            status = str(item.get("status") or "NOT_ATTEMPTED").lower()
            if status not in counts or status in {"total", "attempted"}:
                status = "not_attempted"
                item["status"] = "NOT_ATTEMPTED"
            counts[status] += 1
        return counts

    def _write_result(self, result: dict[str, Any], execution_attempt_id: str) -> dict[str, Any]:
        if not execution_attempt_id:
            raise ValidationError("cannot recover working attempt without execution_attempt_id.")
        if not result.get("result_id"):
            identity = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            result["result_id"] = "RESULT-" + hashlib.sha256(identity).hexdigest()[:24]
        result_path = self.paths.results / f"{execution_attempt_id}.result.json"
        content = _json_bytes(result)
        _atomic_write(result_path.with_suffix(result_path.suffix + ".sha256"), (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"))
        _atomic_write(result_path, content)
        return {
            "status": "RECOVERY_RESULT_WRITTEN",
            "execution_attempt_id": execution_attempt_id,
            "result_path": str(result_path),
        }


def _verify_checksum(path: Path, content: bytes) -> None:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if not checksum_path.exists():
        raise ValidationError(f"checksum file is missing: {checksum_path}")
    expected = checksum_path.read_text(encoding="ascii").strip().lower()
    actual = hashlib.sha256(content).hexdigest()
    if expected != actual:
        raise ValidationError(f"checksum mismatch: {path}")


def _automatic_reconcile_payload(request: dict[str, Any]) -> dict[str, Any]:
    """Carry verified execution context into an Executor-owned RECONCILE request."""
    return {
        field_name: value
        for field_name in ("evidence_share_dir", "applet_uri", "window_title")
        if (value := request.get(field_name)) not in (None, "")
    }


def _read_json_object(path: Path, *, attempts: int = 8) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(attempts, 1)):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValidationError(f"JSON file must contain an object: {path}")
            return data
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.025 * (2**attempt), 0.4))
                continue
            raise
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.025 * (2**attempt), 0.4))
                continue
            raise
    raise RuntimeError(f"cannot read queue JSON {path}: {last_error}")


def _is_timestamp_stale(value: Any, now: datetime, stale_seconds: int) -> bool:
    if not value:
        return True
    try:
        updated = datetime.fromisoformat(str(value))
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (now - updated.astimezone(UTC)).total_seconds() > stale_seconds


def _is_file_stale(path: Path, now: datetime, stale_seconds: int) -> bool:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return (now - modified).total_seconds() > stale_seconds


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the staging filename short.  A long destination plus the previous
    # ``<name>.tmp-<uuid>`` suffix can exceed Windows MAX_PATH before the
    # final replace, even though the destination itself is valid.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
