from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import fetch, media  # noqa: E402


def movie_tree(root: Path, **movies: bytes) -> Path:
    directory = root / media.SOURCE
    directory.mkdir(parents=True)
    for name, data in movies.items():
        (directory / name).write_bytes(data)
    return directory


class MediaTests(unittest.TestCase):
    def test_the_same_movies_give_the_same_bytes(self):
        # Hash pins require byte-identical rebuilds.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            movie_tree(root, **{"intr0.vqa": b"gdi", "intr1.vqa": b"nod"})

            first = media.build(root, root / "one")
            second = media.build(root, root / "two")

            self.assertEqual(first.digest, second.digest)
            self.assertEqual(first.path.read_bytes(), second.path.read_bytes())

    def test_the_file_is_named_after_its_contents(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            movie_tree(root, **{"intr0.vqa": b"gdi"})

            built = media.build(root)

            self.assertEqual(built.path.name, f"opents-movies-{built.digest[:8]}.zip")

    def test_changing_a_movie_changes_the_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = movie_tree(root, **{"intr0.vqa": b"gdi"})
            before = media.build(root, root / "one")

            (directory / "intr0.vqa").write_bytes(b"a different cut")
            after = media.build(root, root / "two")

            self.assertNotEqual(before.digest, after.digest)

    def test_the_movies_are_stored_whole(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            movie_tree(root, **{"intr0.vqa": b"gdi", "intr1.vqa": b"nod"})

            built = media.build(root)

            with zipfile.ZipFile(built.path) as held:
                self.assertEqual(sorted(held.namelist()), ["intr0.vqa", "intr1.vqa"])
                self.assertEqual(held.read("intr1.vqa"), b"nod")

    def test_an_empty_tree_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)

            with self.assertRaises(media.MediaError) as caught:
                media.build(root)

            self.assertIn("seed", str(caught.exception).lower())


class FetchTests(unittest.TestCase):
    def pinned(self, root: Path, archive: Path, digest: str, size: int, **changes) -> None:
        fields = {
            # Forward slashes avoid TOML escapes.
            "path": archive.as_posix(),
            "sha256": digest,
            "size": size,
            "unpacks-to": "assets/MOVIES",
        }
        fields.update(changes)
        lines = ["[movies]"]
        for key, value in fields.items():
            lines.append(f'{key} = {value}' if isinstance(value, int) else f'{key} = "{value}"')
        (root / fetch.PINS).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def built(self, root: Path) -> media.Built:
        movie_tree(root, **{"intr0.vqa": b"gdi", "intr1.vqa": b"nod"})
        return media.build(root)

    def test_a_pinned_file_is_unpacked(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "built"
            root = Path(raw) / "repository"
            source.mkdir()
            root.mkdir()
            movies = self.built(source)
            self.pinned(root, movies.path, movies.digest, movies.size)

            results = fetch.run(root)

            self.assertEqual(len(results), 1)
            self.assertEqual((root / "assets" / "MOVIES" / "intr1.vqa").read_bytes(), b"nod")

    def test_a_file_that_does_not_match_its_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "built"
            root = Path(raw) / "repository"
            source.mkdir()
            root.mkdir()
            movies = self.built(source)
            wrong = "0" * 64
            self.pinned(root, movies.path, wrong, movies.size)

            with self.assertRaises(fetch.FetchError) as caught:
                fetch.run(root)

            self.assertIn("hashes to", str(caught.exception))
            self.assertFalse((root / "assets" / "MOVIES").exists())

    def test_a_file_of_the_wrong_size_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "built"
            root = Path(raw) / "repository"
            source.mkdir()
            root.mkdir()
            movies = self.built(source)
            self.pinned(root, movies.path, movies.digest, movies.size + 1)

            with self.assertRaises(fetch.FetchError):
                fetch.run(root)

    def test_a_pin_without_a_hash_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / fetch.PINS).write_text(
                '[movies]\npath = "x.zip"\nunpacks-to = "assets/MOVIES"\n', encoding="utf-8"
            )

            with self.assertRaises(fetch.FetchError) as caught:
                fetch.run(root)

            self.assertIn("sha256", str(caught.exception))

    def test_a_member_that_climbs_out_of_the_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "hostile.zip"
            with zipfile.ZipFile(archive, "w") as held:
                held.writestr("../escaped.vqa", b"anywhere")
            digest = media.digest_of(archive)
            self.pinned(root, archive, digest, archive.stat().st_size)

            with self.assertRaises(fetch.FetchError) as caught:
                fetch.run(root)

            self.assertIn("safely", str(caught.exception))
            self.assertFalse((root.parent / "escaped.vqa").exists())

    def test_a_pin_naming_nothing_says_so(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / fetch.PINS).write_text(
                '[movies]\nsha256 = "ab"\nunpacks-to = "assets/MOVIES"\n', encoding="utf-8"
            )

            with self.assertRaises(fetch.FetchError) as caught:
                fetch.run(root)

            self.assertIn("neither a url nor a path", str(caught.exception))
