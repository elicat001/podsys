"""本地标题 OCR(services/ocr.py)测试。

不依赖系统 tesseract 二进制:用 monkeypatch 伪造引擎,验证**去噪过滤**(置信度/词长/停用词)与
**优雅降级**(关开关 / 无引擎 → '')。再验证 smart_title 会把 OCR 文字当主体。
"""
from __future__ import annotations

from PIL import Image

from app.config import settings
from app.services import effects, ocr


class _FakeOutput:
    DICT = "dict"


class _FakePytess:
    """伪造的 pytesseract:image_to_data 直接吐预设 dict。"""
    Output = _FakeOutput

    def __init__(self, data):
        self._data = data

    def image_to_data(self, img, config="", output_type=None):
        return self._data


def test_extract_text_filters_conf_len_stopwords(monkeypatch):
    data = {
        "text": ["BEST", "DAD", "x", "AND", "lowconf", "EVER", "123"],
        "conf": [96, 95, 90, 88, 30, 92, 99],
    }
    monkeypatch.setattr(settings, "title_ocr", True)
    monkeypatch.setattr(ocr, "_engine", lambda: _FakePytess(data))
    out = ocr.extract_text(Image.new("RGB", (20, 20), (255, 255, 255)))
    # 留 BEST/DAD/EVER;丢 x(<3字母)、AND(停用词)、lowconf(conf<60)、123(非字母)
    assert out == "BEST DAD EVER"


def test_extract_text_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "title_ocr", False)
    # 即便引擎"可用",关了开关也直接 ''
    monkeypatch.setattr(ocr, "_engine", lambda: _FakePytess({"text": ["HI"], "conf": [99]}))
    assert ocr.extract_text(Image.new("RGB", (20, 20))) == ""


def test_extract_text_no_engine_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "title_ocr", True)
    monkeypatch.setattr(ocr, "_engine", lambda: None)   # 包/二进制缺失
    assert ocr.extract_text(Image.new("RGB", (20, 20))) == ""


def test_extract_text_none_image():
    assert ocr.extract_text(None) == ""


def test_smart_title_leads_with_ocr_text():
    """OCR 文字应成为标题主体,并进入搜索词。"""
    r = effects.smart_title(None, keywords="father, gift", category="apparel",
                            ocr_text="Best Dad Ever")
    assert "Best Dad Ever" in r["title"]
    tags = [t.lower() for t in r["keywords"]]
    assert "best" in tags and "dad" in tags


def test_smart_title_without_ocr_unchanged():
    """无 OCR 文字时仍按关键词出主体(回归保护)。"""
    r = effects.smart_title(None, keywords="cat, funny", category="mug", ocr_text="")
    assert "Cat Funny" in r["title"]
