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
        def image_to_video(self, images, prompt, *, seconds=None, **kwargs):
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
        def image_to_video(self, images, prompt, *, seconds=None, **kwargs):
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


def test_two_shot_15s_provider_failure_errors_and_refunds(client, auth_headers, monkeypatch, png):
    # 已删 GIF 兜底:三分镜 provider 抛错/超时(内部已重试)→ 作业 error + 退回全部 9 点(不再出 GIF)。
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
    assert job["status"] == "error", job                      # provider 真挂 → error(无 GIF 兜底)
    # 扣了 9(video×3)、error 路径退了 9(refund_n=n=3)→ 余额回到原点
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_single_shot_provider_failure_errors_and_refunds(client, auth_headers, monkeypatch, png):
    # 单镜:provider 失败 → 作业 error + 退回 1 笔 video(3 点),不出 GIF。
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
    assert job["status"] == "error", job
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
    monkeypatch.setattr(video_wizard, "_chat", lambda msgs, **kw: fake)
    out = video_wizard.generate_proposals("杯子", "家居", "保温", seconds=15, n=1)
    assert out[0]["shot1"] == "【0-5秒】看手机收到消息"
    assert out[0]["shot2"] == "【0-5秒】拿钥匙推门"
    assert out[0]["shot3"] == "【0-5秒】走在街头"     # 第三拍(动作链 payoff)
    # 预览 storyboard 一律由三拍合成(固定格式),不再用模型自由发挥的 → 杜绝"有时带 0-x秒、有时一段话"的格式漂移
    sb = out[0]["storyboard"]
    assert sb.startswith("【分镜①·0-5s】") and "【分镜②·5-10s】" in sb and "【分镜③·10-15s】" in sb
    assert "合并脚本" not in sb                        # 模型自由发挥的 storyboard 被丢弃,用合成的
    # 5/10s = 单镜【一条连续视频】(CogVideoX 单次生成不分镜):storyboard 是一段连贯描述,不产 shot、不带分镜/时间标签。
    # 即使模型甩了【0-10秒】这类标签,后端 _flatten_storyboard 也会剥掉 → 显示一段连贯文字。
    monkeypatch.setattr(video_wizard, "_chat",
                        lambda msgs, **kw: '[{"title":"t","storyboard":"【0-5秒】走到桌前\\n【5-10秒】端起杯子喝一口"}]')
    out10 = video_wizard.generate_proposals("杯子", "家居", "保温", seconds=10, n=1)
    assert "shot1" not in out10[0]                       # 单镜:不暴露 shot 字段
    sb10 = out10[0]["storyboard"]
    assert "分镜" not in sb10 and "秒" not in sb10 and "【" not in sb10   # 无任何分段/时间标签
    assert "走到桌前" in sb10 and "端起杯子喝一口" in sb10               # 动作内容保留、拼成一段


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


def test_wizard_expand_5s10s_returns_detailed_storyboard(client, auth_headers, monkeypatch):
    # 详细扩展(5/10s):精简 storyboard → 详细时间轴脚本;扣 title=1
    from app.services import video_wizard
    monkeypatch.setattr(video_wizard, "_chat",
                        lambda msgs, **kw: '{"storyboard":"【0-3秒】镜头推近端起杯子。【3-7秒】喝一口、放松微笑。"}')
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/wizard/expand", headers=auth_headers,
                    data={"seconds": "10", "storyboard": "端起杯子喝一口,放松。"})
    assert r.status_code == 200, r.text
    assert "【0-3秒】" in r.json()["storyboard"] and "shot1" not in r.json()   # 5/10s 只回 storyboard
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 1


