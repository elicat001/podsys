"""图案处理工具 service 层。

- expand / dewatermark:走 gpt-image edit(在 router 层调用 OpenAIImageClient)。
- compress:纯离线 Pillow,真实可跑 —— 这里实现 compress_image()。
"""
from __future__ import annotations

import io

from PIL import Image

# 目标像素上限(P1-1:防 60000x60000 这类参数把后端 OOM)
MAX_TARGET_PIXELS = 50_000_000  # 5000 万像素(约 7000x7000)

# 支持的输出格式 -> (PIL format, 是否需要 RGB)
_FORMATS: dict[str, tuple[str, bool]] = {
    "png": ("PNG", False),
    "jpeg": ("JPEG", True),
    "jpg": ("JPEG", True),
    "webp": ("WEBP", False),
}


def compress_image(
    img: Image.Image,
    *,
    target_w: int = 0,
    target_h: int = 0,
    quality: int = 85,
    fmt: str = "jpeg",
) -> tuple[Image.Image, bytes, dict]:
    """裁剪/缩放 + 重新编码压缩(纯离线 Pillow)。

    - target_w/target_h:目标宽高,0 表示该维不改(按另一维等比;两维都给则强制到该尺寸)。
    - quality:JPEG/WEBP 的质量(1-100)。
    - fmt:png | jpeg/jpg | webp。jpeg 会转 RGB(丢弃 alpha)。

    返回 (处理后的 PIL.Image, encoded_bytes, info)。info 含 width/height/format/output_bytes。
    调用方应把 encoded_bytes 直接写盘(保证 output_bytes 与文件一致),并补充 original_bytes。
    """
    fmt_key = (fmt or "jpeg").lower()
    if fmt_key not in _FORMATS:
        raise ValueError(f"不支持的格式: {fmt}(可选 png|jpeg|webp)")
    pil_format, needs_rgb = _FORMATS[fmt_key]

    if quality < 1 or quality > 100:
        raise ValueError("quality 必须在 1-100 之间")

    orig_w, orig_h = img.size

    # --- 计算目标尺寸 ---
    if target_w < 0 or target_h < 0:
        raise ValueError("target_w / target_h 不能为负")

    if target_w and target_h:
        new_w, new_h = target_w, target_h
    elif target_w:
        new_w = target_w
        new_h = max(1, round(orig_h * (target_w / orig_w)))
    elif target_h:
        new_h = target_h
        new_w = max(1, round(orig_w * (target_h / orig_h)))
    else:
        new_w, new_h = orig_w, orig_h

    # P1-1:目标尺寸过大直接拒绝,避免分配巨幅画布 OOM
    if new_w * new_h > MAX_TARGET_PIXELS:
        raise ValueError(
            f"目标尺寸过大({new_w}x{new_h}),像素数不得超过 {MAX_TARGET_PIXELS}")

    out = img
    if (new_w, new_h) != (orig_w, orig_h):
        out = img.resize((new_w, new_h), Image.LANCZOS)

    # JPEG 不支持 alpha,需转 RGB(白底合成)
    if needs_rgb and out.mode in ("RGBA", "LA", "P"):
        rgba = out.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        out = bg
    elif needs_rgb and out.mode != "RGB":
        out = out.convert("RGB")

    # --- 编码到字节,量出体积 ---
    save_kwargs: dict = {}
    if pil_format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = quality
    if pil_format == "JPEG":
        save_kwargs["optimize"] = True

    buf = io.BytesIO()
    out.save(buf, format=pil_format, **save_kwargs)
    encoded = buf.getvalue()

    # 用编码后的字节重新打开,保证返回的 Image 与落盘内容一致(模式/格式)
    final = Image.open(io.BytesIO(encoded))
    final.load()

    info = {
        "width": new_w,
        "height": new_h,
        "format": fmt_key if fmt_key != "jpg" else "jpeg",
        "pil_format": pil_format,
        "output_bytes": len(encoded),
        "quality": quality,
    }
    return final, encoded, info
