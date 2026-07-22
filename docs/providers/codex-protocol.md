# Codex JSON-RPC handshake contract

This document captures the **verified** JSON-RPC handshake contract
between Limitora and the `codex app-server --stdio` provider. It
mirrors the implementation in
``src/limitora/providers/_codex_jsonl_protocol.py``,
``src/limitora/providers/_codex_jsonl_transport.py``, and
``src/limitora/providers/_codex_jsonl.py``.

For the broader provider contract — typed errors, redaction rules,
and snapshot semantics — see
[``docs/architecture/provider-contract.md``](../architecture/provider-contract.md).

## Layered split

The session is split into three private modules behind the unchanged
public surface (``_CodexJsonlSession``, ``_CodexSessionSpec``,
``_CodexJsonlFailure``, ``_CodexJsonlFailureKind``):

| Layer | Owns | Module |
|---|---|---|
| Transport | ``subprocess.Popen`` start, read/write I/O, deadline enforcement, byte-cap, terminate/kill/close cleanup | ``_codex_jsonl_transport.py`` |
| Protocol | JSON-RPC envelope build and parse. **No I/O.** | ``_codex_jsonl_protocol.py`` |
| Mapping | Request ``id`` correlation, notification skipping, error-code → failure-kind lookup, session orchestration | ``_codex_jsonl.py`` |

## Outbound frame shapes

Outbound frames are newline-delimited JSON. They **omit** the
``jsonrpc`` envelope key — the Codex app-server rejects frames that
carry the standard ``"jsonrpc":"2.0"`` discriminator.

### 1. ``initialize`` request (id 1)

```json
{"id":1,"method":"initialize","params":{"clientInfo":{"name":"limitora","version":"<client_version>"}}}
```

* ``id`` is the integer correlation token.
* ``method`` is exactly ``"initialize"``.
* ``params.clientInfo.name`` is always ``"limitora"``.
* ``params.clientInfo.version`` is the package metadata version
  (resolved via ``importlib.metadata.version("limitora")``); when
  metadata is absent (e.g. editable install without ``.dist-info``)
  the adapter falls back to the sentinel ``"0.0.0+unknown"``.
* ``params.protocolVersion`` is **not** sent.

### 2. ``initialized`` notification (no id)

```json
{"method":"initialized","params":{}}
```

Notifications carry no ``id`` and no ``jsonrpc``. The ``initialized``
notification follows a valid ``initialize`` response; sending it
before a valid ``initialize`` response closes the session.

### 3. ``account/rateLimits/read`` request (id 2)

```json
{"id":2,"method":"account/rateLimits/read","params":{}}
```

Exactly one rate-limit request is sent per session. The adapter
never retries, probes, or sends other RPCs.

## Inbound correlation and notification skipping

Every inbound frame is decoded by
``_codex_jsonl_protocol.parse_frame`` into one of three shapes:

| ``ident`` | ``result`` | ``error`` | Meaning |
|---|---|---|---|
| ``int`` | ``dict`` | ``None`` | Correlated response — accepted if ``ident`` matches the outstanding request id, else ``PROTOCOL`` |
| ``int`` | ``None`` | ``dict`` | Correlated error response — mapped via the error-code table below |
| ``None`` | ``None`` | ``None`` | Server-pushed notification — silently skipped |
| — | other shape | other shape | ``PROTOCOL`` |

### Examples

```json
{"id":1,"result":{"serverInfo":{...}}}
```
→ correlated response to the ``initialize`` request. Any
``protocolVersion`` field on the result is ignored; an error reply
remains the only "incompatible server" signal.

```json
{"id":2,"result":{"rateLimits":{"limitId":"codex","planType":"pro","primary":{...},"secondary":null}}}
```
→ correlated response to ``account/rateLimits/read``. The mapping
layer parses the result into a ``ProviderSnapshot``.

``limitId`` belongs to the ``rateLimits`` snapshot and must equal
``"codex"``. It is not a field of ``primary`` or ``secondary``.
Each non-null window contains only the mapped fields
``usedPercent``, ``resetsAt``, and ``windowDurationMins``; either
window may be null.

