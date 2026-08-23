import hashlib, json, os, re, subprocess, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).parents[1]
LEDGER = ROOT / "release/ledger.json"
DOSSIER = ROOT / "release/0.3.1.md"
HISTORICAL_020_DOSSIER = ROOT / "release/0.2.0.md"
HISTORICAL_DOSSIER = ROOT / "release/0.1.0.md"
WORKFLOW = ROOT / ".github/workflows/protected-release.yml"
APPROVED_WORKFLOW_SHA256 = "71cced37d53a0f73a6ba8d659aa6cc3a87473da29b494281fab034e1df0599a8"
AUTHORIZED_CANDIDATE_PATHS = (
    ".github/workflows/python-compatibility.yml",
    "README.md",
    "pyproject.toml",
    "release/0.3.1.md",
    "release/ledger.json",
    "scripts/verify_distributions.py",
    "tests/test_protected_release_workflow.py",
    "tests/test_release_gate.py",
)

def workflow_sha256(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()

def run_git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run_candidate_path_gate(gate, output):
    with tempfile.TemporaryDirectory() as temporary:
        fake_git = Path(temporary) / "git"
        fake_git.write_text("#!/bin/sh\nprintf '%s\\n' \"$GIT_OUTPUT\"\n", encoding="ascii")
        fake_git.chmod(0o755)
        environment = os.environ | {
            "GIT_OUTPUT": output,
            "PATH": f"{temporary}:{os.environ['PATH']}",
        }
        return subprocess.run(["bash", "-c", gate], env=environment, capture_output=True, text=True)


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
        self.assertEqual(self.ledger["version"], "0.3.1")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", self.ledger["source_sha"]))
        self.assertTrue(self.ledger_bytes.endswith(b"\n"))
    def test_version_and_evidence_are_documented(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="ascii")
        self.assertRegex(pyproject, r'(?m)^version = "0\.3\.1"$')
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("0.2.0` is the published baseline", readme)
        self.assertIn("ValueAvailability.RATE_LIMITED", readme)
        self.assertIn("0.3.1` is not published yet", readme)
        for value in ("0.3.1", "v0.3.1", "origin/main", "trusted_main_sha", "candidate_sha", "PR #62", "issue #61"):
            self.assertIn(value, self.doc)
    def test_tag_command_is_candidate_bound(self):
        tags = [line.strip() for line in self.doc.splitlines() if line.strip().startswith("git tag")]
        self.assertEqual(tags, ['git tag -a "$TAG_NAME" "$candidate_sha" -m "Release $TAG_NAME"'])
        self.assertIn("candidate-bound annotated tag", self.doc)
        self.assertIn('candidate_sha="$(git log --first-parent -1 --format=%H "$trusted_main_sha" -- release/0.3.1.md)"', self.doc)
        self.assertNotIn("MERGE_SHA", self.doc)
        self.assertNotIn("AUTHORIZATION_HEAD_FILE", self.doc)
        self.assertIn('EXPECTED_BASE_SHA="f665e26c5124afbf0c5c7c7aad495897a0d70176"', self.doc)
        for phrase in (
            'tag_object="$(git rev-parse "refs/tags/$TAG_NAME")"', 'test "$(git cat-file -t "$tag_object")" = tag',
            'test "$(git rev-parse "refs/tags/$TAG_NAME^{}")" = "$candidate_sha"', 'git push origin "refs/tags/$TAG_NAME"',
            'git ls-remote --tags origin', 'gh run list --workflow protected-release.yml --event push --commit "$candidate_sha"',
            'gh run view "$tag_run_id" --json workflowName,event,headSha', 'Protected release', 'PREFLIGHT_RUN_ID',
            'TAG_NAME=v0.3.1 VERSION=0.3.1',
            'gh run watch "$tag_run_id" --exit-status', 'gh run download "$tag_run_id"', 'build-receipt.json',
            'wheel="$pypi_dir/limitora-$VERSION-py3-none-any.whl"', 'sdist="$pypi_dir/limitora-$VERSION.tar.gz"',
            '--only-binary=:all:', '--no-binary=:all:', 'hashlib.sha256(path.read_bytes()).hexdigest() == receipt[key]',
            'for kind in wheel sdist; do', 'python3 -m venv "$env_dir"', 'installed_artifact_smoke.py',
            '"$python" -m pip check', 'gh release create "$TAG_NAME" --verify-tag',
        ):
            self.assertIn(phrase, self.doc)
        self.assertNotIn('--branch "$TAG_NAME"', self.doc); self.assertEqual(self.doc.count('TAG_NAME=v0.3.1 VERSION=0.3.1'), 2)
        for before, after in (("git push origin", "gh run watch"), ("gh run watch", "python3 -m pip download"), ("pip check", "gh release create")):
            self.assertLess(self.doc.rindex(before), self.doc.rindex(after))
    def test_no_go_and_scope_gates_are_explicit(self):
        for phrase in ("Missing or invalid ledger", "Existing `v0.3.1` tag", "Wrong Pending Trusted Publisher", "OIDC publication failure", "published files differ from the tag-run receipt"):
            self.assertIn(phrase, self.doc)
        self.assertIn("Issue #63 tracks", self.doc)
        self.assertIn("PR #62 and issue #61", self.doc)
        self.assertIn("approved preparation work unit", self.doc)
        self.assertIn("not published to PyPI", self.doc)
        self.assertIn("GitHub Release does not yet exist", self.doc)
    def test_candidate_and_ledger_handoff_are_explicit(self):
        self.assertIn("explicit temporary anchor", self.doc)
        self.assertIn("ledger-only trusted-main commit `T`", self.doc)
        self.assertIn("Exact reviewed release-preparation merge commit `M`", self.doc)
        self.assertIn("Do not tag", self.doc)
        self.assertIn("git show \"$trusted_main_sha:release/ledger.json\"", self.doc)
        self.assertIn("Candidate is not a strict ancestor of trusted main", self.doc)
        self.assertNotIn("release/0.1.0.md", self.doc)

    def test_candidate_merge_boundary_is_explicit_and_fail_closed(self):
        for phrase in (
            'BASE_SHA_FILE="${BASE_SHA_FILE:-/tmp/limitora-0.3.1-base-sha}"',
            'base_sha="$(git rev-parse origin/main^{commit})"',
            'printf \'%s\\n\' "$base_sha" > "$BASE_SHA_FILE"',
            ': "${PREPARATION_PR:?set PREPARATION_PR to the approved release-preparation PR number}"',
            'test -s "$BASE_SHA_FILE"',
            'base_sha="$(cat "$BASE_SHA_FILE")"',
            'test "$(gh pr view "$PREPARATION_PR" --json state --jq \'.state\')" = MERGED',
            'test "$(gh pr view "$PREPARATION_PR" --json baseRefName --jq \'.baseRefName\')" = main',
            'pr_head_sha="$(gh pr view "$PREPARATION_PR" --json headRefOid --jq \'.headRefOid\')"',
            'merge_sha="$(gh pr view "$PREPARATION_PR" --json mergeCommit --jq \'.mergeCommit.oid\')"',
            "git fetch origin main",
            'git fetch origin "pull/$PREPARATION_PR/head"',
            'test "$(git rev-parse FETCH_HEAD)" = "$pr_head_sha"',
            'test "$(git cat-file -t "$merge_sha")" = commit',
            ': "${AUTHORIZATION_PR:?set AUTHORIZATION_PR to the approved ledger-only PR number}"',
            'candidate_sha="$(git log --first-parent -1 --format=%H "$trusted_main_sha" -- release/0.3.1.md)"',
            'EXPECTED_BASE_SHA="f665e26c5124afbf0c5c7c7aad495897a0d70176"',
            'test "$candidate_sha" != "$trusted_main_sha"',
            'candidate_parents=($(git show -s --format=%P "$candidate_sha"))',
            'test "${#candidate_parents[@]}" -eq 2',
            'test "${candidate_parents[0]}" = "$EXPECTED_BASE_SHA"',
            'candidate_paths=$(',
            'git diff-tree --no-commit-id --name-only',
            '| LC_ALL=C sort',
            'test "$candidate_paths" = "$authorized_candidate_paths"',
            'test "$(gh pr view "$AUTHORIZATION_PR" --json state --jq \'.state\')" = MERGED',
            'test "$(gh pr view "$AUTHORIZATION_PR" --json baseRefName --jq \'.baseRefName\')" = main',
            'authorization_merge_sha="$(gh pr view "$AUTHORIZATION_PR" --json mergeCommit --jq \'.mergeCommit.oid\')"',
            'test "$authorization_merge_sha" = "$trusted_main_sha"',
            'authorization_head_sha="$(gh pr view "$AUTHORIZATION_PR" --json headRefOid --jq \'.headRefOid\')"',
            'test "${#parents[@]}" -eq 2',
            'test "${parents[0]}" = "$candidate_sha"',
            'test "${parents[1]}" = "$authorization_head_sha"',
            'git merge-base --is-ancestor "$authorization_head_sha" "$trusted_main_sha"',
            'git diff-tree --no-commit-id --name-only -r "$candidate_sha" "$trusted_main_sha"',
            'git show "$trusted_main_sha:release/ledger.json"',
            'test "$(git rev-parse "$candidate_sha:release/ledger.json")" != "$(git rev-parse "$trusted_main_sha:release/ledger.json")"',
        ):
            self.assertIn(phrase, self.doc)
        self.assertNotIn("MERGE_SHA", self.doc)
        self.assertNotIn("AUTHORIZATION_HEAD_FILE", self.doc)
        self.assertNotIn("```sh", self.doc)
        self.assertNotIn('git diff-tree --no-commit-id --name-only -r "$trusted_main_sha"', self.doc)
        self.assertRegex(self.doc, r"Do\s+not squash, rebase, or use a\s+synthetic merge\.")
        self.assertIn("git show \"$trusted_main_sha:release/ledger.json\"", self.doc)
        self.assertIn('"source_sha": sys.argv[1]', self.doc)
        self.assertNotIn('test "${parents[0]}" != "$base_sha"', self.doc)
        self.assertLess(
            self.doc.index('base_sha="$(git rev-parse origin/main^{commit})"'),
            self.doc.index('base_sha="$(cat "$BASE_SHA_FILE")"'),
        )

    def test_candidate_path_checks_are_exact_and_order_normalized(self):
        sections = (
            ("## Candidate Merge Boundary", "## Ledger Authorization Boundary", '"${parents[0]}" "$merge_sha"'),
            ("## Ledger Authorization Boundary", "## Protected Preflight", '"${candidate_parents[0]}" "$candidate_sha"'),
            ("## Protected Preflight", "## Annotated Tag", '"${candidate_parents[0]}" "$candidate_sha"'),
            ("## Annotated Tag", "## PyPI Verification and GitHub Release", '"${candidate_parents[0]}" "$candidate_sha"'),
        )
        for start_heading, end_heading, revision in sections:
            section = self.doc[self.doc.index(start_heading):self.doc.index(end_heading)]
            with self.subTest(section=start_heading):
                start = section.index('authorized_candidate_paths="$(')
                end_marker = 'test "$candidate_paths" = "$authorized_candidate_paths"'
                gate = section[start:section.index(end_marker, start) + len(end_marker)]
                self.assertIn(
                    f"git diff-tree --no-commit-id --name-only -r {revision} | LC_ALL=C sort",
                    gate,
                )
                passed = run_candidate_path_gate(gate, "\n".join(AUTHORIZED_CANDIDATE_PATHS[::-1]))
                self.assertEqual(passed.returncode, 0)
                rejected = run_candidate_path_gate(
                    gate, "\n".join((*AUTHORIZED_CANDIDATE_PATHS, "unexpected.txt"))
                )
                self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.doc.count('authorized_candidate_paths="$('), 4)
        self.assertNotIn('test "${#candidate_paths[@]}" -eq 8', self.doc)

    def test_authorization_diff_requires_explicit_two_tree_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            run_git(repo, "-c", "init.defaultBranch=main", "init", "-q")
            run_git(repo, "config", "user.email", "test@example.invalid")
            run_git(repo, "config", "user.name", "Release Gate Test")
            (repo / "release").mkdir()
            (repo / "release/0.3.1.md").write_text("base dossier\n", encoding="ascii")
            (repo / "tests").mkdir()
            (repo / "tests/test_release_gate.py").write_text("base test\n", encoding="ascii")
            (repo / "release/ledger.json").write_text(
                '{"schema_version": 1, "version": "0.2.0", "source_sha": "base"}\n',
                encoding="ascii",
            )
            run_git(repo, "add", ".")
            run_git(repo, "commit", "-m", "base")

            run_git(repo, "switch", "-c", "preparation")
            (repo / "release/0.3.1.md").write_text("corrected dossier\n", encoding="ascii")
            (repo / "tests/test_release_gate.py").write_text("corrected test\n", encoding="ascii")
            run_git(repo, "add", "release/0.3.1.md", "tests/test_release_gate.py")
            run_git(repo, "commit", "-m", "preparation")
            run_git(repo, "switch", "main")
            run_git(repo, "merge", "--no-ff", "--no-edit", "preparation")
            merge_sha = run_git(repo, "rev-parse", "HEAD")
            base_sha = run_git(repo, "rev-parse", "HEAD^1")

            run_git(repo, "switch", "-c", "authorization")
            (repo / "release/ledger.json").write_text(
                '{"schema_version": 1, "version": "0.3.1", "source_sha": "merge"}\n',
                encoding="ascii",
            )
            run_git(repo, "add", "release/ledger.json")
            run_git(repo, "commit", "-m", "authorize")
            authorization_head_sha = run_git(repo, "rev-parse", "HEAD")
            run_git(repo, "switch", "main")
            run_git(repo, "merge", "--no-ff", "--no-edit", "authorization")
            trusted_main_sha = run_git(repo, "rev-parse", "HEAD")

            parents = run_git(repo, "show", "-s", "--format=%P", trusted_main_sha).split()
            default_diff = run_git(
                repo, "diff-tree", "--no-commit-id", "--name-only", "-r", trusted_main_sha
            ).splitlines()
            explicit_diff = run_git(
                repo,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                merge_sha,
                trusted_main_sha,
            ).splitlines()
            derived_candidate_sha = run_git(
                repo,
                "log",
                "--first-parent",
                "-1",
                "--format=%H",
                trusted_main_sha,
                "--",
                "release/0.3.1.md",
            )
            candidate_parents = run_git(repo, "show", "-s", "--format=%P", derived_candidate_sha).split()
            candidate_diff = run_git(
                repo,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                candidate_parents[0],
                derived_candidate_sha,
            ).splitlines()

            self.assertEqual(parents, [merge_sha, authorization_head_sha])
            self.assertNotEqual(default_diff, ["release/ledger.json"])
            self.assertEqual(explicit_diff, ["release/ledger.json"])
            self.assertEqual(derived_candidate_sha, merge_sha)
            self.assertEqual(candidate_parents, [base_sha, run_git(repo, "rev-parse", "preparation")])
            self.assertEqual(candidate_diff, ["release/0.3.1.md", "tests/test_release_gate.py"])

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
