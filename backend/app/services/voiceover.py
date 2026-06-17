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


def mux(video_bytes: bytes, audio_bytes: bytes) -> bytes:
    """ffmpeg 用配音替换视频音轨,并用 atempo 把配音**精确贴合视频时长**(快了放慢/慢了加快,保真不变调,
    夹在 0.8~1.5 倍内避免失真)。失败 → 原视频。"""
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
            # 时长微调:atempo=配音时长/视频时长 → 合成后配音≈视频时长(夹 0.8~1.5,差异<3% 不动)
            vdur, adur = _probe_dur(ff, vp), _probe_dur(ff, ap)
            af: list[str] = []
            if vdur > 0.5 and adur > 0.5:
                factor = max(0.8, min(1.5, adur / vdur))
                if abs(factor - 1.0) > 0.03:
                    af = ["-filter:a", f"atempo={factor:.3f}"]
            r = subprocess.run(
                [ff, "-y", "-i", vp, "-i", ap, "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", *af, "-c:a", "aac", "-b:a", "128k", "-loglevel", "error", op],
                capture_output=True)
            if r.returncode != 0 or not os.path.exists(op) or os.path.getsize(op) < 1024:
                log.warning("ffmpeg 叠加失败: %s", (r.stderr or b"")[:200])
                return video_bytes
            with open(op, "rb") as f:
                return f.read()
    except Exception as exc:  # noqa: BLE001
        log.warning("ffmpeg 叠加异常: %s", exc)
        return video_bytes


def add_voiceover(video_bytes: bytes, image: Image.Image, description: str,
                  language: str, seconds: int) -> tuple[bytes, str]:
    """编排:写稿 → 配音 → 叠回。任一步失败都原样返回视频。返回 (视频bytes, 旁白稿 | '')。"""
    if not settings.voiceover_enabled or not _VOICE.get(language):
        return video_bytes, ""
    script = write_script(image, description, language, seconds)
    if not script:
        return video_bytes, ""
    audio = synthesize(script, language)
    if not audio:
        return video_bytes, script
    return mux(video_bytes, audio), script
