"""Task 13 independent SYNC_STATUS publication, import, and reporting."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.enums import ReviewTaskStatus
from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import (
    ShadowBotFileQueueRunner,
    ShadowBotStartBoundaryError,
    ShadowBotStartResult,
)
from app.services.shadowbot_listing_action_contract import (
    build_listing_action_manifest,
    build_listing_action_request,
    validate_listing_action_request,
    validate_listing_action_result,
)


LISTING_ANOMALY_BLOCKED_ACTIONS = (
    "set_offline",
    "set_online",
    "update_price",
)
AUTO_CLEAR_POLICY = "AUTO_CLEAR_BY_COMPLETE_SNAPSHOT"
LISTING_ANOMALY_REVIEW_TYPE = "listing_location_anomaly"


def mapping_source_version(mapping_path: Path) -> str:
    """Return the immutable content version expected by the ShadowBot Worker."""

    return "sha256:" + sha256(Path(mapping_path).read_bytes()).hexdigest()


def prepare_listing_sync_batch(
    repository: SQLiteRuntimeRepository,
    *,
    batch_id: str,
    platform_name: str,
    mapping_path: Path,
    execution_profile: str = "production",
) -> dict[str, Any]:
    """Create and persist one immutable v5 independent SYNC_STATUS manifest."""

    profile = str(execution_profile or "").strip().lower()
    if profile not in {"development", "production"}:
        raise ValidationError("execution_profile 必须是 development 或 production。")
    manifest = build_listing_action_manifest(
        batch_id=batch_id,
        action_type="sync_status",
        task_items=None,
        identity_mapping=None,
        platform_name=platform_name,
        mapping_source_version=mapping_source_version(mapping_path),
    )
    now = _now_text()
    with closing(repository.connect_write()) as connection, connection:
        existing = connection.execute(
            """
            SELECT registry.batch_type, registry.contract_version,
                   registry.platform_name, batch.execution_profile,
                   batch.manifest_sha256, batch.status
            FROM shadowbot_batch_registry AS registry
            JOIN shadowbot_listing_action_batches AS batch
              ON batch.batch_id = registry.batch_id
            WHERE registry.batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["batch_type"]) == "sync_status"
                and int(existing["contract_version"]) == 5
                and str(existing["platform_name"]) == manifest["platform_name"]
                and str(existing["execution_profile"]) == profile
                and str(existing["manifest_sha256"])
                == manifest["manifest_sha256"]
                and str(existing["status"]) == "PREPARED"
            ):
                return manifest
            raise ValidationError("batch_id 已存在且不是同一 PREPARED SYNC_STATUS。")
        connection.execute(
            """
            INSERT INTO shadowbot_batch_registry(
                batch_id, batch_type, contract_version, platform_name, created_at
            ) VALUES (?, 'sync_status', 5, ?, ?)
            """,
            (batch_id, manifest["platform_name"], now),
        )
        connection.execute(
            """
            INSERT INTO shadowbot_listing_action_batches(
                batch_id, contract_version, execution_profile, action_type,
                platform_name, manifest_sha256, status, batch_target_count,
                created_at, updated_at
            ) VALUES (?, 5, ?, 'sync_status', ?, ?, 'PREPARED', 0, ?, ?)
            """,
            (
                batch_id,
                profile,
                manifest["platform_name"],
                manifest["manifest_sha256"],
                now,
                now,
            ),
        )
    return manifest


def publish_listing_sync_batch(
    repository: SQLiteRuntimeRepository,
    runner: ShadowBotFileQueueRunner,
    *,
    manifest: dict[str, Any],
    execution_profile: str,
    applet_uri: str,
    execution_attempt_id: str | None = None,
    window_title: str = "蚂蚁花团供应商",
    capture_evidence: bool = False,
) -> tuple[dict[str, Any], ShadowBotStartResult]:
    """Publish exactly one v5 read-only queue request."""

    _assert_queue_ready_for_publication(runner)
    attempt_id = execution_attempt_id or f"ATTEMPT-{uuid4().hex[:16]}"
    request = build_listing_action_request(
        manifest,
        execution_profile=execution_profile,
        execution_attempt_id=attempt_id,
        applet_uri=applet_uri,
        window_title=window_title,
        capture_evidence=capture_evidence,
    )
    validate_listing_action_request(request)
    now = _now_text()
    with closing(repository.connect_write()) as connection, connection:
        changed = connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET instruction_hash = ?, execution_attempt_id = ?,
                status = 'PUBLISHING', updated_at = ?
            WHERE batch_id = ? AND manifest_sha256 = ? AND status = 'PREPARED'
            """,
            (
                request["instruction_hash"],
                request["execution_attempt_id"],
                now,
                request["batch_id"],
                request["manifest_sha256"],
            ),
        ).rowcount
        if changed != 1:
            raise ValidationError("SYNC_STATUS 批次未处于可发布的 PREPARED 状态。")
    try:
        start_result = runner.start(request)
    except ShadowBotStartBoundaryError as exc:
        terminal_status = "QUEUED" if exc.published else "PREPARED"
        with closing(repository.connect_write()) as connection, connection:
            connection.execute(
                """
                UPDATE shadowbot_listing_action_batches
                SET status = ?, updated_at = ?
                WHERE batch_id = ? AND status = 'PUBLISHING'
                """,
                (terminal_status, _now_text(), request["batch_id"]),
            )
        raise
    with closing(repository.connect_write()) as connection, connection:
        changed = connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET status = 'QUEUED', updated_at = ?
            WHERE batch_id = ? AND status = 'PUBLISHING'
            """,
            (_now_text(), request["batch_id"]),
        ).rowcount
        if changed != 1:
            raise ValidationError("SYNC_STATUS 请求已发布，但批次状态无法更新为 QUEUED。")
    return request, start_result


