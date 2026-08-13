import hashlib, json, re, unittest
from pathlib import Path
ROOT = Path(__file__).parents[1]
LEDGER = ROOT / "release/ledger.json"
DOSSIER = ROOT / "release/0.2.0.md"
HISTORICAL_DOSSIER = ROOT / "release/0.1.0.md"
WORKFLOW = ROOT / ".github/workflows/protected-release.yml"
APPROVED_WORKFLOW_SHA256 = "71cced37d53a0f73a6ba8d659aa6cc3a87473da29b494281fab034e1df0599a8"

def workflow_sha256(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()

class ReleaseGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger_bytes = LEDGER.read_bytes()
        cls.ledger_text = cls.ledger_bytes.decode("ascii")
        cls.ledger = json.loads(cls.ledger_text)
        cls.doc = DOSSIER.read_text(encoding="utf-8")
        cls.historical_doc = HISTORICAL_DOSSIER.read_text(encoding="utf-8")
    def test_ledger_is_exact_and_ascii(self):
        self.assertEqual(set(self.ledger), {"schema_version", "version", "source_sha"})
        self.assertEqual(self.ledger["schema_version"], 1)
        self.assertEqual(self.ledger["version"], "0.2.0")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", self.ledger["source_sha"]))
        self.assertTrue(self.ledger_bytes.endswith(b"\n"))
    def test_version_and_evidence_are_documented(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="ascii")
        self.assertRegex(pyproject, r'(?m)^version = "0\.2\.0"$')
        for value in ("0.2.0", "v0.2.0", "origin/main", "trusted_main_sha", "candidate_sha"):
            self.assertIn(value, self.doc)
    def test_tag_command_is_candidate_bound(self):
        tags = [line.strip() for line in self.doc.splitlines() if line.strip().startswith("git tag")]
        self.assertEqual(tags, ['git tag -a v0.2.0 "$candidate_sha" -m "Release v0.2.0"'])
        self.assertIn('candidate_sha="$MERGE_SHA"', self.doc)
        self.assertNotRegex(self.doc, r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
        self.assertNotIn("trusted_main_sha", tags[0])
    def test_no_go_and_open_issue_gates_are_explicit(self):
        for phrase in ("Missing or invalid ledger", "Existing `v0.2.0` tag", "Wrong Pending Trusted Publisher", "OIDC publication failure", "published files differ from the tag-run receipt"):
            self.assertIn(phrase, self.doc)
        self.assertIn("Issue #18 remains open and independent", self.doc)
        self.assertRegex(self.doc, r"not\s+resolved by `0.2.0`")
        self.assertIn("not published to PyPI", self.doc)
        self.assertIn("GitHub Release does not yet exist", self.doc)
    def test_candidate_and_ledger_handoff_are_explicit(self):
        self.assertIn("ledger is intentionally a preparation anchor", self.doc)
        self.assertIn("must then be rewritten in a\nseparate, later trusted-main ledger-only commit `T`", self.doc)
        self.assertIn("exact two-parent merge commit is `M`", self.doc)
        self.assertIn("Do not tag", self.doc)
        self.assertIn("git show \"$trusted_main_sha:release/ledger.json\"", self.doc)
        self.assertIn("candidate to be an ancestor of `trusted_main_sha`", self.doc)
        self.assertNotIn("release/0.1.0.md", self.doc)

    def test_candidate_merge_boundary_is_explicit_and_fail_closed(self):
        for phrase in (
            'test "$(gh pr view 53 --json state --jq \'.state\')" = OPEN',
            "git fetch origin main",
            "base_sha=$(git rev-parse HEAD)",
            "pr_head_sha=$(gh pr view 53 --json headRefOid --jq '.headRefOid')",
            "git fetch origin pull/53/head",
            'test "$(git rev-parse FETCH_HEAD)" = "$pr_head_sha"',
            'git merge --no-ff "$pr_head_sha" -m "Merge pull request #53"',
            'test "$merge_sha" != "$pr_head_sha"',
            'parents=($(git show -s --format=%P "$merge_sha"))',
            'test "${#parents[@]}" -eq 2',
            'test "${parents[0]}" = "$base_sha"',
            'test "${parents[1]}" = "$pr_head_sha"',
            'git merge-base --is-ancestor "$pr_head_sha" "$merge_sha"',
            'git show "$merge_sha:pyproject.toml" | grep -Fx \'version = "0.2.0"\'',
            'test "$trusted_main_sha" != "$merge_sha"',
            'git merge-base --is-ancestor "$merge_sha" "$trusted_main_sha"',
            'test "$(git rev-parse "$trusted_main_sha^")" = "$merge_sha"',
            'git diff-tree --no-commit-id --name-only -r "$trusted_main_sha"',
            'test "$(git rev-parse "$merge_sha:release/ledger.json")" != "$(git rev-parse "$trusted_main_sha:release/ledger.json")"',
        ):
            self.assertIn(phrase, self.doc)
        self.assertRegex(self.doc, r"Do\s+not squash, rebase, or use a synthetic merge\.")
        self.assertIn("git show \"$trusted_main_sha:release/ledger.json\"", self.doc)
        self.assertIn('${MERGE_SHA:?set MERGE_SHA to the exact verified two-parent merge commit SHA}', self.doc)
        self.assertIn('"source_sha": sys.argv[1]', self.doc)
        self.assertNotRegex(self.doc, r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")

    def test_historical_010_status_is_not_stale(self):
        self.assertIn("historical release record", self.historical_doc)
        self.assertIn("0.1.0` was published to PyPI", self.historical_doc)
        self.assertIn("GitHub Release for `v0.1.0` exists", self.historical_doc)
        self.assertNotIn("0.1.0` is not published to PyPI", self.historical_doc)
        self.assertNotIn("The package is not published yet.", self.historical_doc)
        self.assertNotIn("Release Notes (Not Yet Published)", self.historical_doc)
    def test_protected_workflow_matches_approved_base_bytes(self):
        lf = WORKFLOW.read_bytes().replace(b"\r\n", b"\n")
        for candidate in (lf, lf.replace(b"\n", b"\r\n")):
            self.assertEqual(workflow_sha256(candidate), APPROVED_WORKFLOW_SHA256)
        for mutation in (lf.replace(b"\n", b"\r"), lf.replace(b"name: Protected release", b"name: Protected release!", 1)):
            self.assertNotEqual(workflow_sha256(mutation), APPROVED_WORKFLOW_SHA256)

if __name__ == "__main__":
    unittest.main()
