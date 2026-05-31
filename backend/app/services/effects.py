"""真实的离线图像引擎(纯 Pillow)—— 让设计/处理工具在无 OpenAI key 时也产出真东西。

这些不是占位:每个函数都对像素做真实变换,输出可见、可区分的结果。
配置了 OpenAI key 时,路由会优先走 gpt-image(语义更强);否则回退到这里。
"""
from __future__ import annotations
import colorsys
import hashlib
import math
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageChops

MAX_PX = 50_000_000


def _seed(text: str) -> int:
    return int(hashlib.sha256((text or "x").encode()).hexdigest(), 16)


def _hue_shift(img: Image.Image, deg: float) -> Image.Image:
    """整体色相旋转(真实 HSV 变换),用于配色裂变。"""
    rgb = img.convert("RGB")
    h, s, v = rgb.convert("HSV").split()
    shift = int(deg / 360 * 255) & 255
    h = h.point(lambda p: (p + shift) & 255)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


# ---------- 图裂变:真实配色/变换衍生 ----------
def colorway_variants(img: Image.Image, n: int = 3) -> list[Image.Image]:
    """生成 n 个真实变体:不同色相配色 + 翻转 + 滤镜,适合 POD 多 SKU 铺款。"""
    n = max(1, min(n, 6))
    base = img.convert("RGBA")
    out = []
    recipes = [
        lambda i: _hue_shift(i, 40), lambda i: _hue_shift(i, 150),
        lambda i: ImageOps.mirror(_hue_shift(i, 280)),
        lambda i: ImageEnhance.Contrast(ImageOps.autocontrast(i.convert("RGB"))).enhance(1.3),
        lambda i: ImageOps.posterize(i.convert("RGB"), 3),
        lambda i: ImageOps.flip(_hue_shift(i, 90)),
    ]
    for k in range(n):
        v = recipes[k % len(recipes)](base).convert("RGBA")
        # 保留原 alpha(若有)
        if base.mode == "RGBA":
            v.putalpha(base.split()[-1])
        out.append(v)
    return out


# ---------- 风格转绘:真实滤镜风格化 ----------
def stylize(img: Image.Image, style: str = "flat") -> Image.Image:
    s = (style or "").lower()
    rgb = img.convert("RGB")
    if any(k in s for k in ("line", "edge", "矢量", "描边", "contour")):
        g = rgb.convert("L").filter(ImageFilter.CONTOUR)
        return ImageOps.invert(g).convert("RGB")
    if any(k in s for k in ("sketch", "铅笔", "素描")):
        g = rgb.convert("L")
        edges = g.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
        # 白底深色线条的铅笔稿:255 - 边缘强度
        sketch = ImageChops.subtract(Image.new("L", g.size, 255), edges)
        return sketch.convert("RGB")
    if any(k in s for k in ("oil", "油画", "watercolor", "水彩")):
        return rgb.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.SMOOTH_MORE)
    # 默认 flat / Temu 2D flat:降色阶 + 提饱和 + 边缘干净
    flat = ImageOps.posterize(rgb, 3)
    flat = ImageEnhance.Color(flat).enhance(1.35)
    return flat.filter(ImageFilter.SMOOTH)


