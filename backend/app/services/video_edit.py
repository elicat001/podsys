"""后期编排层(P0 节奏核)—— 治"呆板/静态图加运镜",在不动一致性的前提下加"短视频节奏感"。

做什么:把一段成片按 beat 切成小段,**交替 全景 / 推近(punch-in)**,再拼回。
为什么安全(满足约束):
  - 只对【已生成的像素】重新构图(中心裁切→放回原尺寸),**不重新生成、不碰商品/印花内容** → 商品一致性零风险;
  - **总时长不变、音轨原样保留并对齐**(每段时长不变 → 拼回总长不变 → 旁白/音效不串);
  - 纯构图变化,**无新动作、无物理变化** → 不引入物理错误。
不含什么(需素材/更复杂,后续单独做):音乐床、卡点检测、动态文字(kinetic typography)。

opt-in:仅当 settings.video_punchup=True 才在 _work_aivideo 里启用;默认关 → 现有行为完全不变。
重依赖(imageio_ffmpeg)惰性 import;任何失败 → 返回原片,绝不阻断视频作业。
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 背景音乐目录(放 CC0/可商用 bgm:mp3/m4a/ogg/wav)。文件不入 git(见 .gitignore),由运维投放到服务器。
# 推荐来源:Pixabay Music 的 CC0 区(免署名、可商用)。backend/assets/music/
_MUSIC_DIR = Path(__file__).resolve().parents[2] / "assets" / "music"
_MUSIC_EXT = (".mp3", ".m4a", ".ogg", ".wav", ".aac")


def beat_plan(duration: float, beat: float = 1.8) -> list[tuple[float, float, bool]]:
    """按 beat 切网格,返回 [(start, end, punch?), ...];奇数段推近、偶数段全景;
    末段不足 0.6s 则并入上一段(避免碎尾)。纯函数,可离线单测。"""
    if duration <= 0 or beat <= 0:
        return []
    segs: list[tuple[float, float, bool]] = []
    t, i = 0.0, 0
    while t < duration - 0.05:
        end = min(duration, t + beat)
        if 0 < duration - end < 0.6:   # 末段太短 → 并到本段
            end = duration
        segs.append((round(t, 2), round(end, 2), i % 2 == 1))
        t, i = end, i + 1
    return segs


def _probe(ff: str, path: str) -> tuple[float, int, int, bool]:
    import re
    import subprocess
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    dur = 0.0
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            try:
                hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
                dur = int(hh) * 3600 + int(mm) * 60 + float(ss)
            except Exception:  # noqa: BLE001
                pass
            break
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (720, 1280)
    return dur, w, h, ("Audio:" in r.stderr)


# 构图预设 (zoom 裁切比例, 锚点 ax, ay)。循环取用 → 每个 beat 一个不同景别/机位,
# 把"一段连续单镜"在后期切出【多镜头密度感】(治"信息频率太低=呆板")。
# 通用、纯几何重构图:不重新生成、不碰商品像素、不按场景/商品写死。
_FRAMINGS: list[tuple[float, float, float]] = [
    (1.00, 0.5, 0.50),   # 全景(原画)
    (0.72, 0.5, 0.40),   # 推近 · 偏上(脸/上身)
    (0.82, 0.35, 0.50),  # 中近 · 偏左
    (0.68, 0.5, 0.50),   # 近景 · 居中
    (0.82, 0.66, 0.50),  # 中近 · 偏右
    (0.64, 0.5, 0.36),   # 特写 · 偏上
]


def _framing_filter(i: int, w: int, h: int) -> str:
    """第 i 个 beat 的构图滤镜:按预设循环裁切到不同景别/机位,再放回原尺寸。"""
    z, ax, ay = _FRAMINGS[i % len(_FRAMINGS)]
    if z >= 0.999:
        return f"scale={w}:{h},setsar=1"
    # crop 到 z 比例,锚点 ax/ay 决定取景区域(0=左/上,0.5=中,1=右/下)
    return (f"crop=iw*{z}:ih*{z}:(iw-iw*{z})*{ax}:(ih-ih*{z})*{ay},"
            f"scale={w}:{h},setsar=1")


def punch_up(video_bytes: bytes, *, beat: float = 2.0) -> bytes:
    """节奏快切:按 beat 切段,每段按 _FRAMINGS 循环换一个景别/机位(裁切→放回原尺寸),拼回。
    把连续单镜切出"多镜头"密度感。总时长不变、音轨保留对齐、商品像素不变。失败/太短 → 原片。"""
    try:
        import os
        import subprocess
        import tempfile

        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as d:
            ip = os.path.join(d, "in.mp4"); op = os.path.join(d, "out.mp4")
            with open(ip, "wb") as f:
                f.write(video_bytes)
            dur, w, h, has_audio = _probe(ff, ip)
            plan = beat_plan(dur, beat)
            if len(plan) < 2:
                return video_bytes      # 太短,不值得切
            parts = []
            for i, (s, e, _punch) in enumerate(plan):
                parts.append(f"[0:v]trim={s}:{e},setpts=PTS-STARTPTS,{_framing_filter(i, w, h)}[v{i}]")
            fc = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(plan))) + \
                 f"concat=n={len(plan)}:v=1[outv]"
            maps = ["-map", "[outv]"]
            if has_audio:
                maps += ["-map", "0:a:0", "-c:a", "copy"]   # 原音轨直拷(总长不变 → 对齐)
            cmd = [ff, "-y", "-i", ip, "-filter_complex", fc, *maps,
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                   "-threads", "3", "-loglevel", "error", op]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not os.path.exists(op) or os.path.getsize(op) < 1024:
                log.warning("punch_up ffmpeg 失败,回退原片: %s", (r.stderr or b"")[:200])
                return video_bytes
            with open(op, "rb") as f:
                return f.read()
    except Exception as exc:  # noqa: BLE001 — 后期加工绝不阻断视频作业
        log.warning("punch_up 异常,回退原片: %s", exc)
        return video_bytes


def pick_music(music_dir: str | None = None) -> str | None:
    """从音乐目录随机挑一首 bgm。空目录/不存在 → None(则不加音乐)。"""
    import random
    d = Path(music_dir) if music_dir else _MUSIC_DIR
    if not d.is_dir():
        return None
    tracks = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in _MUSIC_EXT]
    return str(random.choice(tracks)) if tracks else None


def add_music_bed(video_bytes: bytes, music_path: str, *, under_gain_db: float = -18.0,
                  solo_gain_db: float = -4.0) -> bytes:
    """把 bgm 垫进视频:loop/裁到视频时长。
    视频已有音轨(旁白/音效)→ 音乐降到 under_gain_db 混在【下面】(旁白清晰、音乐做底);
    视频无音轨 → 音乐当主音轨(solo_gain_db)。视频流直拷不重编、总时长以视频为准。失败 → 原视频。"""
    try:
        import os
        import subprocess
        import tempfile

        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as d:
            ip = os.path.join(d, "in.mp4"); op = os.path.join(d, "out.mp4")
            with open(ip, "wb") as f:
                f.write(video_bytes)
            _dur_v, _w, _h, has_audio = _probe(ff, ip)
            if has_audio:   # 音乐垫在旁白/音效之下
                fc = (f"[1:a]volume={under_gain_db}dB[mus];"
                      f"[0:a][mus]amix=inputs=2:duration=first:dropout_transition=2[aout]")
            else:           # 无原音轨 → 音乐当主音轨
                fc = f"[1:a]volume={solo_gain_db}dB[aout]"
            cmd = [ff, "-y", "-i", ip, "-stream_loop", "-1", "-i", music_path,
                   "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                   "-loglevel", "error", op]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not os.path.exists(op) or os.path.getsize(op) < 1024:
                log.warning("add_music_bed ffmpeg 失败,回退原片: %s", (r.stderr or b"")[:200])
                return video_bytes
            with open(op, "rb") as f:
                return f.read()
    except Exception as exc:  # noqa: BLE001 — 加音乐绝不阻断视频作业
        log.warning("add_music_bed 异常,回退原片: %s", exc)
        return video_bytes
