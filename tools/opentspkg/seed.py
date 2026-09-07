"""Import an installation into the loose asset tree; not part of packaging.

Unpack nested archives by recognized name, never by signature: SHP data can
start with the same zero bytes as a MIX header.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import mix, names, roundtrip

# Match engine mount precedence: patch, expansion, then base.
# First copies win, preserving expansion tiles and other overrides.
SOURCE_ARCHIVES = (
    "PATCH.MIX",
    "EXPAND01.MIX",
    "TIBSUN.MIX", "CACHE.MIX", "LOCAL.MIX", "CONQUER.MIX",
    "GMENU.MIX", "MULTI.MIX", "MAPS01.MIX", "MAPS02.MIX",
    "MAPS03.MIX", "MOVIES01.MIX", "MOVIES02.MIX", "MOVIES03.MIX",
    "SCORES.MIX", "SCORES01.MIX", "SIDECD01.MIX", "SIDECD02.MIX",
    "E01SCD01.MIX", "E01SCD02.MIX",
)

# Record but omit retired World Domination Tour assets.
RETIRED_ARCHIVES = ("WDT.MIX", "WDTVOX.MIX")

# Optional extra archives, excluded by default.
EXTRA_ARCHIVES = ("EXPAND02.MIX", "EXPAND03.MIX")

# Campaign and multiplayer maps ship loose in separate folders.
MAP_ARCHIVES = ("MAPS01.MIX", "MAPS02.MIX", "MAPS03.MIX", "MULTI.MIX")

MAP_FOLDERS = {
    "MAPS01": "Missions",
    "MAPS02": "Missions",
    "MAPS03": "Missions",
    "MULTI": "Multiplayer",
}

MAP_EXTENSIONS = (".MAP", ".MPR", ".YRM", ".MMP", ".BIN")

# Keys are embedded in the engine and need no separate file.
UNSHIPPED = ("KEY.INI",)

# Flatten patch/container layers, retaining the first winning copy.
# PATCH contains only files identical to expansion overrides.
DISSOLVED_ARCHIVES = ("PATCH", "EXPAND01", "TIBSUN")

# Merge expansion archives into their base counterparts; expansion copies win.
MERGED_INTO = {
    "E01SC01": "SIDEC01",
    "E01SC02": "SIDEC02",
    "E01SNC01": "SIDENC01",
    "E01SNC02": "SIDENC02",
    "E01SCD01": "SIDECD01",
    "E01SCD02": "SIDECD02",
    "E01VOX01": "SPEECH01",
    "E01VOX02": "SPEECH02",
    # Expansion artwork joins the main artwork archive.
    "ECACHE01": "CONQUER",
}

# Merge per-disc movies; duplicate contents must match except renamed intros.
MERGED_MOVIES = ("MOVIES01", "MOVIES02", "MOVIES03")

MOVIE_ARCHIVE = "MOVIES"

# Keep startup movies separate: the engine requires a MOVIES archive
# even when campaign movies are omitted.
STARTUP_ARCHIVE = "MOVIES00"
STARTUP_MOVIES = ("EVA.VQA", "WWLOGO.VQA", "STARTUP.VQA")

# Distinct intro names preserve both campaigns when all discs are installed.
# Suffixes match campaign CDNumber values used by the engine.
RENAMED = {
    ("MOVIES01", "INTRO.VQA"): "intr0.vqa",
    ("MOVIES02", "INTRO.VQA"): "intr1.vqa",
}

# Loose configuration overrides archive members in the engine search order.
LOOSE_EXTENSIONS = (".INI", ".PKT")

# Keep menu configuration with its artwork; GMENU presence enables that menu.
KEPT_WITH_ITS_ARCHIVE = ("NEWMENU.INI", "WDTCHOICE.INI")

# Fallback destinations for dissolved members without an existing archive home.
BY_EXTENSION = {
    ".SHP": "CONQUER",
    ".VXL": "CONQUER",
    ".HVA": "CONQUER",
    ".AUD": "SCORES01",
    ".PCX": "GMENU",
    # Keep the menu movie with GMENU; it is not a campaign or startup movie.
    ".VQA": "GMENU",
}


@dataclass
class Record:
    """The source and destination of one imported member."""

    archive: str
    identifier: int
    size: int
    digest: str
    name: str | None = None
    destination: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        entry = {
            "archive": self.archive,
            "id": self.identifier,
            "size": self.size,
            "sha256": self.digest,
        }
        if self.name:
            entry["name"] = self.name
        if self.destination:
            entry["destination"] = self.destination
        if self.note:
            entry["note"] = self.note
        return entry


@dataclass
class Report:
    source: str
    records: list[Record] = field(default_factory=list)
    archives: dict[str, int] = field(default_factory=dict)

    @property
    def named(self) -> int:
        return sum(1 for record in self.records if record.name)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "archives": self.archives,
            "members": len(self.records),
            "named": self.named,
            "records": [record.as_dict() for record in self.records],
        }


class SeedError(Exception):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive_stem(name: str) -> str:
    return Path(name).stem.upper()


def _unnamed(identifier: int) -> str:
    return f"_id_{identifier & 0xFFFFFFFF:08x}.bin"


def _is_map(name: str) -> bool:
    return name.upper().endswith(MAP_EXTENSIONS)


def _movie_archive(name: str | None) -> str:
    """Select the startup or campaign movie archive."""

    if name and name.upper() in STARTUP_MOVIES:
        return STARTUP_ARCHIVE
    return MOVIE_ARCHIVE


def _filename(label: str, name: str | None, identifier: int) -> str:
    """Apply known renames or preserve an unresolved member ID."""

    renamed = RENAMED.get((label, name.upper())) if name else None
    return renamed or (name or _unnamed(identifier)).lower()


def collect(source: Path, include_extras: bool = False) -> dict[str, mix.Archive]:
    """Collect top-level and recognized nested archives.

    Use known archive names instead of ambiguous MIX/SHP byte signatures.
    """

    wanted = list(SOURCE_ARCHIVES)
    if include_extras:
        wanted += list(EXTRA_ARCHIVES)
    wanted += list(RETIRED_ARCHIVES)

    known = {mix.mix_id(name): name for name in names.archive_names()}

    available = {path.name.upper(): path for path in source.iterdir() if path.is_file()}

    archives: dict[str, mix.Archive] = {}
    pending: list[mix.Archive] = []

    for name in wanted:
        path = available.get(name)
        if path is None:
            continue
        stem = _archive_stem(name)
        if stem in archives:
            continue
        archive = mix.read_archive(path)
        archives[stem] = archive
        pending.append(archive)

    while pending:
        found: list[mix.Archive] = []
        for archive in pending:
            for entry in archive.entries:
                name = known.get(entry.id)
                if name is None:
                    continue
                stem = _archive_stem(name)
                if stem in archives:
                    continue
                nested = mix.read_archive_stream(io.BytesIO(archive.read(entry)))
                archives[stem] = nested
                found.append(nested)
        pending = found

    return archives


def run(
    source: Path,
    root: Path,
    dry_run: bool = False,
    include_extras: bool = False,
) -> Report:
    """Unpack an installation into the repository's loose asset tree."""

    if not source.is_dir():
        raise SeedError(f"{source} is not a directory")

    failures = [result for result in roundtrip.check_directory(source) if not result.ok]
    if failures:
        listed = ", ".join(result.path.name for result in failures)
        raise SeedError(
            "these archives could not be rebuilt exactly, so unpacking them "
            f"would lose something: {listed}"
        )

    archives = collect(source, include_extras=include_extras)
    if not archives:
        raise SeedError(f"{source} holds none of the game's archives")

    database = names.build(
        data
        for archive in archives.values()
        for data in (archive.read(entry) for entry in archive.entries)
    )

    report = Report(source=str(source))
    # Unpacked nested archives must not also survive as packed members.
    containers = set(archives)

    # Route dissolved overrides to the archive holding the base member.
    home: dict[int, str] = {}
    for label, archive in archives.items():
        if label in DISSOLVED_ARCHIVES or label in MERGED_INTO:
            continue
        if label in _stems(RETIRED_ARCHIVES):
            continue
        for entry in archive.entries:
            # Follow movie members into their merged destination.
            destination = (
                _movie_archive(database.resolve(entry.id))
                if label in MERGED_MOVIES
                else label
            )
            home.setdefault(entry.id, destination)

    # Deduplicate by destination and name, not name alone: side archives may
    # legitimately differ. Keep hashes to validate merged movie duplicates.
    written: dict[tuple[str, str], str] = {}

    # Apply dissolved overrides before their base copies.
    first = [label for label in DISSOLVED_ARCHIVES if label in archives]
    first += [label for label in sorted(MERGED_INTO) if label in archives]
    order = first + [label for label in sorted(archives) if label not in first]

    for label in order:
        archive = archives[label]
        report.archives[label] = len(archive.entries)

        for entry in archive.entries:
            data = archive.read(entry)
            name = database.resolve(entry.id)
            record = Record(
                archive=label,
                identifier=entry.id,
                size=entry.size,
                digest=_digest(data),
                name=name,
            )

            destination = _destination(
                label, name, entry.id, containers, home, written, record.digest
            )
            if destination is None:
                record.note = _skip_reason(label, name, entry.id, written)
            else:
                record.destination = str(destination).replace("\\", "/")
                if not dry_run:
                    target = root / destination
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)

            report.records.append(record)

    if not dry_run:
        (root / "seed-report.json").write_text(
            json.dumps(report.as_dict(), indent=1) + "\n", encoding="utf-8"
        )

    return report


