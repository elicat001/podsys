"""套图&标题&来图定制工具组(E3)。

- 标题提取:有 OpenAI key 时调 **文本** 模型(chat.completions)生成电商标题 +
  关键词;无 key 时降级为基于入参拼的占位标题(不调 AI、不扣点)。
- 模特试衣 / 宠物换装 / 合照:复用 gpt-image edit(`OpenAIImageClient.edit`),
  无 key 时构造客户端即抛 RuntimeError -> 路由层 502 + 退点。

只暴露纯逻辑 / AI 封装,鉴权与扣点由 router 层负责。
"""
from __future__ import annotations

import base64
import io
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
# 吸引人的电商标题 system prompt(识图 + SEO + 转化导向)
_TITLE_SYS_PROMPT = (
    "You are a top-converting e-commerce copywriter for print-on-demand merch "
    "(t-shirts, mugs, phone cases, totes, etc.). From the design image (if provided) "
    "and any seller keywords, write ONE catchy, scroll-stopping product listing title "
    "that makes shoppers want to click—while staying keyword-rich for search. "
    "Identify the ACTUAL subject, character, text or visual style you see; "
    "do NOT use generic filler like 'Apparel Collection', 'Everyday Wear' or 'Stylish Clothing'. "
    "Natural, human tone. Under 140 characters. "
    'Respond with JSON only: {"title":"...","keywords":["5-10 specific search terms"]}.'
)


def _img_data_url(img: Image.Image, max_side: int = 512) -> str:
    """把图压到 ≤max_side 的 JPEG 再 base64(省 token/提速),供视觉模型识图。"""
    im = img.convert("RGB")          # convert 返回新图,不改原图
    im.thumbnail((max_side, max_side))  # 只缩不放,保持比例
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _placeholder_title(keywords: str, category: str, img: Image.Image | None = None) -> dict:
    """无 key/无文本模型时:用本地规则引擎派生(多模板+SEO 修饰词+品类话术+主色调)。

    给了图就用图的主色调当前缀(如 Minimalist/Bold Black/具体色名),否则按风格词。
    """
    from . import effects
    r = effects.smart_title(img, keywords=keywords, category=category)
    return {"title": r["title"], "keywords": r["keywords"], "degraded": True}


def generate_title(keywords: str = "", category: str = "apparel",
                   img: Image.Image | None = None) -> dict:
    """生成电商标题 + 关键词。

    返回 `{title, keywords:[...], degraded:bool}`。
    - 无 key:降级占位(degraded=True),调用方据此**不扣点**。
    - 有 key:调 `settings.openai_text_model`(默认 gpt-5.4-mini,本网关需流式);
      **传了图就让模型识图**(图压到 512px JPEG 省 token),结合关键词出吸引人的 SEO 标题;成功 degraded=False。
    - **有 key 但模型不可用 / 调用或解析失败** → 自动降级本地占位(degraded=True),
      不再抛错,调用方据 degraded 退点。
    """
    if not has_openai_key():
        return _placeholder_title(keywords, category, img)

    try:
        from openai import OpenAI  # lazy import

        client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
        user_text = (
            f"category: {category}\n"
            f"seller keywords (optional hints): {keywords.strip() or '(none)'}"
        )
        # 传了图 → 视觉消息(识图);没图 → 纯文本(靠关键词)
        if img is not None:
            user_content: object = [
                {"type": "text", "text": user_text
                 + "\nLook at the attached design image and base the title on what you SEE in it."},
                {"type": "image_url", "image_url": {"url": _img_data_url(img)}},
            ]
        else:
            user_content = user_text
        messages = [
            {"role": "system", "content": _TITLE_SYS_PROMPT},
            {"role": "user", "content": user_content},
        ]
        # 本网关 chat 必须 stream 才吐内容(非流式返回空 choices);累加 delta 取全文。
        if settings.openai_text_stream:
            content = ""
            stream = client.chat.completions.create(
                model=settings.openai_text_model, messages=messages, stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta:
                    content += chunk.choices[0].delta.content or ""
            content = content.strip()
        else:
            resp = client.chat.completions.create(
                model=settings.openai_text_model, messages=messages,
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
    except Exception:  # noqa: BLE001  文本模型不可用(网关只有画图模型)/调用/解析失败 → 降级本地占位
        return _placeholder_title(keywords, category, img)


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
