"""The exact channel inputs used when carrying update removals forward."""

from __future__ import annotations

import base64
import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path

FILES = ("version", "updateexec")


class ChannelError(Exception):
    pass


def fresh_url(url: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}readback={secrets.token_hex(8)}"


def _read(where: str, name: str) -> bytes | None:
    if where.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(fresh_url(where.rstrip("/") + "/" + name), timeout=60) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise ChannelError(f"could not read {where}/{name}: HTTP {error.code}") from error
        except OSError as error:
            raise ChannelError(f"could not read {where}/{name}: {error}") from error
    path = Path(where) / name
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError as error:
        raise ChannelError(f"could not read {path}: {error}") from error


def snapshot(where: str | None) -> dict[str, bytes | None]:
    if not where:
        return dict.fromkeys(FILES)
    files = {name: _read(where, name) for name in FILES}
    if _read(where, "version") != files["version"]:
        raise ChannelError("the channel changed while reading its history; retry the build")
    return files


def text(files: dict[str, bytes | None], name: str) -> str | None:
    data = files[name]
    return None if data is None else data.decode("utf-8", errors="replace")


def _source(where: str) -> str:
    return where.rstrip("/") if where.startswith(("http://", "https://")) else str(Path(where).resolve())


def save(path: Path, where: str, files: dict[str, bytes | None]) -> None:
    payload = {
        "source": _source(where),
        "files": {name: None if data is None else base64.b64encode(data).decode("ascii")
                  for name, data in files.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def check(path: Path, where: str) -> None:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["source"] != _source(where) or set(saved["files"]) != set(FILES):
            raise ValueError("baseline describes a different channel or is incomplete")
        expected = {name: None if value is None else base64.b64decode(value, validate=True)
                    for name, value in saved["files"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ChannelError(f"invalid channel baseline {path}: {error}") from error
    if snapshot(where) != expected:
        raise ChannelError("Live changed since this package was built. Rerun the release workflow to rebuild against current Live.")
