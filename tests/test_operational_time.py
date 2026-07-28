from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from app.enums import SellerPhase
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
    OperationalTimePolicy,
    OperationalTimePolicyRegistry,
    OperationalTimeService,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    ("local_time", "platform_date", "seller_date", "phase"),
    [
        (
            time(15, 59, 59),
            date(2026, 7, 29),
            date(2026, 7, 29),
            SellerPhase.NORMAL_SALES,
        ),
        (
            time(16, 0, 0),
            date(2026, 7, 29),
            date(2026, 7, 29),
            SellerPhase.PEAK_SALES,
        ),
        (
            time(17, 59, 59),
            date(2026, 7, 29),
            date(2026, 7, 29),
            SellerPhase.PEAK_SALES,
        ),
        (
            time(18, 0, 0),
            date(2026, 7, 30),
            date(2026, 7, 29),
            SellerPhase.DELIVERY_OVERLAP,
        ),
        (
            time(19, 59, 59),
            date(2026, 7, 30),
            date(2026, 7, 29),
            SellerPhase.DELIVERY_OVERLAP,
        ),
        (
            time(20, 0, 0),
            date(2026, 7, 30),
            date(2026, 7, 30),
            SellerPhase.NORMAL_SALES,
        ),
    ],
)
def test_operational_time_boundaries(
    local_time: time,
    platform_date: date,
    seller_date: date,
    phase: SellerPhase,
) -> None:
    observed_at = datetime.combine(
        date(2026, 7, 29),
        local_time,
        tzinfo=SHANGHAI,
    )

    context = OperationalTimeService().classify(observed_at)

    assert context.platform_trade_date == platform_date
    assert context.seller_operation_date == seller_date
    assert context.seller_phase is phase
    assert context.time_policy_version == DEFAULT_OPERATIONAL_TIME_POLICY_VERSION
    assert context.timezone_name == "Asia/Shanghai"


def test_operational_time_normalizes_utc_before_classification() -> None:
    observed_at = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    context = OperationalTimeService().classify(observed_at)

    assert context.observed_at == observed_at
    assert context.local_observed_at == datetime(
        2026,
        7,
        29,
        18,
        0,
        tzinfo=SHANGHAI,
    )
    assert context.platform_trade_date == date(2026, 7, 30)
    assert context.seller_operation_date == date(2026, 7, 29)
    assert context.seller_phase is SellerPhase.DELIVERY_OVERLAP


def test_equivalent_timezone_inputs_produce_same_utc_context() -> None:
    utc_input = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    shanghai_input = datetime(
        2026,
        7,
        29,
        18,
        0,
        tzinfo=SHANGHAI,
    )
    service = OperationalTimeService()

    utc_context = service.classify(utc_input)
    shanghai_context = service.classify(shanghai_input)

    assert utc_context.observed_at == shanghai_context.observed_at
    assert utc_context.observed_at == utc_input
    assert utc_context.local_observed_at == shanghai_context.local_observed_at
    assert utc_context.platform_trade_date == shanghai_context.platform_trade_date
    assert utc_context.seller_operation_date == shanghai_context.seller_operation_date
    assert utc_context.seller_phase is shanghai_context.seller_phase


def test_operational_time_selects_policy_at_effective_boundary() -> None:
    boundary = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    policies = (
        OperationalTimePolicy(
            policy_version="V1",
            effective_to=boundary,
        ),
        OperationalTimePolicy(
            policy_version="V2",
            platform_cutoff_local_time=time(17),
            seller_cutoff_local_time=time(20),
            peak_start_local_time=time(16),
            effective_from=boundary,
        ),
    )
    service = OperationalTimeService(policies=policies)

    before = service.classify(
        datetime(2026, 7, 29, 9, 59, 59, tzinfo=timezone.utc)
    )
    at_boundary = service.classify(boundary)

    assert before.time_policy_version == "V1"
    assert before.platform_trade_date == date(2026, 7, 29)
    assert at_boundary.time_policy_version == "V2"
    assert at_boundary.platform_trade_date == date(2026, 7, 30)
    assert at_boundary.observed_at == boundary


def test_operational_time_policy_registry_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        OperationalTimePolicyRegistry(
            (
                OperationalTimePolicy(policy_version="V1"),
                OperationalTimePolicy(
                    policy_version="V2",
                    effective_from=datetime(
                        2026,
                        7,
                        29,
                        10,
                        0,
                        tzinfo=timezone.utc,
                    ),
                ),
            )
        )


def test_operational_time_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        OperationalTimeService().classify(datetime(2026, 7, 29, 18, 0))


@pytest.mark.parametrize(
    (
        "peak_start",
        "platform_cutoff",
        "seller_cutoff",
    ),
    [
        (time(18), time(18), time(20)),
        (time(16), time(20), time(20)),
        (time(20), time(18), time(16)),
    ],
)
def test_operational_time_policy_rejects_invalid_cutoff_order(
    peak_start: time,
    platform_cutoff: time,
    seller_cutoff: time,
) -> None:
    with pytest.raises(ValueError, match="peak_start"):
        OperationalTimePolicy(
            peak_start_local_time=peak_start,
            platform_cutoff_local_time=platform_cutoff,
            seller_cutoff_local_time=seller_cutoff,
        )
