"""视频生成模块:离线 GIF 展示视频(service + HTTP + workflow step)。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _png(color=(40, 120, 200)) -> io.BytesIO:
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(img).ellipse([60, 60, 240, 240], fill=color)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------- service(离线真实) ----------
def test_make_showcase_kenburns_is_valid_gif():
    from app.services import video
    img = Image.open(_png())
    res = video.make_showcase([img], style="kenburns", aspect="square", fps=12)
    assert res["bytes"][:6] in (b"GIF87a", b"GIF89a")  # GIF 魔数
    assert res["frames"] >= 2 and res["width"] == 800 and res["height"] == 800
    # 能被 PIL 当多帧 GIF 读回
    gif = Image.open(io.BytesIO(res["bytes"]))
    assert getattr(gif, "is_animated", False) and gif.n_frames == res["frames"]


def test_make_showcase_empty_images_safe():
    # P1-1:空输入两种 style 都不崩(用占位帧)
    from app.services import video
    for style in ("kenburns", "slideshow"):
        res = video.make_showcase([], style=style, aspect="square")
        assert res["frames"] >= 2 and res["bytes"][:6] in (b"GIF87a", b"GIF89a")


def test_make_showcase_portrait_and_slideshow():
    from app.services import video
    imgs = [Image.open(_png((200, 40, 40))), Image.open(_png((40, 200, 40)))]
    res = video.make_showcase(imgs, style="slideshow", aspect="portrait", fps=10, text="New find!")
    assert res["width"] == 720 and res["height"] == 1280
    assert res["bytes"][:6] in (b"GIF87a", b"GIF89a")


# ---------- HTTP ----------
def test_video_generate_endpoint_charges_and_returns_gif(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/generate", headers=auth_headers,
                    data={"style": "kenburns", "aspect": "square"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["frames"] >= 2 and body["video_url"].endswith(".gif")
    got = client.get(body["video_url"])
    assert got.status_code == 200 and got.content[:6] in (b"GIF87a", b"GIF89a")
    bal1 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert bal1 == bal0 - 3  # video 扣 3


def test_video_generate_requires_auth(client):
    r = client.post("/api/video/generate", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


def test_video_bad_image_refunds(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/generate", headers=auth_headers,
                    files={"file": ("x.png", io.BytesIO(b"nope"), "image/png")})
    assert r.status_code == 400
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_video_options(client, auth_headers):
    r = client.get("/api/video/options", headers=auth_headers)
    assert r.status_code == 200
    assert "portrait" in r.json()["aspects"]


# ---------- AI 图生视频(异步;eager 下 POST 返回后作业已 done)----------
# 测试默认 provider=local(conftest 未设 POD_VIDEO_PROVIDER)→ 兜底 GIF,离线确定性。
def test_ai_generate_local_fallback_gif(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "镜头推近展示", "aspect": "portrait"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert job["status"] == "done", job
    res = job["result"]
    assert res["ext"] == "gif" and res["video_url"].endswith(".gif")
    assert res["engine"] == "local-gif" and res["degraded"] is True
    got = client.get(res["video_url"])
    assert got.status_code == 200 and got.content[:6] in (b"GIF87a", b"GIF89a")
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 3


def test_ai_generate_two_frames_slideshow(client, auth_headers):
    # 上传两张=首尾帧;本地兜底用 slideshow 出 GIF,仍应成功。
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "", "aspect": "square"},
                    files={"file": ("a.png", _png((200, 40, 40)), "image/png"),
                           "file2": ("b.png", _png((40, 200, 40)), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["result"]["video_url"].endswith(".gif")


def test_two_frames_never_override_first_frame_with_mufra(client, auth_headers, monkeypatch, png):
    # 【首尾帧铁律】给了尾帧(2 张图)+ 开了「场景首帧」+ 配了 key → 母帧绝不能触发(否则会把用户首帧掉包)。
    # 回归此前真实 bug:首尾帧被母帧覆盖首帧 → 过渡稀烂。
    from app.ai import openai_image
    from app.config import settings
    called = {"edit": 0}

    def _fake_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        called["edit"] += 1
        from PIL import Image as _Img
        return _Img.new("RGB", (64, 96))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _fake_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "变形过渡", "scene_frame": "true", "seconds": "10", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png"),
                           "file2": ("b.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert called["edit"] == 0    # 首尾帧模式:母帧被跳过,两端都用用户原图


def test_ai_generate_requires_auth(client):
    r = client.post("/api/video/ai-generate", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


# ---------- 双分镜 15s(5s 分镜 + 10s 分镜 → 拼接)----------
def test_concat_videos_single_and_empty_passthrough():
    # 单段/空段不拼接,直接返回(避免无谓重编码)
    from app.services.video_concat import concat_videos
    assert concat_videos([b"abc"], "mp4") == b"abc"
    assert concat_videos([], "mp4") == b""
    assert concat_videos([b"", b"only"], "gif") == b"only"


def test_concat_gif_stitches_all_frames():
    # gif 拼接(纯 Pillow,离线):两段帧数相加、仍是合法可读的动画 GIF
    from PIL import Image

    from app.services import video as vsvc
    from app.services.video_concat import concat_videos
    a = vsvc.make_showcase([Image.open(_png((200, 40, 40)))], style="kenburns",
                           aspect="portrait", fps=12, seconds=5)
    b = vsvc.make_showcase([Image.open(_png((40, 200, 40)))], style="kenburns",
                           aspect="portrait", fps=12, seconds=10)
    merged = concat_videos([a["bytes"], b["bytes"]], "gif")
    assert merged[:6] in (b"GIF87a", b"GIF89a")
    gif = Image.open(io.BytesIO(merged))
    assert gif.is_animated and gif.n_frames == a["frames"] + b["frames"]


def test_ai_generate_two_shot_concats_offline(client, auth_headers):
    # 选 15s = 三分镜:本地兜底 3 段 GIF(5+5+5)并行生成 → 拼接成一段更长 GIF;作业 done、扣 video×3=9。
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "分镜①:产品特写推近", "prompt2": "分镜②:达人出镜使用",
                          "seconds": "15", "aspect": "portrait"},   # 15s=三分镜
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    res = job["result"]
    assert res["ext"] == "gif" and res["video_url"].endswith(".gif")
    assert res["two_shot"] is True
    got = client.get(res["video_url"])
    assert got.status_code == 200 and got.content[:6] in (b"GIF87a", b"GIF89a")
    # 拼接后帧数应明显多于单段上限(5s≈60 帧 + 10s≈120 帧封顶 = 180 > 单段 120)
    from PIL import Image
    assert Image.open(io.BytesIO(got.content)).n_frames > 120
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 9  # video×3


def test_two_shot_generates_two_segments_and_total_seconds(client, auth_headers, monkeypatch, png):
    # 三分镜:provider 被调 3 次(5+5+5 三段并行),旁白按 15s 总时长写稿
    from app.ai import video as video_mod
    from app.services import voiceover as vo_mod

    calls = {"seconds": [], "vo_seconds": []}

    class _FakeProvider:
        def image_to_video(self, images, prompt, size=None, seconds=None, with_audio=None):
            calls["seconds"].append(seconds)
            return {"bytes": b"FAKEMP4" * 16, "ext": "mp4", "meta": {"engine": "fake"}}

    monkeypatch.setattr(video_mod, "get_video_provider", lambda: _FakeProvider())
    monkeypatch.setattr("app.services.video_concat.concat_mp4", lambda segs, keep_audio=False: b"".join(segs))

    def _fake_vo(video_bytes, image, description, language, seconds, subtitle=False):
        calls["vo_seconds"].append(seconds)
        return video_bytes, "稿"
    monkeypatch.setattr(vo_mod, "add_voiceover", _fake_vo)

    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "分镜1", "prompt2": "分镜2", "seconds": "15", "voiceover": "true", "language": "英语", "aspect": "portrait"},
                    files={"file": ("x.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert sorted(calls["seconds"]) == [5, 5, 5]  # 三段:5+5+5 动作链
    assert calls["vo_seconds"] == [15]            # 旁白按拼接后的 15s 总时长写稿


def test_two_shot_native_sound_keeps_audio_in_concat(client, auth_headers, monkeypatch, png):
    # 双分镜 + 视频音效 → 拼接须保留原生音轨(concat_mp4 收到 keep_audio=True);默认/旁白则 False
    from app.ai import video as video_mod

    seen = {}

    class _FakeProvider:
        def image_to_video(self, images, prompt, size=None, seconds=None, with_audio=None):
            return {"bytes": b"FAKEMP4" * 8, "ext": "mp4", "meta": {"engine": "fake"}}

    monkeypatch.setattr(video_mod, "get_video_provider", lambda: _FakeProvider())
    monkeypatch.setattr("app.services.video_concat.concat_mp4",
                        lambda segs, keep_audio=False: seen.update(keep_audio=keep_audio) or b"".join(segs))

    def _run(extra):
        r = client.post("/api/video/ai-generate", headers=auth_headers,
                        data={"prompt": "分镜1", "prompt2": "分镜2", "seconds": "15", "aspect": "portrait", **extra},
                        files={"file": ("x.png", png(), "image/png")})
        assert r.status_code == 200, r.text
        job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
        assert job["status"] == "done", job

    _run({"native_sound": "true"})
    assert seen["keep_audio"] is True          # 视频音效 → 保留音轨
    _run({"voiceover": "true", "language": "英语"})
    assert seen["keep_audio"] is False         # 旁白 → 拼接丢音轨(后叠旁白)


def test_two_shot_15s_provider_failure_falls_back_to_gif_and_refunds(client, auth_headers, monkeypatch, png):
    # 【保证交付】三分镜 provider 抛错/超时 → 不再整作业 error,而是降级兜底本地 GIF + 退回全部 9 点。
    # 用户永远拿到可用结果(成片 done),且降级=不按 AI 视频原价收费(扣 9 退 9,净 0)。
    from app.ai import video as video_mod

    def _boom():
        class _P:
            def image_to_video(self, *a, **k):
                raise RuntimeError("provider down")   # 模拟智谱超时/网关繁忙
        return _P()
    monkeypatch.setattr(video_mod, "get_video_provider", _boom)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "分镜1", "prompt2": "分镜2", "seconds": "15", "aspect": "portrait"},
                    files={"file": ("x.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job                       # 兜底交付:作业成功而非失败
    res = job["result"]
    assert res["video_url"].endswith(".gif")                  # 降级成本地产品展示 GIF
    assert res["degraded"] is True
    assert res.get("warnings"), "降级须显式告知用户原因"
    # 扣了 9(video×3)、兜底退了 9 → 余额回到原点(降级不收 AI 视频原价)
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_single_shot_provider_failure_falls_back_to_gif_and_refunds(client, auth_headers, monkeypatch, png):
    # 单镜同样保证交付:provider 失败 → 兜底 GIF + 退回 1 笔 video(3 点),作业 done。
    from app.ai import video as video_mod

    def _boom():
        class _P:
            def image_to_video(self, *a, **k):
                raise RuntimeError("timeout")
        return _P()
    monkeypatch.setattr(video_mod, "get_video_provider", _boom)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "商品旋转展示", "seconds": "10", "aspect": "portrait"},
                    files={"file": ("x.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    res = job["result"]
    assert res["video_url"].endswith(".gif") and res["degraded"] is True
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0  # 扣3退3


def test_two_shot_15s_insufficient_credits_402_refunds_first(client, auth_headers, monkeypatch, png):
    # 余额只够 1 笔 video(3 点)时选 15s:第二笔扣点失败 → 402 + 退回第一笔(净不扣)
    from app.services import billing
    real_charge = billing.charge
    state = {"calls": 0}

    def _charge_once(db, user, op):
        if op == "video":
            state["calls"] += 1
            if state["calls"] == 2:      # 第二笔(翻倍那笔)模拟余额不足
                raise billing.InsufficientCredits("video", 3, 0)
        return real_charge(db, user, op)
    monkeypatch.setattr(billing, "charge", _charge_once)
    # 注意:charge_for 依赖在 services.billing 里用的是模块内 charge;routers.video 也 import 了 charge
    from app.routers import video as video_router
    monkeypatch.setattr(video_router, "charge", _charge_once)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "x", "seconds": "15", "aspect": "portrait"},
                    files={"file": ("x.png", png(), "image/png")})
    assert r.status_code == 402, r.text
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0  # 净不扣


def test_wizard_proposals_two_shot_returns_shots(monkeypatch):
    # 三分镜(seconds=15)生成方案:每个方案含 shot1/shot2/shot3(动作链),storyboard 为合并展示
    from app.services import video_wizard
    fake = ('[{"title":"出门赴约","angle":"a","model":"无模特","environment":"居家",'
            '"shot1":"【0-5秒】看手机收到消息","shot2":"【0-5秒】拿钥匙推门",'
            '"shot3":"【0-5秒】走在街头","storyboard":"合并脚本"}]')
    monkeypatch.setattr(video_wizard, "_chat", lambda msgs: fake)
    out = video_wizard.generate_proposals("杯子", "家居", "保温", seconds=15, n=1)
    assert out[0]["shot1"] == "【0-5秒】看手机收到消息"
    assert out[0]["shot2"] == "【0-5秒】拿钥匙推门"
    assert out[0]["shot3"] == "【0-5秒】走在街头"     # 第三拍(动作链 payoff)
    # 单段(10s)不产 shot1/shot2/shot3
    monkeypatch.setattr(video_wizard, "_chat",
                        lambda msgs: '[{"title":"t","storyboard":"【0-10秒】展示"}]')
    out10 = video_wizard.generate_proposals("杯子", "家居", "保温", seconds=10, n=1)
    assert "shot1" not in out10[0]


def test_video_delete_goes_to_trash(client, auth_headers):
    """回归:图生视频应入库为素材;删任务 → 素材进回收站(此前视频没入库 → 删了成幽灵、不进回收站)。"""
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "镜头推近", "aspect": "portrait"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    job = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert job["status"] == "done", job
    vurl = job["result"]["video_url"]
    # ① 已登记为素材(否则删任务时无 Asset 可移入回收站)
    assets = client.get("/api/space/assets", headers=auth_headers).json()["items"]
    assert any(a["url"] == vurl for a in assets), "视频应已登记为素材"
    # ② 删任务 → 该视频素材进回收站(可恢复)
    assert client.delete(f"/api/jobs/{jid}", headers=auth_headers).status_code == 200
    trash = client.get("/api/space/trash", headers=auth_headers).json()["items"]
    assert any(t["url"] == vurl for t in trash), "删任务后视频应出现在回收站"


def test_local_gif_generate_lands_in_library(client, auth_headers):
    """回归:本地 GIF(/api/video/generate)也应入库(此前漏 save_as_asset+mirror,GIF 成幽灵)。"""
    r = client.post("/api/video/generate", headers=auth_headers,
                    data={"style": "kenburns", "aspect": "square"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    vurl = r.json()["video_url"]
    assets = client.get("/api/space/assets", headers=auth_headers).json()["items"]
    assert any(a["url"] == vurl for a in assets), "本地 GIF 应已登记为素材"


def test_ai_generate_bad_image_refunds(client, auth_headers):
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    files={"file": ("x.png", io.BytesIO(b"nope"), "image/png")})
    assert r.status_code == 400
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_ai_generate_bad_second_image_ignored(client, auth_headers):
    # 第 2 张坏图不阻断:降级为单图,作业仍 done。
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"aspect": "portrait"},
                    files={"file": ("a.png", _png(), "image/png"),
                           "file2": ("b.png", io.BytesIO(b"bad"), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job


def test_video_options_ai_ready_false_offline(client, auth_headers):
    # provider=local + 无 key → ai_ready=False(前端据此提示「会降级为本地 GIF」)。
    r = client.get("/api/video/options", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ai_ready"] is False and "portrait" in body["aspects"]
    # 扩充后的画幅都在(竖/方/横)
    for a in ("portrait", "portrait34", "square", "landscape43", "landscape"):
        assert a in body["aspects"]
    # 分辨率 + 语言 + 商品类目 + 时长也返回
    assert "1080p" in body["resolutions"] and "4k" in body["resolutions"]
    assert "葡萄牙语" in body["languages"]
    assert "通用" in body["categories"] and "马克杯" in body["categories"]
    assert 5 in body["durations"] and 10 in body["durations"] and 15 in body["durations"]  # 15=多分镜
    assert body["two_shot"]["plan"] == [5, 5, 5] and body["two_shot"]["total"] == 15  # 三分镜 15s
    assert body["two_shot"]["shots"] == 3
    assert body["smart_ready"] is False   # 无 openai key → 智能识别不可用


def test_smart_describe_no_key_502_refunds(client, auth_headers):
    # 无 openai key(conftest 清空)→ 502 + 退点(title=1)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/smart-describe", headers=auth_headers,
                    data={"video_type": "开箱分享", "seconds": "5"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 502
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_smart_describe_ok_charges_title(client, auth_headers, monkeypatch):
    # mock 视觉模型 → 返回脚本;扣 title=1
    from app.services import video_describe
    monkeypatch.setattr(video_describe, "smart_describe",
                        lambda *a, **k: "【0-2秒】镜头推近这件商品,展示印花细节。【2-5秒】拿起转动展示。")
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/smart-describe", headers=auth_headers,
                    data={"video_type": "开箱分享", "seconds": "5", "category": "马克杯"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert "【0-2秒】" in r.json()["description"]
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 1


def test_smart_describe_passes_selling_points(client, auth_headers, monkeypatch):
    # 产品卖点经端点透传给 smart_describe(AI 围绕卖点写脚本)
    from app.services import video_describe
    seen = {}

    def _fake(*a, **k):
        seen.update(k)
        return "【0-2秒】围绕卖点展示商品。"
    monkeypatch.setattr(video_describe, "smart_describe", _fake)
    r = client.post("/api/video/smart-describe", headers=auth_headers,
                    data={"video_type": "达人带货", "seconds": "5", "category": "水杯",
                          "selling_points": "大容量保温·12小时持热·防漏"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert seen.get("selling_points") == "大容量保温·12小时持热·防漏"


def test_wizard_brief_ok_charges_title(client, auth_headers, monkeypatch):
    # Step1:mock 视觉模型 → 结构化简报;扣 title=1
    from app.services import video_wizard
    monkeypatch.setattr(video_wizard, "describe_product",
                        lambda *a, **k: {"name": "田园风抱枕套", "audience": "家居人群",
                                         "selling_points": "亚麻透气、隐形拉链易拆洗"})
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/wizard/brief", headers=auth_headers,
                    data={"language": "英语", "selling_points": "亚麻"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "田园风抱枕套" and "拉链" in r.json()["selling_points"]
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 1


def test_wizard_brief_no_key_502_refunds(client, auth_headers):
    # 无 openai key(conftest 清空)→ 502 + 退点
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/wizard/brief", headers=auth_headers,
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 502
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_wizard_proposals_ok_charges_title(client, auth_headers, monkeypatch):
    # Step2:mock → 3 个方案;扣 title=1
    from app.services import video_wizard
    monkeypatch.setattr(video_wizard, "generate_proposals",
                        lambda *a, **k: [{"title": f"方案{i}", "angle": "x", "model": "m",
                                          "environment": "e", "storyboard": "【0-2秒】展示。"} for i in range(3)])
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/wizard/proposals", headers=auth_headers,
                    data={"name": "抱枕套", "audience": "家居", "selling_points": "亚麻",
                          "seconds": "5", "language": "英语"})
    assert r.status_code == 200, r.text
    assert len(r.json()["proposals"]) == 3 and r.json()["proposals"][0]["storyboard"]
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 1


def test_wizard_proposals_no_key_502_refunds(client, auth_headers):
    # 真实 generate_proposals 无 key → 抛错 → 502 + 退点
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/wizard/proposals", headers=auth_headers,
                    data={"name": "x", "audience": "y", "selling_points": "z", "seconds": "5"})
    assert r.status_code == 502
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_wizard_parse_json_salvage():
    # JSON 容错:剥 ``` 围栏 + 数组被裹进对象都能救回
    from app.services.video_wizard import _loads_json
    assert _loads_json('```json\n{"name":"杯子"}\n```')["name"] == "杯子"
    arr = _loads_json('前缀垃圾 [{"title":"A"},{"title":"B"}] 后缀', expect_list=True)
    assert isinstance(arr, list) and arr[0]["title"] == "A"


def test_zhipu_provider_retries_transient_writetimeout(monkeypatch):
    # 网络健壮性:建任务 POST 第一次 WriteTimeout → 自动重试成功;轮询 SUCCESS → 下载成片(不整单失败)
    import httpx
    from PIL import Image

    from app.ai import video as vmod
    from app.config import settings
    monkeypatch.setattr(settings, "video_api_key", "k")
    calls = {"post": 0, "dl": 0}

    class _Resp:
        def __init__(self, j=None, content=b""):
            self._j = j; self.content = content; self.status_code = 200; self.text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return self._j

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            calls["post"] += 1
            if calls["post"] == 1:
                raise httpx.WriteTimeout("write timed out")   # 首次抖动
            return _Resp(j={"id": "task1"})

        def get(self, url, **k):
            if "async-result" in url:
                return _Resp(j={"task_status": "SUCCESS",
                                "video_result": [{"url": "http://x/v.mp4", "cover_image_url": "c"}]})
            calls["dl"] += 1
            return _Resp(content=b"MP4BYTES")

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr("time.sleep", lambda *a: None)
    out = vmod.ZhipuCogVideoProvider().image_to_video([Image.new("RGB", (32, 32))], "p", seconds=5)
    assert out["bytes"] == b"MP4BYTES" and out["ext"] == "mp4"
    assert calls["post"] == 2 and calls["dl"] == 1   # WriteTimeout 后重试一次成功


def test_zhipu_provider_resubmits_on_task_fail(monkeypatch):
    # 智谱把第1个任务判 FAIL(『网络错误,请稍后重试』)→ 自动重建新任务 → 第2个 SUCCESS(不整单挂)
    import httpx
    from PIL import Image

    from app.ai import video as vmod
    from app.config import settings
    monkeypatch.setattr(settings, "video_api_key", "k")
    calls = {"post": 0, "dl": 0}

    class _Resp:
        def __init__(self, j=None, content=b""):
            self._j = j; self.content = content; self.status_code = 200; self.text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return self._j

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            calls["post"] += 1
            return _Resp(j={"id": f"task{calls['post']}"})

        def get(self, url, **k):
            if "async-result" in url:
                if url.endswith("task1"):
                    return _Resp(j={"task_status": "FAIL",
                                    "error": {"code": "1234", "message": "网络错误,请稍后重试"}})
                return _Resp(j={"task_status": "SUCCESS",
                                "video_result": [{"url": "http://x/v.mp4", "cover_image_url": "c"}]})
            calls["dl"] += 1
            return _Resp(content=b"OKMP4")

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr("time.sleep", lambda *a: None)
    out = vmod.ZhipuCogVideoProvider().image_to_video([Image.new("RGB", (32, 32))], "p", seconds=5)
    assert out["bytes"] == b"OKMP4"
    assert calls["post"] == 2 and calls["dl"] == 1   # 任务1 FAIL → 重建任务2 → 成功


def test_zhipu_provider_4xx_surfaces_response_body(monkeypatch):
    # 智谱 4xx(内容审核/参数/余额)→ 抛错带上【响应体真因】,不再只剩 "400 Bad Request" 无从定位
    import httpx
    import pytest
    from PIL import Image

    from app.ai import video as vmod
    from app.config import settings
    monkeypatch.setattr(settings, "video_api_key", "k")
    body = '{"error":{"code":"1301","message":"系统检测到内容可能包含敏感信息"}}'

    class _Resp400:
        status_code = 400
        text = body

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "400 Bad Request", request=httpx.Request("POST", "http://x"), response=self)

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            return _Resp400()

        def get(self, url, **k):
            return _Resp400()

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr("time.sleep", lambda *a: None)
    with pytest.raises(RuntimeError) as ei:
        vmod.ZhipuCogVideoProvider().image_to_video([Image.new("RGB", (32, 32))], "p", seconds=5)
    msg = str(ei.value)
    assert "400" in msg and "敏感信息" in msg          # 智谱响应体真因已带出


def test_video_edit_beat_plan():
    # 后期节奏核:beat 网格切段(全景/推近交替),覆盖全片、无缝、末段碎尾并入
    from app.services.video_edit import beat_plan
    plan = beat_plan(8.0, 1.8)
    assert len(plan) >= 2
    assert plan[0][0] == 0.0 and plan[-1][1] == 8.0          # 0 开始、覆盖到结尾
    assert all(plan[i][1] == plan[i + 1][0] for i in range(len(plan) - 1))   # 连续无缝
    assert [s[2] for s in plan] == [i % 2 == 1 for i in range(len(plan))]    # 全景/推近交替
    assert beat_plan(0, 1.8) == [] and beat_plan(8, 0) == []  # 边界
    p2 = beat_plan(3.9, 1.8)                                  # 末段 0.3s<0.6 → 并入上一段
    assert p2[-1][1] == 3.9 and all(e - s >= 0.6 for s, e, _ in p2)


def test_video_edit_framing_cycle():
    # 多景别循环(治"镜头密度太低"):相邻 beat 取不同景别/机位;第 0 段=全景(纯 scale 无 crop)。
    from app.services.video_edit import _FRAMINGS, _framing_filter
    f0 = _framing_filter(0, 720, 1280)
    assert "crop" not in f0 and "scale=720:1280" in f0      # 第0段全景:无裁切
    f1 = _framing_filter(1, 720, 1280)
    assert "crop=" in f1                                    # 后续段:裁切到不同景别(推近/偏移)
    # 循环覆盖多种构图(≥4 种),且按 i 循环(通用,不写死场景)
    assert len(_FRAMINGS) >= 4
    assert _framing_filter(len(_FRAMINGS), 720, 1280) == _framing_filter(0, 720, 1280)  # 循环


def test_video_edit_pick_music(tmp_path):
    # 音乐床选曲:空目录/不存在 → None;有音频文件 → 挑到;非音频忽略
    from app.services.video_edit import pick_music
    assert pick_music(str(tmp_path)) is None
    assert pick_music(str(tmp_path / "nope")) is None
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "note.txt").write_bytes(b"x")
    assert pick_music(str(tmp_path)).endswith("a.mp3")


def test_aspect_size_by_resolution():
    # 画幅 × 分辨率 → 尺寸(短边=分辨率档,长边按比例)
    from app.ai.video import aspect_size
    assert aspect_size("portrait", "1080p") == "1080x1920"
    assert aspect_size("landscape", "1080p") == "1920x1080"
    assert aspect_size("square", "720p") == "720x720"
    assert aspect_size("portrait", "4k") == "2160x3840"


def test_compose_prompt_pro_layers():
    # 拼装:脚本(任务/故事层)+ 巴西风格(葡语)+ 语言 + 导演层 + 画面底线;地区随语言变。
    from app.ai.video import compose_prompt
    out = compose_prompt("素人开箱,手持拍摄", language="葡萄牙语")
    assert "素人开箱,手持拍摄" in out
    assert "巴西" in out                             # 葡语 → 巴西本地风格
    assert "葡萄牙语" in out                          # 语言指令
    assert "拉伸" in out                             # 画面底线:印花不被拉伸扭曲
    # 地区风格跟着语言智能变:英语 → 欧美(不是巴西)
    out2 = compose_prompt("镜头推近", language="英语")
    assert "巴西" not in out2 and "欧美" in out2 and "英语" in out2
    # 「无对白」→ 不加地区风格、不加配音句
    out3 = compose_prompt("镜头推近", language="无对白")
    assert "巴西" not in out3 and "欧美" not in out3 and "配音使用" not in out3 and out3.strip()


def test_compose_prompt_is_positive_director_led():
    # 正向导演为主(治"负向堆太多→模型保守无聊"):身份=记录真实生活 + 任务动作>模特动作 + 手持镜头;
    # 旧的大段负向堆砌已移除(只在底线留少量必要的"不")。
    from app.ai.video import compose_prompt
    out = compose_prompt("展示商品", language="无对白")
    assert "导演定位" in out and "真实生活" in out      # 身份层:记录真实生活片段
    assert "任务动作" in out                            # 任务驱动(任务动作 > 模特动作)
    assert "手持" in out                                # 镜头层:手持随手拍
    assert "平滑克制" not in out                         # 不压制镜头
    # 已拆掉负向堆砌:这些旧负向词不应再出现(改用正向导演 + 简短底线)
    assert "自行开合" not in out and "果冻" not in out and "凭空" not in out and "瞬移" not in out


def test_compose_prompt_keeps_essential_guards():
    # 画面底线(简短保留真实踩过的失败):印花一致 + 材质物理(垂坠/回弹/液体)+ 重力连贯。
    from app.ai.video import compose_prompt
    out = compose_prompt("展示商品", language="无对白")
    assert "保持一致" in out                            # 印花/设计一致(POD 关键)
    assert "垂坠" in out and "回弹" in out and "液体" in out  # 材质物理底线(治砖块/弹性失真)
    assert "重力" in out                                # 物理连贯


def test_ai_generate_scene_frame_with_gptimage(client, auth_headers, monkeypatch, png):
    # 配了 key 时「场景首帧」走 gpt-image 编辑首帧,再生视频(本地兜底 GIF);确认流程不崩。
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    called = {"edit": 0}

    def _fake_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        called["edit"] += 1
        assert "立体形态" in prompt and "图案" in prompt   # 场景首帧指令:立体使用形态 + 保留印花(治砖块)
        return _Img.new("RGB", (64, 96), (10, 20, 30))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _fake_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "开箱", "category": "马克杯", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert called["edit"] == 1            # 场景首帧确实调了 gpt-image edit


# ---------- 旁白配音(voiceover)----------
def test_voiceover_language_mapping():
    from app.services import voiceover
    assert voiceover.supported_language("葡萄牙语") and voiceover.supported_language("中文")
    assert not voiceover.supported_language("无对白") and not voiceover.supported_language("火星语")
    assert voiceover.voice_for("葡萄牙语").startswith("pt-BR")
    assert voiceover.voice_for("无对白") is None


def test_voiceover_voice_pool_randomizes():
    # 修复「旁白永远同一个人声」:每语言有多个候选嗓音(男女混合),pick_voice 随机挑一个
    from app.services import voiceover
    for lang, prefix in [("葡萄牙语", "pt-"), ("英语", "en-"), ("中文", "zh-CN-"), ("西班牙语", "es-")]:
        voices = voiceover._VOICE[lang][0]
        assert len(voices) >= 2, f"{lang} 应有多个候选嗓音(不再写死一个)"
        assert all(v.startswith(prefix) for v in voices)
    picks = {voiceover.pick_voice("中文") for _ in range(40)}
    assert len(picks) > 1, "pick_voice 应能随机取到多个不同嗓音"
    assert voiceover.pick_voice("无对白") is None


def test_voiceover_graceful_no_key(png):
    # 无 openai key(conftest 清空)→ 写稿返回空 → add_voiceover 原样返回视频、无旁白,不报错也不引入重依赖
    from PIL import Image

    from app.services import voiceover
    vid = b"FAKE_MP4_BYTES" * 20
    img = Image.open(png())
    for sub in (False, True):   # 字幕开/关都应优雅降级(无 key 无稿)
        out, script = voiceover.add_voiceover(vid, img, "镜头脚本", "葡萄牙语", 10, subtitle=sub)
        assert out == vid and script == "", "无 key 应原样返回视频、无旁白/字幕"
    # 不支持的语言(无对白)→ 直接跳过
    out2, script2 = voiceover.add_voiceover(vid, img, "x", "无对白", 10, subtitle=True)
    assert out2 == vid and script2 == ""


def test_subtitle_render_no_crash():
    # 字幕渲染:有字体→返回合法 PNG;无字体→返回空(优雅降级)。两种都不应崩。
    from app.services import voiceover
    out = voiceover._subtitle_png("Hello world, buy now!", "英语", 720)
    assert out == b"" or out[:8] == b"\x89PNG\r\n\x1a\n", "应为空或合法 PNG"


def test_subtitle_segments_timed():
    # 字幕分段:按短语切、按字数比例分配时间窗、顺序不重叠、覆盖整段时长(跟着语音逐段显示)
    from app.services import voiceover
    segs = voiceover._segments("这款大容量保温杯，颜值在线、握感舒适，随时喝水超方便,喜欢就下单", 10.0, "中文")
    assert 2 <= len(segs) <= 8, "应拆成多段(非整段一次性显示)"
    assert segs[0][0] == 0.0, "首段从 0 开始"
    assert all(s < e for s, e, _ in segs), "每段起 < 止"
    assert all(segs[i][1] <= segs[i + 1][0] + 0.01 for i in range(len(segs) - 1)), "时间窗顺序不重叠"
    assert segs[-1][1] >= 10.0, "末段覆盖到结尾"
    # 拉丁语按词分段
    segs2 = voiceover._segments("This is a really nice cup. Buy it right now please today friend.", 8.0, "英语")
    assert len(segs2) >= 2 and all(t for _, _, t in segs2)


def test_ai_generate_subtitle_offline_skips(client, auth_headers, png):
    # 开旁白+字幕但离线(provider=local → GIF):配音/字幕因 ext!=mp4 跳过,仍出兜底 GIF、作业 done
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "达人带货", "native_sound": "false", "voiceover": "true",
                          "subtitle": "true", "language": "英语", "aspect": "portrait"},
                    files={"file": ("x.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["result"]["video_url"].endswith(".gif")   # 本地兜底,配音/字幕跳过,作业不受影响


def test_video_native_sound_and_voiceover_gate(client, auth_headers, monkeypatch, png):
    # 默认无声 → with_audio=False 不叠旁白;视频音效开 → with_audio=True 不叠旁白;旁白开 → with_audio=False 叠旁白
    from app.ai import video as video_mod
    from app.services import voiceover as vo_mod

    calls = {"with_audio": [], "voiceover": 0}

    class _FakeProvider:
        def image_to_video(self, images, prompt, size=None, seconds=None, with_audio=None):
            calls["with_audio"].append(with_audio)
            return {"bytes": b"FAKEMP4" * 16, "ext": "mp4", "meta": {"engine": "fake"}}

    def _fake_add_voiceover(video_bytes, image, description, language, seconds, subtitle=False):
        calls["voiceover"] += 1
        return video_bytes, "口播稿"

    monkeypatch.setattr(video_mod, "get_video_provider", lambda: _FakeProvider())
    monkeypatch.setattr(vo_mod, "add_voiceover", _fake_add_voiceover)

    def _gen(**extra):
        data = {"prompt": "带货", "aspect": "portrait", "seconds": "5", **extra}
        r = client.post("/api/video/ai-generate", headers=auth_headers, data=data,
                        files={"file": ("x.png", png(), "image/png")})
        assert r.status_code == 200, r.text
        job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
        assert job["status"] == "done", job

    # 默认(都不传)→ 无声、不叠旁白
    _gen()
    assert calls["with_audio"][-1] is False and calls["voiceover"] == 0

    # 视频音效开 → with_audio=True、仍不叠旁白
    _gen(native_sound="true")
    assert calls["with_audio"][-1] is True and calls["voiceover"] == 0

    # 旁白开(音效关)→ with_audio=False、叠旁白一次
    _gen(native_sound="false", voiceover="true", language="英语")
    assert calls["with_audio"][-1] is False and calls["voiceover"] == 1


# ---------- 内容策划层:故事模板库 + 每镜独立母帧 ----------
def test_video_templates_library_structure():
    from app.services.video_templates import STORY_TEMPLATES, default_scenes, templates_for
    # 每个模板结构完整:id/name/story + ≥2 拍,每拍含 scene/action
    for t in STORY_TEMPLATES:
        assert t["id"] and t["name"] and t["story"]
        assert len(t["beats"]) >= 2
        for b in t["beats"][:2]:
            assert b["scene"] and b["action"]
    # 命中类目 → 含该类目模板;通用 → 给默认服装故事(ootd)
    assert any(t["id"] == "ootd" for t in templates_for("T恤"))
    assert any(t["id"] == "mug_morning" for t in templates_for("马克杯"))
    assert any(t["id"] == "ootd" for t in templates_for("通用"))
    # 后台默认中性场景(自动融合 / 向导兜底用):两拍非空且不同,适配任意品类
    s1, s2 = default_scenes("通用")
    assert s1 and s2 and s1 != s2


def test_ai_generate_two_shot_per_shot_mufra(client, auth_headers, monkeypatch, png):
    # 双分镜 + scene1/scene2 + 场景首帧 + key → 每镜各生成一张【独立母帧】(gpt-image edit 调 2 次,各含各自场景)
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    seen = []

    def _fake_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        seen.append(prompt)
        return _Img.new("RGB", (64, 96), (10, 20, 30))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _fake_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "分镜1动作", "prompt2": "分镜2动作",
                          "scene1": "卧室镜子前自拍", "scene2": "城市街头走路",
                          "seconds": "15", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert len(seen) == 3                            # 三镜每镜一张母帧(scene3 缺 → 后台补中性场景)
    assert any("卧室镜子前自拍" in s for s in seen)   # 分镜①母帧用了 scene1
    assert any("城市街头走路" in s for s in seen)     # 分镜②母帧用了 scene2


def test_ai_generate_two_shot_no_scene_auto_fuses_per_shot(client, auth_headers, monkeypatch, png):
    # 故事能力下沉后台:三分镜未给 scene(= 手动「视频类型」路径)+ key + 场景首帧
    # → 后台自动融合中性动作链 3 拍场景 → gpt-image edit 调 3 次、各镜场景不同(自动 per-shot 母帧)
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    from app.services.video_templates import default_scenes
    seen = []

    def _fake_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        seen.append(prompt)
        return _Img.new("RGB", (64, 96), (10, 20, 30))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _fake_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "分镜1", "prompt2": "分镜2", "seconds": "15", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert len(seen) == 3                              # 后台自动融合 → 三镜各一张母帧
    d = default_scenes("通用", 3)
    assert all(any(d[i] in s for s in seen) for i in range(3))  # 用了后台中性 3 拍动作链场景


def test_ai_generate_single_shot_shared_mufra(client, auth_headers, monkeypatch, png):
    # 单镜(10s)+ key + 场景首帧 → 一张共享母帧:gpt-image edit 只调 1 次(单镜不做 per-shot)
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    called = {"edit": 0}

    def _fake_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        called["edit"] += 1
        return _Img.new("RGB", (64, 96), (10, 20, 30))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _fake_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "单镜脚本", "seconds": "10", "scene_frame": "true",
                          "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert called["edit"] == 1                          # 单镜 → 一张共享母帧


def test_ai_generate_records_warning_when_scene_frame_fails(client, auth_headers, monkeypatch, png):
    # 母帧(gpt-image)失败(如额度不足/无 key)→ 不阻断、仍出片,但 Job 结果【显式记录降级 warning】,
    # 不再静默吞掉(治"用户拿到平铺像砖块的成片却不知为何")。
    from app.ai import openai_image
    from app.config import settings

    def _boom(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        raise RuntimeError("Error code: 403 insufficient_user_quota")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _boom)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "p", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job                 # 母帧失败不阻断,仍用原图出片
    warns = job["result"].get("warnings") or []
    assert any("场景母帧" in w for w in warns)           # 降级被显式记录 → 用户/运营能看见真因


def test_scene_frame_retries_then_succeeds(client, auth_headers, monkeypatch, png):
    # 母帧应用层重试:首次快错(SDK 不会自动重试的"网关未返回图像数据")→ 自动重试 → 第二次成功 →
    # 不降级、不记 warning(治"三分镜母帧偶发快错就降级失败")。
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    calls = {"n": 0}

    def _flaky_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("图片网关未返回图像数据")   # 首次快错
        return _Img.new("RGB", (64, 96), (10, 20, 30))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "video_mufra_attempts", 2)
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _flaky_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "单镜脚本", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert calls["n"] == 2                               # 重试了一次(共 2 次尝试)
    assert not any("场景母帧" in w for w in (job["result"].get("warnings") or []))  # 重试成功 → 不降级


def test_scene_frame_exhausts_attempts_then_degrades(client, auth_headers, monkeypatch, png):
    # 重试用尽仍失败 → 降级原图 + 记 warning;尝试次数 = video_mufra_attempts(应用层可控重试,非 SDK)。
    from app.ai import openai_image
    from app.config import settings
    calls = {"n": 0}

    def _boom(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        calls["n"] += 1
        raise RuntimeError("Error code: 502 bad gateway")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "video_mufra_attempts", 2)
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _boom)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "单镜脚本", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert calls["n"] == 2                               # 单镜母帧 1 张 × 2 次尝试
    assert any("场景母帧" in w for w in (job["result"].get("warnings") or []))


def test_wizard_proposals_two_shot_adds_scenes(monkeypatch):
    # 模型给了 scene1/scene2/story → 透传进方案(喂 per-shot 母帧)
    from app.services import video_wizard
    fake = ('[{"title":"出门","story":"出门赴约","model":"无","environment":"街头",'
            '"scene1":"卧室镜子前","shot1":"【0-5秒】看手机",'
            '"scene2":"玄关门口","shot2":"【0-5秒】拿钥匙推门",'
            '"scene3":"城市街头","shot3":"【0-5秒】走在街上","storyboard":"合并"}]')
    monkeypatch.setattr(video_wizard, "_chat", lambda msgs: fake)
    out = video_wizard.generate_proposals("T恤", "年轻人", "潮", seconds=15, n=1, category="T恤")
    assert out[0]["scene1"] == "卧室镜子前" and out[0]["scene2"] == "玄关门口" and out[0]["scene3"] == "城市街头"
    assert out[0]["story"] == "出门赴约"


def test_wizard_proposals_two_shot_scene_fallback(monkeypatch):
    # 模型没给 scene → 退到中性【动作链】通用场景兜底(不写死 OOTD),保证三镜非空且递进(per-shot 前提)
    from app.services import video_wizard
    from app.services.video_templates import default_scenes
    monkeypatch.setattr(video_wizard, "_chat",
                        lambda msgs: '[{"title":"t","shot1":"a","shot2":"b","storyboard":"s"}]')
    out = video_wizard.generate_proposals("T恤", "", "", seconds=15, n=1, category="T恤")
    d = default_scenes("T恤", 3)
    assert out[0]["scene1"] == d[0] and out[0]["scene2"] == d[1] and out[0]["scene3"] == d[2]
    assert len({d[0], d[1], d[2]}) == 3                   # 三拍场景各不相同(递进)
