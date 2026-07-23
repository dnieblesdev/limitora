"""Bounded transport reader tests for the Codex JSONL transport.

Contract of the ``_codex_jsonl_transport`` module: process-lifecycle
layer owning ``subprocess.Popen``, read/write I/O, deadline,
byte-cap, and cleanup. Tested with a ``FakeProcess`` in-process.
"""
from __future__ import annotations

import unittest
from datetime import timedelta
import os
import subprocess
import sys
import threading
import time
from typing import Callable
from unittest.mock import MagicMock, patch

from limitora.providers._codex_jsonl_protocol import _CodexJsonlFailure, _CodexJsonlFailureKind
from limitora.providers._codex_jsonl import _CodexJsonlSession, _CodexSessionSpec
from limitora.providers._codex_jsonl_transport import (
    _BoundedLineReader,
    _PipeReader,
    _PopenFactory,
    _PopenProcess,
    _ReadKind,
    _ReadSignal,
    _cleanup,
)


class FakeProcess:
    """A scripted stand-in for ``_Process``. ``reads`` queues read results; ``wait_raises`` flags timeouts."""

    def __init__(self, reads=(), *, wait_raises=()) -> None:
        self.reads = list(reads)
        self._wait_raises = list(wait_raises)
        self.events: list[str] = []

    def read(self, maximum: int, timeout: float):
        if not self.reads:
            return b""
        return self.reads.pop(0)

    def write(self, data: bytes) -> None: self.events.append("write")
    def poll(self): return None
    def close_stdin(self): self.events.append("close")
    def terminate(self): self.events.append("terminate")
    def wait(self, timeout: float):
        self.events.append("wait")
        if self._wait_raises and self._wait_raises.pop(0):
            raise TimeoutError
    def kill(self): self.events.append("kill")
    def close(self): self.events.append("streams")
    def join_reader(self, timeout): self.events.append("join"); return True


def make_reader(process, *, deadline: float, max_output_bytes: int = 4096,
                monotonic: Callable[[], float] = lambda: 100.0) -> _BoundedLineReader:
    return _BoundedLineReader(process, deadline=deadline, max_output_bytes=max_output_bytes, monotonic=monotonic)


class BoundedLineReaderLineTests(unittest.TestCase):
    def test_read_line_returns_first_newline_terminated_chunk_in_one_call(self):
        process = FakeProcess(reads=[b'{"id":1,"result":{}}\n'])
        reader = make_reader(process, deadline=200.0)
        self.assertEqual(b'{"id":1,"result":{}}\n', reader.read_line())

    def test_read_line_assembles_partial_chunks_until_newline_arrives(self):
        process = FakeProcess(reads=[b'{"id":1', b',"result":{}', b'}\n'])
        reader = make_reader(process, deadline=200.0)
        self.assertEqual(b'{"id":1,"result":{}}\n', reader.read_line())

    def test_read_line_consumes_excess_data_beyond_newline_into_pending_buffer(self):
        process = FakeProcess(reads=[b'{"id":1,"result":{}}\n{"id":2,"result":{}}\n'])
        reader = make_reader(process, deadline=200.0)
        self.assertEqual(b'{"id":1,"result":{}}\n', reader.read_line())
        self.assertTrue(reader.has_pending(), "second line must remain pending")
        self.assertEqual(b'{"id":2,"result":{}}\n', reader.read_line())


class BoundedLineReaderDeadlineTests(unittest.TestCase):
    def test_read_line_raises_timeout_when_deadline_already_passed(self):
        process = FakeProcess(reads=[b""])
        reader = make_reader(process, deadline=50.0, monotonic=lambda: 100.0)
        with self.assertRaises(_CodexJsonlFailure) as raised:
            reader.read_line()
        self.assertEqual(_CodexJsonlFailureKind.TIMEOUT, raised.exception.kind)

    def test_read_line_raises_timeout_when_process_returns_none(self):
        process = FakeProcess(reads=[None])
        reader = make_reader(process, deadline=200.0, monotonic=lambda: 100.0)
        with self.assertRaises(_CodexJsonlFailure) as raised:
            reader.read_line()
        self.assertEqual(_CodexJsonlFailureKind.TIMEOUT, raised.exception.kind)


