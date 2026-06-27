"""Vidu 图生视频路由(第二套引擎,与 CogVideoX 的 /api/video 并存)。前缀 /api/vidu。

与 /api/video 的区别:Vidu viduq3 单次调用就出 15s 多镜头(无母帧链/无三段拼接),端点更薄、计费按【秒数×2】。
独立 router(而非塞进 video.py)→ 与他人正在维护的 video.py 物理隔离,零冲突。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi import File as FastFile
from PIL import Image
from sqlalchemy.orm import Session

from ..ai.vidu import ASPECT_RATIOS, DURATIONS, LANGUAGES, RESOLUTIONS
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import InsufficientCredits, charge, charge_for, cost_of, refund
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/vidu", tags=["vidu"])

# 商品类目(仅作入库标题/分组;Vidu 不依赖类目母帧,任意 SKU 通用)
CATEGORIES: list[str] = ["通用", "T恤", "卫衣", "马克杯", "水杯", "手机壳", "帆布袋", "海报", "抱枕", "毛毯"]


@router.get("/options")
def options(user: User = Depends(current_user)):
    """前端拉取可选项 + 计费规则。Vidu 单次出片,计费 = 秒数 × vidu(2 点/笔)。"""
    return {
        "aspects": list(ASPECT_RATIOS),
        "resolutions": RESOLUTIONS,
        "languages": LANGUAGES,
        "categories": CATEGORIES,
        "durations": DURATIONS,
        "model": settings.vidu_model,
        # 计费:秒数 × 每秒点数(vidu op 单价 2)。前端据此算「扣 N 点」。
        "price_per_second": cost_of("vidu"),
        # smart_ready=配了作图网关 key(可用「智能识别」看图写多镜头脚本);ai_ready=配了 Vidu key(否则兜底 GIF)。
        "smart_ready": bool(settings.openai_api_key),
        "ai_ready": settings.vidu_provider != "local" and bool(settings.vidu_api_key),
    }


@router.post("/smart-describe")
def smart_describe_endpoint(
    file: UploadFile = FastFile(...),
    seconds: int = Form(10),
    language: str = Form("葡萄牙语"),
    category: str = Form("通用"),
    selling_points: str = Form(""),
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """智能识别:看商品图 + 卖点 → 自动写一条【Vidu 多镜头分镜】脚本(填进视频描述)。同步,扣 title=1,失败退点。
    复用作图网关视觉模型(POD_OPENAI_API_KEY);无 key/失败 → 502 + 退点。"""
    img = read_image_or_refund(file.file.read(), db, user, "title")
    if seconds not in DURATIONS:
        seconds = 10
    try:
        from ..services.vidu_script import describe_multishot
        text = describe_multishot(img, seconds=seconds, language=language,
                                  category=category, selling_points=selling_points[:500])
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
    language: str = Form("葡萄牙语"),
    category: str = Form("通用"),
    aspect: str = Form("portrait"),
    resolution: str = Form("720p"),
    seconds: int = Form(5),
    native_sound: bool = Form(False),   # Vidu 自带音频(音效,非真人);默认关。与旁白互斥
    bgm: bool = Form(False),            # Vidu 背景音乐床
    voiceover: bool = Form(False),      # 真人 AI 旁白(无声生成 + edge-tts 叠回)
    subtitle: bool = Form(True),
    user: User = Depends(charge_for("vidu")),
    db: Session = Depends(get_db),
):
    """Vidu 图生视频(viduq3):1 张图=首帧锁定多镜头 / 2 张=多图参考主体一致。单次出片,无需拼接。
    异步,计费 = 秒数 × 2 点(5s=10/10s=20/15s=30,起步 10 点),失败退点。
    Provider 由 POD_VIDU_PROVIDER 决定:默认 local→兜底 GIF;设 vidu + 填 key→真 Vidu。"""
    img1 = file.file.read()
    read_image_or_refund(img1, db, user, "vidu")   # 第 1 张必填;坏图自动退 1 笔 + 400(下方多扣的还没发生)
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
    if category not in CATEGORIES:
        category = "通用"
    if seconds not in DURATIONS:
        seconds = 5
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
        params={"prompt": prompt[:2000], "language": language[:20], "category": category,
                "aspect": aspect, "resolution": resolution, "seconds": seconds, "n": n,
                "native_sound": bool(native_sound), "bgm": bool(bgm),
                "voiceover": bool(voiceover), "subtitle": bool(subtitle), "frames2": bool(img2)},
    )
