"""设计工具:多联画拆分 + 批量套图。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _png(size=(600, 400)) -> io.BytesIO:
    img = Image.new("RGB", size, (240, 240, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, size[0] - 50, size[1] - 50], fill=(60, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_split_horizontal_panels_3(client):
    r = client.post(
        "/api/design/split",
        data={"mode": "horizontal", "panels": 3},
        files={"file": ("x.png", _png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 3


def test_split_grid_2x3(client):
    r = client.post(
        "/api/design/split",
        data={"mode": "grid", "rows": 2, "cols": 3},
        files={"file": ("x.png", _png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 6


def test_mockup_batch_keys(client):
    r = client.post(
        "/api/design/mockup-batch",
        data={"templates": "tshirt,tote"},
        files={"file": ("x.png", _png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    mockups = r.json()["mockups"]
    assert set(mockups.keys()) == {"tshirt", "tote"}
