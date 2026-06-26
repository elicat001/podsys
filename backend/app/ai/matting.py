"""Background-removal providers.

Default `pillow` provider needs no model and runs on CPU — good enough for
uniform-background product photos and lets the whole pipeline run + be verified.
Swap to `rembg` (open-source U2Net/BiRefNet) or `api` for production quality.
"""
from __future__ import annotations

import logging

from PIL import Image, ImageChops, ImageFilter

from ..config import settings

log = logging.getLogger(__name__)


def _estimate_bg_color(rgb: Image.Image, sample: int = 12) -> tuple[int, int, int]:
    """Median-ish background color from the four corners."""
    w, h = rgb.size
    boxes = [
        (0, 0, sample, sample),
        (w - sample, 0, w, sample),
        (0, h - sample, sample, h),
        (w - sample, h - sample, w, h),
    ]
    rs, gs, bs = [], [], []
    for box in boxes:
        crop = rgb.crop(box)
        r, g, b = crop.resize((1, 1)).getpixel((0, 0))[:3]
        rs.append(r); gs.append(g); bs.append(b)
    rs.sort(); gs.sort(); bs.sort()
    mid = len(rs) // 2
    return rs[mid], gs[mid], bs[mid]


class PillowMattingProvider:
    """Color-distance cutout against the estimated background color (C-backed, fast)."""
    name = "pillow"

    def cutout(self, image: Image.Image) -> Image.Image:
        rgb = image.convert("RGB")
        bg = _estimate_bg_color(rgb)
        bg_img = Image.new("RGB", rgb.size, bg)
        diff = ImageChops.difference(rgb, bg_img).convert("L")
        tol = settings.bg_tolerance
        mask = diff.point(lambda p: 255 if p > tol else 0).convert("L")
        # tidy up: drop speckles, then soften the edge a touch
        mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
        mask = mask.filter(ImageFilter.GaussianBlur(0.8))
        out = rgb.convert("RGBA")
        out.putalpha(mask)
        return out


class RembgMattingProvider:
    """Open-source neural matting. Requires `pip install rembg onnxruntime`."""
    name = "rembg"

    def __init__(self) -> None:
        from rembg import new_session, remove  # lazy import
        self._remove = remove
        self._session = new_session("u2net")

    def cutout(self, image: Image.Image) -> Image.Image:
        return self._remove(image.convert("RGBA"), session=self._session)


