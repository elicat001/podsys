"""图生视频「旁白配音」:看商品图写目标语言口播稿 → edge-tts 免费配音 → ffmpeg 叠回视频。

为什么需要:CogVideoX 的 `with_audio` 只产**音效**(CogSound V2A)、**不产任何语言的连贯人声**
(已实测:葡语/中文都出乱码)。所以"葡语带货视频"的语音要靠这条管线补。

**best-effort**:无 key / 不支持的语言 / 无网 / TTS / ffmpeg 任一失败,都返回**原视频**、绝不阻断作业。
重依赖(openai / edge_tts / imageio_ffmpeg)全部**惰性 import**,保持离线启动轻量(对齐项目习惯)。
"""
from __future__ import annotations

import base64
import io
import logging

from PIL import Image

from ..config import settings

log = logging.getLogger(__name__)

# 语言 → (edge-tts 嗓音, 语速估计, 语言名, 字数单位)。语速用于按视频时长估稿长度,让配音尽量贴合时长。
# 中文按「字/秒」,其余按「单词/秒」(实测 pt-BR ~2.6 词/s、zh ~4.6 字/s @rate+6%)。无对白不配音。
_VOICE: dict[str, tuple[str, float, str, str]] = {
    "葡萄牙语": ("pt-BR-FranciscaNeural", 2.6, "巴西葡萄牙语", "个单词"),
    "英语": ("en-US-AvaNeural", 2.6, "英语", "个单词"),
    "西班牙语": ("es-MX-DaliaNeural", 2.6, "拉美西班牙语", "个单词"),
    "中文": ("zh-CN-XiaoxiaoNeural", 4.6, "中文", "个字"),
}
_RATE = "+6%"   # edge-tts 语速微调(实测 +6% 节奏自然又贴时长)


def supported_language(language: str) -> bool:
    """该语言是否有配音嗓音(无对白/未知 → False)。"""
    return language in _VOICE


def voice_for(language: str) -> str | None:
    spec = _VOICE.get(language)
    return spec[0] if spec else None


def write_script(image: Image.Image, description: str, language: str, seconds: int) -> str:
    """网关视觉模型看商品图 → 目标语言口播旁白(贴合画面 + 时长)。无 key / 不支持 / 失败 → ''。"""
    spec = _VOICE.get(language)
    if not spec or not settings.openai_api_key:
        return ""
    _voice, cps, lang_name, unit = spec
    n = max(8, int(seconds * cps))
    try:
        from openai import OpenAI

        from ..ai.openai_image import _API_GATE  # 复用全局网关并发信号量
        im = image.convert("RGB"); im.thumbnail((768, 768))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        prompt = (
            f"你是 TikTok 跨境电商带货文案。看这张商品图,为一条 {seconds} 秒的电商短视频写一句**{lang_name}**口播旁白(voiceover)。\n"
            f"要求:约 {n} {unit}、口语化、有感染力、结尾带行动号召(葡语 compre agora / 英语 buy now / 中文 喜欢就下单);"
            f"紧扣画面里这件【具体商品】的卖点;可参考这段镜头脚本的氛围:'{(description or '')[:200]}'。\n"
            f"**只输出 {lang_name} 旁白正文(一句话)**,不要引号、不要解释、不要其它语言、不要分镜标记。"
        )
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None,
                        timeout=settings.openai_timeout)
        with _API_GATE:
            resp = client.chat.completions.create(
                model=settings.openai_text_model,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]}])
        return (resp.choices[0].message.content or "").strip().strip('"').strip()[:600]
    except Exception as exc:  # noqa: BLE001 — 写稿失败不阻断,上层据空串跳过配音
        log.warning("旁白写稿失败: %s", exc)
        return ""


def synthesize(text: str, language: str) -> bytes:
    """edge-tts 免费 TTS(微软 Edge 神经语音)→ mp3 bytes。无网 / 失败 → b''。"""
    spec = _VOICE.get(language)
    if not spec or not text.strip():
        return b""
    voice = spec[0]
    try:
        import asyncio
        import os
        import tempfile

        import edge_tts
        with tempfile.TemporaryDirectory() as d:
            mp = os.path.join(d, "vo.mp3")
            asyncio.run(edge_tts.Communicate(text, voice, rate=_RATE).save(mp))
            with open(mp, "rb") as f:
                data = f.read()
        return data if len(data) > 256 else b""   # 太小=没合成出东西
    except Exception as exc:  # noqa: BLE001
        log.warning("TTS 合成失败(%s): %s", voice, exc)
        return b""