def test_wizard_expand_15s_returns_detailed_shots(client, auth_headers, monkeypatch):
    # 详细扩展(15s 三分镜):分别扩 shot1/2/3 + 合成 storyboard(保连续性);扣 title=1
    from app.services import video_wizard
    monkeypatch.setattr(video_wizard, "_chat",
                        lambda msgs, **kw: '{"shot1":"①详细","shot2":"②详细承接①","shot3":"③详细承接②"}')
    r = client.post("/api/video/wizard/expand", headers=auth_headers,
                    data={"seconds": "15", "shot1": "①", "shot2": "②", "shot3": "③", "story": "出门"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["shot1"] == "①详细" and d["shot2"] == "②详细承接①" and d["shot3"] == "③详细承接②"
    assert "【分镜①·0-5s】①详细" in d["storyboard"] and "【分镜③·10-15s】" in d["storyboard"]


def test_wizard_expand_no_key_502_refunds(client, auth_headers):
    # 无 openai key(conftest 清空)→ 502 + 退点(失败必退点)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/wizard/expand", headers=auth_headers,
                    data={"seconds": "10", "storyboard": "x"})
    assert r.status_code == 502
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0


def test_wizard_proposals_5s10s_concise_default(monkeypatch):
    # 5/10s = 单镜【一条连续视频】(CogVideoX 单次生成不分镜):storyboard 必须是一段连贯描述,
    # 默认精简、【绝不出现分镜①②/0-x秒 标签】(那是 15s 多视频拼接才有的)。
    from app.services import video_wizard
    seen = {}

    def _cap(msgs, **kw):
        seen["prompt"] = msgs[0]["content"]
        # 模拟模型仍偶尔甩分段标签 → 后端必须 flatten 成一段
        return '[{"title":"t","storyboard":"【0-5秒】端起杯子喝一口\\n【5-10秒】放松靠回椅背"}]'
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    out = video_wizard.generate_proposals("杯子", "家居", "保温", seconds=10, n=1)
    assert "单镜" in seen["prompt"] and "别拆分镜" in seen["prompt"]      # prompt 框定单镜连续、别拆分镜
    assert "别写 0-x 秒" in seen["prompt"]                               # 去时间戳约束
    sb = out[0]["storyboard"]
    assert "分镜" not in sb and "秒" not in sb and "【" not in sb        # 输出无任何分段/时间标签
    assert "端起杯子喝一口" in sb and "放松靠回椅背" in sb               # 动作内容保留、合成一段连贯
    assert "shot1" not in out[0]                                        # 5/10s 输出契约:只有 storyboard,不暴露 shot


def test_flatten_storyboard_strips_segment_and_time_labels():
    # 5/10s 单镜连续:_flatten_storyboard 剥掉分段/时间标签,只留动作内容、合成一段
    from app.services.video_wizard import _flatten_storyboard
    assert _flatten_storyboard("【分镜①·0-5s】走到桌前\n【分镜②·5-10s】端起杯子") == "走到桌前 端起杯子"
    assert _flatten_storyboard("【0-3秒】镜头推近\n【3-7秒】喝一口") == "镜头推近 喝一口"
    assert _flatten_storyboard("分镜①:看手机  分镜②:出门") == "看手机 出门"
    assert _flatten_storyboard("0-3秒:走过去 (3-5秒)坐下") == "走过去 坐下"
    # 无标签的正常一段话原样保留(不误伤内容)
    assert _flatten_storyboard("她端起杯子喝一口,放松地靠回椅背") == "她端起杯子喝一口,放松地靠回椅背"


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


def test_scene_frame_prompt_offloads_hard_action_conditionally():
    # 母帧承接难动作:【按需·条件性】把开盖/穿脱等容易画坏的机械状态变化前移到首帧(已就绪),
    # 商品本体与原图完全一致、绝不重新设计;非强制(默认不锁普通商品)。
    from app.ai.video import scene_frame_prompt
    p = scene_frame_prompt(category="水杯", language="葡萄牙语")
    assert "可直接使用的状态" in p                      # 难动作前移 → 首帧呈现已就绪
    assert "若需要" in p and "不必刻意改动" in p          # 条件性:不需要的商品走原逻辑、不额外限制
    assert "与原图完全一致" in p and "绝不重新设计商品" in p  # 只改状态、不重画商品(防 GPT Image 重设计)
    assert "只出现一件" in p and "不悬空" in p            # 首帧身份/物理锚点:单一个体 + 有真实支撑


def test_direction_block_offloads_hard_but_frees_normal_motion():
    # 视频层:难动作交给首帧(不现场重做),但【除此之外】的普通自然动作显式保持自由、大胆、有运动幅度——
    # 直接对冲「机器人/小幅度/PPT」副作用,不是全局加约束。
    from app.ai.video import compose_prompt
    out = compose_prompt("展示商品", language="无对白")
    assert "已就绪" in out                              # 难动作已在首帧完成、视频不现场重做
    assert "大胆做" in out and "运动幅度" in out          # 普通动作显式解放(非压制)
    assert "缩手缩脚" in out                            # 明确反对为怕变形而僵化


def test_vidu_scene_frame_keeps_playful_interaction_live():
    # Vidu 母帧:只前移真正难的状态变化(开盖/穿脱),按压/旋转/捏压回弹这类把玩互动是 Vidu 强项 → 保留在视频里、不前移。
    from app.ai.vidu import scene_frame_prompt
    p = scene_frame_prompt(language="葡萄牙语")
    assert "可直接使用的状态" in p                       # 难动作前移
    assert "按压" in p and "旋转" in p and "不要" in p   # 把玩互动保留为强项、明确不前移
    assert "只出现一件" in p                             # 首帧身份锚点:单一个体


def test_scene_frame_prompt_is_video_frame0_aligned_to_action():
    # Scene Init(最上游):母帧=视频第0帧、与脚本开头衔接(给 action 就落在它的起始瞬间),不是独立成品展示图。
    from app.ai.video import scene_frame_prompt
    p = scene_frame_prompt(category="水杯", language="葡萄牙语",
                           action="她走进厨房,从台面拿起杯子,喝一口,转身切水果")
    assert "第 0 帧" in p                                  # 母帧=视频第0帧
    assert "走进厨房" in p and "最开始那一刻" in p          # 据脚本对齐起始瞬间、只画开头
    assert "成品" in p                                     # 显式:不是独立成品/展示图
    assert "拥有后的样子" not in p                          # 旧的"卖拥有后的样子"hero-shot 框定已移除
    assert "第 0 帧" in scene_frame_prompt(category="水杯")  # 不给 action 也仍是第0帧框定(默认行为)


def test_scene_init_guide_layered_and_wired(monkeypatch):
    # SCENE_INIT_GUIDE 单一真相源 + 接进看图 LLM 生成器(最上游层,与脚本开头对齐)。
    from app.services.video_continuity import SCENE_INIT_GUIDE
    assert "第 0 帧" in SCENE_INIT_GUIDE and "起始状态" in SCENE_INIT_GUIDE
    from app.services import video_wizard
    seen = {}

    def _cap(msgs, **kw):
        seen["p"] = msgs[0]["content"]
        return '[{"title":"t","storyboard":"端起杯子喝一口"}]'
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    video_wizard.generate_proposals("水壶", "运动", "保温", seconds=10, n=1)
    assert "第 0 帧" in seen["p"]                           # Scene Init 已接入向导


def test_describe_product_grounds_in_observable_not_inference(monkeypatch):
    # 识别逻辑【通用规则,非特判】:先分『直接可见』vs『经验推断』,推断属性不写成确定事实;POD 印花(可见)作首要卖点。
    # 治"白底印花随行杯被识别成不锈钢"——靠通用框架,【不靠针对具体材质词的禁止规则堆叠】。
    from PIL import Image

    from app.services import video_wizard
    seen = {}

    def _cap(msgs, **kw):
        seen["p"] = msgs[0]["content"][0]["text"]
        return '{"name":"圣母印花随行杯","audience":"a","selling_points":"杯身印有圣母图案"}'
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    video_wizard.describe_product(Image.new("RGB", (8, 8)))
    assert "直接可见" in seen["p"] and "经验推断" in seen["p"]   # 通用框架:可见 vs 推断
    assert "绝不写成确定事实" in seen["p"]                       # 推断属性别写成事实
    assert "印花" in seen["p"] and "首要卖点" in seen["p"]       # POD 印花(可见)=首要卖点
    assert "不锈钢" not in seen["p"]                            # 通用规则:不靠针对具体材质词的特判


def test_describe_product_emits_scene_profile(monkeypatch):
    # N3 foundation:Step1 同一次 Vision 调用顺带产出 Scene Profile(抽象 product_type + interaction_risks)。
    from PIL import Image

    from app.services import video_wizard

    def _cap(msgs, **kw):
        return ('{"name":"圣母印花随行杯","audience":"a","selling_points":"印花是圣母图案",'
                '"product_type":"Drinkware","interaction_risks":"complex_state, physical_contact, bogus"}')
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    prof = video_wizard.describe_product(Image.new("RGB", (8, 8)))["profile"]
    assert prof["product_type"] == "drinkware"                                  # 抽象品类、小写归一
    assert prof["interaction_risks"] == ["complex_state", "physical_contact"]   # 仅留合法风险键(去 bogus)、去重排序
    # 缺字段时默认安全(other / 无风险)→ 后续 builder 退回满自由度
    monkeypatch.setattr(video_wizard, "_chat", lambda m, **k: '{"name":"x","audience":"y","selling_points":"z"}')
    b2 = video_wizard.describe_product(Image.new("RGB", (8, 8)))["profile"]
    assert b2["product_type"] == "other" and b2["interaction_risks"] == []


def test_profile_to_capabilities_mapping():
    # N3:Scene Profile 风险 → 该启用的连续性能力集(喂 build_continuity_guide(enabled=))。
    from app.services.video_continuity import profile_to_capabilities
    assert profile_to_capabilities(["complex_state", "bogus"], multi_shot=True) == {"complex_state", "temporal_consistency"}
    assert profile_to_capabilities([], multi_shot=False) == set()               # 无风险 → 空 → 满自由度
    assert profile_to_capabilities(["physical_contact"]) == {"physical_contact"}


def test_generate_proposals_consumes_scene_profile(monkeypatch):
    # N3 consumption:有 profile → 按风险只启用对应连续性能力(其余不写);无 profile → 全部(历史行为,安全默认)。
    from app.services import video_wizard
    seen = {}

    def _cap(msgs, **kw):
        seen["p"] = msgs[0]["content"]
        return '[{"title":"t","storyboard":"端起杯子喝一口"}]'
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    # 只标 object_identity 风险 → prompt 含对象身份,不含物理接触/时序/复杂状态;natural_motion 底线仍在
    video_wizard.generate_proposals("杯子", "家居", "保温", seconds=10, n=1,
                                    profile={"product_type": "drinkware", "interaction_risks": ["object_identity"]})
    p = seen["p"]
    assert "对象身份" in p
    assert "物理接触" not in p and "时序一致" not in p and "复杂状态变化" not in p
    assert "保持自由" in p
    # 无 profile → 含全部能力(= 历史行为,安全默认)
    video_wizard.generate_proposals("杯子", "家居", "保温", seconds=10, n=1)
    assert all(x in seen["p"] for x in ("对象身份", "物理接触", "时序一致", "复杂状态变化"))


def test_continuity_guide_layered_dynamic_and_protects_motion():
    # 分层连续性策略(单一真相源):4 层都在 + 按风险动态(只对真实风险写)+ 显式保护普通动作自由(不规则堆叠)。
    from app.services.video_continuity import CONTINUITY_GUIDE, CONTINUITY_GUIDE_VIDU
    # 四层齐全(L0 状态前移 / L1 对象身份 / L2 物理接触 / L3 时序一致)
    for layer in ("可直接使用", "对象身份", "物理接触", "时序一致"):
        assert layer in CONTINUITY_GUIDE
    # 按风险动态 + 默认满自由度(铁律:没风险别写、不是动作限制、普通动作放开)
    assert "没有就别写" in CONTINUITY_GUIDE
    assert "不是动作限制" in CONTINUITY_GUIDE and "保持自由" in CONTINUITY_GUIDE
    # Vidu 变体:同样四层,但保留按压/旋转把玩强项、不前移
    for layer in ("对象身份", "物理接触", "时序一致"):
        assert layer in CONTINUITY_GUIDE_VIDU
    assert "按压" in CONTINUITY_GUIDE_VIDU and "旋转" in CONTINUITY_GUIDE_VIDU and "不前移" in CONTINUITY_GUIDE_VIDU


def test_capability_registry_and_builder():
    # N1/N2:连续性【能力层】——注册表 + 组装器,能力可按模型渲染、按 enabled 选择性启用(N3 接口已就位)。
    from app.services.video_continuity import (
        CAPABILITIES, CONTINUITY_GUIDE, CONTINUITY_GUIDE_VIDU, build_continuity_guide,
    )
    # N1:六族能力齐全(scene_init / complex_state / object_identity / physical_contact / temporal / natural_motion)
    for key in ("scene_initialization", "complex_state", "object_identity",
                "physical_contact", "temporal_consistency", "natural_motion"):
        assert key in CAPABILITIES
    # N2:默认含全部风险门控能力 + 受保护的 natural_motion 底线
    g = build_continuity_guide("cogvideox")
    assert all(x in g for x in ("复杂状态变化", "对象身份", "物理接触", "时序一致"))
    assert "保持自由" in g
    # 模型变体:Vidu 渲染保留把玩强项、不前移
    gv = build_continuity_guide("vidu")
    assert "按压" in gv and "旋转" in gv and "不前移" in gv
    # N3 接口:enabled 子集 → 只渲染该启用的能力;natural_motion 受保护、永远附上
    only_identity = build_continuity_guide("cogvideox", enabled={"object_identity"})
    assert "对象身份" in only_identity
    assert "物理接触" not in only_identity and "时序一致" not in only_identity
    assert "保持自由" in only_identity
    # 派生常量 == builder 输出(各 builder import 的名字未变 → 行为保持)
    assert CONTINUITY_GUIDE == build_continuity_guide("cogvideox")
    assert CONTINUITY_GUIDE_VIDU == build_continuity_guide("vidu")


def test_wizard_prompt_wires_continuity_guide(monkeypatch):
    # 接线验证:看图 LLM 生成器把统一的连续性指引接进了 prompt(不是各处散落 if)。
    from app.services import video_wizard
    seen = {}

    def _cap(msgs, **kw):
        seen["prompt"] = msgs[0]["content"]
        return '[{"title":"t","storyboard":"端起杯子喝一口"}]'
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    video_wizard.generate_proposals("水壶", "运动", "保温", seconds=10, n=1)
    assert "连续性自检" in seen["prompt"] and "对象身份" in seen["prompt"]   # 统一指引已接入


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
        def image_to_video(self, images, prompt, *, audio=None, **kwargs):   # N4 统一契约:原生音效走 audio=
            calls["with_audio"].append(audio)
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


# ---------- 内容策划层:中性动作链场景兜底(T3-8:写死故事库 STORY_TEMPLATES 已删,仅留中性兜底)----------
def test_default_scenes_neutral_chain():
    from app.services.video_templates import default_scenes
    # 后台默认中性场景(自动融合 / 向导兜底用):两拍非空且不同,适配任意品类(不写死品类故事)
    s1, s2 = default_scenes("通用")
    assert s1 and s2 and s1 != s2
    assert len(default_scenes("通用", 3)) == 3       # 可向 3 拍扩展(双分镜 5+5+5 用)
    # 已删的写死故事库不应再存在(防有人复活「出门/咖啡店」单一文化)
    import app.services.video_templates as vt
    assert not hasattr(vt, "STORY_TEMPLATES") and not hasattr(vt, "templates_for")


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


def test_scene_frame_total_failure_falls_back_to_kenburns_and_refunds(client, auth_headers, monkeypatch, png):
    # 母帧(场景首帧优化)失败 → 退回原始商品图作首帧、喂给视频模型(CogVideoX 的 img2video 让它动),
    # 【不出 GIF、不退点】(老大:GIF 根本不能用;首帧优化=CogVideoX 自己做)。失败仍记 warning 诚实告知。
    from app.ai import openai_image
    from app.config import settings

    def _boom(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        raise RuntimeError("Error code: 503 - {'message': 'No available compatible accounts'}")
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "video_mufra_attempts", 2)
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _boom)
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "p", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job                       # 母帧失败不阻断,仍出片(测试 local provider→GIF)
    assert job["result"]["video_url"]                         # 视频照常交付(原图首帧喂 provider)
    warns = job["result"].get("warnings") or []
    assert any("场景母帧" in w for w in warns)                # 诚实告知母帧失败、退回原图
    assert not any("运镜" in w for w in warns)                # 不再有"运镜片"兜底(GIF 已删)
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 3  # 不退点(扣3)


