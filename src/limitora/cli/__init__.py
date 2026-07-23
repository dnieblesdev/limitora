"""Human-readable and JSON presentation adapter for the public status API.

The CLI is a thin transport: it parses arguments, owns the stdout/stderr
streams and exit codes, and delegates all rendering to
:func:`limitora.output.render_human` and :func:`limitora.output.render_json`.
The activation seam lives in :func:`limitora.composition.activate_provider`,
which is the only module that imports the private adapter transports. The
CLI itself never reaches into adapter internals.

Flag grammar (all forms are space-separated, ``--key=value`` is rejected):

    limitora status [--help] [--json] [--provider {codex,opencode-go}] [flags]

    codex:        --runner PATH [--runner ARG ...]
                  [--codex-allow-authorized-source]
    opencode-go:  --workspace-id ID --auth-cookie COOKIE
                  [--endpoint URL] [--timeout SECONDS]
                  [--opencode-allow-authorized-source]
"""

from dataclasses import dataclass, field, replace
from datetime import timedelta
from os.path import isabs
import sys
from typing import Literal, Protocol, TextIO

from limitora import (
    AuthorizationPolicy,
    Clock,
    CodexJsonlConfig,
    CompositionError,
    CurrentClock,
    Freshness,
    FreshnessPolicy,
    MetricKind,
    OpenCodeGoConfig,
    ProviderConfig,
    ProviderError,
    StatusRequest,
    StatusSnapshotResult,
    StatusUndetectedResult,
    activate_provider,
)
from limitora.output import render_human, render_json


_HELP = (
    "limitora status [--help] [--json] [--provider {codex,opencode-go}] [flags]\n"
    "  codex:        --runner PATH [--runner ARG ...]\n"
    "                A single absolute PATH uses 'app-server --stdio'.\n"
    "                [--codex-allow-authorized-source]\n"
    "  opencode-go:  --workspace-id ID --auth-cookie COOKIE\n"
    "                [--endpoint URL] [--timeout SECONDS]\n"
    "                [--opencode-allow-authorized-source]\n"
    "Without --provider, status prints 'no provider configured' to stderr (exit 4).\n"
)
_USAGE = "Usage: limitora status [--help] [--json] [--provider {codex,opencode-go}] [flags]\n"
_BOOLEAN_FLAGS = frozenset({
    "--help", "--json",
    "--codex-allow-authorized-source", "--opencode-allow-authorized-source",
})
_VALUE_FLAGS = frozenset({
    "--provider", "--runner",
    "--workspace-id", "--auth-cookie", "--endpoint", "--timeout",
})
_KNOWN_FLAGS = _BOOLEAN_FLAGS | _VALUE_FLAGS
_SINGLETON_VALUE_FLAGS = frozenset({
    "--provider", "--workspace-id", "--auth-cookie", "--endpoint", "--timeout",
})
_KNOWN_PROVIDERS = frozenset({"codex", "opencode-go"})
_DEFAULT_ENDPOINT = "https://opencode.ai"
_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 10
_UNCONFIGURED_MESSAGE = "ERROR: no provider configured\n"


class StatusReader(Protocol):
    def read_status(self, request: StatusRequest) -> StatusSnapshotResult | StatusUndetectedResult: ...


class _NoProviderConfigured(Exception):
    pass


def _unconfigured_factory() -> StatusReader:
    raise _NoProviderConfigured


class CliUsageError(ValueError):
    """Raised by :func:`parse` when the argv grammar is invalid."""


@dataclass(frozen=True)
class CodexIntent:
    """Intermediate representation for the ``codex`` provider flags."""

    runner: tuple[str, ...] = ()
    allow_authorized_source: bool = False


@dataclass(frozen=True)
class OpenCodeGoIntent:
    """Intermediate representation for the ``opencode-go`` provider flags."""

    workspace_id: str = ""
    auth_cookie: str = ""
    endpoint: str = _DEFAULT_ENDPOINT
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    allow_authorized_source: bool = False


@dataclass(frozen=True)
class CliIntent:
    """The full intermediate representation of a parsed argv invocation."""

    help_requested: bool = False
    json_requested: bool = False
    provider: Literal["codex", "opencode-go"] | None = None
    codex: CodexIntent | None = None
    opencode: OpenCodeGoIntent | None = None


