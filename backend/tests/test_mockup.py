"""商品套图(/api/mockup)测试:单张/批量、配色、按张扣点、退点、越权/参数校验。

纯本地 Pillow,无 AI。验证管线与计费,不验证视觉效果。
"""
from __future__ import annotations

from PIL import Image

from app.db import Base, engine
from app.services import mockup

Base.metadata.create_all(engine)


def _balance(client, headers) -> int:
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["credits"]


def _files(png):
    return {"file": ("design.png", png(), "image/png")}


# ---- 模板列表带配色 -------------------------------------------------------
def test_templates_expose_colors(client):
    r = client.get("/api/templates")
    assert r.status_code == 200, r.text
    items = r.json()
    ts = next(t for t in items if t["id"] == "tshirt")
    assert "colors" in ts and "white" in ts["colors"] and "black" in ts["colors"]
    assert ts["default_color"] == "white"


def test_mug_template_listed(client):
    """水杯模板出现在列表里(前端下拉数据驱动,加模板即自动可选)。"""
    r = client.get("/api/templates")
    assert r.status_code == 200, r.text
    mug = next((t for t in r.json() if t["id"] == "mug"), None)
    assert mug is not None and mug["label"] == "水杯"
    assert mug["default_color"] == "white" and set(mug["colors"]) == {"white", "black"}


# ---- 单张套图 -------------------------------------------------------------
def test_render_unauth_401(client, png):
    r = client.post("/api/mockup/render", files=_files(png))
    assert r.status_code == 401


def test_render_with_color(client, auth_headers, png, tool_result):
    before = _balance(client, auth_headers)
    r = client.post("/api/mockup/render", headers=auth_headers,
                    files=_files(png), data={"template": "tshirt", "color": "black"})
    res = tool_result(auth_headers, r)
    assert res["template"] == "tshirt" and res["color"] == "black"
    dl = client.get(res["image_url"])
    assert dl.status_code == 200 and len(dl.content) > 0
    assert _balance(client, auth_headers) == before - 1  # asset=1


def test_render_default_color(client, auth_headers, png, tool_result):
    r = client.post("/api/mockup/render", headers=auth_headers,
                    files=_files(png), data={"template": "tote"})
    assert tool_result(auth_headers, r)["color"] is None


def test_render_mug_offline_local_engine(client, auth_headers, png, tool_result):
    """水杯套图:离线(conftest 强制无 key)走本地引擎,产物可下载,engine=local。"""
    r = client.post("/api/mockup/render", headers=auth_headers,
                    files=_files(png), data={"template": "mug", "color": "black"})
    res = tool_result(auth_headers, r)
    assert res["template"] == "mug" and res["color"] == "black"
    assert res["engine"] == "local"  # 测试环境强制离线 → 回退本地合成
    assert client.get(res["image_url"]).status_code == 200


def test_render_bad_template_refund_400(client, auth_headers, png):
    before = _balance(client, auth_headers)
    r = client.post("/api/mockup/render", headers=auth_headers,
                    files=_files(png), data={"template": "spaceship"})
    assert r.status_code == 400
    assert _balance(client, auth_headers) == before


def test_render_bad_color_refund_400(client, auth_headers, png):
    before = _balance(client, auth_headers)
    r = client.post("/api/mockup/render", headers=auth_headers,
                    files=_files(png), data={"template": "tshirt", "color": "neon"})
    assert r.status_code == 400
    assert _balance(client, auth_headers) == before


# ---- service 直测:配色确实改变了产品本体像素 ----------------------------
def test_service_color_changes_body():
    design = Image.new("RGBA", (200, 200), (0, 200, 0, 255))
    white = mockup.render_mockup(design, "tshirt", "white")
    black = mockup.render_mockup(design, "tshirt", "black")
    # 取胸口印区之外、肩部一点的像素(本体但非印花),白衣应明显比黑衣亮
    px_w = white.convert("RGB").getpixel((500, 250))
    px_b = black.convert("RGB").getpixel((500, 250))
    assert sum(px_w) > sum(px_b) + 200
