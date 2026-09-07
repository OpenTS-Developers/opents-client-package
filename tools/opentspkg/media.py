"""Build a deterministic, content-addressed ZIP of campaign movies."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import zipping
from .zipping import BLOCK

# Movies are already compressed.
COMPRESSION = zipfile.ZIP_STORED

SOURCE = Path("assets") / "MOVIES"
OUTPUT = Path("dist-media")


class MediaError(Exception):
    pass


@dataclass
class Built:
    path: Path
    files: int
    size: int
    digest: str

    def __str__(self) -> str:
        return (
            f"{self.path.name}\n"
            f"  {self.files} movies, {self.size:,} bytes\n"
            f"  sha256 {self.digest}"
        )


def digest_of(path: Path) -> str:
    """Compute a file SHA256 in blocks."""

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(BLOCK), b""):
            hasher.update(block)
    return hasher.hexdigest()


def sources(root: Path) -> list[Path]:
    """List campaign movie source files."""

    directory = root / SOURCE
    if not directory.is_dir():
        raise MediaError(
            f"{directory} is not there. Seed it from an installation of the "
            "game, or fetch it, before building the download."
        )

    files = sorted(
        (path for path in directory.iterdir() if path.is_file()),
        key=lambda path: path.name,
    )
    if not files:
        raise MediaError(f"{directory} holds no movies")
    return files


def build(root: Path, output: Path | None = None) -> Built:
    """Build the movie ZIP and name it by content hash."""

    files = sources(root)
    output = output or root / OUTPUT
    output.mkdir(parents=True, exist_ok=True)

    # The final filename depends on the completed ZIP hash.
    working = output / "movies.building"
    zipping.write(((path, path.name) for path in files), working, COMPRESSION)

    digest = digest_of(working)
    target = output / f"opents-movies-{digest[:8]}.zip"
    working.replace(target)

    return Built(target, len(files), target.stat().st_size, digest)
