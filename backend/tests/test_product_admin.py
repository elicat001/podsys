"""batch10 E1:商品库管理深度(筛选 + 批量 + 标签)测试。

用 conftest 的 client / auth_headers fixture;通过 HTTP 接口建商品。
"""
from __future__ import annotations

import uuid

from starlette.routing import Mount

from app.main import app
from app.routers import product_admin

# TL 收口前(main.py 尚未注册本路由),测试自挂载以便独立跑绿。
# main.py 末尾把 StaticFiles 挂在 "/"(贪婪匹配),直接 include_router 会被它拦截,
# 故把本路由的 route 插到静态挂载之前。TL 在 main.py 正式注册后,本块为幂等 no-op。
if not any(getattr(r, "name", None) == "search_products" for r in app.routes):
    _mount_idx = next(
        (i for i, r in enumerate(app.routes) if isinstance(r, Mount)),
        len(app.routes),
    )
    _before = len(app.routes)
    app.include_router(product_admin.router)
    _new = app.routes[_before:]
    del app.routes[_before:]
    app.routes[_mount_idx:_mount_idx] = _new


def _register(client) -> dict:
    """注册一个新随机用户,返回 Bearer 头(本地辅助,避免改 conftest)。"""
    email = f"user_{uuid.uuid4().hex[:10]}@test.local"
    resp = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _make_product(client, headers, title: str) -> int:
    resp = client.post("/api/products", json={"title": title}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["product_id"]


def test_search_by_tag(client, auth_headers):
    ids = [_make_product(client, auth_headers, f"P{i}") for i in range(3)]

    # 对前两个设标签 cat
    for pid in ids[:2]:
        r = client.post(f"/api/products/{pid}/tags", json={"tags": ["cat"]}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["tags"] == ["cat"]

    r = client.get("/api/products/search", params={"tag": "cat"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    returned_ids = {item["id"] for item in body["items"]}
    assert returned_ids == set(ids[:2])
    # 序列化字段齐全
    item = body["items"][0]
    for key in ("id", "title", "sku", "batch", "source", "risk", "tags", "listing_status"):
        assert key in item
    assert item["listing_status"] == "none"


def test_batch_delete(client, auth_headers):
    ids = [_make_product(client, auth_headers, f"D{i}") for i in range(3)]

    # 删一个
    r = client.post(
        "/api/products/batch",
        json={"action": "delete", "product_ids": [ids[0]]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["affected"] == 1

    # search 应该少一个(这批用唯一 batch 隔离不了,改用 tag 标记这一组)
    remaining = client.get("/api/products", headers=auth_headers).json()
    remaining_ids = {p["id"] for p in remaining}
    assert ids[0] not in remaining_ids
    assert ids[1] in remaining_ids and ids[2] in remaining_ids


def test_batch_set_risk_and_add_tag(client, auth_headers):
    ids = [_make_product(client, auth_headers, f"R{i}") for i in range(2)]

    r = client.post(
        "/api/products/batch",
        json={"action": "set_risk", "product_ids": ids, "value": "high"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["affected"] == 2

    r = client.get("/api/products/search", params={"risk": "high"}, headers=auth_headers)
    assert r.status_code == 200
    got = {item["id"] for item in r.json()["items"]}
    assert set(ids).issubset(got)

    # add_tag 去重:加两次同 tag 只保留一个
    client.post(
        "/api/products/batch",
        json={"action": "add_tag", "product_ids": ids, "value": "promo"},
        headers=auth_headers,
    )
    client.post(
        "/api/products/batch",
        json={"action": "add_tag", "product_ids": ids, "value": "promo"},
        headers=auth_headers,
    )
    r = client.get("/api/products/search", params={"tag": "promo"}, headers=auth_headers)
    assert r.json()["total"] == 2
    for item in r.json()["items"]:
        assert item["tags"].count("promo") == 1


def test_batch_does_not_affect_other_users(client, auth_headers):
    mine = [_make_product(client, auth_headers, f"M{i}") for i in range(2)]
    other_headers = _register(client)
    theirs = [_make_product(client, other_headers, f"O{i}") for i in range(2)]

    # 本人 batch delete 传入混合 id(含他人),只影响本人
    r = client.post(
        "/api/products/batch",
        json={"action": "delete", "product_ids": mine + theirs},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["affected"] == 2  # 仅本人 2 个

    # 他人商品仍在
    their_list = client.get("/api/products", headers=other_headers).json()
    their_ids = {p["id"] for p in their_list}
    assert set(theirs).issubset(their_ids)


def test_batch_invalid_action(client, auth_headers):
    pid = _make_product(client, auth_headers, "X")
    r = client.post(
        "/api/products/batch",
        json={"action": "frobnicate", "product_ids": [pid]},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text


def test_tags_unauthorized_owner(client, auth_headers):
    pid = _make_product(client, auth_headers, "Owned")
    other_headers = _register(client)
    # 他人改我的商品标签 → 404
    r = client.post(f"/api/products/{pid}/tags", json={"tags": ["x"]}, headers=other_headers)
    assert r.status_code == 404, r.text


def test_tags_not_found(client, auth_headers):
    r = client.post("/api/products/999999/tags", json={"tags": ["x"]}, headers=auth_headers)
    assert r.status_code == 404


def test_requires_auth(client):
    assert client.get("/api/products/search").status_code == 401
    assert client.post("/api/products/batch", json={"action": "delete", "product_ids": []}).status_code == 401
    assert client.post("/api/products/1/tags", json={"tags": []}).status_code == 401
