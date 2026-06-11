"""Batch 6:ip_guard verbose 分级(P2-2)。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw


def _png(size=(120, 100)) -> io.BytesIO:
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).ellipse([20, 20, 90, 80], fill=(200, 60, 40))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------- ip_guard verbose 分级(P2-2) ----------
def test_ipguard_default_hides_details(client, auth_headers, tool_result):
    r = client.post("/api/ip-guard/scan", headers=auth_headers,
                    data={"title": "official BrandX shirt"},
                    files={"file": ("x.png", _png(), "image/png")})
    body = tool_result(auth_headers, r)  # 后台作业 → 轮询
    assert "risk" in body and "match_count" in body
    assert "matches" not in body  # 默认不回明细


def test_ipguard_verbose_shows_matches(client, auth_headers, tool_result):
    r = client.post("/api/ip-guard/scan", headers=auth_headers,
                    data={"title": "official BrandX shirt", "verbose": "true"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert "matches" in tool_result(auth_headers, r)


