"""Private native absolute-path validation for explicit process runners."""

import os
import posixpath


def _is_native_absolute_runner_path(
    path: object, *, platform: str | None = None
) -> bool:
    if (
        not isinstance(path, str)
        or not path
        or path.strip() != path
        or "\x00" in path
    ):
        return False

    native_platform = os.name if platform is None else platform
    unc_prefix = len(path) >= 2 and path[0] in "/\\" and path[1] in "/\\"
    drive_prefix = (
        len(path) >= 3
        and path[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        and path[1] == ":"
        and path[2] in "/\\"
    )

    if native_platform == "posix":
        return posixpath.isabs(path)
    if native_platform != "nt":
        return False

    normalized = path.replace("/", "\\")
    if normalized.startswith(("\\\\?\\", "\\\\.\\")):
        return False
    if drive_prefix:
        return True
    if not unc_prefix or normalized.startswith("\\\\\\"):
        return False
    server_and_share = normalized[2:].split("\\", 2)
    return len(server_and_share) >= 2 and all(server_and_share[:2])
