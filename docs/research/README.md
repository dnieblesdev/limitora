# Limitora research

This document records concluded investigations and sanitized reference material.

## Research status

| Topic | Goal | Status |
|-------|------|--------|
| Codex API | Understand request/response shape and rate limits | concluded; see [Codex protocol](../providers/codex-protocol.md) |
| OpenCode Go API | Document the provider surface from a Python caller's view | concluded; see [OpenCode Go protocol](../providers/opencode-go-protocol.md) |
| Claude API (future) | Identify models, pricing, and authentication patterns | future |
| Gemini API (future) | Identify models, pricing, and authentication patterns | future |
| Secret redaction | Define a safe pattern for diagnostic samples | concluded; enforced by provider/output contracts |
| CLI/JSON contract | Design the command-line interface and output schema | concluded; see [output contracts](../architecture/output-contracts.md) |
| yasb-limitora integration | Determine how a YASB adapter would consume this library | future |

Completed research is retained as historical evidence, not as a list of pending
implementation work. Current behavior is defined by the linked source, tests,
and provider pages; future investigations require a separate approved scope.

## Sanitized samples

All samples checked into this repository must be redacted. Use the file extensions `*.redacted.json` or `*.redacted.txt`.

Rules for samples:

- Replace tokens, cookies, sessions, and credentials with placeholders.
- Remove hostnames, user IDs, and project IDs unless they are public examples.
- Never include `.env` files, private keys, or unredacted dumps.

## Notes

- Keep provider-specific research in provider-named subdirectories once created.
- Link any external references rather than copying sensitive content.

## Canonical evidence

Provider claims use public first-party or upstream references with the research
access date recorded in [provider data-source viability](provider-data-sources.md).
No private account material, live payload, credential, or provider call belongs
in this directory.
