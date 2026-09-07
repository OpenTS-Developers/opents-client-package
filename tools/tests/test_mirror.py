from __future__ import annotations

import lzma
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import mirror  # noqa: E402


def build_tree(root: Path) -> None:
    """Build compressible, uncompressed and updater fixtures."""

    (root / "MIX").mkdir(parents=True)
    (root / "INI").mkdir()
    (root / "MIX" / "CONQUER.MIX").write_bytes(b"shape " * 20000)
    (root / "MIX" / "MOVIES00.MIX").write_bytes(b"vqa " * 20000)
    (root / "INI" / "rules.ini").write_bytes(b"[General]\r\n")
    (root / "Game.exe").write_bytes(b"code " * 20000)
    (root / "SUN.ini").write_bytes(b"[Video]\r\nRenderer=0\r\n")
    (root / "version").write_text("[DTA]\nVersion=stale\n")


class StampTests(unittest.TestCase):
    def test_a_stamp_is_the_md5_as_decimal_bytes_and_the_size_in_kb(self):
        # Empty-input MD5: d41d8cd98f00b204e9800998ecf8427e.
        self.assertEqual(mirror.stamp(b""), "2122914021714301784233128915223624866126,0")
        self.assertTrue(mirror.stamp(b"x" * 3000).endswith(",2"))

    def test_paths_are_written_the_way_the_updater_writes_them(self):
        self.assertEqual(mirror.updater_path(Path("INI") / "rules.ini"), "INI\\rules.ini")


class CompressionTests(unittest.TestCase):
    def test_the_stream_is_alone_format_with_the_size_left_unknown(self):
        # Unknown size signals the LZMA end marker.
        blob = mirror.lzma_alone(b"OpenTS" * 50000)

        self.assertEqual(struct.unpack("<q", blob[5:13])[0], -1)
        self.assertEqual(lzma.decompress(blob, format=lzma.FORMAT_ALONE), b"OpenTS" * 50000)

    def test_what_is_already_compressed_is_stored_plain(self):
        big = mirror.ARCHIVE_FROM * 2
        self.assertTrue(mirror.archived(Path("MIX/CONQUER.MIX"), big))
        self.assertFalse(mirror.archived(Path("MIX/MOVIES00.MIX"), big))
        self.assertFalse(mirror.archived(Path("MIX/SCORES.MIX"), big))
        self.assertFalse(mirror.archived(Path("a.png"), big))
        self.assertFalse(mirror.archived(Path("MIX/CONQUER.MIX"), 100))


class IniTests(unittest.TestCase):
    def test_a_line_without_equals_is_a_key(self):
        text = "[Delete]\n; note\nMIX\\OLD.MIX\nINI\\gone.ini=\n\n[Other]\nx=1\n"
        self.assertEqual(mirror.section(text, "delete"), {"MIX\\OLD.MIX": "", "INI\\gone.ini": ""})
        self.assertEqual(mirror.section(text, "Other"), {"x": "1"})

    def test_the_version_file_has_its_sections_in_order(self):
        text = mirror.version_text("v1", {"b": "2,0", "a": "1,0"}, {"a": "9,0"}, {"MOVIES": "5,1"})
        self.assertEqual(
            text.splitlines(),
            ["[DTA]", "Version=v1", "", "[FileVersions]", "a=1,0", "b=2,0", "",
             "[ArchivedFiles]", "a=9,0", "", "[AddOns]", "MOVIES=5,1"],
        )


class UpdateScriptTests(unittest.TestCase):
    def test_the_first_release_removes_nothing(self):
        self.assertEqual(mirror.update_script("v1", None, []), "[Rename]\n\n[Delete]\n")

    def test_a_script_carried_forward_unchanged_is_the_same_bytes(self):
        # Whitespace growth would trigger needless nightly uploads.
        first = mirror.update_script("v1", None, [])
        second = mirror.update_script("v2", first, [])
        self.assertEqual(second, first)

        earlier = "[Rename]\nold=new\n\n[Delete]\n; v1: no longer shipped\nA\n; end v1\n"
        self.assertEqual(mirror.update_script("v3", mirror.update_script("v2", earlier, []), []),
                         mirror.update_script("v2", earlier, []))

    def test_earlier_removals_are_carried_forward_as_written(self):
        # Clients skipping versions still need every earlier removal.
        earlier = "[Rename]\nold=new\n\n[Delete]\n; v1: no longer shipped\nA\n; end v1\n"

        text = mirror.update_script("v2", earlier, ["B"])

        self.assertIn("old=new", text)
        self.assertIn("; v1: no longer shipped\nA\n; end v1\n\n; v2: no longer shipped\nB\n; end v2", text)
        self.assertEqual(mirror.section(text, "Delete"), {"A": "", "B": ""})


