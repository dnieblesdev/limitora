# Repository guidance

## Purpose

Limitora provides a typed, provider-agnostic quota and status API, with Codex JSONL and OpenCode Go adapters plus a CLI.

## Structure

- `src/limitora/` contains the public API and the `models`, `core`, `providers`, and `cli` packages.
- `tests/` contains the unit and contract tests.
- `docs/` contains architecture and provider research.
- `.atl/` is generated, ignored runtime state. Keep it producer-managed and do not hand-edit it.

## Commands

- Test: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v`
- Build: `python3 -m build` when the standard build frontend is installed.
- No repository lint, format, or type-check command is configured. Do not invent one.

## Modification constraints

- Preserve typed public contracts across the API, models, core, providers, and CLI.
- Keep errors safe and redacted; never expose credentials or private provider payloads.
- Do not store credentials or private payloads in source, tests, docs, fixtures, or diagnostics.
- Keep generated `.atl/` content managed by its producer.

## Agent skills

- Project skills live in `.agents/skills`.
- Use the native skill tool before non-trivial work.
- Subagents load required skills in their own context; the parent passes skill names.
- Report duplicate skill names instead of assuming implicit precedence.
- Use the ATL registry only as a fallback when native skill loading is unavailable.

For AutoSkills, use `.agents/skills` as the canonical project destination. Preview safely with `npx autoskills --dry-run`; the actual interactive command is `npx autoskills`. Keep `skills-lock.json` when generated, never target `.atl`, and do not use `--version` with AutoSkills 0.3.6.
