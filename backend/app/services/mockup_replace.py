"""套图模板「印花替换」引擎:把真实产品照里的原印花,换成用户上传的新印花,尽量保真实感。

思路(纯本地、几何法,faithful 保留用户那张精确印花):
  ① 复用印花提取的检测(`design_extract._product_mask` + `_print_alpha`)定位原印花的 bbox + mask;
  ② 抹掉原印花:其 mask 处填周围布料色(羽化边),其余像素原样保留(保住布料褶皱);
  ③ 新印花等比缩放进该 bbox(居中,不裁切用户图);
  ④ 把原图该区域的『明暗(亮度)』当作折叠/阴影系数 multiply 到新印花上 → 新印花跟着布料起伏,
     不是平贴;⑤ 合成回去。

效果上限:平贴/微角度(平铺产品照、托特包、画布、正面 T 恤)效果好;强曲面(杯子环绕)/重褶皱
只能近似(无 PSD 智能对象式 warp)。检测不到产品时退化为『居中贴新印花』兜底,不报错。
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage

from .design_extract import _ANALYZE, _flatten_illumination, _print_alpha, _product_mask


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


def replace_print(product: Image.Image, new_print: Image.Image) -> Image.Image:
    """把 product 里的原印花替换成 new_print,带回原图明暗。检测不到产品→居中贴兜底。"""
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
