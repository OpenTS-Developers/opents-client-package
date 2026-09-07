"""Compare original MIX bytes with read/write output.

Exact comparison catches reader/writer losses that self-consistent decoding
could miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import mix


@dataclass
class Result:
    path: Path
    ok: bool
    detail: str
    members: int = 0
    flags: int = 0

    def __str__(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        return f"{mark} {self.path.name:16} {self.detail}"


def check_file(path: Path | str) -> Result:
    """Read an archive and write it back, comparing it with the original."""

    path = Path(path)
    original = path.read_bytes()

    try:
        archive = mix.read_archive(path)
    except mix.MixFormatError as error:
        return Result(path, False, f"could not be read: {error}")

    rebuilt = mix.serialize_archive(archive)

    if rebuilt == original:
        described = _describe(archive)
        return Result(path, True, described, len(archive.entries), archive.flags)

    return Result(path, False, _difference(original, rebuilt), len(archive.entries), archive.flags)


def _describe(archive: mix.Archive) -> str:
    parts = [f"{len(archive.entries)} members", f"flags {archive.flags}"]
    if archive.digest is not None:
        parts.append("digest")
    if archive.key_source is not None:
        parts.append("encrypted index")
    return ", ".join(parts)


def _difference(original: bytes, rebuilt: bytes) -> str:
    if len(original) != len(rebuilt):
        return f"rebuilt {len(rebuilt)} bytes against {len(original)}"
    for offset, (left, right) in enumerate(zip(original, rebuilt)):
        if left != right:
            return f"first difference at byte {offset}"
    return "differs"


def check_directory(directory: Path | str) -> list[Result]:
    """Check every archive directly inside a directory."""

    directory = Path(directory)
    results = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and mix.is_archive_name(path.name):
            results.append(check_file(path))
    return results
