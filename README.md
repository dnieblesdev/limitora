# Limitora

Limitora is a Python library for provider-agnostic LLM interaction with rate limiting and safe local integration. It is being built so scripts and small tools can talk to code-generation providers without embedding UI-specific logic or leaking secrets.

> **Current status**: scaffolding and research. No public API is defined yet.

## Problem

Local scripting tools that call LLM providers often end up tied to a specific editor, desktop widget, or GUI framework. That coupling makes them hard to test, hard to reuse, and risky to extend. Limitora keeps the provider conversation in a plain Python library so integrations can be thin and optional.

## Planned architecture

```text
┌─────────────────────────────────────┐
│           consumers / CLI           │
├─────────────────────────────────────┤
│  limitora.cli (future)              │
│  limitora.core                      │
│  limitora.models                    │
├─────────────────────────────────────┤
│  limitora.providers                 │
│    ├── codex                        │
│    ├── opencode-go                  │
│    ├── claude (future)              │
│    └── gemini (future)              │
├─────────────────────────────────────┤
│  limitora.cache (future)            │
└─────────────────────────────────────┘
```

- `core` will hold the coordinator, rate-limit logic, and request lifecycle.
- `models` will define stable data contracts.
- `providers` will contain one adapter per LLM service.
- `cache` will hold optional, local, redact-first persistence helpers.
- `cli` will be a thin command-line interface that emits JSON.

Limitora never imports YASB, PyQt, Waybar, or any UI integration.

## Provider support

| Phase | Provider | Status |
|-------|----------|--------|
| 1 | Codex | planned |
| 1 | OpenCode Go | planned |
| 2 | Claude | future |
| 2 | Gemini | future |

## Public API

The public API is not defined yet. The first stable surface will live in `limitora.core` and `limitora.models`; provider modules will be imported directly only when a consumer explicitly chooses a backend.

## Planned CLI / JSON output

A future `limitora` CLI will accept subcommands and emit structured JSON. No CLI logic exists in this scaffold.

## Security and privacy

Never store tokens, cookies, sessions, credentials, or provider cache data unredacted in this repository. Diagnostic dumps must be redacted before sharing. Redacted artifacts may use the names `*.redacted.json` or `*.redacted.txt`.

## Roadmap

1. Finalize core models and provider contract.
2. Implement Codex and OpenCode Go providers.
3. Add rate-limiting helpers.
4. Add the JSON CLI.
5. Evaluate Claude and Gemini providers.

## Future relation to yasb-limitora

A separate `yasb-limitora` integration may consume this library to connect YASB to LLM providers. That integration will live in its own repository and import Limitora as a normal Python dependency. Limitora itself will remain UI-free.