def import_listing_sync_result(
    repository: SQLiteRuntimeRepository,
    *,
    request: dict[str, Any],
    result: dict[str, Any],
    result_file_sha256: str,
    source_result_path: str,
    failure_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Atomically accept a v5 snapshot and project all derived runtime facts."""

    validate_listing_action_request(request, check_expiry=False)
    validate_listing_action_result(
        result,
        request=request,
        request_file_sha256=str(result.get("request_file_sha256") or ""),
    )
    if request["action_type"] != "sync_status":
        raise ValidationError("当前导入器只接受独立 SYNC_STATUS 结果。")
    if not _is_hex_sha256(result_file_sha256):
        raise ValidationError("result_file_sha256 无效。")
    snapshot = dict(result["snapshot"])
    _validate_snapshot_result_binding(request, result, snapshot)
    now = datetime.now(UTC)
    now_text = now.isoformat()
    batch_id = str(request["batch_id"])
    result_id = str(result.get("result_id") or "").strip()
    if not result_id:
        raise ValidationError("SYNC_STATUS 结果缺少 result_id。")

    connection = repository.connect_write()
    try:
        connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute(
            """
            SELECT * FROM shadowbot_listing_action_batches
            WHERE batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
        if batch is None or str(batch["action_type"]) != "sync_status":
            raise ValidationError("SYNC_STATUS 批次不存在或类型不匹配。")
        existing_receipt = connection.execute(
            """
            SELECT batch_id, result_sha256
            FROM shadowbot_listing_result_receipts
            WHERE result_id = ?
            """,
            (result_id,),
        ).fetchone()
        if existing_receipt is not None:
            if (
                str(existing_receipt["batch_id"]) != batch_id
                or str(existing_receipt["result_sha256"])
                != result_file_sha256
            ):
                raise ValidationError("同一 result_id 对应了不同结果文件。")
            connection.rollback()
            return _existing_import_summary(repository, batch_id, result_id)
        if str(batch["result_id"] or ""):
            raise ValidationError("SYNC_STATUS 批次已经绑定其他 result_id。")
        _assert_no_unimported_listing_write(connection, batch_id)

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
                batch_id,
                request["execution_attempt_id"],
                request["instruction_hash"],
                request["manifest_sha256"],
                result_file_sha256,
                str(source_result_path or ""),
                now_text,
            ),
        )
        _insert_snapshot(connection, batch_id, snapshot)
        if failure_injector is not None:
            failure_injector("after_snapshot_insert")

        if snapshot["snapshot_complete"] is not True:
            connection.execute(
                """
                UPDATE shadowbot_listing_action_batches
                SET result_id = ?, status = 'FAILED', failed_count = 0,
                    updated_at = ?
                WHERE batch_id = ?
                """,
                (result_id, now_text, batch_id),
            )
            connection.commit()
            return {
                "batch_id": batch_id,
                "result_id": result_id,
                "snapshot_id": snapshot["snapshot_id"],
                "status": "FAILED",
                "projected_count": 0,
                "anomaly_count": 0,
                "review_created_count": 0,
                "review_cleared_count": 0,
                "notification_created_count": 0,
                "notification_cancelled_count": 0,
                "items": [],
                "already_imported": False,
            }

        _assert_snapshot_not_stale(connection, snapshot)
        item_rows = _insert_snapshot_items(connection, snapshot)
        if failure_injector is not None:
            failure_injector("after_snapshot_items_insert")
        projected_items = _project_online_status(connection, snapshot, item_rows)
        if failure_injector is not None:
            failure_injector("after_status_projection")
        anomaly_summary = _apply_listing_anomalies(
            repository,
            connection,
            snapshot=snapshot,
            item_rows=item_rows,
            now=now,
        )
        if failure_injector is not None:
            failure_injector("after_anomaly_projection")
        connection.execute(
            """
            UPDATE shadowbot_listing_action_batches
            SET result_id = ?, status = 'VERIFIED',
                batch_target_count = ?, verified_count = ?,
                failed_count = 0, updated_at = ?
            WHERE batch_id = ?
            """,
            (
                result_id,
                len(item_rows),
                len(item_rows),
                now_text,
                batch_id,
            ),
        )
        connection.commit()
        return {
            "batch_id": batch_id,
            "result_id": result_id,
            "snapshot_id": snapshot["snapshot_id"],
            "status": "VERIFIED",
            "projected_count": len(projected_items),
            **anomaly_summary,
            "items": projected_items,
            "already_imported": False,
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def mark_listing_sync_ack(
    repository: SQLiteRuntimeRepository,
    *,
    result_id: str,
    written: bool,
    error_message: str = "",
) -> None:
    """Persist the post-commit file ACK outcome."""

    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            UPDATE shadowbot_listing_result_receipts
            SET ack_state = ?, ack_updated_at = ?, last_projection_error = ?
            WHERE result_id = ?
            """,
            (
                "WRITTEN" if written else "FAILED",
                _now_text(),
                "" if written else str(error_message or "")[:1000],
                result_id,
            ),
        )


def render_listing_sync_markdown(
    *,
    request: dict[str, Any],
    result: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    """Render a concise human-readable report instead of copying raw JSON."""

    snapshot = result["snapshot"]
    successful = summary["status"] == "VERIFIED"
    lines = [
        "# 平台商品状态同步报告",
        "",
        f"- 结果：{'成功' if successful else '失败'}",
        f"- 批次 ID：`{request['batch_id']}`",
        f"- 运行 ID：`{request['execution_attempt_id']}`",
        f"- 快照 ID：`{snapshot['snapshot_id']}`",
        f"- 平台：{request['platform_name']}",
        "- 扫描范围：上架中、待上架",
        (
            "- 完整性：两页扫描及结束标记均已确认"
            if snapshot["snapshot_complete"]
            else f"- 完整性：失败（{snapshot.get('error_code') or '未知错误'}）"
        ),
        "",
    ]
    if not successful:
        lines.extend(
            [
                "本轮只记录失败快照，没有更新商品上下架状态，也没有创建或清除页面异常。",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## 商品结果",
            "",
            "| SKU | 商品 | 等级 | 页面位置 | 上架中/待上架次数 | 状态投影 | 处理 |",
            "|---|---|---|---|---:|---|---|",
        ]
    )
    projected_by_sku = {
        str(item.get("internal_sku") or ""): item for item in summary.get("items") or []
    }
    for item in snapshot["items"]:
        sku = str(item.get("internal_sku") or "未映射")
        projected = projected_by_sku.get(str(item.get("internal_sku") or ""))
        projection = (
            str(projected.get("online_status"))
            if projected is not None
            else "保留原值/未投影"
        )
        handling = (
            "正常"
            if item["listing_location"] in {"online_only", "waiting_only"}
            else "已创建或更新人工复核"
        )
        lines.append(
            "| {sku} | {name} | {grade} | {location} | {online}/{waiting} | {projection} | {handling} |".format(
                sku=sku,
                name=item["product_name"],
                grade=item["grade"],
                location=item["listing_location"],
                online=item["online_occurrences"],
                waiting=item["waiting_occurrences"],
                projection=projection,
                handling=handling,
            )
        )
    lines.extend(
        [
            "",
            "## 数据库结果",
            "",
            f"- 状态投影：{summary['projected_count']} 项",
            f"- 当前异常：{summary['anomaly_count']} 项",
            f"- 新建 Review：{summary['review_created_count']} 项",
            f"- 自动清除 Review：{summary['review_cleared_count']} 项",
            f"- 新建通知 Outbox：{summary['notification_created_count']} 项",
            f"- 取消未发送通知：{summary['notification_cancelled_count']} 项",
            (
                "- 价格与库存：完整快照的最新页面观察值已投影到 "
                "`listing_status`，原始证据仍保留在快照商品项中。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _insert_snapshot(
    connection: Any,
    batch_id: str,
    snapshot: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO listing_sync_snapshots(
            snapshot_id, batch_id, platform_name, execution_attempt_id,
            scan_started_at, scan_completed_at,
            online_scan_started_at, online_scan_completed_at,
            waiting_scan_started_at, waiting_scan_completed_at,
            online_scan_complete, waiting_scan_complete, snapshot_complete,
            online_end_marker_verified, waiting_end_marker_verified,
            instruction_hash, result_id, status, error_code,
            evidence_manifest_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot["snapshot_id"],
            batch_id,
            snapshot["platform_name"],
            snapshot["execution_attempt_id"],
            snapshot["scan_started_at"],
            snapshot["scan_completed_at"],
            snapshot["online_scan_started_at"],
            snapshot["online_scan_completed_at"],
            snapshot["waiting_scan_started_at"],
            snapshot["waiting_scan_completed_at"],
            int(snapshot["online_scan_complete"]),
            int(snapshot["waiting_scan_complete"]),
            int(snapshot["snapshot_complete"]),
            int(snapshot["online_end_marker_verified"]),
            int(snapshot["waiting_end_marker_verified"]),
            snapshot["instruction_hash"],
            snapshot["result_id"],
            snapshot["status"],
            str(snapshot.get("error_code") or ""),
            snapshot["evidence_manifest_sha256"],
        ),
    )


def _insert_snapshot_items(
    connection: Any,
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in snapshot["items"]:
        row = dict(item)
        connection.execute(
            """
            INSERT INTO listing_sync_snapshot_items(
                snapshot_item_id, snapshot_id, internal_sku, product_name,
                grade, page_identity_key, affected_internal_skus_json,
                online_occurrences, waiting_occurrences, listing_location,
                online_row_identities_json, waiting_row_identities_json,
                online_observed_price, waiting_observed_price,
                online_observed_inventory, waiting_observed_inventory,
                diagnostic_code, online_observed_at, waiting_observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["snapshot_item_id"],
                snapshot["snapshot_id"],
                item.get("internal_sku"),
                item["product_name"],
                item["grade"],
                item["page_identity_key"],
                _json_text(item["affected_internal_skus"]),
                item["online_occurrences"],
                item["waiting_occurrences"],
                item["listing_location"],
                _json_text(item["online_row_identities"]),
                _json_text(item["waiting_row_identities"]),
                item.get("online_observed_price"),
                item.get("waiting_observed_price"),
                item.get("online_observed_inventory"),
                item.get("waiting_observed_inventory"),
                str(item.get("diagnostic_code") or ""),
                item.get("online_observed_at"),
                item.get("waiting_observed_at"),
            ),
        )
        rows.append(row)
    return rows


