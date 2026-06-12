"""套图「印花套用」引擎:把用户上传的设计套到产品照上。

两条路(智能=AI;快速=本地):
- **AI 多图合成(智能,有 key)**:把 [产品照, 新设计] 一起送 gpt-image-2 edit(input_fidelity=high),
  让模型把设计**真实地印到产品上**——跟随瓶身曲面/褶皱/光影、并能**替换原有印花**。复杂底图/曲面/
  需替换原印花的场景用它。代价:会重渲染产品(可能与原照不完全像素一致),但出的是可上架的真实感套图。
- **本地几何(快速,无 key/AI 失败)**:**只做「干净叠印」——绝不擦除产品原有图案**(实测擦除+重绘
  对真实照片效果很差、还会把干净产品洗白)。流程:抠出产品本体→在合适位置(服饰胸口 / 杯瓶中部)
  把设计**等比叠上**,乘产品自身明暗场(跟随曲面/光影)、裁进产品轮廓(不溢出背景)。
  **适合纯色/干净的服饰、杯子、瓶子等**;若产品本身已有印花,本地不去除它——这类请走「智能(AI)」。
  工作分辨率封顶 1600px,纯 numpy/scipy,12 核高并发也轻。
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

from ..config import settings
from .design_extract import _ANALYZE, _product_mask

log = logging.getLogger(__name__)

# 本地法工作分辨率上限(只缩不放):2.5MP 左右,卷积都很轻,12 核机器高并发也扛得住;
# 电商套图预览 ~1600px 足够,与 AI 路径输出尺寸(1024~1536)同量级。
_MAX_SIDE = 1600

# 套图替换 prompt:把第二张设计真实地印到第一张产品上,替换原印花,保留产品/背景。
_MOCKUP_PROMPT = (
    "The FIRST image is a product photo. The SECOND image is a flat design/print. "
    "STEP 1: completely ERASE and paint over EVERY existing printed text, logo and graphic on the "
    "product (remove all old letters/artwork), leaving a clean blank product surface with its original "
    "color and material. "
    "STEP 2: place the design from the SECOND image onto that area, rendered SOLID and FULLY OPAQUE so "
    "NO old text shows through and it is not translucent; make it look realistically printed, following "
    "the product's shape, curvature and lighting. "
    "Keep the product color, background, cap, handle and strap unchanged; keep the design's exact "
    "artwork, colors and proportions. Photorealistic e-commerce product mockup."
)


def _pick_size(product: Image.Image) -> str:
    """按产品照长宽比挑 gpt-image 输出尺寸(竖瓶→竖版,宽图→横版,否则方形)。"""
    w, h = product.size
    if h >= w * 1.2:
        return "1024x1536"
    if w >= h * 1.2:
        return "1536x1024"
    return "1024x1024"


def _replace_ai(product: Image.Image, new_print: Image.Image) -> Image.Image:
    """AI 多图合成:把新设计真实地印到产品上。无 key→OpenAIImageClient() 构造即抛。"""
    from ..ai.openai_image import OpenAIImageClient
    out = OpenAIImageClient().compose(
        [product.convert("RGBA"), new_print.convert("RGBA")],
        _MOCKUP_PROMPT, size=_pick_size(product), input_fidelity="high")
    return out.convert("RGB")


def _product_body(product: Image.Image) -> tuple[np.ndarray | None, str]:
    """抠出产品本体:返回 (全分辨率布尔 mask, kind)。kind ∈ garment/product;抠不到→(None,'none')。

    只要本体轮廓(给定位+裁切用),**不做任何印花检测/擦除**——本地引擎不去碰产品原有图案。
    """
    full = product.convert("RGB")
    W, H = full.size
    small = full.copy()
    small.thumbnail((_ANALYZE, _ANALYZE))
    mask, kind = _product_mask(small)
    if mask is None:
        return None, "none"
    mask_full = np.asarray(
        Image.fromarray((mask * 255).astype("uint8")).resize((W, H), Image.NEAREST)) > 0
    return mask_full, kind


def _prep_print(p: Image.Image) -> Image.Image:
    """确保新印花有可用 alpha:本身带透明→直接用;整体不透明(实底)→键掉与四角一致、连到边缘的纯色背景。

    避免把"黑底/白底的设计图"当成不透明矩形整块贴上产品(那会糊一大块底色)。
    """
    p = p.convert("RGBA")
    arr = np.asarray(p).astype(np.uint8)
    a = arr[..., 3]
    if (a < 250).mean() > 0.02:          # 已有 ≥2% 透明像素 → 认为自带抠好的背景,直接用
        return p
    rgb = arr[..., :3].astype(int)
    h, w = rgb.shape[:2]
    s = max(2, min(h, w) // 20)
    corners = np.concatenate([
        rgb[:s, :s].reshape(-1, 3), rgb[:s, -s:].reshape(-1, 3),
        rgb[-s:, :s].reshape(-1, 3), rgb[-s:, -s:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    if np.median(np.abs(corners - bg).sum(1)) > 60:   # 四角颜色不一致 → 本就没纯色背景,不键
        return p
    bgmask = np.abs(rgb - bg).sum(2) < 45
    lbl, _n = ndimage.label(bgmask)
    edge = set(np.unique(np.concatenate([lbl[0], lbl[-1], lbl[:, 0], lbl[:, -1]])))
    edge.discard(0)
    keyed = np.isin(lbl, list(edge))      # 只键连到图像边缘的背景(保留主体内部同色块)
    if not (0.02 < keyed.mean() < 0.97):  # 没键出多少 / 几乎全键(纯色块)→ 放弃,保持原样
        return p
    soft = ndimage.gaussian_filter(keyed.astype(np.float32), 1.0)
    out = arr.copy()
    out[..., 3] = (a * (1 - soft)).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _fit_contain(img: Image.Image, bw: int, bh: int) -> tuple[Image.Image, int, int]:
    """把 img 等比缩放到 ≤(bw,bh),返回 (缩放后 RGBA, 在 bbox 内居中的 ox, oy)。"""
    img = img.convert("RGBA")
    iw, ih = img.size
    s = min(bw / iw, bh / ih)
    nw, nh = max(1, int(iw * s)), max(1, int(ih * s))
    fitted = img.resize((nw, nh), Image.LANCZOS)
    return fitted, (bw - nw) // 2, (bh - nh) // 2


def replace_print(product: Image.Image, new_print: Image.Image, prefer_local: bool = False) -> Image.Image:
    """套图替换入口:智能=AI 多图合成(真实感);快速/无 key/AI 失败=几何贴合。"""
    if not prefer_local and settings.openai_api_key:
        try:
            return _replace_ai(product, new_print)
        except Exception as exc:  # noqa: BLE001 — AI 失败降级几何,不阻断出图
            log.warning("AI 套图替换失败,降级几何法: %s", exc)
    return _replace_local(product, new_print)


def _placement(mask: np.ndarray | None, kind: str, W: int, H: int) -> tuple[int, int, int, int]:
    """选叠印区域(不擦除,只决定设计贴哪、贴多大)。

    服饰→胸口(水平居中、领口下方),区域偏大;杯/瓶等产品→本体中部偏大;抠不到本体→整图居中。
    """
    if mask is None or not mask.any():
        return int(W * 0.26), int(H * 0.24), int(W * 0.74), int(H * 0.72)
    ys, xs = np.where(mask)
    gl, gt, gr, gb = int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)
    gw, gh = gr - gl, gb - gt
    cx = gl + gw // 2
    if kind == "garment":           # 胸口:领口下方起,宽约本体 56%
        bw = int(gw * 0.56)
        top = gt + int(gh * 0.20)
        bh = int(gh * 0.46)
    else:                            # 杯/瓶/其它产品:本体中部,宽约 52%
        bw = int(gw * 0.52)
        cy = gt + int(gh * 0.50)
        bh = int(gh * 0.46)
        top = cy - bh // 2
    return cx - bw // 2, top, cx + bw // 2, top + bh


def _replace_local(product: Image.Image, new_print: Image.Image) -> Image.Image:
    """本地「干净叠印」:抠出产品本体→在合适位置把设计等比叠上,乘产品自身明暗场(跟随曲面/光影)、
    裁进产品轮廓。**绝不擦除产品原有图案**——擦除+重绘对真实照片效果差、还会洗白干净产品。

    适合纯色/干净的服饰、杯、瓶;产品本身已有印花时本地不去除(请走「智能 AI」)。
    """
    full = product.convert("RGB")
    if max(full.size) > _MAX_SIDE:        # 只缩不放,封顶工作分辨率
        full = ImageOps.contain(full, (_MAX_SIDE, _MAX_SIDE))
    W, H = full.size
    mask, kind = _product_body(full)
    arr = np.asarray(full).astype(np.float32)

    left, t, r, b = _placement(mask, kind, W, H)
    bw, bh = r - left, b - t
    if bw < 8 or bh < 8:
        return full

    # ① 产品自身明暗场(曲面/光影)= 亮度 / 叠印区中位亮度,乘到设计上 → 看起来"印在"产品上
    lum = arr @ np.array([0.299, 0.587, 0.114], np.float32)
    med = float(np.median(lum[t:b, left:r])) or 1.0
    shade = np.clip(lum / med, 0.62, 1.28)

    # ② 设计:键掉实底背景 → 裁到内容(去透明边距,免得缩成小图)→ 等比进叠印区居中
    prepped = _prep_print(new_print)
    ab = prepped.getchannel("A").getbbox()
    if ab:
        prepped = prepped.crop(ab)
    fitted, ox, oy = _fit_contain(prepped, bw, bh)
    fw, fh = fitted.size
    y0, x0 = t + oy, left + ox
    na = np.asarray(fitted).astype(np.float32)
    a_new = na[..., 3:] / 255.0
    sub = shade[y0:y0 + fh, x0:x0 + fw]
    if sub.shape[:2] != (fh, fw):         # 边界对齐兜底
        sub = np.ones((fh, fw), np.float32)
    rgb_new = na[..., :3] * sub[..., None]

    # ③ 裁进产品轮廓:设计 alpha 与产品本体相交,绝不溢出到背景(抠不到本体则不裁)
    if mask is not None and mask.any():
        body = mask[y0:y0 + fh, x0:x0 + fw][..., None].astype(np.float32)
        if body.shape[:2] == (fh, fw):
            a_new = a_new * body

    # ④ 合成到原图(产品像素原样保留,只在设计处叠加)
    out = arr.copy()
    dst = out[y0:y0 + fh, x0:x0 + fw, :]
    out[y0:y0 + fh, x0:x0 + fw, :] = dst * (1 - a_new) + rgb_new * a_new
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")
