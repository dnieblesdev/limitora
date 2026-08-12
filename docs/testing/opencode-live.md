# OpenCode live compatibility

The local driver remains an opt-in operator check. WU4B adds the manual GitHub
Actions workflow at `.github/workflows/opencode-live.yml`; it is not a provider
integration in the library and it never runs automatically.

## Local driver

The driver invokes exactly the selected installed Limitora CLI:

```text
python scripts/opencode_live_driver.py --confirm RUN --cli /absolute/path/to/limitora
python scripts/opencode_live_driver.py --confirm RUN --cli /absolute/path/to/limitora --dotenv /absolute/path/to/local.env
```

The optional dotenv file must be UTF-8, contain only the two required exact
assignments, and have owner-only POSIX permissions. On Windows, the operator
must enforce an owner-only ACL. Quotes, backslashes, dollar signs, and shell
punctuation are literal; there is no expansion or interpolation. `.env` is
ignored by the repository and must never be committed.

The process environment is authoritative. Matching dotenv values are accepted;
conflicts, missing values, malformed input, unsafe paths, oversized dotenv files,
and wrong confirmation return the constant `preflight` result. The child receives
only the two required OpenCode inputs and `PYTHONNOUSERSITE=1`; it never inherits
CI tokens, proxy settings, Python path overrides, or unrelated caller variables.
Shell execution is disabled, stderr is discarded, runtime and stdout are bounded,
and no secret, path, quota, account, or provider body is rendered. POSIX execution
uses a new process group and bounded group cleanup on timeout or output overflow.
Windows fails closed before live execution because equivalent descendant cleanup is
not provided by this driver.

The single output line is `OpenCode live result: <classification>`. Codes are
`0` success, `10` preflight, `20` authentication, `21` schema drift, `22` rate
limited, `23` source unavailable, `24` transport, `25` unexpected regression,
`26` generic provider parse failure, `27` unsupported provider capability, `29`
no valid quota window, `30` invalid UTF-8 JSON, `31` non-object JSON root, and
`32` HTML document response. The `29` classification is the existing exact
no-valid-window message; `30` through `32` are the new parse-cause refinements.
Malformed or structurally invalid upstream data remains `schema_drift`; a
validated provider error kind of `parse_failed` or `unsupported` is classified
separately. For `opencode-go`, only three exact producer-owned parse messages
are refined to `parse_failed_invalid_utf8_json`, `parse_failed_non_object_json`,
or `parse_failed_html_document`; unknown parse messages remain `parse_failed`.
No upstream payload content or message text is rendered or retained. Run the
offline contract tests with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_opencode_live_driver -v`.

## Manual GitHub Actions run

1. Before storing either secret or running the workflow, create and configure a
   protected GitHub Environment named `opencode-live` in repository or
   organization settings, including required reviewers.
2. Store these two secrets only after that protected Environment is configured:
   `LIMITORA_OPENCODE_WORKSPACE_ID` and `LIMITORA_OPENCODE_AUTH_COOKIE`.
3. Open **Actions**, select **OpenCode live compatibility**, and choose
   **Run workflow** on the `main` branch.
4. Enter `RUN` exactly in the `confirmation` input. Any other value fails before
   the live step.
5. Start the workflow and inspect only the live driver's constant classification.

The workflow checks out the selected target commit and requires the producing
protected-release run ID, artifact ID, and artifact digest for that exact SHA. It
verifies the GitHub artifact service digest, protected workflow identity, receipt
fields, repository-bound SHA-256 manifest, and the checked-in distribution
contract before any secret is mapped. The verified wheelhouse is the only package
source (`--no-index`); the installed wheel is checked with
`scripts/verify_distributions.py` and then installed non-editably into a new
virtual environment under `RUNNER_TEMP`. The import-location assertion proves
that `limitora` resolves from that environment rather than the checkout. Python
path variables are cleared for verification and live invocation. CI does not
create or load `.env` files and does not pass `--dotenv`.

The live step emits one line only: `OpenCode live result: <classification>`.
The classifications are `success_snapshot`, `preflight`, `authentication`,
`schema_drift`, `parse_failed`, `unsupported`, `parse_failed_no_valid_quota_window`,
`parse_failed_invalid_utf8_json`, `parse_failed_non_object_json`,
`parse_failed_html_document`, `rate_limited`, `source_unavailable`, `transport`,
and `unexpected_limitora_regression`. A success means the installed CLI returned
the driver's validated v1 fresh OpenCode snapshot envelope. It does not persist
provider payloads, quota values, credentials, artifacts, or a GitHub step
summary. A success also requires non-empty structural commercial-quota window
evidence. The driver does not persist quota/account values or `safe_message`,
credentials, artifacts, or a GitHub step summary. It compares only the exact
allowlisted OpenCode parse messages after envelope validation. Missing or
empty secrets fail preflight; missing, empty, or malformed quota windows are
`schema_drift`; provider error kinds `parse_failed` and `unsupported` use their
own classifications; the existing no-valid-window message and the three new
known OpenCode parse causes use constant classifications; every non-success
exit code fails the workflow.

The workflow declares the `opencode-live` Environment and runs only when
`github.ref` is exactly `refs/heads/main`. GitHub applies the Environment's
configured protection rules before the live step. The workflow does not create
or enforce required-reviewer rules; maintainers must configure them in
repository or organization settings before storing secrets or running it.

Issue #18 remains open until one successful manual run is completed on the
target commit and routine installed wheel/sdist CI evidence exists.
