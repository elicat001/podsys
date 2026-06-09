"""套图模板「印花替换」引擎:把产品照里的原印花换成用户上传的新印花。

两条路(默认 AI,无 key/失败降级几何):
- **AI 多图合成(默认,有 key)**:把 [产品照, 新设计] 一起送 gpt-image-2 edit(input_fidelity=high),
  让模型把设计**真实地印到产品上**——跟随瓶身曲面/褶皱/光影、替换原印花。实测效果远好于几何法
  (几何平贴搞不定瓶子这种曲面 + 旧印花擦不干净)。代价:模型会**重渲染产品**(可能与原照不完全像素一致),
  但出的是可直接上架的真实感套图,符合商品套图诉求。
- **几何兜底(无 key/AI 失败)**:检测原印花 bbox→抹除→新印花等比贴入→原图明暗 multiply。平贴尚可,
  曲面差;检测不到产品→居中贴。仅作离线降级。
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image
from scipy import ndimage

from ..config import settings
from .design_extract import _ANALYZE, _flatten_illumination, _print_alpha, _product_mask

log = logging.getLogger(__name__)

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


def _detect_region(product: Image.Image) -> dict | None:
    """定位原印花:返回 {bbox:(l,t,r,b) 全分辨率, mask: 全分辨率布尔(印花处=True)}。检测不到→None。"""
    full = product.convert("RGB")
    W, H = full.size
    small = full.copy()
    small.thumbnail((_ANALYZE, _ANALYZE))
    sw, sh = small.size
    mask, kind = _product_mask(small)
    if mask is None:
        return None
    orig = np.asarray(small).astype(int)
    rgb = orig.astype(float)
    if kind == "garment":
        rgb = _flatten_illumination(rgb, mask)
    alpha = _print_alpha(rgb.astype(int), mask, kind)
    region = alpha >= 90
    if not region.any():
        return None
    ys, xs = np.where(region)
    sx, sy = W / sw, H / sh
    bbox = (int(xs.min() * sx), int(ys.min() * sy),
            int((xs.max() + 1) * sx), int((ys.max() + 1) * sy))
    mask_full = np.asarray(
        Image.fromarray((region * 255).astype("uint8")).resize((W, H), Image.NEAREST)) > 0
    return {"bbox": bbox, "mask": mask_full}


def _fabric_color(arr: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    """估周围布料色:bbox 外扩一圈、减去 bbox 的『环』里取中位色。"""
    h, w = arr.shape[:2]
    l, t, r, b = bbox
    m = max(8, (r - l) // 4, (b - t) // 4)
    ol, ot, orr, ob = max(0, l - m), max(0, t - m), min(w, r + m), min(h, b + m)
    ring = np.ones((h, w), bool)
    ring[:ot] = False; ring[ob:] = False; ring[:, :ol] = False; ring[:, orr:] = False
    ring[t:b, l:r] = False  # 挖掉 bbox 本身
    if ring.sum() < 20:
        ring = np.ones((h, w), bool); ring[t:b, l:r] = False
    return np.median(arr[ring], axis=0)


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


def _replace_local(product: Image.Image, new_print: Image.Image) -> Image.Image:
    """几何兜底:检测原印花区→抹除→新印花等比贴入→原图明暗 multiply。检测不到→居中贴。"""
    full = product.convert("RGB")
    W, H = full.size
    reg = _detect_region(full)
    arr = np.asarray(full).astype(np.float32)

    if reg is None:  # 兜底:没检测到产品/印花 → 把新印花居中贴(占 ~55% 宽)
        bbox = (int(W * 0.22), int(H * 0.22), int(W * 0.78), int(H * 0.78))
        pmask = np.zeros((H, W), bool)
    else:
        bbox, pmask = reg["bbox"], reg["mask"]

    l, t, r, b = bbox
    bw, bh = r - l, b - t
    if bw < 4 or bh < 4:
        return full

    # ① 抹掉原印花:mask 处填布料色(羽化),其余保留
    out = arr.copy()
    fabric = _fabric_color(arr, bbox)
    if pmask.any():
        soft = ndimage.gaussian_filter(pmask.astype(np.float32), 2.0)[..., None]
        out = out * (1 - soft) + fabric[None, None, :] * soft

    # ② 该 bbox 的明暗系数(亮度/中位亮度)= 折叠/阴影,后面 multiply 到新印花
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    med = float(np.median(lum[t:b, l:r])) or 1.0
    shade = np.clip(lum[t:b, l:r] / med, 0.5, 1.35)

    # ③ 新印花等比进 bbox,居中
    fitted, ox, oy = _fit_contain(new_print, bw, bh)
    fw, fh = fitted.size
    na = np.asarray(fitted).astype(np.float32)
    sub = shade[oy:oy + fh, ox:ox + fw]
    if sub.shape[:2] != (fh, fw):  # 边界对齐兜底
        sub = np.ones((fh, fw), np.float32)
    rgb_new = na[..., :3] * sub[..., None]
    a_new = (na[..., 3:] / 255.0)

    # ④ 合成到 out 的 (t+oy, l+ox)
    y0, x0 = t + oy, l + ox
    dst = out[y0:y0 + fh, x0:x0 + fw, :]
    out[y0:y0 + fh, x0:x0 + fw, :] = dst * (1 - a_new) + rgb_new * a_new
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")
