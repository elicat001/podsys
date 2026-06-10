"""我的空间深度:存储配额 + 回收站 + 资产筛选。"""
from __future__ import annotations

import uuid

from app.db import Base, engine, SessionLocal
from app.main import app
from app.models_db import Asset
from app.routers import space as space_router

# 确保新增列(deleted/batch/tags/size_bytes)所在表已建
Base.metadata.create_all(engine)

# main.py 的路由注册由 TL 收口;测试期幂等挂载 space 路由,保证本文件可独立跑绿。
# 注意:main.py 末尾把 StaticFiles 挂在 "/"(catch-all),必须把 API 路由插到该 mount 之前,
# 否则所有 /api/space/* 会被静态挂载吞掉返回 404。
if not any(getattr(r, "path", "").startswith("/api/space") for r in app.routes):
    # 找到根静态挂载的位置,把 space 路由插到它前面
    mount_idx = next(
        (i for i, r in enumerate(app.router.routes) if getattr(r, "path", "") == ""),
        len(app.router.routes),
    )
    for i, route in enumerate(space_router.router.routes):
        app.router.routes.insert(mount_idx + i, route)


def _add_asset(client, headers, *, png, shape: str, source: str = "upload", seed: int = 0):
    buf = png(shape=shape, seed=seed)
    resp = client.post(
        "/api/assets",
        headers=headers,
        files={"file": (f"{shape}_{seed}.png", buf, "image/png")},
        data={"source": source},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["asset_id"]


def test_quota_counts_bytes_and_categories(client, auth_headers, png):
    a1 = _add_asset(client, auth_headers, png=png, shape="circle", source="upload", seed=1)
    a2 = _add_asset(client, auth_headers, png=png, shape="rect", source="collected", seed=2)
    a3 = _add_asset(client, auth_headers, png=png, shape="noise", source="upload", seed=3)
    assert len({a1, a2, a3}) == 3

    resp = client.get("/api/space/quota", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    q = resp.json()
    assert q["used_bytes"] > 0
    assert q["quota_bytes"] == 2 * 1024 ** 3
    assert 0 <= q["percent"] <= 100
    assert q["over"] is False
    # collected 1 张,material(upload)2 张
    assert q["by_category"]["collected"]["count"] == 1
    assert q["by_category"]["material"]["count"] == 2
    assert q["by_category"]["material"]["bytes"] > 0
    assert q["trash"]["count"] == 0


def test_trash_restore_purge_flow(client, auth_headers, png):
    a1 = _add_asset(client, auth_headers, png=png, shape="circle", source="upload", seed=11)
    a2 = _add_asset(client, auth_headers, png=png, shape="rect", source="upload", seed=12)

    def asset_ids():
        r = client.get("/api/space/assets", headers=auth_headers)
        assert r.status_code == 200, r.text
        return {it["id"] for it in r.json()["items"]}

    assert {a1, a2} <= asset_ids()

    # trash a1
    r = client.post(f"/api/space/assets/{a1}/trash", headers=auth_headers)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert a1 not in asset_ids()

    # /trash 含 a1
    r = client.get("/api/space/trash", headers=auth_headers)
    assert r.status_code == 200
    trash_ids = {it["id"] for it in r.json()["items"]}
    assert a1 in trash_ids and a2 not in trash_ids

    # restore a1 -> 回到 /assets
    r = client.post(f"/api/space/assets/{a1}/restore", headers=auth_headers)
    assert r.status_code == 200 and r.json()["deleted"] is False
    assert a1 in asset_ids()

    # purge a2 -> 哪都不在
    r = client.delete(f"/api/space/assets/{a2}/purge", headers=auth_headers)
    assert r.status_code == 200 and r.json()["purged"] is True
    assert a2 not in asset_ids()
    r = client.get("/api/space/trash", headers=auth_headers)
    assert a2 not in {it["id"] for it in r.json()["items"]}
    # quota 中也不再统计 a2
    q = client.get("/api/space/quota", headers=auth_headers).json()
    assert q["trash"]["count"] == 0


def test_thumbnail_endpoint_caches_and_purge_cleans(client, png):
    """/files?w 返回缓存缩略图(更小 + immutable 头 + 盘上缓存、二次不重建);删文件时连缩略图一起删。"""
    from PIL import Image
    from app import storage
    from app.config import settings
    from app.routers import space as space_router

    # 造一张作业产出图(/files 路径形态)
    job_id = storage.new_job_id()
    p = storage.output_path(job_id, "asset.png")
    Image.open(png(shape="circle")).convert("RGBA").save(p, format="PNG")
    url = storage.output_url(job_id, "asset.png")  # /files/{job_id}/asset.png

    # 缩略图端点:200 + immutable 缓存头 + 盘上生成缓存 + 确实是小图(≤64)
    r = client.get(url + "?w=64")
    assert r.status_code == 200, r.text
    assert "immutable" in r.headers.get("cache-control", "")
    thumb = settings.outputs_dir / job_id / ".thumb_64_asset.png.png"
    assert thumb.is_file(), "缩略图应已缓存到盘"
    assert max(Image.open(thumb).size) <= 64

    # 二次请求只读缓存、不重建(mtime 不变)
    mtime = thumb.stat().st_mtime_ns
    client.get(url + "?w=64")
    assert thumb.stat().st_mtime_ns == mtime, "二次请求应直接读缓存,不重新生成"

    # 删文件(purge 走的同一函数)→ 原图 + 缩略图缓存都删
    a = type("A", (), {"path": url})()
    assert p.is_file()
    space_router._delete_asset_file(a)
    assert not p.exists() and not thumb.exists(), "删除应连缩略图缓存一起删"


def test_filter_by_source_and_tagged(client, auth_headers, png):
    up = _add_asset(client, auth_headers, png=png, shape="circle", source="upload", seed=21)
    col = _add_asset(client, auth_headers, png=png, shape="rect", source="collected", seed=22)

    # 给 up 设标签 + batch(assets router 不支持,直接 ORM)
    db = SessionLocal()
    try:
        a = db.get(Asset, up)
        a.tags = ["spring", "tee"]
        a.batch = "B-2026"
        db.add(a); db.commit()
    finally:
        db.close()

    # source=upload 只命中 up
    r = client.get("/api/space/assets", headers=auth_headers, params={"source": "upload"})
    ids = {it["id"] for it in r.json()["items"]}
    assert up in ids and col not in ids

    # source=collected 只命中 col
    r = client.get("/api/space/assets", headers=auth_headers, params={"source": "collected"})
    ids = {it["id"] for it in r.json()["items"]}
    assert col in ids and up not in ids

    # tagged=true 只命中有标签的 up
    r = client.get("/api/space/assets", headers=auth_headers, params={"tagged": "true"})
    ids = {it["id"] for it in r.json()["items"]}
    assert up in ids and col not in ids

    # tagged=false 只命中无标签的 col
    r = client.get("/api/space/assets", headers=auth_headers, params={"tagged": "false"})
    ids = {it["id"] for it in r.json()["items"]}
    assert col in ids and up not in ids

    # batch 过滤
    r = client.get("/api/space/assets", headers=auth_headers, params={"batch": "B-2026"})
    ids = {it["id"] for it in r.json()["items"]}
    assert up in ids and col not in ids


def test_cannot_trash_others_asset(client, auth_headers, png):
    # 用户 B 的资产
    email = f"other_{uuid.uuid4().hex[:8]}@test.local"
    reg = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert reg.status_code == 200, reg.text
    other_headers = {"Authorization": f"Bearer {reg.json()['token']}"}
    other_id = _add_asset(client, other_headers, png=png, shape="circle", source="upload", seed=31)

    # 用户 A 越权 trash / purge -> 404
    r = client.post(f"/api/space/assets/{other_id}/trash", headers=auth_headers)
    assert r.status_code == 404
    r = client.delete(f"/api/space/assets/{other_id}/purge", headers=auth_headers)
    assert r.status_code == 404


def test_requires_auth(client):
    assert client.get("/api/space/quota").status_code == 401
    assert client.get("/api/space/assets").status_code == 401
    assert client.get("/api/space/trash").status_code == 401
    assert client.post("/api/space/assets/1/trash").status_code == 401
