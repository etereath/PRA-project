from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Callable

from app.enums import IncidentCategory, IncidentEventType, IncidentStatus
from app.exceptions import (
    IncidentIdempotencyConflictError,
    IncidentNotFoundError,
    IncidentTransitionError,
)
from app.models import (
    IncidentMutationResult,
    OperationalIncident,
    OperationalIncidentEvent,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository

INCIDENT_SEVERITIES = frozenset({"S0", "S1", "S2", "S3", "S4"})
INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.OPEN: frozenset(
        {
            IncidentStatus.WAITING_HUMAN,
            IncidentStatus.AUTO_PROTECTING,
            IncidentStatus.RESOLVED,
        }
    ),
    IncidentStatus.WAITING_HUMAN: frozenset(
        {IncidentStatus.OPEN, IncidentStatus.RESOLVED}
    ),
    IncidentStatus.AUTO_PROTECTING: frozenset(
        {IncidentStatus.WAITING_HUMAN, IncidentStatus.RESOLVED}
    ),
    IncidentStatus.RESOLVED: frozenset({IncidentStatus.OPEN, IncidentStatus.CLOSED}),
    IncidentStatus.CLOSED: frozenset(),
}


class OperationalIncidentRepository:
    """Atomic v15 Incident mutations using the shared Runtime connection policy."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository

    def record_detection(
        self,
        *,
        event_key: str,
        dedupe_key: str,
        category: IncidentCategory,
        source_type: str,
        source_ref_id: str,
        severity: str,
        blocks_finalization: bool,
        platform_name: str | None,
        platform_trade_date: date | None,
        seller_operation_date: date | None,
        subject_type: str,
        subject_key: str,
        title: str,
        description: str,
        occurred_at: datetime,
        event_payload: dict[str, object] | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> IncidentMutationResult:
        _require_text(event_key, "event_key")
        _require_text(dedupe_key, "dedupe_key")
        _require_text(source_type, "source_type")
        _require_text(subject_type, "subject_type")
        _require_text(subject_key, "subject_key")
        _require_text(title, "title")
        _require_severity(severity)
        _require_aware_datetime(occurred_at, "occurred_at")
        detection_payload = {
            "category": category.value,
            "dedupe_key": dedupe_key,
            "platform_name": platform_name,
            "platform_trade_date": _date_text(platform_trade_date),
            "seller_operation_date": _date_text(seller_operation_date),
            "subject_type": subject_type,
            "subject_key": subject_key,
            "title": title,
            "description": description,
            "blocks_finalization": blocks_finalization,
            "details": event_payload or {},
        }
        payload = {"detection": detection_payload}

        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._existing_event_replay(
                connection,
                event_key=event_key,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                severity=severity,
                event_payload=payload,
            )
            if replay is not None:
                connection.rollback()
                return replay

            latest = connection.execute(
                """
                SELECT * FROM operational_incidents
                WHERE dedupe_key = ?
                ORDER BY created_at DESC, incident_id DESC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
            latest_event_at = (
                self._latest_event_occurred_at_on_connection(
                    connection,
                    str(latest["incident_id"]),
                )
                if latest is not None
                else None
            )
            late_event = (
                latest_event_at is not None and occurred_at < latest_event_at
            )
            create_new = (
                latest is None
                or (
                    str(latest["incident_status"])
                    == IncidentStatus.CLOSED.value
                    and not late_event
                )
            )
            event_type = (
                IncidentEventType.DETECTED
                if create_new
                else IncidentEventType.REDETECTED
            )
            incident_id = (
                _stable_id("incident", f"{dedupe_key}:{event_key}")
                if create_new
                else str(latest["incident_id"])
            )
            from_status = (
                None if create_new else IncidentStatus(str(latest["incident_status"]))
            )
            to_status = (
                from_status
                if late_event
                else IncidentStatus.OPEN
                if from_status is IncidentStatus.RESOLVED
                else from_status
            )

            if create_new:
                connection.execute(
                    """
                    INSERT INTO operational_incidents(
                        incident_id, dedupe_key, category, source_type, source_ref_id,
                        severity, incident_status, blocks_finalization, platform_name,
                        platform_trade_date, seller_operation_date, subject_type,
                        subject_key, title, description, first_detected_at,
                        last_detected_at, resolved_at, occurrence_count, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
                    """,
                    (
                        incident_id,
                        dedupe_key,
                        category.value,
                        source_type,
                        source_ref_id,
                        severity,
                        int(blocks_finalization),
                        platform_name,
                        _date_text(platform_trade_date),
                        _date_text(seller_operation_date),
                        subject_type,
                        subject_key,
                        title,
                        description,
                        _datetime_text(occurred_at),
                        _datetime_text(occurred_at),
                        _datetime_text(occurred_at),
                        _datetime_text(occurred_at),
                    ),
                )
            elif not late_event:
                connection.execute(
                    """
                    UPDATE operational_incidents
                    SET category = ?, source_type = ?, source_ref_id = ?, severity = ?,
                        incident_status = ?, blocks_finalization = ?, platform_name = ?,
                        platform_trade_date = ?, seller_operation_date = ?, subject_type = ?,
                        subject_key = ?, title = ?, description = ?, last_detected_at = ?,
                        resolved_at = NULL, occurrence_count = occurrence_count + 1, updated_at = ?
                    WHERE incident_id = ?
                    """,
                    (
                        category.value,
                        source_type,
                        source_ref_id,
                        severity,
                        (to_status or IncidentStatus.OPEN).value,
                        int(blocks_finalization),
                        platform_name,
                        _date_text(platform_trade_date),
                        _date_text(seller_operation_date),
                        subject_type,
                        subject_key,
                        title,
                        description,
                        _datetime_text(occurred_at),
                        _datetime_text(occurred_at),
                        incident_id,
                    ),
                )
            _inject(failure_injector, "after_incident_write")
            event = self._insert_event(
                connection,
                event_key=event_key,
                incident_id=incident_id,
                event_type=event_type,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                from_status=from_status,
                to_status=to_status,
                severity=severity,
                event_payload=payload,
            )
            _inject(failure_injector, "after_event_write")
            incident = self._get_required_on_connection(connection, incident_id)
            connection.commit()
            return IncidentMutationResult(incident=incident, event=event)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def transition_status(
        self,
        incident_id: str,
        *,
        to_status: IncidentStatus,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str = "",
        event_payload: dict[str, object] | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> IncidentMutationResult:
        _require_aware_datetime(occurred_at, "occurred_at")
        _require_text(event_key, "event_key")
        _require_text(source_type, "source_type")
        payload = {
            "transition": event_payload or {},
            "requested_to_status": to_status.value,
        }
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            incident = self._get_required_on_connection(connection, incident_id)
            replay = self._existing_event_replay(
                connection,
                event_key=event_key,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                severity=None,
                event_payload=payload,
                incident_id=incident_id,
                event_type=IncidentEventType.STATUS_CHANGED,
            )
            if replay is not None:
                connection.rollback()
                return replay
            latest_event_at = self._latest_event_occurred_at_on_connection(
                connection,
                incident_id,
            )
            late_event = (
                latest_event_at is not None and occurred_at < latest_event_at
            )
            allowed = INCIDENT_TRANSITIONS.get(incident.incident_status)
            if not late_event and (allowed is None or to_status not in allowed):
                raise IncidentTransitionError(
                    f"invalid Incident transition: {incident.incident_status.value} -> {to_status.value}"
                )
            resolved_at = (
                occurred_at
                if to_status in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED}
                else None
            )
            if to_status is IncidentStatus.CLOSED and incident.resolved_at is not None:
                resolved_at = incident.resolved_at
            if not late_event:
                connection.execute(
                    """
                    UPDATE operational_incidents
                    SET incident_status = ?, resolved_at = ?, updated_at = ?
                    WHERE incident_id = ? AND incident_status = ?
                    """,
                    (
                        to_status.value,
                        _datetime_text(resolved_at),
                        _datetime_text(occurred_at),
                        incident_id,
                        incident.incident_status.value,
                    ),
                )
                _inject(failure_injector, "after_incident_write")
            event = self._insert_event(
                connection,
                event_key=event_key,
                incident_id=incident_id,
                event_type=IncidentEventType.STATUS_CHANGED,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                from_status=incident.incident_status,
                to_status=(incident.incident_status if late_event else to_status),
                severity=incident.severity,
                event_payload=payload,
            )
            _inject(failure_injector, "after_event_write")
            updated = self._get_required_on_connection(connection, incident_id)
            connection.commit()
            return IncidentMutationResult(incident=updated, event=event)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def acknowledge(
        self,
        incident_id: str,
        *,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str = "",
        event_payload: dict[str, object] | None = None,
    ) -> IncidentMutationResult:
        return self._append_observation_event(
            incident_id,
            event_type=IncidentEventType.ACK,
            event_key=event_key,
            occurred_at=occurred_at,
            source_type=source_type,
            source_ref_id=source_ref_id,
            event_payload={"ack": event_payload or {}},
        )

    def change_severity(
        self,
        incident_id: str,
        *,
        severity: str,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str = "",
        reason: str = "",
    ) -> IncidentMutationResult:
        _require_severity(severity)
        _require_aware_datetime(occurred_at, "occurred_at")
        payload = {"severity_change": {"reason": reason, "to_severity": severity}}
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            incident = self._get_required_on_connection(connection, incident_id)
            replay = self._existing_event_replay(
                connection,
                event_key=event_key,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                severity=None,
                event_payload=payload,
                incident_id=incident_id,
                event_type=IncidentEventType.SEVERITY_CHANGED,
            )
            if replay is not None:
                connection.rollback()
                return replay
            if severity == incident.severity:
                raise IncidentTransitionError(
                    f"Incident severity is already {severity}: {incident_id}"
                )
            connection.execute(
                "UPDATE operational_incidents SET severity = ?, updated_at = ? WHERE incident_id = ?",
                (severity, _datetime_text(occurred_at), incident_id),
            )
            event = self._insert_event(
                connection,
                event_key=event_key,
                incident_id=incident_id,
                event_type=IncidentEventType.SEVERITY_CHANGED,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                from_status=incident.incident_status,
                to_status=incident.incident_status,
                severity=severity,
                event_payload=payload,
            )
            updated = self._get_required_on_connection(connection, incident_id)
            connection.commit()
            return IncidentMutationResult(incident=updated, event=event)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_related_event(
        self,
        incident_id: str,
        *,
        event_type: IncidentEventType,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str = "",
        event_payload: dict[str, object] | None = None,
    ) -> IncidentMutationResult:
        if event_type not in {
            IncidentEventType.RECOVERY_RECORDED,
            IncidentEventType.REVIEW_RECORDED,
            IncidentEventType.TASK_RECORDED,
        }:
            raise ValueError(
                f"unsupported related Incident event type: {event_type.value}"
            )
        return self._append_observation_event(
            incident_id,
            event_type=event_type,
            event_key=event_key,
            occurred_at=occurred_at,
            source_type=source_type,
            source_ref_id=source_ref_id,
            event_payload={"record": event_payload or {}},
        )

    def get(self, incident_id: str) -> OperationalIncident | None:
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM operational_incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return _row_to_incident(row) if row is not None else None

    def list_active(
        self,
        *,
        category: IncidentCategory | None = None,
        platform_name: str | None = None,
    ) -> list[OperationalIncident]:
        clauses = ["incident_status IN ('OPEN', 'WAITING_HUMAN', 'AUTO_PROTECTING')"]
        params: list[str] = []
        if category is not None:
            clauses.append("category = ?")
            params.append(category.value)
        if platform_name is not None:
            clauses.append("platform_name = ?")
            params.append(platform_name)
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM operational_incidents
                WHERE {" AND ".join(clauses)}
                ORDER BY severity DESC, first_detected_at ASC, incident_id ASC
                """,
                params,
            ).fetchall()
        return [_row_to_incident(row) for row in rows]

    def list_events(self, incident_id: str) -> list[OperationalIncidentEvent]:
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM operational_incident_events
                WHERE incident_id = ?
                ORDER BY occurred_at, event_id
                """,
                (incident_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def find_qualified_online_pulse_observation(
        self,
        *,
        platform_name: str,
        platform_trade_date: date,
        internal_sku: str,
        decision_window_started_at: datetime,
        initial_observation_id: str,
    ) -> dict[str, object] | None:
        """Return the first imported, complete ONLINE_PULSE observation after delivery.

        A pulse may carry its own observation batch or be completed by the existing
        FULL_MARKET_SCAN coverage path.  This query only projects already accepted
        facts; it does not schedule a run or infer that waiting alone is sufficient.
        """

        _require_text(platform_name, "platform_name")
        _require_text(internal_sku, "internal_sku")
        _require_aware_datetime(
            decision_window_started_at,
            "decision_window_started_at",
        )
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                WITH pulse_sources AS (
                    SELECT
                        pulse.run_id AS pulse_run_id,
                        pulse.scheduled_for AS pulse_scheduled_for,
                        pulse.finished_at AS pulse_finished_at,
                        pulse.platform_name AS platform_name,
                        pulse.platform_trade_date AS platform_trade_date,
                        CASE
                            WHEN pulse.run_status = 'SUCCESS' THEN pulse.run_id
                            ELSE merged.parent_run_id
                        END AS observation_run_id,
                        target.finished_at AS target_finished_at
                    FROM automation_runs AS pulse
                    LEFT JOIN automation_run_links AS merged
                      ON merged.child_run_id = pulse.run_id
                     AND merged.relation_type = 'MERGED_RUN'
                    LEFT JOIN automation_runs AS target
                      ON target.run_id = merged.parent_run_id
                    WHERE pulse.job_type = 'ONLINE_PULSE'
                      AND (
                          pulse.run_status = 'SUCCESS'
                          OR (
                              pulse.run_status = 'MERGED'
                              AND target.run_status = 'SUCCESS'
                          )
                      )
                )
                SELECT
                    sources.pulse_run_id,
                    sources.pulse_scheduled_for,
                    sources.pulse_finished_at,
                    sources.target_finished_at,
                    sources.observation_run_id,
                    batches.observation_batch_id,
                    batches.scan_started_at,
                    batches.scan_completed_at,
                    batches.created_at AS imported_at,
                    items.observation_item_id,
                    items.observed_price,
                    items.observed_online,
                    items.observed_at,
                    items.mapping_status,
                    items.mapping_version
                FROM pulse_sources AS sources
                INNER JOIN product_observation_batches AS batches
                  ON batches.automation_run_id = sources.observation_run_id
                INNER JOIN product_observation_items AS items
                  ON items.observation_batch_id = batches.observation_batch_id
                WHERE sources.platform_name = ?
                  AND sources.platform_trade_date = ?
                  AND julianday(sources.pulse_scheduled_for) > julianday(?)
                  AND julianday(batches.scan_started_at) > julianday(?)
                  AND batches.scan_type = 'LISTING_STATUS_SCAN'
                  AND batches.batch_status = 'ACCEPTED'
                  AND batches.scope_complete = 1
                  AND batches.end_marker_verified = 1
                  AND items.internal_sku = ?
                  AND items.platform_trade_date = ?
                  AND items.mapping_status = 'VERIFIED'
                  AND items.observed_online = 1
                  AND items.observed_price IS NOT NULL
                  AND trim(items.observed_price) <> ''
                  AND items.observation_item_id <> ?
                ORDER BY
                    julianday(sources.pulse_scheduled_for) ASC,
                    julianday(batches.scan_completed_at) ASC,
                    items.observation_item_id ASC
                LIMIT 1
                """,
                (
                    platform_name,
                    platform_trade_date.isoformat(),
                    _datetime_text(decision_window_started_at),
                    _datetime_text(decision_window_started_at),
                    internal_sku,
                    platform_trade_date.isoformat(),
                    initial_observation_id,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_product_observation_identity(
        self,
        observation_item_id: str,
    ) -> dict[str, object] | None:
        _require_text(observation_item_id, "observation_item_id")
        with closing(self.runtime_repository.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT
                    items.observation_item_id,
                    items.internal_sku,
                    items.platform_trade_date,
                    items.mapping_status,
                    batches.platform_name,
                    batches.automation_run_id
                FROM product_observation_items AS items
                INNER JOIN product_observation_batches AS batches
                  ON batches.observation_batch_id = items.observation_batch_id
                WHERE items.observation_item_id = ?
                """,
                (observation_item_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _append_observation_event(
        self,
        incident_id: str,
        *,
        event_type: IncidentEventType,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str,
        event_payload: dict[str, object],
    ) -> IncidentMutationResult:
        _require_aware_datetime(occurred_at, "occurred_at")
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            incident = self._get_required_on_connection(connection, incident_id)
            replay = self._existing_event_replay(
                connection,
                event_key=event_key,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                severity=None,
                event_payload=event_payload,
                incident_id=incident_id,
                event_type=event_type,
            )
            if replay is not None:
                connection.rollback()
                return replay
            event = self._insert_event(
                connection,
                event_key=event_key,
                incident_id=incident_id,
                event_type=event_type,
                occurred_at=occurred_at,
                source_type=source_type,
                source_ref_id=source_ref_id,
                from_status=incident.incident_status,
                to_status=incident.incident_status,
                severity=incident.severity,
                event_payload=event_payload,
            )
            connection.execute(
                """
                UPDATE operational_incidents
                SET updated_at = CASE
                    WHEN julianday(updated_at) < julianday(?) THEN ?
                    ELSE updated_at
                END
                WHERE incident_id = ?
                """,
                (
                    _datetime_text(occurred_at),
                    _datetime_text(occurred_at),
                    incident_id,
                ),
            )
            updated = self._get_required_on_connection(connection, incident_id)
            connection.commit()
            return IncidentMutationResult(incident=updated, event=event)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _existing_event_replay(
        self,
        connection: sqlite3.Connection,
        *,
        event_key: str,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str,
        severity: str | None,
        event_payload: dict[str, object],
        incident_id: str | None = None,
        event_type: IncidentEventType | None = None,
        from_status: IncidentStatus | None = None,
        to_status: IncidentStatus | None = None,
    ) -> IncidentMutationResult | None:
        row = connection.execute(
            "SELECT * FROM operational_incident_events WHERE event_key = ?",
            (event_key,),
        ).fetchone()
        if row is None:
            return None
        event = _row_to_event(row)
        matches = (
            event.occurred_at == occurred_at
            and event.source_type == source_type
            and event.source_ref_id == source_ref_id
            and (severity is None or event.severity == severity)
            and _json_text(event.event_payload) == _json_text(event_payload)
            and (incident_id is None or event.incident_id == incident_id)
            and (event_type is None or event.event_type is event_type)
            and (from_status is None or event.from_status is from_status)
            and (to_status is None or event.to_status is to_status)
        )
        if not matches:
            raise IncidentIdempotencyConflictError(
                f"Incident event key reused with different content: {event_key}"
            )
        incident = self._get_required_on_connection(connection, event.incident_id)
        return IncidentMutationResult(incident=incident, event=event, replayed=True)

    @staticmethod
    def _latest_event_occurred_at_on_connection(
        connection: sqlite3.Connection,
        incident_id: str,
    ) -> datetime | None:
        row = connection.execute(
            """
            SELECT occurred_at
            FROM operational_incident_events
            WHERE incident_id = ?
            ORDER BY julianday(occurred_at) DESC, occurred_at DESC, event_id DESC
            LIMIT 1
            """,
            (incident_id,),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(str(row["occurred_at"]))

    @staticmethod
    def _get_required_on_connection(
        connection: sqlite3.Connection,
        incident_id: str,
    ) -> OperationalIncident:
        row = connection.execute(
            "SELECT * FROM operational_incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if row is None:
            raise IncidentNotFoundError(f"Incident not found: {incident_id}")
        return _row_to_incident(row)

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_key: str,
        incident_id: str,
        event_type: IncidentEventType,
        occurred_at: datetime,
        source_type: str,
        source_ref_id: str,
        from_status: IncidentStatus | None,
        to_status: IncidentStatus | None,
        severity: str,
        event_payload: dict[str, object],
    ) -> OperationalIncidentEvent:
        event_id = _stable_id("incident-event", event_key)
        connection.execute(
            """
            INSERT INTO operational_incident_events(
                event_id, event_key, incident_id, event_type, occurred_at,
                source_type, source_ref_id, from_status, to_status, severity,
                event_payload_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_key,
                incident_id,
                event_type.value,
                _datetime_text(occurred_at),
                source_type,
                source_ref_id,
                from_status.value if from_status else None,
                to_status.value if to_status else None,
                severity,
                _json_text(event_payload),
                _datetime_text(occurred_at),
            ),
        )
        row = connection.execute(
            "SELECT * FROM operational_incident_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return _row_to_event(row)


def _row_to_incident(row: sqlite3.Row) -> OperationalIncident:
    return OperationalIncident(
        incident_id=str(row["incident_id"]),
        dedupe_key=str(row["dedupe_key"]),
        category=IncidentCategory(str(row["category"])),
        source_type=str(row["source_type"]),
        source_ref_id=str(row["source_ref_id"]),
        severity=str(row["severity"]),
        incident_status=IncidentStatus(str(row["incident_status"])),
        blocks_finalization=bool(row["blocks_finalization"]),
        platform_name=str(row["platform_name"])
        if row["platform_name"] is not None
        else None,
        platform_trade_date=_text_date(row["platform_trade_date"]),
        seller_operation_date=_text_date(row["seller_operation_date"]),
        subject_type=str(row["subject_type"]),
        subject_key=str(row["subject_key"]),
        title=str(row["title"]),
        description=str(row["description"]),
        first_detected_at=_required_datetime(row["first_detected_at"]),
        last_detected_at=_required_datetime(row["last_detected_at"]),
        resolved_at=_text_datetime(row["resolved_at"]),
        occurrence_count=int(row["occurrence_count"]),
        created_at=_required_datetime(row["created_at"]),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _row_to_event(row: sqlite3.Row) -> OperationalIncidentEvent:
    return OperationalIncidentEvent(
        event_id=str(row["event_id"]),
        event_key=str(row["event_key"]),
        incident_id=str(row["incident_id"]),
        event_type=IncidentEventType(str(row["event_type"])),
        occurred_at=_required_datetime(row["occurred_at"]),
        source_type=str(row["source_type"]),
        source_ref_id=str(row["source_ref_id"]),
        from_status=IncidentStatus(str(row["from_status"]))
        if row["from_status"]
        else None,
        to_status=IncidentStatus(str(row["to_status"])) if row["to_status"] else None,
        severity=str(row["severity"]),
        event_payload=json.loads(str(row["event_payload_json"])),
        created_at=_required_datetime(row["created_at"]),
    )


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_text(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _text_date(value: object) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _text_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _required_datetime(value: object) -> datetime:
    parsed = _text_datetime(value)
    if parsed is None:
        raise ValueError("required Incident datetime is missing")
    return parsed


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_severity(value: str) -> None:
    if value not in INCIDENT_SEVERITIES:
        raise ValueError(f"invalid Incident severity: {value}")


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _inject(injector: Callable[[str], None] | None, point: str) -> None:
    if injector is not None:
        injector(point)
