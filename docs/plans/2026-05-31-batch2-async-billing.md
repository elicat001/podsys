# Batch 2: 异步作业 + 计费闭环 + 采集适配 + 测试体系 Implementation Plan

**Goal:** 把 v0.2 的同步原型升级为具备异步作业、计费扣点、真实采集规则与正式 pytest 测试体系的 v0.3。

**Architecture:** 在现有 FastAPI + SQLAlchemy 单体上新增 4 个相互解耦的工作流(异步 Job / 计费 / 采集适配 / 测试),通过新建独立文件实现,集成点(`main.py` 路由注册、AI 端点扣点、`requirements.txt`)由 Tech Lead 统一收口,避免并行冲突。

**Tech Stack:** FastAPI BackgroundTasks、SQLAlchemy 2.0、PyJWT、Pytest、Pillow。

**团队拓扑(并行 4 人 + 评审 1 人):**
| 角色 | 任务 | 仅触碰文件(避免冲突) |
|---|---|---|
| E1 测试工程师 | 把临时脚本变成正式 pytest 套件 | `backend/tests/**` |
| E2 异步作业工程师 | Job 队列 + 状态查询 | `backend/app/services/jobs.py`, `backend/app/routers/jobs.py` |
| E3 计费工程师 | 点数扣减闭环 + 余额/充值 | `backend/app/services/billing.py`, `backend/app/routers/billing.py` |
| E4 采集工程师 | 真实平台原图 URL 规则 | `backend/app/services/collectors.py`, `extension/**` |
| R 代码评审 | 评审整批 diff | 只读 |

**Tech Lead 收口(不下放给并行 agent):** `backend/app/main.py`(路由注册 + AI 端点扣点依赖)、`backend/requirements.txt`(加 pytest)。

---

### Task E1: Pytest 测试体系

**Files:**
- Create: `backend/tests/conftest.py`(TestClient + 每次用临时 SQLite 的 fixture)
- Create: `backend/tests/test_auth.py` `test_assets.py` `test_design.py` `test_products.py` `test_pipeline.py` `test_phash.py`

**要求:**
- `conftest.py` 提供 `client` fixture:用 `tmp_path` 指向独立 db(通过设置 `POD_DATA_DIR` env 或 monkeypatch settings.data_dir 后再 import app),保证测试隔离、可重复。
- 覆盖:注册/登录/错误密码;素材入库+重复图判 high+同形异色非 high;split 三种模式数量;批量套图键集合;商品创建+local 上架 published+temu 501;process 产出三件套;phash 单元(dhash 同图距离 0、color_distance 单调)。
- 全部用 `assert`,命名 `test_*`,可被 `pytest -q` 收集。

**验收:** `pytest -q` 全绿。

---

### Task E2: 异步作业队列

**Files:**
- Create: `backend/app/services/jobs.py` — `create_job(db, kind, params, owner_id=None) -> Job`;`run_job(job_id, fn)`(更新 status running→done/error,写 result/error);`get_job(db, id)`。
- Create: `backend/app/routers/jobs.py` — `GET /api/jobs/{job_id}`(返回 status/result/error)、`GET /api/jobs?kind=`(列表)。
- 用 SQLAlchemy 直接读写现有 `Job` 表(已存在,主键为 12 位 hex 字符串 id)。

**要求:** 不引入 Celery/Redis;用 FastAPI `BackgroundTasks` 即可。提供一个可被 `/api/process` 复用的封装,使长任务能"立即返回 job_id,后台跑,前端轮询 status"。Tech Lead 负责把它接进 main。

**验收:** 单测:create_job→pending;run_job 成功→done 且 result 写入;run_job 抛错→error 且 error 文本写入。在 `tests/test_jobs.py` 自带。

---

### Task E3: 计费扣点闭环

**Files:**
- Create: `backend/app/services/billing.py` — `COST = {"process":2,"generate":5,"edit":4,"asset":1}`;`charge(db, user, op)`:余额不足抛 `InsufficientCredits`,否则扣减并 commit;`InsufficientCredits(Exception)`。
- Create: `backend/app/routers/billing.py` — `GET /api/billing/balance`(需登录,返回 credits + 价目表)、`POST /api/billing/topup`(dev 用,加点数)。

**要求:** 提供一个 FastAPI 依赖 `def charge_for(op): ...` 工厂,Tech Lead 用它给 AI 端点加扣点。余额不足返回 HTTP 402。自带 `tests/test_billing.py`:扣点后余额下降;不足时 402/异常。

**验收:** 单测全绿。

---

### Task E4: 采集器真实规则 + 插件完善

**Files:**
- Create: `backend/app/services/collectors.py` — `upgrade_to_hires(url, platform)` 针对 amazon(去 `_SXxxx_`/`_ACxxx_` 尺寸段)、etsy(`il_340x270`→`il_fullxfull`)、temu/tiktok(去 `?imageView`/`width=` 查询)给出原图 URL;`detect_platform(url)`。
- Modify: `extension/content.js`(改用与后端一致的规则,导出更稳健的 collectImages)、`extension/README.md`(新建,说明安装/合规边界)。
- **不要**碰后端 main.py。

**要求:** 纯函数、可单测(`tests/test_collectors.py`):给定带尺寸段的 URL → 返回去尺寸的原图 URL;detect_platform 正确识别四平台域名。

**验收:** 单测全绿。

---

## 集成与验收(Tech Lead)

1. 合并各 agent 产出 → `requirements.txt` 加 `pytest`;`main.py` 注册 jobs/billing 路由,并给 `/api/process`、`/api/generate`、`/api/edit` 挂 `charge_for(...)` 依赖。
2. `pip install pytest` → `pytest -q` 全套绿。
3. 代码评审 agent 过整批 diff,修正 P0/P1。
4. 提交。
