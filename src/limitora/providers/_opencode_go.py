"""Private OpenCode Go provider and its deliberately narrow transport seam."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from typing import Callable, Protocol

from limitora.models import (
    MetricKind, ProviderId, ProviderSnapshot, ProviderState, ProviderStatus,
    Quantity, QuotaWindow, SourceMetadata, ValueAvailability, WindowKind,
)

from .contract import ProviderDetection, ProviderError, ProviderErrorKind, ProviderReader, ProviderRequest, map_port_failure
from .ports import HttpResponse, PortFailure, PortKind


class _OpenCodeGoTransport(Protocol):
    def fetch(self) -> HttpResponse | PortFailure: ...


@dataclass(frozen=True)
class OpenCodeGoConfig:
    workspace_id: str
    auth_cookie: str
    endpoint: str
    timeout: timedelta


class OpenCodeGoProvider(ProviderReader):
    PROVIDER_ID = ProviderId("opencode-go")
    SOURCE = SourceMetadata("opencode-go-dashboard")
    _WINDOWS = (("rollingUsage", "five_hour", WindowKind.COMMERCIAL_QUOTA), ("weeklyUsage", "weekly", WindowKind.COMMERCIAL_QUOTA), ("monthlyUsage", "monthly", WindowKind.COMMERCIAL_QUOTA))

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
            if not isinstance(payload, dict) or b"<html" in result.body.lower() or b"<body" in result.body.lower():
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ProviderError(ProviderErrorKind.PARSE_FAILED, self.PROVIDER_ID, "OpenCode Go response could not be parsed", retryable=False)
        fetched_at = self._clock()
        windows = tuple(self._window(payload, key, period, kind, fetched_at) for key, period, kind in self._WINDOWS)
        valid = tuple(window for window in windows if window is not None)
        if not valid:
            raise ProviderError(ProviderErrorKind.PARSE_FAILED, self.PROVIDER_ID, "OpenCode Go response has no valid quota window", retryable=False)
        state = ProviderState.AVAILABLE if len(valid) == len(self._WINDOWS) else ProviderState.PARTIAL
        return ProviderSnapshot(self.PROVIDER_ID, ProviderStatus(self.PROVIDER_ID, state, fetched_at), fetched_at, fetched_at, self.SOURCE, valid)

    def _window(self, payload, key, period, kind, fetched_at):
        value = payload.get(key)
        if not isinstance(value, dict):
            return None
        usage = value.get("usagePercent")
        reset = value.get("resetInSec")
        if isinstance(usage, bool) or not isinstance(usage, (int, float)) or not math.isfinite(usage) or not 0 <= usage <= 100:
            return None
        if isinstance(reset, bool) or not isinstance(reset, int) or reset < 0:
            return None
        used = Decimal(str(usage))
        limit = Decimal("100")
        return QuotaWindow(kind, "account", period, None, ValueAvailability.KNOWN, self.SOURCE,
                           Quantity(limit, MetricKind.COMMERCIAL_QUOTA, "percentage_points"),
                           Quantity(used, MetricKind.COMMERCIAL_QUOTA, "percentage_points"),
                           Quantity(limit - used, MetricKind.COMMERCIAL_QUOTA, "percentage_points"),
                           fetched_at + timedelta(seconds=reset))
