"""Consumer-facing contract tests for the public Limitora library API."""

from datetime import datetime, timedelta, timezone
import json
import logging
import subprocess
import sys
import unittest
from unittest.mock import patch

import limitora
from limitora import (
    AuthorizationPolicy,
    CodexJsonlConfig,
    CompositionError,
    CompositionErrorKind,
    Freshness,
    FreshnessPolicy,
    InvalidProviderSelectionError,
    InvalidStatusRequestError,
    MetricKind,
    OpenCodeGoConfig,
    ProviderError,
    ProviderErrorKind,
    ProviderId,
    ProviderConfig,
    ProviderSnapshot,
    ProviderState,
    ProviderStatus,
    RateLimitResetCredit,
    RateLimitResetCreditsSummary,
    RateLimitResetCreditStatus,
    RateLimitResetType,
    SourceMetadata,
    StatusClient,
    StatusRequest,
    StatusResult,
    StatusSnapshotResult,
    StatusUndetectedResult,
    activate_provider,
)
from limitora.providers import ProviderDetection, ProviderRequest


NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
PROVIDER = ProviderId("selected-provider")
REQUEST = StatusRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
    FreshnessPolicy(timedelta(minutes=5)),
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FalseyClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def __bool__(self) -> bool:
        return False

    def now(self) -> datetime:
        return self._now


class RecordingProvider:
    def __init__(self, *, detected: bool, outcome: ProviderSnapshot | ProviderError) -> None:
        self.detected = detected
        self.outcome = outcome
        self.detect_calls = 0
        self.fetch_calls: list[ProviderRequest] = []

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER

    def detect(self) -> ProviderDetection:
        self.detect_calls += 1
        return ProviderDetection(PROVIDER, self.detected, NOW, "no approved source" if not self.detected else None)

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot:
        self.fetch_calls.append(request)
        if isinstance(self.outcome, ProviderError):
            raise self.outcome
        return self.outcome


def snapshot(*, fetched_at: datetime = NOW) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id=PROVIDER,
        status=ProviderStatus(PROVIDER, ProviderState.AVAILABLE, fetched_at),
        fetched_at=fetched_at,
        data_at=fetched_at,
        source=SourceMetadata("offline-fixture"),
    )


