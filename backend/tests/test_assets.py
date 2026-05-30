"""素材库 + 侵权/查重风险评级。

风险逻辑(见 services/infringement.py):
  high   = 结构 dHash <=6  且 颜色 MAD <=18  (盗图/重复)
  review = 结构 <=12 且 颜色 <=45
  safe   = 其余

注意:check_image 比对的是整个 assets 表(不按 owner 过滤),且 fixture 共享同一
临时 DB。为避免跨用例串扰,各用例用「结构上彼此差异极大」的底图(不同 seed 的条纹),
再在其上叠加目标图形,从而保证只与本用例自身上传的图命中。
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

SIZE = (256, 256)


def _base(seed: int) -> Image.Image:
    """每个用例一张结构独特的底图(细棋盘,seed 决定相位/密度),保证跨用例 dHash 远离。"""
    img = Image.new("RGB", SIZE, (255, 255, 255))
    d = ImageDraw.Draw(img)
    w, h = SIZE
    step = 5 + (seed % 4)
    for y in range(0, h, step):
        for x in range(0, w, step):
            if ((x // step) + (y // step) + seed) % 2 == 0:
                d.rectangle([x, y, x + step, y + step], fill=(0, 0, 0))
    return img


def _to_png(img: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _circle_on(seed: int, fill, ring_bg=None) -> io.BytesIO:
    """在 seed 底图中央画一个圆;ring_bg 可改变圆内/外配色用于颜色差异用例。"""
    img = _base(seed)
    d = ImageDraw.Draw(img)
    w, h = SIZE
    r = min(w, h) // 4
    d.ellipse([w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r], fill=fill)
    return _to_png(img)


def _gradient() -> io.BytesIO:
    """对角彩色渐变 —— 结构(亮度梯度)与配色都与本测试其它图(圆/棋盘)迥异,
    用作 safe 用例,既不与库中任何图同构,配色也差异极大。"""
    g = Image.new("RGB", SIZE)
    d = ImageDraw.Draw(g)
    for x in range(SIZE[0]):
        d.line([(x, 0), (x, SIZE[1])], fill=(x % 256, (x * 2) % 256, (255 - x) % 256))
    return _to_png(g)


def _upload(client, headers, buf, name="a.png"):
    return client.post(
        "/api/assets",
        headers=headers,
        files={"file": (name, buf, "image/png")},
    )


def test_exact_duplicate_is_high(client, auth_headers):
    img = _circle_on(seed=11, fill=(20, 20, 20)).read()
    r1 = _upload(client, auth_headers, io.BytesIO(img))
    assert r1.status_code == 200, r1.text
    r2 = _upload(client, auth_headers, io.BytesIO(img))  # 同一张重传必命中自己
    assert r2.status_code == 200, r2.text
    assert r2.json()["risk"] == "high"


def test_same_shape_different_color_not_high(client, auth_headers):
    # 同 seed 底图 + 同位置圆,但整体配色差异极大(全反相)→ 结构近、颜色远 → 不应 high
    dark = _base(seed=22)
    light = Image.eval(dark, lambda p: 255 - p)  # 反相:结构相同,颜色完全相反
    r1 = _upload(client, auth_headers, _to_png(dark))
    assert r1.status_code == 200, r1.text
    r2 = _upload(client, auth_headers, _to_png(light))
    assert r2.status_code == 200, r2.text
    assert r2.json()["risk"] != "high"


def test_completely_different_structure_is_safe(client, auth_headers):
    # 一张棋盘底图 + 一张彩色渐变图 —— 结构与配色都迥异 → safe
    # 渐变图在结构(亮度梯度)与绝对配色上都与库中任何圆/棋盘相距极远。
    r1 = _upload(client, auth_headers, _to_png(_base(seed=33)))
    assert r1.status_code == 200, r1.text
    r2 = _upload(client, auth_headers, _gradient())
    assert r2.status_code == 200, r2.text
    assert r2.json()["risk"] == "safe"


def test_assets_require_auth(client):
    r = client.post(
        "/api/assets",
        files={"file": ("a.png", _circle_on(seed=1, fill=(1, 2, 3)), "image/png")},
    )
    assert r.status_code == 401
