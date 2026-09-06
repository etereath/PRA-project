from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.automation_ui_channel import has_active_automation_ui_run
from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.listing_identity import listing_identity_key
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_commit_batch import (
    CONTRACT_VERSION,
    build_commit_manifest,
    build_commit_request,
    compute_instruction_hash,
    load_identity_mapping,
    validate_request,
)
from app.services.shadowbot_executor import (
    ShadowBotFileQueueRunner,
    ShadowBotStartBoundaryError,
    ShadowBotStartResult,
)
from app.shadowbot_contract_primitives import (
    canonical_positive_price,
    derive_v4_batch_semantics,
    sha256_json,
)
from app.services.incident_task_result_projection import (
    project_manual_incident_task_result,
)
from app.task_dispatch_priority import (
    assert_selected_tasks_have_dispatch_priority,
)


RESULT_SCHEMA_VERSION = "shadowbot-commit-batch-result-1.1"
FINAL_BATCH_STATUSES = frozenset({"VERIFIED", "PARTIAL", "FAILED", "UNKNOWN"})
ITEM_STATUSES = frozenset(
    {"NOT_ATTEMPTED", "VERIFIED", "NOT_APPLIED", "FAILED", "UNKNOWN"}
)
ITEM_SIDE_EFFECT_STATES = frozenset(
    {"NOT_STARTED", "SUBMIT_INTENT_RECORDED", "SUBMIT_CLICKED", "UNKNOWN", "VERIFIED", "NOT_APPLIED"}
)


@dataclass(frozen=True, slots=True)
class CommitImportPlan:
    batch_id: str
    result_id: str
    batch_status: str
    counts: dict[str, int]
    items: tuple[dict[str, Any], ...]
    listing_observations: tuple[dict[str, Any], ...]
    already_accepted: bool = False


def prepare_task_commit_batch(
    repository: SQLiteRuntimeRepository,
    *,
    task_ids: Iterable[str],
    mapping_path: Path,
    batch_id: str,
    execution_profile: str,
) -> dict[str, Any]:
    """Create and persist one immutable COMMIT manifest from formal task rows."""

    profile = str(execution_profile or "").strip().lower()
    if profile not in {"development", "production"}:
        raise ValidationError("execution_profile 必须是 development 或 production。")
    manifest = build_task_commit_manifest(
        repository,
        task_ids=task_ids,
        mapping_path=mapping_path,
        batch_id=batch_id,
    )
    _persist_prepared_manifest(repository, manifest, profile=profile)
    return manifest


def build_task_commit_manifest(
    repository: SQLiteRuntimeRepository,
    *,
    task_ids: Iterable[str],
    mapping_path: Path,
    batch_id: str,
) -> dict[str, Any]:
    """Build a formal task manifest without persisting or publishing it."""

    ids = [str(task_id or "").strip() for task_id in task_ids]
    if not ids or any(not task_id for task_id in ids) or len(ids) != len(set(ids)):
        raise ValidationError("task_ids 必须是非空且不重复的任务 ID 列表。")

    tasks = []
    platform_name = ""
    now = datetime.now(timezone.utc)
    for task_id in ids:
        task = repository.get_task(task_id)
        if task is None:
            raise ValidationError(f"任务不存在：{task_id}")
        if task.task_status is not TaskStatus.PENDING:
            raise ValidationError(f"任务不是 pending：{task_id}")
        if task.action_type is not TaskActionType.UPDATE_PRICE:
            raise ValidationError(f"任务不是 update_price：{task_id}")
        if not task.internal_sku or not task.platform_name:
            raise ValidationError(f"任务缺少 internal_sku/platform_name：{task_id}")
        if task.expected_old_price is None or task.target_price is None:
            raise ValidationError(f"任务缺少 expected_old_price/target_price：{task_id}")
        if task.expires_at is not None:
            if _task_time_as_utc(task.expires_at) <= now:
                raise ValidationError(f"任务已过期：{task_id}")
        if platform_name and task.platform_name != platform_name:
            raise ValidationError("一个 COMMIT 批次只能包含同一平台任务。")
        platform_name = task.platform_name
        tasks.append(
            {
                "source_task_id": task.task_id,
                "internal_sku": task.internal_sku,
                "expected_old_price": task.expected_old_price,
                "target_price": task.target_price,
            }
        )

    with closing(repository.connect_read()) as connection:
        assert_selected_tasks_have_dispatch_priority(
            connection,
            selected_task_ids=ids,
            platform_name=platform_name,
        )
    mapping = load_identity_mapping(mapping_path, expected_platform_name=platform_name)
    manifest = build_commit_manifest(
        batch_id=batch_id,
        task_items=tasks,
        identity_mapping=mapping,
        platform_name=platform_name,
    )
    _validate_listing_status_targets(repository, manifest)
    return manifest


