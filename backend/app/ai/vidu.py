"""图生视频 Provider —— Vidu(生数科技),与 CogVideoX 并存的【第二套引擎】。

定位(本版聚焦):**把单张商品图 → 真人在生活场景里使用/把玩商品的短视频**(如:按压/旋转解压球、捏捏乐、上身穿搭…)。
路径:商品图 →[场景母帧] gpt-image 把商品合成进"真人正在使用它"的场景做首帧 → Vidu img2video 让它动起来。
默认模型 **viduq2-pro-fast**(快、稳、性价比高;img2video 与 reference2video 同名通用)。

为什么单独一套(而非塞进 ai/video.py)?Vidu 与 CogVideoX 是两套不同模型/接口/提示词;独立文件 → 与他人维护的
ai/video.py 物理隔离、零冲突。重依赖(httpx)方法内惰性 import,保持离线启动轻量。

官方接口(platform.vidu.cn / api.vidu.cn,国际 api.vidu.com)—— 参数以官方文档为准:
- 建任务:POST {base}/ent/v2/img2video       —— 1 张图=首帧;Q2 模型名:viduq2-pro-fast / viduq2-pro / viduq2-turbo
        POST {base}/ent/v2/reference2video —— 多图参考(本版 UI 不暴露,provider 仍支持)
- 轮询:  GET  {base}/ent/v2/tasks/{task_id}/creations —— state ∈ created/queueing/processing/success/failed
- 鉴权:  Authorization: Token {api_key}
- viduq2-pro-fast:时长 1-10s、分辨率 540p/720p/1080p(默认 720p)、audio(音效/环境音)支持;
  movement_amplitude 对 q2/q3 无效(不发);对白口型是 Q3 招牌、Q2 主打音效(故本版不做对白)。
"""
from __future__ import annotations

import base64
import io
import time
from typing import Protocol, runtime_checkable

from PIL import Image

from ..config import settings

# ── 画幅(与前端按钮一一对应)。Vidu 用 "9:16" 字符串;另给像素比例用于 fit_to_aspect 防拉伸。──
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "portrait":    (9, 16),
    "portrait34":  (3, 4),
    "square":      (1, 1),
    "landscape43": (4, 3),
    "landscape":   (16, 9),
}
_VIDU_ASPECT: dict[str, str] = {
    "portrait": "9:16", "portrait34": "3:4", "square": "1:1",
    "landscape43": "4:3", "landscape": "16:9",
}
RESOLUTIONS: list[str] = ["720p", "1080p"]     # viduq2-pro-fast 支持 540/720/1080;POD 暴露 720(默认)/1080
# 时长【连续可选】。viduq2-pro-fast = 1-10s(Q2 上限 10,不是 Q3 的 16)。POD 起步 5s,计费=秒数×2。
DURATION_MIN: int = 5
DURATION_MAX: int = 10

# 声音模式:none=无声(audio=false);sfx=原生音效(audio=true,Vidu 出环境音/动作音,与语言无关);
#   voiceover=真人旁白(audio=false 静音生成 + edge-tts 按市场语言配音 + 字幕)——【葡/西语市场靠这个】,
#   因为 Vidu 原生音频对葡语支持不好,口播仍走 edge-tts(免费、多语言、可烧字幕)。
SOUND_MODES: list[str] = ["none", "sfx", "voiceover"]

# 目标市场 → 语言。决定【场景母帧里出现哪国人 + 氛围】+【真人旁白用什么语言】。主打巴西=葡语。
LANGUAGES: list[str] = ["葡萄牙语", "英语", "西班牙语", "中文"]
_REGION_HINT: dict[str, str] = {
    "葡萄牙语": "巴西年轻人真实生活感、自然光、随手拍质感",
    "英语": "欧美年轻人日常生活感、自然光、随手拍质感",
    "西班牙语": "拉美年轻人热情生活感、自然光、随手拍质感",
    "中文": "本地年轻人生活化日常、自然光、随手拍质感",
}
_REGION_PERSON: dict[str, str] = {"葡萄牙语": "巴西", "英语": "欧美", "西班牙语": "拉美/西语区", "中文": "中国"}


def vidu_aspect(aspect: str) -> str:
    return _VIDU_ASPECT.get(aspect, "9:16")


def clamp_seconds(seconds: int) -> int:
    """把时长夹到 [DURATION_MIN, DURATION_MAX](viduq2-pro-fast 上限 10)。"""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        s = DURATION_MIN
    return max(DURATION_MIN, min(DURATION_MAX, s))


