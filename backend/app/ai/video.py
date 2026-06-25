"""图生视频 Provider(可插拔,对齐 matting/upscale 范式)+ 提示词工程 + 防拉伸。

- LocalGifProvider:不调 AI,用现有 Ken-Burns/轮播出 GIF —— 离线兜底,无 key 也能出东西(降级)。
- ZhipuCogVideoProvider:调智谱 CogVideoX-3(建任务→轮询→下载 mp4)。
  1 图=首帧;[首, 尾] 数组=首尾帧(对应前端 1~2 张图)。
换厂商 = 新增一个 Provider 类 + 改 `POD_VIDEO_PROVIDER`,业务/前端不动。
重依赖(httpx)在方法内惰性 import,保持离线启动轻量。

尺寸:画幅(比例)× 分辨率(短边像素)→ size。CogVideoX-3 支持多分辨率、最高 4K。
防拉伸:按所选画幅把上传图等比 contain 到目标尺寸(模糊背景填充),模型就不会按 size 生硬拉伸商品。
"""
from __future__ import annotations

import base64
import io
import time
from typing import Protocol, runtime_checkable

from PIL import Image

from ..config import settings

# ── 画幅(宽:高 比例)。key 与前端画幅按钮一一对应;顺序=竖→方→横 ──────────
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "portrait":    (9, 16),   # 9:16 竖屏(TikTok/带货短视频首选)
    "portrait34":  (3, 4),    # 3:4  竖屏商品
    "square":      (1, 1),    # 1:1  方形(信息流)
    "landscape43": (4, 3),    # 4:3  横屏经典
    "landscape":   (16, 9),   # 16:9 横屏宽屏
}
# 分辨率档 → 短边像素(长边按比例算)。flat price,4K 也不额外收费,只是更慢、文件更大。
RESOLUTION_SHORT: dict[str, int] = {"720p": 720, "1080p": 1080, "4k": 2160}

# 视频时长(秒)。前端先选时长再选视频类型(脚本随时长变)。
#   5 / 10 = CogVideoX-3 单段直接支持;15 = 「双分镜」复合时长(单段最长 10s,故拆 5s+10s 两段并行生成再拼接)。
DURATIONS: list[int] = [5, 10, 15]          # 用户可选时长(15=双分镜复合时长)
SEGMENT_DURATIONS: list[int] = [5, 10]      # 模型单段真正支持的时长(provider 只会收到这两个,绝不会收到 15)
MULTI_SHOT_DURATION: int = 15               # 选它 → 触发多分镜(two_shot 标志位 = 15s)
# 15s 拆 3 段 ×5s:给【动作链】3 个真实节拍(如 在家收到消息→门口拿钥匙推门→街上走),
# 镜头多+短、动作连续因果,比 5+10 两段更像 TikTok(GPT 判定)。计费 = video×len(plan)=×3。
# 改这里的段数 → 计费 n、前端分镜数需同步(n=len(MULTI_SHOT_PLAN),贯穿全链路,不写死 2/3)。
MULTI_SHOT_PLAN: list[int] = [5, 5, 5]

# 视频配音/对白语言(主打巴西=葡萄牙语)。「无对白」=不加语言指令。
LANGUAGES: list[str] = ["葡萄牙语", "英语", "西班牙语", "中文", "无对白"]

# 视频描述(镜头脚本)由前端「视频类型」按钮填入、可自定义编辑(开箱/达人/场景/广告大片/互动/自定义)。
# ── 专业化提示词架构(参考实操经验)──────────────────────────────────────────
# 视频模型最吃「镜头脚本(动作序列/时间轴)+ 类目专属动作 + 地区风格 + 负向词」,而非堆形容词。
# 后端把这些层统一拼到用户的镜头脚本后面:类目动作 + 地区 UGC 风格(按语言)+ 一致性 + 负向。

# 商品类目 → 专属动作序列(POD 常见品类;模型有具体动作可演,远胜通用开箱)。
# ⚠ 仅 L2「品类模板」用它选使用场景母帧;L1 默认不依赖类目(任意 SKU 通用)。未列入的类目自动走「通用」兜底。
CATEGORIES: list[str] = ["通用", "T恤", "卫衣", "马克杯", "水杯", "手机壳", "帆布袋", "海报", "抱枕", "毛毯"]

