from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Iterable
from uuid import uuid4

from app.automation_ui_channel import (
    UI_AUTOMATION_JOB_TYPES,
    has_active_automation_ui_run,
)
from app.automation_models import (
    AutomationJob,
    AutomationRun,
    AutomationRunClaim,
    AutomationRunEvent,
    AutomationRunLink,
    AutomationRunOutcome,
)
from app.enums import AutomationRunStatus, SellerPhase
from app.listing_observation_identity import (
    ListingObservationSourceIdentity,
    listing_observation_source_identities,
    listing_observation_source_identity_sha256,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.operational_time import (
    OperationalTimeContext,
    OperationalTimePolicy,
)


CLAIMABLE_STATUSES = (
    AutomationRunStatus.SCHEDULED,
    AutomationRunStatus.RUNNING,
)
CHILD_RELATIONS = {
    "LISTING_STATUS_SCAN": "LISTING_STATUS_CHILD",
    "ORDER_SCAN": "ORDER_SCAN_CHILD",
}
CHILD_PARENT_JOB_TYPES = frozenset(
    {"FULL_MARKET_SCAN", "PRE_CUTOFF_FULL_SCAN"}
)
CHILD_PARENT_CLAIM_STATUSES = frozenset(
    {
        AutomationRunStatus.SUCCESS.value,
        AutomationRunStatus.PARTIAL.value,
    }
)
COVERAGE_CANDIDATE = "COVERAGE_CANDIDATE"
LISTING_STATUS_SCAN = "LISTING_STATUS_SCAN"


class AutomationLeaseLostError(RuntimeError):
    """Raised when a business write no longer owns its Automation Run."""


def validate_live_automation_claim_in_transaction(
    connection,
    claim: AutomationRunClaim,
    *,
    now: datetime,
) -> None:
    """Fence a business write using the transaction's current DB state."""

    current = _as_utc(now, "now")
    row = connection.execute(
        """
        SELECT 1
        FROM automation_runs
        WHERE run_id = ?
          AND run_status = 'RUNNING'
          AND lease_owner = ?
          AND lease_version = ?
          AND julianday(lease_expires_at) > julianday(?)
        """,
        (
            claim.run.run_id,
            claim.owner_token,
            claim.lease_version,
            _datetime_text(current),
        ),
    ).fetchone()
    if row is None:
        raise AutomationLeaseLostError(
            "Automation Run lease is no longer owned by this handler"
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

    def validate_job_static_identity(self, expected: AutomationJob) -> None:
        """Reject drift in immutable identity while preserving operator fields."""

        existing = self.get_job(expected.job_id)
        if existing is None:
            raise ValueError("Automation job does not exist")
        if existing.job_type != expected.job_type:
            raise ValueError(
                "Existing default automation job has unexpected job_type"
            )
        if (
            existing.schedule_kind != expected.schedule_kind
            or existing.schedule_expression != expected.schedule_expression
        ):
            raise ValueError(
                "Existing default automation job has unexpected schedule"
            )
        if str(existing.config.get("platform_name") or "") != str(
            expected.config.get("platform_name") or ""
        ):
            raise ValueError(
                "Existing default automation job has unexpected platform_name"
            )

    def load_operational_time_policies(
        self,
    ) -> tuple[OperationalTimePolicy, ...]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT policy_version, timezone_name,
                       platform_cutoff_local_time,
                       seller_cutoff_local_time,
                       peak_start_local_time,
                       effective_from, effective_to
                FROM operational_time_policies
                ORDER BY julianday(effective_from), policy_version
                """
            ).fetchall()
        return tuple(
            OperationalTimePolicy(
                policy_version=str(row["policy_version"]),
                timezone_name=str(row["timezone_name"]),
                platform_cutoff_local_time=time.fromisoformat(
                    str(row["platform_cutoff_local_time"])
                ),
                seller_cutoff_local_time=time.fromisoformat(
                    str(row["seller_cutoff_local_time"])
                ),
                peak_start_local_time=time.fromisoformat(
                    str(row["peak_start_local_time"])
                ),
                effective_from=_required_datetime(row["effective_from"]),
                effective_to=_text_to_datetime(row["effective_to"]),
            )
            for row in rows
        )

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

    def reconcile_scheduled_runs_for_job(
        self,
        job: AutomationJob,
        *,
        now: datetime,
    ) -> tuple[str, ...]:
        """Expire stale queued windows using the job's frozen catch-up policy."""

        current = _as_utc(now, "now")
        policy = str(job.config.get("catchup_policy") or "LATEST_ONLY")
        if policy == "LATEST_ONLY":
            keep_count = 1
        elif policy == "IDEMPOTENT":
            keep_count = int(job.config.get("max_catchup_runs") or 1)
            if keep_count < 1:
                raise ValueError("max_catchup_runs must be positive")
        else:
            raise ValueError(f"Unsupported catchup_policy: {policy}")
        max_lateness = job.config.get("max_lateness_seconds")
        max_lateness_seconds = (
            int(max_lateness) if max_lateness is not None else None
        )
        if max_lateness_seconds is not None and max_lateness_seconds < 0:
            raise ValueError("max_lateness_seconds must not be negative")

        missed: list[str] = []
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT runs.run_id, runs.scheduled_for,
                           EXISTS (
                               SELECT 1
                               FROM automation_run_links AS links
                               INNER JOIN automation_runs AS targets
                                   ON targets.run_id = links.parent_run_id
                               INNER JOIN automation_jobs AS target_jobs
                                   ON target_jobs.job_id = targets.job_id
                               WHERE links.child_run_id = runs.run_id
                                 AND links.relation_type = ?
                                 AND targets.run_status
                                     IN ('SCHEDULED', 'RUNNING')
                                  AND (
                                      target_jobs.enabled = 1
                                      OR target_jobs.schedule_kind = 'CHILD_ONLY'
                                  )
                           ) AS coverage_held
                    FROM automation_runs AS runs
                    WHERE runs.job_id = ?
                      AND runs.run_status = 'SCHEDULED'
                    ORDER BY runs.scheduled_for DESC, runs.run_id DESC
                    """,
                    (COVERAGE_CANDIDATE, job.job_id),
                ).fetchall()
                for index, row in enumerate(rows):
                    if bool(row["coverage_held"]):
                        continue
                    scheduled_for = _required_datetime(row["scheduled_for"])
                    too_old_for_policy = index >= keep_count
                    too_late = (
                        max_lateness_seconds is not None
                        and (current - scheduled_for).total_seconds()
                        > max_lateness_seconds
                    )
                    if not (too_old_for_policy or too_late):
                        continue
                    run_id = str(row["run_id"])
                    reason = (
                        "MAX_LATENESS_EXCEEDED"
                        if too_late
                        else "SUPERSEDED_SCHEDULE_WINDOW"
                    )
                    cursor = connection.execute(
                        """
                        UPDATE automation_runs
                        SET run_status = 'MISSED', finished_at = ?,
                            error_code = 'MISSED_SCHEDULE_WINDOW',
                            error_message = ?, updated_at = ?
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
                        continue
                    _insert_event(
                        connection,
                        run_id=run_id,
                        event_type="RUN_MISSED",
                        from_status=AutomationRunStatus.SCHEDULED,
                        to_status=AutomationRunStatus.MISSED,
                        payload={"reason": reason},
                        created_at=current,
                    )
                    missed.append(run_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return tuple(missed)

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

    def ensure_coverage_candidate(
        self,
        *,
        pulse_run_id: str,
        target_run_id: str,
        executable_target_job_types: Iterable[str],
        now: datetime,
    ) -> bool:
        """Record a non-terminal coverage candidate after atomic revalidation."""

        current = _as_utc(now, "now")
        executable = frozenset(
            value.strip()
            for value in executable_target_job_types
            if value.strip()
        )
        if pulse_run_id == target_run_id:
            raise ValueError("A run cannot cover itself")
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                pulse = connection.execute(
                    """
                    SELECT runs.*, jobs.enabled AS job_enabled
                    FROM automation_runs AS runs
                    INNER JOIN automation_jobs AS jobs
                        ON jobs.job_id = runs.job_id
                    WHERE runs.run_id = ?
                    """,
                    (pulse_run_id,),
                ).fetchone()
                target = connection.execute(
                    """
                    SELECT runs.*, jobs.enabled AS job_enabled
                    FROM automation_runs AS runs
                    INNER JOIN automation_jobs AS jobs
                        ON jobs.job_id = runs.job_id
                    WHERE runs.run_id = ?
                    """,
                    (target_run_id,),
                ).fetchone()
                if pulse is None or target is None:
                    raise ValueError("Both automation runs must exist")
                valid = (
                    str(pulse["run_status"]) == "SCHEDULED"
                    and str(target["run_status"]) == "SCHEDULED"
                    and str(pulse["job_type"]) == "ONLINE_PULSE"
                    and int(pulse["job_enabled"]) == 1
                    and str(target["job_type"])
                    in {"FULL_MARKET_SCAN", "PRE_CUTOFF_FULL_SCAN"}
                    and _coverage_target_is_executable(
                        str(target["job_type"]),
                        executable,
                    )
                    and int(target["job_enabled"]) == 1
                    and str(pulse["platform_name"])
                    == str(target["platform_name"])
                    and str(pulse["platform_trade_date"])
                    == str(target["platform_trade_date"])
                    and abs(
                        (
                            _required_datetime(pulse["scheduled_for"])
                            - _required_datetime(target["scheduled_for"])
                        ).total_seconds()
                    )
                    <= 300
                )
                if not valid:
                    connection.commit()
                    return False
                existing = connection.execute(
                    """
                    SELECT 1
                    FROM automation_run_links
                    WHERE parent_run_id = ?
                      AND child_run_id = ?
                      AND relation_type = ?
                    """,
                    (
                        target_run_id,
                        pulse_run_id,
                        COVERAGE_CANDIDATE,
                    ),
                ).fetchone()
                if existing is None:
                    _insert_link(
                        connection,
                        parent_run_id=target_run_id,
                        child_run_id=pulse_run_id,
                        relation_type=COVERAGE_CANDIDATE,
                        created_at=current,
                    )
                    _insert_event(
                        connection,
                        run_id=pulse_run_id,
                        event_type="COVERAGE_CANDIDATE_CREATED",
                        from_status=AutomationRunStatus.SCHEDULED,
                        to_status=AutomationRunStatus.SCHEDULED,
                        payload={"target_run_id": target_run_id},
                        created_at=current,
                    )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def reconcile_coverage_candidates(
        self,
        *,
        executable_job_types: Iterable[str],
        now: datetime,
    ) -> tuple[str, ...]:
        """Release candidates whose target is no longer executable."""

        current = _as_utc(now, "now")
        executable = frozenset(
            value.strip()
            for value in executable_job_types
            if value.strip()
        )
        released: list[str] = []
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT links.parent_run_id, links.child_run_id,
                           targets.run_status, targets.job_type,
                           jobs.enabled, jobs.schedule_kind
                    FROM automation_run_links AS links
                    INNER JOIN automation_runs AS targets
                        ON targets.run_id = links.parent_run_id
                    INNER JOIN automation_jobs AS jobs
                        ON jobs.job_id = targets.job_id
                    WHERE links.relation_type = ?
                    ORDER BY links.parent_run_id, links.child_run_id
                    """,
                    (COVERAGE_CANDIDATE,),
                ).fetchall()
                for row in rows:
                    valid = (
                        str(row["run_status"])
                        in {"SCHEDULED", "RUNNING"}
                        and (
                            int(row["enabled"]) == 1
                            or str(row["schedule_kind"]) == "CHILD_ONLY"
                        )
                        and _coverage_target_is_executable(
                            str(row["job_type"]),
                            executable,
                        )
                    )
                    if valid:
                        continue
                    target_run_id = str(row["parent_run_id"])
                    pulse_run_id = str(row["child_run_id"])
                    connection.execute(
                        """
                        DELETE FROM automation_run_links
                        WHERE parent_run_id = ? AND child_run_id = ?
                          AND relation_type = ?
                        """,
                        (
                            target_run_id,
                            pulse_run_id,
                            COVERAGE_CANDIDATE,
                        ),
                    )
                    pulse = connection.execute(
                        """
                        SELECT run_status FROM automation_runs
                        WHERE run_id = ?
                        """,
                        (pulse_run_id,),
                    ).fetchone()
                    if (
                        pulse is not None
                        and str(pulse["run_status"]) == "SCHEDULED"
                    ):
                        _insert_event(
                            connection,
                            run_id=pulse_run_id,
                            event_type="COVERAGE_CANDIDATE_RELEASED",
                            from_status=AutomationRunStatus.SCHEDULED,
                            to_status=AutomationRunStatus.SCHEDULED,
                            payload={
                                "target_run_id": target_run_id,
                                "reason": "TARGET_NOT_EXECUTABLE",
                            },
                            created_at=current,
                        )
                        released.append(pulse_run_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return tuple(released)

    def bind_run_input_manifest(
        self,
        claim: AutomationRunClaim,
        *,
        manifest_sha256: str,
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> AutomationRun:
        """Bind one immutable external input to a live Automation child run."""

        manifest = manifest_sha256.strip().lower()
        if not _is_prefixed_sha256(manifest):
            raise ValueError("manifest_sha256 must be a prefixed SHA-256")
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _transaction_time(now=now, clock=clock)
                validate_live_automation_claim_in_transaction(
                    connection,
                    claim,
                    now=current,
                )
                row = connection.execute(
                    """
                    SELECT *
                    FROM automation_runs
                    WHERE run_id = ?
                    """,
                    (claim.run.run_id,),
                ).fetchone()
                if row is None or str(row["job_type"]) != LISTING_STATUS_SCAN:
                    raise ValueError(
                        "Only LISTING_STATUS_SCAN can bind a listing manifest"
                    )
                existing_manifest = str(
                    row["input_manifest_sha256"] or ""
                ).strip()
                if existing_manifest and existing_manifest != manifest:
                    raise ValueError(
                        "Automation run already has a different input manifest"
                    )
                if not existing_manifest:
                    batches = connection.execute(
                        """
                        SELECT batch_id, action_type, platform_name,
                               status, result_id
                        FROM shadowbot_listing_action_batches
                        WHERE manifest_sha256 = ?
                        ORDER BY batch_id
                        """,
                        (manifest,),
                    ).fetchall()
                    if len(batches) != 1:
                        raise ValueError(
                            "Listing manifest must identify exactly one "
                            "prepared SYNC_STATUS batch"
                        )
                    batch = batches[0]
                    if str(batch["action_type"]) != "sync_status":
                        raise ValueError(
                            "Listing manifest batch must use sync_status"
                        )
                    if str(batch["platform_name"]) != str(
                        row["platform_name"]
                    ):
                        raise ValueError(
                            "Listing manifest platform does not match run"
                        )
                    if (
                        str(batch["status"]) != "PREPARED"
                        or str(batch["result_id"] or "")
                    ):
                        raise ValueError(
                            "Listing manifest must be bound before publication "
                            "or result acceptance"
                        )
                    result_facts = connection.execute(
                        """
                        SELECT
                            EXISTS(
                                SELECT 1
                                FROM shadowbot_listing_result_receipts
                                WHERE batch_id = ?
                            ) AS has_receipt,
                            EXISTS(
                                SELECT 1
                                FROM listing_sync_snapshots
                                WHERE batch_id = ?
                            ) AS has_snapshot
                        """,
                        (batch["batch_id"], batch["batch_id"]),
                    ).fetchone()
                    if (
                        result_facts is not None
                        and (
                            int(result_facts["has_receipt"]) == 1
                            or int(result_facts["has_snapshot"]) == 1
                        )
                    ):
                        raise ValueError(
                            "Completed listing facts cannot be bound to a new "
                            "Automation run"
                        )
                duplicate = connection.execute(
                    """
                    SELECT run_id
                    FROM automation_runs
                    WHERE input_manifest_sha256 = ? AND run_id <> ?
                    LIMIT 1
                    """,
                    (manifest, claim.run.run_id),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError(
                        "Listing manifest is already bound to another run"
                    )
                if not existing_manifest:
                    connection.execute(
                        """
                        UPDATE automation_runs
                        SET input_manifest_sha256 = ?, updated_at = ?
                        WHERE run_id = ?
                        """,
                        (
                            manifest,
                            _datetime_text(current),
                            claim.run.run_id,
                        ),
                    )
                    _insert_event(
                        connection,
                        run_id=claim.run.run_id,
                        event_type="INPUT_MANIFEST_BOUND",
                        from_status=AutomationRunStatus.RUNNING,
                        to_status=AutomationRunStatus.RUNNING,
                        payload={"manifest_sha256": manifest},
                        created_at=current,
                    )
                bound = connection.execute(
                    "SELECT * FROM automation_runs WHERE run_id = ?",
                    (claim.run.run_id,),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if bound is None:
            raise RuntimeError("Automation run input manifest was not bound")
        return _row_to_run(bound)

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

    def ensure_child_run_fenced(
        self,
        parent_claim: AutomationRunClaim,
        child_job: AutomationJob,
        *,
        relation_type: str,
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> tuple[AutomationRun, bool]:
        """Create and link a child atomically under the parent's live lease."""

        normalized_relation = relation_type.strip().upper()
        expected_relation = CHILD_RELATIONS.get(child_job.job_type)
        if child_job.schedule_kind != "CHILD_ONLY":
            raise ValueError("Child automation job must use CHILD_ONLY")
        if (
            expected_relation is None
            or normalized_relation != expected_relation
        ):
            raise ValueError("Child job type and relation_type are not allowed")
        parent = parent_claim.run
        if parent.job_type not in CHILD_PARENT_JOB_TYPES:
            raise ValueError("Parent job type cannot create child runs")
        if parent.platform_name != str(
            child_job.config.get("platform_name") or ""
        ):
            raise ValueError("Parent and child platform_name must match")
        logical_key = f"{parent.logical_run_key}:child:{child_job.job_id}"
        child_run_id = _run_id(logical_key)

        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _transaction_time(now=now, clock=clock)
                parent_row = connection.execute(
                    "SELECT * FROM automation_runs WHERE run_id = ?",
                    (parent.run_id,),
                ).fetchone()
                if parent_row is None:
                    raise ValueError("Parent automation run does not exist")
                lease_expires_at = _text_to_datetime(
                    parent_row["lease_expires_at"]
                )
                if (
                    str(parent_row["run_status"])
                    != AutomationRunStatus.RUNNING.value
                    or str(parent_row["lease_owner"])
                    != parent_claim.owner_token
                    or int(parent_row["lease_version"])
                    != parent_claim.lease_version
                    or lease_expires_at is None
                    or lease_expires_at <= current
                ):
                    raise RuntimeError("Parent automation run lease was lost")

                existing = connection.execute(
                    """
                    SELECT * FROM automation_runs
                    WHERE logical_run_key = ?
                    """,
                    (logical_key,),
                ).fetchone()
                created = existing is None
                if existing is None:
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
                            ?, ?, ?, ?, 'SCHEDULED', ?, ?, ?, ?, ?, ?,
                            NULL, NULL, '', 0, NULL, '', '', '', '', ?, ?
                        )
                        """,
                        (
                            child_run_id,
                            child_job.job_id,
                            child_job.job_type,
                            logical_key,
                            str(parent_row["platform_name"]),
                            str(parent_row["platform_trade_date"]),
                            str(parent_row["seller_operation_date"]),
                            str(parent_row["seller_phase"]),
                            str(parent_row["time_policy_version"]),
                            str(parent_row["scheduled_for"]),
                            _datetime_text(current),
                            _datetime_text(current),
                        ),
                    )
                    _insert_event(
                        connection,
                        run_id=child_run_id,
                        event_type="CHILD_RUN_SCHEDULED",
                        from_status=None,
                        to_status=AutomationRunStatus.SCHEDULED,
                        payload={"parent_run_id": parent.run_id},
                        created_at=current,
                    )
                else:
                    expected = {
                        "job_id": child_job.job_id,
                        "job_type": child_job.job_type,
                        "platform_name": str(parent_row["platform_name"]),
                        "platform_trade_date": str(
                            parent_row["platform_trade_date"]
                        ),
                        "seller_operation_date": str(
                            parent_row["seller_operation_date"]
                        ),
                        "seller_phase": str(parent_row["seller_phase"]),
                        "time_policy_version": str(
                            parent_row["time_policy_version"]
                        ),
                        "scheduled_for": str(parent_row["scheduled_for"]),
                    }
                    for field_name, expected_value in expected.items():
                        if str(existing[field_name]) != expected_value:
                            raise ValueError(
                                "Existing child run has different "
                                f"{field_name}"
                            )
                _insert_link(
                    connection,
                    parent_run_id=parent.run_id,
                    child_run_id=child_run_id,
                    relation_type=normalized_relation,
                    created_at=current,
                )
                child_row = connection.execute(
                    "SELECT * FROM automation_runs WHERE run_id = ?",
                    (child_run_id,),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if child_row is None:
            raise RuntimeError("Child automation run was not persisted")
        return _row_to_run(child_row), created

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
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int,
        allowed_job_types: Iterable[str],
    ) -> AutomationRunClaim | None:
        claim, _ = self.claim_next_with_ui_gate(
            owner_token=owner_token,
            now=now,
            clock=clock,
            lease_seconds=lease_seconds,
            allowed_job_types=allowed_job_types,
            ui_job_types=UI_AUTOMATION_JOB_TYPES,
        )
        return claim

    def claim_next_with_ui_gate(
        self,
        *,
        owner_token: str,
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int,
        allowed_job_types: Iterable[str],
        ui_job_types: Iterable[str],
    ) -> tuple[AutomationRunClaim | None, str]:
        """Check write blockers and claim under one SQLite write lock."""

        owner = owner_token.strip()
        allowed = tuple(
            dict.fromkeys(
                job_type.strip()
                for job_type in allowed_job_types
                if job_type.strip()
            )
        )
        ui_types = frozenset(ui_job_types)
        if not owner:
            raise ValueError("owner_token must not be blank")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if not allowed:
            return None, ""
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _transaction_time(now=now, clock=clock)
                blocker = _active_ui_blocker_connection(connection)
                if not blocker and has_active_automation_ui_run(
                    connection,
                    now=current,
                ):
                    blocker = "AUTOMATION_UI_ACTIVE"
                claimable_types = tuple(
                    job_type
                    for job_type in allowed
                    if not blocker or job_type not in ui_types
                )
                claim = _claim_next_connection(
                    connection,
                    owner_token=owner,
                    now=current,
                    lease_seconds=lease_seconds,
                    allowed_job_types=claimable_types,
                )
                connection.commit()
                return claim, blocker
            except Exception:
                connection.rollback()
                raise

    def claim_run(
        self,
        *,
        run_id: str,
        owner_token: str,
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int,
    ) -> AutomationRunClaim | None:
        owner = owner_token.strip()
        if not owner:
            raise ValueError("owner_token must not be blank")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _transaction_time(now=now, clock=clock)
                claim = _claim_specific_run_connection(
                    connection,
                    run_id=run_id,
                    owner_token=owner,
                    now=current,
                    lease_seconds=lease_seconds,
                    ui_job_types=UI_AUTOMATION_JOB_TYPES,
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
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int,
    ) -> AutomationRunClaim | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _transaction_time(now=now, clock=clock)
                expires_at = current + timedelta(seconds=lease_seconds)
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
                    connection.rollback()
                    return None
                row = connection.execute(
                    "SELECT * FROM automation_runs WHERE run_id = ?",
                    (claim.run.run_id,),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
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
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> bool:
        with closing(self.runtime_repository.connect_write()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _transaction_time(now=now, clock=clock)
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
                _settle_linked_runs(
                    connection,
                    parent_run_id=claim.run.run_id,
                    parent_status=outcome.status,
                    now=current,
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def active_ui_blocker(self) -> str:
        """Return the highest existing write-side blocker for read scans."""

        with closing(self.runtime_repository.connect_read()) as connection:
            blocker = _active_ui_blocker_connection(connection)
            if blocker:
                return blocker
            if has_active_automation_ui_run(
                connection,
                now=datetime.now(timezone.utc),
            ):
                return "AUTOMATION_UI_ACTIVE"
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


def _coverage_target_is_executable(
    job_type: str,
    executable_job_types: frozenset[str],
) -> bool:
    if job_type in CHILD_PARENT_JOB_TYPES:
        return (
            job_type in executable_job_types
            and LISTING_STATUS_SCAN in executable_job_types
        )
    return (
        job_type == LISTING_STATUS_SCAN
        and LISTING_STATUS_SCAN in executable_job_types
    )


def _settle_linked_runs(
    connection,
    *,
    parent_run_id: str,
    parent_status: AutomationRunStatus,
    now: datetime,
) -> None:
    allows_children = parent_status in {
        AutomationRunStatus.SUCCESS,
        AutomationRunStatus.PARTIAL,
    }
    run_row = connection.execute(
        "SELECT job_type FROM automation_runs WHERE run_id = ?",
        (parent_run_id,),
    ).fetchone()
    run_job_type = str(run_row["job_type"]) if run_row is not None else ""
    coverage_rows = connection.execute(
        """
        SELECT child_run_id
        FROM automation_run_links
        WHERE parent_run_id = ? AND relation_type = ?
        ORDER BY child_run_id
        """,
        (parent_run_id, COVERAGE_CANDIDATE),
    ).fetchall()

    if coverage_rows and run_job_type in CHILD_PARENT_JOB_TYPES:
        listing_child = None
        if allows_children:
            listing_child = connection.execute(
                """
                SELECT children.run_id
                FROM automation_run_links AS links
                INNER JOIN automation_runs AS children
                    ON children.run_id = links.child_run_id
                WHERE links.parent_run_id = ?
                  AND links.relation_type = 'LISTING_STATUS_CHILD'
                  AND children.job_type = 'LISTING_STATUS_SCAN'
                  AND children.run_status = 'SCHEDULED'
                ORDER BY children.run_id
                LIMIT 1
                """,
                (parent_run_id,),
            ).fetchone()
        if listing_child is None:
            _release_coverage_candidates(
                connection,
                target_run_id=parent_run_id,
                coverage_rows=coverage_rows,
                reason=(
                    "LISTING_STATUS_CHILD_NOT_AVAILABLE"
                    if allows_children
                    else f"TARGET_{parent_status.value}"
                ),
                now=now,
            )
        else:
            listing_child_run_id = str(listing_child["run_id"])
            for row in coverage_rows:
                pulse_run_id = str(row["child_run_id"])
                connection.execute(
                    """
                    DELETE FROM automation_run_links
                    WHERE parent_run_id = ? AND child_run_id = ?
                      AND relation_type = ?
                    """,
                    (
                        parent_run_id,
                        pulse_run_id,
                        COVERAGE_CANDIDATE,
                    ),
                )
                _insert_link(
                    connection,
                    parent_run_id=listing_child_run_id,
                    child_run_id=pulse_run_id,
                    relation_type=COVERAGE_CANDIDATE,
                    created_at=now,
                )
                _insert_event(
                    connection,
                    run_id=pulse_run_id,
                    event_type="COVERAGE_CANDIDATE_RETARGETED",
                    from_status=AutomationRunStatus.SCHEDULED,
                    to_status=AutomationRunStatus.SCHEDULED,
                    payload={
                        "orchestration_run_id": parent_run_id,
                        "listing_status_run_id": listing_child_run_id,
                    },
                    created_at=now,
                )
    elif coverage_rows and run_job_type == LISTING_STATUS_SCAN:
        if (
            parent_status is AutomationRunStatus.SUCCESS
            and _listing_coverage_facts_accepted(
                connection,
                listing_run_id=parent_run_id,
            )
        ):
            _finalize_coverage_candidates(
                connection,
                target_run_id=parent_run_id,
                coverage_rows=coverage_rows,
                now=now,
            )
        else:
            _release_coverage_candidates(
                connection,
                target_run_id=parent_run_id,
                coverage_rows=coverage_rows,
                reason=(
                    "LISTING_FACTS_NOT_ACCEPTED"
                    if parent_status is AutomationRunStatus.SUCCESS
                    else f"TARGET_{parent_status.value}"
                ),
                now=now,
            )
    elif coverage_rows:
        _release_coverage_candidates(
            connection,
            target_run_id=parent_run_id,
            coverage_rows=coverage_rows,
            reason="UNSUPPORTED_COVERAGE_TARGET",
            now=now,
        )

    if allows_children:
        return
    child_relations = tuple(CHILD_RELATIONS.values())
    relation_placeholders = ",".join("?" for _ in child_relations)
    child_rows = connection.execute(
        f"""
        SELECT DISTINCT child_run_id
        FROM automation_run_links
        WHERE parent_run_id = ?
          AND relation_type IN ({relation_placeholders})
        ORDER BY child_run_id
        """,
        (parent_run_id, *child_relations),
    ).fetchall()
    for row in child_rows:
        child_run_id = str(row["child_run_id"])
        cursor = connection.execute(
            """
            UPDATE automation_runs
            SET run_status = 'CANCELLED', finished_at = ?,
                error_code = 'PARENT_RUN_NOT_SUCCESSFUL',
                error_message = ?, updated_at = ?
            WHERE run_id = ? AND run_status = 'SCHEDULED'
            """,
            (
                _datetime_text(now),
                f"Parent run ended as {parent_status.value}",
                _datetime_text(now),
                child_run_id,
            ),
        )
        if cursor.rowcount == 1:
            _insert_event(
                connection,
                run_id=child_run_id,
                event_type="RUN_CANCELLED_BY_PARENT",
                from_status=AutomationRunStatus.SCHEDULED,
                to_status=AutomationRunStatus.CANCELLED,
                payload={
                    "parent_run_id": parent_run_id,
                    "parent_status": parent_status.value,
                },
                created_at=now,
            )


def _finalize_coverage_candidates(
    connection,
    *,
    target_run_id: str,
    coverage_rows,
    now: datetime,
) -> None:
    for row in coverage_rows:
        pulse_run_id = str(row["child_run_id"])
        cursor = connection.execute(
            """
            UPDATE automation_runs
            SET run_status = 'MERGED', finished_at = ?,
                error_code = 'MERGED_INTO_SUCCESSFUL_RUN',
                error_message = ?,
                updated_at = ?
            WHERE run_id = ? AND run_status = 'SCHEDULED'
            """,
            (
                _datetime_text(now),
                f"Covered by accepted listing run {target_run_id}",
                _datetime_text(now),
                pulse_run_id,
            ),
        )
        if cursor.rowcount == 1:
            _insert_link(
                connection,
                parent_run_id=target_run_id,
                child_run_id=pulse_run_id,
                relation_type="MERGED_RUN",
                created_at=now,
            )
            _insert_event(
                connection,
                run_id=pulse_run_id,
                event_type="RUN_MERGED",
                from_status=AutomationRunStatus.SCHEDULED,
                to_status=AutomationRunStatus.MERGED,
                payload={"target_run_id": target_run_id},
                created_at=now,
            )
        connection.execute(
            """
            DELETE FROM automation_run_links
            WHERE parent_run_id = ? AND child_run_id = ?
              AND relation_type = ?
            """,
            (target_run_id, pulse_run_id, COVERAGE_CANDIDATE),
        )


def _release_coverage_candidates(
    connection,
    *,
    target_run_id: str,
    coverage_rows,
    reason: str,
    now: datetime,
) -> None:
    for row in coverage_rows:
        pulse_run_id = str(row["child_run_id"])
        connection.execute(
            """
            DELETE FROM automation_run_links
            WHERE parent_run_id = ? AND child_run_id = ?
              AND relation_type = ?
            """,
            (target_run_id, pulse_run_id, COVERAGE_CANDIDATE),
        )
        pulse = connection.execute(
            "SELECT run_status FROM automation_runs WHERE run_id = ?",
            (pulse_run_id,),
        ).fetchone()
        if (
            pulse is not None
            and str(pulse["run_status"])
            == AutomationRunStatus.SCHEDULED.value
        ):
            _insert_event(
                connection,
                run_id=pulse_run_id,
                event_type="COVERAGE_CANDIDATE_RELEASED",
                from_status=AutomationRunStatus.SCHEDULED,
                to_status=AutomationRunStatus.SCHEDULED,
                payload={
                    "target_run_id": target_run_id,
                    "reason": reason,
                },
                created_at=now,
            )


def _listing_coverage_facts_accepted(
    connection,
    *,
    listing_run_id: str,
) -> bool:
    rows = connection.execute(
        """
        SELECT runs.platform_trade_date, runs.input_manifest_sha256,
                batches.manifest_sha256, snapshots.snapshot_id,
                snapshots.evidence_manifest_sha256,
                receipts.result_sha256,
                observations.observation_batch_id,
                observations.requested_scope_json
        FROM automation_runs AS runs
        INNER JOIN shadowbot_listing_action_batches AS batches
            ON batches.manifest_sha256 = runs.input_manifest_sha256
           AND batches.platform_name = runs.platform_name
        INNER JOIN listing_sync_snapshots AS snapshots
            ON snapshots.batch_id = batches.batch_id
           AND snapshots.platform_name = runs.platform_name
        INNER JOIN shadowbot_listing_result_receipts AS receipts
            ON receipts.result_id = snapshots.result_id
           AND receipts.batch_id = batches.batch_id
        INNER JOIN product_observation_batches AS observations
            ON observations.automation_run_id = runs.run_id
           AND observations.platform_name = runs.platform_name
           AND observations.time_policy_version =
               runs.time_policy_version
        WHERE runs.run_id = ?
          AND runs.job_type = 'LISTING_STATUS_SCAN'
          AND runs.input_manifest_sha256 <> ''
          AND batches.action_type = 'sync_status'
          AND batches.status = 'VERIFIED'
          AND snapshots.status = 'VERIFIED'
          AND snapshots.snapshot_complete = 1
          AND observations.scan_type = 'LISTING_STATUS_SCAN'
          AND observations.batch_status = 'ACCEPTED'
          AND observations.scope_complete = 1
          AND observations.end_marker_verified = 1
        """,
        (listing_run_id,),
    ).fetchall()
    for row in rows:
        try:
            scope = json.loads(str(row["requested_scope_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(scope, dict):
            continue
        run_trade_date = str(row["platform_trade_date"])
        run_manifest = str(row["input_manifest_sha256"])
        conversion_sha256 = str(
            scope.get("source_conversion_sha256") or ""
        )
        mapping_identity_sha256 = str(
            scope.get("source_mapping_identity_sha256") or ""
        )
        validated_mapping_identity_sha256 = str(
            scope.get("validated_mapping_identity_sha256") or ""
        )
        if (
            str(scope.get("source_snapshot_id") or "")
            != str(row["snapshot_id"])
            or str(scope.get("source_manifest_sha256") or "")
            != run_manifest
            or run_manifest != str(row["manifest_sha256"])
            or str(scope.get("source_result_sha256") or "")
            != str(row["result_sha256"])
            or str(scope.get("source_platform_trade_date") or "")
            != run_trade_date
            or not _is_prefixed_sha256(conversion_sha256)
            or not _is_prefixed_sha256(mapping_identity_sha256)
            or (
                validated_mapping_identity_sha256
                != mapping_identity_sha256
            )
        ):
            continue
        source_item_rows = connection.execute(
            """
            SELECT *
            FROM listing_sync_snapshot_items
            WHERE snapshot_id = ?
            ORDER BY snapshot_item_id
            """,
            (row["snapshot_id"],),
        ).fetchall()
        try:
            source_items = tuple(
                {
                    **dict(source_item),
                    "affected_internal_skus": json.loads(
                        str(source_item["affected_internal_skus_json"])
                    ),
                    "online_row_identities": json.loads(
                        str(source_item["online_row_identities_json"])
                    ),
                    "waiting_row_identities": json.loads(
                        str(source_item["waiting_row_identities_json"])
                    ),
                }
                for source_item in source_item_rows
            )
            source_identities = listing_observation_source_identities(
                snapshot_id=str(row["snapshot_id"]),
                evidence_manifest_sha256=str(
                    row["evidence_manifest_sha256"]
                ),
                snapshot_items=source_items,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            mapping_identity_sha256
            != listing_observation_source_identity_sha256(
                source_identities
            )
            or not _persisted_observations_match_source_identities(
                connection,
                observation_batch_id=str(row["observation_batch_id"]),
                source_identities=source_identities,
            )
        ):
            continue
        mismatched_item = connection.execute(
            """
            SELECT 1
            FROM product_observation_items
            WHERE observation_batch_id = ?
              AND platform_trade_date <> ?
            LIMIT 1
            """,
            (row["observation_batch_id"], run_trade_date),
        ).fetchone()
        if mismatched_item is None:
            return True
    return False


def _persisted_observations_match_source_identities(
    connection,
    *,
    observation_batch_id: str,
    source_identities: Iterable[ListingObservationSourceIdentity],
) -> bool:
    expected = {
        identity.evidence_sha256: identity
        for identity in source_identities
    }
    rows = connection.execute(
        """
        SELECT evidence_sha256, internal_sku, mapping_status
        FROM product_observation_items
        WHERE observation_batch_id = ?
        """,
        (observation_batch_id,),
    ).fetchall()
    if len(rows) != len(expected):
        return False
    actual: dict[str, object] = {}
    for row in rows:
        evidence_sha256 = str(row["evidence_sha256"])
        if evidence_sha256 in actual:
            return False
        actual[evidence_sha256] = row
    if set(actual) != set(expected):
        return False
    for evidence_sha256, identity in expected.items():
        row = actual[evidence_sha256]
        internal_sku = str(row["internal_sku"] or "").strip() or None
        if (
            str(row["mapping_status"]) != identity.mapping_status.value
            or internal_sku != identity.internal_sku
        ):
            return False
    return True


def _claim_next_connection(
    connection,
    *,
    owner_token: str,
    now: datetime,
    lease_seconds: int,
    allowed_job_types: tuple[str, ...],
) -> AutomationRunClaim | None:
    if not allowed_job_types:
        return None
    placeholders = ",".join("?" for _ in allowed_job_types)
    child_relations = tuple(CHILD_RELATIONS.values())
    relation_placeholders = ",".join("?" for _ in child_relations)
    parent_statuses = tuple(CHILD_PARENT_CLAIM_STATUSES)
    parent_status_placeholders = ",".join("?" for _ in parent_statuses)
    executable = frozenset(allowed_job_types)
    coverage_target_types = tuple(
        job_type
        for job_type in (
            *sorted(CHILD_PARENT_JOB_TYPES),
            LISTING_STATUS_SCAN,
        )
        if _coverage_target_is_executable(job_type, executable)
    )
    coverage_placeholders = ",".join(
        "?" for _ in coverage_target_types
    ) or "NULL"
    row = connection.execute(
        f"""
        SELECT runs.run_id
        FROM automation_runs AS runs
        INNER JOIN automation_jobs AS jobs ON jobs.job_id = runs.job_id
        WHERE runs.job_type IN ({placeholders})
          AND (
              (
                  runs.run_status = 'SCHEDULED'
                  AND julianday(runs.scheduled_for) <= julianday(?)
                  AND (
                      (
                          jobs.enabled = 1
                          AND jobs.schedule_kind <> 'CHILD_ONLY'
                      )
                      OR (
                          jobs.schedule_kind = 'CHILD_ONLY'
                          AND EXISTS (
                              SELECT 1
                              FROM automation_run_links AS links
                              INNER JOIN automation_runs AS parents
                                  ON parents.run_id = links.parent_run_id
                              WHERE links.child_run_id = runs.run_id
                                AND links.relation_type IN (
                                    {relation_placeholders}
                                )
                                AND parents.run_status IN (
                                    {parent_status_placeholders}
                                )
                          )
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM automation_run_links AS coverage
                      INNER JOIN automation_runs AS targets
                          ON targets.run_id = coverage.parent_run_id
                      INNER JOIN automation_jobs AS target_jobs
                          ON target_jobs.job_id = targets.job_id
                        WHERE coverage.child_run_id = runs.run_id
                         AND coverage.relation_type = 'COVERAGE_CANDIDATE'
                         AND targets.run_status IN ('SCHEDULED', 'RUNNING')
                         AND (
                             target_jobs.enabled = 1
                             OR target_jobs.schedule_kind = 'CHILD_ONLY'
                         )
                         AND targets.job_type IN ({coverage_placeholders})
                   )
              )
              OR (
                  runs.run_status = 'RUNNING'
                  AND (
                      runs.lease_expires_at IS NULL
                      OR julianday(runs.lease_expires_at) <= julianday(?)
                  )
              )
          )
        ORDER BY
            CASE WHEN runs.run_status = 'RUNNING' THEN 0 ELSE 1 END,
            jobs.priority ASC,
            runs.scheduled_for ASC,
            runs.run_id ASC
        LIMIT 1
        """,
        (
            *allowed_job_types,
            _datetime_text(now),
            *child_relations,
            *parent_statuses,
            *coverage_target_types,
            _datetime_text(now),
        ),
    ).fetchone()
    if row is None:
        return None
    return _claim_run(
        connection,
        run_id=str(row["run_id"]),
        owner_token=owner_token,
        now=now,
        lease_seconds=lease_seconds,
    )


def _claim_specific_run_connection(
    connection,
    *,
    run_id: str,
    owner_token: str,
    now: datetime,
    lease_seconds: int,
    ui_job_types: Iterable[str],
) -> AutomationRunClaim | None:
    row = connection.execute(
        """
        SELECT runs.run_status, runs.job_type, jobs.enabled,
               jobs.schedule_kind
        FROM automation_runs AS runs
        INNER JOIN automation_jobs AS jobs ON jobs.job_id = runs.job_id
        WHERE runs.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    status = AutomationRunStatus(str(row["run_status"]))
    if status is AutomationRunStatus.SCHEDULED:
        if str(row["schedule_kind"]) == "CHILD_ONLY":
            relations = tuple(CHILD_RELATIONS.values())
            relation_placeholders = ",".join("?" for _ in relations)
            parent_statuses = tuple(CHILD_PARENT_CLAIM_STATUSES)
            status_placeholders = ",".join("?" for _ in parent_statuses)
            parent = connection.execute(
                f"""
                SELECT 1
                FROM automation_run_links AS links
                INNER JOIN automation_runs AS parents
                    ON parents.run_id = links.parent_run_id
                WHERE links.child_run_id = ?
                  AND links.relation_type IN ({relation_placeholders})
                  AND parents.run_status IN ({status_placeholders})
                LIMIT 1
                """,
                (run_id, *relations, *parent_statuses),
            ).fetchone()
            if parent is None:
                return None
        elif int(row["enabled"]) != 1:
            return None
        coverage = connection.execute(
            """
            SELECT 1
            FROM automation_run_links AS links
            INNER JOIN automation_runs AS targets
                ON targets.run_id = links.parent_run_id
            INNER JOIN automation_jobs AS jobs
                ON jobs.job_id = targets.job_id
            WHERE links.child_run_id = ?
              AND links.relation_type = 'COVERAGE_CANDIDATE'
              AND targets.run_status IN ('SCHEDULED', 'RUNNING')
              AND (
                  jobs.enabled = 1
                  OR jobs.schedule_kind = 'CHILD_ONLY'
              )
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if coverage is not None:
            return None
    elif status is not AutomationRunStatus.RUNNING:
        return None

    if str(row["job_type"]) in frozenset(ui_job_types):
        if _active_ui_blocker_connection(connection):
            return None
        if has_active_automation_ui_run(
            connection,
            now=now,
            exclude_run_id=run_id,
        ):
            return None
    return _claim_run(
        connection,
        run_id=run_id,
        owner_token=owner_token,
        now=now,
        lease_seconds=lease_seconds,
    )


def _active_ui_blocker_connection(connection) -> str:
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


def _transaction_time(
    *,
    now: datetime | None,
    clock: Callable[[], datetime] | None,
) -> datetime:
    if (now is None) == (clock is None):
        raise ValueError("Provide exactly one of now or clock")
    if clock is not None:
        return _as_utc(clock(), "clock")
    if now is None:
        raise ValueError("now is required when clock is not provided")
    return _as_utc(now, "now")


def _is_prefixed_sha256(value: str) -> bool:
    return (
        value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )
