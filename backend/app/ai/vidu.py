"""图生视频 Provider —— Vidu(生数科技 viduq3 系列),与 CogVideoX 并存的【第二套引擎】。

为什么单独一套(而非塞进 ai/video.py)?
- Vidu Q3 单次最高 16s,且 reference2video 多图参考天然保持主体一致 → 【一次调用就出 15s 多分镜】,
  不需要 CogVideoX 那套「拆 3 段 ×5s 并行 + ffmpeg 拼接 + per-shot 母帧」的复杂编排。
- 提示词工程也不同:Vidu 把【多镜头切换写在同一条 prompt 里】(景别递进 + 运镜切换 + 场景递进),
  靠模型自身的多镜头叙事能力;CogVideoX 是一镜一段。故这里是【借鉴思路、另写一套】,不照抄 ai/video.py。

可插拔:换厂商 = 再写一个 Provider;业务/前端不动。重依赖(httpx)方法内惰性 import,保持离线启动轻量。

官方接口(platform.vidu.cn / api.vidu.cn,国际 api.vidu.com):
- 建任务:POST {base}/ent/v2/img2video       —— 1 张图=首帧锁定(印花保真最好),prompt 描述多镜头
        POST {base}/ent/v2/reference2video —— 多图(≤4)参考主体,跨镜一致(2 张图时用)
- 轮询:  GET  {base}/ent/v2/tasks/{task_id}/creations —— state ∈ created/queueing/processing/success/failed
- 鉴权:  Authorization: Token {api_key}
- 图片:  base64 data uri 或公网 url,≤50MB,png/jpeg/webp
- Q3 时长 1-16s、分辨率 540p-1080p;movement_amplitude 对 Q2/Q3 不生效(只对 viduq1/vidu2.0)。
"""
from __future__ import annotations

import base64
import io
import time
from typing import Protocol, runtime_checkable

from PIL import Image

from ..config import settings

# ── 画幅(与前端按钮一一对应)。Vidu 用 "9:16" 这种字符串;同时给一份像素比例用于 fit_to_aspect 防拉伸。──
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "portrait":    (9, 16),
    "portrait34":  (3, 4),
    "square":      (1, 1),
    "landscape43": (4, 3),
    "landscape":   (16, 9),
}
# Vidu aspect_ratio 取值(reference2video 用;img2video 由首帧画幅决定,不传)
_VIDU_ASPECT: dict[str, str] = {
    "portrait": "9:16", "portrait34": "3:4", "square": "1:1",
    "landscape43": "4:3", "landscape": "16:9",
}
RESOLUTIONS: list[str] = ["720p", "1080p"]     # 前端可选;Q3 也支持 540p,但 POD 出片不暴露太低档
DURATIONS: list[int] = [5, 10, 15]             # Q3 支持 1-16s,这里给 5/10/15;10/15s 触发多镜头叙事
MULTISHOT_FROM: int = 10                        # ≥ 这个秒数 → 在同一条 prompt 里写多镜头分镜(Vidu 原生,不拼接)

# 配音/对白语言(主打巴西=葡语)。「无对白」= 不加语言指令。
LANGUAGES: list[str] = ["葡萄牙语", "英语", "西班牙语", "中文", "无对白"]

# 地区 UGC 风格随语言变(别写死巴西)。Vidu 自己的一份(措辞与 ai/video.py 不同,按 Vidu 偏好精简)。
_REGION_STYLE: dict[str, str] = {
    "葡萄牙语": "巴西本地年轻人的真实生活气息,温暖的自然阳光,热情有活力,手机随手拍的社媒质感",
    "英语": "欧美本地年轻人的日常生活感,自然光,自信松弛,真实社媒随手拍质感",
    "西班牙语": "拉美/西语区年轻人的热情生活感,温暖明亮的光线,有感染力,真实社媒随手拍质感",
    "中文": "本地年轻人的生活化日常,自然光,真实自然,随手拍的社媒质感",
}

