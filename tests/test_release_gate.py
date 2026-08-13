import hashlib, json, re, unittest
from pathlib import Path
ROOT = Path(__file__).parents[1]
LEDGER = ROOT / "release/ledger.json"
DOSSIER = ROOT / "release/0.2.0.md"
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
        self.assertEqual(re.search(r"(?m)^candidate_sha=(.+)$", self.doc).group(1), "<exact reviewed candidate commit SHA>")
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
        self.assertIn("must be rewritten on the later trusted-main", self.doc)
        self.assertIn("exact reviewed candidate SHA", self.doc)
        self.assertIn("Do not tag", self.doc)
        self.assertIn("git show \"$trusted_main_sha:release/ledger.json\"", self.doc)
        self.assertIn("candidate to be an ancestor of `trusted_main_sha`", self.doc)
        self.assertNotIn("release/0.1.0.md", self.doc)
    def test_protected_workflow_matches_approved_base_bytes(self):
        lf = WORKFLOW.read_bytes().replace(b"\r\n", b"\n")
        for candidate in (lf, lf.replace(b"\n", b"\r\n")):
            self.assertEqual(workflow_sha256(candidate), APPROVED_WORKFLOW_SHA256)
        for mutation in (lf.replace(b"\n", b"\r"), lf.replace(b"name: Protected release", b"name: Protected release!", 1)):
            self.assertNotEqual(workflow_sha256(mutation), APPROVED_WORKFLOW_SHA256)

if __name__ == "__main__":
    unittest.main()
