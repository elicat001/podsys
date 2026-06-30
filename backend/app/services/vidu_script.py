"""Vidu「智能识别」:看商品图 → 写一条【真人在生活场景里使用/把玩这个商品】的 Vidu 提示词。

本版定位(配合「场景母帧」):产出的是【真人上手互动】的动作脚本,而不是产品平铺展示。
关键是【视觉自适应、绝不硬编码】:模型自己看图判断这是什么商品、它最自然的真实把玩/使用方式
(解压球/指尖陀螺→按压、旋转、把玩;捏捏乐→捏压回弹;杯子→端起喝;T恤→穿在身上走动…),
再写成真人做这个动作 + 该动作的真实物理效果(旋转模糊 / 捏压形变回弹 / 垂坠)的短片。**不写死任何品类或动作。**

复用网关视觉模型(chat.completions + image_url);惰性 import openai,保持离线启动轻量。
无 key / 调用失败 → 抛异常,端点降级退点。
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from ..config import settings


def describe_multishot(image: Image.Image, seconds: int = 10, language: str = "葡萄牙语",
                       selling_points: str = "") -> str:
    """视觉识别商品 + 它最自然的把玩/使用方式 → 生成一条【真人上手互动】的 Vidu 提示词(只返回中文正文)。
    ⚠ 全靠看图自适应:商品是什么、谁在用、怎么用、产生什么物理效果,都由模型判断,绝不套固定模板/不硬编码品类动作。"""
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key(智能识别需要作图的网关 key)")

    im = image.convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    sp = (selling_points or "").strip()[:500]
    sp_line = (
        f"⓪ 【最高优先级 · 卖家提供的卖点】{sp}\n"
        "   动作设计要把这些卖点【视觉化】(用真人的具体互动动作体现),与图明显冲突的以图为准;\n"
    ) if sp else ""
    prompt = (
        "你是 Vidu 图生视频的提示词专家。请仔细观察这张商品图,为它写一条【真人在真实生活场景里上手使用/把玩这个商品】"
        f"的 {seconds} 秒短视频中文提示词。\n硬性要求:\n"
        + sp_line +
        "① 【看图自适应,绝不套模板】先判断这到底是什么商品、它最自然真实的把玩/使用方式是什么,再据此写动作:\n"
        "   例如——解压玩具/指尖陀螺/旋转球 → 真人用手按压顶部并拨动让它原地高速旋转、可见明显旋转动态模糊;\n"
        "   捏捏乐/软胶玩具 → 用手捏压、松手回弹形变;杯子/水壶 → 端起来喝或拿在手里;服饰 → 穿在身上自然走动/转身;\n"
        "   (以上只是示例,真实动作必须由你看图判断,**不要照搬、不要套固定动作**);\n"
        "② 一个真实的人(年龄/性别贴合该商品目标用户,由你判断)在贴合的生活场景里自然做这个动作,神态放松、像随手拍;\n"
        "③ 写出该动作的【真实物理效果】(如旋转模糊、捏压回弹、布料垂坠),让画面有动感而非静止摆拍;\n"
        "④ 动作贴合 AI 视频能力边界:高风险物理变化(开盖/拆封/穿脱/倾倒)可简化,但低风险的连续动作要【大胆做、有真实运动幅度】,"
        "手与物体接触、符合重力,绝不出现部件自行开合或物体凭空出现/消失;商品的图案、文字、颜色必须保持一致、不被改样;\n"
        "⑤ 控制在 50-130 字(Vidu 官方建议,过长会分散模型注意力);\n"
        "⑥ 只输出中文提示词正文,不要解释、标题、前后缀或 markdown 代码块。"
    )
    from openai import OpenAI  # 惰性
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None,
                    timeout=settings.openai_timeout)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}]
    from ..ai.openai_image import _API_GATE  # 复用全局网关并发信号量限流
    with _API_GATE:
        resp = client.chat.completions.create(model=settings.openai_text_model, messages=msgs)
        content = (resp.choices[0].message.content or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lstrip().lower().startswith("text"):
            content = content.lstrip()[4:]
    return content.strip()
