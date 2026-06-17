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


def test_ai_generate_requires_auth(client):
    r = client.post("/api/video/ai-generate", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


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
    assert 5 in body["durations"] and 10 in body["durations"]
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


def test_aspect_size_by_resolution():
    # 画幅 × 分辨率 → 尺寸(短边=分辨率档,长边按比例)
    from app.ai.video import aspect_size
    assert aspect_size("portrait", "1080p") == "1080x1920"
    assert aspect_size("landscape", "1080p") == "1920x1080"
    assert aspect_size("square", "720p") == "720x720"
    assert aspect_size("portrait", "4k") == "2160x3840"


def test_compose_prompt_pro_layers():
    # 专业化拼装:镜头脚本 + 类目动作(马克杯)+ 巴西风格(葡语)+ 语言 + 防拉伸 + 负向
    from app.ai.video import compose_prompt
    out = compose_prompt("素人开箱,手持拍摄", language="葡萄牙语", category="马克杯")
    assert "素人开箱,手持拍摄" in out
    assert "马克杯" in out and "旋转" in out          # 类目专属动作被追加
    assert "巴西" in out                             # 葡语 → 巴西本地风格
    assert "葡萄牙语" in out                          # 语言指令
    assert "拉伸" in out                             # 防拉伸/一致性
    assert "避免" in out                             # 负向词被追加
    # 地区风格跟着语言智能变:英语 → 欧美(不是巴西)
    out2 = compose_prompt("镜头推近", language="英语", category="通用")
    assert "巴西" not in out2 and "欧美" in out2 and "英语" in out2
    # 「无对白」→ 不加地区风格、不加配音句
    out3 = compose_prompt("镜头推近", language="无对白")
    assert "巴西" not in out3 and "欧美" not in out3 and "配音使用" not in out3 and out3.strip()


def test_compose_prompt_has_physics_constraints():
    # 物理连贯约束:无论什么品类(含未知/通用)都通用追加,治"瓶盖凭空而启"这类违背物理的画面。
    # 关键是"通用",不是给某个品类写死 —— 用多个品类都断言它在。
    from app.ai.video import compose_prompt
    for cat in ("通用", "马克杯", "手机壳", "不存在的品类"):
        out = compose_prompt("展示商品", language="无对白", category=cat)
        assert "物理" in out                       # 正/负向都强调遵循真实物理
        assert "自行开合" in out                    # 负向:部件无人触碰不得自行开合
        assert "凭空" in out and "瞬移" in out       # 负向:物体不得凭空出现/瞬移
        assert "保持不变" in out                    # 正向后缀:商品形态/开合状态保持不变


def test_ai_generate_full_params(client, auth_headers):
    # 描述 + 语言 + 类目 + 场景首帧(无 key 自动跳过)+ 画幅/分辨率全走通(本地兜底 GIF)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "达人出镜讲解卖点", "language": "英语",
                          "category": "T恤", "scene_frame": "true", "seconds": "5",
                          "aspect": "portrait34", "resolution": "720p"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job   # 无 openai key → 场景首帧优雅跳过,仍出兜底 GIF
    assert job["result"]["video_url"].endswith(".gif")


def test_ai_generate_scene_frame_with_gptimage(client, auth_headers, monkeypatch, png):
    # 配了 key 时「场景首帧」走 gpt-image 编辑首帧,再生视频(本地兜底 GIF);确认流程不崩。
    from PIL import Image as _Img

    from app.ai import openai_image
    from app.config import settings
    called = {"edit": 0}

    def _fake_edit(self, image, prompt, mask=None, size="auto", background="auto"):
        called["edit"] += 1
        assert "保持商品本身" in prompt   # 用的是场景首帧指令
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
    # 选了字幕但离线(provider=local → GIF):配音/字幕因 ext!=mp4 跳过,仍出兜底 GIF、作业 done
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "达人带货", "subtitle": "true", "aspect": "portrait"},
                    files={"file": ("x.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["result"]["video_url"].endswith(".gif")   # 本地兜底,配音/字幕跳过,作业不受影响