def publish_task_commit_batch(
    repository: SQLiteRuntimeRepository,
    runner: ShadowBotFileQueueRunner,
    *,
    manifest: dict[str, Any],
    execution_profile: str,
    applet_uri: str,
    confirmation_text: str = "",
    confirmed_by: str = "",
    window_title: str = "蚂蚁花团供应商",
    capture_evidence: bool = False,
    fault_injection: str = "",
    authorization_batch_id: str = "",
) -> tuple[dict[str, Any], ShadowBotStartResult]:
    """Publish once; Web price decisions also require their durable authorization."""

    suffix = uuid4().hex[:16]
    request = build_commit_request(
        manifest,
        execution_profile=execution_profile,
        batch_task_id=f"BATCHTASK-{suffix}",
        operation_id=f"OP-{suffix}",
        execution_attempt_id=f"ATTEMPT-{suffix}",
        applet_uri=applet_uri,
        confirmation_text=confirmation_text,
        confirmed_by=confirmed_by,
        window_title=window_title,
        capture_evidence=capture_evidence,
        fault_injection=fault_injection,
    )
    validate_request(request)
    now = _now_text()
    now_value = datetime.fromisoformat(now)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        authorization = connection.execute(
            'SELECT * FROM execution_continuations WHERE batch_id = ?', (request['batch_id'],),
        ).fetchone()
        if authorization_batch_id or authorization is not None:
            from app.repositories.execution_continuation_repository import digest_json
            if (authorization is None or authorization['closed_at'] is not None
                    or authorization_batch_id != request['batch_id']):
                raise ValidationError('Durable authorization is absent or closed')
            envelope = json.loads(authorization['envelope_json'])
            if (digest_json(envelope) != authorization['envelope_sha256']
                    or envelope['manifest']['manifest_sha256'] != request['manifest_sha256']
                    or envelope['context']['execution_profile'] != execution_profile
                    or envelope['context']['applet_uri_sha256'] != digest_json(applet_uri)
                    or sorted(envelope['task_ids']) != sorted(i['source_task_id'] for i in request['items'])):
                raise ValidationError('Durable authorization does not match publication')
            request['expires_at'] = min(request['expires_at'], envelope['expires_at'])
            request['instruction_hash'] = compute_instruction_hash(request)
            validate_request(request)
        assert_selected_tasks_have_dispatch_priority(
            connection,
            selected_task_ids=[
                item["source_task_id"] for item in request["items"]
            ],
            platform_name=request["platform_name"],
        )
        if has_active_automation_ui_run(connection, now=now_value):
            raise ValidationError(
                "Automation UI 扫描正在运行，当前不能获取平台写锁。"
            )
        cursor = connection.execute(
            """
            UPDATE shadowbot_commit_batches
            SET instruction_hash = ?, execution_attempt_id = ?, status = 'PUBLISHING', updated_at = ?
            WHERE batch_id = ? AND manifest_sha256 = ? AND status = 'PREPARED'
            """,
            (
                request["instruction_hash"],
                request["execution_attempt_id"],
                now,
                request["batch_id"],
                request["manifest_sha256"],
            ),
        )
        if cursor.rowcount != 1:
            raise ValidationError("批次未处于可发布的 PREPARED 状态。")
        for item in request["items"]:
            task = connection.execute(
                """
                SELECT task_status, action_type, internal_sku, platform_name,
                       expected_old_price, target_price, expires_at, decision_trace_json
                FROM tasks WHERE task_id = ?
                """,
                (item["source_task_id"],),
            ).fetchone()
            task_expired = False
            if task is not None and str(task["expires_at"] or "").strip():
                try:
                    task_expired = _task_time_as_utc(
                        datetime.fromisoformat(str(task["expires_at"]))
                    ) <= datetime.now(timezone.utc)
                except ValueError as exc:
                    raise ValidationError(
                        f"任务 expires_at 无法解析：{item['source_task_id']}"
                    ) from exc
            if (
                task is None
                or task_expired
                or task["task_status"] != TaskStatus.PENDING.value
                or task["action_type"] != TaskActionType.UPDATE_PRICE.value
                or str(task["internal_sku"] or "").upper() != item["internal_sku"]
                or str(task["platform_name"] or "") != request["platform_name"]
                or not _same_price(task["expected_old_price"], item["expected_old_price"])
                or not _same_price(task["target_price"], item["target_price"])
            ):
                raise ValidationError(f"任务在发布前已变化：{item['source_task_id']}")
            trace = json.loads(task['decision_trace_json'] or '{}')
            from app.services.price_decisions import unresolved_predecessors
            if (trace.get('price_decision_version') and not authorization_batch_id
                    or unresolved_predecessors(connection, item['source_task_id'])):
                raise ValidationError('Price decision requires final authorization and settled predecessors')
            active_lock = connection.execute(
                """
                SELECT operation_id, item_execution_attempt_id, status
                FROM shadowbot_write_locks
                WHERE write_identity_key = ? AND status IN ('ACTIVE', 'UNKNOWN')
                """,
                (item["write_identity_key"],),
            ).fetchone()
            if active_lock is not None:
                raise ValidationError(
                    f"商品写入身份已有活动锁：{item['internal_sku']}"
                )
            existing_operation = connection.execute(
                "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
                (item["operation_id"],),
            ).fetchone()
            product_identity_json = json.dumps(
                {
                    "internal_sku": item["internal_sku"],
                    "expected_product_name": item["expected_product_name"],
                    "expected_grade": item["expected_grade"],
                    "page_identity_key": item["page_identity_key"],
                    "write_identity_key": item["write_identity_key"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if existing_operation is None:
                connection.execute(
                    """
                    INSERT INTO shadowbot_operations(
                        operation_id, task_id, platform, product_identity_json,
                        expected_old_price, target_price, status, lock_owner,
                        approved_payload_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?)
                    """,
                    (
                        item["operation_id"],
                        item["source_task_id"],
                        request["platform_name"],
                        product_identity_json,
                        item["expected_old_price"],
                        item["target_price"],
                        item["item_execution_attempt_id"],
                        item["item_payload_sha256"],
                        now,
                        now,
                    ),
                )
            else:
                if (
                    str(existing_operation["task_id"]) != item["source_task_id"]
                    or str(existing_operation["platform"]) != request["platform_name"]
                    or not _same_price(
                        existing_operation["expected_old_price"],
                        item["expected_old_price"],
                    )
                    or not _same_price(
                        existing_operation["target_price"],
                        item["target_price"],
                    )
                    or str(existing_operation["approved_payload_hash"])
                    != item["item_payload_sha256"]
                    or str(existing_operation["status"]) not in {"PENDING", "START_FAILED"}
                    or str(existing_operation["lock_owner"] or "")
                ):
                    raise ValidationError(
                        f"逐商品操作账本不可复用：{item['source_task_id']}"
                    )
                connection.execute(
                    """
                    UPDATE shadowbot_operations
                    SET status = 'RUNNING', lock_owner = ?, updated_at = ?
                    WHERE operation_id = ?
                    """,
                    (
                        item["item_execution_attempt_id"],
                        now,
                        item["operation_id"],
                    ),
                )
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode,
                    shadowbot_run_id, status, side_effect_state, started_at,
                    instruction_hash, request_file_sha256, queue_request_path,
                    raw_output_json
                ) VALUES (?, ?, 'COMMIT', '', 'STARTING', 'NOT_STARTED', ?, ?, '', '', '{}')
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
                INSERT INTO shadowbot_write_locks(
                    write_identity_key, operation_id, item_execution_attempt_id,
                    batch_id, status, acquired_at, updated_at
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
                UPDATE shadowbot_commit_batch_items
                SET item_execution_attempt_id = ?, side_effect_state = 'NOT_STARTED',
                    updated_at = ?
                WHERE batch_id = ? AND source_task_id = ?
                """,
                (
                    item["item_execution_attempt_id"],
                    now,
                    request["batch_id"],
                    item["source_task_id"],
                ),
            )
            connection.execute(
                "UPDATE tasks SET task_status = ?, result_message = ?, updated_at = ? WHERE task_id = ?",
                (
                    TaskStatus.RUNNING.value,
                    f"ShadowBot COMMIT 发布中：{request['batch_id']}",
                    now,
                    item["source_task_id"],
                ),
            )
    try:
        result = runner.start(request)
    except ShadowBotStartBoundaryError as exc:
        failed_status = "UNKNOWN" if exc.published else "PREPARED"
        with closing(repository.connect_write()) as connection, connection:
            connection.execute(
                "UPDATE shadowbot_commit_batches SET status = ?, updated_at = ? WHERE batch_id = ?",
                (failed_status, _now_text(), request["batch_id"]),
            )
            if exc.published:
                for item in request["items"]:
                    connection.execute(
                        """
                        UPDATE shadowbot_execution_attempts
                        SET status = 'START_UNKNOWN', side_effect_state = 'UNKNOWN',
                            ended_at = ?
                        WHERE execution_attempt_id = ?
                        """,
                        (_now_text(), item["item_execution_attempt_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE shadowbot_operations
                        SET status = 'NEEDS_RECONCILIATION', updated_at = ?
                        WHERE operation_id = ?
                        """,
                        (_now_text(), item["operation_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE shadowbot_write_locks
                        SET status = 'UNKNOWN', updated_at = ?
                        WHERE write_identity_key = ?
                          AND item_execution_attempt_id = ?
                        """,
                        (
                            _now_text(),
                            item["write_identity_key"],
                            item["item_execution_attempt_id"],
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE shadowbot_commit_batch_items
                        SET status = 'UNKNOWN', side_effect_state = 'UNKNOWN',
                            error_code = 'PUBLISH_BOUNDARY_UNKNOWN',
                            error_message = '队列发布边界不确定', updated_at = ?
                        WHERE batch_id = ? AND source_task_id = ?
                        """,
                        (
                            _now_text(),
                            request["batch_id"],
                            item["source_task_id"],
                        ),
                    )
                    connection.execute(
                        "UPDATE tasks SET task_status = ?, result_message = ?, updated_at = ? WHERE task_id = ?",
                        (
                            TaskStatus.MANUAL_REVIEW.value,
                            f"ShadowBot 队列发布边界不确定：{request['batch_id']}",
                            _now_text(),
                            item["source_task_id"],
                        ),
                    )
            else:
                for item in request["items"]:
                    connection.execute(
                        """
                        UPDATE shadowbot_execution_attempts
                        SET status = 'START_FAILED', side_effect_state = 'NOT_STARTED',
                            ended_at = ?
                        WHERE execution_attempt_id = ?
                        """,
                        (_now_text(), item["item_execution_attempt_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE shadowbot_operations
                        SET status = 'PENDING', lock_owner = '', updated_at = ?
                        WHERE operation_id = ?
                        """,
                        (_now_text(), item["operation_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE shadowbot_write_locks
                        SET status = 'RELEASED', released_at = ?, updated_at = ?
                        WHERE write_identity_key = ?
                          AND item_execution_attempt_id = ?
                        """,
                        (
                            _now_text(),
                            _now_text(),
                            item["write_identity_key"],
                            item["item_execution_attempt_id"],
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE tasks SET task_status = ?, result_message = ?, updated_at = ?
                        WHERE task_id = ? AND task_status = ?
                        """,
                        (
                            TaskStatus.PENDING.value,
                            f"ShadowBot COMMIT 未发布：{request['batch_id']}",
                            _now_text(),
                            item["source_task_id"],
                            TaskStatus.RUNNING.value,
                        ),
                    )
        raise
    now = _now_text()
    with closing(repository.connect_write()) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE shadowbot_commit_batches
            SET status = 'QUEUED', updated_at = ?
            WHERE batch_id = ? AND execution_attempt_id = ? AND status = 'PUBLISHING'
            """,
            (now, request["batch_id"], request["execution_attempt_id"]),
        )
        if cursor.rowcount != 1:
            raise ValidationError("队列已发布，但批次账本未能从 PUBLISHING 转为 QUEUED。")
        for item in request["items"]:
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = 'RUNNING', shadowbot_run_id = ?,
                    request_file_sha256 = ?, queue_request_path = ?
                WHERE execution_attempt_id = ? AND status = 'STARTING'
                """,
                (
                    result.shadowbot_run_id,
                    str(result.raw_output.get("request_file_sha256") or ""),
                    str(result.raw_output.get("queue_request_path") or ""),
                    item["item_execution_attempt_id"],
                ),
            )
            connection.execute(
                """
                UPDATE tasks SET task_status = ?, result_message = ?, updated_at = ?
                WHERE task_id = ? AND task_status = ?
                """,
                (
                    TaskStatus.RUNNING.value,
                    f"ShadowBot COMMIT 批次已投递：{request['batch_id']}",
                    now,
                    item["source_task_id"],
                    TaskStatus.RUNNING.value,
                ),
            )
    return request, result


def import_task_commit_result(
    repository: SQLiteRuntimeRepository,
    result: dict[str, Any],
    *,
    listing_observations: Iterable[dict[str, Any]] = (),
    result_file_sha256: str = "",
    source_result_path: str = "",
) -> dict[str, int]:
    """Validate a complete v4 result and accept every projection in one transaction."""

    if not isinstance(result, dict) or result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValidationError("COMMIT 批次结果格式无效。")
    if result.get("contract_version") != CONTRACT_VERSION:
        raise ValidationError("COMMIT 批次结果合同版本无效。")
    batch_id = str(result.get("batch_id") or "")
    batch_status = str(result.get("batch_status") or "").upper()
    if batch_status not in FINAL_BATCH_STATUSES:
        raise ValidationError("COMMIT 批次结果状态无效。")
    result_id = str(result.get("result_id") or "").strip()
    raw_items = result.get("items")
    if not result_id or not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("COMMIT 批次结果缺少 result_id/items。")
    supplied_result_sha256 = str(result_file_sha256 or result.get("result_file_sha256") or "").strip()
    if not supplied_result_sha256:
        supplied_result_sha256 = sha256_json(result).removeprefix("sha256:")

    with closing(repository.connect_write()) as connection, connection:
        plan = _build_commit_import_plan(
            connection,
            result,
            listing_observations=tuple(listing_observations),
            result_file_sha256=supplied_result_sha256,
        )
        if plan.already_accepted:
            return plan.counts
        now = _now_text()
        batch = connection.execute(
            "SELECT * FROM shadowbot_commit_batches WHERE batch_id = ?",
            (plan.batch_id,),
        ).fetchone()
        assert batch is not None
        platform_name = str(batch["platform_name"])
        batch_stopped_before_submit = (
            str(result.get("side_effect_state") or "").upper() == "NOT_STARTED"
            and plan.counts["attempted"] == 0
        )
        for item in plan.items:
            task_id = item["source_task_id"]
            status = item["status"]
            connection.execute(
                """
                UPDATE shadowbot_commit_batch_items
                SET preflight_row = ?, preflight_price = ?, execution_ordinal = ?,
                    submit_attempted = ?, side_effect_state = ?,
                    preflight_observed_at = ?, submit_intent_at = ?,
                    submit_clicked_at = ?, readback_observed_at = ?,
                    actual_price = ?, status = ?, error_code = ?,
                    error_message = ?, updated_at = ?
                WHERE batch_id = ? AND source_task_id = ?
                """,
                (
                    item.get("preflight_row"),
                    item.get("preflight_price"),
                    item.get("execution_ordinal"),
                    1 if item["submit_attempted"] else 0,
                    item["side_effect_state"],
                    item.get("preflight_observed_at"),
                    item.get("submit_intent_at"),
                    item.get("submit_clicked_at"),
                    item.get("readback_observed_at"),
                    item.get("actual_price"),
                    status,
                    str(item.get("error_code") or ""),
                    str(item.get("error_message") or ""),
                    now,
                    batch_id,
                    task_id,
                ),
            )
            task_status = {
                "VERIFIED": TaskStatus.SUCCESS,
                "NOT_APPLIED": TaskStatus.FAILED,
                "FAILED": TaskStatus.FAILED,
                "UNKNOWN": TaskStatus.MANUAL_REVIEW,
                "NOT_ATTEMPTED": TaskStatus.PENDING,
            }[status]
            result_message = _task_result_message(batch_id, status, item)
            if (
                batch_stopped_before_submit
                and status == "FAILED"
                and item["submit_attempted"] is False
            ):
                task_status = TaskStatus.PENDING
                detail = str(item.get("error_message") or item.get("error_code") or status)
                result_message = (
                    f"ShadowBot COMMIT 提交前失败，任务保留 pending：{batch_id}；{detail}"
                )
            connection.execute(
                "UPDATE tasks SET task_status = ?, result_message = ?, updated_at = ? WHERE task_id = ?",
                (task_status.value, result_message, now, task_id),
            )
            project_manual_incident_task_result(
                connection,
                source_task_id=task_id,
                operation_id=item["operation_id"],
                outcome=status,
                result_id=plan.result_id,
                occurred_at=now,
            )
            attempt_status, operation_status, lock_status = _terminal_item_ledger_states(
                status,
                item["side_effect_state"],
            )
            if task_status is TaskStatus.PENDING and not item["submit_attempted"]:
                operation_status = "PENDING"
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, side_effect_state = ?, ended_at = ?,
                    raw_output_json = ?
                WHERE execution_attempt_id = ? AND operation_id = ?
                """,
                (
                    attempt_status,
                    item["side_effect_state"],
                    now,
                    json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    item["item_execution_attempt_id"],
                    item["operation_id"],
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, lock_owner = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (operation_status, "", now, item["operation_id"]),
            )
            checkpoint_version = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM shadowbot_side_effect_checkpoints
                    WHERE operation_id = ?
                    """,
                    (item["operation_id"],),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO shadowbot_side_effect_checkpoints(
                    operation_id, execution_attempt_id, side_effect_state,
                    checkpoint_at, version
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    item["operation_id"],
                    item["item_execution_attempt_id"],
                    item["side_effect_state"],
                    item.get("readback_observed_at")
                    or item.get("submit_clicked_at")
                    or item.get("submit_intent_at")
                    or item.get("preflight_observed_at")
                    or now,
                    checkpoint_version,
                ),
            )
            if lock_status == "UNKNOWN":
                connection.execute(
                    """
                    UPDATE shadowbot_write_locks
                    SET status = 'UNKNOWN', updated_at = ?
                    WHERE write_identity_key = ?
                      AND item_execution_attempt_id = ?
                    """,
                    (
                        now,
                        item["write_identity_key"],
                        item["item_execution_attempt_id"],
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE shadowbot_write_locks
                    SET status = 'RELEASED', released_at = ?, updated_at = ?
                    WHERE write_identity_key = ?
                      AND item_execution_attempt_id = ?
                    """,
                    (
                        now,
                        now,
                        item["write_identity_key"],
                        item["item_execution_attempt_id"],
                    ),
                )
            if status == "VERIFIED":
                identity = listing_identity_key(
                    platform_name,
                    item["expected_product_name"],
                    item["expected_grade"],
                )
                cursor = connection.execute(
                    """
                    UPDATE listing_status
                    SET internal_sku = ?, current_price = ?, source = 'shadowbot_commit_v4',
                        updated_at = ?, price_source = 'shadowbot_commit_v4',
                        price_observed_at = ?, price_source_attempt_id = ?
                    WHERE platform_name = ? AND variety = ? AND grade = ?
                      AND (price_observed_at IS NULL OR
                           julianday(price_observed_at) <= julianday(?))
                    """,
                    (
                        item["internal_sku"],
                        item["actual_price"],
                        item["readback_observed_at"],
                        item["readback_observed_at"],
                        item["item_execution_attempt_id"],
                        identity[0],
                        identity[1],
                        identity[2],
                        item["readback_observed_at"],
                    ),
                )
                if cursor.rowcount != 1 and connection.execute(
                    'SELECT 1 FROM listing_status WHERE platform_name = ? AND variety = ? AND grade = ?',
                    identity,
                ).fetchone() is None:
                    raise ValidationError(f"平台状态身份不存在或不唯一：{task_id}")
        for observation in plan.listing_observations:
            _apply_listing_observation(connection, observation)
        connection.execute(
            """
            UPDATE shadowbot_commit_batches
            SET result_id = ?, status = ?, updated_at = ? WHERE batch_id = ?
            """,
            (plan.result_id, plan.batch_status, now, plan.batch_id),
        )
        connection.execute(
            """
            INSERT INTO shadowbot_commit_result_receipts(
                result_id, batch_id, execution_attempt_id, instruction_hash,
                manifest_sha256, result_sha256, source_result_path,
                accepted_at, ack_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                plan.result_id,
                plan.batch_id,
                result["execution_attempt_id"],
                result["instruction_hash"],
                result["manifest_sha256"],
                supplied_result_sha256,
                str(source_result_path or ""),
                now,
            ),
        )
    return plan.counts


def _build_commit_import_plan(
    connection: Any,
    result: dict[str, Any],
    *,
    listing_observations: tuple[dict[str, Any], ...],
    result_file_sha256: str,
) -> CommitImportPlan:
    batch_id = str(result.get("batch_id") or "").strip()
    result_id = str(result.get("result_id") or "").strip()
    receipt = connection.execute(
        "SELECT * FROM shadowbot_commit_result_receipts WHERE result_id = ?",
        (result_id,),
    ).fetchone()
    raw_items = result.get("items")
    assert isinstance(raw_items, list)
    counts = _result_counts(raw_items)
    if receipt is not None:
        if (
            str(receipt["batch_id"]) != batch_id
            or str(receipt["execution_attempt_id"])
            != str(result.get("execution_attempt_id") or "")
            or str(receipt["result_sha256"]) != result_file_sha256
        ):
            raise ValidationError("COMMIT 技术回执与当前结果冲突。")
        return CommitImportPlan(
            batch_id=batch_id,
            result_id=result_id,
            batch_status=str(result.get("batch_status") or "").upper(),
            counts=counts,
            items=tuple(),
            listing_observations=tuple(),
            already_accepted=True,
        )

    batch = connection.execute(
        "SELECT * FROM shadowbot_commit_batches WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    if batch is None:
        raise ValidationError("COMMIT 批次账本不存在。")
    if str(batch["result_id"] or ""):
        raise ValidationError("COMMIT 批次已导入其他 result_id。")
    for name in ("execution_attempt_id", "instruction_hash", "manifest_sha256"):
        if str(result.get(name) or "") != str(batch[name] or ""):
            raise ValidationError(f"COMMIT 批次结果 {name} 与账本不一致。")

    expected_rows = connection.execute(
        "SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?",
        (batch_id,),
    ).fetchall()
    expected = {str(row["source_task_id"]): row for row in expected_rows}
    supplied = {
        str(item.get("source_task_id") or ""): item
        for item in raw_items
        if isinstance(item, dict)
    }
    if len(supplied) != len(raw_items) or set(supplied) != set(expected):
        raise ValidationError("COMMIT 批次结果项目集合与账本不一致。")
    supplied_counts = result.get("counts")
    if not isinstance(supplied_counts, dict):
        raise ValidationError("COMMIT 批次结果缺少 counts。")
    for name, value in counts.items():
        supplied_value = supplied_counts.get(name)
        if isinstance(supplied_value, bool) or not isinstance(supplied_value, int):
            raise ValidationError("COMMIT 批次结果 counts 必须是整数。")
        if supplied_value != value:
            raise ValidationError("COMMIT 批次结果计数恒等式不成立。")
    expected_semantics = derive_v4_batch_semantics(counts)
    for name in (
        "batch_status",
        "status",
        "run_success_flag",
        "business_operation_completed",
        "side_effect_state",
    ):
        if name not in result:
            raise ValidationError(f"COMMIT 批次结果缺少顶层语义字段：{name}")
        supplied_value = result[name]
        expected_value = expected_semantics[name]
        if isinstance(expected_value, bool):
            if type(supplied_value) is not bool or supplied_value is not expected_value:
                raise ValidationError(f"COMMIT 批次顶层语义与逐商品结果不一致：{name}")
        elif str(supplied_value or "").upper() != str(expected_value).upper():
            raise ValidationError(f"COMMIT 批次顶层语义与逐商品结果不一致：{name}")

    normalized_items: list[dict[str, Any]] = []
    for task_id, raw_item in supplied.items():
        row = expected[task_id]
        normalized_items.append(_normalize_result_item(raw_item, row, task_id))
    normalized_items.sort(
        key=lambda item: (
            item["execution_ordinal"] is None,
            item["execution_ordinal"] or 0,
            item["source_task_id"],
        )
    )
    expected_batch_status = expected_semantics["batch_status"]
    batch_status = str(result.get("batch_status") or "").upper()
    if batch_status != expected_batch_status:
        raise ValidationError(
            f"COMMIT 批次状态与逐商品结果不一致：{batch_status}/{expected_batch_status}"
        )
    normalized_observations = tuple(
        _normalize_listing_observation(observation)
        for observation in listing_observations
    )
    return CommitImportPlan(
        batch_id=batch_id,
        result_id=result_id,
        batch_status=batch_status,
        counts=counts,
        items=tuple(normalized_items),
        listing_observations=normalized_observations,
    )


def _normalize_result_item(
    item: dict[str, Any],
    expected_row: Any,
    task_id: str,
) -> dict[str, Any]:
    for name in (
        "item_id",
        "source_task_id",
        "operation_id",
        "item_execution_attempt_id",
        "write_identity_key",
        "page_identity_key",
        "internal_sku",
        "expected_product_name",
        "expected_grade",
        "expected_old_price",
        "target_price",
        "item_payload_sha256",
    ):
        if str(item.get(name) or "") != str(expected_row[name] or ""):
            raise ValidationError(f"COMMIT 项目 {name} 与账本不一致：{task_id}")
    status = str(item.get("status") or "").upper()
    if status not in ITEM_STATUSES:
        raise ValidationError(f"COMMIT 项目状态无效：{task_id}")
    submit_attempted = item.get("submit_attempted")
    if type(submit_attempted) is not bool:
        raise ValidationError(f"COMMIT 项目 submit_attempted 必须是布尔值：{task_id}")
    side_effect_state = str(item.get("side_effect_state") or "").upper()
    if side_effect_state not in ITEM_SIDE_EFFECT_STATES:
        raise ValidationError(f"COMMIT 项目 side_effect_state 无效：{task_id}")
    preflight_row = item.get("preflight_row")
    if preflight_row is not None and (
        isinstance(preflight_row, bool)
        or not isinstance(preflight_row, int)
        or preflight_row < 1
    ):
        raise ValidationError(f"COMMIT 项目 preflight_row 无效：{task_id}")
    execution_ordinal = item.get("execution_ordinal")
    if execution_ordinal is not None and (
        isinstance(execution_ordinal, bool)
        or not isinstance(execution_ordinal, int)
        or execution_ordinal < 1
    ):
        raise ValidationError(f"COMMIT 项目 execution_ordinal 无效：{task_id}")
    preflight_price = _optional_canonical_price(
        item.get("preflight_price"),
        f"{task_id}.preflight_price",
    )
    actual_price = _optional_canonical_price(
        item.get("actual_price"),
        f"{task_id}.actual_price",
    )
    preflight_observed_at = _optional_observation_time(
        item.get("preflight_observed_at"),
        f"{task_id}.preflight_observed_at",
    )
    submit_intent_at = _optional_observation_time(
        item.get("submit_intent_at"),
        f"{task_id}.submit_intent_at",
    )
    submit_clicked_at = _optional_observation_time(
        item.get("submit_clicked_at"),
        f"{task_id}.submit_clicked_at",
    )
    readback_observed_at = _optional_observation_time(
        item.get("readback_observed_at"),
        f"{task_id}.readback_observed_at",
    )
    if preflight_row is not None and (
        preflight_price is None or preflight_observed_at is None
    ):
        raise ValidationError(f"COMMIT 项目预扫描证据不完整：{task_id}")
    if status in {"FAILED", "NOT_ATTEMPTED"}:
        if submit_attempted or side_effect_state != "NOT_STARTED":
            raise ValidationError(f"COMMIT 提交前失败状态与副作用证据矛盾：{task_id}")
    elif status == "VERIFIED":
        if (
            not submit_attempted
            or side_effect_state != "VERIFIED"
            or submit_intent_at is None
            or submit_clicked_at is None
            or readback_observed_at is None
            or not _same_price(actual_price, item["target_price"])
        ):
            raise ValidationError(f"VERIFIED 项缺少完整提交与回读证据：{task_id}")
    elif status == "UNKNOWN":
        fail_closed_recovery = str(item.get("error_code") or "") in {
            "PHASE_UNAVAILABLE_SIDE_EFFECT_UNKNOWN",
            "PHASE_SNAPSHOT_BINDING_INVALID",
            "PHASE_ITEM_BINDING_INVALID",
            "PHASE_INSUFFICIENT_SIDE_EFFECT_UNKNOWN",
        }
        if (
            not submit_attempted
            or side_effect_state not in {"SUBMIT_CLICKED", "UNKNOWN"}
            or (
                submit_clicked_at is None
                and submit_intent_at is None
                and not fail_closed_recovery
            )
        ):
            raise ValidationError(f"UNKNOWN 项缺少提交风险证据：{task_id}")
    elif status == "NOT_APPLIED":
        no_click_proof = (
            not submit_attempted
            and side_effect_state == "NOT_APPLIED"
            and str(item.get("error_code") or "") == "SUBMIT_NOT_CLICKED"
            and submit_intent_at is not None
            and submit_clicked_at is None
        )
        readback_proof = (
            submit_attempted
            and side_effect_state == "NOT_APPLIED"
            and submit_clicked_at is not None
            and readback_observed_at is not None
            and _same_price(actual_price, item["expected_old_price"])
        )
        if not (no_click_proof or readback_proof):
            raise ValidationError(f"NOT_APPLIED 项缺少无副作用证明：{task_id}")
    return {
        **item,
        "source_task_id": task_id,
        "status": status,
        "submit_attempted": submit_attempted,
        "side_effect_state": side_effect_state,
        "preflight_row": preflight_row,
        "preflight_price": preflight_price,
        "execution_ordinal": execution_ordinal,
        "actual_price": actual_price,
        "preflight_observed_at": preflight_observed_at,
        "submit_intent_at": submit_intent_at,
        "submit_clicked_at": submit_clicked_at,
        "readback_observed_at": readback_observed_at,
        "error_code": str(item.get("error_code") or ""),
        "error_message": str(item.get("error_message") or ""),
    }


def _optional_canonical_price(value: Any, name: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return canonical_positive_price(value, require_canonical=True)
    except ValueError as exc:
        raise ValidationError(f"{name} 不是规范价格。") from exc


def _optional_observation_time(value: Any, name: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{name} 不是 ISO-8601 时间。") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{name} 必须包含时区。")
    return parsed.astimezone(timezone.utc).isoformat()


def _derived_batch_status(counts: dict[str, int]) -> str:
    return derive_v4_batch_semantics(counts)["batch_status"]


def _terminal_item_ledger_states(status: str, side_effect_state: str) -> tuple[str, str, str]:
    if status == "VERIFIED":
        return "VERIFIED", "VERIFIED", "RELEASED"
    if status == "NOT_APPLIED":
        return "NOT_APPLIED", "NOT_APPLIED", "RELEASED"
    if status == "UNKNOWN":
        return "SIDE_EFFECT_UNKNOWN", "NEEDS_RECONCILIATION", "UNKNOWN"
    if status == "NOT_ATTEMPTED":
        return "FAILED", "PENDING", "RELEASED"
    return "FAILED", "FAILED", "RELEASED"


def _normalize_listing_observation(observation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(observation, dict):
        raise ValidationError("COMMIT 页面观察必须是对象。")
    identity = observation.get("identity")
    if not isinstance(identity, tuple) or len(identity) != 3:
        raise ValidationError("COMMIT 页面观察 identity 无效。")
    observed_at = observation.get("observed_at")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValidationError("COMMIT 页面观察时间无效。")
    preserve_existing_price = observation.get("preserve_existing_price") is True
    observed_price: Decimal | None
    if preserve_existing_price:
        observed_price = None
    else:
        try:
            observed_price = Decimal(str(observation.get("observed_price")))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("COMMIT 页面观察价格无效。") from exc
        if not observed_price.is_finite() or observed_price < 0:
            raise ValidationError("COMMIT 页面观察价格无效。")
    inventory = observation.get("inventory")
    if isinstance(inventory, bool) or not isinstance(inventory, int) or inventory < 0:
        raise ValidationError("COMMIT 页面观察库存无效。")
    online_status = str(observation.get("online_status") or "")
    if online_status not in {"online", "offline"}:
        raise ValidationError("COMMIT 页面观察上下架状态无效。")
    return {
        **observation,
        "identity": tuple(str(value) for value in identity),
        "observed_price": observed_price,
        "preserve_existing_price": preserve_existing_price,
        "observed_at": observed_at.astimezone(timezone.utc),
        "inventory": inventory,
        "online_status": online_status,
        "preserve_existing_online_status": observation.get(
            "preserve_existing_online_status"
        )
        is True,
        "execution_attempt_id": str(
            observation.get("execution_attempt_id") or ""
        ),
    }


def _apply_listing_observation(connection: Any, observation: dict[str, Any]) -> None:
    platform_name, variety, grade = observation["identity"]
    observed_text = observation["observed_at"].isoformat()
    existing = connection.execute(
        """
        SELECT listing_status_id, current_price, online_status,
               inventory_observed_at, inventory_source_attempt_id,
               price_source, price_observed_at, price_source_attempt_id
        FROM listing_status
        WHERE platform_name = ? AND variety = ? AND grade = ?
        """,
        (platform_name, variety, grade),
    ).fetchone()
    if existing is not None:
        existing_at = str(existing["inventory_observed_at"] or "")
        existing_attempt = str(existing["inventory_source_attempt_id"] or "")
        if existing_at > observed_text or (
            existing_at == observed_text
            and existing_attempt
            not in {"", observation["execution_attempt_id"]}
        ):
            return
    existing_price_time = (_optional_observation_time(existing['price_observed_at'], 'price_observed_at')
                           if existing is not None else None)
    preserve_price = observation['preserve_existing_price'] or (
        existing_price_time is not None
        and datetime.fromisoformat(existing_price_time) > observation['observed_at']
    )
    if preserve_price:
        if existing is None:
            raise ValidationError("UNKNOWN 页面价格没有可保留的状态记录。")
        observed_price = str(existing["current_price"])
    else:
        observed_price = str(observation["observed_price"])
    if observation["preserve_existing_online_status"]:
        if existing is None:
            raise ValidationError("UNKNOWN 上下架状态没有可保留的状态记录。")
        online_status = str(existing["online_status"])
    else:
        online_status = observation["online_status"]
    connection.execute(
        """
        INSERT INTO listing_status(
            listing_status_id, platform_name, internal_sku, variety, grade,
            current_price, platform_stock_qty, sold_qty, online_status,
            source, updated_at, inventory_source, inventory_observed_at,
            inventory_source_attempt_id, price_source, price_observed_at, price_source_attempt_id
        ) VALUES (?, ?, '', ?, ?, ?, ?, 0, ?, ?, ?, 'shadowbot', ?, ?, ?, ?, ?)
        ON CONFLICT(platform_name, variety, grade) DO UPDATE SET
            current_price = excluded.current_price,
            platform_stock_qty = excluded.platform_stock_qty,
            online_status = excluded.online_status,
            source = excluded.source,
            inventory_source = excluded.inventory_source,
            inventory_observed_at = excluded.inventory_observed_at,
            inventory_source_attempt_id = excluded.inventory_source_attempt_id,
            price_source = excluded.price_source,
            price_observed_at = excluded.price_observed_at,
            price_source_attempt_id = excluded.price_source_attempt_id,
            updated_at = excluded.updated_at
        """,
        (
            f"LISTING-{uuid4().hex[:16]}",
            platform_name,
            variety,
            grade,
            observed_price,
            observation["inventory"],
            online_status,
            "shadowbot_commit_v4_page_snapshot",
            observed_text,
            observed_text,
            observation["execution_attempt_id"],
            existing['price_source'] if preserve_price else 'shadowbot_commit_v4_page_snapshot',
            existing['price_observed_at'] if preserve_price else observed_text,
            existing['price_source_attempt_id'] if preserve_price else observation['execution_attempt_id'],
        ),
    )


def _persist_prepared_manifest(
    repository: SQLiteRuntimeRepository,
    manifest: dict[str, Any],
    *,
    profile: str,
) -> None:
    now = _now_text()
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO shadowbot_batch_registry(
                batch_id, batch_type, contract_version, platform_name,
                created_at
            ) VALUES (?, 'update_price', 4, ?, ?)
            """,
            (
                manifest["batch_id"],
                manifest["platform_name"],
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO shadowbot_commit_batches(
                batch_id, contract_version, execution_profile, platform_name,
                manifest_sha256, status, created_at, updated_at
            ) VALUES (?, 4, ?, ?, ?, 'PREPARED', ?, ?)
            """,
            (
                manifest["batch_id"],
                profile,
                manifest["platform_name"],
                manifest["manifest_sha256"],
                now,
                now,
            ),
        )
        for item in manifest["items"]:
            connection.execute(
                """
                INSERT INTO shadowbot_commit_batch_items(
                    batch_id, source_task_id, item_id, operation_id,
                    internal_sku, expected_product_name,
                    expected_grade, expected_old_price, target_price,
                    item_payload_sha256, write_identity_key, page_identity_key,
                    status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (
                    manifest["batch_id"],
                    item["source_task_id"],
                    item["item_id"],
                    item["operation_id"],
                    item["internal_sku"],
                    item["expected_product_name"],
                    item["expected_grade"],
                    item["expected_old_price"],
                    item["target_price"],
                    item["item_payload_sha256"],
                    item["write_identity_key"],
                    item["page_identity_key"],
                    now,
                ),
            )


def _result_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(items), "attempted": 0, "verified": 0, "not_applied": 0, "failed": 0, "unknown": 0, "not_attempted": 0}
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("COMMIT 批次 item 必须是对象。")
        status = str(item.get("status") or "").upper()
        if status not in ITEM_STATUSES:
            raise ValidationError("COMMIT 批次包含未知项目状态。")
        submit_attempted = item.get("submit_attempted")
        if type(submit_attempted) is not bool:
            raise ValidationError("COMMIT 批次 submit_attempted 必须是布尔值。")
        if submit_attempted:
            counts["attempted"] += 1
        counts[status.lower()] += 1
    if counts["total"] != counts["verified"] + counts["not_applied"] + counts["failed"] + counts["unknown"] + counts["not_attempted"]:
        raise ValidationError("COMMIT 批次项目计数恒等式不成立。")
    return counts


def _task_result_message(batch_id: str, status: str, item: dict[str, Any]) -> str:
    if status == "VERIFIED":
        return f"ShadowBot COMMIT 已独立回读验证：{batch_id}，实际价格 {item.get('actual_price')}"
    if status == "NOT_ATTEMPTED":
        return f"ShadowBot COMMIT 未执行，任务保留 pending：{batch_id}"
    detail = str(item.get("error_message") or item.get("error_code") or status)
    return f"ShadowBot COMMIT {status}：{batch_id}；{detail}"


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_time_as_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    zone_name = str(os.environ.get("PRA_BUSINESS_TIMEZONE") or "Asia/Shanghai").strip()
    try:
        business_zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"无法解析业务时区：{zone_name}") from exc
    return value.replace(tzinfo=business_zone).astimezone(timezone.utc)


def _validate_listing_status_targets(
    repository: SQLiteRuntimeRepository,
    manifest: dict[str, Any],
) -> None:
    with closing(repository.connect_read()) as connection:
        for item in manifest["items"]:
            identity = listing_identity_key(
                manifest["platform_name"],
                item["expected_product_name"],
                item["expected_grade"],
            )
            count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM listing_status
                    WHERE platform_name = ? AND variety = ? AND grade = ?
                    """,
                    identity,
                ).fetchone()[0]
            )
            if count != 1:
                raise ValidationError(
                    f"SKU 未找到唯一平台状态身份：{item['internal_sku']}"
                )


def _same_price(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)).quantize(Decimal("0.01")) == Decimal(str(right)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return False
