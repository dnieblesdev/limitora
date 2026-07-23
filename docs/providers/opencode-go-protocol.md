# OpenCode Go provider protocol and risk boundary

This page documents the current private adapter boundary from repository source
and tests. It is not a claim that OpenCode Go publishes or guarantees this
account-usage response schema. The canonical public context is
[OpenCode Go documentation](https://opencode.ai/docs/go/), accessed 2026-07-14;
the public [OpenCode repository](https://github.com/anomalyco/opencode) is
additional product context, not a private dashboard payload.

## Scope and authorization

| Boundary | Current behavior |
|---|---|
| Product identity | OpenCode Go is treated as a commercial quota source, distinct from the Go language and from upstream model providers. |
| Activation | The provider is opt-in and requires a workspace identifier, an endpoint fixed to `https://opencode.ai`, a positive timeout no greater than 10 seconds, and user-supplied authorization. |
| Default policy | `DENY_AUTHORIZED_SOURCE` fails before transport; the adapter does not discover local credentials or silently authorize a request. |
| Transport | The optional `httpx` dependency is scoped to the `opencode-go` extra; redirects and ambient proxy/environment configuration are disabled. The configured timeout applies to HTTPX connect, read, write, and pool operations and to a Limitora-owned monotonic total budget. |

## Request and observed response shape

The bounded transport issues one `GET` request to the encoded workspace path:

```text
https://opencode.ai/workspace/<encoded-workspace-id>/go
```

The authorization header is constructed inside the transport and is never part
of documentation examples, diagnostics, or output. The current mapping accepts
only an object containing one or more of these synthetic-shape fields:

| Field | Meaning in the adapter | Boundary |
|---|---|---|
| `rollingUsage` | Five-hour commercial quota window | `usagePercent` is a finite `int` or `float` from 0..100; `resetInSec` is an integral non-negative value |
| `weeklyUsage` | Weekly commercial quota window | Same validation |
| `monthlyUsage` | Monthly commercial quota window | Same validation |

Each valid field becomes used and remaining percentage points with a reset
timestamp derived from one captured `fetched_at`. The adapter does not infer a
plan identifier. Fixture values are synthetic shape evidence only; they are not
provider observations or account data.

Commercial quota windows are not technical rate limits. A public subscription
label, upstream provider limit, or HTTP status does not create a quota value.

## Outcomes and fail-closed behavior

| Input or outcome | Safe result |
|---|---|
| 401/403 | Typed unauthorized failure; no retry |
| 429 | Typed rate-limited failure; retryable, without exposing the body |
| 5xx | Typed source-unavailable failure; retryable |
| HTTP transport timeout or unavailability | Typed `TRANSPORT` failure; retryable where the contract permits |
| Redirect or other non-2xx response | Typed unsupported failure; no redirect following |
| HTML login page, malformed JSON, invalid field, or no valid window | Typed parse failure; partial data is retained only when at least one sibling window is valid |
| Response body at or above 512 KiB or configured request budget exhausted | Bounded transport failure; no further response chunks are processed |

An absent, null, malformed, or unsupported window is not converted to zero.
If every candidate window is invalid, the provider fails closed rather than
returning fabricated quota data. Error messages are constant safe summaries;
credentials, cookies, private response bodies, tracebacks, and raw transport
diagnostics are excluded.

HTTPX synchronous timeouts are per-operation inactivity limits, not a strict
wall-clock cancellation deadline. Limitora checks its monotonic total budget
before request execution and whenever response handling returns control,
including before processing each streamed chunk. These checks prevent continued
processing after expiry, but cannot instantaneously interrupt a synchronous
operation while it is blocked inside HTTPX.

## Evidence boundary

The repository tests verify mapping, partial-data behavior, authorization policy,
redirect handling, body/time bounds, optional-dependency scope, and secret
non-disclosure. They do not establish live service availability or a permanent
upstream protocol. Any future protocol change requires fresh public evidence,
updated tests, and a separately approved documentation or implementation scope.

## Related documents

- [Provider contract](../architecture/provider-contract.md)
- [Provider data-source viability](../research/provider-data-sources.md)
- [Codex protocol](codex-protocol.md)