# ── 三层出片体系(L5 商业系统视角:SKU 覆盖率 / 成功率 / 成本 > 单条创意上限)──────────
# 这是「POD 自动生产几万个 SKU 卖货视频」的产品骨架,不是「一条视频怎么爆」。判断标准是工程指标:
# 成功率、稳定性、SKU 泛化、翻车率、成本——而非真人感/创意。前端据此渲染「出片模式」,默认 L1。
#   L1 通用产品片(默认主力)= 产品前置、近乎无人、**无 gpt-image 母帧**、单镜
#       → 翻车面最小(绕开 #1 翻车源「母帧链」)、任意 SKU 通用、成本最低(3 点)、覆盖 ~80% SKU。
#   L2 品类模板 = 单镜 + 品类使用场景母帧(gpt-image,失败自动降级回原图首帧)
#       → 头部品类(服装上身 / 杯子晨用 / 海报上墙)更贴合,引入一次母帧依赖、仍稳定。
#   L3 Hero·真人 = 人物行为(compose_prompt)+ 可三分镜(15s 动作链)+ 智能向导
#       → 表现力上限最高,但变量爆炸(模特/身材/脸/穿搭/房间/光线)、成功率最低,只给爆款单品。
# 三层同时也是 A/B 投放底座(变体 A/B/C):哪个转化更高需真实数据裁决,不靠拍脑袋。
TIERS: list[dict] = [
    {"id": 1, "name": "通用产品片", "tag": "默认·最稳",
     "desc": "上传即出片,商品为主角、第一帧就读懂在卖什么;任意商品通用、成功率最高,适合批量"},
    {"id": 2, "name": "种草结果片", "tag": "卖『拥有后的样子』",
     "desc": "把商品放进让人想拥有/想模仿的好看结果里(好看穿搭 / 惬意氛围 / 理想使用),商品是其中清晰的主角"},
    {"id": 3, "name": "Hero·真人种草", "tag": "上限高·爆款用",
     "desc": "真人出镜 / OOTD / 口播 + 智能向导,表现力最强但变量多、成功率最低,建议只给爆款单品"},
]

# 地区 TikTok UGC 风格:按所选语言智能匹配本地风格(别写死巴西!选什么语言出什么地区的人/氛围),
# 避免「葡语视频里却是国内主播」这种违和。「无对白」=不指定地区风格(中性)。
REGION_STYLE: dict[str, str] = {
    "葡萄牙语": ("整体为巴西 TikTok UGC 风格:年轻的巴西本地用户,居家随性的真实环境,温暖的自然阳光,"
                "热情、有活力、表情丰富的肢体语言,真实社媒随手拍质感,而非商业摄影棚摆拍。"),
    "英语": ("整体为欧美 TikTok UGC 风格:年轻的欧美本地用户,日常居家或街头环境,自然光照,"
            "自信轻松、自然亲切的表达,真实社媒随手拍质感,而非商业摄影棚摆拍。"),
    "西班牙语": ("整体为拉美/西语区 TikTok UGC 风格:年轻的西语区本地用户,热情活力的生活化环境,温暖明亮的光线,"
                "表情丰富、有感染力的肢体语言,真实社媒随手拍质感,而非商业摄影棚摆拍。"),
    "中文": ("整体为中国抖音/TikTok UGC 风格:年轻的本地用户,生活化的居家或街头环境,自然光照,"
            "真实自然的表达,真实社媒随手拍质感,而非商业摄影棚摆拍。"),
}

