# OpenCode live driver

This is an opt-in local operator check, not a CI test and not a provider
integration in the library. It invokes exactly the selected installed Limitora
CLI:

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
