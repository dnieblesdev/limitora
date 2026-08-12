"""Keep synthetic shape evidence separate from semantic corroboration."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test dependency
    import tomli as tomllib
import unittest

from limitora.models import MetricKind
from limitora.providers import AuthorizationPolicy, ProviderRequest
from limitora.providers._opencode_go import OpenCodeGoConfig, OpenCodeGoProvider
from limitora.providers.ports import HttpResponse

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "opencode_go_usage.json"

SYNTHETIC_SHAPE_SENTINELS = {"usage": {
    "rolling": {"status": "ok", "percent": 101.001, "resetsAt": "2030-01-01T00:00:01Z"},
    "weekly": {"status": "ok", "percent": 202.002, "resetsAt": "2030-01-01T00:00:02Z"},
    "monthly": {"status": "ok", "percent": 303.003, "resetsAt": "2030-01-01T00:00:03Z"},
}}

PUBLIC_API_CONTEXT = {
    "source": "https://opencode.ai/zen/go/v1/usage",
    "windows": ("five_hour", "weekly", "monthly"),
}

REFERENCE_CORROBORATION = {
    "percent": "used percentage points in the inclusive range 0..100",
    "resetsAt": "absolute timezone-aware reset timestamp",
    "status": "ok or rate-limited",
}

MAPPING_POLICY = {
    "accepted_windows": ("rolling", "weekly", "monthly"),
    "plan_id": None,
}


class OpenCodeGoEvidenceProvenanceTests(unittest.TestCase):
    def test_fixture_is_shape_only_and_has_exact_synthetic_sentinels(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text())

        self.assertEqual(SYNTHETIC_SHAPE_SENTINELS, payload)
        self.assertNotIn("source", payload)
        self.assertNotIn("windows", payload)

    def test_public_context_is_distinct_from_fixture_shape(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text())

        self.assertEqual(("five_hour", "weekly", "monthly"), PUBLIC_API_CONTEXT["windows"])
        self.assertNotEqual(PUBLIC_API_CONTEXT, payload)
        self.assertNotIn("percent", PUBLIC_API_CONTEXT)
        self.assertNotIn("resetsAt", PUBLIC_API_CONTEXT)

    def test_reference_corroborates_mapping_without_claiming_private_account_context(self) -> None:
        self.assertIn("percent", REFERENCE_CORROBORATION)
        self.assertIn("resetsAt", REFERENCE_CORROBORATION)
        self.assertNotIn("windows", REFERENCE_CORROBORATION)
        self.assertNotIn("source", REFERENCE_CORROBORATION)

    def test_mapping_policy_is_explicit_and_not_derived_from_fixture_sentinels(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text())

        self.assertIsNone(MAPPING_POLICY["plan_id"])
        self.assertEqual(
            ("rolling", "weekly", "monthly"),
            MAPPING_POLICY["accepted_windows"],
        )
        self.assertNotEqual(
            tuple(payload), MAPPING_POLICY["accepted_windows"],
            "fixture keys establish shape, not the mapping policy",
        )

    def test_production_provider_mapping_corrobates_the_declared_policy(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text())
        payload = {"usage": {
            name: {"status": "ok", "percent": index * 25, "resetsAt": f"2026-07-18T12:00:{index * 10:02d}Z"}
            for index, name in enumerate(fixture["usage"])
            if name in MAPPING_POLICY["accepted_windows"]
        }}

        class StubTransport:
            def fetch(self):
                return HttpResponse(200, json.dumps(payload).encode())

        fetched_at = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
        provider = OpenCodeGoProvider(
            OpenCodeGoConfig("opaque", timedelta(seconds=10)),
            StubTransport(),
            clock=lambda: fetched_at,
        )
        snapshot = provider.fetch(ProviderRequest(
            frozenset({MetricKind.COMMERCIAL_QUOTA}),
            AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE,
        ))

        self.assertEqual(3, len(snapshot.quota_windows))
        self.assertTrue(all(window.plan_id is MAPPING_POLICY["plan_id"] for window in snapshot.quota_windows))
        self.assertEqual(("five_hour", "weekly", "monthly"), tuple(window.period for window in snapshot.quota_windows))
        self.assertEqual((0, 25, 50), tuple(window.used.value for window in snapshot.quota_windows))

    def test_httpx_is_scoped_to_the_opencode_go_runtime_extra(self) -> None:
        project = tomllib.loads((FIXTURE_PATH.parents[2] / "pyproject.toml").read_text())
        dependencies = project["project"]["dependencies"]
        provider_extra = project["project"]["optional-dependencies"]["opencode-go"]

        self.assertNotIn("httpx", dependencies)
        self.assertEqual(1, len(provider_extra))
        self.assertTrue(provider_extra[0].startswith("httpx"))
        self.assertNotIn("node", provider_extra[0].lower())


if __name__ == "__main__":
    unittest.main()
