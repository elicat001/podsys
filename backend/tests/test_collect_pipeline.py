"""采集 → 选择 → 同步 全链路(离线)。

sync 的服务端取图通过 monkeypatch `_fetch_image` 返回内存图,保持离线确定性。
"""
import uuid

from PIL import Image

from app.services import collect_tasks as cs


def _fake_fetch(url, referer=""):
    # 颜色随 url 变一下,避免所有图 dhash 完全相同(无所谓,但更真实)
    c = (200, 60, 60) if "kwcdn" in url else (60, 120, 200)
    return Image.new("RGB", (96, 96), c)


def _register(client) -> dict:
    email = f"u_{uuid.uuid4().hex[:10]}@test.local"
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _ingest_two(client, headers):
    return client.post("/api/collect-tasks/ingest", headers=headers, json={
        "source": "plugin", "platform": "temu",
        "items": [
            {"url": "https://img.kwcdn.com/p/a.jpg", "title": "Cup A",
             "price": "12.57", "rating": "4.5", "source_url": "https://temu.com/p/1"},
            {"url": "https://m.media-amazon.com/images/I/x.jpg", "title": "Bottle B",
             "price": "15.78", "rating": "4.0", "source_url": "https://amazon.com/dp/2",
             "platform": "amazon"},
        ],
    })


def test_full_pipeline(client, auth_headers, monkeypatch):
    monkeypatch.setattr(cs, "_fetch_image", _fake_fetch)

    # ── ingest:暂存(未同步,零存储)──
    r = _ingest_two(client, auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2

    # ── staging:两条未同步 ──
    items = client.get("/api/collect-tasks/staging", headers=auth_headers).json()["items"]
    assert len(items) == 2
    assert all(not it["synced"] for it in items)
    assert {it["platform"] for it in items} == {"temu", "amazon"}
    ids = [it["id"] for it in items]

    used0 = client.get("/api/space/quota", headers=auth_headers).json()["used_bytes"]

    # ── sync:服务端取图入库 ──
    r = client.post("/api/collect-tasks/sync", headers=auth_headers, json={"image_ids": ids})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] == 2 and body["failed"] == 0

    # ── 找图库:按平台分组,带 asset_url + risk ──
    groups = client.get("/api/space/collected", headers=auth_headers).json()["groups"]
    plats = {g["platform"] for g in groups}
    assert "temu" in plats and "amazon" in plats
    sample = groups[0]["items"][0]
    assert sample["asset_url"].startswith("/files/")
    assert "risk" in sample and sample["title"]

    # ── 存储随同步增长 ──
    used1 = client.get("/api/space/quota", headers=auth_headers).json()["used_bytes"]
    assert used1 > used0

    # ── staging 清空(都已同步)──
    assert client.get("/api/collect-tasks/staging", headers=auth_headers).json()["items"] == []


def test_looks_like_junk_unit():
    # 真商品图 → 不是 junk
    assert cs._looks_like_junk("https://m.media-amazon.com/images/I/71abc.jpg", "amazon") is False
    assert cs._looks_like_junk("https://img.kwcdn.com/p/abc.jpg", "temu") is False
    # 亚马逊非 /images/I/(导航雪碧图/UI) → junk
    assert cs._looks_like_junk("https://m.media-amazon.com/images/G/01/nav_sprite.png", "amazon") is True
    assert cs._looks_like_junk("https://m.media-amazon.com/images/S/xx.png", "amazon") is True
    # 通用关键词 logo/icon/sprite/favicon → junk
    assert cs._looks_like_junk("https://cdn.x.com/assets/logo.svg", "unknown") is True
    assert cs._looks_like_junk("https://cdn.x.com/icons/cart.png", "unknown") is True
    assert cs._looks_like_junk("https://cdn.x.com/sprite-nav.png", "unknown") is True
    assert cs._looks_like_junk("", "amazon") is True


