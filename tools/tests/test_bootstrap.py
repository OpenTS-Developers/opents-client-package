"""Windows bootstrap integration; run dev.cmd -PrepareOnly to cache tools first."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "nt", "the bootstrap targets Windows")
class BootstrapTests(unittest.TestCase):
    def test_cached_tools_flags_paths_integrity_and_exit_status(self):
        config = json.loads((ROOT / "tools/bootstrap.json").read_text())
        python_name = f"python-{config['python']['version']}-embed-amd64.zip"
        seven_zip_name = f"7zr-{config['seven_zip']['version']}.exe"
        downloads = ROOT / ".cache/tools/downloads"
        if not all((downloads / name).is_file() for name in (python_name, seven_zip_name)):
            self.skipTest("run dev.cmd -PrepareOnly to cache the portable tools first")

        with tempfile.TemporaryDirectory(prefix="OpenTS bootstrap ") as temporary:
            root = Path(temporary).resolve()
            (root / "tools").mkdir()
            cached = root / ".cache/tools/downloads"
            cached.mkdir(parents=True)
            for name in ("dev.cmd", "dev.ps1", "tools/.python-version"):
                shutil.copyfile(ROOT / name, root / name)
            for name in (python_name, seven_zip_name):
                shutil.copyfile(downloads / name, cached / name)
            # Cached runs must not use download URLs.
            for tool in config.values():
                tool["url"] = (root / "missing-download").as_uri()
            (root / "tools/bootstrap.json").write_text(json.dumps(config))
            (root / "tools/build.py").write_text(
                "import json, os, pathlib, sys\n"
                "root = pathlib.Path(__file__).resolve().parents[1]\n"
                "(root / 'invoked.json').write_text(json.dumps({\n"
                " 'argv': sys.argv[1:], 'cwd': str(pathlib.Path.cwd()),\n"
                " 'python': sys.executable, 'seven_zip': os.environ['SEVEN_ZIP'],\n"
                " 'version': list(sys.version_info[:3])}))\n"
                "sys.exit(int(os.environ.get('BOOTSTRAP_TEST_EXIT', '0')))\n"
            )
            environment = dict(os.environ)
            environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
            # PS 7 module paths may be unusable by PS 5.1.
            environment["PSModulePath"] = str(root / "missing-modules")
            runtime = root / f".cache/tools/python-{config['python']['version']}-amd64"

            def run(*flags):
                return subprocess.run(
                    [os.environ["ComSpec"], "/d", "/c", str(root / "dev.cmd"), *flags],
                    cwd=root.parent,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )

            result = run("-WithGame", "-PrepareOnly")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.splitlines()[0], "Preparing OpenTS development client...")
            invoked = json.loads((root / "invoked.json").read_text())
            self.assertEqual(invoked["argv"], ["dev", "--with-game", "--prepare-only"])
            self.assertEqual(Path(invoked["cwd"]), root)
            self.assertEqual(Path(invoked["python"]), runtime / "python.exe")
            self.assertEqual(Path(invoked["seven_zip"]), cached / seven_zip_name)
            self.assertEqual(invoked["version"], [int(n) for n in config["python"]["version"].split(".")])

            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            direct = subprocess.run(
                [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "dev.ps1"), "-PrepareOnly"],
                cwd=root.parent,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)

            result = run()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads((root / "invoked.json").read_text())["argv"], ["dev"])

            stale = runtime / "stale.dll"
            stale.write_bytes(b"obsolete runtime content")
            environment["BOOTSTRAP_TEST_EXIT"] = "37"
            result = run("-PrepareOnly")
            self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
            self.assertFalse(stale.exists())
            self.assertEqual(json.loads((root / "invoked.json").read_text())["argv"], ["dev", "--prepare-only"])

            # Reject corrupt cached tools before invoking Python.
            (root / "invoked.json").unlink()
            (cached / seven_zip_name).write_bytes(b"invalid executable")
            result = run("-PrepareOnly")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "invoked.json").exists())

            shutil.copyfile(downloads / seven_zip_name, cached / seven_zip_name)
            (cached / python_name).write_bytes(b"invalid archive")
            result = run("-PrepareOnly")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "invoked.json").exists())


if __name__ == "__main__":
    unittest.main()
