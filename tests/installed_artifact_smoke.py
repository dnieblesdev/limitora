"""Smoke-test an installed Limitora artifact outside its source checkout."""

from __future__ import annotations

import argparse, hashlib, importlib.util, json, os, platform, site, subprocess, sys, sysconfig
from datetime import datetime, timedelta, timezone
import importlib.metadata as metadata
from pathlib import Path


HELP = (
    "limitora status [--help] [--json] [--provider {codex,opencode-go}] [flags]\n"
    "  codex:        --runner PATH [--runner ARG ...]\n"
    "                A single absolute PATH uses 'app-server --stdio'.\n"
    "                [--codex-allow-authorized-source]\n"
    "  opencode-go:  --workspace-id ID --auth-cookie COOKIE\n"
    "                [--endpoint URL] [--timeout SECONDS]\n"
    "                [--opencode-allow-authorized-source]\n"
    "Without --provider, status prints 'no provider configured' to stderr (exit 4).\n"
)


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
    distribution_rows = [(name, digest) for name, digest in rows if "/" not in name]
    wheelhouse_rows = [(name, digest) for name, digest in rows if name.startswith("wheelhouse/")]
    check(all(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest) for _, digest in rows), "invalid manifest digest")
    check(wheelhouse_rows and len(distribution_rows) + len(wheelhouse_rows) == len(rows) and all(name.endswith(".whl") and not Path(name).name.startswith("limitora-") for name, _ in wheelhouse_rows), "invalid wheelhouse member")
    check({f"wheelhouse/{path.name}" for path in wheelhouse.glob("*.whl")} == {name for name, _ in wheelhouse_rows}, "wheelhouse inventory mismatch")
    expected_names = {f"limitora-{expected_version}-py3-none-any.whl", f"limitora-{expected_version}.tar.gz"}
    check({name for name, _ in distribution_rows} == expected_names, "manifest artifact names mismatch")
    recorded = dict(distribution_rows).get(artifact.name)
    check(recorded is not None, "artifact is absent from manifest")
    actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
    check(actual == recorded, "artifact SHA-256 does not match manifest")
    for name, digest in wheelhouse_rows:
        dependency = wheelhouse / name.removeprefix("wheelhouse/")
        check(hashlib.sha256(dependency.read_bytes()).hexdigest() == digest, f"wheelhouse SHA-256 mismatch: {name}")
    recorded_source = source_sha.read_text(encoding="ascii")
    check(recorded_source == expected_source_sha + "\n" and len(expected_source_sha) == 40 and all(c in "0123456789abcdef" for c in expected_source_sha), "invalid source SHA evidence")
    return actual

