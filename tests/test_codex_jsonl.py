from __future__ import annotations

from datetime import timedelta
import json
import unittest

from limitora.providers._codex_jsonl import (_CodexJsonlFailure, _CodexJsonlFailureKind,
    _CodexJsonlSession, _CodexSessionSpec)


class FakeProcess:
    def __init__(self, lines=(), *, exit_code=0, cleanup_waits=(False,)) -> None:
        self.lines = list(lines)
        self.exit_code, self.cleanup_waits = exit_code, list(cleanup_waits)
        self.writes, self.events = [], []

    def write(self, data): self.writes.append(data)
    def read(self, maximum, timeout):
        if not self.lines: return b""
        chunk = self.lines.pop(0)
        if chunk is None: return None
        if len(chunk) > maximum:
            self.lines.insert(0, chunk[maximum:])
            return chunk[:maximum]
        return chunk
    def poll(self): return self.exit_code
    def close_stdin(self): self.events.append("close")
    def terminate(self): self.events.append("terminate")
    def wait(self, timeout):
        self.events.append("wait")
        if self.cleanup_waits.pop(0): raise TimeoutError
    def kill(self): self.events.append("kill")
    def close(self): self.events.append("streams")
    def join_reader(self, timeout): self.events.append("join"); return True


class Factory:
    def __init__(self, process): self.process, self.specs = process, []
    def start(self, spec): self.specs.append(spec); return self.process


def line(value): return json.dumps(value).encode() + b"\n"
def ok(ident, result): return line({"id": ident, "result": result})
def err(ident, code, message="token=secret"): return line({"id": ident, "error": {"code": code, "message": message}})


class CodexJsonlTests(unittest.TestCase):
    def session(self, process, runner=("/declared/runner",)):
        return (
            _CodexJsonlSession(Factory(process), lambda: 0.0),
            _CodexSessionSpec(runner, timedelta(seconds=1), 1024, timedelta(milliseconds=1), "1.2.3"),
        )

    def test_empty_runner_never_starts_and_bounds_are_required(self):
        process = FakeProcess(); session, spec = self.session(process, ())
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.NOT_CONFIGURED, raised.exception.kind)
        self.assertEqual([], session._factory.specs)
        with self.assertRaises(ValueError): _CodexSessionSpec(("/r",), timedelta(), 1, timedelta(seconds=1), "1.0")
        with self.assertRaises(ValueError): _CodexSessionSpec(("/r",), timedelta(seconds=1), 1, timedelta(seconds=1), " ")
        with self.assertRaises(ValueError): _CodexSessionSpec(("/r",), timedelta(seconds=1), 1, timedelta(seconds=1), "")

    def test_sequential_allowlisted_transcript_returns_only_rate_limit_payload(self):
        process = FakeProcess((ok(1, {}), ok(2, {"rateLimits": {}})))
        session, spec = self.session(process)
        self.assertEqual({"rateLimits": {}}, session.exchange(spec))
        sent = [json.loads(item) for item in process.writes]
        self.assertEqual(["initialize", "initialized", "account/rateLimits/read"], [item["method"] for item in sent])
        self.assertEqual([1, None, 2], [item.get("id") for item in sent])
        # No ``jsonrpc`` envelope key on any outbound frame.
        for item in sent:
            self.assertNotIn("jsonrpc", item)
        # ``initialize`` carries ``clientInfo`` (name+version), no ``protocolVersion``.
        self.assertNotIn("protocolVersion", sent[0]["params"])
        self.assertEqual({"name": "limitora", "version": "1.2.3"}, sent[0]["params"]["clientInfo"])
        self.assertEqual(["close", "terminate", "wait", "streams", "join"], process.events)

    def test_bad_initialization_stops_before_notification_and_rate_limit_request(self):
        process = FakeProcess((err(1, 500),)); session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)
        self.assertEqual(1, len(process.writes))

    def test_protocol_failures_are_redacted(self):
        cases = (
            (b"bad-json\n",),
            (ok(99, {}),),  # unknown id
            (ok(1, {}), ok(2, {}), ok(2, {})),  # duplicate id
            (ok(1, {}), b""),  # EOF before second response
            (line({"id": 1, "result": {}, "future": "x"}),),  # unknown envelope key
        )
        for lines in cases:
            with self.subTest(lines=lines):
                process = FakeProcess(lines); session, spec = self.session(process)
                with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
                self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)
                self.assertNotIn("secret", raised.exception.safe_message.lower())

    def test_partial_unterminated_output_times_out_without_exceeding_cap(self):
        process = FakeProcess((b'{"id":1', None)); session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.TIMEOUT, raised.exception.kind)

    def test_cleanup_closes_streams_when_killed_process_times_out_again(self):
        process = FakeProcess((b"",), cleanup_waits=(True, True)); session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.PROCESS, raised.exception.kind)
        self.assertEqual(["close", "terminate", "wait", "kill", "wait", "streams", "join"], process.events)

    def test_safe_rpc_categories_bounds_and_cleanup_escalation(self):
        cases = (((err(1, 401),), _CodexJsonlFailureKind.UNAUTHORIZED), ((err(1, 429),), _CodexJsonlFailureKind.RATE_LIMITED), ((err(1, 503),), _CodexJsonlFailureKind.UNAVAILABLE), ((b"x" * 1025,), _CodexJsonlFailureKind.OUTPUT_LIMIT))
        for lines, kind in cases:
            with self.subTest(kind=kind):
                process = FakeProcess(lines); session, spec = self.session(process)
                with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
                self.assertEqual(kind, raised.exception.kind)
        process = FakeProcess((None,)); session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.TIMEOUT, raised.exception.kind)
        process = FakeProcess((ok(1, {}), ok(2, {})), exit_code=1); session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure) as raised: session.exchange(spec)
        self.assertEqual(_CodexJsonlFailureKind.PROCESS, raised.exception.kind)
        process = FakeProcess((b"",), cleanup_waits=(True, False)); session, spec = self.session(process)
        with self.assertRaises(_CodexJsonlFailure): session.exchange(spec)
        self.assertEqual(["close", "terminate", "wait", "kill", "wait", "streams", "join"], process.events)


if __name__ == "__main__": unittest.main()
