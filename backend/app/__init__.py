"""PODStudio backend package."""
__version__ = "0.5.0"

# P1-1:全局限制单图最大解码像素,防 decompression bomb(上传/解码侧)。
from PIL import Image as _PILImage
_PILImage.MAX_IMAGE_PIXELS = 64_000_000  # 6400 万像素上限,超出 Pillow 抛 DecompressionBombError