class BoundedLineReaderOutputLimitTests(unittest.TestCase):
    def test_newline_at_exact_cap_is_accepted(self):
        wire = b"x\n"
        reader = make_reader(FakeProcess(reads=[wire]), deadline=200.0, max_output_bytes=len(wire))
        self.assertEqual(wire, reader.read_line())

    def test_read_line_raises_output_limit_when_accumulated_bytes_exceed_cap(self):
        process = FakeProcess(reads=[b"x" * 4096, b"y" * 100])
        reader = make_reader(process, deadline=200.0, max_output_bytes=4096)
        with self.assertRaises(_CodexJsonlFailure) as raised:
            reader.read_line()
        self.assertEqual(_CodexJsonlFailureKind.OUTPUT_LIMIT, raised.exception.kind)


class BoundedLineReaderProtocolTests(unittest.TestCase):
    def test_read_line_raises_protocol_when_process_returns_empty_bytes(self):
        process = FakeProcess(reads=[b""])
        reader = make_reader(process, deadline=200.0, monotonic=lambda: 100.0)
        with self.assertRaises(_CodexJsonlFailure) as raised:
            reader.read_line()
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)


class BoundedLineReaderHasPendingTests(unittest.TestCase):
    def test_has_pending_false_when_buffer_is_empty(self):
        reader = make_reader(FakeProcess(reads=[]), deadline=200.0)
        self.assertFalse(reader.has_pending())

    def test_has_pending_true_after_partial_chunk_arrived(self):
        process = FakeProcess(reads=[b"partial-no-newline", b""])
        reader = make_reader(process, deadline=200.0)
        with self.assertRaises(_CodexJsonlFailure) as raised:
            reader.read_line()
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)
        self.assertTrue(reader.has_pending())


class BoundedLineReaderReadOneTests(unittest.TestCase):
    def test_read_one_returns_bytes_when_extra_data_is_available(self):
        process = FakeProcess(reads=[b"extra"])
        reader = make_reader(process, deadline=200.0)
        self.assertEqual(b"extra", reader.read_one(timeout=0.0))

    def test_read_one_returns_none_when_process_returns_none(self):
        process = FakeProcess(reads=[None])
        reader = make_reader(process, deadline=200.0)
        self.assertIsNone(reader.read_one(timeout=0.0))

    def test_read_one_returns_none_when_process_returns_empty_bytes(self):
        process = FakeProcess(reads=[b""])
        reader = make_reader(process, deadline=200.0)
        self.assertIsNone(reader.read_one(timeout=0.0))

    def test_read_one_appends_to_pending_buffer_without_consuming(self):
        process = FakeProcess(reads=[b"leftover"])
        reader = make_reader(process, deadline=200.0)
        reader.read_one(timeout=0.0)
        self.assertTrue(reader.has_pending())
        self.assertEqual(b"leftover", bytes(reader._buffer))  # type: ignore[attr-defined]


class CleanupEscalationTests(unittest.TestCase):
    def test_cleanup_terminates_then_waits_when_process_exits_within_allowance(self):
        process = FakeProcess()
        failure = _cleanup(process, timedelta(milliseconds=10))
        self.assertIsNone(failure)
        self.assertEqual(["close", "terminate", "wait", "streams", "join"], process.events)

    def test_cleanup_kills_when_terminate_wait_times_out(self):
        process = FakeProcess(wait_raises=(True, False))
        failure = _cleanup(process, timedelta(milliseconds=10))
        self.assertIsNone(failure)
        self.assertEqual(["close", "terminate", "wait", "kill", "wait", "streams", "join"], process.events)

    def test_cleanup_returns_process_failure_when_close_raises(self):
        class BrokenProcess(FakeProcess):
            def close(self):
                self.events.append("streams")
                raise OSError("boom")

        process = BrokenProcess(); process.timeouts = []
        failure = _cleanup(process, timedelta(milliseconds=10))
        self.assertIsNotNone(failure)
        self.assertEqual(_CodexJsonlFailureKind.PROCESS, failure.kind)

    def test_cleanup_escalates_real_timeout_and_continues_after_failures(self):
        class BrokenProcess(FakeProcess):
            def close_stdin(self): self.events.append("close"); raise OSError
            def wait(self, timeout):
                self.events.append("wait"); self.timeouts.append(timeout)
                raise OSError("token=secret" if self.events.count("wait") == 1 else "")
            def close(self): self.events.append("streams"); raise OSError
            def join_reader(self, timeout): self.events.append("join"); self.timeouts.append(timeout); return False
        process = BrokenProcess(); process.timeouts = []
        times = iter((10.0, 10.2, 10.7, 11.2)); failure = _cleanup(process, timedelta(seconds=1), lambda: next(times))
        self.assertEqual(_CodexJsonlFailureKind.PROCESS, failure.kind); self.assertEqual(["close", "terminate", "wait", "kill", "wait", "streams", "join"], process.events)
        self.assertEqual([0.8, 0.3, 0.0], [round(value, 1) for value in process.timeouts])
