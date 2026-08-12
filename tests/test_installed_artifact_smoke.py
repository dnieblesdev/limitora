from pathlib import Path
import inspect
import tempfile
import unittest
from unittest.mock import patch

from tests.installed_artifact_smoke import (
    LIVE_ENV,
    LiveOutcomeKind,
    LivePreflightKind,
    classify_live_outcome,
    cleanup_sitecustomize,
    install_sitecustomize,
    opencode_smoke,
    preflight_live_codex,
    redacted,
    route_config,
    sitecustomize_collision,
    sitecustomize_path,
    ROUTE_PORT_ENV,
    ROUTE_SCENARIO_ENV,
)


class LivePreflightTests(unittest.TestCase):
    def test_absent_opt_in_skips_without_discovery(self):
        calls = []

        def which(name):
            calls.append(name)
            return "/synthetic/codex"

        result = preflight_live_codex({}, which=which)

        self.assertEqual(LivePreflightKind.SKIPPED, result.kind)
        self.assertEqual([], calls)

    def test_non_one_opt_in_fails_without_discovery(self):
        calls = []
        result = preflight_live_codex({LIVE_ENV: "true"}, which=lambda name: calls.append(name))

        self.assertEqual(LivePreflightKind.INVALID_OPT_IN, result.kind)
        self.assertEqual([], calls)
        self.assertNotIn("true", result.safe_message)

    def test_candidate_classifications_are_distinct_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "missing-codex"
            folder = root / "codex-folder"
            folder.mkdir()
            non_executable = root / "codex-file"
            non_executable.write_text("synthetic", encoding="ascii")
            executable = root / "codex-executable"
            executable.write_text("synthetic", encoding="ascii")
            executable.chmod(0o700)
            cases = (
                (None, LivePreflightKind.MISSING),
                ("codex", LivePreflightKind.RELATIVE),
                (str(invalid), LivePreflightKind.INVALID),
                (str(folder), LivePreflightKind.DIRECTORY),
            )
            for candidate, expected in cases:
                with self.subTest(expected=expected):
                    result = preflight_live_codex({LIVE_ENV: "1"}, which=lambda name, candidate=candidate: candidate)
                    self.assertEqual(expected, result.kind)
                    self.assertNotIn(str(root), result.safe_message)
            with patch("tests.installed_artifact_smoke.os.access", return_value=False):
                result = preflight_live_codex({LIVE_ENV: "1"}, which=lambda name: str(non_executable))
            self.assertEqual(LivePreflightKind.NOT_EXECUTABLE, result.kind)
            result = preflight_live_codex({LIVE_ENV: "1"}, which=lambda name: str(executable))
            self.assertEqual(LivePreflightKind.READY, result.kind)
            self.assertEqual(str(executable), result.runner)


class LiveOutcomeTests(unittest.TestCase):
    def test_classification_keeps_only_typed_exit_evidence(self):
        success = classify_live_outcome(0, '{"result":"snapshot","provider_id":{"value":"codex"},"secret":"payload"}')
        provider_error = classify_live_outcome(5, '{"version":1,"error":{"kind":"transport","safe_message":"safe"}}')
        exit_failure = classify_live_outcome(2, "Traceback secret payload")

        self.assertEqual(LiveOutcomeKind.SUCCESS, success.kind)
        self.assertEqual(LiveOutcomeKind.PROVIDER_ERROR, provider_error.kind)
        self.assertEqual(LiveOutcomeKind.EXIT, exit_failure.kind)
        self.assertEqual(0, success.exit_code)
        self.assertNotIn("secret", repr(success))


class InstalledRouteHelperTests(unittest.TestCase):
    def test_opencode_smoke_requires_the_installed_cli_path(self):
        parameters = tuple(inspect.signature(opencode_smoke).parameters)
        self.assertEqual(("require_dependency", "site_packages", "cli"), parameters)
        source = Path(__file__).with_name("installed_artifact_smoke.py").read_text(encoding="ascii")
        self.assertIn("opencode_smoke(args.require_opencode_dependency, site_packages, args.cli)", source)

    def test_opencode_smoke_is_bound_to_supported_api_contract(self):
        source = Path(__file__).with_name("installed_artifact_smoke.py").read_text(encoding="ascii")
        for required in (
            '"https://opencode.ai/zen/go/v1/usage"',
            '"LIMITORA_OPENCODE_API_KEY": api_key',
            '"usage":{"rolling":{"status":"ok","percent":25,"resetsAt":"2026-07-18T12:00:10Z"}',
            'request.headers.get_list("authorization") != ["Bearer " + api_key]',
            'request.headers.get_list("cookie") != []',
            'request.headers.get_list("origin") != []',
            'request.content != b""',
        ):
            self.assertIn(required, source)
        for forbidden in (
            "legacy_opencode_smoke",
            "LIMITORA_OPENCODE_WORKSPACE_ID",
            "LIMITORA_OPENCODE_AUTH_COOKIE",
            "/workspace/",
            "rollingUsage",
            "weeklyUsage",
            "monthlyUsage",
        ):
            self.assertNotIn(forbidden, source)

    def test_route_config_rejects_invalid_port_or_scenario(self):
        valid = {ROUTE_PORT_ENV: "12345", ROUTE_SCENARIO_ENV: "valid"}
        self.assertEqual((12345, "valid"), route_config(valid))
        for invalid in (
            {ROUTE_PORT_ENV: "0", ROUTE_SCENARIO_ENV: "valid"},
            {ROUTE_PORT_ENV: "65536", ROUTE_SCENARIO_ENV: "valid"},
            {ROUTE_PORT_ENV: "port", ROUTE_SCENARIO_ENV: "valid"},
            {ROUTE_PORT_ENV: "12345", ROUTE_SCENARIO_ENV: "unknown"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(AssertionError):
                route_config(invalid)

    def test_sitecustomize_collision_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = sitecustomize_path(Path(directory))
            path.write_text("unrelated", encoding="ascii")
            self.assertTrue(sitecustomize_collision(path))
            with patch("tests.installed_artifact_smoke.importlib.util.find_spec", return_value=type("Spec", (), {"origin": "/other/sitecustomize.py"})()):
                self.assertTrue(sitecustomize_collision(path.with_name("other.py")))

    def test_owned_sitecustomize_is_created_and_exact_cleanup_only(self):
        with tempfile.TemporaryDirectory() as directory, patch("tests.installed_artifact_smoke.importlib.util.find_spec", return_value=None):
            path, owned = install_sitecustomize(Path(directory))
            self.assertEqual(owned, path.read_text(encoding="ascii"))
            cleanup_sitecustomize(path, owned)
            self.assertFalse(path.exists())
            path, owned = install_sitecustomize(Path(directory))
            path.write_text("changed", encoding="ascii")
            cleanup_sitecustomize(path, owned)
            self.assertEqual("changed", path.read_text(encoding="ascii"))

    def test_redaction_helper_rejects_all_sensitive_markers(self):
        self.assertTrue(redacted("scenario=valid requests=1 contract=true"))
        for marker in ("api-key/raw-header-marker", "raw-payload-marker", "proxy/raw-proxy-marker"):
            with self.subTest(marker=marker):
                self.assertFalse(redacted("unsafe " + marker))


if __name__ == "__main__":
    unittest.main()
