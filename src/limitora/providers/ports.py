"""Narrow injected boundaries for provider side effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol


class Clock(Protocol):
    """Supplies time without requiring providers to read a global clock."""

    def now(self) -> datetime: ...


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes | None = field(repr=False)
    timeout: timedelta


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpPort(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class FilePort(Protocol):
    def read(self, path: str) -> bytes: ...


@dataclass(frozen=True)
class CommandSpec:
    arguments: tuple[str, ...]
    timeout: timedelta


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


class CommandPort(Protocol):
    def run(self, command: CommandSpec) -> CommandResult: ...


class PortKind(str, Enum):
    HTTP = "http"
    FILE = "file"
    COMMAND = "command"


class PortFailureKind(str, Enum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"
    INVALID = "invalid"
    FAILED = "failed"


@dataclass(frozen=True)
class PortFailure:
    """A port-level failure whose adapter diagnostic must stay inside the boundary."""

    kind: PortFailureKind
    safe_message: str

    def __post_init__(self) -> None:
        if not self.safe_message or self.safe_message.strip() != self.safe_message:
            raise ValueError("safe message must be a non-empty trimmed string")
