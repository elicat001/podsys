"""极简 PSD(Photoshop)编码器 —— 纯 struct,无第三方依赖。

Pillow 能**读** PSD 但不能**写**,而生产图要支持 PSD 导出,故自写一个:输出**单合并图(flat
composite)** 的 8-bit RGB/RGBA PSD,带分辨率(DPI)资源,PackBits(RLE)压缩。Photoshop /
GIMP / Affinity 等可直接打开;RGBA 时保留透明通道。

正确性由"写出后用 Pillow 再读回、尺寸/像素一致"的往返测试保证(tests/test_psd.py)——
Pillow 的 PsdImagePlugin 能读 PackBits 合并图,读回一致即说明字节结构合法。
"""
from __future__ import annotations

import struct

from PIL import Image

# PSD(非 PSB)单边像素上限。生产稿 30×40cm@300DPI = 3543×4724,远在限内。
_PSD_MAX_SIDE = 30000


def _packbits(data: bytes) -> bytes:
    """标准 PackBits(TIFF/PSD 同款)RLE,压缩一行字节。"""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        # 连续相同字节的游程(最多 128)
        j = i
        while j < n - 1 and data[j] == data[j + 1] and (j - i) < 127:
            j += 1
        run = j - i + 1
        if run >= 2:                       # 重复游程:负计数 + 被重复字节
            out.append(256 - (run - 1))
            out.append(data[i])
            i += run
        else:                              # 文字游程:遇到下一个游程或满 128 即止
            j = i
            while j < n and (j - i) < 128 and not (j < n - 1 and data[j] == data[j + 1]):
                j += 1
            lit = j - i
            out.append(lit - 1)
            out.extend(data[i:i + lit])
            i += lit
    return bytes(out)


def encode_psd(image: Image.Image, dpi: int = 300) -> bytes:
    """把 PIL 图编码为 flat PSD 字节。RGBA→带 alpha(4 通道,保留透明);否则 RGB(3 通道)。8-bit。"""
    img = image if image.mode in ("RGB", "RGBA") else image.convert("RGBA")
    has_alpha = img.mode == "RGBA"
    channels = 4 if has_alpha else 3
    w, h = img.size
    if w > _PSD_MAX_SIDE or h > _PSD_MAX_SIDE:
        raise ValueError(f"PSD 单边上限 {_PSD_MAX_SIDE}px,请调小尺寸/DPI")

    chan_bytes = [b.tobytes() for b in img.split()[:channels]]  # R,G,B[,A]

    out = bytearray()
    # ---- File Header(26B)----
    out += b"8BPS"
    out += struct.pack(">H", 1)            # version=1(PSD)
    out += b"\x00" * 6                      # reserved
    out += struct.pack(">H", channels)
    out += struct.pack(">I", h)
    out += struct.pack(">I", w)
    out += struct.pack(">H", 8)            # depth=8bit
    out += struct.pack(">H", 3)            # color mode=3(RGB);第 4 通道为 alpha

    # ---- Color Mode Data(空)----
    out += struct.pack(">I", 0)

    # ---- Image Resources:分辨率(DPI)资源 id=1005 ----
    fixed = (int(round(dpi)) & 0xFFFF) << 16   # 16.16 定点 PPI
    res = struct.pack(">I", fixed) + struct.pack(">HH", 1, 1) \
        + struct.pack(">I", fixed) + struct.pack(">HH", 1, 1)   # 16B ResolutionInfo
    block = b"8BIM" + struct.pack(">H", 1005) + b"\x00\x00" + struct.pack(">I", len(res)) + res
    out += struct.pack(">I", len(block)) + block

    # ---- Layer and Mask Info(空 = 仅合并图)----
    out += struct.pack(">I", 0)

    # ---- Image Data:RLE(PackBits)----
    out += struct.pack(">H", 1)            # compression=1(RLE)
    counts = bytearray()                   # 先放全部行字节数(逐通道逐行),再放压缩数据
    packed = bytearray()
    for cb in chan_bytes:
        for r in range(h):
            pr = _packbits(cb[r * w:(r + 1) * w])
            counts += struct.pack(">H", len(pr))
            packed += pr
    out += counts
    out += packed
    return bytes(out)
