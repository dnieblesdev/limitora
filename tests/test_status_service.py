"""Offline tests for application-level provider coordination."""

from datetime import datetime, timezone
import unittest

from limitora.core import StatusService
from limitora.models import (
    MetricKind,
    ProviderId,
    ProviderSnapshot,
    ProviderState,
    ProviderStatus,
    SourceMetadata,
)
from limitora.providers import (
    AuthorizationPolicy,
    FakeProvider,
    ProviderDetection,
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
)


NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
PROVIDER = ProviderId("fake-provider")
REQUEST = ProviderRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class UndetectedProvider:
    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER

    def detect(self) -> ProviderDetection:
        return ProviderDetection(PROVIDER, False, NOW, "no approved source is configured")

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot:
        del request
        raise AssertionError("undetected providers must not be read")


def snapshot() -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id=PROVIDER,
        status=ProviderStatus(PROVIDER, ProviderState.AVAILABLE, NOW),
        fetched_at=NOW,
        data_at=NOW,
        source=SourceMetadata("offline-fixture"),
    )


class StatusServiceTests(unittest.TestCase):
    def test_read_status_returns_undetected_result_without_fetching(self) -> None:
        result = StatusService(UndetectedProvider()).read_status(REQUEST)

        self.assertIsInstance(result, ProviderDetection)
        self.assertFalse(result.detected)
        self.assertEqual("no approved source is configured", result.safe_message)

    def test_read_status_preserves_the_detected_provider_snapshot(self) -> None:
        expected = snapshot()
        service = StatusService(FakeProvider(PROVIDER, FixedClock(), detected=True, outcome=expected))

        result = service.read_status(REQUEST)

        self.assertIs(expected, result)

    def test_read_status_propagates_the_original_typed_provider_error(self) -> None:
        expected = ProviderError(
            ProviderErrorKind.UNAUTHORIZED,
            PROVIDER,
            "provider authorization is required",
            retryable=False,
        )
        service = StatusService(FakeProvider(PROVIDER, FixedClock(), detected=True, outcome=expected))

        with self.assertRaises(ProviderError) as raised:
            service.read_status(REQUEST)

        self.assertIs(expected, raised.exception)
        self.assertEqual(ProviderErrorKind.UNAUTHORIZED, raised.exception.kind)


if __name__ == "__main__":
    unittest.main()
