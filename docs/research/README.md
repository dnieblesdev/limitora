# Limitora research

This document tracks open investigations and sanitized reference material.

## Active investigations

| Topic | Goal | Status |
|-------|------|--------|
| Codex API | Understand request/response shape and rate limits | open |
| OpenCode Go API | Document the Go-based provider surface from a Python caller's view | open |
| Claude API (future) | Identify models, pricing, and authentication patterns | future |
| Gemini API (future) | Identify models, pricing, and authentication patterns | future |
| Secret redaction | Define a safe pattern for diagnostic samples | open |
| CLI/JSON contract | Design the command-line interface and output schema | open |
| yasb-limitora integration | Determine how a YASB adapter would consume this library | future |

## Sanitized samples

All samples checked into this repository must be redacted. Use the file extensions `*.redacted.json` or `*.redacted.txt`.

Rules for samples:

- Replace tokens, cookies, sessions, and credentials with placeholders.
- Remove hostnames, user IDs, and project IDs unless they are public examples.
- Never include `.env` files, private keys, or unredacted dumps.

## Notes

- Keep provider-specific research in provider-named subdirectories once created.
- Link any external references rather than copying sensitive content.
