"""Two-stage authorization facade over the existing ShadowBot v4/v5 chains."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable
from uuid import uuid4

from app.automation_ui_channel import has_active_automation_ui_run
from app.enums import ProductMappingStatus, TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.operations_web.auth import (
    AuthorizationBackend,
    Capability,
    Principal,
)
from app.models import TaskStatusHistory
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.execution_continuation_repository import (
    ExecutionContinuationRepository, digest_json,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime_master_data import RuntimeMasterDataProvider
from app.services.master_data_management import MasterDataManagementService
from app.services.shadowbot_commit_batch import load_identity_mapping
from app.services.shadowbot_commit_pipeline import (
    build_task_commit_manifest,
    prepare_task_commit_batch,
    publish_task_commit_batch,
)
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.listing_scan_quality import ListingScanQualityService
from app.services.shadowbot_listing_action_pipeline import (
    propose_listing_action_batch,
    publish_listing_action_batch,
)
from app.utils import utc_now


AUTHORIZATION_TTL = timedelta(minutes=10)
NO_WRITE_RESOLUTION_MAX_AGE = timedelta(minutes=30)
MAX_PREPARATIONS = 512
CONTRACT_VERSION = "task13.7-s2-execution-authorization-2.0"


class ExecutionAuthorizationError(ValidationError):
    pass


class ExecutionAuthorizationForbidden(ExecutionAuthorizationError):
    pass


class ExecutionAuthorizationConflict(ExecutionAuthorizationError):
    pass


class ExecutionAuthorizationBlocked(ExecutionAuthorizationConflict):
    """A temporary scheduling condition; the accepted authorization may wait."""


@dataclass(frozen=True, slots=True)
class ExecutionPreparation:
    confirmation_digest: str
    task_ids: tuple[str, ...]
    batch_id: str
    action_type: TaskActionType
    platform_name: str
    item_count: int
    expires_at: datetime
    principal_subject: str
    idempotency_key: str
    payload_digest: str
    already_applied: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionSubmissionResult:
    batch_id: str
    execution_attempt_id: str
    shadowbot_run_id: str
    task_ids: tuple[str, ...]
    outcome: str = "ACCEPTED"
    closed_at: str | None = None
    message: str = "授权已保存，由执行服务继续推进。"


@dataclass(slots=True)
class _StoredPreparation:
    public: ExecutionPreparation
    payload: dict[str, object]
    state: str = "PREPARED"


class ExecutionAuthorizationApplicationService:
    """Enforce principal, exact Task identity and freshness around v4/v5."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        authorization: AuthorizationBackend,
        products_workbook: Path,
        platform_mappings_workbook: Path,
        shadowbot_identity_mapping: Path,
        queue_root: Path,
        applet_uri: str,
        execution_profile: str,
        configured_account_id: str = "",
        master_data_provider: RuntimeMasterDataProvider | None = None,
        clock=None,
        runner_factory: Callable[[Path], object] = ShadowBotFileQueueRunner,
        v4_prepare=prepare_task_commit_batch,
        v4_build=build_task_commit_manifest,
        v4_publish=publish_task_commit_batch,
        v5_propose=propose_listing_action_batch,
        v5_publish=publish_listing_action_batch,
    ) -> None:
        profile = str(execution_profile or "").strip().lower()
        if profile not in {"development", "production"}:
            raise ValueError("execution_profile 必须是 development 或 production。")
        self.runtime = runtime_repository
        self.authorization = authorization
        self.products_workbook = Path(products_workbook)
        self.platform_mappings_workbook = Path(platform_mappings_workbook)
        self.master_data = master_data_provider or RuntimeMasterDataProvider(
            runtime_repository,
            configured_account_id=configured_account_id,
            products_workbook=self.products_workbook,
            platform_mappings_workbook=self.platform_mappings_workbook,
        )
        self.shadowbot_identity_mapping = Path(shadowbot_identity_mapping)
        self.queue_root = Path(queue_root)
        self.applet_uri = str(applet_uri or "").strip()
        self.execution_profile = profile
        self.clock = clock or utc_now
        self.runner_factory = runner_factory
        self.v4_prepare = v4_prepare
        self.v4_build = v4_build
        self.v4_publish = v4_publish
        self.v5_propose = v5_propose
        self.v5_publish = v5_publish
        self.inventory = InventoryRepository(runtime_repository)
        self.continuations = ExecutionContinuationRepository(runtime_repository)
        self.listing_quality = ListingScanQualityService(
            runtime_repository,
            max_age=NO_WRITE_RESOLUTION_MAX_AGE,
            clock=self.clock,
            master_data=self.master_data,
        )
        self._preparations: dict[str, _StoredPreparation] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = Lock()

    def prepare_execution(
        self,
        authenticated_principal: Principal,
        exact_task_ids: Iterable[str],
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> ExecutionPreparation:
        self._require_capability(authenticated_principal)
        current = _aware_utc(now or self.clock())
        task_ids = _exact_task_ids(exact_task_ids)
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 160:
            raise ExecutionAuthorizationError("本次执行预览已失效，请刷新页面后重试。")
        semantic_key = (authenticated_principal.subject, key)
        with self._lock:
            self._purge(current)
            existing_digest = self._idempotency.get(semantic_key)
            if existing_digest:
                existing = self._preparations.get(existing_digest)
                if existing is not None and existing.public.task_ids == task_ids:
                    return existing.public
                raise ExecutionAuthorizationConflict(
                    "本次执行请求与之前的任务不同，请刷新页面后重新预览。"
                )

        facts = self._revalidate(task_ids, current, allow_already_applied=True)
        already_applied = self._qualified_target_observations(facts) is not None
        action_type = TaskActionType(str(facts["action_type"]))
        batch_id = (
            _local_completion_id(authenticated_principal.subject, key, task_ids)
            if already_applied
            else _batch_id(
                authenticated_principal.subject,
                key,
                task_ids,
                action_type,
            )
        )
        if action_type is TaskActionType.UPDATE_PRICE and not already_applied:
            payload = self._prepare_v4(task_ids, batch_id)
        elif already_applied:
            payload = {}
        else:
            payload = self.v5_propose(
                self.runtime,
                batch_id=batch_id,
                task_ids=list(task_ids),
                mapping_path=self.shadowbot_identity_mapping,
                execution_profile=self.execution_profile,
            )
            if not bool(payload.get("publishable")):
                raise ExecutionAuthorizationConflict(
                    "上下架执行门禁未通过，请处理阻断项后重试。"
                )

        expires_at = min([current + AUTHORIZATION_TTL] + [
            _parse_datetime(item['task_expires_at']) for item in facts['items']
            if item['task_expires_at']
        ])
        digest_payload = _confirmation_payload(
            principal_subject=authenticated_principal.subject,
            idempotency_key=key,
            batch_id=batch_id,
            authorization_identity=_authorization_identity(facts, expires_at),
            already_applied=already_applied,
        )
        payload_digest = _sha256_json(digest_payload)
        confirmation_digest = payload_digest
        public = ExecutionPreparation(
            confirmation_digest=confirmation_digest,
            task_ids=task_ids,
            batch_id=batch_id,
            action_type=action_type,
            platform_name=str(facts["platform_name"]),
            item_count=len(task_ids),
            expires_at=expires_at,
            principal_subject=authenticated_principal.subject,
            idempotency_key=key,
            payload_digest=payload_digest,
            already_applied=already_applied,
        )
        with self._lock:
            self._purge(current)
            if len(self._preparations) >= MAX_PREPARATIONS:
                raise ExecutionAuthorizationError(
                    "执行授权缓存已满，请稍后重试。"
                )
            self._preparations[confirmation_digest] = _StoredPreparation(
                public=public,
                payload=dict(payload),
            )
            self._idempotency[semantic_key] = confirmation_digest
        return public

    def submit_execution(
        self,
        authenticated_principal: Principal,
        exact_task_ids: Iterable[str],
        confirmation_digest: str,
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> ExecutionSubmissionResult:
        self._require_capability(authenticated_principal)
        current = _aware_utc(now or self.clock())
        task_ids = _exact_task_ids(exact_task_ids)
        digest = str(confirmation_digest or "").strip()
        key = str(idempotency_key or "").strip()
        replay = self.continuations.replay(authenticated_principal.subject, key)
        if replay is not None:
            envelope = json.loads(replay['envelope_json'])
            if (envelope['confirmation_digest'] != digest
                    or tuple(envelope['task_ids']) != task_ids):
                raise ExecutionAuthorizationForbidden(
                    "执行确认与登录身份或任务批次不匹配。"
                )
            return self._submission_result(replay, task_ids)
        local_replay = self._replay_local_completion(
            authenticated_principal.subject,
            key,
            task_ids,
            digest,
        )
        if local_replay is not None:
            return local_replay
        with self._lock:
            self._purge(current)
            stored = self._preparations.get(digest)
            if stored is None:
                raise ExecutionAuthorizationConflict(
                    "执行确认已失效，请重新预览。"
                )
            public = stored.public
            if stored.state != "PREPARED":
                raise ExecutionAuthorizationConflict("该执行确认已提交，不能重复使用。")
            if (
                public.principal_subject != authenticated_principal.subject
                or public.task_ids != task_ids
                or public.idempotency_key != key
            ):
                raise ExecutionAuthorizationForbidden(
                    "执行确认与登录身份或任务批次不匹配。"
                )
            stored.state = "SUBMITTING"

        try:
            facts = self._revalidate(task_ids, current, allow_already_applied=True)
            qualified_target = self._qualified_target_observations(facts)
            already_applied = qualified_target is not None
            action_type = public.action_type
            if str(facts["action_type"]) != action_type.value:
                raise ExecutionAuthorizationConflict("任务动作在确认前发生变化。")
            latest_digest_payload = _confirmation_payload(
                principal_subject=authenticated_principal.subject,
                idempotency_key=key,
                batch_id=public.batch_id,
                authorization_identity=_authorization_identity(
                    facts,
                    public.expires_at,
                ),
                already_applied=already_applied,
            )
            if _sha256_json(latest_digest_payload) != public.payload_digest:
                raise ExecutionAuthorizationConflict(
                    "任务决定或确认方式在确认前发生变化，请重新预览。"
                )
            if already_applied:
                result = self._close_already_applied(
                    authenticated_principal=authenticated_principal,
                    task_ids=task_ids,
                    confirmation_digest=digest,
                    idempotency_key=key,
                    completion_id=public.batch_id,
                    facts=facts,
                    qualified_observations=qualified_target,
                    expires_at=public.expires_at,
                    current=current,
                )
                with self._lock:
                    stored.state = "SUBMITTED"
                return result
            if action_type is TaskActionType.UPDATE_PRICE:
                latest_payload = self.v4_build(
                    self.runtime,
                    task_ids=task_ids,
                    mapping_path=self.shadowbot_identity_mapping,
                    batch_id=public.batch_id,
                )
            else:
                latest_payload = self.v5_propose(
                    self.runtime,
                    batch_id=public.batch_id,
                    task_ids=list(task_ids),
                    mapping_path=self.shadowbot_identity_mapping,
                    execution_profile=self.execution_profile,
                )
                if not bool(latest_payload.get("publishable")):
                    raise ExecutionAuthorizationConflict(
                        "执行门禁在确认前发生变化，请重新预览。"
                    )
            if not self.applet_uri:
                raise ExecutionAuthorizationError(
                    "未配置 SHADOWBOT_APPLET_URI，已阻止投递。"
                )
            if action_type is TaskActionType.UPDATE_PRICE:
                envelope = {
                    'version': 1,
                    'capability': Capability.SUBMIT_EXECUTION.value,
                    'principal_subject': authenticated_principal.subject,
                    'idempotency_hash': digest_json(key),
                    'confirmation_digest': digest,
                    'task_ids': list(task_ids),
                    'batch_id': public.batch_id,
                    'expires_at': public.expires_at.isoformat(),
                    'facts': facts,
                    'authorization_identity': _authorization_identity(
                        facts,
                        public.expires_at,
                    ),
                    'manifest': latest_payload,
                    'context': self.continuation_context(),
                }
                self.continuations.accept(envelope, now=current)
                with self._lock:
                    stored.state = 'SUBMITTED'
                return ExecutionSubmissionResult(
                    public.batch_id,
                    '',
                    '',
                    task_ids,
                    message='授权已保存，由执行服务继续推进。',
                )
            runner = self.runner_factory(self.queue_root)
            self._record_authorization_audit(
                task_ids=task_ids,
                principal_subject=authenticated_principal.subject,
                batch_id=public.batch_id,
                action_type=action_type,
                idempotency_key=key,
                changed_at=current,
            )
            development_confirmation = self.execution_profile == "development"
            confirmed_by = (
                authenticated_principal.subject if development_confirmation else ""
            )
            confirmation_text = (
                str(latest_payload.get("required_confirmation") or "")
                if development_confirmation else ""
            )
            self.mark_platform_side_effect_boundary(
                operation_id=public.batch_id,
                facts=facts,
                actor="execution_authorization",
                idempotency_key="v5-publish:" + public.batch_id,
            )
            request, start = self.v5_publish(
                self.runtime, runner, proposal=latest_payload, applet_uri=self.applet_uri,
                confirmation_text=confirmation_text, confirmed_by=confirmed_by,
            )
        except Exception:
            with self._lock:
                stored.state = "CONSUMED_FAILED"
            raise
        with self._lock:
            stored.state = "SUBMITTED"
        return ExecutionSubmissionResult(
            batch_id=public.batch_id,
            execution_attempt_id=str(request["execution_attempt_id"]),
            shadowbot_run_id=str(start.shadowbot_run_id),
            task_ids=task_ids,
            outcome="DISPATCHED",
            message="已投递平台执行，请查看任务详情获取当前结果。",
        )

    def mark_platform_side_effect_boundary(
        self,
        *,
        operation_id: str,
        facts: dict[str, object],
        actor: str,
        idempotency_key: str,
    ) -> None:
        state = self.master_data.authority_state()
        if state.authority_mode != "DB_AUTHORITY":
            return
        generation = int(facts.get("master_data_authority_generation") or -1)
        mapping_digest = str(
            facts.get("master_data_mapping_snapshot_sha256") or ""
        )
        MasterDataManagementService(self.runtime).mark_platform_side_effect(
            operation_id=operation_id,
            authority_generation=generation,
            mapping_snapshot_sha256=mapping_digest,
            actor=actor,
            idempotency_key=idempotency_key,
        )

    def refresh_submission_result(self, principal: Principal, receipt: ExecutionSubmissionResult) -> ExecutionSubmissionResult:
        """Refresh a session-owned receipt without accepting or advancing any work."""
        if receipt.outcome == 'ALREADY_APPLIED':
            local = self._local_completion_by_id(
                principal.subject,
                receipt.batch_id,
                receipt.task_ids,
            )
            if local is None:
                raise ExecutionAuthorizationForbidden(
                    '未找到属于当前账号的本地决定结束回执。'
                )
            return local
        with closing(self.runtime.connect_read()) as connection:
            row = connection.execute(
                'SELECT * FROM execution_continuations WHERE batch_id = ? AND principal_subject = ?',
                (receipt.batch_id, principal.subject),
            ).fetchone()
        if row is None:
            if receipt.outcome == 'DISPATCHED':
                return receipt  # Existing v5 dispatch receipt; no v4 continuation.
            raise ExecutionAuthorizationForbidden('未找到属于当前账号的执行回执。')
        return self._submission_result(row, receipt.task_ids)

    @staticmethod
    def _submission_result(row, task_ids) -> ExecutionSubmissionResult:
        envelope = json.loads(row['envelope_json'])
        if digest_json(envelope) != row['envelope_sha256'] or tuple(envelope['task_ids']) != task_ids:
            raise ExecutionAuthorizationConflict('执行回执与原确认不一致，请检查任务详情。')
        message = row['message'] or (
            '本次授权已结束，请查看任务详情。'
            if row['closed_at']
            else '授权已保存，由执行服务继续推进。'
        )
        return ExecutionSubmissionResult(row['batch_id'], '', '', task_ids,
            outcome=row['outcome'] or ('CLOSED' if row['closed_at'] else 'ACCEPTED'),
            closed_at=row['closed_at'], message=message)

    def _record_authorization_audit(
        self,
        *,
        task_ids: tuple[str, ...],
        principal_subject: str,
        batch_id: str,
        action_type: TaskActionType,
        idempotency_key: str,
        changed_at: datetime,
    ) -> None:
        histories: list[TaskStatusHistory] = []
        for task_id in task_ids:
            task = self.runtime.get_task(task_id)
            if task is None:
                raise ExecutionAuthorizationConflict(
                    "任务在授权记录写入前已不存在，请重新预览。"
                )
            histories.append(
                TaskStatusHistory(
                    history_id=f"AUTH-{uuid4().hex[:16]}",
                    task_id=task_id,
                    from_status=task.task_status,
                    to_status=task.task_status,
                    changed_by=principal_subject,
                    changed_at=changed_at,
                    reason="execution_submission_authorized",
                    metadata={
                        "batch_id": batch_id,
                        "action_type": action_type.value,
                        "execution_profile": self.execution_profile,
                        "idempotency_key_sha256": _sha256_json(idempotency_key),
                        "authorization_contract_version": CONTRACT_VERSION,
                    },
                )
            )
        inserted = self.runtime.insert_status_histories(histories)
        if inserted != len(histories):
            raise ExecutionAuthorizationConflict(
                "执行授权审计未完整写入，已阻止投递。"
            )

    def continuation_context(self) -> dict[str, str]:
        return {
            'execution_profile': self.execution_profile,
            'queue_root': str(self.queue_root.resolve()),
            'applet_uri_sha256': digest_json(self.applet_uri),
        }

    def _prepare_v4(
        self,
        task_ids: tuple[str, ...],
        batch_id: str,
    ) -> dict[str, object]:
        built = self.v4_build(
            self.runtime,
            task_ids=task_ids,
            mapping_path=self.shadowbot_identity_mapping,
            batch_id=batch_id,
        )
        with closing(self.runtime.connect_read()) as connection:
            existing = connection.execute(
                "SELECT status, manifest_sha256 FROM shadowbot_commit_batches "
                "WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if existing is None:
            return self.v4_prepare(
                self.runtime,
                task_ids=task_ids,
                mapping_path=self.shadowbot_identity_mapping,
                batch_id=batch_id,
                execution_profile=self.execution_profile,
            )
        if (
            str(existing["status"]) != "PREPARED"
            or str(existing["manifest_sha256"]) != str(built["manifest_sha256"])
        ):
            raise ExecutionAuthorizationConflict(
                "这批任务已经发生变化或已提交，请重新预览。"
            )
        return built

    def _revalidate(
        self,
        task_ids: tuple[str, ...],
        current: datetime,
        *,
        allow_already_applied: bool = False,
    ) -> dict[str, object]:
        try:
            master_data_snapshot = self.master_data.snapshot()
            self.master_data.ensure_shadowbot_locator(
                self.shadowbot_identity_mapping
            )
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise ExecutionAuthorizationConflict(
                "商品或平台对应关系暂不可用，请重新预览。"
            ) from exc
        products = master_data_snapshot.products
        product_by_sku = {product.internal_sku.upper(): product for product in products}
        mappings = master_data_snapshot.mappings
        shadowbot_mapping_bytes = self.shadowbot_identity_mapping.read_bytes()
        identity_mapping = load_identity_mapping(self.shadowbot_identity_mapping)
        if master_data_snapshot.authority_mode == "DB_AUTHORITY":
            _validate_locator_authority_binding(
                self.shadowbot_identity_mapping,
                account_id=master_data_snapshot.account_id,
                authority_generation=master_data_snapshot.authority_generation,
                mapping_snapshot_sha256=master_data_snapshot.mapping_snapshot_sha256,
            )
        inventory = self.inventory

        with closing(self.runtime.connect_read()) as connection:
            if has_active_automation_ui_run(connection, now=current):
                raise ExecutionAuthorizationBlocked(
                    "平台状态正在更新，暂不能提交执行，请稍后重试。"
                )
            inventory_required = connection.execute(
                "SELECT 1 FROM tasks WHERE task_id IN ("
                + ",".join("?" for _ in task_ids)
                + ") AND action_type <> ? LIMIT 1",
                (*task_ids, TaskActionType.UPDATE_PRICE.value),
            ).fetchone()
            authority = inventory.get_authority_state(connection=connection)
            if inventory_required is not None and authority.authority_mode != "DB_AUTHORITY":
                raise ExecutionAuthorizationConflict("库存资料正在维护，暂不能提交执行。")
            rows = connection.execute(
                "SELECT * FROM tasks WHERE task_id IN ("
                + ",".join("?" for _ in task_ids)
                + ")",
                task_ids,
            ).fetchall()
            rows_by_id = {str(row["task_id"]): row for row in rows}
            if set(rows_by_id) != set(task_ids):
                raise ExecutionAuthorizationConflict("部分任务不存在。")
            action_types = {str(row["action_type"]) for row in rows}
            platforms = {str(row["platform_name"] or "").strip() for row in rows}
            if len(action_types) != 1 or len(platforms) != 1 or "" in platforms:
                raise ExecutionAuthorizationConflict("一次只能提交同平台、同动作任务。")
            action_type = TaskActionType(next(iter(action_types)))
            if action_type not in {
                TaskActionType.UPDATE_PRICE,
                TaskActionType.SET_ONLINE,
                TaskActionType.SET_OFFLINE,
            }:
                raise ExecutionAuthorizationConflict("所选任务类型不能发送到销售平台。")

            item_facts: list[dict[str, object]] = []
            for task_id in task_ids:
                row = rows_by_id[task_id]
                from app.services.price_decisions import unresolved_predecessors
                if unresolved_predecessors(connection, task_id):
                    raise ExecutionAuthorizationBlocked("先前操作尚未收口；新的销售决定已保留。")
                if str(row["task_status"]) != TaskStatus.PENDING.value:
                    raise ExecutionAuthorizationConflict("所选任务已不在待执行状态，请刷新列表。")
                expires_at = _parse_datetime(row["expires_at"])
                if expires_at is not None and expires_at <= current:
                    raise ExecutionAuthorizationConflict(f"任务已过期：{task_id}")
                sku = str(row["internal_sku"] or "").strip().upper()
                product = product_by_sku.get(sku)
                if product is None:
                    raise ExecutionAuthorizationConflict(f"商品资料中缺少商品编码：{sku}")
                balance = (
                    None
                    if action_type is TaskActionType.UPDATE_PRICE
                    else inventory.get_balance(sku, connection=connection)
                )
                if action_type is not TaskActionType.UPDATE_PRICE and balance is None:
                    raise ExecutionAuthorizationConflict(f"数据库库存中缺少商品：{sku}")
                target_price = _optional_decimal(row["target_price"])
                if target_price is not None and target_price < product.base_cost:
                    raise ExecutionAuthorizationConflict(f"任务价格低于基础成本：{task_id}")
                if action_type is TaskActionType.SET_ONLINE:
                    target_inventory = int(row["target_inventory"])
                    if target_inventory > balance.current_qty:
                        raise ExecutionAuthorizationConflict(
                            "上架目标库存超过数据库库存，请重新设置。"
                        )
                pending_review = connection.execute(
                    """
                    SELECT 1 FROM review_tasks
                    WHERE review_status = 'pending'
                      AND (
                        review_tasks.source_task_id = ?
                        OR (
                          review_tasks.internal_sku = ?
                          AND review_tasks.platform_name = ?
                          AND EXISTS (
                            SELECT 1 FROM tasks AS review_source
                            WHERE review_source.task_id = review_tasks.source_task_id
                              AND review_source.action_type = ?
                          )
                        )
                      )
                    LIMIT 1
                    """,
                    (
                        task_id,
                        sku,
                        next(iter(platforms)),
                        action_type.value,
                    ),
                ).fetchone()
                if pending_review is not None:
                    raise ExecutionAuthorizationBlocked(f"任务仍有待处理复核：{task_id}")
                active_locks = connection.execute(
                    """
                    SELECT lock.status, operation.platform,
                           operation.product_identity_json
                    FROM shadowbot_write_locks AS lock
                    JOIN shadowbot_operations AS operation
                      ON operation.operation_id = lock.operation_id
                    WHERE lock.status IN ('ACTIVE', 'UNKNOWN', 'REVIEW_BLOCKED')
                    """,
                ).fetchall()
                active_lock = any(
                    str(row["platform"] or "") == next(iter(platforms))
                    and str(
                        json.loads(str(row["product_identity_json"] or "{}"))
                        .get("internal_sku")
                        or ""
                    ).upper()
                    == sku
                    for row in active_locks
                )
                if active_lock:
                    raise ExecutionAuthorizationBlocked(f"商品 {sku} 正在执行其他平台操作，请稍后重试。")

                identity = identity_mapping.get(sku)
                if identity is None:
                    raise ExecutionAuthorizationConflict(f"影刀执行端缺少商品：{sku}")
                listing = self.runtime.get_listing_status(
                    next(iter(platforms)),
                    identity["expected_product_name"],
                    identity["expected_grade"],
                )
                if listing is None:
                    raise ExecutionAuthorizationConflict(f"缺少商品 {sku} 的最新平台状态。")
                expected_old = _optional_decimal(row["expected_old_price"])
                if action_type is TaskActionType.UPDATE_PRICE:
                    if expected_old is None:
                        raise ExecutionAuthorizationConflict(
                            "任务缺少原价格，请重新创建任务。"
                        )
                    if listing.current_price is None:
                        raise ExecutionAuthorizationConflict(
                            "平台原价格缺失，请先读取平台价格。"
                        )
                    if str(listing.online_status).lower() != 'online':
                        raise ExecutionAuthorizationConflict("商品当前未上架，请重新决定。")
                if (
                    action_type is TaskActionType.UPDATE_PRICE
                    and expected_old != listing.current_price
                    and not (allow_already_applied and target_price == listing.current_price)
                ):
                    raise ExecutionAuthorizationConflict(
                        "任务中的原价格与平台最新价格不一致，请重新预览。"
                    )
                trace = json.loads(str(row["decision_trace_json"] or "{}"))
                resolution = mappings.resolve(
                    platform_name=next(iter(platforms)),
                    platform_product_name=identity["expected_product_name"],
                    grade=identity["expected_grade"],
                    observed_at=current,
                    account_id=master_data_snapshot.account_id,
                    platform_product_identity_digest=str(
                        identity.get("platform_product_identity_digest") or ""
                    ),
                )
                if (
                    resolution.mapping_status is not ProductMappingStatus.VERIFIED
                    or str(resolution.internal_sku or "").upper() != sku
                ):
                    raise ExecutionAuthorizationConflict("商品与平台的对应关系未确认或存在重复。")
                frozen_mapping_ids = tuple(
                    sorted(str(value) for value in trace.get("mapping_ids", []))
                )
                if (
                    frozen_mapping_ids
                    and frozen_mapping_ids != tuple(sorted(resolution.mapping_ids))
                ):
                    raise ExecutionAuthorizationConflict(
                        "当前商品与平台的对应关系发生变化，请重新建立决定。"
                    )
                item_facts.append(
                    {
                        "task_id": task_id,
                        "task_updated_at": str(row["updated_at"]),
                        "task_expires_at": str(row['expires_at'] or ''),
                        "action_type": action_type.value,
                        "internal_sku": sku,
                        "expected_old_price": _decimal_text(expected_old),
                        "target_price": _decimal_text(target_price),
                        "target_inventory": row["target_inventory"],
                        "target_status": str(row["target_status"] or ""),
                        "base_cost": _decimal_text(product.base_cost),
                        "real_inventory": balance.current_qty if balance else None,
                        "real_inventory_version": balance.version if balance else None,
                        "listing_price": _decimal_text(listing.current_price),
                        "listing_status": listing.online_status,
                        "listing_updated_at": _datetime_text(listing.updated_at),
                        "listing_price_observed_at": _datetime_text(listing.price_observed_at),
                        "listing_price_source_attempt_id": listing.price_source_attempt_id,
                        "platform_product_identity_digest": str(
                            identity.get("platform_product_identity_digest") or ""
                        ),
                    }
                )

        if self.shadowbot_identity_mapping.read_bytes() != shadowbot_mapping_bytes:
            raise ExecutionAuthorizationConflict("影刀执行端的商品资料刚刚发生变化，请重新预览。")
        return {
            "action_type": action_type.value,
            "platform_name": next(iter(platforms)),
            "products_sha256": master_data_snapshot.product_snapshot_sha256,
            "platform_mapping_version": mappings.mapping_version,
            "master_data_authority_generation": (
                master_data_snapshot.authority_generation
            ),
            "master_data_mapping_snapshot_sha256": (
                master_data_snapshot.mapping_snapshot_sha256
            ),
            "target_account_id": master_data_snapshot.account_id,
            "shadowbot_mapping_sha256": hashlib.sha256(
                shadowbot_mapping_bytes
            ).hexdigest(),
            "items": item_facts,
        }

    def _qualified_target_observations(self, facts):
        """Return current qualified evidence only when every price target is met."""
        if facts["action_type"] != TaskActionType.UPDATE_PRICE.value:
            return None
        satisfied: list[bool] = []
        evidence: list[dict[str, object]] = []
        for item in facts["items"]:
            target_matches = Decimal(item["listing_price"]) == Decimal(
                item["target_price"]
            )
            quality = None
            if target_matches:
                quality = self.listing_quality.latest(
                    platform_name=facts["platform_name"],
                    internal_sku=item["internal_sku"],
                )
            qualified = bool(
                quality is not None
                and quality.operating_fact_qualified
                and quality.observed_online is True
                and quality.observed_price is not None
                and Decimal(quality.observed_price) == Decimal(item["target_price"])
                and quality.source_execution_attempt_id
                == item["listing_price_source_attempt_id"]
                and _same_timestamp(
                    quality.observed_at,
                    item["listing_price_observed_at"],
                )
                and (
                    not item["platform_product_identity_digest"]
                    or quality.platform_product_identity_digest
                    == item["platform_product_identity_digest"]
                )
            )
            satisfied.append(qualified)
            if qualified:
                evidence.append(
                    {
                        **item,
                        "qualification": quality.as_dict(),
                    }
                )
        if any(satisfied) and not all(satisfied):
            raise ExecutionAuthorizationConflict(
                "所选商品中仅部分已有合格事实证明达到目标价；"
                "请分别确认仍需执行与仅需结束的决定。"
            )
        return tuple(evidence) if satisfied and all(satisfied) else None

    def _close_already_applied(
        self,
        *,
        authenticated_principal: Principal,
        task_ids: tuple[str, ...],
        confirmation_digest: str,
        idempotency_key: str,
        completion_id: str,
        facts: dict[str, object],
        qualified_observations,
        expires_at: datetime,
        current: datetime,
    ) -> ExecutionSubmissionResult:
        """Atomically end decisions without creating platform execution authority."""
        identity = _authorization_identity(facts, expires_at)
        evidence_by_task = {
            str(item["task_id"]): item for item in qualified_observations
        }
        metadata_common = {
            "completion_id": completion_id,
            "confirmation_digest": confirmation_digest,
            "idempotency_hash": digest_json(idempotency_key),
            "capability": Capability.SUBMIT_EXECUTION.value,
            "task_ids": list(task_ids),
            "authorization_identity": identity,
        }
        with closing(self.runtime.connect_write()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT task_id, changed_at, metadata_json FROM task_status_history "
                "WHERE reason = 'ALREADY_APPLIED' AND changed_by = ?",
                (authenticated_principal.subject,),
            ).fetchall()
            replay_rows = [
                row
                for row in existing
                if json.loads(str(row["metadata_json"] or "{}")).get(
                    "completion_id"
                )
                == completion_id
            ]
            if replay_rows:
                if {str(row["task_id"]) for row in replay_rows} != set(task_ids):
                    raise ExecutionAuthorizationForbidden(
                        "本地决定结束回执与任务范围不匹配。"
                    )
                return _local_completion_result(
                    completion_id,
                    task_ids,
                    str(replay_rows[0]["changed_at"]),
                )

            for item in identity["items"]:
                task_id = str(item["task_id"])
                task = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if (
                    task is None
                    or str(task["platform_name"] or "")
                    != str(facts["platform_name"])
                    or not _task_matches_authorization(task, item)
                ):
                    raise ExecutionAuthorizationConflict(
                        "任务决定在本地结束前发生变化，请重新预览。"
                    )
                if task["task_status"] != TaskStatus.PENDING.value:
                    raise ExecutionAuthorizationConflict(
                        "任务已不在待执行状态，请刷新列表。"
                    )
                active = connection.execute(
                    """
                    SELECT 1 FROM execution_continuations AS continuation
                    JOIN shadowbot_commit_batch_items AS batch_item
                      ON batch_item.batch_id = continuation.batch_id
                    WHERE batch_item.source_task_id = ?
                      AND continuation.closed_at IS NULL
                    UNION ALL
                    SELECT 1 FROM shadowbot_commit_batch_items AS batch_item
                    JOIN shadowbot_commit_batches AS batch
                      ON batch.batch_id = batch_item.batch_id
                    WHERE batch_item.source_task_id = ?
                      AND batch.status <> 'PREPARED'
                    UNION ALL
                    SELECT 1 FROM shadowbot_operations
                    WHERE task_id = ?
                      AND status IN (
                        'PENDING', 'RUNNING', 'NEEDS_RECONCILIATION',
                        'MANUAL_REVIEW'
                      )
                    LIMIT 1
                    """,
                    (task_id, task_id, task_id),
                ).fetchone()
                if active is not None:
                    raise ExecutionAuthorizationBlocked(
                        "任务仍有活动平台写责任，不能本地结束。"
                    )
                lock = connection.execute(
                    """
                    SELECT 1 FROM shadowbot_write_locks AS write_lock
                    JOIN shadowbot_operations AS operation
                      ON operation.operation_id = write_lock.operation_id
                    WHERE write_lock.status <> 'RELEASED'
                      AND operation.platform = ?
                      AND upper(json_extract(
                            operation.product_identity_json,
                            '$.internal_sku'
                          )) = ?
                    LIMIT 1
                    """,
                    (facts["platform_name"], item["internal_sku"]),
                ).fetchone()
                if lock is not None:
                    raise ExecutionAuthorizationBlocked(
                        "同商品仍有活动写锁，不能本地结束。"
                    )
                pending_review = connection.execute(
                    """
                    SELECT 1 FROM review_tasks
                    WHERE review_status = 'pending'
                      AND (
                        source_task_id = ?
                        OR (
                          internal_sku = ?
                          AND platform_name = ?
                          AND EXISTS (
                            SELECT 1 FROM tasks AS review_source
                            WHERE review_source.task_id = review_tasks.source_task_id
                              AND review_source.action_type = ?
                          )
                        )
                      )
                    LIMIT 1
                    """,
                    (
                        task_id,
                        item["internal_sku"],
                        facts["platform_name"],
                        TaskActionType.UPDATE_PRICE.value,
                    ),
                ).fetchone()
                if pending_review is not None:
                    raise ExecutionAuthorizationBlocked(
                        "任务仍有待处理复核，不能本地结束。"
                    )
                listing = connection.execute(
                    """
                    SELECT current_price, online_status, price_observed_at,
                           price_source_attempt_id
                    FROM listing_status
                    WHERE platform_name = ? AND internal_sku = ?
                    """,
                    (facts["platform_name"], item["internal_sku"]),
                ).fetchone()
                observation = evidence_by_task[task_id]
                quality = observation["qualification"]
                if (
                    listing is None
                    or listing["current_price"] is None
                    or Decimal(str(listing["current_price"]))
                    != Decimal(str(item["target_price"]))
                    or str(listing["online_status"] or "").lower() != "online"
                    or str(listing["price_source_attempt_id"] or "")
                    != str(quality["source_execution_attempt_id"])
                    or not _same_timestamp(
                        listing["price_observed_at"],
                        quality["observed_at"],
                    )
                ):
                    raise ExecutionAuthorizationConflict(
                        "最新合格平台事实已变化，未结束决定。"
                    )

            closed_at = current.isoformat()
            for task_id in task_ids:
                changed = connection.execute(
                    "UPDATE tasks SET task_status = ?, updated_at = ?, "
                    "result_message = ? WHERE task_id = ? AND task_status = ?",
                    (
                        TaskStatus.SKIPPED.value,
                        closed_at,
                        "当前合格平台事实已满足目标；决定已结束，未执行平台写入。",
                        task_id,
                        TaskStatus.PENDING.value,
                    ),
                ).rowcount
                if changed != 1:
                    raise ExecutionAuthorizationConflict(
                        "任务状态在本地结束前发生变化。"
                    )
                connection.execute(
                    """
                    INSERT INTO task_status_history(
                      history_id, task_id, from_status, to_status, changed_by,
                      changed_at, reason, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ALREADY_APPLIED', ?)
                    """,
                    (
                        "LOCAL-CLOSE-"
                        + hashlib.sha256(
                            f"{completion_id}|{task_id}".encode("utf-8")
                        ).hexdigest()[:20],
                        task_id,
                        TaskStatus.PENDING.value,
                        TaskStatus.SKIPPED.value,
                        authenticated_principal.subject,
                        closed_at,
                        json.dumps(
                            {
                                **metadata_common,
                                "platform_observation": evidence_by_task[task_id],
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                )
        return _local_completion_result(completion_id, task_ids, closed_at)

    def _replay_local_completion(self, subject, key, task_ids, digest):
        with closing(self.runtime.connect_read()) as connection:
            rows = connection.execute(
                "SELECT task_id, changed_at, metadata_json FROM task_status_history "
                "WHERE reason = 'ALREADY_APPLIED' AND changed_by = ?",
                (subject,),
            ).fetchall()
        matching = []
        for row in rows:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            if metadata.get("idempotency_hash") == digest_json(key):
                matching.append((row, metadata))
        if not matching:
            return None
        metadata = matching[0][1]
        if (
            metadata.get("confirmation_digest") != digest
            or tuple(metadata.get("task_ids", ())) != task_ids
        ):
            raise ExecutionAuthorizationForbidden(
                "执行确认与已完成的本地决定结束记录不匹配。"
            )
        return self._local_completion_by_id(
            subject,
            str(metadata["completion_id"]),
            task_ids,
        )

    def _local_completion_by_id(self, subject, completion_id, task_ids):
        with closing(self.runtime.connect_read()) as connection:
            rows = connection.execute(
                "SELECT task_id, changed_at, metadata_json FROM task_status_history "
                "WHERE reason = 'ALREADY_APPLIED' AND changed_by = ?",
                (subject,),
            ).fetchall()
        matched = [
            row
            for row in rows
            if json.loads(str(row["metadata_json"] or "{}")).get(
                "completion_id"
            )
            == completion_id
        ]
        if {str(row["task_id"]) for row in matched} != set(task_ids):
            return None
        return _local_completion_result(
            completion_id,
            task_ids,
            str(matched[0]["changed_at"]),
        )

    def _require_capability(self, principal: Principal) -> None:
        if not self.authorization.allows(principal, Capability.SUBMIT_EXECUTION):
            raise ExecutionAuthorizationForbidden("当前账号没有提交平台执行的权限。")

    def _purge(self, now: datetime) -> None:
        expired = [
            digest
            for digest, stored in self._preparations.items()
            if stored.public.expires_at <= now
        ]
        for digest in expired:
            stored = self._preparations.pop(digest)
            self._idempotency.pop(
                (stored.public.principal_subject, stored.public.idempotency_key),
                None,
            )


def _validate_locator_authority_binding(
    path: Path,
    *,
    account_id: str,
    authority_generation: int,
    mapping_snapshot_sha256: str,
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutionAuthorizationConflict(
            "影刀执行定位资料不可用，请先重新同步。"
        ) from exc
    if not isinstance(payload, dict):
        raise ExecutionAuthorizationConflict("影刀执行定位资料缺少 authority 绑定。")
    claimed_payload_digest = str(payload.get("artifact_payload_sha256") or "")
    digest_payload = dict(payload)
    digest_payload.pop("artifact_payload_sha256", None)
    actual_payload_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        str(payload.get("account_id") or "").strip() != account_id
        or int(payload.get("authority_generation") or -1) != authority_generation
        or str(payload.get("mapping_snapshot_sha256") or "").strip()
        != mapping_snapshot_sha256
        or claimed_payload_digest != actual_payload_digest
    ):
        raise ExecutionAuthorizationConflict(
            "影刀执行定位资料与当前账号或 Runtime mapping generation 不一致。"
        )


def _exact_task_ids(values: Iterable[str]) -> tuple[str, ...]:
    original = [str(value or "").strip() for value in values]
    if not original or any(not value for value in original):
        raise ExecutionAuthorizationError("必须明确选择至少一个任务。")
    if len(original) != len(set(original)):
        raise ExecutionAuthorizationError("不能重复选择同一个任务。")
    if len(original) > 50:
        raise ExecutionAuthorizationError("一次最多提交 50 个任务。")
    return tuple(sorted(original))


def _batch_id(
    subject: str,
    idempotency_key: str,
    task_ids: tuple[str, ...],
    action_type: TaskActionType,
) -> str:
    value = "|".join((subject, idempotency_key, action_type.value, *task_ids))
    return "WEB7E-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _authorization_identity(facts, expires_at: datetime) -> dict[str, object]:
    """Business decision confirmed by the user, excluding execution-time facts."""
    action_type = str(facts["action_type"])
    return {
        "contract_version": CONTRACT_VERSION,
        "platform_name": str(facts["platform_name"]),
        "action_type": action_type,
        "expires_at": _aware_utc(expires_at).isoformat(),
        "items": [
            {
                "task_id": str(item["task_id"]),
                "internal_sku": str(item["internal_sku"]),
                "platform_product_identity_digest": str(
                    item["platform_product_identity_digest"] or ""
                ),
                "expected_old_price": str(item["expected_old_price"] or ""),
                "target_price": str(item["target_price"] or ""),
                "target_inventory": item["target_inventory"],
                "target_status": str(item["target_status"] or ""),
                "task_expires_at": str(item["task_expires_at"] or ""),
            }
            for item in facts["items"]
        ],
    }


def _confirmation_payload(
    *,
    principal_subject: str,
    idempotency_key: str,
    batch_id: str,
    authorization_identity: dict[str, object],
    already_applied: bool,
) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "principal_subject": principal_subject,
        "idempotency_key": idempotency_key,
        "batch_id": batch_id,
        "confirmation_mode": (
            "CLOSE_ALREADY_APPLIED" if already_applied else "EXECUTE"
        ),
        "authorization_identity": authorization_identity,
    }


def _local_completion_id(
    subject: str,
    idempotency_key: str,
    task_ids: tuple[str, ...],
) -> str:
    value = "|".join((subject, idempotency_key, *task_ids))
    return "LOCAL-CLOSE-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _local_completion_result(
    completion_id: str,
    task_ids: tuple[str, ...],
    closed_at: str,
) -> ExecutionSubmissionResult:
    return ExecutionSubmissionResult(
        batch_id=completion_id,
        execution_attempt_id="",
        shadowbot_run_id="",
        task_ids=task_ids,
        outcome="ALREADY_APPLIED",
        closed_at=closed_at,
        message="目标已由最新合格平台事实满足；决定已结束，未执行平台写入。",
    )


def _task_matches_authorization(task, item) -> bool:
    return bool(
        str(task["action_type"]) == TaskActionType.UPDATE_PRICE.value
        and str(task["internal_sku"] or "").upper()
        == str(item["internal_sku"]).upper()
        and _decimal_text(_optional_decimal(task["expected_old_price"]))
        == str(item["expected_old_price"])
        and _decimal_text(_optional_decimal(task["target_price"]))
        == str(item["target_price"])
        and str(task["target_status"] or "") == str(item["target_status"])
        and task["target_inventory"] == item["target_inventory"]
        and str(task["expires_at"] or "") == str(item["task_expires_at"])
    )


def _same_timestamp(left, right) -> bool:
    left_value = _parse_datetime(left)
    right_value = _parse_datetime(right)
    return bool(
        left_value is not None
        and right_value is not None
        and left_value == right_value
    )


def _sha256_json(value: object) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    return Decimal(str(value))


def _decimal_text(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _parse_datetime(value: object) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    return _aware_utc(datetime.fromisoformat(str(value)))


def _datetime_text(value: datetime | None) -> str:
    return "" if value is None else _aware_utc(value).isoformat()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