# 导演层(正向为主)—— 核心 prompt 原则:给模型【目标状态】,而不是堆"不要A不要B"。
# 教训:几轮"加负向修问题"后负向占 80% → 模型变保守(安全/稳定/正确但无聊)。正向导演指令才能拉出 TikTok 感。
# 三层:身份(记录真实生活)+ 任务(人物在做的事,【任务动作 > 模特动作】)+ 镜头(手持随手拍)。通用,不写死。
_DIRECTION_BLOCK = (
    "【导演定位】你在记录一段真实生活里的一个片段,不是拍商品广告、也不是摆拍展示商品。"
    "画面中的人此刻正在做一件具体的、有理由发生的事(例如:收到消息后准备出门赴约、下班回家、周末逛街、早晨出门前、咖啡店歇脚)。"
    "优先表现:真实的生活状态、自然的【任务动作】(找钥匙、拿起包、推门、走向某处、端起杯子、看手机、整理一下就出门)、"
    "当下的情绪和与环境的互动;人物专注在自己做的事上、神态自然放松。"
    "【动作要连成一条因果链(Story Beat,不是步骤罗列)】人物为一个目标、带一种情绪做一连串动作(如 看手机→起身→拿钥匙→推门),"
    "让人觉得「他正在经历一件事」。若本镜的首帧/脚本显示动作【正进行到一半】(承接上一镜的延续状态),"
    "就从那一刻【接着演下去】、保持同一情绪与目标,绝不回到一个静止起始 pose 重新开始;镜头之间共享同一个尚未完成的动作。"
    "只避开高风险物理变化(开盖/拆封/穿脱/倾倒),低风险的连续生活动作要大胆做、有进展。"
    "商品作为这件事里自然穿着/使用的道具出现、清晰可见即可,不必刻意举到镜头前。"
    "镜头像真人手持手机随手拍这个瞬间:有自然手持感,可推近/拉远/跟拍/自拍角度,记录而非摆拍。"
)

# 画面底线(简短、正向措辞)—— 只守住【真实踩过的模型失败】(印花被改、像砖块、跳切),不堆负向喧宾夺主。
_GUARD_BLOCK = (
    "画面要求:商品的图案、文字、颜色与设计细节始终保持一致、不被改样或拉伸扭曲;"
    "材质物理真实——布料柔软垂坠不僵硬如纸板、蓬松物受压会回弹、硬物坚固、液体随容器晃荡;"
    "动作遵循重力与接触、连贯不跳帧;开场与场景过渡自然连续。"
)

# ── 通用电影化层:补齐官方提示词公式里偏弱的【景别角度 + 光影 + 氛围】(镜头语言/主体/主体运动已在别处)──
# ⚠【绝不按品类写死】:这两块对任意 SKU(衣/杯/壳/海报/毯/任何 POD 品)同样适用——避免"只优化了一种商品、
#    其它品类问题依旧"。需要品类差异的只有 L2 的「场景」(由 _SCENE_BY_CAT / 母帧承担),镜头/景别/光影/氛围保持通用。
# 产品路径(L1/L2):干净专业的电商产品短片质感(允许商业感,L1=产品大片,与 L3 的反影棚 UGC 取向不同)。
_CINEMA_PRODUCT = (
    "【镜头与光影(通用)】运镜克制专业:以轻缓的推近、平移或小幅环绕呈现商品(不旋转翻转商品本体);"
    "景别从中景自然过渡到近景特写,突出商品的图案、文字与材质细节;"
    "干净柔和的自然光或柔光散射,均匀照亮商品、真实还原颜色与质感,避免死黑、过曝或杂乱反光;"
    "整体氛围清爽、真实、有质感,像一条高质量的电商产品短片。"
)
# 人物路径(L3):补 compose_prompt 偏弱的【景别 + 光影】,但保持真实 UGC(不要影棚塑料感);通用、不按场景写死。
_CINEMA_HUMAN = (
    "【景别与光影(通用)】镜头在中景与近景之间自然切换:既交代人物与环境、也给到商品的细节特写;"
    "用真实自然的光线(室内窗光 / 室外日光),明暗过渡柔和、贴近真实生活,不要影棚布光的塑料感。"
)
# 结果/种草路径(L2):介于 L1 干净商业感与 L3 原生 UGC 之间——好看、有 vibe、像高质量真人种草,而非硬广。通用。
_CINEMA_RESULT = (
    "【景别与光影(通用)】中景与近景之间自然切换:既给到让人想拥有的整体结果、也带出商品细节;"
    "真实自然的光线与有质感的氛围(温暖、有 vibe、像高质量的真人种草内容),不要硬广摆拍的塑料感。"
)

