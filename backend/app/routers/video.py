"""视频生成路由:商品展示动态视频(GIF)。前缀 /api/video。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..ai.video import ASPECT_RATIOS, CATEGORIES, DURATIONS, LANGUAGES, MULTI_SHOT_PLAN, RESOLUTION_SHORT, TIERS
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
        # 三层出片体系(L5 商业系统视角):默认 L1=通用产品片(最稳)。前端据此渲染「出片模式」。
        "tiers": TIERS,
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
    tier: int = Form(3),              # 出片层级:1/2 → 单镜智能导向(产品向/结果向);3 → L3 故事/分镜
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """智能向导 Step2:据商品简报 → 3 个不同方向的视频方案。同步,扣 title=1,失败退点。「换一批」=再调一次。
    tier=1/2 时产出【单镜】方案(L1 产品向 / L2 结果向),不含分镜——把向导智能适配到单镜,不生硬照搬 L3。"""
    if seconds not in DURATIONS:
        seconds = 10
    if tier not in (1, 2, 3):
        tier = 3
    try:
        from ..services.video_wizard import generate_proposals
        proposals = generate_proposals(name[:200], audience[:300], selling_points[:600],
                                       seconds=seconds, language=language, category=category, n=3, tier=tier)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail=_ai_fail_detail("方案生成失败", exc)) from exc
    return {"proposals": proposals}


@router.post("/wizard/auto")
def wizard_auto(
    file: UploadFile = File(...),
    tier: int = Form(1),              # 1=产品向(L1)/ 2=结果向(L2)
    seconds: int = Form(10),
    language: str = Form("葡萄牙语"),
    category: str = Form("通用"),
    user: User = Depends(charge_for("title")),
    db: Session = Depends(get_db),
):
    """L1/L2 一键智能导向:看商品图 → 一句【为这件商品定制的单镜方案】(填进「视频描述」)。同步,扣 title=1,失败退点。
    走 chat 视觉接口(与坏掉的母帧 images.edit 无关);无 key/失败 → 502 + 退点。"""
    img = read_image_or_refund(file.file.read(), db, user, "title")
    if seconds not in DURATIONS:
        seconds = 10
    if tier not in (1, 2):
        tier = 1
    try:
        from ..services.video_wizard import auto_direction
        result = auto_direction(img, tier=tier, seconds=seconds, language=language)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail=_ai_fail_detail("智能导向失败", exc)) from exc
    if not result.get("description"):
        refund(db, user, "title")
        raise HTTPException(status_code=502, detail="智能导向未返回内容,请重试")
    return result   # {description, scene} — scene 仅 L2 有值,L1 为空


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
    category: str = Form("通用"),     # 商品类目:入库标题用;L2 母帧场景优先用 scene(向导产出),其次回退类目默认
    scene: str = Form(""),            # L2 单镜结果母帧场景(智能导向产出):看图定制、任意商品通用、不靠固定清单
    tier: int = Form(1),              # 出片模式(三层体系):1=通用产品片(默认最稳)/ 2=品类模板 / 3=Hero真人
    scene_frame: bool = Form(False),  # 两步:先 gpt-image 生成场景首帧再生视频(缓解硬切;无 key 自动跳过)
    aspect: str = Form("portrait"),
    resolution: str = Form("1080p"),
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
        resolution = "1080p"
    if category not in CATEGORIES:
        category = "通用"
    if seconds not in DURATIONS:
        seconds = 10
    # ── 三层出片体系 = 三个 A/B 变体(默认 L1 最稳):tier 决定提示词模板 / 是否母帧 / 是否多分镜 ──────────
    # 后端是权威:L1/L2 强制单镜、按 tier 决定母帧(忽略前端传的 scene_frame/15s),保证「默认即最稳」。
    #   L1 通用产品片(默认/变体A·商品前置):产品前置 + 无母帧 + 单镜 → 翻车面最小、成本最低(绕开母帧链)。
    #   L2 种草结果片(变体B·结果前置):单镜 + 结果母帧(把商品放进"想拥有的样子"、商品是清晰主角,失败降级回原图)。
    #   L3 Hero·真人(变体C·商品随附):人物行为 + 可三分镜(15s 动作链)+ 智能向导 → 上限最高、成功率最低。
    # ⚠ 哪个变体转化更高【没有业绩数据无法断言】→ 三层即 A/B 投放底座,让真实留存/转化裁决,不靠拍脑袋。
    if tier not in (1, 2, 3):
        tier = 1
    if tier == 3:
        template = "creative"           # → compose_prompt(人物行为路径)
        two_shot = seconds == 15        # 仅 Hero 才拆三分镜动作链(MULTI_SHOT_PLAN,当前 3×5s)
        use_scene = bool(scene_frame)   # 向导/手动按需(per-shot 或共享母帧)
    else:
        template = "result" if tier == 2 else "universal"   # L2→结果前置(种草) / L1→产品前置
        two_shot = False                # L1/L2 恒单镜(单镜=翻车面最小)
        if seconds == 15:
            seconds = 10                # 单镜模型最长 10s,选了 15s 自动落到 10s
        use_scene = tier == 2           # L2=结果母帧(卖"拥有后的样子");L1=无母帧(最稳,绕开 #1 翻车源)
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
                "scene": scene[:500],  # L2 单镜结果母帧场景(向导产出,看图定制任意商品;空→回退类目默认)
                "two_shot": two_shot, "n": n, "language": language[:20],
                "tier": tier, "template": template,
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
