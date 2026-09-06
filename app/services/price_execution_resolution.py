"""Evidence-bound human closure of a v4 UNKNOWN; no execution or retry authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.enums import ReviewTaskStatus
from app.exceptions import ValidationError
from app.models import ReviewTask
from app.operations_web.auth import Capability
from app.automation_ui_channel import has_active_automation_ui_run
from app.repositories.execution_continuation_repository import digest_json
from app.review_policy import PRICE_EXECUTION_REVIEW_TYPE
from app.services.notification_outbox import NotificationOutboxService
from app.services.shadowbot_queue import ShadowBotQueuePaths


CONCLUSIONS = {
    'CURRENT_TARGET_MET': '当前目标已满足，终止旧决定',
    'STOP_OLD_DECISION': '当前目标未满足，终止旧决定，后续重新决定',
}
ACTIVE_ATTEMPTS = {'STARTING', 'RUNNING'}
OPEN_OPERATIONS = {'NEEDS_RECONCILIATION', 'MANUAL_REVIEW', 'MANUAL_HANDLED'}
SHA256 = re.compile(r'^(?:sha256:)?[0-9a-f]{64}$')


def _utc(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime):
            raise ValueError('missing timestamp')
    except ValueError as exc:
        raise ValidationError('平台证据缺少有效时间。') from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _price(value):
    try:
        price = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError('平台证据缺少有效价格。') from exc
    if not price.is_finite() or price <= 0:
        raise ValidationError('平台证据缺少有效价格。')
    return price


def _identity(prefix, value):
    return prefix + hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def _interval():
    value = int(os.environ.get('PRA_PRICE_REVIEW_REMINDER_MINUTES', '30'))
    if not 1 <= value <= 1440:
        raise ValidationError('人工复核提醒间隔须为 1 至 1440 分钟。')
    return timedelta(minutes=value)


def renew_price_execution_review(repository, review, *, now):
    """Persist a reminder and optional escalation without expiring or reassigning work."""
    current = _utc(now)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM review_tasks WHERE review_task_id = ?',
                                 (review.review_task_id,)).fetchone()
        if (row is None or row['review_status'] != 'pending' or not row['required_by']
                or _utc(row['required_by']) > current):
            return False
        payload = json.loads(row['review_payload_json'])
        count = int(payload.get('reminder_count', 0)) + 1
        escalation = os.environ.get('PRA_PRICE_REVIEW_ESCALATION_SUBJECT', '').strip()
        payload.update(reminder_count=count, last_reminded_at=current.isoformat(),
                       escalation_subject=escalation if count >= 2 else '')
        owner = str(payload['owner_subject'])
        message = f'人工改价复核已逾期，当前负责人：{owner}。请登录经营管理查看任务并按平台证据收口。'
        if count >= 2:
            message += f' 已升级通知：{escalation or "运营管理员"}；确认接手前原负责人继续负责。'
        next_at = current + _interval()
        review.review_payload = payload
        review.required_by = next_at
        review.updated_at = current
        service = NotificationOutboxService(repository, clock=lambda: current)
        candidate, log = service.build_review_notification_candidate(
            review, event_version=f'price-reminder-{count}', message=message)
        connection.execute(
            'UPDATE review_tasks SET review_payload_json = ?, required_by = ?, updated_at = ? '
            'WHERE review_task_id = ?',
            (json.dumps(payload, ensure_ascii=False), next_at.isoformat(), current.isoformat(), review.review_task_id))
        repository._cancel_review_outbox_on_connection(connection, review.review_task_id, changed_at=current)
        if repository._insert_notification_outbox_on_connection(connection, candidate) != 1:
            raise ValidationError('复核提醒未完整保存。')
        # The standard notification log is a compatibility projection of the outbox.
        from app.repositories.sqlite_runtime_repository import _notification_log_to_row
        fields = _notification_log_to_row(log)
        connection.execute('INSERT INTO notification_logs (' + ','.join(fields) + ') VALUES ('
                           + ','.join(':' + k for k in fields) + ')', fields)
        return True


class PriceExecutionResolutionApplicationService:
    def __init__(self, authorization_service):
        self.authorization_service = authorization_service
        self.runtime = authorization_service.runtime
        self.authorization = authorization_service.authorization
        self.paths = ShadowBotQueuePaths(authorization_service.queue_root)
        self.clock = lambda: authorization_service.clock()

    def ensure_reviews(self, batch_id, *, now):
        """A restarted owner recreates a missing handoff, never a second execution."""
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
            required_by = now + _interval()
            review = ReviewTask(review_task_id=review_id, trade_date=task.trade_date,
                scope_type='sku', scope_key=task.internal_sku,
                dedupe_key='price_execution_unknown:' + row['operation_id'],
                source_task_id=task.task_id, review_type=PRICE_EXECUTION_REVIEW_TYPE,
                review_status=ReviewTaskStatus.PENDING, internal_sku=task.internal_sku,
                platform_name=task.platform_name,
                reason='改价结果无法自动确认，请凭平台核验记录终止旧决定；不再重试旧写入。',
                review_payload={'batch_id': batch_id, 'operation_id': row['operation_id'],
                    'owner_subject': continuation['principal_subject'], 'reminder_count': 0,
                    'initial_required_by': required_by.isoformat()},
                required_by=required_by, created_at=now, updated_at=now)
            outbox = NotificationOutboxService(self.runtime, clock=lambda: now)
            candidate, log = outbox.build_review_notification_candidate(review, event_version='price-initial',
                message=f'改价执行需要人工核验，负责人：{continuation["principal_subject"]}。请登录经营管理查看任务。')
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
            payload['execution_stopped_at'] = boundary.isoformat()
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
            model['evidence'] = []
            model['evidence_error'] = ''
            model['overdue'] = _utc(model['payload']['initial_required_by']) <= _utc(self.clock())
            if review['review_status'] != 'pending':
                history = connection.execute('SELECT metadata_json FROM task_status_history WHERE history_id = ?',
                    (model['resolution'].get('history_id', ''),)).fetchone()
                if history:
                    model['resolution'] = json.loads(history[0])
                return model
            try:
                context = self._context(connection, review)
                boundary = self._stopped_boundary(connection, context)
                rows = connection.execute(
                    'SELECT si.snapshot_item_id FROM listing_sync_snapshot_items si '
                    'JOIN listing_sync_snapshots s ON s.snapshot_id = si.snapshot_id '
                    'WHERE si.internal_sku = ? AND s.platform_name = ? '
                    'ORDER BY s.scan_completed_at DESC LIMIT 25',
                    (context['internal_sku'], context['platform_name'])).fetchall()
                for row in rows:
                    try:
                        evidence = self._evidence(connection, context, row[0], boundary, _utc(self.clock()))
                        model['evidence'].append(evidence)
                    except ValidationError:
                        continue
                if not model['evidence']:
                    model['evidence_error'] = '尚无合格的新鲜平台核验记录。请使用现有平台状态扫描取得核验快照后刷新。'
            except ValidationError as exc:
                model['evidence_error'] = str(exc)
            return model

    def claim(self, principal, *, review_id):
        self._require(principal)
        current = _utc(self.clock())
        with closing(self.runtime.connect_write()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            review = self._review(connection, review_id)
            if review['review_status'] != 'pending':
                raise ValidationError('复核已收口，无须接手。')
            payload = json.loads(review['review_payload_json'])
            previous = payload['owner_subject']
            if previous == principal.subject:
                return
            payload['owner_subject'] = principal.subject
            payload['owner_accepted_at'] = current.isoformat()
            connection.execute('UPDATE review_tasks SET review_payload_json = ?, updated_at = ? WHERE review_task_id = ?',
                (json.dumps(payload, ensure_ascii=False), current.isoformat(), review_id))
            from uuid import uuid4
            self._history(connection, 'PRICE-CLAIM-' + uuid4().hex, review['source_task_id'],
                principal.subject, current, 'price_execution_review_claimed',
                {'review_id': review_id, 'previous_owner': previous, 'owner_subject': principal.subject},
                terminal=False)

    def resolve(self, principal, *, review_id, evidence_id, evidence_digest, conclusion, idempotency_key, note=''):
        self._require(principal)
        if (conclusion not in CONCLUSIONS or not idempotency_key or len(idempotency_key) > 160
                or not evidence_id or not evidence_digest or len(note) > 1000):
            raise ValidationError('请选择平台核验记录和处理结论；备注不能代替证据。')
        request_hash = digest_json({'review_id': review_id, 'evidence_id': evidence_id,
            'evidence_digest': evidence_digest, 'conclusion': conclusion,
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
            owner = json.loads(review['review_payload_json'])['owner_subject']
            if owner != principal.subject:
                raise ValidationError('请先确认接手此复核，再提交人工结论。')
            context = self._context(connection, review)
            boundary = self._stopped_boundary(connection, context)
            evidence = self._evidence(connection, context, evidence_id, boundary, current)
            if evidence['digest'] != evidence_digest:
                raise ValidationError('平台证据或处理范围已变化，请刷新并重新确认。')
            expected = 'CURRENT_TARGET_MET' if Decimal(evidence['observed_price']) == Decimal(context['target_price']) else 'STOP_OLD_DECISION'
            if conclusion != expected:
                raise ValidationError('所选结论与平台核验价格不一致。')
            result = {'version': 1, 'review_id': review_id, 'operation_id': context['operation_id'],
                'batch_id': context['batch_id'], 'task_id': context['source_task_id'],
                'principal_subject': principal.subject, 'resolved_at': current.isoformat(),
                'conclusion': conclusion, 'conclusion_label': CONCLUSIONS[conclusion],
                'evidence': evidence, 'request_hash': request_hash, 'note': note,
                'historical_side_effect': 'UNKNOWN', 'stopped_boundary': boundary.isoformat()}
            self._history(connection, history_id, context['source_task_id'], principal.subject, current,
                          'price_execution_human_resolved', result, terminal=True)
            connection.execute("UPDATE tasks SET task_status = 'skipped', updated_at = ?, result_message = ? WHERE task_id = ?",
                (current.isoformat(), CONCLUSIONS[conclusion], context['source_task_id']))
            connection.execute("UPDATE shadowbot_operations SET status = 'MANUAL_HANDLED', resolution_status = 'MANUAL_HANDLED', "
                "resolved_by = ?, resolved_at = ?, lock_owner = '', updated_at = ? WHERE operation_id = ?",
                (principal.subject, current.isoformat(), current.isoformat(), context['operation_id']))
            released = connection.execute("UPDATE shadowbot_write_locks SET status = 'RELEASED', released_at = ?, updated_at = ? "
                "WHERE operation_id = ? AND item_execution_attempt_id = ? AND write_identity_key = ? "
                "AND status IN ('UNKNOWN', 'REVIEW_BLOCKED', 'ACTIVE')",
                (current.isoformat(), current.isoformat(), context['operation_id'],
                 context['item_execution_attempt_id'], context['write_identity_key'])).rowcount
            if released != 1:
                raise ValidationError('写锁归属已变化，未保存部分收口结果。')
            connection.execute("UPDATE review_tasks SET review_status = 'cancelled', resolved_by = ?, resolved_at = ?, "
                "updated_at = ?, resolution_note = ?, resolution_payload_json = ? WHERE review_task_id = ?",
                (principal.subject, current.isoformat(), current.isoformat(), note,
                 json.dumps({'history_id': history_id, 'conclusion': conclusion}, ensure_ascii=False), review_id))
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
            'c.closed_at, c.envelope_json, c.envelope_sha256, o.status AS operation_status, t.task_status '
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
        if (digest_json(envelope) != row['envelope_sha256']
                or envelope['context'] != self.authorization_service.continuation_context()):
            raise ValidationError('请恢复原执行配置并核对授权证据后处理。')
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
                or reconcile_result.get('queue_phase') != 'RESULT_WRITTEN'
                or not SHA256.fullmatch(str(reconcile_result.get('result_file_sha256') or ''))
                or not reconcile_result.get('result_id')
                or reconcile_result.get('error_code') == 'WORKER_INTERRUPTED'):
            raise ValidationError('尚无唯一对账已返回的回执；超时或租约过期不能替代停止证据。')
        times = []
        for attempt in attempts:
            raw = json.loads(attempt['raw_output_json'])
            if (attempt['status'] in ACTIVE_ATTEMPTS or not attempt['ended_at']
                    or raw.get('lease', {}).get('active') is True):
                raise ValidationError('旧执行或对账仍在运行，须先停止或隔离并保留证据。')
            times.append(_utc(attempt['ended_at']))
        for attempt_id in {a['execution_attempt_id'] for a in attempts} | {context['batch_attempt_id']}:
            if ((self.paths.inbox / (attempt_id + '.ready.json')).exists()
                    or (self.paths.working / (attempt_id + '.request.json')).exists()):
                raise ValidationError('旧队列请求尚未归档或隔离，请先由既有恢复流程处理。')
        return max(times)

    def _evidence(self, connection, context, evidence_id, boundary, now):
        if has_active_automation_ui_run(connection, now=now):
            raise ValidationError('平台观察仍在运行，请等待结果入库后刷新核验记录。')
        row = connection.execute(
            'SELECT si.*, s.platform_name, s.status AS snapshot_status, s.snapshot_complete, s.scan_started_at, '
            's.scan_completed_at, s.result_id, s.execution_attempt_id, s.instruction_hash, s.evidence_manifest_sha256, '
            'r.result_sha256, r.instruction_hash AS receipt_instruction, r.execution_attempt_id AS receipt_attempt, '
            'b.action_type, b.execution_profile FROM listing_sync_snapshot_items si '
            'JOIN listing_sync_snapshots s ON s.snapshot_id = si.snapshot_id '
            'JOIN shadowbot_listing_result_receipts r ON r.result_id = s.result_id AND r.batch_id = s.batch_id '
            'JOIN shadowbot_listing_action_batches b ON b.batch_id = s.batch_id '
            'WHERE si.snapshot_item_id = ?', (evidence_id,)).fetchone()
        if (row is None or row['snapshot_status'] != 'VERIFIED' or row['snapshot_complete'] != 1
                or row['action_type'] != 'sync_status' or row['execution_profile'] != self.authorization_service.execution_profile
                or row['platform_name'] != context['platform_name'] or row['internal_sku'] != context['internal_sku']
                or row['product_name'] != context['expected_product_name'] or row['grade'] != context['expected_grade']
                or row['page_identity_key'] != context['page_identity_key']
                or row['receipt_instruction'] != row['instruction_hash'] or row['receipt_attempt'] != row['execution_attempt_id']
                or not SHA256.fullmatch(row['result_sha256']) or not SHA256.fullmatch(row['evidence_manifest_sha256'])
                or row['diagnostic_code'] or row['listing_location'] not in {'online_only', 'waiting_only'}):
            raise ValidationError('平台核验记录身份不匹配、不完整或没有合格回执。')
        page = 'online' if row['listing_location'] == 'online_only' else 'waiting'
        if row[page + '_occurrences'] != 1:
            raise ValidationError('平台核验记录不能唯一定位商品。')
        observed = _utc(row[page + '_observed_at'])
        started, completed = _utc(row['scan_started_at']), _utc(row['scan_completed_at'])
        price = _price(row[page + '_observed_price'])
        if (started <= boundary
                or not started <= observed <= completed <= now
                or now - observed > timedelta(minutes=30)):
            raise ValidationError('请选用旧执行停止之后取得的新鲜平台核验记录。')
        newer = connection.execute(
            'SELECT 1 FROM listing_sync_snapshot_items si JOIN listing_sync_snapshots s ON s.snapshot_id = si.snapshot_id '
            'WHERE si.internal_sku = ? AND s.platform_name = ? AND julianday(s.scan_completed_at) > julianday(?) LIMIT 1',
            (context['internal_sku'], context['platform_name'], row['scan_completed_at'])).fetchone()
        listing = connection.execute('SELECT * FROM listing_status WHERE platform_name = ? AND internal_sku = ?',
            (context['platform_name'], context['internal_sku'])).fetchone()
        if (newer or listing is None or _price(listing['current_price']) != price
                or listing['price_source_attempt_id'] != row['execution_attempt_id']
                or _utc(listing['price_observed_at']) != observed):
            raise ValidationError('已有更新的平台事实，请刷新核验记录。')
        evidence = {'snapshot_item_id': evidence_id, 'snapshot_id': row['snapshot_id'],
            'result_id': row['result_id'], 'execution_attempt_id': row['execution_attempt_id'],
            'result_sha256': row['result_sha256'], 'evidence_manifest_sha256': row['evidence_manifest_sha256'],
            'platform_name': row['platform_name'], 'internal_sku': row['internal_sku'],
            'page_identity_key': row['page_identity_key'], 'observed_price': format(price, '.2f'),
            'observed_at': observed.isoformat(), 'listing_location': row['listing_location'],
            'operation_id': context['operation_id'], 'target_price': context['target_price']}
        evidence['digest'] = digest_json(evidence)
        return evidence

    @staticmethod
    def _history(connection, history_id, task_id, actor, now, reason, metadata, *, terminal):
        status = connection.execute('SELECT task_status FROM tasks WHERE task_id = ?', (task_id,)).fetchone()[0]
        connection.execute('INSERT INTO task_status_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (history_id, task_id, status, 'skipped' if terminal else status, actor, now.isoformat(),
             reason, json.dumps(metadata, ensure_ascii=False, sort_keys=True)))
