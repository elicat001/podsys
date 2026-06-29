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

    seconds=15 → **三分镜**:每个方案额外含 shot1/shot2/shot3 + scene1/2/3(动作链 per-shot 母帧)。"""
    two_shot = seconds == 15
    if two_shot:
        time_rule = (
            "本视频为【三分镜 · 共 15 秒】= 分镜①(0-5s)+ 分镜②(5-10s)+ 分镜③(10-15s)。\n"
            "⚠ 关键要求:这是【一个人经历一件事】,不是把『看手机→出门→走路』三个步骤依次摆拍(那叫流水账)。"
            "三拍共享【同一个目标 + 同一种情绪 + 同一个尚未完成的动作】,靠【因果】串起来(Story Beat,不是 Story Outline):\n"
            "  · 因果:因为发生了某事(如 收到消息),人物产生一个目标(去见人),于是开始一连串动作。\n"
            "  · 切点在【动作中途】:把一个连续动作切成 3 段——后一拍从前一拍【未完成动作的延续点】接着演,而不是从一个静止 pose 重新开始。\n"
            "    例:① 看手机、笑了一下、起身伸手去拿钥匙(动作没做完就切)→ ② 钥匙已在手、正推开门往外走(承接①)→ ③ 已走在街上、边走边回头(承接②)。\n"
            "商品是这条动作链里自然穿着/使用的道具;地点可递进(卧室→门口→街头),但【动作与情绪必须连贯不断】。\n"
            "围绕上面这件具体商品及卖点/受众原创,不要套用固定模板。"
        )
        # ⚠ 不再向模型要 storyboard 字段(预览由后端按固定格式从三拍合成,杜绝"有时带 0-x秒、有时一段话"的格式漂移);
        #   每个 shot 写成【一句连贯动作描述】、别加内部时间戳(外层分镜①/②/③已标时段)。
        shot_fields = (
            '"story":"一句话写清【动机+目标+情绪】(如 收到朋友消息很期待→赶着出门去见面),这是三拍共享的主线",'
            '"scene1":"分镜①母帧:故事【起因】发生的真实场景+人物状态(只描述场景+状态,别写运镜)",'
            '"shot1":"分镜①脚本(约5s):起因动作,写成【一句连贯动作描述】(别写 0-x 秒这类内部时间戳),'
            '且【结尾停在一个未完成的动作上】(如 笑着起身、手伸向钥匙),给②留接口",'
            '"scene2":"分镜②母帧:【承接①未完成动作的那一刻】的状态(如 钥匙已在手、门正被推开的瞬间),不是全新站姿",'
            '"shot2":"分镜②脚本(约5s):从①的延续点接着演(推门、迈步往外),情绪延续,一句连贯动作,结尾再留一个未完成动作给③",'
            '"scene3":"分镜③母帧:【承接②的那一刻】(如 刚迈出家门、一只脚已在街上),不是全新站姿",'
            '"shot3":"分镜③脚本(约5s):承接②继续(已走在街上、边走边自然张望),一句连贯动作,把情绪/目标收住"'
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
        "动作要贴合 AI 视频能力边界:优先简单稳妥的生活化动作(走、坐、转身、整理、端起、看手机),"
        "【表情要鲜活、去僵硬】人物有真实的情绪流动和细微眼神/表情变化(开心自然地笑、专注、放松随情境,有起伏),"
        "像活人不是定格照片;不是不能笑,要避免的是僵硬不变的假笑、呆滞死眼神、面瘫、对镜头从头营业到尾;"
        "弱化开盖/拆封/穿脱/倾倒等复杂物理变化,手与物体全程接触、符合重力。\n"
        "【任务驱动的人物行为(最重要)】:每个分镜是【记录一个真实生活片段】,不是「生成一个镜头展示商品」。"
        "先想清楚:人物此刻为什么在这里、正在做什么任务(收到消息后出门赴约 / 下班回家 / 周末逛街 / 早晨出门前 / 咖啡店歇脚…),"
        "再用具体的【任务动作】把它演出来(找钥匙、拿起包、推门、走向某处、端起杯子、看手机、整理一下就出门"
        "——任务动作比「拉衣角/摆 pose/甩头发」这类模特动作更真实)。商品是这件事里自然穿用的道具。"
        "别为展示商品而僵硬摆拍;口播/种草类可自然对镜头说话。镜头次之:手持随手拍即可,不堆运镜。\n"
        f"硬性:{n} 个方案的【故事与场景】要拉开差距;三分镜三拍是连续因果的动作链、且为递进的不同场景;全部字段用中文。"
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
            s3 = str(p.get("shot3") or p.get("分镜3") or p.get("分镜③") or "").strip()[:1500]
            # 兜底:模型漏拆 → 后段承接前段/storyboard,保证三拍都非空(时长达标、必出三段)
            s1 = s1 or item["storyboard"]
            s2 = s2 or s1
            s3 = s3 or s2
            item["shot1"], item["shot2"], item["shot3"] = s1, s2, s3
            # 场景母帧:优先用模型(产品驱动)产出的;模型漏给 → 退到中性【动作链】通用场景兜底,
            # 保证三镜非空且递进(per-shot 前提),不写死具体故事(如 OOTD),避免给非服装商品套穿搭场景。
            from .video_templates import default_scenes
            defs = default_scenes(category, 3)
            item["scene1"] = (str(p.get("scene1") or p.get("场景1") or "").strip()[:500] or defs[0])
            item["scene2"] = (str(p.get("scene2") or p.get("场景2") or "").strip()[:500] or defs[1])
            item["scene3"] = (str(p.get("scene3") or p.get("场景3") or "").strip()[:500] or defs[2])
            item["story"] = str(p.get("story") or p.get("故事") or p.get("故事线") or "").strip()[:200]
            # 预览 storyboard 一律由三拍按固定格式合成(不用模型自由发挥的)→ 杜绝格式漂移(0-x秒 vs 一段话),
            # 且预览忠实反映【实际喂给生成的 shot1/2/3】(三分镜真正按这三段并行出片)。
            item["storyboard"] = f"【分镜①·0-5s】{s1}\n\n【分镜②·5-10s】{s2}\n\n【分镜③·10-15s】{s3}"
        out.append(item)
    if not out:
        raise RuntimeError("方案解析为空")
    return out
