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

# 视频配音/对白语言(主打巴西=葡萄牙语)。「无对白」=不加语言指令。
LANGUAGES: list[str] = ["葡萄牙语", "英语", "西班牙语", "中文", "无对白"]

# 视频描述(镜头脚本)由前端「视频类型」按钮填入、可自定义编辑(开箱/达人/场景/广告大片/互动/自定义)。
# ── 专业化提示词架构(参考实操经验)──────────────────────────────────────────
# 视频模型最吃「镜头脚本(动作序列/时间轴)+ 类目专属动作 + 地区风格 + 负向词」,而非堆形容词。
# 后端把这些层统一拼到用户的镜头脚本后面:类目动作 + 地区 UGC 风格(按语言)+ 一致性 + 负向。

# 商品类目 → 专属动作序列(POD 常见品类;模型有具体动作可演,远胜通用开箱)。
CATEGORIES: list[str] = ["通用", "T恤", "卫衣", "马克杯", "手机壳", "帆布袋", "海报", "抱枕"]
CATEGORY_ACTIONS: dict[str, str] = {
    "通用": "",
    "T恤": "展开并抖动 T 恤展示版型,镜头扫过胸前印花,再切到模特上身穿着、转身展示正背面效果。",
    "卫衣": "展开卫衣展示厚度与版型,特写印花与面料质感,模特上身穿着、拉一下帽子,展示宽松效果。",
    "马克杯": "拿起马克杯缓慢旋转一周展示杯身印花,倒入咖啡冒出热气,手握杯子展示握感与容量。",
    "手机壳": "拿起手机壳展示图案,装到手机背面,翻转展示边缘与摄像头开孔,握在手中展示手感。",
    "帆布袋": "展开帆布袋展示印花,装入物品展示容量,挎上肩自然走动展示日常使用效果。",
    "海报": "展开海报平铺展示完整画面,镜头缓缓扫过细节,贴/挂到墙上展示装饰效果。",
    "抱枕": "拿起抱枕展示双面印花,捏一捏展示蓬松手感,放到沙发上摆好展示家居氛围。",
}

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

# 负向提示词(模型对负向词很敏感,提升常比正向词更大)。以文本形式追加(稳妥,不依赖单独 API 字段)。
NEGATIVE_BLOCK = (
    "请务必避免:人物僵硬或静止不动、机械重复动作、呆板摆拍感、商业广告片或 CG 动画质感、"
    "过度平滑或冻结的镜头、畸形或多余的手指手部、面部模糊扭曲、过度美颜磨皮、"
    "商品悬浮或不符合物理规律、商品被改样/拉伸/变形、低质量画面。"
)

# 「场景首帧」两步生成:每类目对应的首帧场景(gpt-image 把商品放进该场景做视频第一帧 → 缓解硬切)。
_SCENE_BY_CAT: dict[str, str] = {
    "通用": "这件商品被真实使用的生活化场景",
    "T恤": "模特穿着这件 T 恤的真实街拍/居家场景",
    "卫衣": "模特穿着这件卫衣的真实街拍/居家场景",
    "马克杯": "马克杯摆在桌面、旁边有人准备拿起使用的居家场景",
    "手机壳": "手机装着这个壳被人拿在手里使用的生活场景",
    "帆布袋": "有人挎着这个帆布袋外出的日常生活场景",
    "海报": "这张海报贴在/挂在房间墙上的居家场景",
    "抱枕": "这个抱枕放在沙发上的温馨客厅场景",
}

# 全局一致性 + 自然开场 + 防拉伸 + 质感指令,统一追加到所有 prompt(无论用户怎么写描述都生效)。
# 痛点:① 首帧=上传图后生硬切到场景、② 商品被模型改样/扭曲、③ 按画幅生硬拉伸 → 这段一起按住。
_QUALITY_SUFFIX = (
    "视频以上传的商品图为起始画面,从第一帧起就自然、连贯地融入并展开成上述场景与风格,"
    "开场顺滑、镜头与场景渐进过渡,绝不生硬切换、不瞬间跳变场景,全程画面连续流畅;商品自始至终是画面主体。"
    "严格保持商品的原始比例与外观,绝不拉伸、压扁或扭曲商品;保持商品颜色、图案与细节真实一致、不凭空增减元素;"
    "镜头运动平滑克制,光线自然,商业级高画质、真实可信。"
)

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


def compose_prompt(motion: str = "", language: str = "葡萄牙语", category: str = "通用") -> str:
    """专业化拼装:镜头脚本(用户填/改)+ 类目专属动作 + 地区 UGC 风格(按语言)+ 语言
    + 全局一致性/自然开场/防拉伸 + 负向词 → 最终 prompt。语言/画幅/类目是独立选项,不被描述冲掉。"""
    parts: list[str] = []
    motion = (motion or "").strip()
    if motion:
        parts.append(motion)
    act = CATEGORY_ACTIONS.get(category, "")
    if act:
        parts.append(act)
    region = REGION_STYLE.get(language)   # 按语言智能匹配地区风格(无对白/未知 → 不加)
    if region:
        parts.append(region)
    if language and language != "无对白":
        parts.append(f"视频中的人物对白与配音使用{language}。")
    parts.append(_QUALITY_SUFFIX)
    parts.append(NEGATIVE_BLOCK)
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


