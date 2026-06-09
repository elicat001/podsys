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


# ── 印花提取 ──────────────────────────────────────────────────────────────
def _print_extract_work(job_id: str, job: Job, db: Session) -> dict:
    """读原图 → 提取(engine=fast 本地保真 / 否则 AI 重绘)→ 存透明/白底两版 → 入库素材。"""
    raw = storage.upload_path(job_id).read_bytes()
    src = Image.open(io.BytesIO(raw)); src.load()
    if job.params.get("engine") == "fast":
        from .services.design_extract import extract_design
        design, meta = extract_design(src)
        meta.setdefault("engine", "local")
    else:
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
    imgs = design_tools.make_variants(src, int(p["n"]), prompt=p.get("prompt", ""),
                                      prefer_local=p.get("engine") == "fast")
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


# ── 本地产出类工具(原同步,现也丢后台,见各 router)──────────────────────────
def _work_seamless(job_id: str, job: Job, db: Session) -> dict:
    from .services import seamless as seamless_svc
    out = seamless_svc.seamless_pattern(_load_input(job_id), repeat=int(job.params.get("repeat", 2)))
    out.save(storage.output_path(job_id, "seamless.png"), format="PNG")
    url = storage.output_url(job_id, "seamless.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, "四方连续图", url, source="generated")
    return {"image_url": url}


def _work_compress(job_id: str, job: Job, db: Session) -> dict:
    from .services.image_tools import compress_image
    raw = storage.upload_path(job_id).read_bytes()
    src = Image.open(io.BytesIO(raw)); src.load()
    p = job.params
    out_img, encoded, info = compress_image(
        src, target_w=int(p.get("target_w", 0)), target_h=int(p.get("target_h", 0)),
        quality=int(p.get("quality", 85)), fmt=p.get("fmt", "jpeg"))
    ext = "jpg" if info["pil_format"] == "JPEG" else info["format"]
    name = f"compressed.{ext}"
    storage.output_path(job_id, name).write_bytes(encoded)  # 落编码后字节,与 output_bytes 一致
    url = storage.output_url(job_id, name)
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out_img, "裁剪压缩", url, source="generated",
                      size_bytes=info["output_bytes"])
    return {"image_url": url, "original_bytes": len(raw), "output_bytes": info["output_bytes"],
            "width": info["width"], "height": info["height"], "format": info["format"]}


def _work_mockup(job_id: str, job: Job, db: Session) -> dict:
    from .services import mockup
    p = job.params
    template = p.get("template", "tshirt")
    color = p.get("color") or None
    img, engine = mockup.render_product(_load_input(job_id), template, color)
    name = f"mockup_{template}_{color or 'default'}.png"
    img.save(storage.output_path(job_id, name), format="PNG")
    url = storage.output_url(job_id, name)
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, img, f"套图 {template}", url, source="generated")
    return {"image_url": url, "template": template, "color": color, "engine": engine}


def _work_mockup_batch(job_id: str, job: Job, db: Session) -> dict:
    from .services import mockup
    combos = [(t, c) for t, c in job.params.get("combos", [])]  # [[tid,color],...] → [(tid,color)]
    rendered = mockup.render_variants(_load_input(job_id), combos)
    items, urls = [], []
    for tid, color, im in rendered:
        name = f"mockup_{tid}_{color}.png"
        im.save(storage.output_path(job_id, name), format="PNG")
        url = storage.output_url(job_id, name)
        if job.owner_id is not None:
            save_as_asset(db, job.owner_id, im, f"套图 {tid}/{color}", url, source="generated")
        items.append({"template": tid, "color": color, "url": url})
        urls.append(url)
    return {"items": items, "images": urls, "count": len(items)}


