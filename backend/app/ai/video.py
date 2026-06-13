"""图生视频 Provider(可插拔,对齐 matting/upscale 范式)。

- LocalGifProvider:不调 AI,用现有 Ken-Burns/轮播出 GIF —— 离线兜底,无 key 也能出东西(降级,非真视频)。
- ZhipuCogVideoProvider:调智谱 CogVideoX-3(建任务→轮询→下载 mp4)。
  图生视频:`image_url` 传单图 = 首帧;传 `[首, 尾]` 数组 = 首尾帧(对应前端 1~2 张图)。
  不让用户选分辨率:`size` 按画幅取高分辨率(扣费与分辨率无关)。

换厂商 = 新增一个 Provider 类 + 改 `POD_VIDEO_PROVIDER`,业务/前端不动。
重依赖(httpx)在方法内惰性 import,保持离线启动轻量。
"""
from __future__ import annotations

import base64
import io
import time
from typing import Protocol, runtime_checkable

from PIL import Image

from ..config import settings

# 各画幅对应的高分辨率(不让用户选分辨率,直接用高的)。CogVideoX-3 最高 4K;默认给 1080p 级
# (4K 生成慢、文件大)。要 4K:把 .env 的 POD_VIDEO_SIZE 设成 3840x2160。
ASPECT_SIZE: dict[str, str] = {
    "portrait": "1080x1920",   # 9:16 竖版(TK/带货首选)
    "landscape": "1920x1080",  # 16:9
    "square": "1440x1440",     # 1:1
}

# 厂商官方文档(选型/排错时看)。换厂商照 ZhipuCogVideoProvider 再写一个即可。
_VENDOR_DOCS = {
    "cogvideox": "https://docs.bigmodel.cn/cn/guide/models/video-generation/cogvideox-3",
}


@runtime_checkable
class VideoProvider(Protocol):
    name: str

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait") -> dict:
        """images: 1~2 张(2 张=首尾帧)。返回 {bytes, url, ext('mp4'|'gif'), meta}。"""
        ...


def _encode_data_uri(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class LocalGifProvider:
    """离线兜底:不调 AI,用现有运镜/轮播出 GIF(降级,非真 AI 视频)。无 key/未配置时用它。"""
    name = "local"

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait") -> dict:
        from ..services.video import make_showcase
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

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait") -> dict:
        import httpx  # 惰性
        if not images:
            raise RuntimeError("图生视频至少需要 1 张图")
        encoded = [_encode_data_uri(im) for im in images[:2]]
        image_url = encoded if len(encoded) > 1 else encoded[0]   # 数组=首尾帧
        size = settings.video_size or ASPECT_SIZE.get(aspect, ASPECT_SIZE["portrait"])
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
