"""测试:我的工作流持久化(owner 隔离)。"""
from __future__ import annotations
import uuid

# 确保表存在(本批新表,conftest 不会自动建)
from app.models_workflow import SavedWorkflow  # noqa: F401
from app.db import engine, Base
Base.metadata.create_all(engine)

# 集成点由 Tech Lead 在 main.py 收口(app.include_router(my_workflows.router));
# 测试环境下若尚未挂载则在此自挂,保证本套件可独立运行(不改 main.py/conftest)。
from starlette.routing import Mount  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import my_workflows as _my_workflows  # noqa: E402

if not any(getattr(r, "path", "").startswith("/api/my-workflows") for r in app.routes):
    _before = len(app.routes)
    app.include_router(_my_workflows.router)
    # 新增路由要排在末尾的静态 catch-all Mount(path="")之前才能命中
    _new = app.routes[_before:]
    del app.routes[_before:]
    _mount_idx = next((i for i, r in enumerate(app.routes) if isinstance(r, Mount)),
                      len(app.routes))
    app.routes[_mount_idx:_mount_idx] = _new


def _register(client) -> dict:
    email = f"wf_{uuid.uuid4().hex[:10]}@test.local"
    resp = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_create_list_get_delete(client, auth_headers):
    # 建
    resp = client.post("/api/my-workflows",
                       json={"name": "我的流", "steps": ["extract", "mockup"]},
                       headers=auth_headers)
    assert resp.status_code == 200, resp.text
    wf_id = resp.json()["id"]
    assert resp.json()["name"] == "我的流"

    # 列表含之
    resp = client.get("/api/my-workflows", headers=auth_headers)
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.json()]
    assert wf_id in ids

    # 详情 steps 正确
    resp = client.get(f"/api/my-workflows/{wf_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["steps"] == ["extract", "mockup"]

    # 删除后 404
    resp = client.delete(f"/api/my-workflows/{wf_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    resp = client.get(f"/api/my-workflows/{wf_id}", headers=auth_headers)
    assert resp.status_code == 404


def test_empty_steps_400(client, auth_headers):
    resp = client.post("/api/my-workflows",
                       json={"name": "空流", "steps": []},
                       headers=auth_headers)
    assert resp.status_code == 400


def test_owner_isolation(client, auth_headers):
    # 用户 A 建一个
    resp = client.post("/api/my-workflows",
                       json={"name": "A 的流", "steps": ["extract"]},
                       headers=auth_headers)
    assert resp.status_code == 200
    wf_id = resp.json()["id"]

    # 用户 B 取 / 删 → 404
    other = _register(client)
    assert client.get(f"/api/my-workflows/{wf_id}", headers=other).status_code == 404
    assert client.delete(f"/api/my-workflows/{wf_id}", headers=other).status_code == 404


def test_unauthorized_401(client):
    assert client.get("/api/my-workflows").status_code == 401
    assert client.post("/api/my-workflows",
                       json={"name": "x", "steps": ["extract"]}).status_code == 401
