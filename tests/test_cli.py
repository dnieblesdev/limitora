"""Offline contract tests for the human-readable and JSON status CLI."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

from limitora import (
    AuthorizationPolicy, Freshness, FreshnessPolicy, MetricKind, ProviderError,
    ProviderErrorKind, ProviderId, ProviderSnapshot, ProviderState, ProviderStatus,
    SourceMetadata, StatusRequest, StatusSnapshotResult, StatusUndetectedResult,
)
from limitora.composition import CodexJsonlConfig, CompositionError, OpenCodeGoConfig, activate_provider
from limitora.cli import (
    CliIntent, CliUsageError, CodexIntent, OpenCodeGoIntent, _HELP, _USAGE,
    intent_to_config, main, parse,
)
from limitora.models import Quantity, QuotaWindow, UsageSnapshot, ValueAvailability, WindowKind


UTC = timezone.utc
PROVIDER = ProviderId("fixture-provider")
TIME = datetime(2026, 7, 15, 12, tzinfo=UTC)
EXPECTED_REQUEST = StatusRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
    FreshnessPolicy(timedelta(minutes=5)),
)
CODEX_RUNNER = ("/declared/codex", "run")


def snapshot(*, freshness=Freshness.FRESH, windows=(), usage=None):
    return StatusSnapshotResult(ProviderSnapshot(
        PROVIDER, ProviderStatus(PROVIDER, ProviderState.AVAILABLE, TIME), TIME,
        TIME - timedelta(hours=1), SourceMetadata("offline-fixture"), windows, usage,
    ), freshness)


class FakeClient:
    def __init__(self, result): self.result, self.requests = result, []
    def read_status(self, request):
        self.requests.append(request)
        if isinstance(self.result, Exception): raise self.result
        return self.result


def invoke(argv, result=None):
    output, errors = StringIO(), StringIO()
    client = FakeClient(result) if result is not None else None
    factory_calls = []
    def factory(): factory_calls.append(True); return client
    code = main(argv, client_factory=factory if client else None, stdout=output, stderr=errors)
    return code, output.getvalue(), errors.getvalue(), client, factory_calls


def invoke_with_factory(argv, factory):
    output, errors = StringIO(), StringIO()
    code = main(argv, client_factory=factory, stdout=output, stderr=errors)
    return code, output.getvalue(), errors.getvalue()


def invoke_with_provider(argv, client):
    """Invoke the CLI while patching ``activate_provider`` to return ``client``.

    The CLI delegates to ``activate_provider`` whenever ``--provider`` is
    given, so the test injects the pre-built client through that seam.
    """
    output, errors = StringIO(), StringIO()
    with patch("limitora.cli.activate_provider", return_value=client) as mock_activate:
        code = main(argv, stdout=output, stderr=errors)
    return code, output.getvalue(), errors.getvalue(), client, mock_activate


class HelpAndUnconfiguredTests(unittest.TestCase):
    def test_help_text_writes_to_stdout_and_exits_zero(self):
        code, output, errors, _, _ = invoke(["status", "--help"])
        self.assertEqual(0, code)
        self.assertEqual(_HELP, output)
        self.assertEqual("", errors)

    def test_help_with_json_writes_to_stderr_leaves_stdout_empty(self):
        code, output, errors, _, _ = invoke(["status", "--json", "--help"])
        self.assertEqual(0, code)
        self.assertEqual("", output)
        self.assertEqual(_HELP, errors)

    def test_help_text_documents_provider_and_json_flags(self):
        self.assertIn("--json", _HELP)
        self.assertIn("--provider", _HELP)
        self.assertIn("--runner", _HELP)
        self.assertIn("--workspace-id", _HELP)
        self.assertIn("--auth-cookie", _HELP)
        self.assertIn("--timeout", _HELP)
        self.assertIn("--endpoint", _HELP)

    def test_no_flags_routes_to_unconfigured_stderr_and_exit_four(self):
        code, output, errors, _, _ = invoke(["status"])
        self.assertEqual(4, code)
        self.assertEqual("ERROR: no provider configured\n", errors)
        self.assertEqual("", output)

    def test_json_flag_without_provider_routes_to_unconfigured_exit_four(self):
        code, output, errors, _, _ = invoke(["status", "--json"])
        self.assertEqual(4, code)
        self.assertEqual("ERROR: no provider configured\n", errors)
        self.assertEqual("", output)

    def test_help_still_wins_over_json_with_client_factory_present(self):
        code, output, errors, _, _ = invoke(["status", "--help", "--json"], snapshot())
        self.assertEqual(0, code)
        self.assertEqual("", output)
        self.assertEqual(_HELP, errors)


class InvalidGrammarTests(unittest.TestCase):
    def test_unknown_provider_value_is_usage_error_exit_two(self):
        for argv in (
            ["status", "--provider", "bogus"],
            ["status", "--provider", "BOGUS"],
            ["status", "--provider", "codexx"],
        ):
            with self.subTest(argv=argv):
                code, output, errors, _, _ = invoke(argv)
                self.assertEqual(2, code)
                self.assertEqual("", output)
                self.assertTrue(errors.startswith(_USAGE), msg=errors)
                self.assertTrue(errors.endswith("\n"))

    def test_codex_without_runner_is_usage_error_exit_two(self):
        code, output, errors, _, _ = invoke(["status", "--provider", "codex"])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertTrue(errors.startswith(_USAGE), msg=errors)
        self.assertIn("codex", errors)

    def test_opencode_without_required_flags_is_usage_error_exit_two(self):
        for argv in (
            ["status", "--provider", "opencode-go"],
            ["status", "--provider", "opencode-go", "--workspace-id", "ws"],
            ["status", "--provider", "opencode-go", "--auth-cookie", "c"],
        ):
            with self.subTest(argv=argv):
                code, output, errors, _, _ = invoke(argv)
                self.assertEqual(2, code)
                self.assertEqual("", output)
                self.assertTrue(errors.startswith(_USAGE), msg=errors)
                self.assertIn("opencode-go", errors)

    def test_runner_with_opencode_provider_is_cross_flag_usage_error(self):
        code, output, errors, _, _ = invoke(["status", "--provider", "opencode-go", "--runner", "/x"])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertTrue(errors.startswith(_USAGE), msg=errors)
        self.assertIn("codex flags", errors)

    def test_workspace_id_with_codex_provider_is_cross_flag_usage_error(self):
        code, output, errors, _, _ = invoke(["status", "--provider", "codex", "--workspace-id", "ws"])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertTrue(errors.startswith(_USAGE), msg=errors)
        self.assertIn("opencode-go flags", errors)

    def test_duplicate_json_flag_is_usage_error(self):
        code, output, errors, _, _ = invoke(["status", "--json", "--json"])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("more than once", errors)

    def test_duplicate_workspace_id_is_usage_error(self):
        code, output, errors, _, _ = invoke([
            "status", "--provider", "opencode-go",
            "--workspace-id", "a", "--workspace-id", "b",
            "--auth-cookie", "c",
        ])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("more than once", errors)

    def test_runner_missing_value_at_end_is_usage_error(self):
        code, output, errors, _, _ = invoke(["status", "--provider", "codex", "--runner"])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("requires a value", errors)

    def test_runner_followed_by_flag_is_usage_error(self):
        code, output, errors, _, _ = invoke(["status", "--provider", "codex", "--runner", "--json"])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("requires a value", errors)

    def test_unexpected_positional_is_usage_error(self):
        for argv in (["status", "extra"], ["status", "status"]):
            with self.subTest(argv=argv):
                code, output, errors, _, _ = invoke(argv)
                self.assertEqual(2, code)
                self.assertEqual("", output)
                self.assertIn("unexpected positional", errors)

    def test_keyvalue_form_is_rejected(self):
        for argv in (
            ["status", "--provider=codex"],
            ["status", "--runner=/x"],
            ["status", "--workspace-id=ws"],
        ):
            with self.subTest(argv=argv):
                code, output, errors, _, _ = invoke(argv)
                self.assertEqual(2, code)
                self.assertEqual("", output)
                self.assertIn("--key=value", errors)

    def test_unknown_flag_is_usage_error(self):
        code, output, errors, _, _ = invoke(["status", "--bogus"])
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("unknown flag", errors)

    def test_legacy_grammar_without_status_is_usage_error(self):
        for argv in ([], ["version"]):
            with self.subTest(argv=argv):
                code, output, errors, _, _ = invoke(argv)
                self.assertEqual(2, code)
                self.assertEqual("", output)
                self.assertEqual(_USAGE, errors)

    def test_timeout_must_be_positive_integer_under_eleven(self):
        for argv in (
            ["status", "--provider", "opencode-go",
             "--workspace-id", "ws", "--auth-cookie", "c",
             "--timeout", "0"],
            ["status", "--provider", "opencode-go",
             "--workspace-id", "ws", "--auth-cookie", "c",
             "--timeout", "11"],
            ["status", "--provider", "opencode-go",
             "--workspace-id", "ws", "--auth-cookie", "c",
             "--timeout", "abc"],
        ):
            with self.subTest(argv=argv):
                code, output, errors, _, _ = invoke(argv)
                self.assertEqual(2, code)
                self.assertEqual("", output)
                self.assertIn("--timeout", errors)


class ParseUnitTests(unittest.TestCase):
    def test_parse_help_only(self):
        intent = parse(["status", "--help"])
        self.assertEqual(CliIntent(help_requested=True), intent)

    def test_parse_help_and_json_intent_captures_both(self):
        intent = parse(["status", "--help", "--json"])
        self.assertTrue(intent.help_requested)
        self.assertTrue(intent.json_requested)
        self.assertIsNone(intent.provider)

    def test_parse_codex_with_repeated_runner_builds_tuple(self):
        intent = parse(["status", "--provider", "codex", "--runner", "/bin/sh", "--runner", "-c"])
        self.assertEqual("codex", intent.provider)
        self.assertEqual(("/bin/sh", "-c"), intent.codex.runner)

    def test_parse_opencode_uses_defaults(self):
        intent = parse([
            "status", "--provider", "opencode-go",
            "--workspace-id", "ws1", "--auth-cookie", "c1",
        ])
        self.assertEqual("opencode-go", intent.provider)
        self.assertEqual("ws1", intent.opencode.workspace_id)
        self.assertEqual("c1", intent.opencode.auth_cookie)
        self.assertEqual("https://opencode.ai", intent.opencode.endpoint)
        self.assertEqual(10, intent.opencode.timeout_seconds)
        self.assertFalse(intent.opencode.allow_authorized_source)

    def test_parse_allow_authorized_source_flags(self):
        codex_intent = parse([
            "status", "--provider", "codex", "--runner", "/x",
            "--codex-allow-authorized-source",
        ])
        self.assertTrue(codex_intent.codex.allow_authorized_source)
        opencode_intent = parse([
            "status", "--provider", "opencode-go",
            "--workspace-id", "ws", "--auth-cookie", "c",
            "--opencode-allow-authorized-source",
        ])
        self.assertTrue(opencode_intent.opencode.allow_authorized_source)

    def test_parse_raises_usage_error_for_known_violations(self):
        cases = (
            ["status", "--provider", "bogus"],
            ["status", "--provider", "codex"],
            ["status", "--provider", "codex", "--runner"],
            ["status", "--provider", "codex", "--runner", "--json"],
            ["status", "--json", "--json"],
            ["status", "extra"],
            ["status", "--provider=codex"],
            ["status", "--bogus"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(CliUsageError):
                    parse(argv)

    def test_parse_status_token_must_be_present(self):
        with self.assertRaises(CliUsageError):
            parse([])


class IntentToConfigUnitTests(unittest.TestCase):
    def test_codex_intent_maps_to_codex_config(self):
        intent = CliIntent(
            provider="codex",
            codex=CodexIntent(runner=CODEX_RUNNER),
        )
        config = intent_to_config(intent)
        self.assertEqual(CodexJsonlConfig(runner=CODEX_RUNNER), config)

    def test_opencode_intent_maps_to_opencode_config(self):
        intent = CliIntent(
            provider="opencode-go",
            opencode=OpenCodeGoIntent(workspace_id="ws1", auth_cookie="c1", endpoint="https://opencode.ai", timeout_seconds=5),
        )
        config = intent_to_config(intent)
        self.assertEqual(
            OpenCodeGoConfig(workspace_id="ws1", auth_cookie="c1", endpoint="https://opencode.ai", timeout=timedelta(seconds=5)),
            config,
        )

    def test_intent_without_provider_raises_composition_error(self):
        with self.assertRaises(CompositionError):
            intent_to_config(CliIntent())

    def test_intent_to_config_is_pure_no_io(self):
        intent = CliIntent(provider="codex", codex=CodexIntent(runner=CODEX_RUNNER))
        with patch("subprocess.Popen") as mock_popen:
            intent_to_config(intent)
        mock_popen.assert_not_called()


class CodexActivationTests(unittest.TestCase):
    def test_codex_path_activates_provider_and_renders_human(self):
        fake = FakeClient(snapshot())
        code, output, errors, client, mock_activate = invoke_with_provider(
            ["status", "--provider", "codex", "--runner", "/declared/codex"],
            fake,
        )
        self.assertEqual(0, code)
        self.assertEqual("", errors)
        self.assertIn("RESULT: snapshot", output)
        mock_activate.assert_called_once()
        self.assertIs(client, fake)

    def test_authorization_defaults_to_deny_for_codex(self):
        fake = FakeClient(snapshot())
        code, output, errors, _, _ = invoke_with_provider(
            ["status", "--provider", "codex", "--runner", "/declared/codex"],
            fake,
        )
        self.assertEqual(0, code)
        self.assertIn("RESULT: snapshot", output)
        self.assertEqual(1, len(fake.requests))
        self.assertEqual(AuthorizationPolicy.DENY_AUTHORIZED_SOURCE, fake.requests[0].authorization_policy)

    def test_codex_allow_authorized_source_opt_in(self):
        fake = FakeClient(snapshot())
        code, output, errors, _, _ = invoke_with_provider(
            [
                "status", "--provider", "codex", "--runner", "/declared/codex",
                "--codex-allow-authorized-source",
            ],
            fake,
        )
        self.assertEqual(0, code)
        self.assertEqual("", errors)
        self.assertIn("RESULT: snapshot", output)
        self.assertEqual(AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE, fake.requests[0].authorization_policy)


class JsonRoutingTests(unittest.TestCase):
    def test_json_ok_status_writes_json_document(self):
        fake = FakeClient(snapshot())
        code, output, errors, _, _ = invoke_with_provider(
            ["status", "--json", "--provider", "codex", "--runner", "/declared/codex"],
            fake,
        )
        self.assertEqual(0, code)
        self.assertEqual("", errors)
        self.assertIn('"result": "snapshot"', output)
        self.assertIn('"version": 1', output)
        self.assertTrue(output.endswith("\n"))

    def test_json_stale_status_writes_document_and_exit_three(self):
        fake = FakeClient(snapshot(freshness=Freshness.STALE))
        code, output, errors, _, _ = invoke_with_provider(
            ["status", "--json", "--provider", "codex", "--runner", "/declared/codex"],
            fake,
        )
        self.assertEqual(3, code)
        self.assertEqual("", errors)
        self.assertIn('"freshness": "stale"', output)

    def test_json_undetected_writes_envelope(self):
        fake = FakeClient(StatusUndetectedResult())
        code, output, errors, _, _ = invoke_with_provider(
            ["status", "--json", "--provider", "codex", "--runner", "/declared/codex"],
            fake,
        )
        self.assertEqual(0, code)
        self.assertEqual("", errors)
        self.assertIn('"result": "undetected"', output)

    def test_json_provider_error_writes_envelope_to_stdout_and_stderr_empty(self):
        error = ProviderError(ProviderErrorKind.UNAUTHORIZED, PROVIDER, "safe", retryable=False)
        fake = FakeClient(error)
        code, output, errors, _, _ = invoke_with_provider(
            [
                "status", "--json", "--provider", "codex", "--runner", "/declared/codex",
                "--codex-allow-authorized-source",
            ],
            fake,
        )
        self.assertEqual(5, code)
        self.assertEqual("", errors)
        self.assertIn('"error"', output)
        self.assertIn('"kind": "unauthorized"', output)
        self.assertIn('"safe_message": "safe"', output)

    def test_json_provider_error_human_mode_stays_on_stderr(self):
        error = ProviderError(ProviderErrorKind.UNAUTHORIZED, PROVIDER, "safe", retryable=False)
        fake = FakeClient(error)
        code, output, errors, _, _ = invoke_with_provider(
            [
                "status", "--provider", "codex", "--runner", "/declared/codex",
                "--codex-allow-authorized-source",
            ],
            fake,
        )
        self.assertEqual(5, code)
        self.assertEqual("", output)
        self.assertIn("KIND: unauthorized", errors)


class PrivacyContractTests(unittest.TestCase):
    def test_cli_source_excludes_privacy_forbidden_symbols(self):
        project = Path(__file__).parents[1]
        source = (project / "src/limitora/cli/__init__.py").read_text()
        for forbidden in ("argparse", "subprocess", "import os", "pathlib", "StatusProvider"):
            self.assertNotIn(forbidden, source)

    def test_cli_module_delegates_rendering_to_output(self):
        project = Path(__file__).parents[1]
        source = (project / "src/limitora/cli/__init__.py").read_text()
        self.assertIn("from limitora.output import render_human", source)
        self.assertIn("render_json", source)
        for forbidden in (
            "def _render_snapshot", "def _render_usage", "def _render_error",
            "def _timestamp", "def _optional", "def _quantity",
        ):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, source)

    def test_compose_entry_point_in_pyproject_and_cli_not_in_root_all(self):
        project = Path(__file__).parents[1]
        with (project / "pyproject.toml").open("rb") as file:
            self.assertEqual("limitora.cli:console_main", tomllib.load(file)["project"]["scripts"]["limitora"])
        import limitora
        self.assertNotIn("cli", limitora.__all__)

    def test_provider_error_with_secret_cause_never_leaks_in_human(self):
        error = ProviderError(ProviderErrorKind.TRANSPORT, PROVIDER, "safe", retryable=True)
        error.__cause__ = RuntimeError("token=secret")
        _, output, errors, _, _ = invoke(["status"], error)
        self.assertNotIn("secret", output + errors)
        self.assertNotIn("token=", output + errors)
        self.assertNotIn("Traceback", output + errors)
        self.assertNotIn("__cause__", output + errors)

    def test_help_text_does_not_leak_secrets_or_traceback(self):
        code, output, errors, _, _ = invoke(["status", "--help"])
        self.assertNotIn("secret", output + errors)
        self.assertNotIn("Traceback", output + errors)
        self.assertNotIn("__cause__", output + errors)

    def test_opencode_go_path_default_deny_never_echoes_auth_cookie(self):
        """WU2: auth cookie never appears in any captured stream, default DENY."""
        code, output, errors, _, _ = invoke([
            "status", "--provider", "opencode-go",
            "--workspace-id", "ws-secret-ws",
            "--auth-cookie", "opaque-secret-cookie",
        ])
        self.assertEqual(5, code)
        self.assertEqual("", output)
        self.assertIn("KIND: unauthorized", errors)
        self.assertNotIn("opaque-secret-cookie", errors)
        self.assertNotIn("ws-secret-ws", errors)
        self.assertNotIn("auth=", errors)
        self.assertNotIn("secret", errors)
        self.assertNotIn("Traceback", errors)
        self.assertNotIn("__cause__", errors)

    def test_opencode_go_path_with_allow_authorized_never_echoes_auth_cookie(self):
        """WU2: auth cookie never appears when transport is exercised under ALLOW.

        The real httpx transport is patched so this offline contract test does
        not issue a live HTTP request.
        """
        from limitora.providers import _opencode_go_httpx
        from limitora.providers.ports import HttpResponse

        class StubTransport:
            def __init__(self, config, **_):
                self.config = config

            def fetch(self):
                return HttpResponse(500, b'{"error":"server failure"}')

        with patch.object(_opencode_go_httpx, "_HttpxOpenCodeGoTransport", StubTransport):
            code, output, errors, _, _ = invoke([
                "status", "--provider", "opencode-go",
                "--workspace-id", "ws-secret-ws",
                "--auth-cookie", "opaque-secret-cookie",
                "--opencode-allow-authorized-source",
            ])
        self.assertEqual(5, code)
        combined = output + errors
        self.assertNotIn("opaque-secret-cookie", combined)
        self.assertNotIn("ws-secret-ws", combined)
        self.assertNotIn("auth=", combined)
        self.assertNotIn("secret", combined)
        self.assertNotIn("Traceback", combined)
        self.assertNotIn("__cause__", combined)

    def test_opencode_go_path_with_json_never_echoes_auth_cookie(self):
        """WU2: auth cookie never appears in the JSON envelope on stdout."""
        code, output, errors, _, _ = invoke([
            "status", "--json", "--provider", "opencode-go",
            "--workspace-id", "ws-secret-ws",
            "--auth-cookie", "opaque-secret-cookie",
        ])
        self.assertEqual(5, code)
        self.assertEqual("", errors)
        self.assertIn('"kind": "unauthorized"', output)
        self.assertNotIn("opaque-secret-cookie", output)
        self.assertNotIn("ws-secret-ws", output)
        self.assertNotIn("auth=", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("__cause__", output)


class RendererRegressionTests(unittest.TestCase):
    def test_fixed_request_and_fresh_snapshot_are_rendered(self):
        code, output, errors, client, factories = invoke(["status"], snapshot())
        self.assertEqual(0, code); self.assertEqual("", errors); self.assertEqual([True], factories)
        self.assertEqual([EXPECTED_REQUEST], client.requests)
        self.assertEqual(
            "RESULT: snapshot\nPROVIDER: fixture-provider\nSTATE: available\n"
            "STATUS_OBSERVED_AT: 2026-07-15T12:00:00Z\nFRESHNESS: fresh\n"
            "FETCHED_AT: 2026-07-15T12:00:00Z\nDATA_AT: 2026-07-15T11:00:00Z\n"
            "SOURCE: offline-fixture\nQUOTA_WINDOWS: unavailable\nUSAGE: unavailable\n", output)

    def test_stale_undetected_unconfigured_and_provider_error_routing(self):
        cases = [
            (snapshot(freshness=Freshness.STALE), 3, "RESULT: snapshot", ""),
            (StatusUndetectedResult(), 0, "RESULT: undetected\nSTATUS: unavailable\n", ""),
            (None, 4, "", "ERROR: no provider configured\n"),
            (ProviderError(ProviderErrorKind.UNAUTHORIZED, PROVIDER, "safe failure", retryable=False), 5, "", "ERROR: provider\nPROVIDER: fixture-provider\nKIND: unauthorized\nMESSAGE: safe failure\nRETRYABLE: false\n"),
        ]
        for result, expected_code, expected_output, expected_errors in cases:
            code, output, errors, _, _ = invoke(["status"], result)
            self.assertEqual(expected_code, code); self.assertEqual(expected_errors, errors)
            self.assertTrue(output.startswith(expected_output))
            self.assertTrue((output or errors).endswith("\n"))

    def test_renderer_orders_windows_normalizes_timestamps_and_preserves_absence(self):
        offset = timezone(timedelta(hours=2))
        known = QuotaWindow(WindowKind.COMMERCIAL_QUOTA, "z", "month", "pro", ValueAvailability.KNOWN,
            SourceMetadata("quota"), Quantity(Decimal("10"), MetricKind.COMMERCIAL_QUOTA, "requests"),
            Quantity(Decimal("4"), MetricKind.COMMERCIAL_QUOTA, "requests"), Quantity(Decimal("6"), MetricKind.COMMERCIAL_QUOTA, "requests"), TIME)
        unavailable = QuotaWindow(WindowKind.OTHER, "a", "day", None, ValueAvailability.UNKNOWN, SourceMetadata("missing"))
        usage = UsageSnapshot(PROVIDER, datetime(2026, 7, 15, 14, tzinfo=offset), ValueAvailability.KNOWN,
            SourceMetadata("usage"), Quantity(Decimal("20"), MetricKind.TOKENS, "tokens"))
        code, output, errors, _, _ = invoke(["status"], snapshot(windows=(known, unavailable), usage=usage))
        self.assertEqual((0, ""), (code, errors)); self.assertLess(output.index("KIND: commercial_quota"), output.index("KIND: other"))
        self.assertIn("  PLAN_ID: unavailable\n  AVAILABILITY: unknown\n  SOURCE: missing\n  LIMIT: unavailable", output)
        self.assertIn("RESET_AT: 2026-07-15T12:00:00Z", output)
        self.assertIn("USAGE:\n  OBSERVED_AT: 2026-07-15T12:00:00Z\n  AVAILABILITY: known", output)
        self.assertNotIn("%", output)


if __name__ == "__main__": unittest.main()
