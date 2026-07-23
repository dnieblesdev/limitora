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
| `limitora.composition` | Implement the root-exported construction types and activate one provider lazily | Discover credentials, expose dependencies/transports, or silently fall back |
| `limitora.output` / `limitora.cli` | Project results; parse flags, own streams and exits | Calculate state, instantiate adapters, or embed UI logic |

## Integration rules

- Consumers import `limitora` directly.
- Consumers own environment and configuration access, pass provider values explicitly, and retain the returned `StatusClient` for reuse.
- UI integrations (for example, a future `yasb-limitora` package) live outside this repository.
- Limitora never imports YASB, PyQt, Waybar, or any UI integration.

## API stability

The stable public API is rooted in `limitora` and includes `StatusClient`, `StatusRequest`, freshness types, provider-neutral models, safe provider errors, and the closed construction contract (`CodexJsonlConfig`, `OpenCodeGoConfig`, `ProviderConfig`, `activate_provider`, `CompositionError`, and `CompositionErrorKind`). Dependency types, provider modules, adapters, and transports remain internal.

Importing `limitora` does not load provider implementations, `subprocess`, or the optional `httpx` dependency. `activate_provider` loads only the selected implementation and returns one reusable client; it does not aggregate providers. OpenCode Go's `workspace_id` and `auth_cookie` are sensitive in-memory inputs and are excluded from Limitora-controlled representations.

## MVP constraints

- No daemon process.
- No local HTTP server or IPC channel.
- No UI imports.
- No credential storage in the repository.
