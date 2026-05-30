"""Upscale providers. `pillow` = Lanczos (baseline); swap to Real-ESRGAN for quality."""
from __future__ import annotations
from PIL import Image
from ..config import settings


class PillowUpscaleProvider:
    name = "pillow"

    def upscale(self, image: Image.Image, scale: float = 2.0) -> Image.Image:
        w, h = image.size
        return image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


class RealEsrganUpscaleProvider:
    """Placeholder for Real-ESRGAN GPU upscaler (wire up when GPU available)."""
    name = "realesrgan"

    def upscale(self, image: Image.Image, scale: float = 2.0) -> Image.Image:
        raise NotImplementedError("Real-ESRGAN provider not wired yet — use 'pillow'")


_PROVIDERS = {
    "pillow": PillowUpscaleProvider,
    "realesrgan": RealEsrganUpscaleProvider,
}


def get_upscale_provider():
    cls = _PROVIDERS.get(settings.upscale_provider, PillowUpscaleProvider)
    return cls()
