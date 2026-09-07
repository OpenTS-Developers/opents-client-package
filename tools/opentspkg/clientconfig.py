"""Check pinned TS client file references without launching the game.

This is not a complete INI parser: duplicate sections are valid, and
translated strings need not name controls.
"""

from __future__ import annotations

import configparser
import posixpath
import re
from pathlib import Path
from urllib.parse import urlsplit


def _control_order(ini: configparser.ConfigParser, path: str) -> list[str]:
    """Check explicit $CC siblings in creation order.

    INItializableWindow parses each child immediately; references to later
    siblings fail. Built-in and inherited controls may exist outside this list.
    """
    declarations = {
        parent: [value.split(":", 1)[0] for key, value in ini[parent].items() if key.startswith("$CC")]
        for parent in ini.sections()
    }
    declared = {child for children in declarations.values() for child in children}
    errors = []
    for parent, children in declarations.items():
        positions = {child: index for index, child in enumerate(children)}
        for index, child in enumerate(children):
            if not ini.has_section(child):
                continue  # The section can come from an inherited INI.
            for key, value in ini[child].items():
                if key not in ("$X", "$Y", "$Width", "$Height", "$AnchorPoint"):
                    continue
                for target in re.findall(r"get(?:X|Y|Width|Height|Bottom|Right)\(\s*([A-Za-z_][A-Za-z_0-9]*)\s*\)", value):
                    context = f"{path} [{parent}] [{child}] {key}"
                    if target in positions and positions[target] > index:
                        errors.append(f"{context}: references {target} before its $CC declaration; declare it before {child}")
                    elif (posixpath.basename(path) == "GameLobbyBase.ini" and parent == "GameOptionsPanel"
                          and target not in declared and re.match(r"(?:chk|cmb|lbl)[A-Z]", target)):
                        # Only GameOptionsPanel is fully INI-created before runtime controls.
                        # Do not reject missing controls in arbitrary or inherited panels.
                        errors.append(f"{context}: missing $CC declaration for {target}")
    return errors


