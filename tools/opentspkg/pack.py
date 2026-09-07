"""Pack each assets/ directory into a deterministic MIX archive."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import mix

# Increment to invalidate cached archives after packer changes.
PACKER_VERSION = 1

STAMP_FILE = Path(".build-cache") / "stamps.json"

# Keep packed archives outside dist/, which assembly replaces.
OUTPUT = Path("build") / "MIX"


@dataclass
class Built:
    name: str
    path: Path
    members: int
    size: int
    rebuilt: bool

    def __str__(self) -> str:
        state = "built  " if self.rebuilt else "current"
        return f"{state} {self.name:14} {self.members:5} members  {self.size:>12,} bytes"


class PackError(Exception):
    pass


def _stamp(directory: Path) -> dict:
    """Hash file contents for rebuild detection; checkout timestamps are unreliable."""

    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(directory)).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return {"packer": PACKER_VERSION, "files": files}


def _load_stamps(root: Path) -> dict:
    path = root / STAMP_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_stamps(root: Path, stamps: dict) -> None:
    path = root / STAMP_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamps, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def asset_directories(root: Path) -> list[Path]:
    assets = root / "assets"
    if not assets.is_dir():
        return []
    return sorted((path for path in assets.iterdir() if path.is_dir() and not path.name.startswith(".")), key=lambda p: p.name)


def run(
    root: Path,
    output: Path,
    only: Iterable[str] | None = None,
    force: bool = False,
) -> list[Built]:
    """Pack every asset directory into an archive."""

    directories = asset_directories(root)
    if not directories:
        raise PackError(
            "there is nothing under assets/. Run `build.py seed` against an "
            "installation of the game first."
        )

    wanted = {name.upper() for name in only} if only else None
    if wanted:
        directories = [path for path in directories if path.name.upper() in wanted]
        if not directories:
            raise PackError(f"no asset directory matches {', '.join(sorted(wanted))}")

    stamps = _load_stamps(root)
    output.mkdir(parents=True, exist_ok=True)

    results: list[Built] = []
    for directory in directories:
        name = directory.name.upper()
        target = output / f"{name}.MIX"
        stamp = _stamp(directory)

        if not force and target.is_file() and stamps.get(name) == stamp:
            results.append(Built(name, target, len(stamp["files"]), target.stat().st_size, False))
            continue

        try:
            archive = mix.pack_directory(directory)
        except mix.MixFormatError as error:
            raise PackError(f"{directory}: {error}") from error

        data = mix.serialize_archive(archive)
        target.write_bytes(data)
        stamps[name] = stamp
        results.append(Built(name, target, len(archive.entries), len(data), True))

    _save_stamps(root, stamps)
    return results


def contents(path: Path) -> dict[int, bytes]:
    """Read archive members by ID."""

    archive = mix.read_archive(path)
    return {entry.id: archive.read(entry) for entry in archive.entries}
