from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.installed_artifact_smoke import (
    LIVE_ENV,
    LiveOutcomeKind,
    LivePreflightKind,
    classify_live_outcome,
    preflight_live_codex,
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


if __name__ == "__main__":
    unittest.main()
