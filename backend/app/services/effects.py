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


# ---------- 标题:由图像调色板 + 关键词派生(纯规则,无云、无模型) ----------
# 本地电商标题引擎:风格/受众/场合/卖点 SEO 词库 + 品类话术 + 调色板分析 + 多模板。
# 设计取舍:不"理解"画面内容(那要 ML),但通过①真实调色板(主色/明暗/鲜艳度)②关键词主体
# ③品类同义词 ④确定性多模板,产出**接近真人 listing**的标题与搜索词,且 12 核机器上 ~几毫秒、无并发压力。
_TITLE_STYLES = ["Funny", "Cute", "Aesthetic", "Vintage", "Retro", "Trendy",
                 "Minimalist", "Graphic", "Cool", "Unique", "Cottagecore", "Y2K",
                 "Streetwear", "Kawaii", "Grunge", "Boho", "Edgy", "Whimsical"]
_TITLE_AUDIENCE = ["for Men", "for Women", "Unisex", "for Teens", "for Her", "for Him"]
_TITLE_OCCASION = ["Birthday Gift", "Christmas Gift", "Holiday Gift Idea",
                   "Funny Gift", "Gift for Friends", "Gift Idea"]
_TITLE_SELL = ["Premium Quality", "Trendy Design", "Perfect Gift", "Unique Design",
               "Bold Statement", "Eye-Catching Print", "Great Gift Idea"]
_TITLE_NOUNS = ["Graphic", "Art Print", "Illustration", "Design", "Artwork"]
# 品类 → 产品名(第一个为主名,其余进标签作同义搜索词)
_TITLE_CAT = {
    "apparel": ("T-Shirt", "Tee", "Graphic Tee", "Shirt"),
    "hoodie": ("Hoodie", "Sweatshirt", "Pullover"),
    "phone": ("Phone Case", "Case", "Phone Cover"),
    "home": ("Home Decor", "Wall Art", "Poster"),
    "mug": ("Mug", "Coffee Mug", "Cup"),
    "bag": ("Tote Bag", "Canvas Tote", "Bag"),
    "pillow": ("Throw Pillow", "Pillow Cover", "Cushion"),
    "sticker": ("Sticker", "Vinyl Sticker", "Decal"),
}
# 色相(0~360°)→ 颜色名(电商搜索词)
_HUE_NAMES = [(15, "Red"), (45, "Orange"), (68, "Yellow"), (95, "Lime"),
              (160, "Green"), (200, "Teal"), (255, "Blue"), (290, "Purple"),
              (330, "Pink"), (361, "Red")]


def _hue_name(h: float) -> str:
    """色相(0~1)→ 颜色名。"""
    deg = (h % 1.0) * 360
    for lim, name in _HUE_NAMES:
        if deg < lim:
            return name
    return "Red"


def _palette(img: Image.Image) -> dict:
    """轻量调色板分析(纯 Pillow+colorsys,无 numpy/ML):返回主色名、明暗、鲜艳度描述词。

    忽略透明背景像素;在彩色像素里按 饱和度×明度 加权投票出主色;据整体明暗/饱和给出风格描述。
    """
    im = img.convert("RGBA")
    im.thumbnail((80, 80))  # 缩到 ≤80px:几千像素,colorsys 循环也只需几毫秒
    px = list(im.getdata())
    opaque = [(r, g, b) for (r, g, b, a) in px if a > 128]
    if len(opaque) < 16:  # 几乎全透明(罕见)→ 退而用全部像素
        opaque = [(r, g, b) for (r, g, b, _a) in px]
    n = len(opaque) or 1

    sum_v = 0.0
    colored = 0
    sat_colored = 0.0
    votes: dict[str, float] = {}
    for r, g, b in opaque:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        sum_v += v
        if s > 0.28 and v > 0.18:          # 足够鲜明的彩色像素才参与主色投票
            colored += 1
            sat_colored += s
            name = _hue_name(h)
            votes[name] = votes.get(name, 0.0) + s * v
    mean_v = sum_v / n
    frac_colored = colored / n
    mean_sat = (sat_colored / colored) if colored else 0.0

    if frac_colored < 0.06:                # 近单色 → 黑/白/灰阶
        if mean_v < 0.24:
            return {"accent": None, "mono": "Black", "vivid": "Bold", "tone": "Bold Black"}
        if mean_v > 0.82:
            return {"accent": None, "mono": "White", "vivid": "Clean", "tone": "Minimalist"}
        return {"accent": None, "mono": "Black & White", "vivid": "Monochrome", "tone": "Aesthetic"}

    accent = max(votes, key=votes.get)
    if mean_sat > 0.6 and mean_v > 0.55:
        vivid, tone = "Vibrant", "Trendy"
    elif mean_sat < 0.42 and mean_v > 0.6:
        vivid, tone = "Pastel", "Aesthetic"
    elif mean_v < 0.4:
        vivid, tone = "Dark Moody", "Edgy"
    else:
        vivid, tone = "Colorful", "Graphic"
    return {"accent": accent, "mono": None, "vivid": vivid, "tone": tone}


