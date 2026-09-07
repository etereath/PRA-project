"""Durable authorization attached to the existing v4 batch, never to PREPARED alone."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal

from app.exceptions import ValidationError


SCHEMA_V18_SQL = (
    """
    CREATE TABLE IF NOT EXISTS execution_continuations (
        batch_id TEXT PRIMARY KEY REFERENCES shadowbot_commit_batches(batch_id),
        principal_subject TEXT NOT NULL,
        idempotency_hash TEXT NOT NULL,
        envelope_sha256 TEXT NOT NULL,
        envelope_json TEXT NOT NULL CHECK(json_valid(envelope_json)),
        accepted_at TEXT NOT NULL,
        closed_at TEXT,
        outcome TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT '',
        UNIQUE(principal_subject, idempotency_hash)
    )
    """,
    """CREATE INDEX IF NOT EXISTS ix_execution_continuations_open
       ON execution_continuations(closed_at, accepted_at)""",
    """
    CREATE TRIGGER IF NOT EXISTS execution_continuations_authorization_immutable
    BEFORE UPDATE OF batch_id, principal_subject, idempotency_hash,
        envelope_sha256, envelope_json, accepted_at ON execution_continuations
    BEGIN SELECT RAISE(ABORT, 'execution authorization is immutable'); END
    """,
    """CREATE TRIGGER IF NOT EXISTS execution_continuations_no_delete
       BEFORE DELETE ON execution_continuations
       BEGIN SELECT RAISE(ABORT, 'execution authorization is immutable'); END""",
)


def digest_json(value: object) -> str:
    return 'sha256:' + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


class ExecutionContinuationRepository:
    def __init__(self, runtime):
        self.runtime = runtime

    def replay(self, subject: str, key: str):
        with closing(self.runtime.connect_read()) as connection:
            return connection.execute(
                'SELECT * FROM execution_continuations '
                'WHERE principal_subject = ? AND idempotency_hash = ?',
                (subject, digest_json(key)),
            ).fetchone()

    def accept(self, envelope: dict, *, now: datetime) -> None:
        """The acceptance record and AUTH history commit or roll back together."""
        batch_id = envelope['batch_id']
        with closing(self.runtime.connect_write()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            batch = connection.execute(
                'SELECT * FROM shadowbot_commit_batches WHERE batch_id = ?', (batch_id,),
            ).fetchone()
            if (batch is None or batch['status'] != 'PREPARED'
                    or batch['manifest_sha256'] != envelope['manifest']['manifest_sha256']):
                raise ValidationError('Authorization batch changed before acceptance')
            if datetime.fromisoformat(envelope['expires_at']) <= now:
                raise ValidationError('Authorization expired before acceptance')
            for fact in envelope['facts']['items']:
                task = connection.execute(
                    'SELECT * FROM tasks WHERE task_id = ?', (fact['task_id'],),
                ).fetchone()
                if (task is None or task['task_status'] != 'pending'
                        or task['updated_at'] != fact['task_updated_at']):
                    raise ValidationError('Task changed before acceptance')
                active = connection.execute(
                    """SELECT 1 FROM execution_continuations c
                       JOIN shadowbot_commit_batch_items i ON i.batch_id = c.batch_id
                       WHERE i.source_task_id = ? AND c.closed_at IS NULL""",
                    (fact['task_id'],),
                ).fetchone()
                if active:
                    raise ValidationError('Task already has a durable authorization')
            connection.execute(
                """INSERT INTO execution_continuations(
                    batch_id, principal_subject, idempotency_hash,
                    envelope_sha256, envelope_json, accepted_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (batch_id, envelope['principal_subject'], envelope['idempotency_hash'],
                 digest_json(envelope), json.dumps(envelope, ensure_ascii=False, sort_keys=True),
                 now.isoformat()),
            )
            for task_id in envelope['task_ids']:
                connection.execute(
                    """INSERT INTO task_status_history(
                       history_id, task_id, from_status, to_status, changed_by,
                       changed_at, reason, metadata_json
                    ) VALUES (?, ?, 'pending', 'pending', ?, ?,
                              'execution_submission_authorized', ?)""",
                    ('AUTH-' + batch_id + '-' + task_id, task_id,
                     envelope['principal_subject'], now.isoformat(), json.dumps({
                         'batch_id': batch_id, 'confirmation_digest': envelope['confirmation_digest'],
                         'capability': envelope['capability'],
                         'resolution_only': bool(envelope.get('resolution_only')),
                     }, ensure_ascii=False)),
                )

    def active(self):
        with closing(self.runtime.connect_read()) as connection:
            return connection.execute(
                'SELECT * FROM execution_continuations WHERE closed_at IS NULL '
                'ORDER BY accepted_at, batch_id',
            ).fetchall()

    def note(self, batch_id: str, outcome: str, message: str, *, now: datetime,
             close: bool = False, task_status: str | None = None, evidence: dict | None = None) -> None:
        with closing(self.runtime.connect_write()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if outcome == 'ALREADY_APPLIED':
                current = connection.execute(
                    'SELECT closed_at FROM execution_continuations WHERE batch_id = ?', (batch_id,),
                ).fetchone()
                if current is None or current['closed_at'] is not None:
                    return
                self._validate_no_write_closure(connection, batch_id, evidence or {})
            changed = connection.execute(
                'UPDATE execution_continuations SET outcome = ?, message = ?, closed_at = ? '
                'WHERE batch_id = ? AND closed_at IS NULL AND '
                '(outcome <> ? OR message <> ? OR ? IS NOT NULL)',
                (outcome, message, now.isoformat() if close else None, batch_id,
                 outcome, message, now.isoformat() if close else None),
            ).rowcount
            if changed and task_status:
                for row in connection.execute(
                    'SELECT t.task_id, t.task_status FROM tasks t '
                    'JOIN shadowbot_commit_batch_items i ON i.source_task_id = t.task_id '
                    'WHERE i.batch_id = ?', (batch_id,),
                ).fetchall():
                    if row['task_status'] != 'pending':
                        continue
                    connection.execute(
                        'UPDATE tasks SET task_status = ?, updated_at = ? WHERE task_id = ?',
                        (task_status, now.isoformat(), row['task_id']),
                    )
                    connection.execute(
                        """INSERT INTO task_status_history VALUES
                        (?, ?, ?, ?, 'execution_coordinator', ?, ?, ?)""",
                        ('CONT-' + batch_id + '-' + row['task_id'] + '-' + outcome,
                         row['task_id'], row['task_status'], task_status, now.isoformat(),
                         outcome, json.dumps({'batch_id': batch_id, **(evidence or {})}, ensure_ascii=False)),
                    )

    @staticmethod
    def _validate_no_write_closure(connection, batch_id, evidence):
        """The observation cannot change between validation and atomic closure."""
        items = connection.execute(
            'SELECT i.source_task_id, i.write_identity_key, b.status AS batch_status '
            'FROM shadowbot_commit_batch_items i JOIN shadowbot_commit_batches b ON b.batch_id = i.batch_id '
            'WHERE i.batch_id = ?', (batch_id,),
        ).fetchall()
        facts = evidence.get('platform_observation', [])
        if not items or {i['source_task_id'] for i in items} != {f['task_id'] for f in facts}:
            raise ValidationError('No-write closure scope changed')
        for item in items:
            if item['batch_status'] != 'PREPARED' or connection.execute(
                    "SELECT 1 FROM shadowbot_write_locks WHERE write_identity_key = ? AND status <> 'RELEASED'",
                    (item['write_identity_key'],)).fetchone():
                raise ValidationError('No-write closure is blocked by published work')
        for fact in facts:
            row = connection.execute(
                'SELECT t.task_status, t.updated_at AS task_updated_at, l.current_price, l.online_status, '
                'l.price_observed_at, l.price_source_attempt_id FROM tasks t JOIN listing_status l '
                'ON l.platform_name = t.platform_name AND l.internal_sku = t.internal_sku WHERE t.task_id = ?',
                (fact['task_id'],),
            ).fetchone()
            if (row is None or row['task_status'] != 'pending' or row['task_updated_at'] != fact['task_updated_at']
                    or Decimal(row['current_price']) != Decimal(fact['target_price'])
                    or row['online_status'] != fact['listing_status']
                    or row['price_source_attempt_id'] != fact['listing_price_source_attempt_id']
                    or not row['price_observed_at']
                    or _utc_timestamp(row['price_observed_at']) != _utc_timestamp(fact['listing_price_observed_at'])):
                raise ValidationError('Platform observation changed before no-write closure')


def _utc_timestamp(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
