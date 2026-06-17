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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

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
    return datetime.now(UTC)


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
            # 产物已落本地盘 → 镜像进对象存储(local 模式 no-op;失败只 warning 不影响 done)。
            # 注:采集同步 collect_sync 每张图新建独立 job_id,由 sync_images 内部各自 mirror,不靠这里。
            storage.mirror_job(job_id)
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
# 其余异步端点(文生图/改图/裂变/转绘/梗图/提质/去水印/转矢量/图生视频 等)
# 共用一个 Celery 任务 `run_tool`,按 job.kind 在 TOOL_WORKS 里分派 work(全集见 TOOL_WORKS)。
# 约定:输入图(若有)由 router 落到 storage.upload_path(job_id);参数在 job.params。
# 重依赖(openai/各 service)在 work 内惰性 import,保持离线启动轻量(对齐项目习惯)。
def _load_input(job_id: str) -> Image.Image:
    im = Image.open(io.BytesIO(storage.upload_path(job_id).read_bytes()))
    im.load()
    return im


def _source_preview_url(job_id: str, img: Image.Image, max_side: int = 640) -> str:
    """把输入图存一份缩略图到 outputs(/files 可访问),给分析类任务(标题/侵权)当卡片缩略图 + 预览图。
    保留透明(PNG),≤max_side。"""
    im = img.convert("RGBA")
    im.thumbnail((max_side, max_side))
    im.save(storage.output_path(job_id, "source.png"), format="PNG")
    return storage.output_url(job_id, "source.png")


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
    url = storage.output_url(job_id, "edited.png")
    if job.owner_id is not None:  # 入库 → 可进回收站/计配额/可清理(否则删任务后文件成幽灵)
        save_as_asset(db, job.owner_id, out, "改图", url, source="generated")
    return {"image_url": url}


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


def _work_vectorize(job_id: str, job: Job, db: Session) -> dict:
    from .services.vectorize import to_svg
    src = _load_input(job_id)
    colors = int(job.params.get("colors", 8))
    svg, rect_count = to_svg(src, colors=colors, preset=job.params.get("preset", "auto"))
    storage.output_path(job_id, "vector.svg").write_text(svg, encoding="utf-8")
    url = storage.output_url(job_id, "vector.svg")
    if job.owner_id is not None:  # 入库(用输入图做缩略/查重源,size 用 svg 字节)→ 可进回收站/可清理
        save_as_asset(db, job.owner_id, src, "转矢量", url, source="generated",
                      size_bytes=len(svg.encode("utf-8")))
    return {"svg_url": url, "rect_count": rect_count, "colors": colors}


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

    # 并发处理多张产品图:AI 路径并发度 = 网关上限(POD_OPENAI_MAX_CONCURRENCY;中转站不限就调到 10,
    # 受限则保持 2;_API_GATE 再做全局兜底)。本地路径用适度多核。
    # 关键:图像处理(调网关 / CPU)放线程并发;**save_as_asset 等 DB 写入回到主线程串行**
    # (SQLAlchemy session 非线程安全,绝不能多线程共用同一 session)。
    from .config import settings as _settings
    workers = min(len(products), 4 if prefer_local else max(1, int(_settings.openai_max_concurrency)))

    def _render(idx_prod):
        i, prod = idx_prod
        out = replace_print(prod, new_print, prefer_local=prefer_local)  # 不碰 DB
        name = f"mockup_{i}.png"
        out.save(storage.output_path(job_id, name), format="PNG")
        return i, out, storage.output_url(job_id, name)

    items = list(enumerate(products))
    if workers > 1 and len(items) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_render, items))   # 保序;任一张抛错→整作业 error+按张退点
    else:
        results = [_render(ip) for ip in items]

    urls = []
    for i, out, url in results:
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
        formats=tuple(p["formats"]), bg=tuple(p["bg"]), transparent=p.get("transparent", True),
        bleed_mm=p["bleed_mm"], safe_mm=p["safe_mm"], scale=p["scale"], anchor=p["anchor"],
        cmyk=p["cmyk"], proof=p["proof"])
    files = {fmt: storage.output_url(job_id, name) for fmt, name in result["files"].items()}
    proof_url = storage.output_url(job_id, result["proof"]) if result.get("proof") else None
    # 登记主产物(png 优先)为素材,使删除生产图任务后可在回收站恢复 + 计入存储
    if job.owner_id is not None:
        main_fmt = "png" if "png" in result["files"] else next(iter(result["files"]), None)
        if main_fmt:
            main_path = storage.output_path(job_id, result["files"][main_fmt])
            try:
                img = Image.open(main_path); img.load()
                save_as_asset(db, job.owner_id, img, "生产图", files[main_fmt], source="generated",
                              size_bytes=main_path.stat().st_size)
            except Exception:  # noqa: BLE001 — 入库失败不影响主产物交付
                pass
    return {"files": files, "proof": proof_url, "meta": result["meta"]}