# ── Vidu 提示词「电影化」块:景别 + 运镜 + 光影 + 氛围,品类无关、对任意 SKU 通用(不写死品类)。──
# Vidu 词表:景别(远景/全景/中景/近景/特写)、运镜(推进 zoom in/拉远 zoom out/环绕/跟拍/平移/固定)、
#           运动幅度(大动态/中等动态/小幅动态)。多镜头靠景别递进 + 运镜切换在同一 prompt 内表达。
_CINEMA = (
    "整体为干净、真实、有质感的电商种草短片:用柔和自然光真实还原商品的颜色与材质,"
    "运镜专业克制(轻缓推进、平移或小幅环绕,不旋转翻转商品本体),景别在中景与近景特写之间自然过渡以突出图案细节,"
    "中等偏小的运动幅度,画面稳定不糊。"
)
# 画面底线(POD 非协商:印花像素级一致;材质物理真实;连贯)。Vidu 措辞,正向为主。
_GUARD = (
    "商品上的图案、文字、颜色与设计细节自始至终保持一致、不被改样、不扭曲拉伸;"
    "材质物理真实(布料柔软垂坠、不僵硬如纸板,硬物坚固,液体随容器晃荡);"
    "动作遵循重力与接触、连贯不跳帧,镜头切换自然顺滑。"
)


def vidu_aspect(aspect: str) -> str:
    """画幅 key → Vidu aspect_ratio 字符串(reference2video 用)。"""
    return _VIDU_ASPECT.get(aspect, "9:16")


