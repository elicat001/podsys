"""Vidu 智能方案向导(两步)—— 对标 CogVideoX 的 video_wizard,但按 Vidu【单次调用·单镜连续】逻辑改造。

Step1「商品信息」:看图 → 结构化简报(产品名/受众/卖点)。
Step2「视频方案」:据简报 → N 个方向不同的方案。**每个方案 = 一个场景(给母帧)+ 一条连续动作链脚本**。

与 CogVideoX 的关键区别(不照搬):
- CogVideoX 是 3 段独立生成 + 拼接 → 方案带 shot1/2/3 + scene1/2/3,需要 Match Cut 桥接、多母帧换场景。
- Vidu q2-pro-fast 是【单次调用、一个连续镜头、≤10s】→ 方案只给【一个场景 + 一条一气呵成的连续动作链】,
  不拆分镜、不做 Match Cut、不做多场景拼接(单镜本就连续)。动作链写成"一个连续镜头内的因果动作序列"。

复用作图网关视觉/文本模型(惰性 import openai、_API_GATE 限流);无 key / 解析失败 → 抛异常,端点降级退点。
"""
from __future__ import annotations

import base64
import io
import json
import re

from PIL import Image

from ..config import settings
from .video_continuity import SCENE_INIT_GUIDE


def _data_url(image: Image.Image) -> str:
    im = image.convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _loads_json(content: str, *, expect_list: bool = False):
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


def _chat(messages: list, temperature: float | None = None) -> str:
    """temperature 留空=网关默认;方案生成传较高值(更发散、增创意/降同质化),简报识别用默认(求准)。"""
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key(智能向导需要作图的网关 key)")
    from openai import OpenAI  # 惰性

    from ..ai.openai_image import _API_GATE
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None,
                    timeout=settings.openai_timeout)
    kwargs: dict = {"model": settings.openai_text_model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature   # 网关不支持时会被忽略,无副作用
    with _API_GATE:
        resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def describe_product(image: Image.Image, selling_points: str = "", language: str = "葡萄牙语") -> dict:
    """Step1:看商品图 → 结构化简报 {name, audience, selling_points}。"""
    sp = (selling_points or "").strip()[:500]
    hint = f"\n卖家补充的卖点(请融合,但以图为准):{sp}" if sp else ""
    prompt = (
        "你是跨境电商选品与文案专家。仔细观察这张商品图,提炼出用于拍带货视频的【商品简报】。\n"
        "只输出一个 JSON 对象(禁止任何解释、标题、markdown 代码块),字段固定为:\n"
        '{"name":"产品名称(简洁,含关键品类与风格)",'
        '"audience":"目标受众(一句话:谁会买、用在什么场景)",'
        '"selling_points":"核心卖点(3~5 条,用、或换行分隔,覆盖材质/卖点/玩法/使用等图中可见或可合理推断的点)"}\n'
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
                       language: str = "葡萄牙语", n: int = 3, profile: dict | None = None) -> list[dict]:
    """Step2:据简报 → n 个方向不同的方案。每个 {title, angle, model, environment, scene, storyboard}。
    Vidu 单镜:scene=母帧场景(一个),storyboard=一条连续动作链脚本(不拆分镜、不 Match Cut)。
    profile(Scene Profile · N3):按风险动态启用连续性能力;Vidu 恒单镜(multi_shot=False);无 profile → 历史行为。"""
    from .video_continuity import build_continuity_guide, profile_to_capabilities
    _enabled = (profile_to_capabilities((profile or {}).get("interaction_risks"), multi_shot=False)
                if profile else None)
    continuity_guide = build_continuity_guide("vidu", enabled=_enabled)
    prompt = (
        f"你是 TikTok 跨境电商短视频【内容导演】。根据下面的商品简报,设计 {n} 个【方向明显不同】的带货短视频方案。\n"
        f"⚠ 这是用 Vidu 模型生成的【单镜头、约 {seconds} 秒、一气呵成的连续片段】,不是多段拼接 → "
        "每个方案只设计【一个场景 + 一条连续动作链】,不要分镜、不要场景切换、不要镜头跳切。\n"
        f"产品:{name or '(见卖点)'}\n目标受众:{audience or '(自行判断)'}\n核心卖点:{selling_points or '(自行提炼)'}\n"
        f"投放市场/语言:{language}\n"
        f"只输出一个 JSON 数组(禁止任何解释、标题、markdown 代码块),含 {n} 个对象,每个对象字段固定为:\n"
        '{"title":"方案标题(简短有画面感)",'
        '"angle":"一句话创意方向",'
        '"model":"出镜真人设定(年龄/外貌/气质/穿着,贴合受众与市场;若无人出镜写 无模特)",'
        '"environment":"拍摄场景/地区氛围(一个连续场景)",'
        '"scene":"母帧场景:一句话描述【真人正要上手使用/把玩这个商品的那一刻】的画面(场景+人物状态,别写运镜),给视频首帧合成用",'
        '"storyboard":"一条【连续动作链】脚本:这个人为一个目标、带一种情绪,做一连串连贯因果的动作,一气呵成在一个镜头里完成'
        '(例:拿起商品→上手玩它的核心玩法→产生真实物理效果→自然的情绪反应);别分时间轴步骤、别切场景"}\n'
        "硬性要求:\n"
        "① 【看图自适应,绝不套模板】动作必须贴合这件具体商品最自然的真实玩法/用法(解压玩具→按压旋转把玩、捏压回弹;"
        "杯子→端起喝;服饰→穿在身上自然动);不同方案动作/角度要拉开差距;\n"
        f"② 【创意与差异化 · 治同质化】{n} 个方案要落在【明显不同的生活情境】,别都是同一个桌面/同一种把玩:"
        "可覆盖 居家独处 / 和朋友家人分享炫耀 / 工作学习间隙 / 户外或旅途 / 睡前或清晨 / 被它治愈的一刻 等不同场景与情绪"
        "(按商品真实用法挑、品类不限,别照搬词句);鼓励有想象力的小情节、小转折或反差,但别牺牲真实感与可实现性;\n"
        "③ 【任务驱动 + 去僵硬】把它当【记录真实生活片段】,人物专注做手上的事、神态鲜活有情绪流动和细微表情变化,"
        "不是对镜头僵硬假笑/呆滞摆拍;\n"
        "④ 按商品真实玩法大胆设计【有真实运动幅度】的自然动作,手与物体接触、符合重力,绝不出现部件自行开合或物体凭空出现/消失;\n"
        + SCENE_INIT_GUIDE + "\n"
        + continuity_guide + "\n"   # N3:按 Scene Profile 风险动态选连续性能力(无 profile → 全部=历史行为)
        "⑤ 商品图案/文字/颜色保持一致不被改样;全部字段用中文。"
    )
    # temperature 调高 → 方案更发散、增创意、降同质化(简报识别仍用默认温度求准)
    data = _loads_json(_chat([{"role": "user", "content": prompt}], temperature=0.9), expect_list=True)
    if isinstance(data, dict):
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
            "scene": str(p.get("scene") or p.get("母帧") or p.get("母帧场景") or "").strip()[:500],
            "storyboard": str(p.get("storyboard") or p.get("分镜") or p.get("脚本") or p.get("动作链") or "").strip()[:2000],
        })
    if not out:
        raise RuntimeError("方案解析为空")
    return out
