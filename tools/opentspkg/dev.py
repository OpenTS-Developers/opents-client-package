"""Prepare an isolated client workspace, preserving its settings between runs."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path, PureWindowsPath

from . import assemble, fetch, mix, pack, verify
from .files import set_ini as _set_ini, staging_directory

OUTPUT = Path("build") / "dev-client"
MANIFEST = Path(".build-cache") / "dev-client.json"
USER_FILES = {"sun.ini", "resources/updaterconfig.ini"}


class DevError(Exception):
    pass


def _checked_path(base: Path, relative: str | Path) -> Path:
    """Reject escaped paths and aliases before any workspace write or deletion."""
    name = str(relative).replace("\\", "/")
    parts = name.split("/")
    if PureWindowsPath(name).drive or any(
        p in ("", ".", "..") or ":" in p or p.rstrip(" .") != p for p in parts
    ):
        raise DevError(f"unsafe development workspace path: {name!r}")
    current = base
    for part in parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise DevError(f"the development workspace must use ordinary files, not links: {current}")
    # Relative components without links guarantee containment; avoid costly resolve().
    return current


def _files(directory: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if not directory.exists():
        return files
    if not directory.is_dir():
        raise DevError(f"{directory} must be a directory")
    # Reject linked directories before descent; reuse DirEntry metadata.
    def visit(parent: Path) -> None:
        with os.scandir(parent) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                path = Path(entry.path)
                if (stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise DevError(f"the development workspace must use ordinary files, not links: {path}")
                if stat.S_ISDIR(info.st_mode):
                    visit(path)
                elif stat.S_ISREG(info.st_mode):
                    # Windows DirEntry metadata reports st_nlink as zero;
                    # query the file itself to retain hard-link detection.
                    links = path.stat(follow_symlinks=False).st_nlink if os.name == "nt" else info.st_nlink
                    if links > 1:
                        raise DevError(f"the development workspace must not contain hard links: {path}")
                    files[path.relative_to(directory).as_posix()] = path

    visit(directory)
    return files


def _check_unlocked(paths) -> None:
    """Probe existing files before replacing them, without changing their bytes."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for path in paths:
        if not path.exists():
            continue
        if not path.is_file():
            raise DevError(f"a directory occupies a development file path: {path}")
        try:
            if os.name == "nt":
                # Exclusive access detects running binaries and files without write sharing.
                handle = create(str(path), 0x40000000, 0, None, 3, 0x80, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                kernel.CloseHandle(handle)
            else:
                with path.open("r+b"):
                    pass
        except OSError as error:
            raise DevError(
                f"cannot update {path}; close the development client and check "
                f"that its files are writable ({error})"
            ) from error


def _previous(manifest: Path, runtime: Path) -> set[str]:
    if not manifest.exists():
        return set()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        names = data["files"]
        if data.get("schema") != 1 or not isinstance(names, list):
            raise ValueError("unsupported manifest format")
        for name in names:
            if not isinstance(name, str) or "\\" in name:
                raise ValueError("file names must be relative paths using forward slashes")
            _checked_path(runtime, name)
        return set(names)
    except (ValueError, KeyError, TypeError) as error:
        raise DevError(f"cannot read {manifest}: {error}") from error


def _sync(stage: Path, runtime: Path, manifest: Path, with_game: bool) -> None:
    previous = _previous(manifest, runtime)
    staged = _files(stage)
    # Settings and the chosen update channel belong to this installation.
    managed = {name for name in staged if name.casefold() not in USER_FILES}
    current_folded = {name.casefold() for name in managed}
    if len(current_folded) != len(managed):
        raise DevError("development inputs contain file names differing only in case")
    removed = {
        name for name in previous
        if name.casefold() not in USER_FILES and name not in managed
        and (os.name != "nt" or name.casefold() not in current_folded)
    }
    targets = {name: _checked_path(runtime, name) for name in set(staged) | removed}
    _check_unlocked(list(targets.values()) + [manifest])

    runtime.mkdir(parents=True, exist_ok=True)
    for name, source in staged.items():
        target = targets[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        # Staging and runtime share build/, so each replacement is atomic.
        source.replace(target)
    for name in removed:
        target = targets[name]
        target.unlink(missing_ok=True)
        parent = target.parent
        while parent != runtime:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    manifest.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(manifest.parent, ".dev-manifest-") as working:
        temporary = working / "manifest.json"
        temporary.write_text(
            json.dumps({"schema": 1, "with_game": with_game, "files": sorted(managed)}, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(manifest)


def _launch(runtime: Path) -> None:
    # The launcher starts the supported backend and exits.
    subprocess.Popen([str(runtime / assemble.LAUNCHER)], cwd=runtime)


def _keep_update_channel(runtime: Path, stage: Path) -> None:
    """Keep the selected channel while refreshing its template from source."""
    active = runtime / "Resources" / "UpdaterConfig.ini"
    if not active.is_file():
        return
    contents = active.read_bytes()
    destination = stage / "Resources" / active.name
    for old_template in sorted(active.parent.glob("UpdaterConfig_*.ini")):
        if old_template.read_bytes() == contents:
            updated = destination.parent / old_template.name
            if updated.is_file():
                shutil.copy2(updated, destination)
            # A removed channel falls back to the new source default.
            return
    # Locally configured mirrors are settings too.
    shutil.copy2(active, destination)


def _hide_unavailable_editor(stage: Path) -> None:
    """Hide editor controls in client-only previews without changing their source."""
    resources = stage / "Resources"
    controls = {"mainmenu.ini": "btnMapEditor", "extraswindow.ini": "btnExMapEditor"}
    for path in resources.iterdir():
        section = controls.get(path.name.casefold())
        if section and path.is_file():
            text = path.read_text(encoding="utf-8-sig")
            if re.search(rf"^\s*\[{section}\]", text, re.MULTILINE | re.IGNORECASE):
                _set_ini(path, section, "Visible", "false")


def run(root: Path, with_game: bool = False, prepare_only: bool = False) -> Path:
    """Fetch, stage, sync and optionally launch build/dev-client."""
    if os.name != "nt" and not prepare_only:
        raise DevError("client launch requires Windows; use --prepare-only to check the workspace here")
    root = root.resolve()
    runtime = _checked_path(root, OUTPUT)
    manifest = _checked_path(root, MANIFEST)
    try:
        print("Checking cached client files...", flush=True)
        _check_unlocked(_files(runtime).values())
        _previous(manifest, runtime)
        fetch.run(root, only=["client", "launcher", "engine", "editor"] if with_game else ["client", "launcher"])
        runtime.parent.mkdir(parents=True, exist_ok=True)
        print("Preparing configuration and assets...", flush=True)
        with staging_directory(runtime.parent, ".dev-client-") as temporary:
            stage = temporary / "client"
            if with_game:
                labels = [p.name for p in pack.asset_directories(root) if p.name.upper() != "MOVIES"]
                if not labels:
                    raise DevError("there are no game assets to pack for --with-game")
                packed = pack.run(root, root / pack.OUTPUT, only=labels)
                assemble.run(root, stage, variant="nomovies", archives=[item.path for item in packed])
                findings = verify.engine_contract(stage / "MIX", stage / "INI")
                if not findings.ok:
                    raise DevError("the game data is incomplete: " + "; ".join(findings.problems))
            else:
                assemble.client_layout(root, stage)
                _hide_unavailable_editor(stage)
            definitions = stage / "Resources" / "ClientDefinitions.ini"
            if not definitions.is_file():
                raise DevError("client/Resources/ClientDefinitions.ini is missing")
            _keep_update_channel(runtime, stage)
            print("Validating client configuration...", flush=True)
            findings = verify.client_tree(stage)
            if not findings.ok:
                raise DevError("client configuration is incomplete: " + "; ".join(findings.problems))
            _set_ini(definitions, "UserDefaults", "WriteInstallationPathToRegistry", "false")
            settings = stage / "SUN.ini"
            if (runtime / "SUN.ini").is_file():
                shutil.copy2(runtime / "SUN.ini", settings)
            _set_ini(settings, "Options", "WriteInstallationPathToRegistry", "false")
            print("Refreshing the development folder...", flush=True)
            _sync(stage, runtime, manifest, with_game)
        if not prepare_only:
            print("Starting OpenTS client...", flush=True)
            _launch(runtime)
        return runtime
    except (OSError, assemble.AssembleError, fetch.FetchError, pack.PackError,
            verify.VerifyError, mix.MixFormatError, UnicodeError) as error:
        raise DevError(str(error)) from error
