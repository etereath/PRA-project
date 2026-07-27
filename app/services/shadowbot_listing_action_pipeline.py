"""Task 13 write-action preparation, publication, and result projection."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.listing_automation_gate import (
    evaluate_automation_gate,
    review_block_reasons,
)
from app.services.shadowbot_executor import (
    ShadowBotFileQueueRunner,
    ShadowBotStartBoundaryError,
    ShadowBotStartResult,
)
from app.services.shadowbot_listing_action_contract import (
    build_listing_action_reconcile_request,
    build_listing_action_manifest,
    build_listing_action_request,
    required_development_confirmation,
    validate_listing_action_request,
    validate_listing_action_result,
)
from app.services.shadowbot_listing_sync import (
    _apply_listing_anomalies,
    _assert_queue_ready_for_publication,
    _assert_snapshot_not_stale,
    _insert_snapshot,
    _insert_snapshot_items,
    _project_online_status,
    mapping_source_version,
)
from app.shadowbot_listing_contract import V5_GATE_SUMMARY_SCHEMA_VERSION


def load_identity_mapping(mapping_path: Path) -> dict[str, dict[str, str]]:
    """Load the active SKU-to-page mapping with explicit UTF-8 decoding."""

    try:
        payload = json.loads(Path(mapping_path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("商品身份映射文件无法读取。") from exc
    mappings: dict[str, dict[str, str]] = {}
    for raw in payload.get("mappings") or []:
        if str(raw.get("status") or "active").strip().lower() != "active":
            continue
        sku = str(raw.get("internal_sku") or "").strip().upper()
        if not sku or sku in mappings:
            raise ValidationError("商品身份映射存在空 SKU 或重复 SKU。")
        mappings[sku] = {
            "expected_product_name": str(
                raw.get("expected_product_name") or ""
            ).strip(),
            "expected_grade": str(raw.get("expected_grade") or "").strip(),
        }
    return mappings


def propose_listing_action_batch(
    repository: SQLiteRuntimeRepository,
    *,
    batch_id: str,
    task_ids: list[str],
    mapping_path: Path,
    execution_profile: str = "development",
) -> dict[str, Any]:
    """Build an immutable proposal and evaluate gates without creating a batch."""

    if str(execution_profile).strip().lower() not in {
        "development",
        "production",
    }:
        raise ValidationError("execution_profile 必须是 development 或 production。")
    if not task_ids:
        raise ValidationError("至少需要一个任务。")
    tasks = []
    action_type = ""
    platform_name = ""
    now = datetime.now(UTC)
    for task_id in task_ids:
        task = repository.get_task(str(task_id))
        if task is None:
            raise ValidationError("任务不存在：" + str(task_id))
        if task.task_status is not TaskStatus.PENDING:
            raise ValidationError("任务不是 pending：" + task.task_id)
        if task.action_type not in {
            TaskActionType.SET_ONLINE,
            TaskActionType.SET_OFFLINE,
        }:
            raise ValidationError("任务不是上下架动作：" + task.task_id)
        if task.expires_at is None or _as_utc(task.expires_at) <= now:
            raise ValidationError("任务已经过期：" + task.task_id)
        current_action = task.action_type.value
        current_platform = str(task.platform_name or "").strip()
        if not action_type:
            action_type = current_action
            platform_name = current_platform
        if current_action != action_type or current_platform != platform_name:
            raise ValidationError("同一批次只能包含同平台、同动作任务。")
        if not task.internal_sku:
            raise ValidationError("任务缺少 internal_sku：" + task.task_id)
        item = {
            "source_task_id": task.task_id,
            "internal_sku": task.internal_sku,
            "expected_old_status": (
                "offline" if current_action == "set_online" else "online"
            ),
            "target_status": (
                "online" if current_action == "set_online" else "offline"
            ),
            "expires_at": _as_utc(task.expires_at).isoformat(),
        }
        if current_action == "set_online":
            if task.target_price is None or task.target_inventory is None:
                raise ValidationError(
                    "SET_ONLINE 任务缺少目标价格或库存：" + task.task_id
                )
            item["target_price"] = f"{Decimal(task.target_price):.2f}"
            item["target_inventory"] = int(task.target_inventory)
        tasks.append(item)

    mapping = load_identity_mapping(mapping_path)
    manifest = build_listing_action_manifest(
        batch_id=batch_id,
        action_type=action_type,
        task_items=tasks,
        identity_mapping=mapping,
        platform_name=platform_name,
        mapping_source_version=mapping_source_version(mapping_path),
    )
    gate_items = []
    corrective_authorizations: list[dict[str, str]] = []
    with closing(repository.connect_read()) as connection:
        corrective_authorizations = _corrective_retry_authorizations(
            connection,
            platform_name=platform_name,
            action_type=action_type,
            items=manifest["items"],
        )
        corrective_by_key = {
            item["write_identity_key"]: item
            for item in corrective_authorizations
        }
        for item in manifest["items"]:
            context = _latest_listing_context(
                connection,
                platform_name=platform_name,
                internal_sku=item["internal_sku"],
                action_type=action_type,
            )
            reviews = _open_review_context(
                connection,
                platform_name=platform_name,
                internal_sku=item["internal_sku"],
            )
            locks = _write_lock_context(
                connection,
                write_identity_key=item["write_identity_key"],
            )
            corrective = corrective_by_key.get(item["write_identity_key"])
            if corrective is not None:
                locks = [
                    lock
                    for lock in locks
                    if lock["operation_id"]
                    != corrective["previous_operation_id"]
                ]
            gate = evaluate_automation_gate(
                action_type=action_type,
                internal_sku=item["internal_sku"],
                gate_phase="PRE_PUBLISH",
                online_status=context["online_status"],
                listing_location=context["listing_location"],
                snapshot_valid=context["snapshot_valid"],
                fresh_sync_required=False,
                online_scan_complete=context["online_scan_complete"],
                online_occurrences=context["online_occurrences"],
                observed_price=context["observed_price"],
                observed_inventory=context["observed_inventory"],
                target_price=item.get("target_price"),
                target_inventory=item.get("target_inventory"),
                open_reviews=reviews,
                write_locks=locks,
            )
            gate_items.append(
                {
                    "internal_sku": item["internal_sku"],
                    "operation_id": item["operation_id"],
                    "decision": gate.decision,
                    "block_reasons": list(
                        gate.block_reasons_by_action.get(action_type, ())
                    ),
                    "snapshot_id": context["snapshot_id"],
                    "listing_location": context["listing_location"],
                    "observed_price": context["observed_price"],
                    "observed_inventory": context["observed_inventory"],
                    "corrective_retry_review_task_id": (
                        corrective["review_task_id"]
                        if corrective is not None
                        else ""
                    ),
                }
            )
    return {
        "manifest": manifest,
        "execution_profile": str(execution_profile).strip().lower(),
        "gate_items": gate_items,
        "corrective_authorizations": corrective_authorizations,
        "publishable": all(
            item["decision"] in {"EXECUTE", "ALREADY_APPLIED"}
            for item in gate_items
        ),
        "required_confirmation": (
            required_development_confirmation(batch_id, len(manifest["items"]))
            if str(execution_profile).strip().lower() == "development"
            else ""
        ),
    }


def publish_listing_action_batch(
    repository: SQLiteRuntimeRepository,
    runner: ShadowBotFileQueueRunner,
    *,
    proposal: dict[str, Any],
    applet_uri: str,
    confirmation_text: str = "",
    confirmed_by: str = "",
    execution_attempt_id: str | None = None,
    window_title: str = "蚂蚁花团供应商",
    capture_evidence: bool = False,
    fault_injection: str = "",
    fault_injection_item_ordinal: int | None = None,
) -> tuple[dict[str, Any], ShadowBotStartResult]:
    """Create the COMMIT ledger and publish exactly one immutable request."""

    manifest = dict(proposal["manifest"])
    if not proposal.get("publishable"):
        raise ValidationError("批次门禁未通过，不得创建 COMMIT。")
    _assert_queue_ready_for_publication(runner)
    attempt_id = execution_attempt_id or f"ATTEMPT-{uuid4().hex[:16]}"
    gate_summary = {
        "schema_version": V5_GATE_SUMMARY_SCHEMA_VERSION,
        "gate_phase": "PRE_PUBLISH",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "items": [
            {
                "internal_sku": item["internal_sku"],
                "operation_id": item["operation_id"],
                "decision": gate["decision"],
                "lock_status": "ACTIVE",
                "lock_operation_id": item["operation_id"],
                "block_reasons": [],
            }
            for item, gate in zip(
                manifest["items"],
                proposal["gate_items"],
                strict=True,
            )
        ],
    }
    batch_task_id = _stable_id("BATCH-TASK", manifest["batch_id"])
    batch_operation_id = _stable_id("BATCH-OP", manifest["batch_id"])
    request = build_listing_action_request(
        manifest,
        execution_profile=proposal["execution_profile"],
        execution_attempt_id=attempt_id,
        applet_uri=applet_uri,
        gate_summary=gate_summary,
        batch_task_id=batch_task_id,
        batch_operation_id=batch_operation_id,
        confirmation_text=confirmation_text,
        confirmed_by=confirmed_by,
        window_title=window_title,
        capture_evidence=capture_evidence,
        fault_injection=fault_injection,
        fault_injection_item_ordinal=fault_injection_item_ordinal,
    )
    validate_listing_action_request(request)
    _persist_prepared_write_batch(
        repository,
        request,
        corrective_authorizations=proposal.get(
            "corrective_authorizations",
            [],
        ),
    )
    try:
        start_result = runner.start(request)
    except ShadowBotStartBoundaryError as exc:
        _record_publish_failure(repository, request, published=exc.published)
        raise
    _record_publish_success(repository, request, start_result)
    return request, start_result


def import_listing_action_result(
    repository: SQLiteRuntimeRepository,
    *,
    request: dict[str, Any],
    result: dict[str, Any],
    result_file_sha256: str,
    source_result_path: str,
) -> dict[str, Any]:
    """Atomically import one v5 write result and release or retain each lock."""

    validate_listing_action_request(request, check_expiry=False)
    validate_listing_action_result(
        result,
        request=request,
        request_file_sha256=str(result.get("request_file_sha256") or ""),
    )
    if request["action_type"] not in {"set_online", "set_offline"}:
        raise ValidationError("写结果导入器不接受 SYNC_STATUS。")
    if str(request.get("execution_mode") or "").upper() == "RECONCILE":
        return _import_listing_action_reconcile_result(
            repository,
            request=request,
            result=result,
            result_file_sha256=result_file_sha256,
            source_result_path=source_result_path,
        )
    if not _is_hex_sha256(result_file_sha256):
        raise ValidationError("result_file_sha256 无效。")
    result_id = str(result.get("result_id") or "").strip()
    now_value = datetime.now(UTC)
    now = now_value.isoformat()
    connection = repository.connect_write()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT result_sha256 FROM shadowbot_listing_result_receipts "
            "WHERE result_id = ?",
            (result_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["result_sha256"]) != result_file_sha256:
                raise ValidationError("同一 result_id 对应不同结果。")
            connection.rollback()
            review_summary = _ensure_manual_review_intents(
                repository,
                request=request,
            )
            return {
                "batch_id": request["batch_id"],
                "result_id": result_id,
                "status": result["batch_status"],
                "already_imported": True,
                "manual_review_summary": review_summary,
            }
        connection.execute(
            """
            INSERT INTO shadowbot_listing_result_receipts(
                result_id, batch_id, execution_attempt_id, instruction_hash,
                manifest_sha256, result_sha256, source_result_path,
                accepted_at, ack_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                result_id,
                request["batch_id"],
                request["execution_attempt_id"],
                request["instruction_hash"],
                request["manifest_sha256"],
                result_file_sha256,
                str(source_result_path or ""),
                now,
            ),
        )
        projected: list[str] = []
        post_failure_snapshot = result.get("post_failure_snapshot")
        if isinstance(post_failure_snapshot, dict):
            _insert_snapshot(
                connection,
                request["batch_id"],
                post_failure_snapshot,
            )
            if post_failure_snapshot["snapshot_complete"] is True:
                _assert_snapshot_not_stale(connection, post_failure_snapshot)
                post_failure_items = _insert_snapshot_items(
                    connection,
                    post_failure_snapshot,
                )
                post_failure_projected = _project_online_status(
                    connection,
                    post_failure_snapshot,
                    post_failure_items,
                    projection_source="shadowbot_commit_recovery_scan",
                    source_type="COMMIT_POST_FAILURE_SCAN",
                )
                projected.extend(
                    str(item["internal_sku"])
                    for item in post_failure_projected
                )
                _apply_listing_anomalies(
                    repository,
                    connection,
                    snapshot=post_failure_snapshot,
                    item_rows=post_failure_items,
                    now=now_value,
                )
        request_by_operation = {
            item["operation_id"]: item for item in request["items"]
        }
        for output in result["items"]:
            request_item = request_by_operation[output["operation_id"]]
            outcome = str(output["operation_result"]).upper()
            stored_operation_result = (
                "VERIFIED"
                if outcome == "ALREADY_APPLIED"
                else ""
                if outcome == "NOT_ATTEMPTED"
                else "NOT_APPLIED"
                if outcome == "FAILED"
                else outcome
            )
            connection.execute(
                """
                UPDATE shadowbot_listing_action_batch_items
                SET detail_effect_state = ?, listing_effect_state = ?,
                    observed_price_before_action = ?,
                    observed_inventory_before_action = ?,
                    observed_price_after_detail_save = ?,
                    observed_inventory_after_detail_save = ?,
                    detail_save_clicked_at = ?, action_clicked_at = ?,
                    readback_observed_at = ?, operation_result = ?,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE item_id = ? AND batch_id = ?
                """,
                (
                    output.get("detail_effect_state", "NOT_STARTED"),
                    output.get("listing_effect_state", "NOT_STARTED"),
                    output.get("observed_price_before_action"),
                    output.get("observed_inventory_before_action"),
                    output.get("observed_price_after_detail_save"),
                    output.get("observed_inventory_after_detail_save"),
                    output.get("detail_save_clicked_at"),
                    output.get("action_clicked_at"),
                    output.get("readback_observed_at"),
                    stored_operation_result,
                    str(output.get("error_code") or ""),
                    str(output.get("error_message") or "")[:1000],
                    now,
                    request_item["item_id"],
                    request["batch_id"],
                ),
            )
            operation_status, attempt_status, task_status, lock_status = (
                _outcome_projection(outcome)
            )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, operation_result = ?, lock_owner = ?,
                    updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    operation_status,
                    stored_operation_result,
                    "" if lock_status == "RELEASED" else request_item[
                        "item_execution_attempt_id"
                    ],
                    now,
                    request_item["operation_id"],
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, side_effect_state = ?, ended_at = ?,
                    raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    attempt_status,
                    _item_side_effect_state(output),
                    now,
                    _json_text(output),
                    request_item["item_execution_attempt_id"],
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_write_locks
                SET status = ?, released_at = ?, updated_at = ?
                WHERE write_identity_key = ? AND operation_id = ?
                """,
                (
                    lock_status,
                    now if lock_status == "RELEASED" else None,
                    now,
                    request_item["write_identity_key"],
                    request_item["operation_id"],
                ),
            )
            connection.execute(
                """
                UPDATE tasks
                SET task_status = ?, result_message = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    task_status,
                    _task_result_message(outcome, request["batch_id"]),
                    now,
                    request_item["source_task_id"],
                ),
            )
            if outcome in {"VERIFIED", "ALREADY_APPLIED"}:
                _project_verified_listing(
                    connection,
                    request=request,
                    request_item=request_item,
                    output=output,
                    now=now,
                )
                projected.append(request_item["internal_sku"])
        counts = result["counts"]
        connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET result_id = ?, status = ?, batch_target_count = ?,
                verified_count = ?, unknown_count = ?,
                partial_effect_count = ?, not_attempted_count = ?,
                failed_count = ?, updated_at = ?
            WHERE batch_id = ?
            """,
            (
                result_id,
                result["batch_status"],
                counts["batch_target_count"],
                counts["verified_count"],
                counts["unknown_count"],
                counts["partial_effect_count"],
                counts["not_attempted_count"],
                counts["failed_count"],
                now,
                request["batch_id"],
            ),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    review_summary = _ensure_manual_review_intents(
        repository,
        request=request,
        created_at=now_value,
    )
    return {
        "batch_id": request["batch_id"],
        "result_id": result_id,
        "status": result["batch_status"],
        "projected_skus": list(dict.fromkeys(projected)),
        "post_failure_snapshot_id": str(
            (result.get("post_failure_snapshot") or {}).get("snapshot_id")
            or ""
        ),
        "post_failure_snapshot_complete": bool(
            (result.get("post_failure_snapshot") or {}).get(
                "snapshot_complete"
            )
        ),
        "already_imported": False,
        "manual_review_summary": review_summary,
    }


def ensure_listing_action_reconcile_attempt(
    repository: SQLiteRuntimeRepository,
    runner: ShadowBotFileQueueRunner,
    *,
    source_request: dict[str, Any],
    source_result: dict[str, Any],
    operation_id: str,
) -> dict[str, Any]:
    """Publish the deterministic single-item v5 RECONCILE at most once."""

    request = build_listing_action_reconcile_request(
        source_request,
        source_result,
        operation_id=operation_id,
    )
    item = request["items"][0]
    item_attempt_id = item["item_execution_attempt_id"]
    existing = repository.get_shadowbot_execution_attempt(item_attempt_id)
    if existing is not None:
        return {
            "status": "ALREADY_EXISTS",
            "operation_id": operation_id,
            "execution_attempt_id": request["execution_attempt_id"],
            "item_execution_attempt_id": item_attempt_id,
            "attempt_status": existing.status,
        }
    _assert_queue_ready_for_publication(runner)
    now_value = datetime.now(UTC)
    now = now_value.isoformat()
    connection = repository.connect_write()
    try:
        connection.execute("BEGIN IMMEDIATE")
        operation = connection.execute(
            """
            SELECT status, lock_owner FROM shadowbot_operations
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        write_lock = connection.execute(
            """
            SELECT status, item_execution_attempt_id
            FROM shadowbot_write_locks
            WHERE write_identity_key = ? AND operation_id = ?
            """,
            (item["write_identity_key"], operation_id),
        ).fetchone()
        if (
            operation is None
            or str(operation["status"]) != "NEEDS_RECONCILIATION"
            or str(operation["lock_owner"] or "")
            != request["source_execution_attempt_id"]
            or write_lock is None
            or str(write_lock["status"]) != "UNKNOWN"
        ):
            raise ValidationError("UNKNOWN operation 或写锁已变化，不能创建 RECONCILE。")
        connection.execute(
            """
            INSERT INTO shadowbot_execution_attempts(
                execution_attempt_id, operation_id, execution_mode,
                shadowbot_run_id, status, side_effect_state, started_at,
                instruction_hash, request_file_sha256, queue_request_path,
                ended_at, raw_output_json
            ) VALUES (?, ?, 'RECONCILE', '', 'STARTING', 'NOT_STARTED',
                      ?, ?, '', '', NULL, ?)
            """,
            (
                item_attempt_id,
                operation_id,
                now,
                request["instruction_hash"],
                _json_text(
                    {
                        "contract_version": 5,
                        "batch_id": request["batch_id"],
                        "queue_execution_attempt_id": request[
                            "execution_attempt_id"
                        ],
                        "source_execution_attempt_id": request[
                            "source_execution_attempt_id"
                        ],
                        "source_result_id": request["source_result_id"],
                    }
                ),
            ),
        )
        connection.execute(
            """
            UPDATE shadowbot_operations
            SET status = 'RUNNING', lock_owner = ?, updated_at = ?
            WHERE operation_id = ?
            """,
            (item_attempt_id, now, operation_id),
        )
        connection.execute(
            """
            UPDATE shadowbot_write_locks
            SET item_execution_attempt_id = ?, updated_at = ?
            WHERE write_identity_key = ? AND operation_id = ?
              AND status = 'UNKNOWN'
            """,
            (
                item_attempt_id,
                now,
                item["write_identity_key"],
                operation_id,
            ),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    try:
        started = runner.start(request)
    except ShadowBotStartBoundaryError as exc:
        connection = repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, ended_at = ?, raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    "START_UNKNOWN" if exc.published else "START_FAILED",
                    now,
                    _json_text(
                        {
                            "contract_version": 5,
                            "published": exc.published,
                            "error_code": (
                                "RECONCILE_START_UNKNOWN"
                                if exc.published
                                else "RECONCILE_START_FAILED"
                            ),
                            **dict(exc.raw_output or {}),
                        }
                    ),
                    item_attempt_id,
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = 'NEEDS_RECONCILIATION',
                    lock_owner = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (item_attempt_id, now, operation_id),
            )
            connection.commit()
        finally:
            connection.close()
        raise
    connection = repository.connect_write()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE shadowbot_execution_attempts
            SET status = 'RUNNING', shadowbot_run_id = ?,
                request_file_sha256 = ?, queue_request_path = ?,
                raw_output_json = ?
            WHERE execution_attempt_id = ?
            """,
            (
                started.shadowbot_run_id,
                str(started.raw_output.get("request_file_sha256") or ""),
                str(started.raw_output.get("queue_request_path") or ""),
                _json_text(
                    {
                        "contract_version": 5,
                        "batch_id": request["batch_id"],
                        "queue_execution_attempt_id": request[
                            "execution_attempt_id"
                        ],
                        "source_execution_attempt_id": request[
                            "source_execution_attempt_id"
                        ],
                        "source_result_id": request["source_result_id"],
                        **dict(started.raw_output),
                    }
                ),
                item_attempt_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "status": "PUBLISHED",
        "operation_id": operation_id,
        "execution_attempt_id": request["execution_attempt_id"],
        "item_execution_attempt_id": item_attempt_id,
        "queue_request_path": str(
            started.raw_output.get("queue_request_path") or ""
        ),
    }


def _import_listing_action_reconcile_result(
    repository: SQLiteRuntimeRepository,
    *,
    request: dict[str, Any],
    result: dict[str, Any],
    result_file_sha256: str,
    source_result_path: str,
) -> dict[str, Any]:
    """Resolve one UNKNOWN listing operation from a read-only v5 result."""

    if not _is_hex_sha256(result_file_sha256):
        raise ValidationError("result_file_sha256 无效。")
    item = request["items"][0]
    output = result["items"][0]
    outcome = str(output["operation_result"]).upper()
    if outcome not in {
        "VERIFIED",
        "NOT_APPLIED",
        "NEEDS_RECONCILIATION",
    }:
        raise ValidationError("RECONCILE 结果不允许该 operation_result。")
    result_id = str(result["result_id"])
    now_value = datetime.now(UTC)
    now = now_value.isoformat()
    connection = repository.connect_write()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT result_sha256 FROM shadowbot_listing_result_receipts
            WHERE result_id = ?
            """,
            (result_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["result_sha256"]) != result_file_sha256:
                raise ValidationError("同一 result_id 对应不同结果。")
            connection.rollback()
            return {
                "batch_id": request["batch_id"],
                "result_id": result_id,
                "status": outcome,
                "already_imported": True,
            }
        operation = connection.execute(
            """
            SELECT status FROM shadowbot_operations
            WHERE operation_id = ?
            """,
            (item["operation_id"],),
        ).fetchone()
        if operation is None or str(operation["status"]) not in {
            "RUNNING",
            "NEEDS_RECONCILIATION",
        }:
            raise ValidationError("RECONCILE operation 状态已变化。")
        connection.execute(
            """
            INSERT INTO shadowbot_listing_result_receipts(
                result_id, batch_id, execution_attempt_id,
                instruction_hash, manifest_sha256, result_sha256,
                source_result_path, accepted_at, ack_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                result_id,
                request["batch_id"],
                request["execution_attempt_id"],
                request["instruction_hash"],
                request["manifest_sha256"],
                result_file_sha256,
                str(source_result_path or ""),
                now,
            ),
        )
        stored_result = (
            "VERIFIED"
            if outcome == "VERIFIED"
            else "NOT_APPLIED"
            if outcome == "NOT_APPLIED"
            else "NEEDS_RECONCILIATION"
        )
        connection.execute(
            """
            UPDATE shadowbot_listing_action_batch_items
            SET detail_effect_state = ?, listing_effect_state = ?,
                observed_price_after_detail_save = COALESCE(
                    ?, observed_price_after_detail_save
                ),
                observed_inventory_after_detail_save = COALESCE(
                    ?, observed_inventory_after_detail_save
                ),
                readback_observed_at = ?, operation_result = ?,
                error_code = ?, error_message = ?, updated_at = ?
            WHERE item_id = ? AND batch_id = ?
            """,
            (
                output.get("detail_effect_state", "NOT_STARTED"),
                output.get("listing_effect_state", "NOT_STARTED"),
                output.get("actual_price"),
                output.get("actual_inventory"),
                output.get("readback_observed_at"),
                stored_result,
                str(output.get("error_code") or ""),
                str(output.get("error_message") or "")[:1000],
                now,
                item["item_id"],
                request["batch_id"],
            ),
        )
        operation_status, attempt_status, task_status, lock_status = (
            _outcome_projection(outcome)
        )
        connection.execute(
            """
            UPDATE shadowbot_operations
            SET status = ?, operation_result = ?,
                resolution_status = ?, resolved_by = ?,
                resolved_at = ?, lock_owner = ?, updated_at = ?
            WHERE operation_id = ?
            """,
            (
                operation_status,
                stored_result,
                "UNRESOLVED",
                "",
                None,
                "" if lock_status == "RELEASED" else item[
                    "item_execution_attempt_id"
                ],
                now,
                item["operation_id"],
            ),
        )
        connection.execute(
            """
            UPDATE shadowbot_execution_attempts
            SET status = ?, side_effect_state = ?, ended_at = ?,
                raw_output_json = ?
            WHERE execution_attempt_id = ?
            """,
            (
                attempt_status,
                _item_side_effect_state(output),
                now,
                _json_text(output),
                item["item_execution_attempt_id"],
            ),
        )
        connection.execute(
            """
            UPDATE shadowbot_write_locks
            SET status = ?, released_at = ?, updated_at = ?
            WHERE write_identity_key = ? AND operation_id = ?
            """,
            (
                lock_status,
                now if lock_status == "RELEASED" else None,
                now,
                item["write_identity_key"],
                item["operation_id"],
            ),
        )
        connection.execute(
            """
            UPDATE tasks
            SET task_status = ?, result_message = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (
                task_status,
                (
                    "只读 RECONCILE 已确认上下架操作生效："
                    if outcome == "VERIFIED"
                    else "只读 RECONCILE 已确认上下架操作未生效："
                    if outcome == "NOT_APPLIED"
                    else "只读 RECONCILE 仍无法确认上下架副作用："
                )
                + request["batch_id"],
                now,
                item["source_task_id"],
            ),
        )
        projected: list[str] = []
        if outcome == "VERIFIED":
            _project_verified_listing(
                connection,
                request=request,
                request_item=item,
                output=output,
                now=now,
            )
            projected.append(item["internal_sku"])
        counts = _recompute_listing_batch_counts(
            connection,
            request["batch_id"],
        )
        connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET status = ?, verified_count = ?, unknown_count = ?,
                partial_effect_count = ?, not_attempted_count = ?,
                failed_count = ?, updated_at = ?
            WHERE batch_id = ?
            """,
            (
                counts["batch_status"],
                counts["verified_count"],
                counts["unknown_count"],
                counts["partial_effect_count"],
                counts["not_attempted_count"],
                counts["failed_count"],
                now,
                request["batch_id"],
            ),
        )
        if outcome != "NEEDS_RECONCILIATION":
            review_rows = connection.execute(
                """
                SELECT review_task_id FROM review_tasks
                WHERE source_task_id = ? AND review_status = 'pending'
                  AND reason LIKE ?
                """,
                (
                    item["source_task_id"],
                    "%需要唯一 RECONCILE%",
                ),
            ).fetchall()
            for review_row in review_rows:
                review_id = str(review_row["review_task_id"])
                connection.execute(
                    """
                    UPDATE review_tasks
                    SET review_status = 'cancelled',
                        resolution_payload_json = ?, updated_at = ?,
                        resolved_by = 'system:listing_reconcile',
                        resolved_at = ?, resolution_note = ?
                    WHERE review_task_id = ? AND review_status = 'pending'
                    """,
                    (
                        _json_text(
                            {
                                "resolution_type": (
                                    "RECONCILE_VERIFIED"
                                    if outcome == "VERIFIED"
                                    else "RECONCILE_NOT_APPLIED"
                                ),
                                "reconcile_execution_attempt_id": request[
                                    "execution_attempt_id"
                                ],
                                "result_id": result_id,
                            }
                        ),
                        now,
                        now,
                        "唯一只读 RECONCILE 已给出确定结论",
                        review_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE review_tokens
                    SET revoked_at = ?
                    WHERE review_task_id = ? AND revoked_at IS NULL
                    """,
                    (now, review_id),
                )
                repository._cancel_review_outbox_on_connection(
                    connection,
                    review_id,
                    changed_at=now_value,
                )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    review_summary = _ensure_manual_review_intents(
        repository,
        request=request,
        created_at=now_value,
    )
    return {
        "batch_id": request["batch_id"],
        "result_id": result_id,
        "status": outcome,
        "batch_status": counts["batch_status"],
        "projected_skus": projected,
        "already_imported": False,
        "manual_review_summary": review_summary,
    }


