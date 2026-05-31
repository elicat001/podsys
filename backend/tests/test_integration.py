"""集成层:鉴权 + 计费扣点 + 采集 URL 升级(Tech Lead 收口后的端到端契约)。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw


def _png() -> io.BytesIO:
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(img).ellipse([80, 80, 220, 220], fill=(200, 30, 30))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


def test_process_requires_auth(client):
    r = client.post("/api/process", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


def test_process_charges_credits(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/process", headers=auth_headers,
                    data={"template": "tshirt"}, files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0 - 2, f"process 应扣 2 点: {bal0}->{bal1}"


def test_insufficient_credits_returns_402(client, auth_headers):
    # 把余额烧到不足:process 每次扣 2,初始 100,跑 50 次后为 0,第 51 次应 402
    for _ in range(50):
        client.post("/api/process", headers=auth_headers,
                    data={"template": "tshirt"}, files={"file": ("x.png", _png(), "image/png")})
    r = client.post("/api/process", headers=auth_headers,
                    data={"template": "tshirt"}, files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 402, r.text


def test_topup_increases_balance(client, auth_headers):
    before = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/billing/topup", headers=auth_headers, json={"amount": 50})
    assert r.status_code == 200, r.text
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == before + 50


def test_collect_requires_auth(client):
    r = client.post("/api/collect", json={"url": "https://www.temu.com/x.jpg"})
    assert r.status_code == 401


def test_collect_upgrades_amazon_url(client, auth_headers):
    r = client.post("/api/collect", headers=auth_headers, json={
        "url": "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX466_.jpg"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["platform"] == "amazon"
    assert body["hires_url"] == "https://m.media-amazon.com/images/I/71abcXYZ.jpg"


def test_jobs_require_auth(client):
    assert client.get("/api/jobs").status_code == 401


def test_generate_offline_produces_image(client, auth_headers):
    """新契约(batch11):无 OpenAI key 时 generate 走本地程序化引擎 -> 200 真实图,正常扣 5。"""
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/generate", headers=auth_headers, data={"prompt": "galaxy cat", "size": "512x512"})
    assert r.status_code == 200, r.text
    assert r.json()["image_url"].endswith(".png")
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0 - 5, f"generate 扣 5: {bal0}->{bal1}"
