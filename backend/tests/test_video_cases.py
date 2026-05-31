"""视频案例库(/api/video-cases)测试。"""
from __future__ import annotations

from app.main import app
from app.routers import video_cases as video_cases_router

# main.py 注册由 Tech Lead 收口;在此幂等挂载,确保测试可独立运行,
# 待 TL 在 main.py 注册后此处为无副作用的 no-op。
# 注意:main.py 末尾把前端 StaticFiles 挂在 "/" 上,Starlette 按顺序匹配,
# 若 append 到路由表末尾会被该 Mount 抢先返回 404,故需插到 Mount 之前。
if not any(getattr(r, "path", "").startswith("/api/video-cases") for r in app.routes):
    _before = video_cases_router.router
    app.include_router(_before)
    # 把刚追加到末尾的两条 APIRoute 移到第一个 Mount(静态托管)之前
    _added = app.router.routes[-2:]
    del app.router.routes[-2:]
    _mount_idx = next(
        (i for i, r in enumerate(app.router.routes) if r.__class__.__name__ == "Mount"),
        len(app.router.routes),
    )
    app.router.routes[_mount_idx:_mount_idx] = _added


def test_list_all_returns_items(client, auth_headers):
    resp = client.get("/api/video-cases", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] > 0
    assert len(data["items"]) == data["total"]
    # 种子首条 _comment 应被过滤
    assert all("_comment" not in item for item in data["items"])


def test_filter_by_category(client, auth_headers):
    resp = client.get("/api/video-cases", params={"category": "手机壳"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] > 0
    assert all(item["category"] == "手机壳" for item in data["items"])


def test_categories_have_counts(client, auth_headers):
    resp = client.get("/api/video-cases/categories", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    cats = resp.json()
    names = {c["category"] for c in cats}
    expected = {"服饰", "铁皮画", "家用纺织", "挂钟", "装饰画", "手机壳", "其他"}
    assert expected.issubset(names)
    assert len(cats) == len(expected)
    assert all(c["count"] >= 1 for c in cats)
    # 计数之和应等于总条目数
    total = sum(c["count"] for c in cats)
    all_resp = client.get("/api/video-cases", headers=auth_headers)
    assert total == all_resp.json()["total"]


def test_requires_auth(client):
    assert client.get("/api/video-cases").status_code == 401
    assert client.get("/api/video-cases/categories").status_code == 401