def mark_listing_action_ack(
    repository: SQLiteRuntimeRepository,
    *,
    result_id: str,
    written: bool,
    error_message: str = "",
) -> None:
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            UPDATE shadowbot_listing_result_receipts
            SET ack_state = ?, ack_updated_at = ?, last_projection_error = ?
            WHERE result_id = ?
            """,
            (
                "WRITTEN" if written else "FAILED",
                datetime.now(UTC).isoformat(),
                "" if written else str(error_message or "")[:1000],
                result_id,
            ),
        )


def render_listing_action_markdown(
    *,
    request: dict[str, Any],
    result: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    lines = [
        "# 平台商品上下架执行报告",
        "",
        f"- 结果：{result['batch_status']}",
        f"- 动作：`{request['action_type']}`",
        f"- 批次 ID：`{request['batch_id']}`",
        f"- 运行 ID：`{request['execution_attempt_id']}`",
        f"- 已验证商品数：{result['counts']['verified_count']}",
        f"- UNKNOWN：{result['counts']['unknown_count']}",
        f"- 部分生效：{result['counts']['partial_effect_count']}",
        "",
        "## 逐商品结果",
        "",
        "| SKU | 商品 | 等级 | 结果 | 价格 | 库存 | 错误 |",
        "|---|---|---|---|---:|---:|---|",
    ]
    request_by_operation = {
        item["operation_id"]: item for item in request["items"]
    }
    for item in result["items"]:
        source = request_by_operation[item["operation_id"]]
        lines.append(
            "| {sku} | {name} | {grade} | {outcome} | {price} | "
            "{inventory} | {error} |".format(
                sku=source["internal_sku"],
                name=source["expected_product_name"],
                grade=source["expected_grade"],
                outcome=item["operation_result"],
                price=(
                    item.get("actual_price")
                    or item.get("observed_price_after_detail_save")
                    or "-"
                ),
                inventory=(
                    item.get("actual_inventory")
                    if item.get("actual_inventory") is not None
                    else item.get("observed_inventory_after_detail_save")
                    if item.get("observed_inventory_after_detail_save")
                    is not None
                    else "-"
                ),
                error=str(item.get("error_code") or "-"),
            )
        )
    lines.extend(
        [
            "",
            "## 数据库回写",
            "",
            f"- 已投影 SKU：{', '.join(summary.get('projected_skus') or []) or '无'}",
            (
                "- 失败后两页扫描："
                + (
                    "完整，快照 `"
                    + str(summary.get("post_failure_snapshot_id") or "")
                    + "` 已导入。"
                    if summary.get("post_failure_snapshot_complete")
                    else "未生成或未完整，不投影页面事实。"
                )
            ),
            "- 写锁依据逐商品结果释放、保留 UNKNOWN，或转为 REVIEW_BLOCKED。",
            "",
        ]
    )
    return "\n".join(lines)


def _persist_prepared_write_batch(
    repository: SQLiteRuntimeRepository,
    request: dict[str, Any],
    *,
    corrective_authorizations: list[dict[str, str]] | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    connection = repository.connect_write()
    try:
        connection.execute("BEGIN IMMEDIATE")
        validated_corrective = _corrective_retry_authorizations(
            connection,
            platform_name=request["platform_name"],
            action_type=request["action_type"],
            items=request["items"],
            required_authorizations=corrective_authorizations or [],
        )
        corrective_by_key = {
            item["write_identity_key"]: item
            for item in validated_corrective
        }
        for item in request["items"]:
            task = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (item["source_task_id"],),
            ).fetchone()
            if (
                task is None
                or str(task["task_status"]) != TaskStatus.PENDING.value
                or str(task["action_type"]) != request["action_type"]
                or str(task["internal_sku"] or "").upper()
                != item["internal_sku"]
            ):
                raise ValidationError(
                    "任务在 COMMIT 创建前已变化：" + item["source_task_id"]
                )
            blocking_lock = connection.execute(
                """
                SELECT operation_id, status FROM shadowbot_write_locks
                WHERE write_identity_key = ?
                  AND status IN ('ACTIVE', 'UNKNOWN', 'REVIEW_BLOCKED')
                """,
                (item["write_identity_key"],),
            ).fetchone()
            review_reasons = review_block_reasons(
                request["action_type"],
                _open_review_context(
                    connection,
                    platform_name=request["platform_name"],
                    internal_sku=item["internal_sku"],
                ),
            )
            if review_reasons:
                raise ValidationError(
                    "商品存在未解决的写入 Review："
                    + item["internal_sku"]
                    + "（"
                    + ", ".join(review_reasons)
                    + "）"
                )
            corrective = corrective_by_key.get(item["write_identity_key"])
            authorized_old_lock = (
                blocking_lock is not None
                and corrective is not None
                and str(blocking_lock["status"]) == "REVIEW_BLOCKED"
                and str(blocking_lock["operation_id"])
                == corrective["previous_operation_id"]
            )
            if blocking_lock is not None and not authorized_old_lock:
                raise ValidationError("商品存在阻断写锁：" + item["internal_sku"])
        connection.execute(
            """
            INSERT INTO shadowbot_batch_registry(
                batch_id, batch_type, contract_version, platform_name, created_at
            ) VALUES (?, ?, 5, ?, ?)
            """,
            (
                request["batch_id"],
                request["action_type"],
                request["platform_name"],
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO shadowbot_listing_action_batches(
                batch_id, contract_version, execution_profile, action_type,
                platform_name, manifest_sha256, instruction_hash,
                execution_attempt_id, status, batch_target_count,
                created_at, updated_at
            ) VALUES (?, 5, ?, ?, ?, ?, ?, ?, 'PUBLISHING', ?, ?, ?)
            """,
            (
                request["batch_id"],
                request["execution_profile"],
                request["action_type"],
                request["platform_name"],
                request["manifest_sha256"],
                request["instruction_hash"],
                request["execution_attempt_id"],
                len(request["items"]),
                now,
                now,
            ),
        )
        for item in request["items"]:
            approved = {
                "action_type": request["action_type"],
                "expected_old_status": item["expected_old_status"],
                "target_status": item["target_status"],
                "target_price": item.get("target_price"),
                "target_inventory": item.get("target_inventory"),
            }
            connection.execute(
                """
                INSERT INTO shadowbot_operations(
                    operation_id, task_id, platform, product_identity_json,
                    action_type, expected_old_price, target_price,
                    expected_old_status, target_status, target_inventory,
                    status, operation_result, resolution_status, lock_owner,
                    approved_payload_hash, approved_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 'RUNNING', '',
                          'UNRESOLVED', ?, ?, ?, ?, ?)
                """,
                (
                    item["operation_id"],
                    item["source_task_id"],
                    request["platform_name"],
                    _json_text(
                        {
                            "internal_sku": item["internal_sku"],
                            "expected_product_name": item[
                                "expected_product_name"
                            ],
                            "expected_grade": item["expected_grade"],
                            "page_identity_key": item["page_identity_key"],
                            "write_identity_key": item["write_identity_key"],
                        }
                    ),
                    request["action_type"],
                    item.get("target_price"),
                    item["expected_old_status"],
                    item["target_status"],
                    item.get("target_inventory"),
                    item["item_execution_attempt_id"],
                    item["item_payload_sha256"],
                    _json_text(approved),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode,
                    shadowbot_run_id, status, side_effect_state, started_at,
                    instruction_hash, request_file_sha256, queue_request_path,
                    raw_output_json
                ) VALUES (?, ?, 'COMMIT', '', 'STARTING', 'NOT_STARTED',
                          ?, ?, '', '', '{}')
                """,
                (
                    item["item_execution_attempt_id"],
                    item["operation_id"],
                    now,
                    request["instruction_hash"],
                ),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_listing_action_batch_items(
                    item_id, batch_id, source_task_id, operation_id,
                    item_execution_attempt_id, internal_sku,
                    expected_product_name, expected_grade,
                    item_payload_sha256, write_identity_key,
                    page_identity_key, expected_old_status, target_status,
                    target_price, target_inventory, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["item_id"],
                    request["batch_id"],
                    item["source_task_id"],
                    item["operation_id"],
                    item["item_execution_attempt_id"],
                    item["internal_sku"],
                    item["expected_product_name"],
                    item["expected_grade"],
                    item["item_payload_sha256"],
                    item["write_identity_key"],
                    item["page_identity_key"],
                    item["expected_old_status"],
                    item["target_status"],
                    item.get("target_price"),
                    item.get("target_inventory"),
                    now,
                ),
            )
            corrective = corrective_by_key.get(item["write_identity_key"])
            if corrective is not None:
                operation_update = connection.execute(
                    """
                    UPDATE shadowbot_operations
                    SET resolution_status = 'CORRECTIVE_ACTION_AUTHORIZED',
                        resolved_by = ?, resolved_at = ?,
                        superseded_by_operation_id = ?, updated_at = ?
                    WHERE operation_id = ?
                      AND operation_result = 'PARTIALLY_APPLIED'
                      AND resolution_status = 'UNRESOLVED'
                    """,
                    (
                        "review_task:" + corrective["review_task_id"],
                        now,
                        item["operation_id"],
                        now,
                        corrective["previous_operation_id"],
                    ),
                )
                if operation_update.rowcount != 1:
                    raise ValidationError(
                        "旧 operation 的纠正授权状态已变化："
                        + item["internal_sku"]
                    )
                lock_update = connection.execute(
                    """
                    UPDATE shadowbot_write_locks
                    SET operation_id = ?, item_execution_attempt_id = ?,
                        batch_id = ?, status = 'ACTIVE',
                        acquired_at = ?, released_at = NULL, updated_at = ?
                    WHERE write_identity_key = ?
                      AND operation_id = ?
                      AND status = 'REVIEW_BLOCKED'
                    """,
                    (
                        item["operation_id"],
                        item["item_execution_attempt_id"],
                        request["batch_id"],
                        now,
                        now,
                        item["write_identity_key"],
                        corrective["previous_operation_id"],
                    ),
                )
                if lock_update.rowcount != 1:
                    raise ValidationError(
                        "旧 REVIEW_BLOCKED 写锁已变化：" + item["internal_sku"]
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO shadowbot_write_locks(
                        write_identity_key, operation_id,
                        item_execution_attempt_id, batch_id, status,
                        acquired_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
                    ON CONFLICT(write_identity_key) DO UPDATE SET
                        operation_id = excluded.operation_id,
                        item_execution_attempt_id = excluded.item_execution_attempt_id,
                        batch_id = excluded.batch_id,
                        status = 'ACTIVE',
                        acquired_at = excluded.acquired_at,
                        released_at = NULL,
                        updated_at = excluded.updated_at
                    WHERE shadowbot_write_locks.status = 'RELEASED'
                    """,
                    (
                        item["write_identity_key"],
                        item["operation_id"],
                        item["item_execution_attempt_id"],
                        request["batch_id"],
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE tasks
                SET task_status = 'running', result_message = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    "ShadowBot 上下架 COMMIT 发布中：" + request["batch_id"],
                    now,
                    item["source_task_id"],
                ),
            )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _record_publish_success(
    repository: SQLiteRuntimeRepository,
    request: dict[str, Any],
    start_result: ShadowBotStartResult,
) -> None:
    now = datetime.now(UTC).isoformat()
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE shadowbot_listing_action_batches "
            "SET status = 'QUEUED', updated_at = ? WHERE batch_id = ?",
            (now, request["batch_id"]),
        )
        for item in request["items"]:
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = 'RUNNING', shadowbot_run_id = ?,
                    request_file_sha256 = ?, queue_request_path = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    start_result.shadowbot_run_id,
                    str(
                        start_result.raw_output.get("request_file_sha256")
                        or ""
                    ),
                    str(
                        start_result.raw_output.get("queue_request_path")
                        or ""
                    ),
                    item["item_execution_attempt_id"],
                ),
            )


