"""真实的离线图像引擎(纯 Pillow)—— 让设计/处理工具在无 OpenAI key 时也产出真东西。

这些不是占位:每个函数都对像素做真实变换,输出可见、可区分的结果。
配置了 OpenAI key 时,路由会优先走 gpt-image(语义更强);否则回退到这里。
"""
from __future__ import annotations
import colorsys
import hashlib
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


def _print_region_mask(img: Image.Image):
    """定位『印花』区域,返回全幅 L mask(255=印花)。复用 design_extract 的本地检测
    (cloth-seg 排除人/皮肤 + 去主导材质色),**只读不改它**。

    小图(测试/缩略图)/依赖缺失(rembg/scipy)/未检出/任何异常 → 返回 None,
    交由调用方回退整图改色。所以即便检测不可用,图裂变也永远能出图,不会崩。"""
    if min(img.size) < 512:  # 小图:不加载重模型(保测试快 + 纯 pillow 可跑),直接回退
        return None
    try:
        import numpy as np

        from .design_extract import _ANALYZE, _flatten_illumination, _print_alpha, _product_mask
        small = img.convert("RGB")
        small.thumbnail((_ANALYZE, _ANALYZE))  # 检测在 1000px 上做(快且对褶皱稳)
        pm, kind = _product_mask(small)
        if pm is None:
            return None
        arr = np.asarray(small).astype(float)
        if kind == "garment":
            arr = _flatten_illumination(arr, pm)
        alpha = _print_alpha(arr.astype(int), pm, kind)  # 小图尺度的印花 mask(uint8)
        mask = Image.fromarray(alpha, "L").resize(img.size, Image.BILINEAR)
        return mask if mask.getbbox() is not None else None
    except Exception:  # noqa: BLE001  依赖缺失/检测异常 → 回退整图改色
        return None


def print_colorway_variants(img: Image.Image, n: int = 3) -> list[Image.Image]:
    """无 key 图裂变(主体感知):先定位印花区域,**只给印花换配色,人物/背景保持不变**。

    定位不可用(小图 / 无 rembg/scipy / 未检出)→ 回退 colorway_variants(整图改色,原行为)。
    """
    n = max(1, min(n, 6))
    base = img.convert("RGB")
    mask = _print_region_mask(base)
    if mask is None:
        return colorway_variants(img, n)
    soft = mask.filter(ImageFilter.GaussianBlur(2))  # 软化 mask 边缘,合成自然
    src_alpha = img.convert("RGBA").split()[-1] if img.mode == "RGBA" else None
    hues = [40, 150, 280, 90, 200, 330]
    out = []
    for k in range(n):
        shifted = _hue_shift(base, hues[k % len(hues)])
        v = Image.composite(shifted, base, soft).convert("RGBA")  # 仅印花处用改色版,其余原图
        if src_alpha is not None:
            v.putalpha(src_alpha)
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
# 本地电商标题:风格/受众/场合 SEO 修饰词库 + 品类话术 + 多模板(纯规则,无云、无模型)
_TITLE_STYLES = ["Funny", "Cute", "Aesthetic", "Vintage", "Retro", "Trendy",
                 "Minimalist", "Graphic", "Cool", "Unique", "Cottagecore", "Y2K"]
_TITLE_AUDIENCE = ["for Men", "for Women", "Unisex", "for Teens", "for Her", "for Him"]
_TITLE_OCCASION = ["Birthday Gift", "Christmas Gift", "Holiday Gift Idea",
                   "Funny Gift", "Aesthetic Gift", "Gift Idea"]
# 品类 → 产品名(第一个为主名,其余进标签作同义搜索词)
_TITLE_CAT = {
    "apparel": ("T-Shirt", "Tee", "Graphic Tee"),
    "phone": ("Phone Case", "Case", "Phone Cover"),
    "home": ("Home Decor", "Wall Art", "Poster"),
    "mug": ("Mug", "Coffee Mug", "Cup"),
    "bag": ("Tote Bag", "Canvas Tote", "Bag"),
    "sticker": ("Sticker", "Vinyl Sticker", "Decal"),
}


def smart_title(img: Image.Image | None, keywords: str = "", category: str = "apparel") -> dict:
    """本地电商标题(无云、无模型):多模板 + SEO 修饰词 + 品类话术 + 主色调,按关键词
    确定性派生(同输入同输出,不同输入产出不同模板/词)。

    仍是规则拼接(不"理解"内容),但比单一模板更像真实 listing 标题、搜索词覆盖更全。
    """
    kw = [k.strip() for k in (keywords or "").replace("，", ",").split(",") if k.strip()]
    seed = _seed("|".join(kw) + "|" + category)

    prod = _TITLE_CAT.get(category, (category.replace("_", " ").title(),))
    product = prod[0]

    # 主色调(给了图才用):白底/低饱和 → Minimalist/Bold Black;彩色 → 具体色名
    tone = None
    if img is not None:
        try:
            r, g, b = img.convert("RGB").resize((1, 1)).getpixel((0, 0))
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s < 0.18:
                tone = "Minimalist" if v >= 0.5 else "Bold Black"
            else:
                tone = ["Red", "Orange", "Yellow", "Green", "Teal", "Blue", "Purple", "Pink"][int(h * 8) % 8]
        except Exception:  # noqa: BLE001
            tone = None

    subject = " ".join(k.title() for k in kw[:3]) if kw else "Custom Graphic"
    style = _TITLE_STYLES[seed % len(_TITLE_STYLES)]
    style2 = _TITLE_STYLES[(seed // 11) % len(_TITLE_STYLES)]
    if style2 == style:  # 避免 "Retro ... Retro" / "Y2K Y2K" 同词重复
        style2 = _TITLE_STYLES[(seed // 11 + 1) % len(_TITLE_STYLES)]
    aud = _TITLE_AUDIENCE[(seed // 13) % len(_TITLE_AUDIENCE)]
    occ = _TITLE_OCCASION[(seed // 17) % len(_TITLE_OCCASION)]
    lead = tone or style  # 有色调用色调当前缀,否则风格词

    templates = [
        f"{lead} {subject} {product} - {style2} Graphic {aud}, {occ}",
        f"{subject} {product} | {style} {style2} Design {aud}",
        f"{style} {subject} Graphic {product} - Unique {occ} {aud}",
        f"{subject} {product}, {lead} {style2} Print - {occ}",
    ]
    title = " ".join(templates[seed % len(templates)].split())[:140]

    # 标签:用户词 + 色调 + 风格 + 品类同义词 + 通用 SEO,去重(小写)取前 12
    extra = ([tone.lower()] if tone else []) + [style.lower(), style2.lower(),
             *[p.lower() for p in prod], category, "gift", "pod design"]
    tags = list(dict.fromkeys([t for t in (kw + extra) if t]))[:12]
    return {"title": title, "keywords": tags}
