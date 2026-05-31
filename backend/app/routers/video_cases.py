"""视频案例库(分类示例画廊)。

数据来自 `app/data_seed/video_cases.json` —— 一个**演示用**的本地视频案例种子。
真实落地请替换为真实视频/缩略图资源,并接入资源存储与权限。此处仅做画廊管线演示。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..models_db import User

router = APIRouter(prefix="/api/video-cases", tags=["video-cases"])

_SEED_PATH = Path(__file__).resolve().parent.parent / "data_seed" / "video_cases.json"


@lru_cache(maxsize=1)
def load_cases() -> list[dict]:
    """读取并缓存本地视频案例种子库。

    种子文件首条可能是 `_comment` 说明对象,这里过滤掉含 `_comment` 的项,
    只保留含 id 的真实案例条目。
    """
    with _SEED_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [e for e in raw if isinstance(e, dict) and "_comment" not in e and e.get("id")]


@router.get("")
def list_cases(category: str | None = None, user: User = Depends(current_user)) -> dict:
    """列出视频案例;可按 category 过滤。返回 {total, items}。"""
    cases = load_cases()
    if category:
        cases = [c for c in cases if c.get("category") == category]
    return {"total": len(cases), "items": cases}


@router.get("/categories")
def list_categories(user: User = Depends(current_user)) -> list[dict]:
    """返回去重分类 + 计数:[{category, count}]。"""
    counts: dict[str, int] = {}
    for c in load_cases():
        cat = c.get("category", "其他")
        counts[cat] = counts.get(cat, 0) + 1
    return [{"category": cat, "count": n} for cat, n in counts.items()]