def _probe_dur(ff: str, path: str) -> float:
    """ffmpeg 读时长(秒);读不到 → 0.0。"""
    import subprocess
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            try:
                hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
                return int(hh) * 3600 + int(mm) * 60 + float(ss)
            except Exception:  # noqa: BLE001
                return 0.0
    return 0.0


def _probe_size(ff: str, path: str) -> tuple[int, int]:
    """ffmpeg 读视频分辨率 (w,h);读不到 → (0,0)。"""
    import re
    import subprocess
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# 字幕字体候选(按语言)。中文需中日韩字体(生产装 fonts-noto-cjk;本地 Windows 用雅黑),拉丁文用 DejaVu/Arial。
# 都找不到 → 跳过字幕(不烧方框,优雅降级)。
_FONT_CANDS: dict[str, list[str]] = {
    "cjk": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc"],
    "latin": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"],
}


def _subtitle_png(text: str, language: str, vw: int) -> bytes:
    """字幕文字 → 与视频同宽的透明 PNG(自动换行、白字黑描边、半透明底条)。无对应字体/失败 → b''。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        cjk = language == "中文"
        size = max(26, vw // 22)
        font = None
        for p in _FONT_CANDS["cjk" if cjk else "latin"]:
            try:
                font = ImageFont.truetype(p, size); break
            except Exception:  # noqa: BLE001
                continue
        if font is None:
            return b""   # 无对应字体 → 不烧字幕(避免方框)
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        maxw = int(vw * 0.9)
        lines: list[str] = []
        cur = ""
        for u in (list(text) if cjk else text.split()):     # 中文按字、拉丁按词换行
            test = (cur + ("" if cjk else " ") + u) if cur else u
            if probe.textlength(test, font=font) > maxw and cur:
                lines.append(cur); cur = u
            else:
                cur = test
        if cur:
            lines.append(cur)
        lines = lines[:4]
        pad = size // 2
        lh = int(size * 1.32)
        h = lh * len(lines) + pad
        img = Image.new("RGBA", (vw, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, vw, h], fill=(0, 0, 0, 120))      # 半透明底条
        for i, ln in enumerate(lines):
            x = int((vw - probe.textlength(ln, font=font)) / 2); y = pad // 2 + i * lh
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                d.text((x + dx, y + dy), ln, font=font, fill=(0, 0, 0, 255))   # 黑描边
            d.text((x, y), ln, font=font, fill=(255, 255, 255, 255))           # 白字
        import io as _io
        b = _io.BytesIO(); img.save(b, "PNG")
        return b.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("字幕渲染失败: %s", exc)
        return b""


def _segments(text: str, total_dur: float, language: str) -> list[tuple[float, float, str]]:
    """把口播稿按标点拆成短语段,每段按字数比例分配时间窗(跟着语音逐段显示,非逐字)。段数封顶 8。
    返回 [(起秒, 止秒, 该段文字), ...]。"""
    import re
    cjk = language == "中文"
    max_chars = 16 if cjk else 50
    parts: list[str] = []
    for p in (s.strip() for s in re.split(r"(?<=[。！？!?,，、;；:：])", text) if s.strip()):
        if len(p) <= max_chars:
            parts.append(p)
        elif cjk:
            parts += [p[i:i + max_chars] for i in range(0, len(p), max_chars)]
        else:                                   # 拉丁:按词凑到 max_chars
            cur = ""
            for w in p.split():
                if cur and len(cur) + 1 + len(w) > max_chars:
                    parts.append(cur); cur = w
                else:
                    cur = (cur + " " + w).strip()
            if cur:
                parts.append(cur)
    if not parts:
        parts = [text]
    while len(parts) > 8:                        # 太多 → 相邻两两合并
        parts = [("" if cjk else " ").join(parts[i:i + 2]) for i in range(0, len(parts), 2)]
    lens = [max(1, len(p)) for p in parts]
    tot = sum(lens)
    segs: list[tuple[float, float, str]] = []
    t = 0.0
    for p, ln in zip(parts, lens, strict=True):
        dur = total_dur * ln / tot
        segs.append((round(t, 2), round(t + dur, 2), p))
        t += dur
    if segs:                                     # 末段延到结尾,避免尾巴闪没
        s, _e, txt = segs[-1]
        segs[-1] = (s, round(total_dur + 0.4, 2), txt)
    return segs


def mux(video_bytes: bytes, audio_bytes: bytes, subtitle_text: str = "", language: str = "") -> bytes:
    """ffmpeg 把配音叠回视频(atempo 贴合时长),可选把 subtitle_text 烧成字幕。失败 → 原视频。
    无字幕:`-c:v copy`(快);有字幕:overlay 字幕 PNG → 重编码视频(libx264,限 3 线程防吃满共享 CPU)。"""
    if not audio_bytes:
        return video_bytes
    try:
        import os
        import subprocess
        import tempfile

        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as d:
            vp = os.path.join(d, "v.mp4"); ap = os.path.join(d, "a.mp3"); op = os.path.join(d, "o.mp4")
            with open(vp, "wb") as f: f.write(video_bytes)
            with open(ap, "wb") as f: f.write(audio_bytes)
            # 时长微调:atempo=配音时长/视频时长(夹 0.8~1.5),合成后配音≈视频时长
            vdur, adur = _probe_dur(ff, vp), _probe_dur(ff, ap)
            factor = max(0.8, min(1.5, adur / vdur)) if vdur > 0.5 and adur > 0.5 else 1.0
            # 字幕:分段(短语)→ 按字数比例分配时间窗 → 每段一张 PNG → 跟着语音逐段 overlay(enable 时间窗)。
            # atempo 后配音实际时长≈adur/factor,字幕时间轴按它分配,确保和语音同步。渲染不出(无字体)→ 退回无字幕。
            segs: list[tuple[float, float, bytes]] = []
            if subtitle_text:
                vw, _vh = _probe_size(ff, vp)
                if vw > 0:
                    final_dur = (adur / factor) if (vdur > 0.5 and adur > 0.5) else (adur or vdur or 5.0)
                    for s, e, txt in _segments(subtitle_text, final_dur, language):
                        png = _subtitle_png(txt, language, vw)
                        if png:
                            segs.append((s, e, png))
            if segs:
                fc = f"[1:a]atempo={factor:.3f}[aud]"
                inputs = ["-i", vp, "-i", ap]
                prev = "0:v"
                for i, (s, e, png) in enumerate(segs):
                    with open(os.path.join(d, f"s{i}.png"), "wb") as f: f.write(png)
                    inputs += ["-i", os.path.join(d, f"s{i}.png")]
                    nxt = f"v{i}"
                    fc += (f";[{prev}][{i + 2}:v]overlay=(W-w)/2:H-h-(H/10):"
                           f"enable='between(t,{s:.2f},{e:.2f})'[{nxt}]")
                    prev = nxt
                cmd = [ff, "-y", *inputs, "-filter_complex", fc, "-map", f"[{prev}]", "-map", "[aud]",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                       "-threads", "3", "-c:a", "aac", "-b:a", "128k", "-loglevel", "error", op]
            else:
                af = ["-filter:a", f"atempo={factor:.3f}"] if abs(factor - 1.0) > 0.03 else []
                cmd = [ff, "-y", "-i", vp, "-i", ap, "-map", "0:v:0", "-map", "1:a:0",
                       "-c:v", "copy", *af, "-c:a", "aac", "-b:a", "128k", "-loglevel", "error", op]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not os.path.exists(op) or os.path.getsize(op) < 1024:
                log.warning("ffmpeg 叠加失败: %s", (r.stderr or b"")[:300])
                return video_bytes
            with open(op, "rb") as f:
                return f.read()
    except Exception as exc:  # noqa: BLE001
        log.warning("ffmpeg 叠加异常: %s", exc)
        return video_bytes


def add_voiceover(video_bytes: bytes, image: Image.Image, description: str,
                  language: str, seconds: int, subtitle: bool = False) -> tuple[bytes, str]:
    """编排:写稿 → 配音 →(可选烧字幕)→ 叠回。任一步失败都原样返回视频。返回 (视频bytes, 旁白稿 | '')。
    subtitle=True 时把口播稿同语言烧进画面;language 不支持(无人声)→ 无稿 → 既不配音也不出字幕。"""
    if not settings.voiceover_enabled or not _VOICE.get(language):
        return video_bytes, ""
    script = write_script(image, description, language, seconds)
    if not script:
        return video_bytes, ""
    audio = synthesize(script, language)
    if not audio:
        return video_bytes, script
    return mux(video_bytes, audio, script if subtitle else "", language), script
