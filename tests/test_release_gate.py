import json, re, subprocess, unittest
from pathlib import Path
ROOT = Path(__file__).parents[1]
LEDGER = ROOT / "release/ledger.json"
DOSSIER = ROOT / "release/0.1.0.md"
ALLOWED = {"release/ledger.json", "release/0.1.0.md", "tests/test_release_gate.py"}

def git_lines(*args):
    return subprocess.check_output(["git", *args], text=True, cwd=ROOT).splitlines()
def changed_paths():
    base = git_lines("merge-base", "HEAD", "origin/main")[0]
    return set(git_lines("diff", "--name-only", f"{base}...HEAD", "--") + git_lines("diff", "--name-only", "--") + git_lines("diff", "--cached", "--name-only", "--") + git_lines("ls-files", "--others", "--exclude-standard"))
class ReleaseGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger_bytes = LEDGER.read_bytes()
        cls.ledger_text = cls.ledger_bytes.decode("ascii")
        cls.ledger = json.loads(cls.ledger_text)
        cls.doc = DOSSIER.read_text(encoding="utf-8")
    def test_ledger_is_exact_and_ascii(self):
        self.assertEqual(set(self.ledger), {"schema_version", "version", "source_sha"})
        self.assertEqual(self.ledger, {"schema_version": 1, "version": "0.1.0", "source_sha": "678c095c55a8ddfec344d96c1872c3f75340f9a8"})
        self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", self.ledger["source_sha"]))
        self.assertTrue(self.ledger_bytes.endswith(b"\n"))
    def test_version_and_evidence_are_documented(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="ascii")
        self.assertRegex(pyproject, r'(?m)^version = "0\.1\.0"$')
        for value in ("678c095c55a8ddfec344d96c1872c3f75340f9a8", "30237355636", "8642066017", "be6f750db43916bab18b2bbd3a7bb2c437f8a12a8f15270c9b9080df74bc19c5", "938faf63dd6037bc16dc745dfd11104189a4b2b00479c6afe5f709cb458ceb44", "656089ad89585dd5b03095188f439b2027d77875a27a051d47a4d3921329a873", "84c3675be3bd589b468e0e926c97d6e6130c227b820466fc2c116269328ac1cc"):
            self.assertIn(value, self.doc)
    def test_tag_command_is_candidate_bound(self):
        tags = [line.strip() for line in self.doc.splitlines() if line.strip().startswith("git tag")]
        self.assertEqual(tags, ['git tag -a v0.1.0 "$candidate_sha" -m "Release v0.1.0"'])
        self.assertEqual(re.search(r"(?m)^candidate_sha=([0-9a-f]{40})$", self.doc).group(1), self.ledger["source_sha"])
        self.assertNotIn("trusted_main_sha", tags[0]); self.assertNotIn("PR4", tags[0])
    def test_no_go_and_open_issue_gates_are_explicit(self):
        for phrase in ("Missing or invalid ledger", "Existing `v0.1.0` tag", "Wrong Pending Trusted Publisher", "OIDC publication failure", "Published files differ from the tag-run receipt"):
            self.assertIn(phrase, self.doc)
        self.assertIn("Issue #18 remains open and independent", self.doc)
        self.assertRegex(self.doc, r"not\s+resolved by `0.1.0`")
        self.assertIn("not published to PyPI", self.doc)
        self.assertIn("GitHub Release does not yet exist", self.doc)
    def test_scope_excludes_protected_workflow_and_existing_files(self):
        changed = changed_paths()
        self.assertLessEqual(changed, ALLOWED)
        self.assertNotIn(".github/workflows/protected-release.yml", changed)

if __name__ == "__main__":
    unittest.main()
