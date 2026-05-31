"""工作流编排引擎 —— 把单个工具串成「一键闭环」(对标灵图 首页工作流)。

核心:一个步骤(step)= 一个对 ctx(上下文)做变换的纯函数;一个工作流(workflow)
= 一串 step 名。runner 顺序执行,产物 URL 累积进 ctx['outputs']。
新工具只要注册成 step,就能被任意工作流编排复用 —— 这是平台的「抓手」。

设计为离线可跑:所有内置 step 只依赖本地服务(extract/split/mockup/export);
依赖外部 AI(gpt-image/文本)的 step 在无 key 时优雅降级并在 meta 里标注 skipped。
"""
from __future__ import annotations
from typing import Callable
from PIL import Image
from . import extract, mockup, export, split
from .. import storage

# step: ctx(dict) -> None,就地更新 ctx。ctx 关键字段:
#   image(当前 PIL)/ job_id / outputs(list[url])/ params(dict)/ meta(dict)/ trace(list)
StepFn = Callable[[dict], None]
STEP_REGISTRY: dict[str, StepFn] = {}


def step(name: str):
    def deco(fn: StepFn) -> StepFn:
        STEP_REGISTRY[name] = fn
        return fn
    return deco


def _save(ctx: dict, name: str, img: Image.Image) -> str:
    img.save(storage.output_path(ctx["job_id"], name), format="PNG")
    url = storage.output_url(ctx["job_id"], name)
    ctx["outputs"].append(url)
    return url


# ---------------- 内置步骤 ----------------
@step("extract")  # 印花提取(抠图 + 裁剪 + 可选放大)
def _extract(ctx: dict) -> None:
    p = extract.extract_print(ctx["image"], upscale=float(ctx["params"].get("upscale", 1.0)))
    ctx["image"] = p
    _save(ctx, "extract.png", p)


@step("split")  # 多联画 / 结构性裂变
def _split(ctx: dict) -> None:
    parts = split.split_panels(
        ctx["image"],
        mode=ctx["params"].get("split_mode", "grid"),
        panels=int(ctx["params"].get("panels", 3)),
        rows=int(ctx["params"].get("rows", 2)),
        cols=int(ctx["params"].get("cols", 2)),
    )
    for i, pt in enumerate(parts):
        _save(ctx, f"panel_{i+1}.png", pt)


@step("mockup")  # 商品套图(可批量多模板)
def _mockup(ctx: dict) -> None:
    for tid in ctx["params"].get("templates", ["tshirt"]):
        _save(ctx, f"mockup_{tid}.png", mockup.render_mockup(ctx["image"], tid))


@step("production")  # 履约生产图
def _production(ctx: dict) -> None:
    meta = export.export_production(
        ctx["image"], storage.output_path(ctx["job_id"], "production.png"),
        width_cm=float(ctx["params"].get("width_cm", 30.0)),
        height_cm=float(ctx["params"].get("height_cm", 40.0)),
        dpi=int(ctx["params"].get("dpi", 300)),
    )
    ctx["outputs"].append(storage.output_url(ctx["job_id"], "production.png"))
    ctx["meta"]["production"] = meta


@step("title")  # 标题提取(委托 studio_tools;无 key 自动降级为占位,不报错)
def _title(ctx: dict) -> None:
    from .studio_tools import generate_title
    res = generate_title(keywords=ctx["params"].get("keywords", ""),
                         category=ctx["params"].get("category", "apparel"))
    ctx["meta"]["title"] = res["title"]
    ctx["meta"]["title_keywords"] = res["keywords"]
    if res.get("degraded"):
        ctx["meta"].setdefault("skipped", []).append("title(no openai key, placeholder)")


@step("variants")  # 图裂变(需 gpt-image;无 key 优雅跳过,不让整条工作流失败)
def _variants(ctx: dict) -> None:
    from .design_tools import make_variants
    try:
        imgs = make_variants(ctx["image"], int(ctx["params"].get("variant_n", 3)))
    except RuntimeError as exc:  # 无 OpenAI key → 优雅跳过(真实 bug 不在此吞,留给 runner 暴露)
        ctx["meta"].setdefault("skipped", []).append(f"variants(no key: {exc})")
        return
    for i, im in enumerate(imgs):
        _save(ctx, f"variant_{i+1}.png", im)


