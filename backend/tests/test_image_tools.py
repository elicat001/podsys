"""图案处理工具测试:dewatermark 鉴权 + 无 key 本地引擎。

main.py 由 Tech Lead 收口注册路由;为让本测试自包含可跑,这里在 import 后
把 image_tools 路由挂到测试 app 上(幂等:已挂则跳过)。
"""
from __future__ import annotations

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


def _balance(client, headers) -> int:
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["credits"]


# --------------------------------------------------------------------------
# dewatermark:鉴权 + 无 key 本地引擎
# --------------------------------------------------------------------------
def test_dewatermark_requires_auth(client, png):
    resp = client.post(
        "/api/image-tools/dewatermark",
        files={"file": ("a.png", png(), "image/png")},
    )
    assert resp.status_code == 401


# 新契约(batch11):无 key 走本地引擎 -> 200 真实产物,正常扣 edit(不再 502)
def test_dewatermark_no_key_offline_real(client, auth_headers, png):
    resp = client.post("/api/image-tools/dewatermark", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"]


# --------------------------------------------------------------------------
# 图文替换(replace):AI-only(无本地兜底)→ 未登录 401 / 空需求 400 / 无 key 502,均退点;
# 有 key 走后台作业出图、按 edit(4)扣点
# --------------------------------------------------------------------------
def test_replace_requires_auth(client, png):
    resp = client.post("/api/image-tools/replace",
        files={"file": ("a.png", png(), "image/png")}, data={"prompt": "翻译成葡萄牙语"})
    assert resp.status_code == 401


def test_replace_empty_prompt_400_refund(client, auth_headers, png):
    before = _balance(client, auth_headers)
    resp = client.post("/api/image-tools/replace", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"prompt": "   "})
    assert resp.status_code == 400, resp.text
    assert _balance(client, auth_headers) == before  # 退点,余额不变


def test_replace_no_key_502_refund(client, auth_headers, png):
    """测试环境无 key → 502 且退点(自由指令编辑无本地等价,诚实降级)。"""
    before = _balance(client, auth_headers)
    resp = client.post("/api/image-tools/replace", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"prompt": "把牛仔裤换成淡蓝色"})
    assert resp.status_code == 502, resp.text
    assert _balance(client, auth_headers) == before


def test_replace_with_key_background_success(client, auth_headers, monkeypatch, png):
    """有 key → 后台作业,按需求改图;eager 模式下 POST 返回即已跑完,轮询取结果。扣 edit(4)。"""
    from PIL import Image

    from app.ai import openai_image
    from app.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit",
                        lambda self, image, prompt, **kw: Image.new("RGBA", (32, 32), (7, 8, 9, 255)))
    before = _balance(client, auth_headers)
    resp = client.post("/api/image-tools/replace", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"prompt": "把图中文字翻译成葡萄牙语"})
    assert resp.status_code == 200, resp.text
    jid = resp.json()["job_id"]
    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["result"]["image_url"]
    assert _balance(client, auth_headers) == before - 4


def test_replace_prompt_builder():
    from app.routers.image_tools import _replace_prompt
    out = _replace_prompt("把牛仔裤换成淡蓝色")
    assert "把牛仔裤换成淡蓝色" in out          # 用户原文照传
    assert "keep everything else" in out.lower()  # 保留其余元素的约束


# --------------------------------------------------------------------------
# gpt-image 上传编码(网关稳固):不透明→JPEG(降写超时)、含透明→PNG(保 alpha)、mask 强制 PNG
# --------------------------------------------------------------------------
def test_gptimage_upload_jpeg_for_opaque_png_for_alpha():
    from PIL import Image

    from app.ai.openai_image import _file_tuple, _has_alpha
    opaque = Image.new("RGB", (32, 32), (10, 20, 30))
    transparent = Image.new("RGBA", (32, 32), (10, 20, 30, 0))
    assert not _has_alpha(opaque) and _has_alpha(transparent)
    # 不透明 → JPEG(SOI 魔数 FF D8 FF),体积小、降网关上传写超时(治"母帧/改图失败退回原图")
    name, data, mime = _file_tuple(opaque)
    assert mime == "image/jpeg" and name.endswith(".jpg") and data[:3] == b"\xff\xd8\xff"
    # 含透明 → PNG(保 alpha,否则模型看到的内容会变)
    name2, data2, mime2 = _file_tuple(transparent)
    assert mime2 == "image/png" and name2.endswith(".png") and data2[:8] == b"\x89PNG\r\n\x1a\n"
    # mask 强制 PNG(即便不透明也绝不能 JPEG:它是 alpha 区域)
    assert _file_tuple(opaque, "mask", force_png=True)[2] == "image/png"
