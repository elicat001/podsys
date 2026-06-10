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
        from rembg import remove, new_session  # lazy import
        self._remove = remove
        self._session = new_session("u2net")

    def cutout(self, image: Image.Image) -> Image.Image:
        return self._remove(image.convert("RGBA"), session=self._session)


class ApiMattingProvider:
    """Delegate to a third-party HTTP API (remove.bg-style)."""
    name = "api"

    def cutout(self, image: Image.Image) -> Image.Image:
        import io, urllib.request
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


# ── 一键抠图:优先 rembg(u2net),不可用再退 pillow ──────────────────────────
_REMBG_SESSION = None


def cutout_best(image: Image.Image) -> Image.Image:
    """通用「一键抠图」:**优先 rembg/u2net**(神经分割,边缘干净、无颜色残留),
    会话缓存复用;rembg 缺失/失败 → pillow 颜色距离兜底(离线可跑)。返回透明 RGBA。

    与 get_matting_provider() 解耦:抠图工具要"最好的效果",不受 POD_MATTING_PROVIDER
    (印花提取的离线默认 pillow)影响;但 rembg 不可用时仍优雅降级,不崩。
    """
    global _REMBG_SESSION
    try:
        from rembg import new_session, remove  # 惰性导入,离线启动不受拖累
        if _REMBG_SESSION is None:
            _REMBG_SESSION = new_session("u2net")
        return remove(image.convert("RGBA"), session=_REMBG_SESSION)
    except Exception as exc:  # noqa: BLE001 — rembg 缺包/缺模型/推理失败 → 兜底
        log.warning("rembg 一键抠图不可用,降级 pillow: %s", exc)
        return PillowMattingProvider().cutout(image)
