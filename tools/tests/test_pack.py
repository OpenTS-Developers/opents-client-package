from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import mix, pack  # noqa: E402


class PackRunTests(unittest.TestCase):
    def test_hidden_working_directories_are_not_game_archives(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.build_tree(root)
            (root / "assets" / ".interrupted-download" / "new").mkdir(parents=True)
            self.assertEqual([p.name for p in pack.asset_directories(root)], ["CONQUER"])

    def build_tree(self, root: Path) -> None:
        conquer = root / "assets" / "CONQUER"
        conquer.mkdir(parents=True)
        (conquer / "palette.pal").write_bytes(b"palette bytes")
        (conquer / "gacnst.shp").write_bytes(b"shape bytes")

    def test_an_archive_is_built_for_each_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.build_tree(root)

            results = pack.run(root, root / "MIX")

            self.assertEqual([result.name for result in results], ["CONQUER"])
            built = root / "MIX" / "CONQUER.MIX"
            self.assertTrue(built.is_file())
            self.assertEqual(pack.contents(built)[mix.mix_id("PALETTE.PAL")], b"palette bytes")

    def test_an_unchanged_archive_is_left_alone(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.build_tree(root)

            first = pack.run(root, root / "MIX")
            second = pack.run(root, root / "MIX")

            self.assertTrue(first[0].rebuilt)
            self.assertFalse(second[0].rebuilt)

    def test_a_changed_file_causes_a_rebuild(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.build_tree(root)
            pack.run(root, root / "MIX")

            (root / "assets" / "CONQUER" / "palette.pal").write_bytes(b"different bytes")
            again = pack.run(root, root / "MIX")

            self.assertTrue(again[0].rebuilt)
            self.assertEqual(
                pack.contents(root / "MIX" / "CONQUER.MIX")[mix.mix_id("PALETTE.PAL")],
                b"different bytes",
            )

    def test_the_same_assets_give_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.build_tree(root)

            pack.run(root, root / "one")
            pack.run(root, root / "two", force=True)

            self.assertEqual(
                (root / "one" / "CONQUER.MIX").read_bytes(),
                (root / "two" / "CONQUER.MIX").read_bytes(),
            )

    def test_an_empty_tree_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(pack.PackError):
                pack.run(Path(raw), Path(raw) / "MIX")


if __name__ == "__main__":
    unittest.main()
