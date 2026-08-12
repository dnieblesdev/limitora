import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("live_driver", ROOT / "scripts/opencode_live_driver.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def error_envelope(kind, *, provider="opencode-go", safe_message="safe provider error", retryable=False):
    return json.dumps({
        "version": 1,
        "error": {
            "kind": kind,
            "provider_id": {"value": provider},
            "safe_message": safe_message,
            "retryable": retryable,
        },
    }, separators=(",", ":")).encode("ascii")


class Process:
    def __init__(self, body=b"", code=0, timeout=False):
        self.stdout, self.returncode, self.timeout = io.BytesIO(body), code, timeout
        self.killed = False
        self.pid = 1234
    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise driver.subprocess.TimeoutExpired("synthetic", timeout)
    def kill(self):
        self.killed = True


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cli = self.root / "cli"
        self.cli.write_text("synthetic", encoding="ascii")
        self.cli.chmod(0o700)
        self.env = {driver.WORKSPACE: "workspace-marker", driver.COOKIE: "cookie-marker",
                    "PYTHONPATH": "private", "PYTHONHOME": "private"}
    def tearDown(self):
        self.temp.cleanup()
    def dotenv(self, text, mode=0o600):
        path = self.root / "inputs.env"
        path.write_text(text, encoding="utf-8", newline="")
        path.chmod(mode)
        return path
    def call(self, args, body=b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"fresh","quota_windows":[{"kind":"commercial_quota","scope":"account","period":"weekly"}]}', code=0, **kw):
        with patch.object(driver, "_child", return_value=(code, body)) as child:
            with patch.object(driver.os, "name", "posix"), patch.object(
                driver.stat, "S_IMODE", return_value=0o600
            ):
                result = driver.run(args, environ=kw.pop("environ", self.env))
        return result, None, child

    def test_dotenv_literals_grammar_and_precedence(self):
        for cookie in ("cookie=marker", "YWJjZA=="):
            with self.subTest(cookie=cookie):
                path = self.dotenv(
                    "# comment\n  # another\n"
                    "LIMITORA_OPENCODE_WORKSPACE_ID= a$\\'!\n"
                    f"LIMITORA_OPENCODE_AUTH_COOKIE={cookie}\n"
                )
                args = ["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(path)]
                result, _, child = self.call(args, environ={})
                self.assertEqual(0, result)
                child_env = child.call_args.args[1]
                self.assertEqual(" a$\\'!", child_env[driver.WORKSPACE])
                self.assertEqual(cookie, child_env[driver.COOKIE])
                self.assertNotIn(cookie, child.call_args.args[0])
                self.assertNotIn("PYTHONPATH", child_env)
                self.assertNotIn("PYTHONHOME", child_env)
                self.assertEqual("1", child_env["PYTHONNOUSERSITE"])

                output = io.StringIO()
                with patch.object(driver, "run", return_value=0), redirect_stdout(output):
                    self.assertEqual(0, driver.main(args))
                self.assertNotIn(cookie, output.getvalue())
        self.assertEqual("workspace-marker", self.env[driver.WORKSPACE])

    def test_dotenv_rejects_bad_lines_duplicates_unknown_empty_and_permissions(self):
        cases = ("export LIMITORA_OPENCODE_WORKSPACE_ID=x\n", "UNKNOWN=x\n", "LIMITORA_OPENCODE_WORKSPACE_ID=x\nLIMITORA_OPENCODE_WORKSPACE_ID=x\n", "LIMITORA_OPENCODE_WORKSPACE_ID=   \n", "LIMITORA_OPENCODE_WORKSPACE_ID=x\x00\n", "LIMITORA_OPENCODE_WORKSPACE_ID=x\ny\n", "LIMITORA_OPENCODE_WORKSPACE_ID\n")
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(self.dotenv(text))], environ={}))
        self.assertFalse(driver._dotenv_mode_is_private(0o640, platform="posix"))
        self.assertTrue(driver._dotenv_mode_is_private(0o640, platform="nt"))

    def test_source_duplicates_empty_conflicts_and_env_only(self):
        same = self.dotenv("LIMITORA_OPENCODE_WORKSPACE_ID=workspace-marker\nLIMITORA_OPENCODE_AUTH_COOKIE=cookie-marker\n")
        self.assertEqual(0, self.call(["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(same)], environ=self.env)[0])
        conflict = self.dotenv("LIMITORA_OPENCODE_WORKSPACE_ID=other\nLIMITORA_OPENCODE_AUTH_COOKIE=cookie-marker\n")
        self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(conflict)], environ=self.env))
        for env in ({driver.WORKSPACE: "", driver.COOKIE: "c"}, {driver.WORKSPACE: "w"}):
            self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli)], environ=env))
        self.assertEqual(0, self.call(["--confirm", "RUN", "--cli", str(self.cli)])[0])

    def test_child_environment_is_an_allowlist_and_parent_is_unchanged(self):
        environ = {
            driver.WORKSPACE: "workspace-marker", driver.COOKIE: "cookie-marker",
            "CI": "synthetic-ci-secret", "GITHUB_TOKEN": "synthetic-token",
            "HTTP_PROXY": "http://synthetic-proxy", "PATH": "/synthetic-path",
            "PYTHONPATH": "private", "PYTHONHOME": "private",
        }
        before = dict(environ)
        result, _, child = self.call(["--confirm", "RUN", "--cli", str(self.cli)], environ=environ)
        self.assertEqual(0, result)
        self.assertEqual(before, environ)
        self.assertEqual(
            {driver.WORKSPACE, driver.COOKIE, "PYTHONNOUSERSITE"},
            set(child.call_args.args[1]),
        )
        self.assertNotIn("synthetic-ci-secret", child.call_args.args[1].values())
        self.assertNotIn("synthetic-token", child.call_args.args[1].values())

    def test_dotenv_limits_are_bounded(self):
        oversized = "x" * (driver.MAX_DOTENV_BYTES + 1)
        self.assertEqual(10, driver.run(
            ["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(self.dotenv(oversized))],
            environ={},
        ))
        long_value = "x" * (driver.MAX_VALUE_LENGTH + 1)
        self.assertEqual(10, driver.run(
            ["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(self.dotenv(
                f"{driver.WORKSPACE}={long_value}\n{driver.COOKIE}=cookie\n"
            ))],
            environ={},
        ))

    def test_preflight_confirmation_and_cli_paths_are_constant(self):
        for args in ([], ["--confirm", "NO", "--cli", str(self.cli)], ["--confirm", "RUN"], ["--confirm", "RUN", "--cli", "relative"], ["--confirm", "RUN", "--cli", str(self.root)], ["--confirm", "RUN", "--cli", str(self.root / "missing")]):
            with self.subTest(args=args):
                self.assertEqual(10, driver.run(args, environ=self.env))
        link = self.root / "link"
        if hasattr(os, "symlink"):
            link.symlink_to(self.cli)
            self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(link)], environ=self.env))
            env_link = self.root / "env-link"
            env_link.symlink_to(self.dotenv("LIMITORA_OPENCODE_WORKSPACE_ID=w\nLIMITORA_OPENCODE_AUTH_COOKIE=c\n"))
            self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(env_link)], environ={}))
        self.cli.chmod(0o600)
        with patch.object(driver.os, "access", return_value=False):
            self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli)], environ=self.env))

    def test_exact_command_environment_shell_stderr_and_privacy(self):
        code, _, child = self.call(["--confirm", "RUN", "--cli", str(self.cli)])
        self.assertEqual(0, code)
        self.assertEqual([str(self.cli), *driver.COMMAND_SUFFIX], child.call_args.args[0])
        self.assertNotIn("workspace-marker", child.call_args.args[0])
        self.assertNotIn("cookie-marker", child.call_args.args[0])

    def test_main_emits_one_constant_line(self):
        with patch.object(driver, "run", return_value=10):
            with patch("builtins.print") as printed:
                self.assertEqual(10, driver.main(["--confirm", "NO"]))
        printed.assert_called_once_with("OpenCode live result: preflight")

    def test_transport_timeout_and_bounded_stdout(self):
        with patch.object(driver.os, "name", "posix"), patch.object(driver, "_cleanup_group", return_value=True) as cleanup_group, patch.object(driver.subprocess, "Popen", return_value=Process(timeout=True)), patch.object(driver, "MAX_RUNTIME", 0.01):
            self.assertIsNone(driver._child([str(self.cli)], self.env))
        cleanup_group.assert_called_once()
        with patch.object(driver.os, "name", "posix"), patch.object(driver, "_cleanup_group", return_value=True) as cleanup_group, patch.object(driver.subprocess, "Popen", return_value=Process(b"1234")), patch.object(driver, "MAX_STDOUT", 3):
            self.assertIsNone(driver._child([str(self.cli)], self.env))
        cleanup_group.assert_called_once()
        with patch.object(driver, "_child", return_value=None):
            self.assertEqual(24, driver.run(["--confirm", "RUN", "--cli", str(self.cli)], environ=self.env))

    def test_cleanup_without_windows_kill_signal_fails_closed(self):
        process = Process()
        reader = __import__("threading").Thread(target=lambda: None)
        reader.start()
        with patch.object(driver, "_signal_group", return_value=True), patch.object(driver.signal, "SIGKILL", None, create=True):
            self.assertFalse(driver._cleanup_group(process, reader, allowance=0.01))

    def test_timeout_cleanup_signals_the_whole_process_group_with_bounded_waits(self):
        if not hasattr(driver.signal, "SIGKILL") or not hasattr(driver.os, "killpg"):
            self.skipTest("POSIX process-group cleanup is unavailable")
        process = Process(timeout=True)
        reader = __import__("threading").Thread(target=lambda: None)
        reader.start()
        def signal_group(process, signal_value):
            if signal_value == driver.signal.SIGKILL:
                process.killed = True
            return True
        with patch.object(driver, "_signal_group", side_effect=signal_group) as signal_group:
            self.assertTrue(driver._cleanup_group(process, reader, allowance=0.01))
        self.assertEqual([driver.signal.SIGTERM, driver.signal.SIGKILL], [call.args[1] for call in signal_group.call_args_list])

    def test_held_stdout_pipe_enters_bounded_group_cleanup(self):
        process = Process()
        process.stdout = type("HeldPipe", (), {"read": lambda self, size: b"held"})()
        if not hasattr(driver.signal, "SIGKILL") or not hasattr(driver.os, "killpg"):
            self.skipTest("POSIX process-group cleanup is unavailable")
        with patch.object(driver, "_cleanup_group", return_value=True) as cleanup_group, patch.object(driver.subprocess, "Popen", return_value=process), patch.object(driver, "MAX_RUNTIME", 0.01), patch.object(driver, "MAX_STDOUT", 3), patch.object(driver.os, "name", "posix"):
            self.assertIsNone(driver._child([str(self.cli)], self.env))
        cleanup_group.assert_called_once()

    def test_windows_fails_closed_before_process_start(self):
        with patch.object(driver.os, "name", "nt"), patch.object(driver, "_native_absolute", return_value=True), patch.object(driver.subprocess, "Popen") as popen:
            self.assertEqual(24, driver.run(["--confirm", "RUN", "--cli", str(self.cli)], environ=self.env))
        popen.assert_not_called()

    def test_all_classifications_and_safe_output(self):
        envelopes = [
            (error_envelope("unauthorized"), 5, 20),
            (error_envelope("parse_failed"), 5, 26),
            (error_envelope("unsupported"), 5, 27),
            (error_envelope("rate_limited"), 5, 22),
            (error_envelope("source_unavailable"), 5, 23),
            (error_envelope("transport"), 5, 24),
            (error_envelope("unknown"), 5, 25),
            (error_envelope("parse_failed")[:-2] + b"}", 5, 21),
            (b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error"}}', 5, 21),
            (b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":false,"extra":"ignored"}}', 5, 21),
            (b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go","extra":"ignored"},"safe_message":"safe provider error","retryable":false}}', 5, 21),
            (b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":[],"retryable":false}}', 5, 21),
            (b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":"false"}}', 5, 21),
            (error_envelope("parse_failed", provider="other"), 5, 25),
            (b'{"version":1,"error":{"kind":"unknown","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":false}}', 5, 25),
            (error_envelope("parse_failed"), 0, 25),
            (b"not-json", 5, 21),
            (b'{"version":2}', 0, 21),
            (b'{"version":true,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":false}}', 5, 21),
            (b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":false},"extra":"ignored"}', 5, 21),
            (b'{"version":1,"result":"snapshot","provider_id":{"value":"other"},"freshness":"fresh","quota_windows":[{"kind":"commercial_quota","scope":"account","period":"weekly"}]}', 0, 25),
            (b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"stale","quota_windows":[{"kind":"commercial_quota","scope":"account","period":"weekly"}]}', 0, 25),
            (b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"fresh"}', 0, 21),
            (b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"fresh","quota_windows":[]}', 0, 21),
            (b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"fresh","quota_windows":[{"kind":"technical_rate_limit","scope":"account","period":"weekly"}]}', 0, 21),
            (b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"fresh","quota_windows":[{"kind":"commercial_quota","scope":"account","period":"weekly"}]}', 1, 25),
        ]
        for body, exit_code, expected in envelopes:
            with self.subTest(expected=expected):
                self.assertEqual(expected, driver._classify(exit_code, body))
        self.assertEqual("schema_drift", driver.CLASSIFICATIONS[21])
        self.assertEqual("parse_failed", driver.CLASSIFICATIONS[26])
        self.assertEqual("unsupported", driver.CLASSIFICATIONS[27])
        self.assertNotEqual(driver.PARSE_FAILED, driver.UNSUPPORTED)
        self.assertNotIn("workspace-marker", json.dumps(driver.CLASSIFICATIONS))
        output = io.StringIO()
        with patch.object(driver, "run", return_value=25), redirect_stdout(output):
            driver.main(["--confirm", "RUN", "--cli", str(self.cli)])
        self.assertNotIn("workspace-marker", output.getvalue())
        self.assertNotIn("cookie-marker", output.getvalue())

    def test_specific_error_subtypes_require_exact_producer_envelopes(self):
        cases = (
            error_envelope("parse_failed", provider=""),
            error_envelope("parse_failed", provider="   "),
            error_envelope("parse_failed", safe_message=""),
            error_envelope("parse_failed", safe_message="   "),
            b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":false,"retryable":true}}',
            b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":false},"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},"safe_message":"safe provider error","retryable":false}}',
        )
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(driver.SCHEMA_DRIFT, driver._classify(5, body))

        malformed = (
            b'{"version":1,"error":{"kind":"parse_failed","provider_id":{"value":"opencode-go"},'
            b'"safe_message":"private payload marker","retryable":false,"retryable":true}}'
        )
        self.assertEqual(driver.SCHEMA_DRIFT, driver._classify(5, malformed))
        output = io.StringIO()
        with patch.object(driver, "run", return_value=driver.SCHEMA_DRIFT), redirect_stdout(output):
            self.assertEqual(driver.SCHEMA_DRIFT, driver.main([]))
        self.assertEqual("OpenCode live result: schema_drift\n", output.getvalue())
        self.assertNotIn("private payload marker", output.getvalue())

    def test_driver_source_has_no_provider_imports(self):
        source = (ROOT / "scripts/opencode_live_driver.py").read_text(encoding="utf-8")
        self.assertNotIn("import limitora", source)
        self.assertNotIn("import httpx", source)


if __name__ == "__main__":
    unittest.main()