# 「场景首帧」两步生成:每类目对应的首帧场景(gpt-image 把商品放进该场景做视频第一帧 → 缓解硬切)。
_SCENE_BY_CAT: dict[str, str] = {
    "通用": "这件商品被真实使用的生活化场景",
    "T恤": "模特穿着这件 T 恤的真实街拍/居家场景",
    "卫衣": "模特穿着这件卫衣的真实街拍/居家场景",
    "马克杯": "马克杯摆在桌面、旁边有人准备拿起使用的居家场景",
    "水杯": "水杯摆在桌面或随身携带、旁边有人准备拿起饮用的生活场景",
    "手机壳": "手机装着这个壳被人拿在手里使用的生活场景",
    "帆布袋": "有人挎着这个帆布袋外出的日常生活场景",
    "海报": "这张海报贴在/挂在房间墙上的居家场景",
    "抱枕": "这个抱枕放在沙发上的温馨客厅场景",
    "毛毯": "这条毛毯铺在沙发或床上、有人依偎使用的温馨居家场景",
}

# 厂商官方文档(选型/排错时看)。换厂商照 ZhipuCogVideoProvider 再写一个即可。
_VENDOR_DOCS = {"cogvideox": "https://docs.bigmodel.cn/cn/guide/models/video-generation/cogvideox-3"}


def _r8(n: float) -> int:
    """取最接近的 8 的倍数(多数视频模型对 8/16 倍数友好)。"""
    return max(16, int(round(n / 8)) * 8)


def aspect_size(aspect: str = "portrait", resolution: str = "1080p") -> str:
    """画幅 + 分辨率 → "WxH"。短边=分辨率档像素,长边按比例算。"""
    w, h = ASPECT_RATIOS.get(aspect, ASPECT_RATIOS["portrait"])
    short = RESOLUTION_SHORT.get(resolution, 1080)
    if w <= h:                       # 竖/方:宽是短边
        ww, hh = short, short * h / w
    else:                            # 横:高是短边
        hh, ww = short, short * w / h
    return f"{_r8(ww)}x{_r8(hh)}"


def compose_prompt(motion: str = "", language: str = "葡萄牙语") -> str:
    """专业化拼装:镜头脚本(用户填/改=任务/故事层)+ 地区 UGC 风格(按语言)+ 语言
    + 导演层(正向:身份/任务/镜头)+ 简短画面底线 → 最终 prompt。
    正向导演为主、负向只留必要底线(治"负向太多 → 模型保守无聊");动作交给脚本,不按类目写死。"""
    parts: list[str] = []
    motion = (motion or "").strip()
    if motion:
        parts.append(motion)
    region = REGION_STYLE.get(language)   # 按语言智能匹配地区风格(无对白/未知 → 不加)
    if region:
        parts.append(region)
    if language and language != "无对白":
        parts.append(f"视频中的人物对白与配音使用{language}。")
    parts.append(_DIRECTION_BLOCK)     # 导演层(正向为主):身份=记录真实生活 + 任务动作 + 手持镜头(=镜头语言+主体运动)
    parts.append(_CINEMA_HUMAN)        # 补官方公式偏弱的【景别角度 + 光影】(通用 UGC,不按场景写死)
    parts.append(_GUARD_BLOCK)         # 画面底线(简短):印花一致 + 材质物理 + 连贯
    return " ".join(parts)


