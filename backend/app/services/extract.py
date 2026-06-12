"""Print extraction: cut out the subject, then autocrop to its content bounds.

This mirrors the core "印花提取" feature: turn a photo of a product/design into a
clean, transparent, content-tight print file ready for placement.
"""
from __future__ import annotations

from PIL import Image

from ..ai.matting import get_matting_provider
from ..ai.upscale import get_upscale_provider
from ..config import settings


def extract_print(image: Image.Image, upscale: float = 1.0) -> Image.Image:
    """Return a transparent PNG of just the design, tightly cropped."""
    matting = get_matting_provider()
    cut = matting.cutout(image)            # RGBA, bg transparent

    # autocrop to the opaque content bounding box (+ padding)
    alpha = cut.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        pad = settings.autocrop_padding
        left = max(bbox[0] - pad, 0)
        top = max(bbox[1] - pad, 0)
        right = min(bbox[2] + pad, cut.width)
        bottom = min(bbox[3] + pad, cut.height)
        cut = cut.crop((left, top, right, bottom))

    if upscale and upscale > 1.0:
        cut = get_upscale_provider().upscale(cut, scale=upscale)

    return cut
