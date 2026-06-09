"""Celery 应用 —— 异步作业的执行传输层。

设计要点(与项目既有约定对齐):
- **broker = 独立 Redis 实例**(默认本地 6380),和旁边 Django 项目的 6379/db1 物理隔离。
- **不配 result backend**:作业状态/结果的唯一真相源是 `Job` 表(见 `services/jobs.py` /
  `tasks.py`),前端轮询 `GET /api/jobs/{id}` —— 接口契约与早期 BackgroundTasks 版本一致。
- **eager 模式**(`settings.celery_eager=true`):任务在调用进程内同步执行,不连 broker。
  测试(conftest 强制开启)与「本地无 worker 调试」用,保证 pytest 离线、确定性。
- 任务定义在 `app/tasks.py`,本模块末尾 import 以完成注册(worker 加载本模块即可)。
"""
from __future__ import annotations

from celery import Celery

from .config import settings

celery_app = Celery("podsys", broker=settings.celery_broker_url)

celery_app.conf.update(
    task_ignore_result=True,          # 结果落 Job 表,无需 Celery backend
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,              # 任务跑完才 ack:worker 崩了消息会重投(配合幂等任务)
    worker_prefetch_multiplier=1,     # AI 作业耗时长,一次只预取一条,避免堆积在单 worker
    task_always_eager=settings.celery_eager,
    task_eager_propagates=True,       # eager 下异常照常抛出(任务内部已自行兜底记录到 Job)
    broker_connection_retry_on_startup=True,
)

# 注册任务(放末尾避免循环 import:tasks 内 `from .celery_app import celery_app`)。
from . import tasks  # noqa: E402,F401
