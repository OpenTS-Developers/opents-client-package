from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import assemble, dev, fetch, pack, verify  # noqa: E402


class DevTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.write(".cache/parts/client/Resources/clientdx.exe", b"client")
        self.write(".cache/parts/launcher/CnCNet.LauncherStub.exe", b"launcher")
        self.write(".cache/parts/launcher/CnCNet.LauncherStub.exe.config", b"config")
        self.write("client/Resources/ClientDefinitions.ini", b"; Keep this comment\r\n[Themes]\r\n0=Default,Default Theme/\r\n[Settings]\r\nModMode=false\r\nWindowTitle=OpenTS\r\nLocalGame=ots\r\nIgnoreBattleIni=true\r\nBattleFSFileName=ClientBattle.ini\r\nMapEditorExePath=MapEditor/WorldAlteringEditor.exe\r\n")
        self.write("client/Resources/GameCollectionConfig.ini", b"[CustomGames]\n0=OTS\n[OTS]\nInternalName=ots\nIconFilename=opentsicon.png\n")
        self.write("client/Resources/opentsicon.png", b"icon")
        self.write("client/Resources/Default Theme/DTACnCNetClient.ini", b"[General]\n")
        self.write("client/Resources/KeyboardCommands.ini", b"[Options]\nDefaultKey=27\n")
        self.write("client/Resources/Renderers.ini", b"[Renderers]\n0=Default\n[Default]\nUseQres=false\nSingleCoreAffinity=false\n")
        self.write("client/Resources/MainMenu.ini", b"[MainMenu]\nText=old menu\n")
        self.write("client/Resources/Removed.ini", b"[Removed]\nText=old input\n")
        self.write("client/INI/ClientBattle.ini", b"[Battles]\n0=Mission\n[Mission]\nDescription=client campaigns\nScenario=Maps/Missions/mission.map\n")
        self.write("client/INI/MPMaps.ini", b"[MultiMaps]\n0=Maps/Multiplayer/map\n[Maps/Multiplayer/map]\nDescription=Test\n")
        self.write("client/Maps/Multiplayer/map.png", b"preview")
        self.write("maps/Multiplayer/map.map", b"map")
        self.write("maps/Missions/mission.map", b"mission")
        self.write("ini/battle.ini", b"engine campaigns")
        self.write("ini/keyboard.ini", b"game keyboard defaults")
        self.write("client/root/OPENTS.INI", b"paths")
        self.fetch = self.enterContext(patch.object(fetch, "run", return_value=[]))
        self.launch = self.enterContext(patch.object(dev, "_launch"))

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def prepare(self, **kwargs) -> Path:
        return dev.run(self.root, prepare_only=True, **kwargs)

    def game_inputs(self):
        self.write(".cache/parts/engine/Game.exe", b"engine")
        self.write(".cache/parts/engine/Language.dll", b"strings")
        editor_files = {
            "WorldAlteringEditor.exe": b"editor",
            "WorldAlteringEditor.dll": b"editor library",
            "WorldAlteringEditor.runtimeconfig.json": b'{"runtimeOptions":{"tfm":"net8.0"}}',
            "MapEditorLibrary.dll": b"map library",
            "Config/Default/Constants.ini": b"[Constants]\nExpectedClientExecutableName=OpenTS.exe\n",
            "Config/Default/FileManagerConfig.ini": b"[SearchDirectories]\n0=.\n1=MIX\n2=INI\n",
            "Config/DefaultSettings.ini": b"[General]\nSidebarWidth=265\n",
            "WAECache.mix": b"editor cache",
            "marble.mix": b"editor tiles",
        }
        for name, contents in editor_files.items():
            self.write(f".cache/parts/editor/{name}", contents)
        for name in ("LICENSE.txt", "COPYING", "SOURCE.txt"):
            self.write(f"editor/{name}", b"editor source and license information")
        self.write("ini/firestrm.ini", b"expansion")
        for label in (*verify.REQUIRED_ARCHIVES, *verify.THEATER_ARCHIVES, "MOVIES00", "SOUNDS01"):
            self.write(f"assets/{label}/TEST.BIN", b"data")
        for filename in verify.CACHED_BY_NAME:
            self.write(f"assets/CACHE/{filename}", b"font or palette")

    def updater_inputs(self):
        self.write("client/Resources/UpdaterConfig.ini", b"[DownloadMirrors]\n0=https://example.test/live/\n")
        self.write("client/Resources/UpdaterConfig_Default.ini", b"[DownloadMirrors]\n0=https://example.test/live/\n")
        self.write("client/Resources/UpdaterConfig_Dev.ini", b"[DownloadMirrors]\n0=https://example.test/dev/\n")

    def test_default_needs_no_engine_assets_movies_or_packer(self):
        original = (self.root / "client/Resources/ClientDefinitions.ini").read_bytes()
        with patch.object(pack, "run") as pack_run:
            runtime = self.prepare()
        self.fetch.assert_called_once_with(self.root, only=["client", "launcher"])
        pack_run.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual(runtime, self.root / "build/dev-client")
        self.assertFalse((runtime / "Game.exe").exists())
        self.assertFalse((runtime / "MapEditor").exists())
        self.assertFalse((runtime / "MIX").exists())
        self.assertFalse((runtime / "Resources/ExtrasWindow.ini").exists())
        self.assertFalse((runtime / "version").exists())
        self.assertEqual((runtime / "OpenTS.exe.config").read_bytes(), b"config")
        self.assertEqual((runtime / "INI/battle.ini").read_bytes(), b"engine campaigns")
        self.assertEqual((runtime / "INI/ClientBattle.ini").read_bytes(), (self.root / "client/INI/ClientBattle.ini").read_bytes())
        self.assertEqual((runtime / "Maps/Multiplayer/map.png").read_bytes(), b"preview")
        self.assertEqual((runtime / "Maps/Multiplayer/map.map").read_bytes(), b"map")
        self.assertEqual((runtime / "Maps/Missions/mission.map").read_bytes(), b"mission")
        copied = (runtime / "Resources/ClientDefinitions.ini").read_text()
        self.assertIn("ModMode=false", copied)
        self.assertIn("WriteInstallationPathToRegistry=false", copied)
        self.assertIn("; Keep this comment", copied)
        self.assertEqual((self.root / "client/Resources/ClientDefinitions.ini").read_bytes(), original)

    def test_client_only_hides_editor_controls_before_validation_without_changing_source(self):
        menus = {
            "MainMenu.ini": b"[MainMenu]\nText=menu\n[btnMapEditor]\nVisible=true\n",
            "ExtrasWindow.ini": b"[btnExMapEditor]\nVisible=true\nText=Map Editor\n",
        }
        for name, contents in menus.items():
            self.write(f"client/Resources/{name}", contents)
        self.write(".cache/parts/editor/WorldAlteringEditor.exe", b"cached editor")
        check_client = verify.client_tree

        def check_hidden(stage):
            for name, contents in menus.items():
                self.assertEqual((stage / "Resources" / name).read_bytes(),
                                 contents.replace(b"Visible=true", b"Visible=false"))
            return check_client(stage)

        with patch.object(verify, "client_tree", side_effect=check_hidden):
            runtime = self.prepare()
        self.fetch.assert_called_once_with(self.root, only=["client", "launcher"])
        self.assertFalse((runtime / "MapEditor").exists())
        for name, contents in menus.items():
            self.assertEqual((self.root / "client/Resources" / name).read_bytes(), contents)

    def test_modmode_follows_source_configuration(self):
        runtime = self.prepare()
        definitions = self.root / "client/Resources/ClientDefinitions.ini"
        definitions.write_bytes(definitions.read_bytes().replace(b"ModMode=false", b"ModMode=true"))
        self.prepare()
        self.assertIn("ModMode=true", (runtime / "Resources/ClientDefinitions.ini").read_text())

    def test_refresh_preserves_selected_updater_channel_and_updates_its_template(self):
        self.updater_inputs()
        runtime = self.prepare()
        active = runtime / "Resources/UpdaterConfig.ini"
        active.write_bytes((runtime / "Resources/UpdaterConfig_Dev.ini").read_bytes())
        updated = b"[DownloadMirrors]\n0=https://example.test/new-dev/\n"
        self.write("client/Resources/UpdaterConfig_Dev.ini", updated)
        self.prepare()
        self.assertEqual(active.read_bytes(), updated)
        manifest = json.loads((self.root / dev.MANIFEST).read_text())
        self.assertNotIn("Resources/UpdaterConfig.ini", manifest["files"])
        self.assertIn("Resources/UpdaterConfig_Dev.ini", manifest["files"])

    def test_refresh_preserves_custom_updater_configuration(self):
        self.updater_inputs()
        runtime = self.prepare()
        active = runtime / "Resources/UpdaterConfig.ini"
        custom = b"; Local mirror\n[DownloadMirrors]\n0=https://example.test/custom/\n"
        active.write_bytes(custom)
        self.write("client/Resources/UpdaterConfig.ini", b"[DownloadMirrors]\n0=https://example.test/new-live/\n")
        self.prepare()
        self.assertEqual(active.read_bytes(), custom)

    def test_removed_updater_channel_falls_back_to_source_default(self):
        self.updater_inputs()
        runtime = self.prepare()
        active = runtime / "Resources/UpdaterConfig.ini"
        active.write_bytes((runtime / "Resources/UpdaterConfig_Dev.ini").read_bytes())
        (self.root / "client/Resources/UpdaterConfig_Dev.ini").unlink()
        self.prepare()
        self.assertEqual(active.read_bytes(), (self.root / "client/Resources/UpdaterConfig.ini").read_bytes())
        self.assertFalse((runtime / "Resources/UpdaterConfig_Dev.ini").exists())

    def test_old_manifest_cannot_prune_active_updater_configuration(self):
        self.updater_inputs()
        runtime = self.prepare()
        active = runtime / "Resources/UpdaterConfig.ini"
        custom = b"[DownloadMirrors]\n0=https://example.test/custom/\n"
        active.write_bytes(custom)
        manifest_path = self.root / dev.MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest["files"].append("Resources/UpdaterConfig.ini")
        manifest_path.write_text(json.dumps(manifest))
        (self.root / "client/Resources/UpdaterConfig.ini").unlink()
        self.prepare()
        self.assertEqual(active.read_bytes(), custom)
        self.assertNotIn("Resources/UpdaterConfig.ini", json.loads(manifest_path.read_text())["files"])

    def test_refresh_applies_edits_and_removals_but_preserves_user_state(self):
        runtime = self.prepare()
        state = {
            "Client/client.log": b"log",
            "Maps/Custom/user.map": b"custom",
            "Saved Games/save.SAV": b"save",
            "keyboard.ini": b"personal hotkeys",
            "Resources/my-note.txt": b"personal note",
        }
        for name, content in state.items():
            self.write(f"build/dev-client/{name}", content)
        self.write("build/dev-client/SUN.ini", b"; User settings\r\n[Options]\r\nWriteInstallationPathToRegistry=true\r\nTranslation=ru\r\n[Audio]\r\nClientVolume=0.3\r\n")
        self.write("client/Resources/MainMenu.ini", b"[MainMenu]\nText=edited menu\n")
        (self.root / "client/Resources/Removed.ini").unlink()
        self.prepare()
        self.assertEqual((runtime / "Resources/MainMenu.ini").read_bytes(), b"[MainMenu]\nText=edited menu\n")
        self.assertFalse((runtime / "Resources/Removed.ini").exists())
        for name, content in state.items():
            self.assertEqual((runtime / name).read_bytes(), content)
        settings = (runtime / "SUN.ini").read_bytes()
        self.assertIn(b"WriteInstallationPathToRegistry=false\r\n", settings)
        self.assertIn(b"Translation=ru\r\n", settings)
        self.assertIn(b"ClientVolume=0.3\r\n", settings)
        manifest = json.loads((self.root / dev.MANIFEST).read_text())
        self.assertNotIn("SUN.ini", manifest["files"])
        self.assertNotIn("Client/client.log", manifest["files"])

    def test_refresh_removes_previous_root_campaign_files(self):
        runtime = self.prepare()
        for name in ("battle.ini", "battlefs.ini"):
            (runtime / name).write_bytes(b"previous root campaign file")
        self.write("ini/battlefs.ini", b"engine expansion campaigns")
        manifest_path = self.root / dev.MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest["files"].extend(["battle.ini", "battlefs.ini"])
        manifest_path.write_text(json.dumps(manifest))
        self.prepare()
        self.assertFalse((runtime / "battle.ini").exists())
        self.assertFalse((runtime / "battlefs.ini").exists())
        self.assertEqual((runtime / "INI/battle.ini").read_bytes(), b"engine campaigns")
        self.assertEqual((runtime / "INI/battlefs.ini").read_bytes(), b"engine expansion campaigns")
        self.assertTrue((runtime / "INI/ClientBattle.ini").is_file())

    def test_refresh_replaces_the_previous_launcher_name(self):
        runtime = self.prepare()
        renames = {"OpenTS.exe": "OpenTSClient.exe", "OpenTS.exe.config": "OpenTSClient.exe.config"}
        for current, previous in renames.items():
            (runtime / current).rename(runtime / previous)
        manifest_path = self.root / dev.MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = [renames.get(name, name) for name in manifest["files"]]
        manifest_path.write_text(json.dumps(manifest))
        self.prepare()
        self.assertEqual((runtime / "OpenTS.exe").read_bytes(), b"launcher")
        self.assertEqual((runtime / "OpenTS.exe.config").read_bytes(), b"config")
        for previous in renames.values():
            self.assertFalse((runtime / previous).exists())

    def test_with_game_excludes_cached_movies_and_stale_archives_then_switches_back(self):
        self.game_inputs()
        self.write("assets/MOVIES/large.vqa", b"campaign movie")
        movie = self.write("build/MIX/MOVIES.MIX", b"old movie archive")
        self.write("build/MIX/STALE.MIX", b"stale")
        with patch.object(pack, "run", wraps=pack.run) as packing:
            runtime = self.prepare(with_game=True)
        self.fetch.assert_called_with(self.root, only=["client", "launcher", "engine", "editor"])
        self.assertNotIn("MOVIES", packing.call_args.kwargs["only"])
        self.assertEqual(movie.read_bytes(), b"old movie archive")
        self.assertEqual((runtime / "Game.exe").read_bytes(), b"engine")
        self.assertTrue((runtime / "MIX/MOVIES00.MIX").is_file())
        self.assertFalse((runtime / "MIX/MOVIES.MIX").exists())
        self.assertFalse((runtime / "MIX/STALE.MIX").exists())
        self.write("build/dev-client/MIX/personal.txt", b"keep")
        self.prepare()
        self.assertFalse((runtime / "Game.exe").exists())
        self.assertFalse((runtime / "Language.dll").exists())
        self.assertFalse((runtime / "MIX/CACHE.MIX").exists())
        self.assertEqual((runtime / "MIX/personal.txt").read_bytes(), b"keep")

    def test_with_game_refresh_and_switching_preserve_editor_settings_maps_and_autosaves(self):
        self.game_inputs()
        menu = b"[MainMenu]\nText=menu\n[btnMapEditor]\nVisible=true\n"
        extras = b"[btnExMapEditor]\nVisible=true\nText=Map Editor\n"
        self.write("client/Resources/MainMenu.ini", menu)
        self.write("client/Resources/ExtrasWindow.ini", extras)
        runtime = self.prepare(with_game=True)
        editor = runtime / "MapEditor/WorldAlteringEditor.exe"
        self.assertEqual(editor.read_bytes(), b"editor")
        self.assertFalse((runtime / "MapEditor/MapEditorSettings.ini").exists())
        self.assertEqual((runtime / "Resources/MainMenu.ini").read_bytes(), menu)
        self.assertEqual((runtime / "Resources/ExtrasWindow.ini").read_bytes(), extras)
        state = {
            "MapEditor/MapEditorSettings.ini": b"[General]\nGameDirectory=..\nSidebarWidth=300\n",
            "MapEditor/AutoSaves/autosave.map": b"autosaved map",
            "MapEditor/Maps/personal.map": b"editor map",
            "Maps/Custom/editor-made.map": b"custom map",
        }
        for name, contents in state.items():
            self.write(f"build/dev-client/{name}", contents)
        self.write(".cache/parts/editor/WorldAlteringEditor.exe", b"updated editor")
        for with_game in (True, False, True):
            self.prepare(with_game=with_game)
            if with_game:
                self.assertEqual(editor.read_bytes(), b"updated editor")
            else:
                self.assertFalse(editor.exists())
            expected_visibility = b"Visible=true" if with_game else b"Visible=false"
            self.assertIn(expected_visibility, (runtime / "Resources/MainMenu.ini").read_bytes())
            self.assertIn(expected_visibility, (runtime / "Resources/ExtrasWindow.ini").read_bytes())
            manifest = json.loads((self.root / dev.MANIFEST).read_text())
            for name, contents in state.items():
                self.assertEqual((runtime / name).read_bytes(), contents)
                self.assertNotIn(name, manifest["files"])
        self.assertEqual((self.root / "client/Resources/MainMenu.ini").read_bytes(), menu)
        self.assertEqual((self.root / "client/Resources/ExtrasWindow.ini").read_bytes(), extras)

    def test_failed_game_validation_leaves_existing_workspace_intact(self):
        runtime = self.prepare()
        previous_manifest = (self.root / dev.MANIFEST).read_bytes()
        self.game_inputs()
        (self.root / "assets/SCORES/TEST.BIN").unlink()
        (self.root / "assets/SCORES").rmdir()
        self.write("client/Resources/MainMenu.ini", b"[MainMenu]\nText=not yet applied\n")
        with self.assertRaisesRegex(dev.DevError, "SCORES.MIX"):
            self.prepare(with_game=True)
        self.assertEqual((runtime / "Resources/MainMenu.ini").read_bytes(), b"[MainMenu]\nText=old menu\n")
        self.assertFalse((runtime / "Game.exe").exists())
        self.assertEqual((self.root / dev.MANIFEST).read_bytes(), previous_manifest)
        self.launch.assert_not_called()

    def test_failed_client_validation_leaves_existing_workspace_intact(self):
        runtime = self.prepare()
        previous_manifest = (self.root / dev.MANIFEST).read_bytes()
        self.write("client/Resources/MainMenu.ini", b"[MainMenu]\nBackgroundTexture=missing.png\n")
        with self.assertRaisesRegex(dev.DevError, "missing.png"):
            self.prepare()
        self.assertEqual((runtime / "Resources/MainMenu.ini").read_bytes(), b"[MainMenu]\nText=old menu\n")
        self.assertEqual((self.root / dev.MANIFEST).read_bytes(), previous_manifest)
        self.launch.assert_not_called()

    def test_fetch_failure_does_not_create_or_launch_workspace(self):
        self.fetch.side_effect = fetch.FetchError("download failed")
        with self.assertRaisesRegex(dev.DevError, "download failed"):
            self.prepare()
        self.assertFalse((self.root / dev.OUTPUT).exists())
        self.launch.assert_not_called()

    def test_case_only_rename_does_not_leave_a_stale_copy(self):
        runtime = self.prepare()
        (self.root / "client/Resources/MainMenu.ini").rename(self.root / "client/Resources/mainmenu.ini")
        self.prepare()
        menus = [p.name for p in (runtime / "Resources").iterdir() if p.name.casefold() == "mainmenu.ini"]
        self.assertEqual(menus, ["mainmenu.ini"])

    def test_lock_failure_stops_before_fetch_or_sync(self):
        runtime = self.prepare()
        self.fetch.reset_mock()
        with patch.object(dev, "_check_unlocked", side_effect=dev.DevError("close the development client")):
            with self.assertRaisesRegex(dev.DevError, "close the development client"):
                self.prepare()
        self.fetch.assert_not_called()
        self.assertEqual((runtime / "Resources/MainMenu.ini").read_bytes(), b"[MainMenu]\nText=old menu\n")

    def test_manifest_cannot_delete_source_files(self):
        self.prepare()
        self.write(str(dev.MANIFEST), json.dumps({"schema": 1, "files": ["../../client/Resources/MainMenu.ini"]}).encode())
        self.fetch.reset_mock()
        with self.assertRaisesRegex(dev.DevError, "unsafe"):
            self.prepare()
        self.fetch.assert_not_called()
        self.assertEqual((self.root / "client/Resources/MainMenu.ini").read_bytes(), b"[MainMenu]\nText=old menu\n")

    def test_existing_workspace_hard_link_cannot_modify_sources(self):
        runtime = self.prepare()
        target = runtime / "Resources/MainMenu.ini"
        target.unlink()
        try:
            os.link(self.root / "client/Resources/MainMenu.ini", target)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        with self.assertRaisesRegex(dev.DevError, "hard links"):
            self.prepare()
        self.assertEqual((self.root / "client/Resources/MainMenu.ini").read_bytes(), b"[MainMenu]\nText=old menu\n")

    def test_symlinked_workspace_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "build").mkdir()
        try:
            (self.root / dev.OUTPUT).symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        with self.assertRaisesRegex(dev.DevError, "links"):
            self.prepare()
        self.fetch.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_nested_directory_link_is_refused_before_refresh(self):
        runtime = self.prepare()
        self.fetch.reset_mock()
        try:
            (runtime / "Resources/linked-config").symlink_to(
                self.root / "client/Resources", target_is_directory=True,
            )
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        with self.assertRaisesRegex(dev.DevError, "links"):
            self.prepare()
        self.fetch.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual((self.root / "client/Resources/MainMenu.ini").read_bytes(), b"[MainMenu]\nText=old menu\n")

    @unittest.skipUnless(os.name == "nt", "client launch is Windows-only")
    def test_launch_happens_only_after_a_successful_sync(self):
        def check_runtime(runtime):
            self.assertTrue((runtime / "OpenTS.exe").is_file())
            self.assertIn("ModMode=false", (runtime / "Resources/ClientDefinitions.ini").read_text())
            self.assertTrue((self.root / dev.MANIFEST).is_file())
        self.launch.side_effect = check_runtime
        runtime = dev.run(self.root)
        self.launch.assert_called_once_with(runtime)
        self.launch.reset_mock()
        self.fetch.side_effect = fetch.FetchError("download failed")
        with self.assertRaises(dev.DevError):
            dev.run(self.root)
        self.launch.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows ACL regression")
    def test_staged_files_keep_the_ordinary_parent_access_rules(self):
        # Windows tempfile ACLs must not strip inherited runtime read access.
        subprocess.run(
            ["icacls.exe", str(self.root), "/grant", "*S-1-5-32-545:(OI)(CI)RX"],
            check=True, capture_output=True, timeout=15,
        )
        runtime = self.prepare()
        environment = os.environ.copy()
        environment["OPENTS_TEST_ACL_PATHS"] = json.dumps([
            str(runtime / assemble.LAUNCHER),
            str(runtime / "Resources/ClientDefinitions.ini"),
            str(self.root / dev.MANIFEST),
        ])
        script = """
$ErrorActionPreference = 'Stop'
foreach ($taskAclPath in (ConvertFrom-Json -InputObject $env:OPENTS_TEST_ACL_PATHS)) {
    $taskRules = [System.IO.File]::GetAccessControl($taskAclPath).GetAccessRules(
        $true, $true, [System.Security.Principal.SecurityIdentifier])
    $taskReader = @($taskRules | Where-Object {
        $_.AccessControlType -eq 'Allow' -and
        $_.IdentityReference.Value -eq 'S-1-5-32-545'
    })
    if ($taskReader.Count -eq 0) { throw "Inherited reader missing from $taskAclPath" }
}
"""
        checked = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=environment, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
