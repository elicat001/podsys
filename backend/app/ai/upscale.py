"""Upscale providers.

- pillow     : Lanczos 插值(基线/兜底,快但放大发糊,不还原细节)。
- realesrgan : 本地 AI 超分『真提质』(Real-ESRGAN SRVGG onnx,去噪+复原细节,~几秒);缺失降级 Lanczos。

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


_REALESR_SESSION = None  # 缓存 onnxruntime session


class RealEsrganOnnxProvider:
    """本地 AI 超分『真提质』:Real-ESRGAN 精简版(SRVGG general-x4v3,onnx)。

    去噪 + 复原细节,效果明显(远超 Lanczos 插值),好图也不糟蹋;~几秒。
    用法:upscale(scale=1) → SR x4 后缩回原尺寸 = 『提质不放大』;scale>1 → 提质并放大。
    模型缺失 / onnx 不可用 / 失败 → 降级 Lanczos。
    """

    name = "realesrgan"
    _SCALE = 4
    _MAX_INPUT = None  # 运行时取 settings.upscale_sr_max_input

    def _session(self):
        global _REALESR_SESSION
        if _REALESR_SESSION is None:
            import onnxruntime as ort  # 惰性 import
            path = settings.upscale_realesrgan_path
            if not path.exists():
                raise FileNotFoundError(f"Real-ESRGAN 模型缺失: {path}")
            _REALESR_SESSION = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        return _REALESR_SESSION

    def upscale(self, image: Image.Image, scale: float = 1.0) -> Image.Image:
        try:
            import numpy as np
            src = image.convert("RGB")
            cap = max(64, settings.upscale_sr_max_input)
            if max(src.size) > cap:  # 大图先缩(控耗时/内存;SRVGG 全卷积,任意尺寸)
                r = cap / max(src.size)
                src = src.resize((max(1, round(src.width * r)), max(1, round(src.height * r))), Image.LANCZOS)
            sess = self._session()
            iname = sess.get_inputs()[0].name
            arr = (np.asarray(src).astype("float32") / 255.0).transpose(2, 0, 1)[None]
            y = sess.run(None, {iname: arr})[0]  # 1,3,4H,4W
            up = Image.fromarray((np.clip(y[0].transpose(1, 2, 0), 0, 1) * 255).astype("uint8"))
        except Exception:  # noqa: BLE001  模型缺失/onnx 不可用/失败 → 降级 Lanczos
            return PillowUpscaleProvider().upscale(image, scale)
        # 目标尺寸 = 原图 × scale(scale=1 → 提质不放大;>1 → 放大)
        tw, th = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        return up.resize((tw, th), Image.LANCZOS)


_PROVIDERS = {
    "pillow": PillowUpscaleProvider,
    "realesrgan": RealEsrganOnnxProvider,
}


def get_upscale_provider():
    cls = _PROVIDERS.get(settings.upscale_provider, PillowUpscaleProvider)
    return cls()