def scene_frame_prompt(category: str = "通用", language: str = "葡萄牙语") -> str:
    """「场景首帧」用的 gpt-image 编辑指令:把商品放进类目对应的场景、保持商品本身不变。地区风格随语言变。"""
    scene = _SCENE_BY_CAT.get(category, _SCENE_BY_CAT["通用"])
    region = _REGION_HINT.get(language, "")
    region_txt = f"{region}本地" if region else "本地"
    return (
        f"把图中的商品自然地放进「{scene}」中,作为一段 TikTok 短视频的第一帧画面。"
        "严格保持商品本身的图案、文字、颜色、形状和比例完全不变,只生成商品周围真实生活化的场景与背景,"
        f"{region_txt}居家风格、温暖自然光、真实 UGC 随手拍质感,不要广告摄影棚感、不要改动商品。"
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

    def image_to_video(self, images: list[Image.Image], prompt: str, *, size: str = "1080x1920") -> dict:
        """images: 1~2 张(2 张=首尾帧),已按 size 画幅处理好。返回 {bytes, url, ext('mp4'|'gif'), meta}。"""
        ...


def _parse_size(s: str) -> tuple[int, int]:
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 1080, 1920


def _encode_data_uri(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class LocalGifProvider:
    """离线兜底:不调 AI,用现有运镜/轮播出 GIF(降级,非真 AI 视频)。无 key/未配置时用它。"""
    name = "local"

    def image_to_video(self, images: list[Image.Image], prompt: str, *, size: str = "1080x1920") -> dict:
        from ..services.video import make_showcase
        w, h = _parse_size(size)
        aspect = "portrait" if w < h else ("landscape" if w > h else "square")
        style = "slideshow" if len(images) > 1 else "kenburns"
        out = make_showcase(images[:2], style=style, aspect=aspect, fps=12, seconds=settings.video_seconds)
        return {
            "bytes": out["bytes"], "url": "", "ext": "gif",
            "meta": {"engine": "local-gif", "degraded": True,
                     **{k: out[k] for k in ("frames", "width", "height", "duration_ms")}},
        }


class ZhipuCogVideoProvider:
    """智谱 CogVideoX-3 图生视频。建任务→轮询→下载 mp4。1图=首帧,2图=[首,尾]首尾帧。"""
    name = "cogvideox"

    def __init__(self) -> None:
        if not settings.video_api_key:
            raise RuntimeError("POD_VIDEO_API_KEY 未配置(智谱开放平台 key)")
        self.base = (settings.video_base_url or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        self.model = settings.video_model or "cogvideox-3"

    def image_to_video(self, images: list[Image.Image], prompt: str, *, size: str = "1080x1920") -> dict:
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
            "with_audio": bool(settings.video_with_audio),
        }
        if settings.video_seconds:
            # ⚠ duration 字段名/取值以智谱实测为准(拿到 key 跑通后微调);不支持就删这行。
            body["duration"] = int(settings.video_seconds)
        headers = {"Authorization": "Bearer " + settings.video_api_key, "Content-Type": "application/json"}

        with httpx.Client(timeout=httpx.Timeout(60.0)) as c:
            r = c.post(self.base + "/videos/generations", headers=headers, json=body)
            r.raise_for_status()
            task_id = (r.json() or {}).get("id")
            if not task_id:
                raise RuntimeError(f"智谱未返回任务 id: {r.text[:200]}")
            deadline = time.monotonic() + float(settings.video_timeout)
            while time.monotonic() < deadline:
                time.sleep(float(settings.video_poll_interval))
                rr = c.get(self.base + "/async-result/" + task_id, headers=headers)
                rr.raise_for_status()
                d = rr.json() or {}
                st = str(d.get("task_status", "")).upper()
                if st == "SUCCESS":
                    vids = d.get("video_result") or []
                    url = (vids[0].get("url") if vids else "") or ""
                    if not url:
                        raise RuntimeError("任务成功但无视频 URL")
                    data = c.get(url, timeout=httpx.Timeout(120.0)).content
                    return {"bytes": data, "url": url, "ext": "mp4",
                            "meta": {"engine": "cogvideox-3", "task_id": task_id,
                                     "cover": (vids[0].get("cover_image_url") or ""), "size": size}}
                if st in ("FAIL", "FAILED", "ERROR"):
                    raise RuntimeError(f"智谱视频任务失败: {str(d)[:200]}")
            raise RuntimeError("视频生成超时(可调 POD_VIDEO_TIMEOUT)")


def get_video_provider() -> VideoProvider:
    """按 POD_VIDEO_PROVIDER 取 Provider。默认 local(兜底 GIF);cogvideox=智谱真视频。"""
    p = (settings.video_provider or "local").lower()
    if p in ("cogvideox", "cogvideox-3", "zhipu"):
        return ZhipuCogVideoProvider()
    if p == "local":
        return LocalGifProvider()
    raise RuntimeError(f"未知 POD_VIDEO_PROVIDER: {p}(支持 local / cogvideox)")
