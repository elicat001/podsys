"""文件存储:本地磁盘 + 可选 MinIO/S3 对象存储镜像。

本地磁盘始终是「写缓存 / scratch」(108 处调用点照旧写盘,签名不变);当
`POD_STORAGE_BACKEND=s3` 时,作业收尾调 `mirror_job` 把产物镜像进对象存储(=存储 of record),
`/files` 读取时本地缺失自动 `fetch_to_local` 回源,删除时 `delete_object` 两边都删。
默认 `local` → 下面所有对象层函数都是 no-op,行为与纯本地完全一致(测试零变化)。
未来换阿里云 OSS / 腾讯 COS 只需它们的 S3 兼容端点,无需改业务层。
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)


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


# ── 对象存储(MinIO/S3)镜像层 ──────────────────────────────────────────────
# 设计:本地盘当写缓存,产物 key 与本地目录 1:1 = `outputs/{job_id}/{name}`(单桶,私有)。
# owner 隔离不靠桶,靠 ① MinIO 全 localhost 不对公网暴露 ② 文件只经 /files 端点出去。
# 默认 backend=local → 全部函数 no-op;backend=s3 才真正读写对象存储。失败一律「只 warning 不抛」
# (镜像失败不能毁掉作业;回源失败让 /files 转 404),保证存储层抖动不影响主流程。

# 真实 boto3 client 按凭据缓存复用(对齐 openai_image._SDK_CACHE 的习惯)。测试 monkeypatch
# 整个 `_make_s3_client` 返回内存假 client,因此 boto3 是**惰性 import**(仅 s3 模式真用时才装)。
_S3_CLIENT_CACHE: dict = {}


def _is_s3() -> bool:
    return (settings.storage_backend or "local").lower() == "s3"


def _make_s3_client():
    """构造(并缓存)S3 客户端。MinIO 必须 path-style 寻址 + s3v4 签名。"""
    key = (settings.s3_endpoint_url, settings.s3_access_key, settings.s3_region)
    client = _S3_CLIENT_CACHE.get(key)
    if client is None:
        import boto3  # 惰性 import:仅 s3 模式真正调用时才需要
        from botocore.config import Config
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            region_name=settings.s3_region or None,
            config=Config(signature_version="s3v4",
                          s3={"addressing_style": settings.s3_addressing or "path"}),
        )
        _S3_CLIENT_CACHE[key] = client
    return client


def object_key(job_id: str, name: str) -> str:
    return f"outputs/{job_id}/{name}"


def mirror_job(job_id: str) -> int:
    """把 outputs/{job_id}/ 下文件(跳过 .thumb_ 缩略图缓存)镜像进对象存储。

    返回上传的文件数;local 模式或目录不存在返回 0。失败只 warning 不抛(best-effort)。
    缩略图是派生缓存,不入桶(回源时本地按需重生)。
    """
    if not _is_s3():
        return 0
    try:
        client = _make_s3_client()
        bucket = settings.s3_bucket
        d = settings.outputs_dir / job_id
        n = 0
        if d.is_dir():
            for f in d.iterdir():
                if not f.is_file() or f.name.startswith(".thumb_"):
                    continue
                client.upload_file(str(f), bucket, object_key(job_id, f.name))
                n += 1
        # 可选:连输入图也镜像(默认关;开了用于未来全云端无状态 worker)
        if settings.s3_mirror_uploads:
            for f in settings.uploads_dir.glob(f"{job_id}*"):
                if f.is_file():
                    client.upload_file(str(f), bucket, f"uploads/{f.name}")
        return n
    except Exception as exc:  # noqa: BLE001 — 镜像失败不能毁掉作业,记日志即可
        log.warning("mirror_job(%s) 失败: %s", job_id, exc)
        return 0


def fetch_to_local(job_id: str, name: str) -> Path | None:
    """本地缺失时从对象存储原子下载到本地缓存,返回本地路径;local/对象不存在/失败返回 None。

    原子写:先下到 `{name}.tmp.<uuid>` 再 os.replace,避免两个并发回源写坏同一文件。
    """
    if not _is_s3():
        return None
    dest = settings.outputs_dir / job_id / name
    try:
        client = _make_s3_client()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / f"{name}.tmp.{uuid.uuid4().hex}"
        try:
            client.download_file(settings.s3_bucket, object_key(job_id, name), str(tmp))
            os.replace(tmp, dest)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return dest if dest.is_file() else None
    except Exception as exc:  # noqa: BLE001 — 对象不存在/网络抖动 → 让 /files 转 404
        log.warning("fetch_to_local(%s/%s) 失败: %s", job_id, name, exc)
        return None


def object_exists(job_id: str, name: str) -> bool:
    """对象存储里是否有该文件(retention 删本地前确认有副本);local 恒 False。"""
    if not _is_s3():
        return False
    try:
        _make_s3_client().head_object(Bucket=settings.s3_bucket, Key=object_key(job_id, name))
        return True
    except Exception:  # noqa: BLE001 — 404 或其它 → 视为无副本
        return False


def delete_object(job_id: str, name: str) -> None:
    """删除对象存储里的产物;local no-op。失败只 warning(删盘已成功,对象残留可由 retention 兜底)。"""
    if not _is_s3():
        return
    try:
        _make_s3_client().delete_object(Bucket=settings.s3_bucket, Key=object_key(job_id, name))
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_object(%s/%s) 失败: %s", job_id, name, exc)


def delete_object_for_path(path) -> None:
    """删盘点便捷封装:给一个 outputs 下的本地路径,推出 {job_id}/{name} 并删对象;
    非 outputs 下路径 / local 模式 → 静默跳过。"""
    if not _is_s3() or not path:
        return
    try:
        rel = Path(path).resolve().relative_to(settings.outputs_dir.resolve())
    except Exception:  # noqa: BLE001 — 不在 outputs 下,不处理
        return
    parts = rel.parts
    if len(parts) >= 2:
        delete_object(parts[0], "/".join(parts[1:]))
