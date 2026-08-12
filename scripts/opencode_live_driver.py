"""Bounded, local-only OpenCode live operator boundary.

Usage: python scripts/opencode_live_driver.py --confirm RUN --cli /abs/limitora
Exit codes: 0 success, 10 preflight, 20-31 classified live failures.
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

API_KEY = "LIMITORA_OPENCODE_API_KEY"
COMMAND_SUFFIX = ("status", "--json", "--provider", "opencode-go", "--opencode-allow-authorized-source")
PREFLIGHT, AUTH, SCHEMA_DRIFT, RATE, SOURCE, TRANSPORT, UNEXPECTED = (10, 20, 21, 22, 23, 24, 25)
PARSE_FAILED, UNSUPPORTED = (26, 27)
PARSE_FAILED_NO_VALID_QUOTA_WINDOW = 29
PARSE_FAILED_INVALID_UTF8_JSON, PARSE_FAILED_NON_OBJECT_JSON = (30, 31)
CLASSIFICATIONS = {
    0: "success_snapshot", 10: "preflight", 20: "authentication",
    21: "schema_drift", 22: "rate_limited", 23: "source_unavailable",
    24: "transport", 25: "unexpected_limitora_regression",
    26: "parse_failed", 27: "unsupported", 29: "parse_failed_no_valid_quota_window",
    30: "parse_failed_invalid_utf8_json", 31: "parse_failed_non_object_json",
}
ERROR_CODES = {"unauthorized": 20, "parse_failed": 26, "unsupported": 27,
               "rate_limited": 22, "source_unavailable": 23, "transport": 24}
OPENCODE_PARSE_FAILURE_CODES = {
    "OpenCode Go response has no valid quota window": PARSE_FAILED_NO_VALID_QUOTA_WINDOW,
    "OpenCode Go response is not valid UTF-8 JSON": PARSE_FAILED_INVALID_UTF8_JSON,
    "OpenCode Go response JSON root is not an object": PARSE_FAILED_NON_OBJECT_JSON,
}
ERROR_FIELDS = {"kind", "provider_id", "safe_message", "retryable"}
MAX_RUNTIME = 15
MAX_STDOUT = 512 * 1024
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


def _regular_executable(path: str) -> None:
    if not _native_absolute(path) or os.path.islink(path) or not os.path.isfile(path):
        raise _PreflightError
    try:
        mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    except OSError:
        raise _PreflightError
    if not os.access(path, os.X_OK):
        raise _PreflightError


def _value(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > MAX_VALUE_LENGTH
            or not value.strip() or "\n" in value or "\r" in value or "\0" in value):
        raise _PreflightError
    return value


def _child_environment(api_key: str) -> dict[str, str]:
    """Pass only the provider inputs and the isolated Python-site setting."""
    return {API_KEY: api_key, "PYTHONNOUSERSITE": "1"}


def _arguments(argv: list[str]) -> tuple[str, str]:
    values: dict[str, str] = {}
    i = 0
    while i < len(argv):
        name = argv[i]
        if name not in ("--confirm", "--cli") or name in values or i + 1 >= len(argv):
            raise _PreflightError
        values[name] = argv[i + 1]
        i += 2
    if values.get("--confirm") != "RUN" or "--cli" not in values:
        raise _PreflightError
    return values["--cli"], values["--confirm"]


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
        if kind == "parse_failed" and error["safe_message"] in OPENCODE_PARSE_FAILURE_CODES:
            return OPENCODE_PARSE_FAILURE_CODES[error["safe_message"]] if exit_code == 5 else UNEXPECTED
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
        cli, _ = _arguments(argv)
        _regular_executable(cli)
        source = dict(os.environ if environ is None else environ)
        api_key = _value(source[API_KEY]) if API_KEY in source else None
        if api_key is None:
            raise _PreflightError
    except _PreflightError:
        return PREFLIGHT
    if os.name == "nt":
        return TRANSPORT
    result = _child([cli, *COMMAND_SUFFIX], _child_environment(api_key))
    if result is None:
        return TRANSPORT
    return _classify(*result)


def main(argv: list[str] | None = None) -> int:
    code = run(sys.argv[1:] if argv is None else argv)
    print(f"OpenCode live result: {CLASSIFICATIONS.get(code, CLASSIFICATIONS[25])}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
