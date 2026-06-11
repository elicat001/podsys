"""Batch 8 评审整改回归:P0 run-custom 拒 AI step / P1 step 上限 + SavedWorkflow 校验。"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw


def _png() -> io.BytesIO:
    img = Image.new("RGB", (200, 160), (255, 255, 255))
    ImageDraw.Draw(img).ellipse([40, 40, 160, 120], fill=(40, 120, 200))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------- P0:run-custom 拒绝 AI step(防 N 倍计费套利) ----------
def test_run_custom_rejects_ai_step_and_refunds(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/workflows/run-custom", headers=auth_headers,
                    data={"steps": "extract,variants,mockup", "params": "{}"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 400, r.text
    assert "variants" in r.json()["detail"]
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0, "拒绝 AI step 应退点"


def test_run_custom_offline_still_ok(client, auth_headers):
    r = client.post("/api/workflows/run-custom", headers=auth_headers,
                    data={"steps": "extract,mockup,production", "params": "{}"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    assert client.get(f"/api/jobs/{jid}", headers=auth_headers).json()["status"] == "done"


# ---------- P1:step 数量上限(DoS) ----------
def test_run_custom_step_count_capped(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    steps = ",".join(["mockup"] * 25)  # > MAX_CUSTOM_STEPS(20)
    r = client.post("/api/workflows/run-custom", headers=auth_headers,
                    data={"steps": steps, "params": "{}"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 400
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


# ---------- P1:SavedWorkflow 入库前校验 step 合法 ----------
def test_save_workflow_rejects_invalid_step(client, auth_headers):
    r = client.post("/api/my-workflows", headers=auth_headers,
                    json={"name": "坏流", "steps": ["extract", "nope"]})
    assert r.status_code == 400, r.text


def test_save_workflow_rejects_empty_name(client, auth_headers):
    r = client.post("/api/my-workflows", headers=auth_headers,
                    json={"name": "", "steps": ["extract"]})
    assert r.status_code == 422  # pydantic min_length
