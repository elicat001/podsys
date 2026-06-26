"""FastAPI app — MVP main line: upload → extract → mockup → export."""
from __future__ import annotations

import io
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import storage
from .auth import current_user
from .config import settings
from .db import SessionLocal, get_db, init_db
from .models_db import User
from .routers import assets as assets_router
from .routers import auth as auth_router
from .routers import billing as billing_router
from .routers import collect_tasks as collect_tasks_router
from .routers import design_tools as design_tools_router
from .routers import export as export_router
from .routers import extension as extension_router
from .routers import image_tools as image_tools_router
from .routers import ip_guard as ip_guard_router
from .routers import jobs as jobs_router
from .routers import matting as matting_router
from .routers import me as me_router
from .routers import mockup as mockup_router
from .routers import print_extract as print_extract_router
from .routers import product_admin as product_admin_router
from .routers import products as products_router
from .routers import shops as shops_router
from .routers import space as space_router
from .routers import studio_tools as studio_tools_router
from .routers import team as team_router
from .routers import templates as templates_router
from .routers import vectorize as vectorize_router
from .routers import video as video_router
from .routers import video_cases as video_cases_router
from .services.billing import InsufficientCredits, charge, charge_for, cost_of, refund
from .services.collectors import detect_platform, upgrade_to_hires
from .services.export import export_production
from .services.extract import extract_print
from .services.generate import image_to_image, refine_generate_prompt, text_to_image
from .services.jobs import create_job, run_job
from .services.library import save_as_asset
from .services.mockup import list_templates, render_mockup
from .tasks import run_tool
from .web_utils import submit_celery

app = FastAPI(title="PODStudio API", version="0.3.0")
settings.ensure_dirs()
init_db()

# 仅放行「浏览器扩展」跨源访问(采集助手 background SW 回传)。
# 限定 chrome-extension:// 来源 → 不对任意网站开放;且所有接口仍需 Bearer 鉴权,无 cookie 凭据,风险低。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """前端是单文件静态页且频繁迭代;禁掉 HTML 文档缓存,避免浏览器服旧页
    (这是"改完代码必须硬刷新才生效"的根因)。只作用于 HTML,不动 /files 图片与 JSON API。"""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.middleware("http")
async def _security_headers(request, call_next):
    """安全响应头(应用层兜底,与 nginx 双保险)。HSTS 仅在 https(经 nginx 终止 TLS)下加,
    避免本地 http 开发被锁死。CSP 不在此强加(SPA 内联/第三方较多,易误伤),交由后续按需细化。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-XSS-Protection", "0")  # 现代浏览器已弃用旧式过滤器,显式关闭避免其引入的漏洞
    if request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(auth_router.router)
app.include_router(assets_router.router)
app.include_router(products_router.router)
app.include_router(jobs_router.router)
app.include_router(billing_router.router)
app.include_router(design_tools_router.router)
app.include_router(image_tools_router.router)
app.include_router(studio_tools_router.router)
app.include_router(ip_guard_router.router)
app.include_router(vectorize_router.router)
app.include_router(collect_tasks_router.router)
app.include_router(shops_router.router)
app.include_router(me_router.router)
app.include_router(video_router.router)
app.include_router(product_admin_router.router)
app.include_router(space_router.router)
app.include_router(video_cases_router.router)
app.include_router(templates_router.router)
app.include_router(print_extract_router.router)
app.include_router(export_router.router)
app.include_router(mockup_router.router)
app.include_router(team_router.router)
app.include_router(matting_router.router)
app.include_router(extension_router.router)

# 前端 = Vue 单页应用的构建产物(frontend-vue/dist)。旧的静态 frontend/ 已废弃删除。
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend-vue" / "dist"


class _SPAStaticFiles(StaticFiles):
    """静态文件服务 + SPA history 回退:请求路径不是真实文件时(深链如 /tools/extract
    刷新)返回 index.html,交给 Vue Router 客户端路由,而不是 404。"""

    async def get_response(self, path: str, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # 仅对前端路由回退 index.html;未匹配的 /api /files 仍返回真 404(保留 API 语义)。
            # 用 scope['path'](完整请求路径,带前导斜杠)判断,比 mount 相对 path 更可靠。
            req_path = scope.get("path", "")
            if exc.status_code == 404 and not req_path.startswith(("/api/", "/files/")):
                return await super().get_response("index.html", scope)
            raise


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "matting_provider": settings.matting_provider,
        "upscale_provider": settings.upscale_provider,
    }


@app.get("/api/templates")
def templates() -> list[dict]:
    return list_templates()


