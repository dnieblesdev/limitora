# Provider contract for evidence-backed snapshots

**Current contract:** Limitora ships a small synchronous `ProviderReader` protocol with separate detection and fetching. Side-effect boundaries are injected into adapters. A successful or partial read returns `ProviderSnapshot`; a `ProviderError` represents an outcome that prevents a trustworthy snapshot.

Codex JSONL and OpenCode Go adapters implement this contract behind explicit composition; their transports and credentials are private.

## Contract shape

```text
ProviderReader
  provider_id: ProviderId
  detect() -> ProviderDetection
  fetch(request: ProviderRequest) -> ProviderSnapshot

ProviderRequest
  requested_metrics: frozenset[MetricKind]
  authorization_policy: AuthorizationPolicy

ProviderSnapshot
  provider_id
  fetched_at
  data_at
  status: ProviderStatus
  quota_windows: sequence[QuotaWindow]
  usage: optional UsageSnapshot
  rate_limit_reset_credits: optional RateLimitResetCreditsSummary
  source: SourceMetadata

ProviderError
  kind: ProviderErrorKind
  provider_id
  safe_message
  retryable
```

`StatusRequest` is the public API/client request; its `freshness_policy` is handled there and is not part of provider-boundary `ProviderRequest`.

The contract permits partial data: a reader may return a `ProviderSnapshot` with `ProviderStatus.state = partial` when independently validated fields exist alongside explicit absences. It must not convert unsupported or missing data to numeric zero.

`ProviderSnapshot.rate_limit_reset_credits` is optional technical rate-limit metadata. When present, its `available_count` is non-negative and its `credits` is either nullable or an immutable sequence; absent or explicit-null provider details remain absent, while an explicit empty list remains empty. Each credit exposes typed reset/status values and timezone-aware grant and optional expiration timestamps without retaining opaque provider identifiers.

## Typed errors and safe diagnostics

| `ProviderErrorKind` | Meaning | Retryable | Safe error rule |
|---|---|---:|---|
| `not_configured` | The reader has no approved source configuration. | No | Name the provider and required configuration category only. |
| `unauthorized` | The source requires user authorization that is absent or rejected. | No | State authorization is required; exclude credentials and request details. |
| `source_unavailable` | Research establishes no source for the requested metric. | No | State the metric and evidence classification. |
| `transport` | A network boundary failed before a valid response. | Usually | Include no request payload or authentication material. |
| `command_failed` | A command boundary exited unsuccessfully. | Depends | Include exit category and redacted diagnostic summary only. |
| `file_missing` | A configured local evidence file is absent. | No | Include only an approved logical path identifier. |
| `file_invalid` | A local evidence file cannot be validated. | No | Include validation category, not raw contents. |
| `parse_failed` | A source response cannot be mapped safely. | Depends | Include schema/version expectation, not raw response content. |
| `rate_limited` | A source explicitly reported a technical rate limit. | Yes | Preserve a validated technical reset value only if supplied. |
| `unsupported` | The reader cannot observe the requested metric. | No | State the metric and provider capability. |

Errors and snapshots must contain neither credentials nor raw authentication artifacts. Logging, exception chaining, and diagnostics must use the same redaction boundary.

## Source adapters

The single contract supports these adapter categories without leaking transport details into consumers:

| Adapter category | Responsibility | Deterministic offline test seam |
|---|---|---|
| Codex JSONL | Translate a bounded authorized app-server exchange into a snapshot or typed error. | Inject a fake session and clock. |
| OpenCode Go | Translate an explicit bounded HTTP transport response into a snapshot or typed error. | Inject a fake transport and clock. |
| Test provider | Exercise the contract without external I/O. | Construct `FakeProvider` with fixed outcomes. |

An adapter may support only one category. Provider selection belongs in a composition layer, not in the domain model.

## Design decisions evaluated

| Decision | Option | Evaluation | Recommendation |
|---|---|---|---|
| Contract mechanism | `typing.Protocol` | Structural typing permits lightweight fakes, keeps adapters decoupled, and avoids a framework inheritance hierarchy. | Use `Protocol` for the reader and boundary dependencies. |
| Contract mechanism | Abstract base class | Useful only if shared executable behavior or registration lifecycle becomes necessary. It adds inheritance coupling before such behavior exists. | Do not use initially; revisit only for genuine shared behavior. |
| Execution style | Synchronous | The initial work is a single snapshot acquisition and supports direct deterministic fakes with minimal consumer complexity. | Use synchronous acquisition initially. |
| Execution style | Asynchronous | Helpful for concurrent remote providers, but expands every consumer and fake contract before concurrency is proven necessary. | Defer until a measured multi-provider concurrency requirement exists. |
| HTTP boundary | Concrete client in adapter | Couples tests to transport behavior. | Inject a narrow HTTP-client protocol. |
| Command boundary | Direct process invocation | Makes offline tests platform-dependent. | Inject a command-runner protocol. |
| File boundary | Direct filesystem access | Makes absence and malformed-file tests harder to control. | Inject a filesystem protocol. |
| Time boundary | Direct clock access | Makes freshness and reset tests nondeterministic. | Inject a clock protocol. |
| Timeout policy | Adapter-local defaults | Produces inconsistent cancellation and retry behavior. | Inject a bounded timeout policy; each approved operation must receive an explicit deadline. |

## Injection boundaries

The composition root supplies adapters with narrow dependencies:

- **HTTP client:** accepts a sanitized request description and returns a structured response; it owns transport setup and must not return raw diagnostics to the domain.
- **Command runner:** accepts an approved command specification and returns exit status plus redacted output classification; it never exposes the ambient environment wholesale.
- **Filesystem:** reads only explicitly configured, allowlisted paths and reports absence distinctly from invalid content.
- **Clock:** supplies an instant for observation and freshness evaluation.
- **Timeout policy:** supplies a bounded deadline or duration for each approved HTTP, CLI, or local-file operation. Expiry maps to a retryable `transport` or `command_failed` error as appropriate; it never yields a fabricated snapshot.

No adapter discovers local authentication artifacts. Authorization policy is explicit in `ProviderRequest`, and lack of authorization yields `unauthorized`, never a fabricated measurement.

## Deterministic offline-test scenarios

Offline contract tests instantiate only fakes and fixed clocks to prove these properties:

1. A complete source yields an `available` snapshot with source metadata.
2. One unavailable metric yields a `partial` snapshot without a substitute numeric value.
3. An unauthorized source yields `unauthorized` with a safe error.
4. A technical rate-limit reset is retained as technical data and does not populate a commercial-quota field.
5. A malformed local file yields `file_invalid` or `parse_failed` without raw-content leakage.
6. A global aggregation excludes incompatible, stale, or absent values and reports exclusion reasons.

## Boundary and non-goals

This page does not expose endpoints, commands, local paths, cache internals, configuration secrets, CLI behavior, UI, or YASB integration. Adapter diagnostics are mapped to safe typed errors; raw payloads and authentication material never cross the contract.
