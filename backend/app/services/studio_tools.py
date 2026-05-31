"""套图&标题&来图定制工具组(E3)。

- 标题提取:有 OpenAI key 时调 **文本** 模型(chat.completions)生成电商标题 +
  关键词;无 key 时降级为基于入参拼的占位标题(不调 AI、不扣点)。
- 模特试衣 / 宠物换装 / 合照:复用 gpt-image edit(`OpenAIImageClient.edit`),
  无 key 时构造客户端即抛 RuntimeError -> 路由层 502 + 退点。

只暴露纯逻辑 / AI 封装,鉴权与扣点由 router 层负责。
"""
from __future__ import annotations

import json

from PIL import Image

from ..ai.openai_image import OpenAIImageClient
from ..config import settings

# ---- 试衣 / 换装 / 合照的 prompt 模板 -------------------------------------
TRYON_PROMPT = (
    "Place this clothing print/design realistically onto a fashion model wearing "
    "the apparel. Keep the design's colors, text and proportions intact. "
    "Studio e-commerce photo, natural lighting, front view."
)


def _pet_prompt(costume: str) -> str:
    return (
        f"Dress the pet in this photo in a {costume} costume. Keep the pet's face "
        "and posture natural and cute. Clean studio background, e-commerce ready."
    )


def has_openai_key() -> bool:
    """是否配置了 OpenAI key(决定 title 是否走 AI / 扣点)。"""
    return bool(settings.openai_api_key)


# ---- 标题提取 -------------------------------------------------------------
def _placeholder_title(keywords: str, category: str) -> dict:
    """无 key(或 AI 失败)时的降级占位标题——纯字符串拼接,不调用任何外部服务。"""
    kw_list = [k.strip() for k in keywords.replace("，", ",").split(",") if k.strip()]
    head = " ".join(kw_list[:4]) if kw_list else "Custom Design"
    cat = (category or "apparel").strip()
    title = f"{head} {cat.title()} - Trendy Print Gift".strip()
    # 占位关键词:入参关键词 + 品类兜底
    kws = kw_list[:8] if kw_list else [cat, "print", "gift", "custom"]
    return {"title": title[:120], "keywords": kws, "degraded": True}


def generate_title(keywords: str = "", category: str = "apparel") -> dict:
    """生成电商标题 + 关键词。

    返回 `{title, keywords:[...], degraded:bool}`。
    - 无 key:降级占位(degraded=True),调用方据此**不扣点**。
    - 有 key:调用 OpenAI 文本模型;成功 degraded=False。失败抛异常,调用方退点。
    """
    if not has_openai_key():
        return _placeholder_title(keywords, category)

    from openai import OpenAI  # lazy import

    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    sys_prompt = (
        "You are an e-commerce SEO copywriter for print-on-demand products. "
        "Given product keywords and a category, output a JSON object with exactly "
        'two fields: "title" (a concise, keyword-rich English listing title, max '
        '140 chars) and "keywords" (an array of 5-10 short search keywords). '
        "Respond with JSON only, no prose."
    )
    user_prompt = f"category: {category}\nkeywords: {keywords}"
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
    )
    content = (resp.choices[0].message.content or "").strip()
    # 模型可能裹在 ```json``` 围栏里,做一次容错剥离
    if content.startswith("```"):
        content = content.strip("`")
        if content.lstrip().lower().startswith("json"):
            content = content.lstrip()[4:]
    data = json.loads(content)
    title = str(data.get("title", "")).strip()
    kws = data.get("keywords") or []
    if isinstance(kws, str):
        kws = [k.strip() for k in kws.split(",") if k.strip()]
    kws = [str(k).strip() for k in kws if str(k).strip()]
    if not title:
        raise ValueError("OpenAI 返回的标题为空")
    return {"title": title[:140], "keywords": kws, "degraded": False}


# ---- gpt-image edit 系(试衣 / 换装 / 合照)-------------------------------
def model_tryon(garment: Image.Image, size: str = "auto") -> Image.Image:
    """模特试衣:服饰印花图 -> 模特上身图。无 key 时构造即抛 RuntimeError。"""
    client = OpenAIImageClient()
    return client.edit(garment, TRYON_PROMPT, size=size)


def pet_costume(pet: Image.Image, costume: str = "royal european",
                size: str = "auto") -> Image.Image:
    """宠物换装。无 key 时构造即抛 RuntimeError。"""
    client = OpenAIImageClient()
    return client.edit(pet, _pet_prompt(costume), size=size)


def group_photo(base: Image.Image, prompt: str, size: str = "auto") -> Image.Image:
    """合照:按 prompt 把主体合成进同一张照片。无 key 时构造即抛 RuntimeError。"""
    client = OpenAIImageClient()
    full = (
        "Create a natural group photo. " + prompt.strip() +
        " Keep faces and subjects realistic and well-composed."
    )
    return client.edit(base, full, size=size)
