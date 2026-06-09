"""图案处理工具测试:compress 真实行为 + expand/dewatermark 鉴权/退点。

main.py 由 Tech Lead 收口注册路由;为让本测试自包含可跑,这里在 import 后
把 image_tools 路由挂到测试 app 上(幂等:已挂则跳过)。
"""
from __future__ import annotations

import io

from PIL import Image

from app.main import app
from app.routers import image_tools as image_tools_router

# 幂等注册:无论 main.py 是否已 include,都保证路由存在。
# main.py 把 StaticFiles 挂在 "/"(最后),会吃掉所有路径;因此新路由必须插到
# 该 mount 之前(routes 按顺序匹配),否则 POST 会命中静态挂载返回 405。
if not any(getattr(r, "path", "").startswith("/api/image-tools") for r in app.routes):
    before = len(app.routes)
    app.include_router(image_tools_router.router)
    new_routes = app.routes[before:]
    del app.routes[before:]
    # 找到 "/" 静态 mount 的位置,把新路由插到它前面
    insert_at = next(
        (i for i, r in enumerate(app.routes) if getattr(r, "path", "") == ""),
        len(app.routes),
    )
    app.routes[insert_at:insert_at] = new_routes


def _make_big_png(size=(1200, 1200)) -> io.BytesIO:
    """造一张高熵(类照片)大图:逐像素伪随机噪声,PNG 难压缩,
    保证缩到 300x300 后的 JPEG 体积明显小于原图。"""
    import os

    w, h = size
    img = Image.frombytes("RGB", size, os.urandom(w * h * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _balance(client, headers) -> int:
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["credits"]


# --------------------------------------------------------------------------
# compress:离线真实行为(重点)
# --------------------------------------------------------------------------
def test_compress_resizes_and_changes_format(client, auth_headers, tool_result):
    big = _make_big_png((1200, 1200))
    original_bytes = len(big.getvalue())
    big.seek(0)

    resp = client.post(
        "/api/image-tools/compress",
        headers=auth_headers,
        files={"file": ("big.png", big, "image/png")},
        data={"target_w": 300, "quality": 80, "fmt": "jpeg"},
    )
    body = tool_result(auth_headers, resp)

    assert body["width"] == 300
    # 等比:1200x1200 -> 300x300
    assert body["height"] == 300
    assert body["format"] == "jpeg"
    assert body["output_bytes"] > 0
    assert body["output_bytes"] < original_bytes  # 压缩确实变小

    # 取回产物:能被 PIL 打开,尺寸正确,确为 JPEG
    got = client.get(body["image_url"])
    assert got.status_code == 200
    out_img = Image.open(io.BytesIO(got.content))
    out_img.load()
    assert out_img.size == (300, 300)
    assert out_img.format == "JPEG"
    # 落盘字节与返回的 output_bytes 一致
    assert len(got.content) == body["output_bytes"]


def test_compress_webp_and_single_dimension(client, auth_headers, tool_result):
    big = _make_big_png((800, 400))
    resp = client.post(
        "/api/image-tools/compress",
        headers=auth_headers,
        files={"file": ("big.png", big, "image/png")},
        data={"target_h": 200, "fmt": "webp"},
    )
    body = tool_result(auth_headers, resp)
    assert body["height"] == 200
    assert body["width"] == 400  # 800x400 等比 -> 400x200
    assert body["format"] == "webp"


def test_compress_charges_process_points(client, auth_headers):
    before = _balance(client, auth_headers)
    big = _make_big_png((600, 600))
    resp = client.post(
        "/api/image-tools/compress",
        headers=auth_headers,
        files={"file": ("big.png", big, "image/png")},
        data={"target_w": 200, "fmt": "jpeg"},
    )
    assert resp.status_code == 200, resp.text
    after = _balance(client, auth_headers)
    assert after == before - 2  # process 扣 2


def test_compress_bad_image_refunds(client, auth_headers):
    before = _balance(client, auth_headers)
    resp = client.post(
        "/api/image-tools/compress",
        headers=auth_headers,
        files={"file": ("x.png", io.BytesIO(b"not an image"), "image/png")},
        data={"target_w": 100, "fmt": "jpeg"},
    )
    assert resp.status_code == 400, resp.text
    after = _balance(client, auth_headers)
    assert after == before  # 读图失败已退点,余额不变


# --------------------------------------------------------------------------
# expand / dewatermark:鉴权 + 无 key 退点
# --------------------------------------------------------------------------
def test_expand_requires_auth(client, png):
    resp = client.post(
        "/api/image-tools/expand",
        files={"file": ("a.png", png(), "image/png")},
        data={"prompt": ""},
    )
    assert resp.status_code == 401


def test_dewatermark_requires_auth(client, png):
    resp = client.post(
        "/api/image-tools/dewatermark",
        files={"file": ("a.png", png(), "image/png")},
    )
    assert resp.status_code == 401


# 新契约(batch11):无 key 走本地引擎 -> 200 真实产物,正常扣 edit(不再 502)
def test_expand_no_key_offline_real(client, auth_headers, png):
    before = _balance(client, auth_headers)
    resp = client.post("/api/image-tools/expand", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"prompt": "more space"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"]
    assert _balance(client, auth_headers) == before - 4


def test_dewatermark_no_key_offline_real(client, auth_headers, png):
    resp = client.post("/api/image-tools/dewatermark", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"]
