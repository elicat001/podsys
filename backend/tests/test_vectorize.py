"""转矢量图端点测试(离线真实):/api/vectorize。

覆盖:成功 200 + svg 可取回 + 扣 2 点;colors 越界退点 400;非图退点 400;未登录 401。
"""
from __future__ import annotations

from starlette.routing import Mount

from app.main import app
from app.routers import vectorize as _vectorize_router

# Tech Lead 收口前自助挂载,保证本测试可独立跑绿(main.py 由 Tech Lead 注册,勿改)。
# 注意:main.py 末尾把静态前端 Mount("/") 挂在最后,会吃掉后续 append 的 API 路由,
# 故需把本路由插到该 Mount *之前*,与 Tech Lead 在 main.py 注册的最终顺序一致。
if not any(getattr(r, "path", "") == "/api/vectorize" for r in app.routes):
    app.include_router(_vectorize_router.router)
    new = app.router.routes.pop()  # 刚 append 的 /api/vectorize
    mount_idx = next(
        (i for i, r in enumerate(app.router.routes) if isinstance(r, Mount)),
        len(app.router.routes),
    )
    app.router.routes.insert(mount_idx, new)


def _balance(client, headers) -> int:
    r = client.get("/api/billing/balance", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["credits"]


def test_vectorize_ok_and_charges_two(client, auth_headers, png):
    before = _balance(client, auth_headers)
    img = png(size=(64, 64), shape="rect")
    resp = client.post(
        "/api/vectorize",
        headers=auth_headers,
        files={"file": ("in.png", img, "image/png")},
        data={"colors": "8"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["colors"] == 8
    assert body["rect_count"] > 0
    assert body["svg_url"].startswith("/files/")

    # 取回 svg 内容
    got = client.get(body["svg_url"])
    assert got.status_code == 200, got.text
    text = got.text
    assert "<svg" in text
    assert "<rect" in text

    after = _balance(client, auth_headers)
    assert before - after == 2


def test_vectorize_colors_too_low_refunds(client, auth_headers, png):
    before = _balance(client, auth_headers)
    img = png(size=(40, 40), shape="circle")
    resp = client.post(
        "/api/vectorize",
        headers=auth_headers,
        files={"file": ("in.png", img, "image/png")},
        data={"colors": "1"},
    )
    assert resp.status_code == 400, resp.text
    assert _balance(client, auth_headers) == before


def test_vectorize_colors_too_high_refunds(client, auth_headers, png):
    before = _balance(client, auth_headers)
    img = png(size=(40, 40), shape="circle")
    resp = client.post(
        "/api/vectorize",
        headers=auth_headers,
        files={"file": ("in.png", img, "image/png")},
        data={"colors": "100"},
    )
    assert resp.status_code == 400, resp.text
    assert _balance(client, auth_headers) == before


def test_vectorize_non_image_refunds(client, auth_headers):
    before = _balance(client, auth_headers)
    resp = client.post(
        "/api/vectorize",
        headers=auth_headers,
        files={"file": ("bad.png", b"not-an-image-bytes", "image/png")},
    )
    assert resp.status_code == 400, resp.text
    assert _balance(client, auth_headers) == before


def test_vectorize_requires_auth(client, png):
    img = png(size=(32, 32), shape="rect")
    resp = client.post(
        "/api/vectorize",
        files={"file": ("in.png", img, "image/png")},
    )
    assert resp.status_code == 401, resp.text
