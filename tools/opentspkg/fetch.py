"""Fetch dependencies from pins.toml, verifying hashes before extraction.

Pins can reference URLs or local archives. Engine CI selectors and local
development directories are handled separately from hashed downloads.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse

from . import builds
from .media import BLOCK, digest_of
from .files import replace, staging_directory

PINS = "pins.toml"
CACHE = Path(".cache") / "fetch"

# Engine CI builds may override this pin.
ENGINE = "engine"

# Unhashed local directories are for development only; refused in CI.
LOCAL = "local"

# The client .7z requires an external extractor; SEVEN_ZIP overrides its path.
SEVEN_ZIP_PLACES = (
    Path("C:/Program Files/7-Zip/7z.exe"),
    Path("C:/Program Files (x86)/7-Zip/7z.exe"),
)


class FetchError(Exception):
    pass


@dataclass
class Pin:
    name: str
    unpacks_to: str
    sha256: str = ""
    size: int = 0
    url: str = ""
    path: str = ""
    kind: str = "archive"
    holds: list[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        return self.url or self.path

    @property
    def is_local(self) -> bool:
        return self.kind == LOCAL


@dataclass
class Fetched:
    pin: Pin
    archive: Path
    unpacked: Path
    files: int
    downloaded: bool
    taken: str = ""

    def __str__(self) -> str:
        if self.taken:
            how = self.taken
        elif self.pin.is_local:
            how = f"took {self.files} files from {self.archive}"
        else:
            had = "downloaded" if self.downloaded else "already had"
            how = f"{had} {self.archive.name}, {self.files} files"
        return f"{self.pin.name}: {how} -> {self.unpacked}"


def load(root: Path) -> dict[str, Pin]:
    """Read and validate dependency pins."""

    path = root / PINS
    if not path.is_file():
        raise FetchError(f"{path} is not there")

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise FetchError(f"{path} could not be read: {error}") from error

    pins: dict[str, Pin] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        pin = Pin(
            name=name,
            sha256=str(entry.get("sha256", "")),
            size=int(entry.get("size", 0)),
            unpacks_to=str(entry.get("unpacks-to", "")),
            url=str(entry.get("url", "")),
            path=str(entry.get("path", "")),
            kind=str(entry.get("kind", "archive")),
            holds=[str(item) for item in entry.get("holds", [])],
        )
        if not pin.unpacks_to:
            raise FetchError(f"the pin for {name} does not say where it unpacks to")

        if not pin.is_local and not pin.sha256:
            raise FetchError(
                f"the pin for {name} carries no sha256, so what it names could "
                "not be checked. Fill it in or take the pin out."
            )
        pins[name] = pin
    return pins


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    working = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as response, working.open("wb") as handle:
            while True:
                block = response.read(BLOCK)
                if not block:
                    break
                handle.write(block)
    except OSError as error:
        working.unlink(missing_ok=True)
        raise FetchError(f"{url} could not be fetched: {error}") from error
    working.replace(target)


def _obtain(root: Path, pin: Pin, cache: Path, fresh: bool = False) -> tuple[Path, bool]:
    """Obtain a hash-verified archive, reusing local or cached bytes."""

    if not pin.source:
        raise FetchError(
            f"the pin for {pin.name} names neither a url nor a path, so there "
            "is nothing to fetch. Point it at a copy, or build one."
        )

    # Read local archives in place to avoid duplicating large downloads.
    if not pin.url:
        source = Path(pin.path)
        if not source.is_absolute():
            source = root / source
        if not source.is_file():
            raise FetchError(f"the pin for {pin.name} names {source}, which is not there")
        return source, False

    target = cache / Path(urlparse(pin.url).path).name

    # Reuse only cache entries matching the pin hash.
    if not fresh and target.is_file() and digest_of(target) == pin.sha256:
        return target, False

    _download(pin.url, target)
    return target, True


def _check(pin: Pin, archive: Path) -> None:
    size = archive.stat().st_size
    if pin.size and size != pin.size:
        raise FetchError(
            f"{pin.name}: {archive.name} is {size:,} bytes where the pin says "
            f"{pin.size:,}, so it is not the file the pin names"
        )
    digest = digest_of(archive)
    if digest != pin.sha256:
        raise FetchError(
            f"{pin.name}: {archive.name} hashes to {digest}, not the {pin.sha256} "
            "the pin names. It is not the file this build was written against."
        )


def _seven_zip() -> str:
    override = os.environ.get("SEVEN_ZIP")
    if override:
        return override

    for name in ("7z", "7zz", "7za", "7zr"):
        found = shutil.which(name)
        if found:
            return found

    for place in SEVEN_ZIP_PLACES:
        if place.is_file():
            return str(place)

    raise FetchError(
        "this download is a .7z, which Python does not read, and no 7-Zip was "
        "found. Install it, or set SEVEN_ZIP to where 7z.exe is."
    )


def _unpack_zip(archive: Path, destination: Path) -> int:
    with zipfile.ZipFile(archive) as held:
        names = held.namelist()
        for name in names:
            # Reject the whole archive if any member escapes the extraction root.
            if not _safe_member(name):
                raise FetchError(f"{archive.name} holds {name!r}, which does not unpack safely")
        held.extractall(destination)
    return sum(1 for name in names if not name.endswith("/"))


def _safe_member(name: str) -> bool:
    # Check Windows spellings even when packaging on Linux.
    path = PureWindowsPath(name)
    return bool(name) and not path.drive and not path.root and ".." not in path.parts and ":" not in name


def _destination(root: Path, pin: Pin) -> Path:
    relative = Path(pin.unpacks_to)
    destination = root / relative
    if (not _safe_member(pin.unpacks_to) or destination.resolve() == root.resolve()
            or not destination.resolve().is_relative_to(root.resolve())
            or relative.parts[0].casefold() in {".git", ".github", ".agents", ".codex"}):
        raise FetchError(f"{pin.name}: unsafe extraction destination {pin.unpacks_to!r}")
    for path in (destination, *destination.parents):
        if path == root:
            break
        if path.is_symlink() or path.is_junction():
            raise FetchError(f"{pin.name}: extraction destination uses a link: {path}")
    return destination


def _file_stamps(directory: Path) -> dict[str, str]:
    stamps = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise FetchError(f"an extracted part contains a link: {path}")
        if path.is_file():
            stamps[path.relative_to(directory).as_posix()] = digest_of(path)
    return stamps


def _prepare(root: Path, pin: Pin, identity: str, populate) -> tuple[Path, int]:
    """Reuse checked extracted parts, or replace them only after staging succeeds."""
    destination = _destination(root, pin)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", pin.name):
        raise FetchError(f"invalid pin name: {pin.name!r}")
    marker = root / ".cache" / "parts-state" / f"{pin.name}.json"
    expected = {"source": identity, "destination": pin.unpacks_to, "layout": 1}
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if (all(saved.get(key) == value for key, value in expected.items())
                and destination.is_dir() and saved.get("files") == _file_stamps(destination)):
            return destination, len(saved["files"])
    except (OSError, ValueError, AttributeError):
        pass

    destination.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(root / ".cache" / "parts-staging", f".{pin.name}-") as working:
        staged = working / "new"
        staged.mkdir()
        populate(staged)
        stamps = _file_stamps(staged)
        if not stamps:
            raise FetchError(f"{pin.name}: the downloaded part contains no files")
        previous = working / "previous"
        if destination.exists():
            replace(destination, previous)
        try:
            replace(staged, destination)
        except OSError:
            if previous.exists():
                replace(previous, destination)
            raise
    marker.parent.mkdir(parents=True, exist_ok=True)
    pending = marker.with_suffix(".tmp")
    pending.write_text(json.dumps({**expected, "files": stamps}, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(marker)
    return destination, len(stamps)


def _unpack_7z(archive: Path, destination: Path) -> int:
    # 7-Zip enforces extraction path containment.
    finished = subprocess.run(
        [_seven_zip(), "x", str(archive), f"-o{destination}", "-y"],
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout).strip().splitlines()
        raise FetchError(
            f"{archive.name} could not be unpacked: {detail[-1] if detail else 'no reason given'}"
        )
    return sum(1 for path in destination.rglob("*") if path.is_file())


def _unpack(archive: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".7z":
        return _unpack_7z(archive, destination)
    return _unpack_zip(archive, destination)


def _use_directory(root: Path, pin: Pin) -> Fetched:
    """Copy selected files from a local development directory."""

    # Refuse local directory pins in CI when used; unrelated pins remain fetchable.
    if os.environ.get("CI"):
        raise FetchError(
            f"the pin for {pin.name} names a directory on one machine, which "
            "cannot be built against here. Pin a release or a build instead."
        )

    source = Path(pin.path)
    if not source.is_absolute():
        source = root / source
    if not source.is_dir():
        raise FetchError(f"the pin for {pin.name} names {source}, which is not a directory")

    if not pin.holds:
        raise FetchError(
            f"the pin for {pin.name} names a directory but not which of its "
            "files to take, so `holds` has to say which"
        )

    missing = [item for item in pin.holds if not (source / item).is_file()]
    if missing:
        raise FetchError(
            f"{source} does not hold {', '.join(missing)}, so there is no "
            f"{pin.name} there to build against yet"
        )

    if any(not _safe_member(item) for item in pin.holds):
        raise FetchError(f"{pin.name}: holds contains an unsafe file path")
    identity = "local:" + json.dumps({item: digest_of(source / item) for item in pin.holds}, sort_keys=True)

    def populate(destination):
        for item in pin.holds:
            target = destination / item
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / item, target)

    destination, files = _prepare(root, pin, identity, populate)
    return Fetched(pin, source, destination, files, False)


def _take_build(root: Path, pin: Pin, selector: str, cache: Path) -> Fetched:
    """Fetch an engine CI artifact instead of the release pin."""

    try:
        build = builds.resolve(selector)
        archive, route = builds.download(build, cache, token=builds.token())
    except builds.BuildsError as error:
        raise FetchError(str(error)) from error

    identity = f"artifact:{build.artifact}:{digest_of(archive)}"
    destination, files = _prepare(root, pin, identity, lambda staged: _unpack(archive, staged))
    return Fetched(pin, archive, destination, files, route != "cache", taken=f"took {build} via {route}")


def run(
    root: Path,
    only: list[str] | None = None,
    cache: Path | None = None,
    engine: str | None = None,
    fresh: bool = False,
) -> list[Fetched]:
    """Fetch all pins or the selected dependencies.

    An engine selector (builds.resolve) replaces the engine pin for this run;
    CI artifact downloads are identified by build, not a pinned hash.
    """

    pins = load(root)
    if only:
        missing = [name for name in only if name not in pins]
        if missing:
            raise FetchError(f"nothing is pinned as {', '.join(missing)}")
        pins = {name: pin for name, pin in pins.items() if name in only}

    cache = cache or root / CACHE
    results = []
    for name in sorted(pins):
        pin = pins[name]
        if name == ENGINE and engine:
            results.append(_take_build(root, pin, engine, cache))
            continue
        if pin.is_local:
            results.append(_use_directory(root, pin))
            continue
        archive, downloaded = _obtain(root, pin, cache, fresh=fresh)
        _check(pin, archive)
        destination, files = _prepare(root, pin, f"sha256:{pin.sha256}", lambda staged: _unpack(archive, staged))
        results.append(Fetched(pin, archive, destination, files, downloaded))
    return results
