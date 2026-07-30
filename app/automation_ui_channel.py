from __future__ import annotations

from datetime import datetime, timezone


UI_AUTOMATION_JOB_TYPES = frozenset(
    {
        "ONLINE_PULSE",
        "FULL_MARKET_SCAN",
        "LISTING_STATUS_SCAN",
        "ORDER_SCAN",
        "PRE_CUTOFF_FULL_SCAN",
        "POST_CUTOFF_PULSE",
    }
)


def has_active_automation_ui_run(
    connection,
    *,
    now: datetime,
    exclude_run_id: str = "",
) -> bool:
    """Return whether another live Automation lease owns the shared UI."""

    current = _as_utc(now)
    job_types = tuple(sorted(UI_AUTOMATION_JOB_TYPES))
    placeholders = ",".join("?" for _ in job_types)
    row = connection.execute(
        f"""
        SELECT 1
        FROM automation_runs
        WHERE job_type IN ({placeholders})
          AND run_status = 'RUNNING'
          AND julianday(lease_expires_at) > julianday(?)
          AND run_id <> ?
        LIMIT 1
        """,
        (*job_types, current.isoformat(), exclude_run_id),
    ).fetchone()
    return row is not None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)
