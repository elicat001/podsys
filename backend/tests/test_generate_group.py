"""文生图「类型(印花/商品图)+ 数量(一张/一组)」测试。

覆盖:
- 商品图·一组(5图)→ 走 Celery 后台、打包扣 20 点、result 出 5 张 images。
- 印花强制单张(选了一组也只出 1 张、只扣 5 点)。
- 商品图·一张 → 单图、扣 5 点。
- 一组余额不足 → 402 且不扣点(全退,笔数对齐)。
- 默认参数(不传 gen_type/group)= 原契约(印花单张、扣 5)。
测试环境无 key:一律走本地程序化引擎(离线确定性,见 conftest 三层隔离)。
"""
from __future__ import annotations

from app.services.generate import SET_SHOT_COUNT, generate_product_set, refine_product_prompt


def _balance(client, headers) -> int:
    r = client.get("/api/billing/balance", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["credits"]


def test_product_set_produces_five_images_and_charges_20(client, auth_headers, tool_result):
    """商品图·一组:后台出 5 张图,打包扣 20 点。"""
    before = _balance(client, auth_headers)
    resp = client.post("/api/generate", headers=auth_headers,
                       data={"prompt": "denim jacket", "gen_type": "product", "group": "set"})
    result = tool_result(auth_headers, resp)
    assert len(result["images"]) == SET_SHOT_COUNT == 5, result
    assert all(u.endswith(".png") for u in result["images"])
    assert _balance(client, auth_headers) == before - 20


def test_print_ignores_group_stays_single(client, auth_headers):
    """印花不支持一组:即便传 group=set 也只出 1 张、只扣 5 点(无 key 单张同步出图)。"""
    before = _balance(client, auth_headers)
    resp = client.post("/api/generate", headers=auth_headers,
                       data={"prompt": "galaxy cat", "gen_type": "print", "group": "set"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "images" not in body
    assert body["image_url"].endswith(".png")
    assert _balance(client, auth_headers) == before - 5


def test_product_single_is_one_image_charges_5(client, auth_headers):
    """商品图·一张:单图、扣 5 点(无 key 单张同步出图)。"""
    before = _balance(client, auth_headers)
    resp = client.post("/api/generate", headers=auth_headers,
                       data={"prompt": "ceramic mug", "gen_type": "product", "group": "single"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"].endswith(".png")
    assert _balance(client, auth_headers) == before - 5


def test_default_params_unchanged_contract(client, auth_headers):
    """不传 gen_type/group → 原契约(印花单张,无 key 同步出图,扣 5)。"""
    before = _balance(client, auth_headers)
    r = client.post("/api/generate", headers=auth_headers, data={"prompt": "fox", "size": "512x512"})
    assert r.status_code == 200, r.text
    assert r.json()["image_url"].endswith(".png")
    assert _balance(client, auth_headers) == before - 5


def test_set_insufficient_credits_refunds_all(client, png):
    """一组余额不足 → 402,且不净扣点(预扣的几笔全退,笔数对齐)。"""
    # 造一个余额不足以付一组(<20)的用户
    client.post("/api/auth/register", json={"email": "poor_set@test.io", "password": "pw123456"})
    tok = client.post("/api/auth/login", json={"email": "poor_set@test.io", "password": "pw123456"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    # 把余额花到 < 20:用单张文生图反复扣到不够一组
    bal = _balance(client, h)
    while bal >= 20:
        client.post("/api/generate", headers=h, data={"prompt": "x"})
        bal = _balance(client, h)
    before = _balance(client, h)
    assert before < 20
    resp = client.post("/api/generate", headers=h,
                       data={"prompt": "denim", "gen_type": "product", "group": "set"})
    assert resp.status_code == 402, resp.text
    assert _balance(client, h) == before  # 全退,净扣 0


def test_generate_product_set_offline_distinct():
    """无 key:generate_product_set 返回 5 个(slug,标签,图),分镜 prompt 不同→图可区分。"""
    out = generate_product_set("denim jacket", size="512x512")
    assert len(out) == SET_SHOT_COUNT
    slugs = [s for s, _, _ in out]
    assert slugs == ["white", "size", "scene", "detail", "wear"]
    # 不同分镜的程序化图至少有一对像素不同(prompt seed 不同)
    pixels = [im.convert("RGB").resize((8, 8)).tobytes() for _, _, im in out]
    assert len(set(pixels)) > 1


def test_set_with_key_background_success(client, auth_headers, monkeypatch):
    """有 key:商品图·一组走后台并行出 5 图,打包扣 20 点。"""
    from PIL import Image

    from app.ai import openai_image
    from app.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "generate",
                        lambda self, prompt, size="1024x1024", quality="auto", background="auto":
                        Image.new("RGB", (32, 32), (5, 6, 7)))
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/generate", headers=auth_headers,
                    data={"prompt": "denim", "gen_type": "product", "group": "set"})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert len(job["result"]["images"]) == SET_SHOT_COUNT
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 20


def test_set_with_key_failure_refunds_all_20(client, auth_headers, monkeypatch):
    """有 key:一组后台失败 → 退回全部 20 点(4 笔,笔数对齐)。"""
    from app.ai import openai_image
    from app.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    def _boom(self, prompt, size="1024x1024", quality="auto", background="auto"):
        raise RuntimeError("gateway 500")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "generate", _boom)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/generate", headers=auth_headers,
                    data={"prompt": "denim", "gen_type": "product", "group": "set"})
    assert r.status_code == 200, r.text  # 提交成功;失败在后台
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "error", job
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_refine_product_prompt_adds_photography_hint():
    used, hint = refine_product_prompt("denim jacket")
    assert "photography" in used.lower()
    assert hint  # 透明告知补了什么
    # 已含摄影语义则不重复补
    used2, hint2 = refine_product_prompt("studio product photo of a mug")
    assert hint2 is None
