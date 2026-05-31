"""文生图 / 图生图 / 换装换背景 — 有 gpt-image key 走 AI,无 key 走本地程序化引擎。"""
from __future__ import annotations
from PIL import Image
from ..ai.openai_image import OpenAIImageClient
from ..config import settings
from . import effects


def text_to_image(prompt: str, size: str = "1024x1024", quality: str = "auto") -> Image.Image:
    if settings.openai_api_key:
        return OpenAIImageClient().generate(prompt, size=size, quality=quality)
    # 无 key:程序化图案生成(prompt 决定配色与构图,产出真实可区分的图)
    px = 1024
    try:
        px = int(str(size).lower().split("x")[0])
    except Exception:
        pass
    return effects.procedural_pattern(prompt, min(max(px, 256), 1280))


def image_to_image(image: Image.Image, prompt: str,
                   mask: Image.Image | None = None, size: str = "auto") -> Image.Image:
    """图生图 / 改图 / 换装 / 换背景。mask 可选(指定要重绘的区域)。"""
    return OpenAIImageClient().edit(image, prompt, mask=mask, size=size)
