"""Application-owned composition root for the supported status providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from os.path import isabs
from enum import Enum
from typing import Callable, Literal, Protocol, TypeAlias

from .api import CurrentClock, StatusClient
from .providers.cache import CachedProviderReader, ProviderCachePolicy
from .providers.ports import Clock
from .providers._opencode_go import OpenCodeGoConfig as AdapterOpenCodeGoConfig, OpenCodeGoProvider
from .providers.codex import CodexProvider
@dataclass(frozen=True)
class CodexJsonlConfig:
    runner: tuple[str, ...] = ()
    provider: Literal["codex"] = "codex"
@dataclass(frozen=True)
class OpenCodeGoConfig:
    workspace_id: str
    auth_cookie: str
    provider: Literal["opencode-go"] = "opencode-go"
    endpoint: str = "https://opencode.ai"
    timeout: timedelta = timedelta(seconds=10)
ProviderConfig: TypeAlias = CodexJsonlConfig | OpenCodeGoConfig
class CodexSession(Protocol):
    def exchange(self, spec: object) -> object: ...

class OpenCodeGoTransport(Protocol):
    def fetch(self): ...
@dataclass(frozen=True)
class CodexJsonlDependencies:
    clock: Clock
    session_factory: Callable[[], CodexSession]
@dataclass(frozen=True)
class OpenCodeGoDependencies:
    clock: Clock
    transport_factory: Callable[[OpenCodeGoConfig], OpenCodeGoTransport]
ProviderDependencies: TypeAlias = CodexJsonlDependencies | OpenCodeGoDependencies
class CompositionErrorKind(str, Enum):
    DISABLED = "disabled"
    MISSING = "missing"
    INVALID = "invalid"
    DEPENDENCY_MISMATCH = "dependency_mismatch"


class CompositionError(ValueError):
    _MESSAGES = {
        CompositionErrorKind.DISABLED: "provider composition is disabled",
        CompositionErrorKind.MISSING: "provider composition input is missing",
        CompositionErrorKind.INVALID: "provider composition input is invalid",
        CompositionErrorKind.DEPENDENCY_MISMATCH: "provider composition dependencies do not match",
    }

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.safe_message = self._MESSAGES[kind]
        super().__init__(self.safe_message)
def _fail(kind: str) -> None:
    raise CompositionError(kind)

def _valid_clock(clock: object) -> bool:
    return callable(getattr(clock, "now", None))


def _valid_codex(config: CodexJsonlConfig) -> bool:
    return (
        config.provider == "codex"
        and isinstance(config.runner, tuple)
        and bool(config.runner)
        and all(isinstance(part, str) and part and part.strip() == part for part in config.runner)
        and isabs(config.runner[0])
    )
def _valid_opencode(config: OpenCodeGoConfig) -> bool:
    return (
        config.provider == "opencode-go"
        and isinstance(config.workspace_id, str)
        and bool(config.workspace_id)
        and config.workspace_id.strip() == config.workspace_id
        and isinstance(config.auth_cookie, str)
        and bool(config.auth_cookie)
        and config.endpoint == "https://opencode.ai"
        and isinstance(config.timeout, timedelta)
        and timedelta(0) < config.timeout <= timedelta(seconds=10)
    )
def build_status_client(
    config: ProviderConfig | None,
    dependencies: ProviderDependencies | None,
    *,
    enabled: bool = True,
    cache_policy: ProviderCachePolicy | None = None,
    ) -> StatusClient:
    """Build exactly one selected provider from explicit validated inputs."""
    if not enabled:
        _fail(CompositionErrorKind.DISABLED)
    if config is None or dependencies is None:
        _fail(CompositionErrorKind.MISSING)
    if cache_policy is not None and type(cache_policy) is not ProviderCachePolicy:
        _fail(CompositionErrorKind.INVALID)
    if type(config) is CodexJsonlConfig:
        if not _valid_codex(config):
            _fail(CompositionErrorKind.INVALID)
        if not isinstance(dependencies, CodexJsonlDependencies):
            _fail(CompositionErrorKind.DEPENDENCY_MISMATCH)
        if not _valid_clock(dependencies.clock) or not callable(dependencies.session_factory):
            _fail(CompositionErrorKind.INVALID)
        session = dependencies.session_factory()
        if session is None:
            _fail(CompositionErrorKind.INVALID)
        provider = CodexProvider(config.runner, dependencies.clock, session)
        return StatusClient(_cached(provider, cache_policy, dependencies.clock), dependencies.clock)
    if type(config) is OpenCodeGoConfig:
        if not _valid_opencode(config):
            _fail(CompositionErrorKind.INVALID)
        if not isinstance(dependencies, OpenCodeGoDependencies):
            _fail(CompositionErrorKind.DEPENDENCY_MISMATCH)
        if not _valid_clock(dependencies.clock) or not callable(dependencies.transport_factory):
            _fail(CompositionErrorKind.INVALID)
        adapter_config = AdapterOpenCodeGoConfig(
            config.workspace_id, config.auth_cookie, config.endpoint, config.timeout
        )
        transport = dependencies.transport_factory(config)
        provider = OpenCodeGoProvider(adapter_config, transport, clock=dependencies.clock.now)
        return StatusClient(_cached(provider, cache_policy, dependencies.clock), dependencies.clock)
    _fail(CompositionErrorKind.INVALID)


def _cached(provider, policy, clock):
    return provider if policy is None else CachedProviderReader(provider, policy, clock)


def activate_provider(
    config: ProviderConfig,
    *,
    enabled: bool = True,
    clock: Clock | None = None,
) -> StatusClient:
    """Build a :class:`StatusClient` for a single validated :data:`ProviderConfig`.

    Sole trust boundary that imports the private adapter modules. Dispatches
    on the :data:`ProviderConfig` discriminator and constructs the matching
    :data:`ProviderDependencies` before delegating to
    :func:`build_status_client`. The CLI calls this helper exclusively; it
    never imports ``_codex_jsonl`` or ``_opencode_go_httpx`` directly.

    The Codex branch lazily imports ``_codex_jsonl`` so the module can be
    imported without a working ``subprocess`` environment. The OpenCode Go
    branch is intentionally left as an ``INVALID`` fallthrough in the first
    work unit; the OpenCode Go activation is shipped in a follow-up work
    unit that fills this branch with the lazy ``_opencode_go_httpx`` import.
    """
    resolved_clock = CurrentClock() if clock is None else clock
    if type(config) is CodexJsonlConfig:
        from .providers._codex_jsonl import _CodexJsonlSession
        dependencies = CodexJsonlDependencies(resolved_clock, lambda: _CodexJsonlSession())
    elif type(config) is OpenCodeGoConfig:
        _fail(CompositionErrorKind.INVALID)
    else:
        _fail(CompositionErrorKind.INVALID)
    return build_status_client(config, dependencies, enabled=enabled)
