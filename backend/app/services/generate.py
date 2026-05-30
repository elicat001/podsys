"""文生图 / 图生图 / 换装换背景 — all via the gpt-image ("image2") client."""
from __future__ import annotations
from PIL import Image
from ..ai.openai_image import OpenAIImageClient


def text_to_image(prompt: str, size: str = "1024x1024", quality: str = "auto") -> Image.Image:
    return OpenAIImageClient().generate(prompt, size=size, quality=quality)


def image_to_image(image: Image.Image, prompt: str,
                   mask: Image.Image | None = None, size: str = "auto") -> Image.Image:
    """图生图 / 改图 / 换装 / 换背景。mask 可选(指定要重绘的区域)。"""
    return OpenAIImageClient().edit(image, prompt, mask=mask, size=size)