class PublicLibraryApiTests(unittest.TestCase):
    def test_root_exports_only_the_documented_public_contract(self) -> None:
        expected_exports = {
            "AuthorizationPolicy",
            "Clock",
            "CodexJsonlConfig",
            "CompositionError",
            "CompositionErrorKind",
            "CurrentClock",
            "Freshness",
            "FreshnessPolicy",
            "InvalidProviderSelectionError",
            "InvalidStatusRequestError",
            "MetricKind",
            "OpenCodeGoConfig",
            "ProviderError",
            "ProviderErrorKind",
            "ProviderId",
            "ProviderConfig",
            "ProviderSnapshot",
            "ProviderState",
            "ProviderStatus",
            "RateLimitResetCredit",
            "RateLimitResetCreditsSummary",
            "RateLimitResetCreditStatus",
            "RateLimitResetType",
            "SourceMetadata",
            "StatusClient",
            "StatusProvider",
            "StatusRequest",
            "StatusResult",
            "StatusSnapshotResult",
            "StatusUndetectedResult",
            "activate_provider",
        }

        self.assertEqual(expected_exports, set(limitora.__all__))
        for symbol in expected_exports:
            self.assertIsNotNone(getattr(limitora, symbol))
        for unsupported in ("ProviderReader", "StatusService", "cli", "json", "output"):
            self.assertNotIn(unsupported, limitora.__all__)

    def test_root_import_does_not_eagerly_load_provider_implementations(self) -> None:
        script = """
import json
import sys
import limitora
names = (
    "httpx",
    "subprocess",
    "limitora.providers.codex",
    "limitora.providers._codex_jsonl",
    "limitora.providers._opencode_go",
    "limitora.providers._opencode_go_httpx",
)
print(json.dumps({name: name in sys.modules for name in names}, sort_keys=True))
"""

        completed = subprocess.run(
            [sys.executable, "-c", script], check=True, capture_output=True, text=True
        )

        self.assertEqual({name: False for name in (
            "httpx",
            "subprocess",
            "limitora.providers.codex",
            "limitora.providers._codex_jsonl",
            "limitora.providers._opencode_go",
            "limitora.providers._opencode_go_httpx",
        )}, json.loads(completed.stdout))

    def test_consumers_construct_and_reuse_one_client_per_provider_from_root(self) -> None:
        configs: tuple[ProviderConfig, ...] = (
            CodexJsonlConfig(("/declared/codex",)),
            OpenCodeGoConfig("workspace", "opaque-cookie"),
        )

        for config in configs:
            with self.subTest(provider=config.provider):
                client = activate_provider(config, clock=FixedClock())
                self.assertIsInstance(client, StatusClient)
                with patch.object(client, "read_status", return_value=StatusUndetectedResult()) as read:
                    self.assertIsInstance(client.read_status(REQUEST), StatusUndetectedResult)
                    self.assertIsInstance(client.read_status(REQUEST), StatusUndetectedResult)
                self.assertEqual(2, read.call_count)

    def test_root_construction_preserves_safe_validation_errors(self) -> None:
        secret = "public-construction-secret"
        config = OpenCodeGoConfig(secret, secret, endpoint="https://invalid.example")

        with self.assertLogs("limitora.consumer", level="INFO") as captured:
            logging.getLogger("limitora.consumer").info("config=%r", config)

        with self.assertRaises(CompositionError) as raised:
            activate_provider(config)

        self.assertEqual(CompositionErrorKind.INVALID, raised.exception.kind)
        self.assertNotIn(secret, repr(config))
        self.assertNotIn(secret, "".join(captured.output))
        self.assertNotIn(secret, repr(raised.exception))

    def test_current_clock_is_timezone_aware_and_default_client_is_usable(self) -> None:
        current_time = limitora.CurrentClock().now()
        selected = RecordingProvider(detected=False, outcome=snapshot())

        result = StatusClient(selected).read_status(REQUEST)

        self.assertIsNotNone(current_time.tzinfo)
        self.assertIsNotNone(current_time.utcoffset())
        self.assertIsInstance(result, StatusUndetectedResult)

    def test_invalid_provider_selections_fail_before_provider_operations(self) -> None:
        valid = RecordingProvider(detected=True, outcome=snapshot())

        for invalid in (None, object()):
            with self.subTest(invalid=invalid):
                with self.assertRaises(InvalidProviderSelectionError):
                    StatusClient(invalid)  # type: ignore[arg-type]

        self.assertEqual(0, valid.detect_calls)
        self.assertEqual([], valid.fetch_calls)

    def test_invalid_freshness_policy_raises_the_documented_request_error(self) -> None:
        selected = RecordingProvider(detected=True, outcome=snapshot())
        invalid_request = StatusRequest(
            REQUEST.requested_metrics,
            REQUEST.authorization_policy,
            "five minutes",  # type: ignore[arg-type]
        )
        with self.assertRaises(InvalidStatusRequestError):
            StatusClient(selected).read_status(invalid_request)

    def test_invalid_freshness_policy_stops_before_provider_io(self) -> None:
        selected = RecordingProvider(detected=True, outcome=snapshot())
        invalid_request = StatusRequest(
            REQUEST.requested_metrics,
            REQUEST.authorization_policy,
            object(),  # type: ignore[arg-type]
        )
        with self.assertRaises(InvalidStatusRequestError):
            StatusClient(selected).read_status(invalid_request)

        self.assertEqual(0, selected.detect_calls)
        self.assertEqual([], selected.fetch_calls)

    def test_falsey_injected_clock_controls_freshness(self) -> None:
        selected = RecordingProvider(detected=True, outcome=snapshot())

        with patch("limitora.api.CurrentClock", return_value=FixedClock()):
            result = StatusClient(selected, FalseyClock(NOW + timedelta(minutes=6))).read_status(REQUEST)

        self.assertIsInstance(result, StatusSnapshotResult)
        self.assertEqual(Freshness.STALE, result.freshness)

    def test_falsey_injected_clock_can_keep_a_snapshot_fresh(self) -> None:
        selected = RecordingProvider(detected=True, outcome=snapshot())

        with patch("limitora.api.CurrentClock", return_value=FalseyClock(NOW + timedelta(minutes=6))):
            result = StatusClient(selected, FalseyClock(NOW)).read_status(REQUEST)

        self.assertIsInstance(result, StatusSnapshotResult)
        self.assertEqual(Freshness.FRESH, result.freshness)

    def test_none_clock_selects_the_current_clock_default(self) -> None:
        selected = RecordingProvider(detected=True, outcome=snapshot())

        with patch("limitora.api.CurrentClock", return_value=FixedClock()) as current_clock:
            result = StatusClient(selected, None).read_status(REQUEST)

        self.assertIsInstance(result, StatusSnapshotResult)
        self.assertEqual(Freshness.FRESH, result.freshness)
        current_clock.assert_called_once_with()

    def test_selected_provider_receives_the_converted_immutable_request(self) -> None:
        selected = RecordingProvider(detected=True, outcome=snapshot())

        StatusClient(selected, FixedClock()).read_status(REQUEST)

        self.assertEqual(1, selected.detect_calls)
        self.assertEqual(
            [ProviderRequest(REQUEST.requested_metrics, REQUEST.authorization_policy)],
            selected.fetch_calls,
        )
        with self.assertRaises(ValueError):
            StatusRequest(frozenset(), AuthorizationPolicy.DENY_AUTHORIZED_SOURCE, REQUEST.freshness_policy)

    def test_freshness_policy_rejects_negative_age_and_keeps_boundary_fresh(self) -> None:
        with self.assertRaises(ValueError):
            FreshnessPolicy(timedelta(seconds=-1))
        selected = RecordingProvider(detected=True, outcome=snapshot(fetched_at=NOW - timedelta(minutes=5)))

        result = StatusClient(selected, FixedClock()).read_status(REQUEST)

        self.assertIsInstance(result, StatusSnapshotResult)
        self.assertEqual(Freshness.FRESH, result.freshness)

    def test_stale_result_preserves_the_provider_snapshot_identity(self) -> None:
        expected = snapshot(fetched_at=NOW - timedelta(minutes=6))
        selected = RecordingProvider(detected=True, outcome=expected)

        result = StatusClient(selected, FixedClock()).read_status(REQUEST)

        self.assertIsInstance(result, StatusSnapshotResult)
        self.assertEqual(Freshness.STALE, result.freshness)
        self.assertIs(expected, result.snapshot)

    def test_undetected_provider_returns_a_tagged_result_without_fetching(self) -> None:
        selected = RecordingProvider(detected=False, outcome=snapshot())

        result = StatusClient(selected, FixedClock()).read_status(REQUEST)

        self.assertIsInstance(result, StatusUndetectedResult)
        self.assertEqual(1, selected.detect_calls)
        self.assertEqual([], selected.fetch_calls)

    def test_provider_error_propagates_with_its_original_identity(self) -> None:
        expected = ProviderError(
            ProviderErrorKind.UNAUTHORIZED,
            PROVIDER,
            "provider authorization is required",
            retryable=False,
        )
        selected = RecordingProvider(detected=True, outcome=expected)

        with self.assertRaises(ProviderError) as raised:
            StatusClient(selected, FixedClock()).read_status(REQUEST)

        self.assertIs(expected, raised.exception)


if __name__ == "__main__":
    unittest.main()
