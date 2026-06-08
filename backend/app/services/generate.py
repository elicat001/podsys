"""文生图 / 图生图 / 换装换背景 — 有 gpt-image key 走 AI,无 key 走本地程序化引擎。"""
from __future__ import annotations
from PIL import Image
from ..ai.openai_image import OpenAIImageClient
from ..config import settings
from . import effects

# POD 印花常见的背景/风格关键词(用于判断 prompt 是否"写全了")
_BG_KEYS = ("background", "背景", "白底", "底色", "transparent", "透明")
_STYLE_KEYS = ("style", "风格", "sticker", "贴纸", "flat", "cartoon", "卡通",
               "illustration", "插画", "realistic", "写实", "watercolor", "水彩", "line art")


def refine_prompt(prompt: str) -> tuple[str, str | None]:
    """对偏薄的 prompt 做温和补全,返回(实际使用的 prompt, 给用户的提示)。

    透明、不黑箱:补了什么、为什么补,都通过返回的提示告诉用户。
    - 空 → 用一个安全的默认印花描述。
    - 很短(如"柯基印花")→ 补「风格 + 高细节」引导,降低出垃圾图概率。
    - 没提背景 → 补「白底」(POD 印花通常需要干净背景)。
    """
    p = (prompt or "").strip()
    if not p:
        used = "a simple flat sticker print design, white background, high detail"
        return used, "描述为空,已用默认印花描述生成。建议写明:主体 + 风格 + 背景。"

    adds: list[str] = []
    notes: list[str] = []
    plain = p.replace(" ", "").lower()
    if len(plain) < 6 and not any(k in plain for k in _STYLE_KEYS):
        adds.append("flat sticker illustration, high detail")
        notes.append("描述较短已补风格引导")
    if not any(k in plain for k in _BG_KEYS):
        adds.append("white background")
        notes.append("未提背景已补『白底』")

    if not adds:
        return p, None
    used = p + ", " + ", ".join(adds)
    hint = "(" + ";".join(notes) + ";想要其它效果把描述写具体即可)"
    return used, hint


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
