"""The shared local and CI path from pinned inputs to verified deliverables."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import assemble, channel, fetch, mirror, pack, verify
from .files import staging_directory

VARIANTS = (*assemble.VARIANTS, "both")


class PackageError(Exception):
    pass


@dataclass
class Package:
    root: Path
    bundles: list[Path]
    mirror: Path | None


def _verified(findings: verify.Findings) -> None:
    if findings.problems:
        raise PackageError("package verification failed:\n" + "\n".join(findings.problems))


def run(
    root: Path,
    variant: str = "nomovies",
    tag: str | None = None,
    engine: str | None = None,
    make_mirror: bool = False,
    previous: str | None = None,
    baseline_output: Path | None = None,
    report=print,
) -> Package:
    if variant not in VARIANTS:
        raise PackageError(f"unknown variant: {variant}")
    if tag is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag):
        raise PackageError("a package tag must contain only letters, numbers, dots, underscores and hyphens")
    if make_mirror and not tag:
        raise PackageError("--mirror needs --tag")
    if previous and not make_mirror:
        raise PackageError("--previous needs --mirror")
    if baseline_output and not (make_mirror and previous):
        raise PackageError("--baseline-output needs --mirror and --previous")

    want_movies = make_mirror or variant in {"full", "both"}
    parts = ["engine", "client", "launcher", "editor"] + (["movies"] if want_movies else [])
    try:
        for result in fetch.run(root, only=parts, engine=engine):
            report(str(result))
        # Explicit selection excludes cached movies and stale archives from nomovies.
        selected = [path.name for path in pack.asset_directories(root)
                    if want_movies or path.name.upper() != "MOVIES"]
        if not selected:
            raise PackageError("no game assets are available to pack")
        archives = pack.run(root, root / pack.OUTPUT, only=selected)
        for archive in archives:
            report(str(archive))
        history = channel.snapshot(previous) if make_mirror else None
        variants = ("full", "nomovies") if variant == "both" else (variant,)
        bundles = []
        for selected_variant in variants:
            built = assemble.run(root, root / "dist" / assemble.DISTRIBUTION,
                                 variant=selected_variant, tag=tag,
                                 archives=[item.path for item in archives])
            _verified(verify.engine_contract(built.root / "MIX", built.root / "INI"))
            _verified(verify.client_tree(built.root))
            bundled = assemble.bundle(built.root, root / "dist" / assemble.bundle_name(selected_variant, tag or ""))
            bundles.append(bundled.path)
            report(str(bundled))

        mirrored = None
        if make_mirror:
            # Publish only this invocation's mirror, never older local output.
            destination = root / "dist" / "mirror"
            if destination.is_symlink() or destination.is_junction() or not destination.resolve().is_relative_to(root.resolve()):
                raise PackageError(f"unsafe mirror output: {destination}")
            with staging_directory(root / "dist", ".mirror-") as working:
                staged = working / "mirror"
                result = mirror.build(built.root, tag, staged,
                                      component=root / pack.OUTPUT / mirror.COMPONENT_FILE.name,
                                      previous_version=channel.text(history, "version"),
                                      previous_script=channel.text(history, "updateexec"))
                _verified(verify.mirror_tree(staged, tag))
                if destination.exists():
                    shutil.rmtree(destination)
                staged.replace(destination)
                report(f"verified mirror: {tag}, {result.files} files")
            mirrored = destination
            if baseline_output:
                channel.save(baseline_output, previous, history)
        return Package(built.root, bundles, mirrored)
    except (fetch.FetchError, pack.PackError, assemble.AssembleError, mirror.MirrorError,
            verify.VerifyError, channel.ChannelError, OSError) as error:
        raise PackageError(str(error)) from error
