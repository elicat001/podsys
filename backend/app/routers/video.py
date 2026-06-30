"""视频生成路由:商品展示动态视频(GIF)。前缀 /api/video。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..ai.video import ASPECT_RATIOS, CATEGORIES, DURATIONS, LANGUAGES, MULTI_SHOT_PLAN, RESOLUTION_SHORT
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services import video as video_svc
from ..services.billing import InsufficientCredits, charge, charge_for, refund
from ..services.library import save_as_asset
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/video", tags=["video"])


def _ai_fail_detail(prefix: str, exc: Exception) -> str:
    """把作图 AI 调用失败的真因翻成友好中文(不再泛化吞错)。常见:余额不足→提示充值 / 超时 / 无 key / key 无效。"""
    s = str(exc)
    if "insufficient_user_quota" in s or "额度不足" in s or "余额不足" in s:
        return f"{prefix}:作图 AI 账户余额不足,请充值后重试。"
    if "未配置" in s or "API_KEY" in s.upper():
        return f"{prefix}:作图 AI 未配置(缺 key)。"
    if "timed out" in s or "Timeout" in s or "APITimeout" in s:
        return f"{prefix}:作图 AI 网关超时,请稍后重试。"
    if "PermissionDenied" in s or " 401" in s or " 403" in s:
        return f"{prefix}:作图 AI key 无效或无权限。"
    return f"{prefix}:{s[:140]}"


@router.get("/options")
def options(user: User = Depends(current_user)):
    # ai_ready=true 表示已配好真 AI 视频(否则后端会兜底成本地 GIF)。前端据此提示用户。
    return {
        "aspects": list(ASPECT_RATIOS),
        "resolutions": list(RESOLUTION_SHORT),
        "languages": LANGUAGES,
        "categories": CATEGORIES,
        "durations": DURATIONS,
        # 多分镜 15s:N 段(MULTI_SHOT_PLAN,当前 3×5s)并行生成后拼接成动作链(单段模型只支持 5/10s)。
        # 计费 = video × len(plan);前端据 shots 渲染分镜数、算扣点。键名沿用 two_shot(前端契约)。
        "two_shot": {"plan": list(MULTI_SHOT_PLAN), "total": sum(MULTI_SHOT_PLAN),
                     "shots": len(MULTI_SHOT_PLAN)},
        # smart_ready=true 表示配了作图网关 key,可用「智能识别」(看图自动写脚本)。
        "smart_ready": bool(settings.openai_api_key),
        "ai_ready": settings.video_provider != "local" and bool(settings.video_api_key),
    }


@router.post("/smart-describe")
def smart_describe_endpoint(
    file: UploadFile = File(...),
    video_type: str = Form("开箱分享"),
    seconds: int = Form(10),
    language: str = Form("葡萄牙语"),
    category: str = Form("通用"),
    selling_points: str = Form(""),   # 卖家手填的产品卖点;AI 围绕它写脚本(选填)
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """智能识别:看商品图 + 产品卖点 → 自动生成贴合该商品的镜头脚本(填进「视频描述」)。同步,扣 title=1,失败退点。
    复用作图的网关视觉模型(POD_OPENAI_API_KEY);无 key/失败 → 502 + 退点。"""
    img = read_image_or_refund(file.file.read(), db, user, "title")
    if seconds not in DURATIONS:
        seconds = 10
    try:
        from ..services.video_describe import smart_describe
        text = smart_describe(img, video_type=video_type, seconds=seconds, language=language,
                              category=category, selling_points=selling_points[:500])
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail=_ai_fail_detail("智能识别失败", exc)) from exc
    if not text:
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail="智能识别未返回内容,请重试")
    return {"description": text[:2000]}


@router.post("/wizard/brief")
def wizard_brief(
    file: UploadFile = File(...),
    selling_points: str = Form(""),
    language: str = Form("葡萄牙语"),
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """智能向导 Step1:看商品图 → 结构化商品简报(产品名称/目标受众/核心卖点)。同步,扣 title=1,失败退点。"""
    img = read_image_or_refund(file.file.read(), db, user, "title")
    try:
        from ..services.video_wizard import describe_product
        brief = describe_product(img, selling_points=selling_points[:500], language=language)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail=_ai_fail_detail("商品信息识别失败", exc)) from exc
    return brief


@router.post("/wizard/proposals")
def wizard_proposals(
    name: str = Form(""),
    audience: str = Form(""),
    selling_points: str = Form(""),
    seconds: int = Form(10),
    language: str = Form("葡萄牙语"),
    category: str = Form("通用"),
    product_type: str = Form(""),        # Scene Profile(N3,Step1 传回):抽象品类
    interaction_risks: str = Form(""),   # Scene Profile:逗号分隔的连续性风险键(complex_state/object_identity/physical_contact)
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """智能向导 Step2:据商品简报 → 3 个不同方向的视频方案。同步,扣 title=1,失败退点。「换一批」=再调一次。
    15s(三分镜)时每个方案含 shot1/2/3 + scene1/2/3(动作链 per-shot 母帧)。
    profile(N3):据 Step1 的 Scene Profile 风险【按风险动态启用】连续性能力;留空 → 历史行为(全部能力、安全默认)。"""
    if seconds not in DURATIONS:
        seconds = 10
    profile = None
    if product_type or interaction_risks:
        import re as _re
        profile = {"product_type": product_type[:40],
                   "interaction_risks": [r for r in _re.split(r"[,,、\s]+", interaction_risks) if r]}
    try:
        from ..services.video_wizard import generate_proposals
        proposals = generate_proposals(name[:200], audience[:300], selling_points[:600],
                                       seconds=seconds, language=language, category=category, n=3, profile=profile)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail=_ai_fail_detail("方案生成失败", exc)) from exc
    return {"proposals": proposals}


@router.post("/wizard/expand")
def wizard_expand(
    seconds: int = Form(10),
    storyboard: str = Form(""),     # 5/10s 的精简脚本(扩展它)
    shot1: str = Form(""),          # 15s 三分镜的三拍(分别扩展)
    shot2: str = Form(""),
    shot3: str = Form(""),
    story: str = Form(""),          # 15s 故事主线(保连续性)
    name: str = Form(""),
    selling_points: str = Form(""),
    language: str = Form("葡萄牙语"),
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """详细扩展:把方案的【精简脚本】扩成【详细时间轴脚本】(保持原故事/动作/连续性,只写更细)。
    5/10s 扩 storyboard;15s 扩 shot1/2/3(+合成 storyboard)。同步,扣 title=1,失败退点。"""
    if seconds not in DURATIONS:
        seconds = 10
    try:
        from ..services.video_wizard import expand_proposal
        out = expand_proposal(seconds=seconds, storyboard=storyboard[:2000],
                              shot1=shot1[:1500], shot2=shot2[:1500], shot3=shot3[:1500],
                              story=story[:300], name=name[:200], selling_points=selling_points[:600],
                              language=language)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail=_ai_fail_detail("详细扩展失败", exc)) from exc
    return out


@router.post("/ai-generate")
def ai_generate(
    file: UploadFile = File(...),
    file2: UploadFile | None = File(None),
    prompt: str = Form(""),          # 视频描述/镜头脚本(由前端「视频类型」填入、可自定义编辑);多分镜时=分镜① 脚本
    prompt2: str = Form(""),         # 分镜② 脚本(仅 seconds=15 多分镜;留空则复用 prompt)
    prompt3: str = Form(""),         # 分镜③ 脚本(仅 seconds=15 三分镜;留空则复用 prompt)
    scene1: str = Form(""),          # 分镜①场景母帧描述(内容策划层):给了则每镜独立母帧(治同质化)
    scene2: str = Form(""),          # 分镜②场景母帧描述
    scene3: str = Form(""),          # 分镜③场景母帧描述;给齐 + 开场景首帧 → per-shot 母帧(动作链)
    language: str = Form("葡萄牙语"),  # 配音/对白语言(默认葡语)
    category: str = Form("通用"),     # 商品类目:母帧场景 + 入库标题用
    scene_frame: bool = Form(False),  # 两步:先 gpt-image 生成场景首帧再生视频(缓解硬切;无 key 自动跳过)
    aspect: str = Form("portrait"),
    resolution: str = Form("720p"),   # 默认 720p(快/稳;1080p/4k 仍可选)
    seconds: int = Form(10),          # 视频时长(秒):5 / 10 / 15(15=双分镜=5s+10s 两段,价格翻倍)
    native_sound: bool = Form(False), # 视频音效:用 CogVideoX 自带音频(with_audio=AI 音效,非真人);默认关。与旁白互斥
    voiceover: bool = Form(False),    # 旁白设置:无声生成 + 叠 AI 旁白(看图写目标语言口播稿);默认关
    subtitle: bool = Form(True),      # 字幕:旁白开时把口播稿按所选语言烧进画面
    user: User = Depends(charge_for("video")),
    db: Session = Depends(get_db),
):
    """图生视频(AI):1 张图=让它动起来 / 2 张图=首尾帧过渡。视频描述 + 商品标题 + 语言 + 画幅 + 分辨率。
    异步,扣 video=3,失败退点。Provider 由 POD_VIDEO_PROVIDER 决定:默认 local→兜底 GIF;
    设 cogvideox + 填 key→智谱 CogVideoX-3 真视频。画幅按比例等比贴合上传图(防生硬拉伸)。"""
    img1 = file.file.read()
    read_image_or_refund(img1, db, user, "video")   # 第 1 张必填;坏图自动退点 + 400
    img2 = None
    if file2 is not None:
        b = file2.file.read()
        try:
            Image.open(io.BytesIO(b)).verify()
            img2 = b
        except Exception:  # noqa: BLE001 — 第 2 张坏图忽略(降级为单图,不阻断)
            img2 = None
    if aspect not in ASPECT_RATIOS:
        aspect = "portrait"
    if resolution not in RESOLUTION_SHORT:
        resolution = "720p"
    if category not in CATEGORIES:
        category = "通用"
    if seconds not in DURATIONS:
        seconds = 10
    two_shot = seconds == 15          # 15s = 多分镜:拆 N 段(MULTI_SHOT_PLAN,当前 3×5s)并行生成后拼接
    use_scene = bool(scene_frame)     # 场景首帧(per-shot 或共享母帧);无 key 自动跳过
    # 计费:多分镜 = N 段算力,扣 video×N。charge_for 依赖已扣 1 笔;这里再补扣 N-1 笔 = 共 N 笔。
    # 任一笔余额不足 → 退回【已扣的全部】+ 402。n = 退点笔数(=N),贯穿所有退点路径:
    #   入队失败/配额超(submit_celery 的 n)、worker 失败(TOOL_WORKS n_field="n")、卡死回收(reap 读 params["n"])。
    n = 1
    if two_shot:
        n = len(MULTI_SHOT_PLAN)      # N(当前 3);改 MULTI_SHOT_PLAN 段数 → 计费自动跟随,不写死
        charged = 1                   # charge_for 依赖已扣的 1 笔
        for _ in range(n - 1):        # 再补扣 N-1 笔
            try:
                charge(db, user, "video"); charged += 1
            except InsufficientCredits as exc:
                for _ in range(charged):   # 余额不足 → 退回已扣的全部 N' 笔
                    refund(db, user, "video")
                raise HTTPException(status_code=402, detail=str(exc)) from exc
    return submit_celery(
        run_tool, db, user, kind="aivideo", tool_id="videogen", op="video",
        raw=img1, mask_raw=img2, n=n,
        params={"prompt": prompt[:2000], "prompt2": prompt2[:2000], "prompt3": prompt3[:2000],
                "scene1": scene1[:500], "scene2": scene2[:500], "scene3": scene3[:500],
                "two_shot": two_shot, "n": n, "language": language[:20],
                "category": category, "scene_frame": use_scene, "subtitle": bool(subtitle),
                "native_sound": bool(native_sound), "voiceover": bool(voiceover),
                "aspect": aspect, "resolution": resolution, "seconds": seconds, "frames2": bool(img2)},
    )


@router.post("/generate")
def generate(
    file: UploadFile = File(...),
    file2: UploadFile | None = File(None),
    style: str = Form("kenburns"),
    aspect: str = Form("square"),
    fps: int = Form(12),
    text: str = Form(""),
    user: User = Depends(charge_for("video")),
    db: Session = Depends(get_db),
):
    """从 1~2 张图生成商品展示 GIF(扣 video=3)。失败退点。"""
    images = [read_image_or_refund(file.file.read(), db, user, "video")]
    if file2 is not None:
        try:
            im2 = Image.open(io.BytesIO(file2.file.read())); im2.load()
            images.append(im2)
        except Exception:  # noqa: BLE001  第二张坏图忽略,不阻断
            pass
    try:
        result = video_svc.make_showcase(images, style=style, aspect=aspect, fps=fps, text=text)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "video")
        raise HTTPException(status_code=500, detail="视频生成失败") from exc

    job_id = storage.new_job_id()
    storage.output_path(job_id, "showcase.gif").write_bytes(result["bytes"])
    url = storage.output_url(job_id, "showcase.gif")
    # 入库 → 进我的空间/可删可恢复;镜像进对象存储(local no-op)。否则 GIF 既不计配额也成幽灵。
    save_as_asset(db, user.id, images[0], "商品展示视频", url, source="generated",
                  size_bytes=len(result["bytes"]))
    storage.mirror_job(job_id)
    return {
        "job_id": job_id,
        "video_url": url,
        "frames": result["frames"],
        "width": result["width"],
        "height": result["height"],
        "duration_ms": result["duration_ms"],
    }
