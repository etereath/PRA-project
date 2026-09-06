from __future__ import annotations

import hashlib
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.enums import IncidentCategory, IncidentStatus
from app.exceptions import ValidationError
from app.models import EmergencyOfflinePolicy
from app.repositories.emergency_offline_policy_repository import (
    EMERGENCY_RATIO,
    EmergencyOfflinePolicyRepository,
)
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import load_products
from app.services.incident_management import (
    IncidentDetection,
    IncidentManagementService,
    IncidentNotificationService,
    IncidentPulseEligibility,
)
from app.shadowbot_contract_primitives import contract_identity_key


@dataclass(frozen=True, slots=True)
class EmergencyPriceClassification:
    severity: str
    reason: str
    emergency_threshold: Decimal
    automatic_allowlisted: bool


@dataclass(frozen=True, slots=True)
class EmergencyPolicyEvaluationFacts:
    platform_name: str
    incident_category: IncidentCategory
    incident_severity: str
    incident_status: IncidentStatus
    occurrence_count: int
    policy: EmergencyOfflinePolicy | None
    policy_canonical_sha256: str
    base_cost: Decimal | None
    base_cost_source_ref: str
    pulse: IncidentPulseEligibility
    write_lock_statuses: tuple[str, ...] = ()
    feature_flag_enabled: bool = False


@dataclass(frozen=True, slots=True)
class EmergencyOfflineShadowDecision:
    evaluation_mode: str
    incident_id: str
    review_task_id: str
    eligible_without_feature_flag: bool
    authorization_eligible: bool
    automatic_allowlisted: bool
    blockers: tuple[str, ...]
    severity: str | None
    price_reason: str
    policy_version: str | None
    policy_canonical_sha256: str
    emergency_ratio: Decimal
    base_cost: Decimal | None
    base_cost_source_ref: str
    emergency_threshold: Decimal | None
    second_observed_price: Decimal | None
    first_observation_id: str
    second_observation_id: str
    pulse_run_id: str | None
    mapping_version: str
    master_data_incident_id: str | None = None


class EmergencyOfflinePolicyInterpreter:
    """Pure 6B decision engine shared by shadow and the future 6C gate."""

    @staticmethod
    def classify_price(
        *,
        observed_price: Decimal,
        base_cost: Decimal,
        occurrence_count: int,
    ) -> EmergencyPriceClassification:
        price = _require_nonnegative_decimal(observed_price, "observed_price")
        cost = _require_positive_decimal(base_cost, "base_cost")
        if occurrence_count < 1:
            raise ValueError("occurrence_count must be at least 1")
        threshold = cost * EMERGENCY_RATIO
        if price <= threshold:
            return EmergencyPriceClassification(
                severity="S4",
                reason="EXTREME_PRICE_AT_OR_BELOW_80_PERCENT_OF_BASE_COST",
                emergency_threshold=threshold,
                automatic_allowlisted=True,
            )
        if price < cost:
            return EmergencyPriceClassification(
                severity="S3",
                reason="PRICE_BELOW_BASE_COST",
                emergency_threshold=threshold,
                automatic_allowlisted=False,
            )
        if occurrence_count == 1:
            return EmergencyPriceClassification(
                severity="S1",
                reason="FIRST_ANOMALOUS_PRICE_AT_OR_ABOVE_BASE_COST",
                emergency_threshold=threshold,
                automatic_allowlisted=False,
            )
        return EmergencyPriceClassification(
            severity="S2",
            reason="REPEATED_ANOMALOUS_PRICE_AT_OR_ABOVE_BASE_COST",
            emergency_threshold=threshold,
            automatic_allowlisted=False,
        )

    def evaluate(
        self,
        facts: EmergencyPolicyEvaluationFacts,
    ) -> EmergencyOfflineShadowDecision:
        blockers: list[str] = []
        if facts.incident_category is not IncidentCategory.PRICE_ANOMALY:
            blockers.append("INCIDENT_NOT_PRICE_ANOMALY")
        if facts.incident_severity != "S4":
            blockers.append("INCIDENT_NOT_S4")
        if facts.incident_status not in {
            IncidentStatus.OPEN,
            IncidentStatus.WAITING_HUMAN,
        }:
            blockers.append("INCIDENT_NOT_ACTIVE")

        policy = facts.policy
        if policy is None:
            blockers.append("POLICY_MISSING")
        else:
            if policy.platform_name != facts.platform_name:
                blockers.append("POLICY_PLATFORM_MISMATCH")
            if not policy.is_approved:
                blockers.append("POLICY_NOT_APPROVED")
            if policy.retired_at is not None:
                blockers.append("POLICY_RETIRED")
            if policy.emergency_ratio != EMERGENCY_RATIO:
                blockers.append("POLICY_RATIO_INVALID")

        classification: EmergencyPriceClassification | None = None
        cost = _optional_positive_decimal(facts.base_cost)
        if cost is None:
            blockers.append(
                "BASE_COST_MISSING"
                if facts.base_cost is None
                else "BASE_COST_INVALID"
            )
        if not facts.base_cost_source_ref.strip():
            blockers.append("BASE_COST_SOURCE_MISSING")
        price = _optional_nonnegative_decimal(facts.pulse.observed_price)
        if price is None:
            blockers.append("SECOND_PRICE_UNREADABLE")
        if cost is not None and price is not None:
            classification = self.classify_price(
                observed_price=price,
                base_cost=cost,
                occurrence_count=facts.occurrence_count,
            )
            if not classification.automatic_allowlisted:
                blockers.append("PRICE_NOT_EXTREME_S4")

        if not facts.pulse.eligible:
            blockers.append(f"PULSE_{facts.pulse.reason}")

        lock_statuses = set(facts.write_lock_statuses)
        if "ACTIVE" in lock_statuses:
            blockers.append("WRITE_LOCK_ACTIVE")
        if "UNKNOWN" in lock_statuses:
            blockers.append("WRITE_UNKNOWN")
        if "REVIEW_BLOCKED" in lock_statuses:
            blockers.append("WRITE_REVIEW_BLOCKED")
        if not facts.feature_flag_enabled:
            blockers.append("FEATURE_FLAG_DISABLED")

        normalized_blockers = tuple(dict.fromkeys(blockers))
        non_flag_blockers = tuple(
            blocker
            for blocker in normalized_blockers
            if blocker != "FEATURE_FLAG_DISABLED"
        )
        return EmergencyOfflineShadowDecision(
            evaluation_mode="SHADOW",
            incident_id=facts.pulse.incident_id,
            review_task_id=facts.pulse.review_task_id,
            eligible_without_feature_flag=not non_flag_blockers,
            authorization_eligible=not normalized_blockers,
            automatic_allowlisted=(
                classification.automatic_allowlisted
                if classification is not None
                else False
            ),
            blockers=normalized_blockers,
            severity=(classification.severity if classification else None),
            price_reason=(classification.reason if classification else ""),
            policy_version=(policy.policy_version if policy else None),
            policy_canonical_sha256=facts.policy_canonical_sha256,
            emergency_ratio=EMERGENCY_RATIO,
            base_cost=cost,
            base_cost_source_ref=facts.base_cost_source_ref,
            emergency_threshold=(
                classification.emergency_threshold if classification else None
            ),
            second_observed_price=price,
            first_observation_id="",
            second_observation_id=facts.pulse.observation_item_id or "",
            pulse_run_id=facts.pulse.pulse_run_id,
            mapping_version=facts.pulse.mapping_version,
        )


