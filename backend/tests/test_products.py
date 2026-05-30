"""商品库 + 一键上架。"""
from __future__ import annotations


def _create_product(client, headers, title="Test Tee"):
    return client.post(
        "/api/products",
        headers=headers,
        json={"title": title, "category": "apparel", "price": 19.99},
    )


def test_publish_local_published_with_external_id(client, auth_headers):
    pr = _create_product(client, auth_headers)
    assert pr.status_code == 200, pr.text
    pid = pr.json()["product_id"]

    r = client.post(f"/api/products/{pid}/publish", headers=auth_headers, json={"platform": "local"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "published"
    assert body["external_id"]


def test_publish_temu_501(client, auth_headers):
    pr = _create_product(client, auth_headers)
    pid = pr.json()["product_id"]
    r = client.post(f"/api/products/{pid}/publish", headers=auth_headers, json={"platform": "temu"})
    assert r.status_code == 501


def test_list_products_contains_created(client, auth_headers):
    title = "Unique-Product-XYZ"
    pr = _create_product(client, auth_headers, title=title)
    pid = pr.json()["product_id"]

    r = client.get("/api/products", headers=auth_headers)
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()]
    titles = [p["title"] for p in r.json()]
    assert pid in ids
    assert title in titles


def test_products_require_auth(client):
    r = client.get("/api/products")
    assert r.status_code == 401
