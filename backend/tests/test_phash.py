"""感知哈希:dhash / color_distance / similarity。"""
from __future__ import annotations

from PIL import Image, ImageDraw

from app.services import phash


def _solid(color: tuple[int, int, int], size=(128, 128)) -> Image.Image:
    return Image.new("RGB", size, color)


def _circle(fill: tuple[int, int, int], bg=(255, 255, 255), size=(128, 128)) -> Image.Image:
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    w, h = size
    r = min(w, h) // 4
    d.ellipse([w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r], fill=fill)
    return img


def test_same_image_dhash_distance_zero():
    img = _circle((10, 10, 10))
    h1 = phash.dhash(img)
    h2 = phash.dhash(img.copy())
    assert phash.hamming(h1, h2) == 0


def test_different_color_color_distance_large():
    red = phash.color_sig(_solid((255, 0, 0)))
    blue = phash.color_sig(_solid((0, 0, 255)))
    # 纯红 vs 纯蓝:每个 cell 都差很大
    assert phash.color_distance(red, blue) > 80.0


def test_similarity_identical_is_one():
    h = phash.dhash(_circle((0, 0, 0)))
    assert phash.similarity(h, h) == 1.0
