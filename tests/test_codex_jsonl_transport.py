"""Bounded transport reader tests for the Codex JSONL transport.

Contract of the ``_codex_jsonl_transport`` module: process-lifecycle
layer owning ``subprocess.Popen``, read/write I/O, deadline,
byte-cap, and cleanup. Tested with a ``FakeProcess`` in-process.
"""
from __future__ import annotations

import unittest
from datetime import timedelta
from typing import Callable
from unittest.mock import patch

from limitora.providers._codex_jsonl_protocol import _CodexJsonlFailure, _CodexJsonlFailureKind
from limitora.providers._codex_jsonl_transport import (
    _BoundedLineReader,
    _PopenFactory,
    _PopenProcess,
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
        from limitora.providers._codex_jsonl_transport import _cleanup
        failure = _cleanup(process, timedelta(milliseconds=10))
        self.assertIsNone(failure)
        self.assertEqual(["close", "terminate", "wait", "streams"], process.events)

    def test_cleanup_kills_when_terminate_wait_times_out(self):
        process = FakeProcess(wait_raises=(True, False))
        from limitora.providers._codex_jsonl_transport import _cleanup
        failure = _cleanup(process, timedelta(milliseconds=10))
        self.assertIsNone(failure)
        self.assertEqual(["close", "terminate", "wait", "kill", "wait", "streams"], process.events)

    def test_cleanup_returns_process_failure_when_close_raises(self):
        class BrokenProcess(FakeProcess):
            def close(self):
                self.events.append("streams")
                raise OSError("boom")

        process = BrokenProcess()
        from limitora.providers._codex_jsonl_transport import _cleanup
        failure = _cleanup(process, timedelta(milliseconds=10))
        self.assertIsNotNone(failure)
        self.assertEqual(_CodexJsonlFailureKind.PROCESS, failure.kind)


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
        ) as popen:
            _PopenProcess(command)

        popen.assert_called_once_with(
            command,
            stdin=-1,
            stdout=-1,
            stderr=-3,
            shell=False,
        )

    def test_popen_factory_exposes_start(self):
        factory = _PopenFactory()
        self.assertTrue(callable(getattr(factory, "start", None)))


if __name__ == "__main__":
    unittest.main()
