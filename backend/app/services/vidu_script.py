"""Vidu「智能识别」:看商品图 → 写一条【Vidu 风格的多镜头分镜 prompt】。

与 video_describe.smart_describe(CogVideoX 用,按【时间轴分段】写、每段一个母帧)的【本质区别】:
Vidu 多镜头是写在【同一条 prompt 内】的——用「景别递进 + 运镜切换 + 场景递进」让 Vidu 原生一次出多镜头,
不拆段、不拼接。所以这里产出的是【一段连贯的导演式描述】,而非分时间轴的步骤表。

复用网关视觉模型(同 video_describe 的 chat.completions + image_url);惰性 import openai,保持离线启动轻量。
无 key / 调用失败 → 抛异常,端点降级退点。
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from ..config import settings


def describe_multishot(image: Image.Image, seconds: int = 10, language: str = "葡萄牙语",
                       selling_points: str = "") -> str:
    """视觉识别商品 → 生成一条 Vidu 多镜头 prompt(只返回中文正文)。
    seconds 决定镜头数:5s≈单镜头;10s≈2 个镜头;15s≈3 个镜头(都在同一条 prompt 里用景别/运镜切换表达)。
    ⚠ 不再吃「商品类目」参数:类目=写死的特定动作来源,已下线;商品形态/动作全靠 AI 看图自适应。"""
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key(智能识别需要作图的网关 key)")

    im = image.convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    shots = 3 if seconds >= 15 else (2 if seconds >= 10 else 1)
    sp = (selling_points or "").strip()[:500]
    sp_line = (
        f"⓪ 【最高优先级 · 卖家提供的卖点】{sp}\n"
        "   整条描述要把这些卖点【视觉化】(用景别特写、动作、使用场景体现),而不是堆形容词;与图明显冲突的以图为准;\n"
    ) if sp else ""
    if shots == 1:
        shot_rule = (
            f"① 这是一条 {seconds} 秒的【单镜头】带货短片:商品为绝对主角,镜头轻缓推近/平移展示,"
            "第一帧就让人读懂在卖什么,落在被自然使用的状态上收尾;\n"
        )
    else:
        shot_rule = (
            f"① 这是一条 {seconds} 秒的【{shots} 个连续镜头】带货短片,商品贯穿全片、始终是主角。"
            f"请在【同一条描述】里用「景别变化 + 运镜切换 + 场景递进」写出这 {shots} 个镜头(不要分时间轴步骤表,"
            "要像一段连贯的导演阐述):例如 镜头一中景出场→镜头二近景特写突出图案细节→"
            f"镜头三切到被自然使用/穿用的生活场景收尾;{shots} 个镜头是同一商品、同一氛围下的连续叙事;\n"
        )
    prompt = (
        "你是 Vidu 视频模型的提示词专家(Vidu 擅长在一条 prompt 内表达多镜头连贯叙事)。请观察这张商品图,"
        "为它写一条 Vidu 图生视频的中文提示词。\n硬性要求:\n"
        + sp_line + shot_rule +
        "② 紧扣图中这件【具体商品】的真实外观/材质/卖点来设计画面,商品形态与动作由你看图自适应(不要套固定模板动作);\n"
        "③ 多用 Vidu 吃的【镜头语言】:景别(全景/中景/近景/特写)、运镜(推近/拉远/小幅环绕/平移/跟拍/固定)、"
        "运动幅度(中等或小幅,稳为主);商品的图案、文字、颜色必须保持一致、不被改样;\n"
        "④ 动作贴合 AI 视频能力边界:优先简单稳妥、易连贯的动作,弱化开盖/拆封/穿脱/倾倒等复杂物理变化,"
        "手与物体全程接触、符合重力,绝不出现部件自行开合或物体凭空出现/消失;\n"
        "⑤ 控制在 60-150 字以内(Vidu 官方建议,过长会分散模型注意力);\n"
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
