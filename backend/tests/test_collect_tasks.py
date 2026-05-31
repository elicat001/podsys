"""采集任务列表持久化 — 端到端测试。

注意:
- conftest 已把 POD_DATA_DIR 指向临时目录并 import 了 app.main(触发 init_db)。
  但 init_db 目前只 import models_db,新表尚未建出 → 这里显式 create_all。
- main.py 尚未注册本路由(由 Tech Lead 收口),测试里把 router 挂到 app 上,
  不修改 main.py 本体。
"""
from __future__ import annotations

import uuid

from app.main import app
from app.db import engine, Base
from app.models_collect import CollectionTask, CollectedImage  # noqa: F401  注册表
from app.routers.collect_tasks import router as collect_router

# 确保新表建出 + 路由可用(幂等)
Base.metadata.create_all(engine)
if not any(getattr(r, "path", "").startswith("/api/collect-tasks") for r in app.routes):
    # main.py 把 StaticFiles 挂在 "/"(catch-all),include 在其后会被它拦截 →
    # 把新路由插到该 mount 之前,保证 /api/* 命中。Tech Lead 收口时在 main.py
    # 注册顺序天然在 mount 之前,无此问题。
    before = len(app.router.routes)
    app.include_router(collect_router)
    new_routes = app.router.routes[before:]
    del app.router.routes[before:]
    mount_idx = next(
        (i for i, r in enumerate(app.router.routes)
         if getattr(r, "path", "") == "" and r.__class__.__name__ == "Mount"),
        len(app.router.routes),
    )
    app.router.routes[mount_idx:mount_idx] = new_routes


AMAZON = "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX466_.jpg"
ETSY = "https://i.etsystatic.com/123/r/il/abc/456/il_340x270.456.jpg"
TEMU = "https://img.temu.com/a/b.jpg?width=200"


def _register(client) -> dict:
    email = f"user_{uuid.uuid4().hex[:10]}@test.local"
    resp = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_create_task_upgrades_hires_and_platform(client, auth_headers):
    resp = client.post(
        "/api/collect-tasks",
        json={"source": "plugin", "urls": [AMAZON, ETSY, TEMU]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 3
    task_id = body["task_id"]

    detail = client.get(f"/api/collect-tasks/{task_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    images = detail.json()["images"]
    assert len(images) == 3
    by_platform = {img["platform"]: img for img in images}

    assert "amazon" in by_platform
    assert "_AC_SX466_" not in by_platform["amazon"]["hires_url"]
    assert by_platform["amazon"]["hires_url"].endswith("71abcXYZ.jpg")

    assert "etsy" in by_platform
    assert "il_fullxfull" in by_platform["etsy"]["hires_url"]

    assert "temu" in by_platform
    assert "width=200" not in by_platform["temu"]["hires_url"]


def test_list_contains_task(client, auth_headers):
    resp = client.post(
        "/api/collect-tasks", json={"urls": [AMAZON]}, headers=auth_headers
    )
    task_id = resp.json()["task_id"]
    listing = client.get("/api/collect-tasks", headers=auth_headers)
    assert listing.status_code == 200
    ids = [t["id"] for t in listing.json()]
    assert task_id in ids
    row = next(t for t in listing.json() if t["id"] == task_id)
    assert row["count"] == 1
    assert row["source"] == "plugin"
    assert row["status"] == "collected"


def test_other_user_cannot_read_task(client, auth_headers):
    resp = client.post(
        "/api/collect-tasks", json={"urls": [AMAZON]}, headers=auth_headers
    )
    task_id = resp.json()["task_id"]

    other = _register(client)
    detail = client.get(f"/api/collect-tasks/{task_id}", headers=other)
    assert detail.status_code == 404


def test_select_marks_selected(client, auth_headers):
    resp = client.post(
        "/api/collect-tasks", json={"urls": [AMAZON, ETSY, TEMU]}, headers=auth_headers
    )
    task_id = resp.json()["task_id"]
    detail = client.get(f"/api/collect-tasks/{task_id}", headers=auth_headers).json()
    image_ids = [detail["images"][0]["id"], detail["images"][1]["id"]]

    sel = client.post(
        f"/api/collect-tasks/{task_id}/select",
        json={"image_ids": image_ids},
        headers=auth_headers,
    )
    assert sel.status_code == 200
    assert sel.json()["updated"] == 2

    after = client.get(f"/api/collect-tasks/{task_id}", headers=auth_headers).json()
    selected_map = {img["id"]: img["selected"] for img in after["images"]}
    assert selected_map[image_ids[0]] is True
    assert selected_map[image_ids[1]] is True
    # 未选中的保持 False
    others = [v for k, v in selected_map.items() if k not in image_ids]
    assert all(v is False for v in others)


def test_other_user_cannot_select(client, auth_headers):
    resp = client.post(
        "/api/collect-tasks", json={"urls": [AMAZON]}, headers=auth_headers
    )
    task_id = resp.json()["task_id"]
    detail = client.get(f"/api/collect-tasks/{task_id}", headers=auth_headers).json()
    img_id = detail["images"][0]["id"]

    other = _register(client)
    sel = client.post(
        f"/api/collect-tasks/{task_id}/select",
        json={"image_ids": [img_id]},
        headers=other,
    )
    assert sel.status_code == 404


def test_empty_urls_400(client, auth_headers):
    resp = client.post("/api/collect-tasks", json={"urls": []}, headers=auth_headers)
    assert resp.status_code == 400


def test_requires_auth(client):
    resp = client.post("/api/collect-tasks", json={"urls": [AMAZON]})
    assert resp.status_code == 401
