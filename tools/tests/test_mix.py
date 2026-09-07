"""Synthetic archive tests. Use build.py roundtrip for original game archives."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentspkg import crypt, mix  # noqa: E402
from opentspkg.blowfish import Blowfish  # noqa: E402


class NameHashTests(unittest.TestCase):
    def test_known_ids(self):
        # Known IDs from original game archives.
        self.assertEqual(mix.mix_id("local mix database.dat"), 913179935)
        self.assertEqual(mix.mix_id("MAPS01.MIX"), -1618101367)
        self.assertEqual(mix.mix_id("LOCAL.MIX"), -1470853159)
        self.assertEqual(mix.mix_id("CACHE.MIX"), 995792606)
        self.assertEqual(mix.mix_id("KEY.INI"), 1983676893)

    def test_case_is_ignored(self):
        self.assertEqual(mix.mix_id("conquer.mix"), mix.mix_id("CONQUER.MIX"))

    def test_padding_uses_the_trailing_group(self):
        # Padding uses the final group's length and first byte.
        self.assertNotEqual(mix.mix_id("ABCDE"), mix.mix_id("ABCDF"))

    def test_ids_are_signed(self):
        self.assertLess(mix.mix_id("LOCAL.MIX"), 0)


class PackTests(unittest.TestCase):
    def members(self):
        return [
            mix.Member("GAMMA.SHP", b"gamma data"),
            mix.Member("ALPHA.INI", b"alpha"),
            mix.Member("BETA.AUD", b"beta data here"),
        ]

    def test_index_is_sorted_by_signed_id(self):
        archive = mix.pack(self.members())
        ids = [entry.id for entry in archive.entries]
        self.assertEqual(ids, sorted(ids))

    def test_members_read_back(self):
        archive = mix.pack(self.members())
        for member in self.members():
            self.assertEqual(archive.read_name(member.name), member.data)

    def test_offsets_tile_the_data(self):
        archive = mix.pack(self.members())
        offset = 0
        for entry in archive.entries:
            self.assertEqual(entry.offset, offset)
            offset += entry.size
        self.assertEqual(offset, len(archive.data))

    def test_packing_is_deterministic(self):
        first = mix.serialize_archive(mix.pack(self.members()))
        second = mix.serialize_archive(mix.pack(list(reversed(self.members()))))
        self.assertEqual(first, second)

    def test_written_archive_reads_back(self):
        archive = mix.pack(self.members())
        stream = io.BytesIO(mix.serialize_archive(archive))
        again = mix.read_archive_stream(stream)
        self.assertEqual(len(again.entries), 3)
        self.assertEqual(again.read_name("BETA.AUD"), b"beta data here")

    def test_empty_archive_uses_the_extended_header(self):
        # A zero file count would be mistaken for the extended-header marker.
        archive = mix.pack([])
        data = mix.serialize_archive(archive)
        self.assertTrue(archive.extended)
        again = mix.read_archive_stream(io.BytesIO(data))
        self.assertEqual(len(again.entries), 0)

    def test_duplicate_names_are_refused(self):
        with self.assertRaises(mix.MixFormatError):
            mix.pack([mix.Member("A.INI", b"one"), mix.Member("a.ini", b"two")])


class DirectoryPackTests(unittest.TestCase):
    def test_packs_files_and_unnamed_members(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "ALPHA.INI").write_bytes(b"alpha")
            (directory / "_id_0000007b.bin").write_bytes(b"unnamed")

            archive = mix.pack_directory(directory)

            self.assertEqual(len(archive.entries), 2)
            self.assertEqual(archive.read_name("ALPHA.INI"), b"alpha")
            entry = next(item for item in archive.entries if item.id == 0x7B)
            self.assertEqual(archive.read(entry), b"unnamed")
            ids = [item.id for item in archive.entries]
            self.assertEqual(ids, sorted(ids))


class BlowfishTests(unittest.TestCase):
    def test_canonical_vectors(self):
        vectors = [
            ("0000000000000000", "0000000000000000", "4EF997456198DD78"),
            ("FFFFFFFFFFFFFFFF", "FFFFFFFFFFFFFFFF", "51866FD5B85ECB8A"),
            ("3000000000000000", "1000000000000001", "7D856F9A613063F2"),
            ("0123456789ABCDEF", "1111111111111111", "61F9C3802281B096"),
            ("FEDCBA9876543210", "0123456789ABCDEF", "0ACEAB0FC6A0A28D"),
            ("7CA110454A1A6E57", "01A1D6D039776742", "59C68245EB05282B"),
            ("0113B970FD34F2CE", "059B5E0851CF143A", "48F4D0884C379918"),
        ]
        for key, plain, cipher in vectors:
            with self.subTest(key=key):
                engine = Blowfish(bytes.fromhex(key))
                self.assertEqual(engine.encrypt(bytes.fromhex(plain)).hex().upper(), cipher)
                self.assertEqual(engine.decrypt(bytes.fromhex(cipher)).hex().upper(), plain)

    def test_long_key_round_trip(self):
        engine = Blowfish(bytes(range(1, 57)))
        plain = bytes(range(64))
        self.assertEqual(engine.decrypt(engine.encrypt(plain)), plain)


class CryptTests(unittest.TestCase):
    def test_key_sizes_follow_the_public_key(self):
        self.assertEqual(crypt.BIT_PRECISION, 318)
        self.assertEqual(crypt.PLAIN_BLOCK_SIZE, 39)
        self.assertEqual(crypt.CRYPT_BLOCK_SIZE, 40)
        self.assertEqual(crypt.KEY_SOURCE_SIZE, 80)

    def test_encrypted_index_is_a_whole_number_of_blocks(self):
        for count in range(0, 40):
            size = crypt.encrypted_index_size(count)
            self.assertEqual(size % 8, 0)
            self.assertGreaterEqual(size, 6 + count * 12)
            self.assertLess(size - (6 + count * 12), 8)


if __name__ == "__main__":
    unittest.main()
