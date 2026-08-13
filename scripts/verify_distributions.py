"""Fail-closed wheel and sdist inventory and metadata verification."""

from __future__ import annotations

import base64
from collections import Counter
import csv
from decimal import Decimal
from email.parser import BytesParser
from email.policy import default
import hashlib
from io import StringIO
import math
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import zipfile
from packaging.requirements import Requirement
NAME, VERSION, REQUIRES_PYTHON = "limitora", "0.2.0", ">=3.10"
ROOT = f"{NAME}-{VERSION}"
DIST_INFO = f"{ROOT}.dist-info"
SOURCE_FILES = tuple(f"src/limitora/{name}" for name in (
    "__init__.py", "_runner_path.py", "api.py", "cache/__init__.py", "cli/__init__.py",
    "composition.py", "core/__init__.py", "core/status_service.py", "models/__init__.py", "models/domain.py",
    "output.py", "providers/__init__.py", "providers/_codex_jsonl.py", "providers/_codex_jsonl_protocol.py",
    "providers/_codex_jsonl_transport.py",
    "providers/_opencode_go.py", "providers/_opencode_go_httpx.py", "providers/cache.py",
    "providers/codex.py", "providers/contract.py", "providers/fake.py", "providers/ports.py", "py.typed",
))
EGG_FILES = tuple(f"src/limitora.egg-info/{name}" for name in
                  ("PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt"))
ENTRY_POINTS = b"[console_scripts]\nlimitora = limitora.cli:console_main\n"
EXPECTED_REQUIREMENT = Requirement('httpx>=0.27,<1; extra == "opencode-go"')
EXPECTED_HEADERS = (
    ("Metadata-Version", "2.4"), ("Name", NAME), ("Version", VERSION),
    ("Summary", "Python library for provider-agnostic LLM interaction with rate limiting and safe local integration."),
    ("Author-email", "dnieblesdev <dnieblesp@gmail.com>"), ("License-Expression", "MIT"),
    *(("Classifier", value) for value in ("Programming Language :: Python :: 3 :: Only",
       *(f"Programming Language :: Python :: 3.{minor}" for minor in range(10, 15)), "Typing :: Typed")),
    ("Requires-Python", REQUIRES_PYTHON), ("Description-Content-Type", "text/markdown"),
    ("License-File", "LICENSE"), ("Provides-Extra", "opencode-go"), ("Dynamic", "license-file"),
)
def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)
def safe_name(name: str) -> None:
    parts = PurePosixPath(name).parts
    check(bool(name) and "\\" not in name and not name.startswith("/"), f"unsafe path: {name}")
    check(all(part not in ("", ".", "..") for part in parts), f"unsafe path: {name}")
def verify_metadata(data: bytes) -> None:
    sections = data.split(b"\n\n", 1)
    check(len(sections) == 2 and sections[1] == Path("README.md").read_bytes(), "wrong metadata body")
    metadata = BytesParser(policy=default).parsebytes(data)
    check(metadata.get_unixfrom() is None and not metadata.defects, "malformed metadata envelope")
    requirements = metadata.get_all("Requires-Dist", [])
    check(len(requirements) == 1 and Requirement(requirements[0]) == EXPECTED_REQUIREMENT,
          "wrong opencode-go requirement")
    expected = (*EXPECTED_HEADERS, ("Requires-Dist", requirements[0]))
    check(Counter(metadata.items()) == Counter(expected), "unexpected metadata header")