class PipeReaderTests(unittest.TestCase):
    def test_data_eof_and_error_are_typed(self):
        read_fd, write_fd = os.pipe()
        try:
            reader = _PipeReader(read_fd, read_chunk=4); reader.start()
            os.write(write_fd, b"abc"); self.assertEqual(_ReadSignal(_ReadKind.DATA, b"abc"), reader.read(1))
            os.close(write_fd); write_fd = -1; self.assertEqual(_ReadSignal(_ReadKind.EOF), reader.read(1)); self.assertTrue(reader.stop_and_join(1))
            broken = _PipeReader(-1); broken.start(); self.assertEqual(_ReadSignal(_ReadKind.ERROR), broken.read(1)); self.assertTrue(broken.stop_and_join(1))
        finally:
            os.close(read_fd); os.close(write_fd) if write_fd >= 0 else None
    def test_bounded_backpressure_shutdown_and_join(self):
        read_fd, write_fd = os.pipe()
        try:
            reader = _PipeReader(read_fd, read_chunk=1); reader.start(); os.write(write_fd, b"abc")
            self.assertEqual(1, reader._signals.maxsize); self.assertTrue(reader.stop_and_join(1))
        finally:
            os.close(read_fd); os.close(write_fd)
    def test_join_is_bounded_while_pipe_read_is_blocked(self):
        read_fd, write_fd = os.pipe()
        try:
            reader = _PipeReader(read_fd); reader.start(); self.assertFalse(reader.stop_and_join(0))
            os.close(write_fd); write_fd = -1; self.assertTrue(reader.stop_and_join(1))
        finally:
            os.close(read_fd); os.close(write_fd) if write_fd >= 0 else None