def gptimage_size(aspect: str = "portrait") -> str:
    """画幅 → gpt-image 支持的最接近尺寸(只有 1024x1024 / 1024x1536 / 1536x1024)。场景母帧用。"""
    w, h = ASPECT_RATIOS.get(aspect, ASPECT_RATIOS["portrait"])
    if w < h:
        return "1024x1536"
    if w > h:
        return "1536x1024"
    return "1024x1024"


def fit_to_aspect(im: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比 contain 进目标画幅(不拉伸),其余用同图放大+模糊填充(自然不黑边)。img2video 贴首帧用。"""
    from PIL import ImageFilter, ImageOps
    im = im.convert("RGB")
    if abs(im.width / im.height - target_w / target_h) < 0.02:
        return im.resize((target_w, target_h), Image.LANCZOS)
    bg = ImageOps.fit(im, (target_w, target_h), method=Image.LANCZOS).filter(ImageFilter.GaussianBlur(36))
    fg = im.copy()
    fg.thumbnail((target_w, target_h), Image.LANCZOS)
    bg.paste(fg, ((target_w - fg.width) // 2, (target_h - fg.height) // 2))
    return bg


def _aspect_px(aspect: str, short: int = 1024) -> tuple[int, int]:
    w, h = ASPECT_RATIOS.get(aspect, ASPECT_RATIOS["portrait"])
    if w <= h:
        return short, round(short * h / w)
    return round(short * w / h), short


# 导演层(单镜版,精简):任务驱动 + 一条连续动作链(Story Beat,非摆拍)+ 去僵硬·表情鲜活。
# ⚠ 与 CogVideoX 的区别:不写 Match Cut/多场景拼接(那是它三段拼接专用);Vidu 单镜本就连续,只强调"一气呵成的连续动作"。
_DIRECTION = (
    "这是真人在生活里随手拍的一个连续片段:人物专注做自己手上的事,神态自然鲜活——有真实的情绪流动和细微表情变化"
    "(自然地笑、专注、放松随情境起伏),绝不僵硬假笑、呆滞死眼神或从头对镜头营业;"
    "动作是一条连贯因果的动作链、一气呵成、有真实物理反馈(如旋转、回弹、垂坠);手持随手拍质感,记录真实生活而非摆拍展示商品。"
)


def scene_frame_prompt(language: str = "葡萄牙语", scene: str = "") -> str:
    """场景母帧的 gpt-image 指令:把商品放进【真人正在真实生活里使用/把玩它】的场景做视频第一帧。
    ⚠ 通用·自适应,绝不写死品类/动作/人物属性:人物年龄性别、场景、互动方式都由模型【看商品自己判断】——
    这样换任意 SKU 都成立(解压球→有人要按压旋转它;杯子→有人要端起;T恤→有人穿着),不是硬编码"小孩转球"。
    scene:智能向导可传入【具体场景+人物状态】(产品驱动);留空则由模型看图自适应。"""
    region = _REGION_PERSON.get(language, "")
    rt = f"{region}本地" if region else "本地"
    scene = (scene or "").strip()
    scene_line = f"参考这个具体场景与人物状态:{scene}。" if scene else ""
    return (
        "把图中的商品自然地放进一个【真人正在真实生活里使用或把玩它】的场景,作为短视频第一帧。"
        + scene_line +
        f"让一个{rt}真人(年龄、性别贴合这件商品的真实目标用户,由你看图判断、自然合理)在贴合该商品的生活环境里"
        "(居家桌前/客厅/户外等),正手持、正要上手使用或把玩这个商品,"
        "【正处在一个动作的进行中或即将开始的瞬间】(不是静止摆拍 pose),神态自然放松、像没意识到在拍。"
        "完整保留商品的图案、文字、颜色与形状(产品本体不可改动、丢失或变形),商品以真实立体形态出现、不平铺悬空。"
        f"画面是{rt}年轻人用手机随手拍的真实生活照 / TikTok 质感:自然光、有生活气、真实抓拍,绝不广告摄影棚精修或 CG 感。"
    )


def compose_vidu_prompt(motion: str = "", language: str = "葡萄牙语", seconds: int = 5, *,
                        sound_mode: str = "none") -> str:
    """按 Vidu 官方结构组装 prompt(精简、结构化:主体+动作 → 场景氛围 → 一致性底线 → 音频层)。官方建议 50-150 字。
    - 用户/预设/智能识别写了 motion → 尊重它(这才是"真人把玩商品"的具体动作),只补氛围/底线/音频层。
    - 没写 → 给一句通用的"真人自然使用商品"默认(配合场景母帧的真人首帧)。
    - sound_mode=sfx → 末尾加音频层(贴合画面的环境音效,无对白)。
    """
    parts: list[str] = []
    motion = (motion or "").strip()
    # 动作脚本(智能向导/智能识别/用户写的就是这条连续动作链);没写 → 给一句通用的连续上手动作。
    parts.append(motion or "画面中的人自然地上手使用、把玩这件商品,做出贴合它的一连串连贯真实动作,落在动作的高潮或效果上。")
    parts.append(_DIRECTION)            # 任务驱动 + 单镜连续动作链 + 去僵硬·表情鲜活
    region = _REGION_HINT.get(language)
    if region:
        parts.append(region + "。")
    parts.append("商品的图案、文字、颜色与外形始终保持一致、不变形;材质物理真实、动作遵循重力、连贯不跳帧。")
    if sound_mode == "sfx":
        parts.append("音频:只有贴合画面的真实环境音效与动作音(如旋转/按压/摩擦声),无人声对白。")
    return " ".join(parts)


@runtime_checkable
class ViduVideoProvider(Protocol):
    name: str

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait",
                       resolution: str = "720p", seconds: int = 5, audio: bool = False,
                       audio_type: str = "") -> dict:
        ...


def _encode_data_uri(im: Image.Image) -> str:
    """JPEG base64 data uri(体积小 5~10×,降发图写超时)。Vidu 接受 png/jpeg/webp 的 base64。"""
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class LocalGifProvider:
    """离线兜底:不调 Vidu,用现有运镜/轮播出 GIF(降级,非真 AI 视频)。无 key/未配置/provider 失败时用它。"""
    name = "local"

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait",
                       resolution: str = "720p", seconds: int = 5, audio: bool = False,
                       audio_type: str = "") -> dict:
        from ..services.video import make_showcase
        style = "slideshow" if len(images) > 1 else "kenburns"
        out = make_showcase(images[:2], style=style, aspect=aspect, fps=12, seconds=min(int(seconds or 5), 10))
        return {"bytes": out["bytes"], "url": "", "ext": "gif",
                "meta": {"engine": "local-gif", "degraded": True,
                         **{k: out[k] for k in ("frames", "width", "height", "duration_ms")}}}


class _TaskFailed(Exception):
    """Vidu 把任务判 failed(应用层失败)→ 可重建新任务重试,区别于网络层异常。"""


class ViduProvider:
    """Vidu 图生视频。1 张图 → img2video(默认主路径);2+ 张 → reference2video(多图参考,本版 UI 不暴露但保留)。
    建任务 → 轮询 /tasks/{id}/creations → 下载 mp4。网络层 + 任务级双重重试(对齐 CogVideoX 健壮性)。"""
    name = "vidu"

    def __init__(self) -> None:
        if not settings.vidu_api_key:
            raise RuntimeError("POD_VIDU_API_KEY 未配置(Vidu 开放平台 key)")
        self.base = (settings.vidu_base_url or "https://api.vidu.cn").rstrip("/")
        self.model = settings.vidu_model or "viduq2-pro-fast"          # img2video
        self.ref_model = settings.vidu_ref_model or "viduq2-pro-fast"  # reference2video(viduq2-pro-fast 两端点通用)

    def _headers(self) -> dict:
        return {"Authorization": "Token " + settings.vidu_api_key, "Content-Type": "application/json"}

    def _build_task(self, c, path: str, body: dict) -> str:
        import httpx
        for attempt in range(3):
            try:
                r = c.post(self.base + path, headers=self._headers(), json=body)
                r.raise_for_status()
                d = r.json() or {}
                tid = d.get("task_id") or d.get("id") or ""
                if not tid:
                    raise RuntimeError(f"Vidu 未返回任务 id: {str(d)[:200]}")
                return str(tid)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code < 500 or attempt == 2:
                    try:
                        detail = exc.response.text[:300]
                    except Exception:  # noqa: BLE001
                        detail = ""
                    raise RuntimeError(f"Vidu 建任务失败 HTTP {code}: {detail}") from exc
                time.sleep(2 * (attempt + 1))
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("Vidu 建任务失败")

    def _await_result(self, c, task_id: str) -> dict:
        import httpx
        deadline = time.monotonic() + float(settings.vidu_timeout)
        poll_fails = 0
        while time.monotonic() < deadline:
            time.sleep(float(settings.vidu_poll_interval))
            try:
                rr = c.get(self.base + "/ent/v2/tasks/" + task_id + "/creations", headers=self._headers())
                rr.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError):
                poll_fails += 1
                if poll_fails > 20:
                    raise
                continue
            poll_fails = 0
            d = rr.json() or {}
            st = str(d.get("state", "")).lower()
            if st == "success":
                creations = d.get("creations") or []
                url = (creations[0].get("url") if creations else "") or ""
                if not url:
                    raise _TaskFailed("任务成功但无视频 URL")
                for attempt in range(3):
                    try:
                        data = c.get(url, timeout=httpx.Timeout(180.0)).content
                        break
                    except httpx.TransportError:
                        if attempt == 2:
                            raise
                        time.sleep(3 * (attempt + 1))
                cover = (creations[0].get("cover_url") or "") if creations else ""
                return {"bytes": data, "url": url, "ext": "mp4",
                        "meta": {"engine": getattr(self, "_last_model", self.model),
                                 "task_id": task_id, "cover": cover}}
            if st in ("failed", "fail", "error"):
                raise _TaskFailed(str(d.get("err_code") or d.get("err") or d)[:160])
        raise TimeoutError("Vidu 视频生成超时(可调 POD_VIDU_TIMEOUT)")

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait",
                       resolution: str = "720p", seconds: int = 5, audio: bool = False,
                       audio_type: str = "") -> dict:
        import httpx
        if not images:
            raise RuntimeError("图生视频至少需要 1 张图")
        encoded = [_encode_data_uri(im) for im in images[:7]]   # Vidu 参考图最多 7 张
        if len(encoded) == 1:
            path = "/ent/v2/img2video"
            model = self.model
            body: dict = {"model": model, "images": encoded, "prompt": prompt or "",
                          "duration": clamp_seconds(seconds), "resolution": resolution}
            # 音频【仅 img2video 支持】(官方:reference2video 端点无 audio 字段)。显式发 audio 覆盖默认。
            body["audio"] = bool(audio)
            if audio and audio_type:
                body["audio_type"] = audio_type
        else:
            path = "/ent/v2/reference2video"
            model = self.ref_model
            # ⚠ reference2video【不支持 audio/audio_type】(官方明确)→ 不发,避免无效字段/400。
            body = {"model": model, "images": encoded, "prompt": prompt or "",
                    "duration": clamp_seconds(seconds), "resolution": resolution,
                    "aspect_ratio": vidu_aspect(aspect)}
        # movement_amplitude 对 q2/q3 无效 → 仅老模型(viduq1/vidu2.0)才发
        if model.startswith(("viduq1", "vidu2.0", "vidu1")):
            body["movement_amplitude"] = settings.vidu_movement or "auto"
        self._last_model = model

        last = "未知"
        for task_try in range(3):
            try:
                with httpx.Client(timeout=httpx.Timeout(120.0)) as c:
                    task_id = self._build_task(c, path, body)
                    return self._await_result(c, task_id)
            except _TaskFailed as exc:
                last = f"任务failed: {exc}"
            except httpx.TransportError as exc:
                last = f"网络: {type(exc).__name__}"
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    try:
                        detail = exc.response.text[:300]
                    except Exception:  # noqa: BLE001
                        detail = ""
                    raise RuntimeError(f"Vidu 视频 HTTP {exc.response.status_code}: {detail}") from exc
                last = f"HTTP {exc.response.status_code}"
            time.sleep(5 * (task_try + 1))
        raise RuntimeError(f"Vidu 视频任务多次失败(已重试 3 次): {last}")


def get_vidu_provider() -> ViduVideoProvider:
    """按 POD_VIDU_PROVIDER 取 Provider。默认 local(兜底 GIF);vidu=真 Vidu。"""
    p = (settings.vidu_provider or "local").lower()
    if p in ("vidu", "viduq2", "viduq3", "shengshu"):
        return ViduProvider()
    if p == "local":
        return LocalGifProvider()
    raise RuntimeError(f"未知 POD_VIDU_PROVIDER: {p}(支持 local / vidu)")
