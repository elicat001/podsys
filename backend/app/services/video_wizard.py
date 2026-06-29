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


def _chat(messages: list, temperature: float | None = None) -> str:
    """调网关 chat.completions,返回 content 文本。无 key 抛错。
    temperature:留空=网关默认;方案生成传较高值(更发散、增创意/降同质化),简报识别用默认(求准)。"""
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key(智能向导需要作图的网关 key)")
    from openai import OpenAI  # 惰性

    from ..ai.openai_image import _API_GATE  # 复用全局网关并发信号量限流
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None,
                    timeout=settings.openai_timeout)
    kwargs: dict = {"model": settings.openai_text_model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature   # 网关不支持时会被忽略,无副作用
    with _API_GATE:
        resp = client.chat.completions.create(**kwargs)
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
            "    ⚠ 此例【仅示意「连续动作如何中途切开、后拍承接前拍」】,不是要你把方案都写成「出门」——动作链可发生在任何生活情境里。\n"
            "商品是这条动作链里自然穿着/使用的道具;地点可递进,但【动作与情绪必须连贯不断】。\n"
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
        time_rule = f"视频时长 {seconds} 秒(单镜头连续)。"
        # 与 15s 分镜【同逻辑】:15s 让模型写"约5s 的一拍连贯动作"(精简、结构化),不写完整时间轴;
        # 5/10s 也照此——把它当【一拍连贯动作】用一两句写清,靠"一两句·一镜到底"的正向框定自然精简,
        # 【不靠强行禁止时间轴】(负向反而易让模型乱)。详细时间轴留给「详细扩展」按需展开(与 15s 一致)。
        shot_fields = (
            f'"storyboard":"用【一两句】写清这条 {seconds} 秒真实生活小片段的一拍连贯动作'
            "(动机 → 动作 → 真实效果/情绪),像 15s 分镜里的一拍那样【精简、一镜到底、完整不缺内容】;"
            "人物为一个小目标/小情绪做这串连贯动作,商品是自然用到的道具(非平铺展示),可有小转折;紧扣核心卖点但别像硬广)\""
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
        "先想清楚:人物此刻为什么在这里、正在做什么任务,再用具体的【任务动作】把它演出来"
        "(任务动作比「拉衣角/摆 pose/甩头发」这类模特动作更真实)。商品是这件事里自然穿用的道具。"
        "别为展示商品而僵硬摆拍;口播/种草类可自然对镜头说话。镜头次之:手持随手拍即可,不堆运镜。\n"
        f"【创意与差异化(关键 · 治同质化)】这里的「不同」指【{n} 个方案彼此之间】要不同,【不是】把一条视频内部拆开——"
        "同一个方案(尤其 15s 三分镜)内部仍是【同一件事的连续动作链、关联性绝不能断】(三拍承接同一目标与情绪)。"
        f"在此前提下,{n} 个方案各自落在【明显不同的生活情境】,绝不能都是「出门/通勤带着商品」这一种。"
        "按这件商品的真实用法,从下面不同方向各挑一个(或自行想到更贴切的),让每个方案的【时间 + 地点 + 情绪 + 在做的事】都不一样:"
        "居家独处放松 / 和朋友或家人分享炫耀 / 工作或学习间隙 / 运动健身前后 / 下厨或吃东西 / 睡前或清晨的小仪式 / "
        "出门通勤或赴约 / 户外旅途野餐 / 收到礼物的惊喜 / 忙碌中被它治愈的一刻……(以上仅为方向、品类不限,别照搬词句)。"
        "在【贴合商品真实用法】的前提下,鼓励有想象力的小情节、小转折或反差(意外、对比、前后变化、与人互动、情绪起伏),"
        "别只是「平淡地用一下商品」;但别为创意牺牲真实感与 AI 视频可实现性。\n"
        f"硬性:{n} 个方案的【生活情境、地点、情绪、主要动作】都要明显不同(不要三个都是出门类);"
        "三分镜三拍是连续因果的动作链、且为递进的不同场景;全部字段用中文。"
    )
    # temperature 调高 → 方案更发散、增创意、降同质化(简报识别仍用默认温度求准)
    data = _loads_json(_chat([{"role": "user", "content": prompt}], temperature=0.9), expect_list=True)
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


def expand_proposal(*, seconds: int = 10, storyboard: str = "", shot1: str = "", shot2: str = "",
                    shot3: str = "", story: str = "", name: str = "", selling_points: str = "",
                    language: str = "葡萄牙语") -> dict:
    """把一个【精简版】方案脚本扩展成【详细时间轴脚本】(分秒级 beat + 镜头语言 + 表情情绪)。
    ⚠ 只把原脚本写得更细——【保持原故事/动作/情绪/场景与先后顺序不变,不换故事、不加新情节】。
    - 5/10s:扩展单条 storyboard → 返回 {"storyboard": 详细时间轴}。
    - 15s 三分镜:分别扩展 shot1/2/3(保持三拍承接、连续性不断)→ 返回 {shot1,shot2,shot3,storyboard(合成)}。
    用较低 temperature(忠实扩写,不发散)。无 key / 解析失败 → 抛异常(端点降级退点)。"""
    two_shot = seconds == 15
    common = (
        "你是 TikTok 带货短视频【分镜导演】。把下面这条【精简脚本】扩展成【更详细、可直接拍的时间轴脚本】:\n"
        "硬性:① 严格【保持原故事、动作、情绪、场景与先后顺序不变】,只写得更细,绝不换故事、不加新情节、不改商品用法;\n"
        "② 拆成分秒级 beat(如【0-2秒】【2-4秒】…),每个 beat 写清【镜头语言(景别/推拉摇移/手持)+ 人物动作 + 表情情绪】;\n"
        "③ 动作贴合 AI 视频能力边界(简单稳妥、手与物体接触、符合重力,弱化开盖/拆封/穿脱等复杂物理变化);"
        "商品图案/文字/颜色保持一致不被改样;\n"
        f"④ 投放市场/语言:{language};商品:{name or '(见脚本)'};卖点:{selling_points or '(见脚本)'};全部用中文。\n"
    )
    if two_shot:
        prompt = common + (
            "本视频为【三分镜 · 共15秒】= ①0-5s ②5-10s ③10-15s,三拍是【同一件事的连续动作链】,"
            "扩展后务必保持三拍承接、连续性不断(后拍从前拍延续点接演,不回到起始 pose)。\n"
            f"分镜①(原):{shot1}\n分镜②(原):{shot2}\n分镜③(原):{shot3}\n故事主线:{story}\n"
            '只输出一个 JSON 对象(禁止解释/标题/markdown):'
            '{"shot1":"分镜①详细脚本(约5s,内部分 beat)","shot2":"分镜②详细脚本(承接①)",'
            '"shot3":"分镜③详细脚本(承接②)"}'
        )
        data = _loads_json(_chat([{"role": "user", "content": prompt}], temperature=0.4))
        if not isinstance(data, dict):
            raise RuntimeError("详细扩展解析失败")
        s1 = str(data.get("shot1") or data.get("分镜1") or shot1).strip()[:1500]
        s2 = str(data.get("shot2") or data.get("分镜2") or shot2).strip()[:1500]
        s3 = str(data.get("shot3") or data.get("分镜3") or shot3).strip()[:1500]
        return {"shot1": s1, "shot2": s2, "shot3": s3,
                "storyboard": f"【分镜①·0-5s】{s1}\n\n【分镜②·5-10s】{s2}\n\n【分镜③·10-15s】{s3}"}
    prompt = common + (
        f"视频时长 {seconds} 秒(单镜头连续)。\n精简脚本(原):{storyboard}\n"
        f'只输出一个 JSON 对象(禁止解释/标题/markdown):'
        f'{{"storyboard":"详细时间轴脚本(严格按 {seconds} 秒分 beat,如【0-3秒】【3-7秒】…)"}}'
    )
    data = _loads_json(_chat([{"role": "user", "content": prompt}], temperature=0.4))
    if not isinstance(data, dict):
        raise RuntimeError("详细扩展解析失败")
    sb = str(data.get("storyboard") or data.get("分镜") or data.get("脚本") or storyboard).strip()[:2000]
    if not sb:
        raise RuntimeError("详细扩展返回为空")
    return {"storyboard": sb}
