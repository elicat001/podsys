"""转矢量图(纯离线 raster→SVG):缩放 + 量化 + 同色行程合并成 <rect>。

只用 Pillow + stdlib,无任何网络/AI 依赖。
"""
from __future__ import annotations

from PIL import Image

# 缩放后像素上限(防止 rect 数量爆炸)。max_dim 默认 128,这里再加一道硬上限。
_MAX_PIXELS = 200 * 200


def to_svg(img: Image.Image, colors: int = 8, max_dim: int = 128) -> tuple[str, int]:
    """把位图量化后转成 SVG 矢量图,返回 (svg字符串, rect数量)。

    - colors:量化色数,必须 2<=colors<=64,否则 raise ValueError。
    - max_dim:缩放后长边上限;等比缩小到该范围内做量化以控制 rect 数量,
      但 SVG 的 viewBox 用原图尺寸,保证视觉尺寸不变。
    - 算法:按行遍历量化后的像素,把同色相邻像素合并成一个 <rect>
      (宽=行程长*scale,高=1*scale),fill 用 #rrggbb。
    """
    if not (2 <= colors <= 64):
        raise ValueError(f"colors 必须在 2..64 之间,收到 {colors}")
    if max_dim < 1:
        raise ValueError("max_dim 必须为正整数")

    rgb = img.convert("RGB")
    orig_w, orig_h = rgb.size
    if orig_w < 1 or orig_h < 1:
        raise ValueError("图片尺寸非法")

    # 等比缩放到 max_dim 内(只缩不放),记缩放比用于把小图坐标映射回原始尺寸。
    long_side = max(orig_w, orig_h)
    if long_side > max_dim:
        ratio = max_dim / long_side
        small_w = max(1, int(round(orig_w * ratio)))
        small_h = max(1, int(round(orig_h * ratio)))
    else:
        small_w, small_h = orig_w, orig_h

    # 硬上限:缩后像素过多则进一步缩小。
    while small_w * small_h > _MAX_PIXELS and (small_w > 1 or small_h > 1):
        small_w = max(1, small_w // 2)
        small_h = max(1, small_h // 2)

    small = rgb.resize((small_w, small_h), Image.NEAREST) if (small_w, small_h) != (orig_w, orig_h) else rgb

    # 量化到 colors 种色,再转回 RGB 取每像素颜色。
    quant = small.convert("RGB").quantize(colors=colors)
    quant_rgb = quant.convert("RGB")
    px = quant_rgb.load()

    # 缩后像素 -> 原始坐标的缩放比(让每个 rect 覆盖对应原始区域)。
    sx = orig_w / small_w
    sy = orig_h / small_h

    rects: list[str] = []
    for y in range(small_h):
        x = 0
        while x < small_w:
            r, g, b = px[x, y]
            run = 1
            while x + run < small_w and px[x + run, y] == (r, g, b):
                run += 1
            # 用整数边界,相邻行/列严丝合缝拼接,避免缝隙与累积误差。
            rx = round(x * sx)
            ry = round(y * sy)
            rw = round((x + run) * sx) - rx
            rh = round((y + 1) * sy) - ry
            if rw > 0 and rh > 0:
                fill = f"#{r:02x}{g:02x}{b:02x}"
                rects.append(
                    f'<rect x="{rx}" y="{ry}" width="{rw}" height="{rh}" fill="{fill}"/>'
                )
            x += run

    body = "".join(rects)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {orig_w} {orig_h}" width="{orig_w}" height="{orig_h}" '
        f'shape-rendering="crispEdges">{body}</svg>'
    )
    return svg, len(rects)
