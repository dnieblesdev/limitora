"""Provider contracts and deterministic test implementations."""

from datetime import timedelta

from .contract import (
    AuthorizationPolicy,
    ProviderDetection,
    ProviderError,
    ProviderErrorKind,
    ProviderReader,
    ProviderRequest,
    map_port_failure,
)
from .fake import FakeProvider
from .ports import (
    Clock,
    CommandPort,
    CommandResult,
    CommandSpec,
    FilePort,
    HttpPort,
    HttpRequest,
    HttpResponse,
    PortFailure,
    PortFailureKind,
    PortKind,
)


def _build_opencode_go_provider(
    workspace_id: str,
    auth_cookie: str,
    *,
    endpoint: str = "https://opencode.ai",
    timeout: timedelta = timedelta(seconds=10),
    clock=None,
    transport=None,
):
    """Build the private OpenCode Go adapter without widening provider contracts."""
    from ._opencode_go import OpenCodeGoConfig, OpenCodeGoProvider
    from ._opencode_go_httpx import _HttpxOpenCodeGoTransport

    config = OpenCodeGoConfig(workspace_id, auth_cookie, endpoint, timeout)
    selected_transport = _HttpxOpenCodeGoTransport(config) if transport is None else transport
    return OpenCodeGoProvider(config, selected_transport, clock=clock)

__all__ = [
    "AuthorizationPolicy",
    "Clock",
    "CommandPort",
    "CommandResult",
    "CommandSpec",
    "FakeProvider",
    "FilePort",
    "HttpPort",
    "HttpRequest",
    "HttpResponse",
    "PortFailure",
    "PortFailureKind",
    "PortKind",
    "ProviderDetection",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderReader",
    "ProviderRequest",
    "map_port_failure",
]
