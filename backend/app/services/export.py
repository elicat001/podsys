"""Export production-ready print files at a target physical size / DPI.

Mirrors the 履约 step: take the clean print and emit a print file sized for the
factory (e.g. 30x40cm @ 300DPI) plus a sidecar metadata json.
"""
from __future__ import annotations
import json
from pathlib import Path
from PIL import Image


def cm_to_px(cm: float, dpi: int) -> int:
    return int(round(cm / 2.54 * dpi))


def export_production(
    print_img: Image.Image,
    out_path: Path,
    width_cm: float = 30.0,
    height_cm: float = 40.0,
    dpi: int = 300,
) -> dict:
    target_w = cm_to_px(width_cm, dpi)
    target_h = cm_to_px(height_cm, dpi)

    # fit print into the target canvas, preserving aspect ratio, centered, transparent bg
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    pw, ph = print_img.size
    scale = min(target_w / pw, target_h / ph)
    new = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    placed = print_img.convert("RGBA").resize(new, Image.LANCZOS)
    canvas.alpha_composite(placed, ((target_w - new[0]) // 2, (target_h - new[1]) // 2))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", dpi=(dpi, dpi))

    meta = {
        "file": out_path.name,
        "width_px": target_w,
        "height_px": target_h,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "dpi": dpi,
    }
    out_path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