def _project_online_status(
    connection: Any,
    snapshot: dict[str, Any],
    item_rows: list[dict[str, Any]],
    *,
    projection_source: str = "shadowbot_sync_status",
    source_type: str = "SYNC_STATUS",
) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for item in item_rows:
        internal_sku = str(item.get("internal_sku") or "")
        location = str(item["listing_location"])
        if not internal_sku or location == "ambiguous":
            continue
        observed_price = (
            item.get("online_observed_price")
            if location in {"online_only", "both"}
            else item.get("waiting_observed_price")
        )
        observed_inventory = (
            item.get("online_observed_inventory")
            if location in {"online_only", "both"}
            else item.get("waiting_observed_inventory")
        )
        observed_at = (
            item.get("online_observed_at")
            if location in {"online_only", "both"}
            else item.get("waiting_observed_at")
        )
        matches = connection.execute(
            """
            SELECT listing_status_id FROM listing_status
            WHERE platform_name = ? AND internal_sku = ?
            """,
            (snapshot["platform_name"], internal_sku),
        ).fetchall()
        if len(matches) > 1:
            raise ValidationError(
                f"listing_status 无法按平台和 SKU 唯一定位：{internal_sku}"
            )
        online_status = "online" if location in {"online_only", "both"} else "offline"
        projection_action = "UPDATED"
        if not matches:
            if (
                observed_price is None
                or observed_inventory is None
                or observed_at is None
            ):
                # ABSENT_FROM_BOTH_LISTS is still a valid anomaly fact, but it
                # cannot safely create a formal listing row without a platform
                # price, inventory and observation time.  The anomaly/Review
                # transaction continues below.
                continue
            grade = str(item["grade"]).strip()
            normalized_grade = grade[:-1] if grade.endswith("级") else grade
            identity_conflict = connection.execute(
                """
                SELECT internal_sku FROM listing_status
                WHERE platform_name = ? AND variety = ? AND grade = ?
                """,
                (
                    snapshot["platform_name"],
                    str(item["product_name"]).strip(),
                    normalized_grade,
                ),
            ).fetchone()
            if identity_conflict is not None:
                raise ValidationError(
                    "listing_status 页面身份已绑定其他 SKU："
                    f"{item['product_name']} {grade}"
                )
            listing_status_id = f"LISTING-{uuid4().hex[:16]}"
            connection.execute(
                """
                INSERT INTO listing_status(
                    listing_status_id, platform_name, internal_sku,
                    variety, grade, current_price, platform_stock_qty,
                    online_status, source, updated_at,
                    inventory_source, inventory_observed_at,
                    inventory_source_attempt_id, price_source,
                    price_observed_at, price_source_attempt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing_status_id,
                    snapshot["platform_name"],
                    internal_sku,
                    str(item["product_name"]).strip(),
                    normalized_grade,
                    str(observed_price),
                    int(observed_inventory),
                    online_status,
                    projection_source,
                    snapshot["scan_completed_at"],
                    projection_source,
                    observed_at,
                    snapshot["execution_attempt_id"],
                    projection_source,
                    observed_at,
                    snapshot["execution_attempt_id"],
                ),
            )
            matches = [{"listing_status_id": listing_status_id}]
            projection_action = "CREATED"
        if (
            observed_price is not None
            and observed_inventory is not None
            and observed_at is not None
        ):
            connection.execute(
                """
                UPDATE listing_status
                SET current_price = ?, platform_stock_qty = ?,
                    online_status = ?, source = ?,
                    updated_at = ?,
                    inventory_source = ?,
                    inventory_observed_at = ?,
                    inventory_source_attempt_id = ?,
                    price_source = ?,
                    price_observed_at = ?,
                    price_source_attempt_id = ?,
                    online_status_observed_at = ?,
                    online_status_source_type = ?,
                    online_status_source_id = ?
                WHERE listing_status_id = ?
                """,
                (
                    str(observed_price),
                    int(observed_inventory),
                    online_status,
                    projection_source,
                    snapshot["scan_completed_at"],
                    projection_source,
                    observed_at,
                    snapshot["execution_attempt_id"],
                    projection_source,
                    observed_at,
                    snapshot["execution_attempt_id"],
                    snapshot["scan_completed_at"],
                    source_type,
                    snapshot["snapshot_id"],
                    matches[0]["listing_status_id"],
                ),
            )
        else:
            connection.execute(
                """
                UPDATE listing_status
                SET online_status = ?, source = ?,
                    updated_at = ?, online_status_observed_at = ?,
                    online_status_source_type = ?,
                    online_status_source_id = ?
                WHERE listing_status_id = ?
                """,
                (
                    online_status,
                    projection_source,
                    snapshot["scan_completed_at"],
                    snapshot["scan_completed_at"],
                    source_type,
                    snapshot["snapshot_id"],
                    matches[0]["listing_status_id"],
                ),
            )
        projected.append(
            {
                "internal_sku": internal_sku,
                "listing_location": location,
                "online_status": online_status,
                "projection_action": projection_action,
            }
        )
    return projected


def _apply_listing_anomalies(
    repository: SQLiteRuntimeRepository,
    connection: Any,
    *,
    snapshot: dict[str, Any],
    item_rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, int]:
    desired: dict[str, dict[str, Any]] = {}
    for item in item_rows:
        for anomaly in _item_anomalies(snapshot, item):
            desired[anomaly["dedupe_key"]] = anomaly
    open_rows = connection.execute(
        """
        SELECT * FROM listing_anomaly_cases
        WHERE platform_name = ? AND cleared_at IS NULL
          AND resolution_policy = ?
        """,
        (snapshot["platform_name"], AUTO_CLEAR_POLICY),
    ).fetchall()
    open_by_dedupe = {str(row["dedupe_key"]): row for row in open_rows}
    review_created = 0
    notification_created = 0
    for dedupe_key, anomaly in desired.items():
        existing = open_by_dedupe.get(dedupe_key)
        if existing is None:
            review_id = anomaly["review_task_id"]
            _insert_review(connection, anomaly, snapshot, now)
            review_created += 1
            _insert_review_notification(
                repository,
                connection,
                anomaly=anomaly,
                snapshot=snapshot,
                now=now,
            )
            notification_created += 1
            connection.execute(
                """
                INSERT INTO listing_anomaly_cases(
                    anomaly_case_id, snapshot_id, snapshot_item_id,
                    platform_name, internal_sku, page_identity_key,
                    affected_internal_skus_json, anomaly_subject_key,
                    dedupe_key, reason_code, diagnostic_message,
                    resolution_policy, blocked_actions_json, created_at,
                    review_task_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    anomaly["anomaly_case_id"],
                    snapshot["snapshot_id"],
                    anomaly["snapshot_item_id"],
                    snapshot["platform_name"],
                    anomaly.get("internal_sku"),
                    anomaly["page_identity_key"],
                    _json_text(anomaly["affected_internal_skus"]),
                    anomaly["anomaly_subject_key"],
                    dedupe_key,
                    anomaly["reason_code"],
                    anomaly["diagnostic_message"],
                    AUTO_CLEAR_POLICY,
                    _json_text(list(LISTING_ANOMALY_BLOCKED_ACTIONS)),
                    now.isoformat(),
                    review_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE listing_anomaly_cases
                SET snapshot_id = ?, snapshot_item_id = ?,
                    internal_sku = ?, page_identity_key = ?,
                    affected_internal_skus_json = ?,
                    diagnostic_message = ?, blocked_actions_json = ?
                WHERE anomaly_case_id = ?
                """,
                (
                    snapshot["snapshot_id"],
                    anomaly["snapshot_item_id"],
                    anomaly.get("internal_sku"),
                    anomaly["page_identity_key"],
                    _json_text(anomaly["affected_internal_skus"]),
                    anomaly["diagnostic_message"],
                    _json_text(list(LISTING_ANOMALY_BLOCKED_ACTIONS)),
                    existing["anomaly_case_id"],
                ),
            )
            review_id = str(existing["review_task_id"] or "")
            if review_id:
                connection.execute(
                    """
                    UPDATE review_tasks
                    SET reason = ?, review_payload_json = ?, updated_at = ?
                    WHERE review_task_id = ? AND review_status = 'pending'
                    """,
                    (
                        anomaly["diagnostic_message"],
                        _json_text(_review_payload(anomaly, snapshot)),
                        now.isoformat(),
                        review_id,
                    ),
                )

    review_cleared = 0
    notifications_cancelled = 0
    for dedupe_key, row in open_by_dedupe.items():
        if dedupe_key in desired:
            continue
        review_id = str(row["review_task_id"] or "")
        previous_reason = str(row["reason_code"])
        connection.execute(
            """
            UPDATE listing_anomaly_cases
            SET cleared_at = ?, cleared_by_snapshot_id = ?
            WHERE anomaly_case_id = ? AND cleared_at IS NULL
            """,
            (now.isoformat(), snapshot["snapshot_id"], row["anomaly_case_id"]),
        )
        if review_id:
            changed = connection.execute(
                """
                UPDATE review_tasks
                SET review_status = ?, resolution_payload_json = ?,
                    updated_at = ?, resolved_by = 'system:listing_sync',
                    resolved_at = ?,
                    resolution_note = ?
                WHERE review_task_id = ? AND review_status = 'pending'
                  AND review_type = ?
                """,
                (
                    ReviewTaskStatus.CANCELLED.value,
                    _json_text(
                        {
                            "resolution_type": "AUTO_CLEARED_BY_SNAPSHOT",
                            "resolved_by_snapshot_id": snapshot["snapshot_id"],
                            "previous_reason_code": previous_reason,
                        }
                    ),
                    now.isoformat(),
                    now.isoformat(),
                    "新完整快照已证明原页面位置异常不再存在",
                    review_id,
                    LISTING_ANOMALY_REVIEW_TYPE,
                ),
            ).rowcount
            if changed == 1:
                review_cleared += 1
                connection.execute(
                    """
                    UPDATE review_tokens
                    SET revoked_at = ?
                    WHERE review_task_id = ? AND revoked_at IS NULL
                    """,
                    (now.isoformat(), review_id),
                )
                notifications_cancelled += repository._cancel_review_outbox_on_connection(
                    connection,
                    review_id,
                    changed_at=now,
                )
    return {
        "anomaly_count": len(desired),
        "review_created_count": review_created,
        "review_cleared_count": review_cleared,
        "notification_created_count": notification_created,
        "notification_cancelled_count": notifications_cancelled,
    }


def _item_anomalies(
    snapshot: dict[str, Any],
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    internal_sku = str(item.get("internal_sku") or "")
    affected = list(item.get("affected_internal_skus") or [])
    location = str(item["listing_location"])
    reasons: list[str] = []
    if not internal_sku:
        reasons.append(
            "IDENTITY_MAPPING_CONFLICT" if affected else "UNMAPPED_PRODUCT"
        )
    elif location == "ambiguous":
        reasons.append(
            "IDENTITY_MAPPING_CONFLICT"
            if len(affected) > 1
            else "DUPLICATE_PAGE_IDENTITY"
        )
    elif location == "both":
        reasons.append("PRESENT_IN_BOTH_LISTS")
    elif location == "neither":
        reasons.append("ABSENT_FROM_BOTH_LISTS")
    anomalies: list[dict[str, Any]] = []
    for reason in reasons:
        subject = _anomaly_subject(snapshot["platform_name"], item, reason)
        dedupe_key = "listing-anomaly:" + sha256(
            f"{snapshot['platform_name']}|{reason}|{subject}".encode("utf-8")
        ).hexdigest()
        occurrence = sha256(
            f"{dedupe_key}|{snapshot['snapshot_id']}".encode("utf-8")
        ).hexdigest()[:24]
        anomalies.append(
            {
                "anomaly_case_id": "ANOMALY-" + occurrence,
                "review_task_id": "REVIEW-" + occurrence,
                "snapshot_item_id": item["snapshot_item_id"],
                "internal_sku": internal_sku or None,
                "page_identity_key": item["page_identity_key"],
                "affected_internal_skus": affected,
                "anomaly_subject_key": subject,
                "dedupe_key": dedupe_key,
                "reason_code": reason,
                "diagnostic_message": _anomaly_message(item, reason),
            }
        )
    return anomalies


def _insert_review(
    connection: Any,
    anomaly: dict[str, Any],
    snapshot: dict[str, Any],
    now: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO review_tasks(
            review_task_id, trade_date, scope_type, scope_key, dedupe_key,
            source_task_id, review_type, review_status, internal_sku,
            platform_name, reason, review_payload_json,
            resolution_payload_json, required_by, created_at, updated_at,
            resolved_by, resolved_at, resolution_note
        ) VALUES (?, NULL, 'platform_listing', ?, ?, NULL, ?, 'pending',
                  ?, ?, ?, ?, '{}', ?, ?, ?, '', NULL, '')
        """,
        (
            anomaly["review_task_id"],
            anomaly["anomaly_subject_key"],
            anomaly["dedupe_key"],
            LISTING_ANOMALY_REVIEW_TYPE,
            anomaly.get("internal_sku"),
            snapshot["platform_name"],
            anomaly["diagnostic_message"],
            _json_text(_review_payload(anomaly, snapshot)),
            (now + timedelta(hours=24)).isoformat(),
            now.isoformat(),
            now.isoformat(),
        ),
    )


def _insert_review_notification(
    repository: SQLiteRuntimeRepository,
    connection: Any,
    *,
    anomaly: dict[str, Any],
    snapshot: dict[str, Any],
    now: datetime,
) -> None:
    notification_id = "NOTIFY-" + sha256(
        anomaly["review_task_id"].encode("utf-8")
    ).hexdigest()[:24]
    notification_key = "listing-anomaly-review:" + anomaly["review_task_id"]
    recipient_type = os.getenv(
        "DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role"
    ).strip() or "role"
    recipient_ref = os.getenv(
        "DEFAULT_NOTIFICATION_RECIPIENT", "operations"
    ).strip() or "operations"
    channel = os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "").strip().lower() or "unconfigured"
    connection.execute(
        """
        INSERT INTO notification_outbox(
            notification_id, notification_key, notification_type,
            related_task_id, related_review_task_id, recipient_type,
            recipient_ref, channel, priority, payload_json, status,
            attempt_count, max_attempts, next_attempt_at, deadline_at,
            lease_owner_token, lease_version, lease_expires_at, sent_at,
            provider_message_id, last_error_code, last_error_message,
            created_at, updated_at
        ) VALUES (?, ?, 'mobile_review_required', NULL, ?, ?, ?, ?, 80, ?,
                  'PENDING', 0, 3, NULL, ?, '', 0, NULL, NULL, '', '', '',
                  ?, ?)
        """,
        (
            notification_id,
            notification_key,
            anomaly["review_task_id"],
            recipient_type,
            recipient_ref,
            channel,
            _json_text(
                {
                    "review_task_id": anomaly["review_task_id"],
                    "review_type": LISTING_ANOMALY_REVIEW_TYPE,
                    "reason": anomaly["diagnostic_message"],
                    "message": "平台商品页面位置异常，需要人工复核。",
                    "scope_type": "platform_listing",
                    "scope_key": anomaly["anomaly_subject_key"],
                    "snapshot_id": snapshot["snapshot_id"],
                }
            ),
            (now + timedelta(hours=24)).isoformat(),
            now.isoformat(),
            now.isoformat(),
        ),
    )
    connection.execute(
        """
        INSERT INTO notification_logs(
            notification_id, related_task_id, related_review_task_id,
            recipient_type, recipient, channel, sent_at, send_status,
            dedupe_key, message, error_message, created_at
        ) VALUES (?, NULL, ?, ?, ?, ?, NULL, 'pending', ?, ?, '', ?)
        """,
        (
            notification_id,
            anomaly["review_task_id"],
            recipient_type,
            recipient_ref,
            channel,
            notification_key,
            anomaly["diagnostic_message"],
            now.isoformat(),
        ),
    )


