"""自定义工作流路由测试:GET /api/workflows/steps + POST /api/workflows/run-custom。

TestClient 下 BackgroundTasks 在响应返回后同步执行,故 POST 返回时作业已跑完,
轮询 GET /api/jobs/{id} 立即可见 done。
"""
from __future__ import annotations

# 集成由 Tech Lead 在 main.py 收口(在静态 Mount 之前 include workflow_custom 路由)。
# 本测试自包含:若该路由尚未挂载(TL 未收口前),在导入时幂等挂载,保证 E1 可独立验收。
# 注意:main.py 末尾挂了 catch-all 静态 Mount('/{path}'),后 append 的路由会被它截胡,
# 故必须把新路由插到该 Mount 之前(模拟 TL 在 main.py 静态挂载前 include 的真实顺序)。
from starlette.routing import Mount
from app.main import app
from app.routers import workflow_custom as _wc

if not any(getattr(r, "path", "") == "/api/workflows/run-custom" for r in app.router.routes):
    _before = len(app.router.routes)
    app.include_router(_wc.router)
    _new = app.router.routes[_before:]
    del app.router.routes[_before:]
    _mount_idx = next((i for i, r in enumerate(app.router.routes) if isinstance(r, Mount)),
                      len(app.router.routes))
    app.router.routes[_mount_idx:_mount_idx] = _new


def _credits(client, headers) -> int:
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["credits"]


# ---------------- GET /api/workflows/steps ----------------
def test_list_steps_ok(client, auth_headers):
    r = client.get("/api/workflows/steps", headers=auth_headers)
    assert r.status_code == 200, r.text
    steps = r.json()
    ids = {s["id"] for s in steps}
    expected = {"extract", "split", "mockup", "production", "title",
                "variants", "compress", "seamless"}
    assert expected <= ids
    assert len(steps) >= 8
    for s in steps:
        assert "id" in s
        assert "label" in s
        assert "category" in s
        assert "needs_ai" in s and isinstance(s["needs_ai"], bool)
        assert "offline" in s and isinstance(s["offline"], bool)


def test_list_steps_unauthorized(client):
    r = client.get("/api/workflows/steps")
    assert r.status_code == 401


# ---------------- POST /api/workflows/run-custom ----------------
def test_run_custom_ok(client, auth_headers, png):
    before = _credits(client, auth_headers)
    r = client.post(
        "/api/workflows/run-custom",
        headers=auth_headers,
        data={"steps": "extract,mockup,compress", "params": "{}"},
        files={"file": ("in.png", png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    job_id = body["job_id"]
    assert body["status"] == "pending"

    # 扣 2 点(process)
    assert _credits(client, auth_headers) == before - 2

    # 轮询作业(TestClient 下后台任务已同步跑完)
    jr = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert jr.status_code == 200, jr.text
    job = jr.json()
    assert job["status"] == "done", job
    outputs = job["result"]["outputs"]
    assert any("compressed." in o for o in outputs), outputs
    assert job["result"]["steps_run"] == ["extract", "mockup", "compress"]


def test_run_custom_invalid_step_refunds(client, auth_headers, png):
    before = _credits(client, auth_headers)
    r = client.post(
        "/api/workflows/run-custom",
        headers=auth_headers,
        data={"steps": "extract,nope"},
        files={"file": ("in.png", png(), "image/png")},
    )
    assert r.status_code == 400, r.text
    # 退点:余额不变
    assert _credits(client, auth_headers) == before


def test_run_custom_empty_steps_refunds(client, auth_headers, png):
    before = _credits(client, auth_headers)
    r = client.post(
        "/api/workflows/run-custom",
        headers=auth_headers,
        data={"steps": "  ,  "},
        files={"file": ("in.png", png(), "image/png")},
    )
    assert r.status_code == 400, r.text
    assert _credits(client, auth_headers) == before


def test_run_custom_bad_params_refunds(client, auth_headers, png):
    before = _credits(client, auth_headers)
    r = client.post(
        "/api/workflows/run-custom",
        headers=auth_headers,
        data={"steps": "extract", "params": "{not json"},
        files={"file": ("in.png", png(), "image/png")},
    )
    assert r.status_code == 400, r.text
    assert _credits(client, auth_headers) == before


def test_run_custom_unauthorized(client, png):
    r = client.post(
        "/api/workflows/run-custom",
        data={"steps": "extract"},
        files={"file": ("in.png", png(), "image/png")},
    )
    assert r.status_code == 401
