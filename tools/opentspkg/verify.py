"""Check engine requirements and rebuilt content against the source installation.

Seeding reorganizes archives, so compare member IDs and bytes instead of
whole MIX files; reject missing, altered or extra members.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import mirror, mix, pack, seed

# Required engine archives; independent of the packer layout.
REQUIRED_ARCHIVES = (
    "CACHE", "CONQUER", "SOUNDS", "SCORES",
    "SIDEC01", "SIDEC02", "SPEECH01", "SPEECH02",
)

# At least one MOVIES archive is required; MOVIES00 satisfies nomovies builds.
MOVIE_ARCHIVE_PREFIX = "MOVIES"

# Supported map theaters.
THEATER_ARCHIVES = ("TEMPERAT", "SNOW", "TEM", "SNO", "ISOTEMP", "ISOSNOW")

# These must be in CACHE.MIX: the engine reads its cached memory directly.
CACHED_BY_NAME = (
    "12METFNT.FNT", "KIA6PT.FNT", "6POINT.FNT", "EDITFNT.FNT", "8POINT.FNT",
    "GRAD6FNT.FNT", "UNITSNO.PAL", "TEMPERAT.PAL", "WAYPOINT.PAL", "ANIM.PAL",
    "PALETTE.PAL",
)


@dataclass
class Findings:
    checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def fault(self, message: str) -> None:
        self.problems.append(message)


class VerifyError(Exception):
    pass


def _report(root: Path) -> dict:
    path = root / "seed-report.json"
    if not path.is_file():
        raise VerifyError(
            f"{path} is not there. Run `build.py seed` against an installation first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def against_source(root: Path, source: Path, output: Path) -> Findings:
    """Compare packed members with the original installation."""

    findings = Findings()
    report = _report(root)
    originals = seed.collect(source)

    # Derive expected bytes from the installation to catch seeding errors too.
    # Index once for direct member lookups.
    held: dict[str, dict[int, mix.Entry]] = {
        label: {entry.id: entry for entry in archive.entries}
        for label, archive in originals.items()
    }

    wanted: dict[str, dict[int, bytes]] = {}
    loose: dict[str, tuple[str, int]] = {}

    for record in report["records"]:
        destination = record.get("destination")
        if not destination:
            continue
        parts = destination.split("/")
        if parts[0] != "assets":
            loose[destination] = (record["archive"], record["id"])
            continue

        label, source_label, identifier = parts[1], record["archive"], record["id"]
        entry = held.get(source_label, {}).get(identifier)
        if entry is None:
            findings.fault(f"{label}: {source_label} no longer holds member {identifier}")
            continue
        # Known renames, including campaign intros, change the packed member ID.
        wanted.setdefault(label, {})[mix.packed_id(parts[-1])] = originals[source_label].read(entry)

    for label in sorted(wanted):
        built = output / f"{label}.MIX"
        if not built.is_file():
            findings.fault(f"{label}.MIX was not built")
            continue

        packed = pack.contents(built)
        expected = wanted[label]

        missing = set(expected) - set(packed)
        extra = set(packed) - set(expected)
        if missing:
            findings.fault(f"{label}.MIX is missing {len(missing)} members")
        if extra:
            findings.fault(f"{label}.MIX holds {len(extra)} members that were not put there")

        for identifier, data in expected.items():
            if identifier in packed and packed[identifier] != data:
                findings.fault(f"{label}.MIX: member {identifier} does not match the original")
        findings.checked += len(expected)

    for path, (label, identifier) in loose.items():
        target = root / path
        if not target.is_file():
            findings.fault(f"{path} was not written")
            continue
        archive = originals.get(label)
        entry = None if archive is None else next(
            (item for item in archive.entries if item.id == identifier), None
        )
        if entry is None:
            findings.fault(f"{path}: nothing in the installation holds it any more")
            continue
        if target.read_bytes() != archive.read(entry):
            findings.fault(f"{path} does not match the original")
        findings.checked += 1

    return findings


def engine_contract(output: Path, loose: Path) -> Findings:
    """Check required engine archives, members and configuration."""

    findings = Findings()

    for label in REQUIRED_ARCHIVES:
        if not (output / f"{label}.MIX").is_file():
            findings.fault(f"{label}.MIX is required and is not there")
        findings.checked += 1

    if not any(
        path.is_file()
        and path.name.upper().startswith(MOVIE_ARCHIVE_PREFIX)
        and path.name.upper().endswith(".MIX")
        for path in output.iterdir()
    ):
        findings.fault("the engine wants a movie archive and there is none")
    findings.checked += 1

    for label in THEATER_ARCHIVES:
        if not (output / f"{label}.MIX").is_file():
            findings.fault(f"{label}.MIX is missing, so a map in that theater cannot be played")
        findings.checked += 1

    cache = output / "CACHE.MIX"
    if cache.is_file():
        held = pack.contents(cache)
        for name in CACHED_BY_NAME:
            if mix.mix_id(name) not in held:
                findings.fault(f"{name} is not in CACHE.MIX, and startup fetches it from there")
            findings.checked += 1

    # This file enables Firestorm, which the spawner selects by default.
    if not (loose / "firestrm.ini").is_file():
        findings.fault("firestrm.ini is not there, so the expansion counts as not installed")
    findings.checked += 1

    if (output / "SOUNDS01.MIX").is_file() is False:
        findings.fault("SOUNDS01.MIX is required wherever the expansion is installed")
    findings.checked += 1

    return findings


def mirror_tree(out: Path, tag: str) -> Findings:
    """Verify updater mirror contents."""

    findings = Findings()
    checked, problems = mirror.check(out, tag)
    findings.checked = checked
    for problem in problems:
        findings.fault(problem)
    return findings


def client_tree(root: Path) -> Findings:
    """Check client configuration and local references without the game."""
    from . import clientconfig

    checked, problems = clientconfig.check(root)
    return Findings(checked, problems)