def test_ingest_filters_junk(client, auth_headers):
    r = client.post("/api/collect-tasks/ingest", headers=auth_headers, json={
        "source": "plugin", "platform": "amazon", "items": [
            {"url": "https://m.media-amazon.com/images/I/71real.jpg", "title": "Real Cup"},
            {"url": "https://m.media-amazon.com/images/G/01/nav_sprite._CB.png", "title": "sprite"},
            {"url": "https://m.media-amazon.com/images/S/abcd.png", "title": "ui"},
            {"url": "https://cdn.x.com/assets/brand-logo.svg", "title": "logo", "platform": "unknown"},
        ],
    })
    assert r.status_code == 200
    assert r.json()["count"] == 1  # 只留真商品图,商标/雪碧图/UI 全过滤
    items = client.get("/api/collect-tasks/staging", headers=auth_headers).json()["items"]
    assert len(items) == 1 and items[0]["title"] == "Real Cup"


def test_ingest_requires_auth(client):
    r = client.post("/api/collect-tasks/ingest", json={"items": [{"url": "https://x/a.jpg"}]})
    assert r.status_code in (401, 403)


def test_ingest_empty_items_400(client, auth_headers):
    r = client.post("/api/collect-tasks/ingest", headers=auth_headers, json={"items": []})
    assert r.status_code == 400


def test_sync_empty_400(client, auth_headers):
    r = client.post("/api/collect-tasks/sync", headers=auth_headers, json={"image_ids": []})
    assert r.status_code == 400


def test_delete_staging(client, auth_headers):
    client.post("/api/collect-tasks/ingest", headers=auth_headers, json={
        "platform": "temu", "items": [{"url": "https://img.kwcdn.com/p/z.jpg", "title": "Z"}]})
    items = client.get("/api/collect-tasks/staging", headers=auth_headers).json()["items"]
    zids = [it["id"] for it in items if it["title"] == "Z"]
    assert zids
    r = client.request("DELETE", "/api/collect-tasks/staging", headers=auth_headers,
                       json={"image_ids": zids})
    assert r.status_code == 200 and r.json()["deleted"] == len(zids)
    items2 = client.get("/api/collect-tasks/staging", headers=auth_headers).json()["items"]
    assert all(it["title"] != "Z" for it in items2)


def test_delete_collected_to_trash_and_release(client, auth_headers, monkeypatch):
    monkeypatch.setattr(cs, "_fetch_image", _fake_fetch)
    client.post("/api/collect-tasks/ingest", headers=auth_headers, json={
        "platform": "temu", "items": [{"url": "https://img.kwcdn.com/p/t.jpg", "title": "Trash me"}]})
    sid = [it["id"] for it in client.get("/api/collect-tasks/staging", headers=auth_headers).json()["items"]
           if it["title"] == "Trash me"]
    client.post("/api/collect-tasks/sync", headers=auth_headers, json={"image_ids": sid})

    groups = client.get("/api/space/collected", headers=auth_headers).json()["groups"]
    img = next(it for g in groups for it in g["items"] if it["title"] == "Trash me")

    # 删除 → 进回收站
    r = client.delete(f"/api/space/collected/{img['id']}", headers=auth_headers)
    assert r.status_code == 200
    groups2 = client.get("/api/space/collected", headers=auth_headers).json()["groups"]
    assert all(it["title"] != "Trash me" for g in groups2 for it in g["items"])
    trash = client.get("/api/space/trash", headers=auth_headers).json()["items"]
    assert any(trash)

    # 永久删除 → 释放存储
    used_before = client.get("/api/space/quota", headers=auth_headers).json()["used_bytes"]
    client.delete(f"/api/space/assets/{img['asset_id']}/purge", headers=auth_headers)
    used_after = client.get("/api/space/quota", headers=auth_headers).json()["used_bytes"]
    assert used_after <= used_before


def test_delete_collected_cross_user_404(client, auth_headers, monkeypatch):
    monkeypatch.setattr(cs, "_fetch_image", _fake_fetch)
    client.post("/api/collect-tasks/ingest", headers=auth_headers, json={
        "platform": "temu", "items": [{"url": "https://img.kwcdn.com/p/o.jpg", "title": "Owned"}]})
    sid = [it["id"] for it in client.get("/api/collect-tasks/staging", headers=auth_headers).json()["items"]
           if it["title"] == "Owned"]
    client.post("/api/collect-tasks/sync", headers=auth_headers, json={"image_ids": sid})
    groups = client.get("/api/space/collected", headers=auth_headers).json()["groups"]
    img = next(it for g in groups for it in g["items"] if it["title"] == "Owned")

    other = _register(client)
    r = client.delete(f"/api/space/collected/{img['id']}", headers=other)
    assert r.status_code == 404
