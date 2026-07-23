"""Mapping session: protocol + transport orchestration for the Codex app-server handshake.

Composes a bounded transport reader and a pure protocol codec to run
the corrected handshake: initialize (id 1), ``initialized`` notification,
``account/rateLimits/read`` (id 2). Notifications are silently skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import time
from typing import Callable, Optional

from ._codex_jsonl_protocol import (
    _CodexJsonlFailure,
    _CodexJsonlFailureKind,
    _ParsedFrame,
    build_notification,
    build_request,
    parse_frame,
)
from ._codex_jsonl_transport import (
    _BoundedLineReader,
    _PopenFactory,
    _Process,
    _ProcessFactory,
    _cleanup,
)


__all__ = [
    "_CodexJsonlFailure",
    "_CodexJsonlFailureKind",
    "_CodexSessionSpec",
    "_CodexJsonlSession",
]


_ERROR_CODE_KIND = {
    401: _CodexJsonlFailureKind.UNAUTHORIZED,
    403: _CodexJsonlFailureKind.UNAUTHORIZED,
    429: _CodexJsonlFailureKind.RATE_LIMITED,
    503: _CodexJsonlFailureKind.UNAVAILABLE,
}


@dataclass(frozen=True)
class _CodexSessionSpec:
    """Bounded configuration for one ``_CodexJsonlSession.exchange`` call."""

    runner: tuple[str, ...]
    timeout: timedelta
    max_output_bytes: int
    cleanup_allowance: timedelta
    client_version: str = "0.0.0+unknown"

    def __post_init__(self) -> None:
        if self.timeout <= timedelta() or self.max_output_bytes <= 0 or self.cleanup_allowance <= timedelta():
            raise ValueError("Codex session bounds must be positive")
        if not isinstance(self.client_version, str) or not self.client_version or self.client_version.strip() != self.client_version:
            raise ValueError("Codex client version must be a non-empty stripped string")


class _CodexJsonlSession:
    """The mapping session that orchestrates protocol + transport."""

    def __init__(
        self,
        factory: Optional[_ProcessFactory] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory: _ProcessFactory = factory if factory is not None else _PopenFactory()
        self._monotonic = monotonic

    def exchange(self, spec: _CodexSessionSpec) -> dict:
        if not spec.runner:
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.NOT_CONFIGURED)
        deadline = self._monotonic() + spec.timeout.total_seconds()
        process: Optional[_Process] = None
        failure: Optional[_CodexJsonlFailure] = None
        payload: Optional[dict] = None
        try:
            process = self._factory.start(spec)
            reader = _BoundedLineReader(
                process,
                deadline=deadline,
                max_output_bytes=spec.max_output_bytes,
                monotonic=self._monotonic,
            )
            self._check_deadline(deadline)
            process.write(build_request("initialize", 1, {
                "clientInfo": {"name": "limitora", "version": spec.client_version},
            }))
            self._read_correlated(reader, 1)
            self._check_deadline(deadline)
            process.write(build_notification("initialized", {}))
            self._check_deadline(deadline)
            process.write(build_request("account/rateLimits/read", 2, {}))
            payload = self._read_correlated(reader, 2).result
            extra = self._probe_trailing(reader, deadline)
            if extra is not None:
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            if process.poll() not in (None, 0):
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
        except _CodexJsonlFailure as error:
            failure = error
        except OSError:
            failure = _CodexJsonlFailure(_CodexJsonlFailureKind.UNAVAILABLE)
        except Exception:
            failure = _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
        finally:
            cleanup = _cleanup(process, spec.cleanup_allowance) if process else None
        if cleanup is not None:
            raise cleanup
        if failure is not None:
            raise failure
        assert payload is not None
        return payload

    def _check_deadline(self, deadline: float) -> None:
        if deadline - self._monotonic() <= 0:
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.TIMEOUT)

    def _read_correlated(
        self,
        reader: _BoundedLineReader,
        ident: int,
    ) -> _ParsedFrame:
        """Read frames until the correlated ``ident`` arrives. Skips notifications; maps error codes via ``_ERROR_CODE_KIND``."""
        while True:
            line = reader.read_line()
            frame = parse_frame(line)
            if frame.ident is None:
                continue
            if frame.ident != ident:
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            if frame.error is not None:
                code = frame.error.get("code") if isinstance(frame.error, dict) else None
                kind = _ERROR_CODE_KIND.get(code if isinstance(code, int) else None, _CodexJsonlFailureKind.PROTOCOL)
                raise _CodexJsonlFailure(kind)
            assert frame.result is not None
            return frame

    def _probe_trailing(self, reader: _BoundedLineReader, deadline: float) -> Optional[bytes]:
        """Probe for trailing output after the last newline. ``None`` on EOF/timeout, bytes on extra data."""
        if reader.has_pending():
            return reader.pending()
        return reader.read_one(timeout=min(0.001, max(0.0, deadline - self._monotonic())))
