"""文生图 / 图生图 / 换装换背景 — 有 gpt-image key 走 AI,无 key 走本地程序化引擎。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from ..ai.openai_image import OpenAIImageClient
from ..config import settings
from . import effects

# POD 印花常见的背景/风格关键词(用于判断 prompt 是否"写全了")
_BG_KEYS = ("background", "背景", "白底", "底色", "transparent", "透明")
_STYLE_KEYS = ("style", "风格", "sticker", "贴纸", "flat", "cartoon", "卡通",
               "illustration", "插画", "realistic", "写实", "watercolor", "水彩", "line art")
# 商品图常见关键词(用于判断 prompt 是否已带商业摄影/商品语义)
_PRODUCT_KEYS = ("photo", "摄影", "product", "商品", "实拍", "commercial", "lifestyle")

# 「商品图·一组」固定分镜:(slug 文件名, 中文标签, 拼到用户描述后的英文提示词)。
# 一次出 5 张电商常用图:白底主图 / 尺寸示意 / 场景生活照 / 细节特写 / 上身穿着。
PRODUCT_SHOTS: list[tuple[str, str, str]] = [
    ("white", "白底图",
     "professional e-commerce main product photo on a clean pure white seamless background, "
     "studio lighting, product centered, sharp focus, high detail"),
    ("size", "尺寸图",
     "product dimension infographic, the product shown with measurement annotations, ruler lines "
     "and size labels, clean white background, e-commerce listing style"),
    ("scene", "场景图",
     "lifestyle scene photograph of the product placed in a realistic everyday environment, "
     "natural soft lighting, contextual background, commercial photography"),
    ("detail", "细节图",
     "extreme close-up macro detail shot of the product highlighting material texture, stitching "
     "and craftsmanship, shallow depth of field, studio lighting"),
    ("wear", "穿着图",
     "a model naturally showcasing or wearing the product in a lifestyle pose, full commercial "
     "fashion photography, soft natural light"),
]
# 一组的张数(=打包计费的"折算笔数"由路由层决定,不在此)
SET_SHOT_COUNT = len(PRODUCT_SHOTS)


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


def refine_product_prompt(prompt: str) -> tuple[str, str | None]:
    """商品图(实拍/电商风)的描述补全。与印花不同:不强加『白底』
    (各分镜自带背景),只确保有商业摄影质感,降低出垃圾图概率。"""
    p = (prompt or "").strip()
    if not p:
        used = "a commercial product, professional e-commerce product photography, high detail"
        return used, "描述为空,已用默认商品描述生成。建议写明:商品 + 材质/风格 + 卖点。"
    plain = p.replace(" ", "").lower()
    if any(k in plain for k in _PRODUCT_KEYS):
        return p, None
    used = p + ", professional product commercial photography, high detail"
    return used, "(已补『商业摄影质感』引导;想要其它效果把描述写具体即可)"


def refine_generate_prompt(prompt: str, gen_type: str = "print") -> tuple[str, str | None]:
    """按生成类型分流补全:商品图走 refine_product_prompt,印花走 refine_prompt。"""
    if gen_type == "product":
        return refine_product_prompt(prompt)
    return refine_prompt(prompt)


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


def generate_product_set(prompt: str, size: str = "1024x1024") -> list[tuple[str, str, Image.Image]]:
    """商品图·一组:按 PRODUCT_SHOTS 出 5 张分镜,返回 [(slug, 中文标签, Image), ...]。

    有 key 时**并行**调 gpt-image(同 make_variants 的理由:单次 edit/generate 是阻塞网络 I/O,
    串行 5 次极易触发超时;并行后墙钟≈单次)。任一调用失败时 list() 迭代抛出,交由上层退回全部已扣点。
    无 key 时本地程序化(每个分镜 prompt 不同 → 出可区分的图)。"""
    if settings.openai_api_key:
        client = OpenAIImageClient()

        def _gen(shot: tuple[str, str, str]) -> Image.Image:
            return client.generate(f"{prompt}, {shot[2]}", size=size)

        with ThreadPoolExecutor(max_workers=min(SET_SHOT_COUNT, 5)) as ex:
            imgs = list(ex.map(_gen, PRODUCT_SHOTS))
    else:
        imgs = [text_to_image(f"{prompt}, {shot[2]}", size=size) for shot in PRODUCT_SHOTS]
    return [(shot[0], shot[1], im) for shot, im in zip(PRODUCT_SHOTS, imgs, strict=True)]


def image_to_image(image: Image.Image, prompt: str,
                   mask: Image.Image | None = None, size: str = "auto") -> Image.Image:
    """图生图 / 改图 / 换装 / 换背景。mask 可选(指定要重绘的区域)。"""
    return OpenAIImageClient().edit(image, prompt, mask=mask, size=size)
