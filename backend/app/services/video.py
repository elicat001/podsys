"""商品展示视频生成 —— 离线 Pillow 原生动态 GIF(对标灵图 视频/TK视频)。

无需 ffmpeg/外部 AI:用运镜(Ken-Burns 缩放平移)、多图轮播、9:16 竖版 + 文案叠加
合成商品展示短片,导出为动画 GIF。真实 AI 视频(可灵/Sora 类)留作后续 provider。

设计为确定性、可单测:输出是合法 GIF 字节流,帧数/尺寸可断言。
"""
from __future__ import annotations
import io
from PIL import Image, ImageDraw, ImageFont

# 预设画幅
ASPECTS: dict[str, tuple[int, int]] = {
    "square": (800, 800),       # 1:1
    "portrait": (720, 1280),    # 9:16(TK 竖版)
    "landscape": (1280, 720),   # 16:9
}
MAX_FRAMES = 120  # 防滥用


def _fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """等比缩放并居中裁剪到目标画幅(cover)。"""
    tw, th = size
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    new = (max(1, int(iw * scale)), max(1, int(ih * scale)))
    r = img.convert("RGB").resize(new, Image.LANCZOS)
    left = (new[0] - tw) // 2
    top = (new[1] - th) // 2
    return r.crop((left, top, left + tw, top + th))


def ken_burns(img: Image.Image, size: tuple[int, int], frames: int = 24,
              zoom: float = 1.25) -> list[Image.Image]:
    """Ken-Burns 运镜:从 1.0 缩放到 zoom 的逐帧裁剪,产生缓慢推近。"""
    frames = max(2, min(frames, MAX_FRAMES))
    base = _fit_cover(img, (int(size[0] * zoom), int(size[1] * zoom)))
    bw, bh = base.size
    tw, th = size
    out: list[Image.Image] = []
    for i in range(frames):
        t = i / (frames - 1)
        # 当前可视区从全幅(z=zoom)收缩到 tw×th(z=1):线性插值裁剪框
        cw = int(bw - (bw - tw) * t)
        ch = int(bh - (bh - th) * t)
        x = (bw - cw) // 2
        y = (bh - ch) // 2
        frame = base.crop((x, y, x + cw, y + ch)).resize(size, Image.LANCZOS)
        out.append(frame)
    return out


def slideshow(images: list[Image.Image], size: tuple[int, int],
              hold: int = 8, fade: int = 6) -> list[Image.Image]:
    """多图轮播:每张定格 hold 帧,相邻间 fade 帧交叉淡入。"""
    covers = [_fit_cover(im, size) for im in images] or [Image.new("RGB", size, (240, 240, 240))]
    out: list[Image.Image] = []
    for idx, cur in enumerate(covers):
        for _ in range(max(1, hold)):
            out.append(cur.copy())
        nxt = covers[(idx + 1) % len(covers)]
        if len(covers) > 1 and fade > 0:
            for f in range(1, fade + 1):
                out.append(Image.blend(cur, nxt, f / (fade + 1)))
        if len(out) >= MAX_FRAMES:
            break
    return out[:MAX_FRAMES]


def _overlay_text(frames: list[Image.Image], text: str) -> None:
    """在每帧底部叠加文案(就地)。无字体文件时用默认位图字体。"""
    if not text:
        return
    try:
        font = ImageFont.truetype("arial.ttf", max(20, frames[0].width // 16))
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    for fr in frames:
        d = ImageDraw.Draw(fr)
        w, h = fr.size
        # 半透明底条
        bar_h = max(40, h // 8)
        band = Image.new("RGBA", (w, bar_h), (0, 0, 0, 140))
        fr.paste(Image.new("RGB", (w, bar_h), (0, 0, 0)), (0, h - bar_h),
                 band.split()[-1])
        d.text((w // 2, h - bar_h // 2), text, fill=(255, 255, 255), font=font, anchor="mm")


def frames_to_gif(frames: list[Image.Image], duration_ms: int = 80, loop: int = 0) -> bytes:
    buf = io.BytesIO()
    head, *rest = frames
    head.save(buf, format="GIF", save_all=True, append_images=rest,
              duration=duration_ms, loop=loop, disposal=2)
    return buf.getvalue()


def make_showcase(images: list[Image.Image], style: str = "kenburns",
                  aspect: str = "square", fps: int = 12, text: str = "") -> dict:
    """生成展示视频(GIF)。返回 {bytes, frames, width, height, duration_ms}。"""
    size = ASPECTS.get(aspect, ASPECTS["square"])
    if not images:  # 空输入兜底,使两种 style 契约一致(P1-1)
        images = [Image.new("RGB", size, (240, 240, 240))]
    if style == "slideshow":
        frames = slideshow(images, size)
    else:
        frames = ken_burns(images[0], size)
    _overlay_text(frames, text)
    duration_ms = max(20, int(1000 / max(1, fps)))
    data = frames_to_gif(frames, duration_ms=duration_ms)
    return {"bytes": data, "frames": len(frames), "width": size[0],
            "height": size[1], "duration_ms": duration_ms}