class PopenProcessContractTests(unittest.TestCase):
    def test_popen_process_rejects_relative_runner(self):
        with self.assertRaises(OSError):
            _PopenProcess(("codex",))

    def test_popen_process_validates_before_launch(self):
        with patch(
            "limitora.providers._codex_jsonl_transport._is_native_absolute_runner_path",
            return_value=False,
        ) as validator, patch(
            "limitora.providers._codex_jsonl_transport.subprocess.Popen"
        ) as popen:
            with self.assertRaises(OSError):
                _PopenProcess(("native-runner", "opaque-argument"))

        validator.assert_called_once_with("native-runner")
        popen.assert_not_called()

    def test_popen_process_preserves_exact_argv_without_shell(self):
        command = ("native-runner", "app-server", "--stdio")
        with patch(
            "limitora.providers._codex_jsonl_transport._is_native_absolute_runner_path",
            return_value=True,
        ), patch(
            "limitora.providers._codex_jsonl_transport.subprocess.Popen"
        ) as popen, patch(
            "limitora.providers._codex_jsonl_transport._PipeReader"
        ):
            _PopenProcess(command)

        popen.assert_called_once_with(
            command,
            stdin=-1,
            stdout=-1,
            stderr=-3,
            shell=False,
        )

    def test_read_preserves_partial_remainder_timeout_eof_and_error(self):
        child = MagicMock()
        reader = MagicMock()
        reader.read.side_effect = [
            _ReadSignal(_ReadKind.DATA, b"abc"), None,
            _ReadSignal(_ReadKind.EOF), _ReadSignal(_ReadKind.ERROR),
        ]
        with patch("limitora.providers._codex_jsonl_transport._is_native_absolute_runner_path", return_value=True), patch(
            "limitora.providers._codex_jsonl_transport.subprocess.Popen", return_value=child
        ), patch("limitora.providers._codex_jsonl_transport._PipeReader", return_value=reader):
            process = _PopenProcess(("native-runner",))
        self.assertEqual(b"ab", process.read(2, 1))
        self.assertEqual(b"c", process.read(2, 0))
        self.assertIsNone(process.read(1, 0))
        self.assertEqual(b"", process.read(1, 0))
        with self.assertRaises(OSError): process.read(1, 0)

    def test_reader_start_failure_cleans_launched_child_and_is_redacted(self):
        child, reader = MagicMock(), MagicMock(); child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("token=secret", 0.001), None]; reader.start.side_effect = RuntimeError("token=secret"); reader.stop_and_join.return_value = True
        with patch("limitora.providers._codex_jsonl_transport._is_native_absolute_runner_path", return_value=True), patch(
            "limitora.providers._codex_jsonl_transport.subprocess.Popen", return_value=child
        ), patch("limitora.providers._codex_jsonl_transport._PipeReader", return_value=reader):
            with self.assertRaises(OSError) as raised: _PopenProcess(("native-runner",), timedelta(milliseconds=1))
        self.assertEqual("pipe reader startup failed", str(raised.exception)); self.assertIsNone(raised.exception.__context__)
        child.stdin.close.assert_called_once_with(); child.terminate.assert_called_once_with()
        self.assertEqual(2, child.wait.call_count); child.kill.assert_called_once_with(); child.stdout.close.assert_called_once_with(); self.assertLessEqual(reader.stop_and_join.call_args.args[0], 0.001)
    def test_popen_factory_exposes_start(self):
        factory = _PopenFactory()
        self.assertTrue(callable(getattr(factory, "start", None)))
class SyntheticChildTests(unittest.TestCase):
    def exchange(self, mode: str, *, cap: int = 4096):
        script = f'''import json, sys, time
mode = {mode!r}
for raw in sys.stdin.buffer:
    ident = json.loads(raw).get("id")
    if ident == 1:
        if mode == "eof": break
        if mode == "cap":
            sys.stdout.buffer.write(b"x" * 128); sys.stdout.buffer.flush(); time.sleep(2)
        else:
            wire = b'{{"id":1,"result":{{}}}}\\n'; sys.stdout.buffer.write(wire[:7]); sys.stdout.buffer.flush(); sys.stdout.buffer.write(wire[7:]); sys.stdout.buffer.flush()
    elif ident == 2:
        if mode == "timeout": time.sleep(2)
        else:
            wire = b'{{"id":2,"result":{{"rateLimits":{{}}}}}}\\n'; sys.stdout.buffer.write(wire + (b"trailing" if mode == "trailing" else b"")); sys.stdout.buffer.flush()
            if mode == "success": time.sleep(2)
        break
'''
        timeout = timedelta(seconds=5) if mode == "success" else timedelta(milliseconds=100); spec = _CodexSessionSpec((sys.executable, "-u", "-c", script), timeout, cap, timedelta(milliseconds=200))
        return _CodexJsonlSession().exchange(spec)
    def test_interactive_partial_writes_and_reader_exit(self):
        started = time.monotonic(); self.assertEqual({"rateLimits": {}}, self.exchange("success"))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(any(t.name == "limitora-codex-stdout" and t.is_alive() for t in threading.enumerate()))
    def test_timeout_eof_output_cap_and_trailing_data(self):
        cases = (("timeout", 4096, _CodexJsonlFailureKind.TIMEOUT), ("eof", 4096, _CodexJsonlFailureKind.PROTOCOL),
                 ("cap", 64, _CodexJsonlFailureKind.OUTPUT_LIMIT), ("trailing", 4096, _CodexJsonlFailureKind.PROTOCOL))
        for mode, cap, kind in cases:
            with self.subTest(mode=mode):
                with self.assertRaises(_CodexJsonlFailure) as raised: self.exchange(mode, cap=cap)
                self.assertEqual(kind, raised.exception.kind)
                self.assertFalse(any(t.name == "limitora-codex-stdout" and t.is_alive() for t in threading.enumerate()))
if __name__ == "__main__":
    unittest.main()