def test_mufra_with_backoff_uses_adaptive_limiter(monkeypatch):
    # 母帧走全局【自适应限流器】:_mufra_with_backoff 执行 do_edit 期间【占住一个 in_flight 名额】(排队/限流),
    # 成功后 report 释放。预算从拿到位才起算(等位不计预算)。
    from app.ai import openai_image
    from app import tasks
    held = {}

    def _do():
        held["in_flight_during"] = openai_image._API_GATE.snapshot()[1]   # do_edit 执行瞬间在飞数
        return "ok"
    base = openai_image._API_GATE.snapshot()[1]
    out = tasks._mufra_with_backoff(_do)
    assert out == "ok"
    assert held["in_flight_during"] == base + 1     # 执行时占住一个在飞名额(排队/限流)
    assert openai_image._API_GATE.snapshot()[1] == base   # 结束后 report 释放


# ---------- 自适应并发限流器单测 ----------
def test_adaptive_limiter_ramps_up_on_success_capped():
    from app.ai.openai_image import _AdaptiveLimiter
    lim = _AdaptiveLimiter(start=1, max_limit=3)
    for _ in range(6):
        lim.acquire(); lim.report(True)
    assert lim.snapshot()[0] == 3                    # 成功不断爬升、封顶 max=3


def test_adaptive_limiter_ramps_down_on_capacity_full_floored():
    from app.ai.openai_image import _AdaptiveLimiter
    lim = _AdaptiveLimiter(start=4, max_limit=8)
    for _ in range(10):
        lim.acquire(); lim.report(False, RuntimeError("Error code: 503 - No available compatible accounts"))
    assert lim.snapshot()[0] == 1                    # 撞 503 不断回退、封底 1


