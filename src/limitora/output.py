"""Projection layer for the stable public result types.

Renders a :class:`limitora.api.StatusSnapshotResult`,
:class:`limitora.api.StatusUndetectedResult`, or
:class:`limitora.providers.ProviderError` into either the versioned JSON v1
envelope or the human-readable CLI string documented in
``docs/architecture/output-contracts.md``.

The module consumes only the public types from ``limitora.api``,
``limitora.models``, and ``limitora.providers``. It is intentionally not
re-exported from the ``limitora`` package.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from limitora.api import StatusSnapshotResult, StatusUndetectedResult
from limitora.models import Quantity, QuotaWindow, UsageSnapshot
from limitora.providers import ProviderError


JSONContractVersion = 1
_UNAVAILABLE = "unavailable"


def isoformat_utc(value: datetime) -> str:
    """Render an aware datetime as an ISO-8601 UTC string with a ``Z`` suffix."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def quantity_to_dict(quantity: Quantity | None) -> dict[str, Any] | None:
    """Project a :class:`Quantity` to its JSON shape, or ``None`` when absent."""
    if quantity is None:
        return None
    return {
        "value": str(quantity.value),
        "metric": quantity.metric.value,
        "unit": quantity.unit,
    }


def window_to_dict(window: QuotaWindow) -> dict[str, Any]:
    """Project a :class:`QuotaWindow` with explicit nulls for nullable fields."""
    return {
        "kind": window.kind.value,
        "scope": window.scope,
        "period": window.period,
        "plan_id": window.plan_id,
        "availability": window.availability.value,
        "source": {"reference": window.source.reference},
        "limit": quantity_to_dict(window.limit),
        "used": quantity_to_dict(window.used),
        "remaining": quantity_to_dict(window.remaining),
        "reset_at": isoformat_utc(window.reset_at) if window.reset_at is not None else None,
    }


def usage_to_dict(usage: UsageSnapshot | None) -> dict[str, Any] | None:
    """Project a :class:`UsageSnapshot`, or ``None`` when no usage was captured."""
    if usage is None:
        return None
    return {
        "observed_at": isoformat_utc(usage.observed_at),
        "availability": usage.availability.value,
        "source": {"reference": usage.source.reference},
        "token_limit": quantity_to_dict(usage.token_limit),
        "token_used": quantity_to_dict(usage.token_used),
        "balance": quantity_to_dict(usage.balance),
    }


def snapshot_to_dict(result: StatusSnapshotResult) -> dict[str, Any]:
    """Project a snapshot result into the snapshot envelope dict."""
    snapshot = result.snapshot
    return {
        "version": JSONContractVersion,
        "result": "snapshot",
        "provider_id": {"value": snapshot.provider_id.value},
        "freshness": result.freshness.value,
        "status": {
            "state": snapshot.status.state.value,
            "observed_at": isoformat_utc(snapshot.status.observed_at),
        },
        "fetched_at": isoformat_utc(snapshot.fetched_at),
        "data_at": isoformat_utc(snapshot.data_at),
        "source": {"reference": snapshot.source.reference},
        "quota_windows": [window_to_dict(window) for window in snapshot.quota_windows],
        "usage": usage_to_dict(snapshot.usage),
    }


def undetected_to_dict(_result: StatusUndetectedResult) -> dict[str, Any]:
    """Project an undetected result into the minimal typed envelope."""
    return {
        "version": JSONContractVersion,
        "result": "undetected",
    }


def error_to_dict(error: ProviderError) -> dict[str, Any]:
    """Project a provider error into the sanitized typed envelope."""
    return {
        "version": JSONContractVersion,
        "error": {
            "kind": error.kind.value,
            "provider_id": {"value": error.provider_id.value},
            "safe_message": error.safe_message,
            "retryable": error.retryable,
        },
    }


def _ordered(payload: Any) -> Any:
    """Recursively sort dict keys, promoting ``"version"`` to the front of every dict."""
    if isinstance(payload, dict):
        ordered: dict[str, Any] = {}
        if "version" in payload:
            ordered["version"] = payload["version"]
        for key in sorted(k for k in payload if k != "version"):
            ordered[key] = _ordered(payload[key])
        return ordered
    if isinstance(payload, list):
        return [_ordered(item) for item in payload]
    return payload


