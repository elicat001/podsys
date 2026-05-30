"""Mockup / 套图 generation: place an extracted print onto a product template.

MVP ships built-in, programmatically-drawn templates (t-shirt, tote, canvas) each
with a defined print area. Replace `_build_template` with real product photos +
calibrated print quads for production. Perspective placement is supported via a
4-point quad; rectangular areas are the common case.
"""
from __future__ import annotations
from dataclasses import dataclass
from PIL import Image, ImageDraw


@dataclass
class Template:
    id: str
    label: str
    size: tuple[int, int]
    bg: tuple[int, int, int]
    body: tuple[int, int, int]
    # print area as (left, top, right, bottom) within the template
    print_area: tuple[int, int, int, int]


BUILTIN: dict[str, Template] = {
    "tshirt": Template("tshirt", "T-Shirt (白)", (1000, 1200), (245, 245, 245), (255, 255, 255), (330, 360, 670, 760)),
    "tote": Template("tote", "帆布袋", (1000, 1100), (238, 232, 220), (224, 214, 196), (300, 330, 700, 800)),
    "canvas": Template("canvas", "装饰画布", (1000, 1000), (250, 250, 250), (255, 255, 255), (120, 120, 880, 880)),
    "phonecase": Template("phonecase", "手机壳", (700, 1300), (240, 240, 245), (30, 30, 34), (110, 150, 590, 1150)),
}


def list_templates() -> list[dict]:
    return [{"id": t.id, "label": t.label} for t in BUILTIN.values()]


def _build_template(t: Template) -> Image.Image:
    """Draw a simple product base so mockups render without external assets."""
    img = Image.new("RGBA", t.size, t.bg + (255,))
    d = ImageDraw.Draw(img)
    w, h = t.size
    if t.id == "tshirt":
        d.polygon([(250, 180), (400, 120), (600, 120), (750, 180), (820, 320),
                   (720, 380), (720, 1080), (280, 1080), (280, 380), (180, 320)],
                  fill=t.body, outline=(210, 210, 210))
    elif t.id == "tote":
        d.rectangle([220, 300, 780, 980], fill=t.body, outline=(190, 180, 160))
        d.arc([330, 120, 470, 360], 0, 180, fill=(160, 150, 130), width=14)
        d.arc([530, 120, 670, 360], 0, 180, fill=(160, 150, 130), width=14)
    elif t.id == "phonecase":
        d.rounded_rectangle([70, 90, 630, 1210], radius=80, fill=t.body)
    else:  # canvas
        d.rectangle([60, 60, w - 60, h - 60], fill=t.body, outline=(200, 200, 200), width=6)
    return img


def render_batch(print_img: Image.Image, template_ids: list[str]) -> dict[str, Image.Image]:
    """一次把同一印花贴到多个产品模板(批量套图)。"""
    return {tid: render_mockup(print_img, tid) for tid in template_ids}


def render_mockup(print_img: Image.Image, template_id: str = "tshirt") -> Image.Image:
    t = BUILTIN.get(template_id) or BUILTIN["tshirt"]
    base = _build_template(t)
    l, top, r, b = t.print_area
    area_w, area_h = r - l, b - top

    # fit the print inside the print area, preserving aspect ratio
    pw, ph = print_img.size
    scale = min(area_w / pw, area_h / ph)
    new = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    placed = print_img.convert("RGBA").resize(new, Image.LANCZOS)

    ox = l + (area_w - new[0]) // 2
    oy = top + (area_h - new[1]) // 2
    base.alpha_composite(placed, (ox, oy))
    return base
