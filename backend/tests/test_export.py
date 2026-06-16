"""生产图导出 / 给工厂的最终格式转换(/api/export/production)测试。

只验证管线:鉴权 / 扣点(asset=1)/ 退点 / 多格式产出 / 尺寸·DPI / 出血·安全边·排版 /
CMYK / 打样图 / 参数校验。纯本地确定性操作,无 AI、无 key 依赖。
"""
from __future__ import annotations

import io

from PIL import Image

from app.db import Base, engine

# 确保相关表已建(新表范式;export 不建表,这里仅保险)
Base.metadata.create_all(engine)


def _balance(client, headers) -> int:
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["credits"]


def _post(client, headers, png, **form):
    return client.post(
        "/api/export/production",
        headers=headers,
        files={"file": ("design.png", png(), "image/png")},
        data=form,
    )


# ---- 鉴权 ----------------------------------------------------------------
def test_export_unauth_401(client, png):
    r = client.post(
        "/api/export/production",
        files={"file": ("design.png", png(), "image/png")},
    )
    assert r.status_code == 401


# ---- 正常路径:白底 30×40@300,五格式齐全 --------------------------------
def test_export_default_all_formats(client, auth_headers, png, tool_result):
    before = _balance(client, auth_headers)
    r = _post(client, auth_headers, png, bg="white")  # 白底才出全部5格式(默认透明会跳过 jpg/pdf)
    body = tool_result(auth_headers, r)
    # 五格式都在(含 PSD),URL 指向 /files 且可下载
    assert set(body["files"].keys()) == {"png", "jpg", "tiff", "pdf", "psd"}
    for url in body["files"].values():
        assert url.startswith("/files/")
        dl = client.get(url)
        assert dl.status_code == 200, url
        assert len(dl.content) > 0
    # PSD 必须是合法 PSD:签名 8BPS,且 Pillow 能读回(尺寸一致)
    psd = client.get(body["files"]["psd"]).content
    assert psd[:4] == b"8BPS"
    back = Image.open(io.BytesIO(psd)); back.load()
    assert back.size == (body["meta"]["width_px"], body["meta"]["height_px"])
    # 30×40cm @ 300DPI = 3543×4724
    assert body["meta"]["width_px"] == 3543
    assert body["meta"]["height_px"] == 4724
    assert body["meta"]["dpi"] == 300
    # asset 扣 1 点
    assert _balance(client, auth_headers) == before - 1


# ---- 自定义尺寸 / DPI / 格式子集 -----------------------------------------
def test_export_custom_size_dpi_subset(client, auth_headers, png, tool_result):
    r = _post(client, auth_headers, png, width_cm="10", height_cm="10", dpi="150",
              formats="png,jpg", bg="white")  # 含 jpg → 需白底(默认透明会跳过 jpg)
    body = tool_result(auth_headers, r)
    assert set(body["files"].keys()) == {"png", "jpg"}
    # 10cm @ 150DPI = round(10/2.54*150) = 591
    assert body["meta"]["width_px"] == 591
    assert body["meta"]["height_px"] == 591


# ---- 坏图 → 400 + 退点 ----------------------------------------------------
def test_export_bad_image_refunds_400(client, auth_headers):
    before = _balance(client, auth_headers)
    r = client.post(
        "/api/export/production",
        headers=auth_headers,
        files={"file": ("x.png", b"not an image", "image/png")},
    )
    assert r.status_code == 400
    assert _balance(client, auth_headers) == before  # 退点后余额不变


# ---- 非法格式 → 400 + 退点 ------------------------------------------------
def test_export_invalid_formats_refunds_400(client, auth_headers, png):
    before = _balance(client, auth_headers)
    r = _post(client, auth_headers, png, formats="bmp,gif")
    assert r.status_code == 400
    assert _balance(client, auth_headers) == before


# ---- 非法尺寸 / DPI → 400 + 退点 -----------------------------------------
def test_export_bad_size_refunds_400(client, auth_headers, png):
    before = _balance(client, auth_headers)
    r = _post(client, auth_headers, png, width_cm="999")
    assert r.status_code == 400
    assert _balance(client, auth_headers) == before

    before = _balance(client, auth_headers)
    r = _post(client, auth_headers, png, dpi="5000")
    assert r.status_code == 400
    assert _balance(client, auth_headers) == before


# ---- 出血:全幅画布 = 裁切 + 2×出血 -------------------------------------
def test_export_bleed_expands_canvas(client, auth_headers, png, tool_result):
    r = _post(client, auth_headers, png, width_cm="30", height_cm="40", dpi="300",
              formats="png", bleed_mm="3", safe_mm="5")
    m = tool_result(auth_headers, r)["meta"]
    # 3mm @ 300DPI = round(3/25.4*300) = 35 px;全幅 = 3543+70 × 4724+70
    assert m["trim_w_px"] == 3543 and m["trim_h_px"] == 4724
    assert m["width_px"] == 3543 + 70 and m["height_px"] == 4724 + 70
    assert m["bleed_mm"] == 3.0 and m["safe_mm"] == 5.0


# ---- 默认无出血:全幅 == 裁切(向后兼容) --------------------------------
def test_export_no_bleed_canvas_equals_trim(client, auth_headers, png, tool_result):
    r = _post(client, auth_headers, png, formats="png")
    m = tool_result(auth_headers, r)["meta"]
    assert m["width_px"] == m["trim_w_px"] == 3543
    assert m["height_px"] == m["trim_h_px"] == 4724
    assert m["color_mode"] == "RGB"


