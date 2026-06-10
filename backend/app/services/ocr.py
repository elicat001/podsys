"""轻量 OCR:从**设计图**里识别文字(标语 / typography),给本地标题当主体。

引擎用 Tesseract(pytesseract 是薄封装,**无 Python 依赖冲突**——不像 RapidOCR 会拉 opencv-python
与本项目的 opencv-contrib-headless 打架)。系统需装 tesseract-ocr 二进制:
  - 生产(Ubuntu): apt install -y tesseract-ocr tesseract-ocr-eng
  - Windows 本地: winget install UB-Mannheim.TesseractOCR
缺二进制 / 缺包 / 关开关(POD_TITLE_OCR=false)时**静默返回 ''**(优雅降级,不影响出标题)。

边界(诚实说明):只对**清晰平面文字设计**有效;产品照(褶皱/透视/曲面)、花体装饰字读不准。
故用「置信度 ≥min_conf + 词长 ≥3 字母」过滤——读不准就当没有,绝不让噪声污染标题。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from functools import lru_cache

from PIL import Image, ImageOps

from ..config import settings

log = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z][A-Za-z'&-]{2,}")  # ≥3 字母的英文词(滤掉单字母/数字/符号噪声)
# 停用词:产品照/花体常把这类常见短词当噪声读出来(如误读出孤零零的 "AND"),会污染标题主体,过滤掉。
_STOP = frozenset((
    "the", "and", "for", "you", "your", "are", "with", "this", "that", "from", "out",
    "not", "but", "all", "any", "can", "has", "had", "was", "were", "our", "their",
))
# Windows 默认安装路径(PATH 里没有时兜底);Linux/Mac 走 PATH。
_WIN_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


@lru_cache(maxsize=1)
def _engine():
    """返回可用的 pytesseract 模块,或 None(包/二进制缺失)。结果缓存,避免每次探活。"""
    try:
        import pytesseract  # lazy:未装包也不拖累启动
    except Exception:  # noqa: BLE001
        return None
    try:
        if shutil.which("tesseract") is None:        # PATH 没有 → 试 Windows 默认路径
            for p in _WIN_PATHS:
                if os.path.isfile(p):
                    pytesseract.pytesseract.tesseract_cmd = p
                    break
        pytesseract.get_tesseract_version()           # 探活:二进制不存在会抛
        return pytesseract
    except Exception as exc:  # noqa: BLE001
        log.info("OCR 不可用(未装 tesseract 二进制?):%s", exc)
        return None


def _prep(img: Image.Image) -> Image.Image:
    """OCR 预处理:灰度 + 放大小图 + 自动对比度,显著提升清晰文字设计的识别率。"""
    g = ImageOps.grayscale(img.convert("RGB"))
    longest = max(g.size)
    if longest < 1000:                                # 小图放大,文字更利于识别
        s = 1000 / longest
        g = g.resize((int(g.width * s), int(g.height * s)), Image.LANCZOS)
    elif longest > 2200:                              # 太大缩一下省时
        g.thumbnail((2200, 2200))
    return ImageOps.autocontrast(g)


def extract_text(img: Image.Image | None, min_conf: int = 60, max_words: int = 8) -> str:
    """识别设计图里的清晰文字,返回去噪短语;无文字 / 读不准 / OCR 不可用 → ''。"""
    if not settings.title_ocr or img is None:
        return ""
    pt = _engine()
    if pt is None:
        return ""
    try:
        data = pt.image_to_data(_prep(img), config="--psm 6", output_type=pt.Output.DICT)
    except Exception as exc:  # noqa: BLE001
        log.info("OCR 失败:%s", exc)
        return ""
    words: list[str] = []
    for w, c in zip(data.get("text", []), data.get("conf", [])):
        try:
            conf = float(c)
        except (TypeError, ValueError):
            conf = -1.0
        w = (w or "").strip()
        if conf >= min_conf and _WORD.fullmatch(w) and w.lower() not in _STOP:
            words.append(w)
            if len(words) >= max_words:
                break
    return " ".join(words)
