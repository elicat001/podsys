"""印花提取测试。全本地:cloth-seg 分衣服 + 去主导布料色;无衣服→整图去底。"""
from __future__ import annotations

from PIL import Image, ImageDraw

from app.services.design_extract import extract_design, extract_on_fabric


def _design_on_white(bg=(255, 255, 255)) -> Image.Image:
    img = Image.new("RGB", (200, 200), bg)
    ImageDraw.Draw(img).rectangle([70, 70, 130, 130], fill=(220, 40, 40))  # 居中红色图案
    return img


# ---------- 本地去布料(纯函数,离线) ----------

def test_extract_on_fabric_removes_white_keeps_design():
    out = extract_on_fabric(_design_on_white())
    assert out.mode == "RGBA"
    a = out.getchannel("A")
    w, h = out.size
    # 四角(白布料)应透明,中心(图案)应不透明
    assert a.getpixel((0, 0)) <= 16
    assert a.getpixel((w // 2, h // 2)) >= 200


def test_extract_on_fabric_handles_dark_fabric():
    # 关键:不写死去白色,深色(黑)布料也要能去掉、保留浅色图案
    img = Image.new("RGB", (200, 200), (18, 18, 18))  # 黑布料
    ImageDraw.Draw(img).rectangle([70, 70, 130, 130], fill=(240, 240, 240))  # 白图案
    out = extract_on_fabric(img)
    a = out.getchannel("A")
    w, h = out.size
    assert a.getpixel((0, 0)) <= 16          # 黑布料角落 → 透明
    assert a.getpixel((w // 2, h // 2)) >= 200  # 白图案中心 → 保留


def test_extract_on_fabric_keeps_interior_color():
    out = extract_on_fabric(_design_on_white())
    # 红色图案像素应保留(非全透明)
    assert out.getchannel("A").getbbox() is not None


# ---------- 编排(设计图:cloth-seg 找不到衣服 → 整图去底) ----------

def test_extract_design_on_design_image():
    design, meta = extract_design(_design_on_white())
    assert design.mode == "RGBA"
    assert "method" in meta
    assert design.getchannel("A").getbbox() is not None  # 抠出了内容
    # 自动裁剪到内容:裁完后内容应贴满四边(bbox == 整图;不再用绝对尺寸,因为有超分会放大)
    assert design.getchannel("A").getbbox() == (0, 0, design.size[0], design.size[1])


def test_extract_design_rejects_oversized(monkeypatch):
    import app.services.design_extract as de
    monkeypatch.setattr(de, "_MAX_PX", 100)  # 临时把上限调到很小
    try:
        de.extract_design(_design_on_white())
        assert False, "超大图应抛 ValueError"
    except ValueError:
        pass


# ---------- 接口 ----------

def test_print_extract_endpoint(client, auth_headers, png):
    resp = client.post(
        "/api/print-extract", headers=auth_headers,
        files={"file": ("d.png", png(shape="rect"), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "image_url" in body and "method" in body


def test_print_extract_requires_auth(client, png):
    resp = client.post("/api/print-extract", files={"file": ("d.png", png(), "image/png")})
    assert resp.status_code == 401


def test_print_extract_charges_points(client, auth_headers, png):
    before = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    client.post("/api/print-extract", headers=auth_headers,
                files={"file": ("d.png", png(shape="rect"), "image/png")})
    after = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert after == before - 2  # process 扣 2 点
