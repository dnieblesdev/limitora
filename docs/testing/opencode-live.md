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
conflicts, missing values, malformed input, unsafe paths, and wrong confirmation
return the constant `preflight` result. The child receives a copied environment
with `PYTHONPATH` and `PYTHONHOME` removed and `PYTHONNOUSERSITE=1`. Shell
execution is disabled, stderr is discarded, runtime and stdout are bounded, and
no secret, path, quota, account, or provider body is rendered.

The single output line is `OpenCode live result: <classification>`. Codes are
`0` success, `10` preflight, `20` authentication, `21` schema drift, `22` rate
limited, `23` source unavailable, `24` transport, and `25` unexpected regression.
Malformed or stale upstream data is never treated as success. Run the offline
contract tests with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_opencode_live_driver -v`.

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

The workflow checks out the selected target commit, builds a fresh wheel, and
installs it non-editably into a new virtual environment under `RUNNER_TEMP`.
The import-location assertion proves that `limitora` resolves from that
environment rather than the checkout. Python path variables are cleared for
verification and live invocation. CI does not create or load `.env` files and
does not pass `--dotenv`.

The live step emits one line only: `OpenCode live result: <classification>`.
The classifications are `success_snapshot`, `preflight`, `authentication`,
`schema_drift`, `rate_limited`, `source_unavailable`, `transport`, and
`unexpected_limitora_regression`. A success means the installed CLI returned
the driver's validated v1 fresh OpenCode snapshot envelope. It does not persist
provider payloads, quota values, credentials, artifacts, or a GitHub step
summary. Missing or empty secrets fail preflight; every non-success exit code
fails the workflow.

The workflow declares the `opencode-live` Environment and runs only when
`github.ref` is exactly `refs/heads/main`. GitHub applies the Environment's
configured protection rules before the live step. The workflow does not create
or enforce required-reviewer rules; maintainers must configure them in
repository or organization settings before storing secrets or running it.

Issue #18 remains open until one successful manual run is completed on the
target commit and routine installed wheel/sdist CI evidence exists.
