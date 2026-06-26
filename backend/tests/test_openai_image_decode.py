"""gpt-image 响应解码加固(`OpenAIImageClient._decode`)。

历史 bug:中转网关对 edit/transparent 返 url 而非 b64_json,旧 `_decode` 直接
`base64.b64decode(None)` → 抛看不懂的 `TypeError: argument should be a bytes-like object...`
(一键抠图智能运行线上踩到)。加固后:b64 优先 → 退而取 url → 都没有则抛可定位错误。
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.ai.openai_image import OpenAIImageClient
from app.config import settings


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _Item:
    def __init__(self, b64: str | None = None, url: str | None = None):
        self.b64_json = b64
        self.url = url


class _Resp:
    def __init__(self, items):
        self.data = items


@pytest.fixture
def client(monkeypatch):
    # 构造客户端只需 key 非空(不触网);conftest 强制清空了 key,这里临时给个假 key。
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    return OpenAIImageClient()


def test_decode_b64_path(client):
    """有 b64_json → 正常解码(老路径不变)。"""
    img = client._decode(_Resp([_Item(b64=_png_b64())]))
    assert img.mode == "RGBA" and img.size == (8, 8)


def test_decode_falls_back_to_url(client, monkeypatch):
    """没 b64 但有 url → 走下载兜底(修复线上 TypeError 的核心)。"""
    fake = Image.new("RGBA", (5, 5), (9, 9, 9, 255))
    monkeypatch.setattr(OpenAIImageClient, "_fetch_image", staticmethod(lambda url: fake))
    out = client._decode(_Resp([_Item(b64=None, url="https://gw.example/x.png")]))
    assert out is fake


def test_decode_no_image_raises_clear_error_not_typeerror(client):
    """b64 与 url 都没有 → 抛可定位的 RuntimeError,而不是 base64.b64decode(None) 的 TypeError。"""
    with pytest.raises(RuntimeError) as ei:
        client._decode(_Resp([_Item(b64=None, url=None)]))
    assert "未返回图像数据" in str(ei.value)
    assert not isinstance(ei.value, TypeError)


def test_decode_empty_data_raises_clear_error(client):
    """data 为空(网关返回空响应)→ 同样给清晰错误,不 IndexError。"""
    with pytest.raises(RuntimeError):
        client._decode(_Resp([]))


def test_fetch_image_size_cap(monkeypatch):
    """下载兜底带大小上限:超大响应直接拒,防 OOM。"""
    import httpx

    import app.ai.openai_image as oi

    class _Big:
        content = b"x" * (oi._MAX_IMG_BYTES + 1)
        def raise_for_status(self): pass

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return _Big()

    monkeypatch.setattr(httpx, "Client", _C)  # _fetch_image 内 `import httpx` 拿到同一模块对象
    with pytest.raises(ValueError):
        OpenAIImageClient._fetch_image("https://gw.example/huge.png")