def _stems(archives) -> set[str]:
    return {_archive_stem(item) for item in archives}


def _is_loose(name: str | None) -> bool:
    """Return whether a configuration file ships outside MIX archives."""

    if not name:
        return False
    if name.upper() in KEPT_WITH_ITS_ARCHIVE:
        return False
    return name.upper().endswith(LOOSE_EXTENSIONS)


def _target(
    label: str,
    name: str | None,
    identifier: int,
    home: dict[int, str],
    filename: str,
) -> tuple[str, Path]:
    """Choose the output group and repository-relative path."""

    # Winning game configuration ships loose.
    if _is_loose(name):
        return "ini", Path("ini") / filename

    if label in _stems(MAP_ARCHIVES) and (name is None or _is_map(name)):
        return "maps", Path("maps") / MAP_FOLDERS[label] / filename

    # Split startup movies from merged campaign movies.
    if label in MERGED_MOVIES:
        target = _movie_archive(name)
        return target, Path("assets") / target / filename

    # Merge expansion content into its base archive.
    if label in MERGED_INTO:
        target = MERGED_INTO[label]
        return target, Path("assets") / target / filename

    if label in DISSOLVED_ARCHIVES:
        # Reuse the base member destination to avoid duplicate copies.
        target = home.get(identifier)
        if target is None:
            suffix = Path(filename).suffix.upper()
            target = BY_EXTENSION.get(suffix)
        if target is None:
            raise SeedError(
                f"{name or filename} comes from {label}, which is dissolved, and "
                f"nothing says where a {suffix or 'file with no extension'} belongs"
            )
        return target, Path("assets") / target / filename

    return label, Path("assets") / label / filename


