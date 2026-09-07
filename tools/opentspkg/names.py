"""Recover MIX member names from a curated list and configuration patterns.

Prefer the community data/mix-names.txt list: guessed names can collide by
hash. Unresolved members retain their original IDs and bytes.
"""

from __future__ import annotations

import re
import string
from pathlib import Path
from dataclasses import dataclass, field
from typing import Iterable

from .mix import mix_id

# Names required by the engine.
ENGINE_FILES = (
    # Fonts and palettes read directly from cached archive memory.
    "12METFNT.FNT", "KIA6PT.FNT", "6POINT.FNT", "EDITFNT.FNT", "8POINT.FNT",
    "GRAD6FNT.FNT", "3POINT.FNT", "TYPE.FNT", "VCR.FNT", "SCOREFNT.FNT",
    "UNITSNO.PAL", "TEMPERAT.PAL", "WAYPOINT.PAL", "ANIM.PAL", "PALETTE.PAL",
    "SNOW.PAL", "ISOTEM.PAL", "ISOSNO.PAL", "MOUSEPAL.PAL", "SIDEBAR.PAL",
    "VOXELS.VPL", "DPOD.VXL", "DPOD.HVA",
    # Configuration the game reads.
    "RULES.INI", "ART.INI", "AI.INI", "SOUND.INI", "THEME.INI", "EVA.INI",
    "TEMPERAT.INI", "SNOW.INI", "MISSION.INI", "TUTORIAL.INI", "BATTLE.INI",
    "FIRESTRM.INI", "RULESMD.INI", "SUN.INI", "KEYBOARD.INI", "MPMAPS.INI",
    "RULES01.INI", "ART01.INI", "AI01.INI", "SOUND01.INI", "THEME01.INI",
    "EVA01.INI", "TEMPERAT01.INI", "SNOW01.INI", "MISSION01.INI",
    "BATTLE01.INI", "TUTORIAL01.INI", "MPMAPS01.INI", "KEY.INI",
    # Campaign intros are addressed by campaign disc number.
    "INTR0.VQA", "INTR1.VQA",
    # Archive names identify nested containers.
    "CACHE.MIX", "LOCAL.MIX", "CONQUER.MIX", "SOUNDS.MIX", "SOUNDS01.MIX",
    "SPEECH01.MIX", "SPEECH02.MIX", "TEMPERAT.MIX", "SNOW.MIX", "TEM.MIX",
    "SNO.MIX", "ISOTEMP.MIX", "ISOSNOW.MIX", "SIDEC01.MIX", "SIDEC02.MIX",
    "SIDENC01.MIX", "SIDENC02.MIX", "SIDECD01.MIX", "SIDECD02.MIX",
    "E01VOX01.MIX", "E01VOX02.MIX", "E01SCD01.MIX", "E01SCD02.MIX",
    "MARBLE.MIX", "TIBSUN.MIX", "MULTI.MIX", "GMENU.MIX", "PATCH.MIX",
    "PCACHE.MIX", "SCORES.MIX", "SCORES01.MIX", "WDT.MIX", "WDTVOX.MIX",
    "MAPS01.MIX", "MAPS02.MIX", "MAPS03.MIX", "MOVIES01.MIX", "MOVIES02.MIX",
    "MOVIES03.MIX", "EXPAND01.MIX", "EXPAND02.MIX", "EXPAND03.MIX",
    "local mix database.dat",
)

# Extensions tried for harvested words.
EXTENSIONS = (
    ".SHP", ".VXL", ".HVA", ".AUD", ".PAL", ".INI", ".PCX", ".WSA", ".VQA",
    ".VQP", ".TMP", ".FNT", ".DES", ".UMD", ".LMD", ".MMP", ".MAP", ".BIN",
    ".CSF", ".TXT", ".WAV", ".VPL", ".DAT", ".MRF", ".JUV", ".SUN", ".THM",
    ".TEM", ".SNO", ".URB", ".UBN", ".LUN",
)

# Tileset extensions by theater.
THEATER_EXTENSIONS = (".TEM", ".SNO", ".URB", ".UBN", ".LUN", ".DES")

_WORD = re.compile(r"[A-Za-z0-9_\-]{2,24}")
_SECTION = re.compile(r"(?m)^\s*\[([A-Za-z0-9_\-]{2,16})\]")
_FILENAME_KEY = re.compile(r"(?im)^\s*FileName\s*=\s*([A-Za-z0-9_\-]+)")
_TILESET_MARKER = b"[TileSet0000]"

# Cap candidate length to avoid millions of implausible filename guesses.
_LONGEST_STEM = 12

