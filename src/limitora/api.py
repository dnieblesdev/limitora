"""Stable public facade for reading provider status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol, TypeAlias, runtime_checkable

from limitora.core import StatusService
from limitora.models import MetricKind, ProviderId, ProviderSnapshot
from limitora.providers import AuthorizationPolicy, ProviderDetection, ProviderRequest


@runtime_checkable
class Clock(Protocol):
    """Supplies timezone-aware current time for freshness evaluation."""

    def now(self) -> datetime: ...


class CurrentClock:
    """The default UTC clock for ordinary library use."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@runtime_checkable
class StatusProvider(Protocol):
    """The provider operations accepted by the public facade."""

    @property
    def provider_id(self) -> ProviderId: ...

    def detect(self) -> ProviderDetection: ...

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot: ...


class InvalidProviderSelectionError(ValueError):
    """Raised when a status client receives no usable provider selection."""


class InvalidStatusRequestError(ValueError):
    """Raised when a status read receives an invalid public request."""


@dataclass(frozen=True)
class FreshnessPolicy:
    """The maximum permitted age of a provider snapshot."""

    maximum_age: timedelta

    def __post_init__(self) -> None:
        if self.maximum_age < timedelta(0):
            raise ValueError("maximum age cannot be negative")


@dataclass(frozen=True)
class StatusRequest:
    """An immutable public request for provider status."""

    requested_metrics: frozenset[MetricKind]
    authorization_policy: AuthorizationPolicy
    freshness_policy: FreshnessPolicy

    def __post_init__(self) -> None:
        if not self.requested_metrics:
            raise ValueError("status request must name at least one metric")

    def to_provider_request(self) -> ProviderRequest:
        return ProviderRequest(self.requested_metrics, self.authorization_policy)


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True)
class StatusSnapshotResult:
    """A detected snapshot and its independently evaluated freshness."""

    snapshot: ProviderSnapshot
    freshness: Freshness


@dataclass(frozen=True)
class StatusUndetectedResult:
    """A provider selection for which no usable source was detected."""


StatusResult: TypeAlias = StatusSnapshotResult | StatusUndetectedResult


class StatusClient:
    """Composes one explicitly selected provider into the stable public API."""

    def __init__(self, provider: StatusProvider, clock: Clock | None = None) -> None:
        if provider is None or not isinstance(provider, StatusProvider):
            raise InvalidProviderSelectionError("a StatusProvider selection is required")
        self._service = StatusService(provider)
        self._clock = CurrentClock() if clock is None else clock

    def read_status(self, request: StatusRequest) -> StatusResult:
        if not isinstance(request.freshness_policy, FreshnessPolicy):
            raise InvalidStatusRequestError("freshness policy must be a FreshnessPolicy")
        outcome = self._service.read_status(request.to_provider_request())
        if isinstance(outcome, ProviderDetection):
            return StatusUndetectedResult()
        freshness = (
            Freshness.STALE
            if outcome.is_stale(self._clock.now(), request.freshness_policy.maximum_age)
            else Freshness.FRESH
        )
        return StatusSnapshotResult(outcome, freshness)
