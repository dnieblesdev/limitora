# CLI activation

The `limitora status` CLI is a **thin transport** that parses argv, owns
the stdout/stderr streams and exit codes, and delegates everything else.
It is the only entry point that maps explicit provider flags to a
validated `ProviderConfig` and activates a `StatusClient` through the
composition root.

## Architecture at a glance

```
argv  --parse-->  CliIntent  --intent_to_config-->  ProviderConfig
                                                          |
                                            activate_provider (composition)
                                                          |
                                            build_status_client (composition)
                                                          |
                                            StatusClient.read_status
                                                          |
                                       +------- --json? -------+
                                       v                        v
                                render_json              render_human
                                (stdout)                 (stdout)
```

| Layer | Responsibility | What it MUST NOT do |
|-------|----------------|---------------------|
| `limitora.cli` | Parse argv, own streams and exit codes, build `CliIntent`, call `activate_provider`, route results to `render_human` / `render_json` | Import `argparse`, `subprocess`, `os.environ`, `pathlib`, or `StatusProvider`; instantiate adapters; import `_codex_jsonl` / `_opencode_go_httpx`; inspect credentials; apply cache policy; define presentation strings |
| `limitora.composition` | Validate the closed `ProviderConfig` union, expose `activate_provider`, lazily import private adapter modules | Be called with a `cache_policy`; pre-import `httpx` |
| `limitora.output` | Project typed results into deterministic strings (human or JSON v1) | Import `limitora.cli`; leak credentials, tracebacks, or `__cause__` |

`limitora.output` is intentionally **not** re-exported from `limitora`;
the closed `__all__` invariant in `src/limitora/__init__.py` enforces it.
The CLI imports `render_human` and `render_json` from `limitora.output`.

## Flag grammar

Only space-separated `--key value` is supported for Limitora flags.
`--key=value` is rejected as a usage error (exit 2 stderr). Opaque
runner arguments consumed by `--runner` may contain `=`. All flags live
under the single `status` subcommand.

```
limitora status [--help] [--json] [--provider {codex,opencode-go}] [flags]
```

| Global | Description |
|--------|-------------|
| `--help` | Print human-readable help. Wins over `--json`. Exit 0. Stream: stdout (no `--json`) / stderr (with `--json`). |
| `--json` | Render the result as a JSON v1 document. Stream: stdout. |
| `--provider {codex,opencode-go}` | Select the provider. Required for activation. |

| Codex flags | Description |
|-------------|-------------|
| `--runner PATH` | Repeatable. A single native absolute path is shorthand for `PATH app-server --stdio`. Two or more values are preserved exactly. Each part is a non-empty stripped token and the first part must be absolute for the host platform. |
| `--codex-allow-authorized-source` | Opt in to `ALLOW_AUTHORIZED_SOURCE`. Default is `DENY_AUTHORIZED_SOURCE`. |

| OpenCode Go flags | Description |
|-------------------|-------------|
| `--workspace-id ID` | Required. Non-empty stripped id. |
| `--auth-cookie COOKIE` | Required. Non-empty cookie value. |
| `--endpoint URL` | Default `https://opencode.ai`. Must match exactly. |
| `--timeout SECONDS` | Default 10. Positive integer ≤ 10. |
| `--opencode-allow-authorized-source` | Opt in to `ALLOW_AUTHORIZED_SOURCE`. Default is `DENY_AUTHORIZED_SOURCE`. |

Unknown flags, missing values, duplicate single-cardinality flags,
unexpected positionals, and cross-provider flags all return exit 2 with a
usage message on stderr.

### Codex runner shorthand

The CLI expands exactly one native absolute runner value. POSIX hosts accept
POSIX absolute paths; Windows hosts accept drive-qualified and complete UNC
paths while rejecting POSIX, drive-relative, rooted-without-drive, incomplete
UNC, and device-namespace paths:

```text
--runner /usr/bin/codex
```

into `("/usr/bin/codex", "app-server", "--stdio")` on POSIX. A relative or
non-native single value is not expanded or resolved; composition validation
still requires the first runner token to be native-absolute.

