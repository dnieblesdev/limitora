import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from limitora import (
    AuthorizationPolicy,
    Freshness,
    FreshnessPolicy,
    MetricKind,
    StatusSnapshotResult,
    StatusRequest,
)
from limitora.composition import (
    CodexJsonlConfig,
    CodexJsonlDependencies,
    CompositionError,
    CompositionErrorKind,
    OpenCodeGoConfig,
    OpenCodeGoDependencies,
    build_status_client,
)
from limitora.providers.cache import ProviderCachePolicy
from limitora.providers.ports import HttpResponse
NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
REQUEST = StatusRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE,
    FreshnessPolicy(timedelta(minutes=5)),
)
class FixedClock:
    def now(self):
        return NOW
class Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def exchange(self, spec):
        self.calls += 1
        return self.payload
class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return self.response
def codex_payload():
    return {
        "rateLimits": {
            "planType": "pro",
            "primary": {"limitId": "codex", "windowDurationMins": 300, "usedPercent": 25},
            "secondary": {"limitId": "codex", "windowDurationMins": 10080, "usedPercent": 50},
        }
    }
def opencode_response():
    return HttpResponse(200, b'{"rollingUsage":{"usagePercent":25,"resetInSec":10}}')
class ProviderCompositionTests(unittest.TestCase):
    def test_cache_option_is_default_off_validated_before_factories_and_wraps_both_providers(self):
        clock, calls = FixedClock(), []
        deps = CodexJsonlDependencies(clock, lambda: calls.append(True) or Session(codex_payload()))
        with self.assertRaises(CompositionError): build_status_client(CodexJsonlConfig(("/declared/codex",)), deps, cache_policy=object())
        self.assertEqual([], calls)
        policy = ProviderCachePolicy(timedelta(minutes=1), timedelta(minutes=2))
        client = build_status_client(CodexJsonlConfig(("/declared/codex",)), deps, cache_policy=policy)
        client.read_status(REQUEST); client.read_status(REQUEST)
        self.assertEqual(1, len(calls)); self.assertEqual(1, client._service._provider._reader._session.calls)
        uncached_session = Session(codex_payload())
        uncached = build_status_client(CodexJsonlConfig(("/declared/codex",)), CodexJsonlDependencies(clock, lambda: uncached_session))
        uncached.read_status(REQUEST); uncached.read_status(REQUEST)
        self.assertEqual(2, uncached_session.calls)
        transport = Transport(opencode_response())
        opencode = build_status_client(OpenCodeGoConfig("workspace", "cookie"), OpenCodeGoDependencies(clock, lambda config: transport), cache_policy=policy)
        opencode.read_status(REQUEST); opencode.read_status(REQUEST)
        self.assertEqual(1, transport.calls)
    def test_configs_are_frozen_and_discriminator_is_closed(self):
        codex = CodexJsonlConfig(("/declared/codex",))
        opencode = OpenCodeGoConfig("workspace", "opaque-cookie")

        self.assertEqual("codex", codex.provider)
        self.assertEqual("opencode-go", opencode.provider)
        with self.assertRaises(FrozenInstanceError):
            codex.runner = ("/other",)
        with self.assertRaises(FrozenInstanceError):
            opencode.workspace_id = "other"
    def test_rejects_disabled_missing_unknown_mutable_and_third_provider(self):
        clock = FixedClock()
        codex_calls, opencode_calls = [], []
        class ThirdCodex(CodexJsonlConfig): pass
        class ThirdOpenCode(OpenCodeGoConfig): pass
        deps = CodexJsonlDependencies(clock, lambda: Session(codex_payload()))
        codex_deps = CodexJsonlDependencies(clock, lambda: codex_calls.append(True) or Session(codex_payload()))
        opencode_deps = OpenCodeGoDependencies(clock, lambda config: opencode_calls.append(config) or Transport(opencode_response()))
        cases = (
            (None, deps, False, CompositionErrorKind.DISABLED),
            (None, deps, True, CompositionErrorKind.MISSING),
            (object(), deps, True, CompositionErrorKind.INVALID),
            ({"provider": "codex", "runner": ("/declared/codex",)}, deps, True, CompositionErrorKind.INVALID),
            (OpenCodeGoConfig("workspace", "cookie"), deps, True, CompositionErrorKind.DEPENDENCY_MISMATCH),
            (ThirdCodex(("/declared/codex",)), codex_deps, True, CompositionErrorKind.INVALID),
            (ThirdOpenCode("workspace", "cookie"), opencode_deps, True, CompositionErrorKind.INVALID),
        )
        for config, dependencies, enabled, kind in cases:
            with self.subTest(kind=kind):
                with self.assertRaises(CompositionError) as raised:
                    build_status_client(config, dependencies, enabled=enabled)
                self.assertEqual(kind, raised.exception.kind)
                self.assertEqual(raised.exception.safe_message, str(raised.exception))
        self.assertEqual([], codex_calls)
        self.assertEqual([], opencode_calls)
    def test_rejects_invalid_values_before_factories_or_io(self):
        clock = FixedClock()
        session_calls = []
        transport_calls = []

        def session_factory():
            session_calls.append(True)
            return Session(codex_payload())

        def transport_factory(config):
            transport_calls.append(config)
            return Transport(opencode_response())

        invalid_configs = (
            CodexJsonlConfig(()),
            CodexJsonlConfig(("codex",)),
            CodexJsonlConfig((" /declared/codex",)),
            CodexJsonlConfig(("/declared/codex", "")),
            OpenCodeGoConfig(" ", "cookie"),
            OpenCodeGoConfig("workspace", ""),
            OpenCodeGoConfig("workspace", "cookie", endpoint="http://opencode.ai"),
            OpenCodeGoConfig("workspace", "cookie", timeout=timedelta(0)),
            OpenCodeGoConfig("workspace", "cookie", timeout=timedelta(seconds=11)),
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                deps = (CodexJsonlDependencies(clock, session_factory)
                        if isinstance(config, CodexJsonlConfig)
                        else OpenCodeGoDependencies(clock, transport_factory))
                with self.assertRaises(CompositionError) as raised:
                    build_status_client(config, deps)
                self.assertEqual(CompositionErrorKind.INVALID, raised.exception.kind)
        self.assertEqual([], session_calls)
        self.assertEqual([], transport_calls)
    def test_rejects_missing_and_mismatched_dependencies_without_factory_calls(self):
        config = CodexJsonlConfig(("/declared/codex",))
        with self.assertRaises(CompositionError) as raised:
            build_status_client(config, None)
        self.assertEqual(CompositionErrorKind.MISSING, raised.exception.kind)

        with self.assertRaises(CompositionError) as raised:
            build_status_client(config, OpenCodeGoDependencies(FixedClock(), lambda config: Transport(opencode_response())))
        self.assertEqual(CompositionErrorKind.DEPENDENCY_MISMATCH, raised.exception.kind)

        with self.assertRaises(CompositionError) as raised:
            build_status_client(config, CodexJsonlDependencies(None, lambda: Session(codex_payload())))
        self.assertEqual(CompositionErrorKind.INVALID, raised.exception.kind)
        with self.assertRaises(CompositionError) as raised:
            build_status_client(config, CodexJsonlDependencies(FixedClock(), lambda: None))
        self.assertEqual((CompositionErrorKind.INVALID, "provider composition input is invalid"), (raised.exception.kind, raised.exception.safe_message))
    def test_codex_selects_only_its_factory_and_returns_ready_client(self):
        clock = FixedClock()
        session = Session(codex_payload())
        selected, other = [], []

        def session_factory():
            selected.append(True)
            return session

        def transport_factory(config):
            other.append(config)
            return Transport(opencode_response())

        client = build_status_client(
            CodexJsonlConfig(("/declared/codex",)),
            CodexJsonlDependencies(clock, session_factory),
        )
        result = client.read_status(REQUEST)

        self.assertIsInstance(result, StatusSnapshotResult)
        self.assertEqual(Freshness.FRESH, result.freshness)
        self.assertEqual(1, len(selected))
        self.assertEqual([], other)
        self.assertEqual(1, session.calls)
    def test_opencode_selects_only_its_factory_and_preserves_dependencies(self):
        clock = FixedClock()
        transport = Transport(opencode_response())
        selected, other = [], []

        def transport_factory(config):
            selected.append(config)
            return transport

        def session_factory():
            other.append(True)
            return Session(codex_payload())

        client = build_status_client(
            OpenCodeGoConfig("workspace", "opaque-cookie"),
            OpenCodeGoDependencies(clock, transport_factory),
        )
        result = client.read_status(REQUEST)

        self.assertIsInstance(result, StatusSnapshotResult)
        self.assertEqual(Freshness.FRESH, result.freshness)
        self.assertEqual(1, len(selected))
        self.assertEqual([], other)
        self.assertEqual(1, transport.calls)
        self.assertIs(client._clock, clock)
        self.assertEqual("workspace", selected[0].workspace_id)
    def test_errors_are_constant_and_redacted(self):
        secret = "cookie-that-must-not-leak"
        factory_calls = []

        def transport_factory(config):
            factory_calls.append(config)
            return Transport(opencode_response())

        with self.assertRaises(CompositionError) as raised:
            build_status_client(
                OpenCodeGoConfig("workspace", secret, endpoint="http://bad"),
                OpenCodeGoDependencies(FixedClock(), transport_factory),
            )

        self.assertEqual(CompositionErrorKind.INVALID, raised.exception.kind)
        self.assertEqual([], factory_calls)
        self.assertNotIn(secret, raised.exception.safe_message)
        self.assertNotIn("workspace", raised.exception.safe_message)
        self.assertNotIn("bad", raised.exception.safe_message)
if __name__ == "__main__":
    unittest.main()
