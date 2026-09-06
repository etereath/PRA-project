from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.enums import IncidentCategory, IncidentStatus
from app.models import EmergencyOfflinePolicy
from app.repositories.emergency_offline_policy_repository import (
    EmergencyOfflinePolicyRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import save_table_records
from app.services.emergency_offline_shadow import (
    EmergencyOfflinePolicyInterpreter,
    EmergencyOfflineShadowService,
    EmergencyPolicyEvaluationFacts,
)
from app.services.incident_management import IncidentNotificationService
from app.services.incident_management import (
    IncidentDetection,
    IncidentManagementService,
    IncidentPulseEligibility,
)

NOW = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)


def _policy(
    *,
    approved: bool = True,
    retired: bool = False,
    platform_name: str = "platform",
) -> EmergencyOfflinePolicy:
    return EmergencyOfflinePolicy(
        policy_version="POLICY-1",
        platform_name=platform_name,
        emergency_ratio=Decimal("0.80"),
        approved_by="admin" if approved else None,
        approved_at=NOW if approved else None,
        created_at=NOW - timedelta(minutes=1),
        retired_at=NOW + timedelta(minutes=1) if retired else None,
    )


def _pulse(
    *,
    eligible: bool = True,
    reason: str = "QUALIFIED_ONLINE_PULSE_IMPORTED",
    observed_price: Decimal | None = Decimal("8.00"),
) -> IncidentPulseEligibility:
    return IncidentPulseEligibility(
        incident_id="INCIDENT-1",
        review_task_id="REVIEW-1",
        eligible=eligible,
        reason=reason,
        pulse_run_id="PULSE-2" if eligible else None,
        observation_batch_id="BATCH-2" if eligible else None,
        observation_item_id="OBS-2" if eligible else None,
        pulse_scheduled_for=NOW if eligible else None,
        automatic_eligibility_reached_at=NOW if eligible else None,
        observed_price=observed_price,
        mapping_version="mapping-v1" if eligible else "",
    )


def _facts(**overrides) -> EmergencyPolicyEvaluationFacts:
    values = {
        "platform_name": "platform",
        "incident_category": IncidentCategory.PRICE_ANOMALY,
        "incident_severity": "S4",
        "incident_status": IncidentStatus.WAITING_HUMAN,
        "occurrence_count": 2,
        "policy": _policy(),
        "policy_canonical_sha256": "a" * 64,
        "base_cost": Decimal("10.00"),
        "base_cost_source_ref": "products.xlsx:sha256:test",
        "pulse": _pulse(),
        "write_lock_statuses": (),
        "feature_flag_enabled": False,
    }
    values.update(overrides)
    return EmergencyPolicyEvaluationFacts(**values)


@pytest.mark.parametrize(
    ("price", "occurrence_count", "severity", "allowlisted"),
    [
        (Decimal("10.00"), 1, "S1", False),
        (Decimal("10.00"), 2, "S2", False),
        (Decimal("8.01"), 2, "S3", False),
        (Decimal("8.00"), 2, "S4", True),
        (Decimal("7.99"), 2, "S4", True),
    ],
)
def test_decimal_price_boundaries(
    price: Decimal,
    occurrence_count: int,
    severity: str,
    allowlisted: bool,
) -> None:
    result = EmergencyOfflinePolicyInterpreter.classify_price(
        observed_price=price,
        base_cost=Decimal("10.00"),
        occurrence_count=occurrence_count,
    )

    assert result.severity == severity
    assert result.emergency_threshold == Decimal("8.0000")
    assert result.automatic_allowlisted is allowlisted


def test_shadow_reports_only_feature_flag_blocker_when_other_gates_pass() -> None:
    decision = EmergencyOfflinePolicyInterpreter().evaluate(_facts())

    assert decision.eligible_without_feature_flag
    assert not decision.authorization_eligible
    assert decision.automatic_allowlisted
    assert decision.blockers == ("FEATURE_FLAG_DISABLED",)


