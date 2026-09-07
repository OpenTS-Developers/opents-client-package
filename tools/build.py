#!/usr/bin/env python
"""Packaging CLI; run ``python tools/build.py --help`` for commands."""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import urllib.error
import urllib.parse
import urllib.request

from opentspkg import assemble, builds, channel, fetch, media, mirror, mix, pack, package, roundtrip, seed, verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class BuildError(Exception):
    """A command failure with a user-facing message."""


def command_roundtrip(arguments) -> int:
    """Compare archive read/write output with original bytes."""

    source = Path(arguments.source)
    if not source.exists():
        raise BuildError(f"{source} is not there")

    if source.is_file():
        results = [roundtrip.check_file(source)]
    else:
        results = roundtrip.check_directory(source)
        if not results:
            raise BuildError(f"{source} holds no archives")

    for result in results:
        print(result)

    failed = [result for result in results if not result.ok]
    print()
    print(f"{len(results) - len(failed)} of {len(results)} archives rebuilt exactly")

    if failed:
        print("\nThe archive code does not yet reproduce these, so it cannot be", file=sys.stderr)
        print("trusted to carry their contents forward:", file=sys.stderr)
        for result in failed:
            print(f"  {result.path}: {result.detail}", file=sys.stderr)
        return 1
    return 0


def command_id(arguments) -> int:
    """Print archive IDs for member names."""

    for name in arguments.names:
        identifier = mix.mix_id(name)
        unsigned = identifier & 0xFFFFFFFF
        print(f"{name}\t{identifier}\t0x{unsigned:08x}")
    return 0


def command_seed(arguments) -> int:
    """Import an installation into the loose asset tree."""

    try:
        report = seed.run(
            Path(arguments.source),
            ROOT,
            dry_run=arguments.dry_run,
            include_extras=arguments.include_extras,
        )
    except seed.SeedError as error:
        raise BuildError(str(error)) from error

    print(f"{'archive':16} {'members':>8} {'named':>8}")
    for label in sorted(report.archives):
        records = [record for record in report.records if record.archive == label]
        named = sum(1 for record in records if record.name)
        print(f"{label:16} {len(records):8} {named:8}")

    carried = sum(1 for record in report.records if record.destination)
    print()
    print(
        f"{len(report.records)} members in {len(report.archives)} archives, "
        f"{report.named} named, {carried} carried over"
    )

    if arguments.dry_run:
        print("\nNothing was written. Run again without --dry-run to unpack.")
    else:
        print(f"\nUnpacked into {ROOT}. seed-report.json records where each member came from.")

    return 0


def command_pack(arguments) -> int:
    """Pack game archives from loose assets."""

    output = Path(arguments.output) if arguments.output else ROOT / pack.OUTPUT
    try:
        results = pack.run(ROOT, output, only=arguments.only, force=arguments.force)
    except pack.PackError as error:
        raise BuildError(str(error)) from error

    for result in results:
        print(result)

    rebuilt = sum(1 for result in results if result.rebuilt)
    total = sum(result.size for result in results)
    print()
    print(f"{len(results)} archives, {rebuilt} rebuilt, {total:,} bytes in {output}")
    return 0


def command_media(arguments) -> int:
    """Build the campaign movie download."""

    try:
        built = media.build(ROOT, Path(arguments.output) if arguments.output else None)
    except media.MediaError as error:
        raise BuildError(str(error)) from error

    print(built)
    print()
    print("Put this where the movies are hosted, then record its url, sha256")
    print("and size in pins.toml.")
    return 0


def command_fetch(arguments) -> int:
    """Fetch build dependencies."""

    try:
        results = fetch.run(ROOT, only=arguments.only, engine=arguments.engine, fresh=arguments.fresh)
    except fetch.FetchError as error:
        raise BuildError(str(error)) from error

    for result in results:
        print(result)
    if not results:
        print("Nothing is pinned.")
    return 0


def command_engine(arguments) -> int:
    """Resolve an engine build without downloading it."""

    try:
        build = builds.resolve(arguments.selector)
    except builds.BuildsError as error:
        raise BuildError(str(error)) from error

    print(f"artifact={build.artifact}")
    print(f"name={build.name}")
    print(f"commit={build.commit}")
    print(f"run={build.run}")
    return 0


