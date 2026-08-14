import hashlib, json, re, unittest
from pathlib import Path
ROOT = Path(__file__).parents[1]
LEDGER = ROOT / "release/ledger.json"
DOSSIER = ROOT / "release/0.3.0.md"
HISTORICAL_020_DOSSIER = ROOT / "release/0.2.0.md"
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
        cls.historical_020_doc = HISTORICAL_020_DOSSIER.read_text(encoding="utf-8")
        cls.historical_doc = HISTORICAL_DOSSIER.read_text(encoding="utf-8")
    def test_ledger_is_exact_and_ascii(self):
        self.assertEqual(set(self.ledger), {"schema_version", "version", "source_sha"})
        self.assertEqual(self.ledger["schema_version"], 1)
        self.assertEqual(self.ledger["version"], "0.3.0")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", self.ledger["source_sha"]))
        self.assertTrue(self.ledger_bytes.endswith(b"\n"))
    def test_version_and_evidence_are_documented(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="ascii")
        self.assertRegex(pyproject, r'(?m)^version = "0\.3\.0"$')
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("0.2.0` is the published baseline", readme)
        self.assertIn("ValueAvailability.RATE_LIMITED", readme)
        self.assertIn("0.3.0` is not published yet", readme)
        for value in ("0.3.0", "v0.3.0", "origin/main", "trusted_main_sha", "candidate_sha"):
            self.assertIn(value, self.doc)
    def test_tag_command_is_candidate_bound(self):
        tags = [line.strip() for line in self.doc.splitlines() if line.strip().startswith("git tag")]
        self.assertEqual(tags, ['git tag -a v0.3.0 "$candidate_sha" -m "Release v0.3.0"'])
        self.assertIn('candidate_sha="$MERGE_SHA"', self.doc)
        self.assertNotRegex(self.doc, r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
        self.assertNotIn("trusted_main_sha", tags[0])
    def test_no_go_and_scope_gates_are_explicit(self):
        for phrase in ("Missing or invalid ledger", "Existing `v0.3.0` tag", "Wrong Pending Trusted Publisher", "OIDC publication failure", "published files differ from the tag-run receipt"):
            self.assertIn(phrase, self.doc)
        self.assertIn("Issue #57 tracks", self.doc)
        self.assertIn("approved preparation work unit", self.doc)
        self.assertIn("not published to PyPI", self.doc)
        self.assertIn("GitHub Release does not yet exist", self.doc)
    def test_candidate_and_ledger_handoff_are_explicit(self):
        self.assertIn("explicit temporary anchor", self.doc)
        self.assertIn("separate later ledger-only commit `T`", self.doc)
        self.assertIn("Exact reviewed merge commit `M`", self.doc)
        self.assertIn("Do not tag", self.doc)
        self.assertIn("git show \"$trusted_main_sha:release/ledger.json\"", self.doc)
        self.assertIn("Candidate is not a strict ancestor of trusted main", self.doc)
        self.assertNotIn("release/0.1.0.md", self.doc)

    def test_candidate_merge_boundary_is_explicit_and_fail_closed(self):
        for phrase in (
            'BASE_SHA_FILE="${BASE_SHA_FILE:-/tmp/limitora-0.3.0-base-sha}"',
            'base_sha="$(git rev-parse origin/main^{commit})"',
            'printf \'%s\\n\' "$base_sha" > "$BASE_SHA_FILE"',
            ': "${PREPARATION_PR:?set PREPARATION_PR to the approved preparation PR number}"',
            'test -s "$BASE_SHA_FILE"',
            'base_sha="$(cat "$BASE_SHA_FILE")"',
            'test "$(gh pr view "$PREPARATION_PR" --json state --jq \'.state\')" = MERGED',
            'pr_head_sha="$(gh pr view "$PREPARATION_PR" --json headRefOid --jq \'.headRefOid\')"',
            'merge_sha="$(gh pr view "$PREPARATION_PR" --json mergeCommit --jq \'.mergeCommit.oid\')"',
            "git fetch origin main",
            'git fetch origin "pull/$PREPARATION_PR/head"',
            'test "$(git rev-parse FETCH_HEAD)" = "$pr_head_sha"',
            'test "$(git cat-file -t "$merge_sha")" = commit',
            'parents=($(git show -s --format=%P "$merge_sha"))',
            'test "${#parents[@]}" -eq 2',
            'test "${parents[0]}" = "$base_sha"',
            'test "${parents[1]}" = "$pr_head_sha"',
            'git merge-base --is-ancestor "$pr_head_sha" "$merge_sha"',
            'git show "$merge_sha:pyproject.toml" | grep -Fx \'version = "0.3.0"\'',
            'test "$trusted_main_sha" != "$merge_sha"',
            'git merge-base --is-ancestor "$merge_sha" "$trusted_main_sha"',
            'test "$(git rev-parse "$trusted_main_sha^")" = "$merge_sha"',
            'git diff-tree --no-commit-id --name-only -r "$trusted_main_sha"',
            'test "$(git rev-parse "$merge_sha:release/ledger.json")" != "$(git rev-parse "$trusted_main_sha:release/ledger.json")"',
        ):
            self.assertIn(phrase, self.doc)
        self.assertRegex(self.doc, r"Do\s+not squash, rebase, or use a\s+synthetic merge\.")
        self.assertIn("git show \"$trusted_main_sha:release/ledger.json\"", self.doc)
        self.assertIn('${MERGE_SHA:?set MERGE_SHA to the exact verified two-parent merge commit SHA}', self.doc)
        self.assertIn('"source_sha": sys.argv[1]', self.doc)
        self.assertNotRegex(self.doc, r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
        self.assertNotIn('test "${parents[0]}" != "$base_sha"', self.doc)
        self.assertLess(
            self.doc.index('base_sha="$(git rev-parse origin/main^{commit})"'),
            self.doc.index('base_sha="$(cat "$BASE_SHA_FILE")"'),
        )

    def test_historical_020_status_is_not_stale(self):
        self.assertIn("historical release record", self.historical_020_doc)
        self.assertIn("0.2.0` was published to PyPI", self.historical_020_doc)
        self.assertIn("GitHub Release for `v0.2.0` exists", self.historical_020_doc)
        self.assertIn("Issue #18 was closed independently", self.historical_020_doc)
        self.assertNotIn("0.2.0` is not published to PyPI", self.historical_020_doc)
        self.assertNotIn("Issue #18 remains open", self.historical_020_doc)

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
