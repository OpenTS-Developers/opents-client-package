from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import assemble, mix, pack  # noqa: E402


def repository(root: Path) -> Path:
    parts = root / ".cache" / "parts"
    for name, files in {
        "engine": {"Game.exe": b"game", "Language.dll": b"strings"},
        "launcher": {
            assemble.LAUNCHER_SOURCE: b"stub",
            f"{assemble.LAUNCHER_SOURCE}.config": b"<configuration/>",
        },
    }.items():
        (parts / name).mkdir(parents=True)
        for filename, data in files.items():
            (parts / name / filename).write_bytes(data)

    (parts / "client" / "Resources" / "Binaries").mkdir(parents=True)
    (parts / "client" / "Resources" / "clientdx.exe").write_bytes(b"client")
    (parts / "client" / "Resources" / "Binaries" / "a.dll").write_bytes(b"lib")

    for name in assemble.EDITOR_FILES:
        path = parts / "editor" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"editor")
    (parts / "editor/Config/Default/Constants.ini").write_bytes(
        b"[Constants]\r\nExpectedClientExecutableName=SUN.EXE,OpenTS.exe\r\nMaxWaypoint=702\r\nEnableIniInclude=true\r\n"
    )
    (parts / "editor/Config/DefaultSettings.ini").write_bytes(
        b"; Upstream defaults\r\n[General]\r\nSidebarWidth=265\r\n[Display]\r\nConserveVRAM=no\r\n"
    )
    (root / "editor").mkdir()
    for name in ("LICENSE.txt", "COPYING", "SOURCE.txt"):
        (root / "editor" / name).write_bytes(b"license and source")

    built = root / pack.OUTPUT
    built.mkdir(parents=True)
    for label in ("CONQUER", "MOVIES00", assemble.CAMPAIGN_MOVIES.split(".")[0]):
        mix.write_archive(built / f"{label}.MIX", mix.pack([mix.Member("A.SHP", b"art")]))

    for folder, filename, data in (
        ("ini", "rules.ini", b"[General]\r\n"),
        ("ini", "battle.ini", b"[Battles]\r\n1=GDI1\r\n"),
        ("ini", "battlefs.ini", b"[Battles]\r\n1=GDIFS\r\n"),
        ("maps/Multiplayer", "a_map.map", b"[Map]\r\n"),
        ("client/Maps/Multiplayer", "a_map.png", b"png"),
        ("client/Resources", "ClientDefinitions.ini", b"[Themes]\r\n0=Default,Default Theme/\r\n[Settings]\r\nLocalGame=ots\r\nIgnoreBattleIni=true\r\nBattleFSFileName=ClientBattle.ini\r\n"),
        ("client/Resources", "GameCollectionConfig.ini", b"[CustomGames]\n0=OTS\n[OTS]\nInternalName=ots\nIconFilename=opentsicon.png\n"),
        ("client/Resources", "opentsicon.png", b"icon"),
        ("client/Resources", "KeyboardCommands.ini", b"[Move]\nDefaultKey=0\n"),
        ("client/Resources", "Renderers.ini", b"[Renderers]\n0=Default\n[Default]\nUseQres=false\nSingleCoreAffinity=false\n"),
        ("client/Resources/Default Theme", "DTACnCNetClient.ini", b"[General]\n"),
        ("client/INI", "MPMaps.ini", b"[MultiMaps]\r\n0=Maps/Multiplayer/a_map\r\n[Maps/Multiplayer/a_map]\r\n"),
        ("client/INI", "ClientBattle.ini", b"[Battles]\r\n0=TSCMPGNS\r\n[TSCMPGNS]\r\n"),
        ("client/root", "OPENTS.INI", b"[Paths]\r\n"),
    ):
        target = root / folder
        target.mkdir(parents=True, exist_ok=True)
        (target / filename).write_bytes(data)

    return root


