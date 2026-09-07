"""Assemble fetched dependencies, packed archives and tracked client files."""

from __future__ import annotations

import configparser
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import mirror, pack, zipping
from .files import set_ini

# Only the smaller download includes NoMovies in its name.
DISTRIBUTION = "OpenTS-Client"
LABELS = {"full": "", "nomovies": "-NoMovies"}
VARIANTS = tuple(LABELS)

# Only full distributions include campaign movies.
CAMPAIGN_MOVIES = "MOVIES.MIX"

# The launcher selects a supported client backend.
LAUNCHER = "OpenTS.exe"
LAUNCHER_SOURCE = "CnCNet.LauncherStub.exe"

EDITOR_DIRECTORY = "MapEditor"
EDITOR_EXECUTABLE = "WorldAlteringEditor.exe"
EDITOR_FILES = (
    EDITOR_EXECUTABLE, "WorldAlteringEditor.dll", "WorldAlteringEditor.runtimeconfig.json",
    "MapEditorLibrary.dll", "Config/Default/Constants.ini", "Config/Default/FileManagerConfig.ini",
    "Config/DefaultSettings.ini", "WAECache.mix", "marble.mix",
)

class AssembleError(Exception):
    pass


def bundle_name(variant: str, tag: str = "") -> str:
    """Return the ZIP name for a variant and optional version."""

    if variant not in VARIANTS:
        raise AssembleError(f"there is no {variant} build; it is one of {', '.join(VARIANTS)}")
    return f"{DISTRIBUTION}{LABELS[variant]}{'-' + tag if tag else ''}.zip"


@dataclass
class Placed:
    what: str
    where: str
    files: int


@dataclass
class Built:
    root: Path
    variant: str
    placed: list[Placed] = field(default_factory=list)

    @property
    def files(self) -> int:
        return sum(item.files for item in self.placed)

    @property
    def size(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())


def _copy_tree(source: Path, destination: Path) -> int:
    if not source.is_dir():
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def _require(path: Path, what: str) -> None:
    if not path.exists():
        raise AssembleError(
            f"{what} is not there ({path}). Run `build.py fetch` and "
            "`build.py pack` before assembling."
        )


def client_layout(root: Path, output: Path) -> list[Placed]:
    """Copy the client, launcher and loose files shared by dev and release builds.

    No engine or packed game archives are required.
    """
    parts = root / ".cache" / "parts"
    _require(parts / "client" / "Resources", "the client")
    _require(parts / "launcher" / LAUNCHER_SOURCE, "the launcher")
    output.mkdir(parents=True, exist_ok=True)
    placed_files: list[Placed] = []

    # .NET requires the launcher config filename to follow the executable rename.
    shutil.copy2(parts / "launcher" / LAUNCHER_SOURCE, output / LAUNCHER)
    config = parts / "launcher" / f"{LAUNCHER_SOURCE}.config"
    if config.is_file():
        shutil.copy2(config, output / f"{LAUNCHER}.config")
    placed_files.append(Placed("launcher", LAUNCHER, 2 if config.is_file() else 1))

    # Overlay tracked resources on the downloaded client binaries.
    resources = output / "Resources"
    placed_files.append(
        Placed("client", "Resources", _copy_tree(parts / "client" / "Resources", resources))
    )
    ours = _copy_tree(root / "client" / "Resources", resources)
    placed_files.append(Placed("our client configuration", "Resources", ours))

    # Game INIs override client INIs.
    ini = output / "INI"
    placed_files.append(
        Placed("client configuration", "INI", _copy_tree(root / "client" / "INI", ini))
    )
    placed_files.append(Placed("game configuration", "INI", _copy_tree(root / "ini", ini)))

    # Overlay client previews on the game maps.
    maps = output / "Maps"
    placed_files.append(Placed("maps", "Maps", _copy_tree(root / "maps", maps)))
    placed_files.append(Placed("map previews", "Maps", _copy_tree(root / "client" / "Maps", maps)))
    placed_files.append(Placed("root files", ".", _copy_tree(root / "client" / "root", output)))
    return placed_files


