"""Read and write Tiberian Sun MIX archives.

Follow MixFileClass and CRC in OpenTS code/mixfile.cpp and code/crc.cpp.
Members are identified by filename hashes; the engine binary-searches their
sorted index.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable, Sequence

FLAG_DIGEST = 0x01
FLAG_ENCRYPTED = 0x02

DIGEST_SIZE = 20

_HEADER = struct.Struct("<hi")
_ENTRY = struct.Struct("<iii")
_EXTENDED_PREFIX = struct.Struct("<hh")


def mix_id(name: str) -> int:
    """Compute the engine filename hash without a terminator.

    Uppercase names with partial four-byte groups are padded with the group
    length, then its first byte repeated through the remaining positions.
    """

    data = name.upper().encode("ascii")
    remainder = len(data) & 3
    if remainder:
        data += bytes([remainder]) + bytes([data[len(data) - remainder]]) * (3 - remainder)

    value = zlib.crc32(data) & 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


@dataclass(frozen=True)
class Entry:
    """A stored member index entry."""

    id: int
    offset: int
    size: int


@dataclass
class Member:
    """A named member to pack."""

    name: str
    data: bytes

    @property
    def id(self) -> int:
        return mix_id(self.name)


@dataclass
class Archive:
    """An archive retaining header form, key, padding and data order for exact roundtrips."""

    entries: list[Entry]
    data: bytes
    extended: bool = True
    flags: int = 0
    key_source: bytes | None = None
    digest: bytes | None = None
    trailing: bytes = b""
    header_padding: bytes = b""

    def __len__(self) -> int:
        return len(self.entries)

    def find(self, name: str) -> Entry | None:
        wanted = mix_id(name)
        for entry in self.entries:
            if entry.id == wanted:
                return entry
        return None

    def read(self, entry: Entry) -> bytes:
        return self.data[entry.offset : entry.offset + entry.size]

    def read_name(self, name: str) -> bytes | None:
        entry = self.find(name)
        return None if entry is None else self.read(entry)


class MixFormatError(Exception):
    """An invalid MIX archive."""


def _read_exactly(stream: BinaryIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise MixFormatError(f"file ended after {len(data)} of {count} bytes")
    return data


def read_archive(path: Path | str) -> Archive:
    """Read an archive, decrypting its index if necessary."""

    path = Path(path)
    with path.open("rb") as stream:
        return read_archive_stream(stream)


def read_archive_stream(stream: BinaryIO) -> Archive:
    first, second = _EXTENDED_PREFIX.unpack(_read_exactly(stream, 4))

    extended = first == 0
    flags = second if extended else 0
    key_source: bytes | None = None
    header_padding = b""

    if extended:
        if flags & FLAG_ENCRYPTED:
            from . import crypt

            key_source = _read_exactly(stream, crypt.KEY_SOURCE_SIZE)
            count, data_size, entries, header_padding = crypt.read_encrypted_index(stream, key_source)
        else:
            count, data_size = _HEADER.unpack(_read_exactly(stream, _HEADER.size))
            entries = _read_entries(stream, count)
    else:
        # The four bytes already read are the start of a plain header.
        rest = _read_exactly(stream, _HEADER.size - 4)
        count, data_size = _HEADER.unpack(struct.pack("<hh", first, second) + rest)
        entries = _read_entries(stream, count)

    data = _read_exactly(stream, data_size)

    digest = None
    if flags & FLAG_DIGEST:
        digest = _read_exactly(stream, DIGEST_SIZE)

    trailing = stream.read()

    return Archive(
        entries=entries,
        data=data,
        extended=extended,
        flags=flags,
        key_source=key_source,
        digest=digest,
        trailing=trailing,
        header_padding=header_padding,
    )


def _read_entries(stream: BinaryIO, count: int) -> list[Entry]:
    if count < 0:
        raise MixFormatError(f"member count is negative: {count}")
    raw = _read_exactly(stream, count * _ENTRY.size)
    return [Entry(*_ENTRY.unpack_from(raw, index * _ENTRY.size)) for index in range(count)]


def write_archive(path: Path | str, archive: Archive) -> None:
    """Write an archive in its original form."""

    Path(path).write_bytes(serialize_archive(archive))


def serialize_archive(archive: Archive) -> bytes:
    """Serialize an archive."""

    parts: list[bytes] = []
    index = b"".join(_ENTRY.pack(entry.id, entry.offset, entry.size) for entry in archive.entries)
    header = _HEADER.pack(len(archive.entries), len(archive.data))

    if archive.extended:
        parts.append(_EXTENDED_PREFIX.pack(0, archive.flags))
        if archive.flags & FLAG_ENCRYPTED:
            from . import crypt

            if archive.key_source is None:
                raise MixFormatError("an encrypted index needs the key it was written under")
            parts.append(archive.key_source)
            parts.append(crypt.encrypt_index(header + index + archive.header_padding, archive.key_source))
        else:
            parts.append(header)
            parts.append(index)
    else:
        parts.append(header)
        parts.append(index)

    parts.append(archive.data)
    if archive.digest is not None:
        parts.append(archive.digest)
    parts.append(archive.trailing)

    return b"".join(parts)


def pack(members: Iterable[Member]) -> Archive:
    """Pack deterministically in signed-ID order, as engine binary search requires."""

    ordered = sorted(members, key=lambda member: (member.id, member.name.upper()))

    seen: dict[int, str] = {}
    for member in ordered:
        previous = seen.get(member.id)
        if previous is not None and previous.upper() != member.name.upper():
            raise MixFormatError(
                f"{member.name!r} and {previous!r} share the archive id {member.id}"
            )
        if previous is not None:
            raise MixFormatError(f"{member.name!r} was given twice")
        seen[member.id] = member.name

    entries: list[Entry] = []
    chunks: list[bytes] = []
    offset = 0
    for member in ordered:
        entries.append(Entry(member.id, offset, len(member.data)))
        chunks.append(member.data)
        offset += len(member.data)

    return Archive(entries=entries, data=b"".join(chunks), extended=True, flags=0)


def pack_directory(directory: Path | str) -> Archive:
    """Pack immediate files, preserving IDs encoded as _id_XXXXXXXX.ext."""

    directory = Path(directory)
    members: list[Member] = []
    unnamed: list[tuple[int, bytes]] = []

    for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_dir():
            raise MixFormatError(f"{entry} is a directory; an archive holds no directories")
        name = entry.name
        data = entry.read_bytes()
        if name.lower().startswith("_id_"):
            unnamed.append((_unnamed_id(name), data))
        else:
            _check_name(name)
            members.append(Member(name, data))

    archive = pack(members)
    if unnamed:
        archive = _merge_unnamed(archive, unnamed)
    return archive


def packed_id(filename: str) -> int:
    """Use an encoded _id_XXXXXXXX.ext ID or hash the filename."""

    if filename.lower().startswith("_id_"):
        return _unnamed_id(filename)
    return mix_id(filename)


def _unnamed_id(name: str) -> int:
    digits = name[4:12]
    try:
        value = int(digits, 16)
    except ValueError as error:
        raise MixFormatError(f"{name!r} does not carry an eight digit hexadecimal id") from error
    return value - 0x100000000 if value >= 0x80000000 else value


def _check_name(name: str) -> None:
    try:
        name.encode("ascii")
    except UnicodeEncodeError as error:
        raise MixFormatError(f"{name!r} is not an ASCII name, so the engine cannot hash it") from error


def _merge_unnamed(archive: Archive, unnamed: Sequence[tuple[int, bytes]]) -> Archive:
    pairs: list[tuple[int, bytes]] = [
        (entry.id, archive.read(entry)) for entry in archive.entries
    ]
    pairs.extend(unnamed)
    pairs.sort(key=lambda pair: pair[0])

    ids = [identifier for identifier, _ in pairs]
    if len(set(ids)) != len(ids):
        raise MixFormatError("two members share an archive id")

    entries: list[Entry] = []
    chunks: list[bytes] = []
    offset = 0
    for identifier, data in pairs:
        entries.append(Entry(identifier, offset, len(data)))
        chunks.append(data)
        offset += len(data)

    return Archive(entries=entries, data=b"".join(chunks), extended=True, flags=0)


def is_archive_name(name: str) -> bool:
    return name.lower().endswith(".mix")
