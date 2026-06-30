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
    重读 job 标记 error → 退点 → 提交。幂等:task_acks_late 重投只对 pending/running 重跑,
    终态(done/error)直接跳过(防回收器退点后又被重投导致重复出图/重复退点)。
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.warning("作业 %s 不存在,跳过", job_id)
            return
        # 幂等护栏:已是终态(done/error)直接跳过。覆盖「回收器已判失败+退点后,旧消息又被 broker 重投」
        # 的竞态——否则会重复出图 + 重复退点(done 覆盖 error / 二次 refund)。重投只对 pending/running 重跑。
        if job.status in ("done", "error"):
            log.info("作业 %s 已是终态 %s,跳过重复执行(重投/竞态)", job_id, job.status)
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


# 视频长任务的"当前阶段"提示文案(写进 job.result,running 态 jobs API 也下发 → 我的空间实时显示)。
_STAGE_MUFRA = "母帧合成中…(把商品放进真实场景做首帧,约 1-2 分钟)"
_STAGE_VIDEO = "视频生成中…(AI 出片,可能需要数分钟)"


def _set_stage(db: Session, job: Job, text: str) -> None:
    """把"当前阶段"临时写进 job.result(running 态 jobs API 也下发 result)→ 前端"我的空间"实时显示
    "母帧合成中…/视频生成中…",长任务不再像卡死。done 时由 run_job_in_worker 用最终 result 覆盖;
    error 时前端按 status 显示错误、忽略 stage。纯 UX,失败不阻断作业。"""
    try:
        job.result = {"stage": text}
        db.commit()
    except Exception:  # noqa: BLE001 — 阶段提示失败不影响作业本身
        db.rollback()


def _mufra_permanent(exc: Exception) -> bool:
    """母帧错误是否【永久/重试无用】:鉴权/额度/坏请求 → True(立即放弃,别空等退避);
    其余(503「无可用账号」/超时/连接/5xx/网关抖动)→ False(瞬时拥塞,退避重试熬过它)。"""
    s = str(exc).lower()
    return any(k in s for k in (
        "invalid_api_key", "incorrect api key", "unauthorized", "permission",
        "余额", "insufficient", "quota", " 401", " 403", "code: 400", "400 -", "未配置",
    ))


def _mufra_with_backoff(do_edit):
    """母帧 gpt-image 调用:走【全局自适应并发限流器】(与所有作图共用一个队列)+ 拿位后退避重试。
    ① 每次尝试先 `_API_GATE.acquire()` 排队等位(限流器按中转站可用并发动态放行;【等位不计入预算】)——
       不把作图中转站【自己人挤爆】(实证 503「无可用账号」根因)。
    ② 【首次拿到位】才起算 video_mufra_budget;拿到位才发请求;成功/失败都 `report`(限流器据此升/降并发)。
    ③ 瞬时错指数退避重试(8→16→32→60s,受预算约束)熬过【他人造成的】拥塞;永久错(鉴权/余额/坏请求)立即放弃。
    do_edit 内部应以 use_gate=False 调 edit(并发已由这里 acquire/report 管,别再叠一层)。
    返回结果;预算/次数耗尽则抛最后异常(已 report 出队后再抛)。"""
    import time as _t

    from .ai.openai_image import _API_GATE
    from .config import settings
    last_exc: Exception | None = None
    deadline: float | None = None
    delay = 8.0
    attempts = max(1, int(settings.video_mufra_attempts))
    for i in range(attempts):
        _API_GATE.acquire()                                # 排队等位(不计入预算)
        if deadline is None:
            deadline = _t.monotonic() + float(settings.video_mufra_budget)   # 预算从【首次拿到位】起算
        try:
            result = do_edit()
            _API_GATE.report(True)                         # 进了且成功 → 限流器往上爬
            return result
        except Exception as exc:  # noqa: BLE001 — 失败也 report(配平 + 反馈容量:503→限流器回退)
            _API_GATE.report(False, exc)
            last_exc = exc
            if _mufra_permanent(exc) or i + 1 >= attempts or _t.monotonic() + delay >= deadline:
                break
            _t.sleep(delay)
            delay = min(delay * 2, 60.0)
    raise last_exc if last_exc is not None else RuntimeError("母帧生成失败")


