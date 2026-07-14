# Provider-status implementation roadmap

**Decision:** sequence implementation from pure domain behavior to opt-in provider adapters. Each slice remains independently reviewable and reversible; no provider access is authorized by this document.

## Ordered slices

### 1. Establish domain value objects

- **Objective:** Implement immutable representations for `ProviderStatus`, `QuotaWindow`, `ProviderSnapshot`, `UsageSnapshot`, source metadata, and availability states.
- **Out of scope:** Provider adapters, persistence, transport, commands, UI, CLI, and configuration.
- **Tests:** Construct valid and invalid values; verify missing values cannot become numeric zero.
- **Acceptance criterion:** All four conceptual model types can represent known and absent values without ambiguity.
- **Risks:** Premature serialization choices could constrain later integrations.

### 2. Encode metric and window separation

- **Objective:** Add explicit classifications for commercial quota, technical rate limit, token use, balance, percentage, and reset semantics.
- **Out of scope:** Reading provider data or converting provider-specific values.
- **Tests:** Verify incompatible kinds, scopes, and windows cannot be combined.
- **Acceptance criterion:** A technical rate limit cannot populate a commercial-quota field.
- **Risks:** Provider terminology may be inconsistent and must remain adapter-local.

### 3. Add percentage derivation guards

- **Objective:** Define the pure calculation policy for used/remaining percentages.
- **Out of scope:** Display formatting and any provider retrieval.
- **Tests:** Cover compatible totals, zero denominators, mismatched windows, missing values, and stale observations.
- **Acceptance criterion:** Percentages exist only for compatible known numerator and denominator values; absent data is never `0%`.
- **Risks:** Consumers may expect a single cross-provider percentage where none is meaningful.

### 4. Add aggregation with exclusions

- **Objective:** Implement global aggregation that returns included providers and explicit exclusion reasons.
- **Out of scope:** Polling, scheduling, caching, and UI summaries.
- **Tests:** Cover every exclusion rule in the domain model, including stale and incompatible measurements.
- **Acceptance criterion:** Any excluded requested provider makes the aggregate `partial`, without coercing incompatible values.
- **Risks:** Aggregation can hide essential provider differences if source metadata is discarded.

### 5. Define typed errors and safe diagnostics

- **Objective:** Implement the contract's error taxonomy and redaction-safe diagnostic representation.
- **Out of scope:** Actual HTTP, command, or file execution.
- **Tests:** Assert each error kind's retryability and that diagnostic fields reject unsafe raw payloads.
- **Acceptance criterion:** Error results distinguish authorization, absence, parsing, transport, command, and throttling outcomes without sensitive material.
- **Risks:** Overly broad diagnostic fields could later bypass redaction.

### 6. Introduce protocols and deterministic fakes

- **Objective:** Add `Protocol` boundaries for provider readers, HTTP clients, command runners, filesystems, and clocks, plus test fakes.
- **Out of scope:** Concrete provider selection and production side effects.
- **Tests:** Prove a reader can be exercised entirely with fixed fake dependencies and a fixed clock.
- **Acceptance criterion:** Contract tests run offline and do not depend on platform state.
- **Risks:** Boundary interfaces may become too broad if they mirror third-party libraries.

### 7. Add static capability metadata

- **Objective:** Represent research-backed provider capability classifications separately from account snapshots.
- **Out of scope:** Treating public plan tables as account usage or implementing source acquisition.
- **Tests:** Verify Codex and OpenCode Go capability records preserve `conditional`, `unavailable`, and `requires more research` conclusions.
- **Acceptance criterion:** Unsupported metrics remain explicitly unavailable.
- **Risks:** Public documentation changes; records require dated source references.

### 8. Prototype one approved read-only adapter

- **Objective:** After separate source approval, implement one narrowly scoped adapter using one injected boundary.
- **Out of scope:** Multi-provider polling, local authentication discovery, caching, CLI, and UI.
- **Tests:** Fixture-driven complete, partial, unauthorized, malformed, and rate-limited outcomes.
- **Acceptance criterion:** The adapter produces only validated snapshots or typed safe errors in offline tests.
- **Risks:** Public research may not establish a reliable account-observation source; stop rather than infer.

### 9. Add composition and freshness policy

- **Objective:** Wire approved readers through an explicit composition root and apply caller-provided freshness rules.
- **Out of scope:** Background workers, persistence, user interfaces, and automatic retry loops.
- **Tests:** Verify configured readers receive injected dependencies and stale snapshots are excluded from aggregates.
- **Acceptance criterion:** Composition introduces no hidden I/O and freshness is deterministic under a fixed clock.
- **Risks:** A composition root can accidentally couple provider configuration to domain models.

### 10. Add consumer-facing projection only after evidence

- **Objective:** Define a stable, sanitized projection for a future library consumer after provider evidence and contract tests exist.
- **Out of scope:** CLI, UI, YASB integration, daemon, cache, publishing, and any unauthorised provider feature.
- **Tests:** Verify projections preserve absence, partial state, metric kind, reset semantics, and aggregate exclusions.
- **Acceptance criterion:** A consumer can distinguish unsupported, unauthorized, partial, and known data without inspecting provider internals.
- **Risks:** A consumer projection can prematurely become public API; version deliberately.

## Review units and rollback boundaries

| Commit-sized unit | Suggested Conventional Commit | Rollback boundary |
|---|---|---|
| Research source record | `docs(research): document provider data-source viability` | `docs/research/provider-data-sources.md` only. |
| Domain and contract decision | `docs(architecture): define provider status contracts` | `docs/architecture/domain-model.md` and `docs/architecture/provider-contract.md` only. |
| Roadmap and compatibility record | `docs(architecture): plan provider status delivery` | `docs/architecture/implementation-plan.md` and `docs/research/python-compatibility.md` only. |

## Non-goals

This roadmap creates no implementation authority. In particular, it does not authorize providers, authentication, networking, subprocesses, local-file reads, caches, CLI, UI, YASB integration, configuration changes, tests, CI, or publishing in the current documentation-only phase.
