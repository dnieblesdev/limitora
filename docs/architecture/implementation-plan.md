# Provider-status design roadmap

**Decision:** This is a design roadmap, not current implementation authorization. It sequences independently reviewable work and does not authorize provider access, authentication, networking, local-file reads, subprocesses, cache implementation, CLI implementation, configuration changes, or publishing.

**No-conflation rule:** Commercial quota, technical rate limits, token usage, balances, and percentage derivation are distinct concepts. No slice may map one to another, infer one from another, or present them as interchangeable.

## Ordered slices

### 1. Domain core

- **Purpose/description:** Define typed domain models, invariants, window semantics, percentage validity, freshness and stale semantics, and global aggregation rules. The domain defines aggregation validity but does not select or execute providers.
- **Points to address:** Model compatible metric kind, scope, window, reset, source evidence, absence, freshness, stale age, and aggregate inclusion or exclusion reasons. Define when a percentage is derivable and when a global result is complete or partial.
- **Out of scope:** Provider selection or execution, I/O, serialization, caching, CLI, and UI.
- **Tests:** Mandatory: construct valid and invalid models; cover incompatible metric kinds, scopes, and windows; percentage guards; freshness transitions; and every aggregation exclusion rule.
- **Acceptance criterion:** The domain represents known and absent evidence without numeric invention, and aggregation validity is deterministic without provider knowledge.
- **Risks:** Convenience fields can hide incompatible semantics or turn absence into zero.

### 2. Provider foundation

- **Purpose/description:** Establish typed errors, a `Protocol` provider boundary, detection distinct from fetching, and injected ports for HTTP, filesystem, command runner, and clock.
- **Points to address:** Keep authorization and redaction explicit; inject all side effects; provide a deterministic fake provider and offline contract tests. Adapters never directly access globals, environment, network, or files.
- **Out of scope:** Concrete provider strategies, provider selection, cache policy, and CLI behavior.
- **Tests:** Mandatory: run deterministic offline contracts with fakes for detected, unavailable, unauthorized, malformed, timeout, partial, and safe-redacted error outcomes.
- **Acceptance criterion:** An adapter returns validated typed results or typed redacted errors entirely through injected ports.
- **Risks:** Ports can become broad third-party-client mirrors, and diagnostics can bypass redaction.

### 3. Application service / orchestration

- **Purpose/description:** Create a central `StatusService` or `UsageOrchestrator` that selects providers, may coordinate concurrency, normalizes partial outcomes, applies error and degradation policy, and creates global snapshots.
- **Points to address:** Define provider-selection inputs, isolation between provider failures, deterministic outcome normalization, concurrency boundaries, and aggregation handoff. The CLI must not own this logic.
- **Out of scope:** Transport/parser internals, JSON rendering, command-line parsing, and cache storage mechanics.
- **Tests:** Mandatory: exercise mixed provider outcomes, selection, degradation, concurrency-safe ordering, and global snapshots using fake providers and an injected clock.
- **Acceptance criterion:** Equivalent provider results produce the same typed global snapshot regardless of CLI or output consumer.
- **Risks:** Orchestration can absorb provider parsing details or duplicate domain aggregation rules.

### 4. Public library API

- **Purpose/description:** Define a stable typed Python-facing client or service request/result contract, including provider selection and freshness policy.
- **Points to address:** Specify supported request options, typed results and problems, lifecycle expectations, and compatibility boundaries. Do not expose provider transport, parser, session details, or JSON as the primary API.
- **Out of scope:** JSON schema, CLI flags, adapter internals, and provider-specific authentication mechanisms.
- **Tests:** Mandatory: contract-test typed request and result compatibility, provider selection, freshness policy, and sanitized problem surfaces without a CLI or serialized output.
- **Acceptance criterion:** A Python consumer can obtain and inspect a typed result without depending on JSON or provider implementation details.
- **Risks:** Leaking adapter details would freeze fragile integrations as public API.

### 5. Codex provider

- **Purpose/description:** Define one bounded Codex strategy using an existing authorized authentication path, with timeout, redaction, fixtures, and a manual opt-in smoke test.
- **Points to address:** Classify the source honestly as non-public, undocumented, or reverse-engineered where appropriate; make no stable official API claim. Bound response handling, error classification, and fixture evidence to the selected strategy.
- **Out of scope:** Additional strategies, credential or session discovery, fallback chains, polling, persistence, default enablement, and implementation authorization.
- **Tests:** Mandatory: use offline redacted fixtures for valid, partial, unauthorized, malformed, and timeout outcomes; define a separately manual opt-in smoke test that cannot expose secrets.
- **Acceptance criterion:** The strategy either returns evidence-backed typed data or explicitly degrades; it never conflates technical limits with commercial Codex quota.
- **Risks:** The source may change without notice and authorization behavior may be fragile; unknown values must not be inferred.