def _work_mockup_replace(job_id: str, job: Job, db: Session) -> dict:
    """套图模板印花替换:逐张产品照把原印花换成新印花(新印花=upload_path(job_id))。

    产品照来源:template_id>0 → 团队资源套图模板的图(按 url 读盘);否则 → 临时上传(job_id_m{i})。
    """
    from .services.mockup_replace import replace_print
    p = job.params
    prefer_local = p.get("engine") == "fast"
    new_print = _load_input(job_id)  # 新印花
    products: list[Image.Image] = []
    tid = int(p.get("template_id") or 0)
    if tid:
        from .models_team import MockupTemplate
        tpl = db.get(MockupTemplate, tid)
        if tpl is None:
            raise ValueError("套图模板不存在")
        for im in tpl.images:
            dp = storage.path_from_url(im.path)
            if dp and dp.is_file():
                pim = Image.open(dp); pim.load(); products.append(pim)
    else:
        i = 0
        while True:
            mp = storage.upload_path(f"{job_id}_m{i}")
            if not mp.exists():
                break
            pim = Image.open(mp); pim.load(); products.append(pim); i += 1
    if not products:
        raise ValueError("没有可用的产品图")

    urls = []
    for i, prod in enumerate(products):
        out = replace_print(prod, new_print, prefer_local=prefer_local)
        name = f"mockup_{i}.png"
        out.save(storage.output_path(job_id, name), format="PNG")
        url = storage.output_url(job_id, name)
        urls.append(url)
        if job.owner_id is not None:
            save_as_asset(db, job.owner_id, out.convert("RGBA"), f"套图 {i + 1}", url, source="generated")
    return {"images": urls, "count": len(urls)}


def _work_production(job_id: str, job: Job, db: Session) -> dict:
    from .services import export
    p = job.params
    out_dir = storage.output_path(job_id, "_").parent
    result = export.export_production_multi(
        _load_input(job_id), out_dir, name_base="production",
        width_cm=p["width_cm"], height_cm=p["height_cm"], dpi=p["dpi"],
        formats=tuple(p["formats"]), bg=tuple(p["bg"]),
        bleed_mm=p["bleed_mm"], safe_mm=p["safe_mm"], scale=p["scale"], anchor=p["anchor"],
        cmyk=p["cmyk"], proof=p["proof"])
    files = {fmt: storage.output_url(job_id, name) for fmt, name in result["files"].items()}
    proof_url = storage.output_url(job_id, result["proof"]) if result.get("proof") else None
    return {"files": files, "proof": proof_url, "meta": result["meta"]}


def _work_title(job_id: str, job: Job, db: Session) -> dict:
    """标题提取(分析类,信息结果)。engine=ai 走 gpt 识图;否则本地规则。
    智能若降级(AI 不可用)→ 退回已扣的 1 点(快速本就没扣,见 router)。"""
    from .services import studio_tools
    p = job.params
    img = None
    ipath = storage.upload_path(job_id)  # 可选辅助图(router 有图才落盘)
    if ipath.exists():
        try:
            img = Image.open(ipath); img.load()
        except Exception:  # noqa: BLE001
            img = None
    prefer_local = p.get("engine") != "ai"
    result = studio_tools.generate_title(keywords=p.get("keywords", ""),
                                         category=p.get("category", "apparel"),
                                         img=img, prefer_local=prefer_local)
    if p.get("engine") == "ai" and result.get("degraded") and job.owner_id is not None:
        u = db.get(User, job.owner_id)
        if u is not None:
            refund(db, u, "title")  # 智能降级 → 不收费
    return result


def _work_ipguard(job_id: str, job: Job, db: Session) -> dict:
    """侵权风险检索(分析类,信息结果)。本地库扫描,无 AI/本地之分。"""
    from .services import ip_guard
    p = job.params
    report = ip_guard.scan(_load_input(job_id), title=p.get("title") or None)
    if not p.get("verbose"):
        report = {"risk": report.get("risk"), "advice": report.get("advice"),
                  "match_count": len(report.get("matches", [])), "checked": report.get("checked")}
    return report


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
    # 本地产出类(原同步,现丢后台)
    "seamless": (_work_seamless, "process", None),
    "compress": (_work_compress, "process", None),
    "mockup": (_work_mockup, "asset", None),
    "mockup-batch": (_work_mockup_batch, "asset", "n"),
    "mockup-replace": (_work_mockup_replace, "asset", "n"),
    "production": (_work_production, "asset", None),
    # 分析类(信息结果,丢任务中心)
    "title": (_work_title, None, None),       # 退点在 worker 内按 degrade 处理,不走通用退点
    "ipguard": (_work_ipguard, "process", None),
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
