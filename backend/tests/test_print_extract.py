"""印花提取测试。全本地:cloth-seg 分衣服 + 去主导布料色;无衣服→整图去底。"""
from __future__ import annotations

from PIL import Image, ImageDraw

from app.config import settings
from app.services.design_extract import extract_design, extract_on_fabric
from app.services.print_extract import _white_bg_to_transparent, extract_print_design


def test_white_key_keeps_design_removes_edge_bg():
    """白底→透明:去掉连通边缘的底色,**完整保留设计**(尤其满铺/装饰类不能被删光)。"""
    # 满铺式:中央一大片彩色设计 + 四周白边(类比枕套花纹)
    img = Image.new("RGB", (240, 240), (252, 252, 252))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 200, 200], fill=(60, 160, 90))      # 大片绿色设计
    d.ellipse([90, 90, 150, 150], fill=(250, 250, 250))      # 设计内部的白(被绿包围,不连边缘)
    out = _white_bg_to_transparent(img)
    a = out.getchannel("A")
    assert a.getpixel((3, 3)) == 0          # 边缘白 → 透明
    assert a.getpixel((120, 60)) == 255     # 绿色设计 → 保留不透明
    assert a.getpixel((120, 120)) == 255    # 设计内部白(不连边缘)→ 保留(不被删)
    # 保留的设计应占相当比例(不能像 rembg 那样删光)
    op = sum(a.histogram()[240:256]) / float(240 * 240)
    assert op > 0.3, f"设计被删太多,仅剩 {op:.0%}"


def _design_on_white(bg=(255, 255, 255)) -> Image.Image:
    img = Image.new("RGB", (200, 200), bg)
    ImageDraw.Draw(img).rectangle([70, 70, 130, 130], fill=(220, 40, 40))  # 居中红色图案
    return img


# ---------- AI 重绘 → 抠图去背景 → 透明(端到端) ----------

