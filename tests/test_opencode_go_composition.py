import inspect
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO

import limitora
from limitora import AuthorizationPolicy, Freshness, FreshnessPolicy, MetricKind, StatusClient, StatusRequest, StatusSnapshotResult
from limitora.cli import main
from limitora.cli import _render_snapshot
from limitora.models import ProviderState
from limitora.providers import HttpPort, ProviderReader
from limitora.providers import _build_opencode_go_provider
from limitora.providers.ports import HttpResponse


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


class OpenCodeGoCompositionTests(unittest.TestCase):
    def provider(self, result):
        transport = StubTransport(result)
        provider = _build_opencode_go_provider(
            "workspace", "opaque-cookie", transport=transport, clock=lambda: NOW
        )
        return provider, transport

    def test_private_composition_returns_contract_provider_without_public_private_exports(self):
        provider, transport = self.provider(HttpResponse(200, b'{"rollingUsage":{"usagePercent":20,"resetInSec":10}}'))

        self.assertIsInstance(provider, ProviderReader)
        self.assertEqual("opencode-go", provider.provider_id.value)
        result = provider.fetch(REQUEST.to_provider_request())
        self.assertEqual(ProviderState.PARTIAL, result.status.state)
        self.assertEqual(1, transport.calls)
        self.assertNotIn("OpenCodeGoProvider", limitora.providers.__all__)
        self.assertNotIn("OpenCodeGoConfig", limitora.providers.__all__)
        signature = inspect.signature(HttpPort.send)
        self.assertEqual(("self", "request"), tuple(signature.parameters))
        self.assertEqual("HttpResponse", signature.return_annotation)

    def test_composed_provider_reaches_public_client_and_cli_with_planless_identity(self):
        provider, _ = self.provider(HttpResponse(200, b'{"rollingUsage":{"usagePercent":20,"resetInSec":10}}'))
        class FixedClock:
            def now(self):
                return NOW

        client = StatusClient(provider, clock=FixedClock())
        output, errors = StringIO(), StringIO()

        code = main(["status"], client_factory=lambda: client, stdout=output, stderr=errors)

        self.assertEqual(5, code)
        self.assertEqual(("", True), (output.getvalue(), "KIND: unauthorized" in errors.getvalue()))

    def test_public_presentation_keeps_composed_planless_identity_unfabricated(self):
        provider, _ = self.provider(HttpResponse(200, b'{"rollingUsage":{"usagePercent":20,"resetInSec":10}}'))
        snapshot = provider.fetch(REQUEST.to_provider_request())

        rendered = _render_snapshot(StatusSnapshotResult(snapshot, Freshness.FRESH))

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
