"""Blowfish for encrypted MIX indexes.

Derive tables from hexadecimal digits of pi and check published constants
at import time.
"""

from __future__ import annotations

import struct

BLOCK_SIZE = 8
KEY_SIZE = 56

_ROUNDS = 16
_WORDS_NEEDED = 18 + 4 * 256
_MASK = 0xFFFFFFFF


def _pi_hex_digits(count: int) -> int:
    """Return ``count`` hexadecimal digits of pi using integer Machin sums.

    Guard digits keep the final requested digit exact.
    """

    guard = 16
    scale = 16 ** (count + guard)

    def arctan_inverse(x: int) -> int:
        total = scale // x
        term = total
        divisor = 1
        square = x * x
        while term:
            term //= square
            divisor += 2
            if (divisor // 2) % 2:
                total -= term // divisor
            else:
                total += term // divisor
        return total

    pi = 16 * arctan_inverse(5) - 4 * arctan_inverse(239)
    return (pi % scale) // (16 ** guard)


def _pi_words(count: int) -> list[int]:
    digits = _pi_hex_digits(count * 8)
    words = []
    for index in range(count):
        shift = 4 * 8 * (count - index - 1)
        words.append((digits >> shift) & _MASK)
    return words


_CONSTANTS = _pi_words(_WORDS_NEEDED)

# Check the derived table against published endpoint constants.
assert _CONSTANTS[0] == 0x243F6A88, "pi expansion does not match Blowfish's P array"
assert _CONSTANTS[18] == 0xD1310BA6, "pi expansion does not match Blowfish's S boxes"
assert _CONSTANTS[-1] == 0x3AC372E6, "pi expansion does not match Blowfish's S boxes"

_P_INIT = tuple(_CONSTANTS[:18])
_S_INIT = tuple(tuple(_CONSTANTS[18 + box * 256 : 18 + (box + 1) * 256]) for box in range(4))


class Blowfish:
    """Block-at-a-time Blowfish in electronic codebook mode."""

    __slots__ = ("_p", "_s")

    def __init__(self, key: bytes):
        if not key:
            raise ValueError("a Blowfish key cannot be empty")

        self._p = list(_P_INIT)
        self._s = [list(box) for box in _S_INIT]

        for index in range(18):
            word = 0
            for step in range(4):
                word = ((word << 8) | key[(index * 4 + step) % len(key)]) & _MASK
            self._p[index] ^= word

        left = right = 0
        for index in range(0, 18, 2):
            left, right = self._encrypt_block(left, right)
            self._p[index], self._p[index + 1] = left, right

        for box in self._s:
            for index in range(0, 256, 2):
                left, right = self._encrypt_block(left, right)
                box[index], box[index + 1] = left, right

    def _f(self, word: int) -> int:
        s = self._s
        return (
            ((s[0][(word >> 24) & 0xFF] + s[1][(word >> 16) & 0xFF]) & _MASK)
            ^ s[2][(word >> 8) & 0xFF]
        ) + s[3][word & 0xFF] & _MASK

    def _encrypt_block(self, left: int, right: int) -> tuple[int, int]:
        p = self._p
        for index in range(_ROUNDS):
            left ^= p[index]
            right ^= self._f(left)
            left, right = right, left
        left, right = right, left
        right ^= p[_ROUNDS]
        left ^= p[_ROUNDS + 1]
        return left, right

    def _decrypt_block(self, left: int, right: int) -> tuple[int, int]:
        p = self._p
        for index in range(_ROUNDS + 1, 1, -1):
            left ^= p[index]
            right ^= self._f(left)
            left, right = right, left
        left, right = right, left
        right ^= p[1]
        left ^= p[0]
        return left, right

    def encrypt(self, data: bytes) -> bytes:
        return self._apply(data, self._encrypt_block)

    def decrypt(self, data: bytes) -> bytes:
        return self._apply(data, self._decrypt_block)

    @staticmethod
    def _apply(data: bytes, transform) -> bytes:
        if len(data) % BLOCK_SIZE:
            raise ValueError("Blowfish works in whole blocks of eight bytes")
        out = bytearray(len(data))
        for offset in range(0, len(data), BLOCK_SIZE):
            left, right = struct.unpack_from(">II", data, offset)
            left, right = transform(left, right)
            struct.pack_into(">II", out, offset, left, right)
        return bytes(out)
