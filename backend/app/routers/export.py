"""生产图导出 / 给工厂的最终格式转换路由。前缀 /api/export。

定位:**只做「把一张已经做好的设计稿/印花,按目标物理尺寸+DPI 排版成印厂可用文件」**。
不抠图、不放大、不调 AI——抠图/印花提取/放大在前面各自的工具里完成,本工具只负责
最后一步的格式转换与多格式输出(PNG 透明 / JPG 白底 / TIFF 无损 / PDF)。

计费:`charge_for("asset")`(1 点,纯本地确定性);读图失败退点 + 400。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import storage
from ..db import get_db
from ..models_db import User
from ..services import export
from ..services.billing import charge_for, refund
from ..web_utils import read_image_or_refund

router = APIRouter(prefix="/api/export", tags=["export"])

_BG = {"white": (255, 255, 255), "black": (0, 0, 0)}


def _parse_formats(formats: str) -> list[str]:
    """CSV → 有序去重的合法格式列表(过滤掉不支持的)。"""
    seen: list[str] = []
    for f in formats.split(","):
        f = f.strip().lower()
        if f == "jpeg":
            f = "jpg"
        if f in export.SUPPORTED_FORMATS and f not in seen:
            seen.append(f)
    return seen


@router.post("/production")
async def production(
    file: UploadFile = File(...),
    width_cm: float = Form(30.0),
    height_cm: float = Form(40.0),
    dpi: int = Form(300),
    formats: str = Form("png,jpg,tiff,pdf"),
    bg: str = Form("white"),
    bleed_mm: float = Form(0.0),
    safe_mm: float = Form(0.0),
    scale: str = Form("contain"),
    anchor: str = Form("center"),
    cmyk: bool = Form(False),
    proof: bool = Form(False),
    user: User = Depends(charge_for("asset")),
    db: Session = Depends(get_db),
):
    """把上传的设计稿导出为工厂级多格式生产文件。

    入参:设计稿(建议透明底 PNG)+ 尺寸/DPI/格式/底色 + 出血/安全边/排版模式/锚点/CMYK/打样图。
    返回:`{job_id, files:{fmt:url}, proof:url|null, meta}`。读图/参数非法 → 400(退点)。
    """
    src = read_image_or_refund(await file.read(), db, user, "asset")

    def _bad(detail: str):
        refund(db, user, "asset")
        return HTTPException(status_code=400, detail=detail)

    fmts = _parse_formats(formats)
    if not fmts:
        raise _bad("未指定有效导出格式(png/jpg/tiff/pdf)")
    if not (1.0 <= width_cm <= 100.0 and 1.0 <= height_cm <= 100.0):
        raise _bad("尺寸需在 1~100cm 之间")
    if not (72 <= dpi <= 600):
        raise _bad("DPI 需在 72~600 之间")
    if not (0.0 <= bleed_mm <= 20.0):
        raise _bad("出血需在 0~20mm 之间")
    if not (0.0 <= safe_mm <= 50.0):
        raise _bad("安全边需在 0~50mm 之间")
    if scale not in export.SCALE_MODES:
        raise _bad("排版模式仅支持 contain/cover/actual")
    if anchor not in export.ANCHORS:
        raise _bad("锚点仅支持 center/top/bottom")

    job_id = storage.new_job_id()
    out_dir = storage.output_path(job_id, "_").parent
    try:
        result = export.export_production_multi(
            src, out_dir, name_base="production",
            width_cm=width_cm, height_cm=height_cm, dpi=dpi,
            formats=tuple(fmts), bg=_BG.get(bg, (255, 255, 255)),
            bleed_mm=bleed_mm, safe_mm=safe_mm, scale=scale, anchor=anchor,
            cmyk=cmyk, proof=proof,
        )
    except ValueError as exc:  # 超 MAX_PX / 安全边过大等 → 400 + 退点
        raise _bad(str(exc)) from exc

    files = {fmt: storage.output_url(job_id, name) for fmt, name in result["files"].items()}
    proof_url = storage.output_url(job_id, result["proof"]) if result.get("proof") else None
    return {"job_id": job_id, "files": files, "proof": proof_url, "meta": result["meta"]}