class BuildTests(unittest.TestCase):
    def test_a_mirror_is_laid_out_with_the_component_beside_the_versions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "MOVIES.MIX").write_bytes(b"movie " * 1000)

            result = mirror.build(root / "base", "v1", root / "out", component=root / "MOVIES.MIX")

            self.assertTrue((root / "out" / "v1" / "MIX" / "CONQUER.MIX.lzma").is_file())
            self.assertTrue((root / "out" / "v1" / "MIX" / "MOVIES00.MIX").is_file())
            self.assertTrue((root / "out" / "v1" / "INI" / "rules.ini").is_file())
            self.assertTrue((root / "out" / "Components" / "MOVIES.MIX").is_file())
            self.assertEqual(result.archived, 2)

            text = (root / "out" / "v1" / "version").read_text()
            files = mirror.section(text, "FileVersions")
            self.assertIn("MIX\\CONQUER.MIX", files)
            self.assertNotIn("version", files)
            self.assertNotIn("MIX\\MOVIES.MIX", files)
            self.assertEqual(mirror.section(text, "AddOns"), {"MOVIES": result.component})
            self.assertEqual(mirror.section(text, "DTA")["Version"], "v1")

    def test_what_a_player_writes_is_never_tracked(self):
        # Tracking SUN.ini would overwrite player settings.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "MOVIES.MIX").write_bytes(b"m")

            mirror.build(root / "base", "v1", root / "out", component=root / "MOVIES.MIX")

            text = (root / "out" / "v1" / "version").read_text()
            self.assertNotIn("SUN.ini", mirror.section(text, "FileVersions"))
            self.assertNotIn("SUN.ini", mirror.stamp_tree(root / "base"))
            self.assertFalse((root / "out" / "v1" / "SUN.ini").exists())
            self.assertTrue((root / "base" / "SUN.ini").is_file())

    def test_the_channel_a_player_chose_is_never_tracked(self):
        # Preserve the selected channel; ship its source templates.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "base" / "Resources").mkdir()
            (root / "base" / "Resources" / "UpdaterConfig.ini").write_bytes(b"[DownloadMirrors]\r\n")
            (root / "base" / "Resources" / "UpdaterConfig_Live.ini").write_bytes(b"[DownloadMirrors]\r\n")
            (root / "MOVIES.MIX").write_bytes(b"m")

            mirror.build(root / "base", "v1", root / "out", component=root / "MOVIES.MIX")

            files = mirror.section((root / "out" / "v1" / "version").read_text(), "FileVersions")
            self.assertNotIn("Resources\\UpdaterConfig.ini", files)
            self.assertIn("Resources\\UpdaterConfig_Live.ini", files)
            self.assertFalse((root / "out" / "v1" / "Resources" / "UpdaterConfig.ini").exists())

    def test_editor_preferences_are_never_tracked(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "base/MapEditor/Config").mkdir(parents=True)
            (root / "base/MapEditor/MapEditorSettings.ini").write_text("[General]\nGameDirectory=private-path\n")
            (root / "base/MapEditor/Config/DefaultSettings.ini").write_text("[General]\nGameDirectory=..\n")
            (root / "MOVIES.MIX").write_bytes(b"m")
            mirror.build(root / "base", "v1", root / "out", component=root / "MOVIES.MIX")
            files = mirror.section((root / "out/v1/version").read_text(), "FileVersions")
            self.assertNotIn("MapEditor\\MapEditorSettings.ini", files)
            self.assertIn("MapEditor\\Config\\DefaultSettings.ini", files)
            self.assertFalse((root / "out/v1/MapEditor/MapEditorSettings.ini").exists())

    def test_files_no_longer_shipped_are_named_for_removal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "MOVIES.MIX").write_bytes(b"m")
            previous = "[FileVersions]\nMIX\\CONQUER.MIX=1,1\nMIX\\GONE.MIX=2,2\n"

            result = mirror.build(root / "base", "v2", root / "out", component=root / "MOVIES.MIX",
                                  previous_version=previous)

            script = (root / "out" / "v2" / "updateexec").read_text()
            self.assertEqual(result.removed, 1)
            self.assertEqual(mirror.section(script, "Delete"), {"MIX\\GONE.MIX": ""})

    def test_the_same_build_gives_the_same_mirror(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "MOVIES.MIX").write_bytes(b"m")

            mirror.build(root / "base", "v1", root / "one", component=root / "MOVIES.MIX")
            mirror.build(root / "base", "v1", root / "two", component=root / "MOVIES.MIX")

            for name in ("version", "updateexec", "MIX/CONQUER.MIX.lzma"):
                self.assertEqual((root / "one" / "v1" / name).read_bytes(), (root / "two" / "v1" / name).read_bytes())

    def test_a_full_build_supplies_its_own_component(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "base" / "MIX" / "MOVIES.MIX").write_bytes(b"movie")

            result = mirror.build(root / "base", "v1", root / "out")

            self.assertEqual(result.component, mirror.stamp(b"movie"))
            self.assertFalse((root / "out" / "v1" / "MIX" / "MOVIES.MIX").exists())

    def test_a_mirror_without_the_component_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")

            with self.assertRaises(mirror.MirrorError):
                mirror.build(root / "base", "v1", root / "out")