def _review_payload(
    anomaly: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_item_id": anomaly["snapshot_item_id"],
        "reason_code": anomaly["reason_code"],
        "blocked_actions": list(LISTING_ANOMALY_BLOCKED_ACTIONS),
        "resolution_policy": AUTO_CLEAR_POLICY,
        "page_identity_key": anomaly["page_identity_key"],
        "affected_internal_skus": anomaly["affected_internal_skus"],
    }


def _assert_no_unimported_listing_write(connection: Any, batch_id: str) -> None:
    active = connection.execute(
        """
        SELECT batch_id FROM shadowbot_listing_action_batches
        WHERE batch_id <> ?
          AND action_type IN ('set_online', 'set_offline')
          AND status IN ('PUBLISHING', 'QUEUED', 'RUNNING')
        LIMIT 1
        """,
        (batch_id,),
    ).fetchone()
    if active is not None:
        raise ValidationError(
            "SYNC_STATUS 不得越过尚未导入的上下架写操作："
            + str(active["batch_id"])
        )


def _assert_queue_ready_for_publication(
    runner: ShadowBotFileQueueRunner,
) -> None:
    active: list[Path] = []
    for directory, pattern in (
        ("inbox", "*.ready.json"),
        ("working", "*.request.json"),
        ("results", "*.result.json"),
    ):
        path = runner.queue_dir / directory
        if path.exists():
            active.extend(sorted(path.glob(pattern)))
    if active:
        raise ValidationError(
            "上一请求尚未完成数据库导入和归档，拒绝发布新的 SYNC_STATUS："
            + str(active[0])
        )


