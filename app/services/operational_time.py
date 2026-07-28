from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.enums import SellerPhase


DEFAULT_OPERATIONAL_TIME_POLICY_VERSION = "CN_SINGLE_PLATFORM_2026_V1"
DEFAULT_OPERATIONAL_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class OperationalTimePolicy:
    """Versioned cutoffs used to derive all operational business dates."""

    policy_version: str = DEFAULT_OPERATIONAL_TIME_POLICY_VERSION
    timezone_name: str = DEFAULT_OPERATIONAL_TIMEZONE
    platform_cutoff_local_time: time = time(hour=18)
    seller_cutoff_local_time: time = time(hour=20)
    peak_start_local_time: time = time(hour=16)

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be blank")
        try:
            ZoneInfo(self.timezone_name)
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Unknown operational timezone: {self.timezone_name}"
            ) from exc
        if not (
            self.peak_start_local_time
            < self.platform_cutoff_local_time
            < self.seller_cutoff_local_time
        ):
            raise ValueError(
                "Operational cutoffs must satisfy peak_start "
                "< platform_cutoff < seller_cutoff"
            )


@dataclass(frozen=True, slots=True)
class OperationalTimeContext:
    """One timezone-normalized event and its two business-date dimensions."""

    observed_at: datetime
    local_observed_at: datetime
    platform_trade_date: date
    seller_operation_date: date
    seller_phase: SellerPhase
    time_policy_version: str
    timezone_name: str


class OperationalTimeService:
    """Derive frozen 18:00/20:00 business semantics from aware datetimes."""

    def __init__(self, policy: OperationalTimePolicy | None = None) -> None:
        self.policy = policy or OperationalTimePolicy()
        self._timezone = ZoneInfo(self.policy.timezone_name)

    def classify(self, observed_at: datetime) -> OperationalTimeContext:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")

        local_observed_at = observed_at.astimezone(self._timezone)
        local_date = local_observed_at.date()
        local_time = local_observed_at.time().replace(tzinfo=None)

        platform_trade_date = local_date
        if local_time >= self.policy.platform_cutoff_local_time:
            platform_trade_date += timedelta(days=1)

        seller_operation_date = local_date
        if local_time >= self.policy.seller_cutoff_local_time:
            seller_operation_date += timedelta(days=1)

        if (
            self.policy.peak_start_local_time
            <= local_time
            < self.policy.platform_cutoff_local_time
        ):
            seller_phase = SellerPhase.PEAK_SALES
        elif (
            self.policy.platform_cutoff_local_time
            <= local_time
            < self.policy.seller_cutoff_local_time
        ):
            seller_phase = SellerPhase.DELIVERY_OVERLAP
        else:
            seller_phase = SellerPhase.NORMAL_SALES

        return OperationalTimeContext(
            observed_at=observed_at,
            local_observed_at=local_observed_at,
            platform_trade_date=platform_trade_date,
            seller_operation_date=seller_operation_date,
            seller_phase=seller_phase,
            time_policy_version=self.policy.policy_version,
            timezone_name=self.policy.timezone_name,
        )