def render_json(
    result: StatusSnapshotResult | StatusUndetectedResult | ProviderError,
    *,
    version: int = 1,
) -> str:
    """Render a public result as a deterministic, versioned JSON v1 string."""
    if version != JSONContractVersion:
        raise ValueError(f"unsupported JSON contract version: {version}")
    if isinstance(result, StatusSnapshotResult):
        payload = snapshot_to_dict(result)
    elif isinstance(result, StatusUndetectedResult):
        payload = undetected_to_dict(result)
    elif isinstance(result, ProviderError):
        payload = error_to_dict(result)
    else:
        raise TypeError(f"unsupported result type: {type(result).__name__}")
    return json.dumps(_ordered(payload), sort_keys=False, allow_nan=False)


def _human_optional(value: object | None) -> str:
    return _UNAVAILABLE if value is None else str(value)


def _human_quantity(value: Quantity | None) -> str:
    return _UNAVAILABLE if value is None else f"{value.value} {value.unit}"


def _render_human_usage(usage: UsageSnapshot | None) -> list[str]:
    if usage is None:
        return ["USAGE: unavailable"]
    return [
        "USAGE:",
        f"  OBSERVED_AT: {isoformat_utc(usage.observed_at)}",
        f"  AVAILABILITY: {usage.availability.value}",
        f"  SOURCE: {usage.source.reference}",
        f"  TOKEN_LIMIT: {_human_quantity(usage.token_limit)}",
        f"  TOKEN_USED: {_human_quantity(usage.token_used)}",
        f"  BALANCE: {_human_quantity(usage.balance)}",
    ]


def _render_human_snapshot(result: StatusSnapshotResult) -> str:
    snapshot = result.snapshot
    lines = [
        "RESULT: snapshot",
        f"PROVIDER: {snapshot.provider_id.value}",
        f"STATE: {snapshot.status.state.value}",
        f"STATUS_OBSERVED_AT: {isoformat_utc(snapshot.status.observed_at)}",
        f"FRESHNESS: {result.freshness.value}",
        f"FETCHED_AT: {isoformat_utc(snapshot.fetched_at)}",
        f"DATA_AT: {isoformat_utc(snapshot.data_at)}",
        f"SOURCE: {snapshot.source.reference}",
    ]
    windows = sorted(
        snapshot.quota_windows,
        key=lambda window: (window.kind.value, window.scope, window.period, window.plan_id or ""),
    )
    if not windows:
        lines.append("QUOTA_WINDOWS: unavailable")
    else:
        lines.append("QUOTA_WINDOWS:")
        for window in windows:
            lines.extend((
                f"  KIND: {window.kind.value}",
                f"  SCOPE: {window.scope}",
                f"  PERIOD: {window.period}",
                f"  PLAN_ID: {_human_optional(window.plan_id)}",
                f"  AVAILABILITY: {window.availability.value}",
                f"  SOURCE: {window.source.reference}",
                f"  LIMIT: {_human_quantity(window.limit)}",
                f"  USED: {_human_quantity(window.used)}",
                f"  REMAINING: {_human_quantity(window.remaining)}",
                f"  RESET_AT: {_UNAVAILABLE if window.reset_at is None else isoformat_utc(window.reset_at)}",
            ))
    lines.extend(_render_human_usage(snapshot.usage))
    return "\n".join(lines) + "\n"


def _render_human_error(error: ProviderError) -> str:
    return "\n".join((
        "ERROR: provider",
        f"PROVIDER: {error.provider_id.value}",
        f"KIND: {error.kind.value}",
        f"MESSAGE: {error.safe_message}",
        f"RETRYABLE: {str(error.retryable).lower()}",
    )) + "\n"


def render_human(
    result: StatusSnapshotResult | StatusUndetectedResult | ProviderError,
) -> str:
    """Render a public result as a human-readable string, byte-identical to the legacy CLI."""
    if isinstance(result, StatusSnapshotResult):
        return _render_human_snapshot(result)
    if isinstance(result, StatusUndetectedResult):
        return "RESULT: undetected\nSTATUS: unavailable\n"
    if isinstance(result, ProviderError):
        return _render_human_error(result)
    raise TypeError(f"unsupported result type: {type(result).__name__}")