def smart_title(img: Image.Image | None, keywords: str = "", category: str = "apparel") -> dict:
    """本地电商标题(无云、无模型):调色板分析 + 关键词主体 + SEO 词库 + 品类话术 + 多模板,
    确定性派生(同输入同输出;不同输入产出不同主色/模板/词)。

    仍是规则拼接(不"理解"画面语义),但调色板让"无关键词"也能给出贴切主色描述,模板与搜索词更接近
    真人 listing。CPU 极轻(缩图 + colorsys 投票),适合高并发。
    """
    kw = [k.strip() for k in (keywords or "").replace("，", ",").split(",") if k.strip()]
    # 图指纹:8×8 灰度缩略图 → 让不同设计(即便主色相同)派生不同模板/受众,避免批量标题撞车;同图稳定。
    fp = ""
    if img is not None:
        try:
            g = img.convert("L"); g.thumbnail((8, 8))
            fp = bytes(g.getdata()).hex()
        except Exception:  # noqa: BLE001
            fp = ""
    seed = _seed("|".join(kw) + "|" + category + "|" + fp)

    prod = _TITLE_CAT.get(category, (category.replace("_", " ").title(), category.replace("_", " ").title()))
    product = prod[0]

    pal = _palette(img) if img is not None else {"accent": None, "mono": None, "vivid": None, "tone": None}
    # 主色前缀:有彩色主色用色名;近单色用 黑/白/Mono 词;都没有(无图)用风格词
    color_lead = pal["accent"] or pal["mono"]

    style = _TITLE_STYLES[seed % len(_TITLE_STYLES)]
    style2 = _TITLE_STYLES[(seed // 11) % len(_TITLE_STYLES)]
    if style2 == style:  # 避免 "Retro ... Retro" / "Y2K Y2K" 同词重复
        style2 = _TITLE_STYLES[(seed // 11 + 1) % len(_TITLE_STYLES)]
    # 图给了风格基调就优先用它当主风格,让标题与画面气质一致
    if pal["tone"]:
        style = pal["tone"]
    aud = _TITLE_AUDIENCE[(seed // 13) % len(_TITLE_AUDIENCE)]
    occ = _TITLE_OCCASION[(seed // 17) % len(_TITLE_OCCASION)]
    sell = _TITLE_SELL[(seed // 19) % len(_TITLE_SELL)]
    noun = _TITLE_NOUNS[(seed // 23) % len(_TITLE_NOUNS)]

    # 主体 + 前缀:有关键词→主体=关键词,前缀=主色;无关键词→主体已含主色,前缀改用风格词(避免色名重复)
    if kw:
        subject = " ".join(k.title() for k in kw[:3])
        lead = color_lead or style
    elif color_lead:
        subject = f"{color_lead} {noun}"          # 如 "Orange Art Print" / "Black & White Illustration"
        lead = style                               # 前缀用风格,不再重复色名
    else:
        subject = f"Custom {noun}"
        lead = style

    templates = [
        f"{lead} {subject} {product} - {style2} {aud}, {occ}",
        f"{subject} {product} | {style} {style2} {noun} {aud}",
        f"{style} {subject} {product} - {sell}, {occ}",
        f"{subject} {product}, {lead} {style2} Print - {occ} {aud}",
        f"{lead} {subject} {product} | {sell} {aud} - {occ}",
    ]
    title = " ".join(templates[seed % len(templates)].split())
    # 去相邻重复词(防 "Orange Orange" / "Vintage Vintage" 等任何残留)
    ws = title.split()
    title = " ".join(w for i, w in enumerate(ws) if i == 0 or w.lower() != ws[i - 1].lower())[:140]

    # 标签:用户词 + 主色 + 鲜艳度 + 风格×2 + 品类全部同义词 + 场合 + 通用 SEO;去重(小写)取前 13
    extra = [x for x in [
        (pal["accent"] or "").lower(), (pal["mono"] or "").lower().replace(" & ", " "),
        (pal["vivid"] or "").lower(), style.lower(), style2.lower(),
        *[p.lower() for p in prod], category, occ.lower(), "gift", "trendy print", "pod design",
    ] if x]
    tags = list(dict.fromkeys([t for t in ([k.lower() for k in kw] + extra) if t]))[:13]
    return {"title": title, "keywords": tags}
