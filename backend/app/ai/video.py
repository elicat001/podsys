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

# 商品类目 → 母帧使用场景(`_SCENE_BY_CAT`)+ 入库标题。未列入的类目自动走「通用」兜底。
CATEGORIES: list[str] = ["通用", "T恤", "卫衣", "马克杯", "水杯", "手机壳", "帆布袋", "海报", "抱枕", "毛毯"]

# 地区 TikTok UGC 风格:按所选语言智能匹配本地风格(别写死巴西!选什么语言出什么地区的人/氛围),
# 避免「葡语视频里却是国内主播」这种违和。「无对白」=不指定地区风格(中性)。
REGION_STYLE: dict[str, str] = {
    "葡萄牙语": ("整体为巴西 TikTok UGC 风格:巴西本地真实用户(年龄/外貌贴合商品受众,不写死年轻),居家随性的真实环境,温暖的自然阳光,"
                "热情、有活力、表情自然鲜活、有真实情绪流动的肢体语言(生动而不僵硬),真实社媒随手拍质感,而非商业摄影棚摆拍。"),
    "英语": ("整体为欧美 TikTok UGC 风格:欧美本地真实用户(年龄/外貌贴合商品受众,不写死年轻),日常居家或街头环境,自然光照,"
            "自信轻松、自然亲切的表达,真实社媒随手拍质感,而非商业摄影棚摆拍。"),
    "西班牙语": ("整体为拉美/西语区 TikTok UGC 风格:西语区本地真实用户(年龄/外貌贴合商品受众,不写死年轻),热情活力的生活化环境,温暖明亮的光线,"
                "表情自然鲜活、有真实情绪流动、有感染力的肢体语言(生动而不僵硬),真实社媒随手拍质感,而非商业摄影棚摆拍。"),
    "中文": ("整体为中国抖音/TikTok UGC 风格:本地真实用户(年龄/外貌贴合商品受众,不写死年轻),生活化的居家或街头环境,自然光照,"
            "真实自然的表达,真实社媒随手拍质感,而非商业摄影棚摆拍。"),
}

# 导演层(正向为主)—— 核心 prompt 原则:给模型【目标状态】,而不是堆"不要A不要B"。
# 教训:几轮"加负向修问题"后负向占 80% → 模型变保守(安全/稳定/正确但无聊)。正向导演指令才能拉出 TikTok 感。
# 三层:身份(记录真实生活)+ 任务(人物在做的事,【任务动作 > 模特动作】)+ 镜头(手持随手拍)。通用,不写死。
_DIRECTION_BLOCK = (
    "【导演定位】你在记录一段真实生活里的一个片段,不是拍商品广告、也不是摆拍展示商品。"
    "画面中的人此刻正在做一件具体的、有理由发生的事——【具体做什么以本镜脚本/首帧为准】,"
    "贴合这件商品的真实使用情境去演,别套用固定的『出门赴约』之类模板。"
    "优先表现:真实的生活状态、贴合当下情境的【任务动作】(由脚本决定做什么,而不是为展示商品摆 pose)、"
    "当下的情绪和与环境的互动;人物专注在自己做的事上、神态自然放松。"
    "【表情要鲜活、去僵硬(关键)】人物是活生生在经历当下,**有真实的情绪流动和细微的眼神、面部变化**"
    "(开心就自然地笑、专注时认真、放松时松弛——情绪随情境自然流动、有起伏),像一个真人,而不是一张会动的定格 AI 照片。"
    "**不是不能笑**;要避免的是【僵硬不变的表情、呆滞发直的死眼神、对着镜头从头到尾挂着同一个假笑营业、面瘫】。"
    "真实的人也常不刻意看镜头、专注在自己做的事上。"
    "【动作要连成一条因果链(Story Beat,不是步骤罗列)】人物为一个目标、带一种情绪做一连串连贯动作"
    "(把脚本里的动作演成『前一个动作未做完→自然延续→承接下一个』的连续过程),让人觉得「他正在经历一件事」。"
    "若本镜的首帧/脚本显示动作【正进行到一半】(承接上一镜的延续状态),"
    "就从那一刻【接着演下去】、保持同一情绪与目标,绝不回到一个静止起始 pose 重新开始;镜头之间共享同一个尚未完成的动作。"
    "【难动作交给首帧、视频自然接着演(按需)】若这件商品要用起来涉及很容易画坏/穿模的机械动作(开盖/拆封/穿脱/倾倒),"
    "首帧通常已让它处于可直接使用的状态——视频就不必在画面里现场重做这个机械过程,自然地从『已就绪』接着演后续使用即可;"
    "除此之外的一切自然动作(拿起、举起、喝、挥手、转身、走动、把玩等)都正常【大胆做、有真实运动幅度】,别因为怕变形就缩手缩脚、别缩成几乎不动的微动作。"
    "商品作为这件事里自然穿着/使用的道具出现、清晰可见即可,不必刻意举到镜头前。"
    "镜头像真人手持手机随手拍这个瞬间:有自然手持感,可推近/拉远/跟拍/自拍角度,记录而非摆拍。"
)

