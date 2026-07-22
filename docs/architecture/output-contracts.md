# Output Contracts

`limitora.output` is a **projection layer**, not a contract surface.
The typed Python API (`limitora.api`, `limitora.models`,
`limitora.providers`) remains the primary boundary; output is
generated from those typed values at the process edge.

## Scope

- **In scope**: deterministic JSON v1 envelopes, human-readable CLI
  string, shared timestamp helper, absence rules, sanitized error
  envelopes, the `output ∉ limitora.__all__` invariant, and the seam
  between the CLI and the projection module.
- **Out of scope**: the `--json` CLI flag, provider activation, JSON
  v2+, and any change to the typed Python API.

## Module boundaries

| Module | Responsibility | What it MUST NOT do |
|--------|----------------|---------------------|
| `limitora.output` | Project public result types into deterministic strings | Import `limitora.cli`; depend on provider transport, ports, or session material; store credentials or raw payloads |
| `limitora.cli` | Argument parsing, stream ownership, exit codes, delegation to `render_human` | Define presentation strings, instantiate providers, calculate global state, apply cache policy |

The CLI imports `render_human` from `limitora.output` and forwards
snapshot, undetected, and error results to it. The CLI defines no
`_render_*` helpers, no `_timestamp` / `_optional` / `_quantity`
helpers, and no presentation strings. The seam is verified by
`tests/test_cli.py`.

`limitora.output` is intentionally **not** re-exported from
`limitora`. The closed `__all__` list in `src/limitora/__init__.py`
enforces this; the invariant is locked by
`tests/test_public_library_api.py`.

## Public projection API

```python
from limitora.output import render_json, render_human, isoformat_utc

JSONContractVersion = 1
```

`render_json(result, *, version=1)` and `render_human(result)` accept
the union `StatusSnapshotResult | StatusUndetectedResult |
ProviderError` and dispatch on type. `isoformat_utc` is the only
timestamp helper; both projections share it so human and JSON never
drift, and a single timestamp encoding is enforced across both.

## JSON v1 schema

Every payload begins with `"version": 1` as the first key. The
remaining keys are deterministically sorted (recursive `sort_keys`
semantics, with `"version"` promoted to the front). The schema is
enforced by `tests/test_output.py`:

- **Snapshot** — `{"version", "result": "snapshot", "provider_id":
  {"value":..}, "freshness", "status": {"state", "observed_at"},
  "fetched_at", "data_at", "source": {"reference":..},
  "quota_windows": [..], "rate_limit_reset_credits": ..|null, "usage": ..|null}`.
- **Window** — `kind`, `scope`, `period`, `plan_id` (nullable),
  `availability`, `source.reference`, `limit`/`used`/`remaining` (each
  `{"value", "metric", "unit"}` or `null`), `reset_at` (or `null`).
- **Usage** — `observed_at`, `availability`, `source.reference`,
  `token_limit`/`token_used`/`balance` (each quantity or `null`).
- **Reset credits** — `available_count` and nullable ordered `credits`; each detail contains `reset_type`, `status`, `granted_at`, nullable `expires_at`, `title`, and `description`. No provider credit identifier is emitted.
- **Undetected** — `{"version": 1, "result": "undetected"}`.
- **Error** — `{"version": 1, "error": {"kind", "provider_id":
  {"value":..}, "safe_message", "retryable"}}`.

## Encoding rules

| Field / shape | Rule |
|---------------|------|
| `version` | First key in every payload; integer `1` |
| Timestamps | ISO-8601 UTC with `Z` suffix; `isoformat_utc` is the only helper |
| `Decimal` quantities | JSON **string** (`str(Decimal)`) to preserve precision |
| Enum values | `enum.value` (always `str, Enum` members) |
| Value objects | `ProviderId → {"value": ..}`, `SourceMetadata → {"reference": ..}` |
| Key ordering | `sort_keys` semantics after promoting `"version"` to the front |
| Determinism | `render_json` is byte-identical across calls for the same input |

## Absence rules

A single, uniform absence rule applies across all envelope shapes
and is locked by per-fixture contract tests.

| Condition | JSON encoding |
|-----------|---------------|
| Nullable scalar (`plan_id`, `limit`, `used`, `remaining`, `reset_at`, `token_limit`, `token_used`, `balance`, `usage`) | `null` (the field is **present**, not omitted) |
| Empty collection (`quota_windows` when there are no windows) | `[]` |
| Reset-credit details unavailable / fetched empty | `credits: null` / `credits: []` |
| Undetected result | `{"version": 1, "result": "undetected"}` (typed envelope, never `null`, never the snapshot schema) |
| Error result | `{"version": 1, "error": {...}}` (typed envelope, never the snapshot schema) |

The rule applies uniformly to JSON and human projections. The human
projection emits the literal `unavailable` for absent scalars and
`QUOTA_WINDOWS: unavailable` / `USAGE: unavailable` for absent
collections.
Reset-credit human output distinguishes unavailable details from a known empty list and escapes display text so control characters cannot inject fields or lines.

## Sanitization rules

The error envelope is the only path by which provider failures reach
the output boundary. The projection is **defense-in-depth** — provider
adapters already raise sanitized `ProviderError` instances, but the
projection must not regress that contract. The error envelope carries
only `kind`, `provider_id`, `safe_message`, and `retryable`; never
`__cause__`, traceback text, raw adapter payload, credentials, or
session material. A contract test
(`tests/test_output.py::ErrorSanitizationTests`) scans the rendered
output for `secret`, `secret-credential`, `RuntimeError`,
`__cause__`, and `Traceback` to prove sanitization holds.

## Human projection (CLI)

`render_human` produces the same string the legacy CLI renderers
produced. The CLI is a thin transport that delegates every render to
`render_human`; the CLI module defines no presentation strings of its
own. The byte-identity contract is locked by
`tests/test_output.py::RenderHumanByteIdentityTests` and the existing
`tests/test_cli.py` assertions, which run unmodified
post-relocation.

## Projection, not contract

Output is a **projection** of the typed public API, not the API
itself. A consumer that depends on JSON fields, key order, specific
string formats, or undocumented absence behavior is depending on a
projection that may evolve. Any breaking change to the JSON shape
requires a new version field (`"version": 2`) and a parallel
`render_json` dispatch path; `JSONContractVersion` is the source of
truth. The typed Python API is the contract. Everything that crosses
the output boundary is generated from those typed values at the
moment of projection. The projection deliberately does not invent,
fill, or coerce absent fields, and it does not expose
`StatusProvider`, `Clock`, port failures, or adapter diagnostics.
