"""图生视频 Provider —— Vidu(生数科技 viduq3 系列),与 CogVideoX 并存的【第二套引擎】。

为什么单独一套(而非塞进 ai/video.py)?
- Vidu Q3 单次最高 16s、24fps,且【原生音画同步】(一次推理同时出 对白口型 + 音效 + BGM),
  reference2video 多图参考(1-7 张)天然保持主体一致 → 一次调用就出 15s 多镜头带声短片,
  不需要 CogVideoX 那套「拆段并行 + ffmpeg 拼接 + per-shot 母帧」。
- 提示词工程也不同:Vidu 把【多镜头 + 音频层】写在同一条 prompt 里(官方建议 50-150 字、音频层放末尾),
  靠模型自身能力;CogVideoX 是一镜一段。故这里是【借鉴思路、另写一套】,不照抄 ai/video.py。

可插拔:换厂商 = 再写一个 Provider;业务/前端不动。重依赖(httpx)方法内惰性 import,保持离线启动轻量。

官方接口(platform.vidu.cn / api.vidu.cn,国际 api.vidu.com)—— 参数以官方文档为准:
- 建任务:POST {base}/ent/v2/img2video       —— 1 张图=首帧锁定(印花保真最好)。Q3 模型名:viduq3-pro / viduq3-turbo / viduq3-pro-fast
        POST {base}/ent/v2/reference2video —— 1-7 张参考图,跨镜主体一致。Q3 模型名:viduq3 / viduq3-mix / viduq3-turbo
  ⚠ 两端点的合法 Q3 模型名不同 → 按端点选对(否则 400)。故 img2video / reference2video 各用一个配置项。
- 轮询:  GET  {base}/ent/v2/tasks/{task_id}/creations —— state ∈ created/queueing/processing/success/failed
- 鉴权:  Authorization: Token {api_key}
- 时长:  Q3 = 1-16s(reference2video 3-16s);分辨率 540p/720p/1080p;aspect_ratio 9:16/3:4/1:1/4:3/16:9
- 音频:  audio(bool,Q3 默认 true)+ audio_type(All / Speech_only / Sound-effect_only)+ voice_id(可选,默认自动)
          对白语言由 prompt 文本控制(中/英支持最好);bgm 参数【对 Q3 无效】(Q3 原生出 BGM);movement_amplitude 对 Q3 无效。
"""
from __future__ import annotations

import base64
import io
import time
from typing import Protocol, runtime_checkable

from PIL import Image

from ..config import settings

# ── 画幅(与前端按钮一一对应)。Vidu 用 "9:16" 字符串;同时给像素比例用于 fit_to_aspect 防拉伸。──
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
RESOLUTIONS: list[str] = ["720p", "1080p"]     # 前端可选(Q3 也支持 540p,POD 出片不暴露太低档)
# 时长【连续可选】(Q3 支持 1-16s;POD 起步 5s,计费=秒数×2)。前端用滑块,不再是固定几档。
DURATION_MIN: int = 5
DURATION_MAX: int = 16
MULTISHOT_FROM: int = 10                        # ≥ 这个秒数 → 在同一条 prompt 内写多镜头(Vidu 原生,不拼接)

# 声音模式(前端单选,互斥)→ 映射到 Vidu audio/audio_type 或 edge-tts 旁白:
#   none      = 无声(audio=false)
#   sfx       = 原生音效(audio=true, audio_type=Sound-effect_only,只环境音无人声)
#   dialogue  = 原生音画同步(audio=true, audio_type=All,对白口型同步+音效+BGM;对白语言由 prompt 控制,中/英最佳)
#   voiceover = 真人旁白(audio=false 静音生成 + edge-tts 叠回,多语言+字幕,补 Q3 对葡/西语支持)
SOUND_MODES: list[str] = ["none", "sfx", "dialogue", "voiceover"]
# audio_type 取值以官方 img2video 文档大小写为准(reference2video 文档为小写,实测若被拒按该端点大小写调这里即可)
_AUDIO_TYPE = {"sfx": "Sound-effect_only", "dialogue": "All"}
# 原生对白语言(Q3 原生音画同步支持最好的是中/英;葡/西走 voiceover 旁白)
NATIVE_DIALOGUE_LANGS: list[str] = ["英文", "中文"]
# edge-tts 旁白语言(补 Q3 不擅长的市场语言)
LANGUAGES: list[str] = ["葡萄牙语", "英语", "西班牙语", "中文", "无对白"]

