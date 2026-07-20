import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO

import limitora
from limitora import AuthorizationPolicy, Freshness, FreshnessPolicy, MetricKind, StatusRequest, StatusSnapshotResult
from limitora.cli import main
from limitora.models import ProviderState
from limitora.output import render_human
from limitora.providers.ports import HttpResponse
from limitora.composition import OpenCodeGoConfig, OpenCodeGoDependencies, build_status_client


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
REQUEST = StatusRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE,
    FreshnessPolicy(timedelta(minutes=5)),
)


class StubTransport:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return self.result


class FixedClock:
    def now(self):
        return NOW


class OpenCodeGoCompositionTests(unittest.TestCase):
    def provider(self, result):
        transport = StubTransport(result)
        client = build_status_client(
            OpenCodeGoConfig("workspace", "opaque-cookie"),
            OpenCodeGoDependencies(FixedClock(), lambda config: transport),
        )
        return client, transport

    def test_public_composition_returns_ready_client_without_public_private_exports(self):
        client, transport = self.provider(HttpResponse(200, b'{"rollingUsage":{"usagePercent":20,"resetInSec":10}}'))

        result = client.read_status(REQUEST)
        self.assertEqual(ProviderState.PARTIAL, result.snapshot.status.state)
        self.assertEqual(1, transport.calls)
        self.assertNotIn("OpenCodeGoProvider", limitora.providers.__all__)
        self.assertNotIn("OpenCodeGoConfig", limitora.providers.__all__)

    def test_composed_provider_reaches_public_client_and_cli_with_planless_identity(self):
        client, _ = self.provider(HttpResponse(200, b'{"rollingUsage":{"usagePercent":20,"resetInSec":10}}'))
        output, errors = StringIO(), StringIO()

        code = main(["status"], client_factory=lambda: client, stdout=output, stderr=errors)

        self.assertEqual(5, code)
        self.assertEqual(("", True), (output.getvalue(), "KIND: unauthorized" in errors.getvalue()))

    def test_public_presentation_keeps_composed_planless_identity_unfabricated(self):
        client, _ = self.provider(HttpResponse(200, b'{"rollingUsage":{"usagePercent":20,"resetInSec":10}}'))
        snapshot = client.read_status(REQUEST).snapshot

        rendered = render_human(StatusSnapshotResult(snapshot, Freshness.FRESH))

        self.assertIn("PROVIDER: opencode-go", rendered)
        self.assertIn("PLAN_ID: unavailable", rendered)
        self.assertNotIn("PLAN_ID: free", rendered)

    def test_private_provider_modules_have_no_runtime_or_secret_discovery_boundary(self):
        from pathlib import Path

        root = Path(__file__).parents[1] / "src" / "limitora" / "providers"
        source = "\n".join((root / name).read_text() for name in ("_opencode_go.py", "_opencode_go_httpx.py"))
        for forbidden in ("subprocess", "os.environ", "logging", "node", "cookiejar"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
