import threading
import unittest
from datetime import datetime, timedelta, timezone

from limitora.models import MetricKind, ProviderId, ProviderSnapshot, ProviderState, ProviderStatus, SourceMetadata
from limitora.api import Freshness, FreshnessPolicy, StatusClient, StatusRequest, StatusUndetectedResult
from limitora.providers import AuthorizationPolicy, ProviderDetection, ProviderError, ProviderErrorKind, ProviderRequest
from limitora.providers.cache import CachedProviderReader, ProviderCachePolicy


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
REQUEST = ProviderRequest(frozenset({MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE)


class Clock:
    def __init__(self): self.value = NOW
    def now(self): return self.value


def snapshot(provider=ProviderId("provider"), at=NOW):
    return ProviderSnapshot(provider, ProviderStatus(provider, ProviderState.AVAILABLE, at), at, at, SourceMetadata("test"))


class Reader:
    def __init__(self, outcomes, provider=ProviderId("provider")):
        self.provider_id, self.outcomes, self.calls, self.detects, self.detected = provider, list(outcomes), 0, 0, True
        self.started, self.release = None, None
    def detect(self):
        self.detects += 1
        return ProviderDetection(self.provider_id, self.detected, NOW)
    def fetch(self, request):
        self.calls += 1
        if self.started:
            self.started.set(); self.release.wait(1)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException): raise outcome
        return outcome


def error():
    return ProviderError(ProviderErrorKind.TRANSPORT, ProviderId("provider"), "unavailable", retryable=True)


class ProviderCacheTests(unittest.TestCase):
    def test_policy_validation_reuse_identity_and_key_isolation(self):
        with self.assertRaises(ValueError): ProviderCachePolicy(timedelta(-1), timedelta())
        with self.assertRaises(ValueError): ProviderCachePolicy(timedelta(2), timedelta(1))
        clock, first, second = Clock(), snapshot(), snapshot(at=NOW + timedelta(seconds=1))
        reader = Reader([first, second, snapshot()])
        cached = CachedProviderReader(reader, ProviderCachePolicy(timedelta(minutes=1), timedelta(minutes=2)), clock)
        self.assertIs(first, cached.fetch(REQUEST)); self.assertIs(first, cached.fetch(REQUEST))
        self.assertIs(second, cached.fetch(ProviderRequest(REQUEST.requested_metrics, AuthorizationPolicy.DENY_AUTHORIZED_SOURCE)))
        self.assertEqual(2, reader.calls)

    def test_different_requested_metric_sets_do_not_reuse_cached_snapshots(self):
        clock, first, second = Clock(), snapshot(), snapshot(at=NOW + timedelta(seconds=1))
        reader = Reader([first, second])
        cached = CachedProviderReader(reader, ProviderCachePolicy(timedelta(minutes=1), timedelta(minutes=2)), clock)
        other_metrics = ProviderRequest(frozenset({MetricKind.TOKENS}), REQUEST.authorization_policy)

        self.assertIs(first, cached.fetch(REQUEST))
        self.assertIs(second, cached.fetch(other_metrics))
        self.assertEqual(2, reader.calls)

    def test_status_client_evaluates_reused_snapshot_with_each_freshness_policy(self):
        clock, cached_snapshot = Clock(), snapshot(at=NOW - timedelta(minutes=2))
        reader = Reader([cached_snapshot])
        cached = CachedProviderReader(reader, ProviderCachePolicy(timedelta(minutes=3), timedelta(minutes=4)), clock)
        client = StatusClient(cached, clock)
        strict = StatusRequest(REQUEST.requested_metrics, REQUEST.authorization_policy, FreshnessPolicy(timedelta(minutes=1)))
        relaxed = StatusRequest(REQUEST.requested_metrics, REQUEST.authorization_policy, FreshnessPolicy(timedelta(minutes=3)))

        strict_result = client.read_status(strict)
        relaxed_result = client.read_status(relaxed)

        self.assertIs(cached_snapshot, strict_result.snapshot)
        self.assertIs(cached_snapshot, relaxed_result.snapshot)
        self.assertEqual(Freshness.STALE, strict_result.freshness)
        self.assertEqual(Freshness.FRESH, relaxed_result.freshness)
        self.assertEqual(1, reader.calls)

    def test_detection_passthrough_and_ttl_stale_failure_boundaries(self):
        clock, first, refreshed = Clock(), snapshot(), snapshot(at=NOW + timedelta(seconds=1))
        reader = Reader([first, refreshed, error(), error(), error()])
        cached = CachedProviderReader(reader, ProviderCachePolicy(timedelta(seconds=1), timedelta(seconds=3)), clock)
        self.assertTrue(cached.detect().detected); self.assertIs(first, cached.fetch(REQUEST)); self.assertIs(first, cached.fetch(REQUEST))
        clock.value += timedelta(seconds=1); self.assertIs(refreshed, cached.fetch(REQUEST))
        clock.value += timedelta(seconds=2); self.assertIs(refreshed, cached.fetch(REQUEST))
        clock.value += timedelta(seconds=1); self.assertIs(refreshed, cached.fetch(REQUEST))
        clock.value += timedelta(seconds=1)
        with self.assertRaises(ProviderError): cached.fetch(REQUEST)
        self.assertEqual(1, reader.detects); self.assertEqual(5, reader.calls)

    def test_negative_and_untyped_failures_never_use_stale_entries(self):
        clock, first = Clock(), snapshot()
        reader = Reader([first, RuntimeError("unexpected")])
        cached = CachedProviderReader(reader, ProviderCachePolicy(timedelta(), timedelta(minutes=1)), clock)
        self.assertIs(first, cached.fetch(REQUEST)); clock.value -= timedelta(seconds=1)
        with self.assertRaises(RuntimeError): cached.fetch(REQUEST)

    def test_undetected_status_read_never_fetches(self):
        reader = Reader([snapshot()]); reader.detected = False
        client = StatusClient(CachedProviderReader(reader, ProviderCachePolicy(timedelta(), timedelta()), Clock()), Clock())
        result = client.read_status(StatusRequest(REQUEST.requested_metrics, REQUEST.authorization_policy, FreshnessPolicy(timedelta())))
        self.assertIsInstance(result, StatusUndetectedResult); self.assertEqual(0, reader.calls)

    def test_delayed_provider_failure_rechecks_clock_before_stale_fallback(self):
        clock, first, expected = Clock(), snapshot(), error()

        class DelayedFailureReader(Reader):
            def fetch(self, request):
                self.calls += 1
                outcome = self.outcomes.pop(0)
                if isinstance(outcome, ProviderError):
                    clock.value += timedelta(seconds=2)
                    raise outcome
                return outcome

        cached = CachedProviderReader(DelayedFailureReader([first, expected]), ProviderCachePolicy(timedelta(seconds=1), timedelta(seconds=3)), clock)
        self.assertIs(first, cached.fetch(REQUEST)); clock.value += timedelta(seconds=2)
        with self.assertRaises(ProviderError) as raised: cached.fetch(REQUEST)
        self.assertIs(expected, raised.exception)

    def test_status_client_detects_again_when_fetch_uses_cache_hit(self):
        clock, expected = Clock(), snapshot()
        reader = Reader([expected])
        client = StatusClient(CachedProviderReader(reader, ProviderCachePolicy(timedelta(minutes=1), timedelta(minutes=2)), clock), clock)
        request = StatusRequest(REQUEST.requested_metrics, REQUEST.authorization_policy, FreshnessPolicy(timedelta(minutes=1)))

        first, second = client.read_status(request), client.read_status(request)

        self.assertIs(expected, first.snapshot); self.assertIs(expected, second.snapshot)
        self.assertEqual(2, reader.detects); self.assertEqual(1, reader.calls)

    def test_independent_cached_readers_isolate_provider_entries(self):
        clock, provider_a, provider_b = Clock(), ProviderId("provider-a"), ProviderId("provider-b")
        first_a, first_b = snapshot(provider_a), snapshot(provider_b)
        reader_a, reader_b = Reader([first_a], provider_a), Reader([first_b], provider_b)
        cached_a = CachedProviderReader(reader_a, ProviderCachePolicy(timedelta(minutes=1), timedelta(minutes=2)), clock)
        cached_b = CachedProviderReader(reader_b, ProviderCachePolicy(timedelta(minutes=1), timedelta(minutes=2)), clock)

        self.assertIs(first_a, cached_a.fetch(REQUEST)); self.assertIs(first_b, cached_b.fetch(REQUEST))
        self.assertIs(first_a, cached_a.fetch(REQUEST)); self.assertIs(first_b, cached_b.fetch(REQUEST))
        self.assertEqual(1, reader_a.calls); self.assertEqual(1, reader_b.calls)

    def test_invalidation_and_generation_reject_inflight_results(self):
        for targeted, failing in ((True, False), (False, False), (True, True), (False, True)):
            with self.subTest(targeted=targeted, failing=failing):
                clock, old, fresh = Clock(), snapshot(), snapshot(at=NOW + timedelta(seconds=1))
                outcomes = [old, error(), fresh] if failing else [old, fresh]
                reader = Reader(outcomes)
                cached = CachedProviderReader(reader, ProviderCachePolicy(timedelta(), timedelta(minutes=2)), clock)
                if failing: cached.fetch(REQUEST)
                reader.started, reader.release = threading.Event(), threading.Event()
                thread = threading.Thread(target=lambda: self._ignore_failure(cached))
                thread.start(); self.assertTrue(reader.started.wait(1))
                cached.invalidate(REQUEST if targeted else None); reader.release.set(); thread.join(1)
                self.assertFalse(thread.is_alive()); self.assertIs(fresh, cached.fetch(REQUEST)); self.assertEqual(3 if failing else 2, reader.calls)

    @staticmethod
    def _ignore_failure(cached):
        try: cached.fetch(REQUEST)
        except ProviderError: pass


if __name__ == "__main__": unittest.main()
