from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import uuid4

from app.automation_models import (
    AutomationJob,
    AutomationRun,
    AutomationRunClaim,
    AutomationRunEvent,
    AutomationRunLink,
    AutomationRunOutcome,
)
from app.enums import AutomationRunStatus, SellerPhase
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.operational_time import OperationalTimeContext


CLAIMABLE_STATUSES = (
    AutomationRunStatus.SCHEDULED,
    AutomationRunStatus.RUNNING,
)


class AutomationRepository:
    """Transactional automation ledger with fenced run leases."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository

    def upsert_job(self, job: AutomationJob, *, now: datetime) -> AutomationJob:
        current = _as_utc(now, "now")
        _validate_job(job)
        config_json = _json_dump(job.config)
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT job_type, schedule_kind,
                           schedule_expression, config_json, created_at
                    FROM automation_jobs
                    WHERE job_id = ?
                    """,
                    (job.job_id,),
                ).fetchone()
                created_at = (
                    _required_datetime(existing["created_at"])
                    if existing is not None
                    else current
                )
                if (
                    existing is not None
                    and str(existing["job_type"]) != job.job_type
                ):
                    raise ValueError(
                        "An existing automation job_id cannot change job_type"
                    )
                if existing is not None and (
                    str(existing["schedule_kind"]) != job.schedule_kind
                    or str(existing["schedule_expression"])
                    != job.schedule_expression
                ):
                    raise ValueError(
                        "An existing automation job_id cannot change schedule"
                    )
                if existing is not None:
                    existing_config = _json_load(existing["config_json"])
                    if str(
                        existing_config.get("platform_name") or ""
                    ) != str(job.config.get("platform_name") or ""):
                        raise ValueError(
                            "An existing automation job_id cannot change "
                            "platform_name"
                        )
                connection.execute(
                    """
                    INSERT INTO automation_jobs(
                        job_id, job_type, display_name, enabled,
                        schedule_kind, schedule_expression, priority,
                        config_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        job_type = excluded.job_type,
                        display_name = excluded.display_name,
                        enabled = excluded.enabled,
                        schedule_kind = excluded.schedule_kind,
                        schedule_expression = excluded.schedule_expression,
                        priority = excluded.priority,
                        config_json = excluded.config_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        job.job_id,
                        job.job_type,
                        job.display_name,
                        int(job.enabled),
                        job.schedule_kind,
                        job.schedule_expression,
                        job.priority,
                        config_json,
                        _datetime_text(created_at),
                        _datetime_text(current),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        stored = self.get_job(job.job_id)
        if stored is None:
            raise RuntimeError("Automation job was not persisted")
        return stored

    def get_job(self, job_id: str) -> AutomationJob | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM automation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def list_jobs(self, *, enabled_only: bool = False) -> list[AutomationJob]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM automation_jobs
                {where}
                ORDER BY priority ASC, job_id ASC
                """
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def latest_scheduled_for(self, job_id: str) -> datetime | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT MAX(scheduled_for) AS scheduled_for
                FROM automation_runs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return _text_to_datetime(row["scheduled_for"]) if row else None

    def ensure_run(
        self,
        *,
        job: AutomationJob,
        scheduled_for: datetime,
        time_context: OperationalTimeContext,
        initial_status: AutomationRunStatus,
        now: datetime,
        logical_run_key: str | None = None,
        event_type: str = "RUN_SCHEDULED",
        event_payload: dict[str, object] | None = None,
    ) -> tuple[AutomationRun, bool]:
        scheduled_utc = _as_utc(scheduled_for, "scheduled_for")
        current = _as_utc(now, "now")
        logical_key = logical_run_key or _logical_run_key(
            job.job_id,
            scheduled_utc,
        )
        run_id = _run_id(logical_key)
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM automation_runs
                    WHERE logical_run_key = ?
                    """,
                    (logical_key,),
                ).fetchone()
                if existing is not None:
                    _validate_existing_run(
                        existing,
                        job=job,
                        scheduled_for=scheduled_utc,
                        time_context=time_context,
                    )
                    connection.commit()
                    return _row_to_run(existing), False
                connection.execute(
                    """
                    INSERT INTO automation_runs(
                        run_id, job_id, job_type, logical_run_key,
                        run_status, platform_name,
                        platform_trade_date, seller_operation_date,
                        seller_phase, time_policy_version,
                        scheduled_for, started_at, finished_at,
                        lease_owner, lease_version, lease_expires_at,
                        input_manifest_sha256, output_manifest_sha256,
                        error_code, error_message, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, '', 0,
                        NULL, '', '', ?, ?, ?, ?
                    )
                    """,
                    (
                        run_id,
                        job.job_id,
                        job.job_type,
                        logical_key,
                        initial_status.value,
                        str(job.config.get("platform_name") or ""),
                        time_context.platform_trade_date.isoformat(),
                        time_context.seller_operation_date.isoformat(),
                        time_context.seller_phase.value,
                        time_context.time_policy_version,
                        _datetime_text(scheduled_utc),
                        (
                            _datetime_text(current)
                            if initial_status
                            in {
                                AutomationRunStatus.MISSED,
                                AutomationRunStatus.SKIPPED,
                                AutomationRunStatus.CANCELLED,
                            }
                            else None
                        ),
                        (
                            str(event_payload.get("error_code") or "")
                            if event_payload
                            else ""
                        ),
                        (
                            str(event_payload.get("error_message") or "")
                            if event_payload
                            else ""
                        ),
                        _datetime_text(current),
                        _datetime_text(current),
                    ),
                )
                _insert_event(
                    connection,
                    run_id=run_id,
                    event_type=event_type,
                    from_status=None,
                    to_status=initial_status,
                    payload=event_payload or {},
                    created_at=current,
                )
                created = connection.execute(
                    "SELECT * FROM automation_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if created is None:
            raise RuntimeError("Automation run was not persisted")
        return _row_to_run(created), True

    def get_run(self, run_id: str) -> AutomationRun | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM automation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _row_to_run(row) if row is not None else None

    def list_runs(
        self,
        *,
        job_id: str | None = None,
        statuses: Iterable[AutomationRunStatus] | None = None,
        limit: int | None = None,
    ) -> list[AutomationRun]:
        clauses: list[str] = []
        values: list[object] = []
        if job_id:
            clauses.append("job_id = ?")
            values.append(job_id)
        status_values = tuple(statuses or ())
        if status_values:
            placeholders = ",".join("?" for _ in status_values)
            clauses.append(f"run_status IN ({placeholders})")
            values.extend(status.value for status in status_values)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        limit_sql = ""
        if limit is not None:
            if limit < 1:
                return []
            limit_sql = "LIMIT ?"
            values.append(limit)
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM automation_runs
                {where}
                ORDER BY scheduled_for DESC, run_id DESC
                {limit_sql}
                """,
                tuple(values),
            ).fetchall()
        return [_row_to_run(row) for row in rows]

    def append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        now: datetime,
        payload: dict[str, object] | None = None,
    ) -> None:
        current = _as_utc(now, "now")
        with closing(
            self.runtime_repository.connect_write()
        ) as connection, connection:
            row = connection.execute(
                "SELECT run_status FROM automation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Automation run does not exist")
            status = AutomationRunStatus(str(row["run_status"]))
            _insert_event(
                connection,
                run_id=run_id,
                event_type=event_type,
                from_status=status,
                to_status=status,
                payload=payload or {},
                created_at=current,
            )

    def mark_merged(
        self,
        *,
        run_id: str,
        target_run_id: str,
        now: datetime,
        reason: str,
    ) -> bool:
        current = _as_utc(now, "now")
        if run_id == target_run_id:
            raise ValueError("A run cannot merge into itself")
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                source = connection.execute(
                    "SELECT run_status FROM automation_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                target = connection.execute(
                    "SELECT run_status FROM automation_runs WHERE run_id = ?",
                    (target_run_id,),
                ).fetchone()
                if source is None or target is None:
                    raise ValueError("Both automation runs must exist")
                source_status = AutomationRunStatus(
                    str(source["run_status"])
                )
                if source_status is AutomationRunStatus.MERGED:
                    existing_link = connection.execute(
                        """
                        SELECT 1
                        FROM automation_run_links
                        WHERE parent_run_id = ?
                          AND child_run_id = ?
                          AND relation_type = 'MERGED_RUN'
                        """,
                        (target_run_id, run_id),
                    ).fetchone()
                    connection.commit()
                    return existing_link is not None
                if source_status is not AutomationRunStatus.SCHEDULED:
                    connection.rollback()
                    return False
                cursor = connection.execute(
                    """
                    UPDATE automation_runs
                    SET run_status = 'MERGED',
                        finished_at = ?,
                        error_code = 'MERGED_INTO_HIGHER_PRIORITY_RUN',
                        error_message = ?,
                        updated_at = ?
                    WHERE run_id = ? AND run_status = 'SCHEDULED'
                    """,
                    (
                        _datetime_text(current),
                        reason,
                        _datetime_text(current),
                        run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                _insert_link(
                    connection,
                    parent_run_id=target_run_id,
                    child_run_id=run_id,
                    relation_type="MERGED_RUN",
                    created_at=current,
                )
                _insert_event(
                    connection,
                    run_id=run_id,
                    event_type="RUN_MERGED",
                    from_status=AutomationRunStatus.SCHEDULED,
                    to_status=AutomationRunStatus.MERGED,
                    payload={
                        "target_run_id": target_run_id,
                        "reason": reason,
                    },
                    created_at=current,
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def ensure_link(
        self,
        *,
        parent_run_id: str,
        child_run_id: str,
        relation_type: str,
        now: datetime,
    ) -> AutomationRunLink:
        current = _as_utc(now, "now")
        if parent_run_id == child_run_id:
            raise ValueError("A run cannot be linked to itself")
        normalized_relation = relation_type.strip().upper()
        if not normalized_relation:
            raise ValueError("relation_type must not be blank")
        with closing(
            self.runtime_repository.connect_write()
        ) as connection, connection:
            _insert_link(
                connection,
                parent_run_id=parent_run_id,
                child_run_id=child_run_id,
                relation_type=normalized_relation,
                created_at=current,
            )
            row = connection.execute(
                """
                SELECT *
                FROM automation_run_links
                WHERE parent_run_id = ?
                  AND child_run_id = ?
                  AND relation_type = ?
                """,
                (parent_run_id, child_run_id, normalized_relation),
            ).fetchone()
        if row is None:
            raise RuntimeError("Automation run link was not persisted")
        return _row_to_link(row)

    def list_events(self, run_id: str) -> list[AutomationRunEvent]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM automation_run_events
                WHERE run_id = ?
                ORDER BY created_at ASC, event_id ASC
                """,
                (run_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_links(
        self,
        *,
        parent_run_id: str | None = None,
        child_run_id: str | None = None,
    ) -> list[AutomationRunLink]:
        if not parent_run_id and not child_run_id:
            raise ValueError("A parent or child run id is required")
        clauses: list[str] = []
        values: list[object] = []
        if parent_run_id:
            clauses.append("parent_run_id = ?")
            values.append(parent_run_id)
        if child_run_id:
            clauses.append("child_run_id = ?")
            values.append(child_run_id)
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM automation_run_links
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at ASC, relation_type ASC
                """,
                tuple(values),
            ).fetchall()
        return [_row_to_link(row) for row in rows]

    def claim_next(
        self,
        *,
        owner_token: str,
        now: datetime,
        lease_seconds: int,
        allowed_job_types: Iterable[str],
    ) -> AutomationRunClaim | None:
        current = _as_utc(now, "now")
        owner = owner_token.strip()
        allowed = tuple(
            dict.fromkeys(
                job_type.strip()
                for job_type in allowed_job_types
                if job_type.strip()
            )
        )
        if not owner:
            raise ValueError("owner_token must not be blank")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if not allowed:
            return None
        placeholders = ",".join("?" for _ in allowed)
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    f"""
                    SELECT runs.run_id
                    FROM automation_runs AS runs
                    INNER JOIN automation_jobs AS jobs
                        ON jobs.job_id = runs.job_id
                    WHERE runs.job_type IN ({placeholders})
                      AND (
                          (
                              runs.run_status = 'SCHEDULED'
                              AND julianday(runs.scheduled_for)
                                  <= julianday(?)
                          )
                          OR (
                              runs.run_status = 'RUNNING'
                              AND (
                                  runs.lease_expires_at IS NULL
                                  OR julianday(runs.lease_expires_at)
                                      <= julianday(?)
                              )
                          )
                      )
                    ORDER BY
                        CASE
                            WHEN runs.run_status = 'RUNNING' THEN 0
                            ELSE 1
                        END ASC,
                        jobs.priority ASC,
                        runs.scheduled_for ASC,
                        runs.run_id ASC
                    LIMIT 1
                    """,
                    (*allowed, _datetime_text(current), _datetime_text(current)),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                claim = _claim_run(
                    connection,
                    run_id=str(row["run_id"]),
                    owner_token=owner,
                    now=current,
                    lease_seconds=lease_seconds,
                )
                connection.commit()
                return claim
            except Exception:
                connection.rollback()
                raise

    def claim_run(
        self,
        *,
        run_id: str,
        owner_token: str,
        now: datetime,
        lease_seconds: int,
    ) -> AutomationRunClaim | None:
        current = _as_utc(now, "now")
        owner = owner_token.strip()
        if not owner:
            raise ValueError("owner_token must not be blank")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                claim = _claim_run(
                    connection,
                    run_id=run_id,
                    owner_token=owner,
                    now=current,
                    lease_seconds=lease_seconds,
                )
                connection.commit()
                return claim
            except Exception:
                connection.rollback()
                raise

    def renew_lease(
        self,
        claim: AutomationRunClaim,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> AutomationRunClaim | None:
        current = _as_utc(now, "now")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        expires_at = current + timedelta(seconds=lease_seconds)
        with closing(
            self.runtime_repository.connect_write()
        ) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE automation_runs
                SET lease_expires_at = ?, updated_at = ?
                WHERE run_id = ?
                  AND run_status = 'RUNNING'
                  AND lease_owner = ?
                  AND lease_version = ?
                  AND julianday(lease_expires_at) > julianday(?)
                """,
                (
                    _datetime_text(expires_at),
                    _datetime_text(current),
                    claim.run.run_id,
                    claim.owner_token,
                    claim.lease_version,
                    _datetime_text(current),
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM automation_runs WHERE run_id = ?",
                (claim.run.run_id,),
            ).fetchone()
        if row is None:
            return None
        return AutomationRunClaim(
            run=_row_to_run(row),
            owner_token=claim.owner_token,
            lease_version=claim.lease_version,
            lease_expires_at=expires_at,
            reclaimed=claim.reclaimed,
        )

    def finish_run(
        self,
        claim: AutomationRunClaim,
        outcome: AutomationRunOutcome,
        *,
        now: datetime,
    ) -> bool:
        current = _as_utc(now, "now")
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE automation_runs
                    SET run_status = ?,
                        finished_at = ?,
                        lease_owner = '',
                        lease_expires_at = NULL,
                        output_manifest_sha256 = ?,
                        error_code = ?,
                        error_message = ?,
                        updated_at = ?
                    WHERE run_id = ?
                      AND run_status = 'RUNNING'
                      AND lease_owner = ?
                      AND lease_version = ?
                      AND julianday(lease_expires_at) > julianday(?)
                    """,
                    (
                        outcome.status.value,
                        _datetime_text(current),
                        outcome.output_manifest_sha256,
                        outcome.error_code,
                        outcome.error_message[:2000],
                        _datetime_text(current),
                        claim.run.run_id,
                        claim.owner_token,
                        claim.lease_version,
                        _datetime_text(current),
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                _insert_event(
                    connection,
                    run_id=claim.run.run_id,
                    event_type="RUN_FINISHED",
                    from_status=AutomationRunStatus.RUNNING,
                    to_status=outcome.status,
                    payload={
                        **outcome.event_payload,
                        "error_code": outcome.error_code,
                    },
                    created_at=current,
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def active_ui_blocker(self) -> str:
        """Return the highest existing write-side blocker for read scans."""

        with closing(self.runtime_repository.connect_read()) as connection:
            unknown = connection.execute(
                """
                SELECT 1
                FROM shadowbot_operations AS operations
                LEFT JOIN shadowbot_write_locks AS locks
                    ON locks.operation_id = operations.operation_id
                WHERE operations.status = 'NEEDS_RECONCILIATION'
                   OR locks.status = 'UNKNOWN'
                LIMIT 1
                """
            ).fetchone()
            if unknown is not None:
                return "UNKNOWN_OR_RECONCILE_ACTIVE"
            active_write = connection.execute(
                """
                SELECT 1
                FROM shadowbot_write_locks
                WHERE status = 'ACTIVE'
                LIMIT 1
                """
            ).fetchone()
            if active_write is not None:
                return "AUTHORIZED_WRITE_ACTIVE"
        return ""

    def health_snapshot(self, *, now: datetime) -> dict[str, object]:
        current = _as_utc(now, "now")
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT run_status, COUNT(*) AS run_count
                FROM automation_runs
                GROUP BY run_status
                """
            ).fetchall()
            expired = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM automation_runs
                    WHERE run_status = 'RUNNING'
                      AND (
                          lease_expires_at IS NULL
                          OR julianday(lease_expires_at) <= julianday(?)
                      )
                    """,
                    (_datetime_text(current),),
                ).fetchone()[0]
            )
        counts = {str(row["run_status"]): int(row["run_count"]) for row in rows}
        return {
            "status": "DEGRADED" if expired else "HEALTHY",
            "run_counts": counts,
            "expired_running_count": expired,
            "observed_at": _datetime_text(current),
        }


def _claim_run(
    connection,
    *,
    run_id: str,
    owner_token: str,
    now: datetime,
    lease_seconds: int,
) -> AutomationRunClaim | None:
    before = connection.execute(
        "SELECT * FROM automation_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if before is None:
        return None
    before_status = AutomationRunStatus(str(before["run_status"]))
    if before_status not in CLAIMABLE_STATUSES:
        return None
    if before_status is AutomationRunStatus.SCHEDULED:
        if _required_datetime(before["scheduled_for"]) > now:
            return None
        claim_condition = "run_status = 'SCHEDULED'"
        condition_values: tuple[object, ...] = ()
        reclaimed = False
    else:
        previous_expiry = _text_to_datetime(before["lease_expires_at"])
        if previous_expiry is not None and previous_expiry > now:
            return None
        claim_condition = (
            "run_status = 'RUNNING' AND "
            "(lease_expires_at IS NULL "
            "OR julianday(lease_expires_at) <= julianday(?))"
        )
        condition_values = (_datetime_text(now),)
        reclaimed = True
    expires_at = now + timedelta(seconds=lease_seconds)
    cursor = connection.execute(
        f"""
        UPDATE automation_runs
        SET run_status = 'RUNNING',
            started_at = COALESCE(started_at, ?),
            finished_at = NULL,
            lease_owner = ?,
            lease_version = lease_version + 1,
            lease_expires_at = ?,
            error_code = '',
            error_message = '',
            updated_at = ?
        WHERE run_id = ?
          AND {claim_condition}
        """,
        (
            _datetime_text(now),
            owner_token,
            _datetime_text(expires_at),
            _datetime_text(now),
            run_id,
            *condition_values,
        ),
    )
    if cursor.rowcount != 1:
        return None
    claimed_row = connection.execute(
        "SELECT * FROM automation_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if claimed_row is None:
        raise RuntimeError("Claimed automation run disappeared")
    claimed_run = _row_to_run(claimed_row)
    event_type = "LEASE_RECLAIMED" if reclaimed else "RUN_STARTED"
    _insert_event(
        connection,
        run_id=run_id,
        event_type=event_type,
        from_status=before_status,
        to_status=AutomationRunStatus.RUNNING,
        payload={
            "lease_version": claimed_run.lease_version,
            "lease_expires_at": _datetime_text(expires_at),
        },
        created_at=now,
    )
    return AutomationRunClaim(
        run=claimed_run,
        owner_token=owner_token,
        lease_version=claimed_run.lease_version,
        lease_expires_at=expires_at,
        reclaimed=reclaimed,
    )


def _insert_event(
    connection,
    *,
    run_id: str,
    event_type: str,
    from_status: AutomationRunStatus | None,
    to_status: AutomationRunStatus | None,
    payload: dict[str, object],
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO automation_run_events(
            event_id, run_id, event_type,
            from_status, to_status, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"AUTO-EVENT-{uuid4().hex}",
            run_id,
            event_type,
            from_status.value if from_status else None,
            to_status.value if to_status else None,
            _json_dump(payload),
            _datetime_text(created_at),
        ),
    )


def _insert_link(
    connection,
    *,
    parent_run_id: str,
    child_run_id: str,
    relation_type: str,
    created_at: datetime,
) -> None:
    parent = connection.execute(
        "SELECT 1 FROM automation_runs WHERE run_id = ?",
        (parent_run_id,),
    ).fetchone()
    child = connection.execute(
        "SELECT 1 FROM automation_runs WHERE run_id = ?",
        (child_run_id,),
    ).fetchone()
    if parent is None or child is None:
        raise ValueError("Both linked automation runs must exist")
    connection.execute(
        """
        INSERT OR IGNORE INTO automation_run_links(
            parent_run_id, child_run_id, relation_type, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            parent_run_id,
            child_run_id,
            relation_type,
            _datetime_text(created_at),
        ),
    )


def _validate_job(job: AutomationJob) -> None:
    for field_name, value in (
        ("job_id", job.job_id),
        ("job_type", job.job_type),
        ("display_name", job.display_name),
        ("schedule_kind", job.schedule_kind),
        ("schedule_expression", job.schedule_expression),
    ):
        if not value.strip():
            raise ValueError(f"{field_name} must not be blank")
    if not isinstance(job.config, dict):
        raise ValueError("Automation job config must be an object")
    if not str(job.config.get("platform_name") or "").strip():
        raise ValueError("Automation job config.platform_name is required")


def _validate_existing_run(
    row,
    *,
    job: AutomationJob,
    scheduled_for: datetime,
    time_context: OperationalTimeContext,
) -> None:
    expected = {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "platform_name": str(job.config.get("platform_name") or ""),
        "platform_trade_date": time_context.platform_trade_date.isoformat(),
        "seller_operation_date": (
            time_context.seller_operation_date.isoformat()
        ),
        "seller_phase": time_context.seller_phase.value,
        "time_policy_version": time_context.time_policy_version,
        "scheduled_for": _datetime_text(scheduled_for),
    }
    for field_name, expected_value in expected.items():
        if str(row[field_name]) != str(expected_value):
            raise ValueError(
                "Automation logical_run_key already exists with different "
                f"{field_name}"
            )


def _logical_run_key(job_id: str, scheduled_for: datetime) -> str:
    return f"{job_id}:{_datetime_text(scheduled_for)}"


def _run_id(logical_run_key: str) -> str:
    digest = hashlib.sha256(logical_run_key.encode("utf-8")).hexdigest()
    return f"AUTO-RUN-{digest[:32]}"


def _row_to_job(row) -> AutomationJob:
    return AutomationJob(
        job_id=str(row["job_id"]),
        job_type=str(row["job_type"]),
        display_name=str(row["display_name"]),
        enabled=bool(row["enabled"]),
        schedule_kind=str(row["schedule_kind"]),
        schedule_expression=str(row["schedule_expression"]),
        priority=int(row["priority"]),
        config=_json_load(row["config_json"]),
        created_at=_text_to_datetime(row["created_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
    )


def _row_to_run(row) -> AutomationRun:
    return AutomationRun(
        run_id=str(row["run_id"]),
        job_id=str(row["job_id"]),
        job_type=str(row["job_type"]),
        logical_run_key=str(row["logical_run_key"]),
        run_status=AutomationRunStatus(str(row["run_status"])),
        platform_name=str(row["platform_name"]),
        platform_trade_date=_text_to_date(row["platform_trade_date"]),
        seller_operation_date=_text_to_date(
            row["seller_operation_date"]
        ),
        seller_phase=SellerPhase(str(row["seller_phase"])),
        time_policy_version=str(row["time_policy_version"]),
        scheduled_for=_required_datetime(row["scheduled_for"]),
        started_at=_text_to_datetime(row["started_at"]),
        finished_at=_text_to_datetime(row["finished_at"]),
        lease_owner=str(row["lease_owner"] or ""),
        lease_version=int(row["lease_version"]),
        lease_expires_at=_text_to_datetime(row["lease_expires_at"]),
        input_manifest_sha256=str(row["input_manifest_sha256"] or ""),
        output_manifest_sha256=str(row["output_manifest_sha256"] or ""),
        error_code=str(row["error_code"] or ""),
        error_message=str(row["error_message"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
    )


def _row_to_event(row) -> AutomationRunEvent:
    from_status = row["from_status"]
    to_status = row["to_status"]
    return AutomationRunEvent(
        event_id=str(row["event_id"]),
        run_id=str(row["run_id"]),
        event_type=str(row["event_type"]),
        from_status=(
            AutomationRunStatus(str(from_status))
            if from_status is not None
            else None
        ),
        to_status=(
            AutomationRunStatus(str(to_status))
            if to_status is not None
            else None
        ),
        payload=_json_load(row["payload_json"]),
        created_at=_required_datetime(row["created_at"]),
    )


def _row_to_link(row) -> AutomationRunLink:
    return AutomationRunLink(
        parent_run_id=str(row["parent_run_id"]),
        child_run_id=str(row["child_run_id"]),
        relation_type=str(row["relation_type"]),
        created_at=_required_datetime(row["created_at"]),
    )


def _json_dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_load(value: object) -> dict[str, object]:
    parsed = json.loads(str(value or "{}"))
    if not isinstance(parsed, dict):
        raise ValueError("Stored automation JSON must be an object")
    return parsed


def _datetime_text(value: datetime) -> str:
    return _as_utc(value, "datetime").isoformat()


def _text_to_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value))
    return _as_utc(parsed, "stored datetime")


def _required_datetime(value: object) -> datetime:
    parsed = _text_to_datetime(value)
    if parsed is None:
        raise ValueError("Required automation datetime is missing")
    return parsed


def _text_to_date(value: object):
    from datetime import date

    return date.fromisoformat(str(value))


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