def command_dev(arguments) -> int:
    from opentspkg import dev

    try:
        built = dev.run(ROOT, with_game=arguments.with_game, prepare_only=arguments.prepare_only)
    except dev.DevError as error:
        raise BuildError(str(error)) from error
    print(f"Development client: {built}")
    return 0


def command_package(arguments) -> int:
    try:
        result = package.run(
            ROOT, variant=arguments.variant, tag=arguments.tag, engine=arguments.engine,
            make_mirror=arguments.mirror, previous=arguments.previous,
            baseline_output=Path(arguments.baseline_output) if arguments.baseline_output else None,
        )
    except package.PackageError as error:
        raise BuildError(str(error)) from error
    print(f"Package ready: {result.root}")
    return 0


def command_check_baseline(arguments) -> int:
    try:
        channel.check(Path(arguments.baseline), arguments.previous)
    except channel.ChannelError as error:
        raise BuildError(str(error)) from error
    print("Live still matches the history used to build this release.")
    return 0


def _fresh(url: str) -> str:
    """Add a unique query to bypass cached CDN responses."""

    return f"{url}?{secrets.token_hex(4)}"


def command_assemble(arguments) -> int:
    """Assemble the runnable distribution."""

    output = Path(arguments.output) if arguments.output else ROOT / "dist" / "OpenTS-Client"
    try:
        built = assemble.run(ROOT, output, variant=arguments.variant, tag=arguments.tag)
    except assemble.AssembleError as error:
        raise BuildError(str(error)) from error

    for placed in built.placed:
        print(f"  {placed.files:>5} {placed.what} -> {placed.where}")
    print()
    print(f"{built.variant} build: {built.files:,} files, {built.size:,} bytes in {built.root}")
    return 0


def _previous_text(where: str | None, name: str) -> str | None:
    """Read a previous channel file; return None if no channel exists."""

    if not where:
        return None
    if where.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(_fresh(where.rstrip("/") + "/" + name), timeout=60) as reply:
                return reply.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise BuildError(f"{where}/{name} could not be read: HTTP {error.code}") from error
    path = Path(where) / name
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def command_mirror(arguments) -> int:
    """Build a versioned updater mirror."""

    built = Path(arguments.built) if arguments.built else ROOT / "dist" / "OpenTS-Client"
    output = Path(arguments.output) if arguments.output else ROOT / "dist" / "mirror"
    component = ROOT / pack.OUTPUT / mirror.COMPONENT_FILE.name
    try:
        result = mirror.build(
            built,
            arguments.tag,
            output,
            component=component if component.is_file() else None,
            previous_version=_previous_text(arguments.previous, mirror.VERSION_FILE),
            previous_script=_previous_text(arguments.previous, mirror.UPDATE_SCRIPT),
        )
        findings = verify.mirror_tree(output, arguments.tag)
    except mirror.MirrorError as error:
        raise BuildError(str(error)) from error

    print(result)
    for problem in findings.problems:
        print(f"  {problem}")
    if findings.problems:
        print(f"{len(findings.problems)} problems across {findings.checked} checks")
        return 1
    print(f"{findings.checked} checks passed reading it back")
    return 0


def command_readback(arguments) -> int:
    """Compare CDN responses with the local mirror."""

    built = Path(arguments.built)
    if not (built / mirror.VERSION_FILE).is_file():
        raise BuildError(f"{built} holds no version file, so there is nothing to compare with")
    base = arguments.url.rstrip("/") + "/"

    def fetch_served(name: str) -> bytes:
        with urllib.request.urlopen(_fresh(base + urllib.parse.quote(name, safe="/")), timeout=120) as reply:
            return reply.read()

    checked, problems = mirror.readback(built, fetch_served)
    for problem in problems:
        print(f"  {problem}")
    if problems:
        print(f"{len(problems)} problems across {checked} checks reading {base}")
        return 1
    print(f"served as laid out: {checked} checks against {base}")
    return 0


def command_bundle(arguments) -> int:
    """Zip an assembled build."""

    built = Path(arguments.built) if arguments.built else ROOT / "dist" / assemble.DISTRIBUTION
    if arguments.output:
        target = Path(arguments.output)
    else:
        target = ROOT / "dist" / assemble.bundle_name(arguments.variant, arguments.tag or "")
    try:
        bundled = assemble.bundle(built, target)
    except assemble.AssembleError as error:
        raise BuildError(str(error)) from error

    print(bundled)
    return 0


