from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


LISTING_STATUS_SCAN = "LISTING_STATUS_SCAN"


@dataclass(frozen=True, slots=True)
class ListingScanQuality:
    accepted: bool
    reason: str
    run_id: str = ""
    observation_batch_id: str = ""
    source_snapshot_id: str = ""
    scan_completed_at: datetime | None = None


def latest_listing_scan_quality(
    connection,
    *,
    platform_name: str,
    internal_sku: str,
    current_status: str,
    listing_source_id: str,
    now: datetime,
    max_age: timedelta,
) -> ListingScanQuality:
    """Validate the last due scheduled listing scan without scheduling a new one."""

    current = _as_utc(now)
    run = connection.execute(
        """
        SELECT run_id, run_status
        FROM automation_runs
        WHERE job_type = ? AND platform_name = ? AND scheduled_for <= ?
        ORDER BY scheduled_for DESC, created_at DESC, run_id DESC
        LIMIT 1
        """,
        (LISTING_STATUS_SCAN, platform_name, current.isoformat()),
    ).fetchone()
    if run is None:
        return _rejected("尚无可用的定时商品扫描，请等待下一次定时扫描。")

    run_id = str(run["run_id"])
    if str(run["run_status"]) != "SUCCESS":
        return _rejected(
            "最近一次定时商品扫描未完整成功，请等待下一次定时扫描。",
            run_id=run_id,
        )

    batches = connection.execute(
        """
        SELECT observation_batch_id, batch_status, scan_completed_at,
               requested_scope_json, scope_complete, end_marker_verified
        FROM product_observation_batches
        WHERE automation_run_id = ? AND platform_name = ? AND scan_type = ?
        ORDER BY scan_completed_at DESC, observation_batch_id DESC
        """,
        (run_id, platform_name, LISTING_STATUS_SCAN),
    ).fetchall()
    if len(batches) != 1:
        return _rejected(
            "最近一次定时商品扫描的数据批次不完整，请等待下一次定时扫描。",
            run_id=run_id,
        )
    batch = batches[0]
    batch_id = str(batch["observation_batch_id"])
    if (
        str(batch["batch_status"]) != "ACCEPTED"
        or int(batch["scope_complete"]) != 1
        or int(batch["end_marker_verified"]) != 1
    ):
        return _rejected(
            "最近一次定时商品扫描未完成全部页面或尾部确认，请等待下一次定时扫描。",
            run_id=run_id,
            batch_id=batch_id,
        )

    completed_at = _parse_datetime(batch["scan_completed_at"])
    if completed_at is None or not _is_fresh(completed_at, current, max_age):
        return _rejected(
            "最近一次定时商品扫描已经过期，请等待下一次定时扫描。",
            run_id=run_id,
            batch_id=batch_id,
            completed_at=completed_at,
        )

    try:
        scope = json.loads(str(batch["requested_scope_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        scope = None
    source_snapshot_id = (
        str(scope.get("source_snapshot_id") or "").strip()
        if isinstance(scope, dict)
        else ""
    )
    if not source_snapshot_id:
        return _rejected(
            "最近一次定时商品扫描缺少可核对的页面快照，请等待下一次定时扫描。",
            run_id=run_id,
            batch_id=batch_id,
            completed_at=completed_at,
        )

    items = connection.execute(
        """
        SELECT observed_online, mapping_status
        FROM product_observation_items
        WHERE observation_batch_id = ? AND internal_sku = ?
        ORDER BY observation_item_id
        """,
        (batch_id, internal_sku),
    ).fetchall()
    if len(items) != 1 or str(items[0]["mapping_status"]) != "VERIFIED":
        return _rejected(
            "最近一次定时商品扫描未能确认该商品，请等待下一次定时扫描。",
            run_id=run_id,
            batch_id=batch_id,
            completed_at=completed_at,
            source_snapshot_id=source_snapshot_id,
        )

    normalized_status = str(current_status or "").strip().lower()
    observed_status = "online" if int(items[0]["observed_online"]) == 1 else "offline"
    if (
        source_snapshot_id != str(listing_source_id or "").strip()
        or observed_status != normalized_status
    ):
        return _rejected(
            "当前商品状态不是最近一次合格定时扫描的结果，请等待下一次定时扫描。",
            run_id=run_id,
            batch_id=batch_id,
            completed_at=completed_at,
            source_snapshot_id=source_snapshot_id,
        )

    return ListingScanQuality(
        accepted=True,
        reason="",
        run_id=run_id,
        observation_batch_id=batch_id,
        source_snapshot_id=source_snapshot_id,
        scan_completed_at=completed_at,
    )


def _rejected(
    reason: str,
    *,
    run_id: str = "",
    batch_id: str = "",
    completed_at: datetime | None = None,
    source_snapshot_id: str = "",
) -> ListingScanQuality:
    return ListingScanQuality(
        accepted=False,
        reason=reason,
        run_id=run_id,
        observation_batch_id=batch_id,
        source_snapshot_id=source_snapshot_id,
        scan_completed_at=completed_at,
    )


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_fresh(observed_at: datetime, current: datetime, max_age: timedelta) -> bool:
    return observed_at <= current and current - observed_at <= max_age