@pytest.mark.parametrize(
    ("overrides", "expected_blocker"),
    [
        ({"policy": None}, "POLICY_MISSING"),
        ({"policy": _policy(approved=False)}, "POLICY_NOT_APPROVED"),
        ({"policy": _policy(retired=True)}, "POLICY_RETIRED"),
        ({"base_cost": None}, "BASE_COST_MISSING"),
        ({"base_cost": Decimal("0")}, "BASE_COST_INVALID"),
        ({"base_cost_source_ref": ""}, "BASE_COST_SOURCE_MISSING"),
        (
            {"pulse": _pulse(eligible=False, reason="REVIEW_RESOLVED")},
            "PULSE_REVIEW_RESOLVED",
        ),
        (
            {
                "pulse": _pulse(
                    eligible=False,
                    reason="WAITING_FOR_QUALIFIED_ONLINE_PULSE",
                )
            },
            "PULSE_WAITING_FOR_QUALIFIED_ONLINE_PULSE",
        ),
        ({"write_lock_statuses": ("ACTIVE",)}, "WRITE_LOCK_ACTIVE"),
        ({"write_lock_statuses": ("UNKNOWN",)}, "WRITE_UNKNOWN"),
        ({"write_lock_statuses": ("REVIEW_BLOCKED",)}, "WRITE_REVIEW_BLOCKED"),
        ({"incident_severity": "S3"}, "INCIDENT_NOT_S4"),
        (
            {"incident_category": IncidentCategory.WRITE_UNKNOWN},
            "INCIDENT_NOT_PRICE_ANOMALY",
        ),
    ],
)
def test_shadow_fails_closed_for_each_frozen_gate(
    overrides: dict[str, object],
    expected_blocker: str,
) -> None:
    decision = EmergencyOfflinePolicyInterpreter().evaluate(_facts(**overrides))

    assert expected_blocker in decision.blockers
    assert not decision.eligible_without_feature_flag
    assert not decision.authorization_eligible


def test_s3_price_is_never_automatic_even_when_feature_flag_is_true() -> None:
    decision = EmergencyOfflinePolicyInterpreter().evaluate(
        _facts(
            pulse=_pulse(observed_price=Decimal("8.01")),
            feature_flag_enabled=True,
        )
    )

    assert decision.severity == "S3"
    assert not decision.automatic_allowlisted
    assert "PRICE_NOT_EXTREME_S4" in decision.blockers
    assert not decision.authorization_eligible


class _QualifiedPulseService:
    def __init__(self, pulse: IncidentPulseEligibility) -> None:
        self.pulse = pulse

    def evaluate_online_pulse_eligibility(
        self,
        incident_id: str,
        review_task_id: str,
        *,
        initial_observation_id: str | None = None,
    ) -> IncidentPulseEligibility:
        assert incident_id == self.pulse.incident_id
        assert review_task_id == self.pulse.review_task_id
        assert initial_observation_id == "OBS-1"
        return self.pulse


def _runtime_with_s4_incident(
    tmp_path: Path,
) -> tuple[SQLiteRuntimeRepository, str]:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    mutation = IncidentManagementService(runtime).detect(
        IncidentDetection(
            event_key="price-observation:OBS-1",
            category=IncidentCategory.PRICE_ANOMALY,
            source_type="PRODUCT_OBSERVATION",
            source_ref_id="OBS-1",
            severity="S4",
            blocks_finalization=False,
            platform_name="platform",
            platform_trade_date=date(2026, 8, 3),
            seller_operation_date=date(2026, 8, 3),
            subject_type="internal_sku",
            subject_key="SKU-1",
            title="extreme price",
            description="",
            occurred_at=NOW,
            reason="EXTREME_PRICE",
        )
    )
    return runtime, mutation.incident.incident_id


def _approve_policy(runtime: SQLiteRuntimeRepository) -> None:
    policies = EmergencyOfflinePolicyRepository(runtime)
    policies.create_draft(
        policy_version="POLICY-1",
        platform_name="platform",
        created_at=NOW,
    )
    policies.approve(
        "POLICY-1",
        approved_by="admin",
        approved_at=NOW + timedelta(seconds=1),
    )


def _products_path(
    tmp_path: Path,
    *,
    include_sku: bool = True,
    base_cost: str = "10.00",
) -> Path:
    path = tmp_path / "products.xlsx"
    rows = []
    if include_sku:
        rows.append(
            {
                "internal_sku": "SKU-1",
                "product_name": "艾莎",
                "grade": "B",
                "stem_length": "60cm",
                "unit": "扎",
                "base_cost": base_cost,
                "current_stock": "5",
                "sale_enabled": "True",
                "last_price": "",
                "recommended_price": "",
                "remark": "",
                "feature_season": "",
                "feature_color": "",
            }
        )
    save_table_records("products", path, rows)
    return path


