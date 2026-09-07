"""Write deterministic ZIPs with sorted entries and fixed metadata."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Iterable

# Use the earliest ZIP timestamp for deterministic output.
EPOCH = (1980, 1, 1, 0, 0, 0)

# Fixed Unix metadata keeps output identical across platforms.
UNIX = 3

BLOCK = 1 << 20


def entry(name: str, size: int, compress: int) -> zipfile.ZipInfo:
    """Create a ZIP entry with platform-independent metadata."""

    info = zipfile.ZipInfo(name.replace("\\", "/"), date_time=EPOCH)
    info.compress_type = compress
    info.create_system = UNIX
    info.external_attr = 0o644 << 16
    info.file_size = size
    return info


def write(files: Iterable[tuple[Path, str]], target: Path, compress: int) -> int:
    """Write files in name order and return their count."""

    ordered = sorted(files, key=lambda pair: pair[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        for path, name in ordered:
            info = entry(name, path.stat().st_size, compress)
            with archive.open(info, "w") as into, path.open("rb") as source:
                shutil.copyfileobj(source, into, BLOCK)
    return len(ordered)
