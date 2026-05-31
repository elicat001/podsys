"""四方连续图(seamless repeating pattern)—— 纯离线 Pillow,确定性、无需 AI。

mirror 模式用镜像拼贴构造 2x2 基块,边缘按构造天然无缝;再按 repeat 平铺成连续大图。
适合服饰家纺四方连续印花的快速生成与多尺寸输出。
"""
from __future__ import annotations
from PIL import Image, ImageOps

MAX_OUTPUT_PIXELS = 50_000_000  # 防超大输出 OOM


def mirror_block(img: Image.Image) -> Image.Image:
    """用镜像构造无缝 2x2 基块:原图 / 水平翻 / 垂直翻 / 中心翻,边缘天然对齐。"""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    flip_h = ImageOps.mirror(rgba)
    flip_v = ImageOps.flip(rgba)
    flip_hv = ImageOps.flip(flip_h)
    block = Image.new("RGBA", (w * 2, h * 2))
    block.paste(rgba, (0, 0))
    block.paste(flip_h, (w, 0))
    block.paste(flip_v, (0, h))
    block.paste(flip_hv, (w, h))
    return block


def tile(base: Image.Image, rows: int, cols: int) -> Image.Image:
    bw, bh = base.size
    if (bw * cols) * (bh * rows) > MAX_OUTPUT_PIXELS:
        raise ValueError("四方连续输出尺寸过大,请减小 repeat 或输入图")
    out = Image.new("RGBA", (bw * cols, bh * rows))
    for r in range(rows):
        for c in range(cols):
            out.paste(base, (c * bw, r * bh))
    return out


def seamless_pattern(img: Image.Image, repeat: int = 2, mode: str = "mirror") -> Image.Image:
    """生成四方连续图:先造无缝基块,再平铺 repeat×repeat。"""
    if repeat < 1 or repeat > 8:
        raise ValueError("repeat 必须在 1~8 之间")
    if mode != "mirror":
        raise ValueError(f"不支持的模式: {mode}(当前支持 mirror)")
    base = mirror_block(img)
    return tile(base, repeat, repeat)