def _consume_value(argv: list[str], i: int, *, allow_runner_argument: bool = False) -> tuple[str | None, int]:
    """Return ``(value, next_index)`` for the flag at ``argv[i]`` or ``(None, i+1)`` if missing.

    By default a value is considered present only when the next token does
    not start with ``-`` (so an unflagged token is required). Runner
    arguments may start with ``-`` or ``--`` unless they collide with a
    known Limitora flag.
    """
    next_index = i + 1
    if next_index >= len(argv):
        return None, next_index
    candidate = argv[next_index]
    if candidate.startswith("--") and (
        not allow_runner_argument or candidate in _KNOWN_FLAGS
    ):
        return None, next_index
    if candidate.startswith("-") and not allow_runner_argument:
        return None, next_index
    return candidate, next_index + 1


def _usage_error(message: str) -> CliUsageError:
    return CliUsageError(f"{_USAGE}{message}\n")


def parse(argv: list[str]) -> CliIntent:
    """Hand-rolled argv parser. Builds a typed :class:`CliIntent`; never constructs providers.

    Raises :class:`CliUsageError` for any structural or semantic grammar
    failure. The error message is a redacted constant suitable for
    ``stderr`` (exit code 2).
    """
    if not argv or argv[0] != "status":
        raise CliUsageError(_USAGE)

    tokens = argv[1:]
    help_seen = False
    json_seen = False
    codex_allow = False
    opencode_allow = False
    provider: str | None = None
    codex_runner: list[str] = []
    opencode_workspace: str | None = None
    opencode_cookie: str | None = None
    opencode_endpoint: str | None = None
    opencode_timeout: int | None = None

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("--help",):
            if help_seen:
                raise _usage_error("--help specified more than once")
            help_seen = True
            i += 1
            continue
        if token == "--json":
            if json_seen:
                raise _usage_error("--json specified more than once")
            json_seen = True
            i += 1
            continue
        if token == "--codex-allow-authorized-source":
            if codex_allow:
                raise _usage_error("--codex-allow-authorized-source specified more than once")
            codex_allow = True
            i += 1
            continue
        if token == "--opencode-allow-authorized-source":
            if opencode_allow:
                raise _usage_error("--opencode-allow-authorized-source specified more than once")
            opencode_allow = True
            i += 1
            continue
        if token in _VALUE_FLAGS:
            value, next_i = _consume_value(
                tokens, i, allow_runner_argument=token == "--runner"
            )
            if value is None:
                raise _usage_error(f"{token} requires a value")
            if token == "--provider":
                if provider is not None:
                    raise _usage_error("--provider specified more than once")
                if value not in _KNOWN_PROVIDERS:
                    raise _usage_error(f"unknown --provider value: {value}")
                provider = value
            elif token == "--runner":
                codex_runner.append(value)
            elif token == "--workspace-id":
                if opencode_workspace is not None:
                    raise _usage_error("--workspace-id specified more than once")
                opencode_workspace = value
            elif token == "--auth-cookie":
                if opencode_cookie is not None:
                    raise _usage_error("--auth-cookie specified more than once")
                opencode_cookie = value
            elif token == "--endpoint":
                if opencode_endpoint is not None:
                    raise _usage_error("--endpoint specified more than once")
                opencode_endpoint = value
            else:  # --timeout
                if opencode_timeout is not None:
                    raise _usage_error("--timeout specified more than once")
                if not value.isdigit():
                    raise _usage_error("--timeout must be a positive integer between 1 and 10")
                seconds = int(value)
                if seconds <= 0 or seconds > _MAX_TIMEOUT_SECONDS:
                    raise _usage_error("--timeout must be a positive integer between 1 and 10")
                opencode_timeout = seconds
            i = next_i
            continue
        # Unknown flag or unexpected positional
        if "=" in token:
            raise _usage_error(f"--key=value form is not supported: {token}")
        if not token.startswith("--"):
            raise _usage_error(f"unexpected positional: {token}")
        raise _usage_error(f"unknown flag: {token}")

    # Cross-flag checks: a codex flag without codex provider (or with opencode)
    if (codex_runner or codex_allow) and provider is not None and provider != "codex":
        raise _usage_error("codex flags require --provider codex")
    if (opencode_workspace is not None or opencode_cookie is not None
            or opencode_endpoint is not None or opencode_timeout is not None
            or opencode_allow) and provider is not None and provider != "opencode-go":
        raise _usage_error("opencode-go flags require --provider opencode-go")

    # Missing-required checks
    if provider == "codex" and not codex_runner:
        raise _usage_error("--provider codex requires at least one --runner")
    if provider == "opencode-go":
        if opencode_workspace is None or opencode_cookie is None:
            raise _usage_error("--provider opencode-go requires --workspace-id and --auth-cookie")

    codex_intent: CodexIntent | None = None
    if codex_runner or codex_allow:
        codex_intent = CodexIntent(tuple(codex_runner), codex_allow)
    opencode_intent: OpenCodeGoIntent | None = None
    if (opencode_workspace is not None or opencode_cookie is not None
            or opencode_endpoint is not None or opencode_timeout is not None
            or opencode_allow):
        opencode_intent = OpenCodeGoIntent(
            workspace_id=opencode_workspace or "",
            auth_cookie=opencode_cookie or "",
            endpoint=opencode_endpoint or _DEFAULT_ENDPOINT,
            timeout_seconds=opencode_timeout if opencode_timeout is not None else _DEFAULT_TIMEOUT_SECONDS,
            allow_authorized_source=opencode_allow,
        )

    return CliIntent(
        help_requested=help_seen,
        json_requested=json_seen,
        provider=provider,
        codex=codex_intent,
        opencode=opencode_intent,
    )


