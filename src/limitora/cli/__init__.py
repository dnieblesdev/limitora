"""Human-readable presentation adapter for the public status API."""

from datetime import timedelta, timezone
import sys
from typing import Protocol, TextIO

from limitora.api import (
    Freshness,
    FreshnessPolicy,
    StatusRequest,
    StatusSnapshotResult,
    StatusUndetectedResult,
)
from limitora.models import MetricKind, Quantity, UsageSnapshot
from limitora.providers import AuthorizationPolicy, ProviderError


_HELP = "limitora status: human-readable status only; JSON and provider/configuration options are unavailable.\n"
_USAGE = "Usage: limitora status [--help]\n"
_UNAVAILABLE = "unavailable"


class StatusReader(Protocol):
    def read_status(self, request: StatusRequest) -> StatusSnapshotResult | StatusUndetectedResult: ...


class _NoProviderConfigured(Exception):
    pass


def _unconfigured_factory() -> StatusReader:
    raise _NoProviderConfigured


def _default_request() -> StatusRequest:
    return StatusRequest(
        frozenset({MetricKind.COMMERCIAL_QUOTA}),
        AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
        FreshnessPolicy(timedelta(minutes=5)),
    )


def _timestamp(value) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional(value: object | None) -> str:
    return _UNAVAILABLE if value is None else str(value)


def _quantity(value: Quantity | None) -> str:
    return _UNAVAILABLE if value is None else f"{value.value} {value.unit}"


def _render_snapshot(result: StatusSnapshotResult) -> str:
    snapshot = result.snapshot
    lines = [
        "RESULT: snapshot",
        f"PROVIDER: {snapshot.provider_id.value}",
        f"STATE: {snapshot.status.state.value}",
        f"STATUS_OBSERVED_AT: {_timestamp(snapshot.status.observed_at)}",
        f"FRESHNESS: {result.freshness.value}",
        f"FETCHED_AT: {_timestamp(snapshot.fetched_at)}",
        f"DATA_AT: {_timestamp(snapshot.data_at)}",
        f"SOURCE: {snapshot.source.reference}",
    ]
    windows = sorted(snapshot.quota_windows, key=lambda window: (
        window.kind.value, window.scope, window.period, window.plan_id or "",
    ))
    if not windows:
        lines.append("QUOTA_WINDOWS: unavailable")
    else:
        lines.append("QUOTA_WINDOWS:")
        for window in windows:
            lines.extend((
                f"  KIND: {window.kind.value}", f"  SCOPE: {window.scope}",
                f"  PERIOD: {window.period}", f"  PLAN_ID: {_optional(window.plan_id)}",
                f"  AVAILABILITY: {window.availability.value}",
                f"  SOURCE: {window.source.reference}", f"  LIMIT: {_quantity(window.limit)}",
                f"  USED: {_quantity(window.used)}", f"  REMAINING: {_quantity(window.remaining)}",
                f"  RESET_AT: {_UNAVAILABLE if window.reset_at is None else _timestamp(window.reset_at)}",
            ))
    lines.extend(_render_usage(snapshot.usage))
    return "\n".join(lines) + "\n"


def _render_usage(usage: UsageSnapshot | None) -> list[str]:
    if usage is None:
        return ["USAGE: unavailable"]
    return [
        "USAGE:", f"  OBSERVED_AT: {_timestamp(usage.observed_at)}",
        f"  AVAILABILITY: {usage.availability.value}", f"  SOURCE: {usage.source.reference}",
        f"  TOKEN_LIMIT: {_quantity(usage.token_limit)}", f"  TOKEN_USED: {_quantity(usage.token_used)}",
        f"  BALANCE: {_quantity(usage.balance)}",
    ]


def _render_error(error: ProviderError) -> str:
    return "\n".join((
        "ERROR: provider", f"PROVIDER: {error.provider_id.value}", f"KIND: {error.kind.value}",
        f"MESSAGE: {error.safe_message}", f"RETRYABLE: {str(error.retryable).lower()}",
    )) + "\n"


def main(argv: list[str] | None = None, *, client_factory=None,
         stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    output, errors = sys.stdout if stdout is None else stdout, sys.stderr if stderr is None else stderr
    if arguments == ["status", "--help"]:
        output.write(_HELP)
        return 0
    if arguments != ["status"]:
        errors.write(_USAGE)
        return 2
    try:
        client = _unconfigured_factory() if client_factory is None else client_factory()
    except _NoProviderConfigured:
        errors.write("ERROR: no provider configured\n")
        return 4
    try:
        result = client.read_status(_default_request())
    except ProviderError as error:
        errors.write(_render_error(error))
        return 5
    if isinstance(result, StatusUndetectedResult):
        output.write("RESULT: undetected\nSTATUS: unavailable\n")
        return 0
    if isinstance(result, StatusSnapshotResult):
        output.write(_render_snapshot(result))
        return 3 if result.freshness is Freshness.STALE else 0
    raise TypeError("status client returned an unsupported result")


def console_main() -> None:
    raise SystemExit(main())
