"""网关对话统一入口(chat.completions,支持文本 + 视觉 image_url)。

红线:`services/` 层【不直接 import openai】,一律经此调用(可插拔——换厂商只改这一层)。
并发受全局自适应限流器(openai_image._API_GATE)约束;重依赖 openai 惰性 import;无 key 抛错(调用方降级/退点)。
"""
from __future__ import annotations

from ..config import settings


def chat(messages: list, *, model: str | None = None, temperature: float | None = None,
         timeout: float | None = None, stream: bool | None = None) -> str:
    """调网关 chat.completions,返回 content 文本(已 strip)。无 key 抛 RuntimeError。

    messages: OpenAI 消息格式(content 可为纯文本或含 image_url 的多模态数组)。
    temperature 留空=网关默认;stream 留空=按 settings.openai_text_stream。
    """
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key")
    from openai import OpenAI  # 惰性(重依赖)

    from .openai_image import _API_GATE  # 复用全局自适应并发限流器
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None,
                    timeout=timeout if timeout is not None else settings.openai_timeout)
    kwargs: dict = {"model": model or settings.openai_text_model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature   # 网关不支持时会被忽略,无副作用
    use_stream = settings.openai_text_stream if stream is None else stream
    with _API_GATE:
        if use_stream:
            content = ""
            for ch in client.chat.completions.create(stream=True, **kwargs):
                if ch.choices and ch.choices[0].delta:
                    content += ch.choices[0].delta.content or ""
            return content.strip()
        resp = client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()