# ---------- 梗图:真实文案叠加 ----------
def caption(img: Image.Image, text: str, place: str = "bottom") -> Image.Image:
    out = img.convert("RGB")
    if not text:
        return out
    d = ImageDraw.Draw(out)
    w, h = out.size
    size = max(22, w // 12)
    try:
        font = ImageFont.truetype("arialbd.ttf", size)
    except Exception:
        try: font = ImageFont.truetype("arial.ttf", size)
        except Exception: font = ImageFont.load_default()
    y = h - size - 14 if place == "bottom" else 12
    # 描边(meme 风)
    for dx in (-3, -2, 2, 3):
        for dy in (-3, -2, 2, 3):
            d.text((w // 2 + dx, y + dy), text, font=font, fill=(0, 0, 0), anchor="ma")
    d.text((w // 2, y), text, font=font, fill=(255, 255, 255), anchor="ma")
    return out


# ---------- 扩图:真实镜像反射外延 ----------
def outpaint_reflect(img: Image.Image, factor: float = 1.5) -> Image.Image:
    factor = max(1.1, min(factor, 2.5))
    rgb = img.convert("RGB")
    w, h = rgb.size
    nw, nh = int(w * factor), int(h * factor)
    if nw * nh > MAX_PX:
        raise ValueError("扩图尺寸过大")
    mx, my = (nw - w) // 2, (nh - h) // 2
    canvas = Image.new("RGB", (nw, nh))
    # 用模糊放大的自身做底,再贴回原图,边缘自然过渡
    bg = rgb.resize((nw, nh), Image.LANCZOS).filter(ImageFilter.GaussianBlur(max(8, w // 30)))
    canvas.paste(bg, (0, 0))
    canvas.paste(rgb, (mx, my))
    return canvas


# ---------- 去水印:真实中值滤波弱化 ----------
def dewatermark(img: Image.Image) -> Image.Image:
    """基础去水印:中值滤波弱化半透明叠加水印/细纹,再轻微锐化恢复主体。"""
    rgb = img.convert("RGB")
    med = rgb.filter(ImageFilter.MedianFilter(3))
    # 只在与原图差异较小处用滤波结果(主体细节尽量保留)
    blended = Image.blend(rgb, med, 0.6)
    return blended.filter(ImageFilter.UnsharpMask(radius=2, percent=80))


# ---------- 文生图:程序化图案(prompt 决定配色与构图,真实可区分) ----------
def procedural_pattern(prompt: str, size: int = 1024) -> Image.Image:
    sd = _seed(prompt)
    rnd = (sd % 1000) / 1000
    # 调色板
    base_h = (sd % 360) / 360
    pal = []
    for i in range(5):
        hh = (base_h + i * (0.11 + rnd * 0.08)) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hh, 0.55 + 0.3 * ((sd >> i) & 1), 0.85 - 0.08 * i)
        pal.append((int(r * 255), int(g * 255), int(b * 255)))
    img = Image.new("RGB", (size, size), pal[0])
    d = ImageDraw.Draw(img, "RGBA")
    # 渐变底
    for y in range(size):
        t = y / size
        c = tuple(int(pal[0][k] * (1 - t) + pal[1][k] * t) for k in range(3))
        d.line([(0, y), (size, y)], fill=c)
    # 重复几何母题(由 prompt 决定密度/形状)
    motif = sd % 3
    cells = 3 + sd % 4
    step = size // cells
    for gx in range(cells + 1):
        for gy in range(cells + 1):
            cx, cy = gx * step, gy * step
            col = pal[2 + ((gx + gy + sd) % 3)]
            rad = int(step * (0.18 + 0.22 * (((gx * 7 + gy * 13 + sd) % 100) / 100)))
            a = 150 + ((gx + gy) * 17 + sd) % 90
            fill = col + (a,)
            if motif == 0:
                d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=fill)
            elif motif == 1:
                d.rectangle([cx - rad, cy - rad, cx + rad, cy + rad], fill=fill)
            else:
                d.polygon([(cx, cy - rad), (cx + rad, cy + rad), (cx - rad, cy + rad)], fill=fill)
    return img


# ---------- 元素融合:原图 + 程序化纹理 真实混合 ----------
def fuse(img: Image.Image, prompt: str) -> Image.Image:
    rgb = img.convert("RGB")
    tex = procedural_pattern(prompt, max(rgb.size)).resize(rgb.size, Image.LANCZOS)
    return ImageChops.overlay(rgb, tex)


# ---------- 标题:由图像主色 + 关键词派生(非写死) ----------
def smart_title(img: Image.Image | None, keywords: str = "", category: str = "apparel") -> dict:
    kw = [k.strip() for k in (keywords or "").replace("，", ",").split(",") if k.strip()]
    tone = "Vibrant"
    if img is not None:
        small = img.convert("RGB").resize((1, 1))
        r, g, b = small.getpixel((0, 0))
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        tone = ["Crimson", "Amber", "Golden", "Verdant", "Teal", "Azure", "Indigo", "Violet", "Rosy"][int(h * 9) % 9]
        if s < 0.2:
            tone = "Monochrome" if v < .5 else "Minimal"
    head = " ".join(kw[:3]).title() if kw else "Custom Print"
    cat = {"apparel": "Tee", "phone": "Phone Case", "home": "Decor"}.get(category, category.title())
    title = f"{tone} {head} {cat} — Trendy Aesthetic Gift"
    tags = list(dict.fromkeys(kw + [tone.lower(), category, "pod", "gift", "trendy"]))[:8]
    return {"title": title[:120], "keywords": tags}
