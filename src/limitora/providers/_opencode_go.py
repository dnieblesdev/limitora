"""Private OpenCode Go provider and its deliberately narrow transport seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from typing import Callable, Protocol

from limitora.models import (
    MetricKind, ProviderId, ProviderSnapshot, ProviderState, ProviderStatus,
    Quantity, QuotaWindow, SourceMetadata, ValueAvailability, WindowKind,
)

from .contract import AuthorizationPolicy, ProviderDetection, ProviderError, ProviderErrorKind, ProviderReader, ProviderRequest, map_port_failure
from .ports import HttpResponse, PortFailure, PortKind


PARSE_FAILED_INVALID_UTF8_JSON = "OpenCode Go response is not valid UTF-8 JSON"
PARSE_FAILED_NON_OBJECT_JSON = "OpenCode Go response JSON root is not an object"


class _OpenCodeGoTransport(Protocol):
    def fetch(self) -> HttpResponse | PortFailure: ...


@dataclass(frozen=True)
class OpenCodeGoConfig:
    api_key: str = field(repr=False)
    timeout: timedelta


class OpenCodeGoProvider(ProviderReader):
    PROVIDER_ID = ProviderId("opencode-go")
    SOURCE = SourceMetadata("opencode-go-api")
    _WINDOWS = (("rolling", "five_hour", WindowKind.COMMERCIAL_QUOTA), ("weekly", "weekly", WindowKind.COMMERCIAL_QUOTA), ("monthly", "monthly", WindowKind.COMMERCIAL_QUOTA))

    def __init__(self, config: OpenCodeGoConfig, transport: _OpenCodeGoTransport, *, clock: Callable[[], datetime] | None = None) -> None:
        self._config = config
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def provider_id(self) -> ProviderId:
        return self.PROVIDER_ID

    def detect(self):
        return ProviderDetection(self.PROVIDER_ID, True, self._clock())

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot:
        if request.authorization_policy is AuthorizationPolicy.DENY_AUTHORIZED_SOURCE:
            raise ProviderError(ProviderErrorKind.UNAUTHORIZED, self.PROVIDER_ID, "OpenCode Go authorization denied", retryable=False)
        if MetricKind.COMMERCIAL_QUOTA not in request.requested_metrics:
            raise ProviderError(ProviderErrorKind.UNSUPPORTED, self.PROVIDER_ID, "requested metric is unsupported", retryable=False)
        result = self._transport.fetch()
        if isinstance(result, PortFailure):
            raise map_port_failure(self.PROVIDER_ID, PortKind.HTTP, result)
        if result.status_code in (401, 403):
            raise ProviderError(ProviderErrorKind.UNAUTHORIZED, self.PROVIDER_ID, "OpenCode Go authorization failed", retryable=False)
        if result.status_code == 429:
            raise ProviderError(ProviderErrorKind.RATE_LIMITED, self.PROVIDER_ID, "OpenCode Go source is rate limited", retryable=True)
        if 500 <= result.status_code <= 599:
            raise ProviderError(ProviderErrorKind.SOURCE_UNAVAILABLE, self.PROVIDER_ID, "OpenCode Go source is unavailable", retryable=True)
        if 300 <= result.status_code <= 399 or not 200 <= result.status_code <= 299:
            raise ProviderError(ProviderErrorKind.UNSUPPORTED, self.PROVIDER_ID, "OpenCode Go response is unsupported", retryable=False)
        try:
            payload = json.loads(result.body)
        except (UnicodeDecodeError, TypeError, json.JSONDecodeError):
            raise ProviderError(ProviderErrorKind.PARSE_FAILED, self.PROVIDER_ID, PARSE_FAILED_INVALID_UTF8_JSON, retryable=False)
        if not isinstance(payload, dict):
            raise ProviderError(ProviderErrorKind.PARSE_FAILED, self.PROVIDER_ID, PARSE_FAILED_NON_OBJECT_JSON, retryable=False)
        fetched_at = self._clock()
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError(ProviderErrorKind.PARSE_FAILED, self.PROVIDER_ID, "OpenCode Go response has no valid quota window", retryable=False)
        windows = tuple(self._window(usage, key, period, kind) for key, period, kind in self._WINDOWS)
        valid = tuple(window for window in windows if window is not None)
        if not valid:
            raise ProviderError(ProviderErrorKind.PARSE_FAILED, self.PROVIDER_ID, "OpenCode Go response has no valid quota window", retryable=False)
        state = ProviderState.AVAILABLE if len(valid) == len(self._WINDOWS) else ProviderState.PARTIAL
        return ProviderSnapshot(self.PROVIDER_ID, ProviderStatus(self.PROVIDER_ID, state, fetched_at), fetched_at, fetched_at, self.SOURCE, valid)

    def _window(self, payload, key, period, kind):
        value = payload.get(key)
        if not isinstance(value, dict):
            return None
        status = value.get("status")
        usage = value.get("percent")
        reset = value.get("resetsAt")
        if not isinstance(status, str) or status not in {"ok", "rate-limited"}:
            return None
        if isinstance(usage, bool) or not isinstance(usage, (int, float)) or not math.isfinite(usage) or not 0 <= usage <= 100:
            return None
        if not isinstance(reset, str) or not reset.strip():
            return None
        try:
            reset_at = datetime.fromisoformat(reset.replace("Z", "+00:00"))
        except ValueError:
            return None
        if reset_at.tzinfo is None or reset_at.utcoffset() is None:
            return None
        used = Decimal(str(usage))
        limit = Decimal("100")
        return QuotaWindow(kind, "account", period, None, ValueAvailability.KNOWN, self.SOURCE,
                           Quantity(limit, MetricKind.COMMERCIAL_QUOTA, "percentage_points"),
                           Quantity(used, MetricKind.COMMERCIAL_QUOTA, "percentage_points"),
                           Quantity(limit - used, MetricKind.COMMERCIAL_QUOTA, "percentage_points"),
                           reset_at)
