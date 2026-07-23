"""Bounded process-lifecycle transport for the Codex app-server handshake.

Owns ``subprocess.Popen`` start, read/write I/O, deadline enforcement,
byte-cap, and cleanup. No JSON parsing, no session orchestration.
"""
from __future__ import annotations

from datetime import timedelta
import os
import select
import subprocess
import time
from typing import Callable, Optional, Protocol

from limitora._runner_path import _is_native_absolute_runner_path

from ._codex_jsonl_protocol import _CodexJsonlFailure, _CodexJsonlFailureKind


__all__ = [
    "_Process",
    "_ProcessFactory",
    "_PopenProcess",
    "_PopenFactory",
    "_BoundedLineReader",
    "_cleanup",
]


class _Process(Protocol):
    """The narrow process-handle protocol used by the transport layer."""

    def write(self, data: bytes) -> None: ...
    def read(self, maximum: int, timeout: float) -> Optional[bytes]: ...
    def poll(self) -> Optional[int]: ...
    def close_stdin(self) -> None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float) -> None: ...
    def kill(self) -> None: ...
    def close(self) -> None: ...


class _ProcessFactory(Protocol):
    """A factory that returns a ``_Process`` for a given session spec."""

    def start(self, spec: "_CodexSessionSpec") -> _Process: ...


class _PopenProcess:
    """A ``_Process`` backed by ``subprocess.Popen``.

    Enforces the runner-is-absolute-path invariant and connects
    ``stderr`` to ``DEVNULL`` so provider output never lands in
    process diagnostics.
    """

    def __init__(self, command: tuple[str, ...]) -> None:
        if not command or not _is_native_absolute_runner_path(command[0]):
            raise OSError("runner must be an explicit path")
        self._child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )

    def write(self, data: bytes) -> None:
        assert self._child.stdin is not None
        self._child.stdin.write(data)
        self._child.stdin.flush()

    def read(self, maximum: int, timeout: float) -> Optional[bytes]:
        assert self._child.stdout is not None
        ready, _, _ = select.select([self._child.stdout], [], [], max(timeout, 0))
        return os.read(self._child.stdout.fileno(), maximum) if ready else None

    def poll(self) -> Optional[int]:
        return self._child.poll()

    def close_stdin(self) -> None:
        if self._child.stdin is not None:
            self._child.stdin.close()

    def terminate(self) -> None:
        if self._child.poll() is None:
            self._child.terminate()

    def wait(self, timeout: float) -> None:
        self._child.wait(timeout)

    def kill(self) -> None:
        self._child.kill()

    def close(self) -> None:
        if self._child.stdout is not None:
            self._child.stdout.close()


class _PopenFactory:
    """The default :class:`_ProcessFactory`; uses :class:`_PopenProcess`."""

    def start(self, spec: "_CodexSessionSpec") -> _Process:
        return _PopenProcess(spec.runner)


class _BoundedLineReader:
    """A newline-delimited line reader with deadline + output-cap enforcement.

    ``read_line`` returns the next newline-terminated bytes line;
    ``read_one`` is a trailing-data probe. Raises ``TIMEOUT``,
    ``OUTPUT_LIMIT``, or ``PROTOCOL`` per the failure vocabulary.
    """

    def __init__(
        self,
        process: _Process,
        *,
        deadline: float,
        max_output_bytes: int,
        monotonic: Callable[[], float] = time.monotonic,
        read_chunk: int = 4096,
    ) -> None:
        self._process = process
        self._deadline = deadline
        self._max_output_bytes = max_output_bytes
        self._monotonic = monotonic
        self._read_chunk = read_chunk
        self._buffer = bytearray()
        self._output_bytes = 0

    def read_line(self) -> bytes:
        while b"\n" not in self._buffer:
            remaining = self._deadline - self._monotonic()
            if remaining <= 0:
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.TIMEOUT)
            budget = self._max_output_bytes - self._output_bytes
            if budget <= 0:
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.OUTPUT_LIMIT)
            chunk = self._process.read(max(1, min(budget, self._read_chunk)), remaining)
            if chunk is None:
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.TIMEOUT)
            if not chunk:
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
            self._output_bytes += len(chunk)
            if self._output_bytes > self._max_output_bytes:
                raise _CodexJsonlFailure(_CodexJsonlFailureKind.OUTPUT_LIMIT)
            self._buffer.extend(chunk)
        newline = self._buffer.index(b"\n")
        line = bytes(self._buffer[: newline + 1])
        del self._buffer[: newline + 1]
        return line

    def has_pending(self) -> bool:
        return bool(self._buffer)

    def read_one(self, timeout: float) -> Optional[bytes]:
        """Read up to 1 byte and append to the pending buffer. ``None`` on timeout/EOF."""
        chunk = self._process.read(1, timeout)
        if not chunk:
            return None
        self._output_bytes += len(chunk)
        if self._output_bytes > self._max_output_bytes:
            raise _CodexJsonlFailure(_CodexJsonlFailureKind.OUTPUT_LIMIT)
        self._buffer.extend(chunk)
        return chunk


def _cleanup(process: _Process, allowance: timedelta) -> Optional[_CodexJsonlFailure]:
    """Tear down ``process``: terminate -> wait -> kill -> wait -> close.

    Returns ``PROCESS`` failure on teardown exception, else ``None``.
    """
    failure: Optional[_CodexJsonlFailure] = None
    try:
        process.close_stdin()
        process.terminate()
        try:
            process.wait(allowance.total_seconds())
        except TimeoutError:
            process.kill()
            process.wait(allowance.total_seconds())
    except Exception:
        failure = _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
    finally:
        try:
            process.close()
        except Exception:
            failure = _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS)
    return failure
