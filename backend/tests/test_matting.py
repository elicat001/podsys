"""一键抠图(/api/matting)测试:纯去背景 → 透明 PNG,异步丢任务中心,产物入库(可回收/计存储)。

mock cutout_best 避免在测试里真跑 rembg(下模型/慢);只验管线:鉴权 / 扣点 / 退点 / 异步 / 入库。
"""
from __future__ import annotations

from PIL import Image

from app.db import Base, engine

Base.metadata.create_all(engine)


def _f(png):
    return {"file": ("a.png", png(), "image/png")}


def test_matting_unauth_401(client, png):
    assert client.post("/api/matting", files=_f(png)).status_code == 401


def test_matting_async_transparent_and_charges(client, auth_headers, png, monkeypatch, tool_result):
    import app.ai.matting as matting
    fake = Image.new("RGBA", (40, 40), (10, 20, 30, 255))
    monkeypatch.setattr(matting, "cutout_best", lambda im: fake)
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/matting", headers=auth_headers, files=_f(png))
    body = tool_result(auth_headers, r)  # 后台作业 → 轮询
    assert "image_url" in body and body["image_url"].startswith("/files/")
    assert client.get(body["image_url"]).status_code == 200
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before - 2  # process 扣 2

    # 产物已入库(可在回收站/搜图可见),名为"一键抠图"
    assets = client.get("/api/space/assets", headers=auth_headers).json()["items"]
    assert any(a["name"] == "一键抠图" for a in assets)


def test_matting_bad_image_refunds(client, auth_headers):
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/matting", headers=auth_headers,
                    files={"file": ("a.png", b"not-an-image", "image/png")})
    assert r.status_code == 400
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before  # 读图失败已退点
