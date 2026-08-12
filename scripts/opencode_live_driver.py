"""Bounded, local-only OpenCode live operator boundary.

Usage: python scripts/opencode_live_driver.py --confirm RUN --cli /abs/limitora
Exit codes: 0 success, 10 preflight, 20-27 classified live failures.
"""

from __future__ import annotations

import json
import ntpath
import os
import signal
import stat
import subprocess
import sys
import threading
import time

WORKSPACE = "LIMITORA_OPENCODE_WORKSPACE_ID"
COOKIE = "LIMITORA_OPENCODE_AUTH_COOKIE"
REQUIRED = (WORKSPACE, COOKIE)
COMMAND_SUFFIX = ("status", "--json", "--provider", "opencode-go", "--opencode-allow-authorized-source")
PREFLIGHT, AUTH, SCHEMA_DRIFT, RATE, SOURCE, TRANSPORT, UNEXPECTED = (10, 20, 21, 22, 23, 24, 25)
PARSE_FAILED, UNSUPPORTED = (26, 27)
CLASSIFICATIONS = {
    0: "success_snapshot", 10: "preflight", 20: "authentication",
    21: "schema_drift", 22: "rate_limited", 23: "source_unavailable",
    24: "transport", 25: "unexpected_limitora_regression",
    26: "parse_failed", 27: "unsupported",
}
ERROR_CODES = {"unauthorized": 20, "parse_failed": 26, "unsupported": 27,
               "rate_limited": 22, "source_unavailable": 23, "transport": 24}
ERROR_FIELDS = {"kind", "provider_id", "safe_message", "retryable"}
MAX_RUNTIME = 15
MAX_STDOUT = 512 * 1024
MAX_DOTENV_BYTES = 16 * 1024
MAX_DOTENV_LINES = 32
MAX_VALUE_LENGTH = 8 * 1024


class _PreflightError(Exception):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


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
        if not _dotenv_mode_is_private(mode):
            raise _PreflightError
    elif not os.access(path, os.X_OK):
        raise _PreflightError


def _dotenv_mode_is_private(mode: int, *, platform: str | None = None) -> bool:
    return (os.name if platform is None else platform) == "nt" or not mode & 0o077


def _value(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > MAX_VALUE_LENGTH
            or not value.strip() or "\n" in value or "\r" in value or "\0" in value):
        raise _PreflightError
    return value


def _dotenv(path: str) -> dict[str, str]:
    _regular_executable(path, dotenv=True)
    try:
        with open(path, "rb") as source:
            data = source.read(MAX_DOTENV_BYTES + 1)
        if len(data) > MAX_DOTENV_BYTES:
            raise _PreflightError
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise _PreflightError
    if b"\0" in data or "\r" in text.replace("\r\n", ""):
        raise _PreflightError
    lines = text.replace("\r\n", "\n").split("\n")
    if len(lines) > MAX_DOTENV_LINES:
        raise _PreflightError
    values: dict[str, str] = {}
    for line in lines:
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
    result: dict[str, str] = {}
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


def _child_environment(secrets: dict[str, str]) -> dict[str, str]:
    """Pass only the provider inputs and the isolated Python-site setting."""
    return {WORKSPACE: secrets[WORKSPACE], COOKIE: secrets[COOKIE], "PYTHONNOUSERSITE": "1"}


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


