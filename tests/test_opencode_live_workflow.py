import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/opencode-live.yml"
DOC = ROOT / "docs/testing/opencode-live.md"


def block(text: str, header: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next(index for index, line in enumerate(lines) if line.rstrip("\n") == header)
    indent = len(header) - len(header.lstrip())
    selected = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        selected.append(line)
    return "".join(selected)


def named_steps(job: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^      - name: (.+)\n", job))
    return {
        match.group(1): job[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(job)]
        for index, match in enumerate(matches)
    }


class OpenCodeLiveWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="ascii")
        cls.documentation = DOC.read_text(encoding="ascii")
        cls.trigger = block(cls.workflow, "on:")
        cls.job = block(cls.workflow, "  opencode-live:")
        cls.steps = named_steps(cls.job)

    def test_manual_trigger_is_exclusive_and_confirmation_is_exact(self):
        self.assertIn("workflow_dispatch:", self.trigger)
        for forbidden in ("push:", "pull_request:", "schedule:", "workflow_run:", "repository_dispatch:", "fork"):
            self.assertNotIn(forbidden, self.trigger)
        confirmation = block(self.trigger, "    inputs:")
        self.assertRegex(confirmation, r"(?m)^      confirmation:\n")
        self.assertRegex(confirmation, r"(?m)^        required: true\n")
        self.assertIn("CONFIRMATION: ${{ inputs.confirmation }}", self.steps["Confirm manual run"])
        self.assertIn('test "$CONFIRMATION" = "RUN"', self.steps["Confirm manual run"])

    def test_permissions_timeout_and_non_overlapping_concurrency(self):
        self.assertEqual(1, self.workflow.count("permissions: {}"))
        self.assertEqual("    permissions:\n      contents: read\n", block(self.job, "    permissions:"))
        self.assertRegex(self.job, r"(?m)^    timeout-minutes: 15$")
        self.assertIn("    if: github.ref == 'refs/heads/main'\n", self.job)
        self.assertIn("    environment: opencode-live\n", self.job)
        concurrency = block(self.workflow, "concurrency:")
        self.assertIn("group: opencode-live-${{ github.repository }}", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)

    def test_actions_are_full_sha_pinned_and_job_is_ubuntu_only(self):
        actions = re.findall(r"uses: ([^\s]+)", self.workflow)
        self.assertEqual(
            actions,
            [
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            ],
        )
        self.assertRegex(self.job, r"(?m)^    runs-on: ubuntu-latest$")
        self.assertNotIn("matrix:", self.job)
        jobs = block(self.workflow, "jobs:")
        self.assertEqual(1, len(re.findall(r"(?m)^  [a-z][a-z0-9-]+:\n", jobs)))

    def test_build_venv_and_non_editable_install_are_scoped(self):
        build = self.steps["Build fresh wheel from target commit"]
        install = self.steps["Install wheel in isolated environment"]
        gate = "    if: github.ref == 'refs/heads/main'"
        checkout = "          ref: ${{ github.sha }}"
        self.assertLess(self.workflow.index(gate), self.workflow.index(checkout))
        self.assertIn("ref: ${{ github.sha }}", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', build)
        self.assertIn("python -m pip install -r requirements-compatibility.txt", build)
        self.assertIn("python -m build --wheel", build)
        self.assertIn('python -m venv "$RUNNER_TEMP/limitora-opencode-venv"', install)
        self.assertIn('"httpx==0.28.1"', install)
        self.assertRegex(install, r'pip install "\$\{wheels\[0\]\}"')
        self.assertNotIn("--no-editable", install)
        self.assertNotIn("pip install -e", install)
        self.assertIn("pip check", install)

    def test_verification_and_live_invocation_clear_python_environment(self):
        verification = self.steps["Assert installed import location"]
        live = self.steps["Run OpenCode live driver"]
        for section in (verification, live):
            self.assertIn("unset PYTHONPATH PYTHONHOME", section)
            self.assertIn("export PYTHONNOUSERSITE=1", section)
        self.assertIn('python" -I -c', verification)
        self.assertIn('assert checkout not in (location, *location.parents)', verification)
        self.assertIn(
            'python scripts/opencode_live_driver.py --confirm RUN --cli "$RUNNER_TEMP/limitora-opencode-venv/bin/limitora"',
            live,
        )
        self.assertNotIn("--dotenv", self.workflow)

    def test_secrets_are_mapped_only_on_live_step(self):
        secret_lines = [line for line in self.workflow.splitlines() if "secrets." in line]
        self.assertEqual(
            secret_lines,
            [
                "          LIMITORA_OPENCODE_WORKSPACE_ID: ${{ secrets.LIMITORA_OPENCODE_WORKSPACE_ID }}",
                "          LIMITORA_OPENCODE_AUTH_COOKIE: ${{ secrets.LIMITORA_OPENCODE_AUTH_COOKIE }}",
            ],
        )
        live = self.steps["Run OpenCode live driver"]
        self.assertIn("LIMITORA_OPENCODE_WORKSPACE_ID:", live)
        self.assertIn("LIMITORA_OPENCODE_AUTH_COOKIE:", live)
        self.assertNotIn("secrets.", self.workflow[: self.workflow.index("      - name: Run OpenCode live driver")])

    def test_live_step_has_no_unsafe_output_or_extra_execution(self):
        live = self.steps["Run OpenCode live driver"]
        for forbidden in ("set -x", "printenv", "GITHUB_STEP_SUMMARY", "upload-artifact", "actions/cache", "secret-length", "env "):
            self.assertNotIn(forbidden, live)
        self.assertEqual(live.count("python scripts/opencode_live_driver.py"), 1)
        self.assertNotIn("continue-on-error", live)
        self.assertNotIn("||", live)
        self.assertNotIn("/bin/limitora", "\n".join(line for line in self.workflow.splitlines() if "python scripts/opencode_live_driver.py" not in line))
        self.assertNotIn("codex", self.workflow.lower())
        for forbidden in ("--dotenv", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "GITHUB_ENV", "upload-artifact", "actions/cache"):
            self.assertNotIn(forbidden, self.workflow)
        self.assertNotRegex(self.workflow, r"(?<![A-Za-z0-9_])\.env(?:\s|['\"`]|$)")
        self.assertNotIn("PYTHONPATH: src", self.workflow)
        self.assertNotIn("PYTHONPATH=src", self.workflow)

    def test_documentation_matches_manual_boundary_and_evidence(self):
        for phrase in (
            ".github/workflows/opencode-live.yml",
            "LIMITORA_OPENCODE_WORKSPACE_ID",
            "LIMITORA_OPENCODE_AUTH_COOKIE",
            "success_snapshot",
            "authentication",
            "schema_drift",
            "rate_limited",
            "source_unavailable",
            "transport",
            "unexpected_limitora_regression",
            "opencode-live",
            "required reviewers",
            "Before storing either secret or running the workflow",
            "does not create",
            "enforce required-reviewer rules",
            "Issue #18 remains open",
            "wheel",
            "sdist",
        ):
            self.assertIn(phrase, self.documentation)
        self.assertIn("--confirm RUN", self.documentation)
        self.assertIn("does not pass `--dotenv`", self.documentation.split("## Manual GitHub Actions run", 1)[-1])


if __name__ == "__main__":
    unittest.main()
