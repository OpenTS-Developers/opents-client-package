"""Build a static CnCNet updater mirror from an assembled distribution.

Each version contains file hashes, optional LZMA payloads and removal scripts;
optional components are shared between versions.
"""

from __future__ import annotations

import hashlib
import lzma
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

VERSION_FILE = "version"
UPDATE_SCRIPT = "updateexec"
PRE_UPDATE_SCRIPT = "preupdateexec"

# Optional component ID matches UpdaterConfig.ini; bytes are shared by versions.
COMPONENT = "MOVIES"
COMPONENT_FILE = Path("MIX") / "MOVIES.MIX"
COMPONENTS = "Components"

# Updater metadata is fetched directly, not inventoried as game files.
OWN_FILES = {VERSION_FILE, UPDATE_SCRIPT, PRE_UPDATE_SCRIPT}

# Exclude settings and channel selection so updates preserve player choices.
PLAYER_WRITTEN = {"sun.ini", "resources/updaterconfig.ini", "mapeditor/mapeditorsettings.ini"}

ALREADY_COMPRESSED = {".vqa", ".aud", ".png", ".jpg", ".ogg", ".wma", ".mp3", ".zip", ".7z", ".lzma"}

# These MIX archives contain already compressed members.
STORED_PLAIN = ("MOVIES", "SCORES")

# Small files do not justify compression overhead.
ARCHIVE_FROM = 64 * 1024

class MirrorError(Exception):
    pass


def tracked(relative: Path) -> bool:
    """Return whether a file belongs in the updater inventory."""

    if relative == COMPONENT_FILE:
        return False
    if relative.parent == Path(".") and relative.name in OWN_FILES:
        return False
    return relative.as_posix().lower() not in PLAYER_WRITTEN


def stamp(data: bytes) -> str:
    """Encode MD5 as concatenated decimal bytes, followed by size in KiB."""

    digest = "".join(str(byte) for byte in hashlib.md5(data).digest())
    return f"{digest},{len(data) // 1024}"


def lzma_alone(data: bytes) -> bytes:
    """Compress using the updater-compatible LZMA-alone format."""

    # Keep the unknown size: an exact size makes the updater reject the end marker.
    return lzma.compress(data, format=lzma.FORMAT_ALONE, preset=6)


def updater_path(relative: Path) -> str:
    return relative.as_posix().replace("/", "\\")


def archived(relative: Path, size: int) -> bool:
    if size < ARCHIVE_FROM:
        return False
    if relative.suffix.lower() in ALREADY_COMPRESSED:
        return False
    return not relative.name.upper().startswith(STORED_PLAIN)


def section(text: str, name: str) -> dict[str, str]:
    """Read an INI section, treating keys without = as empty values."""

    found: dict[str, str] = {}
    inside = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("["):
            inside = line[1 : line.find("]")].lower() == name.lower()
            continue
        if inside:
            key, _, value = line.partition("=")
            found[key.strip()] = value.strip()
    return found


def version_text(
    tag: str,
    files: dict[str, str],
    archives: dict[str, str] | None,
    addons: dict[str, str],
) -> str:
    lines = ["[DTA]", f"Version={tag}", "", "[FileVersions]"]
    lines += [f"{name}={value}" for name, value in sorted(files.items())]
    if archives:
        lines += ["", "[ArchivedFiles]"]
        lines += [f"{name}={value}" for name, value in sorted(archives.items())]
    lines += ["", "[AddOns]"]
    lines += [f"{name}={value}" for name, value in sorted(addons.items())]
    return "\n".join(lines) + "\n"


def stamp_tree(root: Path) -> dict[str, str]:
    """Hash tracked files, excluding updater metadata and optional components."""

    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if not tracked(relative):
            continue
        files[updater_path(relative)] = stamp(path.read_bytes())
    return files


def _script_sections(text: str | None) -> tuple[list[str], list[str]]:
    """Preserve prior rename/delete lines and comments, trimming edge blanks."""

    renames: list[str] = []
    deletes: list[str] = []
    current: list[str] | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.lower() == "[rename]":
            current = renames
            continue
        if line.lower() == "[delete]":
            current = deletes
            continue
        if current is not None:
            current.append(raw.rstrip())

    def trimmed(lines: list[str]) -> list[str]:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    return trimmed(renames), trimmed(deletes)


def update_script(tag: str, previous: str | None, removed: list[str]) -> str:
    """Append removals while retaining history for clients skipping versions."""

    renames, deletes = _script_sections(previous)
    if removed:
        if deletes and deletes[-1].strip():
            deletes.append("")
        deletes.append(f"; {tag}: no longer shipped")
        deletes.extend(sorted(removed))
        deletes.append(f"; end {tag}")

    lines = ["[Rename]", *renames, "", "[Delete]", *deletes]
    return "\n".join(lines).rstrip("\n") + "\n"


@dataclass
class Mirror:
    tag: str
    root: Path
    files: int
    archived: int
    size: int
    component: str
    removed: int

    def __str__(self) -> str:
        return (
            f"{self.tag}: {self.files} files, {self.archived} compressed, "
            f"{self.size:,} bytes in {self.root}; {self.removed} removed since last"
        )


