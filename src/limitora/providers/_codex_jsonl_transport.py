"""Bounded process-lifecycle transport for the Codex app-server handshake.

Owns ``subprocess.Popen`` start, read/write I/O, deadline enforcement,
byte-cap, and cleanup. No JSON parsing, no session orchestration.
"""
from __future__ import annotations

from datetime import timedelta
from enum import Enum
import os
import queue
import subprocess
import threading
import time
from typing import Callable, NamedTuple, Optional, Protocol

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
    def join_reader(self, timeout: float) -> bool: ...


class _ProcessFactory(Protocol):
    """A factory that returns a ``_Process`` for a given session spec."""

    def start(self, spec: "_CodexSessionSpec") -> _Process: ...


class _ReadKind(Enum):
    DATA = 1
    EOF = 2
    ERROR = 3


class _ReadSignal(NamedTuple):
    kind: _ReadKind
    data: bytes = b""


class _PipeReader:
    """One bounded, blocking pipe reader for a subprocess stdout handle."""

    def __init__(self, descriptor: int, read_chunk: int = 4096) -> None:
        self._descriptor = descriptor
        self._read_chunk = read_chunk
        self._signals: queue.Queue[_ReadSignal] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="limitora-codex-stdout", daemon=True
        )
    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            chunk = os.read(self._descriptor, self._read_chunk)
            signal = _ReadSignal(_ReadKind.DATA, chunk) if chunk else _ReadSignal(_ReadKind.EOF)
        except Exception:
            signal = _ReadSignal(_ReadKind.ERROR)
        self._signals.put(signal)
        while signal.kind is _ReadKind.DATA and not self._stop.is_set():
            try:
                chunk = os.read(self._descriptor, self._read_chunk)
                signal = _ReadSignal(_ReadKind.DATA, chunk) if chunk else _ReadSignal(_ReadKind.EOF)
            except Exception:
                signal = _ReadSignal(_ReadKind.ERROR)
            self._signals.put(signal)

    def read(self, timeout: float) -> Optional[_ReadSignal]:
        try:
            return self._signals.get(timeout=max(timeout, 0))
        except queue.Empty:
            return None

    def stop_and_join(self, timeout: float) -> bool:
        self._stop.set()
        try:
            self._signals.get_nowait()
        except queue.Empty:
            pass
        self._thread.join(max(timeout, 0))
        return not self._thread.is_alive()


class _PopenProcess:
    """A ``_Process`` backed by ``subprocess.Popen``.

    Enforces the runner-is-absolute-path invariant and connects
    ``stderr`` to ``DEVNULL`` so provider output never lands in
    process diagnostics.
    """

    def __init__(self, command: tuple[str, ...], cleanup_allowance: timedelta = timedelta(seconds=1)) -> None:
        if not command or not _is_native_absolute_runner_path(command[0]):
            raise OSError("runner must be an explicit path")
        self._child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        self._reader: Optional[_PipeReader] = None
        self._remainder = bytearray()
        if not self._start_reader():
            _cleanup(self, cleanup_allowance)
            raise OSError("pipe reader startup failed")

    def _start_reader(self) -> bool:
        try:
            if self._child.stdout is None:
                return False
            self._reader = _PipeReader(self._child.stdout.fileno())
            self._reader.start()
            return True
        except Exception:
            return False

    def write(self, data: bytes) -> None:
        assert self._child.stdin is not None
        self._child.stdin.write(data)
        self._child.stdin.flush()

    def read(self, maximum: int, timeout: float) -> Optional[bytes]:
        if self._remainder:
            chunk = bytes(self._remainder[:maximum])
            del self._remainder[:maximum]
            return chunk
        assert self._reader is not None
        signal = self._reader.read(timeout)
        if signal is None:
            return None
        if signal.kind is _ReadKind.ERROR:
            raise OSError("pipe read failed")
        if signal.kind is _ReadKind.EOF:
            return b""
        chunk = signal.data[:maximum]
        self._remainder.extend(signal.data[maximum:])
        return chunk

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

    def join_reader(self, timeout: float) -> bool:
        return self._reader is None or self._reader.stop_and_join(timeout)


class _PopenFactory:
    """The default :class:`_ProcessFactory`; uses :class:`_PopenProcess`."""

    def start(self, spec: "_CodexSessionSpec") -> _Process:
        return _PopenProcess(spec.runner, spec.cleanup_allowance)


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

    def pending(self) -> bytes:
        return bytes(self._buffer)

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


def _attempt(action: Callable[[], object]) -> bool:
    try:
        return action() is not False
    except Exception:
        return False


def _cleanup(process: _Process, allowance: timedelta, monotonic: Callable[[], float] = time.monotonic) -> Optional[_CodexJsonlFailure]:
    """Tear down ``process``: terminate -> wait -> kill -> wait -> close.

    Returns ``PROCESS`` failure on teardown exception, else ``None``.
    """
    deadline = monotonic() + allowance.total_seconds()
    remaining = lambda: max(0.0, deadline - monotonic())
    failed = not _attempt(process.close_stdin)
    failed = not _attempt(process.terminate) or failed
    try:
        process.wait(remaining())
        must_kill = False
    except Exception as error:
        must_kill = True; failed = not isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) or failed
    if must_kill:
        failed = not _attempt(process.kill) or failed
        failed = not _attempt(lambda: process.wait(remaining())) or failed
    failed = not _attempt(process.close) or failed
    failed = not _attempt(lambda: process.join_reader(remaining())) or failed
    return _CodexJsonlFailure(_CodexJsonlFailureKind.PROCESS) if failed else None
