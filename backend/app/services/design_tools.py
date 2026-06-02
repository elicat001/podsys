"""印花设计工具(E1):图裂变 / 元素融合 / 风格转绘 / 梗图印花。

全部走 gpt-image edit(`app/ai/openai_image.py` 的 `OpenAIImageClient.edit`)。
本模块只负责「prompt 拼装 + gpt 调用」,router 负责鉴权/扣点/退点/存盘。
无 OpenAI key 时 `OpenAIImageClient()` 构造即抛 RuntimeError,由 router 捕获后 502+退点。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from ..ai.openai_image import OpenAIImageClient
from ..config import settings
from . import effects


def _has_key() -> bool:
    return bool(settings.openai_api_key)


def _downscale_for_edit(image: Image.Image, max_side: int = 1024) -> Image.Image:
    """发给 gpt-image edit 前等比缩小:输出最大 1024×1536,发超大原图(用户照片常 18~20MP)
    纯属浪费上传体积与网关处理时间(实测大图比小图慢数倍)。缩到最长边 ≤ max_side,
    上传从数 MB 降到 ~1MB,产出质量不受影响(输出本就 ≤1024)。"""
    m = max(image.size)
    if m <= max_side:
        return image
    s = max_side / m
    return image.resize((max(1, round(image.width * s)), max(1, round(image.height * s))), Image.LANCZOS)


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
    """图裂变:生成 n 个变体。

    有 key 时**并行**调用 gpt-image:单次 edit 是阻塞网络 I/O(实测网关 ~80s),
    串行 n 次会让整个请求达到 ~n×单次,极易触发浏览器/网关超时(前端表现为
    "Failed to fetch")。并行后墙钟≈单次。SDK 客户端(httpx 连接池)线程安全、可跨线程复用。
    任一调用失败时 list() 迭代会抛出,交由 router 退回全部已扣点。
    """
    if _has_key():
        client = _client(); p = variants_prompt(prompt)
        src = _downscale_for_edit(image)  # 大图先缩,显著降低上传+网关耗时(输出≤1024,质量不变)
        with ThreadPoolExecutor(max_workers=min(n, 4)) as ex:
            return list(ex.map(lambda _: client.edit(src, p), range(n)))
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