@app.post("/api/process")
def process(
    file: UploadFile = File(...),
    template: str = Form("tshirt"),
    upscale: float = Form(1.0),
    width_cm: float = Form(30.0),
    height_cm: float = Form(40.0),
    dpi: int = Form(300),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    # 同步阻塞端点:声明为普通 def,FastAPI 在线程池执行,不阻塞事件循环。
    raw = file.file.read()
    try:
        src = Image.open(io.BytesIO(raw))
        src.load()
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")  # P0-2: 预扣后失败要退点
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    job_id = storage.new_job_id()
    storage.upload_path(job_id).write_bytes(raw)

    # ① 提取印花(抠图 + 自动裁剪 + 可选放大)
    print_img = extract_print(src, upscale=upscale)
    print_path = storage.output_path(job_id, "print.png")
    print_img.save(print_path, format="PNG")
    save_as_asset(db, user.id, print_img, "印花提取", storage.output_url(job_id, "print.png"), source="generated")

    # ② 套图预览
    mockup_img = render_mockup(print_img, template_id=template)
    mockup_path = storage.output_path(job_id, "mockup.png")
    mockup_img.save(mockup_path, format="PNG")

    # ③ 导出生产文件
    prod_path = storage.output_path(job_id, "production.png")
    meta = export_production(print_img, prod_path, width_cm=width_cm, height_cm=height_cm, dpi=dpi)

    storage.mirror_job(job_id)  # 三件套已落盘 → 镜像进对象存储(local no-op)

    return JSONResponse({
        "job_id": job_id,
        "print_url": storage.output_url(job_id, "print.png"),
        "mockup_url": storage.output_url(job_id, "mockup.png"),
        "production_url": storage.output_url(job_id, "production.png"),
        "production_meta": meta,
    })


@app.post("/api/process-async")
async def process_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    template: str = Form("tshirt"),
    upscale: float = Form(1.0),
    width_cm: float = Form(30.0),
    height_cm: float = Form(40.0),
    dpi: int = Form(300),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """异步主线:立即返回 job_id,后台跑抠图→套图→导出;前端轮询 GET /api/jobs/{id}。"""
    raw = await file.read()
    try:
        src = Image.open(io.BytesIO(raw)); src.load()
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    job = create_job(db, "process", params={"template": template}, owner_id=user.id)
    jid = job.id
    uid = user.id
    storage.upload_path(jid).write_bytes(raw)

    def _work() -> dict:
        try:
            print_img = extract_print(src, upscale=upscale)
            print_img.save(storage.output_path(jid, "print.png"), format="PNG")
            mockup_img = render_mockup(print_img, template_id=template)
            mockup_img.save(storage.output_path(jid, "mockup.png"), format="PNG")
            meta = export_production(print_img, storage.output_path(jid, "production.png"),
                                     width_cm=width_cm, height_cm=height_cm, dpi=dpi)
            return {
                "print_url": storage.output_url(jid, "print.png"),
                "mockup_url": storage.output_url(jid, "mockup.png"),
                "production_url": storage.output_url(jid, "production.png"),
                "production_meta": meta,
            }
        except Exception:
            # 后台失败也要退点(与同步路径 P0-2 一致),用独立 session
            s = SessionLocal()
            try:
                u = s.get(User, uid)
                if u:
                    refund(s, u, "process")
            finally:
                s.close()
            raise

    background_tasks.add_task(run_job, jid, _work)
    return JSONResponse({"job_id": jid, "status": "pending"})


# 文生图「商品图·一组」(5 图)的打包优惠价(点)。须为 generate 单价的整数倍,便于折算成扣点笔数。
SET_PACKAGE_CREDITS = 20


@app.post("/api/generate")
async def generate(background_tasks: BackgroundTasks,
                   prompt: str = Form(...), size: str = Form("1024x1024"),
                   gen_type: str = Form("print"),   # print=印花(透明印花稿)| product=商品图(实拍风)
                   group: str = Form("single"),     # single=一张 | set=一组(5图,仅商品图)
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """文生图。两个维度:
    - 类型:印花(transparent 印花稿)/ 商品图(白底/场景/穿着等实拍风)。
    - 数量:一张 / 一组(仅商品图;一次出 白底/尺寸/场景/细节/穿着 5 图,打包价)。
    计费:单张=5点;一组打包=20点(折算 4 笔 generate,任一失败按笔全退、笔数对齐)。
    执行:一组 / 有 key 的单张一律走 Celery 后台(任务中心看结果);无 key 单张本地同步出图(沿用原契约)。"""
    gen_type = gen_type if gen_type in ("print", "product") else "print"
    is_set = gen_type == "product" and group == "set"   # 印花不支持一组(强制单张)
    # 一组打包优惠价 20 点(折算成 generate 的笔数,便于复用扣点/退点原语,笔数对齐 reaper/worker);单张 5 点。
    n = SET_PACKAGE_CREDITS // cost_of("generate") if is_set else 1

    # 按 n 预扣(打包价),余额不足全退 + 402(P0-1)
    charged = 0
    try:
        for _ in range(n):
            charge(db, user, "generate")
            charged += 1
    except InsufficientCredits as exc:
        for _ in range(charged):
            refund(db, user, "generate")
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    used_prompt, hint = refine_generate_prompt(prompt, gen_type)

    # 一组,或有 key 的单张 -> Celery 后台作业(前端轮询 /api/jobs/{id})
    if is_set or settings.openai_api_key:
        return JSONResponse(submit_celery(
            run_tool, db, user, kind="generate", tool_id="generate", op="generate", raw=None,
            params={"prompt": used_prompt, "orig": prompt, "hint": hint, "size": size,
                    "gen_type": gen_type, "is_set": is_set, "n": n}, n=n))

    # 无 key 单张:本地程序化同步出图
    try:
        img = text_to_image(used_prompt, size=size)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "generate")  # P0-2: 调用失败退点
        raise HTTPException(status_code=502, detail="生成失败,请换个更具体的描述再试一次") from exc
    job_id = storage.new_job_id()
    out = storage.output_path(job_id, "generated.png")
    img.save(out, format="PNG")
    save_as_asset(db, user.id, img, f"文生图: {prompt[:24]}", storage.output_url(job_id, "generated.png"), source="generated")
    storage.mirror_job(job_id)  # 镜像进对象存储(local no-op)
    return JSONResponse({
        "job_id": job_id,
        "image_url": storage.output_url(job_id, "generated.png"),
        "prompt_used": used_prompt,
        "hint": hint,
    })


@app.post("/api/edit")
async def edit(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(...),
    mask: UploadFile | None = File(None),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """图生图 / 改图 / 换装 / 换背景(gpt-image / image2 edit)。"""
    raw = await file.read()
    mask_raw = await mask.read() if mask is not None else None
    if settings.openai_api_key:  # gpt-image 耗时 -> Celery 后台作业,前端轮询
        # 输入图(+可选 mask)落盘,worker 自己读(见 tasks._work_edit)。
        return JSONResponse(submit_celery(
            run_tool, db, user, kind="edit", tool_id="", op="edit", raw=raw, mask_raw=mask_raw,
            params={"prompt": prompt, "size": size}))
    try:
        src = Image.open(io.BytesIO(raw)); src.load()
        mask_img = None
        if mask_raw is not None:
            mask_img = Image.open(io.BytesIO(mask_raw)); mask_img.load()
        out_img = image_to_image(src, prompt, mask=mask_img, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")  # P0-2: 调用失败退点
        raise HTTPException(status_code=502, detail="改图失败,请稍后重试") from exc
    job_id = storage.new_job_id()
    out = storage.output_path(job_id, "edited.png")
    out_img.save(out, format="PNG")
    storage.mirror_job(job_id)  # 镜像进对象存储(local no-op)
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "edited.png")})


