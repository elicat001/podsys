"""把多段视频拼接成一段 —— 双分镜 15s(= 5s 分镜 + 10s 分镜,异步并行生成后拼接)。

为什么要拼接:CogVideoX-3 单段只支持 5/10s,做 15s 双分镜只能拆两段生成再首尾相接。
- mp4(真 CogVideoX 产物):ffmpeg concat filter —— 先把各段统一缩放/补边到首段尺寸 + 统一帧率,
  再 concat 重编码。段间尺寸/帧率/编码可能不一致,concat **demuxer** 要求完全一致 → 改用 **filter** 重编码才稳。
  各段原音轨一律丢弃(双分镜默认无声;真人旁白在拼接后对整段统一叠加,见 _work_aivideo)。
- gif(本地兜底产物):纯 Pillow 解帧 → 顺序拼接 → 重新编码(离线确定性,不依赖 ffmpeg,便于单测)。

重依赖(imageio_ffmpeg)惰性 import。拼接失败 → 抛错(上层 _work_aivideo 据此判作业 error + 退点)。
"""
from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)


def concat_gif(segments: list[bytes]) -> bytes:
    """顺序拼接多个动画 GIF 为一个:逐帧解码 → 统一到首帧尺寸 → 重新编码(保留每帧时长)。"""
    from PIL import Image, ImageSequence
    frames: list[Image.Image] = []
    durations: list[int] = []
    size: tuple[int, int] | None = None
    for data in segments:
        im = Image.open(io.BytesIO(data))
        for fr in ImageSequence.Iterator(im):
            f = fr.convert("RGB")
            if size is None:
                size = f.size
            elif f.size != size:
                f = f.resize(size, Image.LANCZOS)
            frames.append(f.copy())
            durations.append(int(fr.info.get("duration", 80)))
    if not frames:
        raise RuntimeError("拼接失败:无可用帧")
    buf = io.BytesIO()
    head, *rest = frames
    head.save(buf, format="GIF", save_all=True, append_images=rest,
              duration=durations, loop=0, disposal=2)
    return buf.getvalue()


def _has_audio_stream(ff: str, path: str) -> bool:
    """ffmpeg 探测该文件是否含音轨。"""
    import subprocess
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "Audio:" in r.stderr


def concat_mp4(segments: list[bytes], keep_audio: bool = False) -> bytes:
    """ffmpeg 把多段 mp4 拼接为一段:各段缩放补边到首段尺寸 + 统一 30fps → concat 重编码。

    keep_audio=True(=选了「视频音效」,各段带 CogVideoX 原生音轨)→ concat 带 a=1 保留并拼接音轨;
    否则(默认无声 / 旁白模式——旁白在拼接后对整段统一叠加)→ a=0 丢音轨。
    稳妥:只有「要保留」且「每段都确有音轨」才走 a=1(缺音轨时 concat a=1 会失败)。
    """
    import os
    import subprocess
    import tempfile

    import imageio_ffmpeg

    from .voiceover import _probe_size  # 复用已有的 ffmpeg 读分辨率助手(同包,惰性可用)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as d:
        paths: list[str] = []
        for i, data in enumerate(segments):
            p = os.path.join(d, f"seg{i}.mp4")
            with open(p, "wb") as f:
                f.write(data)
            paths.append(p)
        w, h = _probe_size(ff, paths[0])
        if w <= 0 or h <= 0:
            w, h = 1080, 1920
        audio = keep_audio and all(_has_audio_stream(ff, p) for p in paths)
        op = os.path.join(d, "out.mp4")
        inputs: list[str] = []
        for p in paths:
            inputs += ["-i", p]
        n = len(paths)
        # 每段:等比缩放进 w×h + 居中补边 + 方形像素 + 统一 30fps,再 concat
        fc = ""
        for i in range(n):
            fc += (f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                   f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}];")
        if audio:   # 视频音效:保留各段原生音轨,一并 concat
            fc += "".join(f"[v{i}][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[outv][outa]"
            maps = ["-map", "[outv]", "-map", "[outa]", "-c:a", "aac", "-b:a", "128k"]
        else:       # 默认无声 / 旁白(旁白后叠):只拼视频
            fc += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
            maps = ["-map", "[outv]"]
        cmd = [ff, "-y", *inputs, "-filter_complex", fc, *maps,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-threads", "3", "-loglevel", "error", op]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0 or not os.path.exists(op) or os.path.getsize(op) < 1024:
            raise RuntimeError(f"ffmpeg 拼接失败: {(r.stderr or b'')[:300]!r}")
        with open(op, "rb") as f:
            return f.read()


def concat_videos(segments: list[bytes], ext: str = "mp4", keep_audio: bool = False) -> bytes:
    """按产物类型拼接多段视频。空段过滤;单段直接返回(无需拼接)。
    ext: 'gif'(Pillow 解帧拼接,离线) | 其它一律按 mp4(ffmpeg 重编码拼接)。
    keep_audio:仅 mp4 生效——选了「视频音效」时为 True,拼接保留各段原生音轨(gif 无音轨,忽略)。"""
    segs = [s for s in segments if s]
    if len(segs) <= 1:
        return segs[0] if segs else b""
    if (ext or "mp4").lower() == "gif":
        return concat_gif(segs)
    return concat_mp4(segs, keep_audio=keep_audio)
