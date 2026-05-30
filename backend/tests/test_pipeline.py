"""主流程:上传 → 提取 → 套图 → 导出生产文件。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.services.export import cm_to_px


def _png(size=(400, 400)) -> io.BytesIO:
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    w, h = size
    r = min(w, h) // 4
    d.ellipse([w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r], fill=(200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_process_returns_urls_and_meta(client, auth_headers):
    r = client.post(
        "/api/process",
        headers=auth_headers,
        data={"template": "tshirt", "upscale": 1.0, "width_cm": 30, "height_cm": 40, "dpi": 300},
        files={"file": ("x.png", _png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("print_url", "mockup_url", "production_url", "production_meta"):
        assert k in body, body

    meta = body["production_meta"]
    assert meta["dpi"] == 300
    assert meta["width_cm"] == 30
    assert meta["height_cm"] == 40
    # 物理尺寸换算正确
    assert meta["width_px"] == cm_to_px(30, 300)
    assert meta["height_px"] == cm_to_px(40, 300)


def test_process_urls_serve_png(client, auth_headers):
    r = client.post(
        "/api/process",
        headers=auth_headers,
        data={"template": "tshirt", "dpi": 300},
        files={"file": ("x.png", _png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("print_url", "mockup_url", "production_url"):
        url = body[key]
        got = client.get(url)
        assert got.status_code == 200, f"{key} -> {url}: {got.status_code}"
        # 能被 PIL 当作 PNG 打开
        im = Image.open(io.BytesIO(got.content))
        im.load()
        assert im.format == "PNG"
