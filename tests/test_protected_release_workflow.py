from pathlib import Path
import re
import unittest
WORKFLOW = Path(__file__).parents[1] / ".github/workflows/protected-release.yml"
PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
SHA = re.compile(r"^[0-9a-f]{40}$")
BUILD_WHEELHOUSE_PACKAGES = "httpx==0.28.1 setuptools==83.0.0 exceptiongroup==1.3.1 typing_extensions==4.16.0 packaging==26.0"
OFFLINE_PACKAGING_INSTALL = "python -m pip install --disable-pip-version-check --no-index --find-links bundle/wheelhouse packaging==26.0"


def job_sections(text):
    marks = list(re.finditer(r"(?m)^  (build|validate|publish):\n", text))
    return {mark.group(1): text[mark.start():marks[index + 1].start() if index + 1 < len(marks) else len(text)] for index, mark in enumerate(marks)}


def accepts_identity(candidate, tag, annotated, version, project, peeled, trusted, ancestor, checkout, ledger):
    return bool(SHA.fullmatch(candidate)) and (not tag or (re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag) and annotated and tag[1:] == version)) and version == project and peeled == candidate and candidate != trusted and ancestor and checkout == candidate and ledger == (1, version, candidate)
def accepts_artifact(receipt, artifact_id, digest, manifest, wheel, sdist):
    return receipt == (artifact_id, digest, manifest, wheel, sdist)
def normalize_digest(raw):
    value = raw.removeprefix("sha256:")
    return f"sha256:{value}" if re.fullmatch(r"[0-9a-f]{64}", value) else None
class ProtectedReleaseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="ascii")
        cls.jobs = job_sections(cls.text)
        cls.publish = cls.jobs["publish"]
    def require(self, *parts):
        for part in parts:
            self.assertIn(part, self.text)
    def test_only_dispatch_and_v_tag_triggers(self):
        self.require("  workflow_dispatch:", "  push:\n    tags: [\"v*\"]", "permissions: {}")
        self.assertNotRegex(self.text, r"(?m)^\s+(pull_request|release):")
    def test_manual_inputs_and_publish_gate(self):
        self.require("candidate-sha:", "expected-version:", "^[0-9a-f]{40}$", "github.event_name == 'push'", "github.ref_type == 'tag'")
        self.assertIn("\n    permissions:\n      id-token: write\n", self.publish)
        self.assertIn("\n    environment: pypi\n", self.publish)
        self.assertNotIn("contents:", self.publish)
        self.assertNotIn("environment:", self.jobs["build"] + self.jobs["validate"])
        self.assertRegex(self.publish, r"(?m)^    if: github\.event_name == 'push' && github\.ref_type == 'tag' .*$")
    def test_tag_and_ledger_identity_checks_are_fail_closed(self):
        self.require("refs/tags/$tag_name", "cat-file -t", "tag_object", "^{}", "origin/main^{commit}", "git show \"$trusted_main_sha:release/ledger.json\"", "merge-base --is-ancestor", "git checkout --detach", "test \"$(git rev-parse HEAD)\" = \"$source_sha\"", "ledger_main_sha", "release-ledger.json", "schema_version", "ledger version mismatch", "ledger source SHA mismatch")
        self.assertNotIn('with open("release/ledger.json"', self.text)
    def test_exact_artifact_handoff_and_receipts(self):
        self.require("artifact-id", "artifact-digest", "steps.digest.outputs.value", "RAW_DIGEST", "normalized=\"${RAW_DIGEST#sha256:}\"", "actions/artifacts/$ARTIFACT_ID", "artifact-ids:", "build-receipt.json", "manifest.sha256", "source_sha", "run_id", "run_attempt", "wheel_sha256", "sdist_sha256", "GITHUB_STEP_SUMMARY")
        self.assertEqual(self.text.count("actions/upload-artifact@"), 1)
        download_uses = re.findall(r"(?m)^      - uses: actions/download-artifact@([0-9a-f]{40}) # v4\.3\.0$", self.text)
        self.assertEqual(download_uses, ["d3f86a106a0bac45b974a628896c90dbdf5c8093"] * 2)
        download_block = (
            "      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0\n"
            "        with:\n"
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}\n"
            "          merge-multiple: true\n"
            "          path: bundle\n"
        )
        self.assertEqual(self.text.count(download_block), 2)
        self.assertGreaterEqual(self.text.count("actions/artifacts/$ARTIFACT_ID"), 2)
    def test_matrix_is_complete_and_publish_requires_success(self):
        validate = self.jobs["validate"]
        self.assertIn("os: [ubuntu-latest, windows-latest]", validate)
        self.assertTrue(all(version in validate for version in ("3.10", "3.11", "3.12", "3.13", "3.14")))
        self.assertIn("fail-fast: false", validate)
        self.assertNotIn("continue-on-error", validate)
        self.assertIn("needs.validate.result == 'success'", self.publish)
    def _assert_packaging_contract(self, text):
        jobs = job_sections(text)
        build = jobs["build"]
        validate = jobs["validate"]
        publish = jobs["publish"]
        self.assertIn(BUILD_WHEELHOUSE_PACKAGES, build)
        self.assertLess(build.index("python -m pip download"), build.index(BUILD_WHEELHOUSE_PACKAGES))
        self.assertEqual(validate.count(OFFLINE_PACKAGING_INSTALL), 1)
        receipt_end = validate.index("\n          PY\n", validate.index("python - \"$SOURCE_SHA\""))
        offline_install = validate.index(OFFLINE_PACKAGING_INSTALL)
        distribution_verification = validate.index("python scripts/verify_distributions.py dist")
        self.assertLess(receipt_end, offline_install)
        self.assertLess(offline_install, distribution_verification)
        install_lines = re.findall(r"(?m)^\s*(?:python -m )?pip install[^\n]*", validate + publish)
        packaging_lines = [line.strip() for line in install_lines if re.search(r"\bpackaging(?:[<>=!~]|\s|$)", line)]
        self.assertEqual(packaging_lines, [OFFLINE_PACKAGING_INSTALL])
        self.assertNotIn("python -m pip install -r requirements-compatibility.txt", validate)

    def test_packaging_is_built_and_installed_only_for_orchestration(self):
        self._assert_packaging_contract(self.text)

    def test_packaging_contract_is_mutation_sensitive(self):
        mutations = (
            ("build pin removed", self.text.replace(" packaging==26.0", "", 1)),
            ("offline install removed", self.text.replace(OFFLINE_PACKAGING_INSTALL, "", 1)),
            ("pin loosened", self.text.replace("packaging==26.0", "packaging>=26.0", 1)),
            ("no-index removed", self.text.replace(OFFLINE_PACKAGING_INSTALL, OFFLINE_PACKAGING_INSTALL.replace(" --no-index", ""), 1)),
            ("find-links source changed", self.text.replace(OFFLINE_PACKAGING_INSTALL, OFFLINE_PACKAGING_INSTALL.replace("bundle/wheelhouse", "bundle"), 1)),
        )
        for name, mutated in mutations:
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_packaging_contract(mutated)

    def test_public_metadata_does_not_depend_on_packaging(self):
        metadata = PYPROJECT.read_text(encoding="ascii")
        project = re.search(r"(?ms)^\[project\]\n(.*?)(?=^\[|\Z)", metadata).group(1)
        optional = re.search(r"(?ms)^\[project\.optional-dependencies\]\n(.*?)(?=^\[|\Z)", metadata).group(1)
        self.assertRegex(project, r"(?m)^dependencies = \[\]\s*$")
        self.assertRegex(optional, r'(?m)^opencode-go = \["httpx>=0\.27,<1"\]\s*$')
        self.assertNotIn("packaging", project + optional)

    def test_validate_and_publish_do_not_rebuild(self):
        validate = self.jobs["validate"]
        self.assertNotIn("python -m build", validate + self.publish)
        self.assertNotRegex(validate, r"(?im)^\s*python -m (?:build|twine|mypy)\b")
        self.assertNotIn("actions/setup-python@", self.publish)
        self.assertNotIn("actions/checkout@", self.publish)
        self.assertNotIn("skip-existing", self.publish)
        self.assertNotRegex(self.publish, r"(?im)^\s*(?:python -m )?pip (?:install|download)\b")
        self.assertNotRegex(self.publish, r"(?im)^\s*(password|username|token):")
    def test_all_actions_are_full_sha_pinned_and_reviewed(self):
        uses = re.findall(r"uses:\s+([^\s#]+)@([0-9a-f]+)\s+#\s+(v[0-9.]+)", self.text)
        self.assertEqual(len(uses), 8)
        self.assertTrue(all(len(sha) == 40 for _, sha, _ in uses))
        self.assertEqual({name for name, _, _ in uses}, {"actions/checkout", "actions/setup-python", "actions/upload-artifact", "actions/download-artifact", "pypa/gh-action-pypi-publish"})
    def test_digest_normalization_fixture(self):
        digest = "a" * 64
        self.assertEqual(normalize_digest(digest), "sha256:" + digest)
        self.assertEqual(normalize_digest("sha256:" + digest), "sha256:" + digest)
        for invalid in ("", "sha256", "sha256:" + "g" * 64, digest[:-1]):
            self.assertIsNone(normalize_digest(invalid))
    def test_fixture_identity_and_ledger_rejections(self):
        self.assertTrue(SHA.fullmatch("a" * 40))
        for candidate in ("", "a" * 39, "g" * 40, "a" * 41):
            self.assertIsNone(SHA.fullmatch(candidate))
        valid = {"schema_version": 1, "version": "0.1.0", "source_sha": "a" * 40}
        for ledger in ({}, {**valid, "version": "9.9.9"}, {**valid, "source_sha": "b" * 40}, {**valid, "schema_version": 2}):
            self.assertFalse(ledger.get("schema_version") == 1 and ledger.get("version") == "0.1.0" and ledger.get("source_sha") == "a" * 40)
    def test_identity_fixture_rejects_tag_and_checkout_substitution(self):
        sha = "a" * 40
        trusted = "b" * 40
        valid = (sha, "v0.1.0", True, "0.1.0", "0.1.0", sha, trusted, True, sha, (1, "0.1.0", sha))
        self.assertTrue(accepts_identity(*valid))
        for mutation in ((sha[:39], *valid[1:]), (sha, "v0.1", *valid[2:]), (sha, "v9.9.9", *valid[2:]), (sha, "v0.1.0", False, *valid[3:]), (sha, "v0.1.0", True, "0.1.0", "0.1.0", "c" * 40, trusted, True, sha, valid[9]), (sha, "v0.1.0", True, "0.1.0", "0.1.0", sha, sha, True, sha, valid[9]), (sha, "v0.1.0", True, "0.1.0", "0.1.0", sha, trusted, False, sha, valid[9]), (sha, "v0.1.0", True, "0.1.0", "0.1.0", sha, trusted, True, "c" * 40, valid[9]), (sha, "v0.1.0", True, "0.1.0", "0.1.0", sha, trusted, True, sha, (1, "0.1.0", trusted))):
            self.assertFalse(accepts_identity(*mutation))
    def test_artifact_fixture_rejects_id_digest_manifest_and_hash_substitution(self):
        valid = ("8633349610", "sha256:artifact", "manifest", "wheel", "sdist")
        self.assertTrue(accepts_artifact(valid, *valid))
        for mutation in (("other", *valid[1:]), (valid[0], "sha256:other", *valid[2:]), (valid[0], valid[1], "other", *valid[3:]), (valid[0], valid[1], valid[2], "other", valid[4]), (valid[0], valid[1], valid[2], valid[3], "other")):
            self.assertFalse(accepts_artifact(valid, *mutation))
if __name__ == "__main__":
    unittest.main()
