import argparse
import contextlib
import http.server
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build as cli
from opentspkg import mirror


class ReadbackCommandTests(unittest.TestCase):
    def test_http_readback_encodes_file_paths_without_reencoding_the_base(self):
        plain = {
            "INI/Game Options/Disable Super Weapons.ini": b"[General]\nSuperWeapons=no\n",
            "Resources/Thème #1/100% Ready.ini": b"[Window]\n",
        }
        archived_name = "Resources/Theme Files/Map #1.pcx"
        archived_data = b"image" * 100
        compressed = mirror.lzma_alone(archived_data)
        files = {name.replace("/", "\\"): mirror.stamp(data) for name, data in plain.items()}
        files[archived_name.replace("/", "\\")] = mirror.stamp(archived_data)
        version = mirror.version_text(
            "test", files, {archived_name.replace("/", "\\"): mirror.stamp(compressed)}, {},
        ).encode("utf-8")
        prefix = "/updates%20root/dev/"
        responses = {
            prefix + "version": version,
            prefix + "INI/Game%20Options/Disable%20Super%20Weapons.ini": next(iter(plain.values())),
            prefix + "Resources/Th%C3%A8me%20%231/100%25%20Ready.ini": b"[Window]\n",
            prefix + "Resources/Theme%20Files/Map%20%231.pcx.lzma": compressed,
        }
        requested = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requested.append(self.path)
                data = responses.get(self.path.partition("?")[0])
                self.send_response(200 if data is not None else 404)
                self.send_header("Content-Length", str(len(data or b"")))
                self.end_headers()
                self.wfile.write(data or b"")

            def log_message(self, format, *args):
                pass

        with tempfile.TemporaryDirectory() as temporary, http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            root = Path(temporary)
            (root / "version").write_bytes(version)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                arguments = argparse.Namespace(built=str(root), url=f"http://127.0.0.1:{server.server_port}{prefix}")
                with contextlib.redirect_stdout(io.StringIO()):
                    result = cli.command_readback(arguments)
            finally:
                server.shutdown()
                thread.join(timeout=5)
            self.assertEqual(result, 0)
            self.assertEqual({path.partition("?")[0] for path in requested}, set(responses))
            self.assertTrue(all(path.partition("?")[2] for path in requested))


if __name__ == "__main__":
    unittest.main()
