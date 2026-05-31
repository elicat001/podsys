"""E3 套图&标题&来图定制工具组测试。

只验证管线(鉴权/扣点/退点/路由/降级),不验证真实 AI 输出。
测试环境无 OpenAI key(conftest 用临时 data_dir,默认 settings.openai_api_key 为空)。
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter

from app.main import app
from app.routers import studio_tools as studio_router


@pytest.fixture(scope="module", autouse=True)
def _register_router():
    """main.py 的路由注册由 Tech Lead 收口;测试期自助挂载本组路由(幂等)。

    main.py 把 StaticFiles 挂在 `/`(catch-all)且为最后一条路由,因此简单
    append 会被它抢先匹配(返回 405)。这里把本组路由插到 StaticFiles mount
    之前,保证 /api/* 优先命中。
    """
    if not any(getattr(r, "path", "").startswith("/api/studio/") for r in app.routes):
        # 找到 catch-all 静态挂载的位置,把本组路由插到它前面
        routes = app.router.routes
        insert_at = len(routes)
        for i, r in enumerate(routes):
            if getattr(r, "path", None) == "" or getattr(r, "name", "") == "frontend":
                insert_at = i
                break
        sub = APIRouter()
        sub.include_router(studio_router.router)
        for j, r in enumerate(sub.routes):
            routes.insert(insert_at + j, r)
    yield


def _balance(client, headers) -> int:
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["credits"]


# ---- /title 标题提取(无 key 降级,不扣点)--------------------------------
def test_title_unauth_401(client):
    r = client.post("/api/studio/title", data={"keywords": "cat lover"})
    assert r.status_code == 401


def test_title_no_key_degraded_no_charge(client, auth_headers):
    before = _balance(client, auth_headers)
    r = client.post(
        "/api/studio/title",
        data={"keywords": "cat lover, funny, gift", "category": "apparel"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded"] is True
    assert isinstance(body["title"], str) and body["title"].strip()
    assert isinstance(body["keywords"], list) and len(body["keywords"]) > 0
    # 无 key 降级 -> 不扣点,余额不变
    assert _balance(client, auth_headers) == before


def test_title_no_key_empty_keywords_still_ok(client, auth_headers):
    r = client.post("/api/studio/title", data={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["degraded"] is True
    assert r.json()["title"].strip()


# ---- gpt-image 系:未登录 401 / 登录无 key 502 且退点(余额不变)----------
def _multipart(png):
    return {"file": ("in.png", png(), "image/png")}


def test_tryon_unauth_401(client, png):
    r = client.post("/api/studio/tryon", files=_multipart(png))
    assert r.status_code == 401


def test_tryon_no_key_502_refund(client, auth_headers, png):
    before = _balance(client, auth_headers)
    r = client.post("/api/studio/tryon", files=_multipart(png), headers=auth_headers)
    assert r.status_code == 502, r.text
    assert _balance(client, auth_headers) == before  # 退点后余额不变


def test_pet_costume_unauth_401(client, png):
    r = client.post("/api/studio/pet-costume", files=_multipart(png))
    assert r.status_code == 401


def test_pet_costume_no_key_502_refund(client, auth_headers, png):
    before = _balance(client, auth_headers)
    r = client.post(
        "/api/studio/pet-costume",
        files=_multipart(png),
        data={"costume": "astronaut"},
        headers=auth_headers,
    )
    assert r.status_code == 502, r.text
    assert _balance(client, auth_headers) == before


def test_group_photo_unauth_401(client, png):
    r = client.post(
        "/api/studio/group-photo",
        files=_multipart(png),
        data={"prompt": "two friends at the beach"},
    )
    assert r.status_code == 401


def test_group_photo_no_key_502_refund(client, auth_headers, png):
    before = _balance(client, auth_headers)
    r = client.post(
        "/api/studio/group-photo",
        files=_multipart(png),
        data={"prompt": "two friends at the beach"},
        headers=auth_headers,
    )
    assert r.status_code == 502, r.text
    assert _balance(client, auth_headers) == before