def compose_product_prompt(motion: str = "", language: str = "葡萄牙语") -> str:
    """Universal Template(L1/L2 用):**产品为主角、近乎无人**的展示提示词(高良品率、任意 SKU 通用)。
    与 compose_prompt(人物行为为主、变量多、易翻车)相反——这里要:商品居中清晰、**第一帧就读懂在卖什么**、
    近乎无人(最多一只手)、零剧情、运镜克制(只推近/平移,**不旋转翻转** → 平面商品图也不会被扭成砖块)。
    工业化默认主力:翻车面最小(无人体物理 / 无多母帧链)。【信息密度优先于艺术感】——
    用户能多快读懂在卖什么,是带货视频的真差距,不是占屏面积或真人感。"""
    parts: list[str] = []
    motion = (motion or "").strip()
    # 主体运动(默认):商品为主、运动克制,适配 6s 内可展现(官方:主体运动不宜过于复杂)。
    parts.append(motion or "镜头轻缓地推近并平移展示这件商品(Ken-Burns 式),"
                           "商品保持自身姿态、不旋转不翻转(尤其平面图,避免被扭曲成砖块/纸板),"
                           "最后自然落到它被真实使用的状态。")
    # 主体 + 主体描述(信息密度优先):第一帧就读懂在卖什么。
    parts.append(
        "【主体】商品自始至终是画面绝对主体、居中、清晰可辨、占据画面主要面积;"
        "**第一帧(前 0.5 秒)就要让人一眼看懂这是什么商品、卖点是什么**——图案/设计/材质/文字清楚呈现、不被遮挡;"
        "近乎无人(最多一只手自然拿取或摆放),不要人物剧情、不要模特走位换姿势。"
    )
    parts.append(_CINEMA_PRODUCT)   # 镜头语言 + 景别角度 + 光影 + 氛围(通用,不按品类写死)
    parts.append(_GUARD_BLOCK)      # 印花一致 + 材质物理 + 连贯(复用同一底线)
    if language and language != "无对白":
        parts.append(f"如有文字/旁白,使用{language}。")
    return " ".join(parts)


def compose_result_prompt(motion: str = "", language: str = "葡萄牙语") -> str:
    """变体 B「结果前置 / 种草」用(L2):首帧/全片呈现一个【让人想拥有、想成为的真实生活结果】——
    好看的穿搭、惬意的氛围、理想的使用状态——**商品是这个结果里清晰、突出的视觉主角**(自然穿用,
    不是孤立特写、也不是可有可无的背景)。卖的不是孤立的图案,而是"拥有它之后的样子"
    (例:不是放大土星印花,而是'穿上这件 T 的那个有 vibe 的人')。

    取舍依据(团队对竞品的复盘):首帧怼印花易被读成广告→划走;真正留得住人的是"想模仿的结果/vibe"。
    但 POD 非协商铁律仍在——印花/图案像素级保真(产品本体不可改),只是让它【自然穿用在好看的结果里】。
    **通用、不按品类写死**:衣=好看穿搭 / 杯=惬意晨间 / 毯=温馨依偎 / 海报=空间氛围升级,对任意 SKU 都成立。"""
    parts: list[str] = []
    motion = (motion or "").strip()
    parts.append(motion or "镜头自然呈现一个真实、好看、让人想拥有的生活片段,"
                           "商品被自然地穿着或使用在其中,运镜轻缓克制,真实生活质感而非硬广摆拍。")
    parts.append(
        "【主角与钩子】把这一幕拍成一个让人想模仿、想拥有的结果(好看的穿搭 / 惬意的氛围 / 理想的使用状态),"
        "商品是这个结果里**清晰、突出的视觉主角**——自然穿着或使用、不被遮挡,"
        "让人很快(约 1 秒内)看出在卖什么,但通过'好看的结果'来呈现,而不是孤立的产品特写或怼脸印花;"
        "可以有人物或使用场景,重点是'拥有它之后的样子'好看到让人想要。"
    )
    parts.append(_CINEMA_RESULT)   # 景别 + 光影 + 氛围(种草 vibe,通用)
    parts.append(_GUARD_BLOCK)     # 印花一致 + 材质物理 + 连贯(POD 非协商:图案不可改)
    if language and language != "无对白":
        parts.append(f"如有文字/旁白,使用{language}。")
    return " ".join(parts)


def gptimage_size(aspect: str = "portrait") -> str:
    """画幅 → gpt-image 支持的最接近尺寸(只有 1024x1024 / 1024x1536 / 1536x1024 / auto)。"""
    w, h = ASPECT_RATIOS.get(aspect, ASPECT_RATIOS["portrait"])
    if w < h:
        return "1024x1536"   # 竖
    if w > h:
        return "1536x1024"   # 横
    return "1024x1024"       # 方


