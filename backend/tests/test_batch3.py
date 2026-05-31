"""Batch 3:异步 process + 作业 owner 隔离 + 注册限流 + collectors P1 修复。"""
from __future__ import annotations
import io
import uuid
from PIL import Image, ImageDraw

from app.services.collectors import upgrade_to_hires, detect_platform


def _png() -> io.BytesIO:
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(img).ellipse([80, 80, 220, 220], fill=(200, 30, 30))
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


def _register(client) -> dict:
    email = f"b3_{uuid.uuid4().hex[:10]}@test.local"
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------- 异步 process ----------
def test_async_process_completes(client, auth_headers):
    r = client.post("/api/process-async", headers=auth_headers,
                    data={"template": "tshirt"}, files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["job_id"]
    jid = body["job_id"]
    # TestClient 会在返回前跑完 BackgroundTasks;轮询应为 done
    j = client.get(f"/api/jobs/{jid}", headers=auth_headers)
    assert j.status_code == 200, j.text
    jb = j.json()
    assert jb["status"] == "done", jb
    assert jb["result"]["production_url"]
    # 产物可取回
    got = client.get(jb["result"]["production_url"])
    assert got.status_code == 200
    assert Image.open(io.BytesIO(got.content)).format == "PNG"


def test_async_job_owner_isolation(client, auth_headers):
    r = client.post("/api/process-async", headers=auth_headers,
                    data={"template": "tshirt"}, files={"file": ("x.png", _png(), "image/png")})
    jid = r.json()["job_id"]
    other = _register(client)
    # 他人作业:404(不泄露存在性)
    assert client.get(f"/api/jobs/{jid}", headers=other).status_code == 404
    # 本人可见
    assert client.get(f"/api/jobs/{jid}", headers=auth_headers).status_code == 200


# ---------- 注册限流 ----------
def test_register_rate_limited(client, monkeypatch):
    from app.ratelimit import register_limiter
    from app.config import settings
    register_limiter.reset()
    monkeypatch.setattr(settings, "register_rate_limit", 3)
    try:
        codes = [client.post("/api/auth/register",
                             json={"email": f"rl{i}_{uuid.uuid4().hex[:6]}@x.com", "password": "pw"}).status_code
                 for i in range(5)]
        assert 429 in codes, codes
        assert codes.count(200) == 3, codes  # 前 3 个成功,后续被限
    finally:
        register_limiter.reset()  # 清理,避免影响后续用例(且 monkeypatch 会还原 limit)


# ---------- collectors P1 修复 ----------
def test_etsy_no_case_mangle():
    # 大写尺寸段不应被(错误地)改成小写模板:真实 etsy 为小写,大写视为非匹配保持原样
    up = "https://i.etsystatic.com/1/r/il/ab/2/IL_340X270.2.jpg"
    assert upgrade_to_hires(up, "etsy") == up  # 大写不匹配 → 原样
    low = "https://i.etsystatic.com/1/r/il/ab/2/il_340x270.2.jpg"
    assert "il_fullxfull" in upgrade_to_hires(low, "etsy")


def test_signed_query_preserved():
    # 含签名参数的 temu/tiktok URL 不应被改写(避免破坏签名)
    signed = "https://img.temu.com/a/b.jpg?width=200&sign=abc123&token=xyz"
    assert upgrade_to_hires(signed, "temu") == signed


def test_noop_query_unchanged():
    # 没有缩放参数 → 原样返回,不重编码
    u = "https://img.temu.com/a/b.jpg?color=red&v=2"
    assert upgrade_to_hires(u, "temu") == u


def test_scaling_query_still_stripped():
    u = "https://img.temu.com/a/b.jpg?imageView2=2/w/300&keep=1"
    out = upgrade_to_hires(u, "temu")
    assert "imageView2" not in out and "keep=1" in out
