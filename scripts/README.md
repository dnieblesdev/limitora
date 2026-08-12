# Scripts policy

This directory holds helper scripts for development, research, and diagnostics.

## Rules

| Rule | Description |
|------|-------------|
| Read-only | Scripts must not modify source files, test data, or active sessions without explicit confirmation. |
| Safe | Scripts must validate inputs and fail cleanly. |
| Secret redaction | Any output that could contain tokens, cookies, sessions, or credentials must be redacted before it is saved or shared. |
| Documented | Each script must include a header comment explaining purpose, usage, and exit codes. |
| Removable | Scripts must not be required for the library to function; they are optional tooling. |
| No session modification | Scripts must never create, alter, or terminate user/provider sessions. |

## Allowed extensions

- Python (`.py`)
- Shell (`.sh`)

## Prohibited

- Storing `.env` files or credentials.
- Writing unredacted diagnostic dumps.
- Network calls that are not explicitly documented in the script header.

## OpenCode live driver

`opencode_live_driver.py` is an explicit local operator boundary. It invokes an
installed Limitora CLI only after `--confirm RUN`, reads the one required API key
from the process environment, and emits one constant classification line. It
does not import Limitora, discover files, load dotenv files, or print provider
payloads.