def editor_layout(root: Path, output: Path) -> Placed:
    source = root / ".cache" / "parts" / "editor"
    for name in EDITOR_FILES:
        _require(source / name, "the map editor")
    constants = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=(";",))
    constants.read(source / "Config/Default/Constants.ini", encoding="utf-8-sig")
    accepted = constants.get("Constants", "ExpectedClientExecutableName", fallback="").split(",")
    if LAUNCHER.casefold() not in {name.strip().casefold() for name in accepted}:
        raise AssembleError(f"the pinned map editor does not recognize {LAUNCHER}")
    for name in ("LICENSE.txt", "COPYING", "SOURCE.txt"):
        _require(root / "editor" / name, "map editor license and source information")
    target = output / EDITOR_DIRECTORY
    count = _copy_tree(source, target) + _copy_tree(root / "editor", target)
    # WAE changes its working directory to its executable's folder.
    set_ini(target / "Config/DefaultSettings.ini", "General", "GameDirectory", "..")
    return Placed("map editor", EDITOR_DIRECTORY, count)


def run(
    root: Path,
    output: Path,
    variant: str = "nomovies",
    mix: Path | None = None,
    tag: str | None = None,
    archives: list[Path] | None = None,
) -> Built:
    """Assemble a runnable distribution."""

    if variant not in VARIANTS:
        raise AssembleError(f"there is no {variant} build; it is one of {', '.join(VARIANTS)}")

    parts = root / ".cache" / "parts"
    mix = mix or root / pack.OUTPUT
    _require(parts / "engine", "the game")
    _require(parts / "client" / "Resources", "the client")
    _require(parts / "launcher" / LAUNCHER_SOURCE, "the launcher")
    if archives is None:
        _require(mix, "the built archives")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    built = Built(root=output, variant=variant)
    built.placed.append(Placed("game", ".", _copy_tree(parts / "engine", output)))

    archive_directory = output / "MIX"
    archive_directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(archives if archives is not None else mix.glob("*.MIX")):
        if variant == "nomovies" and path.name.upper() == CAMPAIGN_MOVIES:
            continue
        shutil.copy2(path, archive_directory / path.name)
        count += 1
    built.placed.append(Placed("archives", "MIX", count))

    built.placed.extend(client_layout(root, output))
    built.placed.append(editor_layout(root, output))

    # Version files track campaign movies as an optional component, not a base file.
    if tag:
        addons = {}
        component = (next((path for path in archives if path.name.upper() == CAMPAIGN_MOVIES), None)
                     if archives is not None else mix / mirror.COMPONENT_FILE.name)
        if component is not None and component.is_file():
            addons[mirror.COMPONENT] = mirror.stamp(component.read_bytes())
        text = mirror.version_text(tag, mirror.stamp_tree(output), None, addons)
        (output / mirror.VERSION_FILE).write_text(text, encoding="utf-8")
        built.placed.append(Placed("version file", mirror.VERSION_FILE, 1))

    return built


@dataclass
class Bundled:
    path: Path
    files: int
    size: int

    def __str__(self) -> str:
        return f"{self.path.name}  {self.files:,} files  {self.size:,} bytes"


def bundle(built: Path, target: Path) -> Bundled:
    """Create a deterministic ZIP with an OpenTS-Client/ top-level folder."""

    if not built.is_dir():
        raise AssembleError(f"{built} is not there, so there is nothing to bundle")

    files = [
        (path, f"{DISTRIBUTION}/{path.relative_to(built).as_posix()}")
        for path in built.rglob("*")
        if path.is_file()
    ]
    count = zipping.write(files, target, zipfile.ZIP_DEFLATED)
    return Bundled(target, count, target.stat().st_size)
