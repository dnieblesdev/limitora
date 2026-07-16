"""Offline contract tests for the human-readable status CLI."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
import tomllib
import unittest

from limitora import (
    AuthorizationPolicy, Freshness, FreshnessPolicy, MetricKind, ProviderError,
    ProviderErrorKind, ProviderId, ProviderSnapshot, ProviderState, ProviderStatus,
    SourceMetadata, StatusRequest, StatusSnapshotResult, StatusUndetectedResult,
)
from limitora.models import Quantity, QuotaWindow, UsageSnapshot, ValueAvailability, WindowKind
from limitora.cli import main


UTC = timezone.utc
PROVIDER = ProviderId("fixture-provider")
TIME = datetime(2026, 7, 15, 12, tzinfo=UTC)
EXPECTED_REQUEST = StatusRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
    FreshnessPolicy(timedelta(minutes=5)),
)


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


class CliTests(unittest.TestCase):
    def test_help_and_invalid_grammar_use_exclusive_streams(self):
        code, output, errors, _, _ = invoke(["status", "--help"])
        self.assertEqual((0, "limitora status: human-readable status only; JSON and provider/configuration options are unavailable.\n", ""), (code, output, errors))
        for argv in ([], ["version"], ["status", "--json"], ["status", "extra"]):
            code, output, errors, _, _ = invoke(argv)
            self.assertEqual(2, code); self.assertEqual("", output)
            self.assertEqual("Usage: limitora status [--help]\n", errors)

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

    def test_privacy_packaging_root_and_scope_boundaries(self):
        error = ProviderError(ProviderErrorKind.TRANSPORT, PROVIDER, "safe", retryable=True)
        error.__cause__ = RuntimeError("token=secret")
        _, output, errors, _, _ = invoke(["status"], error)
        self.assertNotIn("secret", output + errors)
        project = Path(__file__).parents[1]
        with (project / "pyproject.toml").open("rb") as file:
            self.assertEqual("limitora.cli:console_main", tomllib.load(file)["project"]["scripts"]["limitora"])
        import limitora
        self.assertNotIn("cli", limitora.__all__)
        source = (project / "src/limitora/cli/__init__.py").read_text()
        for forbidden in ("argparse", "subprocess", "import os", "pathlib", "StatusProvider"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__": unittest.main()
