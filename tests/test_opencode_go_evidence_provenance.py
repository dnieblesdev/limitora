"""Keep synthetic shape evidence separate from semantic corroboration."""

import json
from pathlib import Path
import tomllib
import unittest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "opencode_go_dashboard_usage.json"

SYNTHETIC_SHAPE_SENTINELS = {
    "rollingUsage": {"usagePercent": 101.001, "resetInSec": 100001},
    "weeklyUsage": {"usagePercent": 202.002, "resetInSec": 200002},
    "monthlyUsage": {"usagePercent": 303.003, "resetInSec": 300003},
    "subscriptionPlan": None,
}

PUBLIC_DASHBOARD_CONTEXT = {
    "source": "https://opencode.ai/docs/go/",
    "windows": ("five_hour", "weekly", "monthly"),
}

REFERENCE_CORROBORATION = {
    "usagePercent": "used percentage points in the inclusive range 0..100",
    "resetInSec": "non-negative integral seconds after one captured fetched_at",
}

MAPPING_POLICY = {
    "accepted_windows": ("rollingUsage", "weeklyUsage", "monthlyUsage"),
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

        self.assertEqual(("five_hour", "weekly", "monthly"), PUBLIC_DASHBOARD_CONTEXT["windows"])
        self.assertNotEqual(PUBLIC_DASHBOARD_CONTEXT, payload)
        self.assertNotIn("usagePercent", PUBLIC_DASHBOARD_CONTEXT)
        self.assertNotIn("resetInSec", PUBLIC_DASHBOARD_CONTEXT)

    def test_reference_corroborates_mapping_without_claiming_dashboard_context(self) -> None:
        self.assertIn("usagePercent", REFERENCE_CORROBORATION)
        self.assertIn("resetInSec", REFERENCE_CORROBORATION)
        self.assertNotIn("windows", REFERENCE_CORROBORATION)
        self.assertNotIn("source", REFERENCE_CORROBORATION)

    def test_mapping_policy_is_explicit_and_not_derived_from_fixture_sentinels(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text())

        self.assertIsNone(MAPPING_POLICY["plan_id"])
        self.assertEqual(
            ("rollingUsage", "weeklyUsage", "monthlyUsage"),
            MAPPING_POLICY["accepted_windows"],
        )
        self.assertNotEqual(
            tuple(payload), MAPPING_POLICY["accepted_windows"],
            "fixture keys establish shape, not the mapping policy",
        )

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
