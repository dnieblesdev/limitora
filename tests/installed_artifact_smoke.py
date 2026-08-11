"""Smoke-test an installed Limitora artifact outside its source checkout."""

from __future__ import annotations

import argparse, hashlib, importlib.util, json, os, platform, shutil, site, socket, subprocess, sys, sysconfig, tempfile, threading, time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import importlib.metadata as metadata
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import quote

from limitora._runner_path import _is_native_absolute_runner_path


HELP = "limitora status [--help] [--json] [--provider {codex,opencode-go}] [flags]\n  codex:        --runner PATH [--runner ARG ...]\n                A single absolute PATH uses 'app-server --stdio'.\n                [--codex-allow-authorized-source]\n  opencode-go:  --workspace-id ID --auth-cookie COOKIE\n                [--endpoint URL] [--timeout SECONDS]\n                [--opencode-allow-authorized-source]\n                or LIMITORA_OPENCODE_WORKSPACE_ID /\n                LIMITORA_OPENCODE_AUTH_COOKIE\nWithout --provider, status prints 'no provider configured' to stderr (exit 4).\n"
LIVE_ENV = "LIMITORA_CODEX_LIVE"
ROUTE_PORT_ENV = "LIMITORA_TEST_HTTPX_ROUTE_PORT"
ROUTE_SCENARIO_ENV = "LIMITORA_TEST_HTTPX_SCENARIO"
ROUTE_SCENARIOS = ("valid", "partial", "json", "html", "401", "403", "429", "5xx", "redirect", "timeout", "connection", "declared", "streamed")
_SECRETS = ("workspace/raw-path-marker", "cookie/raw-header-marker", "raw-payload-marker", "proxy/raw-proxy-marker")


def route_config(environ: Mapping[str, str]) -> tuple[int, str]:
    port, scenario = environ.get(ROUTE_PORT_ENV), environ.get(ROUTE_SCENARIO_ENV)
    check(port is not None and port.isdigit() and 1 <= int(port) <= 65535, "invalid test route port")
    check(scenario in ROUTE_SCENARIOS, "invalid test route scenario")
    return int(port), scenario


def sitecustomize_path(site_packages: Path) -> Path:
    return site_packages / "sitecustomize.py"


def sitecustomize_collision(path: Path) -> bool:
    if path.exists():
        return True
    spec = importlib.util.find_spec("sitecustomize")
    return bool(spec and spec.origin and Path(spec.origin).resolve() != path.resolve())


ROUTE_SHIM = r'''import os
from urllib.parse import quote
import httpx

class RouteTransport(httpx.BaseTransport):
    def __init__(self, port):
        self._transport = httpx.HTTPTransport()
        self._port = int(port)
    def handle_request(self, request):
        workspace = os.environ.get("LIMITORA_OPENCODE_WORKSPACE_ID", "")
        cookie = os.environ.get("LIMITORA_OPENCODE_AUTH_COOKIE", "")
        expected = "https://opencode.ai/workspace/" + quote(workspace, safe="") + "/go"
        if (request.method != "GET" or str(request.url) != expected
                or request.headers.get_list("host") != ["opencode.ai"]
                or request.headers.get_list("cookie") != ["auth=" + cookie]
                or request.content != b""):
            raise AssertionError("HTTPX request contract failed")
        url = request.url.copy_with(scheme="http", authority="127.0.0.1:" + str(self._port))
        rewritten = httpx.Request(request.method, url, headers=request.headers,
                                  content=request.content)
        return self._transport.handle_request(rewritten)
    def close(self):
        self._transport.close()

class RoutedClient(httpx.Client):
    def __init__(self, *args, **kwargs):
        timeout = kwargs.get("timeout")
        if (kwargs.get("follow_redirects") is not False or kwargs.get("trust_env") is not False
                or not isinstance(timeout, httpx.Timeout) or not all(value and value > 0 for value in (timeout.connect, timeout.read, timeout.write, timeout.pool)) or "transport" in kwargs
                or "proxy" in kwargs or "proxies" in kwargs):
            raise AssertionError("HTTPX client contract failed")
        kwargs["transport"] = RouteTransport(os.environ["LIMITORA_TEST_HTTPX_ROUTE_PORT"])
        super().__init__(*args, **kwargs)

httpx.Client = RoutedClient
'''


