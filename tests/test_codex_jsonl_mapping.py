"""Mapping session contract tests for the Codex JSONL handshake."""
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

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def read(self, maximum: int, timeout: float):
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

    def test_outbound_frames_omit_jsonrpc_envelope_key(self):
        process = ScriptedProcess(reads=[ok(1, {}), ok(2, {"rateLimits": {}})])
        session, spec = self.session(process)
        session.exchange(spec)
        for frame in process.writes:
            self.assertNotIn(b"jsonrpc", frame)


if __name__ == "__main__":
    unittest.main()
