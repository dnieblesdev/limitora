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
        config = OpenCodeGoConfig("workspace", "secret", "https://opencode.ai", timedelta(seconds=10))
        return OpenCodeGoProvider(config, StubTransport(result), clock=lambda: NOW)

    def test_maps_three_approved_windows_with_one_fetch_timestamp_and_planless_identity(self):
        body = b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},"weeklyUsage":{"usagePercent":50,"resetInSec":20},"monthlyUsage":{"usagePercent":75,"resetInSec":30},"subscriptionPlan":null}'
        snapshot = self.provider(HttpResponse(200, body)).fetch(self.request())

        self.assertEqual(ProviderState.AVAILABLE, snapshot.status.state)
        self.assertEqual(NOW, snapshot.fetched_at)
        self.assertEqual(("five_hour", "weekly", "monthly"), tuple(w.period for w in snapshot.quota_windows))
        self.assertTrue(all(w.plan_id is None and w.reset_at > NOW for w in snapshot.quota_windows))
        self.assertEqual(Decimal("75"), snapshot.quota_windows[0].remaining.value)

    def test_invalid_sibling_is_partial_and_no_valid_window_is_parse_failure(self):
        body = b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},"weeklyUsage":{"usagePercent":101,"resetInSec":20}}'
        result = self.provider(HttpResponse(200, body)).fetch(self.request())
        self.assertEqual(ProviderState.PARTIAL, result.status.state)
        self.assertEqual(("five_hour",), tuple(w.period for w in result.quota_windows))

        with self.assertRaises(ProviderError) as raised:
            self.provider(HttpResponse(200, b'{"weeklyUsage":{}}')).fetch(self.request())
        self.assertEqual(ProviderErrorKind.PARSE_FAILED, raised.exception.kind)

    def test_status_mapping_is_typed_and_body_is_not_exposed(self):
        for status, kind in ((301, ProviderErrorKind.UNSUPPORTED), (401, ProviderErrorKind.UNAUTHORIZED), (403, ProviderErrorKind.UNAUTHORIZED), (418, ProviderErrorKind.UNSUPPORTED), (429, ProviderErrorKind.RATE_LIMITED), (503, ProviderErrorKind.SOURCE_UNAVAILABLE)):
            with self.subTest(status=status):
                with self.assertRaises(ProviderError) as raised:
                    self.provider(HttpResponse(status, b"password=secret")).fetch(self.request())
                self.assertEqual(kind, raised.exception.kind)
                self.assertNotIn("secret", raised.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