def _work_matting(job_id: str, job: Job, db: Session) -> dict:
    """一键抠图:去背景 → 透明 PNG。优先 rembg/u2net(干净),兜底 pillow。登记为素材(可回收/计存储)。"""
    from .ai.matting import cutout_best
    out = cutout_best(_load_input(job_id)).convert("RGBA")
    out.save(storage.output_path(job_id, "cutout.png"), format="PNG")
    url = storage.output_url(job_id, "cutout.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, "一键抠图", url, source="generated")
    return {"image_url": url}


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
    if img is not None:  # 用户上传了设计图 → 存预览图,任务卡片/预览弹窗直接显示原图
        try:
            result["image_url"] = _source_preview_url(job_id, img)
        except Exception:  # noqa: BLE001 — 预览图失败不影响标题结果
            pass
    if p.get("engine") == "ai" and result.get("degraded") and job.owner_id is not None:
        u = db.get(User, job.owner_id)
        if u is not None:
            refund(db, u, "title")  # 智能降级 → 不收费
    return result


def _work_ipguard(job_id: str, job: Job, db: Session) -> dict:
    """侵权风险检索(分析类,信息结果)。本地库扫描,无 AI/本地之分。"""
    from .services import ip_guard
    p = job.params
    src = _load_input(job_id)
    # engine=ai → 深度检测(本地信号 + 网关视觉模型);否则 → 快速检测(纯本地)
    if p.get("engine") == "ai":
        report = ip_guard.scan_ai(src, title=p.get("title") or None)
    else:
        report = ip_guard.scan(src, title=p.get("title") or None)
    if not p.get("verbose"):
        report = {"risk": report.get("risk"), "advice": report.get("advice"),
                  "match_count": len(report.get("matches", [])), "checked": report.get("checked"),
                  "degraded": report.get("degraded")}
    try:  # 被检图存预览,任务卡片/预览弹窗直接显示
        report["image_url"] = _source_preview_url(job_id, src)
    except Exception:  # noqa: BLE001
        pass
    return report


def _work_sync(job_id: str, job: Job, db: Session) -> dict:
    """采集同步(一个商品=一个任务):对 params.image_ids 服务端取图入库,标侵权风险。
    采集免费,不扣点(op=None,失败不退点);最多并发数由 worker 决定(默认 3)。"""
    from sqlalchemy import select as _select

    from .models_collect import CollectedImage
    from .services import collect_tasks as ct
    ids = [int(i) for i in (job.params.get("image_ids") or [])]
    if job.owner_id is None or not ids:
        return {"synced": 0, "failed": 0, "title": job.params.get("title", ""), "errors": ["无效任务"]}
    res = ct.sync_images(db, job.owner_id, ids)
    # 缩略图 = 本组首张已同步素材,任务卡片/最近任务直接显示
    thumb = ""
    rows = db.execute(
        _select(CollectedImage).where(CollectedImage.id.in_(ids), CollectedImage.synced == True)  # noqa: E712
    ).scalars().all()
    if rows:
        thumb = rows[0].asset_url or ""
    out = {
        "synced": res["synced"], "failed": res["failed"],
        "title": job.params.get("title", ""), "image_url": thumb, "errors": res.get("errors", []),
    }
    if res["synced"] == 0 and res["failed"] > 0:  # 全失败 → 标记作业 error(最近任务显示失败)
        raise RuntimeError(res["errors"][0] if res["errors"] else "同步取图失败")
    return out


def _work_aivideo(job_id: str, job: Job, db: Session) -> dict:
    """AI 图生视频(智谱 CogVideoX-3 或本地兜底 GIF)。读 1~2 张输入图(2 张=首尾帧)→ provider → 存产物。
    输入图:第 1 张在 upload_path(job_id);可选第 2 张(尾帧)在 upload_path(job_id_mask)。"""
    from .ai.video import (
        aspect_size,
        compose_prompt,
        fit_to_aspect,
        get_video_provider,
        gptimage_size,
        scene_frame_prompt,
    )
    from .config import settings
    p = job.params
    cat = p.get("category", "通用")
    aspect = p.get("aspect", "portrait")
    size = aspect_size(aspect, p.get("resolution", "1080p"))
    tw, th = (int(x) for x in size.split("x"))
    raw = [_load_input(job_id)]
    mpath = storage.upload_path(f"{job_id}_mask")   # 复用 mask 槽放第 2 张(尾帧)
    if mpath.exists():
        im2 = Image.open(mpath); im2.load(); raw.append(im2)
    imgs = [fit_to_aspect(im, tw, th) for im in raw]   # 按画幅等比贴合,防模型生硬拉伸
    # 两步生成(可选「场景首帧」):先用 gpt-image 把商品放进场景做第一帧 → 缓解首帧→场景的硬切。
    # 无 key / 失败 → 优雅降级,直接用原图首帧(不阻断)。
    lang = p.get("language", "葡萄牙语")
    if p.get("scene_frame") and settings.openai_api_key:
        try:
            from .ai.openai_image import OpenAIImageClient
            framed = OpenAIImageClient().edit(raw[0], scene_frame_prompt(cat, lang), size=gptimage_size(aspect))
            imgs[0] = fit_to_aspect(framed, tw, th)
        except Exception:  # noqa: BLE001
            pass
    # 提示词工程:镜头脚本 + 类目动作 + 地区风格(随语言)+ 语言 + 一致性/防拉伸 + 负向
    prompt = compose_prompt(p.get("prompt", ""), language=lang, category=cat)
    out = get_video_provider().image_to_video(imgs, prompt, size=size, seconds=p.get("seconds"))
    # 旁白配音(best-effort):选了配音 + 真 mp4(非本地兜底 GIF)才做。看图写目标语言口播稿 → edge-tts → 叠回。
    # 失败/无网/无 key 一律保留原视频,绝不阻断视频作业(CogVideoX 只产音效不产语音,语音靠这条补)。
    if p.get("voiceover") and out.get("ext") == "mp4":
        try:
            from .services.voiceover import add_voiceover
            new_bytes, script = add_voiceover(out["bytes"], raw[0], p.get("prompt", ""),
                                              lang, int(p.get("seconds") or settings.video_seconds))
            out["bytes"] = new_bytes
            if script:
                out.setdefault("meta", {})["voiceover"] = script[:200]
        except Exception:  # noqa: BLE001 — 配音绝不阻断视频作业
            pass
    ext = out.get("ext", "mp4")
    name = f"video.{ext}"
    storage.output_path(job_id, name).write_bytes(out["bytes"])
    url = storage.output_url(job_id, name)
    meta = out.get("meta", {})
    if job.owner_id is not None:  # 入库(用首帧做查重源,size 用视频字节)→ 删任务时可进回收站,不再成幽灵
        save_as_asset(db, job.owner_id, imgs[0], f"图生视频 {cat}", url, source="generated",
                      size_bytes=len(out["bytes"]))
    return {"video_url": url, "ext": ext, "voiceover": meta.get("voiceover", ""),
            "cover": meta.get("cover", ""), "engine": meta.get("engine", ""),
            "degraded": bool(meta.get("degraded"))}


# kind → (work, refund_op, n_param)。n_param 非空时退点笔数 = job.params[n_param](如裂变按张扣)。
TOOL_WORKS: dict[str, tuple[Work, str, str | None]] = {
    "generate": (_work_generate, "generate", None),
    "edit": (_work_edit, "edit", None),
    "variants": (_work_variants, "edit", "n"),
    "restyle": (_work_restyle, "edit", None),
    "meme": (_work_meme, "edit", None),
    "upscale": (_work_upscale, "process", None),
    "dewatermark": (_work_gptedit, "edit", None),
    "vectorize": (_work_vectorize, "process", None),
    "mockup": (_work_mockup, "asset", None),
    "mockup-replace": (_work_mockup_replace, "asset", "n"),
    "production": (_work_production, "asset", None),
    "matting": (_work_matting, "process", None),
    # 分析类(信息结果,丢任务中心)
    "title": (_work_title, None, None),       # 退点在 worker 内按 degrade 处理,不走通用退点
    "ipguard": (_work_ipguard, "process", None),
    # 采集同步(免费,不扣点 → op=None)
    "collect_sync": (_work_sync, None, None),
    # AI 图生视频(智谱 CogVideoX-3 / 本地兜底)
    "aivideo": (_work_aivideo, "video", None),
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