def check(runtime: Path) -> tuple[int, list[str]]:
    """Return checks performed and actionable problems in an assembled client."""
    runtime = Path(runtime)
    files = {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_file()}
    folded = {p.casefold(): p for p in sorted(files)}
    problems: list[str] = []
    count = 0
    parsed: dict[str, configparser.ConfigParser] = {}

    def require(names: list[str], context: str) -> str | None:
        nonlocal count
        count += 1
        names = [posixpath.normpath(n.replace("\\", "/")) for n in names]
        for name in names:
            if name in files:
                return name
        case = next((folded[n.casefold()] for n in names if n.casefold() in folded), None)
        detail = f"wrong case; found {case}" if case else "missing"
        problems.append(f"{context}: {detail}: {' or '.join(names)}")
        return None

    def read(name: str) -> configparser.ConfigParser:
        if name not in parsed:
            ini = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
            ini.optionxform = str
            if require([name], "client configuration"):
                try:
                    # The client recognizes comments even without preceding space.
                    text = (runtime / name).read_text(encoding="utf-8-sig")
                    ini.read_string("\n".join(line.split(";", 1)[0] for line in text.splitlines()))
                except (OSError, UnicodeError, configparser.Error) as error:
                    problems.append(f"{name}: cannot read INI: {error}")
            parsed[name] = ini
        return parsed[name]

    def section(ini: configparser.ConfigParser, name: str, context: str) -> dict[str, str]:
        nonlocal count
        count += 1
        if not ini.has_section(name):
            problems.append(f"{context}: missing [{name}] section")
            return {}
        return dict(ini[name])

    definitions = read("Resources/ClientDefinitions.ini")
    settings = section(definitions, "Settings", "ClientDefinitions.ini")
    require([settings.get("LauncherExe", "OpenTS.exe")], "client launcher")
    if "Resources/MainMenu.ini" in files:
        main_menu = read("Resources/MainMenu.ini")
        if main_menu.has_section("btnMapEditor"):
            visible = main_menu.get("btnMapEditor", "Visible", fallback="true").lower()
            if visible not in ("false", "no", "0"):
                require([settings.get("MapEditorExePath", "FinalSun/FinalSun.exe")], "map editor")
    credits = settings.get("CreditsURL", "")
    if credits and not urlsplit(credits).scheme:
        require([credits], "local credits page")
    themes = []
    for value in section(definitions, "Themes", "ClientDefinitions.ini").values():
        if "," not in value:
            problems.append(f"ClientDefinitions.ini: invalid theme entry: {value}")
            continue
        themes.append("Resources/" + value.split(",", 1)[1].strip().rstrip("/\\"))

    def asset(value: str, context: str) -> None:
        for theme in themes or ["Resources"]:
            require([f"{theme}/{value}", f"Resources/{value}", value], context)

    catalog = read("Resources/GameCollectionConfig.ini")
    game_ids: set[str] = set()
    for name in section(catalog, "CustomGames", "GameCollectionConfig.ini").values():
        game = section(catalog, name, "GameCollectionConfig.ini")
        identity = game.get("InternalName", "").lower()
        if not identity or len(identity) > 4 or identity in game_ids:
            problems.append(f"GameCollectionConfig.ini [{name}]: invalid or duplicate InternalName")
        game_ids.add(identity)
        asset(game.get("IconFilename", identity + "icon.png"), f"game [{name}] icon")
    if settings.get("LocalGame", "").lower() not in game_ids:
        problems.append("ClientDefinitions.ini: LocalGame does not identify a configured custom game")

    map_path = settings.get("MPMapsPath", "INI/MPMaps.ini")
    maps = read(map_path)
    for base in section(maps, "MultiMaps", map_path).values():
        entry = section(maps, base, map_path)
        inherited = entry.get("BaseSection")
        if inherited:
            entry = section(maps, inherited, map_path) | entry
        base_path = base.replace("\\", "/")
        require([base_path + "." + settings.get("MapFileExtension", "map")], f"map [{base}]")
        # Map.InitializeFromMpMapsINI uses PreviewImage, a sibling basename.
        preview = entry.get("PreviewImage", posixpath.basename(base_path)) + ".png"
        require([posixpath.join(posixpath.dirname(base_path), preview)], f"map [{base}] preview")

    battle_paths = ["INI/" + settings.get("BattleFSFileName", "BattleFS.ini")]
    if settings.get("IgnoreBattleIni", "false").lower() not in ("true", "yes", "1"):
        battle_paths.insert(0, "INI/Battle.ini")
    for index, battle_path in enumerate(battle_paths):
        battle_path = posixpath.normpath(battle_path.replace("\\", "/"))
        last = index == len(battle_paths) - 1
        if battle_path not in files and battle_path.casefold() not in folded:
            if last:
                require([battle_path], "client campaign list")
            continue
        battles = read(battle_path)
        names = list(battles["Battles"].values()) if battles.has_section("Battles") else []
        # The client falls back only when no valid mission sections were loaded.
        if not any(battles.has_section(name) for name in names):
            if last:
                problems.append(f"{battle_path}: no campaign sections referenced by [Battles]")
            continue
        for name in names:
            battle = section(battles, name, battle_path)
            if battle.get("Scenario"):
                require([battle["Scenario"]], f"campaign [{name}]")
        break

    # Ignore binary and translation directories: those do not supply window INIs.
    ini_paths = {p for p in files if p.startswith("Resources/") and p.count("/") == 1 and p.endswith(".ini")}
    for theme in themes:
        require([f"{theme}/DTACnCNetClient.ini"], "theme configuration")
        ini_paths.update(p for p in files if posixpath.dirname(p) == theme and p.endswith(".ini"))
    for path in sorted(ini_paths):
        ini = read(path)
        problems.extend(_control_order(ini, path))
        for name in ini.sections():
            for key, value in ini[name].items():
                if not value:
                    continue
                context = f"{path} [{name}] {key}"
                if key.endswith("Texture") and not key.startswith("SolidColor"):
                    asset(value, context)
                elif key == "CustomIniPath":
                    require([value], context)
                elif name == "INISystem" and key == "BasedOn":
                    for base in value.split(","):
                        base = base.strip()
                        if "$THEME_DIR$" in base:
                            for theme in themes:
                                require([base.replace("$THEME_DIR$", theme + "/")], context)
                        else:
                            require([posixpath.join(posixpath.dirname(path), base)], context)

    keys: dict[int, str] = {}
    hotkeys = read("Resources/KeyboardCommands.ini")
    for name in hotkeys.sections():
        try:
            key = int(hotkeys.get(name, "DefaultKey", fallback="0"))
        except ValueError:
            problems.append(f"KeyboardCommands.ini [{name}]: invalid DefaultKey")
            continue
        if key and key in keys:
            problems.append(f"KeyboardCommands.ini: {name} and {keys[key]} share DefaultKey={key}")
        keys[key] = name

    renderers = read("Resources/Renderers.ini")
    for name in section(renderers, "Renderers", "Renderers.ini").values():
        renderer = section(renderers, name, "Renderers.ini")
        if not renderer.get("DLLName"):
            for key in ("UseQres", "SingleCoreAffinity"):
                count += 1
                if renderer.get(key, "").lower() not in ("false", "no", "0"):
                    problems.append(f"Renderers.ini [{name}]: native OpenTS requires {key}=false")
    return count, problems
