from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opentspkg import clientconfig  # noqa: E402


def runtime_fixture(root: Path) -> Path:
    files = {
        "OpenTS.exe": "launcher",
        "Resources/ClientDefinitions.ini": "[Themes]\n0=Default,Default Theme/\n[Settings]\nLocalGame=ots\nLauncherExe=OpenTS.exe\nMPMapsPath=INI/MPMaps.ini\n",
        "Resources/GameCollectionConfig.ini": "[CustomGames]\n0=OTS\n[OTS]\nInternalName=ots\nIconFilename=opentsicon.png\n",
        "Resources/opentsicon.png": "icon",
        "Resources/Default Theme/DTACnCNetClient.ini": "[General]\n",
        "Resources/Default Theme/button.png": "texture",
        "Resources/GenericWindow.ini": "[Window]\nIdleTexture=button.png\n[Window]\nHoverTexture=button.png\nBackgroundTexture=\nSolidColorBackgroundTexture=0,0,0,0\n",
        "Resources/SkirmishLobby.ini": "[INISystem]\nBasedOn=GenericWindow.ini\n[Checkbox]\nCustomIniPath=INI/Option.ini\n",
        "Resources/KeyboardCommands.ini": "[First]\nDefaultKey=0\n[Second]\nDefaultKey=0\n[Third]\nDefaultKey=32\n",
        "Resources/Renderers.ini": "[Renderers]\n0=Default\n[Default]\nUseQres=false\nSingleCoreAffinity=false\n",
        "INI/MPMaps.ini": "[MultiMaps]\n0=Maps/Multiplayer/test\n[Maps/Multiplayer/test]\nDescription=Test\n",
        "Maps/Multiplayer/test.map": "map",
        "Maps/Multiplayer/test.png": "preview",
        "INI/Battle.ini": "[Battles]\n0=Heading\n1=Mission\n[Heading]\nDescription=Campaigns\n[Mission]\nScenario=Maps/Missions/test.map\n",
        "Maps/Missions/test.map": "mission",
        "INI/Option.ini": "[General]\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class ClientConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = runtime_fixture(Path(self.temp.name))

    def errors(self):
        return clientconfig.check(self.root)[1]

    def test_client_only_tree_and_repeated_sections_are_valid(self):
        count, errors = clientconfig.check(self.root)
        self.assertGreater(count, 20)
        self.assertEqual(errors, [])

    def test_visible_map_editor_requires_its_configured_executable(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "MapEditorExePath=MapEditor/WorldAlteringEditor.exe\n")
        main_menu = self.root / "Resources/MainMenu.ini"
        for visibility in ("", "Visible=true\n"):
            with self.subTest(visibility=visibility):
                main_menu.write_text("[btnMapEditor]\n" + visibility)
                self.assertEqual(self.errors(), ["map editor: missing: MapEditor/WorldAlteringEditor.exe"])
        executable = self.root / "MapEditor/WorldAlteringEditor.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"editor")
        self.assertEqual(self.errors(), [])

    def test_hidden_map_editor_allows_client_only_preview(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "MapEditorExePath=MapEditor/WorldAlteringEditor.exe\n")
        main_menu = self.root / "Resources/MainMenu.ini"
        for visibility in ("false", "no", "0"):
            with self.subTest(visibility=visibility):
                main_menu.write_text(f"[btnMapEditor]\nVisible={visibility}\n")
                self.assertEqual(self.errors(), [])

    def test_unspecified_map_editor_controls_do_not_require_an_editor(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "MapEditorExePath=MapEditor/WorldAlteringEditor.exe\n")
        self.assertEqual(self.errors(), [])
        (self.root / "Resources/MainMenu.ini").write_text("[MainMenu]\n")
        self.assertEqual(self.errors(), [])

    def test_custom_campaign_list_ignores_engine_battles(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "IgnoreBattleIni=true\nBattleFSFileName=ClientBattle.ini\n")
        (self.root / "INI/Battle.ini").rename(self.root / "INI/ClientBattle.ini")
        (self.root / "INI/Battle.ini").write_text("[Battles]\n0=Engine\n[Engine]\nScenario=engine-only.map\n")
        (self.root / "INI/BattleFS.ini").write_text("[Battles]\n0=EngineFS\n[EngineFS]\nScenario=engine-fs-only.map\n")
        self.assertEqual(self.errors(), [])
        (self.root / "INI/ClientBattle.ini").unlink()
        self.assertTrue(any("missing: INI/ClientBattle.ini" in error for error in self.errors()))

    def test_primary_campaigns_prevent_loading_the_fallback(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "IgnoreBattleIni=false\nBattleFSFileName=Missing.ini\n")
        self.assertEqual(self.errors(), [])

    def test_empty_or_missing_primary_campaigns_use_configured_fallback(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "BattleFSFileName=ClientBattle.ini\n")
        primary = self.root / "INI/Battle.ini"
        primary.rename(self.root / "INI/ClientBattle.ini")
        for content in (None, "[Battles]\n", "[Battles]\n0=MissingSection\n", "[Other]\n"):
            with self.subTest(content=content):
                if content is None:
                    primary.unlink(missing_ok=True)
                else:
                    primary.write_text(content)
                self.assertEqual(self.errors(), [])

    def test_campaign_filename_is_relative_to_ini(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "IgnoreBattleIni=true\nBattleFSFileName=../Resources/ClientBattle.ini\n")
        (self.root / "INI/Battle.ini").rename(self.root / "Resources/ClientBattle.ini")
        self.assertEqual(self.errors(), [])

    def test_empty_selected_campaign_list_is_reported(self):
        definitions = self.root / "Resources/ClientDefinitions.ini"
        definitions.write_text(definitions.read_text() + "IgnoreBattleIni=true\nBattleFSFileName=ClientBattle.ini\n")
        (self.root / "INI/ClientBattle.ini").write_text("[Battles]\n0=MissingSection\n")
        self.assertTrue(any("no campaign sections" in error for error in self.errors()))

    def test_reports_missing_preview_mission_and_option_separately(self):
        for name in ("Maps/Multiplayer/test.png", "Maps/Missions/test.map", "INI/Option.ini"):
            (self.root / name).unlink()
        errors = self.errors()
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("preview" in e for e in errors))
        self.assertTrue(any("campaign [Mission]" in e for e in errors))
        self.assertTrue(any("CustomIniPath" in e for e in errors))

    def test_texture_case_mismatch_is_detected_even_on_windows(self):
        path = self.root / "Resources/GenericWindow.ini"
        path.write_text("[Window]\nIdleTexture=Button.png\n", encoding="utf-8")
        self.assertTrue(any("wrong case" in e and "button.png" in e for e in self.errors()))

    def test_preview_image_is_a_sibling_basename_and_can_be_inherited(self):
        path = self.root / "INI/MPMaps.ini"
        path.write_text("[MultiMaps]\n0=Maps/Multiplayer/test\n[Maps/Multiplayer/test]\nBaseSection=Shared\n[Shared]\nPreviewImage=alternate\n", encoding="utf-8")
        (self.root / "Maps/Multiplayer/test.png").rename(self.root / "Maps/Multiplayer/alternate.png")
        self.assertEqual(self.errors(), [])

    def test_missing_inheritance_and_map_section_are_actionable(self):
        (self.root / "Resources/GenericWindow.ini").unlink()
        (self.root / "INI/MPMaps.ini").write_text("[MultiMaps]\n0=Maps/Multiplayer/test\n", encoding="utf-8")
        errors = self.errors()
        self.assertTrue(any("BasedOn" in e for e in errors))
        self.assertTrue(any("missing [Maps/Multiplayer/test]" in e for e in errors))

    def test_native_renderer_flags_and_key_collisions_are_rejected(self):
        (self.root / "Resources/Renderers.ini").write_text("[Renderers]\n0=Default\n[Default]\nUIName=OpenTS\n", encoding="utf-8")
        (self.root / "Resources/KeyboardCommands.ini").write_text("[A]\nDefaultKey=32\n[B]\nDefaultKey=32\n", encoding="utf-8")
        errors = self.errors()
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("DefaultKey=32" in e for e in errors))

    def test_local_identity_must_exist_in_custom_catalog(self):
        path = self.root / "Resources/ClientDefinitions.ini"
        path.write_text(path.read_text().replace("LocalGame=ots", "LocalGame=TS"), encoding="utf-8")
        self.assertTrue(any("LocalGame" in e for e in self.errors()))

    def test_local_credits_must_ship_but_web_links_are_not_fetched(self):
        path = self.root / "Resources/ClientDefinitions.ini"
        original = path.read_text()
        path.write_text(original + "CreditsURL=credits.htm\n", encoding="utf-8")
        self.assertTrue(any("local credits page" in e for e in self.errors()))
        (self.root / "credits.htm").write_text("Credits", encoding="utf-8")
        self.assertEqual(self.errors(), [])
        path.write_text(original + "CreditsURL=https://example.invalid/credits\n", encoding="utf-8")
        self.assertEqual(self.errors(), [])

    def test_unused_translation_layouts_do_not_override_active_references(self):
        path = self.root / "Resources/Translations/ru/Default Theme/SkirmishLobby.ini"
        path.parent.mkdir(parents=True)
        path.write_text("[SkirmishLobby]\nBackgroundTexture=missing.png\n", encoding="utf-8")
        self.assertEqual(self.errors(), [])

    def test_layout_sibling_must_be_declared_before_it_is_referenced(self):
        path = self.root / "Resources/GameLobbyBase.ini"
        declarations = "[GameOptionsPanel]\n$CC_17=chkIngameAllying:GameLobbyCheckBox\n$CC_24=chkAttackNeutralUnits:GameLobbyCheckBox\n"
        attributes = "[chkIngameAllying]\n$Y=getY(chkAttackNeutralUnits) + 25\n[chkAttackNeutralUnits]\n$Y=100\n"
        path.write_text(declarations + attributes, encoding="utf-8")
        self.assertTrue(any("chkAttackNeutralUnits before its $CC declaration" in e for e in self.errors()))
        # File order matters, not the numeric suffix on the registration keys.
        path.write_text("[GameOptionsPanel]\n$CC_24=chkAttackNeutralUnits:GameLobbyCheckBox\n$CC_17=chkIngameAllying:GameLobbyCheckBox\n" + attributes, encoding="utf-8")
        self.assertEqual(self.errors(), [])

    def test_missing_lobby_option_reference_is_reported(self):
        path = self.root / "Resources/GameLobbyBase.ini"
        path.write_text("[GameOptionsPanel]\n$CC_0=chkFirst:GameLobbyCheckBox\n[chkFirst]\n$Y=getY(chkRemoved) + 25\n", encoding="utf-8")
        self.assertTrue(any("missing $CC declaration for chkRemoved" in e for e in self.errors()))

    def test_generic_layout_may_reference_builtins_inherited_controls_and_self(self):
        path = self.root / "Resources/GenericWindow.ini"
        path.write_text("[Window]\n$CC_0=Panel:XNAPanel\n$CC_1=Inherited:XNAPanel\n[Panel]\n$X=getX(Panel)\n$Y=getY(btnRuntime)\n$Width=getWidth($Self)\n$Height=getHeight($ParentControl)\n", encoding="utf-8")
        self.assertEqual(self.errors(), [])


if __name__ == "__main__":
    unittest.main()
