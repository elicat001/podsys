"""Provider protocols — the seam that lets us swap Pillow ↔ rembg ↔ GPU ↔ API."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from PIL import Image


@runtime_checkable
class MattingProvider(Protocol):
    """Background removal: RGB(A) image in, RGBA image out (subject opaque, bg transparent)."""
    name: str

    def cutout(self, image: Image.Image) -> Image.Image:
        ...


@runtime_checkable
class UpscaleProvider(Protocol):
    """Image super-resolution: image in, larger image out."""
    name: str

    def upscale(self, image: Image.Image, scale: float = 2.0) -> Image.Image:
        ...


@runtime_checkable
class VideoProvider(Protocol):
    """图生视频统一契约(N4:CogVideoX / Vidu / 未来 Runway/Kling 共用一份,业务层只认它)。

    images: 1~2 张(2 张=首尾帧),已按画幅贴合好。厂商各取所需:
    - 画幅/分辨率:provider 内部据 aspect+resolution 算真正尺寸(CogVideoX 的 size / Vidu 的 resolution+aspect)。
    - audio:原生音效开关(None=用配置默认);audio_type:音效类型(仅 Vidu 用,CogVideoX 忽略)。
    返回 {bytes, url, ext('mp4'|'gif'), meta}。
    """
    name: str

    def image_to_video(self, images: list[Image.Image], prompt: str, *, aspect: str = "portrait",
                       resolution: str = "720p", seconds: int | None = None,
                       audio: bool | None = None, audio_type: str = "") -> dict:
        ...
