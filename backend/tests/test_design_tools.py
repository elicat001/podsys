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


def test_fuse_requires_auth(dt_client, png):
    resp = dt_client.post(
        "/api/design-tools/fuse",
        files={"file": ("a.png", png(), "image/png")},
        data={"prompt": "cat astronaut"},
    )
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
def test_variants_no_key_offline_real(client, dt_client, auth_headers, png):
    before = _balance(client, auth_headers)
    resp = dt_client.post("/api/design-tools/variants", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"n": "3"})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["images"]) == 3
    assert _balance(client, auth_headers) == before - 3 * 4


def test_fuse_no_key_offline_real(client, dt_client, auth_headers, png):
    before = _balance(client, auth_headers)
    resp = dt_client.post("/api/design-tools/fuse", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")}, data={"prompt": "galaxy cat"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"]
    assert _balance(client, auth_headers) == before - 4


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
    assert "galaxy" in design_tools_router.design_tools.fuse_prompt("galaxy")
    assert design_tools_router.design_tools.variants_prompt("")  # 非空