Repeat `--runner` to provide explicit argv. Every value is preserved:

```text
--runner /usr/bin/codex --runner app-server --runner --stdio
```

Opaque values beginning with `--`, such as `--stdio`, are accepted only
as runner arguments when they are not known Limitora flags. For example,
`--runner --json` and `--runner --provider` are missing-value collisions,
not runner arguments. The `CodexJsonlConfig` library contract does not
apply this shorthand: a directly constructed one-token runner remains a
one-token runner through composition and transport.

## Stream and exit-code policy

| Outcome | Human stream | `--json` stream | Exit |
|---------|--------------|-----------------|------|
| Help | stdout (no `--json`) / stderr (with `--json`) | (unchanged) | 0 |
| Ok snapshot | stdout | stdout | 0 |
| Stale snapshot | stdout | stdout | 3 |
| Undetected | stdout | stdout | 0 |
| `ProviderError` | stderr | stdout (JSON envelope) | 5 |
| Usage error | stderr | stderr | 2 |
| Unconfigured (no `--provider` and no `client_factory`) | stderr (`ERROR: no provider configured\n`) | stderr | 4 |
| `CompositionError` | stderr (redacted `safe_message`) | stderr | 2 |

`--help` wins over `--json` so the help text is always human-readable.
The `["status"]` no-flags path is preserved byte-for-byte: stderr gets
exactly `ERROR: no provider configured\n` and the process exits 4.

## Unconfigured behavior

When no `--provider` is given and no `client_factory` is injected, the
CLI is side-effect-free: it prints the documented unconfigured message
to stderr and exits 4. `--provider codex` and `--provider opencode-go`
are the only opt-ins to activation. There is no implicit default
provider, no env or config-file discovery, and no `os.environ` reads.

## `activate_provider` boundary

```python
def activate_provider(
    config: ProviderConfig, *, enabled: bool = True, clock: Clock | None = None,
) -> StatusClient: ...
```

- Is implemented in `limitora.composition` and exported from the stable `limitora` root with the closed config and composition error types.
- The **only** module that imports `_codex_jsonl` and
  `_opencode_go_httpx`.
- Dispatches on the `ProviderConfig` discriminator and constructs the
  matching `ProviderDependencies` (clock + adapter factory) before
  delegating to `build_status_client`.
- Both adapter imports are lazy; the module can be loaded without a
  working `subprocess` environment (Codex) and without the optional
  `httpx` dependency installed (OpenCode Go).
- The `_HttpxOpenCodeGoTransport` constructor does not touch `httpx`;
  the lazy import stays inside `.fetch()` so the CLI works in
  environments where `httpx` is absent.
- Does **not** accept `cache_policy`. Cache stays a library concern,
  not a presentation concern.

The CLI calls `activate_provider` exclusively. The CLI never imports
`_codex_jsonl` or `_opencode_go_httpx` directly, never instantiates
`CodexProvider` or `OpenCodeGoProvider`, never inspects credentials, and
never executes runners. This boundary is enforced by a contract test in
`tests/test_provider_composition.py::ActivateProviderTests`.

## Privacy guarantees

| Rule | Enforcement |
|------|-------------|
| CLI source contains no `argparse`, `subprocess`, `import os`, `pathlib`, or `StatusProvider` substring | `tests/test_cli.py::PrivacyContractTests::test_cli_source_excludes_privacy_forbidden_symbols` |
| Captured output (stdout ∪ stderr) contains no `secret`, `__cause__`, `Traceback`, or `auth=` substring for any argv shape | `tests/test_cli.py::PrivacyContractTests` |
| JSON `error` envelope carries only `kind`, `provider_id`, `safe_message`, `retryable` | `tests/test_output.py::ErrorSanitizationTests` and `tests/test_cli.py::JsonRoutingTests` |
| Auth cookie never appears in any captured stream for the OpenCode Go path (default DENY, ALLOW, and `--json`) | `tests/test_cli.py::PrivacyContractTests` |
| Composition `safe_message` is a redacted constant; credentials and provider payloads are never echoed | `tests/test_provider_composition.py::test_errors_are_constant_and_redacted` |
| Config and intent representations omit `workspace_id` and `auth_cookie`; HTTP request representations omit URL, headers, and body | `tests/test_public_library_api.py`, `tests/test_cli.py`, and `tests/test_opencode_go_httpx.py` |