def test_adaptive_limiter_non_capacity_error_keeps_limit():
    from app.ai.openai_image import _AdaptiveLimiter
    lim = _AdaptiveLimiter(start=2, max_limit=6)
    lim.acquire(); lim.report(False, RuntimeError("Request timed out"))   # 超时=非容量问题
    assert lim.snapshot()[0] == 2                    # 非容量错 → limit 不变


def test_adaptive_limiter_blocks_when_full_then_releases():
    import threading
    import time as _t

    from app.ai.openai_image import _AdaptiveLimiter
    lim = _AdaptiveLimiter(start=1, max_limit=1)
    lim.acquire()                                    # 占满(limit=1, in_flight=1)
    got = {"v": False}

    def _second():
        lim.acquire(); got["v"] = True               # 应阻塞,直到第一个 report 释放
    t = threading.Thread(target=_second); t.start()
    _t.sleep(0.2)
    assert got["v"] is False                          # 满了 → 第二个阻塞在 acquire
    lim.report(True)                                  # 释放一个在飞名额
    t.join(timeout=2)
    assert got["v"] is True                           # 第二个被放行
    lim.report(True)


def test_adaptive_limiter_context_manager_protocol():
    # 回归:_API_GATE 由 Semaphore 改成 _AdaptiveLimiter 后,service 层(向导/简报/旁白/侵权/vidu)仍用 `with _API_GATE:`。
    # 必须支持上下文管理器协议(否则 'object does not support the context manager protocol')。语义 == run()。
    from app.ai.openai_image import _AdaptiveLimiter
    lim = _AdaptiveLimiter(start=2, max_limit=4)
    with lim:                                          # __enter__ = acquire(占位)
        assert lim.snapshot()[1] == 1                 # 块内 in_flight=1
    assert lim.snapshot() == (3, 0)                   # 正常退出 = report(成功) → limit 爬升、in_flight 归零
    # 块内抛【容量错】→ report(失败, exc) → limit 回退,且异常照常上抛(不吞)
    lim2 = _AdaptiveLimiter(start=3, max_limit=6)
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        with lim2:
            raise RuntimeError("Error code: 503 - No available compatible accounts")
    assert lim2.snapshot() == (2, 0)                  # 容量错 → limit 由 3 回退到 2、in_flight 归零


