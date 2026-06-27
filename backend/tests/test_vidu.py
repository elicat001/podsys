"""Vidu 图生视频(/api/vidu)测试 —— 第二套引擎,与 CogVideoX 并存。

覆盖:options 形状 / 鉴权 / 计费=秒数×2(连续时长) / 时长 clamp / 本地兜底 GIF /
     provider 失败降级退点 / 余额不足 402 退首笔 / 提示词(多镜头 + 音频层) /
     provider 真实请求体形状(假 httpx:img2video vs reference2video、模型名、audio 参数、防硬编码 bug)。
测试默认 provider=local(conftest 锁 POD_VIDU_PROVIDER=local)→ 离线确定性,不连 Vidu。
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
    assert d["duration"] == {"min": 5, "max": 16}     # 连续时长
    assert d["resolutions"] == ["720p", "1080p"]
    assert d["price_per_second"] == 2
    assert "dialogue" in d["sound_modes"] and "voiceover" in d["sound_modes"]
    assert d["ai_ready"] is False
    assert "portrait" in d["aspects"]
    assert "categories" not in d                        # 商品类目已下线


def test_vidu_options_requires_auth(client):
    assert client.get("/api/vidu/options").status_code == 401


def test_vidu_ai_generate_requires_auth(client):
    r = client.post("/api/vidu/ai-generate", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


# ---------- 计费 = 秒数 × 2(连续时长) ----------
def test_vidu_local_fallback_charges_seconds_x2(client, auth_headers, tool_result):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"prompt": "镜头推近展示商品", "aspect": "portrait", "seconds": 5},
                    files={"file": ("x.png", _png(), "image/png")})
    res = tool_result(auth_headers, r)
    assert res["ext"] == "gif" and res["video_url"].endswith(".gif")
    assert res["engine"] == "local-gif" and res["degraded"] is True
    assert _bal(client, auth_headers) == bal0 - 10     # 5s × 2 = 10


def test_vidu_continuous_duration_charges(client, auth_headers, tool_result):
    """连续时长:7s → 14 点(证明不再是固定 5/10/15 档)。"""
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 7}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal0 - 14     # 7s × 2 = 14


def test_vidu_15s_charges_30(client, auth_headers, tool_result):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 15}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal0 - 30


def test_vidu_duration_clamped(client, auth_headers, tool_result):
    """越界时长被夹到 [5,16]:3→5(扣10)、20→16(扣32)。"""
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 3}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal0 - 10     # clamp 到 5

    bal1 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 20}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal1 - 32     # clamp 到 16


def test_vidu_bad_image_refunds(client, auth_headers):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 5}, files={"file": ("x.png", io.BytesIO(b"nope"), "image/png")})
    assert r.status_code == 400
    assert _bal(client, auth_headers) == bal0


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
    assert _bal(client, auth_headers) == bal0          # 扣 20 退 20


# ---------- 余额不足:补扣第 N 笔失败 → 退回已扣的全部 + 402 ----------
def test_vidu_insufficient_credits_402_refunds_charged(client, auth_headers, monkeypatch):
    from app.routers import vidu as vidu_router
    from app.services import billing
    real_charge = billing.charge
    calls = {"n": 0}

    def _charge_once(db, user, op):
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
    assert _bal(client, auth_headers) == bal0


# ---------- 提示词组装:多镜头 + 音频层(与 CogVideoX 分段拼接不同) ----------
def test_compose_vidu_prompt_15s_is_multishot():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="葡萄牙语", seconds=15)
    assert "镜头三" in p and "镜头一" in p
    assert "图案" in p


def test_compose_vidu_prompt_5s_is_single_shot():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="无对白", seconds=5)
    assert "镜头三" not in p


def test_compose_vidu_prompt_respects_user_script():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("我自己的镜头脚本ABC", language="英语", seconds=15)
    assert "我自己的镜头脚本ABC" in p
    assert "镜头三" not in p


def test_compose_vidu_prompt_dialogue_audio_layer():
    """音画同步:prompt 末尾追加音频层,含对白语言。"""
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="英语", seconds=5, sound_mode="dialogue", dialogue_lang="中文")
    assert "音频" in p and "中文" in p and "口型" in p


def test_compose_vidu_prompt_sfx_audio_layer():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="英语", seconds=5, sound_mode="sfx")
    assert "音频" in p and "音效" in p
    # 无声模式不加音频层
    p2 = compose_vidu_prompt("", language="英语", seconds=5, sound_mode="none")
    assert "音频" not in p2


def test_vidu_billing_op_cost():
    from app.services.billing import cost_of
    assert cost_of("vidu") == 2


def test_clamp_seconds():
    from app.ai.vidu import clamp_seconds
    assert clamp_seconds(3) == 5 and clamp_seconds(20) == 16 and clamp_seconds(8) == 8


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
    """记录 post 的 path/body,模拟 建任务→轮询success→下载 三步。"""
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
        return _FakeResp(content=b"\x00\x00\x00\x18ftypmp42FAKEMP4")


def _setup(monkeypatch):
    import httpx

    from app.config import settings
    monkeypatch.setattr(settings, "vidu_api_key", "k-test")
    monkeypatch.setattr(settings, "vidu_provider", "vidu")
    monkeypatch.setattr(settings, "vidu_model", "viduq3-pro")
    monkeypatch.setattr(settings, "vidu_ref_model", "viduq3")
    monkeypatch.setattr(settings, "vidu_poll_interval", 0.0)
    monkeypatch.setattr(settings, "vidu_base_url", "https://api.vidu.cn")
    monkeypatch.setattr(httpx, "Client", _FakeClient)


def test_vidu_provider_single_image_uses_img2video(monkeypatch):
    """1 张图 → img2video,用 img2video 合法模型名 viduq3-pro;audio 显式发;Q3 不发 movement/bgm。"""
    from app.ai import vidu as vidu_mod
    _setup(monkeypatch)
    prov = vidu_mod.get_vidu_provider()
    assert isinstance(prov, vidu_mod.ViduProvider)
    img = Image.open(_png())
    out = prov.image_to_video([img], "多镜头脚本", aspect="portrait", resolution="720p",
                              seconds=15, audio=True, audio_type="All")
    assert out["ext"] == "mp4" and out["bytes"].startswith(b"\x00\x00\x00\x18ftyp")
    assert out["meta"]["engine"] == "viduq3-pro"
    cap = _FakeClient.captured
    assert cap["url"].endswith("/ent/v2/img2video")
    assert cap["headers"]["Authorization"] == "Token k-test"
    body = cap["body"]
    assert body["model"] == "viduq3-pro" and body["duration"] == 15 and body["resolution"] == "720p"
    assert body["images"][0].startswith("data:image/jpeg;base64,")
    assert body["audio"] is True and body["audio_type"] == "All"
    assert "movement_amplitude" not in body and "bgm" not in body and "voice_id" not in body


def test_vidu_provider_two_images_uses_reference2video(monkeypatch):
    """2 张图 → reference2video,用 reference 合法模型名 viduq3 + aspect_ratio;audio=false 不发 audio_type。"""
    from app.ai import vidu as vidu_mod
    _setup(monkeypatch)
    prov = vidu_mod.get_vidu_provider()
    imgs = [Image.open(_png((200, 40, 40))), Image.open(_png((40, 200, 40)))]
    out = prov.image_to_video(imgs, "脚本", aspect="square", resolution="1080p", seconds=10, audio=False)
    assert out["ext"] == "mp4" and out["meta"]["engine"] == "viduq3"
    cap = _FakeClient.captured
    assert cap["url"].endswith("/ent/v2/reference2video")
    body = cap["body"]
    assert body["model"] == "viduq3" and len(body["images"]) == 2 and body["aspect_ratio"] == "1:1"
    assert body["audio"] is False and "audio_type" not in body


def test_vidu_duration_clamped_in_provider(monkeypatch):
    """provider 内也夹时长:越界 30 → 16。"""
    from app.ai import vidu as vidu_mod
    _setup(monkeypatch)
    prov = vidu_mod.get_vidu_provider()
    prov.image_to_video([Image.open(_png())], "x", seconds=30)
    assert _FakeClient.captured["body"]["duration"] == 16