The CLI is the only module that touches argv. The `intent_to_config`
mapper is a pure data function: no I/O, no logging, no printing.
Consumers own environment access and pass credentials explicitly;
Limitora performs no environment lookup. Cookies and runners reach the transport only through the private
adapter modules, which place them in `Cookie:` request headers or
subprocess argv — never in user-visible output.

## Authorization policy

| Provider | Default | Opt-in flag |
|----------|---------|-------------|
| Codex | `DENY_AUTHORIZED_SOURCE` | `--codex-allow-authorized-source` |
| OpenCode Go | `DENY_AUTHORIZED_SOURCE` | `--opencode-allow-authorized-source` |

A user that supplies `--auth-cookie` but forgets the corresponding
`--allow-authorized-source` opt-in sees a `KIND: unauthorized` error
(redacted). There is no implicit allow based on credential presence.

## Test matrix

| Layer | File | Coverage |
|-------|------|----------|
| Parser (unit) | `tests/test_cli.py::ParseUnitTests`, `InvalidGrammarTests` | Every row of the grammar matrix; precedence order; defaults; cross-flag; duplicate; missing-value; unexpected-positional; `--key=value` rejection |
| Mapper (unit) | `tests/test_cli.py::IntentToConfigUnitTests` | `CliIntent` → `ProviderConfig` for both providers; no I/O; raises `CompositionError` when no provider is set |
| Composition (unit) | `tests/test_provider_composition.py::ActivateProviderTests` | `activate_provider` constructs the right dependency factory for each provider; no I/O at construction; injected clock; default `CurrentClock`; httpx not pre-imported; sole-importer contract; third config → `INVALID` |
| CLI integration | `tests/test_cli.py::CodexActivationTests`, `JsonRoutingTests`, `HelpAndUnconfiguredTests`, `RendererRegressionTests` | Codex happy path; authz default+opt-in; `--json` ok / stale / undetected / error envelope; `--help` precedence over `--json`; unconfigured preserved byte-for-byte |
| OpenCode end-to-end | `tests/test_opencode_go_composition.py::OpenCodeGoCompositionTests` | argv → `activate_provider` → `read_status` → stdout JSON; human mode; default DENY produces UNAUTHORIZED before transport |
| Privacy (contract) | `tests/test_cli.py::PrivacyContractTests`, `tests/test_provider_composition.py::ProviderCompositionTests::test_errors_are_constant_and_redacted` | Source scan; stream scan; redacted messages; auth-cookie-never-leaks across all argv shapes |

## Chained PRs (WU1 + WU2)

This slice was split into two stacked work units to keep each PR under
the 400-line review budget. The chain strategy is `stacked-to-main`:
each PR targets `main` after the previous one merges.

| WU | Scope | PR |
|----|-------|-----|
| 1 | IR types, full grammar parser, `intent_to_config`, `activate_provider` (Codex branch + INVALID fallthrough for OpenCode), `main` orchestration with `--json` / `--help` / unconfigured / `CompositionError` routing, rewritten `_HELP`, grammar + Codex + JSON + privacy tests | PR 1 → `main` |
| 2 | Fill `activate_provider` OpenCode branch (lazy `_HttpxOpenCodeGoTransport`); OpenCode end-to-end CLI tests; auth-cookie privacy tests; this document | PR 2 → `main` |

**Grammar is frozen at the end of WU1.** WU2 does not modify the parser,
the IR types, or the flag grammar. The intermediate state in WU1 was
`--provider opencode-go …` parsing validly but `activate_provider`
raising `CompositionError(INVALID)` (exit 2 stderr). WU2 enables it.

## Non-goals

- `--json-pretty` formatting knob.
- Cache policy CLI flag (library-only).
- Environment or config-file discovery (`os.environ`, `.atl/`).
- `argparse` migration.
- Additional providers beyond Codex and OpenCode Go.
