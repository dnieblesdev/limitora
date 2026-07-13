# Limitora architecture

This document describes the direction and boundaries for the Limitora library.

## Direction

Limitora is a plain Python library that talks to LLM providers. It does not run a daemon, open a local HTTP port, or use IPC in the first MVP. Later versions may add direct import-based integrations, but any network or process boundary will be opt-in and documented.

## Boundaries

| Layer | Responsibility | What it must NOT do |
|-------|----------------|---------------------|
| `limitora.core` | Coordinate requests, enforce rate limits, manage provider selection | Import UI frameworks or session managers |
| `limitora.models` | Define stable request/response contracts | Depend on provider SDKs |
| `limitora.providers` | Adapt provider-specific APIs to Limitora models | Leak tokens, cookies, sessions, or credentials |
| `limitora.cache` | Optional local persistence with redaction first | Store unredacted secrets |
| `limitora.cli` | Thin command-line wrapper that emits JSON | Embed UI logic |

## Integration rules

- Consumers import `limitora` directly.
- UI integrations (for example, a future `yasb-limitora` package) live outside this repository.
- Limitora never imports YASB, PyQt, Waybar, or any UI integration.

## API stability

The first stable public API will be defined in `limitora.core` and `limitora.models`. Provider modules may change as provider APIs evolve; consumers should go through the core coordinator rather than calling provider modules directly.

## MVP constraints

- No daemon process.
- No local HTTP server or IPC channel.
- No UI imports.
- No credential storage in the repository.
