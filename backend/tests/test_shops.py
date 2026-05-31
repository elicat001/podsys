"""店铺管理 + 按店铺上架 测试。"""
from __future__ import annotations

# 确保 shops 表存在(独立模块,import 即注册到 Base.metadata)
from app.models_shop import Shop  # noqa: F401
from app.db import engine, Base

Base.metadata.create_all(engine)

# 本任务只交付 router,主线 main.py 由 Tech Lead 收口注册。
# 测试自挂载该 router(幂等),避免依赖尚未合入的 main.py 改动。
from app.main import app  # noqa: E402
from app.routers import shops as shops_router  # noqa: E402

if not any(getattr(r, "path", "").startswith("/api/shops") for r in app.router.routes):
    # main.py 把 StaticFiles 挂在 "/"(catch-all),append 的路由会被它吃掉。
    # 因此把 shops 路由插到该挂载点之前,保证 /api/* 优先命中。
    before = len(app.router.routes)
    app.include_router(shops_router.router)
    new_routes = app.router.routes[before:]
    del app.router.routes[before:]
    mount_idx = next(
        (i for i, r in enumerate(app.router.routes) if getattr(r, "path", "") == ""),
        len(app.router.routes),
    )
    app.router.routes[mount_idx:mount_idx] = new_routes


def _create_product(client, headers, title="Tee A") -> int:
    resp = client.post("/api/products", json={"title": title}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["product_id"]


def test_create_and_list_shop(client, auth_headers):
    resp = client.post("/api/shops", json={"platform": "local", "name": "My Local Shop"},
                       headers=auth_headers)
    assert resp.status_code == 200, resp.text
    shop_id = resp.json()["shop_id"]
    assert resp.json()["platform"] == "local"
    assert resp.json()["name"] == "My Local Shop"

    resp = client.get("/api/shops", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    ids = [s["shop_id"] for s in resp.json()]
    assert shop_id in ids


def test_publish_product_to_local_shop(client, auth_headers):
    shop_id = client.post("/api/shops", json={"platform": "local", "name": "Shop1"},
                          headers=auth_headers).json()["shop_id"]
    product_id = _create_product(client, auth_headers)

    resp = client.post(f"/api/shops/{shop_id}/publish-product",
                       json={"product_id": product_id}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "published"
    assert data["external_id"]
    assert data["shop_id"] == shop_id


def test_publish_others_product_404(client, auth_headers):
    # 另一个用户建商品
    import uuid
    other = client.post("/api/auth/register",
                        json={"email": f"o_{uuid.uuid4().hex[:8]}@test.local", "password": "pw123456"})
    other_headers = {"Authorization": f"Bearer {other.json()['token']}"}
    other_product = _create_product(client, other_headers, title="Other Tee")

    shop_id = client.post("/api/shops", json={"platform": "local", "name": "MineShop"},
                          headers=auth_headers).json()["shop_id"]

    resp = client.post(f"/api/shops/{shop_id}/publish-product",
                       json={"product_id": other_product}, headers=auth_headers)
    assert resp.status_code == 404, resp.text


def test_publish_to_others_shop_404(client, auth_headers):
    import uuid
    other = client.post("/api/auth/register",
                        json={"email": f"o_{uuid.uuid4().hex[:8]}@test.local", "password": "pw123456"})
    other_headers = {"Authorization": f"Bearer {other.json()['token']}"}
    other_shop = client.post("/api/shops", json={"platform": "local", "name": "OtherShop"},
                            headers=other_headers).json()["shop_id"]

    product_id = _create_product(client, auth_headers)
    resp = client.post(f"/api/shops/{other_shop}/publish-product",
                       json={"product_id": product_id}, headers=auth_headers)
    assert resp.status_code == 404, resp.text


def test_requires_auth(client):
    assert client.get("/api/shops").status_code == 401
    assert client.post("/api/shops", json={"platform": "local", "name": "x"}).status_code == 401
    assert client.post("/api/shops/1/publish-product", json={"product_id": 1}).status_code == 401