def _record_publish_failure(
    repository: SQLiteRuntimeRepository,
    request: dict[str, Any],
    *,
    published: bool,
) -> None:
    now = datetime.now(UTC).isoformat()
    batch_status = "UNKNOWN" if published else "FAILED"
    lock_status = "UNKNOWN" if published else "RELEASED"
    attempt_status = "START_UNKNOWN" if published else "START_FAILED"
    side_effect_state = "UNKNOWN" if published else "NOT_STARTED"
    operation_status = "NEEDS_RECONCILIATION" if published else "PENDING"
    operation_result = "NEEDS_RECONCILIATION" if published else "NOT_ATTEMPTED"
    error_code = "RUNNER_START_UNKNOWN" if published else "RUNNER_START_FAILED"
    error_message = "队列发布边界不确定" if published else "队列未发布，未开始执行"
    raw_output = _json_text(
        {
            "error_code": error_code,
            "error_message": error_message,
            "published": published,
        }
    )
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE shadowbot_listing_action_batches "
            "SET status = ?, updated_at = ? WHERE batch_id = ?",
            (batch_status, now, request["batch_id"]),
        )
        for item in request["items"]:
            connection.execute(
                """
                UPDATE shadowbot_listing_action_batch_items
                SET detail_effect_state = 'NOT_STARTED',
                    listing_effect_state = 'NOT_STARTED',
                    operation_result = ?, error_code = ?, error_message = ?,
                    updated_at = ?
                WHERE item_id = ? AND batch_id = ?
                """,
                (
                    operation_result,
                    error_code,
                    error_message,
                    now,
                    item["item_id"],
                    request["batch_id"],
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, operation_result = ?, lock_owner = '',
                    updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    operation_status,
                    operation_result,
                    now,
                    item["operation_id"],
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, side_effect_state = ?, ended_at = ?,
                    raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    attempt_status,
                    side_effect_state,
                    now,
                    raw_output,
                    item["item_execution_attempt_id"],
                ),
            )
            connection.execute(
                "UPDATE shadowbot_write_locks SET status = ?, released_at = ?, "
                "updated_at = ? WHERE operation_id = ?",
                (
                    lock_status,
                    None if published else now,
                    now,
                    item["operation_id"],
                ),
            )
            connection.execute(
                "UPDATE tasks SET task_status = ?, result_message = ?, "
                "updated_at = ? WHERE task_id = ?",
                (
                    (
                        TaskStatus.MANUAL_REVIEW.value
                        if published
                        else TaskStatus.PENDING.value
                    ),
                    (
                        "队列发布边界不确定"
                        if published
                        else "队列未发布，任务恢复 pending"
                    ),
                    now,
                    item["source_task_id"],
                ),
            )
        counts = _recompute_listing_batch_counts(connection, request["batch_id"])
        connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET verified_count = ?, unknown_count = ?, partial_effect_count = ?,
                not_attempted_count = ?, failed_count = ?, updated_at = ?
            WHERE batch_id = ?
            """,
            (
                counts["verified_count"],
                counts["unknown_count"],
                counts["partial_effect_count"],
                counts["not_attempted_count"],
                counts["failed_count"],
                now,
                request["batch_id"],
            ),
        )


def _latest_listing_context(
    connection: Any,
    *,
    platform_name: str,
    internal_sku: str,
    action_type: str,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT snapshot.snapshot_id, snapshot.scan_started_at,
               snapshot.online_scan_complete,
               item.listing_location, item.online_occurrences,
               item.online_observed_price, item.online_observed_inventory,
               item.waiting_observed_price, item.waiting_observed_inventory,
               status.online_status, status.last_listing_change_at
        FROM listing_sync_snapshot_items AS item
        JOIN listing_sync_snapshots AS snapshot
          ON snapshot.snapshot_id = item.snapshot_id
        LEFT JOIN listing_status AS status
          ON status.platform_name = snapshot.platform_name
         AND status.internal_sku = item.internal_sku
        WHERE snapshot.platform_name = ?
          AND item.internal_sku = ?
          AND snapshot.snapshot_complete = 1
          AND snapshot.status = 'VERIFIED'
        ORDER BY snapshot.scan_completed_at DESC, snapshot.snapshot_id DESC
        LIMIT 1
        """,
        (platform_name, internal_sku),
    ).fetchone()
    if row is None:
        return {
            "snapshot_id": "",
            "snapshot_valid": False,
            "listing_location": None,
            "online_status": "",
            "online_scan_complete": False,
            "online_occurrences": 0,
            "observed_price": None,
            "observed_inventory": None,
        }
    scan_started = _parse_aware(row["scan_started_at"])
    last_change_text = str(row["last_listing_change_at"] or "")
    snapshot_valid = (
        not last_change_text
        or scan_started >= _parse_aware(last_change_text)
    )
    location = str(row["listing_location"])
    if not snapshot_valid:
        return {
            "snapshot_id": str(row["snapshot_id"]),
            "snapshot_valid": False,
            "listing_location": None,
            "online_status": str(row["online_status"] or ""),
            "online_scan_complete": False,
            "online_occurrences": 0,
            "observed_price": None,
            "observed_inventory": None,
        }
    prefix = "waiting" if location == "waiting_only" else "online"
    return {
        "snapshot_id": str(row["snapshot_id"]),
        "snapshot_valid": snapshot_valid,
        "listing_location": location,
        "online_status": str(row["online_status"] or ""),
        "online_scan_complete": bool(row["online_scan_complete"]),
        "online_occurrences": int(row["online_occurrences"]),
        "observed_price": row[f"{prefix}_observed_price"],
        "observed_inventory": row[f"{prefix}_observed_inventory"],
    }