def test_application_shadow_is_zero_task_zero_run_event_and_flag_stays_off(
    tmp_path: Path,
) -> None:
    runtime, incident_id = _runtime_with_s4_incident(tmp_path)
    _approve_policy(runtime)
    products_path = _products_path(tmp_path)
    pulse = _pulse()
    pulse = replace(pulse, incident_id=incident_id)
    service = EmergencyOfflineShadowService(
        runtime,
        review_service=_QualifiedPulseService(pulse),  # type: ignore[arg-type]
    )
    with closing(runtime.connect_read()) as connection:
        before = tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("tasks", "automation_run_events", "shadowbot_operations")
        )

    decision = service.evaluate(
        evaluation_id="SHADOW-1",
        incident_id=incident_id,
        review_task_id="REVIEW-1",
        products_path=products_path,
        evaluated_at=NOW + timedelta(minutes=10),
        initial_observation_id="OBS-1",
    )

    with closing(runtime.connect_read()) as connection:
        after = tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("tasks", "automation_run_events", "shadowbot_operations")
        )
    assert before == after == (0, 0, 0)
    assert decision.eligible_without_feature_flag
    assert decision.blockers == ("FEATURE_FLAG_DISABLED",)
    assert not decision.authorization_eligible
    assert decision.evaluation_mode == "SHADOW"


def test_default_shadow_service_uses_pulse_eligibility_service(
    tmp_path: Path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()

    service = EmergencyOfflineShadowService(runtime)

    assert isinstance(service.review_service, IncidentNotificationService)


def test_missing_base_cost_records_master_data_incident_and_replays(
    tmp_path: Path,
) -> None:
    runtime, incident_id = _runtime_with_s4_incident(tmp_path)
    _approve_policy(runtime)
    products_path = _products_path(tmp_path, include_sku=False)
    pulse = _pulse()
    pulse = replace(pulse, incident_id=incident_id)
    service = EmergencyOfflineShadowService(
        runtime,
        review_service=_QualifiedPulseService(pulse),  # type: ignore[arg-type]
    )
    arguments = {
        "evaluation_id": "SHADOW-MISSING-COST",
        "incident_id": incident_id,
        "review_task_id": "REVIEW-1",
        "products_path": products_path,
        "evaluated_at": NOW + timedelta(minutes=10),
        "initial_observation_id": "OBS-1",
    }

    first = service.evaluate(**arguments)
    replay = service.evaluate(**arguments)

    assert first == replay
    assert first.master_data_incident_id is not None
    assert "BASE_COST_MISSING" in first.blockers
    with closing(runtime.connect_read()) as connection:
        master_incidents = int(
            connection.execute(
                "SELECT COUNT(*) FROM operational_incidents "
                "WHERE category = 'MASTER_DATA'"
            ).fetchone()[0]
        )
        master_events = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM operational_incident_events AS event
                JOIN operational_incidents AS incident
                  ON incident.incident_id = event.incident_id
                WHERE incident.category = 'MASTER_DATA'
                """
            ).fetchone()[0]
        )
    assert master_incidents == 1
    assert master_events == 1


def test_invalid_authoritative_base_cost_records_master_data_incident(
    tmp_path: Path,
) -> None:
    runtime, incident_id = _runtime_with_s4_incident(tmp_path)
    _approve_policy(runtime)
    products_path = _products_path(tmp_path, base_cost="0")
    pulse = replace(_pulse(), incident_id=incident_id)
    service = EmergencyOfflineShadowService(
        runtime,
        review_service=_QualifiedPulseService(pulse),  # type: ignore[arg-type]
    )

    decision = service.evaluate(
        evaluation_id="SHADOW-INVALID-COST",
        incident_id=incident_id,
        review_task_id="REVIEW-1",
        products_path=products_path,
        evaluated_at=NOW + timedelta(minutes=10),
        initial_observation_id="OBS-1",
    )

    assert "BASE_COST_INVALID" in decision.blockers
    assert decision.master_data_incident_id is not None
    assert not decision.eligible_without_feature_flag
