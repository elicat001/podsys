"""智能识别:看上传的商品图,自动生成一段贴合该商品的 TikTok 视频「镜头脚本」。

复用网关视觉模型(同 ip_guard 的 `chat.completions` + image_url 模式,零本地模型)。
用 `services` 层、惰性 import openai,保持离线启动轻量。无 key / 调用失败 → 抛异常,端点降级退点。
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from ..config import settings


def smart_describe(image: Image.Image, video_type: str = "开箱分享", seconds: int = 10,
                   language: str = "葡萄牙语", category: str = "通用") -> str:
    """视觉识别商品 → 生成 seconds 秒的「video_type」风格分镜脚本(只返回中文脚本正文)。"""
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key(智能识别需要作图的网关 key)")

    im = image.convert("RGB")
    im.thumbnail((768, 768))                        # 压小省 token
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    prompt = (
        "你是 TikTok 跨境电商短视频的分镜脚本专家。请仔细观察这张商品图,为它写一段视频「镜头脚本」。\n"
        f"硬性要求:\n"
        f"① 视频时长 {seconds} 秒,严格按 {seconds} 秒的节奏分时间轴(如【0-2秒】【2-4秒】…),动作连续、有趣、真实、能抓人;\n"
        f"② 风格为「{video_type}」;商品类目「{category}」,要紧扣图中这件【具体商品】的外观/品类/卖点来设计画面与动作;\n"
        "③ 描述镜头语言(推拉摇移/特写)、人物行为、产品展示动作,而不是堆砌形容词;\n"
        "④ 动作要贴合 AI 视频的能力边界(这是所有商品的通用约束,不只针对某一类):优先用简单稳妥、易连贯呈现的"
        "展示类动作(缓慢旋转展示、镜头推近看细节、手持端起观察、手指划过表面);对开盖/拧盖、拆封开箱、组装拆卸、"
        "穿脱、折叠、倾倒液体等复杂物理状态变化要尽量简化或弱化,务必让手与物体全程接触、动作连续且符合重力,"
        "绝不出现部件无人触碰却自行开合、或物体凭空出现/消失/移动;尽量不让镜头运动与物体操作在同一时刻同时发生;\n"
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