def _open_review_context(
    connection: Any,
    *,
    platform_name: str,
    internal_sku: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT reason_code, diagnostic_message, blocked_actions_json
        FROM listing_anomaly_cases
        WHERE platform_name = ? AND internal_sku = ? AND cleared_at IS NULL
        """,
        (platform_name, internal_sku),
    ).fetchall()
    contexts: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_value(row["blocked_actions_json"])
        contexts.append(
            {
                "reason_code": str(
                    row["reason_code"]
                    or row["diagnostic_message"]
                    or "LISTING_ANOMALY_REVIEW_OPEN"
                ),
                "blocked_actions": payload
                if isinstance(payload, list)
                else payload.get("blocked_actions")
                if isinstance(payload, dict)
                else None,
            }
        )

    review_rows = connection.execute(
        """
        SELECT review_task_id, review_type, reason, review_payload_json
        FROM review_tasks
        WHERE platform_name = ?
          AND internal_sku = ?
          AND review_status = 'pending'
        ORDER BY created_at, review_task_id
        """,
        (platform_name, internal_sku),
    ).fetchall()
    for row in review_rows:
        payload = _json_object(row["review_payload_json"])
        contexts.append(
            {
                "review_task_id": str(row["review_task_id"]),
                "reason_code": str(
                    payload.get("reason_code")
                    or row["reason"]
                    or row["review_type"]
                    or "REVIEW_TASK_OPEN"
                ),
                # Missing or malformed blocked_actions intentionally remains
                # invalid so the gate fails closed.
                "blocked_actions": payload.get("blocked_actions"),
            }
        )
    return contexts


def _corrective_retry_authorizations(
    connection: Any,
    *,
    platform_name: str,
    action_type: str,
    items: list[dict[str, Any]],
    required_authorizations: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Resolve an approved retry and its still-blocked operations as one unit."""

    if not items:
        return []
    task_ids = {str(item["source_task_id"]) for item in items}
    required = required_authorizations or []
    required_review_ids = {
        str(item.get("review_task_id") or "")
        for item in required
        if str(item.get("review_task_id") or "")
    }
    if required and len(required_review_ids) != 1:
        raise ValidationError("纠正重试授权必须来自同一个人工复核。")
    query = """
        SELECT review_task_id, review_payload_json, resolution_payload_json
        FROM review_tasks
        WHERE review_status = 'approved'
          AND review_type = 'manual_review'
          AND platform_name = ?
    """
    params: list[Any] = [platform_name]
    if required_review_ids:
        query += " AND review_task_id = ?"
        params.append(next(iter(required_review_ids)))
    query += " ORDER BY updated_at DESC"
    review_rows = connection.execute(query, tuple(params)).fetchall()
    selected_review_id = ""
    for review in review_rows:
        review_payload = _json_object(review["review_payload_json"])
        resolution_payload = _json_object(review["resolution_payload_json"])
        if str(resolution_payload.get("decision") or "") != "retry_task":
            continue
        affected = resolution_payload.get("affected_task_ids")
        if not isinstance(affected, list):
            affected = review_payload.get("affected_task_ids")
        affected_ids = {
            str(task_id).strip()
            for task_id in affected or []
            if str(task_id).strip()
        }
        if affected_ids != task_ids:
            continue
        review_actions = {
            str(value).strip().lower()
            for value in (
                review_payload.get("action_types")
                if isinstance(review_payload.get("action_types"), list)
                else [review_payload.get("action_type")]
            )
            if str(value or "").strip()
        }
        if review_actions and review_actions != {action_type}:
            continue
        selected_review_id = str(review["review_task_id"])
        break
    if not selected_review_id:
        if required:
            raise ValidationError("人工复核的纠正重试授权已失效或任务集合不匹配。")
        return []

    authorizations: list[dict[str, str]] = []
    required_by_key = {
        str(item.get("write_identity_key") or ""): item
        for item in required
    }
    for item in items:
        row = connection.execute(
            """
            SELECT lock.operation_id, operation.task_id,
                   operation.action_type, operation.operation_result,
                   operation.resolution_status
            FROM shadowbot_write_locks AS lock
            JOIN shadowbot_operations AS operation
              ON operation.operation_id = lock.operation_id
            WHERE lock.write_identity_key = ?
              AND lock.status = 'REVIEW_BLOCKED'
            """,
            (item["write_identity_key"],),
        ).fetchone()
        if (
            row is None
            or str(row["task_id"]) != item["source_task_id"]
            or str(row["action_type"]) != action_type
            or str(row["operation_result"]) != "PARTIALLY_APPLIED"
            or str(row["resolution_status"]) != "UNRESOLVED"
        ):
            if required:
                raise ValidationError(
                    "纠正重试对应的旧 operation 或写锁已变化："
                    + item["internal_sku"]
                )
            return []
        authorization = {
            "review_task_id": selected_review_id,
            "write_identity_key": item["write_identity_key"],
            "previous_operation_id": str(row["operation_id"]),
            "source_task_id": item["source_task_id"],
            "internal_sku": item["internal_sku"],
        }
        expected = required_by_key.get(item["write_identity_key"])
        if required and (
            expected is None
            or str(expected.get("previous_operation_id") or "")
            != authorization["previous_operation_id"]
        ):
            raise ValidationError("纠正重试授权与当前旧 operation 不一致。")
        authorizations.append(authorization)
    if required and len(authorizations) != len(required):
        raise ValidationError("纠正重试授权项目数量不一致。")
    return authorizations


def _write_lock_context(
    connection: Any,
    *,
    write_identity_key: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT operation_id, status FROM shadowbot_write_locks
        WHERE write_identity_key = ?
        """,
        (write_identity_key,),
    ).fetchall()
    return [
        {
            "operation_id": str(row["operation_id"]),
            "status": str(row["status"]),
        }
        for row in rows
    ]


def _json_object(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_value(value: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _outcome_projection(outcome: str) -> tuple[str, str, str, str]:
    if outcome in {"VERIFIED", "ALREADY_APPLIED"}:
        return ("VERIFIED", "VERIFIED", TaskStatus.SUCCESS.value, "RELEASED")
    if outcome == "NEEDS_RECONCILIATION":
        return (
            "NEEDS_RECONCILIATION",
            "UNKNOWN",
            TaskStatus.MANUAL_REVIEW.value,
            "UNKNOWN",
        )
    if outcome == "PARTIALLY_APPLIED":
        return (
            "PARTIALLY_APPLIED",
            "PARTIAL",
            TaskStatus.MANUAL_REVIEW.value,
            "REVIEW_BLOCKED",
        )
    if outcome == "NOT_ATTEMPTED":
        return (
            "PENDING",
            "NOT_ATTEMPTED",
            TaskStatus.PENDING.value,
            "RELEASED",
        )
    return ("FAILED", "FAILED", TaskStatus.FAILED.value, "RELEASED")


def _recompute_listing_batch_counts(
    connection: Any,
    batch_id: str,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT operation_result
        FROM shadowbot_listing_action_batch_items
        WHERE batch_id = ?
        """,
        (batch_id,),
    ).fetchall()
    counts: dict[str, Any] = {
        "batch_target_count": len(rows),
        "verified_count": 0,
        "unknown_count": 0,
        "partial_effect_count": 0,
        "not_attempted_count": 0,
        "failed_count": 0,
    }
    for row in rows:
        outcome = str(row["operation_result"] or "").upper()
        if outcome == "VERIFIED":
            counts["verified_count"] += 1
        elif outcome == "NEEDS_RECONCILIATION":
            counts["unknown_count"] += 1
        elif outcome == "PARTIALLY_APPLIED":
            counts["partial_effect_count"] += 1
        elif outcome in {"", "NOT_ATTEMPTED"}:
            counts["not_attempted_count"] += 1
        else:
            counts["failed_count"] += 1
    if counts["unknown_count"]:
        batch_status = "UNKNOWN"
    elif counts["partial_effect_count"]:
        batch_status = "PARTIAL"
    elif counts["verified_count"] == counts["batch_target_count"]:
        batch_status = "VERIFIED"
    elif counts["verified_count"] or counts["not_attempted_count"]:
        batch_status = "PARTIAL"
    else:
        batch_status = "FAILED"
    counts["batch_status"] = batch_status
    return counts


def _item_side_effect_state(output: dict[str, Any]) -> str:
    states = {
        str(output.get("detail_effect_state") or "NOT_STARTED").upper(),
        str(output.get("listing_effect_state") or "NOT_STARTED").upper(),
    }
    for state in ("UNKNOWN", "PARTIAL", "VERIFIED", "NOT_APPLIED"):
        if state in states:
            return state
    return "NOT_STARTED"


def _project_verified_listing(
    connection: Any,
    *,
    request: dict[str, Any],
    request_item: dict[str, Any],
    output: dict[str, Any],
    now: str,
) -> None:
    action = request["action_type"]
    target_status = "online" if action == "set_online" else "offline"
    changed_at = str(output.get("action_clicked_at") or now)
    if action == "set_online":
        connection.execute(
            """
            UPDATE listing_status
            SET current_price = ?, platform_stock_qty = ?,
                online_status = 'online', source = 'shadowbot_set_online',
                inventory_source = 'SET_ONLINE_POSTCHECK',
                inventory_observed_at = ?,
                inventory_source_attempt_id = ?,
                price_source = 'SET_ONLINE_POSTCHECK',
                price_observed_at = ?, price_source_attempt_id = ?,
                last_listing_change_at = ?,
                last_listing_operation_id = ?,
                online_status_observed_at = ?,
                online_status_source_type = 'SET_ONLINE_POSTCHECK',
                online_status_source_id = ?, updated_at = ?
            WHERE platform_name = ? AND internal_sku = ?
            """,
            (
                output.get("actual_price") or request_item["target_price"],
                output.get("actual_inventory")
                if output.get("actual_inventory") is not None
                else request_item["target_inventory"],
                output.get("readback_observed_at") or now,
                request_item["item_execution_attempt_id"],
                output.get("readback_observed_at") or now,
                request_item["item_execution_attempt_id"],
                changed_at,
                request_item["operation_id"],
                output.get("readback_observed_at") or now,
                request_item["operation_id"],
                now,
                request["platform_name"],
                request_item["internal_sku"],
            ),
        )
    else:
        observed_price = output.get("observed_price_before_action")
        observed_inventory = output.get("observed_inventory_before_action")
        precheck_observed_at = str(
            output.get("action_clicked_at")
            or output.get("readback_observed_at")
            or now
        )
        connection.execute(
            """
            UPDATE listing_status
            SET current_price = COALESCE(:observed_price, current_price),
                platform_stock_qty = COALESCE(
                    :observed_inventory,
                    platform_stock_qty
                ),
                online_status = :target_status,
                source = 'shadowbot_set_offline',
                inventory_source = CASE
                    WHEN :observed_inventory IS NOT NULL
                    THEN 'SET_OFFLINE_PRECHECK'
                    ELSE inventory_source
                END,
                inventory_observed_at = CASE
                    WHEN :observed_inventory IS NOT NULL
                    THEN :precheck_observed_at
                    ELSE inventory_observed_at
                END,
                inventory_source_attempt_id = CASE
                    WHEN :observed_inventory IS NOT NULL
                    THEN :item_execution_attempt_id
                    ELSE inventory_source_attempt_id
                END,
                price_source = CASE
                    WHEN :observed_price IS NOT NULL
                    THEN 'SET_OFFLINE_PRECHECK'
                    ELSE price_source
                END,
                price_observed_at = CASE
                    WHEN :observed_price IS NOT NULL
                    THEN :precheck_observed_at
                    ELSE price_observed_at
                END,
                price_source_attempt_id = CASE
                    WHEN :observed_price IS NOT NULL
                    THEN :item_execution_attempt_id
                    ELSE price_source_attempt_id
                END,
                last_listing_change_at = :changed_at,
                last_listing_operation_id = :operation_id,
                online_status_observed_at = :readback_observed_at,
                online_status_source_type = 'SET_OFFLINE_POSTCHECK',
                online_status_source_id = :operation_id,
                updated_at = :now
            WHERE platform_name = :platform_name
              AND internal_sku = :internal_sku
            """,
            {
                "observed_price": observed_price,
                "observed_inventory": observed_inventory,
                "target_status": target_status,
                "precheck_observed_at": precheck_observed_at,
                "item_execution_attempt_id": request_item[
                    "item_execution_attempt_id"
                ],
                "changed_at": changed_at,
                "operation_id": request_item["operation_id"],
                "readback_observed_at": (
                    output.get("readback_observed_at") or now
                ),
                "now": now,
                "platform_name": request["platform_name"],
                "internal_sku": request_item["internal_sku"],
            },
        )


def _task_result_message(outcome: str, batch_id: str) -> str:
    messages = {
        "VERIFIED": "上下架操作已完成并回读验证",
        "ALREADY_APPLIED": "平台状态和目标资料已经满足任务",
        "NEEDS_RECONCILIATION": "写操作副作用不确定，需要唯一 RECONCILE",
        "PARTIALLY_APPLIED": "资料部分生效，需要人工复核",
        "NOT_APPLIED": "写操作未生效",
        "FAILED": "写操作失败",
        "NOT_ATTEMPTED": "批次中断，当前商品未尝试",
    }
    return f"{messages.get(outcome, outcome)}：{batch_id}"


def _ensure_manual_review_intents(
    repository: SQLiteRuntimeRepository,
    *,
    request: dict[str, Any],
    created_at: datetime | None = None,
) -> dict[str, Any]:
    from app.services.runtime import ReviewTaskService

    review_tasks = []
    for item in request["items"]:
        task = repository.get_task(str(item["source_task_id"]))
        if task is not None and task.task_status is TaskStatus.MANUAL_REVIEW:
            review_tasks.append(task)
    if not review_tasks:
        return {
            "source_task_count": 0,
            "inserted_review_tasks_count": 0,
            "inserted_notification_logs_count": 0,
            "notification_errors": [],
        }
    summary = ReviewTaskService(repository).create_from_tasks(
        review_tasks,
        manual_review_created_at=created_at,
    )
    result = {
        "source_task_count": len(review_tasks),
        "inserted_review_tasks_count": summary.inserted_review_tasks_count,
        "inserted_notification_logs_count": summary.inserted_notification_logs_count,
        "notification_errors": list(summary.notification_errors),
    }
    if summary.notification_errors:
        raise RuntimeError(
            "人工复核通知意图创建失败："
            + "; ".join(summary.notification_errors)
        )
    return result


def _stable_id(prefix: str, value: str) -> str:
    digest = sha256(str(value).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValidationError("时间必须包含时区。")
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        try:
            business_zone = ZoneInfo("Asia/Shanghai")
        except ZoneInfoNotFoundError as exc:
            raise ValidationError("无法解析业务时区 Asia/Shanghai。") from exc
        return value.replace(tzinfo=business_zone).astimezone(UTC)
    return value.astimezone(UTC)


def _is_hex_sha256(value: str) -> bool:
    return len(str(value)) == 64 and all(
        character in "0123456789abcdef"
        for character in str(value).lower()
    )