def test_scene_frame_retries_then_succeeds(client, auth_headers, monkeypatch, png):
    # 母帧退避重试:首次瞬时错("网关未返回图像数据")→ 退避后重试 → 第二次成功 → 不降级、不记 warning。
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    calls = {"n": 0}

    def _flaky_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("图片网关未返回图像数据")   # 首次瞬时错
        return _Img.new("RGB", (64, 96), (10, 20, 30))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)   # 退避不真等,测试快
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "video_mufra_attempts", 2)
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _flaky_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "单镜脚本", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert calls["n"] == 2                               # 退避后重试了一次(共 2 次尝试)
    assert not any("场景母帧" in w for w in (job["result"].get("warnings") or []))  # 重试成功 → 不降级


def test_scene_frame_503_no_accounts_retries_then_succeeds(client, auth_headers, monkeypatch, png):
    # 中转站 503「无可用账号」是瞬时拥塞(实证根因)→ 退避后重试 → 成功(治"一撞 503 就降级")。
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    calls = {"n": 0}

    def _flaky_edit(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Error code: 503 - {'error': {'message': 'No available compatible accounts'}}")
        return _Img.new("RGB", (64, 96), (10, 20, 30))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "video_mufra_attempts", 3)
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _flaky_edit)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "单镜脚本", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert calls["n"] == 2                               # 503 退避重试后成功
    assert not any("场景母帧" in w for w in (job["result"].get("warnings") or []))


