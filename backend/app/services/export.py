"""Export production-ready print files at a target physical size / DPI.

Mirrors the 履约 step: take the clean print and emit a print file sized for the
factory (e.g. 30x40cm @ 300DPI) plus a sidecar metadata json.

- `export_production`     : 单文件 PNG 透明印花稿 + 边车 json(主线 /api/process 用,签名不动)。
- `export_production_multi`: 「给工厂的最终格式转换」——把一张【已经做好的设计稿】按目标物理
  尺寸+DPI 居中排版,一次导出多格式(PNG 透明 / JPG 白底 / TIFF 无损 / PDF)。**纯本地
  确定性操作,不做抠图/AI。**
"""
from __future__ import annotations
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# 排版/锚点合法值(上游路由校验,这里兜底)。
SCALE_MODES = ("contain", "cover", "actual")
ANCHORS = ("center", "top", "bottom")

# 支持的导出格式(键=对外格式名,值=Pillow 保存用的扩展名/格式)。
SUPPORTED_FORMATS: tuple[str, ...] = ("png", "jpg", "tiff", "pdf")
# 单张画布像素上限(防超大尺寸×高 DPI 打爆内存;生产稿本就高清,放宽到 1.2 亿)。
MAX_PX = 120_000_000


def cm_to_px(cm: float, dpi: int) -> int:
    return int(round(cm / 2.54 * dpi))


def mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm / 25.4 * dpi))


