from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.enums import SellerPhase


DEFAULT_OPERATIONAL_TIME_POLICY_VERSION = "CN_SINGLE_PLATFORM_2026_V1"
DEFAULT_OPERATIONAL_TIMEZONE = "Asia/Shanghai"
DEFAULT_OPERATIONAL_TIME_POLICY_EFFECTIVE_FROM = datetime(
    2025,
    12,
    31,
    16,
    0,
    tzinfo=timezone.utc,
)


@dataclass(frozen=True, slots=True)
class OperationalTimePolicy:
    """Versioned cutoffs used to derive all operational business dates."""

    policy_version: str = DEFAULT_OPERATIONAL_TIME_POLICY_VERSION
    timezone_name: str = DEFAULT_OPERATIONAL_TIMEZONE
    platform_cutoff_local_time: time = time(hour=18)
    seller_cutoff_local_time: time = time(hour=20)
    peak_start_local_time: time = time(hour=16)
    effective_from: datetime = DEFAULT_OPERATIONAL_TIME_POLICY_EFFECTIVE_FROM
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be blank")
        if self.timezone_name != DEFAULT_OPERATIONAL_TIMEZONE:
            raise ValueError(
                "Operational timezone must be Asia/Shanghai"
            )
        if not (
            self.peak_start_local_time
            < self.platform_cutoff_local_time
            < self.seller_cutoff_local_time
        ):
            raise ValueError(
                "Operational cutoffs must satisfy peak_start "
                "< platform_cutoff < seller_cutoff"
            )
        effective_from = _as_utc(self.effective_from, "effective_from")
        effective_to = (
            _as_utc(self.effective_to, "effective_to")
            if self.effective_to is not None
            else None
        )
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be later than effective_from")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)


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


class OperationalTimePolicyRegistry:
    """Select exactly one non-overlapping policy for an observed instant."""

    def __init__(self, policies: Iterable[OperationalTimePolicy]) -> None:
        ordered = tuple(
            sorted(
                policies,
                key=lambda policy: (
                    policy.effective_from,
                    policy.policy_version,
                ),
            )
        )
        if not ordered:
            raise ValueError("At least one operational time policy is required")
        versions = [policy.policy_version for policy in ordered]
        if len(set(versions)) != len(versions):
            raise ValueError("Operational time policy versions must be unique")
        for previous, current in zip(ordered, ordered[1:]):
            if (
                previous.effective_to is None
                or current.effective_from < previous.effective_to
            ):
                raise ValueError(
                    "Operational time policy effective ranges must not overlap"
                )
        self._policies = ordered

    @property
    def policies(self) -> tuple[OperationalTimePolicy, ...]:
        return self._policies

    def select(self, observed_at: datetime) -> OperationalTimePolicy:
        observed_utc = _as_utc(observed_at, "observed_at")
        matches = tuple(
            policy
            for policy in self._policies
            if policy.effective_from <= observed_utc
            and (
                policy.effective_to is None
                or observed_utc < policy.effective_to
            )
        )
        if len(matches) != 1:
            raise ValueError(
                "Exactly one operational time policy must be effective "
                "for observed_at"
            )
        return matches[0]


class OperationalTimeService:
    """Derive frozen 18:00/20:00 business semantics from aware datetimes."""

    def __init__(
        self,
        policy: OperationalTimePolicy | None = None,
        *,
        policies: Iterable[OperationalTimePolicy] | None = None,
    ) -> None:
        if policy is not None and policies is not None:
            raise ValueError("Pass policy or policies, not both")
        selected_policies = (
            tuple(policies)
            if policies is not None
            else (policy or OperationalTimePolicy(),)
        )
        self.policy_registry = OperationalTimePolicyRegistry(
            selected_policies
        )

    def classify(self, observed_at: datetime) -> OperationalTimeContext:
        observed_utc = _as_utc(observed_at, "observed_at")
        policy = self.policy_registry.select(observed_utc)
        operational_timezone = ZoneInfo(policy.timezone_name)
        local_observed_at = observed_utc.astimezone(operational_timezone)
        local_date = local_observed_at.date()
        local_time = local_observed_at.time().replace(tzinfo=None)

        platform_trade_date = local_date
        if local_time >= policy.platform_cutoff_local_time:
            platform_trade_date += timedelta(days=1)

        seller_operation_date = local_date
        if local_time >= policy.seller_cutoff_local_time:
            seller_operation_date += timedelta(days=1)

        if (
            policy.peak_start_local_time
            <= local_time
            < policy.platform_cutoff_local_time
        ):
            seller_phase = SellerPhase.PEAK_SALES
        elif (
            policy.platform_cutoff_local_time
            <= local_time
            < policy.seller_cutoff_local_time
        ):
            seller_phase = SellerPhase.DELIVERY_OVERLAP
        else:
            seller_phase = SellerPhase.NORMAL_SALES

        return OperationalTimeContext(
            observed_at=observed_utc,
            local_observed_at=local_observed_at,
            platform_trade_date=platform_trade_date,
            seller_operation_date=seller_operation_date,
            seller_phase=seller_phase,
            time_policy_version=policy.policy_version,
            timezone_name=policy.timezone_name,
        )


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
