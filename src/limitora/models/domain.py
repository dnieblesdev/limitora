"""Immutable, side-effect-free provider status domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum


class ProviderState(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_ERROR = "transient_error"
    INVALID_DATA = "invalid_data"


class ValueAvailability(str, Enum):
    KNOWN = "known"
    UNLIMITED = "unlimited"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_AUTHORIZED = "not_authorized"
    NOT_APPLICABLE = "not_applicable"
    INVALID = "invalid"
    ERROR = "error"


class WindowKind(str, Enum):
    COMMERCIAL_QUOTA = "commercial_quota"
    TECHNICAL_RATE_LIMIT = "technical_rate_limit"
    OTHER = "other"


class MetricKind(str, Enum):
    COMMERCIAL_QUOTA = "commercial_quota"
    TECHNICAL_RATE_LIMIT = "technical_rate_limit"
    TOKENS = "tokens"
    BALANCE = "balance"


class RateLimitResetType(str, Enum):
    CODEX_RATE_LIMITS = "codex_rate_limits"
    UNKNOWN = "unknown"


class RateLimitResetCreditStatus(str, Enum):
    AVAILABLE = "available"
    REDEEMING = "redeeming"
    REDEEMED = "redeemed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderId:
    value: str

    def __post_init__(self) -> None:
        if not self.value or self.value.strip() != self.value:
            raise ValueError("provider identifier must be a non-empty trimmed string")


@dataclass(frozen=True)
class SourceMetadata:
    reference: str

    def __post_init__(self) -> None:
        if not self.reference or self.reference.strip() != self.reference:
            raise ValueError("source reference must be a non-empty trimmed string")


@dataclass(frozen=True)
class Percentage:
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("percentage value must be a Decimal")
        if not Decimal("0") <= self.value <= Decimal("100"):
            raise ValueError("percentage must be between 0 and 100")

    @classmethod
    def from_ratio(cls, numerator: Decimal, denominator: Decimal) -> "Percentage":
        if numerator < 0 or denominator <= 0 or numerator > denominator:
            raise ValueError("percentage ratio must be within a non-negative total")
        return cls(numerator * Decimal("100") / denominator)


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    metric: MetricKind
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("quantity value must be a Decimal")
        if self.value < 0:
            raise ValueError("quantity cannot be negative")
        if not self.unit or self.unit.strip() != self.unit:
            raise ValueError("quantity unit must be a non-empty trimmed string")


def _require_aware(timestamp: datetime, name: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_identity(value: str, name: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{name} must be a non-empty trimmed string")


_PLANLESS_COMMERCIAL_SOURCE = "opencode-go-dashboard"


@dataclass(frozen=True)
class ProviderStatus:
    provider_id: ProviderId
    state: ProviderState
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True)
class QuotaWindow:
    kind: WindowKind
    scope: str
    period: str
    plan_id: str | None
    availability: ValueAvailability
    source: SourceMetadata
    limit: Quantity | None = None
    used: Quantity | None = None
    remaining: Quantity | None = None
    reset_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_identity(self.scope, "scope")
        _require_identity(self.period, "period")
        if self.plan_id is not None:
            _require_identity(self.plan_id, "plan_id")
        if self.reset_at is not None:
            _require_aware(self.reset_at, "reset_at")
        values = tuple(value for value in (self.limit, self.used, self.remaining) if value)
        if self.availability is ValueAvailability.KNOWN:
            if not values:
                raise ValueError("known window requires a quantity")
            if (
                self.kind is WindowKind.COMMERCIAL_QUOTA
                and self.plan_id is None
                and self.source.reference != _PLANLESS_COMMERCIAL_SOURCE
            ):
                raise ValueError("known commercial window requires a plan identifier")
            expected = MetricKind(self.kind.value) if self.kind is not WindowKind.OTHER else None
            if expected is not None and any(value.metric is not expected for value in values):
                raise ValueError("window quantities must match its kind")
            if len({(value.metric, value.unit) for value in values}) != 1:
                raise ValueError("window quantities must use the same metric and unit")
            if self.limit is not None:
                if self.used is not None and self.used.value > self.limit.value:
                    raise ValueError("used quantity cannot exceed limit")
                if self.remaining is not None and self.remaining.value > self.limit.value:
                    raise ValueError("remaining quantity cannot exceed limit")
                if self.used is not None and self.remaining is not None:
                    if self.used.value + self.remaining.value != self.limit.value:
                        raise ValueError("used and remaining quantities must equal limit")
        elif values or self.reset_at is not None:
            raise ValueError("non-known window cannot contain numeric values or a reset")

    @property
    def remaining_percentage(self) -> Percentage | None:
        if self.availability is not ValueAvailability.KNOWN:
            return None
        if self.limit is None or self.remaining is None:
            return None
        return Percentage.from_ratio(self.remaining.value, self.limit.value)

    @property
    def used_percentage(self) -> Percentage | None:
        if self.availability is not ValueAvailability.KNOWN:
            return None
        if self.limit is None or self.used is None:
            return None
        return Percentage.from_ratio(self.used.value, self.limit.value)

    @property
    def unit(self) -> str | None:
        for value in (self.limit, self.used, self.remaining):
            if value is not None:
                return value.unit
        return None


@dataclass(frozen=True)
class UsageSnapshot:
    provider_id: ProviderId
    observed_at: datetime
    availability: ValueAvailability
    source: SourceMetadata
    token_limit: Quantity | None = None
    token_used: Quantity | None = None
    balance: Quantity | None = None

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        values = tuple(value for value in (self.token_limit, self.token_used, self.balance) if value)
        if self.availability is ValueAvailability.KNOWN:
            if not values:
                raise ValueError("known usage requires a quantity")
            if self.token_limit is not None and self.token_limit.metric is not MetricKind.TOKENS:
                raise ValueError("token limit must use the tokens metric")
            if self.token_used is not None and self.token_used.metric is not MetricKind.TOKENS:
                raise ValueError("token used must use the tokens metric")
            if self.balance is not None and self.balance.metric is not MetricKind.BALANCE:
                raise ValueError("balance must use the balance metric")
            if self.token_limit is not None and self.token_used is not None:
                if self.token_limit.unit != self.token_used.unit:
                    raise ValueError("token quantities must use the same unit")
                if self.token_used.value > self.token_limit.value:
                    raise ValueError("token usage cannot exceed its limit")
        elif values:
            raise ValueError("non-known usage cannot contain numeric values")

    @property
    def token_used_percentage(self) -> Percentage | None:
        if self.token_limit is None or self.token_used is None:
            return None
        return Percentage.from_ratio(self.token_used.value, self.token_limit.value)

    @property
    def token_remaining_percentage(self) -> Percentage | None:
        if self.token_limit is None or self.token_used is None:
            return None
        return Percentage.from_ratio(self.token_limit.value - self.token_used.value, self.token_limit.value)


@dataclass(frozen=True)
class RateLimitResetCredit:
    reset_type: RateLimitResetType
    status: RateLimitResetCreditStatus
    granted_at: datetime
    expires_at: datetime | None
    title: str | None
    description: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.reset_type, RateLimitResetType):
            raise TypeError("reset_type must be a RateLimitResetType")
        if not isinstance(self.status, RateLimitResetCreditStatus):
            raise TypeError("status must be a RateLimitResetCreditStatus")
        if not isinstance(self.granted_at, datetime):
            raise TypeError("granted_at must be a datetime")
        _require_aware(self.granted_at, "granted_at")
        if self.expires_at is not None:
            if not isinstance(self.expires_at, datetime):
                raise TypeError("expires_at must be a datetime or None")
            _require_aware(self.expires_at, "expires_at")
        for value, name in ((self.title, "title"), (self.description, "description")):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")


@dataclass(frozen=True)
class RateLimitResetCreditsSummary:
    available_count: int
    credits: tuple[RateLimitResetCredit, ...] | None

    def __post_init__(self) -> None:
        if type(self.available_count) is not int:
            raise TypeError("available_count must be an integer")
        if self.available_count < 0:
            raise ValueError("available_count cannot be negative")
        if self.credits is not None:
            if not isinstance(self.credits, tuple):
                raise TypeError("credits must be a tuple or None")
            if any(not isinstance(credit, RateLimitResetCredit) for credit in self.credits):
                raise TypeError("credits must contain RateLimitResetCredit values")


@dataclass(frozen=True)
class ProviderSnapshot:
    provider_id: ProviderId
    status: ProviderStatus
    fetched_at: datetime
    data_at: datetime
    source: SourceMetadata
    quota_windows: tuple[QuotaWindow, ...] = ()
    usage: UsageSnapshot | None = None
    rate_limit_reset_credits: RateLimitResetCreditsSummary | None = None

    def __post_init__(self) -> None:
        _require_aware(self.fetched_at, "fetched_at")
        _require_aware(self.data_at, "data_at")
        if self.status.observed_at > self.fetched_at or self.data_at > self.fetched_at:
            raise ValueError("observed data cannot be newer than its fetch")
        if self.status.provider_id != self.provider_id:
            raise ValueError("status provider must match snapshot provider")
        if self.usage is not None and self.usage.provider_id != self.provider_id:
            raise ValueError("usage provider must match snapshot provider")
        if self.rate_limit_reset_credits is not None and not isinstance(
            self.rate_limit_reset_credits, RateLimitResetCreditsSummary
        ):
            raise TypeError("rate_limit_reset_credits must be a RateLimitResetCreditsSummary or None")
        if len({(window.kind, window.scope, window.period) for window in self.quota_windows}) != len(self.quota_windows):
            raise ValueError("snapshot cannot contain ambiguous quota windows")
        if self.status.state is ProviderState.RATE_LIMITED:
            if any(window.kind is not WindowKind.TECHNICAL_RATE_LIMIT for window in self.quota_windows):
                raise ValueError("rate-limited snapshots can only contain technical windows")
        states_without_numeric_evidence = {
            ProviderState.UNAVAILABLE,
            ProviderState.UNAUTHORIZED,
            ProviderState.TRANSIENT_ERROR,
            ProviderState.INVALID_DATA,
        }
        if self.status.state in states_without_numeric_evidence:
            has_known_window = any(
                window.availability is ValueAvailability.KNOWN for window in self.quota_windows
            )
            has_known_usage = self.usage is not None and self.usage.availability is ValueAvailability.KNOWN
            if has_known_window or has_known_usage:
                raise ValueError("terminal status cannot contain known numeric evidence")

    def is_stale(self, now: datetime, maximum_age: timedelta) -> bool:
        _require_aware(now, "now")
        if maximum_age < timedelta(0):
            raise ValueError("maximum age cannot be negative")
        return now - self.fetched_at > maximum_age


class ExclusionReason(str, Enum):
    STATUS = "status"
    ABSENT = "absent"
    INCOMPATIBLE = "incompatible"
    INCOMPATIBLE_COMPARABILITY = "incompatible_comparability"
    AMBIGUOUS = "ambiguous"
    STALE = "stale"
    NO_PERCENTAGE = "no_percentage"


@dataclass(frozen=True)
class AggregateExclusion:
    provider_id: ProviderId
    reason: ExclusionReason


@dataclass(frozen=True)
class RemainingAggregate:
    kind: WindowKind
    scope: str
    period: str
    plan_id: str | None
    unit: str
    remaining_percentage: Percentage | None
    included_provider_ids: tuple[ProviderId, ...]
    exclusions: tuple[AggregateExclusion, ...]

    @property
    def partial(self) -> bool:
        return bool(self.exclusions)


def aggregate_remaining_percentages(
    snapshots: tuple[ProviderSnapshot, ...],
    *,
    kind: WindowKind,
    scope: str,
    period: str,
    plan_id: str | None,
    unit: str,
    now: datetime,
    maximum_age: timedelta,
) -> RemainingAggregate:
    """Return the lowest comparable remaining percentage without selecting providers."""
    if kind is WindowKind.OTHER:
        raise ValueError("other window kinds are not globally comparable")
    _require_identity(scope, "scope")
    _require_identity(period, "period")
    _require_identity(unit, "unit")
    if plan_id is not None:
        _require_identity(plan_id, "plan_id")
    _require_aware(now, "now")
    if maximum_age < timedelta(0):
        raise ValueError("maximum age cannot be negative")

    included: list[tuple[ProviderId, Percentage]] = []
    exclusions: list[AggregateExclusion] = []
    eligible_states = {ProviderState.AVAILABLE, ProviderState.PARTIAL, ProviderState.RATE_LIMITED}
    for snapshot in snapshots:
        if snapshot.status.state not in eligible_states:
            exclusions.append(AggregateExclusion(snapshot.provider_id, ExclusionReason.STATUS))
            continue
        if snapshot.is_stale(now, maximum_age):
            exclusions.append(AggregateExclusion(snapshot.provider_id, ExclusionReason.STALE))
            continue
        matching = tuple(window for window in snapshot.quota_windows if window.kind is kind)
        compatible = tuple(
            window for window in matching if window.scope == scope and window.period == period
        )
        if len(compatible) > 1:
            exclusions.append(AggregateExclusion(snapshot.provider_id, ExclusionReason.AMBIGUOUS))
            continue
        if not compatible:
            reason = ExclusionReason.INCOMPATIBLE if matching or snapshot.quota_windows else ExclusionReason.ABSENT
            exclusions.append(AggregateExclusion(snapshot.provider_id, reason))
            continue
        window = compatible[0]
        if window.availability is not ValueAvailability.KNOWN:
            exclusions.append(AggregateExclusion(snapshot.provider_id, ExclusionReason.ABSENT))
            continue
        if (
            window.plan_id != plan_id
            or window.unit != unit
            or (
                kind is WindowKind.COMMERCIAL_QUOTA
                and plan_id is None
                and window.source.reference != _PLANLESS_COMMERCIAL_SOURCE
            )
        ):
            exclusions.append(
                AggregateExclusion(snapshot.provider_id, ExclusionReason.INCOMPATIBLE_COMPARABILITY)
            )
            continue
        percentage = window.remaining_percentage
        if percentage is None:
            exclusions.append(AggregateExclusion(snapshot.provider_id, ExclusionReason.NO_PERCENTAGE))
            continue
        included.append((snapshot.provider_id, percentage))

    included.sort(key=lambda item: item[0].value)
    exclusions.sort(key=lambda item: item.provider_id.value)
    lowest = min((value for _, value in included), key=lambda value: value.value, default=None)
    return RemainingAggregate(
        kind=kind,
        scope=scope,
        period=period,
        plan_id=plan_id,
        unit=unit,
        remaining_percentage=lowest,
        included_provider_ids=tuple(provider for provider, _ in included),
        exclusions=tuple(exclusions),
    )
