# Provider-status domain model

**Decision:** model provider observations as typed, timestamped evidence. Missing data is explicit and is never represented as `0%`.

## Minimal conceptual model

| Concept | Purpose | Required conceptual fields |
|---|---|---|
| `ProviderStatus` | The latest outcome for one provider observation. | provider identifier; state; observed-at time; optional typed error; optional snapshot |
| `QuotaWindow` | One named allowance or technical-limit window. | kind; scope; period; limit value; used value; remaining value; reset time; value availability; source reference |
| `ProviderSnapshot` | A provider-scoped set of observations captured at one point in time. | provider identifier; observed-at time; status; quota windows; optional usage snapshot; source metadata |
| `UsageSnapshot` | A provider-scoped account or product usage observation that is not implicitly a quota window. | observed-at time; token-use values when evidenced; balance when evidenced; used/remaining percentage only when derivable; availability and source metadata |
| `RateLimitResetCreditsSummary` | Optional account inventory for resetting eligible rate limits. | non-negative available count; nullable immutable credit details |
| `RateLimitResetCredit` | One privacy-safe reset-credit detail. | typed reset kind and status; aware grant/optional expiration times; optional title and description |

`QuotaWindow.kind` distinguishes `commercial_quota` from `technical_rate_limit`. They may coexist for one provider and must never overwrite each other.
Reset-credit inventory is snapshot metadata, not a quota window or usage value. Opaque provider credit identifiers are never modeled.

## State semantics

| `ProviderStatus.state` | Meaning | Snapshot rule |
|---|---|---|
| `available` | A source produced at least one usable, evidence-backed value. | Preserve partial values and their source metadata. |
| `partial` | A source produced usable values and explicit absences or unsupported values. | Return available values without manufacturing the absent ones. |
| `unavailable` | No supported source exists for the requested observation. | Return no numeric substitute. |
| `unauthorized` | The required user-authorized source cannot be used. | Preserve the typed error; do not imply provider failure. |
| `rate_limited` | A source explicitly reports a technical throttling condition. | Preserve only the technical-limit window and its reset semantics if present. |
| `transient_error` | A retryable transport, parsing, or command failure prevented a reliable observation. | Preserve the typed error and any independently valid partial data. |
| `invalid_data` | A source response was present but cannot be validated against the provider adapter's expectations. | Discard invalid values while retaining safe diagnostic classification. |

## Absence-value model

Every optional measurement carries one of these availability states:

| Availability | Meaning | Serialization consequence |
|---|---|---|
| `known` | A validated source supplied the value. | Include the typed value and source reference. |
| `unavailable` | Public research establishes no source for the measurement. | Omit the numeric value; retain the reason. |
| `unknown` | A source may exist, but evidence is insufficient. | Omit the numeric value; retain the reason. |
| `not_authorized` | A supported source requires user authorization that is not present. | Omit the numeric value; retain the reason. |
| `not_applicable` | The metric does not apply to the provider or source type. | Omit the numeric value; retain the reason. |
| `invalid` | The observed value failed validation. | Omit the numeric value; retain the reason. |

**Invariant:** `0%` means a known computed percentage equal to zero. It must never represent missing, unavailable, unknown, unauthorized, invalid, or inapplicable data. Likewise, a numeric zero is valid only when supplied or derived from compatible known values.

A used/remaining percentage is allowed only when the numerator and denominator are known, have the same provider, scope, metric kind, and time window, and the denominator is greater than zero. A reset time is allowed only when its associated window is known.

## Global aggregation exclusion rules

A global aggregate must exclude a provider snapshot when **any** of these conditions applies:

1. The provider status is `unavailable`, `unauthorized`, `transient_error`, or `invalid_data`.
2. The requested metric is absent, `unknown`, `not_authorized`, `not_applicable`, or `invalid`.
3. The measurement is a technical rate limit while the aggregate is for commercial quota, or vice versa.
4. The measurement lacks the provider, scope, period, or observation time required to establish comparability.
5. A percentage cannot be derived from compatible known used and total values.
6. The source identifies only a plan-wide range, published price, or model-context capacity rather than the account-scoped measurement requested.
7. A reset value belongs to a different window than the metric being aggregated.
8. The snapshot has expired according to the caller's explicit freshness policy.

An aggregate must report its included provider identifiers, exclusions with reasons, and its own `partial` state whenever any requested provider is excluded. It must not normalize different commercial plans, technical windows, credits, balances, or token-use metrics into a single percentage.

## Non-goals

This is a conceptual model only. It introduces no storage format, provider adapter, network behavior, command execution, cache, UI, CLI, YASB integration, or configuration change.
