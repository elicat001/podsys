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
