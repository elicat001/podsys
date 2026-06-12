"""多图裁剪 / 多联画:把一张图切成 N 联(横/竖/网格)。"""
from __future__ import annotations

from PIL import Image


def split_panels(img: Image.Image, mode: str = "horizontal", panels: int = 3,
                 rows: int = 2, cols: int = 2) -> list[Image.Image]:
    """mode: horizontal(横向N联) | vertical(纵向N联) | grid(rows×cols)。"""
    img = img.convert("RGBA")
    w, h = img.size
    out: list[Image.Image] = []

    if mode == "horizontal":
        step = w // panels
        for i in range(panels):
            x0 = i * step
            x1 = w if i == panels - 1 else (i + 1) * step
            out.append(img.crop((x0, 0, x1, h)))
    elif mode == "vertical":
        step = h // panels
        for i in range(panels):
            y0 = i * step
            y1 = h if i == panels - 1 else (i + 1) * step
            out.append(img.crop((0, y0, w, y1)))
    elif mode == "grid":
        cw, ch = w // cols, h // rows
        for r in range(rows):
            for c in range(cols):
                x1 = w if c == cols - 1 else (c + 1) * cw
                y1 = h if r == rows - 1 else (r + 1) * ch
                out.append(img.crop((c * cw, r * ch, x1, y1)))
    else:
        raise ValueError(f"unknown split mode: {mode}")
    return out
