# Limitora architecture

This document describes the shipped architecture and its boundaries for the Limitora library.

## Current shape

Limitora is a plain Python library that reads provider status through explicit composition. It does not run a daemon, open a local HTTP port, or use IPC. Network, command, and authorized-session boundaries are opt-in through the selected adapter.

## Boundaries

| Layer | Responsibility | What it must NOT do |
|-------|----------------|---------------------|
| `limitora.api` | Stable typed requests, clients, and freshness results | Expose adapter or transport details |
| `limitora.core` | Coordinate detection and provider snapshot reads | Import UI frameworks or session managers |
| `limitora.models` | Define stable typed domain contracts | Depend on provider SDKs |
| `limitora.providers` | Define contracts, safe errors, cache, and adapters | Leak tokens, cookies, sessions, or credentials |
| `limitora.composition` | Validate explicit configuration and select one provider | Discover credentials or silently fall back |
| `limitora.output` / `limitora.cli` | Project results; parse flags, own streams and exits | Calculate state, instantiate adapters, or embed UI logic |

## Integration rules

- Consumers import `limitora` directly.
- UI integrations (for example, a future `yasb-limitora` package) live outside this repository.
- Limitora never imports YASB, PyQt, Waybar, or any UI integration.

## API stability

The stable public API is rooted in `limitora` and includes `StatusClient`, `StatusRequest`, freshness types, provider-neutral models, and safe provider errors. Provider modules and composition details may change as sources evolve; consumers must not depend on private adapters.

## MVP constraints

- No daemon process.
- No local HTTP server or IPC channel.
- No UI imports.
- No credential storage in the repository.
