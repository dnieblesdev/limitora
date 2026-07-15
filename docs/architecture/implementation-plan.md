# Provider-status implementation roadmap

**Decision:** sequence implementation from pure domain behavior through deterministic fake and JSON contracts before considering either provider. Each slice remains independently reviewable and reversible; no provider access or implementation is authorized by this document.

## Ordered slices

### 1. Domain core

- **Purpose/description:** Implement the domain models, window and status semantics, percentage guards, and global aggregation with explicit exclusions.
- **Points to address:** Model `ProviderStatus`, `QuotaWindow`, `ProviderSnapshot`, `UsageSnapshot`, source metadata, absence states, freshness, and aggregate inclusion/exclusion reasons. Preserve the invariant that commercial quota, technical rate limits, token usage, balances, and percentages are distinct and cannot be conflated.
- **Out of scope:** Providers, transport, commands, local files, serialization, cache, CLI, UI, and configuration.
- **Tests:** Construct valid and invalid models; cover absent values, compatible percentage derivation, incompatible kinds/scopes/windows, stale observations, and every aggregate exclusion rule.
- **Acceptance criterion:** The domain represents known and absent evidence unambiguously; a technical rate limit cannot populate commercial-quota data, and any excluded requested provider makes an aggregate `partial`.
- **Risks:** Premature convenience fields could hide incompatible provider semantics or turn absence into numeric zero.

### 2. Provider foundation

- **Purpose/description:** Establish typed errors, the provider protocol, injected side-effect ports, deterministic fakes, and offline contract tests.
- **Points to address:** Define safe error and redaction rules; inject narrow HTTP, command, filesystem, clock, and timeout-policy dependencies; keep authorization explicit; provide fake providers and fixed-clock fixtures.
- **Out of scope:** Concrete provider strategies, real I/O, authentication discovery, provider selection, and persistence.
- **Tests:** Run provider-contract scenarios entirely with fakes for complete, partial, unauthorized, unsupported, malformed, timeout, and technical-rate-limited outcomes; assert no raw credentials or payloads escape diagnostics.
- **Acceptance criterion:** Contract tests are deterministic and offline, and a reader returns only validated snapshots or typed, redacted errors.
- **Risks:** Ports can become too broad by mirroring third-party clients; broad diagnostics can bypass redaction.

### 3. Output contract

- **Purpose/description:** Define JSON serialization as the stable consumer boundary and prove compatibility using fake-provider snapshots.
- **Points to address:** Serialize status, absence reasons, metric kind, window/reset semantics, source metadata, aggregate exclusions, and freshness without inventing numeric values.
- **Out of scope:** CLI presentation, YASB UI, real providers, persistent storage, and a public network API.
- **Tests:** Use fakes to verify JSON for available, partial, stale, unavailable, unauthorized, invalid, and technical-rate-limited snapshots; retain the distinction among quota, rate limits, token usage, balances, and derived percentages.
- **Acceptance criterion:** A consumer can distinguish known, partial, stale, unavailable, and error states from JSON without provider internals or ambiguous zero values.
- **Risks:** Serialization can become an accidental public API; version compatibility deliberately and do not flatten incompatible metrics.

### 4. Codex provider

- **Purpose/description:** Evaluate one bounded initial Codex observation strategy behind the established provider and output contracts, with a strict source and authorization boundary.
- **Points to address:** Limit discovery to one strategy, use injected timeout and redaction boundaries, and create fixture tests for valid, partial, unauthorized, malformed, timeout, and safe-error outcomes. Any dashboard- or client-derived machine interface is non-public, undocumented, and reverse-engineered where applicable; it is not an official stable API.
- **Out of scope:** Implementation, official-API claims, local credential/cookie/session discovery, provider fallback strategies, polling, persistence, and UI.
- **Tests:** Specify offline fixtures only; no live calls, accounts, local secrets, or authenticated browser state.
- **Acceptance criterion:** The strategy has a documented evidence boundary and can be rejected safely when no stable, authorized, evidence-backed source is available; it never maps API technical rate limits into commercial Codex quota.
- **Risks:** The source can change without notice, authorization behavior can be fragile, and reverse-engineered assumptions can become invalid. Stop rather than infer values.

