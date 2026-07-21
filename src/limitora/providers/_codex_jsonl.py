"""Private bounded JSONL transport for the Codex app-server handshake.

The failure vocabulary (``_CodexJsonlFailure`` /
``_CodexJsonlFailureKind``) is now owned by the
:mod:`limitora.providers._codex_jsonl_protocol` module so it can be
reused by the transport and mapping layers without circular imports.
The names are re-exported here so existing
``from ._codex_jsonl import _CodexJsonlFailure`` call sites keep
working byte-identically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import os
import select
import subprocess
import time
from typing import Callable, Protocol

from ._codex_jsonl_protocol import _CodexJsonlFailure, _CodexJsonlFailureKind


@dataclass(frozen=True)
class _CodexSessionSpec:
    runner: tuple[str, ...]
    timeout: timedelta
    max_output_bytes: int
    cleanup_allowance: timedelta

    def __post_init__(self) -> None:
        if self.timeout <= timedelta() or self.max_output_bytes <= 0 or self.cleanup_allowance <= timedelta():
            raise ValueError("Codex session bounds must be positive")


class _Process(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, maximum: int, timeout: float) -> bytes | None: ...
    def poll(self) -> int | None: ...
    def close_stdin(self) -> None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float) -> None: ...
    def kill(self) -> None: ...
    def close(self) -> None: ...


class _ProcessFactory(Protocol):
    def start(self, spec: _CodexSessionSpec) -> _Process: ...


class _PopenProcess:
    def __init__(self, command: tuple[str, ...]) -> None:
        if not command[0].startswith("/"):
            raise OSError("runner must be an explicit path")
        self._child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False)

    def write(self, data: bytes) -> None:
        assert self._child.stdin is not None
        self._child.stdin.write(data); self._child.stdin.flush()

    def read(self, maximum: int, timeout: float) -> bytes | None:
        assert self._child.stdout is not None
        ready, _, _ = select.select([self._child.stdout], [], [], max(timeout, 0))
        return os.read(self._child.stdout.fileno(), maximum) if ready else None

    def poll(self) -> int | None: return self._child.poll()
    def close_stdin(self) -> None:
        if self._child.stdin: self._child.stdin.close()
    def terminate(self) -> None:
        if self._child.poll() is None: self._child.terminate()
    def wait(self, timeout: float) -> None: self._child.wait(timeout)
    def kill(self) -> None: self._child.kill()
    def close(self) -> None:
        if self._child.stdout: self._child.stdout.close()


class _PopenFactory:
    def start(self, spec: _CodexSessionSpec) -> _Process: return _PopenProcess(spec.runner)


class _CodexJsonlSession:
    def __init__(self, factory: _ProcessFactory | None = None, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._factory, self._monotonic = factory or _PopenFactory(), monotonic

    def exchange(self, spec: _CodexSessionSpec) -> dict[str, object]:
        if not spec.runner: raise _CodexJsonlFailure(_CodexJsonlFailureKind.NOT_CONFIGURED)
        deadline, process, failure, payload = self._monotonic() + spec.timeout.total_seconds(), None, None, None
        self._output_bytes, self._buffer = 0, bytearray()
        try:
            process = self._factory.start(spec)
            self._check_deadline(deadline)
            self._send(process, {"jsonrpc":"2.0", "id":1, "method":"initialize", "params":{"protocolVersion":"2", "clientInfo":{"name":"limitora"}}})
            initial = self._read(process, deadline, spec.max_output_bytes, 1)
            if initial.get("protocolVersion") != "2": raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            self._check_deadline(deadline)
            self._send(process, {"jsonrpc":"2.0", "method":"initialized", "params":{}})
            self._check_deadline(deadline)
            self._send(process, {"jsonrpc":"2.0", "id":2, "method":"account/rateLimits/read", "params":{}})
            payload = self._read(process, deadline, spec.max_output_bytes, 2)
            if self._buffer: raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            extra = process.read(1, 0)
            if extra:
                self._output_bytes += len(extra)
                if self._output_bytes > spec.max_output_bytes: raise _CodexJsonlFailure(_CodexJsonlFailureKind.OUTPUT_LIMIT)
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            if process.poll() not in (None, 0): raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
        except _CodexJsonlFailure as error:
            failure = error
        except OSError:
            failure = _CodexJsonlFailure(_CodexJsonlFailureKind.UNAVAILABLE)
        except Exception:
            failure = _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
        finally:
            cleanup = self._cleanup(process, spec.cleanup_allowance) if process else None
        if cleanup: raise cleanup
        if failure: raise failure
        assert payload is not None
        return payload

    @staticmethod
    def _send(process: _Process, message: dict[str, object]) -> None:
        process.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")

    def _check_deadline(self, deadline: float) -> None:
        if deadline - self._monotonic() <= 0: raise _CodexJsonlFailure(_CodexJsonlFailureKind.TIMEOUT)

    def _read(self, process: _Process, deadline: float, cap: int, ident: int) -> dict[str, object]:
        while b"\n" not in self._buffer:
            remaining = deadline - self._monotonic()
            if remaining <= 0: raise _CodexJsonlFailure(_CodexJsonlFailureKind.TIMEOUT)
            chunk = process.read(max(1, min(cap - self._output_bytes, 4096)), remaining)
            if chunk is None: raise _CodexJsonlFailure(_CodexJsonlFailureKind.TIMEOUT)
            if not chunk: raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            self._output_bytes += len(chunk)
            if self._output_bytes > cap: raise _CodexJsonlFailure(_CodexJsonlFailureKind.OUTPUT_LIMIT)
            self._buffer.extend(chunk)
        newline = self._buffer.index(b"\n")
        line = bytes(self._buffer[:newline + 1])
        del self._buffer[:newline + 1]
        try: envelope = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError): raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL) from None
        if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0" or type(envelope.get("id")) is not int or envelope["id"] != ident:
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
        if set(envelope) - {"jsonrpc", "id", "result", "error"} or ("result" in envelope) == ("error" in envelope):
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
        if "error" in envelope:
            code = envelope["error"].get("code") if isinstance(envelope["error"], dict) else None
            kind = {401:_CodexJsonlFailureKind.UNAUTHORIZED, 403:_CodexJsonlFailureKind.UNAUTHORIZED, 429:_CodexJsonlFailureKind.RATE_LIMITED, 503:_CodexJsonlFailureKind.UNAVAILABLE}.get(code, _CodexJsonlFailureKind.PROTOCOL)
            raise _CodexJsonlFailure(kind)
        if not isinstance(envelope["result"], dict): raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
        return envelope["result"]

    @staticmethod
    def _cleanup(process: _Process, allowance: timedelta) -> _CodexJsonlFailure | None:
        failure = None
        try:
            process.close_stdin(); process.terminate()
            try: process.wait(allowance.total_seconds())
            except TimeoutError: process.kill(); process.wait(allowance.total_seconds())
        except Exception:
            failure = _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
        finally:
            try: process.close()
            except Exception: failure = _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
        return failure
