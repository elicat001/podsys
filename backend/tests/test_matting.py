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


def test_uniform_bg_cutout_clean_and_keeps_interior():
    """平背景洪水填充:主体实心不透明、角落背景透明、**主体内部同色区域(被包围)保留**。"""
    import numpy as np
    from PIL import ImageDraw

    from app.ai.matting import _uniform_bg_cutout
    im = Image.new("RGB", (200, 200), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([50, 50, 150, 150], fill=(220, 40, 40))   # 红方块(主体)
    d.ellipse([92, 92, 108, 108], fill=(255, 255, 255))   # 内部白点(被红包围,不连边缘)
    out = _uniform_bg_cutout(im)
    assert out is not None and out.mode == "RGBA"
    a = np.asarray(out)[..., 3]
    assert a[100, 100] == 255      # 内部白(被包围)→ 保留不透明,不被当背景抠掉
    assert a[120, 120] == 255      # 红主体 → 不透明
    assert a[4, 4] == 0            # 角落背景 → 透明


def test_uniform_bg_cutout_skips_busy_bg():
    """背景花哨(四角色差大)→ 返回 None,交给神经网络,不强行洪水填充。"""
    import numpy as np

    from app.ai.matting import _uniform_bg_cutout
    arr = (np.random.RandomState(7).rand(80, 80, 3) * 255).astype("uint8")
    assert _uniform_bg_cutout(Image.fromarray(arr, "RGB")) is None


def test_matting_bad_image_refunds(client, auth_headers):
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/matting", headers=auth_headers,
                    files={"file": ("a.png", b"not-an-image", "image/png")})
    assert r.status_code == 400
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before  # 读图失败已退点


# ── 智能运行(AI 识别主体)──────────────────────────────────────────────────

def test_matting_ai_no_key_502_and_refunds(client, auth_headers, png):
    """无 key 时选「智能运行」→ 502 且退点(余额不变),提示改用快速运行。"""
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/matting", headers=auth_headers, files=_f(png), data={"engine": "ai"})
    assert r.status_code == 502, r.text
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before


def test_matting_ai_with_key_extracts_subject(client, auth_headers, png, monkeypatch, tool_result):
    """有 key 时「智能运行」→ 后台走 gpt-image 主体提取,产物入库名为「智能抠图」,扣 2 点。
    monkeypatch 客户端避免真连网关,并捕获传入的 prompt 验证 prompt 工程已生效。"""
    from app.ai import openai_image
    from app.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    seen = {}

    def _fake_remove(self, image, prompt=None):
        seen["prompt"] = prompt
        return Image.new("RGBA", (40, 40), (7, 8, 9, 255))
    monkeypatch.setattr(openai_image.OpenAIImageClient, "remove_background", _fake_remove)

    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/matting", headers=auth_headers, files=_f(png),
                    data={"engine": "ai", "prompt": "只保留陀螺,去掉手指"})
    body = tool_result(auth_headers, r)
    assert body["image_url"].startswith("/files/")
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before - 2
    # prompt 工程:通用主体词 + 用户提示都进了最终 prompt
    assert "main foreground subject" in seen["prompt"]
    assert "只保留陀螺,去掉手指" in seen["prompt"]
    # 入库名为「智能抠图」(区别于快速的「一键抠图」)
    assets = client.get("/api/space/assets", headers=auth_headers).json()["items"]
    assert any(a["name"] == "智能抠图" for a in assets)


def test_build_subject_prompt_general_and_hint():
    """prompt 工程:默认通用(不写死品类),hint 仅追加、不改写主体逻辑。"""
    from app.ai.matting import build_subject_prompt
    base = build_subject_prompt()
    # 通用主体识别词,且显式去除手/道具/阴影等无关元素
    assert "main foreground subject" in base
    for kw in ("hands", "fingers", "props", "transparent background"):
        assert kw in base
    # 没写死任何具体品类
    for hard in ("t-shirt", "spinner", "mug", "陀螺", "衣服"):
        assert hard.lower() not in base.lower()
    # hint 被追加(原文带入,中文也行)
    withhint = build_subject_prompt("只保留陀螺")
    assert withhint.startswith(base)
    assert "只保留陀螺" in withhint