class EmergencyOfflineShadowService:
    """Read-only application service; it can only return shadow evidence."""

    def __init__(
        self,
        runtime_repository: SQLiteRuntimeRepository,
        *,
        review_service: IncidentNotificationService | None = None,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.incidents = OperationalIncidentRepository(runtime_repository)
        self.incident_service = IncidentManagementService(runtime_repository)
        self.policies = EmergencyOfflinePolicyRepository(runtime_repository)
        self.review_service = review_service or IncidentNotificationService(
            runtime_repository
        )
        self.interpreter = EmergencyOfflinePolicyInterpreter()

    def evaluate(
        self,
        *,
        evaluation_id: str,
        incident_id: str,
        review_task_id: str,
        products_path: Path,
        evaluated_at: datetime,
        policy_version: str | None = None,
        initial_observation_id: str | None = None,
    ) -> EmergencyOfflineShadowDecision:
        if not evaluation_id.strip():
            raise ValueError("evaluation_id is required")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        if incident.platform_name is None:
            raise ValueError("Incident has no platform_name")

        policy = (
            self.policies.get(policy_version)
            if policy_version is not None
            else self.policies.get_active(incident.platform_name)
        )
        policy_sha256 = (
            self.policies.canonical_sha256(policy) if policy is not None else ""
        )
        base_cost, base_cost_source_ref, base_cost_error_reason = (
            self._read_authoritative_base_cost(
                products_path,
                internal_sku=incident.subject_key,
            )
        )
        pulse = self.review_service.evaluate_online_pulse_eligibility(
            incident_id,
            review_task_id,
            initial_observation_id=initial_observation_id,
        )
        lock_statuses = self._write_lock_statuses(
            incident.platform_name,
            incident.subject_key,
        )
        decision = self.interpreter.evaluate(
            EmergencyPolicyEvaluationFacts(
                platform_name=incident.platform_name,
                incident_category=incident.category,
                incident_severity=incident.severity,
                incident_status=incident.incident_status,
                occurrence_count=incident.occurrence_count,
                policy=policy,
                policy_canonical_sha256=policy_sha256,
                base_cost=base_cost,
                base_cost_source_ref=base_cost_source_ref,
                pulse=pulse,
                write_lock_statuses=lock_statuses,
                feature_flag_enabled=False,
            )
        )
        master_data_incident_id = self._record_invalid_base_cost(
            evaluation_id=evaluation_id,
            source_incident_id=incident_id,
            platform_name=incident.platform_name,
            platform_trade_date=incident.platform_trade_date,
            seller_operation_date=incident.seller_operation_date,
            internal_sku=incident.subject_key,
            base_cost=base_cost,
            base_cost_source_ref=base_cost_source_ref,
            base_cost_error_reason=base_cost_error_reason,
            evaluated_at=evaluated_at,
        )
        return replace(
            decision,
            first_observation_id=(initial_observation_id or incident.source_ref_id),
            master_data_incident_id=master_data_incident_id,
        )

    @staticmethod
    def _read_authoritative_base_cost(
        products_path: Path,
        *,
        internal_sku: str,
    ) -> tuple[Decimal | None, str, str]:
        try:
            content_before = products_path.read_bytes()
            products = load_products(products_path)
            content_after = products_path.read_bytes()
        except (OSError, ValueError, ValidationError):
            return None, "", "PRODUCT_MASTER_UNAVAILABLE"
        if content_before != content_after:
            return None, "", "PRODUCT_MASTER_CHANGED_DURING_READ"
        product = next(
            (
                candidate
                for candidate in products
                if candidate.internal_sku == internal_sku
            ),
            None,
        )
        if product is None:
            return None, "", "PRODUCT_NOT_FOUND"
        content_sha256 = hashlib.sha256(content_before).hexdigest()
        return (
            product.base_cost,
            f"{products_path.name}:sha256:{content_sha256}",
            "",
        )

    def _write_lock_statuses(
        self,
        platform_name: str,
        internal_sku: str,
    ) -> tuple[str, ...]:
        write_identity_key = contract_identity_key(
            platform_name,
            internal_sku,
            None,
            None,
        )
        with closing(self.runtime_repository.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT status FROM shadowbot_write_locks
                WHERE write_identity_key = ?
                  AND status IN ('ACTIVE', 'UNKNOWN', 'REVIEW_BLOCKED')
                """,
                (write_identity_key,),
            ).fetchall()
        return tuple(sorted(str(row["status"]) for row in rows))

    def _record_invalid_base_cost(
        self,
        *,
        evaluation_id: str,
        source_incident_id: str,
        platform_name: str,
        platform_trade_date,
        seller_operation_date,
        internal_sku: str,
        base_cost: Decimal | None,
        base_cost_source_ref: str,
        base_cost_error_reason: str,
        evaluated_at: datetime,
    ) -> str | None:
        if (
            _optional_positive_decimal(base_cost) is not None
            and base_cost_source_ref.strip()
        ):
            return None
        reason = base_cost_error_reason or (
            "BASE_COST_MISSING"
            if base_cost is None
            else "BASE_COST_INVALID"
            if _optional_positive_decimal(base_cost) is None
            else "BASE_COST_SOURCE_MISSING"
        )
        mutation = self.incident_service.detect(
            IncidentDetection(
                event_key=f"emergency-shadow-master-data:{evaluation_id}",
                category=IncidentCategory.MASTER_DATA,
                source_type="EMERGENCY_POLICY_SHADOW",
                source_ref_id=evaluation_id,
                severity="S3",
                blocks_finalization=False,
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
                seller_operation_date=seller_operation_date,
                subject_type="internal_sku",
                subject_key=internal_sku,
                title="商品基础成本不可用于紧急保护判定",
                description="基础成本缺失、非法或没有可追溯来源，已停止自动保护评估。",
                occurred_at=evaluated_at,
                reason=reason,
                payload={
                    "source_incident_id": source_incident_id,
                    "base_cost_source_ref": base_cost_source_ref,
                },
            )
        )
        return mutation.incident.incident_id


def _optional_positive_decimal(value: object) -> Decimal | None:
    try:
        normalized = Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None
    if normalized is None or not normalized.is_finite() or normalized <= 0:
        return None
    return normalized


def _optional_nonnegative_decimal(value: object) -> Decimal | None:
    try:
        normalized = Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None
    if normalized is None or not normalized.is_finite() or normalized < 0:
        return None
    return normalized


def _require_positive_decimal(value: object, field: str) -> Decimal:
    normalized = _optional_positive_decimal(value)
    if normalized is None:
        raise ValueError(f"{field} must be finite and positive")
    return normalized


def _require_nonnegative_decimal(value: object, field: str) -> Decimal:
    normalized = _optional_nonnegative_decimal(value)
    if normalized is None:
        raise ValueError(f"{field} must be finite and non-negative")
    return normalized
