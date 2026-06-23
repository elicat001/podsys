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
    """Step 2:据简报 → n 个不同方向的视频方案。每个 {title, angle, model, environment, storyboard}。

    seconds=15 → **双分镜**:每个方案额外含 shot1(分镜①·约 5s)+ shot2(分镜②·约 10s),
    storyboard 为两段合并展示。下游(前端)在 15s 时把 shot1/shot2 分别填进 分镜①/分镜② 脚本。"""
    two_shot = seconds == 15
    if two_shot:
        time_rule = (
            "本视频为【双分镜 · 共 15 秒】= 分镜①(0-5 秒)+ 分镜②(5-15 秒),两拍要连成一个【生活小故事/事件】:"
            "分镜①铺垫(准备/起手),分镜②payoff(出门/落座/展示)。"
            "关键:商品是这段真实生活里【自然出现/穿着的道具】,不是被摆拍展示的主角;"
            "两拍必须是【两个明显不同的场景/地点】,绝不要两拍都在同一地方做同一件事。\n"
            "请【完全围绕上面这件具体商品及其卖点/受众/投放市场】原创故事线与两拍场景,"
            "贴合商品真实使用情境(穿戴类→上身/出街,家居/杯具→居家使用,配件→随身日常…),不要套用任何固定模板套路。"
        )
        shot_fields = (
            '"story":"一句话故事线(如 出门前镜子前确认穿搭→走上街头展示)",'
            '"scene1":"分镜①的【场景母帧】:商品自然出现在什么真实生活场景里(给 AI 合成视频第一帧用,只描述场景+人物状态,别写运镜)",'
            '"shot1":"分镜①镜头脚本(约 5 秒,按【0-x秒】分时间轴,这一拍发生的动作)",'
            '"scene2":"分镜②的【场景母帧】:换一个与 scene1 明显不同地点的真实生活场景",'
            '"shot2":"分镜②镜头脚本(约 10 秒,按【0-x秒】分时间轴,承接分镜①、推进故事)",'
            '"storyboard":"两拍合并的完整脚本(仅供预览)"'
        )
    else:
        time_rule = f"视频时长 {seconds} 秒。"
        shot_fields = (
            f'"storyboard":"完整分镜脚本,严格按 {seconds} 秒分时间轴(如【0-3秒】【3-7秒】…),自成一体地融入上面的模特与环境,'
            "描述镜头语言(推拉摇移/特写)+人物动作+产品展示,紧扣核心卖点)\""
        )
    prompt = (
        f"你是 TikTok 跨境电商短视频【内容导演】。根据下面的商品简报,设计 {n} 个【故事方向彼此明显不同】的带货短视频方案。\n"
        "目标:做出『一条真人随手发的 TikTok 生活内容』,而不是『商品展示页』——靠真实生活场景和小事件自然带出商品。\n"
        f"{time_rule}\n"
        f"产品:{name or '(见卖点)'}\n目标受众:{audience or '(自行判断)'}\n核心卖点:{selling_points or '(自行提炼)'}\n"
        f"投放市场/语言:{language};商品类目:{category}\n"
        f"只输出一个 JSON 数组(禁止任何解释、标题、markdown 代码块),含 {n} 个对象,每个对象字段固定为:\n"
        '{"title":"方案标题(简短有画面感)",'
        '"angle":"一句话创意方向",'
        '"model":"出镜模特设定(年龄/外貌/气质/穿着,贴合目标受众与投放市场;若无人出镜写 无模特)",'
        '"environment":"整体拍摄风格/地区氛围",'
        + shot_fields +
        "}\n"
        "动作要贴合 AI 视频能力边界:优先简单稳妥的生活化动作(走、坐、转身、微笑、整理、端起),"
        "弱化开盖/拆封/穿脱/倾倒等复杂物理变化,手与物体全程接触、符合重力。\n"
        f"硬性:{n} 个方案的【故事与场景】要拉开差距;双分镜两拍必须是不同地点/场景;全部字段用中文。"
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
        item = {
            "title": str(p.get("title") or p.get("标题") or "方案").strip()[:40],
            "angle": str(p.get("angle") or p.get("方向") or "").strip()[:100],
            "model": str(p.get("model") or p.get("模特") or "").strip()[:300],
            "environment": str(p.get("environment") or p.get("环境") or p.get("场景") or "").strip()[:300],
            "storyboard": str(p.get("storyboard") or p.get("分镜") or p.get("脚本") or "").strip()[:2000],
        }
        if two_shot:
            s1 = str(p.get("shot1") or p.get("分镜1") or p.get("分镜①") or "").strip()[:1500]
            s2 = str(p.get("shot2") or p.get("分镜2") or p.get("分镜②") or "").strip()[:1500]
            # 兜底:模型没拆两段 → 用合并 storyboard 兜底,保证双分镜两段都非空(时长达标、必出两段)
            s1 = s1 or item["storyboard"]
            s2 = s2 or item["storyboard"]
            item["shot1"], item["shot2"] = s1, s2
            # 场景母帧:优先用模型(产品驱动)产出的;模型漏给 → 退到中性通用场景兜底,保证两镜非空且不同
            # (per-shot 的前提),但不再写死具体故事(如 OOTD),避免给非服装商品套穿搭场景。
            from .video_templates import default_scenes
            d1, d2 = default_scenes(category)
            item["scene1"] = (str(p.get("scene1") or p.get("场景1") or "").strip()[:500] or d1)
            item["scene2"] = (str(p.get("scene2") or p.get("场景2") or "").strip()[:500] or d2)
            item["story"] = str(p.get("story") or p.get("故事") or p.get("故事线") or "").strip()[:200]
            if not item["storyboard"]:
                item["storyboard"] = f"【分镜①·0-5秒】{s1}\n\n【分镜②·5-15秒】{s2}"
        out.append(item)
    if not out:
        raise RuntimeError("方案解析为空")
    return out