def _work_generate(job_id: str, job: Job, db: Session) -> dict:
    from .services.generate import generate_product_set, text_to_image
    p = job.params
    size = p.get("size", "1024x1024")
    orig = p.get("orig", "")
    if p.get("is_set"):  # 商品图·一组:5 张分镜,产出 images 数组(任务中心按多图网格渲染)
        urls = []
        for slug, label, img in generate_product_set(p["prompt"], size=size):
            name = f"{slug}.png"
            img.save(storage.output_path(job_id, name), format="PNG")
            url = storage.output_url(job_id, name)
            urls.append(url)
            if job.owner_id is not None:
                save_as_asset(db, job.owner_id, img, f"商品图·{label}: {orig[:16]}", url, source="generated")
        return {"images": urls, "prompt_used": p["prompt"], "hint": p.get("hint", "")}
    # 单张(印花 / 商品图)
    img = text_to_image(p["prompt"], size=size)
    img.save(storage.output_path(job_id, "generated.png"), format="PNG")
    url = storage.output_url(job_id, "generated.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, img, f"文生图: {orig[:24]}", url, source="generated")
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
    """扩图 / 去水印 / 图文替换共用:gpt-image edit,prompt(含模板)在 params。
    入库素材名可由 params['asset_name'] 指定(便于在我的空间区分),默认「图案处理」。"""
    from .ai.openai_image import OpenAIImageClient
    p = job.params
    out = OpenAIImageClient().edit(_load_input(job_id), p["prompt"], size=p.get("size", "auto"))
    out.save(storage.output_path(job_id, "result.png"), format="PNG")
    url = storage.output_url(job_id, "result.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, p.get("asset_name", "图案处理"), url, source="generated")
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
    """一键抠图:去背景 → 透明 PNG。双引擎:
    - fast(快速,本地):rembg/u2net(干净),兜底 pillow——纯去背景、保真原像素。
    - ai(智能):gpt-image 识别主体并扣出(连手/道具/无关元素一起去掉),非保真但能处理本地搞不定的图。
    登记为素材(可回收/计存储)。"""
    p = job.params
    src = _load_input(job_id)
    if p.get("engine") == "ai":
        from .ai.matting import ai_subject_cutout
        out = ai_subject_cutout(src, hint=p.get("prompt", "")).convert("RGBA")
        name = "智能抠图"
    else:
        from .ai.matting import cutout_best
        out = cutout_best(src).convert("RGBA")
        name = "一键抠图"
    out.save(storage.output_path(job_id, "cutout.png"), format="PNG")
    url = storage.output_url(job_id, "cutout.png")
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, out, name, url, source="generated")
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
    prompt_fn = compose_prompt   # 人物行为/故事路径(镜头脚本 + 地区风格 + 导演层 + 画面底线)
    cat = p.get("category", "通用")
    aspect = p.get("aspect", "portrait")
    size = aspect_size(aspect, p.get("resolution", "720p"))
    tw, th = (int(x) for x in size.split("x"))
    raw = [_load_input(job_id)]
    mpath = storage.upload_path(f"{job_id}_mask")   # 复用 mask 槽放第 2 张(尾帧)
    if mpath.exists():
        im2 = Image.open(mpath); im2.load(); raw.append(im2)
    imgs = [fit_to_aspect(im, tw, th) for im in raw]   # 按画幅等比贴合,防模型生硬拉伸
    # 两步生成(可选「场景首帧」):先用 gpt-image 把商品放进场景做第一帧 → 缓解首帧→场景的硬切。
    # 无 key / 失败 → 优雅降级,直接用原图首帧(不阻断)。
    lang = p.get("language", "葡萄牙语")
    # 【首尾帧铁律】用户给了尾帧(2 张图)→ 两端都由用户定,绝不走「场景母帧」(否则母帧把首帧整个掉包,
    # 首尾帧形同虚设、过渡稀烂——真实踩过的 bug)。母帧只服务「单图→让它动起来」那条路径。
    two_frames = len(raw) > 1
    use_scene = bool(p.get("scene_frame") and settings.openai_api_key) and not two_frames
    # 内容策划层(per-shot 母帧 + 动作链):多分镜每镜各自脚本(prompt/prompt2/prompt3…)+ 各自场景母帧
    # (scene1/scene2/scene3…)→ 镜头间真换场景、动作连续。段数由 MULTI_SHOT_PLAN 决定(当前 3×5s),不写死。
    from .ai.video import MULTI_SHOT_PLAN
    n_shots = len(MULTI_SHOT_PLAN)
    scenes = [(p.get(f"scene{i + 1}") or "").strip() for i in range(n_shots)]
    prompts = [p.get("prompt" if i == 0 else f"prompt{i + 1}", "") for i in range(n_shots)]
    # 故事能力下沉后台(自动融合):多分镜 + 有 key 但未给场景(= 手动「视频类型」路径)→ 按类目补中性
    # 【动作链】场景,让手动路径也走 per-shot。仅 use_scene(有 key)时补;无 key 不补 → 回退共享/原图首帧。
    # 向导路径已带 AI 场景,不进这里。
    if p.get("two_shot") and use_scene and any(not s for s in scenes):
        try:
            from .services.video_templates import default_scenes
            defaults = default_scenes(cat, n_shots)
            scenes = [scenes[i] or (defaults[i] if i < len(defaults) else "") for i in range(n_shots)]
        except Exception:  # noqa: BLE001 — 模板兜底失败不阻断,退回共享母帧
            pass
    per_shot_frames = bool(p.get("two_shot") and use_scene and all(scenes))

    warnings: list[str] = []   # 显式记录"静默降级"(母帧/配音失败),写进 Job 结果让用户看见,不再无声吞掉

    # 母帧源图:把原图缩到长边 ≤1024 再发给 gpt-image(输出本就 ≤1024×1536),大幅降上传体积/耗时,
    # 治"母帧 Request timed out"(尤其三分镜要调 3 次 gpt-image)。商品印花在 1024 下仍清晰,不损保真。
    _frame_src = raw[0]
    if max(_frame_src.size) > 1024:
        _frame_src = _frame_src.copy()
        _frame_src.thumbnail((1024, 1024), Image.LANCZOS)

    def _scene_frame(scene: str, action: str = ""):
        """gpt-image 把商品合成进 scene 做母帧并贴合画幅;失败返回 None(调用方降级原图首帧)。
        action=该镜脚本 → 母帧落在它的【起始瞬间】(Scene Init:母帧=视频第0帧、与脚本开头衔接)。
        走 _mufra_with_backoff:中转站偶发 503「无可用账号」/超时是【瞬时拥塞】,指数退避重试熬过它
        (受 video_mufra_budget 约束);永久错(鉴权/余额)立即放弃。预算内仍失败 → 降级原图 + 记 warning
        (显式降级,不静默——母帧才是 3D 物理的关键)。"""
        from .ai.openai_image import OpenAIImageClient
        try:
            framed = _mufra_with_backoff(lambda: OpenAIImageClient().edit(
                _frame_src, scene_frame_prompt(cat, lang, scene=scene, action=action),
                size=gptimage_size(aspect), timeout=settings.video_mufra_timeout,
                max_retries=0, use_gate=False))   # 并发由 _MUFRA_GATE 管,别叠 _API_GATE
            return fit_to_aspect(framed, tw, th)
        except Exception as exc:  # noqa: BLE001 — 母帧失败不阻断视频作业,但要让用户看见降级
            if not any(w.startswith("场景母帧") for w in warnings):   # 去重(per-shot 每段各调一次)
                warnings.append(
                    "场景母帧生成失败,已退回原始平铺商品图作首帧——成片的立体感/物理真实度会明显变差"
                    "(衣物可能像砖块般僵硬)。常见原因:作图 AI 网关繁忙(中转站 503 无可用账号)/ 超时 / 未配 key / 余额不足。"
                    f"详情:{str(exc)[:160]}")
            return None

    if use_scene:                           # 进入母帧阶段:我的空间显示"母帧合成中…"(长任务不像卡死)
        _set_stage(db, job, _STAGE_MUFRA)
    # 母帧在视频生成前完成。母帧失败(_scene_frame 返回 None)→ 退回原始商品图作首帧,由 CogVideoX 自己的
    # 首帧优化(img2video)让它动起来——【不出 GIF】(老大:GIF 根本不能用)。母帧失败仍记 warning 诚实告知。
    seg_imgs: list | None = None
    if per_shot_frames:                     # 多分镜每镜独立母帧:并行提交(全局自适应限流器排队+退避熬拥塞);失败的镜退回原图
        with ThreadPoolExecutor(max_workers=n_shots) as ex:  # 各镜母帧=该镜脚本(prompts[i])的第0帧 → Scene Init 对齐
            frames = list(ex.map(_scene_frame, scenes[:n_shots], prompts[:n_shots]))
        seg_imgs = [[f] if f is not None else imgs for f in frames]
    elif use_scene:                         # 单镜 / 双分镜未给场景:一张共享母帧(脚本=storyboard 的第0帧)
        shared = _scene_frame("", prompts[0])
        if shared is not None:
            imgs[0] = shared
    # 视频音效(默认关)= CogVideoX 自带音频(with_audio=true,AI 音效非真人);默认无声;旁白开 = 无声再叠真人 AI 旁白。三者:默认无声 / 音效 / 旁白互斥。
    native_sound = bool(p.get("native_sound", False))
    # 【已删 GIF 兜底】provider(CogVideoX)内部已重试(网络层 + 任务级重建 3 次)仍失败 → 异常【向上抛】→
    # run_job_in_worker 统一标 error + 按 refund_n(=params["n"])退点。不再降级出 GIF(老大:GIF 根本不能用)。
    _set_stage(db, job, _STAGE_VIDEO)       # 母帧阶段完 → 进视频生成(我的空间显示"视频生成中…")
    if p.get("two_shot"):
        # 多分镜 15s:单段模型最多 10s,故拆 N 段(当前 3×5s)【并行】生成,再首尾拼接=15s。母帧已生成好。
        from .services.video_concat import concat_videos
        prov = get_video_provider()
        if seg_imgs is None:                # 双分镜未给场景(非 per_shot):各镜共用 imgs(可能含共享母帧)
            seg_imgs = [imgs for _ in range(n_shots)]
        plan = [(seg_imgs[i], prompt_fn(prompts[i] or prompts[0], language=lang), MULTI_SHOT_PLAN[i])
                for i in range(n_shots)]

        def _seg(item: tuple) -> dict:
            shot_imgs, sp, sec = item
            return prov.image_to_video(shot_imgs, sp, size=size, seconds=sec, with_audio=native_sound)

        with ThreadPoolExecutor(max_workers=n_shots) as ex:
            segs = list(ex.map(_seg, plan))   # 保序;任一段抛错 → 向上抛 → 整单 error + 退点
        ext0 = segs[0].get("ext", "mp4")
        merged = concat_videos([s["bytes"] for s in segs], ext0, keep_audio=native_sound)
        out = {"bytes": merged, "ext": ext0,
               "meta": {**segs[-1].get("meta", {}), "two_shot": True, "shots": n_shots,
                        "shot_seconds": list(MULTI_SHOT_PLAN)}}
        total_seconds = sum(MULTI_SHOT_PLAN)
    else:
        prompt = prompt_fn(p.get("prompt", ""), language=lang)
        out = get_video_provider().image_to_video(imgs, prompt, size=size, seconds=p.get("seconds"),
                                                  with_audio=native_sound)
        total_seconds = int(p.get("seconds") or settings.video_seconds)
    # 后期节奏快切(默认开):按 beat 切段、全景/推近交替,治"呆板"。在旁白/字幕【之前】做 → 不裁字幕。
    # 不改时长/音轨/商品像素(一致性零风险)。【best-effort:失败必须回退原片,绝不阻断已生成好的视频】
    # (否则一个后期特效的 bug 会让整单 error+退点、用户白丢已出好的成片)。
    if settings.video_punchup and out.get("ext") == "mp4":
        try:
            from .services.video_edit import punch_up
            out["bytes"] = punch_up(out["bytes"])
        except Exception as exc:  # noqa: BLE001 — 后期特效失败 → 保留原片,不阻断交付
            warnings.append(f"节奏快切(后期)失败,已输出未切镜的原片。详情:{str(exc)[:160]}")
    # 旁白配音(best-effort):仅「旁白设置」开 + 真 mp4(非本地兜底 GIF)才做。看图写目标语言口播稿 → edge-tts → 叠回。
    # 失败/无网/无 key 一律保留原视频,绝不阻断视频作业(CogVideoX 只产音效不产语音,语音靠这条补)。
    if p.get("voiceover") and out.get("ext") == "mp4":
        try:
            from .services.voiceover import add_voiceover
            new_bytes, script = add_voiceover(out["bytes"], raw[0], p.get("prompt", ""),
                                              lang, total_seconds,
                                              subtitle=bool(p.get("subtitle", True)))
            out["bytes"] = new_bytes
            if script:
                out.setdefault("meta", {})["voiceover"] = script[:200]
        except Exception as exc:  # noqa: BLE001 — 配音不阻断视频作业,但显式记录降级(不再静默无声)
            warnings.append(f"旁白配音失败,已输出无声视频。详情:{str(exc)[:160]}")
    # 背景音乐床:暂时停用(已注释)。基建保留在 services/video_edit.py(pick_music/add_music_bed)+
    # backend/assets/music/,需要时取消下面注释 + 放 CC0 曲子 + 开 POD_VIDEO_MUSIC 即可恢复。
    # 现阶段先聚焦"保证生成效果",不叠音乐层。
    # if settings.video_music and out.get("ext") == "mp4":
    #     from .services.video_edit import add_music_bed, pick_music
    #     track = pick_music()
    #     if track:
    #         out["bytes"] = add_music_bed(out["bytes"], track)
    ext = out.get("ext", "mp4")
    name = f"video.{ext}"
    storage.output_path(job_id, name).write_bytes(out["bytes"])
    url = storage.output_url(job_id, name)
    meta = out.get("meta", {})
    # 封面/缩略图 = 用户上传的【商品图(第一张)】:干净、稳定、所见即所得(卡片显示用户当初传的那张)。
    # 不用 CogVideoX 的 cover_image_url(带水印 + 签名外链会过期),也不用视频抽帧(有贴边/模糊)。
    cover = ""
    try:
        thumb = raw[0].convert("RGB")
        thumb.thumbnail((640, 640))
        thumb.save(storage.output_path(job_id, "cover.jpg"), format="JPEG", quality=85)
        cover = storage.output_url(job_id, "cover.jpg")
    except Exception:  # noqa: BLE001 — 缩略图失败不影响视频交付
        cover = meta.get("cover", "")
    # 注:已删 GIF 兜底 → provider 真失败直接抛错(run_job_in_worker 标 error + 退点),不再在此降级退点。
    if job.owner_id is not None:  # 入库(用首帧做查重源,size 用视频字节)→ 删任务时可进回收站,不再成幽灵
        save_as_asset(db, job.owner_id, imgs[0], f"图生视频 {cat}", url, source="generated",
                      size_bytes=len(out["bytes"]))
    return {"video_url": url, "ext": ext, "voiceover": meta.get("voiceover", ""),
            "cover": cover, "engine": meta.get("engine", ""),
            "two_shot": bool(meta.get("two_shot")),
            "degraded": bool(meta.get("degraded")),   # local provider(离线/无 key)出 GIF 时为 True;真 provider 出片为 False
            # 并行母帧多线程 append warnings 可能产生重复条目(良性竞态)→ 去重(保序),前端在成片卡片上提示
            "warnings": list(dict.fromkeys(warnings))}   # 显式暴露降级原因(母帧失败/配音失败等)


def _work_viduvideo(job_id: str, job: Job, db: Session) -> dict:
    """AI 图生视频(Vidu viduq2-pro-fast / 本地兜底 GIF)—— 第二套引擎,与 CogVideoX 并存。
    定位:单张商品图 →[场景母帧] 真人在生活场景里使用/把玩商品 → img2video 让它动起来。
    场景母帧:gpt-image 把商品合成进"真人正在使用它"的场景做首帧(无 key/失败自动降级回原图,不阻断)。"""
    from .ai.vidu import (
        _aspect_px,
        clamp_seconds,
        compose_vidu_prompt,
        fit_to_aspect,
        get_vidu_provider,
    )
    from .config import settings
    p = job.params
    cat = p.get("category", "通用")
    aspect = p.get("aspect", "portrait")
    resolution = p.get("resolution", "720p")
    seconds = clamp_seconds(p.get("seconds") or 5)
    lang = p.get("language", "葡萄牙语")
    sound_mode = p.get("sound_mode", "none")
    native_audio = sound_mode == "sfx"                # 原生音效 → Vidu 出声(audio=true);none=无声
    # ⚠ 官方:audio=true 不指定 audio_type 默认 "All"=音效+人声(且只支持中/英) → 会给画面人配上中/英语音、与巴西场景人对不上。
    # 故原生音效强制 "Sound-effect_only"=【只出环境/动作音效、绝不配人声】。葡/西/英/中口播只走 edge-tts 真人旁白。
    audio_type = "Sound-effect_only" if native_audio else ""
    use_scene = bool(p.get("scene_frame") and settings.openai_api_key)   # 场景母帧需作图网关 key
    raw = _load_input(job_id)
    tw, th = _aspect_px(aspect)

    warnings: list[str] = []
    # 场景母帧:gpt-image 把商品合成进"真人正在使用它"的场景做视频首帧 → 缓解"白底图突然长出人/场景"的硬切。
    # 母帧失败 → 退回原始商品图作首帧,由 Vidu 自己的 img2video 让它动起来(【不出 GIF】);失败记 warning 诚实告知。
    first = fit_to_aspect(raw, tw, th)
    if use_scene:
        _set_stage(db, job, _STAGE_MUFRA)   # 进入母帧阶段:我的空间显示"母帧合成中…"
        from .ai.vidu import gptimage_size, scene_frame_prompt
        src = raw
        if max(src.size) > 1024:
            src = src.copy(); src.thumbnail((1024, 1024), Image.LANCZOS)
        from .ai.openai_image import OpenAIImageClient
        # 走 _mufra_with_backoff:全局自适应限流器排队(等位不计预算)+ 拿位后退避重试熬中转站瞬时拥塞(503/超时);
        # 永久错立即放弃。对齐 CogVideoX 母帧策略。
        try:
            framed = _mufra_with_backoff(lambda: OpenAIImageClient().edit(
                src, scene_frame_prompt(lang, scene=p.get("scene", ""), action=p.get("prompt", "")),
                size=gptimage_size(aspect), timeout=settings.video_mufra_timeout,
                max_retries=0, use_gate=False))   # 并发由 _mufra_with_backoff 的 acquire/report 管,别叠
            first = fit_to_aspect(framed, tw, th)
        except Exception as exc:  # noqa: BLE001 — 母帧失败不阻断,退回原图首帧(喂 Vidu 自己动)+ 告知
            warnings.append("场景母帧生成失败,已退回原始商品图作首帧——成片少了真人使用场景。"
                            f"常见:作图 AI 网关繁忙(中转站 503 无可用账号)/超时/未配 key/余额不足。详情:{str(exc)[:160]}")
    prompt = compose_vidu_prompt(p.get("prompt", ""), language=lang, seconds=seconds, sound_mode=sound_mode)
    _set_stage(db, job, _STAGE_VIDEO)   # 母帧(若有)完 → 进视频生成

    # 【已删 GIF 兜底】provider(Vidu)内部已重试仍失败 → 异常【向上抛】→ run_job_in_worker 标 error + 按 n 退点。
    out = get_vidu_provider().image_to_video(
        [first], prompt, aspect=aspect, resolution=resolution, seconds=seconds,
        audio=native_audio, audio_type=audio_type)
    # 真人旁白(best-effort):仅 voiceover 模式 + 真 mp4 才做。Vidu 原生音频对葡/西语支持差,口播走 edge-tts(免费、按市场语言、可烧字幕)。
    if sound_mode == "voiceover" and out.get("ext") == "mp4":
        try:
            from .services.voiceover import add_voiceover
            vo_lang = p.get("vo_lang") or lang   # 旁白语言(独立于场景地区);留空回退场景地区语言
            new_bytes, script = add_voiceover(out["bytes"], raw, p.get("prompt", ""), vo_lang, seconds,
                                              subtitle=bool(p.get("subtitle", True)))
            out["bytes"] = new_bytes
            if script:
                out.setdefault("meta", {})["voiceover"] = script[:200]
        except Exception as exc:  # noqa: BLE001 — 配音不阻断,显式记录降级
            warnings.append(f"旁白配音失败,已输出无声视频。详情:{str(exc)[:160]}")
    ext = out.get("ext", "mp4")
    name = f"video.{ext}"
    storage.output_path(job_id, name).write_bytes(out["bytes"])
    url = storage.output_url(job_id, name)
    meta = out.get("meta", {})
    # 封面 = 用户上传的商品图(干净稳定、所见即所得),不用 Vidu 外链 cover(签名会过期)
    cover = ""
    try:
        thumb = raw.convert("RGB"); thumb.thumbnail((640, 640))
        thumb.save(storage.output_path(job_id, "cover.jpg"), format="JPEG", quality=85)
        cover = storage.output_url(job_id, "cover.jpg")
    except Exception:  # noqa: BLE001
        cover = meta.get("cover", "")
    # 注:已删 GIF 兜底 → provider 真失败直接抛错(run_job_in_worker 标 error + 退点),不再在此降级退点。
    if job.owner_id is not None:
        save_as_asset(db, job.owner_id, raw, f"Vidu 图生视频 {cat}", url, source="generated",
                      size_bytes=len(out["bytes"]))
    return {"video_url": url, "ext": ext,
            "cover": cover, "engine": meta.get("engine", ""),
            "degraded": bool(meta.get("degraded")),   # local provider(离线/无 key)出 GIF 时为 True
            "warnings": list(dict.fromkeys(warnings))}


# kind → work(worker 函数)。【计费/退点 op 与笔数不在这里】——单一真相源在 app/tool_specs.py:TOOL_BILLING,
# tasks(正常失败退点)与 jobs.reaper(僵尸回收退点)共读它,杜绝两处漂移(历史上 reaper 漏登记 viduvideo/matting 致静默退错点)。
# 加新工具:这里注册 work + tool_specs.TOOL_BILLING 加一行(op/笔数只此一处)。
TOOL_WORKS: dict[str, Work] = {
    "generate": _work_generate,
    "edit": _work_edit,
    "variants": _work_variants,
    "restyle": _work_restyle,
    "meme": _work_meme,
    "upscale": _work_upscale,
    "dewatermark": _work_gptedit,
    "imgreplace": _work_gptedit,   # 图文替换:gpt-image 按需求改图(替换文字语言/改颜色/换元素等)
    "vectorize": _work_vectorize,
    "mockup": _work_mockup,
    "mockup-replace": _work_mockup_replace,
    "production": _work_production,
    "matting": _work_matting,
    "title": _work_title,          # 退点在 worker 内按 degrade 处理(tool_specs 标 worker_self_refunds)
    "ipguard": _work_ipguard,
    "collect_sync": _work_sync,    # 采集同步:免费(tool_specs op=None)
    "aivideo": _work_aivideo,      # 智谱 CogVideoX-3 / 本地兜底
    "viduvideo": _work_viduvideo,  # Vidu / 本地兜底
}


@celery_app.task(name="podsys.run_tool")
def run_tool(job_id: str) -> None:
    """通用工具作业:按 job.kind 分派到 TOOL_WORKS 的 work,失败按 tool_specs 的 op/笔数退点。"""
    from .tool_specs import billing_n_for, worker_refund_op_for
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        work = TOOL_WORKS.get(job.kind)
        if work is None:
            job.status = "error"
            job.error = f"未注册的作业类型: {job.kind}"
            job.finished_at = _now()
            db.commit()
            return
        op = worker_refund_op_for(job.kind, job.params)   # worker_self_refunds 的(title)返回 None,防双退
        n = billing_n_for(job.kind, job.params)
    finally:
        db.close()
    run_job_in_worker(job_id, work, refund_op=op, refund_n=n)