class CheckTests(unittest.TestCase):
    def test_a_built_mirror_reads_back_clean(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "MOVIES.MIX").write_bytes(b"m" * 2000)
            mirror.build(root / "base", "v1", root / "out", component=root / "MOVIES.MIX")

            checked, problems = mirror.check(root / "out", "v1")

            self.assertEqual(problems, [])
            self.assertGreaterEqual(checked, 5)

    def test_a_changed_file_is_caught(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build_tree(root / "base")
            (root / "MOVIES.MIX").write_bytes(b"m")
            mirror.build(root / "base", "v1", root / "out", component=root / "MOVIES.MIX")
            (root / "out" / "v1" / "INI" / "rules.ini").write_bytes(b"[Tampered]\r\n")
            (root / "out" / "Components" / "MOVIES.MIX").write_bytes(b"other")

            _, problems = mirror.check(root / "out", "v1")

            self.assertEqual(len(problems), 2)


class ReadbackTests(unittest.TestCase):
    def _laid_out(self, root: Path) -> Path:
        build_tree(root / "base")
        (root / "MOVIES.MIX").write_bytes(b"m")
        mirror.build(root / "base", "v1", root / "out", component=root / "MOVIES.MIX")
        return root / "out" / "v1"

    def test_a_version_served_as_laid_out_reads_back_clean(self):
        with tempfile.TemporaryDirectory() as raw:
            version = self._laid_out(Path(raw))
            asked: list[str] = []

            def fetch(name: str) -> bytes:
                asked.append(name)
                return (version / name).read_bytes()

            checked, problems = mirror.readback(version, fetch)

            self.assertEqual(problems, [])
            self.assertEqual(checked, len(asked))
            self.assertIn("version", asked)
            self.assertTrue(any(name.endswith(".lzma") for name in asked))
            self.assertFalse(any("\\" in name for name in asked))

    def test_what_is_served_differently_is_caught(self):
        with tempfile.TemporaryDirectory() as raw:
            version = self._laid_out(Path(raw))

            def altered(name: str) -> bytes:
                data = (version / name).read_bytes()
                return data + b"!" if name == "INI/rules.ini" else data

            _, problems = mirror.readback(version, altered)
            self.assertEqual(problems, ["INI\\rules.ini served does not match the version file"])

            asked: list[str] = []

            def stale(name: str) -> bytes:
                asked.append(name)
                return b"[DTA]\nVersion=v0\n"

            checked, problems = mirror.readback(version, stale)
            self.assertEqual(problems, ["the version file served is not the one laid out"])
            self.assertEqual((checked, asked), (1, ["version"]))
