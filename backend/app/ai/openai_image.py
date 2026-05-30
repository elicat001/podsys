"""OpenAI gpt-image-1 ("image2") client.

Powers four tasks through one model:
  - 抠图/去背景      -> images.edit + background="transparent"
  - 文生图           -> images.generate
  - 图生图/改图      -> images.edit (image + prompt)
  - 换装/换背景      -> images.edit (image [+ mask] + prompt)

gpt-image-1 always returns base64 PNG (no url option), so we decode b64_json.
NOTE: gpt-image-1 is NOT a super-resolution model — do not use it for 无损放大
of print files (it regenerates pixels and will distort the design). Keep a real
upscaler (Pillow/Real-ESRGAN) for production files. See README.
"""
from __future__ import annotations
import base64
import io
from PIL import Image
from ..config import settings

# gpt-image-1 accepts these sizes (plus "auto")
VALID_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _file_tuple(img: Image.Image, name: str = "image.png"):
    return (name, _png_bytes(img), "image/png")


class OpenAIImageClient:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("POD_OPENAI_API_KEY 未配置,无法调用 gpt-image")
        from openai import OpenAI  # lazy import
        self.model = settings.openai_image_model
        self.client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )

    def _decode(self, resp) -> Image.Image:
        b64 = resp.data[0].b64_json
        return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")

    @staticmethod
    def _size(size: str) -> str:
        return size if size in VALID_SIZES else "auto"

    # ---- 文生图 ----
    def generate(self, prompt: str, size: str = "1024x1024",
                 quality: str = "auto", background: str = "auto") -> Image.Image:
        resp = self.client.images.generate(
            model=self.model, prompt=prompt, n=1,
            size=self._size(size), quality=quality, background=background,
        )
        return self._decode(resp)

    # ---- 图生图 / 改图 / 换装换背景 ----
    def edit(self, image: Image.Image, prompt: str,
             mask: Image.Image | None = None, size: str = "auto",
             background: str = "auto") -> Image.Image:
        kwargs = dict(
            model=self.model,
            image=_file_tuple(image),
            prompt=prompt, n=1, size=self._size(size), background=background,
        )
        if mask is not None:
            kwargs["mask"] = _file_tuple(mask, "mask.png")
        resp = self.client.images.edit(**kwargs)
        return self._decode(resp)

    # ---- 抠图 / 去背景 ----
    def remove_background(self, image: Image.Image) -> Image.Image:
        return self.edit(
            image,
            prompt=("Remove the background completely. Keep only the main subject / "
                    "design intact with clean edges. Output a transparent background."),
            background="transparent",
        )
