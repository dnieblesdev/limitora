"""Bounded, local-only OpenCode live operator boundary.

Usage: python scripts/opencode_live_driver.py --confirm RUN --cli /abs/limitora
Exit codes: 0 success, 10 preflight, 20-25 classified live failures.
"""

from __future__ import annotations

import json
import ntpath
import os
import stat
import subprocess
import sys
import threading

WORKSPACE = "LIMITORA_OPENCODE_WORKSPACE_ID"
COOKIE = "LIMITORA_OPENCODE_AUTH_COOKIE"
REQUIRED = (WORKSPACE, COOKIE)
COMMAND_SUFFIX = ("status", "--json", "--provider", "opencode-go", "--opencode-allow-authorized-source")
PREFLIGHT, AUTH, SCHEMA, RATE, SOURCE, TRANSPORT, UNEXPECTED = (10, 20, 21, 22, 23, 24, 25)
CLASSIFICATIONS = {
    0: "success_snapshot", 10: "preflight", 20: "authentication",
    21: "schema_drift", 22: "rate_limited", 23: "source_unavailable",
    24: "transport", 25: "unexpected_limitora_regression",
}
ERROR_CODES = {"unauthorized": 20, "parse_failed": 21, "unsupported": 21,
               "rate_limited": 22, "source_unavailable": 23, "transport": 24}
MAX_RUNTIME = 15
MAX_STDOUT = 512 * 1024


class _PreflightError(Exception):
    pass


def _native_absolute(path: str) -> bool:
    if os.name == "nt":
        drive, tail = ntpath.splitdrive(path)
        return bool(drive and tail.startswith(("/", "\\")))
    return os.path.isabs(path)


def _regular_executable(path: str, *, dotenv: bool = False) -> None:
    if not _native_absolute(path) or os.path.islink(path) or not os.path.isfile(path):
        raise _PreflightError
    try:
        mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    except OSError:
        raise _PreflightError
    if dotenv:
        if os.name != "nt" and mode & 0o077:
            raise _PreflightError
    elif not os.access(path, os.X_OK):
        raise _PreflightError


def _value(value: object) -> str:
    if not isinstance(value, str) or not value or not value.strip() or "\n" in value or "\r" in value or "\0" in value:
        raise _PreflightError
    return value


def _dotenv(path: str) -> dict[str, str]:
    _regular_executable(path, dotenv=True)
    try:
        with open(path, "rb") as source:
            data = source.read()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise _PreflightError
    if b"\0" in data or "\r" in text.replace("\r\n", ""):
        raise _PreflightError
    values: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise _PreflightError
        key, raw = line.split("=", 1)
        if key not in REQUIRED or key in values:
            raise _PreflightError
        values[key] = _value(raw)
    return values


def _secrets(environ: dict[str, str], dotenv_path: str | None) -> dict[str, str]:
    file_values = _dotenv(dotenv_path) if dotenv_path is not None else {}
    result = dict(environ)
    for key in REQUIRED:
        process = _value(environ[key]) if key in environ else None
        file_value = file_values.get(key)
        if process is not None and file_value is not None and process != file_value:
            raise _PreflightError
        selected = process if process is not None else file_value
        if selected is None:
            raise _PreflightError
        result[key] = selected
    return result


def _arguments(argv: list[str]) -> tuple[str, str | None, str]:
    values: dict[str, str] = {}
    i = 0
    while i < len(argv):
        name = argv[i]
        if name not in ("--confirm", "--cli", "--dotenv") or name in values or i + 1 >= len(argv):
            raise _PreflightError
        values[name] = argv[i + 1]
        i += 2
    if values.get("--confirm") != "RUN" or "--cli" not in values:
        raise _PreflightError
    return values["--cli"], values.get("--dotenv"), values["--confirm"]


def _read_stdout(pipe, box: list[bytes]) -> None:
    try:
        box.append(pipe.read(MAX_STDOUT + 1))
    except Exception:
        box.append(b"")


def _child(command: list[str], environ: dict[str, str]) -> tuple[int, bytes] | None:
    try:
        process = subprocess.Popen(command, env=environ, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   shell=False)
    except OSError:
        return None
    box: list[bytes] = []
    reader = threading.Thread(target=_read_stdout, args=(process.stdout, box), daemon=True)
    reader.start()
    try:
        process.wait(timeout=MAX_RUNTIME)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=1)
        return None
    reader.join(timeout=1)
    if reader.is_alive() or not box or len(box[0]) > MAX_STDOUT:
        return None
    return process.returncode, box[0]


def _classify(exit_code: int, stdout: bytes) -> int:
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return SCHEMA
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return SCHEMA
    if "error" in payload:
        error = payload["error"]
        kind = error.get("kind") if isinstance(error, dict) else None
        code = ERROR_CODES.get(kind)
        return code if code is not None and exit_code == 5 else UNEXPECTED
    if payload.get("result") != "snapshot":
        return SCHEMA
    if exit_code != 0:
        return UNEXPECTED
    provider = payload.get("provider_id")
    if not isinstance(provider, dict) or "value" not in provider or "freshness" not in payload:
        return SCHEMA
    if provider["value"] != "opencode-go" or payload["freshness"] != "fresh":
        return UNEXPECTED
    return 0


def run(argv: list[str], *, environ: dict[str, str] | None = None) -> int:
    try:
        cli, dotenv_path, _ = _arguments(argv)
        _regular_executable(cli)
        if dotenv_path is not None:
            _regular_executable(dotenv_path, dotenv=True)
        source = dict(os.environ if environ is None else environ)
        child_env = _secrets(source, dotenv_path)
        child_env.pop("PYTHONPATH", None)
        child_env.pop("PYTHONHOME", None)
        child_env["PYTHONNOUSERSITE"] = "1"
    except _PreflightError:
        return PREFLIGHT
    result = _child([cli, *COMMAND_SUFFIX], child_env)
    if result is None:
        return TRANSPORT
    return _classify(*result)


def main(argv: list[str] | None = None) -> int:
    code = run(sys.argv[1:] if argv is None else argv)
    print(f"OpenCode live result: {CLASSIFICATIONS.get(code, CLASSIFICATIONS[25])}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