# 语言 → 地区(场景首帧的本地风格也跟着语言变,别写死巴西)
_REGION_HINT: dict[str, str] = {"葡萄牙语": "巴西", "英语": "欧美", "西班牙语": "拉美/西语区", "中文": "中国"}


def scene_frame_prompt(category: str = "通用", language: str = "葡萄牙语", scene: str = "") -> str:
    """「场景首帧」用的 gpt-image 编辑指令:把商品以【真实立体使用形态】放进场景做视频第一帧。地区风格随语言变。

    ⚠ 治"平面像砖块被提起来":图生视频会"跟着首帧走"——平铺的衣服会被当成刚性平面来动,像砖块/纸板。
    所以首帧必须呈现真实三维形态:衣物【穿在真人模特身上】有自然版型/褶皱/垂坠;物品被自然拿握/摆放。
    印花/图案/文字/颜色完整保留(产品本体不能改),但【形状要立体真实、不能锁成平面】(关键区别于旧版)。
    scene 给了就用它(per-shot 母帧场景);留空回退类目默认场景。"""
    scene = (scene or "").strip() or _SCENE_BY_CAT.get(category, _SCENE_BY_CAT["通用"])
    region = _REGION_HINT.get(language, "")
    region_txt = f"{region}本地" if region else "本地"
    return (
        f"把图中的商品自然地放进「{scene}」中,作为一段 TikTok 短视频的第一帧画面。"
        "【首帧要卖『拥有后的样子』】让这一帧成为一个让人想拥有、想模仿的好看结果或氛围"
        "(好看的穿搭 / 惬意的生活感 / 理想的使用状态),商品在其中是清晰、突出的视觉主角——"
        "不是孤立的产品特写、也不是可有可无的背景;让人很快看出在卖什么,但卖的是'拥有它之后的样子'。"
        "【关键】让商品以真实使用时的立体形态出现,绝不能平铺、悬空或像纸板/砖块一样的平面:"
        "若是衣物(T恤/卫衣/裙装等),要真实地穿在一个真人模特身上,有自然的版型、布料褶皱、垂坠和身体支撑;"
        "若是杯子/手机壳/帆布袋/抱枕等物品,让它被自然地拿在手里或摆在该在的位置,有真实的体积与受光。"
        "必须完整保留商品上的图案、文字、印花、颜色与设计细节(这是产品本身,不可改动、丢失或扭曲变形),"
        "但要让商品呈现真实的三维形态与材质质感(布料柔软、有垂坠与褶皱)。"
        f"画面要像{region_txt}年轻人用手机随手拍的真实生活照/TikTok:自然光、略带生活气、真实抓拍质感,"
        "绝不要广告摄影棚、精修大片或 CG 感。"
    )


