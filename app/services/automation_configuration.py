"""Allowlisted, version-safe Automation configuration for the operations Web."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.automation_models import AutomationJob, AutomationRun
from app.enums import AutomationRunStatus
from app.operations_web.auth import AuthorizationBackend, Capability, Principal
from app.repositories.automation_repository import AutomationRepository
from app.repositories.inventory_repository import InventoryRepository
from app.services.automation import (
    CHILD_ONLY,
    DAILY_TASK_GENERATION,
    DAILY_LOCAL_TIME,
    FULL_MARKET_SCAN,
    INTERVAL_MINUTES,
    LISTING_STATUS_SCAN,
    ONLINE_PULSE,
    ORDER_SCAN,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    POST_CUTOFF_PULSE,
    PRE_CUTOFF_FULL_SCAN,
    SALES_PLAN_INPUT_BUILD,
    REVIEW_TIMEOUT_MAINTENANCE,
)
from app.services.operational_time import (
    OperationalTimeContext,
    OperationalTimePolicy,
    OperationalTimePolicyRegistry,
    OperationalTimeService,
)


CONFIGURABLE_JOB_TYPES = frozenset(
    {
        ONLINE_PULSE,
        FULL_MARKET_SCAN,
        PRE_CUTOFF_FULL_SCAN,
        POST_CUTOFF_PULSE,
        PLATFORM_TRADE_DAY_SETTLEMENT,
        SALES_PLAN_INPUT_BUILD,
        REVIEW_TIMEOUT_MAINTENANCE,
        DAILY_TASK_GENERATION,
    }
)
CHILD_JOB_TYPES = frozenset({LISTING_STATUS_SCAN, ORDER_SCAN})
RERUN_JOB_TYPES = frozenset(
    {PLATFORM_TRADE_DAY_SETTLEMENT, SALES_PLAN_INPUT_BUILD}
)


class AutomationConfigurationError(ValueError):
    """The requested Automation change is outside the frozen allowlist."""


class AutomationConfigurationApplicationService:
    def __init__(
        self,
        automation: AutomationRepository,
        inventory: InventoryRepository,
        authorization: AuthorizationBackend,
        *,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self.automation = automation
        self.inventory = inventory
        self.authorization = authorization
        self.clock = clock

    def configure_job(
        self,
        principal: Principal,
        *,
        job_id: str,
        enabled: bool,
        interval_minutes: int | None = None,
        offset_minutes: int | None = None,
        source_allowlist: tuple[str, ...] | None = None,
    ) -> AutomationJob:
        self._authorize(principal)
        current = self.automation.get_job(job_id.strip())
        if current is None:
            raise AutomationConfigurationError("自动化方案已变化，请刷新后重试。")
        if current.job_type in CHILD_JOB_TYPES or current.schedule_kind == CHILD_ONLY:
            raise AutomationConfigurationError("子扫描只能继承父任务，不能独立配置。")
        if current.job_type not in CONFIGURABLE_JOB_TYPES:
            raise AutomationConfigurationError("该自动化方案不在 Web 配置白名单中。")

        now = self._now()
        policy = OperationalTimePolicyRegistry(
            self.automation.load_operational_time_policies()
        ).select(now)
        schedule_kind = current.schedule_kind
        schedule_expression = current.schedule_expression
        config = dict(current.config)
        config.pop("updated_by", None)
        config.pop("effective_at", None)
        config.pop("configuration_version", None)

        if current.job_type == ONLINE_PULSE:
            minutes = self._bounded_multiple(
                interval_minutes,
                current=current.schedule_expression,
                minimum=10,
                maximum=30,
                multiple=5,
                label="上架中小扫描间隔",
            )
            schedule_kind = INTERVAL_MINUTES
            schedule_expression = str(minutes)
            config.pop("interval_offset_minutes", None)
        elif current.job_type == FULL_MARKET_SCAN:
            minutes = self._bounded_multiple(
                interval_minutes,
                current=current.schedule_expression,
                minimum=60,
                maximum=180,
                multiple=30,
                label="完整市场扫描间隔",
            )
            if offset_minutes not in (None, 10):
                raise AutomationConfigurationError("完整市场扫描分钟偏移固定为 10。")
            schedule_kind = INTERVAL_MINUTES
            schedule_expression = str(minutes)
            config["interval_offset_minutes"] = 10
        elif current.job_type == PRE_CUTOFF_FULL_SCAN:
            self._reject_free_schedule(interval_minutes, offset_minutes)
            schedule_kind = DAILY_LOCAL_TIME
            schedule_expression = self._time_expression(
                policy.platform_cutoff_local_time,
                -5,
            )
            config["time_policy_version"] = policy.policy_version
        elif current.job_type == POST_CUTOFF_PULSE:
            self._reject_free_schedule(interval_minutes, offset_minutes)
            schedule_kind = DAILY_LOCAL_TIME
            schedule_expression = self._time_expression(
                policy.platform_cutoff_local_time,
                5,
            )
            config["time_policy_version"] = policy.policy_version
        elif current.job_type == PLATFORM_TRADE_DAY_SETTLEMENT:
            self._reject_free_schedule(interval_minutes, offset_minutes)
            schedule_kind = DAILY_LOCAL_TIME
            schedule_expression = self._time_expression(
                policy.seller_cutoff_local_time,
                0,
            )
            config["time_policy_version"] = policy.policy_version
        elif current.job_type == SALES_PLAN_INPUT_BUILD:
            if interval_minutes is not None:
                raise AutomationConfigurationError("销售计划输入不接受运行间隔。")
            offset = 5 if offset_minutes is None else int(offset_minutes)
            if not 5 <= offset <= 30:
                raise AutomationConfigurationError("销售计划输入后置偏移必须在 5 到 30 分钟之间。")
            schedule_kind = DAILY_LOCAL_TIME
            schedule_expression = self._time_expression(
                policy.seller_cutoff_local_time,
                offset,
            )
            config["settlement_offset_minutes"] = offset
            config["time_policy_version"] = policy.policy_version
        elif current.job_type == REVIEW_TIMEOUT_MAINTENANCE:
            if offset_minutes is not None:
                raise AutomationConfigurationError("复核超时维护不接受分钟偏移。")
            minutes = self._bounded_multiple(
                interval_minutes,
                current=current.schedule_expression,
                minimum=5,
                maximum=30,
                multiple=5,
                label="复核超时扫描间隔",
            )
            schedule_kind = INTERVAL_MINUTES
            schedule_expression = str(minutes)
        elif current.job_type == DAILY_TASK_GENERATION:
            if interval_minutes is not None:
                raise AutomationConfigurationError("每日任务生成不接受运行间隔。")
            offset = 5 if offset_minutes is None else int(offset_minutes)
            if not 0 <= offset <= 30:
                raise AutomationConfigurationError("每日任务生成后置偏移必须在 0 到 30 分钟之间。")
            plan_input_offset = 5
            plan_jobs = [
                item
                for item in self.automation.list_jobs(enabled_only=True)
                if item.job_type == SALES_PLAN_INPUT_BUILD
            ]
            if len(plan_jobs) == 1:
                plan_input_offset = int(
                    plan_jobs[0].config.get("settlement_offset_minutes") or 5
                )
            schedule_kind = DAILY_LOCAL_TIME
            schedule_expression = self._time_expression(
                policy.seller_cutoff_local_time,
                plan_input_offset + offset,
            )
            config["plan_input_offset_minutes"] = offset
            requested_sources = (
                source_allowlist
                if source_allowlist is not None
                else tuple(config.get("source_allowlist") or ())
            )
            sources = {
                str(value).strip().upper()
                for value in requested_sources
                if str(value).strip()
            }
            sources.discard("PRODUCTS")
            if not sources:
                sources = {"PRICE_RULES", "LISTING_RULES"}
            if not sources.issubset({"PRICE_RULES", "LISTING_RULES"}):
                raise AutomationConfigurationError("每日任务生成来源不在固定白名单中。")
            config["source_allowlist"] = ["PRODUCTS", *sorted(sources)]
            config["time_policy_version"] = policy.policy_version

        version = self._configuration_version(
            job_type=current.job_type,
            schedule_kind=schedule_kind,
            schedule_expression=schedule_expression,
            config=config,
        )
        config.update(
            {
                "configuration_schema": "pra-automation-config-v1",
                "configuration_version": version,
                "updated_by": principal.subject,
                "effective_at": now.isoformat(),
            }
        )
        schedule_changed = (
            schedule_kind != current.schedule_kind
            or schedule_expression != current.schedule_expression
        )
        successor_id = current.job_id
        if schedule_changed:
            successor_id = (
                "AUTOMATION-"
                + current.job_type.replace("_", "-")
                + "-V-"
                + version.removeprefix("sha256:")[:12].upper()
            )
        successor = replace(
            current,
            job_id=successor_id,
            enabled=bool(enabled),
            schedule_kind=schedule_kind,
            schedule_expression=schedule_expression,
            config=config,
            created_at=None,
            updated_at=None,
        )
        try:
            if schedule_changed and current.job_type == SALES_PLAN_INPUT_BUILD:
                daily_current, daily_successor = self._daily_successor_for_plan_change(
                    principal=principal,
                    policy=policy,
                    plan_successor=successor,
                    now=now,
                )
                return self.automation.replace_job_versions(
                    replacements=(
                        (current.job_id, successor),
                        (daily_current.job_id, daily_successor),
                    ),
                    now=now,
                )[0]
            if schedule_changed:
                return self.automation.replace_job_version(
                    previous_job_id=current.job_id,
                    successor=successor,
                    now=now,
                )
            return self.automation.upsert_job(successor, now=now)
        except (RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            raise AutomationConfigurationError(str(exc)) from exc

    def _daily_successor_for_plan_change(
        self,
        *,
        principal: Principal,
        policy: OperationalTimePolicy,
        plan_successor: AutomationJob,
        now: datetime,
    ) -> tuple[AutomationJob, AutomationJob]:
        platform_name = str(plan_successor.config.get("platform_name") or "")
        daily_jobs = [
            item
            for item in self.automation.list_jobs(enabled_only=True)
            if item.job_type == DAILY_TASK_GENERATION
            and str(item.config.get("platform_name") or "") == platform_name
        ]
        if len(daily_jobs) != 1:
            raise AutomationConfigurationError(
                "每日任务生成依赖版本不唯一，销售计划输入未修改。"
            )
        current = daily_jobs[0]
        daily_offset = int(current.config.get("plan_input_offset_minutes") or 5)
        plan_offset = int(
            plan_successor.config.get("settlement_offset_minutes") or 5
        )
        schedule_expression = self._time_expression(
            policy.seller_cutoff_local_time,
            plan_offset + daily_offset,
        )
        config = dict(current.config)
        for key in ("updated_by", "effective_at", "configuration_version"):
            config.pop(key, None)
        config.update(
            {
                "time_policy_version": policy.policy_version,
                "sales_plan_input_offset_minutes": plan_offset,
                "upstream_configuration_version": plan_successor.config[
                    "configuration_version"
                ],
            }
        )
        version = self._configuration_version(
            job_type=current.job_type,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression=schedule_expression,
            config=config,
        )
        config.update(
            {
                "configuration_schema": "pra-automation-config-v1",
                "configuration_version": version,
                "updated_by": principal.subject,
                "effective_at": now.isoformat(),
            }
        )
        successor_id = (
            "AUTOMATION-"
            + current.job_type.replace("_", "-")
            + "-V-"
            + version.removeprefix("sha256:")[:12].upper()
        )
        return current, replace(
            current,
            job_id=successor_id,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression=schedule_expression,
            config=config,
            created_at=None,
            updated_at=None,
        )

    @staticmethod
    def _configuration_version(
        *,
        job_type: str,
        schedule_kind: str,
        schedule_expression: str,
        config: dict[str, object],
    ) -> str:
        normalized = {
            "job_type": job_type,
            "platform_name": str(config.get("platform_name") or ""),
            "schedule_kind": schedule_kind,
            "schedule_expression": schedule_expression,
            "interval_offset_minutes": config.get("interval_offset_minutes"),
            "settlement_offset_minutes": config.get(
                "settlement_offset_minutes"
            ),
            "plan_input_offset_minutes": config.get(
                "plan_input_offset_minutes"
            ),
            "sales_plan_input_offset_minutes": config.get(
                "sales_plan_input_offset_minutes"
            ),
            "source_allowlist": config.get("source_allowlist"),
            "time_policy_version": config.get("time_policy_version"),
            "upstream_configuration_version": config.get(
                "upstream_configuration_version"
            ),
        }
        return "sha256:" + hashlib.sha256(
            json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def configure_inventory_alert(
        self,
        principal: Principal,
        *,
        scope_type: str,
        scope_key: str,
        enabled: bool,
        threshold_qty: int,
        repeat_interval_minutes: int,
        expected_version: int | None,
    ):
        self._authorize(principal)
        try:
            return self.inventory.save_alert_policy(
                scope_type=scope_type,
                scope_key=scope_key,
                enabled=enabled,
                threshold_qty=threshold_qty,
                repeat_interval_minutes=repeat_interval_minutes,
                updated_by=principal.subject,
                expected_version=expected_version,
                updated_at=self._now(),
            )
        except (RuntimeError, ValueError) as exc:
            raise AutomationConfigurationError(str(exc)) from exc

    def schedule_rerun(
        self,
        principal: Principal,
        *,
        job_id: str,
        target_trade_date: date,
        idempotency_key: str,
    ) -> tuple[AutomationRun, bool]:
        self._authorize(principal)
        job = self.automation.get_job(job_id.strip())
        if job is None or job.job_type not in RERUN_JOB_TYPES:
            raise AutomationConfigurationError("该方案不允许从 Web 补跑。")
        if not job.enabled:
            raise AutomationConfigurationError("自动化方案已停用，不能创建补跑。")
        key = idempotency_key.strip()
        if not 8 <= len(key) <= 200:
            raise AutomationConfigurationError("补跑幂等键无效，请刷新页面后重试。")
        now = self._now()
        policies = self.automation.load_operational_time_policies()
        registry = OperationalTimePolicyRegistry(policies)
        offset = (
            int(job.config.get("settlement_offset_minutes") or 5)
            if job.job_type == SALES_PLAN_INPUT_BUILD
            else 0
        )
        candidates: list[tuple[datetime, OperationalTimeContext]] = []
        operational_time = OperationalTimeService(policies=policies)
        for candidate_policy in registry.policies:
            local_timezone = ZoneInfo(candidate_policy.timezone_name)
            local_anchor = datetime.combine(
                target_trade_date - timedelta(days=1),
                candidate_policy.seller_cutoff_local_time,
                tzinfo=local_timezone,
            )
            candidate = (
                local_anchor + timedelta(minutes=offset)
            ).astimezone(timezone.utc)
            try:
                effective_policy = registry.select(candidate)
            except ValueError:
                continue
            if effective_policy.policy_version != candidate_policy.policy_version:
                continue
            candidate_context = operational_time.classify(candidate)
            if (
                candidate_context.platform_trade_date == target_trade_date
                and candidate_context.seller_operation_date == target_trade_date
            ):
                candidates.append((candidate, candidate_context))
        if len(candidates) != 1:
            raise AutomationConfigurationError("目标交易日无法由当前时间策略唯一派生。")
        scheduled_for, context = candidates[0]
        logical_run_key = "web-rerun:" + hashlib.sha256(
            key.encode("utf-8")
        ).hexdigest()
        try:
            return self.automation.ensure_run(
                job=job,
                scheduled_for=scheduled_for,
                time_context=context,
                initial_status=AutomationRunStatus.SCHEDULED,
                now=now,
                logical_run_key=logical_run_key,
                event_type="RUN_MANUALLY_SCHEDULED",
                event_payload={
                    "actor": principal.subject,
                    "target_trade_date": target_trade_date.isoformat(),
                    "request_source": "authenticated_web",
                    "platform_write_performed": False,
                },
            )
        except ValueError as exc:
            raise AutomationConfigurationError(str(exc)) from exc

    def _authorize(self, principal: Principal) -> None:
        if not self.authorization.allows(principal, Capability.MANAGE_BUSINESS):
            raise AutomationConfigurationError("当前账号没有自动化方案管理权限。")

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AutomationConfigurationError("自动化配置时钟必须包含时区。")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _bounded_multiple(
        supplied: int | None,
        *,
        current: str,
        minimum: int,
        maximum: int,
        multiple: int,
        label: str,
    ) -> int:
        try:
            value = int(current) if supplied is None else int(supplied)
        except (TypeError, ValueError) as exc:
            raise AutomationConfigurationError(f"{label}无效。") from exc
        if not minimum <= value <= maximum or value % multiple:
            raise AutomationConfigurationError(
                f"{label}必须在 {minimum} 到 {maximum} 分钟之间，且为 {multiple} 的倍数。"
            )
        return value

    @staticmethod
    def _reject_free_schedule(
        interval_minutes: int | None,
        offset_minutes: int | None,
    ) -> None:
        if interval_minutes is not None or offset_minutes is not None:
            raise AutomationConfigurationError("该方案时间只从交易日策略派生，不能单独修改。")

    @staticmethod
    def _time_expression(base, offset_minutes: int) -> str:
        anchor = datetime(2000, 1, 1, base.hour, base.minute)
        shifted = anchor + timedelta(minutes=offset_minutes)
        return shifted.strftime("%H:%M")
