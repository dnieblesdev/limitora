import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from limitora.models import ProviderState, ValueAvailability
from limitora.providers import ProviderError, ProviderErrorKind, ProviderRequest, AuthorizationPolicy
from limitora.models import MetricKind
from limitora.providers._opencode_go import OpenCodeGoConfig, OpenCodeGoProvider
from limitora.providers.ports import HttpResponse


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


class StubTransport:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def fetch(self):
        self.calls += 1
        return self.result
class OpenCodeGoProviderTests(unittest.TestCase):
    def request(self):
        return ProviderRequest(frozenset({MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE)

    def test_denied_authorized_source_fails_before_transport(self):
        provider = self.provider(HttpResponse(200, b'{}'))
        with self.assertRaises(ProviderError) as raised:
            provider.fetch(ProviderRequest(frozenset({MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.DENY_AUTHORIZED_SOURCE))
        self.assertEqual(ProviderErrorKind.UNAUTHORIZED, raised.exception.kind)
        self.assertEqual(0, provider._transport.calls)

    def provider(self, result):
        config = OpenCodeGoConfig("secret", timedelta(seconds=10))
        return OpenCodeGoProvider(config, StubTransport(result), clock=lambda: NOW)

    def test_maps_three_approved_windows_with_one_fetch_timestamp_and_planless_identity(self):
        body = b'{"usage":{"rolling":{"status":"ok","percent":25,"resetsAt":"2026-07-18T12:00:10Z"},"weekly":{"status":"rate-limited","percent":50,"resetsAt":"2026-07-18T12:00:20Z"},"monthly":{"status":"ok","percent":75,"resetsAt":"2026-07-18T12:00:30Z"}}}'
        snapshot = self.provider(HttpResponse(200, body)).fetch(self.request())

        self.assertEqual(ProviderState.AVAILABLE, snapshot.status.state)
        self.assertEqual(NOW, snapshot.fetched_at)
        self.assertEqual(("five_hour", "weekly", "monthly"), tuple(w.period for w in snapshot.quota_windows))
        self.assertTrue(all(w.plan_id is None for w in snapshot.quota_windows))
        self.assertEqual(
            (ValueAvailability.KNOWN, ValueAvailability.RATE_LIMITED, ValueAvailability.KNOWN),
            tuple(w.availability for w in snapshot.quota_windows),
        )
        self.assertEqual(
            (NOW + timedelta(seconds=10), None, NOW + timedelta(seconds=30)),
            tuple(w.reset_at for w in snapshot.quota_windows),
        )
        self.assertEqual(Decimal("75"), snapshot.quota_windows[0].remaining.value)

    def test_maps_rate_limited_window_to_typed_non_numeric_availability(self):
        body = b'{"usage":{"rolling":{"status":"rate-limited","percent":25,"resetsAt":"2026-07-18T12:00:10Z"}}}'

        snapshot = self.provider(HttpResponse(200, body)).fetch(self.request())
        window = snapshot.quota_windows[0]

        self.assertEqual(ValueAvailability.RATE_LIMITED, window.availability)
        self.assertIsNone(window.limit)
        self.assertIsNone(window.used)
        self.assertIsNone(window.remaining)
        self.assertIsNone(window.reset_at)
        self.assertIsNone(window.remaining_percentage)

    def test_invalid_sibling_is_partial_and_no_valid_window_is_parse_failure(self):
        body = b'{"usage":{"rolling":{"status":"ok","percent":25,"resetsAt":"2026-07-18T12:00:10Z"},"weekly":{"status":"ok","percent":101,"resetsAt":"2026-07-18T12:00:20Z"}}}'
        result = self.provider(HttpResponse(200, body)).fetch(self.request())
        self.assertEqual(ProviderState.PARTIAL, result.status.state)
        self.assertEqual(("five_hour",), tuple(w.period for w in result.quota_windows))

        invalid_rate_limited = b'{"usage":{"rolling":{"status":"rate-limited","percent":101,"resetsAt":"2026-07-18T12:00:10Z"},"weekly":{"status":"ok","percent":50,"resetsAt":"2026-07-18T12:00:20Z"}}}'
        result = self.provider(HttpResponse(200, invalid_rate_limited)).fetch(self.request())
        self.assertEqual(ProviderState.PARTIAL, result.status.state)
        self.assertEqual(("weekly",), tuple(w.period for w in result.quota_windows))

        with self.assertRaises(ProviderError) as raised:
            self.provider(HttpResponse(200, b'{"usage":{"weekly":{}}}')).fetch(self.request())
        self.assertEqual(ProviderErrorKind.PARSE_FAILED, raised.exception.kind)
        self.assertEqual("OpenCode Go response has no valid quota window", raised.exception.safe_message)

    def test_non_string_status_values_keep_nested_window_parsing_fail_closed(self):
        for status in (b"[]", b"{\"private\":\"secret\"}"):
            with self.subTest(status=status):
                body = b'{"usage":{"rolling":{"status":"ok","percent":25,"resetsAt":"2026-07-18T12:00:10Z"},"weekly":{"status":' + status + b',"percent":50,"resetsAt":"2026-07-18T12:00:20Z"}}}'
                result = self.provider(HttpResponse(200, body)).fetch(self.request())
                self.assertEqual(ProviderState.PARTIAL, result.status.state)
                self.assertEqual(("five_hour",), tuple(w.period for w in result.quota_windows))

    def test_malformed_bodies_are_safe_parse_failures(self):
        cases = (
            (b"<html><body>login</body></html>", "OpenCode Go response is not valid UTF-8 JSON"),
            (b"not-json", "OpenCode Go response is not valid UTF-8 JSON"),
            (b"\xff", "OpenCode Go response is not valid UTF-8 JSON"),
            (b"[]", "OpenCode Go response JSON root is not an object"),
        )
        for body, safe_message in cases:
            with self.subTest(body=body):
                with self.assertRaises(ProviderError) as raised:
                    self.provider(HttpResponse(200, body)).fetch(self.request())
                self.assertEqual(ProviderErrorKind.PARSE_FAILED, raised.exception.kind)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(safe_message, raised.exception.safe_message)
                self.assertNotIn("login", raised.exception.safe_message)
                self.assertNotIn("\xff", raised.exception.safe_message)

    def test_status_mapping_is_typed_and_body_is_not_exposed(self):
        for status, kind in ((301, ProviderErrorKind.UNSUPPORTED), (401, ProviderErrorKind.UNAUTHORIZED), (403, ProviderErrorKind.UNAUTHORIZED), (418, ProviderErrorKind.UNSUPPORTED), (429, ProviderErrorKind.RATE_LIMITED), (503, ProviderErrorKind.SOURCE_UNAVAILABLE)):
            with self.subTest(status=status):
                with self.assertRaises(ProviderError) as raised:
                    self.provider(HttpResponse(status, b"password=secret")).fetch(self.request())
                self.assertEqual(kind, raised.exception.kind)
                self.assertNotIn("secret", raised.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