def intent_to_config(intent: CliIntent) -> ProviderConfig:
    """Pure data mapper from :class:`CliIntent` to :data:`ProviderConfig`."""
    if intent.provider == "codex":
        if intent.codex is None:  # pragma: no cover - guarded by parse
            raise CompositionError("invalid")
        runner = intent.codex.runner
        if len(runner) == 1 and isabs(runner[0]):
            runner += ("app-server", "--stdio")
        return CodexJsonlConfig(runner=runner)
    if intent.provider == "opencode-go":
        if intent.opencode is None:  # pragma: no cover - guarded by parse
            raise CompositionError("invalid")
        return OpenCodeGoConfig(
            workspace_id=intent.opencode.workspace_id,
            auth_cookie=intent.opencode.auth_cookie,
            endpoint=intent.opencode.endpoint,
            timeout=timedelta(seconds=intent.opencode.timeout_seconds),
        )
    raise CompositionError("invalid")


def _default_request(policy: AuthorizationPolicy) -> StatusRequest:
    return StatusRequest(
        frozenset({MetricKind.COMMERCIAL_QUOTA}),
        policy,
        FreshnessPolicy(timedelta(minutes=5)),
    )


def _render_result(
    result: StatusSnapshotResult | StatusUndetectedResult | ProviderError,
    json_mode: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    if isinstance(result, ProviderError):
        if json_mode:
            out.write(render_json(result))
            out.write("\n")
        else:
            err.write(render_human(result))
        return 5
    if isinstance(result, StatusUndetectedResult):
        if json_mode:
            out.write(render_json(result))
            out.write("\n")
        else:
            out.write(render_human(result))
        return 0
    if isinstance(result, StatusSnapshotResult):
        if json_mode:
            out.write(render_json(result))
            out.write("\n")
        else:
            out.write(render_human(result))
        return 3 if result.freshness is Freshness.STALE else 0
    raise TypeError("status client returned an unsupported result")


def main(argv: list[str] | None = None, *, client_factory=None,
         stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    out, err = sys.stdout if stdout is None else stdout, sys.stderr if stderr is None else stderr

    try:
        intent = parse(arguments)
    except CliUsageError as usage:
        message = str(usage)
        err.write(message if message.endswith("\n") else f"{message}\n")
        return 2

    if intent.help_requested:
        if intent.json_requested:
            err.write(_HELP)
        else:
            out.write(_HELP)
        return 0

    if intent.provider is None:
        try:
            client = _unconfigured_factory() if client_factory is None else client_factory()
        except _NoProviderConfigured:
            err.write(_UNCONFIGURED_MESSAGE)
            return 4
        try:
            result = client.read_status(_default_request(AuthorizationPolicy.DENY_AUTHORIZED_SOURCE))
        except ProviderError as error:
            return _render_result(error, intent.json_requested, out, err)
        return _render_result(result, intent.json_requested, out, err)

    try:
        config = intent_to_config(intent)
    except CompositionError as error:
        err.write(f"{error.safe_message}\n")
        return 2

    policy = AuthorizationPolicy.DENY_AUTHORIZED_SOURCE
    if intent.provider == "codex" and intent.codex is not None and intent.codex.allow_authorized_source:
        policy = AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE
    elif intent.provider == "opencode-go" and intent.opencode is not None and intent.opencode.allow_authorized_source:
        policy = AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE

    try:
        client = activate_provider(config, clock=CurrentClock())
    except CompositionError as error:
        err.write(f"{error.safe_message}\n")
        return 2

    try:
        result = client.read_status(_default_request(policy))
    except ProviderError as error:
        return _render_result(error, intent.json_requested, out, err)
    return _render_result(result, intent.json_requested, out, err)


def console_main() -> None:
    raise SystemExit(main())