def _assert_snapshot_not_stale(connection: Any, snapshot: dict[str, Any]) -> None:
    scan_started = _parse_aware(snapshot["scan_started_at"])
    for item in snapshot["items"]:
        internal_sku = str(item.get("internal_sku") or "")
        if not internal_sku:
            continue
        rows = connection.execute(
            """
            SELECT last_listing_change_at FROM listing_status
            WHERE platform_name = ? AND internal_sku = ?
            """,
            (snapshot["platform_name"], internal_sku),
        ).fetchall()
        for row in rows:
            value = str(row["last_listing_change_at"] or "")
            if value and _parse_aware(value) > scan_started:
                raise ValidationError(
                    f"LISTING_SYNC_SNAPSHOT_STALE：{internal_sku}"
                )


def _validate_snapshot_result_binding(
    request: dict[str, Any],
    result: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    expected = {
        "platform_name": request["platform_name"],
        "execution_attempt_id": request["execution_attempt_id"],
        "mapping_source_version": request["mapping_source_version"],
        "instruction_hash": request["instruction_hash"],
        "result_id": result.get("result_id"),
    }
    for field, value in expected.items():
        if str(snapshot.get(field) or "") != str(value or ""):
            raise ValidationError(f"SYNC_STATUS snapshot {field} 绑定不一致。")


def _existing_import_summary(
    repository: SQLiteRuntimeRepository,
    batch_id: str,
    result_id: str,
) -> dict[str, Any]:
    with closing(repository.connect_read()) as connection:
        snapshot = connection.execute(
            """
            SELECT snapshot_id, status FROM listing_sync_snapshots
            WHERE batch_id = ? AND result_id = ?
            """,
            (batch_id, result_id),
        ).fetchone()
    return {
        "batch_id": batch_id,
        "result_id": result_id,
        "snapshot_id": str(snapshot["snapshot_id"]) if snapshot else "",
        "status": str(snapshot["status"]) if snapshot else "UNKNOWN",
        "projected_count": 0,
        "anomaly_count": 0,
        "review_created_count": 0,
        "review_cleared_count": 0,
        "notification_created_count": 0,
        "notification_cancelled_count": 0,
        "items": [],
        "already_imported": True,
    }


def _anomaly_subject(
    platform_name: str,
    item: dict[str, Any],
    reason: str,
) -> str:
    if reason == "IDENTITY_MAPPING_CONFLICT":
        return "skus:" + ",".join(item.get("affected_internal_skus") or [])
    if reason == "DUPLICATE_PAGE_IDENTITY":
        return f"{item['snapshot_item_id']}|{item['page_identity_key']}"
    if item.get("internal_sku"):
        return f"{platform_name}|sku:{item['internal_sku']}"
    return f"{platform_name}|page:{item['page_identity_key']}"


def _anomaly_message(item: dict[str, Any], reason: str) -> str:
    name_grade = f"{item['product_name']} {item['grade']}".strip()
    messages = {
        "UNMAPPED_PRODUCT": f"页面商品未映射到库存 SKU：{name_grade}",
        "IDENTITY_MAPPING_CONFLICT": f"页面身份对应多个库存 SKU：{name_grade}",
        "ABSENT_FROM_BOTH_LISTS": f"商品在上架中和待上架两页均不存在：{name_grade}",
        "DUPLICATE_PAGE_IDENTITY": f"商品页面身份不唯一：{name_grade}",
        "PRESENT_IN_BOTH_LISTS": f"商品同时出现在上架中和待上架：{name_grade}",
    }
    return messages[reason]


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValidationError("时间必须包含时区。")
    return parsed.astimezone(UTC)


def _is_hex_sha256(value: str) -> bool:
    return len(str(value)) == 64 and all(
        character in "0123456789abcdef" for character in str(value).lower()
    )


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _now_text() -> str:
    return datetime.now(UTC).isoformat()
