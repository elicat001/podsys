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