# 画面底线(简短、正向措辞)—— 只守住【真实踩过的模型失败】(印花被改、像砖块、跳切),不堆负向喧宾夺主。
_GUARD_BLOCK = (
    "画面要求:商品的图案、文字、颜色与设计细节始终保持一致、不被改样或拉伸扭曲;"
    "材质物理真实——布料柔软垂坠不僵硬如纸板、蓬松物受压会回弹、硬物坚固、液体随容器晃荡;"
    "动作遵循重力与接触、连贯不跳帧;开场与场景过渡自然连续。"
)

# 镜头电影化层:补 compose_prompt 偏弱的【景别 + 光影】,但保持真实 UGC(不要影棚塑料感);通用、不按场景写死。
_CINEMA_HUMAN = (
    "【景别与光影(通用)】镜头在中景与近景之间自然切换:既交代人物与环境、也给到商品的细节特写;"
    "用真实自然的光线(室内窗光 / 室外日光),明暗过渡柔和、贴近真实生活,不要影棚布光的塑料感。"
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


def scene_frame_prompt(category: str = "通用", language: str = "葡萄牙语", scene: str = "", action: str = "") -> str:
    """「场景首帧」用的 gpt-image 编辑指令:生成视频【真正的第 0 帧】(脚本开头那一刻),把商品以真实立体使用形态放进场景。地区风格随语言变。

    ⚠ Scene Init(最优先):母帧 = 视频第0帧,不是独立成品图。给了 action(该镜脚本)就让首帧落在它的【起始瞬间】(动作即将开始、尚未展开),
    人物/商品状态与位置/环境/构图都与脚本开头衔接 → 视频能从这帧顺畅接着演,避免开头一次性补全人物/环境/交互而崩(复制/悬空/瞬移/穿模)。
    ⚠ 治"平面像砖块被提起来":图生视频"跟着首帧走"——平铺的衣服会被当成刚性平面来动,像砖块/纸板。
    所以首帧必须呈现真实三维形态:衣物【穿在真人模特身上】有自然版型/褶皱/垂坠;物品被自然拿握/摆放。
    印花/图案/文字/颜色完整保留(产品本体不能改),但【形状要立体真实、不能锁成平面】。
    scene 给了就用它(per-shot 母帧场景);留空回退类目默认场景。action 给了就据它对齐起始状态。"""
    scene = (scene or "").strip() or _SCENE_BY_CAT.get(category, _SCENE_BY_CAT["通用"])
    region = _REGION_HINT.get(language, "")
    region_txt = f"{region}本地" if region else "本地"
    action = (action or "").strip()
    action_line = (
        f"【这一帧是下面这段视频的第 0 帧】视频开头的动作是:『{action[:400]}』——只呈现它【最开始那一刻】的真实状态"
        "(动作即将开始、尚未展开:人物在起始位置、商品在动作开始前该在的位置与状态),绝不要画到动作的中段或结果。"
    ) if action else ""
    return (
        f"把图中的商品自然地放进「{scene}」中,作为一段 TikTok 短视频的【第一帧(第 0 帧)】画面。"
        + action_line +
        "【这是视频真正开始播放的那一刻,不是独立的商品成品/展示图】呈现脚本开头那一瞬的真实起始状态:动作即将开始、尚未展开,"
        "人物、商品状态与位置、环境、镜头构图都停在『马上要动起来』的起点,让视频能从这一帧顺畅地接着演下去;"
        "画面仍要真实有生活感、商品清晰是视觉主角,但重点是【和脚本开头严丝合缝地衔接】,而不是一张摆好的成品展示。"
        "【关键】让商品以真实使用时的立体形态出现,绝不能平铺、悬空或像纸板/砖块一样的平面:"
        "若是衣物(T恤/卫衣/裙装等),要真实地穿在一个真人模特身上,有自然的版型、布料褶皱、垂坠和身体支撑;"
        "若是杯子/手机壳/帆布袋/抱枕等物品,让它被自然地拿在手里或摆在该在的位置,有真实的体积与受光。"
        "必须完整保留商品上的图案、文字、印花、颜色与设计细节(这是产品本身,不可改动、丢失或扭曲变形),"
        "但要让商品呈现真实的三维形态与材质质感(布料柔软、有垂坠与褶皱)。"
        "【若需要『难动作』才能开始用,把它前移到这一帧(按需,不强制)】先判断:要自然使用这件商品,"
        "是否需要一个对视频模型很容易画坏/穿模的机械状态变化(如拧开瓶盖、拉开拉链、穿脱衣物、拆开包装、倒出液体)?"
        "——若需要,就让这一帧【直接呈现该动作已完成、商品处于可直接使用的状态】(如瓶盖已拧开放在一旁、衣服已穿在身上、包装已打开),"
        "好让随后的视频只演自然使用、无需在画面里现场做这个机械过程;若不需要(商品本就能直接拿/用/穿戴展示),"
        "就按最自然的状态呈现、不必刻意改动。无论哪种,商品本体的图案/文字/颜色/形状/比例都与原图完全一致(只是状态不同,绝不重新设计商品)。"
        "【首帧身份锚点】画面里这件商品清晰、完整、只出现一件(同一个个体,不重复/不分身),并有真实支撑(被自然拿握或稳稳放置)、不悬空——给后续视频一个稳定的对象与物理锚点。"
        f"画面要像{region_txt}真实用户(年龄、外貌贴合商品受众,不写死年轻)用手机随手拍的真实生活照/TikTok:自然光、略带生活气、真实抓拍质感,"
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