def assert_isolated(checkout: Path, expected_version: str) -> tuple[Path, object]:
    site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
    check(site.ENABLE_USER_SITE is False, "user site is enabled")
    check("PYTHONPATH" not in os.environ and "PYTHONHOME" not in os.environ, "Python path override remains set")
    check(os.environ.get("PYTHONNOUSERSITE") == "1", "user-site override is not disabled")
    checkout = checkout.resolve()
    for entry in sys.path:
        if entry:
            check(not under(Path(entry), checkout), f"checkout path remains in sys.path: {entry}")

    import limitora

    check(under(Path(limitora.__file__), site_packages), "limitora.__file__ is outside site-packages")
    distribution = metadata.distribution("limitora")
    check(distribution.version == expected_version, "installed version mismatch")
    check(under(Path(distribution.locate_file("limitora")), site_packages), "distribution is outside site-packages")
    for file in distribution.files or ():
        check(str(checkout).casefold() not in str(file).casefold(), "distribution metadata contains a checkout path")
    direct_url = distribution.read_text("direct_url.json")
    if direct_url:
        check(not json.loads(direct_url).get("dir_info", {}).get("editable"), "editable installation metadata found")
        check(str(checkout).casefold() not in direct_url.casefold(), "direct URL contains checkout path")
    for path in site_packages.rglob("*.egg-link"):
        raise AssertionError(f"editable egg-link found: {path}")
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
    now = datetime.now(timezone.utc)
    provider_id = limitora.ProviderId("installed-smoke")
    source = limitora.SourceMetadata("installed-smoke")
    status = limitora.ProviderStatus(provider_id, limitora.ProviderState.AVAILABLE, now)
    snapshot = limitora.ProviderSnapshot(provider_id, status, now, now, source)
    check(snapshot.provider_id == provider_id, "public snapshot construction failed")
    freshness = limitora.FreshnessPolicy(timedelta(minutes=5))
    request = limitora.StatusRequest(
        frozenset({limitora.MetricKind.COMMERCIAL_QUOTA}),
        limitora.AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
        freshness,
    )

    offline_provider_id = provider_id

    class OfflineProvider:
        provider_id = offline_provider_id

        def detect(self):
            return ProviderDetection(offline_provider_id, False, now, "offline fixture")

        def fetch(self, request: ProviderRequest):
            raise AssertionError("offline provider must not fetch")

    result = limitora.StatusClient(OfflineProvider(), limitora.CurrentClock()).read_status(request)
    check(isinstance(result, limitora.StatusUndetectedResult), "public StatusClient flow failed")
    runner = str(Path.cwd() / "not-a-real-codex-runner")
    check(isinstance(limitora.activate_provider(limitora.CodexJsonlConfig((runner,))), limitora.StatusClient),
          "Codex public construction failed")
    check(isinstance(limitora.activate_provider(limitora.OpenCodeGoConfig("workspace", "cookie")), limitora.StatusClient),
          "OpenCode Go public construction failed")


def cli_smoke(cli: Path) -> None:
    check(under(cli, Path(sys.prefix)), "CLI is not installed in the active virtual environment")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    for arguments, expected in (
        (("status", "--help"), (0, HELP, "")),
        (("status",), (4, "", "ERROR: no provider configured\n")),
    ):
        completed = subprocess.run(
            [str(cli), *arguments], cwd=Path.cwd(), env=environment,
            capture_output=True, text=True, check=False,
        )
        check((completed.returncode, completed.stdout, completed.stderr) == expected,
              f"unexpected CLI result for {' '.join(arguments)}")


CODEX_FIXTURE = r'''import json, os, sys, time

payload = {"rateLimits": {"limitId": "codex", "planType": "plus",
    "primary": {"windowDurationMins": 300, "usedPercent": 25, "resetsAt": 2000000000},
    "secondary": {"windowDurationMins": 10080, "usedPercent": 50, "resetsAt": 2000000000}}}
methods = []
for expected in ("initialize", "initialized", "account/rateLimits/read"):
    message = json.loads(sys.stdin.readline())
    if message.get("method") != expected: raise SystemExit("unexpected fixture method")
    methods.append(expected)
    if expected == "initialize": result = {"id": 1, "result": {}}
    elif expected == "account/rateLimits/read":
        with open(os.environ["LIMITORA_CODEX_RECEIPT"], "w", encoding="ascii") as receipt: json.dump(methods, receipt)
        result = {"id": 2, "result": payload}
    else: continue
    print(json.dumps(result, separators=(",", ":")), flush=True)
time.sleep(60)
'''


