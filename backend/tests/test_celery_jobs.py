"""Celery 异步作业链路测试(Phase A:印花提取试点)。

测试在 eager 模式下跑(conftest 设 POD_CELERY_EAGER=true):`.delay()` 在测试进程内同步执行,
**不连 Redis、不起 worker**。所以可以:POST 端点 → 拿 job_id → 任务已同步跑完 → 轮询 Job 看终态。
覆盖:成功落 done + 产物 + 时间戳/tool_id;失败落 error + 退点;owner 隔离。
"""
from __future__ import annotations

from app.db import Base, engine

# 确保新列/表已建(新临时库由 create_all 直接建全)
Base.metadata.create_all(engine)


def _enable_ai(monkeypatch):
    """打开印花提取的 AI 路径(走 Celery),并避免真调网关:fake 掉提取函数。"""
    from app.config import settings
    monkeypatch.setattr(settings, "print_extract_ai", True)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-fake")


def _fake_design():
    from PIL import Image
    img = Image.new("RGBA", (64, 64), (10, 200, 120, 255))
    return img, {"method": "ai_flatten", "engine": "ai", "size": [64, 64]}


def test_print_extract_celery_success(client, auth_headers, png, monkeypatch):
    _enable_ai(monkeypatch)
    import app.tasks as tasks
    monkeypatch.setattr(tasks, "extract_print_design", lambda src: _fake_design())

    resp = client.post("/api/print-extract", headers=auth_headers,
                       files={"file": ("in.png", png(), "image/png")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # AI 路径:立即返回 pending + job_id(任务在 eager 下已同步跑完)
    assert body["status"] == "pending"
    jid = body["job_id"]

    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["tool_id"] == "extract"
    assert job["result"]["image_url"].endswith("/design.png")
    assert job["result"]["white_url"].endswith("/design_white.png")
    # 时间戳:running→done 全程,started/finished/duration 都应有值
    assert job["started_at"] and job["finished_at"]
    assert job["duration_sec"] is not None and job["duration_sec"] >= 0

    # 产物可下载
    assert client.get(job["result"]["image_url"]).status_code == 200


def test_print_extract_celery_failure_refunds(client, auth_headers, monkeypatch, png):
    _enable_ai(monkeypatch)
    import app.tasks as tasks

    def _boom(src):
        raise RuntimeError("网关炸了")

    monkeypatch.setattr(tasks, "extract_print_design", _boom)

    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    resp = client.post("/api/print-extract", headers=auth_headers,
                       files={"file": ("in.png", png(), "image/png")})
    assert resp.status_code == 200
    jid = resp.json()["job_id"]

    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert job["status"] == "error", job
    assert "网关炸了" in job["error"]
    assert job["finished_at"]
    # 失败必退点:扣 2(process)后任务失败应退回,净额不变
    after = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    assert after == before, f"退点不平:before={before} after={after}"


def test_enqueue_broker_down_refunds(client, auth_headers, monkeypatch, png):
    """broker 不可用(.delay 抛错)→ 端点退点 + 502 + 作业标记失败(不静默吞点)。"""
    _enable_ai(monkeypatch)
    import app.routers.print_extract as pr

    def _boom_delay(jid):
        raise RuntimeError("Error connecting to redis")

    monkeypatch.setattr(pr.run_print_extract, "delay", _boom_delay)

    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    resp = client.post("/api/print-extract", headers=auth_headers,
                       files={"file": ("in.png", png(), "image/png")})
    assert resp.status_code == 502, resp.text
    after = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    assert after == before, f"broker 挂了应退点:before={before} after={after}"


def test_job_timestamps_are_utc_aware(client, auth_headers, monkeypatch, png):
    """作业时间戳序列化必须带 UTC 偏移(否则前端按本地时区解析会差几小时)。"""
    _enable_ai(monkeypatch)
    import app.tasks as tasks
    monkeypatch.setattr(tasks, "extract_print_design", lambda src: _fake_design())
    jid = client.post("/api/print-extract", headers=auth_headers,
                      files={"file": ("in.png", png(), "image/png")}).json()["job_id"]
    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    for field in ("created_at", "started_at", "finished_at"):
        assert job[field] and job[field].endswith("+00:00"), f"{field}={job[field]} 缺 UTC 偏移"


def test_variants_celery_dispatch(client, auth_headers, png, monkeypatch):
    """run_tool 分派:图裂变(按张扣 n*4)→ Celery 作业出 n 张图 + 计费正确。"""
    from PIL import Image
    from app.config import settings
    from app.services import design_tools as dt
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(dt, "make_variants",
                        lambda src, n, prompt="", prefer_local=False: [Image.new("RGB", (16, 16), (i * 40, 0, 0)) for i in range(n)])

    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    resp = client.post("/api/design-tools/variants", headers=auth_headers,
                       data={"n": 3}, files={"file": ("a.png", png(), "image/png")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    job = client.get(f"/api/jobs/{resp.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["tool_id"] == "variants"
    assert len(job["result"]["images"]) == 3
    after = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    assert after == before - 12, f"3张×edit(4)应扣12:before={before} after={after}"


def test_generate_celery_dispatch(client, auth_headers, monkeypatch):
    """run_tool 分派:文生图(无输入图)→ Celery 作业出图 + 带 prompt_used。"""
    from PIL import Image
    from app.config import settings
    from app.services import generate as gen
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(gen, "text_to_image", lambda prompt, size="1024x1024": Image.new("RGB", (32, 32), (0, 128, 0)))

    resp = client.post("/api/generate", headers=auth_headers, data={"prompt": "a happy cat"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    job = client.get(f"/api/jobs/{resp.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["tool_id"] == "generate"
    assert job["result"]["image_url"].endswith("generated.png")
    assert job["result"]["prompt_used"]


def test_variants_broker_down_refunds_n(client, auth_headers, png, monkeypatch):
    """按张扣的端点 broker 挂了 → 退回全部 n 笔(退点笔数对齐)。"""
    from app.config import settings
    import app.routers.design_tools as dtr

    def _boom(jid):
        raise RuntimeError("Error connecting to redis")

    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(dtr.run_tool, "delay", _boom)

    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    resp = client.post("/api/design-tools/variants", headers=auth_headers,
                       data={"n": 3}, files={"file": ("a.png", png(), "image/png")})
    assert resp.status_code == 502, resp.text
    after = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    assert after == before, f"broker 挂了应退回 3 笔:before={before} after={after}"


def test_reaper_marks_stale_running_error_and_refunds(client, auth_headers):
    """超时还 running 的僵尸 → 列表端点惰性清理为 error + 退点。"""
    from datetime import datetime, timezone, timedelta
    from app.db import SessionLocal
    from app.models_db import Job
    me = client.get("/api/auth/me", headers=auth_headers).json()
    uid, before = me["user_id"], me["credits"]
    s = SessionLocal()
    try:
        s.add(Job(id="stalejob0001", kind="vectorize", tool_id="vectorize", status="running",
                  params={}, result={}, error="", owner_id=uid,
                  created_at=datetime.now(timezone.utc) - timedelta(hours=2)))
        s.commit()
    finally:
        s.close()
    jobs = client.get("/api/jobs", headers=auth_headers).json()
    stale = next(x for x in jobs if x["id"] == "stalejob0001")
    assert stale["status"] == "error", stale
    after = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    assert after == before + 2, f"vectorize(process=2)应退回:before={before} after={after}"


def test_delete_job_trashes_asset(client, auth_headers, png, monkeypatch):
    """删除任务 → 作业行删除(再查 404)+ 关联素材进回收站。"""
    _enable_ai(monkeypatch)
    import app.tasks as tasks
    monkeypatch.setattr(tasks, "extract_print_design", lambda src: _fake_design())
    jid = client.post("/api/print-extract", headers=auth_headers,
                      files={"file": ("a.png", png(), "image/png")}).json()["job_id"]
    assert client.get(f"/api/jobs/{jid}", headers=auth_headers).json()["status"] == "done"

    assert client.delete(f"/api/jobs/{jid}", headers=auth_headers).status_code == 200
    assert client.get(f"/api/jobs/{jid}", headers=auth_headers).status_code == 404
    trash = client.get("/api/space/trash", headers=auth_headers).json()["items"]
    assert any(t["name"] == "印花提取" for t in trash), trash


def test_quota_over_rejects_and_refunds(client, auth_headers, png, monkeypatch):
    """超出存储配额 → 413 + 退点(不往满盘塞)。"""
    monkeypatch.setattr("app.services.quota.usage",
                        lambda db, uid: {"over": True, "used_bytes": 9, "quota_bytes": 1, "percent": 900.0})
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/vectorize", headers=auth_headers,
                    data={"colors": 6}, files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 413, r.text
    after = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    assert after == before, f"超容应退点:before={before} after={after}"


def test_job_owner_isolation(client, auth_headers, monkeypatch, png):
    """他人作业 404(不泄露存在性)。"""
    _enable_ai(monkeypatch)
    import app.tasks as tasks
    monkeypatch.setattr(tasks, "extract_print_design", lambda src: _fake_design())

    jid = client.post("/api/print-extract", headers=auth_headers,
                      files={"file": ("in.png", png(), "image/png")}).json()["job_id"]

    # 另一个用户访问该作业 → 404
    other = client.post("/api/auth/register",
                        json={"email": "other_celery@test.local", "password": "pw123456"})
    other_headers = {"Authorization": f"Bearer {other.json()['token']}"}
    assert client.get(f"/api/jobs/{jid}", headers=other_headers).status_code == 404
