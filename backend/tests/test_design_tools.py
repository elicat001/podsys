"""印花设计工具测试(E1)。

集成点(main.py 注册路由)由 Tech Lead 收口,测试期我们把路由挂到一个本地
TestClient 上(复用 conftest 的 auth_headers / png fixtures 造用户与图)。
测试环境无 OpenAI key,故 gpt-image 端点应 502 且退点(余额不变)。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import design_tools as design_tools_router


@pytest.fixture(scope="module")
def dt_client() -> TestClient:
    """挂载 design-tools 路由的独立 app(不依赖 main.py 是否已注册)。"""
    app = FastAPI()
    app.include_router(design_tools_router.router)
    with TestClient(app) as c:
        yield c


# ---- 未登录 -> 401(各端点抽测)---------------------------------------------

def test_variants_requires_auth(dt_client, png):
    resp = dt_client.post("/api/design-tools/variants", files={"file": ("a.png", png(), "image/png")})
    assert resp.status_code == 401


def test_restyle_requires_auth(dt_client, png):
    resp = dt_client.post(
        "/api/design-tools/restyle",
        files={"file": ("a.png", png(), "image/png")},
        data={"style": "Temu 2D flat"},
    )
    assert resp.status_code == 401


def test_meme_requires_auth(dt_client, png):
    resp = dt_client.post(
        "/api/design-tools/meme",
        files={"file": ("a.png", png(), "image/png")},
        data={"text": "Monday again"},
    )
    assert resp.status_code == 401


# ---- 已登录但无 key -> 502 且退点(余额不变)--------------------------------

def _balance(client, headers) -> int:
    resp = client.get("/api/billing/balance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["credits"]


# 新契约(batch11):无 OpenAI key 走本地真实引擎 -> 200 + 真实产物,正常扣点(不再 502)
def test_variants_no_key_offline_real(client, dt_client, auth_headers, png, tool_result):
    before = _balance(client, auth_headers)
    resp = dt_client.post("/api/design-tools/variants", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"n": "3"})
    assert len(tool_result(auth_headers, resp)["images"]) == 3  # 后台本地换色 → 轮询 3 张
    assert _balance(client, auth_headers) == before - 3 * 4


def test_restyle_no_key_offline_real(client, dt_client, auth_headers, png):
    resp = dt_client.post("/api/design-tools/restyle", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"style": "Temu 2D flat"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"]


def test_meme_no_key_offline_real(client, dt_client, auth_headers, png):
    resp = dt_client.post("/api/design-tools/meme", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"text": "It is what it is"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"]


# ---- 参数校验:variants n 越界 -> 400 且退点 --------------------------------

def test_variants_invalid_n_rejected(client, dt_client, auth_headers, png):
    before = _balance(client, auth_headers)
    resp = dt_client.post(
        "/api/design-tools/variants",
        headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")},
        data={"n": "0"},
    )
    assert resp.status_code == 400, resp.text
    assert _balance(client, auth_headers) == before


def test_variants_too_many_rejected(client, dt_client, auth_headers, png):
    resp = dt_client.post(
        "/api/design-tools/variants",
        headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")},
        data={"n": "99"},
    )
    assert resp.status_code == 400, resp.text


# ---- service 层 prompt 拼装 sanity ------------------------------------------

def test_prompt_builders():
    assert "Temu 2D flat" in design_tools_router.design_tools.restyle_prompt("Temu 2D flat")
    assert "Monday" in design_tools_router.design_tools.meme_prompt("Monday")
    assert design_tools_router.design_tools.variants_prompt("")  # 非空


# ---- 有 key 时 make_variants 并行调用(修 Failed to fetch 超时)-----------------
# 离线无法真打网关,这里 monkeypatch 出一个假 client,确定性验证并行 gather 逻辑。

def test_make_variants_parallel_with_key(monkeypatch):
    from PIL import Image

    from app.services import design_tools as svc

    calls = []

    class FakeClient:
        def edit(self, img, p):  # 多线程调用;CPython list.append 受 GIL 保护,线程安全
            calls.append(p)
            return Image.new("RGBA", (8, 8), (1, 2, 3, 255))

    monkeypatch.setattr(svc, "_has_key", lambda: True)
    monkeypatch.setattr(svc, "_client", lambda: FakeClient())
    out = svc.make_variants(Image.new("RGB", (8, 8)), 3, prompt="x")
    assert len(out) == 3
    assert len(calls) == 3  # 恰好 n 次,不多不少(扣点笔数对齐)


def test_make_variants_failure_propagates(monkeypatch):
    """任一并行调用失败 -> 抛出,router 才能退回全部已扣点。"""
    from PIL import Image

    from app.services import design_tools as svc

    class BadClient:
        def edit(self, img, p):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(svc, "_has_key", lambda: True)
    monkeypatch.setattr(svc, "_client", lambda: BadClient())
    with pytest.raises(RuntimeError):
        svc.make_variants(Image.new("RGB", (8, 8)), 2)


# ---- 有 key 时端到端走后台作业(修 Failed to fetch:gpt-image 太慢)---------
# 用全量 app 的 client(含 /api/jobs 轮询)。TestClient 同步执行 BackgroundTasks,
# 故 POST 返回后作业已 done/error,可直接轮询。

def test_variants_with_key_background_success(client, auth_headers, monkeypatch, png):
    from PIL import Image

    from app.ai import openai_image
    from app.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit",
                        lambda self, image, prompt, mask=None, size="auto", background="auto":
                        Image.new("RGBA", (32, 32), (5, 6, 7, 255)))
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/design-tools/variants", headers=auth_headers,
                    files={"file": ("a.png", png(), "image/png")}, data={"n": "2"})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]  # 有 key -> 后台作业
    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert len(job["result"]["images"]) == 2
    # 按张扣点:n=2 张 × 4(edit)= 8
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 8


def test_variants_with_key_background_failure_refunds_all(client, auth_headers, monkeypatch, png):
    from app.ai import openai_image
    from app.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    def _boom(self, image, prompt, mask=None, size="auto", background="auto"):
        raise RuntimeError("gateway 500")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _boom)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/design-tools/variants", headers=auth_headers,
                    files={"file": ("a.png", png(), "image/png")}, data={"n": "3"})
    assert r.status_code == 200, r.text  # 提交成功;失败发生在后台
    jid = r.json()["job_id"]
    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert job["status"] == "error", job
    # 失败应退回全部 3 笔,余额复原
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0
