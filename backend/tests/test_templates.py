"""batch10 E4:模板(刊登模板 + 导出模板)测试。

用 conftest 的 client / auth_headers fixture;通过 HTTP 接口建/列/删。
测试顶部确保表存在(新表)。
"""
from __future__ import annotations

import uuid

# 先 import app(触发 init_db 注册 users 等所有表的 mapper),再建新表,
# 否则 ListingTemplate/ExportTemplate 的 FK(users.id)在 create_all 时无法解析。
from app.main import app  # noqa: E402
from app.routers import templates as _templates_router  # noqa: E402

# 确保新表已建(独立文件,init_db 之外也能跑)
from app.models_template import ListingTemplate, ExportTemplate  # noqa: F401,E402
from app.db import engine, Base  # noqa: E402

Base.metadata.create_all(engine)

# 本路由集成由 TL 在 main.py 收口(在静态根挂载之前 include);测试期自挂载,
# 保证本测试独立可验。注意:main.py 末尾把 StaticFiles 挂在 "/",
# Starlette 按顺序匹配,故后追加的路由会被根挂载吞掉 → 必须插到根挂载之前。
if not any(getattr(r, "path", "") == "/api/templates/listing" for r in app.routes):
    _before = len(app.routes)
    app.include_router(_templates_router.router)
    _added = app.routes[_before:]
    del app.routes[_before:]
    # 找到根静态挂载 "/" 的位置,把新路由插到它前面(没有则直接追加)
    _mount_idx = next(
        (i for i, r in enumerate(app.routes) if getattr(r, "path", "") == ""), len(app.routes)
    )
    app.router.routes[_mount_idx:_mount_idx] = _added


def _register(client) -> dict:
    """注册一个新随机用户,返回 Bearer 头(本地辅助,避免改 conftest)。"""
    email = f"user_{uuid.uuid4().hex[:10]}@test.local"
    resp = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# --- 刊登模板 ------------------------------------------------------------
def test_listing_create_list_delete(client, auth_headers):
    # 建
    r = client.post("/api/templates/listing",
                    json={"name": "temu默认", "platform": "temu", "fields": {"title": "T恤"}},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert r.json()["platform"] == "temu"
    assert r.json()["fields"] == {"title": "T恤"}

    # GET 含
    r = client.get("/api/templates/listing", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert any(t["id"] == tid for t in r.json())

    # DELETE
    r = client.delete(f"/api/templates/listing/{tid}", headers=auth_headers)
    assert r.status_code == 200, r.text

    # GET 不含
    r = client.get("/api/templates/listing", headers=auth_headers)
    assert all(t["id"] != tid for t in r.json())


def test_listing_default_fields(client, auth_headers):
    r = client.post("/api/templates/listing", json={"name": "极简"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["fields"] == {}
    assert r.json()["platform"] == ""


def test_listing_empty_name_422(client, auth_headers):
    r = client.post("/api/templates/listing", json={"name": ""}, headers=auth_headers)
    assert r.status_code == 422, r.text
    r = client.post("/api/templates/listing", json={"name": "   "}, headers=auth_headers)
    assert r.status_code == 422, r.text


def test_listing_delete_other_owner_404(client, auth_headers):
    r = client.post("/api/templates/listing", json={"name": "我的"}, headers=auth_headers)
    tid = r.json()["id"]
    other = _register(client)
    r = client.delete(f"/api/templates/listing/{tid}", headers=other)
    assert r.status_code == 404, r.text
    # 越权也看不到
    r = client.get("/api/templates/listing", headers=other)
    assert all(t["id"] != tid for t in r.json())


def test_listing_requires_auth(client):
    assert client.get("/api/templates/listing").status_code == 401
    assert client.post("/api/templates/listing", json={"name": "x"}).status_code == 401


# --- 导出模板 ------------------------------------------------------------
def test_export_create_list_delete(client, auth_headers):
    r = client.post("/api/templates/export", json={"name": "T恤30x40"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    tid = body["id"]
    assert body["dpi"] == 300
    assert body["width_cm"] == 30.0
    assert body["height_cm"] == 40.0
    assert body["fmt"] == "png"

    # GET 含
    r = client.get("/api/templates/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert any(t["id"] == tid for t in r.json())

    # DELETE 删除
    r = client.delete(f"/api/templates/export/{tid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    r = client.get("/api/templates/export", headers=auth_headers)
    assert all(t["id"] != tid for t in r.json())


def test_export_custom_values(client, auth_headers):
    r = client.post("/api/templates/export",
                    json={"name": "海报", "dpi": 150, "width_cm": 21.0,
                          "height_cm": 29.7, "fmt": "jpg"},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dpi"] == 150
    assert body["fmt"] == "jpg"
    assert body["height_cm"] == 29.7


def test_export_empty_name_422(client, auth_headers):
    r = client.post("/api/templates/export", json={"name": ""}, headers=auth_headers)
    assert r.status_code == 422, r.text


def test_export_delete_other_owner_404(client, auth_headers):
    r = client.post("/api/templates/export", json={"name": "我的导出"}, headers=auth_headers)
    tid = r.json()["id"]
    other = _register(client)
    r = client.delete(f"/api/templates/export/{tid}", headers=other)
    assert r.status_code == 404, r.text


def test_export_requires_auth(client):
    assert client.get("/api/templates/export").status_code == 401
    assert client.post("/api/templates/export", json={"name": "x"}).status_code == 401
