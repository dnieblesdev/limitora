"""Provider contracts and deterministic test implementations."""

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
