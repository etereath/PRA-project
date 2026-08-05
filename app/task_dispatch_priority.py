from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.exceptions import ValidationError


INCIDENT_REVIEW_TASK_PRIORITY = 0
SYSTEM_EMERGENCY_TASK_PRIORITY = 1
NORMAL_TASK_LANE = 2


def task_dispatch_lane(task: Mapping[str, Any] | Any) -> int:
    """Return the cross-entry dispatch lane for one runtime task.

    Lower values run first.  Only Incident Review actions and the narrowly
    authorized automatic emergency task enter the urgent lanes; ordinary
    MANUAL tasks retain their existing task priority semantics.
    """

    origin_type = _value(task, "origin_type")
    origin_ref_id = _value(task, "origin_ref_id")
    if origin_type == "MANUAL" and origin_ref_id.startswith("incident-review:"):
        return INCIDENT_REVIEW_TASK_PRIORITY
    if origin_type == "SYSTEM_EMERGENCY":
        return SYSTEM_EMERGENCY_TASK_PRIORITY
    return NORMAL_TASK_LANE


def dispatch_sort_key(task: Mapping[str, Any] | Any) -> tuple[object, ...]:
    return (
        task_dispatch_lane(task),
        int(_raw_value(task, "priority") or 0),
        str(_raw_value(task, "created_at") or ""),
        str(_raw_value(task, "task_id") or ""),
    )


def assert_selected_tasks_have_dispatch_priority(
    connection,
    *,
    selected_task_ids: Iterable[str],
    platform_name: str,
) -> None:
    """Prevent explicit task IDs from bypassing an urgent pending task."""

    selected_ids = tuple(dict.fromkeys(str(value) for value in selected_task_ids))
    if not selected_ids:
        raise ValidationError("selected task IDs must not be empty")
    rows = connection.execute(
        """
        SELECT task_id, origin_type, origin_ref_id, priority, created_at
        FROM tasks
        WHERE task_status = 'pending' AND platform_name = ?
        """,
        (platform_name,),
    ).fetchall()
    selected = [row for row in rows if str(row["task_id"]) in selected_ids]
    if len(selected) != len(selected_ids):
        raise ValidationError("selected pending task set changed before dispatch")
    selected_lanes = {task_dispatch_lane(row) for row in selected}
    if len(selected_lanes) != 1:
        raise ValidationError("urgent Incident tasks cannot be mixed with another lane")
    best_pending = min(rows, key=dispatch_sort_key, default=None)
    if best_pending is None:
        return
    selected_best = min(selected, key=dispatch_sort_key)
    if task_dispatch_lane(selected_best) > task_dispatch_lane(best_pending):
        raise ValidationError(
            "higher-priority Incident action must dispatch first: "
            + str(best_pending["task_id"])
        )


def has_pending_urgent_incident_task(connection) -> bool:
    rows = connection.execute(
        """
        SELECT origin_type, origin_ref_id
        FROM tasks
        WHERE task_status = 'pending'
          AND action_type IN ('update_price', 'set_offline')
        """
    ).fetchall()
    return any(task_dispatch_lane(row) < NORMAL_TASK_LANE for row in rows)


def _value(task: Mapping[str, Any] | Any, field: str) -> str:
    value = _raw_value(task, field)
    enum_value = getattr(value, "value", value)
    return str(enum_value or "")


def _raw_value(task: Mapping[str, Any] | Any, field: str) -> Any:
    if isinstance(task, Mapping) or hasattr(task, "keys"):
        try:
            return task[field]
        except (KeyError, IndexError):
            return None
    return getattr(task, field, None)
