"""显式引擎(快速=本地 / 智能=AI)测试。测试环境无 key:
- fast → 本地实现,正常出结果(不依赖 key);
- ai → 无 key 直接 502(不再隐形降级本地)。
"""
from __future__ import annotations

from app.db import Base, engine

Base.metadata.create_all(engine)


def _f(png):
    return {"file": ("a.png", png(), "image/png")}


# ---- 印花提取 ----
def test_extract_fast_local(client, auth_headers, png, tool_result):
    r = client.post("/api/print-extract", headers=auth_headers, data={"engine": "fast"}, files=_f(png))
    assert "image_url" in tool_result(auth_headers, r)  # 后台作业(本地引擎)→ 轮询出图


def test_extract_ai_without_key_502(client, auth_headers, png):
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/print-extract", headers=auth_headers, data={"engine": "ai"}, files=_f(png))
    assert r.status_code == 502
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before  # 退点


# ---- 图裂变 ----
def test_variants_fast_local(client, auth_headers, png, tool_result):
    r = client.post("/api/design-tools/variants", headers=auth_headers,
                    data={"n": 2, "engine": "fast"}, files=_f(png))
    assert len(tool_result(auth_headers, r)["images"]) == 2  # 后台本地换色 → 2 张


def test_variants_ai_without_key_502(client, auth_headers, png):
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/design-tools/variants", headers=auth_headers,
                    data={"n": 3, "engine": "ai"}, files=_f(png))
    assert r.status_code == 502
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before  # n 笔全退


# ---- 标题提取 ----
def test_title_fast_local_no_charge(client, auth_headers, png, tool_result):
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/studio/title", headers=auth_headers, data={"keywords": "cat", "engine": "fast"})
    assert tool_result(auth_headers, r).get("degraded") is True  # 后台本地规则
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before  # 快速不扣点


def test_title_ai_without_key_502(client, auth_headers):
    r = client.post("/api/studio/title", headers=auth_headers, data={"keywords": "cat", "engine": "ai"})
    assert r.status_code == 502


# ---- 商品套图替换 ----
def test_mockup_replace_ai_without_key_502(client, auth_headers, png):
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/mockup/replace", headers=auth_headers, data={"template_id": 0, "engine": "ai"},
                    files=[("file", ("p.png", png(), "image/png")), ("mockups", ("m.png", png(), "image/png"))])
    assert r.status_code == 502
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before  # 未扣点
