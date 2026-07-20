"""Human-readable presentation adapter for the public status API.

The CLI is a thin transport: it parses arguments, owns the stdout/stderr
streams and exit codes, and delegates all rendering to
:func:`limitora.output.render_human`. No presentation strings live here.
"""

from datetime import timedelta
import sys
from typing import Protocol, TextIO

from limitora.api import (
    Freshness,
    FreshnessPolicy,
    StatusRequest,
    StatusSnapshotResult,
    StatusUndetectedResult,
)
from limitora.models import MetricKind
from limitora.output import render_human
from limitora.providers import AuthorizationPolicy, ProviderError


_HELP = "limitora status: human-readable status only; JSON and provider/configuration options are unavailable.\n"
_USAGE = "Usage: limitora status [--help]\n"


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
        errors.write(render_human(error))
        return 5
    if isinstance(result, StatusUndetectedResult):
        output.write(render_human(result))
        return 0
    if isinstance(result, StatusSnapshotResult):
        output.write(render_human(result))
        return 3 if result.freshness is Freshness.STALE else 0
    raise TypeError("status client returned an unsupported result")


def console_main() -> None:
    raise SystemExit(main())
