"""以图搜图测试(/api/search/by-image)。

约定:
- 用 conftest 的 client / auth_headers / png(make_png)。
- search 路由由 Tech Lead 在 main.py 注册;为让本测试自包含,这里在 import 时把
  search_router 挂到同一个 app 上(若已注册则幂等,不会重复匹配路由前缀)。
- 入库走 /api/assets(需 Bearer)。造 3 张结构与配色都迥异的图,避免互相高相似:
    A 棋盘(黑白条纹)、B 居中红圆(白底)、C 对角彩色渐变。
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.main import app
from app.routers.search import router as search_router

# 自包含:确保 /api/search 已挂载(main.py 由 Tech Lead 收口注册,这里幂等补挂)。
# 注意:main.py 把 StaticFiles 挂在 "/" 上(catch-all),import 期补挂的路由若追加到
# 末尾会被静态挂载抢先匹配 → 405。故把 search 路由插到路由表最前,保证 /api/* 命中。
# Tech Lead 在 main.py 中于 mount 之前 include_router 后,本补挂幂等跳过。
if not any(getattr(r, "path", "") == "/api/search/by-image" for r in app.routes):
    before = len(app.router.routes)
    app.include_router(search_router)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[:0] = added

SIZE = (256, 256)


def _checkerboard(seed: int = 0, fill=(0, 0, 0)) -> io.BytesIO:
    img = Image.new("RGB", SIZE, (255, 255, 255))
    d = ImageDraw.Draw(img)
    w, h = SIZE
    step = 5 + (seed % 4)
    for y in range(0, h, step):
        for x in range(0, w, step):
            if ((x // step) + (y // step) + seed) % 2 == 0:
                d.rectangle([x, y, x + step, y + step], fill=fill)
    return _png(img)


def _circle(fill=(220, 30, 30)) -> io.BytesIO:
    img = Image.new("RGB", SIZE, (255, 255, 255))
    d = ImageDraw.Draw(img)
    w, h = SIZE
    r = min(w, h) // 4
    d.ellipse([w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r], fill=fill)
    return _png(img)


def _gradient() -> io.BytesIO:
    img = Image.new("RGB", SIZE)
    d = ImageDraw.Draw(img)
    for x in range(SIZE[0]):
        d.line([(x, 0), (x, SIZE[1])], fill=(x % 256, (x * 2) % 256, (255 - x) % 256))
    return _png(img)


def _png(img: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _upload(client, headers, buf, name="a.png") -> int:
    r = client.post("/api/assets", headers=headers, files={"file": (name, buf, "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["asset_id"]


def _search(client, headers, buf, top_k=10, name="q.png"):
    return client.post(
        "/api/search/by-image",
        headers=headers,
        files={"file": (name, buf, "image/png")},
        data={"top_k": str(top_k)},
    )


def test_search_returns_self_first(client, auth_headers):
    # 三张结构与配色都迥异的图入库
    board_bytes = _checkerboard(seed=2).read()
    a_board = _upload(client, auth_headers, io.BytesIO(board_bytes), name="board.png")
    a_circle = _upload(client, auth_headers, _circle(), name="circle.png")
    a_grad = _upload(client, auth_headers, _gradient(), name="grad.png")
    assert len({a_board, a_circle, a_grad}) == 3

    # 用棋盘原图(同一张)检索 → 命中非空,首位是它,similarity 最高且接近 1.0
    r = _search(client, auth_headers, io.BytesIO(board_bytes))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    results = body["results"]
    assert results, "results 不应为空"
    assert results[0]["asset_id"] == a_board
    assert results[0]["similarity"] >= 0.99
    # 降序单调
    sims = [x["similarity"] for x in results]
    assert sims == sorted(sims, reverse=True)


def test_search_top_k_limits(client, auth_headers):
    _upload(client, auth_headers, _checkerboard(seed=1), name="b1.png")
    _upload(client, auth_headers, _circle(fill=(30, 30, 220)), name="c1.png")
    _upload(client, auth_headers, _gradient(), name="g1.png")
    r = _search(client, auth_headers, _checkerboard(seed=1), top_k=1)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    assert len(r.json()["results"]) == 1


def test_search_requires_auth(client):
    r = client.post(
        "/api/search/by-image",
        files={"file": ("q.png", _circle(), "image/png")},
    )
    assert r.status_code == 401


def test_search_empty_library(client, auth_headers):
    # 全新用户(auth_headers 每次注册新邮箱),素材库为空
    r = _search(client, auth_headers, _circle())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0
    assert body["results"] == []


def test_search_bad_image_400(client, auth_headers):
    r = client.post(
        "/api/search/by-image",
        headers=auth_headers,
        files={"file": ("x.png", io.BytesIO(b"not an image"), "image/png")},
    )
    assert r.status_code == 400
