"""Pure protocol codec tests for the Codex JSONL transport.

These tests describe the contract of the new
``_codex_jsonl_protocol`` module: a pure layer that owns JSON-RPC
envelope build and parse with zero I/O. They are the RED step for
Phase 1.1 of the codex-handshake-fix change.
"""
from __future__ import annotations

import json
import unittest

from limitora.providers._codex_jsonl_protocol import (
    _CodexJsonlFailure,
    _CodexJsonlFailureKind,
    _ParsedFrame,
    build_notification,
    build_request,
    parse_frame,
)


class ProtocolFailureTests(unittest.TestCase):
    """Failure vocabulary lives in the protocol module."""

    def test_failure_kind_values_are_stable(self):
        kinds = tuple(member.value for member in _CodexJsonlFailureKind)
        self.assertEqual(
            (
                "not_configured",
                "timeout",
                "output_limit",
                "protocol",
                "unauthorized",
                "rate_limited",
                "unavailable",
                "process",
            ),
            kinds,
        )

    def test_failure_safe_message_contains_kind_text_without_kind_object(self):
        failure = _CodexJsonlFailure(_CodexJsonlFailureKind.PROTOCOL)
        self.assertEqual("Codex JSONL transport protocol", failure.safe_message)
        self.assertEqual("Codex JSONL transport protocol", str(failure))


class BuildRequestTests(unittest.TestCase):
    """Outbound request frames must omit the JSON-RPC envelope key."""

    def test_build_request_round_trip(self):
        wire = build_request("account/rateLimits/read", 7, {"plan": "pro"})
        payload = json.loads(wire.decode("utf-8"))
        self.assertEqual({"id": 7, "method": "account/rateLimits/read", "params": {"plan": "pro"}}, payload)
        self.assertNotIn("jsonrpc", payload)

    def test_build_request_never_contains_jsonrpc_key(self):
        for ident in (0, 1, 999, -1):
            wire = build_request("initialize", ident, {})
            self.assertNotIn(b"jsonrpc", wire, f"ident={ident} leaked jsonrpc key")

    def test_build_request_terminates_with_newline(self):
        wire = build_request("initialize", 1, {})
        self.assertTrue(wire.endswith(b"\n"), "frame must be newline-delimited for JSONL")


class BuildNotificationTests(unittest.TestCase):
    """Outbound notifications carry no id and no jsonrpc key."""

    def test_build_notification_round_trip(self):
        wire = build_notification("initialized", {})
        payload = json.loads(wire.decode("utf-8"))
        self.assertEqual({"method": "initialized", "params": {}}, payload)
        self.assertNotIn("id", payload)
        self.assertNotIn("jsonrpc", payload)

    def test_build_notification_omits_id_and_jsonrpc_bytes(self):
        wire = build_notification("initialized", {"k": 1})
        self.assertNotIn(b'"id"', wire)
        self.assertNotIn(b"jsonrpc", wire)


class ParseFrameTests(unittest.TestCase):
    """``parse_frame`` must distinguish notifications from correlated responses."""

    def test_parse_frame_response_yields_parsed_frame_with_ident(self):
        frame = parse_frame(b'{"id":3,"result":{"rateLimits":{}}}\n')
        self.assertEqual(_ParsedFrame(ident=3, result={"rateLimits": {}}, error=None), frame)

    def test_parse_frame_notification_with_method_yields_ident_none(self):
        frame = parse_frame(b'{"method":"serverNotification","params":{}}\n')
        self.assertEqual(_ParsedFrame(ident=None, result=None, error=None), frame)

    def test_parse_frame_notification_with_jsonrpc_key_yields_ident_none(self):
        # Codex app-server may emit standard JSON-RPC 2.0 frames; parse_frame
        # must accept the ``jsonrpc`` envelope key on inbound traffic.
        frame = parse_frame(b'{"jsonrpc":"2.0","method":"serverNotification","params":{}}\n')
        self.assertEqual(_ParsedFrame(ident=None, result=None, error=None), frame)

    def test_parse_frame_error_response_yields_error_dict(self):
        frame = parse_frame(b'{"id":1,"error":{"code":401,"message":"token=secret"}}\n')
        self.assertEqual(_ParsedFrame(ident=1, result=None, error={"code": 401, "message": "token=secret"}), frame)

    def test_parse_frame_malformed_json_raises_protocol(self):
        with self.assertRaises(_CodexJsonlFailure) as raised:
            parse_frame(b"not-json\n")
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)
        self.assertNotIn("not-json", raised.exception.safe_message)

    def test_parse_frame_non_object_envelope_raises_protocol(self):
        for wire in (b"[1,2,3]\n", b'"a-string"\n', b"42\n", b"null\n"):
            with self.subTest(wire=wire):
                with self.assertRaises(_CodexJsonlFailure) as raised:
                    parse_frame(wire)
                self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_parse_frame_envelope_with_both_result_and_error_raises_protocol(self):
        wire = b'{"id":1,"result":{},"error":{"code":2}}\n'
        with self.assertRaises(_CodexJsonlFailure) as raised:
            parse_frame(wire)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_parse_frame_envelope_with_unknown_keys_raises_protocol(self):
        wire = b'{"id":1,"result":{},"extra":"nope"}\n'
        with self.assertRaises(_CodexJsonlFailure) as raised:
            parse_frame(wire)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_parse_frame_response_without_result_or_error_raises_protocol(self):
        wire = b'{"id":1,"method":"x","params":{}}\n'  # looks like a request, not a response
        with self.assertRaises(_CodexJsonlFailure) as raised:
            parse_frame(wire)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_parse_frame_non_int_id_raises_protocol(self):
        wire = b'{"id":"1","result":{}}\n'
        with self.assertRaises(_CodexJsonlFailure) as raised:
            parse_frame(wire)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_parse_frame_non_dict_result_raises_protocol(self):
        wire = b'{"id":1,"result":"scalar"}\n'
        with self.assertRaises(_CodexJsonlFailure) as raised:
            parse_frame(wire)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)

    def test_parse_frame_non_dict_error_raises_protocol(self):
        wire = b'{"id":1,"error":"scalar"}\n'
        with self.assertRaises(_CodexJsonlFailure) as raised:
            parse_frame(wire)
        self.assertEqual(_CodexJsonlFailureKind.PROTOCOL, raised.exception.kind)


if __name__ == "__main__":
    unittest.main()
