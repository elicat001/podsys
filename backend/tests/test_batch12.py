"""Batch 12 深度审计整改回归:产物入库打通 + 真超分 + 侵权库扩充。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw


def _png(seed=0, size=(400, 400)) -> io.BytesIO:
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([90, 90, 310, 310], fill=(200 - seed * 20 % 200, 60, 40 + seed * 30 % 200))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------- 根因①:工具产物进素材库(我的空间/搜图/商品库不再永远空) ----------
def test_process_output_lands_in_library(client, auth_headers):
    before = client.get("/api/space/quota", headers=auth_headers).json()
    ov0 = client.get("/api/me/overview", headers=auth_headers).json()["assets"]
    r = client.post("/api/process", headers=auth_headers, data={"template": "tshirt"},
                    files={"file": ("x.png", _png(1), "image/png")})
    assert r.status_code == 200, r.text
    after = client.get("/api/space/quota", headers=auth_headers).json()
    ov1 = client.get("/api/me/overview", headers=auth_headers).json()["assets"]
    assert after["used_bytes"] > before["used_bytes"], "工具产物应进库占用存储"
    assert ov1 == ov0 + 1, "我的空间资产数应 +1"


def test_generate_offline_lands_in_library(client, auth_headers):
    ov0 = client.get("/api/me/overview", headers=auth_headers).json()["assets"]
    r = client.post("/api/generate", headers=auth_headers, data={"prompt": "galaxy cat", "size": "512x512"})
    assert r.status_code == 200, r.text
    ov1 = client.get("/api/me/overview", headers=auth_headers).json()["assets"]
    assert ov1 == ov0 + 1


# ---------- 根因②:真·超分(放大输入,非整条流水线出套图) ----------
def test_upscale_actually_enlarges(client, auth_headers):
    r = client.post("/api/image-tools/upscale", headers=auth_headers,
                    data={"scale": "2"}, files={"file": ("x.png", _png(size=(200, 200)), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["width"] == 400 and body["height"] == 400, "2x 应放大到 400x400"
    got = client.get(body["image_url"])
    assert got.status_code == 200 and Image.open(io.BytesIO(got.content)).size == (400, 400)


def test_upscale_target_resolution(client, auth_headers):
    # 目标分辨率「2K」:256px 长边 → 长边 2048(scale=8)
    r = client.post("/api/image-tools/upscale", headers=auth_headers,
                    data={"target": "2k"}, files={"file": ("x.png", _png(size=(256, 256)), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["width"] == 2048 and r.json()["height"] == 2048, "2K 应放大到长边 2048"
    # 已 ≥ 目标 → 不缩小:2000px 选「1K」仍是 2000
    r2 = client.post("/api/image-tools/upscale", headers=auth_headers,
                     data={"target": "1k"}, files={"file": ("x.png", _png(size=(2000, 2000)), "image/png")})
    assert r2.status_code == 200, r2.text
    assert r2.json()["width"] == 2000, "已大于目标 → 不缩小"


# ---------- 侵权库扩充 + 关键词命中 ----------
def test_ipguard_library_expanded_and_hits(client, auth_headers, tool_result):
    lib = client.get("/api/ip-guard/library", headers=auth_headers).json()
    assert lib["total"] >= 18, f"侵权库应已扩充: {lib['total']}"
    r = client.post("/api/ip-guard/scan", headers=auth_headers,
                    data={"title": "cute baby yoda mandalorian shirt", "verbose": "true"},
                    files={"file": ("x.png", _png(), "image/png")})
    body = tool_result(auth_headers, r)  # 后台作业 → 轮询
    assert body["risk"] == "high" and len(body["matches"]) >= 1, "应命中 Star Wars 关键词"