### 5. OpenCode Go provider

- **Purpose/description:** Keep OpenCode Go as a separate, explicit opt-in investigation for a fragile local reverse-engineered dashboard/session approach.
- **Points to address:** Isolate it behind its own provider identifier and authorization boundary; require safe degradation; never persist, expose, log, or discover cookies or sessions; use only redacted fixture shapes for contract planning.
- **Out of scope:** Implementation, default enablement, cookie/session persistence or exposure, local-authentication discovery, upstream-provider attribution, polling, and UI.
- **Tests:** Specify offline fixtures for opt-in unavailable, unauthorized, malformed, expired-session, timeout, and partial outcomes; prove no session material enters snapshots, errors, or JSON.
- **Acceptance criterion:** Without a safe user-authorized source, the provider yields an explicit unavailable or unauthorized result and does not fabricate account data.
- **Risks:** This approach is local, fragile, non-public, undocumented, and reverse-engineered; it can break with dashboard or session changes and has elevated privacy risk.

### 6. CLI status

- **Purpose/description:** Present normal, partial, stale, unavailable, and error snapshots safely to a command-line consumer after the JSON contract exists.
- **Points to address:** Render status and absence reasons faithfully, preserve metric and window labels, make stale data visible, and avoid exposing unsafe diagnostics or credentials.
- **Out of scope:** YASB UI, graphical interfaces, provider acquisition, background polling, persistence, and configuration workflows.
- **Tests:** Render JSON-contract fixtures for each status class, including incompatible metrics and redacted errors; assert stale and unavailable are not displayed as normal or zero.
- **Acceptance criterion:** CLI output lets a user distinguish normal, partial, stale, unavailable, and error snapshots without conflating measurement categories.
- **Risks:** Presentation shortcuts can conceal freshness or transform absence into apparent zero usage.

### 7. In-memory cache

- **Purpose/description:** Add an in-memory cache with explicit TTL, freshness, invalidation, and safe-degradation behavior.
- **Points to address:** Use an injected clock; distinguish fresh from stale entries; invalidate deterministically; return stale, unavailable, or typed error states rather than fabricate a refreshed observation.
- **Out of scope:** Disk or database persistence, shared/distributed caches, background refresh workers, provider implementation, and cache recovery across process restarts.
- **Tests:** Cover TTL boundaries, fixed-clock freshness transitions, invalidation, cache misses, provider errors, and stale fallback without persistence.
- **Acceptance criterion:** Cache behavior is deterministic, does not survive process lifetime, and never converts unavailable, expired, or failed retrieval into a current known value.
- **Risks:** Cached values can be mistaken for live data unless freshness remains visible; invalidation bugs can retain stale account observations.

## Provider sequencing rationale

Codex and OpenCode Go remain separate because they are distinct products with different evidence, authorization, privacy, and failure characteristics. Combining them would invite unsupported attribution of one product's limits or sessions to the other. Both provider slices follow fake-provider and JSON compatibility contracts so that any future source work is bounded by deterministic behavior, redaction, absence semantics, and safe consumer output before fragile provider-specific assumptions are considered.

## Review units and rollback boundaries

| Commit-sized unit | Suggested Conventional Commit | Rollback boundary |
|---|---|---|
| Research source record | `docs(research): document provider data-source viability` | `docs/research/provider-data-sources.md` only. |
| Domain and contract decision | `docs(architecture): define provider status contracts` | `docs/architecture/domain-model.md` and `docs/architecture/provider-contract.md` only. |
| Roadmap and compatibility record | `docs(architecture): plan provider status delivery` | `docs/architecture/implementation-plan.md` and `docs/research/python-compatibility.md` only. |

## Non-goals

This roadmap creates no implementation authority. In particular, it does not authorize providers, authentication, networking, subprocesses, local-file reads, caches, CLI, UI, YASB integration, configuration changes, tests, CI, or publishing in the current documentation-only phase.
