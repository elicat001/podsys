"""Batch 5 评审整改回归:P0-1 按张计费 / P1-4 坏图退点 / P1-1 像素上限。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw


def _png(size=(300, 300)) -> io.BytesIO:
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).ellipse([60, 60, 240, 240], fill=(30, 120, 200))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------- P0-1:variants 按张计费,且失败按张退点(笔数对齐) ----------
def test_variants_charges_per_image(client, auth_headers, monkeypatch, tool_result):
    # 让 AI 成功:monkeypatch make_variants 返回 n 张占位图(worker 内同模块可见)
    from app.services import design_tools
    monkeypatch.setattr(design_tools, "make_variants",
                        lambda img, n, prompt="", prefer_local=False: [Image.new("RGBA", (32, 32)) for _ in range(n)])
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/design-tools/variants", headers=auth_headers,
                    data={"n": 3}, files={"file": ("x.png", _png(), "image/png")})
    assert len(tool_result(auth_headers, r)["images"]) == 3  # 后台作业 → 轮询 3 张
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0 - 3 * 4, f"variants n=3 应扣 12 点(3×edit): {bal0}->{bal1}"


def test_variants_failure_refunds_all(client, auth_headers, monkeypatch):
    from app.services import design_tools
    def _boom(img, n, prompt="", prefer_local=False):
        raise RuntimeError("no key")
    monkeypatch.setattr(design_tools, "make_variants", _boom)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/design-tools/variants", headers=auth_headers,
                    data={"n": 4}, files={"file": ("x.png", _png(), "image/png")})
    # 异步:端点立即返回 pending;作业在 worker 失败(eager 同步跑完)→ error + 按 4 笔全退
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "error", job
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0, f"失败应把 4 笔全退回: {bal0}->{bal1}"


def test_variants_insufficient_credits_402(client, auth_headers, monkeypatch):
    # 把余额烧到不足以付 6 张:先充到已知值再请求 n=6
    # 初始 100;process 不影响这里。直接请求 n=6 需 24 点,余额够;先把余额降低。
    # 用 topup 反向不可行,改为连续消耗:此处用一个新账户余额 100,请求 n=6=24<100 仍够。
    # 故构造余额不足:把 COST 临时调高让单价>余额。
    from app.services import billing
    monkeypatch.setitem(billing.COST, "edit", 200)  # 单张 200 点,远超 100 余额
    r = client.post("/api/design-tools/variants", headers=auth_headers,
                    data={"n": 1}, files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 402, r.text


# ---------- P1-4:studio 坏图也要退点 ----------
def test_studio_tryon_bad_image_refunds(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/studio/tryon", headers=auth_headers,
                    files={"file": ("x.png", io.BytesIO(b"not an image"), "image/png")})
    assert r.status_code == 400, r.text
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0, f"坏图应退点: {bal0}->{bal1}"


# ---------- P1-1:compress 目标尺寸过大被拒(不 OOM) ----------
def test_compress_rejects_oversize(client, auth_headers):
    r = client.post("/api/image-tools/compress", headers=auth_headers,
                    data={"target_w": 60000, "target_h": 60000, "fmt": "jpeg"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 400, r.text  # service 抛 ValueError -> 400
