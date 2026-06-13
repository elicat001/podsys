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
    # 分辨率 + 语言也返回
    assert "1080p" in body["resolutions"] and "4k" in body["resolutions"]
    assert "葡萄牙语" in body["languages"]


def test_aspect_size_by_resolution():
    # 画幅 × 分辨率 → 尺寸(短边=分辨率档,长边按比例)
    from app.ai.video import aspect_size
    assert aspect_size("portrait", "1080p") == "1080x1920"
    assert aspect_size("landscape", "1080p") == "1920x1080"
    assert aspect_size("square", "720p") == "720x720"
    assert aspect_size("portrait", "4k") == "2160x3840"


def test_compose_prompt_motion_title_lang_quality():
    # 提示词:视频描述(用户填/改)+ 商品标题 + 语言 + 一致性/防拉伸指令
    from app.ai.video import compose_prompt
    out = compose_prompt("素人开箱,手持拍摄", title="复古印花连衣裙", language="葡萄牙语")
    assert "素人开箱,手持拍摄" in out
    assert "复古印花连衣裙" in out
    assert "葡萄牙语" in out                    # 语言指令
    assert "拉伸" in out                        # 防拉伸指令被统一追加
    # 「无对白」→ 不加配音语言句;空描述也安全(只剩标题/质感)
    out2 = compose_prompt("", language="无对白")
    assert "配音使用" not in out2 and out2.strip()


def test_ai_generate_full_params(client, auth_headers):
    # 描述 + 标题 + 语言 + 新画幅(3:4)+ 分辨率(720p)全走通(本地兜底 GIF)
    r = client.post("/api/video/ai-generate", headers=auth_headers,
                    data={"prompt": "达人出镜讲解卖点", "title": "复古印花连衣裙", "language": "英语",
                          "aspect": "portrait34", "resolution": "720p"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["result"]["video_url"].endswith(".gif")


# ---------- workflow step ----------
def test_video_workflow_step(client, auth_headers):
    r = client.post("/api/workflows/run-custom", headers=auth_headers,
                    data={"steps": "extract,mockup,video", "params": "{}"},
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    j = client.get(f"/api/jobs/{jid}", headers=auth_headers).json()
    assert j["status"] == "done", j
    assert any(u.endswith("showcase.gif") for u in j["result"]["outputs"])
    assert j["result"]["meta"].get("video")