def _destination(
    label: str,
    name: str | None,
    identifier: int,
    containers: set[str],
    home: dict[int, str],
    written: dict[tuple[str, str], str],
    digest: str,
) -> Path | None:
    """Return a member destination, or None when omitted."""

    if label in _stems(RETIRED_ARCHIVES):
        return None

    if name and name.upper() in UNSHIPPED:
        return None

    # Do not retain packed copies of archives already unpacked separately.
    if name and mix.is_archive_name(name) and _archive_stem(name) in containers:
        return None

    filename = _filename(label, name, identifier)
    target, destination = _target(label, name, identifier, home, filename)
    key = (target, filename)

    # The earlier copy wins by engine mount precedence.
    if key in written:
        # Merged movies may only share names when their bytes match.
        if label in MERGED_MOVIES and written[key] != digest:
            raise SeedError(
                f"{filename} is in more than one movie archive and the copies "
                "differ, so merging them would lose one. Give each a name of "
                "its own in RENAMED before seeding."
            )
        return None

    written[key] = digest
    return destination


def _skip_reason(
    label: str, name: str | None, identifier: int, written: dict[tuple[str, str], str]
) -> str:
    if label in _stems(RETIRED_ARCHIVES):
        return "belongs to the retired World Domination Tour"
    if name and name.upper() in UNSHIPPED:
        return "the game carries its own copy"
    filename = _filename(label, name, identifier)
    if any(filename == held for _, held in written):
        if label in MERGED_MOVIES:
            return "the same file is in another movie archive"
        return "superseded by the patch or the expansion"
    return "unpacked in its own right"
