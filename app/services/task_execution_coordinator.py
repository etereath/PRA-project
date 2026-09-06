"""Queue Service owner for accepted v4 authorizations; batch/attempt remain authoritative.

Only the existing single Queue Service host calls run_cycle. Web only accepts.
The v4 transaction is the publication claim; a restart never republishes a
PUBLISHING/QUEUED/UNKNOWN batch, even when no request file can be found.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.exceptions import ValidationError
from app.enums import TaskStatus
from app.repositories.execution_continuation_repository import digest_json
from app.services.execution_authorization import (
    ExecutionAuthorizationBlocked, ExecutionAuthorizationConflict,
)
from app.services.shadowbot_commit_batch import validate_request
from app.services.shadowbot_executor import ShadowBotStartBoundaryError
from app.services.shadowbot_queue import ShadowBotQueuePaths, read_checked_queue_json
from app.services.price_execution_resolution import PriceExecutionResolutionApplicationService


MESSAGES = {
    'ACCEPTED': '授权已保存，由执行服务继续推进。',
    'BLOCKED': '暂时等待平台占用或复核解除；执行服务会在授权有效期内继续。',
    'TRACKING': '已交给执行链，等待平台执行及结果回读。',
    'RECONCILING': '执行结果未知，正在沿唯一只读对账收口；不会再次猜测写入。',
    'HUMAN': '请由管理员检查该操作及唯一对账记录，完成有证据的人工处置；写锁继续保留。',
    'RECONFIRM': '事实或执行条件已变化。请重新预览并确认本次价格决定。',
    'EXPIRED': '本次授权或决定已过期，未再投递。请按当前事实重新决定。',
    'SUPERSEDED': '本次价格决定已取消或由新决定替代。',
    'ALREADY_APPLIED': '新鲜平台观察已证明目标价格，无需再次写入。',
    'COMPLETE': '执行链已收口；执行结果及平台回读见下方记录。',
    'HUMAN_RESOLVED': '已按平台证据人工终止旧决定；原未知执行历史保留。',
    'RETRY_PENDING': '执行服务暂时异常，将重试检查；已有执行不会重复投递。',
}


class TaskExecutionCoordinator:
    def __init__(self, authorization_service, *, executor, publishing_grace=timedelta(seconds=30)):
        self.service = authorization_service
        self.runtime = authorization_service.runtime
        self.store = authorization_service.continuations
        self.executor = executor
        self.paths = ShadowBotQueuePaths(authorization_service.queue_root)
        self.publishing_grace = publishing_grace
        self.price_resolution = PriceExecutionResolutionApplicationService(authorization_service)

    def run_cycle(self, *, now=None):
        current = now or self.service.clock()
        events = []
        for row in self.store.active():
            try:
                outcome = self._advance(row, current)
            except sqlite3.DatabaseError:
                raise
            except Exception:
                # Do not persist raw platform/configuration errors or poison sibling work.
                outcome = 'RETRY_PENDING'
                self.store.note(row['batch_id'], outcome, MESSAGES[outcome], now=current)
            events.append({'batch_id': row['batch_id'], 'status': outcome})
        return events

    def _note(self, batch_id, outcome, now, *, close=False, task_status=None, evidence=None):
        if outcome == 'HUMAN':
            self.price_resolution.ensure_reviews(batch_id, now=now)
        self.store.note(batch_id, outcome, MESSAGES[outcome], now=now,
                        close=close, task_status=task_status, evidence=evidence)
        return outcome

    def _advance(self, row, now):
        envelope = json.loads(row['envelope_json'])
        if digest_json(envelope) != row['envelope_sha256']:
            raise ValidationError('Authorization envelope integrity failure')
        batch_id = row['batch_id']
        with closing(self.runtime.connect_read()) as connection:
            batch = connection.execute('SELECT * FROM shadowbot_commit_batches WHERE batch_id = ?',
                                       (batch_id,)).fetchone()
            items = connection.execute(
                'SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?', (batch_id,),
            ).fetchall()
        if batch is None or batch['manifest_sha256'] != envelope['manifest']['manifest_sha256']:
            raise ValidationError('Authorization batch identity mismatch')
        if batch['status'] in {'VERIFIED', 'PARTIAL', 'FAILED'}:
            return self._note(batch_id, 'COMPLETE', now, close=True)
        if batch['status'] != 'PREPARED' and envelope['context'] != self.service.continuation_context():
            # A different queue/profile/applet is not evidence about the original execution.
            return self._note(batch_id, 'HUMAN', now)
        if batch['status'] == 'UNKNOWN':
            operations = [self.runtime.get_shadowbot_operation(i['operation_id']) for i in items]
            with closing(self.runtime.connect_read()) as connection:
                resolved = {op.operation_id for op in operations if op and
                            self.price_resolution.is_resolved(connection, op.operation_id)}
            if all(op and (op.status in {'VERIFIED', 'NOT_APPLIED'} or op.operation_id in resolved)
                   for op in operations):
                for operation in operations:
                    task = self.runtime.get_task(operation.task_id)
                    if operation.status == 'NOT_APPLIED' and task.task_status is TaskStatus.MANUAL_REVIEW:
                        self.executor.runtime_task_service.change_status(
                            task_id=task.task_id, to_status=TaskStatus.SKIPPED,
                            changed_by='execution_coordinator', reason='reconcile_proved_not_applied',
                            metadata={'operation_id': operation.operation_id, 'batch_id': batch_id},
                            result_message='只读对账确认未应用；旧决定已终止，新决定需正常授权。',
                        )
                return self._note(batch_id, 'HUMAN_RESOLVED' if resolved else 'COMPLETE', now, close=True)
            if any(op and op.status in {'MANUAL_REVIEW', 'MANUAL_HANDLED'}
                   and op.operation_id not in resolved for op in operations):
                return self._note(batch_id, 'HUMAN', now)
            for item, operation in zip(items, operations):
                if operation and operation.status == 'NEEDS_RECONCILIATION':
                    reconcile = self.executor.ensure_reconcile_attempt(
                        operation_id=item['operation_id'],
                        source_execution_attempt_id=item['item_execution_attempt_id'],
                        runner_payload={'applet_uri': self.service.applet_uri},
                    )
                    if reconcile is not None and reconcile.status not in {'STARTING', 'RUNNING'}:
                        return self._note(batch_id, 'HUMAN', now)
            return self._note(batch_id, 'RECONCILING', now)
        if batch['status'] != 'PREPARED':
            return self._track(batch, now)

        tasks = [self.runtime.get_task(t) for t in envelope['task_ids']]
        if any(t is None or t.task_status.value != 'pending' for t in tasks):
            return self._note(batch_id, 'SUPERSEDED', now, close=True)
        if (datetime.fromisoformat(envelope['expires_at']) <= now or any(
                t.expires_at and _utc(t.expires_at) <= now for t in tasks)):
            return self._note(batch_id, 'EXPIRED', now, close=True, task_status='expired')
        if envelope['context'] != self.service.continuation_context():
            return self._note(batch_id, 'RECONFIRM', now, close=True)
        try:
            facts = self.service._revalidate(tuple(envelope['task_ids']), now,
                                             allow_already_applied=True)
            manifest = self.service.v4_build(
                self.runtime, task_ids=envelope['task_ids'],
                mapping_path=self.service.shadowbot_identity_mapping, batch_id=batch_id,
            )
        except ExecutionAuthorizationBlocked:
            return self._note(batch_id, 'BLOCKED', now)
        except (ExecutionAuthorizationConflict, ValidationError):
            return self._note(batch_id, 'RECONFIRM', now, close=True)
        if all(Decimal(i['listing_price']) == Decimal(i['target_price']) for i in facts['items']):
            return self._note(batch_id, 'ALREADY_APPLIED', now, close=True, task_status='skipped',
                              evidence={'platform_observation': facts['items']})
        if (_semantic_facts(facts) != _semantic_facts(envelope['facts'])
                or manifest['manifest_sha256'] != batch['manifest_sha256']):
            return self._note(batch_id, 'RECONFIRM', now, close=True)
        profile = self.service.execution_profile
        try:
            self.service.v4_publish(
                self.runtime, self.service.runner_factory(self.service.queue_root),
                manifest=manifest, execution_profile=profile,
                applet_uri=self.service.applet_uri,
                confirmation_text=(manifest.get('development_confirmation_text', '')
                                   if profile == 'development' else ''),
                confirmed_by=envelope['principal_subject'] if profile == 'development' else '',
                authorization_batch_id=batch_id,
            )
        except ShadowBotStartBoundaryError:
            # The publisher recorded NOT_STARTED or UNKNOWN. Re-read on the next cycle.
            return self._note(batch_id, 'RETRY_PENDING', now)
        except ValidationError:
            # A blocker or supersession can race the last revalidation. The v4
            # transaction decides whether it claimed publication; never reset it here.
            return self._note(batch_id, 'BLOCKED', now)
        return self._note(batch_id, 'TRACKING', now)

    def _track(self, batch, now):
        attempt = batch['execution_attempt_id']
        paths = (self.paths.inbox / (attempt + '.ready.json'),
                 self.paths.working / (attempt + '.request.json'))
        for path in paths:
            if path.exists():
                request, _ = read_checked_queue_json(path)
                validate_request(request, check_expiry=False)
                if (request['batch_id'] != batch['batch_id']
                        or request['execution_attempt_id'] != attempt
                        or request['instruction_hash'] != batch['instruction_hash']):
                    raise ValidationError('Queue identity mismatch')
                return self._note(batch['batch_id'], 'TRACKING', now)
        if now - _utc(datetime.fromisoformat(batch['updated_at'])) > self.publishing_grace:
            self.runtime.quarantine_shadowbot_commit_batch(
                batch['batch_id'], reason='PUBLISHING_EVIDENCE_MISSING', now=now,
            )
            return self._note(batch['batch_id'], 'RECONCILING', now)
        return self._note(batch['batch_id'], 'TRACKING', now)


def _semantic_facts(facts):
    result = dict(facts)
    result['items'] = [{k: v for k, v in item.items()
                        if k not in {'task_updated_at', 'listing_updated_at',
                                     'listing_price_observed_at', 'listing_price_source_attempt_id',
                                     'real_inventory', 'real_inventory_version'}}
                       for item in facts['items']]
    return result


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