def install_sitecustomize(site_packages: Path) -> tuple[Path, str]:
    path = sitecustomize_path(site_packages)
    check(not sitecustomize_collision(path), "sitecustomize collision detected")
    try:
        with path.open("x", encoding="ascii", newline="") as stream:
            stream.write(ROUTE_SHIM)
    except FileExistsError:
        raise AssertionError("sitecustomize collision detected")
    return path, ROUTE_SHIM


def cleanup_sitecustomize(path: Path, owned: str) -> None:
    if path.is_file() and path.read_text(encoding="ascii") == owned:
        path.unlink()


def redacted(text: str) -> bool:
    lowered = text.casefold()
    return not any(marker.casefold() in lowered for marker in _SECRETS)


class LivePreflightKind(str, Enum):
    SKIPPED = "skipped"
    INVALID_OPT_IN = "invalid_opt_in"
    MISSING = "missing"
    RELATIVE = "relative"
    INVALID = "invalid"
    DIRECTORY = "directory"
    NOT_EXECUTABLE = "not_executable"
    READY = "ready"


@dataclass(frozen=True)
class LivePreflight:
    kind: LivePreflightKind
    runner: str | None = None

    @property
    def safe_message(self) -> str:
        return {
            LivePreflightKind.INVALID_OPT_IN: "live Codex opt-in must equal 1",
            LivePreflightKind.MISSING: "live Codex executable was not discovered",
            LivePreflightKind.RELATIVE: "discovered Codex executable is not host-absolute",
            LivePreflightKind.INVALID: "discovered Codex executable is invalid",
            LivePreflightKind.DIRECTORY: "discovered Codex executable is a directory",
            LivePreflightKind.NOT_EXECUTABLE: "discovered Codex executable is not executable",
        }.get(self.kind, "live Codex preflight skipped")


class LiveOutcomeKind(str, Enum):
    SUCCESS = "success"
    PROVIDER_ERROR = "provider_error"
    EXIT = "exit"


@dataclass(frozen=True)
class LiveOutcome:
    kind: LiveOutcomeKind
    exit_code: int


def preflight_live_codex(
    environ: Mapping[str, str],
    *,
    which: Callable[[str], str | None] | None = None,
) -> LivePreflight:
    value = environ.get(LIVE_ENV)
    if value is None:
        return LivePreflight(LivePreflightKind.SKIPPED)
    if value != "1":
        return LivePreflight(LivePreflightKind.INVALID_OPT_IN)
    candidate = (shutil.which if which is None else which)("codex")
    if candidate is None:
        return LivePreflight(LivePreflightKind.MISSING)
    if not _is_native_absolute_runner_path(candidate):
        return LivePreflight(LivePreflightKind.RELATIVE)
    path = Path(candidate)
    if not path.exists():
        return LivePreflight(LivePreflightKind.INVALID)
    if path.is_dir():
        return LivePreflight(LivePreflightKind.DIRECTORY)
    if not path.is_file():
        return LivePreflight(LivePreflightKind.INVALID)
    if not os.access(path, os.X_OK):
        return LivePreflight(LivePreflightKind.NOT_EXECUTABLE)
    return LivePreflight(LivePreflightKind.READY, candidate)


def classify_live_outcome(exit_code: int, stdout: str) -> LiveOutcome:
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return LiveOutcome(LiveOutcomeKind.EXIT, exit_code)
    if exit_code == 0 and isinstance(payload, dict) and payload.get("result") == "snapshot":
        provider = payload.get("provider_id")
        if isinstance(provider, dict) and provider.get("value") == "codex":
            return LiveOutcome(LiveOutcomeKind.SUCCESS, exit_code)
    if exit_code == 5 and isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return LiveOutcome(LiveOutcomeKind.PROVIDER_ERROR, exit_code)
    return LiveOutcome(LiveOutcomeKind.EXIT, exit_code)


def check(condition: bool, message: str) -> None:
    if not condition: raise AssertionError(message)


