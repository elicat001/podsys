"""Background-removal providers.

Default `pillow` provider needs no model and runs on CPU — good enough for
uniform-background product photos and lets the whole pipeline run + be verified.
Swap to `rembg` (open-source U2Net/BiRefNet) or `api` for production quality.
"""
from __future__ import annotations
from PIL import Image, ImageChops, ImageFilter
from ..config import settings


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