def test_ai_extract_path_outputs_transparent(monkeypatch):
    """端到端(mock gpt):AI 引擎拿到『近白底重绘图』后,最终 design 必须是真透明,
    而非白底——这正是用户报的『下载透明仍是白底』要根治的路径。"""
    # 模拟 gpt-image edit 的返回:主体画在近白底上(不透明 RGBA)
    fake = Image.new("RGB", (400, 400), (238, 236, 232))
    ImageDraw.Draw(fake).ellipse([120, 120, 280, 280], fill=(170, 60, 40))

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def edit(self, image, prompt, **k):
            return fake.convert("RGBA")

    # 模拟 Real-ESRGAN:超分时 convert("RGB") 会丢 alpha——若抠透明在放大之前做,
    # 透明会被抹掉。这个假上采样器复现该失败模式,确保流水线顺序正确(抠透明必须最后做)。
    class _AlphaDroppingUpscaler:
        name = "fake"

        def upscale(self, image, scale=1.0):
            rgb = image.convert("RGB")  # 丢 alpha(同真 Real-ESRGAN)
            tw, th = max(1, int(image.width * scale)), max(1, int(image.height * scale))
            return rgb.resize((tw, th), Image.LANCZOS)

    monkeypatch.setattr("app.ai.openai_image.OpenAIImageClient", _FakeClient)
    monkeypatch.setattr("app.services.print_extract.get_upscale_provider", lambda: _AlphaDroppingUpscaler())
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "print_extract_ai", True)

    design, meta = extract_print_design(Image.new("RGB", (300, 300), (200, 200, 200)))
    assert meta["engine"] == "ai"
    assert design.mode == "RGBA"
    a = design.getchannel("A")
    lo, hi = a.getextrema()
    assert lo == 0 and hi == 255            # 确有透明像素(背景)+ 不透明像素(主体)
    assert a.getpixel((3, 3)) == 0          # 角落背景 → 透明
    assert a.getpixel((a.size[0] // 2, a.size[1] // 2)) == 255  # 主体 → 不透明


# ---------- 本地去布料(纯函数,离线) ----------

def test_extract_on_fabric_removes_white_keeps_design():
    out = extract_on_fabric(_design_on_white())
    assert out.mode == "RGBA"
    a = out.getchannel("A")
    w, h = out.size
    # 四角(白布料)应透明,中心(图案)应不透明
    assert a.getpixel((0, 0)) <= 16
    assert a.getpixel((w // 2, h // 2)) >= 200


def test_extract_on_fabric_handles_dark_fabric():
    # 关键:不写死去白色,深色(黑)布料也要能去掉、保留浅色图案
    img = Image.new("RGB", (200, 200), (18, 18, 18))  # 黑布料
    ImageDraw.Draw(img).rectangle([70, 70, 130, 130], fill=(240, 240, 240))  # 白图案
    out = extract_on_fabric(img)
    a = out.getchannel("A")
    w, h = out.size
    assert a.getpixel((0, 0)) <= 16          # 黑布料角落 → 透明
    assert a.getpixel((w // 2, h // 2)) >= 200  # 白图案中心 → 保留


def test_extract_on_fabric_keeps_interior_color():
    out = extract_on_fabric(_design_on_white())
    # 红色图案像素应保留(非全透明)
    assert out.getchannel("A").getbbox() is not None


# ---------- 编排(设计图:cloth-seg 找不到衣服 → 整图去底) ----------

def test_extract_design_on_design_image():
    design, meta = extract_design(_design_on_white())
    assert design.mode == "RGBA"
    assert "method" in meta
    assert design.getchannel("A").getbbox() is not None  # 抠出了内容
    # 自动裁剪到内容:裁完后内容应贴满四边(bbox == 整图;不再用绝对尺寸,因为有超分会放大)
    assert design.getchannel("A").getbbox() == (0, 0, design.size[0], design.size[1])


def test_extract_design_rejects_oversized(monkeypatch):
    import app.services.design_extract as de
    monkeypatch.setattr(de, "_MAX_PX", 100)  # 临时把上限调到很小
    try:
        de.extract_design(_design_on_white())
        assert False, "超大图应抛 ValueError"
    except ValueError:
        pass


# ---------- 接口 ----------

def test_print_extract_endpoint(client, auth_headers, png):
    resp = client.post(
        "/api/print-extract", headers=auth_headers,
        files={"file": ("d.png", png(shape="rect"), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "image_url" in body and "method" in body


def test_print_extract_requires_auth(client, png):
    resp = client.post("/api/print-extract", files={"file": ("d.png", png(), "image/png")})
    assert resp.status_code == 401


def test_print_extract_charges_points(client, auth_headers, png):
    before = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    client.post("/api/print-extract", headers=auth_headers,
                files={"file": ("d.png", png(shape="rect"), "image/png")})
    after = client.get("/api/billing/balance", headers=auth_headers).json()["credits"]
    assert after == before - 2  # process 扣 2 点


# ---------- 编排:AI 重绘默认 + 本地兜底 ----------

def test_orchestrator_uses_local_without_key():
    # 离线(conftest 已清空 key)→ 自动走本地保真算法
    from app.services import print_extract as pe
    _design, meta = pe.extract_print_design(_design_on_white())
    assert meta["engine"] == "local"
    assert meta["method"] != "ai_flatten"


def test_orchestrator_prefers_ai_when_available(monkeypatch):
    from app.services import print_extract as pe
    monkeypatch.setattr(pe.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(pe.settings, "print_extract_ai", True)
    fake = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
    monkeypatch.setattr(pe, "_extract_ai", lambda img: (fake, {"method": "ai_flatten", "engine": "ai", "size": [64, 64]}))
    out, meta = pe.extract_print_design(_design_on_white())
    assert meta["engine"] == "ai" and meta["method"] == "ai_flatten"
    assert out is fake


def test_orchestrator_falls_back_on_ai_failure(monkeypatch):
    from app.services import print_extract as pe
    monkeypatch.setattr(pe.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(pe.settings, "print_extract_ai", True)

    def _boom(img):
        raise RuntimeError("网关 502")

    monkeypatch.setattr(pe, "_extract_ai", _boom)
    out, meta = pe.extract_print_design(_design_on_white())
    assert meta["engine"] == "local"  # AI 失败 → 自动降级本地
    assert out.mode == "RGBA"


def test_print_extract_ai_goes_background(client, auth_headers, png, monkeypatch):
    """有 key → AI 路径改 Celery 作业:端点立即返回 pending+job_id,作业完成后带结果(image_url/white_url)。"""
    import app.routers.print_extract as pr
    import app.tasks as tasks
    monkeypatch.setattr(pr.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(pr.settings, "print_extract_ai", True)
    fake = Image.new("RGBA", (32, 32), (1, 2, 3, 255))
    # 提取在 worker 任务里发生(闭包不能跨进程),故 fake 打在 app.tasks 上而非 router 上。
    monkeypatch.setattr(tasks, "extract_print_design",
                        lambda src: (fake, {"method": "ai_flatten", "engine": "ai", "size": [32, 32]}))
    resp = client.post("/api/print-extract", headers=auth_headers,
                       files={"file": ("d.png", png(), "image/png")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") == "pending" and "job_id" in body
    # Celery eager(conftest 设)→ .delay() 同步跑完,作业应已完成
    job = client.get(f"/api/jobs/{body['job_id']}", headers=auth_headers).json()
    assert job["status"] == "done", job
    assert job["result"]["engine"] == "ai"
    assert "image_url" in job["result"] and "white_url" in job["result"]


def test_orchestrator_ai_flag_off_stays_local(monkeypatch):
    from app.services import print_extract as pe
    monkeypatch.setattr(pe.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(pe.settings, "print_extract_ai", False)  # 显式关 AI

    def _should_not_call(img):
        raise AssertionError("关了 AI 还调用了 _extract_ai")

    monkeypatch.setattr(pe, "_extract_ai", _should_not_call)
    _out, meta = pe.extract_print_design(_design_on_white())
    assert meta["engine"] == "local"
