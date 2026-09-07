from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import builds  # noqa: E402

R = builds.REPOSITORY
BLOB = "https://blob.example/signed.zip"


def artifact(identifier: int, name: str, sha: str = "abc1234" + "0" * 33, run: int = 77, expired: bool = False) -> dict:
    return {"id": identifier, "name": name, "expired": expired, "workflow_run": {"id": run, "head_sha": sha}}


class FakeApi:
    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, path: str) -> object:
        self.asked.append(path)
        if path not in self.answers:
            raise AssertionError(f"nothing was expected to ask for {path}")
        return self.answers[path]


class FakeTransport:
    def __init__(self, answers: dict[str, builds.Response]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> builds.Response:
        self.calls.append((url, dict(headers)))
        return self.answers[url]


class ResolveTests(unittest.TestCase):
    def test_nightly_is_the_latest_successful_nightly_on_main(self):
        api = FakeApi({
            f"repos/{R}/actions/workflows/engine-nightly.yml/runs?branch=main&status=success&per_page=1":
                {"workflow_runs": [{"id": 5, "head_sha": "feed" * 10}]},
            f"repos/{R}/actions/runs/5/artifacts":
                {"artifacts": [artifact(1, "opents-nightly-Debug-feedfee"), artifact(2, "opents-nightly-Release-feedfee")]},
        })

        build = builds.resolve("nightly", api)

        self.assertEqual((build.artifact, build.name, build.run), (2, "opents-nightly-Release-feedfee", 5))
        self.assertEqual(build.commit, "feed" * 10)

    def test_a_commit_finds_the_build_of_that_commit(self):
        full = "0" * 40
        api = FakeApi({
            f"repos/{R}/commits/0000000": {"sha": full},
            f"repos/{R}/actions/workflows/engine.yml/runs?head_sha={full}&status=success&per_page=1":
                {"workflow_runs": [{"id": 9, "head_sha": full}]},
            f"repos/{R}/actions/runs/9/artifacts": {"artifacts": [artifact(3, "opents-Release-0000000")]},
        })

        self.assertEqual(builds.resolve("0000000", api).artifact, 3)

    def test_a_branch_finds_its_latest_build(self):
        api = FakeApi({
            f"repos/{R}/actions/workflows/engine.yml/runs?branch=cncnet&status=success&per_page=1":
                {"workflow_runs": [{"id": 4, "head_sha": "b" * 40}]},
            f"repos/{R}/actions/runs/4/artifacts": {"artifacts": [artifact(8, "opents-Release-bbbbbbb")]},
        })

        self.assertEqual(builds.resolve("cncnet", api).artifact, 8)

    def test_an_artifact_or_a_run_is_named_directly(self):
        api = FakeApi({
            f"repos/{R}/actions/artifacts/12": artifact(12, "opents-Release-abc1234", run=3),
            f"repos/{R}/actions/runs/3/artifacts": {"artifacts": [artifact(12, "opents-Release-abc1234", run=3)]},
        })

        self.assertEqual(builds.resolve("artifact:12", api).run, 3)
        self.assertEqual(builds.resolve("run:3", api).artifact, 12)

    def test_what_cannot_be_used_is_refused_with_a_reason(self):
        api = FakeApi({
            f"repos/{R}/actions/artifacts/1": artifact(1, "opents-Release-abc1234", expired=True),
            f"repos/{R}/actions/artifacts/2": artifact(2, "opents-Debug-abc1234"),
            f"repos/{R}/actions/workflows/engine.yml/runs?branch=nowhere&status=success&per_page=1":
                {"workflow_runs": []},
        })

        for selector, word in (
            ("artifact:1", "expired"),
            ("artifact:2", "Release"),
            ("nowhere", "no successful"),
            ("", "no build"),
        ):
            with self.assertRaises(builds.BuildsError) as caught:
                builds.resolve(selector, api)
            self.assertIn(word, str(caught.exception))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.build = builds.Build(42, "opents-Release-abc1234", "abc1234" + "0" * 33, 7)
        self.api_url = f"{builds.API}/repos/{R}/actions/artifacts/42/zip"
        self.link_url = f"{builds.NIGHTLY_LINK}/{R}/actions/artifacts/42.zip"

    def test_with_a_token_the_api_serves_it_and_the_token_stays_home(self):
        # Do not forward API headers to the signed download host.
        transport = FakeTransport({self.api_url: (302, {"Location": BLOB}, b""), BLOB: (200, {}, b"zip")})

        with tempfile.TemporaryDirectory() as raw:
            path, route = builds.download(self.build, Path(raw), transport, token="secret")
            self.assertEqual(path.read_bytes(), b"zip")

        self.assertEqual(route, "the GitHub API")
        self.assertIn("Authorization", transport.calls[0][1])
        self.assertEqual(transport.calls[1], (BLOB, {}))

    def test_without_a_token_nightly_link_serves_it(self):
        transport = FakeTransport({self.link_url: (302, {"Location": BLOB}, b""), BLOB: (200, {}, b"zip")})

        with tempfile.TemporaryDirectory() as raw:
            _, route = builds.download(self.build, Path(raw), transport)

        self.assertEqual(route, "nightly.link")
        self.assertEqual(transport.calls[0], (self.link_url, {}))

    def test_an_api_refusal_falls_back_to_nightly_link(self):
        transport = FakeTransport({self.api_url: (401, {}, b""), self.link_url: (200, {}, b"zip")})

        with tempfile.TemporaryDirectory() as raw:
            _, route = builds.download(self.build, Path(raw), transport, token="wrong")

        self.assertEqual(route, "nightly.link")

    def test_a_cached_copy_is_used_without_asking(self):
        transport = FakeTransport({})

        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / "engine-42.zip").write_bytes(b"had")
            path, route = builds.download(self.build, Path(raw), transport)
            self.assertEqual(path.read_bytes(), b"had")

        self.assertEqual(route, "cache")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
