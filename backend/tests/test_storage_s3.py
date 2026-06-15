"""对象存储(MinIO/S3)镜像层的离线测试。

全程用 `s3_backend` fixture 注入的内存假 client(见 tests/_fakes.py),**不连任何真实 MinIO/网络**。
默认 local 模式由 test_local_mode_noop 守住「零行为变化」。其余用例验证 backend=s3 时:
镜像收尾(同步/异步/无 key 各路径)、/files 本地缺失回源、删除同步、失败被吞。
"""
from __future__ import annotations

import os
import shutil
import time

from app import storage
from app.config import settings
from app.services.retention import run_retention


def _f(png):
    return {"file": ("a.png", png(), "image/png")}


def _jid_from_url(url: str) -> str:
    # /files/{job_id}/{name} → job_id
    return url.split("/")[2]


# ── local 默认:全部 no-op,守住基线 ──────────────────────────────────────
def test_local_mode_noop(png, tmp_path):
    """不声明 s3_backend → 默认 local:对象层函数都是 no-op,不抛、不动网络。"""
    assert settings.storage_backend == "local"
    assert storage.mirror_job("nope") == 0
    assert storage.fetch_to_local("nope", "x.png") is None
    assert storage.object_exists("nope", "x.png") is False
    storage.delete_object("nope", "x.png")          # 不抛
    storage.delete_object_for_path(tmp_path / "x")   # 不抛


# ── 单元:mirror 上传产物、跳过缩略图 ─────────────────────────────────────
def test_mirror_uploads_outputs_skips_thumb(s3_backend, png):
    jid = "unit_mirror"
    d = settings.outputs_dir / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / "print.png").write_bytes(png().read())
    (d / ".thumb_72_print.png").write_bytes(b"thumbcache")  # 派生缓存,不应入桶

    n = storage.mirror_job(jid)
    assert n == 1
    assert f"outputs/{jid}/print.png" in s3_backend.store
    assert f"outputs/{jid}/.thumb_72_print.png" not in s3_backend.store


def test_object_exists_and_delete(s3_backend, png):
    jid = "unit_exists"
    d = settings.outputs_dir / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.png").write_bytes(png().read())
    storage.mirror_job(jid)
    assert storage.object_exists(jid, "x.png") is True
    storage.delete_object(jid, "x.png")
    assert storage.object_exists(jid, "x.png") is False


# ── 收尾点覆盖:同步 /api/process 三件套进桶 + /files 删本地后回源 ──────────
def test_process_sync_finalizes_and_files_refetch(client, auth_headers, png, s3_backend):
    r = client.post("/api/process", headers=auth_headers, data={"template": "tshirt"}, files=_f(png))
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    for name in ("print.png", "mockup.png", "production.png"):
        assert f"outputs/{jid}/{name}" in s3_backend.store, f"{name} 应已镜像进对象存储"

    # 模拟 retention 清掉本地缓存:删整个作业目录
    shutil.rmtree(settings.outputs_dir / jid)
    # 直读 → 应从对象存储回源,仍 200,且写回本地
    resp = client.get(f"/files/{jid}/print.png", headers=auth_headers)
    assert resp.status_code == 200 and len(resp.content) > 0
    assert (settings.outputs_dir / jid / "print.png").is_file()

    # 缩略图分支:再删本地,带 ?w= 请求 → 先回源原图再生成 thumb,仍 200
    shutil.rmtree(settings.outputs_dir / jid)
    resp = client.get(f"/files/{jid}/print.png", params={"w": 72}, headers=auth_headers)
    assert resp.status_code == 200 and len(resp.content) > 0


def test_files_404_when_missing_everywhere(client, auth_headers, s3_backend):
    """本地无、对象存储也无 → 真 404(回源失败不掩盖)。"""
    resp = client.get("/files/ghostjob/print.png", headers=auth_headers)
    assert resp.status_code == 404


# ── 收尾点覆盖:异步 Celery 工具(eager)→ 产物进桶 ────────────────────────
def test_async_tool_finalizes(client, auth_headers, png, s3_backend, tool_result):
    r = client.post("/api/print-extract", headers=auth_headers, data={"engine": "fast"}, files=_f(png))
    assert "image_url" in tool_result(auth_headers, r)
    jid = r.json()["job_id"]
    assert any(k.startswith(f"outputs/{jid}/") for k in s3_backend.store), "异步工具产物应镜像进对象存储"


# ── 收尾点覆盖:无 key 同步分支(/api/generate)→ 产物进桶 ──────────────────
def test_generate_nokey_finalizes(client, auth_headers, s3_backend):
    r = client.post("/api/generate", headers=auth_headers, data={"prompt": "galaxy cat", "size": "512x512"})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    assert f"outputs/{jid}/generated.png" in s3_backend.store