def test_scene_frame_permanent_error_fails_fast_no_retry(client, auth_headers, monkeypatch, png):
    # 永久错(鉴权/余额)→ 立即放弃、【不空等退避重试】(只调 1 次)→ 降级 + 记 warning。
    from app.ai import openai_image
    from app.config import settings
    calls = {"n": 0}
    slept = {"n": 0}

    def _boom(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        calls["n"] += 1
        raise RuntimeError("Error code: 401 - invalid_api_key")
    monkeypatch.setattr("time.sleep", lambda *a, **k: slept.__setitem__("n", slept["n"] + 1))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "video_mufra_attempts", 5)
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _boom)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "单镜脚本", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert calls["n"] == 1 and slept["n"] == 0          # 永久错:不重试、不退避空等
    # 母帧失败 → 退回原图喂 provider(不出 GIF),warning 记"场景母帧失败、退回原图"
    assert any("场景母帧" in w for w in (job["result"].get("warnings") or []))


def test_punch_up_failure_is_best_effort(client, auth_headers, monkeypatch, png):
    # 后期节奏快切(punch_up)失败 → 不阻断:仍交付原片(status done + video_url),只记 warning、不退点。
    # 治"一个后期特效 bug 让整单 error+退点、用户白丢已出好的成片"。
    from app.ai import video as video_mod
    from app.config import settings
    from app.services import video_edit

    class _FakeMp4:
        name = "cogvideox"

        def image_to_video(self, images, prompt, *, seconds=None, **kwargs):
            return {"bytes": b"\x00\x00\x00\x18ftypmp42FAKEMP4", "ext": "mp4", "meta": {"engine": "cogvideox"}}
    monkeypatch.setattr(settings, "video_punchup", True)
    monkeypatch.setattr(video_mod, "get_video_provider", lambda: _FakeMp4())
    monkeypatch.setattr(video_edit, "punch_up", lambda b: (_ for _ in ()).throw(RuntimeError("ffmpeg 崩了")))
    bal0 = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "镜头", "seconds": "10", "scene_frame": "false", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job                       # 后期失败不阻断,仍出片
    assert job["result"]["video_url"]                         # 原片照常交付
    assert any("节奏快切" in w for w in (job["result"].get("warnings") or []))
    assert client.get("/api/billing/balance", headers=auth_headers).json()["credits"] == bal0 - 3  # 不额外退点(正常扣 video=3)