def build(
    base: Path,
    tag: str,
    out: Path,
    component: Path | None = None,
    previous_version: str | None = None,
    previous_script: str | None = None,
) -> Mirror:
    """Build one version and its shared movie component.

    Use the explicit component archive or the full build copy. Previous channel
    version/script inputs preserve removal history.
    """

    if not base.is_dir():
        raise MirrorError(f"{base} is not a build to mirror")

    source = component or base / COMPONENT_FILE
    if not source.is_file():
        raise MirrorError(
            f"{source} is not there. A mirror offers the campaign movies as a "
            "download, so it cannot be built without them."
        )

    root = out / tag
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    files: dict[str, str] = {}
    archives: dict[str, str] = {}
    size = 0

    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if not tracked(relative):
            continue

        data = path.read_bytes()
        name = updater_path(relative)
        files[name] = stamp(data)

        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if archived(relative, len(data)):
            blob = lzma_alone(data)
            target.with_name(target.name + ".lzma").write_bytes(blob)
            archives[name] = stamp(blob)
            size += len(blob)
        else:
            target.write_bytes(data)
            size += len(data)

    components = out / COMPONENTS
    components.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, components / COMPONENT_FILE.name)
    component_stamp = stamp(source.read_bytes())

    (root / VERSION_FILE).write_text(
        version_text(tag, files, archives, {COMPONENT: component_stamp}), encoding="utf-8"
    )

    removed = sorted(set(section(previous_version or "", "FileVersions")) - set(files))
    (root / UPDATE_SCRIPT).write_text(update_script(tag, previous_script, removed), encoding="utf-8")
    (root / PRE_UPDATE_SCRIPT).write_text("[Rename]\n\n[Delete]\n", encoding="utf-8")

    return Mirror(tag, root, len(files), len(archives), size, component_stamp, len(removed))


def check(out: Path, tag: str) -> tuple[int, list[str]]:
    """Verify mirror files against the updater inventory."""

    root = out / tag
    problems: list[str] = []
    checked = 0

    version = root / VERSION_FILE
    if not version.is_file():
        return 0, [f"{version} is not there"]
    text = version.read_text(encoding="utf-8")

    if section(text, "DTA").get("Version") != tag:
        says = section(text, "DTA").get("Version")
        problems.append(f"{VERSION_FILE} says it is {says!r}, not {tag!r}")
    checked += 1

    files = section(text, "FileVersions")
    archives = section(text, "ArchivedFiles")
    for name, expected in files.items():
        relative = Path(name.replace("\\", "/"))
        if name in archives:
            packed = root / relative.with_name(relative.name + ".lzma")
            if not packed.is_file():
                problems.append(f"{name} is listed as compressed and {packed.name} is not there")
                continue
            blob = packed.read_bytes()
            if stamp(blob) != archives[name]:
                problems.append(f"{name}: the compressed file does not match its entry")
            try:
                data = lzma.decompress(blob, format=lzma.FORMAT_ALONE)
            except lzma.LZMAError as error:
                problems.append(f"{name}: the compressed file will not decode ({error})")
                continue
        else:
            plain = root / relative
            if not plain.is_file():
                problems.append(f"{name} is listed and is not there")
                continue
            data = plain.read_bytes()
        if stamp(data) != expected:
            problems.append(f"{name} does not match its entry")
        checked += 1

    for name in archives:
        if name not in files:
            problems.append(f"{name} is compressed but not listed as a file")

    addons = section(text, "AddOns")
    component = out / COMPONENTS / COMPONENT_FILE.name
    if COMPONENT not in addons:
        problems.append(f"{VERSION_FILE} offers no {COMPONENT} component")
    elif not component.is_file():
        problems.append(f"{component} is offered and is not there")
    elif stamp(component.read_bytes()) != addons[COMPONENT]:
        problems.append(f"{component.name} does not match its entry")
    checked += 1

    for name in (UPDATE_SCRIPT, PRE_UPDATE_SCRIPT):
        if not (root / name).is_file():
            problems.append(f"{name} is not there, and the updater fetches it")
        checked += 1

    return checked, problems


def readback(root: Path, fetch: Callable[[str], bytes], sample: int = 3) -> tuple[int, list[str]]:
    """Verify the served version file, sampled plain files and one LZMA file.

    fetch accepts a relative path with forward slashes and returns bytes.
    """

    problems: list[str] = []
    checked = 0

    def served(name: str) -> bytes | None:
        try:
            return fetch(name.replace("\\", "/"))
        except OSError as error:
            problems.append(f"{name} could not be fetched: {error}")
            return None

    local = (root / VERSION_FILE).read_bytes()
    text = served(VERSION_FILE)
    checked += 1
    if text is None:
        return checked, problems
    if text != local:
        problems.append("the version file served is not the one laid out")
        return checked, problems

    listed = local.decode("utf-8")
    files = section(listed, "FileVersions")
    archives = section(listed, "ArchivedFiles")
    for name in sorted(item for item in files if item not in archives)[:sample]:
        data = served(name)
        checked += 1
        if data is not None and stamp(data) != files[name]:
            problems.append(f"{name} served does not match the version file")
    for name in sorted(archives)[:1]:
        data = served(name + ".lzma")
        checked += 1
        if data is not None and stamp(data) != archives[name]:
            problems.append(f"{name}.lzma served does not match the version file")

    return checked, problems
