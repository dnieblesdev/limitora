# Limitora

Limitora is a typed Python library for provider-agnostic quota and status observations. It keeps provider adapters, composition, caching, output projection, and the CLI behind explicit boundaries so scripts and small tools can inspect status without embedding UI logic or leaking secrets.

> **Current status**: version `0.1.0` ships the typed public API, deterministic output projections, a provider-aware `limitora status` CLI, Codex JSONL support, and opt-in OpenCode Go support.

## Install and typed quick path

```bash
python -m pip install -e .
```

The public API can be used without invoking a provider:

```python
from datetime import timedelta
from limitora import AuthorizationPolicy, FreshnessPolicy, MetricKind, StatusRequest

request = StatusRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
    FreshnessPolicy(timedelta(minutes=5)),
)
print(request.requested_metrics)
```

Provider reads require an explicit `StatusClient` or composition boundary; provider calls are never implicit.

## Problem

Local scripting tools that call LLM providers often end up tied to a specific editor, desktop widget, or GUI framework. That coupling makes them hard to test, hard to reuse, and risky to extend. Limitora keeps the provider conversation in a plain Python library so integrations can be thin and optional.

## Architecture at a glance

```text
┌─────────────────────────────────────┐
│           consumers / CLI           │
├─────────────────────────────────────┤
│  limitora.cli / limitora.output     │
│  limitora.composition / limitora.api│
│  limitora.core / limitora.models    │
├─────────────────────────────────────┤
│  limitora.providers                 │
│    ├── codex                        │
│    ├── opencode-go                  │
│    └── explicit provider adapters   │
├─────────────────────────────────────┤
│  limitora.providers.cache           │
└─────────────────────────────────────┘
```

- `api` and `models` define the stable typed consumer boundary.
- `core` coordinates detection and snapshot reads; `composition` selects one explicit provider.
- `providers` owns contracts and private adapters; `cache` is opt-in, in-memory reuse.
- `output` projects typed results to JSON v1 or human text; `cli` owns parsing, streams, and exits.

Limitora never imports YASB, PyQt, Waybar, or any UI integration.

## Provider support

| Provider | Status | Boundary |
|----------|--------|----------|
| Codex | shipped | Explicit Codex JSONL adapter; authorized source is opt-in. |
| OpenCode Go | shipped, opt-in | Explicit dashboard adapter; authorization and endpoint behavior are qualified. |
| Claude / Gemini | not shipped | No adapter or support promise. |

## Public API

The stable root surface includes `StatusClient`, `StatusRequest`, freshness types, provider-neutral models, and safe provider errors. Provider transport, parser, session, and credential details are not public API.

The consuming application owns environment or configuration access and passes values explicitly; Limitora does not read credentials from the environment. Treat both `workspace_id` and `auth_cookie` as sensitive. Limitora-controlled representations omit them, and request representations omit workspace-bearing URLs, headers, and bodies.

## CLI status

`limitora status` supports `--json`, `--help`, and explicit `--provider codex|opencode-go` activation. Without a provider it performs no provider I/O and reports `ERROR: no provider configured` on stderr with exit code 4. Routing is documented in [`cli-activation.md`](docs/architecture/cli-activation.md).

## Security and privacy

Never store tokens, cookies, sessions, credentials, or provider cache data unredacted in this repository. Diagnostic dumps must be redacted before sharing. Redacted artifacts may use the names `*.redacted.json` or `*.redacted.txt`.

## Roadmap status

The original provider-status roadmap is **concluded/historical** for the shipped baseline. Current implementation evidence is in the source and tests; future provider work is not promised.

1. ✅ Typed domain, provider contract, orchestration, and public API.
2. ✅ Codex and OpenCode Go adapters with explicit composition.
3. ✅ In-memory cache and deterministic JSON v1/human projections.
4. ✅ Explicit CLI activation with safe, documented failure boundaries.
5. ⏸ Claude and Gemini remain unimplemented evaluation items.

## Future relation to yasb-limitora

A separate `yasb-limitora` integration may consume this library to connect YASB to LLM providers. That integration will live in its own repository and import Limitora as a normal Python dependency. Limitora itself will remain UI-free.