class CollectIn(BaseModel):
    url: str
    platform: str | None = None


@app.post("/api/collect")
def collect(body: CollectIn, user: User = Depends(current_user)):
    """采集辅助:把平台缩略图 URL 升级为原图 URL(规则收敛在后端,纯字符串变换)。

    需登录(P0-3:避免被当作公开 URL 变换/探测代理)。
    合规:仅做 URL 变换,不代抓图片内容;复用图片的授权判断由调用方负责。
    """
    platform = body.platform or detect_platform(body.url)
    return {"platform": platform, "hires_url": upgrade_to_hires(body.url, platform)}


# 产物文件按 (job_id, name) 唯一且不可变(作业产出写一次不再变)→ 可长期 immutable 缓存,
# 浏览器有效期内连条件请求都不发,重复打开/刷新任务中心更快。(HTML/JS 走另一条 SPA 路由,不受影响)
_FILE_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


@app.get("/files/{job_id}/{name}")
def get_file(job_id: str, name: str, w: int = 0):
    """产物文件。带 ?w=<px> 时返回**缓存的缩略图**(长边≤w),供任务中心列表用——
    避免把整张几 MP 大图塞进 72px 缩略框解码导致滚动卡顿(首次生成、之后命中盘上缓存,不重存)。
    非栅格图(svg/pdf)或缩略失败 → 回退原文件。"""
    p = settings.outputs_dir / job_id / name
    if not p.exists():
        # 本地缓存缺失(retention 清掉 / 多实例没这份)→ 从对象存储回源到本地;local 模式返回 None。
        storage.fetch_to_local(job_id, name)
        if not p.exists():
            raise HTTPException(status_code=404, detail="file not found")
    if w and 0 < w <= 1024 and not name.lower().endswith((".svg", ".pdf")):
        thumb = settings.outputs_dir / job_id / f".thumb_{w}_{name}.png"
        if not thumb.exists():            # 仅首次生成并存盘;之后直接读这个小文件
            try:
                im = Image.open(p); im.load()
                im = im.convert("RGBA")
                im.thumbnail((w, w))
                im.save(thumb, format="PNG")
            except Exception:  # noqa: BLE001 — 非图片/解码失败 → 回退原图
                return FileResponse(p, headers=_FILE_CACHE)
        return FileResponse(thumb, headers=_FILE_CACHE)
    return FileResponse(p, headers=_FILE_CACHE)


# ── 服务前端:Vue 单页应用(history 模式)──
# nginx 把 / 反代到本服务;这里把构建产物 dist 挂在 "/"(mounted last,/api 与 /files
# 已在前面注册,优先匹配)。用 _SPAStaticFiles 在 404 时回退 index.html,支持 history
# 深链刷新。**保留 name="frontend"**:多个测试靠这个名字把自己的路由插到本 mount 之前。
# dist 不存在(纯离线测试/未构建)时整段跳过——前端缺失不影响 API 与测试。
if FRONTEND_DIST.exists():
    app.mount("/", _SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
