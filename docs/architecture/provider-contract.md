# Provider contract for evidence-backed snapshots

**Recommendation:** define a small synchronous `Protocol` for provider acquisition and inject all side-effect boundaries. Return a `ProviderSnapshot` for successful or partial observations; reserve typed errors for outcomes that prevent a trustworthy snapshot.

This is a conceptual contract, not an implementation.

## Contract shape

```text
ProviderReader
  provider_id: ProviderId
  read_status(request: StatusRequest) -> ProviderSnapshot | ProviderReadError

StatusRequest
  requested_metrics: set[MetricKind]
  freshness_policy: FreshnessPolicy
  authorization_policy: AuthorizationPolicy

ProviderSnapshot
  provider_id
  observed_at
  status: ProviderStatus
  quota_windows: sequence[QuotaWindow]
  usage: optional UsageSnapshot
  source: SourceMetadata

ProviderReadError
  kind: ErrorKind
  provider_id
  safe_message
  retryable
  cause_category
```

The contract permits partial data: a reader may return a `ProviderSnapshot` with `ProviderStatus.state = partial` when independently validated fields exist alongside explicit absences. It must not convert unsupported or missing data to numeric zero.

## Typed errors and safe diagnostics

| `ErrorKind` | Meaning | Retryable | Safe error rule |
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

The single contract supports four adapter categories without leaking transport details into consumers:

| Adapter category | Responsibility | Deterministic offline test seam |
|---|---|---|
| HTTP provider | Translate an approved HTTP response into a snapshot or typed error. | Inject a fake HTTP client returning fixed response objects. |
| CLI provider | Translate a bounded command result into a snapshot or typed error. | Inject a fake command runner returning fixed exit/result objects. |
| Local-file provider | Parse an explicitly configured, approved local evidence file. | Inject a fake filesystem returning fixed file bytes or absence. |
| Static/public provider | Return researched public capability metadata, not account measurements. | Construct in memory with a fixed clock. |

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

## Injection boundaries

The composition root supplies adapters with narrow dependencies:

- **HTTP client:** accepts a sanitized request description and returns a structured response; it owns transport setup and must not return raw diagnostics to the domain.
- **Command runner:** accepts an approved command specification and returns exit status plus redacted output classification; it never exposes the ambient environment wholesale.
- **Filesystem:** reads only explicitly configured, allowlisted paths and reports absence distinctly from invalid content.
- **Clock:** supplies an instant for observation and freshness evaluation.

No adapter discovers local authentication artifacts. Authorization policy is explicit in `StatusRequest`, and lack of authorization yields `unauthorized` or `not_authorized`, never a fabricated measurement.

## Deterministic offline-test scenarios

Future tests should instantiate only fakes and fixed clocks to prove these contract properties:

1. A complete source yields an `available` snapshot with source metadata.
2. One unavailable metric yields a `partial` snapshot without a substitute numeric value.
3. An unauthorized source yields `unauthorized` with a safe error.
4. A technical rate-limit reset is retained as technical data and does not populate a commercial-quota field.
5. A malformed local file yields `file_invalid` or `parse_failed` without raw-content leakage.
6. A global aggregation excludes incompatible, stale, or absent values and reports exclusion reasons.

## Non-goals

No endpoint, command, local path, cache, provider behavior, configuration, CLI, UI, or YASB integration is specified or implemented here.