def _read_stdout(pipe, box: list[bytes], overflow: threading.Event) -> None:
    try:
        output = bytearray()
        while True:
            chunk = pipe.read(min(64 * 1024, MAX_STDOUT + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_STDOUT:
                overflow.set()
                break
        box.append(bytes(output))
    except Exception:
        box.append(b"")


def _signal_group(process, signal_value: int) -> bool:
    try:
        os.killpg(process.pid, signal_value)
        return True
    except ProcessLookupError:
        return True
    except Exception:
        return False


def _cleanup_group(process, reader: threading.Thread, *, allowance: float) -> bool:
    deadline = time.monotonic() + allowance
    failed = not _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        failed = True
    kill_signal = getattr(signal, "SIGKILL", None)
    if kill_signal is None:
        failed = True
    else:
        failed = not _signal_group(process, kill_signal) or failed
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except Exception:
        failed = True
    try:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    except Exception:
        failed = True
    return not failed and not reader.is_alive()


def _child(command: list[str], environ: dict[str, str]) -> tuple[int, bytes] | None:
    if os.name == "nt":
        return None
    try:
        process = subprocess.Popen(command, env=environ, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   shell=False, start_new_session=True)
    except OSError:
        return None
    box: list[bytes] = []
    overflow = threading.Event()
    reader = threading.Thread(target=_read_stdout, args=(process.stdout, box, overflow), daemon=True)
    reader.start()
    deadline = time.monotonic() + MAX_RUNTIME
    try:
        while True:
            if overflow.is_set():
                if not _cleanup_group(process, reader, allowance=1):
                    return None
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if not _cleanup_group(process, reader, allowance=1):
                    return None
                return None
            try:
                process.wait(timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                continue
    except Exception:
        _cleanup_group(process, reader, allowance=1)
        return None
    reader.join(timeout=1)
    if reader.is_alive() or overflow.is_set() or not box or len(box[0]) > MAX_STDOUT:
        _cleanup_group(process, reader, allowance=1)
        return None
    return process.returncode, box[0]


def _classify(exit_code: int, stdout: bytes) -> int:
    try:
        payload = json.loads(stdout.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return SCHEMA_DRIFT
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("version"), int)
        or isinstance(payload["version"], bool)
        or payload["version"] != 1
    ):
        return SCHEMA_DRIFT
    if "error" in payload:
        error = payload["error"]
        if set(payload) != {"version", "error"} or not isinstance(error, dict) or set(error) != ERROR_FIELDS:
            return SCHEMA_DRIFT
        kind = error["kind"]
        if not isinstance(kind, str) or kind not in ERROR_CODES:
            return UNEXPECTED
        provider = error["provider_id"]
        if (
            not isinstance(provider, dict)
            or set(provider) != {"value"}
            or not isinstance(provider["value"], str)
            or not provider["value"]
            or provider["value"].strip() != provider["value"]
            or not isinstance(error["safe_message"], str)
            or not error["safe_message"]
            or error["safe_message"].strip() != error["safe_message"]
            or not isinstance(error["retryable"], bool)
        ):
            return SCHEMA_DRIFT
        if provider["value"] != "opencode-go":
            return UNEXPECTED
        return ERROR_CODES[kind] if exit_code == 5 else UNEXPECTED
    if payload.get("result") != "snapshot":
        return SCHEMA_DRIFT
    if exit_code != 0:
        return UNEXPECTED
    provider = payload.get("provider_id")
    if not isinstance(provider, dict) or "value" not in provider or "freshness" not in payload:
        return SCHEMA_DRIFT
    if provider["value"] != "opencode-go" or payload["freshness"] != "fresh":
        return UNEXPECTED
    windows = payload.get("quota_windows")
    if not isinstance(windows, list) or not windows:
        return SCHEMA_DRIFT
    if any(
        not isinstance(window, dict)
        or window.get("kind") != "commercial_quota"
        or not isinstance(window.get("scope"), str)
        or not window["scope"]
        or not isinstance(window.get("period"), str)
        or not window["period"]
        for window in windows
    ):
        return SCHEMA_DRIFT
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
    if os.name == "nt":
        return TRANSPORT
    result = _child([cli, *COMMAND_SUFFIX], _child_environment(child_env))
    if result is None:
        return TRANSPORT
    return _classify(*result)


def main(argv: list[str] | None = None) -> int:
    code = run(sys.argv[1:] if argv is None else argv)
    print(f"OpenCode live result: {CLASSIFICATIONS.get(code, CLASSIFICATIONS[25])}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