# 地区风格随语言变(别写死巴西);精简成短语(官方建议 prompt 别过长堆砌)。
_REGION_HINT: dict[str, str] = {
    "葡萄牙语": "巴西年轻人真实生活感、自然光、随手拍质感",
    "英语": "欧美年轻人日常生活感、自然光、随手拍质感",
    "西班牙语": "拉美年轻人热情生活感、自然光、随手拍质感",
    "中文": "本地年轻人生活化日常、自然光、随手拍质感",
    "英文": "自然光、真实生活感、随手拍质感",
}


def vidu_aspect(aspect: str) -> str:
    return _VIDU_ASPECT.get(aspect, "9:16")


def clamp_seconds(seconds: int) -> int:
    """把时长夹到 Q3 合法且 POD 允许的区间 [DURATION_MIN, DURATION_MAX]。"""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        s = DURATION_MIN
    return max(DURATION_MIN, min(DURATION_MAX, s))


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


def _default_multishot(n: int) -> str:
    """通用多镜头骨架(n=2/3),品类无关、产品为主角、精简(对齐 Vidu 50-150 字建议)。
    ⚠ 不写死任何品类/人物属性(避免僵尸化与硬编码偏见);只规定镜头语言与节奏,内容随商品自适应。"""
    if n >= 3:
        return ("三个连续镜头、同一商品同一氛围:镜头一中景,商品在干净自然环境里出场、轻缓推近;"
                "镜头二近景特写,小幅环绕突出图案与材质细节;镜头三中景跟拍,商品被自然使用/穿用收尾,切换顺滑。")
    return ("两个连续镜头、氛围一致:镜头一中景商品出场、轻缓推近;"
            "镜头二近景特写小幅环绕,突出图案与材质细节、落在被自然使用的状态,切换顺滑。")


def compose_vidu_prompt(motion: str = "", language: str = "葡萄牙语", seconds: int = 5, *,
                        sound_mode: str = "none", dialogue_lang: str = "英文") -> str:
    """按 Vidu Q3 官方结构组装 prompt(精简、结构化、音频层放末尾)。

    官方公式:主体+动作 → 场景 → 运镜/景别 → 光影/氛围 → 音频层(SFX/BGM/对白)。官方建议 50-150 字,过长会分散注意力。
    与 CogVideoX(一镜一段、靠母帧换场景)的本质区别:多镜头 + 音频【写在同一条 prompt 内】,Vidu 一次出片。

    - 用户写了 motion(脚本)→ 尊重用户脚本,只补地区氛围 + 一致性底线 + 音频层,不强塞默认骨架。
    - 没写且 ≥MULTISHOT_FROM → 给通用多镜头骨架;否则单镜头展示句。
    - sound_mode:dialogue/sfx 时在末尾追加【音频层】(dialogue 用 dialogue_lang 说话、口型同步)。
    """
    parts: list[str] = []
    motion = (motion or "").strip()
    if motion:
        parts.append(motion)
    elif seconds >= MULTISHOT_FROM:
        parts.append(_default_multishot(3 if seconds >= 15 else 2))
    else:
        parts.append("商品居中、清晰可辨、占据画面主体,镜头轻缓推近并小幅平移,第一帧就读懂在卖什么,落在被自然使用的状态。")
    region = _REGION_HINT.get(language)
    if region:
        parts.append(region + "。")
    # 印花一致底线(一句,简短 —— 官方忌负向堆砌)
    parts.append("商品的图案、文字、颜色始终保持一致、不变形;材质物理真实、动作连贯。")
    # 音频层(放末尾,Vidu 官方建议位置)
    if sound_mode == "dialogue":
        dl = dialogue_lang or "英文"
        parts.append(f"音频:画面中的人用{dl}自然说出贴合商品卖点的简短话语、口型同步,搭配贴合场景的环境音效与轻背景音乐。")
    elif sound_mode == "sfx":
        parts.append("音频:只有贴合画面的真实环境音效,无人声。")
    return " ".join(parts)