def command_verify(arguments) -> int:
    """Verify required files, optionally against the source installation."""

    output = Path(arguments.output) if arguments.output else ROOT / pack.OUTPUT

    try:
        if arguments.client:
            findings = [verify.client_tree(Path(arguments.client))]
        elif arguments.mirror:
            if not arguments.tag:
                raise BuildError("--mirror needs --tag to say which version to read")
            findings = [verify.mirror_tree(Path(arguments.mirror), arguments.tag)]
        else:
            findings = [verify.engine_contract(output, ROOT / "ini")]
        if arguments.against:
            findings.append(verify.against_source(ROOT, Path(arguments.against), output))
    except verify.VerifyError as error:
        raise BuildError(str(error)) from error

    checked = sum(item.checked for item in findings)
    problems = [problem for item in findings for problem in item.problems]

    for problem in problems:
        print(f"  {problem}")

    if problems:
        print()
        print(f"{len(problems)} problems across {checked} checks")
        return 1

    print(f"{checked} checks passed")
    if not arguments.against and not arguments.client:
        print("Pass --against <installation> to compare every member with the original.")
    return 0


def parser() -> argparse.ArgumentParser:
    parsed = argparse.ArgumentParser(
        prog="build.py",
        description="Build the OpenTS distribution.",
    )
    commands = parsed.add_subparsers(dest="command", required=True)

    development = commands.add_parser("dev", help="prepare and run the client for configuration editing")
    development.add_argument("--with-game", action="store_true", help="also prepare the pinned engine and game archives (no campaign movies)")
    development.add_argument("--prepare-only", action="store_true", help="prepare the development folder without launching")
    development.set_defaults(handler=command_dev)

    distribution = commands.add_parser("package", help="fetch, pack, verify and bundle a complete distribution")
    distribution.add_argument("--variant", choices=package.VARIANTS, default="nomovies")
    distribution.add_argument("--tag", help="the version to write and use in download names")
    distribution.add_argument("--engine", metavar="BUILD", help="use an engine CI build instead of the pin")
    distribution.add_argument("--mirror", action="store_true", help="also build and verify the updater mirror (requires movies and a tag)")
    distribution.add_argument("--previous", help="previous updater channel URL or local directory")
    distribution.add_argument("--baseline-output", help="save the exact previous-channel history for the deployment check")
    distribution.set_defaults(handler=command_package)

    baseline = commands.add_parser("check-baseline", help="refuse automatic deployment if Live changed since the build")
    baseline.add_argument("--baseline", required=True, help="baseline JSON saved by package")
    baseline.add_argument("--previous", required=True, help="the Live channel URL or directory")
    baseline.set_defaults(handler=command_check_baseline)

    check = commands.add_parser(
        "roundtrip",
        help="rebuild original archives and compare them byte for byte",
        description=(
            "Read an archive, write it back, and compare it with the original. "
            "This is how the archive code is judged, so point it at an "
            "installation before trusting a build."
        ),
    )
    check.add_argument("source", help="an archive, or a directory holding archives")
    check.set_defaults(handler=command_roundtrip)

    fill = commands.add_parser(
        "seed",
        help="unpack an installation into the loose asset tree",
        description=(
            "Read the archives an installation holds and write every member "
            "out as a file. Run this once, against a copy of the game you own. "
            "Every archive must rebuild exactly first, or nothing is written."
        ),
    )
    fill.add_argument("source", help="the directory the game is installed in")
    fill.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be unpacked without writing anything",
    )
    fill.add_argument(
        "--include-extras",
        action="store_true",
        help="also read the expansion archives held back from the distribution",
    )
    fill.set_defaults(handler=command_seed)

    build = commands.add_parser(
        "pack",
        help="build the game's archives from the loose asset tree",
        description=(
            "Pack each directory under assets/ into the archive named after "
            "it. An archive whose files have not changed is left alone."
        ),
    )
    build.add_argument("only", nargs="*", help="the archives to build, or all of them")
    build.add_argument("--output", help="where to write the archives")
    build.add_argument("--force", action="store_true", help="rebuild even where nothing changed")
    build.set_defaults(handler=command_pack)

    download = commands.add_parser(
        "media",
        help="build the download that carries the campaign movies",
        description=(
            "Pack assets/MOVIES into one file named after the hash of its own "
            "contents. The same movies always give the same bytes, so anyone "
            "with a copy of the game can rebuild the file a pin names."
        ),
    )
    download.add_argument("--output", help="where to write the download")
    download.set_defaults(handler=command_media)

    pull = commands.add_parser(
        "fetch",
        help="fetch what the build downloads rather than keeps",
        description=(
            "Download everything pins.toml names, check it against the hash "
            "the pin carries, and unpack it. Nothing is used before its hash "
            "matches."
        ),
    )
    pull.add_argument("only", nargs="*", help="the pins to fetch, or all of them")
    pull.add_argument("--fresh", action="store_true", help="download again even when the cache matches, to check remote availability")
    pull.add_argument(
        "--engine",
        metavar="BUILD",
        help=(
            "take the game from a build CI made instead of the pin: nightly, "
            "a branch, a commit, run:<id> or artifact:<id>"
        ),
    )
    pull.set_defaults(handler=command_fetch)

    which = commands.add_parser(
        "engine",
        help="say which build of the game a selector names",
        description=(
            "Look up the build --engine would take, and print its artifact, "
            "name, commit and run, one per line."
        ),
    )
    which.add_argument("selector", help="nightly, a branch, a commit, run:<id> or artifact:<id>")
    which.set_defaults(handler=command_engine)

    put = commands.add_parser(
        "assemble",
        help="put the distribution together into a folder",
        description=(
            "Copy the game, the client, the archives, the maps and the "
            "configuration into the shape a player unzips. Everything it "
            "copies has already been fetched, packed or committed."
        ),
    )
    put.add_argument(
        "--variant",
        choices=assemble.VARIANTS,
        default="nomovies",
        help="base leaves the campaign movies out; full includes them",
    )
    put.add_argument("--output", help="where to build the distribution")
    put.add_argument("--tag", help="the version this build is; writes its version file")
    put.set_defaults(handler=command_assemble)

    lay = commands.add_parser(
        "mirror",
        help="lay out the update mirror for a version",
        description=(
            "Write the tree the client's updater reads for one version, beside "
            "the components shared between versions, and read it back the way "
            "the updater will."
        ),
    )
    lay.add_argument("--tag", required=True, help="the version being published")
    lay.add_argument("--built", help="the assembled base build to mirror")
    lay.add_argument("--output", help="where to lay the mirror out")
    lay.add_argument(
        "--previous",
        help="the live mirror, as a URL or a directory, so removals carry forward",
    )
    lay.set_defaults(handler=command_mirror)

    served = commands.add_parser(
        "readback",
        help="check a version as served against the tree it was laid out from",
        description=(
            "Fetch the version file and a few of the files it names from a "
            "channel, past any cache, and compare them with a laid-out mirror."
        ),
    )
    served.add_argument("--built", required=True, help="the laid-out version, dist/mirror/<tag>")
    served.add_argument("--url", required=True, help="where that version is served")
    served.set_defaults(handler=command_readback)

    wrap = commands.add_parser(
        "bundle",
        help="zip an assembled build",
        description=(
            "Zip a build under a folder of its own. The same build always "
            "gives the same bytes."
        ),
    )
    wrap.add_argument("--built", help="the assembled build to zip")
    wrap.add_argument("--output", help="where to write the zip")
    wrap.add_argument(
        "--variant", choices=assemble.VARIANTS, default="nomovies", help="which build this is"
    )
    wrap.add_argument("--tag", help="the version, for the name")
    wrap.set_defaults(handler=command_bundle)

    check_build = commands.add_parser(
        "verify",
        help="check a build holds what it should",
        description=(
            "Check the built archives against what the engine needs, and, with "
            "--against, against the installation they were made from: every "
            "member must come back out with the same bytes and nothing else."
        ),
    )
    check_build.add_argument("--against", help="the installation the build was seeded from")
    check_build.add_argument("--client", metavar="FOLDER", help="check a prepared client and its configuration, without game archives")
    check_build.add_argument("--output", help="where the archives were written")
    check_build.add_argument("--mirror", help="a mirror tree to read back instead")
    check_build.add_argument("--tag", help="the version in that mirror to read")
    check_build.set_defaults(handler=command_verify)

    identify = commands.add_parser(
        "id",
        help="print the archive id a name hashes to",
    )
    identify.add_argument("names", nargs="+", help="the file names to hash")
    identify.set_defaults(handler=command_id)

    return parsed


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        return arguments.handler(arguments)
    except (BuildError, OSError) as error:
        print(f"ACTION REQUIRED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