def under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def verify_artifact(
    artifact: Path,
    manifest: Path,
    source_sha: Path,
    expected_source_sha: str,
    expected_version: str,
    wheelhouse: Path,
) -> str:
    rows = [(name, digest) for digest, name in (line.split("  ", 1) for line in manifest.read_text(encoding="ascii").splitlines())]
    check(rows == sorted(rows), "manifest is not sorted")
    distribution_rows, wheelhouse_rows, receipt_rows = ([(name, digest) for name, digest in rows if "/" not in name and name.endswith((".whl", ".tar.gz"))], [(name, digest) for name, digest in rows if name.startswith("wheelhouse/")], [(name, digest) for name, digest in rows if name == "source-sha.txt"])
    check(all(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest) for _, digest in rows), "invalid manifest digest")
    check(len(receipt_rows) == 1 and wheelhouse_rows and len(distribution_rows) + len(wheelhouse_rows) + 1 == len(rows) and all(name.endswith(".whl") and not Path(name).name.startswith("limitora-") for name, _ in wheelhouse_rows), "invalid manifest boundary")
    check({f"wheelhouse/{path.name}" for path in wheelhouse.glob("*.whl")} == {name for name, _ in wheelhouse_rows}, "wheelhouse inventory mismatch")
    expected_names = {f"limitora-{expected_version}-py3-none-any.whl", f"limitora-{expected_version}.tar.gz"}; check({name for name, _ in distribution_rows} == expected_names, "manifest artifact names mismatch")
    recorded = dict(distribution_rows).get(artifact.name); check(recorded is not None, "artifact is absent from manifest")
    actual = hashlib.sha256(artifact.read_bytes()).hexdigest(); check(actual == recorded, "artifact SHA-256 does not match manifest")
    for name, digest in wheelhouse_rows:
        check(hashlib.sha256((wheelhouse / name.removeprefix("wheelhouse/")).read_bytes()).hexdigest() == digest, f"wheelhouse SHA-256 mismatch: {name}")
    check(hashlib.sha256(source_sha.read_bytes()).hexdigest() == receipt_rows[0][1], "source receipt SHA-256 mismatch"); recorded_source = source_sha.read_text(encoding="ascii")
    check(recorded_source == expected_source_sha + "\n" and len(expected_source_sha) == 40 and all(c in "0123456789abcdef" for c in expected_source_sha), "invalid source SHA evidence")
    return actual
def assert_isolated(checkout: Path, expected_version: str) -> tuple[Path, object]:
    site_packages = Path(sysconfig.get_paths()["purelib"]).resolve(); check(site.ENABLE_USER_SITE is False, "user site is enabled")
    check("PYTHONPATH" not in os.environ and "PYTHONHOME" not in os.environ and os.environ.get("PYTHONNOUSERSITE") == "1", "Python path or user-site override remains set")
    checkout = checkout.resolve()
    for entry in filter(None, sys.path): check(not under(Path(entry), checkout), f"checkout path remains in sys.path: {entry}")

    import limitora

    check(under(Path(limitora.__file__), site_packages), "limitora.__file__ is outside site-packages"); distribution = metadata.distribution("limitora")
    check(distribution.version == expected_version, "installed version mismatch")
    check(under(Path(distribution.locate_file("limitora")), site_packages), "distribution is outside site-packages")
    for file in distribution.files or ():
        check(str(checkout).casefold() not in str(file).casefold(), "distribution metadata contains a checkout path")
    direct_url = distribution.read_text("direct_url.json")
    if direct_url:
        check(not json.loads(direct_url).get("dir_info", {}).get("editable"), "editable installation metadata found")
        check(str(checkout).casefold() not in direct_url.casefold(), "direct URL contains checkout path")
    for path in site_packages.rglob("*.egg-link"): raise AssertionError(f"editable egg-link found: {path}")
    for path in site_packages.glob("*.pth"):
        text = path.read_text(encoding="utf-8", errors="replace")
        check(str(checkout).casefold() not in text.casefold() and "egg-link" not in text.casefold(),
              f"editable finder found: {path}")
    return site_packages, distribution
