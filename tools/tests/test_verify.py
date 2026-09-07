from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import mix, verify  # noqa: E402


class EngineContractTests(unittest.TestCase):
    def build(self, directory: Path, *, cache_holds_fonts: bool = True) -> tuple[Path, Path]:
        output = directory / "MIX"
        loose = directory / "ini"
        output.mkdir()
        loose.mkdir()

        names = (
            verify.REQUIRED_ARCHIVES
            + verify.THEATER_ARCHIVES
            + ("MOVIES01", "SOUNDS01")
        )
        for label in names:
            mix.write_archive(output / f"{label}.MIX", mix.pack([]))

        members = [mix.Member(name, b"x") for name in verify.CACHED_BY_NAME] if cache_holds_fonts else []
        mix.write_archive(output / "CACHE.MIX", mix.pack(members))

        (loose / "firestrm.ini").write_bytes(b"[General]\r\n")
        return output, loose

    def test_a_complete_build_passes(self):
        with tempfile.TemporaryDirectory() as raw:
            output, loose = self.build(Path(raw))
            findings = verify.engine_contract(output, loose)
            self.assertTrue(findings.ok, findings.problems)

    def test_a_missing_archive_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            output, loose = self.build(Path(raw))
            (output / "CONQUER.MIX").unlink()

            findings = verify.engine_contract(output, loose)

            self.assertFalse(findings.ok)
            self.assertTrue(any("CONQUER.MIX" in problem for problem in findings.problems))

    def test_fonts_must_be_in_the_cached_archive(self):
        # Startup reads these specifically from CACHE.MIX.
        with tempfile.TemporaryDirectory() as raw:
            output, loose = self.build(Path(raw), cache_holds_fonts=False)

            findings = verify.engine_contract(output, loose)

            self.assertFalse(findings.ok)
            self.assertTrue(any("CACHE.MIX" in problem for problem in findings.problems))

    def test_the_expansion_must_be_switched_on(self):
        with tempfile.TemporaryDirectory() as raw:
            output, loose = self.build(Path(raw))
            (loose / "firestrm.ini").unlink()

            findings = verify.engine_contract(output, loose)

            self.assertFalse(findings.ok)
            self.assertTrue(any("firestrm.ini" in problem for problem in findings.problems))


if __name__ == "__main__":
    unittest.main()
