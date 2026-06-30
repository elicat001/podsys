"""智能识别:看上传的商品图,自动生成一段贴合该商品的 TikTok 视频「镜头脚本」。

复用网关视觉模型(同 ip_guard 的 `chat.completions` + image_url 模式,零本地模型)。
用 `services` 层、惰性 import openai,保持离线启动轻量。无 key / 调用失败 → 抛异常,端点降级退点。
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from ..config import settings
from .video_continuity import CONTINUITY_GUIDE


def smart_describe(image: Image.Image, video_type: str = "开箱分享", seconds: int = 10,
                   language: str = "葡萄牙语", category: str = "通用",
                   selling_points: str = "") -> str:
    """视觉识别商品 → 生成 seconds 秒的「video_type」风格分镜脚本(只返回中文脚本正文)。
    selling_points:卖家手填的产品卖点(可空);非空时脚本必须围绕这些卖点把功能视觉化。"""
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key(智能识别需要作图的网关 key)")

    im = image.convert("RGB")
    im.thumbnail((768, 768))                        # 压小省 token
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    sp = (selling_points or "").strip()[:500]
    sp_line = (
        f"⓪ 【最高优先级 · 卖家提供的产品卖点】{sp}\n"
        "   整段脚本必须紧紧围绕这些卖点来设计画面与展示动作,把每条卖点【视觉化】——用镜头特写、人物动作、"
        "使用场景去体现它(而不是单纯堆砌形容词或念出来);若某条卖点与图中商品明显冲突,以图为准、忽略该条;\n"
    ) if sp else ""
    prompt = (
        "你是 TikTok 跨境电商短视频的分镜脚本专家。请仔细观察这张商品图,为它写一段视频「镜头脚本」。\n"
        "硬性要求:\n"
        + sp_line +
        f"① 视频时长 {seconds} 秒,严格按 {seconds} 秒的节奏分时间轴(如【0-2秒】【2-4秒】…),动作连续、有趣、真实、能抓人;\n"
        f"② 风格为「{video_type}」;商品类目「{category}」,要紧扣图中这件【具体商品】的外观/品类/卖点来设计画面与动作;\n"
        "③ 【任务驱动·最重要】把视频当成【记录一个真实生活片段】,而不是「展示商品」:先按这件商品的真实用法,"
        "想清楚人物此刻最可能在什么生活情境里用它(居家独处/工作学习间隙/运动前后/下厨吃喝/出门通勤/户外旅途/睡前清晨…"
        "按商品选最贴切的那个,别千篇一律都写成出门),再用具体的【任务动作】演出来"
        "(动作由情境和商品用法决定,比「拉衣角/摆 pose」这类模特动作更真实);商品是这件事里自然穿用的道具;"
        "别为展示商品而僵硬摆拍,口播类可自然对镜头说话;镜头次之,手持随手拍即可;\n"
        "④ 按商品真实用法大胆设计【有真实运动幅度、有进展】的自然动作、让画面有动感而非几乎不动;"
        "让手与物体接触、动作连续且符合重力,绝不出现部件无人触碰却自行开合、或物体凭空出现/消失/移动;\n"
        + CONTINUITY_GUIDE + "\n"
        "⑤ 只输出中文脚本正文,不要任何解释、标题、前后缀或 markdown 代码块。"
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
    if content.startswith("```"):                   # 容错剥 ``` 围栏
        content = content.strip("`")
        if content.lstrip().lower().startswith("text"):
            content = content.lstrip()[4:]
    return content.strip()
