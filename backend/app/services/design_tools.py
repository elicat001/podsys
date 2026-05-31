"""印花设计工具(E1):图裂变 / 元素融合 / 风格转绘 / 梗图印花。

全部走 gpt-image edit(`app/ai/openai_image.py` 的 `OpenAIImageClient.edit`)。
本模块只负责「prompt 拼装 + gpt 调用」,router 负责鉴权/扣点/退点/存盘。
无 OpenAI key 时 `OpenAIImageClient()` 构造即抛 RuntimeError,由 router 捕获后 502+退点。
"""
from __future__ import annotations

from PIL import Image

from ..ai.openai_image import OpenAIImageClient
from ..config import settings
from . import effects


def _has_key() -> bool:
    return bool(settings.openai_api_key)


def _client() -> OpenAIImageClient:
    """惰性构造客户端;无 key 时抛 RuntimeError。"""
    return OpenAIImageClient()


def variants_prompt(extra: str = "") -> str:
    """图裂变 prompt:保留主体设计,变换配色/排版/风格生成爆款变体。"""
    base = (
        "Keep the main subject and core design of this print intact. "
        "Generate a bestseller variant by changing the color palette, layout and "
        "overall style so it looks like a fresh, eye-catching POD print design."
    )
    extra = (extra or "").strip()
    return f"{base} {extra}".strip() if extra else base


def fuse_prompt(prompt: str) -> str:
    """元素融合 prompt:把输入图与给定 prompt 融合出新爆款印花。"""
    prompt = (prompt or "").strip()
    return (
        "Fuse the elements of this image with the following idea to create a new "
        f"bestseller POD print design: {prompt}. Blend them naturally into a single "
        "cohesive, commercially appealing print."
    )


def restyle_prompt(style: str) -> str:
    """风格转绘 prompt:用目标风格重绘整张图。"""
    style = (style or "").strip()
    return (
        f"Repaint and restyle this image entirely in the following art style: {style}. "
        "Preserve the main subject and composition while fully adopting the target style, "
        "suitable as a POD print design."
    )


def meme_prompt(text: str, extra: str = "") -> str:
    """梗图印花 prompt:在图上加梗文案排版。"""
    text = (text or "").strip()
    extra = (extra or "").strip()
    base = (
        f'Add a bold, well-laid-out meme caption that reads "{text}" onto this image, '
        "integrating the typography into a trendy meme-style POD print design with clean, "
        "legible text placement."
    )
    return f"{base} {extra}".strip() if extra else base


# 有 OpenAI key → gpt-image(语义更强);无 key → 本地真实引擎(effects),不再报错。
def make_variants(image: Image.Image, n: int, prompt: str = "") -> list[Image.Image]:
    """图裂变:生成 n 个变体。"""
    if _has_key():
        client = _client(); p = variants_prompt(prompt)
        return [client.edit(image, p) for _ in range(n)]
    return effects.colorway_variants(image, n)


def make_fuse(image: Image.Image, prompt: str) -> Image.Image:
    """元素融合。"""
    if _has_key():
        return _client().edit(image, fuse_prompt(prompt))
    return effects.fuse(image, prompt)


def make_restyle(image: Image.Image, style: str) -> Image.Image:
    """风格转绘。"""
    if _has_key():
        return _client().edit(image, restyle_prompt(style))
    return effects.stylize(image, style)


def make_meme(image: Image.Image, text: str, prompt: str = "") -> Image.Image:
    """梗图印花。"""
    if _has_key():
        return _client().edit(image, meme_prompt(text, prompt))
    return effects.caption(image, text)