def _place_on_canvas(print_img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """把设计稿等比居中贴到 target_w×target_h 的透明 RGBA 画布上,返回画布。"""
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    pw, ph = print_img.size
    scale = min(target_w / pw, target_h / ph)
    new = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    placed = print_img.convert("RGBA").resize(new, Image.LANCZOS)
    canvas.alpha_composite(placed, ((target_w - new[0]) // 2, (target_h - new[1]) // 2))
    return canvas


def export_production(
    print_img: Image.Image,
    out_path: Path,
    width_cm: float = 30.0,
    height_cm: float = 40.0,
    dpi: int = 300,
) -> dict:
    target_w = cm_to_px(width_cm, dpi)
    target_h = cm_to_px(height_cm, dpi)

    canvas = _place_on_canvas(print_img, target_w, target_h)

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


def _flatten(canvas: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    """把透明 RGBA 画布压平到不透明底色上(JPG/PDF 不支持 alpha,必须压平)。"""
    flat = Image.new("RGB", canvas.size, bg)
    flat.paste(canvas, mask=canvas.split()[3])
    return flat


Box = tuple[int, int, int, int]


def _compose(
    print_img: Image.Image,
    canvas_w: int,
    canvas_h: int,
    trim_box: Box,
    safe_box: Box,
    scale: str,
    anchor: str,
) -> Image.Image:
    """按排版模式+锚点把设计稿放到全幅(含出血)透明画布上。

    - contain(适应):缩放到【安全区】内,留白,可上采样。
    - cover(填满):缩放到【全画布】铺满,溢出裁切(用于满版出血)。
    - actual(原寸):不缩放,按原像素放到【裁切区】内,超出自动裁切。
    水平恒居中;竖直由 anchor(center/top/bottom)决定。
    """
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    pw, ph = print_img.size
    if scale == "cover":
        ref: Box = (0, 0, canvas_w, canvas_h)
        s = max(canvas_w / pw, canvas_h / ph)
    elif scale == "actual":
        ref = trim_box
        s = 1.0
    else:  # contain
        bx0, by0, bx1, by1 = safe_box
        ref = safe_box
        s = min((bx1 - bx0) / pw, (by1 - by0) / ph)

    nw, nh = max(1, int(pw * s)), max(1, int(ph * s))
    placed = print_img.convert("RGBA")
    if (nw, nh) != (pw, ph):
        placed = placed.resize((nw, nh), Image.LANCZOS)

    rx0, ry0, rx1, ry1 = ref
    x = rx0 + ((rx1 - rx0) - nw) // 2  # 水平居中
    if anchor == "top":
        y = ry0
    elif anchor == "bottom":
        y = ry1 - nh
    else:
        y = ry0 + ((ry1 - ry0) - nh) // 2

    # paste(带 mask) 能自动裁切越界/负偏移,cover/actual 溢出安全。
    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    layer.paste(placed, (x, y), placed)
    return Image.alpha_composite(canvas, layer)


def _save_one(
    canvas: Image.Image, flat: Image.Image, fmt: str, path: Path, dpi: int, cmyk: bool
) -> None:
    """按格式保存单个生产文件。canvas=透明 RGBA;flat=已压平 RGB(JPG/PDF/CMYK 用)。

    cmyk=True 时对 jpg/tiff/pdf 做【近似】CMYK 转换(无 ICC);png 不支持 CMYK,始终 RGBA。
    """
    if fmt == "png":
        canvas.save(path, format="PNG", dpi=(dpi, dpi))
    elif fmt == "tiff":
        img = flat.convert("CMYK") if cmyk else canvas
        img.save(path, format="TIFF", dpi=(dpi, dpi), compression="tiff_lzw")
    elif fmt == "jpg":
        img = flat.convert("CMYK") if cmyk else flat
        img.save(path, format="JPEG", quality=95, dpi=(dpi, dpi))
    elif fmt == "pdf":
        img = flat.convert("CMYK") if cmyk else flat
        img.save(path, format="PDF", resolution=float(dpi))
    else:  # pragma: no cover - 上游已过滤
        raise ValueError(f"unsupported format: {fmt}")


def _font(size: int) -> ImageFont.ImageFont:
    """取一个可读字号的默认字体(Pillow<10 不支持 size 参数则退化)。"""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - 老版本 Pillow
        return ImageFont.load_default()


def _dash_rect(draw: ImageDraw.ImageDraw, box: Box, color, width: int, dash: int) -> None:
    """画虚线矩形(ImageDraw 无原生虚线,手动分段)。"""
    x0, y0, x1, y1 = box
    for x in range(x0, x1, dash * 2):
        draw.line([(x, y0), (min(x + dash, x1), y0)], fill=color, width=width)
        draw.line([(x, y1), (min(x + dash, x1), y1)], fill=color, width=width)
    for y in range(y0, y1, dash * 2):
        draw.line([(x0, y), (x0, min(y + dash, y1))], fill=color, width=width)
        draw.line([(x1, y), (x1, min(y + dash, y1))], fill=color, width=width)


def _make_proof(canvas: Image.Image, trim_box: Box, safe_box: Box, meta: dict) -> Image.Image:
    """生成打样核对图:白底缩略 + 裁切线(红)/安全区(绿虚线)/出血边(蓝虚线=画布边)+ 尺寸标注。"""
    flat = _flatten(canvas, (255, 255, 255))
    long_side = max(flat.size)
    k = min(1.0, 1400 / long_side)
    if k < 1.0:
        flat = flat.resize((max(1, int(flat.width * k)), max(1, int(flat.height * k))), Image.LANCZOS)

    def sc(b: Box) -> Box:
        return (int(b[0] * k), int(b[1] * k), int(b[2] * k), int(b[3] * k))

    proof = flat.convert("RGB")
    d = ImageDraw.Draw(proof)
    cw, ch = proof.size
    # 出血边 = 画布外缘(蓝虚线);裁切线(红实线);安全区(绿虚线)
    if meta["bleed_mm"] > 0:
        _dash_rect(d, (1, 1, cw - 2, ch - 2), (40, 120, 230), 2, 14)
    d.rectangle(sc(trim_box), outline=(220, 40, 40), width=2)
    if meta["safe_mm"] > 0:
        _dash_rect(d, sc(safe_box), (40, 170, 70), 2, 14)

    # 标注文字(左上角半透明底条)
    f = _font(20)
    lines = [
        f"成品净尺寸 {meta['width_cm']}×{meta['height_cm']}cm @ {meta['dpi']}DPI",
        f"出血 {meta['bleed_mm']}mm · 安全边 {meta['safe_mm']}mm · 排版 {meta['scale']}/{meta['anchor']}",
        f"全幅(含出血) {meta['canvas_w_px']}×{meta['canvas_h_px']}px · {meta['color_mode']}",
        "红=裁切线  绿虚=安全区  蓝虚=出血边",
    ]
    pad = 8
    bw = max(d.textlength(t, font=f) for t in lines) + pad * 2
    bh = (20 + 4) * len(lines) + pad
    strip = Image.new("RGBA", (int(bw), int(bh)), (0, 0, 0, 160))
    proof.paste(strip, (8, 8), strip)
    for i, t in enumerate(lines):
        d.text((8 + pad, 8 + pad + i * 24), t, fill=(255, 255, 255), font=f)
    return proof


def export_production_multi(
    print_img: Image.Image,
    out_dir: Path,
    name_base: str = "production",
    width_cm: float = 30.0,
    height_cm: float = 40.0,
    dpi: int = 300,
    formats: tuple[str, ...] = SUPPORTED_FORMATS,
    bg: tuple[int, int, int] = (255, 255, 255),
    bleed_mm: float = 0.0,
    safe_mm: float = 0.0,
    scale: str = "contain",
    anchor: str = "center",
    cmyk: bool = False,
    proof: bool = False,
) -> dict:
    """工厂级多格式导出。返回 `{"files": {fmt: filename}, "proof": filename|None, "meta": {...}}`。

    几何:成品净尺寸=width×height(裁切区);四周加 bleed(出血)→ 全幅画布;安全区=裁切区内缩 safe。
    生产文件输出为【全幅(含出血)】尺寸。formats/scale/anchor 须合法(上游校验)。
    cmyk 仅对 jpg/tiff/pdf 生效(近似,无 ICC)。proof=True 额外出一张打样核对图。
    超过 MAX_PX 抛 ValueError(防 OOM,路由层转 400)。
    """
    trim_w = cm_to_px(width_cm, dpi)
    trim_h = cm_to_px(height_cm, dpi)
    bleed = mm_to_px(bleed_mm, dpi)
    safe = mm_to_px(safe_mm, dpi)
    canvas_w = trim_w + 2 * bleed
    canvas_h = trim_h + 2 * bleed
    if trim_w < 1 or trim_h < 1:
        raise ValueError("目标尺寸过小")
    # 安全边过大导致安全区塌缩 → 视为参数错误
    if 2 * safe >= min(trim_w, trim_h):
        raise ValueError("安全边过大,已超过成品尺寸的一半")
    if canvas_w * canvas_h > MAX_PX:
        raise ValueError(f"全幅像素 {canvas_w}×{canvas_h} 超过上限,请调小尺寸/出血/DPI")

    trim_box: Box = (bleed, bleed, bleed + trim_w, bleed + trim_h)
    safe_box: Box = (bleed + safe, bleed + safe, canvas_w - bleed - safe, canvas_h - bleed - safe)

    scale = scale if scale in SCALE_MODES else "contain"
    anchor = anchor if anchor in ANCHORS else "center"
    canvas = _compose(print_img, canvas_w, canvas_h, trim_box, safe_box, scale, anchor)
    flat = _flatten(canvas, bg)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "width_px": canvas_w,            # 输出文件实际尺寸(含出血)
        "height_px": canvas_h,
        "trim_w_px": trim_w,             # 成品净尺寸(裁切后)
        "trim_h_px": trim_h,
        "canvas_w_px": canvas_w,
        "canvas_h_px": canvas_h,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "dpi": dpi,
        "bleed_mm": bleed_mm,
        "safe_mm": safe_mm,
        "scale": scale,
        "anchor": anchor,
        "color_mode": "CMYK(近似·无ICC)" if cmyk else "RGB",
    }

    files: dict[str, str] = {}
    for fmt in formats:
        if fmt not in SUPPORTED_FORMATS:
            continue
        fname = f"{name_base}.{fmt}"
        _save_one(canvas, flat, fmt, out_dir / fname, dpi, cmyk)
        files[fmt] = fname
    meta["formats"] = list(files.keys())

    proof_name: str | None = None
    if proof:
        proof_name = f"{name_base}_proof.jpg"
        _make_proof(canvas, trim_box, safe_box, meta).save(
            out_dir / proof_name, format="JPEG", quality=88
        )

    (out_dir / f"{name_base}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"files": files, "proof": proof_name, "meta": meta}