def api_smoke() -> None:
    import limitora
    from limitora.providers import ProviderDetection, ProviderRequest

    check(len(limitora.__all__) == len(set(limitora.__all__)), "public API contains duplicate symbols")
    for name in limitora.__all__:
        check(getattr(limitora, name) is not None, f"missing public symbol: {name}")
    now = datetime.now(timezone.utc); provider_id = limitora.ProviderId("installed-smoke"); source = limitora.SourceMetadata("installed-smoke")
    status = limitora.ProviderStatus(provider_id, limitora.ProviderState.AVAILABLE, now); snapshot = limitora.ProviderSnapshot(provider_id, status, now, now, source)
    check(snapshot.provider_id == provider_id, "public snapshot construction failed"); freshness = limitora.FreshnessPolicy(timedelta(minutes=5))
    request = limitora.StatusRequest(frozenset({limitora.MetricKind.COMMERCIAL_QUOTA}), limitora.AuthorizationPolicy.DENY_AUTHORIZED_SOURCE, freshness)

    offline_provider_id = provider_id

    class OfflineProvider:
        provider_id = offline_provider_id

        def detect(self):
            return ProviderDetection(offline_provider_id, False, now, "offline fixture")

        def fetch(self, request: ProviderRequest):
            raise AssertionError("offline provider must not fetch")

    result = limitora.StatusClient(OfflineProvider(), limitora.CurrentClock()).read_status(request); check(isinstance(result, limitora.StatusUndetectedResult), "public StatusClient flow failed")
    runner = str(Path.cwd() / "not-a-real-codex-runner"); check(isinstance(limitora.activate_provider(limitora.CodexJsonlConfig((runner,))), limitora.StatusClient), "Codex public construction failed")
    check(isinstance(limitora.activate_provider(limitora.OpenCodeGoConfig("workspace", "cookie")), limitora.StatusClient), "OpenCode Go public construction failed")


def cli_smoke(cli: Path) -> None:
    check(under(cli, Path(sys.prefix)), "CLI is not installed in the active virtual environment")
    environment = os.environ.copy(); environment.pop("PYTHONPATH", None); environment.pop("PYTHONHOME", None); environment["PYTHONNOUSERSITE"] = "1"
    for arguments, expected in (
        (("status", "--help"), (0, HELP, "")),
        (("status",), (4, "", "ERROR: no provider configured\n")),
    ):
        completed = subprocess.run([str(cli), *arguments], cwd=Path.cwd(), env=environment, capture_output=True, text=True, check=False)
        check((completed.returncode, completed.stdout, completed.stderr) == expected, f"unexpected CLI result for {' '.join(arguments)}")


CODEX_FIXTURE = r'''import json, os, sys, time
payload = {"rateLimits": {"limitId": "codex", "planType": "plus",
    "primary": {"windowDurationMins": 300, "usedPercent": 25, "resetsAt": 2000000000},
    "secondary": {"windowDurationMins": 10080, "usedPercent": 50, "resetsAt": 2000000000}}}
methods = []
client_info_present = False
receipt_path = os.environ.get("LIMITORA_CODEX_RECEIPT")
if receipt_path is None:
    raise SystemExit("fixture receipt is not configured")
private_names = {"limitora_opencode_workspace_id", "limitora_opencode_auth_cookie"}
if any(name.casefold() in private_names for name in os.environ):
    raise SystemExit("fixture received a private environment value")
for expected in ("initialize", "initialized", "account/rateLimits/read"):
    raw = sys.stdin.buffer.readline()
    if not raw: raise SystemExit("fixture input ended")
    message = json.loads(raw)
    if message.get("method") != expected: raise SystemExit("fixture method order mismatch")
    if expected == "initialize":
        if set(message) != {"id", "method", "params"} or message.get("id") != 1:
            raise SystemExit("fixture initialize envelope mismatch")
        params = message.get("params")
        client_info = params.get("clientInfo") if isinstance(params, dict) else None
        if not isinstance(client_info, dict) or not client_info.get("name") or not client_info.get("version"):
            raise SystemExit("fixture client info missing")
        client_info_present = True
    elif expected == "initialized":
        if set(message) != {"method", "params"} or message.get("params") != {}:
            raise SystemExit("fixture initialized envelope mismatch")
    elif set(message) != {"id", "method", "params"} or message.get("id") != 2 or message.get("params") != {}:
        raise SystemExit("fixture rate limit envelope mismatch")
    if "jsonrpc" in message: raise SystemExit("fixture received jsonrpc envelope")
    methods.append(expected)
    if expected == "initialize": result = {"id": 1, "result": {}}
    elif expected == "account/rateLimits/read":
        with open(receipt_path, "w", encoding="ascii") as receipt:
            json.dump({"methods": methods, "client_info": client_info_present, "private_env": False, "pid": os.getpid()}, receipt)
        result = {"id": 2, "result": payload}
    else: continue
    print(json.dumps(result, separators=(",", ":")), flush=True)
time.sleep(60)'''


