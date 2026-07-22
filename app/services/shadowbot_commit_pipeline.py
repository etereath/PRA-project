from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.listing_identity import listing_identity_key
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_commit_batch import (
    CONTRACT_VERSION,
    build_commit_manifest,
    build_commit_request,
    load_identity_mapping,
    validate_request,
)
from app.services.shadowbot_executor import (
    ShadowBotFileQueueRunner,
    ShadowBotStartBoundaryError,
    ShadowBotStartResult,
)


RESULT_SCHEMA_VERSION = "shadowbot-commit-batch-result-1.0"
FINAL_BATCH_STATUSES = frozenset({"VERIFIED", "PARTIAL", "FAILED", "UNKNOWN"})
ITEM_STATUSES = frozenset(
    {"NOT_ATTEMPTED", "VERIFIED", "NOT_APPLIED", "FAILED", "UNKNOWN"}
)


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
) -> tuple[dict[str, Any], ShadowBotStartResult]:
    """Publish one request. Production uses valid pending tasks as its authority."""

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
    )
    validate_request(request)
    now = _now_text()
    with closing(repository.connect_write()) as connection, connection:
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
                       expected_old_price, target_price, expires_at
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
) -> dict[str, int]:
    """Verify and atomically import a v4 batch result into tasks and listing status."""

    if not isinstance(result, dict) or result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValidationError("COMMIT 批次结果格式无效。")
    if result.get("contract_version") != CONTRACT_VERSION:
        raise ValidationError("COMMIT 批次结果合同版本无效。")
    batch_id = str(result.get("batch_id") or "")
    batch_status = str(result.get("batch_status") or "").upper()
    if batch_status not in FINAL_BATCH_STATUSES:
        raise ValidationError("COMMIT 批次结果状态无效。")
    result_id = str(result.get("result_id") or "").strip()
    items = result.get("items")
    if not result_id or not isinstance(items, list) or not items:
        raise ValidationError("COMMIT 批次结果缺少 result_id/items。")

    with closing(repository.connect_write()) as connection, connection:
        batch = connection.execute(
            "SELECT * FROM shadowbot_commit_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise ValidationError("COMMIT 批次账本不存在。")
        if str(batch["result_id"] or ""):
            if str(batch["result_id"]) == result_id:
                return _result_counts(items)
            raise ValidationError("COMMIT 批次已导入其他 result_id。")
        for name in ("execution_attempt_id", "instruction_hash", "manifest_sha256"):
            if str(result.get(name) or "") != str(batch[name] or ""):
                raise ValidationError(f"COMMIT 批次结果 {name} 与账本不一致。")

        expected_rows = connection.execute(
            "SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
        expected = {str(row["source_task_id"]): row for row in expected_rows}
        supplied = {str(item.get("source_task_id") or ""): item for item in items if isinstance(item, dict)}
        if len(supplied) != len(items) or set(supplied) != set(expected):
            raise ValidationError("COMMIT 批次结果项目集合与账本不一致。")
        counts = _result_counts(items)
        supplied_counts = result.get("counts")
        if not isinstance(supplied_counts, dict) or any(
            int(supplied_counts.get(name, -1)) != value for name, value in counts.items()
        ):
            raise ValidationError("COMMIT 批次结果计数恒等式不成立。")

        now = _now_text()
        platform_name = str(batch["platform_name"])
        batch_stopped_before_submit = (
            str(result.get("side_effect_state") or "").upper() == "NOT_STARTED"
            and counts["attempted"] == 0
        )
        for task_id, item in supplied.items():
            row = expected[task_id]
            status = str(item.get("status") or "").upper()
            if status not in ITEM_STATUSES:
                raise ValidationError(f"COMMIT 项目状态无效：{task_id}")
            for name in (
                "internal_sku",
                "expected_product_name",
                "expected_grade",
                "expected_old_price",
                "target_price",
                "item_payload_sha256",
            ):
                if str(item.get(name) or "") != str(row[name] or ""):
                    raise ValidationError(f"COMMIT 项目 {name} 与账本不一致：{task_id}")
            connection.execute(
                """
                UPDATE shadowbot_commit_batch_items
                SET preflight_row = ?, preflight_price = ?, execution_ordinal = ?,
                    submit_attempted = ?, actual_price = ?, status = ?, error_code = ?,
                    error_message = ?, updated_at = ?
                WHERE batch_id = ? AND source_task_id = ?
                """,
                (
                    item.get("preflight_row"),
                    item.get("preflight_price"),
                    item.get("execution_ordinal"),
                    1 if item.get("submit_attempted") else 0,
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
                and not bool(item.get("submit_attempted"))
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
            if status == "VERIFIED":
                if not _same_price(item.get("actual_price"), item["target_price"]):
                    raise ValidationError(f"VERIFIED 项缺少目标价回读证据：{task_id}")
                identity = listing_identity_key(
                    platform_name,
                    item["expected_product_name"],
                    item["expected_grade"],
                )
                cursor = connection.execute(
                    """
                    UPDATE listing_status
                    SET internal_sku = ?, current_price = ?, source = 'shadowbot_commit_v4',
                        updated_at = ?
                    WHERE platform_name = ? AND variety = ? AND grade = ?
                    """,
                    (
                        item["internal_sku"],
                        item["actual_price"],
                        now,
                        identity[0],
                        identity[1],
                        identity[2],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValidationError(f"平台状态身份不存在或不唯一：{task_id}")
        connection.execute(
            """
            UPDATE shadowbot_commit_batches
            SET result_id = ?, status = ?, updated_at = ? WHERE batch_id = ?
            """,
            (result_id, batch_status, now, batch_id),
        )
    return counts


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
                    batch_id, source_task_id, internal_sku, expected_product_name,
                    expected_grade, expected_old_price, target_price,
                    item_payload_sha256, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (
                    manifest["batch_id"],
                    item["source_task_id"],
                    item["internal_sku"],
                    item["expected_product_name"],
                    item["expected_grade"],
                    item["expected_old_price"],
                    item["target_price"],
                    item["item_payload_sha256"],
                    now,
                ),
            )


def _result_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(items), "attempted": 0, "verified": 0, "not_applied": 0, "failed": 0, "unknown": 0, "not_attempted": 0}
    for item in items:
        status = str(item.get("status") or "").upper()
        if status not in ITEM_STATUSES:
            raise ValidationError("COMMIT 批次包含未知项目状态。")
        if item.get("submit_attempted"):
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