class AssembleTests(unittest.TestCase):
    def test_editor_keeps_standard_profile_and_sets_a_relative_game_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))
            built = assemble.run(root, root / "out")
            editor = built.root / "MapEditor"
            self.assertTrue((editor / "WorldAlteringEditor.exe").is_file())
            self.assertEqual((editor / "Config/Default/Constants.ini").read_bytes(),
                             (root / ".cache/parts/editor/Config/Default/Constants.ini").read_bytes())
            self.assertEqual((editor / "Config/DefaultSettings.ini").read_bytes(),
                             b"; Upstream defaults\r\n[General]\r\nSidebarWidth=265\r\nGameDirectory=..\r\n[Display]\r\nConserveVRAM=no\r\n")
            self.assertNotIn(b"GameDirectory", (root / ".cache/parts/editor/Config/DefaultSettings.ini").read_bytes())
            self.assertFalse((editor / "MapEditorSettings.ini").exists())
            for name in ("LICENSE.txt", "COPYING", "SOURCE.txt"):
                self.assertEqual((editor / name).read_bytes(), b"license and source")

    def test_editor_must_support_the_launcher_and_include_its_runtime(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))
            constants = root / ".cache/parts/editor/Config/Default/Constants.ini"
            constants.write_text("[Constants]\nExpectedClientExecutableName=SUN.EXE\n")
            with self.assertRaisesRegex(assemble.AssembleError, "does not recognize OpenTS.exe"):
                assemble.run(root, root / "out")
            constants.write_text("[Constants]\nExpectedClientExecutableName=OpenTS.exe\n")
            (root / ".cache/parts/editor/WorldAlteringEditor.runtimeconfig.json").unlink()
            with self.assertRaisesRegex(assemble.AssembleError, "map editor"):
                assemble.run(root, root / "out")

    def test_explicit_archive_list_excludes_stale_outputs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))
            selected = root / pack.OUTPUT / "CONQUER.MIX"
            built = assemble.run(root, root / "out", archives=[selected])
            self.assertEqual([p.name for p in (built.root / "MIX").iterdir()], ["CONQUER.MIX"])

    def test_the_smaller_build_leaves_the_campaign_movies_out(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            built = assemble.run(root, root / "out", variant="nomovies")

            self.assertFalse((built.root / "MIX" / assemble.CAMPAIGN_MOVIES).exists())
            self.assertTrue((built.root / "MIX" / "MOVIES00.MIX").is_file())

    def test_a_full_build_ships_them(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            built = assemble.run(root, root / "out", variant="full")

            self.assertTrue((built.root / "MIX" / assemble.CAMPAIGN_MOVIES).is_file())

    def test_the_launcher_takes_our_name_and_keeps_its_settings(self):
        # .NET requires the config filename to match the executable.
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            built = assemble.run(root, root / "out")

            self.assertTrue((built.root / "OpenTS.exe").is_file())
            self.assertTrue((built.root / "OpenTS.exe.config").is_file())
            self.assertFalse((built.root / "OpenTSClient.exe").exists())
            self.assertFalse((built.root / "OpenTSClient.exe.config").exists())
            self.assertFalse((built.root / assemble.LAUNCHER_SOURCE).exists())

    def test_our_configuration_joins_the_client_it_configures(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            built = assemble.run(root, root / "out")

            resources = built.root / "Resources"
            self.assertTrue((resources / "clientdx.exe").is_file())
            self.assertTrue((resources / "Binaries" / "a.dll").is_file())
            self.assertTrue((resources / "ClientDefinitions.ini").is_file())

    def test_the_game_and_client_configuration_share_one_folder(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            built = assemble.run(root, root / "out")

            self.assertTrue((built.root / "INI" / "rules.ini").is_file())
            self.assertTrue((built.root / "INI" / "MPMaps.ini").is_file())
            self.assertTrue((built.root / "Maps" / "Multiplayer" / "a_map.map").is_file())
            self.assertTrue((built.root / "OPENTS.INI").is_file())

    def test_a_map_preview_lands_beside_its_map(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            built = assemble.run(root, root / "out")

            self.assertTrue((built.root / "Maps" / "Multiplayer" / "a_map.png").is_file())

    def test_the_game_and_the_client_each_keep_their_own_campaign_list(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            built = assemble.run(root, root / "out")

            for name in ("battle.ini", "battlefs.ini"):
                self.assertEqual((built.root / "INI" / name).read_bytes(), (root / "ini" / name).read_bytes())
                self.assertFalse((built.root / name).exists())
            self.assertEqual(
                (built.root / "INI" / "ClientBattle.ini").read_bytes(),
                (root / "client" / "INI" / "ClientBattle.ini").read_bytes(),
            )

    def test_game_inis_override_client_inis(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))
            (root / "client/INI/rules.ini").write_bytes(b"client defaults")

            built = assemble.run(root, root / "out")

            self.assertEqual((built.root / "INI/rules.ini").read_bytes(), (root / "ini/rules.ini").read_bytes())

    def test_a_build_is_emptied_before_it_is_written(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))
            stale = root / "out" / "MIX" / "GONE.MIX"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"old")

            built = assemble.run(root, root / "out")

            self.assertFalse((built.root / "MIX" / "GONE.MIX").exists())

    def test_an_unknown_variant_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))

            with self.assertRaises(assemble.AssembleError):
                assemble.run(root, root / "out", variant="deluxe")

    def test_assembling_without_the_parts_says_what_to_run(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(assemble.AssembleError) as caught:
                assemble.run(Path(raw), Path(raw) / "out")

            self.assertIn("fetch", str(caught.exception))


class BundleTests(unittest.TestCase):
    def test_the_same_build_gives_the_same_zip(self):
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))
            built = assemble.run(root, root / "out")

            (root / "one").mkdir()
            (root / "two").mkdir()
            first = assemble.bundle(built.root, root / "one" / "OpenTS-Client.zip")
            second = assemble.bundle(built.root, root / "two" / "OpenTS-Client.zip")

            self.assertEqual(first.path.read_bytes(), second.path.read_bytes())
            self.assertEqual(first.files, second.files)

    def test_the_build_unzips_into_a_folder_of_its_own(self):
        # Tags and variants must not change the extracted folder name.
        with tempfile.TemporaryDirectory() as raw:
            root = repository(Path(raw))
            built = assemble.run(root, root / "out")

            bundled = assemble.bundle(built.root, root / "OpenTS-Client-NoMovies-v9.zip")

            with zipfile.ZipFile(bundled.path) as held:
                roots = {Path(name).parts[0] for name in held.namelist()}
            self.assertEqual(roots, {assemble.DISTRIBUTION})

    def test_the_complete_game_takes_the_plain_name(self):
        self.assertEqual(assemble.bundle_name("full", "v0.2.0"), "OpenTS-Client-v0.2.0.zip")
        self.assertEqual(
            assemble.bundle_name("nomovies", "v0.2.0"), "OpenTS-Client-NoMovies-v0.2.0.zip"
        )
        self.assertEqual(assemble.bundle_name("full"), "OpenTS-Client.zip")
        with self.assertRaises(assemble.AssembleError):
            assemble.bundle_name("deluxe")

    def test_bundling_nothing_says_so(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(assemble.AssembleError):
                assemble.bundle(Path(raw) / "absent", Path(raw) / "out.zip")
