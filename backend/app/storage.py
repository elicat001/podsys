"""Local file storage (MVP). Swap for S3/MinIO/OSS later behind the same helpers."""
from __future__ import annotations

import uuid
from pathlib import Path

from .config import settings


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def upload_path(job_id: str, ext: str = "png") -> Path:
    settings.ensure_dirs()
    return settings.uploads_dir / f"{job_id}.{ext}"


def output_path(job_id: str, name: str) -> Path:
    settings.ensure_dirs()
    d = settings.outputs_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def output_url(job_id: str, name: str) -> str:
    return f"/files/{job_id}/{name}"


def path_from_url(url: str) -> Path | None:
    """把对外 url(/files/{job_id}/{name})解析回磁盘路径;非本地 /files url → None。"""
    if not url or not url.startswith("/files/"):
        return None
    return settings.outputs_dir / url[len("/files/"):]


def url_from_path(path) -> str | None:
    """把 Asset.path 归一化为对外 url(/files/{job_id}/{name})。
    兼容两种历史存法:① 已是 /files/ url(原样返回);② outputs 下的磁盘绝对路径(转换)。
    都不是 → None。"""
    if not path:
        return None
    s = str(path)
    if s.startswith("/files/"):
        return s
    try:
        rel = Path(s).resolve().relative_to(settings.outputs_dir.resolve())
        return "/files/" + rel.as_posix()
    except Exception:
        return None
