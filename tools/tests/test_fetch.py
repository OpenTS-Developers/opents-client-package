import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opentspkg import builds, fetch, media


def archive(root, name, files):
    path = root / name
    with zipfile.ZipFile(path, "w") as held:
        for filename, data in files.items():
            held.writestr(filename, data)
    return path


def pin(root, path, url=""):
    (root / "pins.toml").write_text(
        f'[engine]\npath="{path.as_posix()}"\nurl="{url}"\n'
        f'sha256="{media.digest_of(path)}"\nunpacks-to=".cache/parts/engine"\n', encoding="utf-8")


class FetchCacheTests(unittest.TestCase):
    def test_unchanged_parts_need_no_network_or_extraction(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = archive(root, "engine.zip", {"Game.exe": b"one"})
            pin(root, source)
            fetch.run(root)
            with patch.object(fetch, "_unpack", side_effect=AssertionError("extracted again")), patch.object(fetch, "_download", side_effect=AssertionError("network")):
                fetch.run(root)

    def test_changed_pin_removes_old_binaries_and_corrupted_cache_is_repaired(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pin(root, archive(root, "old.zip", {"Game.exe": b"old", "old.dll": b"gone"}))
            fetch.run(root)
            pin(root, archive(root, "new.zip", {"Game.exe": b"new", "new.dll": b"present"}))
            fetched = fetch.run(root)[0]
            self.assertFalse((fetched.unpacked / "old.dll").exists())
            (fetched.unpacked / "Game.exe").write_bytes(b"bad")
            fetch.run(root)
            self.assertEqual((fetched.unpacked / "Game.exe").read_bytes(), b"new")

    def test_failed_extraction_keeps_previous_working_part(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pin(root, archive(root, "good.zip", {"Game.exe": b"good"}))
            previous = fetch.run(root)[0].unpacked
            pin(root, archive(root, "bad.zip", {"../escape": b"bad"}))
            with self.assertRaises(fetch.FetchError):
                fetch.run(root)
            self.assertEqual((previous / "Game.exe").read_bytes(), b"good")

    def test_switching_from_nightly_back_to_pin_cleans_both_directions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pin(root, archive(root, "pinned.zip", {"Game.exe": b"pin", "pinned.dll": b"pin"}))
            fetch.run(root)
            nightly = archive(root, "nightly.zip", {"Game.exe": b"nightly", "nightly.dll": b"nightly"})
            with patch.object(builds, "resolve", return_value=builds.Build(1, "test", "abcdef", 1)), patch.object(builds, "download", return_value=(nightly, "cache")):
                destination = fetch.run(root, engine="nightly")[0].unpacked
            self.assertFalse((destination / "pinned.dll").exists())
            fetch.run(root)
            self.assertFalse((destination / "nightly.dll").exists())
            self.assertEqual((destination / "Game.exe").read_bytes(), b"pin")

    def test_fresh_download_checks_remote_even_with_a_valid_cache(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cached = archive(root, "engine.zip", {"Game.exe": b"pin"})
            pin(root, cached, url="https://example.com/engine.zip")
            with patch.object(fetch, "_download") as download:
                fetch.run(root, cache=root)
                download.assert_not_called()
                fetch.run(root, cache=root, fresh=True)
                download.assert_called_once_with("https://example.com/engine.zip", cached)

    def test_windows_escape_spelling_is_refused_on_every_platform(self):
        for name in (r"C:\escape", r"..\escape", "/escape", r"\\server\escape", "file:stream"):
            self.assertFalse(fetch._safe_member(name), name)


if __name__ == "__main__":
    unittest.main()
