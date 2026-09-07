"""One-shot price decisions reuse Task/history; execution evidence stays immutable."""

from __future__ import annotations

import json
from datetime import datetime


def record_price_supersession(connection, tasks, *, subject: str, now: datetime) -> None:
    for task in tasks:
        if task.action_type.value != 'update_price':
            continue
        predecessors = []
        rows = connection.execute(
            """SELECT * FROM tasks WHERE internal_sku = ? AND platform_name = ?
               AND task_status IN ('pending', 'running', 'manual_review')
               AND action_type IN ('update_price', 'set_online', 'set_offline')""",
            (task.internal_sku, task.platform_name),
        ).fetchall()
        for old in rows:
            # Pending alone does not prove that no publisher has crossed its boundary.
            published = connection.execute(
                """SELECT 1 FROM shadowbot_commit_batch_items i
                   JOIN shadowbot_commit_batches b ON b.batch_id = i.batch_id
                   WHERE i.source_task_id = ? AND b.status <> 'PREPARED'
                   UNION ALL
                   SELECT 1 FROM shadowbot_operations
                   WHERE task_id = ? AND status NOT IN ('PENDING', 'START_FAILED')""",
                (old['task_id'], old['task_id']),
            ).fetchone()
            replaceable = (
                old['task_status'] == 'pending' and old['action_type'] == 'update_price'
                and old['origin_type'] == 'MANUAL'
                and str(old['origin_ref_id']).startswith('web-manual:')
                and published is None
            )
            if not replaceable:
                predecessors.append(old['task_id'])
                continue
            connection.execute(
                "UPDATE tasks SET task_status = 'cancelled', updated_at = ? WHERE task_id = ?",
                (now.isoformat(), old['task_id']),
            )
            close_price_authorizations(connection, old['task_id'], now=now)
            connection.execute(
                """INSERT INTO task_status_history VALUES
                   (?, ?, 'pending', 'cancelled', ?, ?, 'price_decision_superseded', ?)""",
                ('SUPERSEDE-' + old['task_id'] + '-' + task.task_id,
                 old['task_id'], subject, now.isoformat(),
                 json.dumps({'superseded_by': task.task_id}, ensure_ascii=False)),
            )
        task.decision_trace['price_decision_version'] = 1
        task.decision_trace['predecessor_task_ids'] = predecessors


def close_price_authorizations(connection, task_id: str, *, now: datetime) -> None:
    # A multi-item authorization is indivisible. Its other pending decisions
    # return to human confirmation; never silently drop items from its scope.
    connection.execute(
        """UPDATE execution_continuations SET closed_at = ?, outcome = CASE WHEN
             (SELECT COUNT(*) FROM shadowbot_commit_batch_items i
              WHERE i.batch_id = execution_continuations.batch_id) = 1
             THEN 'SUPERSEDED' ELSE 'RECONFIRM' END,
           message = '批次内有价格决定取消或被替代；仍待执行的决定请重新预览确认。'
           WHERE closed_at IS NULL AND batch_id IN
             (SELECT batch_id FROM shadowbot_commit_batch_items WHERE source_task_id = ?)""",
        (now.isoformat(), task_id),
    )


def unresolved_predecessors(connection, task_id: str) -> bool:
    row = connection.execute('SELECT decision_trace_json FROM tasks WHERE task_id = ?',
                             (task_id,)).fetchone()
    trace = json.loads(row['decision_trace_json']) if row else {}
    for predecessor in trace.get('predecessor_task_ids', []):
        old = connection.execute('SELECT task_status FROM tasks WHERE task_id = ?',
                                 (predecessor,)).fetchone()
        if old is None or old['task_status'] in {'pending', 'running', 'manual_review'}:
            return True
        if connection.execute(
            """SELECT 1 FROM shadowbot_operations WHERE task_id = ?
               AND status IN ('RUNNING', 'NEEDS_RECONCILIATION', 'MANUAL_REVIEW')""",
            (predecessor,),
        ).fetchone():
            return True
    return False
