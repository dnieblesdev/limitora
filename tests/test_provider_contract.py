"""Offline contract tests for the provider foundation."""

from datetime import datetime, timezone
import unittest

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
    PortFailure,
    PortFailureKind,
    PortKind,
    ProviderError,
    ProviderErrorKind,
    ProviderReader,
    ProviderRequest,
    map_port_failure,
)


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
PROVIDER = ProviderId("fake-provider")
REQUEST = ProviderRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def snapshot(state: ProviderState = ProviderState.AVAILABLE) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id=PROVIDER,
        status=ProviderStatus(PROVIDER, state, NOW),
        fetched_at=NOW,
        data_at=NOW,
        source=SourceMetadata("offline-fixture"),
    )


class ProviderContractTests(unittest.TestCase):
    def test_fake_is_a_provider_reader_and_detection_uses_the_injected_clock(self) -> None:
        fake = FakeProvider(PROVIDER, FixedClock(), detected=True, outcome=snapshot())

        self.assertIsInstance(fake, ProviderReader)
        self.assertEqual(PROVIDER, fake.provider_id)
        self.assertEqual(NOW, fake.detect().checked_at)
        self.assertTrue(fake.detect().detected)
        self.assertEqual(snapshot(), fake.fetch(REQUEST))

    def test_fake_returns_a_partial_snapshot_without_inventing_numeric_evidence(self) -> None:
        partial = snapshot(ProviderState.PARTIAL)
        fake = FakeProvider(PROVIDER, FixedClock(), detected=True, outcome=partial)

        result = fake.fetch(REQUEST)

        self.assertEqual(ProviderState.PARTIAL, result.status.state)
        self.assertEqual((), result.quota_windows)
        self.assertIsNone(result.usage)

    def test_fake_reports_an_undetected_source_separately_from_fetching(self) -> None:
        unavailable = ProviderError(
            ProviderErrorKind.SOURCE_UNAVAILABLE,
            PROVIDER,
            "requested metric has no approved source",
            retryable=False,
        )
        fake = FakeProvider(
            PROVIDER,
            FixedClock(),
            detected=False,
            outcome=unavailable,
            detection_message="no approved source is configured",
        )

        detection = fake.detect()
        with self.assertRaises(ProviderError) as raised:
            fake.fetch(REQUEST)

        self.assertFalse(detection.detected)
        self.assertEqual(ProviderErrorKind.SOURCE_UNAVAILABLE, raised.exception.kind)
        self.assertFalse(raised.exception.retryable)

    def test_fake_propagates_typed_unauthorized_malformed_and_timeout_errors(self) -> None:
        cases = (
            (ProviderErrorKind.UNAUTHORIZED, False),
            (ProviderErrorKind.PARSE_FAILED, False),
            (ProviderErrorKind.TRANSPORT, True),
        )
        for kind, retryable in cases:
            with self.subTest(kind=kind):
                expected = ProviderError(kind, PROVIDER, "safe provider failure", retryable=retryable)
                fake = FakeProvider(PROVIDER, FixedClock(), detected=True, outcome=expected)

                with self.assertRaises(ProviderError) as raised:
                    fake.fetch(REQUEST)

                self.assertIs(expected, raised.exception)
                self.assertEqual(retryable, raised.exception.retryable)

    def test_port_failure_mapping_never_exposes_adapter_diagnostics(self) -> None:
        failure = PortFailure(
            PortFailureKind.TIMEOUT,
            "Authorization: Bearer token-value; Cookie: session=abc; password=secret",
        )

        error = map_port_failure(PROVIDER, PortKind.HTTP, failure)

        self.assertEqual(ProviderErrorKind.TRANSPORT, error.kind)
        self.assertTrue(error.retryable)
        self.assertEqual("HTTP request timed out", error.safe_message)
        self.assertNotIn(failure.safe_message, error.safe_message)
        for secret_marker in ("authorization", "bearer", "token", "cookie", "session", "password", "secret"):
            self.assertNotIn(secret_marker, error.safe_message.lower())

    def test_port_failure_mapping_maps_file_missing_and_invalid(self) -> None:
        cases = (
            (PortFailureKind.MISSING, ProviderErrorKind.FILE_MISSING, "configured file is missing"),
            (PortFailureKind.INVALID, ProviderErrorKind.FILE_INVALID, "configured file is invalid"),
        )
        for failure_kind, error_kind, message in cases:
            with self.subTest(failure_kind=failure_kind):
                error = map_port_failure(
                    PROVIDER,
                    PortKind.FILE,
                    PortFailure(failure_kind, "adapter diagnostic"),
                )

                self.assertEqual(error_kind, error.kind)
                self.assertEqual(message, error.safe_message)
                self.assertFalse(error.retryable)

    def test_port_failure_mapping_maps_each_command_retryability_branch(self) -> None:
        cases = (
            (PortFailureKind.TIMEOUT, True, "provider command timed out"),
            (PortFailureKind.UNAVAILABLE, True, "provider command is unavailable"),
            (PortFailureKind.MISSING, False, "provider command failed"),
            (PortFailureKind.INVALID, False, "provider command failed"),
            (PortFailureKind.FAILED, False, "provider command failed"),
        )
        for failure_kind, retryable, message in cases:
            with self.subTest(failure_kind=failure_kind):
                error = map_port_failure(
                    PROVIDER,
                    PortKind.COMMAND,
                    PortFailure(failure_kind, "adapter diagnostic"),
                )

                self.assertEqual(ProviderErrorKind.COMMAND_FAILED, error.kind)
                self.assertEqual(message, error.safe_message)
                self.assertEqual(retryable, error.retryable)


if __name__ == "__main__":
    unittest.main()
