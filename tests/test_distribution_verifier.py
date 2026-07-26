from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from zipfile import ZipFile, ZipInfo
from scripts.verify_distributions import EXPECTED_HEADERS, verify_metadata, verify_pax_fields, verify_sdist, verify_wheel, verify_wheel_metadata, verify_zip_fields
class DistributionVerifierTests(unittest.TestCase):
    def metadata(self) -> bytes:
        headers = (*EXPECTED_HEADERS, ("Requires-Dist", 'httpx<1,>=0.27; extra == "opencode-go"'))
        return "".join(f"{name}: {value}\n" for name, value in headers).encode() + b"\n" + Path("README.md").read_bytes()
    def test_rejects_dependency_substitution_and_unexpected_metadata(self):
        metadata = self.metadata()
        verify_metadata(metadata)
        mutations = (
            metadata.replace(b"httpx<1", b"httpx-sse<1"),
            metadata.replace(b"Metadata-Version:", b"X-Arbitrary: payload\nMetadata-Version:"),
            b"From hidden\n" + metadata,
            metadata + b"arbitrary body",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation[:30]), self.assertRaises(ValueError):
                verify_metadata(mutation)
    def test_rejects_zip_side_channels(self):
        for field in ("archive", "extra", "comment"):
            stream = BytesIO()
            with ZipFile(stream, "w") as archive:
                info = ZipInfo("member")
                if field == "extra": info.extra = b"\xfe\xca\x00\x00"
                if field == "comment": info.comment = b"payload"
                archive.writestr(info, b"content")
                if field == "archive": archive.comment = b"payload"
            with self.subTest(field=field), ZipFile(stream) as archive, self.assertRaises(ValueError):
                verify_zip_fields(archive)
    def test_rejects_wheel_unixfrom_and_parser_defects(self):
        valid = b"Wheel-Version: 1.0\nGenerator: setuptools (83.0.0)\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
        verify_wheel_metadata(valid)
        for mutation in (b"From hidden\n" + valid, b"Broken header\n" + valid):
            with self.assertRaises(ValueError):
                verify_wheel_metadata(mutation)
    def test_rejects_unexpected_or_unbound_pax_metadata(self):
        member = lambda effective=1.0, **values: SimpleNamespace(name="member", pax_headers=values, mtime=effective)
        verify_pax_fields(SimpleNamespace(pax_headers={}), [member(mtime="1.0")])
        cases = (({"payload": "x"}, member(mtime="1.0")), ({}, member(mtime="1.0", payload="x")),
                 ({}, member(mtime="01.0")), ({}, member(mtime="2.0")),
                 ({}, member(float("inf"), mtime="1E+1000000")))
        for global_pax, item in cases:
            with self.assertRaises(ValueError):
                verify_pax_fields(SimpleNamespace(pax_headers=global_pax), [item])
    def test_rejects_renamed_distributions_before_opening(self):
        with self.assertRaisesRegex(ValueError, "wheel filename"):
            verify_wheel(Path("renamed.whl"))
        with self.assertRaisesRegex(ValueError, "sdist filename"):
            verify_sdist(Path("renamed.tar.gz"), b"")