# ── 收尾点覆盖:素材上传(/api/assets)+ 删除同步删对象 ───────────────────
def test_asset_upload_finalizes_and_purge_deletes_object(client, auth_headers, png, s3_backend):
    r = client.post("/api/assets", headers=auth_headers, files=_f(png))
    assert r.status_code == 200, r.text
    body = r.json()
    jid = _jid_from_url(body["url"])
    key = f"outputs/{jid}/asset.png"
    assert key in s3_backend.store

    # 永久删除 → 应同步删掉对象存储里的副本
    resp = client.delete(f"/api/space/assets/{body['asset_id']}/purge", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert key not in s3_backend.store


# ── 健壮性:镜像失败只 warning 不抛,作业仍能完成 ─────────────────────────
def test_mirror_failure_swallowed(s3_backend, png):
    jid = "fail_mirror"
    d = settings.outputs_dir / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.png").write_bytes(png().read())

    def boom(*a, **k):
        raise RuntimeError("network down")

    s3_backend.upload_file = boom
    assert storage.mirror_job(jid) == 0  # 被吞,返回 0,不抛


# ── 阶段三:s3 模式配额按 DB(Asset)计,删本地缓存不影响用量(retention 安全)─────────
def test_quota_db_truth_in_s3_mode(client, auth_headers, png, s3_backend):
    from app.services.quota import quota_bytes_limit
    r = client.post("/api/assets", headers=auth_headers, files=_f(png))
    assert r.status_code == 200, r.text
    jid = _jid_from_url(r.json()["url"])

    q1 = client.get("/api/space/quota", headers=auth_headers).json()
    assert q1["quota_bytes"] == quota_bytes_limit()      # 默认 1 GiB
    assert q1["used_bytes"] > 0

    # 模拟 retention 删掉本地缓存文件(MinIO 仍有副本)
    (settings.outputs_dir / jid / "asset.png").unlink()
    q2 = client.get("/api/space/quota", headers=auth_headers).json()
    assert q2["used_bytes"] == q1["used_bytes"], "s3 模式用量按 DB 计,删本地缓存不应让配额失真"


# ── 阶段三:retention 只删「超龄 + MinIO 有副本」的本地文件,无副本/新文件都保留 ─────
def test_retention_deletes_only_mirrored_and_aged(s3_backend, png, monkeypatch):
    monkeypatch.setattr(settings, "s3_retention_days", 1)
    old = time.time() - 2 * 86400  # 2 天前(超过 1 天阈值)

    # A:超龄 + 已镜像 → 应删本地、保留 MinIO
    ja = "ret_mirrored"
    da = settings.outputs_dir / ja; da.mkdir(parents=True, exist_ok=True)
    (da / "print.png").write_bytes(png().read())
    assert storage.mirror_job(ja) == 1
    os.utime(da / "print.png", (old, old))

    # B:超龄 + 未镜像 → 绝不删(防丢数据)
    jb = "ret_nocopy"
    db_ = settings.outputs_dir / jb; db_.mkdir(parents=True, exist_ok=True)
    (db_ / "print.png").write_bytes(png().read())
    os.utime(db_ / "print.png", (old, old))

    # C:已镜像但新鲜 → 保留
    jc = "ret_fresh"
    dc = settings.outputs_dir / jc; dc.mkdir(parents=True, exist_ok=True)
    (dc / "print.png").write_bytes(png().read())
    storage.mirror_job(jc)

    run_retention()

    assert not (da / "print.png").exists(), "超龄+有副本 应删本地"
    assert storage.object_exists(ja, "print.png"), "retention 只删本地,MinIO 副本须保留"
    assert (db_ / "print.png").exists(), "无副本 绝不删"
    assert (dc / "print.png").exists(), "新鲜文件 应保留"

    # 删后仍可经 fetch_to_local 从 MinIO 回源
    assert storage.fetch_to_local(ja, "print.png") is not None
    assert (da / "print.png").exists()


def test_retention_skips_when_local_or_disabled(png):
    # 默认 local 模式:retention 直接跳过(绝不碰本地真相源)
    assert run_retention().get("skipped") is True


# ── 一次性补传:把存量本地文件全镜像进对象存储 ──────────────────────────────
def test_mirror_all_backfills_existing(s3_backend, png):
    for jid in ("backfill_a", "backfill_b"):
        d = settings.outputs_dir / jid
        d.mkdir(parents=True, exist_ok=True)
        (d / "print.png").write_bytes(png().read())
    s = storage.mirror_all()
    assert s["skipped"] is False
    assert storage.object_exists("backfill_a", "print.png")
    assert storage.object_exists("backfill_b", "print.png")