def fit_to_aspect(im: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """把图片放进 target_w×target_h:等比 contain(不拉伸不变形),其余用同图放大+模糊做背景填充
    (自然、不黑边)。这样首帧就已经是目标画幅 → 模型不会按 size 生硬拉伸商品。"""
    from PIL import ImageFilter, ImageOps
    im = im.convert("RGB")
    if abs(im.width / im.height - target_w / target_h) < 0.02:   # 已接近目标比例 → 只等比缩放
        return im.resize((target_w, target_h), Image.LANCZOS)
    bg = ImageOps.fit(im, (target_w, target_h), method=Image.LANCZOS).filter(ImageFilter.GaussianBlur(36))
    fg = im.copy()
    fg.thumbnail((target_w, target_h), Image.LANCZOS)
    bg.paste(fg, ((target_w - fg.width) // 2, (target_h - fg.height) // 2))
    return bg


@runtime_checkable
class VideoProvider(Protocol):
    name: str

    def image_to_video(self, images: list[Image.Image], prompt: str, *, size: str = "1080x1920",
                       seconds: int | None = None, with_audio: bool | None = None) -> dict:
        """images: 1~2 张(2 张=首尾帧),已按 size 画幅处理好。返回 {bytes, url, ext('mp4'|'gif'), meta}。"""
        ...


def _parse_size(s: str) -> tuple[int, int]:
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 1080, 1920


def _encode_data_uri(im: Image.Image) -> str:
    # 用 JPEG(而非 PNG)编码:体积小 5~10×,上传快得多 → 大幅降低发图时的网络写超时(WriteTimeout)。
    # 商品图首帧 q90 质量足够;已 convert RGB(去 alpha,JPEG 不支持透明)。
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class LocalGifProvider:
    """离线兜底:不调 AI,用现有运镜/轮播出 GIF(降级,非真 AI 视频)。无 key/未配置时用它。"""
    name = "local"

    def image_to_video(self, images: list[Image.Image], prompt: str, *, size: str = "1080x1920",
                       seconds: int | None = None, with_audio: bool | None = None) -> dict:
        from ..services.video import make_showcase
        w, h = _parse_size(size)
        aspect = "portrait" if w < h else ("landscape" if w > h else "square")
        style = "slideshow" if len(images) > 1 else "kenburns"
        out = make_showcase(images[:2], style=style, aspect=aspect, fps=12,
                            seconds=int(seconds or settings.video_seconds))
        return {
            "bytes": out["bytes"], "url": "", "ext": "gif",
            "meta": {"engine": "local-gif", "degraded": True,
                     **{k: out[k] for k in ("frames", "width", "height", "duration_ms")}},
        }


class _TaskFailed(Exception):
    """智谱把任务判 FAIL(应用层失败,常见『网络错误,请稍后重试』)→ 可重新建任务重试,区别于网络层异常。"""


class ZhipuCogVideoProvider:
    """智谱 CogVideoX-3 图生视频。建任务→轮询→下载 mp4。1图=首帧,2图=[首,尾]首尾帧。"""
    name = "cogvideox"

    def __init__(self) -> None:
        if not settings.video_api_key:
            raise RuntimeError("POD_VIDEO_API_KEY 未配置(智谱开放平台 key)")
        self.base = (settings.video_base_url or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        self.model = settings.video_model or "cogvideox-3"

    def _build_task(self, c, body: dict, headers: dict) -> str:
        """提交任务 → task_id。网络抖动重试 3 次;4xx(鉴权/参数)不重试。"""
        import httpx
        for attempt in range(3):
            try:
                r = c.post(self.base + "/videos/generations", headers=headers, json=body)
                r.raise_for_status()
                tid = (r.json() or {}).get("id") or ""
                if not tid:
                    raise RuntimeError(f"智谱未返回任务 id: {r.text[:200]}")
                return tid
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code < 500 or attempt == 2:
                    # 4xx(参数/鉴权/内容审核/余额)或重试用尽:带上智谱的响应体,否则只剩"400 Bad Request"无从定位
                    try:
                        detail = exc.response.text[:300]
                    except Exception:  # noqa: BLE001
                        detail = ""
                    raise RuntimeError(f"智谱建任务失败 HTTP {code}: {detail}") from exc
                time.sleep(2 * (attempt + 1))
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("建任务失败")  # 不会到这(循环里要么 return 要么 raise),兜底

    def _await_result(self, c, task_id: str, headers: dict, size: str) -> dict:
        """轮询直到 SUCCESS(下载并返回 result)。任务 FAIL → 抛 _TaskFailed(可重建)。
        轮询偶发网络抖动容忍(连续超阈值才放弃);超时抛 TimeoutError(不重建,已等满)。"""
        import httpx
        deadline = time.monotonic() + float(settings.video_timeout)
        poll_fails = 0
        while time.monotonic() < deadline:
            time.sleep(float(settings.video_poll_interval))
            try:
                rr = c.get(self.base + "/async-result/" + task_id, headers=headers)
                rr.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError):
                poll_fails += 1
                if poll_fails > 20:
                    raise
                continue
            poll_fails = 0
            d = rr.json() or {}
            st = str(d.get("task_status", "")).upper()
            if st == "SUCCESS":
                vids = d.get("video_result") or []
                url = (vids[0].get("url") if vids else "") or ""
                if not url:
                    raise _TaskFailed("任务成功但无视频 URL")
                for attempt in range(3):       # 下载成片重试
                    try:
                        data = c.get(url, timeout=httpx.Timeout(180.0)).content
                        break
                    except httpx.TransportError:
                        if attempt == 2:
                            raise
                        time.sleep(3 * (attempt + 1))
                return {"bytes": data, "url": url, "ext": "mp4",
                        "meta": {"engine": "cogvideox-3", "task_id": task_id,
                                 "cover": (vids[0].get("cover_image_url") or ""), "size": size}}
            if st in ("FAIL", "FAILED", "ERROR"):
                err = (d.get("error") or {})
                raise _TaskFailed(str(err.get("message") or err or str(d))[:160])
        raise TimeoutError("视频生成超时(可调 POD_VIDEO_TIMEOUT)")

    def image_to_video(self, images: list[Image.Image], prompt: str, *, size: str = "1080x1920",
                       seconds: int | None = None, with_audio: bool | None = None) -> dict:
        import httpx  # 惰性
        if not images:
            raise RuntimeError("图生视频至少需要 1 张图")
        encoded = [_encode_data_uri(im) for im in images[:2]]
        image_url = encoded if len(encoded) > 1 else encoded[0]   # 数组=首尾帧
        size = settings.video_size or size                        # .env 强制 size 优先
        body: dict = {
            "model": self.model,
            "prompt": prompt or "",
            "image_url": image_url,
            "quality": settings.video_quality or "quality",
            "size": size,
            "fps": int(settings.video_fps or 30),
            # 有声/无声按请求决定(人声 on→true 自带音效;旁白 on→false 出无声,再叠 AI 旁白)。未传则回退配置。
            "with_audio": bool(settings.video_with_audio if with_audio is None else with_audio),
        }
        dur = int(seconds or settings.video_seconds or 0)
        if dur:
            # ⚠ duration 字段名/取值以智谱实测为准(拿到 key 跑通后微调);不支持就删这行。CogVideoX-3 支持 5/10。
            body["duration"] = dur
        headers = {"Authorization": "Bearer " + settings.video_api_key, "Content-Type": "application/json"}

        # 三层健壮性:① 建任务/轮询/下载 各自重试网络抖动(_build_task/_await_result);
        # ② 任务级重试——智谱偶发把任务判 FAIL(实测返回『网络错误,请稍后重试』),它让我们稍后重试,
        #    于是退避后【重新建一个新任务】(重新轮询同一个 FAIL 任务没用);最多 3 个任务。
        # 双分镜可达 17min、轮询上百次、还可能撞上并发软限流 → 没这层会整单挂。
        last = "未知"
        for task_try in range(3):
            try:
                with httpx.Client(timeout=httpx.Timeout(120.0)) as c:
                    task_id = self._build_task(c, body, headers)
                    return self._await_result(c, task_id, headers, size)
            except _TaskFailed as exc:
                last = f"任务FAIL: {exc}"
            except httpx.TransportError as exc:
                last = f"网络: {type(exc).__name__}"
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    try:
                        detail = exc.response.text[:300]
                    except Exception:  # noqa: BLE001
                        detail = ""
                    raise RuntimeError(f"智谱视频 HTTP {exc.response.status_code}: {detail}") from exc
                last = f"HTTP {exc.response.status_code}"
            time.sleep(5 * (task_try + 1))      # 智谱让"稍后重试" → 退避后重建任务
        raise RuntimeError(f"智谱视频任务多次失败(已重试 3 次): {last}")


def get_video_provider() -> VideoProvider:
    """按 POD_VIDEO_PROVIDER 取 Provider。默认 local(兜底 GIF);cogvideox=智谱真视频。"""
    p = (settings.video_provider or "local").lower()
    if p in ("cogvideox", "cogvideox-3", "zhipu"):
        return ZhipuCogVideoProvider()
    if p == "local":
        return LocalGifProvider()
    raise RuntimeError(f"未知 POD_VIDEO_PROVIDER: {p}(支持 local / cogvideox)")
