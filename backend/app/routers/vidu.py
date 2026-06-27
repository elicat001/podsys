"""Vidu 图生视频路由(第二套引擎,与 CogVideoX 的 /api/video 并存)。前缀 /api/vidu。

与 /api/video 的区别:Vidu viduq3 单次调用就出 16s 多镜头【带原生音画同步】(无母帧链/无三段拼接),
端点更薄、时长连续可选(5-16s)、计费按【秒数×2】。独立 router → 与他人维护的 video.py 物理隔离,零冲突。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi import File as FastFile
from PIL import Image
from sqlalchemy.orm import Session

from ..ai.vidu import (
    ASPECT_RATIOS,
    DURATION_MAX,
    DURATION_MIN,
    LANGUAGES,
    NATIVE_DIALOGUE_LANGS,
    RESOLUTIONS,
    SOUND_MODES,
    clamp_seconds,
)
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import InsufficientCredits, charge, charge_for, cost_of, refund
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/vidu", tags=["vidu"])


@router.get("/options")
def options(user: User = Depends(current_user)):
    """前端拉取可选项 + 计费规则。Vidu 单次出片,计费 = 秒数 × vidu(2 点/笔)。
    时长连续(min..max);声音支持原生音画同步(Q3)。商品类目【已下线】(走智能识别,不再写死)。"""
    return {
        "aspects": list(ASPECT_RATIOS),
        "resolutions": RESOLUTIONS,
        "languages": LANGUAGES,
        # 时长连续可选(前端用滑块):min..max,Q3 1-16s,POD 起步 5s。
        "duration": {"min": DURATION_MIN, "max": DURATION_MAX},
        # 声音模式(原生音画同步是 Q3 招牌能力):none/sfx/dialogue(中英对白口型)/voiceover(edge-tts 多语言)
        "sound_modes": SOUND_MODES,
        "native_dialogue_langs": NATIVE_DIALOGUE_LANGS,
        "model": settings.vidu_model,
        "price_per_second": cost_of("vidu"),   # 计费 = 秒数 × 单价(2)
        "smart_ready": bool(settings.openai_api_key),
        "ai_ready": settings.vidu_provider != "local" and bool(settings.vidu_api_key),
    }


@router.post("/smart-describe")
def smart_describe_endpoint(
    file: UploadFile = FastFile(...),
    seconds: int = Form(10),
    language: str = Form("葡萄牙语"),
    selling_points: str = Form(""),
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """智能识别:看商品图 + 卖点 → 自动写一条【Vidu 多镜头分镜】脚本(填进视频描述)。同步,扣 title=1,失败退点。
    复用作图网关视觉模型(POD_OPENAI_API_KEY);无 key/失败 → 502 + 退点。类目下线 → 由 AI 看图自适应,不写死动作。"""
    img = read_image_or_refund(file.file.read(), db, user, "title")
    seconds = clamp_seconds(seconds)
    try:
        from ..services.vidu_script import describe_multishot
        text = describe_multishot(img, seconds=seconds, language=language,
                                  selling_points=selling_points[:500])
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail="智能识别失败(作图 AI 服务未配置或调用失败)") from exc
    if not text:
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail="智能识别未返回内容,请重试")
    return {"description": text[:2000]}


@router.post("/ai-generate")
def ai_generate(
    file: UploadFile = FastFile(...),
    file2: UploadFile | None = FastFile(None),
    prompt: str = Form(""),
    language: str = Form("葡萄牙语"),    # 旁白(voiceover)语言 + 地区风格
    aspect: str = Form("portrait"),
    resolution: str = Form("720p"),
    seconds: int = Form(5),             # 连续 5-16s(后端 clamp)
    sound_mode: str = Form("none"),     # none / sfx / dialogue / voiceover(互斥)
    dialogue_lang: str = Form("英文"),   # 原生音画同步(dialogue)时的对白语言(中/英)
    subtitle: bool = Form(True),        # voiceover 模式的字幕
    user: User = Depends(charge_for("vidu")),
    db: Session = Depends(get_db),
):
    """Vidu 图生视频(viduq3):1 张图=首帧锁定多镜头 / 2+ 张=多图参考主体一致。单次出片含原生音画同步,无需拼接。
    异步,计费 = 秒数 × 2 点(连续时长 5-16s),失败退点。
    Provider 由 POD_VIDU_PROVIDER 决定:默认 local→兜底 GIF;设 vidu + 填 key→真 Vidu。"""
    img1 = file.file.read()
    read_image_or_refund(img1, db, user, "vidu")   # 第 1 张必填;坏图自动退 1 笔 + 400(下方多扣还没发生)
    img2 = None
    if file2 is not None:
        b = file2.file.read()
        try:
            Image.open(io.BytesIO(b)).verify()
            img2 = b
        except Exception:  # noqa: BLE001 — 第 2 张坏图忽略(降级单图,不阻断)
            img2 = None
    if aspect not in ASPECT_RATIOS:
        aspect = "portrait"
    if resolution not in RESOLUTIONS:
        resolution = "720p"
    if sound_mode not in SOUND_MODES:
        sound_mode = "none"
    if dialogue_lang not in NATIVE_DIALOGUE_LANGS:
        dialogue_lang = "英文"
    seconds = clamp_seconds(seconds)   # 夹到 5-16
    # 计费:秒数 × vidu(2 点/笔)。charge_for 已扣 1 笔;这里再补扣 seconds-1 笔 = 共 seconds 笔。
    # 任一笔余额不足 → 退回【已扣的全部】+ 402。n=退点笔数(=秒数),贯穿所有退点路径(submit_celery/worker/reaper)。
    n = int(seconds)
    charged = 1
    for _ in range(n - 1):
        try:
            charge(db, user, "vidu"); charged += 1
        except InsufficientCredits as exc:
            for _ in range(charged):
                refund(db, user, "vidu")
            raise HTTPException(status_code=402, detail=str(exc)) from exc
    return submit_celery(
        run_tool, db, user, kind="viduvideo", tool_id="viduvideo", op="vidu",
        raw=img1, mask_raw=img2, n=n,
        params={"prompt": prompt[:2000], "language": language[:20], "category": "通用",
                "aspect": aspect, "resolution": resolution, "seconds": seconds, "n": n,
                "sound_mode": sound_mode, "dialogue_lang": dialogue_lang,
                "subtitle": bool(subtitle), "frames2": bool(img2)},
    )