# ---- 排版模式 / 锚点 -------------------------------------------------------
def test_export_scale_anchor_modes(client, auth_headers, png, tool_result):
    for scale in ("contain", "cover", "actual"):
        for anchor in ("center", "top", "bottom"):
            r = _post(client, auth_headers, png, formats="png", width_cm="10", height_cm="10",
                      dpi="150", scale=scale, anchor=anchor)
            m = tool_result(auth_headers, r)["meta"]
            assert m["scale"] == scale and m["anchor"] == anchor


# ---- CMYK:jpg 输出确为 CMYK 模式;png 仍 RGB ----------------------------
def test_export_cmyk_jpg(client, auth_headers, png, tool_result):
    r = _post(client, auth_headers, png, formats="jpg,png", width_cm="10", height_cm="10",
              dpi="150", cmyk="true", bg="white")  # jpg 需底色(默认透明会跳过 jpg)
    body = tool_result(auth_headers, r)
    assert "CMYK" in body["meta"]["color_mode"]
    jpg = client.get(body["files"]["jpg"])
    assert Image.open(io.BytesIO(jpg.content)).mode == "CMYK"
    # png 不支持 CMYK,仍是 RGBA/RGB(非 CMYK)
    pn = client.get(body["files"]["png"])
    assert Image.open(io.BytesIO(pn.content)).mode != "CMYK"


# ---- 底色:透明(默认,跳过无透明通道的 jpg/pdf)/ 白·黑底(所有格式压平)-------
def test_export_background_modes_all_formats(client, auth_headers, png, tool_result):
    """透明默认:只出能真透明的 png/tiff/psd、跳过 jpg/pdf;白/黑底:所有格式压平到底色。
    用非正方画布(10×20)逼出透明 padding 检验角落。"""
    # ① 透明(默认):请求全部5格式 → 只产出 png/tiff/psd,跳过 jpg/pdf;角落 padding 透明
    r = _post(client, auth_headers, png, formats="png,jpg,tiff,pdf,psd",
              width_cm="10", height_cm="20", dpi="150")
    body = tool_result(auth_headers, r)
    assert body["meta"]["background"] == "transparent"
    assert set(body["files"].keys()) == {"png", "tiff", "psd"}, "透明应跳过 jpg/pdf"
    for fmt in ("png", "tiff"):
        im = Image.open(io.BytesIO(client.get(body["files"][fmt]).content)); im.load()
        assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0, f"{fmt} 角落 padding 应透明"

    # ② 黑底:全部5格式都产出,且 png/tiff 压平成不透明黑底(此前这俩格式忽略底色)
    r = _post(client, auth_headers, png, formats="png,jpg,tiff,pdf,psd",
              width_cm="10", height_cm="20", dpi="150", bg="black")
    body = tool_result(auth_headers, r)
    assert body["meta"]["background"] == "black"
    assert set(body["files"].keys()) == {"png", "jpg", "tiff", "pdf", "psd"}, "白/黑底应出全部格式"
    for fmt in ("png", "tiff"):
        im = Image.open(io.BytesIO(client.get(body["files"][fmt]).content)); im.load()
        assert im.convert("RGBA").getpixel((0, 0)) == (0, 0, 0, 255), f"{fmt} 角落应不透明黑底"


# ---- 打样核对图 -----------------------------------------------------------
def test_export_proof_image(client, auth_headers, png, tool_result):
    r = _post(client, auth_headers, png, formats="png", width_cm="10", height_cm="10",
              dpi="150", bleed_mm="3", safe_mm="5", proof="true")
    body = tool_result(auth_headers, r)
    assert body["proof"] and body["proof"].startswith("/files/")
    pr = client.get(body["proof"])
    assert pr.status_code == 200
    img = Image.open(io.BytesIO(pr.content))
    assert img.format == "JPEG" and max(img.size) <= 1400
    # 不要 proof 时为 null
    r2 = _post(client, auth_headers, png, formats="png", width_cm="10", height_cm="10", dpi="150")
    assert tool_result(auth_headers, r2)["proof"] is None


# ---- 非法排版/锚点/出血/安全边 → 400 + 退点 -----------------------------
def test_export_invalid_layout_params_refund_400(client, auth_headers, png):
    for bad in (
        {"scale": "weird"}, {"anchor": "left"}, {"bleed_mm": "99"}, {"safe_mm": "999"},
    ):
        before = _balance(client, auth_headers)
        r = _post(client, auth_headers, png, formats="png", **bad)
        assert r.status_code == 400, (bad, r.text)
        assert _balance(client, auth_headers) == before


# ---- 安全边过大(塌缩)→ 作业失败 + 退点(语义校验在 worker 内,故为 job error 而非 400)----
def test_export_safe_too_large_job_error_refunds(client, auth_headers, png):
    before = _balance(client, auth_headers)
    # 2cm 成品 + 安全边 15mm → 2×15mm 已 >= 20mm 成品 → 塌缩(export_production_multi 抛 ValueError)
    r = _post(client, auth_headers, png, formats="png", width_cm="2", height_cm="2",
              dpi="150", safe_mm="15")
    assert r.status_code == 200, r.text  # 入队成功
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "error", job
    assert _balance(client, auth_headers) == before  # 作业失败退点


# ---- 余额不足 → 402 -------------------------------------------------------
def test_export_insufficient_credits_402(client, auth_headers, png):
    # 把余额耗尽:asset=1,新用户 100 点 → 用极小画布快速跑到 402
    r = None
    for _ in range(120):
        r = _post(client, auth_headers, png, formats="png", width_cm="2", height_cm="2", dpi="72")
        if r.status_code == 402:
            break
    assert r is not None and r.status_code == 402