def test_scene_frame_exhausts_attempts_then_degrades(client, auth_headers, monkeypatch, png):
    # 瞬时错重试用尽仍失败 → 降级原图 + 记 warning;尝试次数 = video_mufra_attempts。
    from app.ai import openai_image
    from app.config import settings
    calls = {"n": 0}

    def _boom(self, image, prompt, mask=None, size="auto", background="auto", **kwargs):
        calls["n"] += 1
        raise RuntimeError("Error code: 502 bad gateway")
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "video_mufra_attempts", 2)
    monkeypatch.setattr(openai_image.OpenAIImageClient, "edit", _boom)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "单镜脚本", "seconds": "10", "scene_frame": "true", "aspect": "portrait"},
                    files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert calls["n"] == 2                               # 单镜母帧 1 张 × 2 次尝试(退避重试用尽)
    # 母帧失败 → 退回原图喂 provider(不出 GIF),warning 记"场景母帧失败、退回原图"
    assert any("场景母帧" in w for w in (job["result"].get("warnings") or []))


def test_wizard_proposals_two_shot_adds_scenes(monkeypatch):
    # 模型给了 scene1/scene2/story → 透传进方案(喂 per-shot 母帧)
    from app.services import video_wizard
    fake = ('[{"title":"出门","story":"出门赴约","model":"无","environment":"街头",'
            '"scene1":"卧室镜子前","shot1":"【0-5秒】看手机",'
            '"scene2":"玄关门口","shot2":"【0-5秒】拿钥匙推门",'
            '"scene3":"城市街头","shot3":"【0-5秒】走在街上","storyboard":"合并"}]')
    monkeypatch.setattr(video_wizard, "_chat", lambda msgs, **kw: fake)
    out = video_wizard.generate_proposals("T恤", "年轻人", "潮", seconds=15, n=1, category="T恤")
    assert out[0]["scene1"] == "卧室镜子前" and out[0]["scene2"] == "玄关门口" and out[0]["scene3"] == "城市街头"
    assert out[0]["story"] == "出门赴约"


