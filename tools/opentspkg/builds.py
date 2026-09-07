"""Resolve and download engine CI artifacts by commit, branch, run or artifact.

Use the public API for discovery and authenticated downloads when available,
falling back to nightly.link. Generated artifact ZIPs have no fixed hash;
record the selected commit and artifact ID instead.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = "OpenTS-Developers/OpenTS"
API = "https://api.github.com"
NIGHTLY_LINK = "https://nightly.link"

# Engine artifact workflows and playable build configuration.
NIGHTLY_WORKFLOW = "engine-nightly.yml"
PUSH_WORKFLOW = "engine.yml"
CONFIGURATION = "Release"

NIGHTLY = "nightly"

# Transport response: status, headers, body.
Response = tuple[int, dict[str, str], bytes]
Transport = Callable[[str, dict[str, str]], Response]
Api = Callable[[str], object]


class BuildsError(Exception):
    pass


@dataclass(frozen=True)
class Build:
    artifact: int
    name: str
    commit: str
    run: int

    def __str__(self) -> str:
        return f"{self.name} (commit {self.commit[:7]}, run {self.run}, artifact {self.artifact})"


def token() -> str:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


class _StayPut(urllib.request.HTTPRedirectHandler):
    """Leave redirects to the caller so credentials never reach another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(url: str, headers: dict[str, str]) -> Response:
    opener = urllib.request.build_opener(_StayPut())
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=120) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()
    except OSError as error:
        raise BuildsError(f"{url} could not be fetched: {error}") from error


def github_api(path: str) -> object:
    """GET public GitHub API data; an optional token raises the rate limit."""

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    status, _, body = transport(f"{API}/{path}", headers)
    if status != 200:
        raise BuildsError(f"GitHub answered {status} to {path}")
    return json.loads(body)


def _release_artifact(artifacts: list[dict], where: str) -> dict:
    wanted = [item for item in artifacts if f"-{CONFIGURATION}-" in item["name"]]
    if not wanted:
        names = ", ".join(item["name"] for item in artifacts) or "nothing"
        raise BuildsError(f"{where} uploaded no {CONFIGURATION} build; it holds {names}")
    found = wanted[0]
    if found.get("expired"):
        raise BuildsError(
            f"{found['name']} has expired and cannot be downloaded any more. "
            "Builds are kept for ninety days; name a newer one."
        )
    return found


def _from_run(run: dict, api: Api) -> Build:
    listing = api(f"repos/{REPOSITORY}/actions/runs/{run['id']}/artifacts")
    artifact = _release_artifact(listing["artifacts"], f"run {run['id']}")
    return Build(artifact["id"], artifact["name"], run["head_sha"], run["id"])


def _latest_run(workflow: str, query: str, api: Api, described: str) -> dict:
    listing = api(f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs?{query}&status=success&per_page=1")
    runs = listing["workflow_runs"]
    if not runs:
        raise BuildsError(f"no successful build of {described} is on record")
    return runs[0]


def resolve(selector: str, api: Api = github_api) -> Build:
    """Resolve nightly, artifact:<id>, run:<id>, a commit or a branch.

    Nightly selects the latest successful main nightly; branches select their
    latest successful build. Seven or more hexadecimal digits identify a commit.
    """

    chosen = selector.strip()
    if not chosen:
        raise BuildsError("no build was named")

    if chosen == NIGHTLY:
        run = _latest_run(NIGHTLY_WORKFLOW, "branch=main", api, "the nightly")
        return _from_run(run, api)

    if chosen.startswith("artifact:"):
        identifier = chosen.partition(":")[2]
        item = api(f"repos/{REPOSITORY}/actions/artifacts/{identifier}")
        artifact = _release_artifact([item], f"artifact {identifier}")
        return Build(artifact["id"], artifact["name"], item["workflow_run"]["head_sha"], item["workflow_run"]["id"])

    if chosen.startswith("run:"):
        identifier = chosen.partition(":")[2]
        listing = api(f"repos/{REPOSITORY}/actions/runs/{identifier}/artifacts")
        artifact = _release_artifact(listing["artifacts"], f"run {identifier}")
        return Build(artifact["id"], artifact["name"], artifact["workflow_run"]["head_sha"], int(identifier))

    if re.fullmatch(r"[0-9a-fA-F]{7,40}", chosen):
        commit = api(f"repos/{REPOSITORY}/commits/{chosen}")
        run = _latest_run(PUSH_WORKFLOW, f"head_sha={commit['sha']}", api, f"commit {chosen}")
        return _from_run(run, api)

    run = _latest_run(PUSH_WORKFLOW, f"branch={chosen}", api, f"branch {chosen}")
    return _from_run(run, api)


def _follow_once(url: str, headers: dict[str, str], transport: Transport) -> bytes | None:
    """Follow one signed-URL redirect without forwarding request headers.

    Return None when the first request is refused, allowing a fallback.
    """

    status, reply_headers, body = transport(url, headers)
    if status in (301, 302, 303, 307, 308):
        location = {key.lower(): value for key, value in reply_headers.items()}.get("location")
        if not location:
            raise BuildsError(f"{url} redirected nowhere")
        status, _, body = transport(location, {})
    if status == 200:
        return body
    if status in (401, 403, 404):
        return None
    raise BuildsError(f"{url} answered {status}")


def download(build: Build, cache: Path, transport: Transport = transport, token: str = "") -> tuple[Path, str]:
    """Cache an artifact ZIP and return the download route.

    Try authenticated API access first, then nightly.link. Reuse cached bytes
    by artifact ID; generated ZIPs have no pinned hash.
    """

    target = cache / f"engine-{build.artifact}.zip"
    if target.is_file():
        return target, "cache"

    body = None
    route = ""
    if token:
        body = _follow_once(
            f"{API}/repos/{REPOSITORY}/actions/artifacts/{build.artifact}/zip",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            transport,
        )
        route = "the GitHub API"
    if body is None:
        body = _follow_once(f"{NIGHTLY_LINK}/{REPOSITORY}/actions/artifacts/{build.artifact}.zip", {}, transport)
        route = "nightly.link"
    if body is None:
        raise BuildsError(
            f"{build.name} could not be downloaded from the GitHub API or from "
            "nightly.link. If the artifact is gone, name a newer build."
        )

    cache.mkdir(parents=True, exist_ok=True)
    working = target.with_suffix(".part")
    working.write_bytes(body)
    working.replace(target)
    return target, route