def codex_smoke() -> None:
    import limitora
    from limitora.models import MetricKind
    from limitora.providers import AuthorizationPolicy, ProviderRequest
    from limitora.providers.codex import CodexProvider

    fixture = Path.cwd() / "codex-local-fixture.py"
    receipt = Path.cwd() / "codex-local-receipt.json"
    fixture.write_text(CODEX_FIXTURE, encoding="ascii")
    runner = (sys.executable, str(fixture))
    check(Path(sys.executable).is_absolute(), "Codex interpreter path is not absolute")
    environment = os.environ.copy()
    environment["LIMITORA_CODEX_RECEIPT"] = str(receipt)
    previous = os.environ.get("LIMITORA_CODEX_RECEIPT")
    os.environ.update(environment)
    provider = CodexProvider(runner, limitora.CurrentClock())
    snapshot = provider.fetch(ProviderRequest(
        frozenset({MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE,
    ))
    if previous is None: os.environ.pop("LIMITORA_CODEX_RECEIPT", None)
    else: os.environ["LIMITORA_CODEX_RECEIPT"] = previous
    check(snapshot.provider_id.value == "codex", "Codex installed provider failed")
    check(json.loads(receipt.read_text(encoding="ascii")) == ["initialize", "initialized", "account/rateLimits/read"],
          "Codex local process handshake transcript mismatch")


def opencode_smoke(require_dependency: bool, site_packages: Path) -> None:
    from limitora.models import MetricKind, ProviderState
    from limitora.providers import AuthorizationPolicy, ProviderError, ProviderErrorKind, ProviderRequest
    from limitora.providers._opencode_go import OpenCodeGoConfig, OpenCodeGoProvider
    from limitora.providers.ports import HttpResponse

    if require_dependency:
        import httpx
        check(under(Path(httpx.__file__), site_packages), "imported httpx is outside site-packages")
        check(metadata.version("httpx") == httpx.__version__, "imported httpx version metadata mismatch")
    else:
        check(importlib.util.find_spec("httpx") is None, "base installation unexpectedly contains httpx")

    class LocalTransport:
        calls = 0

        def fetch(self):
            self.calls += 1; return HttpResponse(200, b'{"rollingUsage":{"usagePercent":25,"resetInSec":10},"weeklyUsage":{"usagePercent":50,"resetInSec":20},"monthlyUsage":{"usagePercent":75,"resetInSec":30}}')

    transport = LocalTransport()
    provider = OpenCodeGoProvider(
        OpenCodeGoConfig("workspace", "synthetic-cookie", "https://opencode.ai", timedelta(seconds=10)),
        transport,
    )
    request = ProviderRequest(frozenset({MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.DENY_AUTHORIZED_SOURCE)
    try:
        provider.fetch(request)
    except ProviderError as error:
        check(error.kind is ProviderErrorKind.UNAUTHORIZED, "OpenCode Go deny path changed")
    else:
        raise AssertionError("OpenCode Go deny path did not fail")
    check(transport.calls == 0, "OpenCode Go contacted transport before authorization")
    snapshot = provider.fetch(ProviderRequest(
        frozenset({MetricKind.COMMERCIAL_QUOTA}), AuthorizationPolicy.ALLOW_AUTHORIZED_SOURCE,
    ))
    check(snapshot.status.state is ProviderState.AVAILABLE and transport.calls == 1,
          "OpenCode Go local transport smoke failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-sha", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--installation", choices=("wheel", "sdist"))
    parser.add_argument("--require-opencode-dependency", action="store_true")
    args = parser.parse_args()
    digest = verify_artifact(args.artifact, args.manifest, args.source_sha,
                             args.expected_source_sha, args.expected_version, args.wheelhouse)
    check(args.cli is not None and args.installation is not None, "smoke arguments are incomplete")
    site_packages, distribution = assert_isolated(args.checkout, args.expected_version)
    api_smoke()
    cli_smoke(args.cli)
    codex_smoke()
    opencode_smoke(args.require_opencode_dependency, site_packages)
    for name, module in sys.modules.items():
        if name == "limitora" or name.startswith("limitora."):
            location = getattr(module, "__file__", None)
            check(location is not None and under(Path(location), site_packages),
                  f"imported module is outside site-packages: {name}")
    print(json.dumps({
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
        "opencode_go": "mocked-transport-pass",
        "opencode_go_dependency": "installed" if args.require_opencode_dependency else "absent",
        "provider_scope": "PR2 smoke only; does not cover #18 provider protocol E2E",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
