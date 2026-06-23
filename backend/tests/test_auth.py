"""注册 / 登录 / 当前用户。"""
from __future__ import annotations

import uuid


def _email() -> str:
    return f"u_{uuid.uuid4().hex[:10]}@test.local"


def test_register_success_gives_100_credits(client):
    email = _email()
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credits"] == 100
    assert "token" in body and body["token"]
    assert "user_id" in body


def test_register_duplicate_email_409(client):
    email = _email()
    r1 = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r2.status_code == 409


def test_register_disabled_returns_403(client, monkeypatch):
    # 测试阶段暂停注册:register_enabled=False → 403「测试阶段,功能暂不支持」(后端硬堵,不只靠前端)
    from app.config import settings
    monkeypatch.setattr(settings, "register_enabled", False)
    r = client.post("/api/auth/register", json={"email": _email(), "password": "pw123456"})
    assert r.status_code == 403
    assert "测试阶段" in r.json()["detail"]


def test_login_success(client):
    email = _email()
    client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    r = client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    assert r.json()["token"]


def test_login_wrong_password_401(client):
    email = _email()
    client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    r = client.post("/api/auth/login", json={"email": email, "password": "WRONG"})
    assert r.status_code == 401


def test_me_returns_email(client):
    email = _email()
    reg = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    token = reg.json()["token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email


def test_me_requires_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
