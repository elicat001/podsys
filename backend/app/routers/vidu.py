"""Vidu 图生视频路由(第二套引擎,与 CogVideoX 的 /api/video 并存)。前缀 /api/vidu。

本版定位:单张商品图 →[场景母帧] 真人在生活场景里使用/把玩商品 → viduq2-pro-fast 出片。
端点薄、时长连续 5-10s(q2-pro-fast 上限 10)、计费按【秒数×2】。独立 router → 与他人维护的 video.py 物理隔离。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi import File as FastFile
from sqlalchemy.orm import Session

from ..ai.vidu import (
    ASPECT_RATIOS,
    DURATION_MAX,
    DURATION_MIN,
    LANGUAGES,
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
    """前端可选项 + 计费规则。计费 = 秒数 × vidu(2 点/笔)。时长连续 5-10s;声音 = 无声/原生音效。"""
    return {
        "aspects": list(ASPECT_RATIOS),
        "resolutions": RESOLUTIONS,
        "languages": LANGUAGES,
        "duration": {"min": DURATION_MIN, "max": DURATION_MAX},
        "sound_modes": SOUND_MODES,           # none / sfx
        "model": settings.vidu_model,
        "price_per_second": cost_of("vidu"),
        # smart_ready=配了作图网关 key → 可用「智能识别」+「场景母帧」(都靠 gpt-image/视觉模型)
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
    """智能识别:看商品图 → 判断它最自然的把玩/使用方式 → 写一条【真人上手互动】脚本(填进视频描述)。
    同步,扣 title=1,失败退点。视觉自适应、不写死动作;无 key/失败 → 502 + 退点。"""
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
    prompt: str = Form(""),
    language: str = Form("葡萄牙语"),    # 地区/语言:影响场景母帧里的人 + 氛围
    aspect: str = Form("portrait"),
    resolution: str = Form("720p"),
    seconds: int = Form(5),             # 连续 5-10s(后端 clamp)
    sound_mode: str = Form("none"),     # none / sfx / voiceover
    subtitle: bool = Form(True),        # 真人旁白(voiceover)模式的字幕开关
    scene_frame: bool = Form(True),     # 场景母帧:gpt-image 把商品合成进"真人使用它"的场景做首帧(无 key 自动跳过)
    user: User = Depends(charge_for("vidu")),
    db: Session = Depends(get_db),
):
    """Vidu 图生视频(viduq2-pro-fast):单张商品图 →[场景母帧] 真人上手使用/把玩商品 → 单次出片。
    异步,计费 = 秒数 × 2 点(连续 5-10s),失败退点。Provider 由 POD_VIDU_PROVIDER 决定(默认 local→兜底 GIF)。"""
    img1 = file.file.read()
    read_image_or_refund(img1, db, user, "vidu")   # 坏图自动退 1 笔 + 400(下方多扣还没发生)
    if aspect not in ASPECT_RATIOS:
        aspect = "portrait"
    if resolution not in RESOLUTIONS:
        resolution = "720p"
    if sound_mode not in SOUND_MODES:
        sound_mode = "none"
    seconds = clamp_seconds(seconds)   # 夹到 5-10
    # 计费:秒数 × vidu(2 点/笔)。charge_for 已扣 1 笔;再补扣 seconds-1 笔 = 共 seconds 笔。
    # 任一笔余额不足 → 退回【已扣的全部】+ 402。n=退点笔数(=秒数),贯穿所有退点路径。
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
        raw=img1, n=n,
        params={"prompt": prompt[:2000], "language": language[:20], "category": "通用",
                "aspect": aspect, "resolution": resolution, "seconds": seconds, "n": n,
                "sound_mode": sound_mode, "subtitle": bool(subtitle), "scene_frame": bool(scene_frame)},
    )