@runtime_checkable
class ViduVideoProvider(Protocol):
    name: str

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait",
                       resolution: str = "720p", seconds: int = 5, audio: bool = False,
                       audio_type: str = "All", voice_id: str = "") -> dict:
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
                       audio_type: str = "All", voice_id: str = "") -> dict:
        from ..services.video import make_showcase
        style = "slideshow" if len(images) > 1 else "kenburns"
        out = make_showcase(images[:2], style=style, aspect=aspect, fps=12, seconds=min(int(seconds or 5), 10))
        return {"bytes": out["bytes"], "url": "", "ext": "gif",
                "meta": {"engine": "local-gif", "degraded": True,
                         **{k: out[k] for k in ("frames", "width", "height", "duration_ms")}}}


class _TaskFailed(Exception):
    """Vidu 把任务判 failed(应用层失败)→ 可重建新任务重试,区别于网络层异常。"""


class ViduProvider:
    """Vidu(viduq3 系列)图生视频。1 张图 → img2video(首帧锁定,印花保真最好);2+ 张 → reference2video(多图参考)。
    建任务 → 轮询 /tasks/{id}/creations → 下载 mp4。网络层 + 任务级双重重试(对齐 CogVideoX 健壮性)。"""
    name = "vidu"

    def __init__(self) -> None:
        if not settings.vidu_api_key:
            raise RuntimeError("POD_VIDU_API_KEY 未配置(Vidu 开放平台 key)")
        self.base = (settings.vidu_base_url or "https://api.vidu.cn").rstrip("/")
        self.model = settings.vidu_model or "viduq3-pro"          # img2video 合法 Q3 名
        self.ref_model = settings.vidu_ref_model or "viduq3"      # reference2video 合法 Q3 名

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
                       audio_type: str = "All", voice_id: str = "") -> dict:
        import httpx
        if not images:
            raise RuntimeError("图生视频至少需要 1 张图")
        encoded = [_encode_data_uri(im) for im in images[:7]]   # Vidu 参考图最多 7 张
        # 1 张 → img2video(首帧锁定,印花保真最好);2+ 张 → reference2video(多图参考主体)。两端点模型名不同!
        if len(encoded) == 1:
            path = "/ent/v2/img2video"
            model = self.model
            body: dict = {"model": model, "images": encoded, "prompt": prompt or "",
                          "duration": clamp_seconds(seconds), "resolution": resolution}
        else:
            path = "/ent/v2/reference2video"
            model = self.ref_model
            body = {"model": model, "images": encoded, "prompt": prompt or "",
                    "duration": clamp_seconds(seconds), "resolution": resolution,
                    "aspect_ratio": vidu_aspect(aspect)}
        # 音频:显式发 audio(覆盖 Q3 默认 true);开启时带 audio_type / 可选 voice_id。bgm 对 Q3 无效,不发。
        body["audio"] = bool(audio)
        if audio and audio_type:
            body["audio_type"] = audio_type
        if audio and voice_id:
            body["voice_id"] = voice_id
        # movement_amplitude 对 Q3 无效 → 仅老模型(viduq1/vidu2.0)才发
        if model.startswith(("viduq1", "vidu2", "vidu1")):
            body["movement_amplitude"] = settings.vidu_movement or "auto"
        self._last_model = model   # 记录实际用的模型,写进 meta.engine

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
    if p in ("vidu", "viduq3", "shengshu"):
        return ViduProvider()
    if p == "local":
        return LocalGifProvider()
    raise RuntimeError(f"未知 POD_VIDU_PROVIDER: {p}(支持 local / vidu)")