def codex_smoke(cli: Path) -> None:
    check(Path(sys.executable).is_absolute(), "Codex interpreter path is not absolute")
    with tempfile.TemporaryDirectory(prefix="limitora-codex-smoke-") as directory:
        root = Path(directory); fixture = root / "fake-codex.py"; receipt = root / "receipt.json"
        fixture.write_text(CODEX_FIXTURE, encoding="ascii")
        environment = os.environ.copy(); environment["LIMITORA_CODEX_RECEIPT"] = str(receipt)
        environment["LIMITORA_OPENCODE_WORKSPACE_ID"] = "synthetic-workspace-secret"
        environment["lImItOrA_oPeNcOdE_aUtH_cOoKiE"] = "synthetic-auth-cookie"
        command = [str(cli), "status", "--provider", "codex", "--runner", str(sys.executable), "--runner", str(fixture), "--codex-allow-authorized-source"]
        for arguments, expected_json in ((command, False), (command[:2] + ["--json"] + command[2:], True)):
            if receipt.exists(): receipt.unlink()
            started = time.monotonic()
            completed = subprocess.run(arguments, cwd=Path.cwd(), env=environment, capture_output=True, text=True, check=False, timeout=10)
            check(time.monotonic() - started < 10, "Codex child cleanup exceeded the smoke bound")
            check(completed.returncode == 0, "installed Codex CLI failed")
            output = completed.stdout + completed.stderr
            for marker in ("rateLimits", "limitId", "Traceback", "auth", "synthetic-workspace-secret", "synthetic-auth-cookie"):
                check(marker.casefold() not in output.casefold(), "installed Codex output leaked unsafe evidence")
            receipt_data = json.loads(receipt.read_text(encoding="ascii"))
            check(receipt_data["methods"] == ["initialize", "initialized", "account/rateLimits/read"], "Codex fixture order mismatch")
            check(receipt_data == {"methods": receipt_data["methods"], "client_info": True, "private_env": False, "pid": receipt_data["pid"]}, "Codex receipt contains non-structural evidence")
            if expected_json:
                payload = json.loads(completed.stdout)
                check(payload["result"] == "snapshot" and payload["provider_id"] == {"value": "codex"}, "installed Codex JSON identity mismatch")
                windows = {(window["period"], window["plan_id"], window["used"]["value"], window["remaining"]["value"]) for window in payload["quota_windows"]}
                check(windows == {("five_hour", "plus", "25", "75"), ("weekly", "plus", "50", "50")}, "installed Codex JSON windows mismatch")
            else:
                check("PROVIDER: codex" in completed.stdout and "SOURCE: codex-app-server-v2" in completed.stdout, "installed Codex human identity mismatch")
                check("PERIOD: five_hour" in completed.stdout and "PERIOD: weekly" in completed.stdout, "installed Codex human windows mismatch")
        return


def live_smoke(cli: Path, environ: Mapping[str, str]) -> str:
    preflight = preflight_live_codex(environ)
    if preflight.kind is LivePreflightKind.SKIPPED:
        return preflight.kind.value
    check(preflight.kind is LivePreflightKind.READY, preflight.safe_message)
    assert preflight.runner is not None
    command = [str(cli), "status", "--json", "--provider", "codex", "--runner", preflight.runner, "--codex-allow-authorized-source"]
    try:
        completed = subprocess.run(command, cwd=Path.cwd(), env=dict(environ), capture_output=True, text=True, check=False, timeout=30)
    except subprocess.TimeoutExpired:
        check(False, "live Codex CLI timed out")
    except OSError:
        check(False, "live Codex CLI could not start")
    outcome = classify_live_outcome(completed.returncode, completed.stdout)
    check(outcome.kind in (LiveOutcomeKind.SUCCESS, LiveOutcomeKind.PROVIDER_ERROR), "live Codex CLI returned an unclassified result")
    return outcome.kind.value


