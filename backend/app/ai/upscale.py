"""Upscale providers.

- pillow : Lanczos 插值(基线,快但放大发糊,不还原细节)。
- onnx   : 本地 AI 超分(Swin2SR x4 ONNX,onnxruntime CPU)。能真还原细节、放大更清晰;
           纯本地、不联网、不用 key。慢(CPU 上几十秒~分钟),建议端点走后台作业。

设计:大图先缩到 `upscale_sr_max_input`(SR 本就用于低清放大,且控 CPU 耗时/内存);
模型固定 x4,按需缩放到目标倍数;halo 分块推理(消接缝、控内存);
模型缺失 / onnxruntime 不可用 / 推理异常 → 自动降级 Lanczos(保持纯 Pillow 也能跑)。
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

    轻量 CNN,**毫秒级**(比 Swin2SR 快几百倍),只比 Lanczos 略清晰(边缘更锐)——
    速度优先、不丢原有功能的折中。模型缺失 / dnn_superres 不可用 / 失败 → 降级 Lanczos。
    """

    name = "fsrcnn"
    _MODEL_SCALE = 4
    _MAX_INPUT = 1200  # 输入长边上限(x4 → 4800;只为控内存。FSRCNN 快,比 onnx 的 512 宽松)

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


_ORT_SESSION = None  # 缓存 onnxruntime session(避免每次重载 52MB 模型)


class OnnxUpscaleProvider:
    """本地 AI 超分:Swin2SR x4 ONNX。任何环节失败 → 降级 Lanczos。"""

    name = "onnx"
    _MODEL_SCALE = 4   # 模型固定 4 倍
    _TILE = 128        # 分块输入边长(x4 → 512 输出),控内存/单块耗时
    _HALO = 8          # 块周边上下文,消除拼接接缝(Swin 窗口 8 的倍数)
    _WIN = 8           # Swin 窗口大小:输入边长须为其整数倍,否则 reshape 报错

    def _session(self):
        global _ORT_SESSION
        if _ORT_SESSION is None:
            import onnxruntime as ort  # 惰性 import(重依赖)
            path = settings.upscale_onnx_path
            if not path.exists():
                raise FileNotFoundError(f"超分模型缺失: {path}")
            _ORT_SESSION = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        return _ORT_SESSION

    def upscale(self, image: Image.Image, scale: float = 2.0) -> Image.Image:
        try:
            src = image.convert("RGB")
            # 大图先缩到上限(SR 用于低清放大;且控 CPU 耗时/内存)
            cap = max(16, settings.upscale_sr_max_input)
            if max(src.size) > cap:
                r = cap / max(src.size)
                src = src.resize((max(1, round(src.width * r)), max(1, round(src.height * r))), Image.LANCZOS)
            up4 = self._run_x4(src)
        except Exception:  # noqa: BLE001  模型缺失/onnx 不可用/推理失败 → 降级
            return PillowUpscaleProvider().upscale(image, scale)
        # 模型出 x4;按需缩放到「原图 × scale」
        if abs(scale - self._MODEL_SCALE) < 0.01:
            return up4
        tw, th = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        return up4.resize((tw, th), Image.LANCZOS)

    def _run_x4(self, image: Image.Image) -> Image.Image:
        import numpy as np
        sess = self._session()
        iname = sess.get_inputs()[0].name
        src = np.asarray(image).astype("float32") / 255.0  # HWC, 0..1
        H, W = src.shape[:2]
        S, T, HALO = self._MODEL_SCALE, self._TILE, self._HALO
        out = np.zeros((H * S, W * S, 3), dtype="float32")
        for y0 in range(0, H, T):
            for x0 in range(0, W, T):
                y1, x1 = min(y0 + T, H), min(x0 + T, W)
                # 读取带 halo 的输入块(给推理足够上下文,消接缝)
                ry0, rx0 = max(0, y0 - HALO), max(0, x0 - HALO)
                ry1, rx1 = min(H, y1 + HALO), min(W, x1 + HALO)
                up = self._infer(src[ry0:ry1, rx0:rx1], sess, iname)  # (rh*S, rw*S, 3)
                # 从放大块里取出本 tile 对应中心区(去掉 halo),写回输出
                oy, ox = (y0 - ry0) * S, (x0 - rx0) * S
                out[y0 * S:y1 * S, x0 * S:x1 * S] = up[oy:oy + (y1 - y0) * S, ox:ox + (x1 - x0) * S]
        return Image.fromarray((np.clip(out, 0, 1) * 255).astype("uint8"))

    def _infer(self, region, sess, iname):
        import numpy as np
        h, w = region.shape[:2]
        # pad 到 Swin 窗口(8)的整数倍,否则 patch_unembed reshape 报错
        ph, pw = (self._WIN - h % self._WIN) % self._WIN, (self._WIN - w % self._WIN) % self._WIN
        t = np.pad(region, ((0, ph), (0, pw), (0, 0)), mode="reflect")
        res = sess.run(None, {iname: t.transpose(2, 0, 1)[None]})[0][0]  # 3, H*4, W*4
        res = res.transpose(1, 2, 0)  # HWC
        return res[: h * self._MODEL_SCALE, : w * self._MODEL_SCALE]  # 去 pad 区


class RealEsrganUpscaleProvider:
    """占位(GPU Real-ESRGAN);本地 CPU 超分请用 'onnx'。"""
    name = "realesrgan"

    def upscale(self, image: Image.Image, scale: float = 2.0) -> Image.Image:
        raise NotImplementedError("Real-ESRGAN GPU provider 未接;CPU 本地超分请用 POD_UPSCALE_PROVIDER=onnx")


_PROVIDERS = {
    "pillow": PillowUpscaleProvider,
    "fsrcnn": FsrcnnUpscaleProvider,
    "onnx": OnnxUpscaleProvider,
    "realesrgan": RealEsrganUpscaleProvider,
}


def get_upscale_provider():
    cls = _PROVIDERS.get(settings.upscale_provider, PillowUpscaleProvider)
    return cls()