def fit_to_aspect(im: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """把图片按目标画幅等比 contain(不拉伸),其余用同图放大+模糊填充(自然不黑边)。
    img2video 用它把首帧先贴成目标画幅,模型就不会按比例生硬拉伸商品。"""
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
    """画幅 → 一个用于 fit_to_aspect 的像素尺寸(短边 short)。仅用于贴首帧,不决定输出分辨率。"""
    w, h = ASPECT_RATIOS.get(aspect, ASPECT_RATIOS["portrait"])
    if w <= h:
        return short, round(short * h / w)
    return round(short * w / h), short


def compose_vidu_prompt(motion: str = "", language: str = "葡萄牙语", seconds: int = 5) -> str:
    """把用户镜头脚本 + 地区风格 + 语言 + 电影化层 + 画面底线 → Vidu 最终 prompt。

    与 CogVideoX 那套(一镜一段、靠母帧换场景)的【本质区别】:
    - 时长 ≥ MULTISHOT_FROM(10/15s)且用户没写明分镜 → 这里【在同一条 prompt 内】给出多镜头叙事提示
      (镜头一/二/三:景别递进 + 运镜切换 + 场景递进),交给 Vidu 原生多镜头能力一次出片,不做后期拼接。
    - 用户自己写了脚本(motion)→ 尊重用户脚本,只补地区风格/语言/电影化/底线,不强塞分镜模板(避免覆盖用户意图)。
    """
    parts: list[str] = []
    motion = (motion or "").strip()
    if motion:
        parts.append(motion)
    elif seconds >= MULTISHOT_FROM:
        # 用户没写脚本且要长片 → 给一个【通用·品类无关】的多镜头分镜骨架(产品为主角、第一眼读懂在卖什么)。
        n = 3 if seconds >= 15 else 2
        parts.append(_default_multishot(n))
    else:
        parts.append(
            "镜头轻缓推近并平移展示这件商品(Ken-Burns 式),商品居中、清晰可辨、占据画面主要面积,"
            "第一帧就让人一眼读懂这是什么商品、卖点是什么;最后自然落到它被真实使用的状态。"
        )
    region = _REGION_STYLE.get(language)
    if region:
        parts.append(region + "。")
    parts.append(_CINEMA)
    parts.append(_GUARD)
    if language and language != "无对白":
        parts.append(f"如有文字或人声,使用{language}。")
    return " ".join(parts)


def _default_multishot(n: int) -> str:
    """通用多镜头骨架(n=2 或 3),品类无关、产品为主角。每镜不同景别+运镜+场景递进,连成一条连续叙事。
    ⚠ 不写死任何品类/人物属性(避免僵尸化与硬编码偏见);只规定镜头语言与节奏,内容随商品自适应。"""
    if n >= 3:
        return (
            "一条由三个连续镜头组成的带货短片,商品始终是绝对主角、贯穿全片:"
            "【镜头一·0–5秒】中景全貌,商品在一个干净自然的环境里出场,轻缓推近,第一眼读懂在卖什么;"
            "【镜头二·5–10秒】切到近景特写,镜头小幅平移或环绕,突出图案、文字与材质细节,运动幅度中等;"
            "【镜头三·10–15秒】切到商品被自然使用/穿用的真实生活场景,中景跟拍,落在「拥有后的样子」上收尾。"
            "三个镜头是同一件商品、同一种氛围下的连续叙事,镜头切换自然顺滑。"
        )
    return (
        "一条由两个连续镜头组成的带货短片,商品始终是绝对主角:"
        "【镜头一·0–5秒】中景全貌,商品在干净自然的环境里出场、轻缓推近,第一眼读懂在卖什么;"
        "【镜头二·5–10秒】切到近景特写并小幅环绕,突出图案与材质细节,落在被自然使用的状态上收尾。"
        "两个镜头连续叙事、氛围一致,切换自然顺滑。"
    )


@runtime_checkable
class ViduVideoProvider(Protocol):
    name: str

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait",
                       resolution: str = "720p", seconds: int = 5, with_audio: bool = False,
                       bgm: bool = False) -> dict:
        """images: 1~多张参考图。返回 {bytes, url, ext, meta}。"""
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
                       resolution: str = "720p", seconds: int = 5, with_audio: bool = False,
                       bgm: bool = False) -> dict:
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
        self.model = settings.vidu_model or "viduq3"

    def _headers(self) -> dict:
        return {"Authorization": "Token " + settings.vidu_api_key, "Content-Type": "application/json"}

    def _build_task(self, c, path: str, body: dict) -> str:
        """提交任务 → task_id。网络抖动重试 3 次;4xx(鉴权/参数)带响应体不重试。"""
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
        raise RuntimeError("Vidu 建任务失败")  # 兜底,不会到这

    def _await_result(self, c, task_id: str) -> dict:
        """轮询直到 success(下载并返回)。failed → _TaskFailed(可重建);超时 → TimeoutError(不重建)。"""
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
                        "meta": {"engine": self.model, "task_id": task_id, "cover": cover}}
            if st in ("failed", "fail", "error"):
                raise _TaskFailed(str(d.get("err_code") or d.get("err") or d)[:160])
        raise TimeoutError("Vidu 视频生成超时(可调 POD_VIDU_TIMEOUT)")

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait",
                       resolution: str = "720p", seconds: int = 5, with_audio: bool = False,
                       bgm: bool = False) -> dict:
        import httpx  # 惰性
        if not images:
            raise RuntimeError("图生视频至少需要 1 张图")
        encoded = [_encode_data_uri(im) for im in images[:4]]   # Vidu 参考图最多 4 张
        # 1 张 → img2video(首帧锁定,印花保真最好);2+ 张 → reference2video(多图参考主体)
        if len(encoded) == 1:
            path = "/ent/v2/img2video"
            body: dict = {"model": self.model, "images": encoded, "prompt": prompt or "",
                          "duration": int(seconds), "resolution": resolution}
        else:
            path = "/ent/v2/reference2video"
            body = {"model": self.model, "images": encoded, "prompt": prompt or "",
                    "duration": int(seconds), "resolution": resolution,
                    "aspect_ratio": vidu_aspect(aspect)}
        if bgm:
            body["bgm"] = True
        if with_audio:
            body["audio"] = True
        # movement_amplitude 只对 viduq1/vidu2.0 生效,Q2/Q3 不生效 → 仅老模型才发,避免无效字段
        if self.model.startswith(("viduq1", "vidu2", "vidu1")):
            body["movement_amplitude"] = settings.vidu_movement or "auto"

        # 任务级重试:Vidu 偶发把任务判 failed(让稍后重试)→ 退避后重建新任务,最多 3 个(重轮询同一 failed 无用)。
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
