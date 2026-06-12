"""Perceptual hashing — pure Pillow, no numpy/scipy.

dHash (gradient) gives robust near-duplicate detection, which
covers the real POD risk: 盗图 / 重复铺货. Hamming distance ≈ visual similarity.
(Semantic IP — logos/characters — would need a model; out of scope by design.)
"""
from __future__ import annotations

from PIL import Image


def _gray(img: Image.Image, w: int, h: int) -> list[int]:
    g = img.convert("L").resize((w, h), Image.LANCZOS)
    return list(g.getdata())


def dhash(img: Image.Image, size: int = 8) -> str:
    px = _gray(img, size + 1, size)
    bits = []
    for row in range(size):
        for col in range(size):
            i = row * (size + 1) + col
            bits.append(1 if px[i] < px[i + 1] else 0)
    return _bits_to_hex(bits)


def color_sig(img: Image.Image, grid: int = 4) -> str:
    """Absolute color signature: grid×grid cells of actual mean RGB, hex-encoded.

    Unlike a relative bit-hash, this captures the real colorway, so two images with
    the same shape but different colors are correctly far apart.
    """
    small = img.convert("RGB").resize((grid, grid), Image.LANCZOS)
    out = bytearray()
    for (r, g, b) in small.getdata():
        out += bytes((r, g, b))
    return out.hex()


def color_distance(a: str, b: str) -> float:
    """Mean absolute per-channel color difference (0..255). Lower = closer color."""
    if not a or not b or len(a) != len(b):
        return 255.0
    ba, bb = bytes.fromhex(a), bytes.fromhex(b)
    return sum(abs(x - y) for x, y in zip(ba, bb, strict=False)) / len(ba)


def _bits_to_hex(bits: list[int]) -> str:
    val = 0
    for b in bits:
        val = (val << 1) | b
    return f"{val:0{len(bits)//4}x}"


def hamming(a: str, b: str) -> int:
    if len(a) != len(b):
        return max(len(a), len(b)) * 4
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def similarity(a: str, b: str) -> float:
    """0..1, 1 = identical (based on 64-bit dhash)."""
    bits = max(len(a), len(b)) * 4
    return 1.0 - hamming(a, b) / bits
