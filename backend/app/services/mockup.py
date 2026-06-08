"""Mockup / 套图 generation: place an extracted print onto a product template.

升级:从「扁平贴纸」到「印上去的质感」——
- 服装配色变体:同一印花可套到 白/黑/灰/藏青/沙/红 等多色产品上(`GARMENT_COLORS`)。
- 真实感融合:用 soft-light 把产品的明暗/褶皱叠加到印花上(印花跟着布料起伏),
  再加一层柔和接触阴影,不再是平铺贴纸。
- 批量套图:`render_variants` 一次出「多模板 × 多配色」一整组,供上架。

仍为纯 Pillow、无外部素材;生产环境可把 `_draw_body` 换成真实产品照 + 标定印区。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from PIL import Image, ImageChops, ImageDraw, ImageFilter


# 可选服装配色(印在彩色产品上的常用色)。
GARMENT_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (242, 242, 242),
    "black": (40, 40, 42),
    "heather": (158, 160, 165),
    "navy": (44, 58, 92),
    "sand": (214, 198, 170),
    "red": (176, 52, 52),
}


@dataclass
class Template:
    id: str
    label: str
    size: tuple[int, int]
    bg: tuple[int, int, int]
    body: tuple[int, int, int]
    # print area as (left, top, right, bottom) within the template
    print_area: tuple[int, int, int, int]
    default_color: str = "white"
    # 该产品支持的配色(为空表示沿用所有 GARMENT_COLORS)
    palette: tuple[str, ...] = field(default_factory=tuple)


BUILTIN: dict[str, Template] = {
    "tshirt": Template("tshirt", "T-Shirt", (1000, 1200), (245, 245, 245), (242, 242, 242),
                       (330, 360, 670, 760), "white",
                       ("white", "black", "heather", "navy", "sand", "red")),
    "tote": Template("tote", "帆布袋", (1000, 1100), (238, 232, 220), (224, 214, 196),
                     (300, 330, 700, 800), "sand", ("white", "black", "sand", "navy")),
    "canvas": Template("canvas", "装饰画布", (1000, 1000), (250, 250, 250), (255, 255, 255),
                       (120, 120, 880, 880), "white", ("white",)),
    "phonecase": Template("phonecase", "手机壳", (700, 1300), (240, 240, 245), (30, 30, 34),
                          (110, 150, 590, 1150), "black",
                          ("black", "white", "navy", "red")),
}


def list_templates() -> list[dict]:
    out = []
    for t in BUILTIN.values():
        colors = list(t.palette) if t.palette else list(GARMENT_COLORS.keys())
        out.append({"id": t.id, "label": t.label, "colors": colors,
                    "default_color": t.default_color})
    return out


def _draw_body(d: ImageDraw.ImageDraw, t: Template, fill, outline=None) -> None:
    """把产品本体形状画到 draw 上(base 用实色,mask 用 255)。"""
    w, h = t.size
    if t.id == "tshirt":
        d.polygon([(250, 180), (400, 120), (600, 120), (750, 180), (820, 320),
                   (720, 380), (720, 1080), (280, 1080), (280, 380), (180, 320)],
                  fill=fill, outline=outline)
    elif t.id == "tote":
        d.rectangle([220, 300, 780, 980], fill=fill, outline=outline)
        if outline is not None:  # 提手仅在可视层画,mask 不需要
            d.arc([330, 120, 470, 360], 0, 180, fill=(160, 150, 130), width=14)
            d.arc([530, 120, 670, 360], 0, 180, fill=(160, 150, 130), width=14)
    elif t.id == "phonecase":
        d.rounded_rectangle([70, 90, 630, 1210], radius=80, fill=fill)
    else:  # canvas
        d.rectangle([60, 60, w - 60, h - 60], fill=fill, outline=outline, width=6)


def _build_template(t: Template, body: tuple[int, int, int]) -> tuple[Image.Image, Image.Image]:
    """画产品底图,返回 (RGBA 底图, L 本体蒙版)。"""
    img = Image.new("RGBA", t.size, t.bg + (255,))
    _draw_body(ImageDraw.Draw(img), t, body + (255,), outline=(210, 210, 210))
    mask = Image.new("L", t.size, 0)
    _draw_body(ImageDraw.Draw(mask), t, 255)
    return img, mask


def _clamp(v: int) -> int:
    return 0 if v < 0 else 255 if v > 255 else v


def _shading_layer(t: Template, mask: Image.Image) -> Image.Image:
    """生成 soft-light 用的明暗层:128=不变,>128 提亮、<128 压暗。

    上方受光(略亮)、下方略暗,叠几条柔和褶皱;本体外恒为 128(不影响背景)。
    """
    w, h = t.size
    sh = Image.new("L", (w, h), 128)
    d = ImageDraw.Draw(sh)
    # 竖直受光:顶部 +18 渐变到底部 -18
    for y in range(h):
        d.line([(0, y), (w, y)], fill=_clamp(128 + int(18 * (1 - 2 * y / h))))
    # 几条柔和褶皱(压暗),分布在本体中下部
    l, top, r, b = t.print_area
    wf = max(6, h // 90)
    for frac in (0.45, 0.62, 0.78):
        y = int(top + (b - top) * frac)
        d.line([(l - 40, y - 18), (r + 40, y + 18)], fill=104, width=wf)
    sh = sh.filter(ImageFilter.GaussianBlur(max(10, h // 70)))
    neutral = Image.new("L", (w, h), 128)
    return Image.composite(sh, neutral, mask)


def render_mockup(print_img: Image.Image, template_id: str = "tshirt",
                  color: str | None = None) -> Image.Image:
    """把印花套到产品上(真实感:soft-light 融合明暗 + 柔和接触阴影 + 可选配色)。"""
    t = BUILTIN.get(template_id) or BUILTIN["tshirt"]
    body = GARMENT_COLORS.get(color) if color else None
    if body is None:
        body = GARMENT_COLORS.get(t.default_color, t.body)
    base, mask = _build_template(t, body)

    l, top, r, b = t.print_area
    area_w, area_h = r - l, b - top
    pw, ph = print_img.size
    scale = min(area_w / pw, area_h / ph)
    new = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    placed = print_img.convert("RGBA").resize(new, Image.LANCZOS)
    ox = l + (area_w - new[0]) // 2
    oy = top + (area_h - new[1]) // 2

    # 印花层(整版透明 + 印花放到印区位置)
    layer = Image.new("RGBA", t.size, (0, 0, 0, 0))
    layer.paste(placed, (ox, oy), placed)

    # 柔和接触阴影:由印花 alpha 模糊压暗,轻微偏移,营造「印上去」的贴合
    alpha = layer.split()[3]
    shadow_a = alpha.filter(ImageFilter.GaussianBlur(7)).point(lambda a: int(a * 0.18))
    shadow = Image.new("RGBA", t.size, (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", t.size, (0, 0, 0, 255)), (3, 4), shadow_a)
    base = Image.alpha_composite(base, shadow)

    combined = Image.alpha_composite(base, layer)

    # soft-light 把布料明暗/褶皱叠到印花+本体上,只作用于本体区域
    sh = _shading_layer(t, mask).convert("RGB")
    rgb = combined.convert("RGB")
    shaded = ImageChops.soft_light(rgb, sh)
    out = Image.composite(shaded, rgb, mask)
    return out.convert("RGBA")


def render_batch(print_img: Image.Image, template_ids: list[str]) -> dict[str, Image.Image]:
    """一次把同一印花贴到多个产品模板(各用其默认配色)。"""
    return {tid: render_mockup(print_img, tid) for tid in template_ids}


def render_variants(print_img: Image.Image,
                    combos: list[tuple[str, str | None]]) -> list[tuple[str, str, Image.Image]]:
    """批量套图:combos=[(template_id, color), ...] → [(template_id, color, image), ...]。"""
    res = []
    for tid, color in combos:
        t = BUILTIN.get(tid) or BUILTIN["tshirt"]
        c = color or t.default_color
        res.append((t.id, c, render_mockup(print_img, tid, c)))
    return res
