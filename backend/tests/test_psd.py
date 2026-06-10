"""PSD 编码器(services/psd.py)单测:自写的 flat PSD 必须是合法 PSD。

验证手段:写出后用 Pillow 的 PsdImagePlugin **读回**,尺寸/模式/像素一致即说明字节结构合法
(Pillow 能读 PackBits 合并图);Photoshop 兼容性由此间接保证。纯本地、无 AI、无 key。
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.services.psd import _packbits, encode_psd


def _roundtrip(im: Image.Image) -> Image.Image:
    data = encode_psd(im, dpi=300)
    assert data[:4] == b"8BPS", "PSD 签名应为 8BPS"
    back = Image.open(io.BytesIO(data))
    back.load()
    return back


def test_packbits_roundtrip_matches_pil_on_rgba():
    """RGBA(含透明 + 大片纯色,考验 PackBits + alpha 通道)往返像素一致。"""
    im = Image.new("RGBA", (160, 110), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 140, 90], fill=(220, 40, 40, 255))
    d.ellipse([50, 30, 110, 80], fill=(30, 120, 200, 255))
    back = _roundtrip(im)
    assert back.size == im.size
    assert back.mode == "RGBA"
    assert list(back.convert("RGBA").getdata()) == list(im.getdata())


def test_rgb_roundtrip_matches():
    """无 alpha → 3 通道 RGB PSD,往返一致。"""
    im = Image.new("RGB", (80, 60), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([10, 10, 60, 50], fill=(10, 200, 90))
    back = _roundtrip(im)
    assert back.size == im.size
    assert list(back.convert("RGB").getdata()) == list(im.getdata())


def _unpackbits(packed: bytes) -> bytes:
    """标准 PackBits 解码,用于校验 _packbits 可逆。"""
    out = bytearray()
    i = 0
    while i < len(packed):
        n = packed[i]; i += 1
        if n < 128:                      # 文字:复制 n+1 字节
            out += packed[i:i + n + 1]; i += n + 1
        elif n > 128:                    # 游程:重复下一字节 257-n 次
            out += bytes([packed[i]]) * (257 - n); i += 1
        # n == 128 为 no-op
    return bytes(out)


def test_packbits_literal_and_run():
    """PackBits 压缩可逆:游程 + 文字 + 超 128 的长游程都能原样解回。"""
    raw = b"AAAAA" + bytes(range(20)) + b"\x07" * 130
    assert _unpackbits(_packbits(raw)) == raw


def test_psd_rejects_oversized():
    """超过 PSD 单边上限 → ValueError(防越界生成坏文件)。"""
    import app.services.psd as psd
    big = Image.new("RGB", (psd._PSD_MAX_SIDE + 1, 4), (0, 0, 0))
    try:
        encode_psd(big)
        assert False, "超大图应抛 ValueError"
    except ValueError:
        pass