@step("seamless")  # 四方连续图(离线,服饰家纺连续印花)
def _seamless(ctx: dict) -> None:
    from .seamless import seamless_pattern
    try:
        out = seamless_pattern(ctx["image"], repeat=int(ctx["params"].get("repeat", 2)))
    except ValueError as exc:
        ctx["meta"].setdefault("skipped", []).append(f"seamless({exc})")
        return
    ctx["image"] = out
    _save(ctx, "seamless.png", out)


@step("compress")  # 裁剪压缩(离线,导出前归一化尺寸/体积/格式)
def _compress(ctx: dict) -> None:
    from .image_tools import compress_image
    try:
        final, encoded, info = compress_image(
            ctx["image"],
            target_w=int(ctx["params"].get("target_w", 0)),
            target_h=int(ctx["params"].get("target_h", 0)),
            quality=int(ctx["params"].get("quality", 85)),
            fmt=ctx["params"].get("fmt", "jpeg"),
        )
    except ValueError as exc:  # 非法 fmt/尺寸越界 → 跳过,不让整条工作流 500
        ctx["meta"].setdefault("skipped", []).append(f"compress({exc})")
        return
    fmt = info.get("format", "jpeg")
    name = f"compressed.{fmt}"
    storage.output_path(ctx["job_id"], name).write_bytes(encoded)
    ctx["outputs"].append(storage.output_url(ctx["job_id"], name))
    ctx["meta"]["compress"] = info


# ---------------- 预设工作流(对标灵图首页卡片) ----------------
WORKFLOWS: dict[str, dict] = {
    "phone-remake": {
        "label": "手机壳-爆款二创",
        "desc": "输入图 → 印花提取 → 图裂变 → 商品套图 → 标题提取",
        "steps": ["extract", "split", "mockup", "title"],
        "defaults": {"templates": ["phonecase"], "split_mode": "grid"},
    },
    "tee-extract-mockup": {
        "label": "T恤-提取套图生产",
        "desc": "输入图 → 印花提取 → 商品套图 → 生产图 → 标题",
        "steps": ["extract", "mockup", "production", "title"],
        "defaults": {"templates": ["tshirt", "tote"]},
    },
    "canvas-series": {
        "label": "装饰画-多联系列",
        "desc": "输入图 → 印花提取 → 多联裂变 → 套图",
        "steps": ["extract", "split", "mockup"],
        "defaults": {"templates": ["canvas"], "split_mode": "horizontal", "panels": 3},
    },
    "tee-full": {
        "label": "T恤-全链路(提取·裂变·套图·压缩·生产·标题)",
        "desc": "输入图 → 提取 → 图裂变(需AI,无key跳过)→ 套图 → 压缩 → 生产图 → 标题",
        "steps": ["extract", "variants", "mockup", "compress", "production", "title"],
        "defaults": {"templates": ["tshirt"], "variant_n": 3, "fmt": "jpeg", "target_w": 1200},
    },
}


def list_workflows() -> list[dict]:
    return [{"id": k, "label": v["label"], "desc": v["desc"], "steps": v["steps"]}
            for k, v in WORKFLOWS.items()]


def run_workflow(image: Image.Image, workflow_id: str, job_id: str,
                 params: dict | None = None) -> dict:
    wf = WORKFLOWS.get(workflow_id)
    if wf is None:
        raise ValueError(f"unknown workflow: {workflow_id} (have {list(WORKFLOWS)})")
    ctx = {
        "image": image, "job_id": job_id, "outputs": [], "meta": {}, "trace": [],
        "params": {**wf.get("defaults", {}), **(params or {})},
    }
    for s in wf["steps"]:
        fn = STEP_REGISTRY.get(s)
        if fn is None:
            ctx["meta"].setdefault("skipped", []).append(f"{s}(unknown step)")
            continue
        fn(ctx)
        ctx["trace"].append(s)
    return {"workflow": workflow_id, "steps_run": ctx["trace"],
            "outputs": ctx["outputs"], "meta": ctx["meta"]}
