"""Projection layer for the stable public result types.

Renders a :class:`limitora.api.StatusSnapshotResult`,
:class:`limitora.api.StatusUndetectedResult`, or
:class:`limitora.providers.ProviderError` into the versioned JSON v1 envelope
documented in ``docs/architecture/output-contracts.md``.

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