The optional top-level ``rateLimitResetCredits`` is separate account inventory. A present summary maps a non-negative ``availableCount`` and optional-nullable ordered ``credits`` details; missing or null details are unavailable, while an empty list is known empty. List length is not assumed to equal the count. Details retain typed reset kind/status, aware grant/expiration times, title, and description. The required opaque upstream credit ``id`` is validated and immediately discarded, never retained or projected.

```json
{"method":"serverNotification","params":{}}
```
→ server-pushed notification. Silently skipped; reading continues
until the correlated response arrives.

```json
{"id":99,"result":{}}
```
→ wrong-phase / unknown id. ``PROTOCOL`` is raised; the session
closes.

```json
{"id":1,"result":{},"error":{"code":2}}
```
→ malformed envelope (both ``result`` and ``error``). ``PROTOCOL``
is raised; the session closes.

```json
not-json
```
→ malformed line. ``PROTOCOL`` is raised; the session closes.

## Error-code → failure-kind table

Error responses are mapped by the **mapping** layer (the spec places
the lookup there). The transport never logs the message; only the
typed kind propagates.

| JSON-RPC error code | Failure kind | Translated ``ProviderErrorKind`` | Retryable |
|---:|---|---|---:|
| ``401`` | ``UNAUTHORIZED`` | ``unauthorized`` | No |
| ``403`` | ``UNAUTHORIZED`` | ``unauthorized`` | No |
| ``429`` | ``RATE_LIMITED`` | ``rate_limited`` | Yes |
| ``503`` | ``UNAVAILABLE`` | ``source_unavailable`` | Yes |
| any other int | ``PROTOCOL`` | ``parse_failed`` | No |
| non-int / missing | ``PROTOCOL`` | ``parse_failed`` | No |

The provider-facing ``ProviderError.safe_message`` is
``"Codex quota source <kind>"`` and never includes the upstream
error message or any token.

## Cleanup sequence

Each ``_CodexJsonlSession.exchange`` call runs the bounded
cleanup regardless of outcome:

1. ``process.close_stdin()`` — close the child's stdin so it sees
   EOF.
2. ``process.terminate()`` — ``SIGTERM``.
3. ``process.wait(cleanup_allowance)`` — bounded wait.
4. If the child is still alive: ``process.kill()`` (``SIGKILL``)
   then ``process.wait(cleanup_allowance)`` again.
5. ``process.close()`` — close the captured stdout stream.

Any exception during teardown is converted to a redacted
``_CodexJsonlFailure(kind=PROCESS)``; that failure takes precedence
over a payload-returning outcome, so the session never reports
success if cleanup failed.

## Redaction rules

The session is redaction-strict:

* The provider-facing ``ProviderError.safe_message`` never includes
  the JSON-RPC ``error.message`` (which may carry tokens like
  ``"token=secret"`` from the upstream example payload).
* The protocol layer raises ``PROTOCOL`` with a redacted message
  (``"Codex JSONL transport protocol"``); it never echoes the raw
  bytes it failed to parse.
* ``stderr`` is connected to ``subprocess.DEVNULL``; raw process
  output never lands in diagnostics.
* ``_PopenProcess`` requires the runner to be an absolute path
  (``runner[0].startswith("/")``); relative or empty runners are
  rejected with ``OSError`` before ``subprocess.Popen`` runs.

## Trailing-data probe

After the final correlated response, the session probes for any
extra output the server may have written past the closing newline:

1. ``_BoundedLineReader.read_one(timeout=0.0)`` reads up to 1 byte.
2. If any bytes arrive, ``PROTOCOL`` is raised.
3. If ``process.poll()`` returns a non-zero exit code, ``PROCESS``
   is raised.
4. Otherwise the session returns the rate-limit payload.

The probe uses a near-immediate deadline (``monotonic() + 0.001``)
so a stalled server cannot extend the session past the spec
contract.

## Cross-references

* Provider contract: [``docs/architecture/provider-contract.md``](../architecture/provider-contract.md)
* Composition root: ``src/limitora/composition.py``
* Provider adapter: ``src/limitora/providers/codex.py``
* Protocol module: ``src/limitora/providers/_codex_jsonl_protocol.py``
* Transport module: ``src/limitora/providers/_codex_jsonl_transport.py``
* Mapping session: ``src/limitora/providers/_codex_jsonl.py``
