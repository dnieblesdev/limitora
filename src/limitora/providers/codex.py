"""Private opt-in mapping for pinned Codex v2 commercial quota windows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from os.path import isabs

from limitora.models import (MetricKind, ProviderId, ProviderSnapshot, ProviderState, ProviderStatus,
                             Quantity, QuotaWindow, SourceMetadata, ValueAvailability, WindowKind)

from ._codex_jsonl import _CodexJsonlFailure, _CodexJsonlFailureKind, _CodexJsonlSession, _CodexSessionSpec
from .contract import AuthorizationPolicy, ProviderDetection, ProviderError, ProviderErrorKind, ProviderRequest
from .ports import Clock


_PROVIDER_ID = ProviderId("codex")
_SOURCE = SourceMetadata("codex-app-server-v2")
_SUPPORTED_PLANS = frozenset({"free", "plus", "pro", "team", "business", "enterprise", "edu"})
_PERIODS = {300: "five_hour", 10080: "weekly"}


class _MappingError(Exception):
    def __init__(self, kind: ProviderErrorKind) -> None: self.kind = kind


class CodexProvider:
    """An unexported explicit-runner adapter; it never discovers credentials or binaries."""

    def __init__(self, runner: tuple[str, ...], clock: Clock, session: _CodexJsonlSession | object | None = None) -> None:
        self._runner, self._clock = runner, clock
        self._session = _CodexJsonlSession() if session is None else session

    @property
    def provider_id(self) -> ProviderId: return _PROVIDER_ID

    def detect(self) -> ProviderDetection:
        configured = (bool(self._runner) and isabs(self._runner[0])
                      and all(isinstance(part, str) and part.strip() == part and part for part in self._runner))
        return ProviderDetection(_PROVIDER_ID, configured, self._clock.now(), None if configured else "Codex runner is not configured")

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot:
        if not self.detect().detected: raise self._failure(ProviderErrorKind.NOT_CONFIGURED, False)
        if request.authorization_policy is AuthorizationPolicy.DENY_AUTHORIZED_SOURCE:
            raise self._failure(ProviderErrorKind.UNAUTHORIZED, False)
        try:
            payload = self._session.exchange(_CodexSessionSpec(self._runner, timedelta(seconds=5), 65_536, timedelta(seconds=1)))
        except _CodexJsonlFailure as error:
            raise self._transport_failure(error.kind) from None
        return self._snapshot(payload)

    def _snapshot(self, payload: object) -> ProviderSnapshot:
        try:
            rate_limits = payload["rateLimits"] if isinstance(payload, dict) else None
            if not isinstance(rate_limits, dict): raise _MappingError(ProviderErrorKind.UNSUPPORTED)
            plan = rate_limits.get("planType")
            if not isinstance(plan, str) or plan.strip() != plan or plan not in _SUPPORTED_PLANS:
                raise _MappingError(ProviderErrorKind.UNSUPPORTED)
            raw_windows = tuple(rate_limits.get(key) for key in ("primary", "secondary"))
            durations = [item.get("windowDurationMins") for item in raw_windows if isinstance(item, dict)]
            if len(durations) != len(set(durations)) or any(type(value) is not int or value not in _PERIODS for value in durations):
                raise _MappingError(ProviderErrorKind.UNSUPPORTED)
            windows, errors = [], []
            for raw in raw_windows:
                if raw is None: continue
                try: windows.append(self._window(raw, plan))
                except _MappingError as error: errors.append(error)
            if not windows: raise _MappingError(errors[0].kind if errors else ProviderErrorKind.UNSUPPORTED)
        except _MappingError as error:
            raise self._failure(error.kind, False) from None
        now = self._clock.now()
        state = ProviderState.AVAILABLE if len(windows) == 2 else ProviderState.PARTIAL
        return ProviderSnapshot(_PROVIDER_ID, ProviderStatus(_PROVIDER_ID, state, now), now, now, _SOURCE, tuple(windows))

    @staticmethod
    def _window(raw: object, plan: str) -> QuotaWindow:
        if not isinstance(raw, dict): raise _MappingError(ProviderErrorKind.UNSUPPORTED)
        if raw.get("limitId") != "codex": raise _MappingError(ProviderErrorKind.UNSUPPORTED)
        duration, used = raw.get("windowDurationMins"), raw.get("usedPercent")
        if type(duration) is not int or duration not in _PERIODS: raise _MappingError(ProviderErrorKind.UNSUPPORTED)
        if type(used) is not int or not 0 <= used <= 100: raise _MappingError(ProviderErrorKind.PARSE_FAILED)
        reset = raw.get("resetsAt")
        if reset is not None:
            if type(reset) is not int: raise _MappingError(ProviderErrorKind.PARSE_FAILED)
            try: reset = datetime.fromtimestamp(reset, timezone.utc)
            except (OverflowError, OSError, ValueError): raise _MappingError(ProviderErrorKind.PARSE_FAILED) from None
        quantity = lambda value: Quantity(Decimal(value), MetricKind.COMMERCIAL_QUOTA, "percentage_points")
        return QuotaWindow(WindowKind.COMMERCIAL_QUOTA, "account", _PERIODS[duration], plan, ValueAvailability.KNOWN,
                           _SOURCE, quantity(100), quantity(used), quantity(100 - used), reset)

    @staticmethod
    def _failure(kind: ProviderErrorKind, retryable: bool) -> ProviderError:
        return ProviderError(kind, _PROVIDER_ID, "Codex quota source " + kind.value.replace("_", " "), retryable=retryable)

    @classmethod
    def _transport_failure(cls, kind: _CodexJsonlFailureKind) -> ProviderError:
        translated = {
            _CodexJsonlFailureKind.NOT_CONFIGURED: (ProviderErrorKind.NOT_CONFIGURED, False),
            _CodexJsonlFailureKind.UNAUTHORIZED: (ProviderErrorKind.UNAUTHORIZED, False),
            _CodexJsonlFailureKind.RATE_LIMITED: (ProviderErrorKind.RATE_LIMITED, True),
            _CodexJsonlFailureKind.UNAVAILABLE: (ProviderErrorKind.SOURCE_UNAVAILABLE, True),
            _CodexJsonlFailureKind.TIMEOUT: (ProviderErrorKind.TRANSPORT, True),
            _CodexJsonlFailureKind.PROTOCOL: (ProviderErrorKind.PARSE_FAILED, False),
        }.get(kind, (ProviderErrorKind.COMMAND_FAILED, True))
        return cls._failure(*translated)