def test_wizard_proposals_two_shot_scene_fallback(monkeypatch):
    # 模型没给 scene → 退到中性【动作链】通用场景兜底(不写死 OOTD),保证三镜非空且递进(per-shot 前提)
    from app.services import video_wizard
    from app.services.video_templates import default_scenes
    monkeypatch.setattr(video_wizard, "_chat",
                        lambda msgs, **kw: '[{"title":"t","shot1":"a","shot2":"b","storyboard":"s"}]')
    out = video_wizard.generate_proposals("T恤", "", "", seconds=15, n=1, category="T恤")
    d = default_scenes("T恤", 3)
    assert out[0]["scene1"] == d[0] and out[0]["scene2"] == d[1] and out[0]["scene3"] == d[2]
    assert len({d[0], d[1], d[2]}) == 3                   # 三拍场景各不相同(递进)


def test_wizard_proposals_prompt_pushes_diversity_and_temperature(monkeypatch):
    # 治同质化:方案 prompt 显式要求"跨不同生活情境 + 创意",且方案生成用更高 temperature(增多样性、降同质)
    from app.services import video_wizard
    seen = {}

    def _cap(msgs, **kw):
        seen["prompt"] = msgs[0]["content"]
        seen["temperature"] = kw.get("temperature")
        return '[{"title":"t","shot1":"a","shot2":"b","shot3":"c"}]'
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    video_wizard.generate_proposals("杯子", "家居", "保温", seconds=15, n=3)
    assert "生活情境" in seen["prompt"]                   # 跨情境多样化指令
    assert "出门" in seen["prompt"]                        # 明确"别都是出门类"
    assert "关联性" in seen["prompt"]                      # 差异是【方案之间】,15s 内部三拍连续性不能断
    assert seen["temperature"] and seen["temperature"] >= 0.8   # 方案用更高温度增多样性


def test_wizard_proposals_5s10s_creative_storyboard(monkeypatch):
    # 5s/10s 也享受跨方案多样化 + 更高 temperature;且是单镜【一条连续视频】(storyboard 一段连贯、不分镜)
    from app.services import video_wizard
    seen = {}

    def _cap(msgs, **kw):
        seen["prompt"] = msgs[0]["content"]
        seen["temperature"] = kw.get("temperature")
        return '[{"title":"t","storyboard":"真人端起杯子喝一口,放松靠回去"}]'
    monkeypatch.setattr(video_wizard, "_chat", _cap)
    for secs in (5, 10):
        seen.clear()
        out = video_wizard.generate_proposals("杯子", "家居", "保温", seconds=secs, n=3)
        assert "生活情境" in seen["prompt"]                # 跨方案多样化对 5/10s 同样生效
        assert "单镜" in seen["prompt"] and "别拆分镜" in seen["prompt"]   # 框定单镜连续视频(非分镜)
        assert seen["temperature"] and seen["temperature"] >= 0.8
        assert "shot1" not in out[0]                       # 5/10s 输出契约:只有 storyboard,不暴露 shot
        assert "分镜" not in out[0]["storyboard"]          # 单镜连续:storyboard 无分镜标签
