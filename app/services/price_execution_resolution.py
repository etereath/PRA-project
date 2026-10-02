"""Zero-write human termination of a stopped v4 UNKNOWN one-shot."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime

from app.enums import ReviewTaskStatus
from app.exceptions import ValidationError
from app.models import ReviewTask
from app.operations_web.auth import Capability
from app.repositories.execution_continuation_repository import digest_json
from app.review_policy import PRICE_EXECUTION_REVIEW_TYPE
from app.shadowbot_contract_primitives import validate_queue_stop_fence
from app.services.listing_scan_quality import ListingScanQualityService
from app.services.notification_outbox import NotificationOutboxService


TERMINATION_CONCLUSION = 'OLD_ONE_SHOT_TERMINATED'
TERMINATION_LABEL = '旧 one-shot 已终止；历史执行结果仍为未知'
ACTIVE_ATTEMPTS = {'STARTING', 'RUNNING'}
OPEN_OPERATIONS = {'NEEDS_RECONCILIATION', 'MANUAL_REVIEW', 'MANUAL_HANDLED'}


@dataclass(frozen=True, slots=True)
class StoppedBoundary:
    stopped_at: datetime
    attempt_proofs: tuple[dict[str, str], ...]

    def as_dict(self):
        return {
            'stopped_at': self.stopped_at.isoformat(),
            'attempts': list(self.attempt_proofs),
        }


def _utc(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime):
            raise ValueError('missing timestamp')
    except ValueError as exc:
        raise ValidationError('平台证据缺少有效时间。') from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _identity(prefix, value):
    return prefix + hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


class PriceExecutionResolutionApplicationService:
    def __init__(self, authorization_service, *, quality_service=None):
        self.authorization_service = authorization_service
        self.runtime = authorization_service.runtime
        self.authorization = authorization_service.authorization
        self.clock = lambda: authorization_service.clock()
        self.quality = quality_service or ListingScanQualityService(
            self.runtime,
            clock=self.clock,
        )

    def ensure_reviews(self, batch_id, *, now):
        """Create the simple pending todo after the unique reconcile stops."""
        with closing(self.runtime.connect_read()) as connection:
            continuation = connection.execute(
                'SELECT * FROM execution_continuations WHERE batch_id = ? AND closed_at IS NULL',
                (batch_id,)).fetchone()
            rows = connection.execute(
                'SELECT i.operation_id, i.source_task_id FROM shadowbot_commit_batch_items i '
                'JOIN shadowbot_commit_batches b ON b.batch_id = i.batch_id '
                'JOIN shadowbot_operations o ON o.operation_id = i.operation_id '
                "WHERE i.batch_id = ? AND b.status = 'UNKNOWN' "
                "AND o.status IN ('NEEDS_RECONCILIATION', 'MANUAL_REVIEW', 'MANUAL_HANDLED')",
                (batch_id,)).fetchall()
        if continuation is None:
            return
        for row in rows:
            review_id = _identity('PRICE-REVIEW-', row['operation_id'])
            if self.runtime.get_review_task(review_id) is not None:
                self._park_stopped_operation(review_id, now=now)
                continue
            task = self.runtime.get_task(row['source_task_id'])
            review = ReviewTask(review_task_id=review_id, trade_date=task.trade_date,
                scope_type='sku', scope_key=task.internal_sku,
                dedupe_key='price_execution_unknown:' + row['operation_id'],
                source_task_id=task.task_id, review_type=PRICE_EXECUTION_REVIEW_TYPE,
                review_status=ReviewTaskStatus.PENDING, internal_sku=task.internal_sku,
                platform_name=task.platform_name,
                reason='改价结果无法自动确认；管理员可在停止证明和最新合格观察齐备后零写终止旧 one-shot。',
                review_payload={'batch_id': batch_id, 'operation_id': row['operation_id'],
                    'historical_side_effect': 'UNKNOWN'},
                required_by=None, created_at=now, updated_at=now)
            outbox = NotificationOutboxService(self.runtime, clock=lambda: now)
            candidate, log = outbox.build_review_notification_candidate(review, event_version='price-initial',
                message='改价执行需要管理员核验。系统会选择停止边界之后最新的合格平台观察；不会重放旧写入。')
            self.runtime.insert_review_task_with_notification_outbox(review, candidate, compatibility_log=log)
            self._park_stopped_operation(review_id, now=now)

    def _park_stopped_operation(self, review_id, *, now):
        # Existing MANUAL_REVIEW / REVIEW_BLOCKED keep this SKU unwritable while
        # allowing the existing read-only Automation scan to gather fresh facts.
        with closing(self.runtime.connect_write()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            review = self._review(connection, review_id)
            if review['review_status'] != 'pending':
                return
            try:
                context = self._context(connection, review)
                boundary = self._stopped_boundary(connection, context)
            except ValidationError:
                return
            changed = connection.execute("UPDATE shadowbot_write_locks SET status = 'REVIEW_BLOCKED', updated_at = ? "
                "WHERE operation_id = ? AND item_execution_attempt_id = ? AND write_identity_key = ? "
                "AND status IN ('UNKNOWN', 'REVIEW_BLOCKED')",
                (now.isoformat(), context['operation_id'], context['item_execution_attempt_id'], context['write_identity_key'])).rowcount
            if changed != 1:
                return
            connection.execute("UPDATE shadowbot_operations SET status = 'MANUAL_REVIEW', updated_at = ? WHERE operation_id = ?",
                (now.isoformat(), context['operation_id']))
            payload = json.loads(review['review_payload_json'])
            payload['execution_stopped_at'] = boundary.stopped_at.isoformat()
            payload['stop_proof'] = boundary.as_dict()
            connection.execute('UPDATE review_tasks SET review_payload_json = ? WHERE review_task_id = ?',
                (json.dumps(payload, ensure_ascii=False), review_id))

    def for_task(self, task_id):
        with closing(self.runtime.connect_read()) as connection:
            review = connection.execute(
                'SELECT * FROM review_tasks WHERE source_task_id = ? AND review_type = ? '
                'ORDER BY created_at DESC LIMIT 1', (task_id, PRICE_EXECUTION_REVIEW_TYPE)).fetchone()
            if review is None:
                return None
            model = dict(review)
            model['payload'] = json.loads(review['review_payload_json'])
            model['resolution'] = json.loads(review['resolution_payload_json'])
            model['evidence'] = None
            model['evidence_error'] = ''
            if review['review_status'] != 'pending':
                history = connection.execute('SELECT metadata_json FROM task_status_history WHERE history_id = ?',
                    (model['resolution'].get('history_id', ''),)).fetchone()
                if history:
                    model['resolution'] = json.loads(history[0])
                return model
            try:
                context = self._context(connection, review)
                boundary = self._stopped_boundary(connection, context)
                model['evidence'] = self._latest_evidence(
                    context,
                    boundary.stopped_at,
                    _utc(self.clock()),
                )
            except ValidationError as exc:
                model['evidence_error'] = str(exc)
            return model

    def resolve(self, principal, *, review_id, idempotency_key, note=''):
        self._require(principal)
        if not idempotency_key or len(idempotency_key) > 160 or len(note) > 1000:
            raise ValidationError('缺少有效幂等键；备注不能代替系统停止证明和合格观察。')
        request_hash = digest_json({'review_id': review_id,
            'idempotency_key': idempotency_key, 'principal_subject': principal.subject, 'note': note})
        with closing(self.runtime.connect_write()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            current = _utc(self.clock())
            review = self._review(connection, review_id)
            history_id = _identity('PRICE-RESOLVED-', json.loads(review['review_payload_json'])['operation_id'])
            previous = connection.execute('SELECT metadata_json FROM task_status_history WHERE history_id = ?',
                                          (history_id,)).fetchone()
            if previous:
                saved = json.loads(previous[0])
                if saved['request_hash'] != request_hash:
                    raise ValidationError('该操作已经收口；本次请求与原处置不一致。')
                return saved
            if review['review_status'] != 'pending':
                raise ValidationError('复核已结束。')
            context = self._context(connection, review)
            boundary = self._stopped_boundary(connection, context)
            evidence = self._latest_evidence(
                context,
                boundary.stopped_at,
                current,
            )
            result = {'version': 2, 'review_id': review_id, 'operation_id': context['operation_id'],
                'batch_id': context['batch_id'], 'task_id': context['source_task_id'],
                'principal_subject': principal.subject, 'resolved_at': current.isoformat(),
                'conclusion': TERMINATION_CONCLUSION, 'conclusion_label': TERMINATION_LABEL,
                'evidence': evidence, 'request_hash': request_hash, 'note': note,
                'historical_side_effect': 'UNKNOWN',
                'stopped_boundary': boundary.stopped_at.isoformat(),
                'stop_proof': boundary.as_dict()}
            self._history(connection, history_id, context['source_task_id'], principal.subject, current,
                          'price_execution_human_resolved', result, terminal=True)
            connection.execute("UPDATE tasks SET task_status = 'skipped', updated_at = ?, result_message = ? WHERE task_id = ?",
                (current.isoformat(), TERMINATION_LABEL, context['source_task_id']))
            connection.execute("UPDATE shadowbot_operations SET status = 'MANUAL_HANDLED', resolution_status = 'MANUAL_HANDLED', "
                "resolved_by = ?, resolved_at = ?, lock_owner = '', updated_at = ? WHERE operation_id = ?",
                (principal.subject, current.isoformat(), current.isoformat(), context['operation_id']))
            released = connection.execute("UPDATE shadowbot_write_locks SET status = 'RELEASED', released_at = ?, updated_at = ? "
                "WHERE operation_id = ? AND item_execution_attempt_id = ? AND write_identity_key = ? "
                "AND status IN ('UNKNOWN', 'REVIEW_BLOCKED')",
                (current.isoformat(), current.isoformat(), context['operation_id'],
                 context['item_execution_attempt_id'], context['write_identity_key'])).rowcount
            if released != 1:
                raise ValidationError('写锁归属已变化，未保存部分收口结果。')
            connection.execute("UPDATE review_tasks SET review_status = 'cancelled', resolved_by = ?, resolved_at = ?, "
                "updated_at = ?, resolution_note = ?, resolution_payload_json = ? WHERE review_task_id = ?",
                (principal.subject, current.isoformat(), current.isoformat(), note,
                 json.dumps({'history_id': history_id, 'conclusion': TERMINATION_CONCLUSION}, ensure_ascii=False), review_id))
            self.runtime._cancel_review_outbox_on_connection(connection, review_id, changed_at=current)
            connection.execute('UPDATE review_tokens SET revoked_at = ? WHERE review_task_id = ? AND revoked_at IS NULL',
                               (current.isoformat(), review_id))
            operations = connection.execute('SELECT o.operation_id, o.status, t.task_status FROM shadowbot_operations o '
                'JOIN tasks t ON t.task_id = o.task_id '
                'JOIN shadowbot_commit_batch_items i ON i.operation_id = o.operation_id WHERE i.batch_id = ?',
                (context['batch_id'],)).fetchall()
            if all((row['status'] in {'VERIFIED', 'NOT_APPLIED'} and row['task_status'] in {'success', 'skipped', 'failed', 'expired'})
                   or self.is_resolved(connection, row['operation_id'])
                   for row in operations):
                connection.execute("UPDATE execution_continuations SET closed_at = ?, outcome = 'HUMAN_RESOLVED', "
                    "message = '已按平台证据人工终止旧决定；原未知执行历史保留。' WHERE batch_id = ? AND closed_at IS NULL",
                    (current.isoformat(), context['batch_id']))
            return result

    @staticmethod
    def is_resolved(connection, operation_id):
        return connection.execute(
            "SELECT 1 FROM task_status_history h JOIN shadowbot_operations o ON o.task_id = h.task_id "
            "JOIN tasks t ON t.task_id = o.task_id JOIN shadowbot_write_locks w ON w.operation_id = o.operation_id "
            "WHERE o.operation_id = ? AND o.resolution_status = 'MANUAL_HANDLED' AND o.status = 'MANUAL_HANDLED' "
            "AND h.history_id = ? AND h.reason = 'price_execution_human_resolved' "
            "AND t.task_status = 'skipped' AND w.status = 'RELEASED'",
            (operation_id, _identity('PRICE-RESOLVED-', operation_id))).fetchone() is not None

    def _require(self, principal):
        if not self.authorization.allows(principal, Capability.HANDLE_REVIEW):
            raise ValidationError('当前账号没有人工复核权限。')

    @staticmethod
    def _review(connection, review_id):
        row = connection.execute('SELECT * FROM review_tasks WHERE review_task_id = ? AND review_type = ?',
                                 (review_id, PRICE_EXECUTION_REVIEW_TYPE)).fetchone()
        if row is None:
            raise ValidationError('人工改价复核不存在。')
        return row

    def _context(self, connection, review):
        payload = json.loads(review['review_payload_json'])
        row = connection.execute(
            'SELECT i.*, b.platform_name, b.status AS batch_status, b.execution_attempt_id AS batch_attempt_id, '
            'c.closed_at, c.envelope_json, c.envelope_sha256, o.status AS operation_status, '
            'o.lock_owner AS operation_lock_owner, t.task_status '
            'FROM shadowbot_commit_batch_items i JOIN shadowbot_commit_batches b ON b.batch_id = i.batch_id '
            'JOIN execution_continuations c ON c.batch_id = b.batch_id '
            'JOIN shadowbot_operations o ON o.operation_id = i.operation_id '
            'JOIN tasks t ON t.task_id = i.source_task_id WHERE i.operation_id = ? AND i.batch_id = ?',
            (payload['operation_id'], payload['batch_id'])).fetchone()
        if (row is None or row['closed_at'] or row['batch_status'] != 'UNKNOWN'
                or row['operation_status'] not in OPEN_OPERATIONS or row['task_status'] != 'manual_review'
                or row['source_task_id'] != review['source_task_id']):
            raise ValidationError('旧执行状态已变化，不能从此入口收口。')
        envelope = json.loads(row['envelope_json'])
        if digest_json(envelope) != row['envelope_sha256']:
            raise ValidationError('旧授权交接证据完整性校验失败。')
        return row

    def _stopped_boundary(self, connection, context):
        attempts = connection.execute('SELECT * FROM shadowbot_execution_attempts WHERE operation_id = ?',
                                      (context['operation_id'],)).fetchall()
        reconciles = [a for a in attempts if a['execution_mode'] == 'RECONCILE']
        commits = [a for a in attempts if a['execution_mode'] == 'COMMIT']
        if len(reconciles) != 1 or len(commits) != 1 or commits[0]['execution_attempt_id'] != context['item_execution_attempt_id']:
            raise ValidationError('须先由原执行链完成唯一对账，不能创建第二次对账。')
        expected_reconcile = 'RECONCILE-' + hashlib.sha256(
            context['item_execution_attempt_id'].encode('utf-8')).hexdigest()[:20]
        if reconciles[0]['execution_attempt_id'] != expected_reconcile:
            raise ValidationError('对账与原执行不匹配。')
        if reconciles[0]['side_effect_state'] in {'VERIFIED', 'NOT_APPLIED'}:
            raise ValidationError('唯一对账已有明确结果，请先由 Importer 完成结果入库。')
        reconcile_result = json.loads(reconciles[0]['raw_output_json'])
        if (reconcile_result.get('source_execution_attempt_id') != context['item_execution_attempt_id']
                or (not self._terminal_result(reconcile_result)
                    and not self._not_published(reconciles[0], reconcile_result)
                    and not self._queue_fence(reconciles[0], reconcile_result, context))):
            raise ValidationError('尚无唯一对账已返回的回执；超时或租约过期不能替代停止证据。')
        commit_receipt = connection.execute(
            'SELECT 1 FROM shadowbot_commit_result_receipts '
            'WHERE batch_id = ? AND execution_attempt_id = ?',
            (context['batch_id'], context['batch_attempt_id']),
        ).fetchone()
        commit_raw = json.loads(commits[0]['raw_output_json'])
        if commit_receipt is None and not self._not_published(commits[0], commit_raw):
            raise ValidationError('旧 COMMIT 尚无持久结果回执，不能证明其请求已消费。')
        times = []
        for attempt in attempts:
            raw = json.loads(attempt['raw_output_json'])
            if (attempt['status'] in ACTIVE_ATTEMPTS or not attempt['ended_at']
                    or raw.get('lease', {}).get('active') is True):
                raise ValidationError('旧执行或对账仍在运行，须先停止或隔离并保留证据。')
            times.append(_utc(attempt['ended_at']))
            fence = self._queue_fence(attempt, raw, context)
            if fence is not None:
                times.append(_utc(fence['fenced_at']))
        if context['operation_lock_owner']:
            raise ValidationError('旧执行仍有活动租约 owner，不能人工终止。')
        proofs = []
        for attempt in attempts:
            raw = json.loads(attempt['raw_output_json'])
            fence = self._queue_fence(attempt, raw, context)
            if ((attempt['execution_mode'] == 'COMMIT' and commit_receipt is not None)
                    or self._terminal_result(raw)):
                proof_type = 'IMPORTED_RESULT'
            elif self._not_published(attempt, raw):
                proof_type = 'NOT_PUBLISHED'
            elif fence is not None:
                proof_type = 'QUEUE_REQUEST_QUARANTINED'
            else:
                raise ValidationError('STOP_PROOF_INCOMPLETE')
            proofs.append({
                'execution_attempt_id': attempt['execution_attempt_id'],
                'execution_mode': attempt['execution_mode'],
                'proof_type': proof_type,
                'ended_at': _utc(attempt['ended_at']).isoformat(),
                **({'proof_sha256': fence['proof_sha256']} if fence else {}),
            })
        return StoppedBoundary(max(times), tuple(proofs))

    @staticmethod
    def _terminal_result(raw):
        return bool(
            raw.get('queue_phase') == 'RESULT_WRITTEN'
            and raw.get('result_file_sha256')
            and raw.get('result_id')
        )

    @staticmethod
    def _not_published(attempt, raw):
        """An explicit start boundary is stronger than a timeout or queue scan."""
        return bool(
            attempt['status'] == 'START_FAILED'
            and raw.get('published') is False
            and not attempt['shadowbot_run_id']
            and not attempt['request_file_sha256']
            and not attempt['queue_request_path']
        )

    @staticmethod
    def _queue_fence(attempt, raw, context):
        proof = raw.get('queue_stop_fence')
        if proof is None or attempt['execution_mode'] != 'RECONCILE':
            return None
        try:
            return validate_queue_stop_fence(
                proof,
                execution_attempt_id=str(attempt['execution_attempt_id']),
                operation_id=str(context['operation_id']),
                source_execution_attempt_id=str(context['item_execution_attempt_id']),
                instruction_hash=str(attempt['instruction_hash']),
            )
        except ValueError:
            return None

    def _latest_evidence(self, context, boundary, now):
        quality = self.quality.latest(
            platform_name=context['platform_name'],
            internal_sku=context['internal_sku'],
        )
        if not quality.operating_fact_qualified or quality.observed_price is None:
            reasons = ', '.join(quality.fact_reason_codes) or 'NO_QUALIFIED_OBSERVATION'
            raise ValidationError('停止边界之后尚无最新合格平台观察：' + reasons)
        observed = _utc(quality.observed_at)
        completed = _utc(quality.scan_completed_at)
        if observed <= boundary or completed <= boundary or completed > now:
            raise ValidationError('最新合格平台观察必须严格晚于旧执行停止边界。')
        evidence = quality.as_dict()
        evidence.update(
            operation_id=context['operation_id'],
            stopped_boundary=boundary.isoformat(),
        )
        return evidence

    @staticmethod
    def _history(connection, history_id, task_id, actor, now, reason, metadata, *, terminal):
        status = connection.execute('SELECT task_status FROM tasks WHERE task_id = ?', (task_id,)).fetchone()[0]
        connection.execute('INSERT INTO task_status_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (history_id, task_id, status, 'skipped' if terminal else status, actor, now.isoformat(),
             reason, json.dumps(metadata, ensure_ascii=False, sort_keys=True)))
