"""Pytest fixtures + test isolation.

测试隔离的关键:`app.db` 在 import 时就根据 `settings.data_dir` 建 engine,
而 `settings` 又在 `app.config` import 时实例化。因此必须在任何 `from app...`
import 之前,把环境变量 `POD_DATA_DIR`(Settings 用 env_prefix=POD_,字段 data_dir)
指向一个临时目录,这样开发库 backend/data/podstudio.db 永远不会被测试触碰。

这里在模块顶层(任何 app import 之前)就设置好 POD_DATA_DIR,指向一个进程级临时目录。
"""
from __future__ import annotations

import io
import os
import tempfile
import uuid
from pathlib import Path

# --- 测试隔离:必须在 import app 之前设置 ---------------------------------
_TMP_DATA_DIR = Path(tempfile.gettempdir()) / f"podstudio_test_{uuid.uuid4().hex[:8]}"
_TMP_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ["POD_DATA_DIR"] = str(_TMP_DATA_DIR)


# ── 测试数据库:用 MySQL 同库名加 _test 的隔离库(项目已全面转 MySQL,无 SQLite 兜底)──
# 取真实 POD_DATABASE_URL(env 或 backend/.env),把库名换成 <db>_test,**绝不在真实库上跑**。
def _read_env_file_var(name: str) -> str:
    envf = Path(__file__).resolve().parent.parent / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(name + "="):
                return s[len(name) + 1:].strip()
    return ""


def _derive_test_db_url() -> str:
    from sqlalchemy.engine import make_url
    base = os.environ.get("POD_DATABASE_URL") or _read_env_file_var("POD_DATABASE_URL")
    if not base:
        raise RuntimeError(
            "测试需要 MySQL:请在 backend/.env 配 POD_DATABASE_URL(mysql+pymysql://...);"
            "测试会自动改用同库名加 _test 的隔离库(如 podsys_test),不碰真实数据。"
        )
    url = make_url(base)
    db = url.database or ""
    if not db.endswith("_test"):
        db = db + "_test"
    url = url.set(database=db)
    # 安全栅栏:测试库名必须以 _test 结尾,杜绝在真实库上 drop_all
    assert (url.database or "").endswith("_test"), "测试库名必须以 _test 结尾(防误删真实库)"
    return url.render_as_string(hide_password=False)


os.environ["POD_DATABASE_URL"] = _derive_test_db_url()
# 关键:确保即使存在 .env 也不会覆盖临时目录(env 变量优先级高于 .env 默认值)
# 测试必须『离线、确定性、不碰真实外部 API』:即使 backend/.env 配了真 key,
# 也在这里强制清空 key + 锁定 pillow 引擎,否则 AI 类测试会真去调网关→超时/不稳定。
# (env 变量优先级高于 .env,所以这几行能盖掉 .env 里的值)
os.environ["POD_OPENAI_API_KEY"] = ""
os.environ["POD_MATTING_PROVIDER"] = "pillow"
os.environ["POD_UPSCALE_PROVIDER"] = "pillow"
# 图生视频同理:强制本地兜底引擎(出 GIF、不调智谱),清空 key,保证离线确定性——
# 否则 .env 里配了 POD_VIDEO_PROVIDER=cogvideox 时,视频测试会真去调智谱网关 / 因无 key 报错。
os.environ["POD_VIDEO_PROVIDER"] = "local"
os.environ["POD_VIDEO_API_KEY"] = ""
# 关闭本地标题 OCR:避免测试依赖系统 tesseract 二进制 + 保持离线确定性/速度(标题走纯规则路径)
os.environ["POD_TITLE_OCR"] = "false"
# Celery 强制 eager:任务在测试进程内同步执行,**不连 Redis broker / 不起 worker**。
# 这样异步端点(印花提取 AI 路径等)在测试里走「入队→立即同步跑完」,可断言 Job 最终态,
# 且保持离线确定性。env 优先于 .env,celery_app 构造时读取 settings.celery_eager。
os.environ["POD_CELERY_EAGER"] = "true"
# 进程退出清理本次临时库,避免历史 podstudio_test_* 累积撑满 Temp(ENOSPC)
import atexit  # noqa: E402
import shutil  # noqa: E402
atexit.register(lambda: shutil.rmtree(_TMP_DATA_DIR, ignore_errors=True))
# -------------------------------------------------------------------------

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

# 现在再 import app —— 此时 settings.data_dir/database_url 已指向临时目录 + *_test 库
from app.config import settings  # noqa: E402
from app.db import Base, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402

# 干净起点:确认连的是 *_test 库后,重建全部表(drop+create),每次跑测试从空库开始,不污染真实库
assert (engine.url.database or "").endswith("_test"), "拒绝在非 _test 库上重建表"
try:
    init_db()  # 导入全部模型 + create_all
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
except Exception as _e:  # noqa: BLE001
    raise RuntimeError(
        f"准备测试库 {engine.url.database} 失败:{_e}\n"
        f"先建库+授权:CREATE DATABASE {engine.url.database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; "
        f"GRANT ALL ON {engine.url.database}.* TO '<用户>'@'localhost','<用户>'@'127.0.0.1';"
    ) from _e


def make_png(
    *,
    size: tuple[int, int] = (256, 256),
    bg: tuple[int, int, int] = (255, 255, 255),
    shape: str = "circle",
    fill: tuple[int, int, int] = (200, 30, 30),
    seed: int | None = None,
) -> io.BytesIO:
    """在内存里造一张 PNG。

    shape: circle | rect | noise
    返回一个定位到 0 的 BytesIO,可直接喂给 TestClient 的 files=。
    """
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    w, h = size
    if shape == "circle":
        # 居中圆
        r = min(w, h) // 4
        cx, cy = w // 2, h // 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    elif shape == "rect":
        d.rectangle([w // 4, h // 4, w * 3 // 4, h * 3 // 4], fill=fill)
    elif shape == "noise":
        # 用 seed 造一张结构完全不同的图(棋盘/条纹),保证 dhash 差异大
        s = (seed or 0) % 7 + 3
        for y in range(0, h, s):
            for x in range(0, w, s):
                if ((x // s) + (y // s) + (seed or 0)) % 2 == 0:
                    d.rectangle([x, y, x + s, y + s], fill=fill)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client: TestClient) -> dict:
    """注册一个随机邮箱用户,返回 Bearer 头。"""
    email = f"user_{uuid.uuid4().hex[:10]}@test.local"
    resp = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def png():
    """暴露造图工厂给测试。"""
    return make_png


@pytest.fixture()
def tool_result(client):
    """异步工具端点助手:POST 响应 → 轮询作业 → 断言 done 并返回 result。

    所有耗时端点都迁到 Celery 后返回 {job_id, status:"pending"};eager 模式(conftest 强制)下
    任务已在 POST 时同步跑完,这里取回 job.result 供断言。失败的断言会打印整个 job 便于定位。
    """
    def _get(headers, resp):
        assert resp.status_code == 200, resp.text
        job = client.get(f"/api/jobs/{resp.json()['job_id']}", headers=headers).json()
        assert job["status"] == "done", job
        return job["result"]
    return _get


def test__isolation_uses_tmp_dir():
    """sanity:确认测试库确实在临时目录,而非开发库 backend/data。"""
    assert str(settings.data_dir) == str(_TMP_DATA_DIR)
    assert "podstudio_test_" in str(settings.data_dir)
