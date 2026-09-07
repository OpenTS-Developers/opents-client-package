"""Read and reproduce encrypted MIX indexes.

The archive stores a Blowfish key encrypted with the game public key. Follow
PKStraw/PKey/BlowStraw in code/pkstraw.cpp, code/pk.cpp and code/mpmath.cpp.
New packages are unencrypted; encryption support enables exact roundtrips.
"""

from __future__ import annotations

import base64
import struct
from typing import BinaryIO

from .blowfish import BLOCK_SIZE, KEY_SIZE, Blowfish

# Game public key from code/_pk.cpp.
PUBLIC_KEY_BLOB = "AihRvNoIbTn85FZRYNZRcT+i6KpU+maCsEqr3Q5q+LDB5tH7Tz2qQ38V"

# PKey::Fast_Exponent is implicit, not stored.
FAST_EXPONENT = 65537


def _decode_modulus(blob: str) -> int:
    data = base64.b64decode(blob)
    if not data or data[0] != 0x02:
        raise ValueError("the public key is not a DER encoded integer")

    length = data[1]
    index = 2
    if length & 0x80:
        count = length & 0x7F
        if count > 2:
            raise ValueError("the public key's length field is too long")
        length = 0
        for _ in range(count):
            length = (length << 8) | data[index]
            index += 1

    return int.from_bytes(data[index : index + length], "big")


MODULUS = _decode_modulus(PUBLIC_KEY_BLOB)

# Block sizes follow PKey::BitPrecision.
BIT_PRECISION = MODULUS.bit_length() - 1
PLAIN_BLOCK_SIZE = (BIT_PRECISION - 1) // 8
CRYPT_BLOCK_SIZE = PLAIN_BLOCK_SIZE + 1

_KEY_BLOCKS = ((KEY_SIZE - 1) // PLAIN_BLOCK_SIZE) + 1

# PKStraw encrypted and plain key lengths.
KEY_SOURCE_SIZE = _KEY_BLOCKS * CRYPT_BLOCK_SIZE
PLAIN_KEY_SIZE = _KEY_BLOCKS * PLAIN_BLOCK_SIZE

_HEADER = struct.Struct("<hi")
_ENTRY_SIZE = 12


def blowfish_key(key_source: bytes) -> bytes:
    """Decode the archive key source with the game public key.

    Engine buffers store each integer block little-endian, regardless of the
    public key encoding.
    """

    if len(key_source) != KEY_SOURCE_SIZE:
        raise ValueError(f"a key source is {KEY_SOURCE_SIZE} bytes, not {len(key_source)}")

    plain = bytearray()
    for offset in range(0, KEY_SOURCE_SIZE, CRYPT_BLOCK_SIZE):
        block = int.from_bytes(key_source[offset : offset + CRYPT_BLOCK_SIZE], "little")
        result = pow(block, FAST_EXPONENT, MODULUS)
        plain += result.to_bytes(PLAIN_BLOCK_SIZE, "little")

    return bytes(plain[:KEY_SIZE])


def encrypted_index_size(count: int) -> int:
    """Return the stored size of an index holding ``count`` members."""

    plain = _HEADER.size + count * _ENTRY_SIZE
    return ((plain + BLOCK_SIZE - 1) // BLOCK_SIZE) * BLOCK_SIZE


def read_encrypted_index(stream: BinaryIO, key_source: bytes):
    """Decrypt the index and retain its original padding.

    The first decrypted block supplies the member count and remaining size.
    """

    from .mix import Entry, MixFormatError

    cipher = Blowfish(blowfish_key(key_source))

    first = stream.read(BLOCK_SIZE)
    if len(first) != BLOCK_SIZE:
        raise MixFormatError("file ended inside the encrypted index")
    plain = bytearray(cipher.decrypt(first))

    count, data_size = _HEADER.unpack_from(plain, 0)
    if count < 0:
        raise MixFormatError(f"member count is negative: {count}")

    total = encrypted_index_size(count)
    remaining = total - BLOCK_SIZE
    if remaining:
        rest = stream.read(remaining)
        if len(rest) != remaining:
            raise MixFormatError("file ended inside the encrypted index")
        plain += cipher.decrypt(rest)

    entries = [
        Entry(*struct.unpack_from("<iii", plain, _HEADER.size + index * _ENTRY_SIZE))
        for index in range(count)
    ]
    padding = bytes(plain[_HEADER.size + count * _ENTRY_SIZE :])

    return count, data_size, entries, padding


def encrypt_index(plain: bytes, key_source: bytes) -> bytes:
    """Encrypt an index with its original padding."""

    cipher = Blowfish(blowfish_key(key_source))
    if len(plain) % BLOCK_SIZE:
        raise ValueError("an index is encrypted in whole blocks")
    return cipher.encrypt(plain)
