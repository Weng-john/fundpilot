"""Minimal Ethereum ABI helpers: Keccak-256, function selectors, static word decoding.

Only what is needed to read lending-protocol state via ``eth_call``: functions taking an
``address`` and returning static values (uint/bool/address). No dependency on web3.
"""

from __future__ import annotations

from collections.abc import Sequence

_RC = [
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
_MASK = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _keccak_f(a: list[list[int]]) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Original Keccak-256 (Ethereum), not NIST SHA3-256 (different padding)."""
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * (-len(msg) % rate)
    msg[-1] |= 0x80
    state = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off : off + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[8 * i : 8 * i + 8], "little")
            state[i % 5][i // 5] ^= lane
        _keccak_f(state)
    out = b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


def selector(signature: str) -> str:
    """4-byte function selector as ``0x`` hex, e.g. ``transfer(address,uint256)``."""
    return "0x" + keccak256(signature.encode()).hex()[:8]


def encode_address(address: str) -> str:
    raw = address.lower().removeprefix("0x")
    if len(raw) != 40 or any(c not in "0123456789abcdef" for c in raw):
        raise ValueError(f"invalid address: {address!r}")
    return raw.rjust(64, "0")


def call_data(signature: str, *addresses: str) -> str:
    return selector(signature) + "".join(encode_address(a) for a in addresses)


def decode_words(result: str, count: int) -> list[int]:
    """Split an ``eth_call`` result into ``count`` 32-byte unsigned words."""
    raw = result.removeprefix("0x")
    if len(raw) < 64 * count:
        raise ValueError(f"expected {count} words, got {len(raw) // 64}")
    return [int(raw[64 * i : 64 * (i + 1)], 16) for i in range(count)]


def word_to_address(word: int) -> str:
    return "0x" + f"{word:064x}"[-40:]


def encode_words(values: Sequence[int]) -> str:
    """Inverse of :func:`decode_words` (used by tests and mocks)."""
    return "0x" + "".join(f"{v:064x}" for v in values)
