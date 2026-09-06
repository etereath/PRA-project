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
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.services.product_mapping import compile_product_mapping_workbook
from app.services.shadowbot_commit_batch import load_identity_mapping
from app.services.shadowbot_commit_pipeline import (
    build_task_commit_manifest,
    prepare_task_commit_batch,
    publish_task_commit_batch,
)
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_listing_action_pipeline import (
    propose_listing_action_batch,
    publish_listing_action_batch,
)
from app.utils import utc_now


AUTHORIZATION_TTL = timedelta(minutes=10)
MAX_PREPARATIONS = 512
CONTRACT_VERSION = "task13.5-7e-execution-authorization-1.0"


class ExecutionAuthorizationError(ValidationError):
    pass


class ExecutionAuthorizationForbidden(ExecutionAuthorizationError):
    pass


class ExecutionAuthorizationConflict(ExecutionAuthorizationError):
    pass


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


@dataclass(frozen=True, slots=True)
class ExecutionSubmissionResult:
    batch_id: str
    execution_attempt_id: str
    shadowbot_run_id: str
    task_ids: tuple[str, ...]


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

        facts = self._revalidate(task_ids, current)
        action_type = TaskActionType(str(facts["action_type"]))
        batch_id = _batch_id(
            authenticated_principal.subject,
            key,
            task_ids,
            action_type,
        )
        if action_type is TaskActionType.UPDATE_PRICE:
            payload = self._prepare_v4(task_ids, batch_id)
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

        expires_at = current + AUTHORIZATION_TTL
        digest_payload = {
            "contract_version": CONTRACT_VERSION,
            "principal_subject": authenticated_principal.subject,
            "idempotency_key": key,
            "task_ids": list(task_ids),
            "batch_id": batch_id,
            "action_type": action_type.value,
            "facts": facts,
            "execution_payload": _execution_payload_identity(payload, action_type),
            "expires_at": expires_at.isoformat(),
        }
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
            facts = self._revalidate(task_ids, current)
            action_type = public.action_type
            if str(facts["action_type"]) != action_type.value:
                raise ExecutionAuthorizationConflict("任务动作在确认前发生变化。")
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
            latest_digest_payload = {
                "contract_version": CONTRACT_VERSION,
                "principal_subject": authenticated_principal.subject,
                "idempotency_key": key,
                "task_ids": list(task_ids),
                "batch_id": public.batch_id,
                "action_type": action_type.value,
                "facts": facts,
                "execution_payload": _execution_payload_identity(
                    latest_payload,
                    action_type,
                ),
                "expires_at": public.expires_at.isoformat(),
            }
            if _sha256_json(latest_digest_payload) != public.payload_digest:
                raise ExecutionAuthorizationConflict(
                    "任务或最新经营事实在确认前发生变化，请重新预览。"
                )
            if not self.applet_uri:
                raise ExecutionAuthorizationError(
                    "未配置 SHADOWBOT_APPLET_URI，已阻止投递。"
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
            if action_type is TaskActionType.UPDATE_PRICE:
                confirmation_text = (
                    str(latest_payload.get("development_confirmation_text") or "")
                    if development_confirmation
                    else ""
                )
                request, start = self.v4_publish(
                    self.runtime,
                    runner,
                    manifest=latest_payload,
                    execution_profile=self.execution_profile,
                    applet_uri=self.applet_uri,
                    confirmation_text=confirmation_text,
                    confirmed_by=confirmed_by,
                )
            else:
                confirmation_text = (
                    str(latest_payload.get("required_confirmation") or "")
                    if development_confirmation
                    else ""
                )
                request, start = self.v5_publish(
                    self.runtime,
                    runner,
                    proposal=latest_payload,
                    applet_uri=self.applet_uri,
                    confirmation_text=confirmation_text,
                    confirmed_by=confirmed_by,
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
        )

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
    ) -> dict[str, object]:
        products_bytes = self.products_workbook.read_bytes()
        products = load_products(self.products_workbook)
        if self.products_workbook.read_bytes() != products_bytes:
            raise ExecutionAuthorizationConflict("商品资料刚刚发生变化，请重新预览。")
        product_by_sku = {product.internal_sku.upper(): product for product in products}
        mapping_bytes = self.platform_mappings_workbook.read_bytes()
        mappings = compile_product_mapping_workbook(self.platform_mappings_workbook)
        if self.platform_mappings_workbook.read_bytes() != mapping_bytes:
            raise ExecutionAuthorizationConflict("商品与平台的对应关系刚刚发生变化，请重新预览。")
        shadowbot_mapping_bytes = self.shadowbot_identity_mapping.read_bytes()
        identity_mapping = load_identity_mapping(self.shadowbot_identity_mapping)
        inventory = self.inventory

        with closing(self.runtime.connect_read()) as connection:
            if has_active_automation_ui_run(connection, now=current):
                raise ExecutionAuthorizationConflict(
                    "平台状态正在更新，暂不能提交执行，请稍后重试。"
                )
            authority = inventory.get_authority_state(connection=connection)
            if authority.authority_mode != "DB_AUTHORITY":
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
                if str(row["task_status"]) != TaskStatus.PENDING.value:
                    raise ExecutionAuthorizationConflict("所选任务已不在待执行状态，请刷新列表。")
                expires_at = _parse_datetime(row["expires_at"])
                if expires_at is not None and expires_at <= current:
                    raise ExecutionAuthorizationConflict(f"任务已过期：{task_id}")
                sku = str(row["internal_sku"] or "").strip().upper()
                product = product_by_sku.get(sku)
                if product is None:
                    raise ExecutionAuthorizationConflict(f"商品资料中缺少商品编码：{sku}")
                balance = inventory.get_balance(sku, connection=connection)
                if balance is None:
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
                        source_task_id = ?
                        OR (internal_sku = ? AND platform_name = ?)
                      )
                    LIMIT 1
                    """,
                    (task_id, sku, next(iter(platforms))),
                ).fetchone()
                if pending_review is not None:
                    raise ExecutionAuthorizationConflict(f"任务仍有待处理复核：{task_id}")
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
                    raise ExecutionAuthorizationConflict(f"商品 {sku} 正在执行其他平台操作，请稍后重试。")

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
                if (
                    action_type is TaskActionType.UPDATE_PRICE
                    and expected_old != listing.current_price
                ):
                    raise ExecutionAuthorizationConflict(
                        "任务中的原价格与平台最新价格不一致，请重新预览。"
                    )
                trace = json.loads(str(row["decision_trace_json"] or "{}"))
                frozen_mapping_version = str(trace.get("mapping_version") or "")
                if (
                    frozen_mapping_version
                    and frozen_mapping_version != mappings.mapping_version
                ):
                    raise ExecutionAuthorizationConflict("商品与平台的对应关系发生变化，请重新预览。")
                resolution = mappings.resolve(
                    platform_name=next(iter(platforms)),
                    platform_product_name=identity["expected_product_name"],
                    grade=identity["expected_grade"],
                    observed_at=current,
                )
                if (
                    resolution.mapping_status is not ProductMappingStatus.VERIFIED
                    or str(resolution.internal_sku or "").upper() != sku
                ):
                    raise ExecutionAuthorizationConflict("商品与平台的对应关系未确认或存在重复。")
                item_facts.append(
                    {
                        "task_id": task_id,
                        "task_updated_at": str(row["updated_at"]),
                        "action_type": action_type.value,
                        "internal_sku": sku,
                        "expected_old_price": _decimal_text(expected_old),
                        "target_price": _decimal_text(target_price),
                        "target_inventory": row["target_inventory"],
                        "target_status": str(row["target_status"] or ""),
                        "base_cost": _decimal_text(product.base_cost),
                        "real_inventory": balance.current_qty,
                        "real_inventory_version": balance.version,
                        "listing_price": _decimal_text(listing.current_price),
                        "listing_status": listing.online_status,
                        "listing_updated_at": _datetime_text(listing.updated_at),
                    }
                )

        if self.shadowbot_identity_mapping.read_bytes() != shadowbot_mapping_bytes:
            raise ExecutionAuthorizationConflict("影刀执行端的商品资料刚刚发生变化，请重新预览。")
        return {
            "action_type": action_type.value,
            "platform_name": next(iter(platforms)),
            "products_sha256": hashlib.sha256(products_bytes).hexdigest(),
            "platform_mapping_version": mappings.mapping_version,
            "shadowbot_mapping_sha256": hashlib.sha256(
                shadowbot_mapping_bytes
            ).hexdigest(),
            "items": item_facts,
        }

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


def _execution_payload_identity(
    payload: dict[str, object],
    action_type: TaskActionType,
) -> dict[str, object]:
    manifest = payload if action_type is TaskActionType.UPDATE_PRICE else payload["manifest"]
    return {
        "batch_id": manifest["batch_id"],
        "platform_name": manifest["platform_name"],
        "manifest_sha256": manifest["manifest_sha256"],
        "items": [
            {
                "source_task_id": item["source_task_id"],
                "item_payload_sha256": item["item_payload_sha256"],
            }
            for item in manifest["items"]
        ],
    }


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
