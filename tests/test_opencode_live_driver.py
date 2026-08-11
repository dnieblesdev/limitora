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


class Process:
    def __init__(self, body=b"", code=0, timeout=False):
        self.stdout, self.returncode, self.timeout = io.BytesIO(body), code, timeout
        self.killed = False
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
    def call(self, args, body=b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"fresh"}', code=0, **kw):
        process = Process(body, code, kw.pop("timeout", False))
        with patch.object(driver.subprocess, "Popen", return_value=process) as popen:
            result = driver.run(args, environ=kw.pop("environ", self.env))
        return result, process, popen

    def test_dotenv_literals_grammar_and_precedence(self):
        for cookie in ("cookie=marker", "YWJjZA=="):
            with self.subTest(cookie=cookie):
                path = self.dotenv(
                    "# comment\n  # another\n"
                    "LIMITORA_OPENCODE_WORKSPACE_ID= a$\\'!\n"
                    f"LIMITORA_OPENCODE_AUTH_COOKIE={cookie}\n"
                )
                args = ["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(path)]
                result, _, popen = self.call(args, environ={})
                self.assertEqual(0, result)
                child_env = popen.call_args.kwargs["env"]
                self.assertEqual(" a$\\'!", child_env[driver.WORKSPACE])
                self.assertEqual(cookie, child_env[driver.COOKIE])
                self.assertNotIn(cookie, popen.call_args.args[0])
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
        self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(self.dotenv("LIMITORA_OPENCODE_WORKSPACE_ID=x\n", 0o640))], environ={}))

    def test_source_duplicates_empty_conflicts_and_env_only(self):
        same = self.dotenv("LIMITORA_OPENCODE_WORKSPACE_ID=workspace-marker\nLIMITORA_OPENCODE_AUTH_COOKIE=cookie-marker\n")
        self.assertEqual(0, self.call(["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(same)], environ=self.env)[0])
        conflict = self.dotenv("LIMITORA_OPENCODE_WORKSPACE_ID=other\nLIMITORA_OPENCODE_AUTH_COOKIE=cookie-marker\n")
        self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli), "--dotenv", str(conflict)], environ=self.env))
        for env in ({driver.WORKSPACE: "", driver.COOKIE: "c"}, {driver.WORKSPACE: "w"}):
            self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli)], environ=env))
        self.assertEqual(0, self.call(["--confirm", "RUN", "--cli", str(self.cli)])[0])

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
        self.assertEqual(10, driver.run(["--confirm", "RUN", "--cli", str(self.cli)], environ=self.env))

    def test_exact_command_environment_shell_stderr_and_privacy(self):
        code, _, popen = self.call(["--confirm", "RUN", "--cli", str(self.cli)])
        self.assertEqual(0, code)
        self.assertEqual([str(self.cli), *driver.COMMAND_SUFFIX], popen.call_args.args[0])
        kwargs = popen.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertIs(kwargs["stderr"], driver.subprocess.DEVNULL)
        self.assertNotIn("workspace-marker", popen.call_args.args[0])
        self.assertNotIn("cookie-marker", popen.call_args.args[0])

    def test_main_emits_one_constant_line(self):
        with patch.object(driver, "run", return_value=10):
            with patch("builtins.print") as printed:
                self.assertEqual(10, driver.main(["--confirm", "NO"]))
        printed.assert_called_once_with("OpenCode live result: preflight")

    def test_transport_timeout_and_bounded_stdout(self):
        self.assertEqual(24, self.call(["--confirm", "RUN", "--cli", str(self.cli)], timeout=True)[0])
        with patch.object(driver, "MAX_STDOUT", 3):
            self.assertEqual(24, self.call(["--confirm", "RUN", "--cli", str(self.cli)], body=b"1234")[0])
        with patch.object(driver.subprocess, "Popen", side_effect=OSError):
            self.assertEqual(24, driver.run(["--confirm", "RUN", "--cli", str(self.cli)], environ=self.env))

    def test_all_classifications_and_safe_output(self):
        envelopes = [(b'{"version":1,"error":{"kind":"unauthorized"}}', 5, 20), (b'{"version":1,"error":{"kind":"parse_failed"}}', 5, 21), (b'{"version":1,"error":{"kind":"unsupported"}}', 5, 21), (b'{"version":1,"error":{"kind":"rate_limited"}}', 5, 22), (b'{"version":1,"error":{"kind":"source_unavailable"}}', 5, 23), (b'{"version":1,"error":{"kind":"transport"}}', 5, 24), (b'{"version":1,"error":{"kind":"unknown"}}', 5, 25), (b"not-json", 5, 21), (b'{"version":2}', 0, 21), (b'{"version":1,"result":"snapshot","provider_id":{"value":"other"},"freshness":"fresh"}', 0, 25), (b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"stale"}', 0, 25), (b'{"version":1,"result":"snapshot","provider_id":{"value":"opencode-go"},"freshness":"fresh"}', 1, 25)]
        for body, exit_code, expected in envelopes:
            with self.subTest(expected=expected):
                result, _, _ = self.call(["--confirm", "RUN", "--cli", str(self.cli)], body=body, code=exit_code)
                self.assertEqual(expected, result)
        self.assertNotIn("workspace-marker", json.dumps(driver.CLASSIFICATIONS))
        output = io.StringIO()
        with patch.object(driver, "run", return_value=25), redirect_stdout(output):
            driver.main(["--confirm", "RUN", "--cli", str(self.cli)])
        self.assertNotIn("workspace-marker", output.getvalue())
        self.assertNotIn("cookie-marker", output.getvalue())

    def test_driver_source_has_no_provider_imports(self):
        source = (ROOT / "scripts/opencode_live_driver.py").read_text(encoding="utf-8")
        self.assertNotIn("import limitora", source)
        self.assertNotIn("import httpx", source)


if __name__ == "__main__":
    unittest.main()
