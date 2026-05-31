"""Batch 6:四方连续图(离线)+ ip_guard verbose 分级 + gpt-image key 路径计费(mock)。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw


def _png(size=(120, 100)) -> io.BytesIO:
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).ellipse([20, 20, 90, 80], fill=(200, 60, 40))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------- 四方连续图(离线真实) ----------
def test_seamless_service_dimensions():
    from app.services.seamless import mirror_block, seamless_pattern
    img = Image.open(_png((100, 80)))
    blk = mirror_block(img)
    assert blk.size == (200, 160) and blk.mode == "RGBA"
    pat = seamless_pattern(img, repeat=2)
    assert pat.size == (400, 320)  # 2x2 镜像块 再 2x2 平铺


def test_seamless_endpoint_charges_and_returns(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/design-tools/seamless", headers=auth_headers,
                    data={"repeat": 2}, files={"file": ("x.png", _png((100, 80)), "image/png")})
    assert r.status_code == 200, r.text
    got = client.get(r.json()["image_url"])
    assert got.status_code == 200 and Image.open(io.BytesIO(got.content)).size == (400, 320)
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0 - 2  # process 扣 2


def test_seamless_bad_repeat_refunds(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/design-tools/seamless", headers=auth_headers,
                    data={"repeat": 99}, files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 400
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


# ---------- ip_guard verbose 分级(P2-2) ----------
def test_ipguard_default_hides_details(client, auth_headers):
    r = client.post("/api/ip-guard/scan", headers=auth_headers,
                    data={"title": "official BrandX shirt"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "risk" in body and "match_count" in body
    assert "matches" not in body  # 默认不回明细


def test_ipguard_verbose_shows_matches(client, auth_headers):
    r = client.post("/api/ip-guard/scan", headers=auth_headers,
                    data={"title": "official BrandX shirt", "verbose": "true"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert "matches" in r.json()


# ---------- gpt-image KEY 路径计费(P2-3:之前无 key 无法测的成功/失败分支,用 mock 覆盖) ----------
def test_tryon_with_key_success_charges(client, auth_headers, monkeypatch):
    from app.config import settings
    from app.ai import openai_image
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit",
                        lambda self, image, prompt, mask=None, size="auto", background="auto":
                        Image.new("RGBA", (64, 64), (10, 20, 30, 255)))
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/studio/tryon", headers=auth_headers,
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["image_url"]
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0 - 4, f"有 key 成功应扣 4(edit): {bal0}->{bal1}"


def test_tryon_with_key_failure_refunds(client, auth_headers, monkeypatch):
    from app.config import settings
    from app.ai import openai_image
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    def _boom(self, image, prompt, mask=None, size="auto", background="auto"):
        raise RuntimeError("upstream 500")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _boom)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/studio/tryon", headers=auth_headers,
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 502, r.text
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0, f"有 key 失败应退点: {bal0}->{bal1}"
