"""FastAPI app — MVP main line: upload → extract → mockup → export."""
from __future__ import annotations
import io
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .config import settings
from . import storage
from .services.extract import extract_print
from .services.mockup import render_mockup, list_templates
from .services.export import export_production
from .services.generate import text_to_image, image_to_image, refine_prompt
from .services.collectors import detect_platform, upgrade_to_hires
from .services.billing import charge_for, refund
from .auth import current_user
from .models_db import User
from .db import init_db, get_db, SessionLocal
from .services.jobs import create_job, run_job
from .services.library import save_as_asset
from .tasks import run_tool
from .web_utils import submit_celery
from sqlalchemy.orm import Session
from .routers import auth as auth_router
from .routers import assets as assets_router
from .routers import design as design_router
from .routers import products as products_router
from .routers import jobs as jobs_router
from .routers import billing as billing_router
from .routers import workflow as workflow_router
from .routers import design_tools as design_tools_router
from .routers import image_tools as image_tools_router
from .routers import studio_tools as studio_tools_router
from .routers import ip_guard as ip_guard_router
from .routers import search as search_router
from .routers import vectorize as vectorize_router
from .routers import collect_tasks as collect_tasks_router
from .routers import shops as shops_router
from .routers import workflow_custom as workflow_custom_router
from .routers import my_workflows as my_workflows_router
from .routers import me as me_router
from .routers import video as video_router
from .routers import product_admin as product_admin_router
from .routers import space as space_router
from .routers import video_cases as video_cases_router
from .routers import templates as templates_router
from .routers import print_extract as print_extract_router
from .routers import export as export_router
from .routers import mockup as mockup_router
from .routers import team as team_router

app = FastAPI(title="PODStudio API", version="0.3.0")
settings.ensure_dirs()
init_db()


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """前端是单文件静态页且频繁迭代;禁掉 HTML 文档缓存,避免浏览器服旧页
    (这是"改完代码必须硬刷新才生效"的根因)。只作用于 HTML,不动 /files 图片与 JSON API。"""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


app.include_router(auth_router.router)
app.include_router(assets_router.router)
app.include_router(design_router.router)
app.include_router(products_router.router)
app.include_router(jobs_router.router)
app.include_router(billing_router.router)
app.include_router(workflow_router.router)
app.include_router(design_tools_router.router)
app.include_router(image_tools_router.router)
app.include_router(studio_tools_router.router)
app.include_router(ip_guard_router.router)
app.include_router(search_router.router)
app.include_router(vectorize_router.router)
app.include_router(collect_tasks_router.router)
app.include_router(shops_router.router)
app.include_router(workflow_custom_router.router)
app.include_router(my_workflows_router.router)
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


@app.post("/api/generate")
async def generate(background_tasks: BackgroundTasks,
                   prompt: str = Form(...), size: str = Form("1024x1024"),
                   user: User = Depends(charge_for("generate")),
                   db: Session = Depends(get_db)):
    """文生图(gpt-image / image2)。对偏薄的描述温和补全并透明返回。"""
    used_prompt, hint = refine_prompt(prompt)
    if settings.openai_api_key:  # gpt-image 耗时 -> Celery 后台作业,前端轮询 /api/jobs/{id}
        # 无输入图(纯文生图),只把 prompt/hint/size 传给 worker(见 tasks._work_generate)。
        return JSONResponse(submit_celery(
            run_tool, db, user, kind="generate", tool_id="generate", op="generate", raw=None,
            params={"prompt": used_prompt, "orig": prompt, "hint": hint, "size": size}))
    try:
        img = text_to_image(used_prompt, size=size)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "generate")  # P0-2: 调用失败退点
        raise HTTPException(status_code=502, detail="生成失败,请换个更具体的描述再试一次") from exc
    job_id = storage.new_job_id()
    out = storage.output_path(job_id, "generated.png")
    img.save(out, format="PNG")
    save_as_asset(db, user.id, img, f"文生图: {prompt[:24]}", storage.output_url(job_id, "generated.png"), source="generated")
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


@app.get("/files/{job_id}/{name}")
def get_file(job_id: str, name: str):
    p = settings.outputs_dir / job_id / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(p)


# ── 服务前端:Vue 单页应用(history 模式)──
# nginx 把 / 反代到本服务;这里把构建产物 dist 挂在 "/"(mounted last,/api 与 /files
# 已在前面注册,优先匹配)。用 _SPAStaticFiles 在 404 时回退 index.html,支持 history
# 深链刷新。**保留 name="frontend"**:多个测试靠这个名字把自己的路由插到本 mount 之前。
# dist 不存在(纯离线测试/未构建)时整段跳过——前端缺失不影响 API 与测试。
if FRONTEND_DIST.exists():
    app.mount("/", _SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
