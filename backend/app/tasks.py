"""Celery 任务定义 —— 在 **worker 进程**里跑的顶层函数。

与早期 `BackgroundTasks` 版本的关键区别:任务跑在独立进程,**不能传闭包**。
所以每个任务只接收可序列化的 `job_id`,在 worker 内:
  ① 按 job_id 从磁盘读输入(`storage.upload_path`)、从 `Job.params` 读参数;
  ② 调 service 干活、存产物;
  ③ 把状态/结果写回 `Job` 表(唯一真相源,前端轮询它)。

`Job` 表仍是状态机:pending → running(started_at)→ done/error(finished_at)。
失败按 `refund_op` 退点(对齐 P0-2「失败必退点」),用任务自己的 DB session。
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Callable

from PIL import Image
from sqlalchemy.orm import Session

from . import storage
from .celery_app import celery_app
from .db import SessionLocal
from .models_db import Job, User
from .services.billing import refund
from .services.library import save_as_asset
from .services.print_extract import extract_print_design, save_print_outputs

log = logging.getLogger(__name__)

# work 回调签名:work(job_id, job, db) -> dict(存入 job.result)。
Work = Callable[[str, Job, Session], dict]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_job_in_worker(job_id: str, work: Work, *, refund_op: str | None = None,
                      refund_n: int = 1) -> None:
    """通用作业执行骨架:置 running → 跑 work → 记 done/error + finished_at;失败按 refund_n 笔退 refund_op。

    用任务自己的 DB session(worker 进程,不存在请求 session)。work 抛错时:回滚半提交 →
    重读 job 标记 error → 退点 → 提交。幂等友好(task_acks_late 重投时会从 pending/running 重跑)。
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.warning("作业 %s 不存在,跳过", job_id)
            return
        job.status = "running"
        job.started_at = _now()
        db.commit()

        try:
            result = work(job_id, job, db)
            job.status = "done"
            job.result = result if isinstance(result, dict) else {"value": result}
            job.error = ""
        except Exception as exc:  # noqa: BLE001 — 作业内任何失败都记 error + 退点,不让 worker 崩
            db.rollback()
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "error"
            # 保留异常类型,无 message 的异常(如 KeyError())也能定位(对齐旧 run_job 的 P1-1)
            job.error = f"{type(exc).__name__}: {exc}".strip()
            if refund_op and job.owner_id is not None:
                user = db.get(User, job.owner_id)
                if user is not None:
                    for _ in range(refund_n):
                        refund(db, user, refund_op)
            log.warning("作业 %s 失败: %s", job_id, job.error)

        job.finished_at = _now()
        db.commit()
    finally:
        db.close()


# ── 印花提取(Phase A 试点)────────────────────────────────────────────────
def _print_extract_work(job_id: str, job: Job, db: Session) -> dict:
    """从盘上读原图 → AI 重绘提取 → 存透明/白底两版 → 入库素材。"""
    raw = storage.upload_path(job_id).read_bytes()
    src = Image.open(io.BytesIO(raw))
    src.load()
    design, meta = extract_print_design(src)
    url, result = save_print_outputs(job_id, design, meta)
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, design, "印花提取", url, source="generated")
    return result


@celery_app.task(name="podsys.print_extract")
def run_print_extract(job_id: str) -> None:
    run_job_in_worker(job_id, _print_extract_work, refund_op="process")


# ── 通用工具作业(Phase B)──────────────────────────────────────────────────
# 其余异步端点(文生图/改图/裂变/融合/转绘/梗图/提质/扩图/去水印/试衣/换装/合照/转矢量)
# 共用一个 Celery 任务 `run_tool`,按 job.kind 在 TOOL_WORKS 里分派 work。
# 约定:输入图(若有)由 router 落到 storage.upload_path(job_id);参数在 job.params。
# 重依赖(openai/各 service)在 work 内惰性 import,保持离线启动轻量(对齐项目习惯)。
def _load_input(job_id: str) -> Image.Image:
    im = Image.open(io.BytesIO(storage.upload_path(job_id).read_bytes()))
    im.load()
    return im


