"""团队资源(套图模板)+ 商品套图印花替换 测试。

替换引擎的检测会用到 rembg(慢/需模型),测试里 monkeypatch 掉 → 走兜底(居中贴),
只验证 路由/作业/计费/产物 这条链路;替换效果靠手动+真实产品照验证。
"""
from __future__ import annotations

from app.db import Base, engine

Base.metadata.create_all(engine)


def _imgs(png, n, field="files"):
    return [(field, (f"p{i}.png", png(), "image/png")) for i in range(n)]


def test_template_crud(client, auth_headers, png):
    r = client.post("/api/team/mockup-templates", headers=auth_headers,
                    data={"name": "夏季T恤套图"}, files=_imgs(png, 3))
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["name"] == "夏季T恤套图" and t["image_count"] == 3
    assert all(im["url"].startswith("/files/") for im in t["images"])
    tid = t["id"]
    # 产品照可下载
    assert client.get(t["images"][0]["url"]).status_code == 200
    # 列表含它
    assert any(x["id"] == tid for x in client.get("/api/team/mockup-templates", headers=auth_headers).json())
    # 删除
    assert client.delete(f"/api/team/mockup-templates/{tid}", headers=auth_headers).status_code == 200
    assert not any(x["id"] == tid for x in client.get("/api/team/mockup-templates", headers=auth_headers).json())


def test_template_add_and_remove_images(client, auth_headers, png):
    """已建模板可追加图、删单张;至少保留 1 张。"""
    tid = client.post("/api/team/mockup-templates", headers=auth_headers,
                      data={"name": "可编辑"}, files=_imgs(png, 1)).json()["id"]
    # 追加 2 张 → 共 3
    r = client.post(f"/api/team/mockup-templates/{tid}/images", headers=auth_headers, files=_imgs(png, 2))
    assert r.status_code == 200 and r.json()["image_count"] == 3, r.text
    # 删 1 张 → 共 2
    img_id = r.json()["images"][0]["id"]
    r2 = client.delete(f"/api/team/mockup-templates/{tid}/images/{img_id}", headers=auth_headers)
    assert r2.status_code == 200 and r2.json()["image_count"] == 2
    # 删到只剩 1 张后再删 → 400(至少保留 1 张)
    imgs = r2.json()["images"]
    client.delete(f"/api/team/mockup-templates/{tid}/images/{imgs[0]['id']}", headers=auth_headers)
    last = client.get("/api/team/mockup-templates", headers=auth_headers).json()
    last = next(t for t in last if t["id"] == tid)
    assert client.delete(f"/api/team/mockup-templates/{tid}/images/{last['images'][0]['id']}",
                         headers=auth_headers).status_code == 400


def test_template_add_over_cap(client, auth_headers, png):
    """追加后超过 10 张上限 → 400。"""
    tid = client.post("/api/team/mockup-templates", headers=auth_headers,
                      data={"name": "满"}, files=_imgs(png, 8)).json()["id"]
    assert client.post(f"/api/team/mockup-templates/{tid}/images",
                       headers=auth_headers, files=_imgs(png, 3)).status_code == 400  # 8+3>10


def test_template_requires_auth(client, png):
    r = client.post("/api/team/mockup-templates", data={"name": "x"}, files=_imgs(png, 1))
    assert r.status_code == 401


def test_template_validation(client, auth_headers, png):
    # 空名 → 400
    assert client.post("/api/team/mockup-templates", headers=auth_headers,
                       data={"name": "  "}, files=_imgs(png, 1)).status_code == 400


def test_mockup_replace_uploaded(client, auth_headers, png, monkeypatch, tool_result):
    """上传产品照 + 印花 → 逐张替换出图(走兜底检测,不触发 rembg)。"""
    import app.services.mockup_replace as mr
    monkeypatch.setattr(mr, "_product_mask", lambda small: (None, "none"))  # 强制兜底,避免 rembg
    files = [("file", ("print.png", png(), "image/png"))] + _imgs(png, 2, field="mockups")
    before = client.get("/api/auth/me", headers=auth_headers).json()["credits"]
    r = client.post("/api/mockup/replace", headers=auth_headers, data={"template_id": 0}, files=files)
    assert r.status_code == 200, r.text
    res = tool_result(auth_headers, r)
    assert res["count"] == 2 and len(res["images"]) == 2
    assert client.get(res["images"][0]).status_code == 200
    assert client.get("/api/auth/me", headers=auth_headers).json()["credits"] == before - 2  # asset×2


def test_mockup_replace_from_template(client, auth_headers, png, monkeypatch, tool_result):
    """从团队资源套图模板替换:选模板=用其全部图。"""
    import app.services.mockup_replace as mr
    monkeypatch.setattr(mr, "_product_mask", lambda small: (None, "none"))
    tpl = client.post("/api/team/mockup-templates", headers=auth_headers,
                      data={"name": "模板A"}, files=_imgs(png, 2)).json()
    r = client.post("/api/mockup/replace", headers=auth_headers,
                    data={"template_id": tpl["id"]},
                    files=[("file", ("print.png", png(), "image/png"))])
    assert r.status_code == 200, r.text
    res = tool_result(auth_headers, r)
    assert res["count"] == 2  # 模板有 2 张图 → 出 2 张
