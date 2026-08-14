from pathlib import Path
import hashlib
import re
import unittest
WORKFLOW = Path(__file__).parents[1] / ".github/workflows/protected-release.yml"
COMPATIBILITY = WORKFLOW.with_name("python-compatibility.yml")
SMOKE = Path(__file__).parents[1] / "tests/installed_artifact_smoke.py"
PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
SHA = re.compile(r"^[0-9a-f]{40}$")
BUILD_WHEELHOUSE_PACKAGES = "httpx==0.28.1 setuptools==83.0.0 exceptiongroup==1.3.1 typing_extensions==4.16.0 packaging==26.0"
OFFLINE_PACKAGING_INSTALL = '[sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--find-links", str(wheelhouse), "packaging==26.0"]'
SMOKE_SHA256 = "18a42333d49bafdb738721e60596aaafbaeab75ff5b4347b57e63b0ff16b0571"


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
def canonical_smoke_source_sha256(source):
    return hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()
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

    def test_validate_preserves_receipt_hash_and_wheelhouse_checks_on_windows(self):
        validate = self.jobs["validate"]
        audit = validate[validate.index("python - \"$SOURCE_SHA\""):validate.index("for kind, artifact")]
        self.assertIn("actions/artifacts/$ARTIFACT_ID", validate)
        for part in ("build-receipt.json", "manifest.sha256", "archive-inventory.txt", "wheel_sha256", "sdist_sha256", "source-sha.txt", "wheelhouse"):
            self.assertIn(part, audit)
        self.assertNotRegex(audit, r"if .*RUNNER_OS.*Linux.*\n(?:.*\n)*?python - \"\$SOURCE_SHA\"")

    def _assert_staging_contract(self, text):
        validate = job_sections(text)["validate"]
        for part in (
            'if os.environ["RUNNER_OS"] == "Linux":',
            'Path(os.environ["GITHUB_WORKSPACE"]).resolve()',
            'Path(os.environ["RUNNER_TEMP"]).resolve()',
            'tempfile.mkdtemp(prefix="limitora-", dir=runner_temp)',
            'assert runner_temp in (stage, *stage.parents)',
            'assert repo not in (stage, *stage.parents) and stage not in (repo, *repo.parents)',
            'shutil.copytree(downloaded_bundle, bundle)',
            'source_files.keys() == staged_files.keys()',
            'source_files[name].read_bytes() == staged_files[name].read_bytes()',
            'shutil.copy2(repo / "tests/installed_artifact_smoke.py", bundle / "smoke.py")',
            'env_dir = stage / kind / "venv"',
            'str(wheelhouse)',
            'str(artifact)',
            'str(bundle / "manifest.sha256")',
            'str(bundle / "source-sha.txt")',
            'str(bundle / "smoke.py")',
            '"--checkout", str(repo)',
            '"--wheelhouse", str(wheelhouse)',
            '"-I"',
            'cwd=stage',
            'env.pop("PYTHONPATH", None)',
            'env.pop("PYTHONHOME", None)',
            'env["PYTHONNOUSERSITE"] = "1"',
        ):
            self.assertIn(part, validate)
        stage_start = validate.index('stage = Path(tempfile.mkdtemp')
        venv_start = validate.index('env_dir = stage / kind / "venv"')
        self.assertLess(stage_start, venv_start)
        self.assertLess(validate.index("shutil.copytree(downloaded_bundle, bundle)"), venv_start)
        self.assertLess(validate.index('shutil.copy2(repo / "tests/installed_artifact_smoke.py", bundle / "smoke.py")'), venv_start)
        self.assertNotIn('repo / "tests/installed_artifact_smoke.py"), "--artifact"', validate)
        self.assertNotIn('str(repo / "tests/installed_artifact_smoke.py")', validate)
        self.assertNotIn('str(downloaded_bundle / artifact.name)', validate)
        self.assertNotIn('"--wheelhouse", str(repo)', validate)
        self.assertNotIn('"--manifest", str(repo)', validate)
        self.assertNotIn('"--source-sha", str(repo)', validate)
        self.assertLess(validate.index('if os.environ["RUNNER_OS"] == "Linux":'), validate.index('subprocess.run([sys.executable, "scripts/verify_distributions.py", "dist"]'))

    def test_installed_smokes_follow_compatibility_staging_contract(self):
        compatibility = COMPATIBILITY.read_text(encoding="ascii")
        self._assert_staging_contract(self.text)
        for part in ('RUNNER_TEMP', 'cp -a "$repo/artifact"', 'smoke.py', ' -I ', 'PYTHONNOUSERSITE=1'):
            self.assertIn(part, compatibility)

    def test_staging_contract_is_mutation_sensitive(self):
        mutations = (
            ("distribution audit unconditional", self.text.replace('if os.environ["RUNNER_OS"] == "Linux":\n', "", 1)),
            ("stage under workspace", self.text.replace('tempfile.mkdtemp(prefix="limitora-", dir=runner_temp)', 'tempfile.mkdtemp(prefix="limitora-", dir=repo)', 1)),
            ("install original bundle", self.text.replace('str(artifact)]', 'str(downloaded_bundle / artifact.name)]', 1)),
            ("smoke original artifact", self.text.replace('"--artifact", str(artifact)', '"--artifact", str(downloaded_bundle / artifact.name)', 1)),
            ("checkout as wheelhouse", self.text.replace('"--wheelhouse", str(wheelhouse)', '"--wheelhouse", str(repo)', 1)),
            ("isolated interpreter flag removed", self.text.replace('smoke = [str(python), "-I",', 'smoke = [str(python),', 1)),
            ("smoke copy omitted", self.text.replace('shutil.copy2(repo / "tests/installed_artifact_smoke.py", bundle / "smoke.py")\n', "", 1)),
        )
        for name, mutated in mutations:
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_staging_contract(mutated)

    def test_direct_url_smoke_source_is_unchanged_and_fail_closed(self):
        source = SMOKE.read_bytes()
        self.assertEqual(canonical_smoke_source_sha256(source), SMOKE_SHA256)
        text = source.decode("ascii")
        for part in (
            'direct_url = distribution.read_text("direct_url.json")',
            'check(not json.loads(direct_url).get("dir_info", {}).get("editable"), "editable installation metadata found")',
            'check(str(checkout).casefold() not in direct_url.casefold(), "direct URL contains checkout path")',
        ):
            self.assertIn(part, text)

    def test_smoke_source_integrity_accepts_lf_and_crlf_only(self):
        lf = SMOKE.read_bytes().replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        self.assertNotEqual(lf, crlf)
        self.assertEqual(canonical_smoke_source_sha256(lf), SMOKE_SHA256)
        self.assertEqual(canonical_smoke_source_sha256(crlf), SMOKE_SHA256)
        self.assertEqual(canonical_smoke_source_sha256(b"line\rline"), hashlib.sha256(b"line\rline").hexdigest())

    def test_direct_url_contract_is_mutation_sensitive(self):
        source = SMOKE.read_text(encoding="ascii")
        for mutation in (
            source.replace('check(str(checkout).casefold() not in direct_url.casefold(), "direct URL contains checkout path")', "", 1),
            source.replace('not in direct_url.casefold()', 'in direct_url.casefold()', 1),
        ):
            self.assertNotEqual(canonical_smoke_source_sha256(mutation.encode("ascii")), SMOKE_SHA256)
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
        distribution_verification = validate.index('subprocess.run([sys.executable, "scripts/verify_distributions.py", "dist"]')
        self.assertLess(receipt_end, offline_install)
        self.assertLess(offline_install, distribution_verification)
        self.assertIn('"--find-links", str(wheelhouse)', validate)
        self.assertNotIn("--find-links bundle/wheelhouse", validate)
        self.assertNotIn("python -m pip install -r requirements-compatibility.txt", validate)

    def test_packaging_is_built_and_installed_only_for_orchestration(self):
        self._assert_packaging_contract(self.text)

    def test_packaging_contract_is_mutation_sensitive(self):
        mutations = (
            ("build pin removed", self.text.replace(" packaging==26.0", "", 1)),
            ("offline install removed", self.text.replace(OFFLINE_PACKAGING_INSTALL, "", 1)),
            ("pin loosened", self.text.replace("packaging==26.0", "packaging>=26.0", 1)),
            ("no-index removed", self.text.replace(OFFLINE_PACKAGING_INSTALL, OFFLINE_PACKAGING_INSTALL.replace(', "--no-index"', ""), 1)),
            ("find-links source changed", self.text.replace(OFFLINE_PACKAGING_INSTALL, OFFLINE_PACKAGING_INSTALL.replace("str(wheelhouse)", "str(bundle)"), 1)),
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
        valid = {"schema_version": 1, "version": "0.3.0", "source_sha": "a" * 40}
        for ledger in ({}, {**valid, "version": "9.9.9"}, {**valid, "source_sha": "b" * 40}, {**valid, "schema_version": 2}):
            self.assertFalse(ledger.get("schema_version") == 1 and ledger.get("version") == "0.3.0" and ledger.get("source_sha") == "a" * 40)
    def test_identity_fixture_rejects_tag_and_checkout_substitution(self):
        sha = "a" * 40
        trusted = "b" * 40
        valid = (sha, "v0.3.0", True, "0.3.0", "0.3.0", sha, trusted, True, sha, (1, "0.3.0", sha))
        self.assertTrue(accepts_identity(*valid))
        for mutation in ((sha[:39], *valid[1:]), (sha, "v0.1", *valid[2:]), (sha, "v9.9.9", *valid[2:]), (sha, "v0.3.0", False, *valid[3:]), (sha, "v0.3.0", True, "0.3.0", "0.3.0", "c" * 40, trusted, True, sha, valid[9]), (sha, "v0.3.0", True, "0.3.0", "0.3.0", sha, sha, True, sha, valid[9]), (sha, "v0.3.0", True, "0.3.0", "0.3.0", sha, trusted, False, sha, valid[9]), (sha, "v0.3.0", True, "0.3.0", "0.3.0", sha, trusted, True, "c" * 40, valid[9]), (sha, "v0.3.0", True, "0.3.0", "0.3.0", sha, trusted, True, sha, (1, "0.3.0", trusted))):
            self.assertFalse(accepts_identity(*mutation))
    def test_artifact_fixture_rejects_id_digest_manifest_and_hash_substitution(self):
        valid = ("8633349610", "sha256:artifact", "manifest", "wheel", "sdist")
        self.assertTrue(accepts_artifact(valid, *valid))
        for mutation in (("other", *valid[1:]), (valid[0], "sha256:other", *valid[2:]), (valid[0], valid[1], "other", *valid[3:]), (valid[0], valid[1], valid[2], "other", valid[4]), (valid[0], valid[1], valid[2], valid[3], "other")):
            self.assertFalse(accepts_artifact(valid, *mutation))
if __name__ == "__main__":
    unittest.main()
