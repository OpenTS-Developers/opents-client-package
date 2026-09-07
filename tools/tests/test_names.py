"""Name recovery using synthetic archives; no game installation required."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import mix, names, seed  # noqa: E402


class ConfigurationTests(unittest.TestCase):
    def test_a_file_opening_with_comments_is_still_recognised(self):
        # Theater INIs start with comments before their first section.
        data = b"; Tiberian Sun\r\n; theater\r\n\r\n[General]\r\nName=Temperate\r\n"
        self.assertTrue(names.looks_like_configuration(data))

    def test_plain_text_is_not_configuration(self):
        self.assertFalse(names.looks_like_configuration(b"just some prose, no sections"))

    def test_tileset_names_are_taken_from_the_theater_file(self):
        data = b"[TileSet0000]\r\nFileName=CLIFF\r\nTilesInSet=4\r\n"
        self.assertEqual(names.tileset_bases(data), {"CLIFF"})


class DatabaseTests(unittest.TestCase):
    """MIX hashes ignore filename case."""

    def assertNamed(self, database, name):
        resolved = database.resolve(mix.mix_id(name))
        self.assertIsNotNone(resolved, f"{name} was not recovered")
        self.assertEqual(resolved.lower(), name.lower())

    def test_the_list_is_loaded(self):
        self.assertGreater(len(names.known_names()), 10000)

    def test_engine_files_are_known(self):
        database = names.build([])
        self.assertNamed(database, "CONQUER.MIX")
        self.assertNamed(database, "PALETTE.PAL")

    def test_spoken_lines_are_generated(self):
        database = names.build([])
        self.assertNamed(database, "00-I026.AUD")
        self.assertNamed(database, "41-N123.AUD")

    def test_words_are_tried_with_the_game_s_extensions(self):
        configuration = b"[General]\r\nImage=ZZQQTEST\r\n"
        database = names.build([configuration])
        self.assertNamed(database, "ZZQQTEST.SHP")

    def test_tileset_files_are_reached_through_their_numbers(self):
        configuration = b"[TileSet0000]\r\nFileName=ZZCLIFF\r\n"
        database = names.build([configuration])
        self.assertNamed(database, "ZZCLIFF01.TEM")
        self.assertNamed(database, "ZZCLIFF07a.SNO")

    def test_the_communitys_list_is_trusted_over_a_guess(self):
        # R4ES.THM collides with the known ctaray_c.shp hash.
        database = names.build([b"[General]\r\nName=R4ES\r\n"])
        self.assertEqual(database.resolve(mix.mix_id("R4ES.THM")), "ctaray_c.shp")


class CollectTests(unittest.TestCase):
    def nested_installation(self, directory: Path) -> None:
        inner = mix.pack([mix.Member("PALETTE.PAL", b"palette bytes")])
        # SHP files share the MIX zero-byte prefix.
        shape = b"\x00\x00\x40\x00\x30\x00\x02\x00" + b"shape body"
        outer = mix.pack(
            [
                mix.Member("CACHE.MIX", mix.serialize_archive(inner)),
                mix.Member("GACNST.SHP", shape),
            ]
        )
        mix.write_archive(directory / "TIBSUN.MIX", outer)

    def test_nested_archives_are_lifted_out(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw)
            self.nested_installation(source)

            archives = seed.collect(source)

            self.assertIn("TIBSUN", archives)
            self.assertIn("CACHE", archives)
            self.assertEqual(len(archives), 2)

    def test_shape_files_are_not_read_as_archives(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw)
            self.nested_installation(source)

            archives = seed.collect(source)

            self.assertEqual(sorted(archives), ["CACHE", "TIBSUN"])


class SeedTests(unittest.TestCase):
    def build_installation(self, directory: Path) -> None:
        inner = mix.pack([mix.Member("PALETTE.PAL", b"palette bytes")])
        outer = mix.pack(
            [
                mix.Member("CACHE.MIX", mix.serialize_archive(inner)),
                mix.Member("KEY.INI", b"[PublicKey]\r\n1=x\r\n"),
            ]
        )
        mix.write_archive(directory / "TIBSUN.MIX", outer)

    def test_each_side_keeps_its_own_copy_of_a_shared_name(self):
        # Side archives mount separately and may reuse names for different artwork.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()

            gdi = mix.pack([mix.Member("LIMPICON.SHP", b"the GDI icon")])
            nod = mix.pack([mix.Member("LIMPICON.SHP", b"the Nod icon")])
            expansion = mix.pack(
                [
                    mix.Member("E01SC01.MIX", mix.serialize_archive(gdi)),
                    mix.Member("E01SC02.MIX", mix.serialize_archive(nod)),
                ]
            )
            mix.write_archive(source / "EXPAND01.MIX", expansion)

            seed.run(source, root)

            self.assertEqual(
                (root / "assets" / "SIDEC01" / "limpicon.shp").read_bytes(), b"the GDI icon"
            )
            self.assertEqual(
                (root / "assets" / "SIDEC02" / "limpicon.shp").read_bytes(), b"the Nod icon"
            )

    def movie_installation(self, directory: Path, **archives: dict) -> None:
        for label, members in archives.items():
            mix.write_archive(
                directory / f"{label}.MIX",
                mix.pack([mix.Member(name, data) for name, data in members.items()]),
            )

    def test_the_movie_archives_are_settled_into_one(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.movie_installation(
                source,
                MOVIES01={"NOD_FLAG.VQA": b"a flag", "WWLOGO.VQA": b"the logo"},
                MOVIES03={"FSGDIM06.VQA": b"firestorm", "WWLOGO.VQA": b"the logo"},
            )

            seed.run(source, root)

            self.assertTrue((root / "assets" / "MOVIES" / "nod_flag.vqa").is_file())
            self.assertTrue((root / "assets" / "MOVIES" / "fsgdim06.vqa").is_file())
            # MOVIES00 also ships in no-movies builds.
            self.assertTrue((root / "assets" / "MOVIES00" / "wwlogo.vqa").is_file())
            self.assertFalse((root / "assets" / "MOVIES01").exists())
            self.assertFalse((root / "assets" / "MOVIES03").exists())

    def test_each_campaign_keeps_its_own_intro(self):
        # Both discs name their distinct campaign intros INTRO.VQA.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.movie_installation(
                source,
                MOVIES01={"INTRO.VQA": b"the GDI intro"},
                MOVIES02={"INTRO.VQA": b"the Nod intro"},
            )

            seed.run(source, root)

            movies = root / "assets" / "MOVIES"
            self.assertEqual((movies / "intr0.vqa").read_bytes(), b"the GDI intro")
            self.assertEqual((movies / "intr1.vqa").read_bytes(), b"the Nod intro")
            self.assertFalse((movies / "intro.vqa").exists())

    def test_a_movie_held_by_two_archives_is_written_once(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.movie_installation(
                source,
                MOVIES01={"NOD_FLAG.VQA": b"the same flag"},
                MOVIES02={"NOD_FLAG.VQA": b"the same flag"},
            )

            report = seed.run(source, root)

            self.assertEqual(
                (root / "assets" / "MOVIES" / "nod_flag.vqa").read_bytes(), b"the same flag"
            )
            carried = [record for record in report.records if record.destination]
            self.assertEqual(len(carried), 1)

    def test_movies_sharing_a_name_but_not_their_contents_are_refused(self):
        # Shared names with different bytes would lose content when merged.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.movie_installation(
                source,
                MOVIES01={"NOD_FLAG.VQA": b"one flag"},
                MOVIES02={"NOD_FLAG.VQA": b"a different flag"},
            )

            with self.assertRaises(seed.SeedError) as caught:
                seed.run(source, root)

            self.assertIn("nod_flag.vqa", str(caught.exception))

    def test_a_menus_layout_stays_with_the_menu_it_draws(self):
        # Menu layouts stay with their artwork; other INIs ship loose.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()

            menu = mix.pack(
                [
                    mix.Member("NEWMENU.INI", b"[Menu]\r\nName=main\r\n"),
                    mix.Member("RULES.INI", b"[General]\r\nSpeed=1\r\n"),
                ]
            )
            mix.write_archive(source / "GMENU.MIX", menu)

            seed.run(source, root)

            self.assertTrue((root / "assets" / "GMENU" / "newmenu.ini").is_file())
            self.assertFalse((root / "ini" / "newmenu.ini").exists())
            self.assertTrue((root / "ini" / "rules.ini").is_file())

    def test_members_are_written_where_they_belong(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.build_installation(source)

            report = seed.run(source, root)

            self.assertTrue((root / "assets" / "CACHE" / "palette.pal").is_file())
            self.assertEqual(
                (root / "assets" / "CACHE" / "palette.pal").read_bytes(), b"palette bytes"
            )
            self.assertTrue((root / "seed-report.json").is_file())
            self.assertEqual(report.archives["CACHE"], 1)

    def test_a_lifted_archive_is_not_also_kept_packed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.build_installation(source)

            seed.run(source, root)

            self.assertFalse((root / "assets" / "TIBSUN" / "cache.mix").exists())

    def test_the_game_s_own_keys_are_not_carried_over(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.build_installation(source)

            seed.run(source, root)

            self.assertFalse((root / "assets" / "TIBSUN" / "key.ini").exists())

    def test_a_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repository"
            source = Path(raw) / "game"
            root.mkdir()
            source.mkdir()
            self.build_installation(source)

            report = seed.run(source, root, dry_run=True)

            self.assertFalse((root / "assets").exists())
            self.assertFalse((root / "seed-report.json").exists())
            self.assertTrue(report.records)


if __name__ == "__main__":
    unittest.main()
