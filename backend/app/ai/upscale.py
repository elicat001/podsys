"""Upscale providers.

- pillow : Lanczos 插值(基线,快但放大发糊,不还原细节)。
- fsrcnn : 本地快速超分(FSRCNN x4,cv2.dnn_superres / opencv-contrib)。毫秒级,比 Lanczos 略清晰
           (边缘更锐);纯本地、不联网、不用 key。模型缺失 / 不可用 → 自动降级 Lanczos。

⚠️ 放大绝不能用 gpt-image:它是生成模型,会重绘像素、改动印花,毁掉生产文件。
"""
from __future__ import annotations

from PIL import Image

from ..config import settings


class PillowUpscaleProvider:
    name = "pillow"

    def upscale(self, image: Image.Image, scale: float = 2.0) -> Image.Image:
        w, h = image.size
        return image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


_FSRCNN_SR = None  # 缓存 cv2.dnn_superres 实例


class FsrcnnUpscaleProvider:
    """本地快速超分:FSRCNN x4(cv2.dnn_superres,opencv-contrib)。

    轻量 CNN,**毫秒级**,只比 Lanczos 略清晰(边缘更锐)——速度优先、不丢原有功能的折中。
    模型缺失 / dnn_superres 不可用 / 失败 → 降级 Lanczos。
    """

    name = "fsrcnn"
    _MODEL_SCALE = 4
    _MAX_INPUT = 1200  # 输入长边上限(x4 → 4800;只为控内存)

    def _sr(self):
        global _FSRCNN_SR
        if _FSRCNN_SR is None:
            import cv2
            path = settings.upscale_fsrcnn_path
            if not path.exists():
                raise FileNotFoundError(f"FSRCNN 模型缺失: {path}")
            sr = cv2.dnn_superres.DnnSuperResImpl_create()
            sr.readModel(str(path))
            sr.setModel("fsrcnn", self._MODEL_SCALE)
            _FSRCNN_SR = sr
        return _FSRCNN_SR

    def upscale(self, image: Image.Image, scale: float = 2.0) -> Image.Image:
        try:
            import cv2
            import numpy as np
            src = image.convert("RGB")
            if max(src.size) > self._MAX_INPUT:  # 控 4x 输出内存
                r = self._MAX_INPUT / max(src.size)
                src = src.resize((max(1, round(src.width * r)), max(1, round(src.height * r))), Image.LANCZOS)
            bgr = cv2.cvtColor(np.asarray(src), cv2.COLOR_RGB2BGR)
            up = self._sr().upsample(bgr)  # x4 BGR
            out = Image.fromarray(cv2.cvtColor(up, cv2.COLOR_BGR2RGB))
        except Exception:  # noqa: BLE001  模型缺失/dnn_superres 不可用/失败 → 降级 Lanczos
            return PillowUpscaleProvider().upscale(image, scale)
        if abs(scale - self._MODEL_SCALE) < 0.01:
            return out
        tw, th = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        return out.resize((tw, th), Image.LANCZOS)


_PROVIDERS = {
    "pillow": PillowUpscaleProvider,
    "fsrcnn": FsrcnnUpscaleProvider,
}


def get_upscale_provider():
    cls = _PROVIDERS.get(settings.upscale_provider, PillowUpscaleProvider)
    return cls()
