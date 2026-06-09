"""套图模板「印花替换」引擎:把产品照里的原印花换成用户上传的新印花。

两条路(默认 AI,无 key/失败降级几何):
- **AI 多图合成(默认,有 key)**:把 [产品照, 新设计] 一起送 gpt-image-2 edit(input_fidelity=high),
  让模型把设计**真实地印到产品上**——跟随瓶身曲面/褶皱/光影、替换原印花。实测效果远好于几何法
  (几何平贴搞不定瓶子这种曲面 + 旧印花擦不干净)。代价:模型会**重渲染产品**(可能与原照不完全像素一致),
  但出的是可直接上架的真实感套图,符合商品套图诉求。
- **几何兜底(无 key/AI 失败/快速运行)**:检测原印花区→EDT 最近邻抹除得带褶皱的干净底布→新印花
  等比贴入,乘**底布**明暗场(跟随褶皱,不被旧印花污染)+ 接触阴影。工作分辨率封顶 1600px。
  平贴(正面 T 恤等)效果好;曲面(瓶身)一般;检测不到产品→居中贴。离线/快速路径用。
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

from ..config import settings
from .design_extract import _ANALYZE, _flatten_illumination, _print_alpha, _product_mask

log = logging.getLogger(__name__)

# 几何法工作分辨率上限(只缩不放):2.5MP 左右,EDT/卷积都很轻,12 核机器高并发也扛得住;
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


def _detect_region(product: Image.Image) -> dict | None:
    """定位原印花:返回 {bbox:(l,t,r,b), mask: 印花处=True, garment: 产品本体=True}(均全分辨率)。检测不到→None。"""
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
    # 较低阈值 + 闭运算 + 填洞:尽量完整覆盖印花本体(含元素间空隙),避免抹除后仍有残影;
    # 但只取"与主材质色有差异"的像素,故产品本体(纯色瓶身/布料)不会被整体当成印花。
    region = alpha >= 60
    region = ndimage.binary_closing(region, structure=np.ones((3, 3), bool), iterations=2)
    region = ndimage.binary_fill_holes(region)
    if not region.any():
        return None
    ys, xs = np.where(region)
    sx, sy = W / sw, H / sh
    bbox = (int(xs.min() * sx), int(ys.min() * sy),
            int((xs.max() + 1) * sx), int((ys.max() + 1) * sy))

    def _up(small_mask: np.ndarray) -> np.ndarray:
        return np.asarray(Image.fromarray((small_mask * 255).astype("uint8")).resize(
            (W, H), Image.NEAREST)) > 0

    return {"bbox": bbox, "mask": _up(region), "garment": _up(mask)}


def _inpaint_fabric(arr: np.ndarray, holes: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    """抹掉旧印花:用最近的**有效布料**像素填充空洞(EDT 最近邻),接缝处轻模糊。

    比"整块填单一中位色"好得多——最近邻把周围布料的褶皱明暗带进填充区,得到干净底布,后续明暗场
    (shade)才不被旧印花污染。`valid` 给定时只从有效布料(产品本体且非空洞)取色,绝不把背景吸进来。
    纯 scipy,O(N),~几毫秒。
    """
    if not holes.any():
        return arr
    inv = (~valid) if valid is not None else holes  # 取色源:valid 区(或退化为非空洞区)
    ind = ndimage.distance_transform_edt(inv, return_distances=False, return_indices=True)
    filled = arr[ind[0], ind[1]]          # 每个像素取最近的有效布料像素
    base = arr.copy()
    base[holes] = filled[holes]
    # 最近邻填充会留 Voronoi 条纹:在空洞内用大尺度模糊抹平成局部布料均色,接缝处宽羽化融合。
    sig = max(8.0, (holes.sum() ** 0.5) / 14)   # 随空洞尺寸自适应模糊半径
    blur = ndimage.gaussian_filter(base, sigma=(sig, sig, 0))
    soft = ndimage.gaussian_filter(holes.astype(np.float32), 6.0)[..., None]
    return base * (1 - soft) + blur * soft


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


def _replace_local(product: Image.Image, new_print: Image.Image) -> Image.Image:
    """几何兜底:检测原印花区→EDT 抹除得干净底布→新印花等比贴入,乘底布明暗场(跟随褶皱)+ 接触阴影。

    检测不到产品/印花时退化为居中贴。明暗场取自**抹除后的底布**(而非含旧印花的原图),旧印花的
    深色不再错误压暗新印花——这是相对旧版的关键修正。
    """
    full = product.convert("RGB")
    if max(full.size) > _MAX_SIDE:        # 只缩不放,封顶工作分辨率
        full = ImageOps.contain(full, (_MAX_SIDE, _MAX_SIDE))
    W, H = full.size
    reg = _detect_region(full)
    arr = np.asarray(full).astype(np.float32)

    if reg is None:  # 兜底:没检测到产品/印花 → 居中贴(不抹除,占 ~52% 宽)
        bbox = (int(W * 0.24), int(H * 0.22), int(W * 0.76), int(H * 0.74))
        holes = np.zeros((H, W), bool)
        valid = None
    else:
        garment = reg["garment"]
        g_area = int(garment.sum()) or 1
        print_frac = float((reg["mask"] & garment).sum()) / g_area
        if print_frac > 0.55:
            # 印花≈整个产品 → 检测不可靠(纯色瓶身/硬质曲面常被误判)。不抹除以免毁掉产品,
            # 退化为把新印花居中贴在产品本体上(原品可能边缘微露,但完整不破)。曲面属 AI 路径强项。
            ys, xs = np.where(garment)
            gl, gt, gr, gb = int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)
            cw, ch = gr - gl, gb - gt
            bbox = (gl + int(cw * 0.18), gt + int(ch * 0.30), gr - int(cw * 0.18), gt + int(ch * 0.72))
            holes = np.zeros((H, W), bool)
            valid = None
        else:
            # 抹除"印花本体(闭运算后)+ 适度外扩"∩ 产品本体:扩一圈消残影,但不误删纯色瓶身/布料。
            d = max(2, int(min(W, H) * 0.012))
            holes = (ndimage.distance_transform_edt(~reg["mask"]) <= d) & garment
            valid = garment & ~holes           # 取色只从产品本体的非印花布料,绝不吸入背景
            if holes.any():                    # bbox 由实际抹除区推出,新印花贴回此处
                ys, xs = np.where(holes)
                bbox = (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))

    l, t, r, b = bbox
    bw, bh = r - l, b - t
    if bw < 8 or bh < 8:
        return full

    # ① 抹掉原印花 → 干净底布(保褶皱明暗)
    base = _inpaint_fabric(arr, holes, valid)

    # ② 底布明暗场(褶皱/阴影)= 亮度 / bbox 内中位亮度,后面 multiply 到新印花
    lum = base @ np.array([0.299, 0.587, 0.114], np.float32)
    med = float(np.median(lum[t:b, l:r])) or 1.0
    shade = np.clip(lum / med, 0.6, 1.35)

    # ③ 新印花:确保有 alpha(键掉实底)→ 等比进 bbox 居中
    fitted, ox, oy = _fit_contain(_prep_print(new_print), bw, bh)
    fw, fh = fitted.size
    y0, x0 = t + oy, l + ox
    na = np.asarray(fitted).astype(np.float32)
    a_new = na[..., 3:] / 255.0
    sub = shade[y0:y0 + fh, x0:x0 + fw]
    if sub.shape[:2] != (fh, fw):         # 边界对齐兜底
        sub = np.ones((fh, fw), np.float32)
    rgb_new = na[..., :3] * sub[..., None]

    # ④ 接触阴影:印花外缘一圈轻微压暗,削弱"贴上去"的悬浮感
    out = base.copy()
    ra = np.zeros((H, W), np.float32)
    ra[y0:y0 + fh, x0:x0 + fw] = a_new[..., 0]
    halo = np.clip(ndimage.gaussian_filter(ra, 4.0) - ra, 0, 1) * 0.18
    out *= (1 - halo[..., None])

    # ⑤ 合成
    dst = out[y0:y0 + fh, x0:x0 + fw, :]
    out[y0:y0 + fh, x0:x0 + fw, :] = dst * (1 - a_new) + rgb_new * a_new
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")
