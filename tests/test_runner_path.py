"""Host-independent tests for the private native runner-path contract."""

import unittest

from limitora._runner_path import _is_native_absolute_runner_path


class NativeRunnerPathTests(unittest.TestCase):
    def test_posix_path_matrix(self):
        cases = (
            ("/usr/bin/codex", True),
            ("/opt/Codex App/codex", True),
            ("codex", False),
            ("./codex", False),
            ("C:\\codex.exe", False),
            ("C:/codex.exe", False),
            (r"\\server\share\codex.exe", False),
            ("//server/share/codex", True),
            ("", False),
            (" ", False),
            ("/usr/bin/co\x00dex", False),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(
                    expected,
                    _is_native_absolute_runner_path(path, platform="posix"),
                )

    def test_windows_path_matrix(self):
        cases = (
            ("C:\\Program Files\\Codex\\codex.exe", True),
            ("C:/Program Files/Codex/codex.exe", True),
            (r"\\server\share\codex.exe", True),
            ("//server/share/codex.exe", True),
            ("/usr/bin/codex", False),
            (r"\codex.exe", False),
            ("/codex.exe", False),
            ("C:codex.exe", False),
            (r"\\server", False),
            ("//server/", False),
            (r"\\?\C:\codex.exe", False),
            (r"\\.\C:\codex.exe", False),
            ("//?/C:/codex.exe", False),
            ("//./C:/codex.exe", False),
            ("", False),
            (" ", False),
            ("C:\\co\x00dex.exe", False),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(
                    expected,
                    _is_native_absolute_runner_path(path, platform="nt"),
                )

    def test_unknown_platform_fails_closed(self):
        self.assertFalse(
            _is_native_absolute_runner_path("/usr/bin/codex", platform="other")
        )


if __name__ == "__main__":
    unittest.main()
