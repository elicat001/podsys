"""工作流编排引擎:预设列举 + 一键串联执行(异步,经 Job 系统)。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw

from app.services.workflow import run_workflow, list_workflows, STEP_REGISTRY


def _png() -> io.BytesIO:
    img = Image.new("RGB", (400, 400), (255, 255, 255))
    ImageDraw.Draw(img).ellipse([100, 100, 300, 300], fill=(40, 120, 200))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------- 引擎单元(离线,不经 HTTP) ----------
def test_step_registry_has_core_steps():
    for s in ("extract", "split", "mockup", "production", "title"):
        assert s in STEP_REGISTRY


def test_run_workflow_offline_produces_outputs(tmp_path, monkeypatch):
    img = Image.open(_png())
    out = run_workflow(img, "tee-extract-mockup", job_id="wftest1")
    assert out["workflow"] == "tee-extract-mockup"
    assert "extract" in out["steps_run"] and "production" in out["steps_run"]
    assert any("production.png" in u for u in out["outputs"])
    assert any("mockup_" in u for u in out["outputs"])
    assert out["meta"].get("production", {}).get("dpi") == 300


def test_tee_full_pipeline_offline(monkeypatch):
    """新链路:variants 无 key 优雅跳过,compress 离线真实产出,不让工作流失败。"""
    img = Image.open(_png())
    out = run_workflow(img, "tee-full", job_id="wffull1")
    # variants 因无 key 被跳过(记录在 meta.skipped),但工作流整体成功
    assert any("variants" in s for s in out["meta"].get("skipped", []))
    # compress 真实产出 + 生产图 + 标题
    assert "compress" in out["meta"] and out["meta"]["compress"]["output_bytes"] > 0
    assert any("production.png" in u for u in out["outputs"])
    assert any("compressed." in u for u in out["outputs"])
    assert out["meta"].get("title")


def test_run_workflow_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        run_workflow(Image.open(_png()), "nope", job_id="x")


# ---------- HTTP 层 ----------
def test_list_workflows_endpoint(client):
    r = client.get("/api/workflows")
    assert r.status_code == 200
    ids = {w["id"] for w in r.json()}
    assert {"phone-remake", "tee-extract-mockup", "canvas-series"} <= ids


def test_run_workflow_async_completes(client, auth_headers):
    r = client.post("/api/workflows/run", headers=auth_headers,
                    data={"workflow_id": "tee-extract-mockup"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    j = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert j["status"] == "done", j
    assert any("production.png" in u for u in j["result"]["outputs"])
    assert j["result"]["steps_run"]  # 步骤被执行


def test_run_workflow_requires_auth(client):
    r = client.post("/api/workflows/run", data={"workflow_id": "tee-extract-mockup"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


def test_run_workflow_bad_id_returns_400_and_refunds(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/workflows/run", headers=auth_headers,
                    data={"workflow_id": "does-not-exist"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 400
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0, "未知工作流应退点"
