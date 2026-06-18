"""智能方案向导:两步生成带货视频脚本。

Step 1「商品信息」:看图 → 结构化商品简报(产品名称 / 目标受众 / 核心卖点)。
Step 2「视频方案」:据简报 → N 个不同方向的方案(标题 / 方向 / 模特 / 环境 / 分镜)。

复用作图网关视觉/文本模型(同 video_describe),`services` 层、惰性 import openai、_API_GATE 限流。
模型返回 JSON,容错解析(剥 ``` 围栏、缺字段兜底、数组被裹进对象也救);无 key / 解析失败 → 抛异常,端点降级退点。
"""
from __future__ import annotations

import base64
import io
import json
import re

from PIL import Image

from ..config import settings


def _data_url(image: Image.Image) -> str:
    im = image.convert("RGB")
    im.thumbnail((768, 768))                        # 压小省 token
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _loads_json(content: str, *, expect_list: bool = False):
    """容错解析模型返回的 JSON:剥 ``` 围栏 → 直接 loads → 失败再截取首个 […]/{…} 子串。"""
    s = (content or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = re.sub(r"^\s*json\s*", "", s, flags=re.IGNORECASE)
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        a, b = ("[", "]") if expect_list else ("{", "}")
        i, j = s.find(a), s.rfind(b)
        if i != -1 and j != -1 and j > i:
            return json.loads(s[i:j + 1])
        raise


def _chat(messages: list) -> str:
    """调网关 chat.completions,返回 content 文本。无 key 抛错。"""
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key(智能向导需要作图的网关 key)")
    from openai import OpenAI  # 惰性

    from ..ai.openai_image import _API_GATE  # 复用全局网关并发信号量限流
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None,
                    timeout=settings.openai_timeout)
    with _API_GATE:
        resp = client.chat.completions.create(model=settings.openai_text_model, messages=messages)
    return (resp.choices[0].message.content or "").strip()


def describe_product(image: Image.Image, selling_points: str = "", language: str = "葡萄牙语") -> dict:
    """Step 1:看商品图 → 结构化简报 {name, audience, selling_points}。卖家手填卖点作补充提示。"""
    sp = (selling_points or "").strip()[:500]
    hint = f"\n卖家补充的卖点(请融合,但以图为准):{sp}" if sp else ""
    prompt = (
        "你是跨境电商选品与文案专家。仔细观察这张商品图,提炼出用于拍带货视频的【商品简报】。\n"
        "只输出一个 JSON 对象(禁止任何解释、标题、markdown 代码块),字段固定为:\n"
        '{"name":"产品名称(简洁,含关键品类与风格)",'
        '"audience":"目标受众(一句话:谁会买、用在什么场景)",'
        '"selling_points":"核心卖点(3~5 条,用、或换行分隔,覆盖材质/卖点/使用/工艺等图中可见或可合理推断的点)"}\n'
        "要求:紧扣图中这件【具体商品】;中文输出;不要编造图中明显没有的信息。" + hint
    )
    data = _loads_json(_chat([{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _data_url(image)}},
    ]}]))
    if not isinstance(data, dict):
        raise RuntimeError("商品简报解析失败")
    return {
        "name": str(data.get("name") or data.get("名称") or "").strip()[:120],
        "audience": str(data.get("audience") or data.get("受众") or "").strip()[:300],
        "selling_points": str(data.get("selling_points") or data.get("卖点") or "").strip()[:1200],
    }


def generate_proposals(name: str, audience: str, selling_points: str, *, seconds: int = 10,
                       language: str = "葡萄牙语", category: str = "通用", n: int = 3) -> list[dict]:
    """Step 2:据简报 → n 个不同方向的视频方案。每个 {title, angle, model, environment, storyboard}。"""
    prompt = (
        f"你是 TikTok 跨境电商短视频策划。根据下面的商品简报,设计 {n} 个【方向彼此明显不同】的 {seconds} 秒带货视频方案。\n"
        f"产品:{name or '(见卖点)'}\n目标受众:{audience or '(自行判断)'}\n核心卖点:{selling_points or '(自行提炼)'}\n"
        f"投放市场/语言:{language};商品类目:{category}\n"
        f"只输出一个 JSON 数组(禁止任何解释、标题、markdown 代码块),含 {n} 个对象,每个对象字段固定为:\n"
        '{"title":"方案标题(简短有画面感,如 温馨家居氛围感)",'
        '"angle":"一句话创意方向",'
        '"model":"出镜模特设定(年龄/外貌/气质/穿着,贴合目标受众与投放市场;若无人出镜写 无模特)",'
        '"environment":"拍摄环境/场景(贴合卖点与市场风格)",'
        f'"storyboard":"完整分镜脚本,严格按 {seconds} 秒分时间轴(如【0-3秒】【3-7秒】…),自成一体地融入上面的模特与环境,'
        "描述镜头语言(推拉摇移/特写)+人物动作+产品展示,紧扣核心卖点;动作要贴合 AI 视频能力边界——"
        '优先简单稳妥的展示类动作,弱化开盖/拆封/穿脱/倾倒等复杂物理变化,手与物体全程接触、符合重力)"}\n'
        f"硬性:{n} 个方案方向要拉开差距(如 情感氛围 / 卖点测评 / 场景搭配 等不同切入);全部字段用中文。"
    )
    data = _loads_json(_chat([{"role": "user", "content": prompt}]), expect_list=True)
    if isinstance(data, dict):                       # 容错:模型把数组裹进 {"proposals":[…]} / {"方案":[…]}
        data = data.get("proposals") or data.get("方案") or data.get("plans") or []
    if not isinstance(data, list):
        raise RuntimeError("方案解析失败")
    out = []
    for p in data[:n]:
        if not isinstance(p, dict):
            continue
        out.append({
            "title": str(p.get("title") or p.get("标题") or "方案").strip()[:40],
            "angle": str(p.get("angle") or p.get("方向") or "").strip()[:100],
            "model": str(p.get("model") or p.get("模特") or "").strip()[:300],
            "environment": str(p.get("environment") or p.get("环境") or p.get("场景") or "").strip()[:300],
            "storyboard": str(p.get("storyboard") or p.get("分镜") or p.get("脚本") or "").strip()[:2000],
        })
    if not out:
        raise RuntimeError("方案解析为空")
    return out
