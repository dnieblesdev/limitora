import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

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

    def test_full_cli_path_opencode_go_writes_json_document_to_stdout(self):
        """End-to-end: argv -> activate_provider -> read_status -> render_json on stdout."""
        from limitora.providers import _opencode_go_httpx

        captured = []

        class StubTransport:
            def __init__(self, config, **_):
                captured.append(config)

            def fetch(self):
                return HttpResponse(
                    200,
                    b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},'
                    b'"weeklyUsage":{"usagePercent":10,"resetInSec":100},'
                    b'"monthlyUsage":{"usagePercent":5,"resetInSec":1000}}',
                )

        with patch.object(_opencode_go_httpx, "_HttpxOpenCodeGoTransport", StubTransport):
            output, errors = StringIO(), StringIO()
            code = main(
                [
                    "status", "--json", "--provider", "opencode-go",
                    "--workspace-id", "ws1", "--auth-cookie", "c1",
                    "--opencode-allow-authorized-source",
                ],
                stdout=output, stderr=errors,
            )
            out_text = output.getvalue()
            err_text = errors.getvalue()

        self.assertEqual(0, code)
        self.assertEqual("", err_text)
        self.assertIn('"result": "snapshot"', out_text)
        self.assertIn('"version": 1', out_text)
        self.assertIn('"freshness": "fresh"', out_text)
        self.assertTrue(out_text.endswith("\n"))
        # The transport received the parsed config
        self.assertEqual(1, len(captured))
        self.assertEqual("ws1", captured[0].workspace_id)
        self.assertEqual("c1", captured[0].auth_cookie)
        self.assertEqual("https://opencode.ai", captured[0].endpoint)

    def test_full_cli_path_opencode_go_human_mode_renders_human(self):
        """End-to-end: argv -> activate_provider -> read_status -> render_human on stdout."""
        from limitora.providers import _opencode_go_httpx

        class StubTransport:
            def __init__(self, config, **_):
                pass

            def fetch(self):
                return HttpResponse(
                    200,
                    b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},'
                    b'"weeklyUsage":{"usagePercent":10,"resetInSec":100},'
                    b'"monthlyUsage":{"usagePercent":5,"resetInSec":1000}}',
                )

        with patch.object(_opencode_go_httpx, "_HttpxOpenCodeGoTransport", StubTransport):
            output, errors = StringIO(), StringIO()
            code = main(
                [
                    "status", "--provider", "opencode-go",
                    "--workspace-id", "ws1", "--auth-cookie", "c1",
                    "--opencode-allow-authorized-source",
                ],
                stdout=output, stderr=errors,
            )
            out_text = output.getvalue()
            err_text = errors.getvalue()

        self.assertEqual(0, code)
        self.assertEqual("", err_text)
        self.assertIn("RESULT: snapshot", out_text)
        self.assertIn("PROVIDER: opencode-go", out_text)

    def test_full_cli_path_opencode_go_authorization_default_is_deny(self):
        """End-to-end: without --opencode-allow-authorized-source, default DENY is honored."""
        from limitora.providers import _opencode_go_httpx

        class StubTransport:
            def __init__(self, config, **_):
                pass

            def fetch(self):
                return HttpResponse(
                    200,
                    b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},'
                    b'"weeklyUsage":{"usagePercent":10,"resetInSec":100},'
                    b'"monthlyUsage":{"usagePercent":5,"resetInSec":1000}}',
                )

        with patch.object(_opencode_go_httpx, "_HttpxOpenCodeGoTransport", StubTransport):
            output, errors = StringIO(), StringIO()
            code = main(
                [
                    "status", "--json", "--provider", "opencode-go",
                    "--workspace-id", "ws1", "--auth-cookie", "c1",
                ],
                stdout=output, stderr=errors,
            )
            out_text = output.getvalue()
            err_text = errors.getvalue()

        # Without --opencode-allow-authorized-source, the provider rejects
        # with UNAUTHORIZED before calling the transport.
        self.assertEqual(5, code)
        self.assertEqual("", err_text)
        self.assertIn('"kind": "unauthorized"', out_text)


if __name__ == "__main__":
    unittest.main()
