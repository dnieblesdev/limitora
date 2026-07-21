"""Pure JSON-RPC codec for the Codex app-server handshake.

This module owns the protocol layer: it builds and parses JSON-RPC
envelope bytes. It performs **no** I/O and has **no** dependencies on
the transport (process lifecycle) layer or the mapping (session
orchestration) layer.

The split mirrors the spec for ``codex-handshake-fix``:

    * Transport owns ``subprocess.Popen`` and read/write I/O.
    * Protocol owns JSON-RPC envelope build and parse.
    * Mapping owns request ``id`` correlation, error-code lookup, and
      session orchestration.

Public surface for downstream modules:

    * :class:`_CodexJsonlFailure`
    * :class:`_CodexJsonlFailureKind`
    * :class:`_ParsedFrame`
    * :func:`build_request`
    * :func:`build_notification`
    * :func:`parse_frame`
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Optional


class _CodexJsonlFailureKind(str, Enum):
    """Categories of failures emitted by the Codex JSONL transport."""

    NOT_CONFIGURED = "not_configured"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    PROTOCOL = "protocol"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    PROCESS = "process"


class _CodexJsonlFailure(Exception):
    """A typed, redacted failure raised by the Codex JSONL transport.

    The ``safe_message`` deliberately contains no provider payload
    (no token, no request, no response body) and is safe to surface
    in user-facing diagnostics.
    """

    def __init__(self, kind: _CodexJsonlFailureKind) -> None:
        self.kind = kind
        self.safe_message = "Codex JSONL transport " + kind.value.replace("_", " ")
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class _ParsedFrame:
    """A single decoded JSON-RPC frame from the Codex app-server.

    ``ident`` distinguishes a correlated response (``int``) from a
    server-pushed notification (``None``) that the mapping layer must
    silently skip.
    """

    ident: Optional[int]
    result: Optional[dict]
    error: Optional[dict]


def build_request(method: str, ident: int, params: dict) -> bytes:
    """Encode a request frame with **no** ``jsonrpc`` envelope key.

    The Codex app-server rejects outbound frames that carry the
    standard ``jsonrpc: "2.0"`` discriminator. Outbound is custom:
    ``{"id": ..., "method": ..., "params": ...}``.
    """
    payload = {"id": ident, "method": method, "params": params}
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def build_notification(method: str, params: dict) -> bytes:
    """Encode a notification frame with **no** ``id`` and **no** ``jsonrpc``.

    The ``initialized`` notification follows the initial ``initialize``
    response; it has no ``id`` because notifications are not correlated
    to a request.
    """
    payload = {"method": method, "params": params}
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


# Envelope keys accepted on inbound frames. ``jsonrpc`` is accepted for
# tolerance with the standard JSON-RPC 2.0 wire format; the spec forbids
# it on outbound, not on inbound.
_INBOUND_KEYS = frozenset({"id", "method", "params", "result", "error", "jsonrpc"})


def parse_frame(line: bytes) -> _ParsedFrame:
    """Decode a single JSON-RPC frame.

    Returns:

        * :class:`_ParsedFrame` with ``ident`` set to ``int`` for a
          correlated response (exactly one of ``result`` or ``error``).
        * :class:`_ParsedFrame` with ``ident`` set to ``None`` for a
          server-pushed notification (``method`` present, no ``id``,
          no ``result``/``error``).

    Raises:
        _CodexJsonlFailure: with kind ``PROTOCOL`` for malformed JSON,
            non-object envelopes, unknown envelope keys, wrong shape
            (both/neither of ``result`` and ``error``), or wrong types
            (``id`` not int, ``result``/``error`` not dict, ``method``
            absent on a frame without ``id``).
    """
    try:
        envelope = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL) from None
    if not isinstance(envelope, dict):
        raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
    extra_keys = set(envelope) - _INBOUND_KEYS
    if extra_keys:
        raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
    if "id" in envelope:
        ident = envelope["id"]
        if not isinstance(ident, int) or isinstance(ident, bool):
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
        has_result = "result" in envelope
        has_error = "error" in envelope
        if has_result == has_error:
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
        if has_error:
            error = envelope["error"]
            if not isinstance(error, dict):
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            return _ParsedFrame(ident=ident, result=None, error=error)
        result = envelope["result"]
        if not isinstance(result, dict):
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
        return _ParsedFrame(ident=ident, result=result, error=None)
    # No ``id`` => notification. Must carry ``method`` and must not
    # carry ``result``/``error`` (notifications are not responses).
    if "method" not in envelope or "result" in envelope or "error" in envelope:
        raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
    return _ParsedFrame(ident=None, result=None, error=None)
