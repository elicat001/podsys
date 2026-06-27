"""Vidu 图生视频(/api/vidu)测试 —— 第二套引擎,与 CogVideoX 并存。

覆盖:options 形状 / 鉴权 / 计费=秒数×2 / 本地兜底 GIF / provider 失败降级退点 / 余额不足 402 退首笔 /
     提示词多镜头组装 / provider 真实请求体形状(假 httpx,防硬编码 bug)。
测试默认 provider=local(conftest 未设 POD_VIDU_PROVIDER + 默认 local)→ 离线确定性,不连 Vidu。
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _png(color=(40, 120, 200)) -> io.BytesIO:
    img = Image.new("RGB", (320, 320), (255, 255, 255))
    ImageDraw.Draw(img).ellipse([60, 60, 260, 260], fill=color)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


def _bal(client, headers) -> int:
    return client.get("/api/billing/balance", headers=headers).json()["credits"]


# ---------- options / 鉴权 ----------
def test_vidu_options_shape(client, auth_headers):
    r = client.get("/api/vidu/options", headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["durations"] == [5, 10, 15]
    assert d["resolutions"] == ["720p", "1080p"]
    assert d["price_per_second"] == 2          # vidu op = 2 点/秒
    assert d["ai_ready"] is False              # 默认 provider=local + 无 key
    assert "portrait" in d["aspects"]


def test_vidu_options_requires_auth(client):
    assert client.get("/api/vidu/options").status_code == 401


def test_vidu_ai_generate_requires_auth(client):
    r = client.post("/api/vidu/ai-generate", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


# ---------- 计费 = 秒数 × 2(本地兜底 GIF;provider=local 是配置选择 → 照常扣、不退) ----------
def test_vidu_local_fallback_charges_seconds_x2(client, auth_headers, tool_result):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"prompt": "镜头推近展示商品", "aspect": "portrait", "seconds": 5},
                    files={"file": ("x.png", _png(), "image/png")})
    res = tool_result(auth_headers, r)
    assert res["ext"] == "gif" and res["video_url"].endswith(".gif")
    assert res["engine"] == "local-gif" and res["degraded"] is True
    got = client.get(res["video_url"])
    assert got.status_code == 200 and got.content[:6] in (b"GIF87a", b"GIF89a")
    assert _bal(client, auth_headers) == bal0 - 10    # 5s × 2 = 10


def test_vidu_15s_charges_30(client, auth_headers, tool_result):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 15}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal0 - 30    # 15s × 2 = 30


def test_vidu_bad_image_refunds(client, auth_headers):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 5}, files={"file": ("x.png", io.BytesIO(b"nope"), "image/png")})
    assert r.status_code == 400
    assert _bal(client, auth_headers) == bal0          # 坏图:只扣的 1 笔已退,净不扣


# ---------- provider 失败 → 兜底 GIF + 退回全部点数 ----------
def test_vidu_provider_failure_falls_back_to_gif_and_refunds(client, auth_headers, monkeypatch, tool_result):
    from app.ai import vidu as vidu_mod

    def _boom():
        raise RuntimeError("Vidu 网关 500 繁忙")
    monkeypatch.setattr(vidu_mod, "get_vidu_provider", _boom)
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 10}, files={"file": ("x.png", _png(), "image/png")})
    res = tool_result(auth_headers, r)
    assert res["video_url"].endswith(".gif") and res["degraded"] is True
    assert res["warnings"] and any("兜底" in w for w in res["warnings"])
    assert _bal(client, auth_headers) == bal0          # 扣 20 退 20,净不扣


# ---------- 余额不足:补扣第 N 笔失败 → 退回已扣的全部 + 402 ----------
def test_vidu_insufficient_credits_402_refunds_charged(client, auth_headers, monkeypatch):
    from app.routers import vidu as vidu_router
    from app.services import billing
    real_charge = billing.charge
    calls = {"n": 0}

    def _charge_once(db, user, op):
        # charge_for 先扣 1 笔成功;第 2 次补扣抛余额不足 → 触发退回 + 402
        calls["n"] += 1
        if op == "vidu" and calls["n"] >= 2:
            raise billing.InsufficientCredits("vidu", 2, 0)
        return real_charge(db, user, op)
    monkeypatch.setattr(billing, "charge", _charge_once)
    monkeypatch.setattr(vidu_router, "charge", _charge_once)
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 10}, files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 402
    assert _bal(client, auth_headers) == bal0          # 净不扣(退回已扣的全部)


# ---------- 提示词组装:多镜头写在同一条 prompt 内(与 CogVideoX 分段拼接不同) ----------
def test_compose_vidu_prompt_15s_is_multishot():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="葡萄牙语", seconds=15)
    assert "镜头三" in p and "镜头一" in p            # 三镜头骨架(单条 prompt 内)
    assert "图案" in p                                  # 印花一致底线


def test_compose_vidu_prompt_5s_is_single_shot():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="无对白", seconds=5)
    assert "镜头三" not in p                            # 5s 不强塞多镜头骨架
    assert "葡萄牙语" not in p                          # 无对白 → 不加语言指令


def test_compose_vidu_prompt_respects_user_script():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("我自己的镜头脚本ABC", language="英语", seconds=15)
    assert "我自己的镜头脚本ABC" in p                   # 用户写了脚本 → 不覆盖
    assert "镜头三" not in p                            # 不再强塞默认骨架


def test_vidu_billing_op_cost():
    from app.services.billing import cost_of
    assert cost_of("vidu") == 2


# ---------- provider 真实请求体形状(假 httpx,不连网络;防硬编码 API bug) ----------
class _FakeResp:
    def __init__(self, payload=None, content=b""):
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """记录 post 的 path/body,模拟 建任务→轮询success→下载 的完整三步。"""
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        _FakeClient.captured = {"url": url, "headers": headers, "body": json}
        return _FakeResp({"task_id": "task-123", "state": "created"})

    def get(self, url, headers=None, timeout=None):
        if url.endswith("/creations"):
            return _FakeResp({"state": "success",
                              "creations": [{"url": "https://v/out.mp4", "cover_url": "https://v/c.jpg"}]})
        return _FakeResp(content=b"\x00\x00\x00\x18ftypmp42FAKEMP4")   # 下载成片


def test_vidu_provider_single_image_uses_img2video(monkeypatch):
    """1 张图 → img2video,首帧锁定;请求体含 model/duration/resolution;viduq3 不发 movement_amplitude。"""
    import httpx

    from app.ai import vidu as vidu_mod
    from app.config import settings
    monkeypatch.setattr(settings, "vidu_api_key", "k-test")
    monkeypatch.setattr(settings, "vidu_provider", "vidu")
    monkeypatch.setattr(settings, "vidu_model", "viduq3")
    monkeypatch.setattr(settings, "vidu_poll_interval", 0.0)
    monkeypatch.setattr(settings, "vidu_base_url", "https://api.vidu.cn")
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    prov = vidu_mod.get_vidu_provider()
    assert isinstance(prov, vidu_mod.ViduProvider)
    img = Image.open(_png())
    out = prov.image_to_video([img], "多镜头脚本", aspect="portrait", resolution="720p", seconds=15)
    assert out["ext"] == "mp4" and out["bytes"].startswith(b"\x00\x00\x00\x18ftyp")
    cap = _FakeClient.captured
    assert cap["url"].endswith("/ent/v2/img2video")               # 单图走 img2video
    assert cap["headers"]["Authorization"] == "Token k-test"      # 鉴权头格式
    body = cap["body"]
    assert body["model"] == "viduq3" and body["duration"] == 15 and body["resolution"] == "720p"
    assert isinstance(body["images"], list) and body["images"][0].startswith("data:image/jpeg;base64,")
    assert "movement_amplitude" not in body                       # Q3 不发该字段


def test_vidu_provider_two_images_uses_reference2video(monkeypatch):
    """2 张图 → reference2video,带 aspect_ratio + 多图参考主体一致。"""
    import httpx

    from app.ai import vidu as vidu_mod
    from app.config import settings
    monkeypatch.setattr(settings, "vidu_api_key", "k-test")
    monkeypatch.setattr(settings, "vidu_provider", "vidu")
    monkeypatch.setattr(settings, "vidu_poll_interval", 0.0)
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    prov = vidu_mod.get_vidu_provider()
    imgs = [Image.open(_png((200, 40, 40))), Image.open(_png((40, 200, 40)))]
    out = prov.image_to_video(imgs, "脚本", aspect="square", resolution="1080p", seconds=10)
    assert out["ext"] == "mp4"
    cap = _FakeClient.captured
    assert cap["url"].endswith("/ent/v2/reference2video")
    body = cap["body"]
    assert len(body["images"]) == 2 and body["aspect_ratio"] == "1:1"
