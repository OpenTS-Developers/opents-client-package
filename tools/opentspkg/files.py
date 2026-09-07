"""File operations and generated configuration helpers."""

from contextlib import contextmanager
from pathlib import Path
import re
import secrets
import shutil
import time


def _retry_windows(operation):
    # Virus scanners and indexers can briefly hold newly extracted files open.
    delays = iter((0.1, 0.2, 0.5, 1, 2, 4))
    while True:
        try:
            return operation()
        except OSError as error:
            delay = next(delays, None)
            if getattr(error, "winerror", None) not in (5, 32, 33) or delay is None:
                raise
            time.sleep(delay)


def replace(source: Path, destination: Path) -> None:
    _retry_windows(lambda: source.replace(destination))


@contextmanager
def staging_directory(parent: Path, prefix: str):
    # Python 3.13's mkdtemp applies an owner-only Windows ACL. Moving its
    # children into a distribution retains that ACL instead of inheriting the
    # output directory's permissions. Build output uses ordinary mkdir rules.
    parent = parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / f"{prefix}{secrets.token_hex(12)}"
    directory.mkdir()
    try:
        yield directory
    finally:
        if (directory.is_symlink() or directory.is_junction()
                or directory.resolve().parent != parent):
            raise OSError(f"unsafe staging cleanup: {directory}")
        _retry_windows(lambda: shutil.rmtree(directory))


def set_ini(path: Path, section: str, key: str, value: str) -> None:
    """Change a generated INI without rewriting its unrelated keys or comments."""
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            text = handle.read()
    else:
        text = ""
    newline = "\r\n" if "\r\n" in text else "\n"
    output: list[str] = []
    active = found_section = found_key = False
    for line in text.splitlines(keepends=True):
        heading = re.match(r"^\s*\[([^]]+)\]", line)
        if heading:
            if active and not found_key:
                output.append(f"{key}={value}{newline}")
            active = heading[1].casefold() == section.casefold()
            found_section |= active
            found_key = False
        if active and re.match(rf"^\s*{re.escape(key)}\s*=", line, re.IGNORECASE):
            line = f"{key}={value}{newline}"
            found_key = True
        output.append(line if line.endswith(("\n", "\r")) else line + newline)
    if active and not found_key:
        output.append(f"{key}={value}{newline}")
    if not found_section:
        output.extend([f"[{section}]{newline}", f"{key}={value}{newline}"])
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("".join(output))
