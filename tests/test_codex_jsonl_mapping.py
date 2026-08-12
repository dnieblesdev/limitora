"""Mapping session tests for the Codex JSONL transport.

The 7 contract rows the spec mandates for the refactored
``_CodexJsonlSession``: (a) sequence, (b) notification skip,
(c) multiple notifications, (d) unknown id, (e) clientInfo.version,
(f) trailing data, (g) no ``jsonrpc`` in outbound frames.
"""
from __future__ import annotations

from datetime import timedelta
import json
import unittest

from limitora.providers._codex_jsonl import _CodexJsonlFailure, _CodexJsonlFailureKind, _CodexJsonlSession, _CodexSessionSpec


def line(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def ok(ident: int, result: dict) -> bytes:
    return line({"id": ident, "result": result})


def err(ident: int, code: int, message: str = "token=secret") -> bytes:
    return line({"id": ident, "error": {"code": code, "message": message}})


def notification(method: str, params: dict | None = None) -> bytes:
    return line({"method": method, "params": params or {}})


class ScriptedProcess:
    """A scripted ``_Process``. ``reads`` queues read results; ``writes`` and ``events`` record activity."""

    def __init__(self, reads=(), *, exit_code=0, cleanup_waits=(False,)) -> None:
        self.reads = list(reads)
        self.exit_code = exit_code
        self.cleanup_waits = list(cleanup_waits)
        self.writes: list[bytes] = []
        self.events: list[str] = []
        self.timeouts: list[float] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def read(self, maximum: int, timeout: float):
        self.timeouts.append(timeout)
        if not self.reads:
            return b""
        chunk = self.reads.pop(0)
        if chunk is None:
            return None
        if len(chunk) > maximum:
            self.reads.insert(0, chunk[maximum:])
            return chunk[:maximum]
        return chunk

    def poll(self):
        return self.exit_code

    def close_stdin(self): self.events.append("close")
    def terminate(self): self.events.append("terminate")
    def wait(self, timeout: float):
        self.events.append("wait")
        if self.cleanup_waits.pop(0):
            raise TimeoutError
    def kill(self): self.events.append("kill")
    def close(self): self.events.append("streams")
    def join_reader(self, timeout): self.events.append("join"); return True


class _CodexServerMock(ScriptedProcess):
    """Stateful server boundary that answers only the documented handshake."""

    _METHODS = {"initialize", "initialized", "account/rateLimits/read"}
    _MISSING = object()

    def __init__(self, client_version: str, rate_limits: dict, *, notifications=("remoteControl/status/changed",)) -> None:
        super().__init__()
        self._notifications = tuple(notifications)
        self._steps = (
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "limitora", "version": client_version}}},
            {"method": "initialized", "params": {}},
            {"id": 2, "method": "account/rateLimits/read", "params": {}},
        )
        self._responses = ({"id": 1, "result": {}}, None, {"id": 2, "result": {"rateLimits": rate_limits}})
        self._step = 0

    def write(self, data: bytes) -> None:
        try:
            frame = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AssertionError("Codex mock received an invalid frame payload") from None
        if not isinstance(frame, dict):
            raise AssertionError("Codex mock received an invalid frame payload")
        if self._step >= len(self._steps):
            raise AssertionError("Codex mock received a write after the handshake")

        expected = self._steps[self._step]
        actual_method = frame.get("method")
        if actual_method != expected["method"]:
            if actual_method in self._METHODS:
                raise AssertionError("Codex mock received a method out of order")
            raise AssertionError("Codex mock received an unexpected method")
        if frame.get("id", self._MISSING) != expected.get("id", self._MISSING):
            raise AssertionError("Codex mock received an unexpected request id")
        if frame.get("params", object()) != expected["params"] or set(frame) != set(expected):
            raise AssertionError("Codex mock received an unexpected payload")

        self.writes.append(data)
        response = self._responses[self._step]
        if response is not None:
            self.reads.extend(notification(method) for method in self._notifications)
            self.reads.append(line(response))
        self._step += 1


class MappingFactory:
    def __init__(self, process): self.process, self.specs = process, []
    def start(self, spec): self.specs.append(spec); return self.process


