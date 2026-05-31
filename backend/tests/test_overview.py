"""我的空间总览测试 —— GET /api/me/overview。"""
from __future__ import annotations

# 确保 shop / collect 表已建(它们由独立模块注册,测试库需先 create_all)
from app.db import engine, Base
import app.models_shop  # noqa: F401
import app.models_collect  # noqa: F401

Base.metadata.create_all(engine)

# 本任务只交付 router,主线 main.py 由 Tech Lead 收口注册。
# 测试自挂载该 router(幂等),避免依赖尚未合入的 main.py 改动。
from app.main import app  # noqa: E402
from app.routers import me as me_router  # noqa: E402

if not any(getattr(r, "path", "").startswith("/api/me") for r in app.router.routes):
    # main.py 把 StaticFiles 挂在 "/"(catch-all),append 的路由会被它吃掉。
    # 因此把 me 路由插到该挂载点之前,保证 /api/* 优先命中。
    before = len(app.router.routes)
    app.include_router(me_router.router)
    new_routes = app.router.routes[before:]
    del app.router.routes[before:]
    mount_idx = next(
        (i for i, r in enumerate(app.router.routes) if getattr(r, "path", "") == ""),
        len(app.router.routes),
    )
    app.router.routes[mount_idx:mount_idx] = new_routes


def test_overview_new_user_all_zero(client, auth_headers):
    r = client.get("/api/me/overview", headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["credits"] == 100
    assert d["assets"] == 0
    assert d["products"] == 0
    assert d["shops"] == 0
    assert d["jobs"] == 0
    assert d["collect_tasks"] == 0


def test_overview_counts_products(client, auth_headers):
    r = client.post(
        "/api/products",
        json={"title": "测试商品"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/me/overview", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["products"] == 1


def test_overview_requires_auth(client):
    r = client.get("/api/me/overview")
    assert r.status_code == 401
