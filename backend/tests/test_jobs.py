"""作业队列服务测试。

测试隔离由共享的 conftest.py 负责:它在任何 `import app` 之前把 POD_DATA_DIR
指向进程级临时目录,并通过 import app.main 触发 init_db() 建好所有表。
因此这里直接用 app.db.SessionLocal 即可,绝不会触碰开发库 backend/data。
为防止单独运行(无 conftest 副作用)时报错,fixture 里再兜底调用一次 init_db()。
"""
from __future__ import annotations

import pytest

from app.db import SessionLocal, init_db
from app.services import jobs


@pytest.fixture(autouse=True)
def _ensure_schema():
    init_db()  # 幂等:表已存在则 no-op


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def test_create_job_pending(session):
    job = jobs.create_job(session, "process", params={"a": 1})
    assert job.id and len(job.id) == 12
    assert job.status == "pending"
    assert job.params == {"a": 1}


def test_run_job_done(session):
    job = jobs.create_job(session, "process")
    jobs.run_job(job.id, lambda: {"ok": 1})
    refreshed = jobs.get_job(session, job.id)
    session.refresh(refreshed)
    assert refreshed.status == "done"
    assert refreshed.result == {"ok": 1}
    assert refreshed.error == ""


def test_run_job_error(session):
    job = jobs.create_job(session, "process")

    def boom():
        raise ValueError("boom!")

    jobs.run_job(job.id, boom)
    refreshed = jobs.get_job(session, job.id)
    session.refresh(refreshed)
    assert refreshed.status == "error"
    assert refreshed.error
    assert "boom" in refreshed.error


def test_get_job_missing(session):
    assert jobs.get_job(session, "doesnotexist") is None


def test_submit_helper(session):
    """submit() 应建 pending 作业并把 run_job 注册到 background_tasks。"""

    class FakeBG:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn, *args, **kwargs):
            self.tasks.append((fn, args, kwargs))

    bg = FakeBG()
    job_id = jobs.submit(bg, session, "process", lambda: {"ok": 1}, params={"x": 2})
    assert len(job_id) == 12
    job = jobs.get_job(session, job_id)
    assert job.status == "pending"
    assert job.params == {"x": 2}
    assert len(bg.tasks) == 1
    fn, args, _ = bg.tasks[0]
    assert fn is jobs.run_job and args[0] == job_id

    # 真正执行注册的后台任务,验证闭环
    fn(*args)
    session.refresh(job)
    assert job.status == "done"
    assert job.result == {"ok": 1}