### 6. OpenCode Go provider

- **Purpose/description:** Define an independent opt-in authenticated-session or dashboard adapter for OpenCode Go as a fragile, local-reverse-engineered integration.
- **Points to address:** Require defensive parsing, response-size limits, explicit incompatible-response degradation, and strict non-exposure and non-persistence of secrets, cookies, and headers.
- **Out of scope:** Default enablement, credential or session discovery, cookie/session persistence, upstream attribution, polling, UI, and implementation authorization.
- **Tests:** Mandatory: use offline redacted fixtures for unavailable, unauthorized, expired-session, malformed, oversized, incompatible, timeout, and partial outcomes; prove session material never enters typed results, errors, JSON, or logs.
- **Acceptance criterion:** An incompatible or unauthorized response yields a sanitized explicit degradation and never fabricated account data.
- **Risks:** Dashboard and session behavior are non-public, undocumented, reverse-engineered, privacy-sensitive, and likely to break.

### 7. In-memory cache

- **Purpose/description:** Cache valid typed snapshots, never rendered JSON, with separate reuse TTL and maximum stale age.
- **Points to address:** Define cache keys, invalidation, fallback policy, injected-clock boundaries, reuse eligibility, stale eligibility, and process-lifetime limits.
- **Out of scope:** Disk, database, distributed, or cross-process cache; background refresh; serialization cache; and provider implementation.
- **Tests:** Mandatory: cover key isolation, TTL and maximum-stale-age boundaries, invalidation, misses, valid reuse, stale fallback, and failed retrieval using fixed clocks.
- **Acceptance criterion:** The cache never presents rendered JSON or failed/expired data as fresh, and its fallback is explicit in typed freshness state.
- **Risks:** Reuse TTL can be confused with maximum stale age, retaining account observations beyond their allowed use.

### 8. Output contracts

- **Purpose/description:** Define versioned deterministic JSON schema and human rendering as projections of public-library results, not as the primary API.
- **Points to address:** Specify generated, fetched, fresh, and reset timestamp meanings; stable ordering; sanitized public problems; metric and absence representation; and stdout, stderr, and exit-code behavior for future CLI consumers.
- **Out of scope:** Provider execution, provider selection, aggregation policy, cache policy, Python library API design, and command-line parsing.
- **Tests:** Mandatory: validate versioned JSON and human projections from typed library fixtures for complete, partial, stale, unavailable, and error states; assert deterministic ordering and stream/exit behavior.
- **Acceptance criterion:** Consumers can distinguish typed state and timestamp meanings without provider internals, while JSON remains a projection rather than the library contract.
- **Risks:** JSON fields can accidentally become an unversioned primary API, and rendering can hide absence or freshness.

### 9. CLI status

- **Purpose/description:** Implement a thin parser and adapter over the public library API for status, JSON, provider selection, and later cache controls.
- **Points to address:** Map arguments to typed library requests, use output contracts for rendering and streams, and preserve sanitized public problems. It renders only: it must never execute providers, calculate global state, or apply cache policy.
- **Out of scope:** Provider adapters, orchestration, aggregation, cache decisions, JSON-schema ownership, and UI.
- **Tests:** Mandatory: verify argument mapping and rendering against a fake public-library client for normal, partial, stale, unavailable, and error results; assert no provider port is invoked by the CLI layer.
- **Acceptance criterion:** The CLI is replaceable without changing provider behavior, aggregation, freshness policy, or cache policy.
- **Risks:** Convenience logic in commands can fork policy from the public library API.

## Provider separation rationale

Codex and OpenCode Go remain separate because they have different products, evidence sources, authorization paths, privacy boundaries, and failure modes. Combining them would blur attribution and encourage unsafe assumptions about quotas, sessions, or source stability. Each remains bounded by the shared provider contract while retaining its own risk classification.

## Non-goals

This roadmap authorizes no implementation. In particular, it does not authorize providers, authentication, networking, subprocesses, local-file reads, caches, CLI, UI, configuration changes, tests, CI, or publishing during the current documentation-only phase.