def verify_zip_fields(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    check(not archive.comment, "wheel archive comment is forbidden")
    check(infos and all(not info.extra and not info.comment for info in infos),
          "wheel member extra or comment is forbidden")
def verify_wheel_metadata(data: bytes) -> None:
    wheel = BytesParser(policy=default).parsebytes(data)
    check(wheel.get_unixfrom() is None and not wheel.defects and Counter(wheel.items()) == Counter((
        ("Wheel-Version", "1.0"), ("Generator", "setuptools (83.0.0)"),
        ("Root-Is-Purelib", "true"), ("Tag", "py3-none-any"),
    )) and not wheel.get_payload(), "wrong wheel metadata")
def verify_pax_fields(archive: tarfile.TarFile, members: list[tarfile.TarInfo]) -> None:
    check(not archive.pax_headers, "global PAX metadata is forbidden")
    for member in members:
        check(set(member.pax_headers) <= {"mtime"}, f"unexpected PAX metadata: {member.name}")
        if "mtime" in member.pax_headers:
            value = member.pax_headers["mtime"]
            timestamp = Decimal(value)
            check(timestamp.is_finite() and timestamp >= 0 and str(timestamp) == value
                  and math.isfinite(member.mtime) and member.mtime >= 0
                  and float(timestamp) == member.mtime, f"invalid PAX mtime: {member.name}")
def verify_wheel(path: Path) -> tuple[list[str], bytes]:
    check(path.name == f"{ROOT}-py3-none-any.whl", "wrong wheel filename")
    wheel_files = {source.removeprefix("src/") for source in SOURCE_FILES}
    wheel_files.update(f"{DIST_INFO}/{name}" for name in
                       ("licenses/LICENSE", "METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD"))
    with zipfile.ZipFile(path) as archive:
        verify_zip_fields(archive)
        infos = archive.infolist()
        names = [info.filename for info in infos]
        check(len(names) == len(set(names)), "duplicate wheel member")
        for info in infos:
            safe_name(info.filename)
            kind = stat.S_IFMT(info.external_attr >> 16)
            check(kind in (0, stat.S_IFREG), f"non-regular wheel member: {info.filename}")
        check(set(names) == wheel_files, "wheel allowlist mismatch")
        for source in SOURCE_FILES:
            check(archive.read(source.removeprefix("src/")) == Path(source).read_bytes(),
                  f"wheel source mismatch: {source}")
        check(archive.read(f"{DIST_INFO}/licenses/LICENSE") == Path("LICENSE").read_bytes(),
              "wheel license mismatch")
        check(archive.read("limitora/py.typed") == b"", "py.typed must be empty")
        metadata = archive.read(f"{DIST_INFO}/METADATA")
        verify_metadata(metadata)
        check(archive.read(f"{DIST_INFO}/entry_points.txt") == ENTRY_POINTS, "wrong entry point")
        check(archive.read(f"{DIST_INFO}/top_level.txt") == b"limitora\n", "wrong top level")
        verify_wheel_metadata(archive.read(f"{DIST_INFO}/WHEEL"))
        rows = list(csv.reader(StringIO(archive.read(f"{DIST_INFO}/RECORD").decode())))
        check(len(rows) == len(names) and {row[0] for row in rows} == set(names), "RECORD mismatch")
        for member, digest, size in rows:
            if member.endswith("/RECORD"):
                check(not digest and not size, "RECORD must not hash itself")
                continue
            algorithm, encoded = digest.split("=", 1)
            actual = base64.urlsafe_b64encode(hashlib.sha256(archive.read(member)).digest()).rstrip(b"=").decode()
            check(algorithm == "sha256" and encoded == actual and int(size) == len(archive.read(member)),
                  f"bad RECORD entry: {member}")
    return sorted(names), metadata
def verify_sdist(path: Path, wheel_metadata: bytes) -> list[str]:
    check(path.name == f"{ROOT}.tar.gz", "wrong sdist filename")
    tracked = ("LICENSE", "MANIFEST.in", "README.md", "pyproject.toml", *SOURCE_FILES)
    generated = ("PKG-INFO", "setup.cfg", *EGG_FILES)
    files = {f"{ROOT}/{name}" for name in (*tracked, *generated)}
    directories = {ROOT}
    for name in files:
        directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != ".")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        verify_pax_fields(archive, members)
        names = [member.name for member in members]
        check(len(names) == len(set(names)), "duplicate sdist member")
        for member in members:
            safe_name(member.name)
            check(member.isdir() or member.isreg(), f"link or device in sdist: {member.name}")
            if member.isreg():
                check(not member.mode & 0o111, f"executable sdist member: {member.name}")
        check(set(names) == files | directories, "sdist allowlist mismatch")
        read = lambda name: archive.extractfile(f"{ROOT}/{name}").read()  # type: ignore[union-attr]
        for name in tracked:
            check(read(name) == Path(name).read_bytes(), f"sdist source mismatch: {name}")
        check(read("PKG-INFO") == wheel_metadata == read("src/limitora.egg-info/PKG-INFO"),
              "sdist metadata mismatch")
        verify_metadata(wheel_metadata)
        check(read("setup.cfg") == b"[egg_info]\ntag_build = \ntag_date = 0\n\n", "wrong setup.cfg")
        sources = read("src/limitora.egg-info/SOURCES.txt").decode().splitlines()
        expected_sources = {"LICENSE", "MANIFEST.in", "README.md", "pyproject.toml", *SOURCE_FILES, *EGG_FILES}
        check(len(sources) == len(set(sources)) and set(sources) == expected_sources, "wrong SOURCES.txt")
        check(read("src/limitora.egg-info/dependency_links.txt") == b"\n", "unexpected dependency link")
        check(read("src/limitora.egg-info/entry_points.txt") == ENTRY_POINTS, "wrong sdist entry point")
        requirements = [line.strip() for line in read("src/limitora.egg-info/requires.txt").decode().splitlines()
                        if line.strip()]
        check(len(requirements) == 2 and requirements[0] == "[opencode-go]"
              and Requirement(requirements[1]) == Requirement("httpx>=0.27,<1"), "wrong extra")
        check(read("src/limitora.egg-info/top_level.txt") == b"limitora\n", "wrong sdist top level")
    return sorted(names)
def main() -> None:
    directory = Path(sys.argv[1] if len(sys.argv) == 2 else "dist")
    wheels, sdists = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    check(len(wheels) == len(sdists) == 1, "expected exactly one wheel and one sdist")
    wheel_names, metadata = verify_wheel(wheels[0])
    sdist_names = verify_sdist(sdists[0], metadata)
    print("sha256:", *(f"{path.name} {hashlib.sha256(path.read_bytes()).hexdigest()}" for path in (*wheels, *sdists)), sep="\n  ")
    print("wheel:", *wheel_names, sep="\n  ")
    print("sdist:", *sdist_names, sep="\n  ")
if __name__ == "__main__":
    main()