class MappingSessionContractTests(unittest.TestCase):

    def session(self, process, *, client_version="1.2.3", runner=("/declared/codex",)):
        spec = _CodexSessionSpec(
            runner, timedelta(seconds=1), 4096, timedelta(milliseconds=10),
            client_version=client_version,
        )
        return _CodexJsonlSession(MappingFactory(process), lambda: 0.0), spec

    def sent_methods(self, process: ScriptedProcess) -> list[str]:
        return [json.loads(item)["method"] for item in process.writes]

    def sent_payloads(self, process: ScriptedProcess) -> list[dict]:
        return [json.loads(item) for item in process.writes]

    def test_sequence_is_initialize_then_initialized_notification_then_rate_limits_read(self):
        process = ScriptedProcess(reads=[ok(1, {}), ok(2, {"rateLimits": {}})])
        session, spec = self.session(process)
        self.assertEqual({"rateLimits": {}}, session.exchange(spec))
        self.assertEqual(
            ["initialize", "initialized", "account/rateLimits/read"],
            self.sent_methods(process),
        )
        sent = self.sent_payloads(process)
        self.assertEqual([1, None, 2], [item.get("id") for item in sent])

    def test_exchange_uses_stateful_server_contract_and_returns_rate_limits(self):
        process = _CodexServerMock(
            "1.2.3",
            {"five_hour": {"used_percent": 12}},
            notifications=("remoteControl/status/changed", "server/ready"),
        )
        session, spec = self.session(process)

        self.assertEqual({"rateLimits": {"five_hour": {"used_percent": 12}}}, session.exchange(spec))
        self.assertEqual(
            ["initialize", "initialized", "account/rateLimits/read"],
            self.sent_methods(process),
        )
        self.assertEqual([1, None, 2], [json.loads(item).get("id") for item in process.writes])

    def test_notification_before_response_is_silently_skipped(self):
        process = ScriptedProcess(reads=[
            notification("server/hello"),
            ok(1, {}),
            ok(2, {"rateLimits": {}}),
        ])
        session, spec = self.session(process)
        self.assertEqual({"rateLimits": {}}, session.exchange(spec))

    def test_multiple_notifications_in_same_buffer_are_all_skipped(self):
        process = ScriptedProcess(reads=[
            notification("server/event1"),
            notification("server/event2"),
            notification("server/event3"),
            ok(1, {}),
            ok(2, {"rateLimits": {}}),
        ])
        session, spec = self.session(process)
        self.assertEqual({"rateLimits": {}}, session.exchange(spec))

    def test_unknown_id_response_raises_protocol(self):
        process = ScriptedProcess(reads=[ok(99, {}), ok(1, {})])
        session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised:
            session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_initialize_payload_carries_client_info_name_and_version(self):
        process = ScriptedProcess(reads=[ok(1, {}), ok(2, {"rateLimits": {}})])
        session, spec = self.session(process, client_version="9.9.9")
        session.exchange(spec)
        sent = self.sent_payloads(process)
        self.assertEqual({"name": "limitora", "version": "9.9.9"}, sent[0]["params"]["clientInfo"])

    def test_initialize_payload_does_not_carry_protocol_version(self):
        process = ScriptedProcess(reads=[ok(1, {}), ok(2, {"rateLimits": {}})])
        session, spec = self.session(process)
        session.exchange(spec)
        sent = self.sent_payloads(process)
        self.assertNotIn("protocolVersion", sent[0]["params"])

    def test_trailing_data_after_final_response_raises_protocol(self):
        process = ScriptedProcess(reads=[
            ok(1, {}),
            ok(2, {"rateLimits": {}}),
            b'{"id":3,"result":{}}\n',
        ])
        session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised:
            session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_same_chunk_trailing_data_is_rejected(self):
        process = ScriptedProcess(reads=[ok(1, {}), ok(2, {}) + b"trailing"]); session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)
    def test_cap_is_cumulative_across_notifications_and_both_responses(self):
        transcript = notification("n") + ok(1, {}) + ok(2, {})
        process = ScriptedProcess(reads=[transcript]); session, spec = self.session(process)
        spec = _CodexSessionSpec(spec.runner, spec.timeout, len(transcript) - 1, spec.cleanup_allowance)
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.OUTPUT_LIMIT, raised.exception.kind)
    def test_trailing_probe_uses_remaining_original_deadline(self):
        for now, expected in ((10.25, 0.001), (10.9995, 0.0005)):
            process = ScriptedProcess(reads=[ok(1, {}), ok(2, {}), None]); times = iter((10.0,) * 6 + (now,))
            spec = _CodexSessionSpec(("/declared/codex",), timedelta(seconds=1), 4096, timedelta(milliseconds=10)); _CodexJsonlSession(MappingFactory(process), lambda: next(times)).exchange(spec)
            self.assertAlmostEqual(expected, process.timeouts[-1])

    def test_outbound_frames_omit_jsonrpc_envelope_key(self):
        process = ScriptedProcess(reads=[ok(1, {}), ok(2, {"rateLimits": {}})])
        session, spec = self.session(process)
        session.exchange(spec)
        for frame in process.writes:
            self.assertNotIn(b"jsonrpc", frame)


if __name__ == "__main__":
    unittest.main()
