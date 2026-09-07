import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opentspkg import channel, mix, pack, package, verify
from test_assemble import repository


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = repository(Path(self.temp.name))
        labels = set(verify.REQUIRED_ARCHIVES + verify.THEATER_ARCHIVES + ("MOVIES00", "MOVIES", "SOUNDS01"))
        for name in labels:
            directory = self.root / "assets" / name
            directory.mkdir(parents=True)
            (directory / "sample.bin").write_bytes(b"sample")
        for name in verify.CACHED_BY_NAME:
            (self.root / "assets" / "CACHE" / name).write_bytes(b"cached")
        (self.root / "ini" / "firestrm.ini").write_bytes(b"[General]\n")
        self.fetch = self.enterContext(patch.object(package.fetch, "run", return_value=[]))

    def run_package(self, **kwargs):
        return package.run(self.root, report=lambda _: None, **kwargs)

    def test_no_movies_ignores_previously_fetched_movies_and_stale_archives(self):
        (self.root / pack.OUTPUT / "GONE.MIX").write_bytes(b"obsolete")
        with patch.object(pack, "run", wraps=pack.run) as packing:
            built = self.run_package()
        self.assertNotIn("MOVIES", packing.call_args.kwargs["only"])
        self.fetch.assert_called_once_with(self.root, only=["engine", "client", "launcher", "editor"], engine=None)
        self.assertFalse((built.root / "MIX" / "MOVIES.MIX").exists())
        self.assertFalse((built.root / "MIX" / "GONE.MIX").exists())
        with zipfile.ZipFile(built.bundles[0]) as held:
            self.assertIn("OpenTS-Client/OpenTS.exe", held.namelist())
            self.assertIn("OpenTS-Client/MapEditor/WorldAlteringEditor.exe", held.namelist())

    def test_both_variants_and_mirror_share_history_and_layout(self):
        previous = self.root / "live"
        previous.mkdir()
        (previous / "version").write_bytes(b"[DTA]\r\nVersion=v0\r\n[FileVersions]\r\ngone.dll=old\r\n")
        (previous / "updateexec").write_bytes(b"[Delete]\r\nolder.dll\r\n")
        baseline = self.root / "dist" / "live-baseline.json"
        built = self.run_package(variant="both", tag="v1", make_mirror=True, previous=str(previous), baseline_output=baseline)
        self.assertEqual(len(built.bundles), 2)
        self.assertFalse((built.root / "MIX" / "MOVIES.MIX").exists())
        with zipfile.ZipFile(built.bundles[0]) as full:
            self.assertIn("OpenTS-Client/MIX/MOVIES.MIX", full.namelist())
        self.assertTrue(verify.mirror_tree(built.mirror, "v1").ok)
        self.assertTrue((built.mirror / "Components" / "MOVIES.MIX").is_file())
        script = (built.mirror / "v1" / "updateexec").read_text()
        self.assertIn("gone.dll", script)
        self.assertIn("older.dll", script)
        channel.check(baseline, str(previous))
        (previous / "updateexec").write_bytes(b"[Delete]\r\nnew-history.dll\r\n")
        with self.assertRaisesRegex(channel.ChannelError, "Rerun"):
            channel.check(baseline, str(previous))

    def test_engine_selection_is_forwarded_and_invalid_mirror_never_downloads(self):
        self.run_package(engine="artifact:42")
        self.assertEqual(self.fetch.call_args.kwargs["engine"], "artifact:42")
        self.fetch.reset_mock()
        with self.assertRaises(package.PackageError):
            self.run_package(make_mirror=True)
        self.fetch.assert_not_called()

    def test_tagged_no_movies_package_does_not_use_stale_movie_metadata(self):
        built = self.run_package(tag="v1")
        first = built.bundles[0].read_bytes()
        (self.root / pack.OUTPUT / "MOVIES.MIX").write_bytes(b"stale movies from another pin")
        again = self.run_package(tag="v1")
        self.assertEqual(first, again.bundles[0].read_bytes())

    def test_failed_engine_verification_does_not_bundle_or_mirror(self):
        (self.root / "ini" / "firestrm.ini").unlink()
        with self.assertRaisesRegex(package.PackageError, "firestrm.ini"):
            self.run_package(tag="v1", make_mirror=True)
        self.assertFalse(list((self.root / "dist").glob("*.zip")))
        self.assertFalse((self.root / "dist" / "mirror").exists())


class BaselineTests(unittest.TestCase):
    def test_absent_is_distinct_from_empty_and_bad_baselines_fail(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline = root / "baseline.json"
            channel.save(baseline, str(root), channel.snapshot(str(root)))
            channel.check(baseline, str(root))
            (root / "version").write_bytes(b"")
            with self.assertRaises(channel.ChannelError):
                channel.check(baseline, str(root))
            baseline.write_text("{}")
            with self.assertRaises(channel.ChannelError):
                channel.check(baseline, str(root))

    def test_channel_switch_during_capture_is_refused(self):
        with patch.object(channel, "_read", side_effect=[b"v1", b"history", b"v2"]):
            with self.assertRaisesRegex(channel.ChannelError, "changed while reading"):
                channel.snapshot("https://example.com/live")


if __name__ == "__main__":
    unittest.main()
