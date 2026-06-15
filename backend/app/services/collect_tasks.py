"""采集任务服务层 — 采集→选择→同步。

纯 DB 逻辑,不依赖 FastAPI;路由层负责鉴权与 HTTP 语义。
- ingest:插件回传商品卡(图+标题/价格/评分/链接)→ 暂存(synced=False),只存元数据+URL,零存储。
- sync:对选中暂存项,服务端取 CDN 图 → 存为 Asset(source=collected)→ 此时存储才增长;并标侵权风险。
对每个 URL 用 collectors 的纯函数判定平台并升级到高清地址(与插件 content.js 规则一致)。
"""
from __future__ import annotations

import io
import re
import ssl
import urllib.error
import urllib.request
from functools import lru_cache
from urllib.parse import urlparse

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..models_collect import CollectedImage, CollectionTask
from ..models_db import Asset
from .collectors import detect_platform, upgrade_to_hires
from .library import save_as_asset

_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_FETCH_BYTES = 25 * 1024 * 1024  # 25MB 上限,防超大图打爆内存


def _origin(url: str) -> str:
    try:
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}/"
    except Exception:  # noqa: BLE001
        pass
    return ""


@lru_cache(maxsize=1)
def _verified_ctx() -> ssl.SSLContext:
    """带 CA 根证书的 SSL 上下文。优先用 certifi 的 CA bundle —— 本机(尤其 Windows)
    系统 CA 链常缺失,会报 CERTIFICATE_VERIFY_FAILED;certifi 自带一份完整 CA 解决之。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _read_url(req: urllib.request.Request, ctx: ssl.SSLContext) -> bytes:
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:  # noqa: S310
        return resp.read(_MAX_FETCH_BYTES + 1)


def _fetch_image(url: str, referer: str = "") -> Image.Image:
    """服务端取 CDN 图(带浏览器 UA + Referer 过基础防盗链)。
    抽成独立函数:测试里 monkeypatch 它即可离线、不触网。"""
    headers = {"User-Agent": _FETCH_UA, "Accept": "image/*,*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        data = _read_url(req, _verified_ctx())
    except urllib.error.URLError as e:
        # 本机 CA 链不全(Windows 常见)→ 取「公开商品图」非敏感数据,退一步用不校验上下文重试。
        reason = getattr(e, "reason", None)
        if not (isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY" in str(e)):
            raise
        data = _read_url(req, ssl._create_unverified_context())  # noqa: S323
    if len(data) > _MAX_FETCH_BYTES:
        raise ValueError("图片过大,超出上限")
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def image_to_dict(img: CollectedImage) -> dict:
    created = None
    try:
        if img.task and img.task.created_at:
            created = img.task.created_at.isoformat()
    except Exception:  # noqa: BLE001
        pass
    return {
        "id": img.id,
        "task_id": img.task_id,
        "url": img.url,
        "hires_url": img.hires_url,
        "platform": img.platform,
        "title": img.title,
        "price": img.price,
        "rating": img.rating,
        "source_url": img.source_url,
        "selected": img.selected,
        "synced": img.synced,
        "asset_url": img.asset_url,
        "created_at": created,
    }


# 「非商品图」特征(品牌商标 / 导航雪碧图 / UI 图标 / 占位图等):URL 里常见这些词,一律不采。
_JUNK_URL_RE = re.compile(
    r"sprite|nav[-_]?sprite|/logos?[-_/.]|[-_/]logo[-_/.]|favicon|/icons?[-_/.]|"
    r"[-_/]icon[-_/.]|/badges?[-_/.]|placeholder|loading|grey-?pixel|transparent[-_]?pixel|"
    r"/captcha|x-locale|prime[-_]?(?:logo|badge)|/g/01/|smile\.amazon",
    re.IGNORECASE,
)
# 「买家秀/评论图」:标题(来自 img alt)多为 "Customer Image, click to open customer review",
# 这类图常是占位/裁切图,采进来显示不出来(用户反馈的黑卡)。后端按标题兜底过滤(防旧版插件漏网)。
_JUNK_TITLE_RE = re.compile(
    r"customer image|customer review|click to open|review image|buyer show", re.IGNORECASE
)


def _looks_like_junk(url: str, platform: str, title: str = "") -> bool:
    """是否「非商品图」(品牌商标/导航雪碧图/UI 图标/买家秀评论图等)→ 采集时过滤。

    - 通用:URL 含 sprite/logo/icon/favicon/placeholder 等关键词;
    - 亚马逊:商品主图在 `/images/I/`,而 `/images/G/`、`/images/S/` 等是站点 UI/品牌/导航资源;
    - 标题/alt 命中「customer image / customer review」→ 评论区买家秀图,排除。
    """
    u = (url or "").lower()
    if not u:
        return True
    if _JUNK_URL_RE.search(u):
        return True
    if title and _JUNK_TITLE_RE.search(title):
        return True
    if platform == "amazon" and "/images/" in u and "/images/i/" not in u:
        return True
    return False


# ── 采集→选择→同步 ──────────────────────────────────────────

def ingest(
    db: Session,
    owner_id: int,
    items: list[dict],
    source: str = "plugin",
    platform_hint: str = "",
) -> CollectionTask:
    """插件回传商品卡 → 建任务 + 一批暂存采集图(synced=False,只存元数据+URL)。"""
    task = CollectionTask(
        id=storage.new_job_id(), owner_id=owner_id, source=source, status="collected", count=0
    )
    db.add(task)
    added = 0
    for it in items:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        plat = (it.get("platform") or platform_hint or detect_platform(url)) or "unknown"
        if _looks_like_junk(url, plat, it.get("title") or ""):
            continue  # 品牌商标/导航雪碧图/UI 图标/买家秀评论图 → 不采
        hires = (it.get("hires_url") or "").strip() or upgrade_to_hires(url, plat)
        db.add(
            CollectedImage(
                task_id=task.id, url=url[:1024], hires_url=hires[:1024], platform=plat[:32],
                title=(it.get("title") or "")[:255],
                price=(it.get("price") or "")[:32],
                rating=(it.get("rating") or "")[:16],
                source_url=(it.get("source_url") or "")[:1024],
            )
        )
        added += 1
    task.count = added
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def list_staging(db: Session, owner_id: int, platform: str | None = None) -> list[dict]:
    """本人未同步的暂存采集图(选择工作台数据源)。"""
    conds = [CollectionTask.owner_id == owner_id, CollectedImage.synced == False]  # noqa: E712
    if platform:
        conds.append(CollectedImage.platform == platform)
    rows = db.execute(
        # 按 id 升序 = 采集顺序(页面上的第一张/主图在前),卡片缩略图即主图,直观好认
        select(CollectedImage).join(CollectionTask).where(*conds).order_by(CollectedImage.id.asc())
    ).scalars().all()
    return [image_to_dict(i) for i in rows]


def delete_staging(db: Session, owner_id: int, image_ids: list[int]) -> int:
    """删除选中的暂存项(未同步)。返回删除条数。"""
    if not image_ids:
        return 0
    rows = db.execute(
        select(CollectedImage).join(CollectionTask).where(
            CollectionTask.owner_id == owner_id,
            CollectedImage.id.in_(image_ids),
            CollectedImage.synced == False,  # noqa: E712
        )
    ).scalars().all()
    n = 0
    for img in rows:
        db.delete(img)
        n += 1
    db.commit()
    return n


def filter_syncable(db: Session, owner_id: int, image_ids: list[int]) -> list[int]:
    """从一批 id 里筛出「本人 + 未同步」的(owner 隔离 + 去掉已同步/越权)。异步同步建任务前用,避免重复同步/越权。"""
    if not image_ids:
        return []
    rows = db.execute(
        select(CollectedImage.id).join(CollectionTask).where(
            CollectionTask.owner_id == owner_id,
            CollectedImage.id.in_(image_ids),
            CollectedImage.synced == False,  # noqa: E712
        )
    ).scalars().all()
    return list(rows)


def sync_images(db: Session, owner_id: int, image_ids: list[int], fetcher=None) -> dict:
    """对选中暂存项:服务端取图 → 存为 Asset(source=collected,标侵权风险)→ 回写 synced。
    每张独立 try:失败不影响其它(采集免费,不涉退点)。
    fetcher 默认在调用时解析为模块级 _fetch_image(便于测试 monkeypatch)。"""
    fetcher = fetcher or _fetch_image
    if not image_ids:
        return {"synced": 0, "failed": 0, "errors": []}
    rows = db.execute(
        select(CollectedImage).join(CollectionTask).where(
            CollectionTask.owner_id == owner_id,
            CollectedImage.id.in_(image_ids),
            CollectedImage.synced == False,  # noqa: E712
        )
    ).scalars().all()
    synced, failed, errors = 0, 0, []
    for img in rows:
        out_path = None
        try:
            src = img.hires_url or img.url
            referer = _origin(img.source_url) or _origin(src)
            pil = fetcher(src, referer)
            job_id = storage.new_job_id()
            out_path = storage.output_path(job_id, "asset.png")
            pil.convert("RGBA").save(out_path, format="PNG")
            size = out_path.stat().st_size
            # 侵权查重(同步取图后顺带做;关 OCR 保持批量同步够快)
            risk = "unknown"
            try:
                from .ip_guard import scan
                risk = scan(pil, img.title or None, use_ocr=False).get("risk", "unknown")
            except Exception:  # noqa: BLE001
                pass
            # path 存 /files/ URL:purge 能真删盘释放空间 + quota 仍能磁盘游走计入
            url = storage.output_url(job_id, "asset.png")
            asset = save_as_asset(
                db, owner_id, pil, (img.title or "采集图")[:255], url,
                source="collected", size_bytes=size,
            )
            if not asset:
                raise RuntimeError("入库失败")
            asset.batch = (img.platform or "")[:64]
            asset.risk = risk
            db.add(asset)
            img.synced = True
            img.synced_asset_id = asset.id
            img.asset_url = url
            db.add(img)
            db.commit()
            storage.mirror_job(job_id)  # 每张采集图独立 job_id,各自镜像进对象存储(local no-op)
            synced += 1
        except Exception as e:  # noqa: BLE001
            db.rollback()
            failed += 1
            if len(errors) < 5:
                errors.append(f"#{img.id}: {e}")
            if out_path is not None:
                try:
                    out_path.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
    return {"synced": synced, "failed": failed, "errors": errors}


def list_collected(db: Session, owner_id: int, platform: str | None = None) -> list[dict]:
    """找图库:本人已同步且 asset 未进回收站的采集图,按平台分组。"""
    conds = [
        CollectionTask.owner_id == owner_id,
        CollectedImage.synced == True,  # noqa: E712
        Asset.deleted == False,  # noqa: E712 — 进回收站的不在找图显示
    ]
    if platform:
        conds.append(CollectedImage.platform == platform)
    rows = db.execute(
        select(CollectedImage, Asset.risk)
        .join(CollectionTask)
        .join(Asset, Asset.id == CollectedImage.synced_asset_id)
        .where(*conds)
        .order_by(CollectedImage.id.desc())
    ).all()
    groups: dict[str, list[dict]] = {}
    for img, risk in rows:
        groups.setdefault(img.platform or "其它", []).append({
            "id": img.id,
            "asset_id": img.synced_asset_id,
            "asset_url": img.asset_url,
            "title": img.title,
            "price": img.price,
            "rating": img.rating,
            "source_url": img.source_url,
            "platform": img.platform,
            "risk": risk,
        })
    return [{"platform": p, "items": items} for p, items in groups.items()]


def delete_collected(db: Session, owner_id: int, image_id: int) -> bool:
    """从找图移除:把对应 asset 软删进回收站(可恢复/可永久删释放空间)。
    暂存记录保留(asset 恢复后能在找图重新出现,与回收站语义一致)。"""
    img = db.get(CollectedImage, image_id)
    if img is None or not img.synced_asset_id:
        return False
    task = db.get(CollectionTask, img.task_id)
    if task is None or task.owner_id != owner_id:
        return False
    asset = db.get(Asset, img.synced_asset_id)
    if asset is None or asset.owner_id != owner_id:
        return False
    asset.deleted = True
    db.add(asset)
    db.commit()
    return True
