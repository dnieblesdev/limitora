from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from limitora.models import MetricKind, ProviderState
from limitora.providers._codex_jsonl import _CodexJsonlFailure, _CodexJsonlFailureKind
from limitora.providers.contract import AuthorizationPolicy, ProviderError, ProviderErrorKind, ProviderRequest
from limitora.providers.codex import CodexProvider


class Clock:
    def now(self): return datetime(2026, 1, 2, tzinfo=timezone.utc)


class Session:
    def __init__(self, result=None, failure=None): self.result, self.failure, self.calls = result, failure, []
    def exchange(self, spec):
        self.calls.append(spec)
        if self.failure: raise self.failure
        return self.result


def request(policy=AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE):
    return ProviderRequest(frozenset({MetricKind.COMMERCIAL_QUOTA}), policy)


def payload(primary=None, secondary=None, **extra):
    limits = {"planType": "pro"}
    if primary is not None: limits["primary"] = primary
    if secondary is not None: limits["secondary"] = secondary
    limits.update(extra)
    return {"rateLimits": limits}


def window(duration, used, reset=1_800_000_000):
    value = {"limitId": "codex", "windowDurationMins": duration, "usedPercent": used}
    if reset != "missing": value["resetsAt"] = reset
    return value


class CodexProviderTests(unittest.TestCase):
    def provider(self, result=None, failure=None, runner=("/declared/codex",)):
        session = Session(result, failure)
        return CodexProvider(runner, Clock(), session), session

    def test_detection_and_unconfigured_fetch_never_exchange(self):
        for runner in ((), ("codex",)):
            with self.subTest(runner=runner):
                provider, session = self.provider(runner=runner)
                self.assertFalse(provider.detect().detected)
                with self.assertRaises(ProviderError) as raised: provider.fetch(request())
                self.assertEqual(ProviderErrorKind.NOT_CONFIGURED, raised.exception.kind)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual([], session.calls)

    def test_policy_denial_short_circuits_before_exchange(self):
        provider, session = self.provider(payload(window(300, 5)))
        with self.assertRaises(ProviderError) as raised:
            provider.fetch(request(AuthorizationPolicy.DENY_AUTHORIZED_SOURCE))
        self.assertEqual(ProviderErrorKind.UNAUTHORIZED, raised.exception.kind)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual([], session.calls)

    def test_full_mapping_uses_percentage_points_and_utc_resets(self):
        provider, session = self.provider(payload(window(300, 25), window(10080, 60)))
        snapshot = provider.fetch(request())
        self.assertEqual(ProviderState.AVAILABLE, snapshot.status.state)
        self.assertEqual(1, len(session.calls))
        five_hour, weekly = snapshot.quota_windows
        self.assertEqual(("five_hour", 100, 25, 75, "percentage_points"),
                         (five_hour.period, five_hour.limit.value, five_hour.used.value, five_hour.remaining.value, five_hour.unit))
        self.assertEqual(("weekly", 100, 60, 40), (weekly.period, weekly.limit.value, weekly.used.value, weekly.remaining.value))
        self.assertEqual(("pro", timezone.utc), (five_hour.plan_id, five_hour.reset_at.tzinfo))

    def test_partial_mapping_keeps_missing_or_null_reset_absent(self):
        provider, _ = self.provider(payload(window(300, 1, "missing"), window(10080, 101)))
        snapshot = provider.fetch(request())
        self.assertEqual(ProviderState.PARTIAL, snapshot.status.state)
        self.assertEqual(1, len(snapshot.quota_windows))
        self.assertIsNone(snapshot.quota_windows[0].reset_at)
        provider, _ = self.provider(payload(window(300, 2, None)))
        self.assertIsNone(provider.fetch(request()).quota_windows[0].reset_at)

    def test_partial_mapping_preserves_valid_primary_when_secondary_shape_is_malformed(self):
        provider, _ = self.provider(payload(window(300, 25), secondary=[]))
        snapshot = provider.fetch(request())
        self.assertEqual(ProviderState.PARTIAL, snapshot.status.state)
        self.assertEqual(1, len(snapshot.quota_windows))
        five_hour = snapshot.quota_windows[0]
        self.assertEqual(("five_hour", 100, 25, 75, "percentage_points"),
                         (five_hour.period, five_hour.limit.value, five_hour.used.value,
                          five_hour.remaining.value, five_hour.unit))

    def test_partial_mapping_preserves_valid_secondary_when_primary_shape_is_malformed(self):
        provider, _ = self.provider(payload(primary=[], secondary=window(10080, 60)))
        snapshot = provider.fetch(request())
        self.assertEqual(ProviderState.PARTIAL, snapshot.status.state)
        self.assertEqual(1, len(snapshot.quota_windows))
        weekly = snapshot.quota_windows[0]
        self.assertEqual(("weekly", 100, 60, 40, "percentage_points"),
                         (weekly.period, weekly.limit.value, weekly.used.value,
                          weekly.remaining.value, weekly.unit))

    def test_structural_and_value_failures_are_closed_and_redacted(self):
        cases = (
            (payload(window(300, 1), planType=" unknown "), ProviderErrorKind.UNSUPPORTED),
            (payload(window(300, 1), planType="unknown"), ProviderErrorKind.UNSUPPORTED),
            (payload(window(300, 1), planType=None), ProviderErrorKind.UNSUPPORTED),
            (payload([]), ProviderErrorKind.UNSUPPORTED),
            (payload(window(300, 1), planType="pro "), ProviderErrorKind.UNSUPPORTED),
            (payload({"limitId": "other", "windowDurationMins": 300, "usedPercent": 1}), ProviderErrorKind.UNSUPPORTED),
            (payload(window(60, 1)), ProviderErrorKind.UNSUPPORTED),
            (payload(window(300, 1), window(300, 2)), ProviderErrorKind.UNSUPPORTED),
            ({"rateLimits": {"credits": {"secret": 1}}}, ProviderErrorKind.UNSUPPORTED),
            (payload(window(300, True)), ProviderErrorKind.PARSE_FAILED),
            (payload(window(300, 1.5)), ProviderErrorKind.PARSE_FAILED),
            (payload(window(300, -1)), ProviderErrorKind.PARSE_FAILED),
            (payload(window(300, 1, "bad")), ProviderErrorKind.PARSE_FAILED),
            (payload(window(300, 1, True)), ProviderErrorKind.PARSE_FAILED),
            ({"rateLimits": []}, ProviderErrorKind.UNSUPPORTED),
        )
        for data, kind in cases:
            with self.subTest(data=data):
                provider, _ = self.provider(data)
                with self.assertRaises(ProviderError) as raised: provider.fetch(request())
                self.assertEqual(kind, raised.exception.kind)
                self.assertNotIn("secret", raised.exception.safe_message.lower())

    def test_ignored_noncommercial_fields_cannot_create_quota(self):
        data = payload(window(300, 7), individualLimit={"usedPercent": 99}, credits={"amount": 99},
                       rateLimitReachedType="technical", rateLimitsByLimitId={"api": {}})
        provider, _ = self.provider(data)
        snapshot = provider.fetch(request())
        self.assertEqual(ProviderState.PARTIAL, snapshot.status.state)
        self.assertEqual(1, len(snapshot.quota_windows))

    def test_transport_categories_map_to_safe_typed_outcomes(self):
        cases = (
            (_CodexJsonlFailureKind.UNAUTHORIZED, ProviderErrorKind.UNAUTHORIZED, False),
            (_CodexJsonlFailureKind.RATE_LIMITED, ProviderErrorKind.RATE_LIMITED, True),
            (_CodexJsonlFailureKind.UNAVAILABLE, ProviderErrorKind.SOURCE_UNAVAILABLE, True),
            (_CodexJsonlFailureKind.TIMEOUT, ProviderErrorKind.TRANSPORT, True),
            (_CodexJsonlFailureKind.PROCESS, ProviderErrorKind.COMMAND_FAILED, True),
            (_CodexJsonlFailureKind.OUTPUT_LIMIT, ProviderErrorKind.COMMAND_FAILED, True),
            (_CodexJsonlFailureKind.PROTOCOL, ProviderErrorKind.PARSE_FAILED, False),
        )
        for failure, kind, retryable in cases:
            with self.subTest(failure=failure):
                provider, _ = self.provider(failure=_CodexJsonlFailure(failure))
                with self.assertRaises(ProviderError) as raised: provider.fetch(request())
                self.assertEqual((kind, retryable), (raised.exception.kind, raised.exception.retryable))
                self.assertNotIn("jsonl", raised.exception.safe_message.lower())


if __name__ == "__main__": unittest.main()
