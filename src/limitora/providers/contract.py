"""Provider contract types independent of concrete source adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from limitora.models import MetricKind, ProviderId, ProviderSnapshot

from .ports import PortFailure, PortFailureKind, PortKind


class AuthorizationPolicy(str, Enum):
    ALLOW_AUTHORIZED_SOURCE = "allow_authorized_source"
    DENY_AUTHORIZED_SOURCE = "deny_authorized_source"


@dataclass(frozen=True)
class ProviderRequest:
    requested_metrics: frozenset[MetricKind]
    authorization_policy: AuthorizationPolicy

    def __post_init__(self) -> None:
        if not self.requested_metrics:
            raise ValueError("provider request must name at least one metric")


@dataclass(frozen=True)
class ProviderDetection:
    provider_id: ProviderId
    detected: bool
    checked_at: datetime
    safe_message: str | None = None

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("checked_at must be timezone-aware")
        if self.safe_message is not None:
            if not self.safe_message or self.safe_message.strip() != self.safe_message:
                raise ValueError("safe message must be a non-empty trimmed string")


class ProviderErrorKind(str, Enum):
    NOT_CONFIGURED = "not_configured"
    UNAUTHORIZED = "unauthorized"
    SOURCE_UNAVAILABLE = "source_unavailable"
    TRANSPORT = "transport"
    COMMAND_FAILED = "command_failed"
    FILE_MISSING = "file_missing"
    FILE_INVALID = "file_invalid"
    PARSE_FAILED = "parse_failed"
    RATE_LIMITED = "rate_limited"
    UNSUPPORTED = "unsupported"


class ProviderError(Exception):
    """A provider failure safe to expose outside an adapter boundary."""

    def __init__(
        self,
        kind: ProviderErrorKind,
        provider_id: ProviderId,
        safe_message: str,
        *,
        retryable: bool,
    ) -> None:
        if not safe_message or safe_message.strip() != safe_message:
            raise ValueError("safe message must be a non-empty trimmed string")
        self.kind = kind
        self.provider_id = provider_id
        self.safe_message = safe_message
        self.retryable = retryable
        super().__init__(safe_message)


def map_port_failure(
    provider_id: ProviderId, port: PortKind, failure: PortFailure
) -> ProviderError:
    """Translate a port failure without exposing its adapter diagnostic."""
    if port is PortKind.HTTP:
        message = {
            PortFailureKind.TIMEOUT: "HTTP request timed out",
            PortFailureKind.UNAVAILABLE: "HTTP source is unavailable",
        }.get(failure.kind, "HTTP request failed")
        return ProviderError(
            ProviderErrorKind.TRANSPORT,
            provider_id,
            message,
            retryable=failure.kind in {PortFailureKind.TIMEOUT, PortFailureKind.UNAVAILABLE},
        )
    if port is PortKind.FILE:
        kind = ProviderErrorKind.FILE_MISSING if failure.kind is PortFailureKind.MISSING else ProviderErrorKind.FILE_INVALID
        message = "configured file is missing" if kind is ProviderErrorKind.FILE_MISSING else "configured file is invalid"
        return ProviderError(kind, provider_id, message, retryable=False)
    message = {
        PortFailureKind.TIMEOUT: "provider command timed out",
        PortFailureKind.UNAVAILABLE: "provider command is unavailable",
    }.get(failure.kind, "provider command failed")
    return ProviderError(
        ProviderErrorKind.COMMAND_FAILED,
        provider_id,
        message,
        retryable=failure.kind in {PortFailureKind.TIMEOUT, PortFailureKind.UNAVAILABLE},
    )


@runtime_checkable
class ProviderReader(Protocol):
    """Detect an approved source separately from reading a validated snapshot."""

    @property
    def provider_id(self) -> ProviderId: ...

    def detect(self) -> ProviderDetection: ...

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot: ...