class ApiMattingProvider:
    """Delegate to a third-party HTTP API (remove.bg-style)."""
    name = "api"

    def cutout(self, image: Image.Image) -> Image.Image:
        import io
        import urllib.request
        if not settings.matting_api_url:
            raise RuntimeError("POD_MATTING_API_URL not configured")
        buf = io.BytesIO()
        image.convert("RGBA").save(buf, format="PNG")
        req = urllib.request.Request(
            settings.matting_api_url,
            data=buf.getvalue(),
            headers={
                "Content-Type": "image/png",
                "Authorization": f"Bearer {settings.matting_api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return Image.open(io.BytesIO(resp.read())).convert("RGBA")


class GptImageMattingProvider:
    """抠图走 OpenAI gpt-image-1（"image2"）的 edit + transparent background。"""
    name = "gptimage"

    def __init__(self) -> None:
        from .openai_image import OpenAIImageClient
        self._client = OpenAIImageClient()

    def cutout(self, image: Image.Image) -> Image.Image:
        return self._client.remove_background(image)


_PROVIDERS = {
    "pillow": PillowMattingProvider,
    "rembg": RembgMattingProvider,
    "api": ApiMattingProvider,
    "gptimage": GptImageMattingProvider,
}


def get_matting_provider():
    key = settings.matting_provider
    cls = _PROVIDERS.get(key)
    if cls is None:
        raise ValueError(f"Unknown matting provider: {key!r} (have {list(_PROVIDERS)})")
    return cls()


# ── 一键抠图:平背景走边缘洪水填充(硬边干净),复杂背景走 rembg,兜底 pillow ──────────
_REMBG_SESSION = None


def _uniform_bg_cutout(image: Image.Image, tol: int = 36, band: int = 70,
                       max_uniformity: float = 14.0) -> Image.Image | None:
    """纯色/极平背景(卡通图、干净棚拍)→ **边缘洪水填充抠图**:只抠掉与画面边缘相连的背景色,
    主体内部的同色区域(如皮卡丘白肚皮)保留 → 得到**实心、硬边、无半透明残留**的结果。

    背景不够平(实拍/场景,四角色差大)或几乎没背景 → 返回 None,交给神经网络。
    纯 numpy/scipy、无模型,快且确定;解决 u2net 在大块平涂区(如皮卡丘尾巴)给半透明 alpha 的问题。
    """
    import numpy as np
    from scipy import ndimage

    rgb_im = image.convert("RGB")
    rgb = np.asarray(rgb_im).astype(np.int16)
    h, w = rgb.shape[:2]
    s = max(4, min(h, w) // 40)
    corners = np.concatenate([rgb[:s, :s].reshape(-1, 3), rgb[:s, -s:].reshape(-1, 3),
                              rgb[-s:, :s].reshape(-1, 3), rgb[-s:, -s:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    if float(np.median(np.abs(corners - bg).sum(1))) > max_uniformity:
        return None                                       # 背景不平 → 神经网络
    dist = np.abs(rgb - bg).sum(2).astype(np.float32)     # 到背景色的距离(0~765)
    lbl, _n = ndimage.label(dist <= tol)                  # 接近背景色的像素块
    edge = set(int(x) for x in np.concatenate([lbl[0], lbl[-1], lbl[:, 0], lbl[:, -1]]))
    edge.discard(0)
    bg_region = np.isin(lbl, list(edge))                  # 只取"连到画面边缘"的背景(保内部同色)
    if bg_region.mean() < 0.03:
        return None                                       # 几乎没背景可去 → 交给神经网络
    near = ndimage.binary_dilation(bg_region, iterations=3)
    ramp = np.clip((dist - tol) / band, 0.0, 1.0)         # 边缘按距离平滑过渡
    alpha = np.where(near, ramp, 1.0) * 255.0             # 背景外缘渐隐;主体内部恒不透明
    alpha = ndimage.gaussian_filter(alpha, 0.6)           # 轻抗锯齿
    out = np.dstack([np.asarray(rgb_im), alpha.astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


# ── 智能抠图(AI 识别主体并扣出)──────────────────────────────────────────────
# prompt 工程刻意「通用、不写死具体品类」:不假设主体是衣服/陀螺/杯子,而是让模型自己判定
# 「画面主体/主商品」,连背景**带无关元素**(手、手指、手臂、模特/人台、支架、道具、其他物体、
# 阴影倒影)一起去掉,只留主体、干净边缘、透明底。可选 hint 用于消歧(如一图多物时指定
# 「被手指按住的那个」)或针对难图补充线索——避免为某类图硬编码逻辑而引入回归 bug。
_SUBJECT_PROMPT = (
    "Identify the single main foreground subject of this photo — the primary product or object "
    "the image is about — and isolate it cleanly. "
    "Completely remove the background and every element that is not part of that subject, "
    "including any hands, fingers, arms, mannequins, models, holders, stands, props, other "
    "people or objects, and any cast shadows or reflections. "
    "Keep the main subject whole and undistorted, preserving its exact shape, colors, textures "
    "and fine details with crisp, accurate edges — do not redraw, restyle, beautify, crop or add "
    "anything. Output only the extracted subject on a fully transparent background."
)


def build_subject_prompt(hint: str = "") -> str:
    """组装智能抠图的 prompt:通用主体识别词 +(可选)用户补充提示。

    hint 为空 → 纯通用词(模型自己挑最主要的主体);hint 非空 → 追加一句,用于一图多物消歧
    或难图补充线索(如「only keep the spinning toy pressed by the finger, remove the hand」)。
    用户中文也可,模型能理解;不在此对 hint 做品类判断/硬编码。"""
    base = _SUBJECT_PROMPT
    h = (hint or "").strip()
    if h:
        base += (" The user gives this additional instruction about which subject to keep and "
                 f"what to exclude — follow it: {h}.")
    return base


def ai_subject_cutout(image: Image.Image, hint: str = "") -> Image.Image:
    """智能抠图:用 gpt-image 识别主体并扣出(连手/道具/背景一起去掉),输出透明 PNG。

    注:gpt-image 是生成式模型,会**重绘**主体像素(非 100% 保真),换来的是本地算法做不到的
    『去掉手指/支架等遮挡与无关元素、自动判定主体』——与印花提取 AI 路径同款取舍(见 CLAUDE.md)。
    无 key 时 OpenAIImageClient() 会抛错,交由上层退点 + 502。"""
    from .openai_image import OpenAIImageClient
    out = OpenAIImageClient().remove_background(image, prompt=build_subject_prompt(hint))
    return out.convert("RGBA")


def cutout_best(image: Image.Image) -> Image.Image:
    """通用「一键抠图」,按背景复杂度分两路:
    ① 纯色/极平背景(卡通、干净棚拍)→ 边缘洪水填充(硬边、实心、无半透明残留,最适合简单图);
    ② 复杂/实拍背景 → rembg/u2net 神经分割(会话缓存);rembg 缺失/失败 → pillow 颜色距离兜底。
    与 get_matting_provider() 解耦(不受 POD_MATTING_PROVIDER 影响);任何分支异常都优雅降级,不崩。
    """
    try:
        flat = _uniform_bg_cutout(image)
        if flat is not None:
            return flat
    except Exception as exc:  # noqa: BLE001 — 平背景抠图异常不致命,转神经网络
        log.warning("平背景抠图失败,转神经网络: %s", exc)
    global _REMBG_SESSION
    try:
        from rembg import new_session, remove  # 惰性导入,离线启动不受拖累
        if _REMBG_SESSION is None:
            _REMBG_SESSION = new_session("u2net")
        return remove(image.convert("RGBA"), session=_REMBG_SESSION)
    except Exception as exc:  # noqa: BLE001 — rembg 缺包/缺模型/推理失败 → 兜底
        log.warning("rembg 一键抠图不可用,降级 pillow: %s", exc)
        return PillowMattingProvider().cutout(image)