# Extensions tried for numbered series.
_SERIES_EXTENSIONS = (".SHP", ".AUD", ".VQA", ".PCX", ".VXL")


@dataclass
class NameDatabase:
    """Known archive IDs, names and candidate words."""

    names: dict[int, str] = field(default_factory=dict)

    def add(self, name: str) -> None:
        try:
            identifier = mix_id(name)
        except UnicodeEncodeError:
            return
        # Never replace a known name with a generated hash collision.
        self.names.setdefault(identifier, name)

    def add_many(self, names: Iterable[str]) -> None:
        for name in names:
            self.add(name)

    def resolve(self, identifier: int) -> str | None:
        return self.names.get(identifier)

    def __len__(self) -> int:
        return len(self.names)


NAME_LIST = Path(__file__).resolve().parent.parent / "data" / "mix-names.txt"


def known_names(path: Path | None = None) -> list[str]:
    """Read the community filename list."""

    path = path or NAME_LIST
    if not path.is_file():
        return []

    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def looks_like_configuration(data: bytes) -> bool:
    """Look for an INI section and assignment near the start, allowing comments."""

    head = data[:8192]
    if b"=" not in head:
        return False
    return _SECTION.search(head.decode("cp1252", "ignore")) is not None


def harvest_words(text: str) -> set[str]:
    """Extract possible filenames from configuration text."""

    return set(_WORD.findall(text))


def tileset_bases(data: bytes) -> set[str]:
    """Read tileset base names from theater configuration."""

    if _TILESET_MARKER not in data[:65536]:
        return set()
    return set(_FILENAME_KEY.findall(data.decode("cp1252", "ignore")))


def speech_names() -> Iterable[str]:
    """Generate names matching the Speech table in code/vox.cpp.

    Use its two-digit prefix, letter and three-digit suffix pattern.
    """

    for prefix in range(100):
        for letter in ("I", "N"):
            for number in range(1000):
                yield f"{prefix:02d}-{letter}{number:03d}.AUD"


def archive_names() -> Iterable[str]:
    """Generate archive names, including numbered expansion and side archives.

    These identify nested archives during seeding.
    """

    yield from (name for name in ENGINE_FILES if name.upper().endswith(".MIX"))

    for number in range(100):
        for stem in ("EXPAND", "ECACHE", "ELOCAL", "PCACHE"):
            yield f"{stem}{number:02d}.MIX"

    for addon in range(100):
        for side in range(1, 9):
            for stem in ("SC", "SNC", "SCD", "VOX"):
                yield f"E{addon:02d}{stem}{side:02d}.MIX"

    for side in range(1, 9):
        for stem in ("SIDEC", "SIDENC", "SIDECD", "SPEECH", "SOUNDS", "SCORES", "MAPS", "MOVIES"):
            yield f"{stem}{side:02d}.MIX"


def section_names(text: str) -> set[str]:
    """Extract INI section names as candidates for numbered file series."""

    return set(_SECTION.findall(text))


def _numbered(base: str, extension: str):
    """Generate numbered filename forms."""

    yield base + extension
    for number in range(100):
        yield f"{base}{number:02d}{extension}"
    for number in range(10):
        yield f"{base}{number}{extension}"


def tileset_names(base: str) -> Iterable[str]:
    """Generate tileset filenames from a base, number and optional variant letter."""

    for extension in THEATER_EXTENSIONS:
        yield base + extension
        for number in range(100):
            stem = f"{base}{number:02d}"
            yield stem + extension
            for letter in string.ascii_lowercase:
                yield f"{stem}{letter}{extension}"
        for number in range(10):
            yield f"{base}{number}{extension}"


def build(members: Iterable[bytes]) -> NameDatabase:
    """Build name candidates from curated names and detected configuration text."""

    database = NameDatabase()

    # Curated names take precedence over colliding guesses.
    database.add_many(known_names())
    database.add_many(ENGINE_FILES)
    database.add_many(speech_names())

    words: set[str] = set()
    sections: set[str] = set()
    bases: set[str] = set()

    for data in members:
        if not looks_like_configuration(data):
            continue
        text = data.decode("cp1252", "ignore")
        bases |= tileset_bases(data)
        sections |= section_names(text)
        words |= harvest_words(text)

    for base in bases:
        database.add_many(tileset_names(base))

    for word in words:
        if "." in word:
            database.add(word)
        elif len(word) <= _LONGEST_STEM:
            for extension in EXTENSIONS:
                database.add(word + extension)

    # Section names also suggest numbered file series.
    for word in sections:
        if len(word) > _LONGEST_STEM:
            continue
        for extension in _SERIES_EXTENSIONS:
            database.add_many(_numbered(word, extension))

    return database