def legacy_opencode_smoke(require_dependency: bool, site_packages: Path) -> None:
    import limitora
    from limitora.providers import AuthorizationPolicy, ProviderError, ProviderErrorKind
    from limitora.providers._opencode_go_httpx import _HttpxOpenCodeGoTransport

    if require_dependency:
        import httpx
        check(under(Path(httpx.__file__), site_packages), "imported httpx is outside site-packages")
        check(metadata.version("httpx") == httpx.__version__, "imported httpx version metadata mismatch")
    else:
        check(importlib.util.find_spec("httpx") is None, "base installation unexpectedly contains httpx")

    client = limitora.activate_provider(limitora.OpenCodeGoConfig("space/id", "synthetic-cookie"))
    transport = client._service._provider._transport
    check(isinstance(transport, _HttpxOpenCodeGoTransport), "production OpenCode Go transport was bypassed")
    request = limitora.StatusRequest(
        frozenset({limitora.MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
        limitora.FreshnessPolicy(timedelta(minutes=5)),
    )
    try:
        client.read_status(request)
    except ProviderError as error:
        check(error.kind is ProviderErrorKind.UNAUTHORIZED, "OpenCode Go deny path changed")
    else:
        raise AssertionError("OpenCode Go deny path did not fail")
    if not require_dependency:
        return

    body = b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},"weeklyUsage":{"usagePercent":50,"resetInSec":20},"monthlyUsage":{"usagePercent":75,"resetInSec":30}}'
    clients = []

    class Response:
        status_code = 200

        def __init__(self):
            self.headers = {"content-length": str(len(body)), "content-type": "application/json"}

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_bytes(self):
            yield body[:len(body) // 2]
            yield body[len(body) // 2:]

    class Client:
        def __init__(self, **options):
            check(options["follow_redirects"] is False and options["trust_env"] is False, "HTTPX client is not hermetic")
            clients.append(self)

        def __enter__(self): return self
        def __exit__(self, *args): return False

        def stream(self, method, url, **kwargs):
            check((method, url, kwargs) == ("GET", "https://opencode.ai/workspace/space%2Fid/go", {"headers": {"Cookie": "auth=synthetic-cookie"}, "content": None}), "unexpected OpenCode Go request URL/headers/body")
            return Response()

    def blocked(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    class BlockedSocket:
        def __init__(self, *args, **kwargs): blocked(*args, **kwargs)
        def connect(self, *args, **kwargs): blocked(*args, **kwargs)
        def connect_ex(self, *args, **kwargs): blocked(*args, **kwargs)

    import httpx
    originals = httpx.Client, socket.getaddrinfo, socket.socket, socket.create_connection
    httpx.Client, socket.getaddrinfo, socket.socket, socket.create_connection = Client, blocked, BlockedSocket, blocked
    try:
        request = limitora.StatusRequest(frozenset({limitora.MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE, limitora.FreshnessPolicy(timedelta(minutes=5)))
        result = client.read_status(request)
        check(isinstance(result, limitora.StatusSnapshotResult) and result.snapshot.status.state.value == "available",
              "OpenCode Go productive response mapping failed")
        check(tuple(window.period for window in result.snapshot.quota_windows) == ("five_hour", "weekly", "monthly"),
              "OpenCode Go quota mapping failed")
        check(len(clients) == 1, "_HttpxOpenCodeGoTransport was not invoked")
        for attempt in (lambda: socket.getaddrinfo("opencode.ai", 443), lambda: socket.create_connection(("opencode.ai", 443)), lambda: socket.socket(), lambda: BlockedSocket.connect(None, ("opencode.ai", 443)), lambda: BlockedSocket.connect_ex(None, ("opencode.ai", 443))):
            try: attempt()
            except AssertionError: pass
            else: raise AssertionError("network guard did not raise")
    finally:
        httpx.Client, socket.getaddrinfo, socket.socket, socket.create_connection = originals


def opencode_smoke(require_dependency: bool, site_packages: Path) -> tuple[str, ...]:
    import limitora
    from limitora.providers import AuthorizationPolicy, ProviderError, ProviderErrorKind
    from limitora.providers._opencode_go_httpx import _HttpxOpenCodeGoTransport
    if not require_dependency:
        check(importlib.util.find_spec("httpx") is None, "base installation unexpectedly contains httpx")
        client = limitora.activate_provider(limitora.OpenCodeGoConfig("space/id", "synthetic-cookie"))
        check(isinstance(client._service._provider._transport, _HttpxOpenCodeGoTransport), "production OpenCode Go transport was bypassed")
        request = limitora.StatusRequest(frozenset({limitora.MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.DENY_AUTHORIZED_SOURCE, limitora.FreshnessPolicy(timedelta(minutes=5)))
        try: client.read_status(request)
        except ProviderError as error: check(error.kind is ProviderErrorKind.UNAUTHORIZED, "OpenCode Go deny path changed")
        else: raise AssertionError("OpenCode Go deny path did not fail")
        return ()
    import httpx
    check(under(Path(httpx.__file__), site_packages), "imported httpx is outside site-packages")
    check(metadata.version("httpx") == httpx.__version__, "installed httpx version metadata mismatch")
    owned_path, owned = install_sitecustomize(site_packages)
    workspace, cookie = _SECRETS[:2]; payload = b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},"weeklyUsage":{"usagePercent":50,"resetInSec":20},"monthlyUsage":{"usagePercent":75,"resetInSec":30}}'; results = []
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    try:
        with tempfile.TemporaryDirectory(prefix="limitora-opencode-smoke-") as directory:
            receipt_path = Path(directory) / "receipt.json"
            for scenario in ROUTE_SCENARIOS:
                state = {"requests": 0, "contract": True}; expected_path = "/workspace/" + quote(workspace, safe="") + "/go"
                class Handler(BaseHTTPRequestHandler):
                    protocol_version = "HTTP/1.0"
                    def log_message(self, *args): pass
                    def do_GET(self):
                        state["requests"] += 1; length = self.headers.get("Content-Length")
                        if length and length.isdigit() and int(length): self.rfile.read(int(length)); state["contract"] = False
                        state["contract"] &= (self.path == expected_path and self.headers.get_all("Host") == ["opencode.ai"] and self.headers.get_all("Cookie") == ["auth=" + cookie] and length in (None, "0"))
                        if scenario == "timeout": time.sleep(12); return
                        if scenario == "redirect": self.send_response(302); self.send_header("Location", "https://opencode.ai/login"); self.end_headers(); return
                        if scenario == "declared": self.send_response(200); self.send_header("Content-Length", str(512 * 1024 + 1)); self.end_headers(); return
                        if scenario == "streamed": self.send_response(200); self.end_headers(); self.wfile.write(b"x" * (512 * 1024 + 1)); self.wfile.flush(); return
                        status = int(scenario) if scenario in ("401", "403", "429") else 503 if scenario == "5xx" else 200
                        body = payload if scenario == "valid" else payload.replace(b'"weeklyUsage":{"usagePercent":50,"resetInSec":20}', b'"weeklyUsage":{"usagePercent":"bad","resetInSec":20}') if scenario == "partial" else b"{raw-payload-marker" if scenario == "json" else b"<html>raw-payload-marker login</html>" if scenario == "html" else b"raw-payload-marker"
                        self.send_response(status); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                server = thread = None
                try:
                    if scenario != "connection":
                        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); server.daemon_threads = True; thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); port = server.server_address[1]
                    else:
                        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM); probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]; probe.close()
                    environment = os.environ.copy(); environment.update({"LIMITORA_OPENCODE_WORKSPACE_ID": workspace, "LIMITORA_OPENCODE_AUTH_COOKIE": cookie, ROUTE_PORT_ENV: str(port), ROUTE_SCENARIO_ENV: scenario, "HTTP_PROXY": "http://127.0.0.1:1/proxy/raw-proxy-marker", "HTTPS_PROXY": "http://127.0.0.1:1/proxy/raw-proxy-marker", "ALL_PROXY": "http://127.0.0.1:1/proxy/raw-proxy-marker", "http_proxy": "http://127.0.0.1:1/proxy/raw-proxy-marker", "https_proxy": "http://127.0.0.1:1/proxy/raw-proxy-marker", "all_proxy": "http://127.0.0.1:1/proxy/raw-proxy-marker"}); environment.pop("PYTHONPATH", None); environment.pop("PYTHONHOME", None); environment["PYTHONNOUSERSITE"] = "1"; route_config(environment)
                    command = [str(cli), "status", "--json", "--provider", "opencode-go", "--opencode-allow-authorized-source"]
                    completed = subprocess.run(command, cwd=Path.cwd(), env=environment, capture_output=True, text=True, check=False, timeout=15)
                    check(completed.returncode == (0 if scenario in ("valid", "partial") else 5), "installed OpenCode scenario returned an unexpected exit")
                    evidence = json.loads(completed.stdout)
                    if scenario in ("valid", "partial"): check(evidence["result"] == "snapshot" and evidence["provider_id"] == {"value": "opencode-go"} and len(evidence["quota_windows"]) == (3 if scenario == "valid" else 2), "installed OpenCode snapshot evidence mismatch")
                    else: check(evidence["result"] == "error", "installed OpenCode error envelope missing")
                    check(redacted(completed.stdout + completed.stderr), "installed OpenCode output leaked unsafe evidence")
                    if server is not None: check(state["requests"] == 1 and state["contract"], "loopback request contract failed")
                    receipt_path.write_text(json.dumps({"scenario": scenario, "requests": state["requests"], "contract": bool(state["contract"])}), encoding="ascii"); check(redacted(receipt_path.read_text(encoding="ascii")), "OpenCode receipt leaked unsafe evidence"); results.append(scenario)
                finally:
                    if server is not None: server.shutdown(); server.server_close()
                    if thread is not None: thread.join(timeout=2)
    finally:
        cleanup_sitecustomize(owned_path, owned)
    return tuple(results)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True); parser.add_argument("--manifest", type=Path, required=True); parser.add_argument("--source-sha", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True); parser.add_argument("--expected-version", required=True); parser.add_argument("--checkout", type=Path, required=True); parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--cli", type=Path); parser.add_argument("--installation", choices=("wheel", "sdist")); parser.add_argument("--require-opencode-dependency", action="store_true")
    args = parser.parse_args()
    digest = verify_artifact(args.artifact, args.manifest, args.source_sha, args.expected_source_sha, args.expected_version, args.wheelhouse)
    check(args.cli is not None and args.installation is not None, "smoke arguments are incomplete")
    site_packages, distribution = assert_isolated(args.checkout, args.expected_version)
    api_smoke()
    cli_smoke(args.cli)
    codex_smoke(args.cli)
    live_result = live_smoke(args.cli, os.environ)
    opencode_scenarios = opencode_smoke(args.require_opencode_dependency, site_packages)
    for name, module in sys.modules.items():
        if name == "limitora" or name.startswith("limitora."): check((location := getattr(module, "__file__", None)) is not None and under(Path(location), site_packages), f"imported module is outside site-packages: {name}")
    evidence = {
        "source_sha": args.expected_source_sha,
        "artifact": args.artifact.name,
        "sha256": digest,
        "wheelhouse": "verified",
        "installation": args.installation,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "site_packages": str(site_packages),
        "distribution": distribution.version,
        "api": "pass",
        "cli": "pass",
        "codex": "local-handshake-cleanup-pass",
        "live_codex": live_result,
        "opencode_go": "installed-httpx-loopback-pass",
        "opencode_scenarios": {"count": len(opencode_scenarios), "names": opencode_scenarios},
        "opencode_go_dependency": "installed" if args.require_opencode_dependency else "absent",
        "provider_scope": "Installed OpenCode loopback scenarios only; no live service evidence",
    }
    check(redacted(json.dumps(evidence)), "final smoke evidence leaked unsafe evidence")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