def _work_generate(job_id: str, job: Job, db: Session) -> dict:
    from .services.generate import text_to_image
    p = job.params
    img = text_to_image(p["prompt"], size=p.get("size", "1024x1024"))
    img.save(storage.output_path(job_id, "generated.png"), format="PNG")
    url = storage.output_url(job_id, "generated.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, img, f"文生图: {p.get('orig', '')[:24]}", url, source="generated")
    return {"image_url": url, "prompt_used": p["prompt"], "hint": p.get("hint", "")}


def _work_edit(job_id: str, job: Job, db: Session) -> dict:
    from .services.generate import image_to_image
    src = _load_input(job_id)
    mask = None
    mpath = storage.upload_path(f"{job_id}_mask")
    if mpath.exists():
        mask = Image.open(mpath)
        mask.load()
    out = image_to_image(src, job.params["prompt"], mask=mask, size=job.params.get("size", "auto"))
    out.save(storage.output_path(job_id, "edited.png"), format="PNG")
    return {"image_url": storage.output_url(job_id, "edited.png")}


def _work_variants(job_id: str, job: Job, db: Session) -> dict:
    from .services import design_tools
    src = _load_input(job_id)
    p = job.params
    imgs = design_tools.make_variants(src, int(p["n"]), prompt=p.get("prompt", ""))
    urls = []
    for i, im in enumerate(imgs):
        name = f"variant_{i + 1}.png"
        im.save(storage.output_path(job_id, name), format="PNG")
        url = storage.output_url(job_id, name)
        urls.append(url)
        if job.owner_id is not None:
            save_as_asset(db, job.owner_id, im, f"图裂变 {i + 1}", url, source="generated")
    return {"images": urls}


def _work_fuse(job_id: str, job: Job, db: Session) -> dict:
    from .services import design_tools
    out = design_tools.make_fuse(_load_input(job_id), job.params["prompt"])
    out.save(storage.output_path(job_id, "fused.png"), format="PNG")
    url = storage.output_url(job_id, "fused.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, "元素融合", url, source="generated")
    return {"image_url": url}


def _work_restyle(job_id: str, job: Job, db: Session) -> dict:
    from .services import design_tools
    style = job.params["style"]
    out = design_tools.make_restyle(_load_input(job_id), style)
    out.save(storage.output_path(job_id, "restyled.png"), format="PNG")
    url = storage.output_url(job_id, "restyled.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, f"风格转绘: {style[:20]}", url, source="generated")
    return {"image_url": url}


def _work_meme(job_id: str, job: Job, db: Session) -> dict:
    from .services import design_tools
    p = job.params
    out = design_tools.make_meme(_load_input(job_id), p.get("text", ""), prompt=p.get("prompt", ""))
    out.save(storage.output_path(job_id, "meme.png"), format="PNG")
    url = storage.output_url(job_id, "meme.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, "梗图印花", url, source="generated")
    return {"image_url": url}


def _work_upscale(job_id: str, job: Job, db: Session) -> dict:
    from .ai.upscale import get_upscale_provider
    out = get_upscale_provider().upscale(_load_input(job_id).convert("RGB"),
                                         scale=float(job.params.get("scale", 1.0)))
    out.save(storage.output_path(job_id, "upscaled.png"), format="PNG")
    url = storage.output_url(job_id, "upscaled.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, "提质", url, source="generated")
    return {"image_url": url, "width": out.width, "height": out.height}


def _work_gptedit(job_id: str, job: Job, db: Session) -> dict:
    """扩图 / 去水印共用:gpt-image edit,prompt(含模板)在 params。"""
    from .ai.openai_image import OpenAIImageClient
    p = job.params
    out = OpenAIImageClient().edit(_load_input(job_id), p["prompt"], size=p.get("size", "auto"))
    out.save(storage.output_path(job_id, "result.png"), format="PNG")
    url = storage.output_url(job_id, "result.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, "图案处理", url, source="generated")
    return {"image_url": url}


def _work_tryon(job_id: str, job: Job, db: Session) -> dict:
    from .services import studio_tools
    out = studio_tools.model_tryon(_load_input(job_id), size=job.params.get("size", "auto"))
    out.save(storage.output_path(job_id, "tryon.png"), format="PNG")
    return {"image_url": storage.output_url(job_id, "tryon.png")}


def _work_pet(job_id: str, job: Job, db: Session) -> dict:
    from .services import studio_tools
    p = job.params
    out = studio_tools.pet_costume(_load_input(job_id), costume=p.get("costume", "royal european"),
                                   size=p.get("size", "auto"))
    out.save(storage.output_path(job_id, "pet.png"), format="PNG")
    return {"image_url": storage.output_url(job_id, "pet.png")}


def _work_group(job_id: str, job: Job, db: Session) -> dict:
    from .services import studio_tools
    p = job.params
    out = studio_tools.group_photo(_load_input(job_id), p["prompt"], size=p.get("size", "auto"))
    out.save(storage.output_path(job_id, "group.png"), format="PNG")
    return {"image_url": storage.output_url(job_id, "group.png")}


def _work_vectorize(job_id: str, job: Job, db: Session) -> dict:
    from .services.vectorize import to_svg
    colors = int(job.params.get("colors", 8))
    svg, rect_count = to_svg(_load_input(job_id), colors=colors, preset=job.params.get("preset", "auto"))
    storage.output_path(job_id, "vector.svg").write_text(svg, encoding="utf-8")
    return {"svg_url": storage.output_url(job_id, "vector.svg"), "rect_count": rect_count, "colors": colors}


# kind → (work, refund_op, n_param)。n_param 非空时退点笔数 = job.params[n_param](如裂变按张扣)。
TOOL_WORKS: dict[str, tuple[Work, str, str | None]] = {
    "generate": (_work_generate, "generate", None),
    "edit": (_work_edit, "edit", None),
    "variants": (_work_variants, "edit", "n"),
    "fuse": (_work_fuse, "edit", None),
    "restyle": (_work_restyle, "edit", None),
    "meme": (_work_meme, "edit", None),
    "upscale": (_work_upscale, "process", None),
    "expand": (_work_gptedit, "edit", None),
    "dewatermark": (_work_gptedit, "edit", None),
    "tryon": (_work_tryon, "edit", None),
    "pet-costume": (_work_pet, "edit", None),
    "group-photo": (_work_group, "edit", None),
    "vectorize": (_work_vectorize, "process", None),
}


@celery_app.task(name="podsys.run_tool")
def run_tool(job_id: str) -> None:
    """通用工具作业:按 job.kind 分派到 TOOL_WORKS 的 work,失败按 refund_op/笔数退点。"""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        spec = TOOL_WORKS.get(job.kind)
        if spec is None:
            job.status = "error"
            job.error = f"未注册的作业类型: {job.kind}"
            job.finished_at = _now()
            db.commit()
            return
        work, op, n_field = spec
        n = int(job.params.get(n_field, 1)) if n_field else 1
    finally:
        db.close()
    run_job_in_worker(job_id, work, refund_op=op, refund_n=n)
