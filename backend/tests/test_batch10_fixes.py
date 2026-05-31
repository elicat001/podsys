"""Batch 10 评审整改回归:P1-1 回收站资产在 search/overview 也被排除;P2-1 set_risk 白名单。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw


def _png(seed=0) -> io.BytesIO:
    img = Image.new("RGB", (256, 256), (255, 255, 255))
    d = ImageDraw.Draw(img)
    step = 6 + seed % 3
    for y in range(0, 256, step):
        for x in range(0, 256, step):
            if ((x // step) + (y // step) + seed) % 2 == 0:
                d.rectangle([x, y, x + step, y + step], fill=(0, 0, 0))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


def _add_asset(client, H, seed):
    return client.post("/api/assets", headers=H,
                       files={"file": (f"a{seed}.png", _png(seed), "image/png")}).json()["asset_id"]


def test_trashed_asset_excluded_from_overview_and_search(client, auth_headers):
    H = auth_headers
    aid = _add_asset(client, H, 1)
    _add_asset(client, H, 9)
    ov0 = client.get("/api/me/overview", headers=H).json()["assets"]
    # 同图搜索能命中(回收前)
    client.post(f"/api/space/assets/{aid}/trash", headers=H)
    ov1 = client.get("/api/me/overview", headers=H).json()["assets"]
    assert ov1 == ov0 - 1, f"overview.assets 应排除回收站: {ov0}->{ov1}"
    # 以图搜图:用被回收的那张图搜,不应把它排为命中
    r = client.post("/api/search/by-image", headers=H,
                    files={"file": ("q.png", _png(1), "image/png")})
    ids = [m["asset_id"] for m in r.json()["results"]]
    assert aid not in ids, "回收站资产不应出现在以图搜图结果"


def test_batch_set_risk_rejects_invalid_value(client, auth_headers):
    pid = client.post("/api/products", headers=auth_headers,
                      json={"title": "T", "price": 9.9}).json()["product_id"]
    r = client.post("/api/products/batch", headers=auth_headers,
                    json={"action": "set_risk", "product_ids": [pid], "value": "garbage"})
    assert r.status_code == 400, r.text
    # 合法值通过
    r2 = client.post("/api/products/batch", headers=auth_headers,
                     json={"action": "set_risk", "product_ids": [pid], "value": "high"})
    assert r2.status_code == 200 and r2.json()["affected"] == 1
