"""Vidu 图生视频(/api/vidu)测试 —— 第二套引擎(viduq2-pro-fast,真人上手 + 场景母帧)。

覆盖:options 形状 / 鉴权 / 计费=秒数×2 / 时长 clamp(5-10) / 本地兜底 GIF / 场景母帧无 key 降级 /
     provider 失败降级退点 / 余额不足 402 / 提示词(动作 + 音效层) /
     provider 真实请求体形状(假 httpx:img2video vs reference2video、模型名、audio 参数、防硬编码 bug)。
测试默认 provider=local + 无作图 key(conftest)→ 离线确定性,不连 Vidu、不调 gpt-image 母帧。
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
    assert d["duration"] == {"min": 5, "max": 10}     # viduq2-pro-fast 上限 10
    assert d["resolutions"] == ["720p", "1080p"]
    assert d["price_per_second"] == 2
    assert d["sound_modes"] == ["none", "sfx", "voiceover"]   # 无声/原生音效/真人旁白(葡西靠旁白)
    assert d["model"] == "viduq2-pro-fast"
    assert d["ai_ready"] is False
    assert "categories" not in d                       # 商品类目已下线


def test_vidu_options_requires_auth(client):
    assert client.get("/api/vidu/options").status_code == 401


def test_vidu_ai_generate_requires_auth(client):
    r = client.post("/api/vidu/ai-generate", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


# ---------- 计费 = 秒数 × 2(连续时长 5-10) ----------
def test_vidu_local_fallback_charges_seconds_x2(client, auth_headers, tool_result):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"prompt": "真人把玩商品", "seconds": 5, "scene_frame": "true"},
                    files={"file": ("x.png", _png(), "image/png")})
    res = tool_result(auth_headers, r)
    assert res["ext"] == "gif" and res["engine"] == "local-gif" and res["degraded"] is True
    assert _bal(client, auth_headers) == bal0 - 10     # 5s × 2


def test_vidu_continuous_duration_charges(client, auth_headers, tool_result):
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 7}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal0 - 14     # 7s × 2


def test_vidu_duration_clamped(client, auth_headers, tool_result):
    """越界时长夹到 [5,10]:3→5(扣10)、20→10(扣20)。"""
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 3}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal0 - 10

    bal1 = _bal(client, auth_headers)
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 20}, files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert _bal(client, auth_headers) == bal1 - 20     # clamp 到 10


def test_vidu_scene_frame_without_key_degrades_gracefully(client, auth_headers, tool_result):
    """场景母帧开启 + 无作图 key(conftest 清空)→ 不调 gpt-image,直接用原图首帧,正常交付。"""
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 5, "scene_frame": "true"},
                    files={"file": ("x.png", _png(), "image/png")})
    res = tool_result(auth_headers, r)
    assert res["video_url"] and "warnings" in res      # 不崩;无 key 不报母帧失败(没进母帧分支)
    assert not any("母帧" in w for w in res["warnings"])


def test_vidu_voiceover_mode_accepted(client, auth_headers, tool_result):
    """真人旁白模式被接受并正常交付(本地兜底出 GIF 时 edge-tts 不触发=不崩;真 mp4 时才叠旁白)。"""
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 5, "sound_mode": "voiceover", "subtitle": "true", "language": "葡萄牙语"},
                    files={"file": ("x.png", _png(), "image/png")})
    res = tool_result(auth_headers, r)
    assert res["video_url"]                            # 不崩、正常出片


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


# ---------- 提示词组装 ----------
def test_compose_vidu_prompt_default_is_real_person_action():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="葡萄牙语", seconds=5)
    assert "图案" in p              # 一致性底线
    assert ("把玩" in p or "使用" in p)


def test_compose_vidu_prompt_respects_user_script():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("我的专属按压旋转脚本ABC", language="英语", seconds=8)
    assert "我的专属按压旋转脚本ABC" in p


def test_compose_vidu_prompt_sfx_audio_layer():
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("", language="英语", seconds=5, sound_mode="sfx")
    assert "音频" in p and "音效" in p
    p2 = compose_vidu_prompt("", language="英语", seconds=5, sound_mode="none")
    assert "音频" not in p2


def test_vidu_billing_op_cost():
    from app.services.billing import cost_of
    assert cost_of("vidu") == 2


def test_clamp_seconds():
    from app.ai.vidu import clamp_seconds
    assert clamp_seconds(3) == 5 and clamp_seconds(20) == 10 and clamp_seconds(8) == 8


def test_compose_vidu_prompt_has_direction_block():
    """导演层:任务驱动 + 连续动作链 + 去僵硬,且尊重用户脚本。"""
    from app.ai.vidu import compose_vidu_prompt
    p = compose_vidu_prompt("拿起球按住顶部拨动让它旋转", seconds=10)
    assert "拿起球按住顶部拨动让它旋转" in p
    assert "动作链" in p and "僵硬" in p          # 去僵硬·连续动作链


def test_scene_frame_prompt_uses_wizard_scene():
    from app.ai.vidu import scene_frame_prompt
    p = scene_frame_prompt("葡萄牙语", scene="年轻人坐在桌前正要拨动旋转球")
    assert "年轻人坐在桌前正要拨动旋转球" in p     # 向导场景被带入
    assert "巴西" in p                            # 地区随语言


# ---------- 智能方案向导(brief / proposals) ----------
def test_vidu_wizard_brief(client, auth_headers, monkeypatch):
    from app.services import vidu_wizard
    monkeypatch.setattr(vidu_wizard, "_chat",
                        lambda msgs: '{"name":"减压旋转球","audience":"年轻人减压","selling_points":"按压旋转、彩色、解压"}')
    r = client.post("/api/vidu/wizard/brief", headers=auth_headers,
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "减压旋转球" and "解压" in d["selling_points"]


def test_vidu_wizard_proposals_have_scene_and_storyboard(client, auth_headers, monkeypatch):
    from app.services import vidu_wizard
    fake = ('[{"title":"桌面解压","angle":"真人把玩","model":"年轻人","environment":"居家桌前",'
            '"scene":"年轻人坐在桌前正要拨动旋转球","storyboard":"拿起球→按住顶部→拨动→高速旋转→看着笑"}]')
    monkeypatch.setattr(vidu_wizard, "_chat", lambda msgs: fake)
    r = client.post("/api/vidu/wizard/proposals", headers=auth_headers,
                    data={"name": "减压旋转球", "seconds": 10})
    assert r.status_code == 200, r.text
    props = r.json()["proposals"]
    assert len(props) == 1 and props[0]["scene"] and props[0]["storyboard"]


def test_vidu_wizard_brief_no_key_502_refunds(client, auth_headers):
    """conftest 清空作图 key → 简报识别抛错 → 502 + 退 title 点(失败必退点)。"""
    bal0 = _bal(client, auth_headers)
    r = client.post("/api/vidu/wizard/brief", headers=auth_headers,
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 502
    assert _bal(client, auth_headers) == bal0


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
    monkeypatch.setattr(settings, "vidu_model", "viduq2-pro-fast")
    monkeypatch.setattr(settings, "vidu_ref_model", "viduq2-pro-fast")
    monkeypatch.setattr(settings, "vidu_poll_interval", 0.0)
    monkeypatch.setattr(settings, "vidu_base_url", "https://api.vidu.cn")
    monkeypatch.setattr(httpx, "Client", _FakeClient)


def test_vidu_provider_single_image_uses_img2video(monkeypatch):
    """1 张图 → img2video,用 viduq2-pro-fast;audio 显式发;q2 不发 movement;audio_type 默认不发。"""
    from app.ai import vidu as vidu_mod
    _setup(monkeypatch)
    prov = vidu_mod.get_vidu_provider()
    assert isinstance(prov, vidu_mod.ViduProvider)
    out = prov.image_to_video([Image.open(_png())], "真人按压旋转", aspect="portrait",
                              resolution="720p", seconds=8, audio=True)
    assert out["ext"] == "mp4" and out["meta"]["engine"] == "viduq2-pro-fast"
    cap = _FakeClient.captured
    assert cap["url"].endswith("/ent/v2/img2video")
    assert cap["headers"]["Authorization"] == "Token k-test"
    body = cap["body"]
    assert body["model"] == "viduq2-pro-fast" and body["duration"] == 8 and body["resolution"] == "720p"
    assert body["audio"] is True and "audio_type" not in body       # 默认不发 audio_type
    assert "movement_amplitude" not in body and "bgm" not in body


def test_vidu_provider_two_images_uses_reference2video(monkeypatch):
    """2 张图 → reference2video(provider 仍支持);即便 audio=True 也【完全不含 audio】(对齐官方,防 400)。"""
    from app.ai import vidu as vidu_mod
    _setup(monkeypatch)
    prov = vidu_mod.get_vidu_provider()
    imgs = [Image.open(_png((200, 40, 40))), Image.open(_png((40, 200, 40)))]
    out = prov.image_to_video(imgs, "脚本", aspect="square", resolution="1080p", seconds=10, audio=True)
    assert out["ext"] == "mp4"
    cap = _FakeClient.captured
    assert cap["url"].endswith("/ent/v2/reference2video")
    body = cap["body"]
    assert body["model"] == "viduq2-pro-fast" and len(body["images"]) == 2 and body["aspect_ratio"] == "1:1"
    assert "audio" not in body and "audio_type" not in body


def test_vidu_duration_clamped_in_provider(monkeypatch):
    """provider 内也夹时长:越界 30 → 10。"""
    from app.ai import vidu as vidu_mod
    _setup(monkeypatch)
    prov = vidu_mod.get_vidu_provider()
    prov.image_to_video([Image.open(_png())], "x", seconds=30)
    assert _FakeClient.captured["body"]["duration"] == 10


def test_vidu_provider_sends_audio_type_sound_effect_only(monkeypatch):
    """给了 audio_type 时 provider 必须发出去(防"默认 All=配人声"的 bug)。"""
    from app.ai import vidu as vidu_mod
    _setup(monkeypatch)
    prov = vidu_mod.get_vidu_provider()
    prov.image_to_video([Image.open(_png())], "x", audio=True, audio_type="Sound-effect_only")
    body = _FakeClient.captured["body"]
    assert body["audio"] is True and body["audio_type"] == "Sound-effect_only"


def test_vidu_sfx_mode_forces_sound_effect_only(client, auth_headers, monkeypatch, tool_result):
    """原生音效(sfx)→ worker 必须给 provider 传 audio_type=Sound-effect_only(只出音效、不配人声)。"""
    from app.ai import vidu as vidu_mod
    captured = {}

    class _Fake:
        name = "vidu"

        def image_to_video(self, images, prompt, *, aspect="portrait", resolution="720p",
                           seconds=5, audio=False, audio_type=""):
            captured["audio"] = audio
            captured["audio_type"] = audio_type
            return {"bytes": b"\x00\x00\x00\x18ftypmp42X", "ext": "mp4", "url": "",
                    "meta": {"engine": "viduq2-pro-fast"}}
    monkeypatch.setattr(vidu_mod, "get_vidu_provider", lambda: _Fake())
    r = client.post("/api/vidu/ai-generate", headers=auth_headers,
                    data={"seconds": 5, "sound_mode": "sfx", "scene_frame": "false"},
                    files={"file": ("x.png", _png(), "image/png")})
    tool_result(auth_headers, r)
    assert captured["audio"] is True and captured["audio_type"] == "Sound-effect_only"
