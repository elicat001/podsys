"""图案处理工具测试:dewatermark 鉴权 + 无 key 本地引擎。

main.py 由 Tech Lead 收口注册路由;为让本测试自包含可跑,这里在 import 后
把 image_tools 路由挂到测试 app 上(幂等:已挂则跳过)。
"""
from __future__ import annotations

from app.main import app
from app.routers import image_tools as image_tools_router

# 幂等注册:无论 main.py 是否已 include,都保证路由存在。
# main.py 把 StaticFiles 挂在 "/"(最后),会吃掉所有路径;因此新路由必须插到
# 该 mount 之前(routes 按顺序匹配),否则 POST 会命中静态挂载返回 405。
if not any(getattr(r, "path", "").startswith("/api/image-tools") for r in app.routes):
    before = len(app.routes)
    app.include_router(image_tools_router.router)
    new_routes = app.routes[before:]
    del app.routes[before:]
    # 找到 "/" 静态 mount 的位置,把新路由插到它前面
    insert_at = next(
        (i for i, r in enumerate(app.routes) if getattr(r, "path", "") == ""),
        len(app.routes),
    )
    app.routes[insert_at:insert_at] = new_routes


def _balance(client, headers) -> int:
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["credits"]


# --------------------------------------------------------------------------
# dewatermark:鉴权 + 无 key 本地引擎
# --------------------------------------------------------------------------
def test_dewatermark_requires_auth(client, png):
    resp = client.post(
        "/api/image-tools/dewatermark",
        files={"file": ("a.png", png(), "image/png")},
    )
    assert resp.status_code == 401


# 新契约(batch11):无 key 走本地引擎 -> 200 真实产物,正常扣 edit(不再 502)
def test_dewatermark_no_key_offline_real(client, auth_headers, png):
    resp = client.post("/api/image-tools/dewatermark", headers=auth_headers,
        files={"file": ("a.png", png(), "image/png")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"]
