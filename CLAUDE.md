# PODStudio / 灵犀POD — 项目记忆

> **⚠️ 给 AI 的强制工作手册。本文件是本项目的最高约束,每次执行任务都必须遵守,不得脱离。**
>
> 执行任何任务前的固定动作:
> 1. 先通读本文件,对齐"红线"与"代码范式";
> 2. 动手时严格照抄既有范式(三件套分层、扣点退点、owner 隔离等);
> 3. 改完**必须** `pytest -q` 全绿才算完成;
> 4. 若本文件某条约定与用户临时要求冲突,**先停下来向用户确认**,不要擅自违背本文件。
>
> 本文件优先级高于你的临时判断;当不确定该怎么做时,以本文件为准。

## 项目是什么

一站式 **POD(按需印制)设计工作流系统**,对标"灵图 POD"。核心是把用户上传的图片,经
**抠图 → 提取印花 → 放大 → 套图预览 → 导出生产文件** 的流水线,变成可直接送工厂印刷的高清文件。

- 后端:Python 3 + FastAPI + Uvicorn
- **异步作业:Celery + 独立 Redis 实例(默认 127.0.0.1:6380)**。几乎所有产出类端点(印花提取/文生图/
  改图/裂变/转绘/梗图/提质/去水印/转矢量/图生视频 + 套图/批量套图/生产图
  + 采集同步 `collect_sync`:一个商品=一个任务、免费不扣点、只进顶栏「最近任务」不混进作图任务中心)
  = 立即返回 `job_id` → worker 后台跑 → 前端轮询 `/api/jobs/{id}`,前端「提交即走」丢任务中心。
  仍**同步**的:一键抠图 `/api/process`(核心管线)、标题/侵权(分析类,返回文字/风险而非可下载图)。
  详见「异步作业(Celery)」一节。(历史上是 BackgroundTasks,已迁移。)
- 图像:Pillow(默认离线 CPU),可切换 rembg / OpenAI gpt-image / 第三方 API
- 存储:本地文件 + **MySQL 8**(SQLAlchemy 2.0,utf8mb4;已全面转 MySQL,不再支持 SQLite)。`POD_DATABASE_URL` 必填;测试走 `*_test` 隔离库(conftest)。S3/MinIO 接口已预留
- 前端:**Vue 3 SPA**(`frontend-vue/`:Vite + vue-router(history 模式)+ pinia + element-plus,深色风格)。后端 `main.py` 服务其构建产物 `dist`(SPA 回退,见「部署」一节);旧的静态 `frontend/*.html` 已废弃删除。**需登录才能用**(无游客自动注册)。
- 鉴权:JWT(PyJWT)+ pbkdf2 密码哈希;计费:点数(credits)模式

**⚠️ 别低估范围**:核心是上面那条 5 步主线,但项目其实已铺开 **~26 个 router** 的完整能力矩阵(对标灵图 ipoddy 的"采集→作图→上架→制图"):
采集(collectors/collect_tasks/Chrome 插件规划)、抠图、印花提取、放大/提质、套图(+批量替换)、生产图导出、文生图/图生图/改图、作图工具(裂变/风格转绘/梗图)、去水印、标题提取、侵权检测、转矢量、店铺/商品/上架、可视化工作流编辑器、我的空间(配额/回收站)、图生视频(智谱 CogVideoX-3,可插拔,有声,无 key 兜底 GIF)、视频案例库、模板。
**广度够、深度浅**:多数功能停在"架构完整 + MVP 实现 / 留接口";AI 类功能无 key 时是占位。改动前先 `git log --oneline` + 翻 `routers/` 了解已有什么,别重复造。

**🟦 AI 厂商未定(重要)**:本文档示例多用 OpenAI/gpt-image,但公司大概率选**国产**视觉/文生图厂商(通义万相 / 豆包即梦 / 百度 / 智谱等)。**要换厂商,只在 `app/ai/` 那层加一个 Provider 实现**(照 `matting.py` 的策略模式),**严禁在 `services/`、`routers/` 里写死某家 API**。

## 目录结构与分层

```
backend/app/
├── main.py            # FastAPI 入口:挂载所有 router + 核心 /api/process。集成总线,改动需谨慎
├── config.py          # 配置(环境变量 POD_ 前缀 + .env)
├── db.py              # SQLAlchemy + SQLite 连接;init_db() 里 import 所有 models_* 注册表
├── storage.py         # 本地文件存储,对外 /files/{job_id}/{name}
├── auth.py            # JWT + pbkdf2;current_user 依赖
├── models_db.py       # 核心表:User / Asset / Job / Product / Listing
├── models_*.py        # 其它表:collect / shop / workflow / template(各自独立文件)
├── ai/                # 可插拔 AI Provider(策略模式)
│   ├── base.py        # Protocol 接口:MattingProvider / UpscaleProvider
│   ├── matting.py     # 抠图实现:pillow(默认) / rembg / api / gptimage
│   ├── upscale.py     # 放大实现:pillow(Lanczos,默认) / realesrgan(Real-ESRGAN SRVGG onnx,真提质)
│   └── openai_image.py# OpenAI gpt-image-1 客户端
├── routers/           # 接口层(薄,只做参数校验 + 调 service)
├── services/          # 业务逻辑层(extract/mockup/export/workflow/billing/...)
└── data_seed/         # 演示种子数据(JSON)

backend/tests/         # pytest 测试(conftest.py 提供 client / auth_headers / png fixtures)
docs/plans/            # 历史迭代计划(batch2~batch10),记录每批做了什么
frontend-vue/          # Vue 3 SPA 源码(Vite;node_modules/dist 不入库,服务器构建)
deploy.sh              # 生产部署脚本(在服务器上跑;见「部署」一节)
```

**核心流程在 `main.py` 的 `/api/process`**:扣点 → 存原图 → `extract_print`(抠图+裁剪+放大)
→ `render_mockup`(套图)→ `export_production`(导出生产文件)→ 返回三件套 URL。

## 异步作业(Celery)— 所有耗时端点的执行层

> 早期是 FastAPI `BackgroundTasks`(同进程,随重启丢任务);已整体迁到 **Celery + 独立 Redis**。
> `Job` 表(`models_db.py`)始终是状态/结果的**唯一真相源**,前端轮询 `/api/jobs/{id}`——
> 这套**接口契约从 BackgroundTasks 时代到现在没变**,前端无感知。

**组件**:
- `app/celery_app.py`:Celery 应用,broker = `settings.celery_broker_url`(默认 `redis://127.0.0.1:6380/0`)。
  **不配 result backend**(结果落 Job 表)。`task_always_eager` 由 `settings.celery_eager` 驱动。
- `app/tasks.py`:worker 侧顶层任务。**闭包不能跨进程**,故任务只收 `job_id`,自己按 id 从盘上读输入
  (`storage.upload_path(job_id)`)+ 从 `Job.params` 读参数。
  - `run_job_in_worker(job_id, work, refund_op=, refund_n=)`:通用骨架(running+started_at → work →
    done/error+finished_at;失败按笔退点)。**所有任务都走它**。
  - `run_print_extract`(印花提取专用)+ `run_tool`(其余一批 kind 共用,按 `job.kind` 在 `TOOL_WORKS`
    注册表里分派 `_work_*`;含采集同步 `collect_sync` —— op=None 免费不退点)。
- `app/web_utils.py`:router 侧两个收尾助手——
  - `enqueue_or_refund(task, job, db, user, op, n)`:`task.delay()`;**broker 挂了 → 退 n 笔 + 502**(P0)。
  - `submit_celery(task, db, user, kind=, tool_id=, op=, raw=, params=, n=, mask_raw=)`:建 Job + 落输入图
    + 入队,返回 `{job_id, status:"pending"}`。**异步端点的标准收尾就一行调它**。

**加一个新的异步工具**(照抄范式):① router 同步做鉴权/扣点/读图校验,失败即退点;
② `return submit_celery(run_tool, db, user, kind="x", tool_id="x", op="edit", raw=raw, params={...})`;
③ 在 `tasks.py` 写 `_work_x(job_id, job, db)`(读盘+params→service→存产物→`save_as_asset`→return dict)
并登记进 `TOOL_WORKS`;④ 前端 `tools.js` 该工具 `async:true`,`id` 尽量等于 `kind`(否则在 `KIND_ALIAS` 补别名)。

**红线**:
- **测试强制 eager**(`conftest.py` 设 `POD_CELERY_EAGER=true`):任务在测试进程内同步跑,**不连 Redis、不起 worker**,保持离线确定性。改 conftest 要保留这条。
- **独立 Redis 实例**:本地/生产都另起一个 6380 实例,**与别的项目(如同机 Django 的 6379/db1)物理隔离**,key 别串。
- `tasks.py` 里重依赖(openai/各 service)**惰性 import**(函数内),保持离线启动轻量。
- `/api/process-async`(本地快管线,非 AI)仍走 `services/jobs.py` 的 `run_job`/`submit`(BackgroundTasks)——这是**唯一**保留的同进程后台,别误以为还有两套 AI 作业系统。

**本地跑通真链路**(测试不需要;手动验证 AI 工具才需要):
```powershell
# 1. 装 Memurai Developer,把 memurai.conf 的 port 改成 6380,重启其服务(与默认 6379 区分)
# 2. 起 worker:最多 3 个任务并发。Windows 用 threads 池(prefork 在 Win 不稳;线程对 AI/IO 型任务够用):
cd D:\podsys\backend; .\.venv\Scripts\celery.exe -A app.celery_app worker -l info --pool=threads --concurrency=3
# 3. 后端照常:.\.venv\Scripts\python -m uvicorn app.main:app --port 10000
# 没起 Memurai/worker 时,AI 工具会走「优雅降级」→ 502 + 自动退点(不崩、不白扣,但出不了结果)。
```

## 🔴 红线(绝对禁止)

- **禁止删除 `backend/data/podstudio.db`**(开发数据库,删了用户/素材全没)
- **禁止用系统 Python**,必须用项目 venv:`backend/.venv/Scripts/python.exe`
- **禁止擅自启动 uvicorn 跑测试**(测试用 TestClient,见下);需要手动验证再单独启
- 改动 `main.py` / `db.py` / `models_db.py` / `tests/conftest.py` 要格外小心(集成总线)
- **任何改动后必须 `pytest -q` 全绿**才算完成(当前基线 **272 passed**)

## 🟡 必守的代码范式(新代码照抄)

1. **三件套分层**:新功能 = `routers/xxx.py`(薄) + `services/xxx.py`(逻辑) + `tests/test_xxx.py`(必带测试)。router 不写业务逻辑。

2. **鉴权 + 扣点**:收费接口签名固定写
   ```python
   user: User = Depends(charge_for("op")),   # op ∈ process|generate|edit|asset|video
   db: Session = Depends(get_db),
   ```
   只鉴权不扣点用 `user: User = Depends(current_user)`。

3. **失败必退点(P0 级)**:预扣点后,若读图/AI 调用失败,**先 `refund(db, user, "op")` 再抛异常**。
   - 读图失败 → 400;余额不足 → 402;AI 无 key/调用失败 → 502。
   - 读图退点封装:`from app.web_utils import read_image_or_refund`。

4. **新建数据表**:写在自己的 `models_xxx.py`,`from app.db import Base`,然后在 `db.init_db()` 里 import 注册。**不要往 `models_db.py` 塞**(除非是核心表)。

5. **owner 隔离**:任何查/改用户数据都要 `where(owner_id == user.id)`;越权一律返回 **404**(不是 403,避免泄露资源是否存在)。

6. **离线可跑 / 优雅降级**:无 OpenAI key 时不能崩——AI 类端点返回 502(并退点),或降级为占位结果。新功能默认要能在纯 `pillow` 模式下被测试。

7. **可插拔 Provider**:涉及抠图/放大,只调 `get_matting_provider()` / `get_upscale_provider()`,**不要在业务层硬编码具体实现**。切换实现改 `.env` 配置即可。

8. **工作流 step**:新作图工具若要进工作流编排,在 `services/workflow.py` 注册成 step(`@step("name")` + 加 `STEP_META`),即可被自定义工作流复用。

9. **新增第三方库必须登记依赖(AI 自动执行,勿遗漏)**:只要代码里 `import` 了一个**不在标准库、也不在现有 `requirements.txt`** 里的新包,**必须同步把它(带版本约束)写进 `backend/requirements.txt` 并提醒用户提交**。否则会出现"本地能跑、远端/别人 clone 后缺包跑不起来"。
   - 判断标准:能 `pip install xxx` 的就是第三方库,需登记;Python 自带的(如 `io`/`os`/`json`/`pathlib`/`hashlib`)不用。
   - 重依赖(如 `rembg`/`onnxruntime`)若是可选功能,按现有风格在 `requirements.txt` 里用注释列出(`# 可选 ...`)并保持惰性 import,不强制安装。
   - 每次交付前自检:`git diff` 有没有新 import 却没改 `requirements.txt`。

## 测试

- 运行:`cd backend && ./.venv/Scripts/python.exe -m pytest -q`
- 测试用 `TestClient`,**不需要启 uvicorn**。
- `conftest.py` 已做三层隔离,**不会污染开发库、也不碰真实外部 API**:① `POD_DATA_DIR` 指向临时目录(文件存储);② **强制离线**——清空 `POD_OPENAI_API_KEY` + 锁定 `pillow` 引擎;③ **DB 用隔离的 `*_test` 库**——读 `.env` 的 `POD_DATABASE_URL`、把库名换成 `<库>_test`(如 `podsys_test`),**带安全栅栏:库名不以 `_test` 结尾就拒绝 drop_all**,每次跑测试 drop+create 从空库开始,绝不碰真实库。所以即使 `.env` 配了真 key + 真库,`pytest` 仍离线、且只动 `*_test`(~110s)。**改 conftest 要保留这三层隔离 + `_test` 栅栏**(早期踩过坑:配了真 key 没隔离,AI 类测试真去调网关,11 个超时失败、跑了 29 分钟)。
  - ⚠️ 跑测试前需先建好 `*_test` 库 + 授权(本机:`CREATE DATABASE podsys_test CHARACTER SET utf8mb4; GRANT ALL ON podsys_test.* TO 'podsys'@'127.0.0.1','podsys'@'localhost';`)。conftest 连不上会打印这条提示。
- 可用 fixtures:`client`(TestClient)、`auth_headers`(已注册用户的 Bearer 头)、`png`(内存造图工厂)。
- 新表的测试在文件顶部确保建表:`from app.db import engine, Base; Base.metadata.create_all(engine)`。
- 覆盖要点:正常路径 + 未登录 401 + 越权 404 + 参数非法 400/422 + 余额不足 402 +(AI 类)无 key 502 且退点。

## 开发工具链(本地工具,**不入库**)

> ⚠️ 这些工具的文件(`backend/pyproject.toml`、`backend/requirements-dev.txt`、`backend/e2e/`)都在 `.gitignore` 里,**只在本机存在,不会进 git**。所以:① 本节命令在已配置好的本机能直接用;② **新 clone 的人需自行重装**(`pip install ruff mypy pytest-cov pytest-playwright` + `python -m playwright install chromium`),仓库里不会有这些配置文件。它们是个人开发辅助,不是项目交付物。

开发工具单独记在 `backend/requirements-dev.txt`,**不进生产 `requirements.txt`**。配置都在 `backend/pyproject.toml`。所有命令在 `backend/` 目录下用 venv 执行。

| 工具 | 作用 | 命令 |
|---|---|---|
| **Ruff** | Python lint + 格式化(替代 black/flake8/isort) | 检查:`./.venv/Scripts/ruff.exe check app`  自动修:`ruff check app --fix`  格式化:`ruff format app` |
| **Mypy** | 静态类型检查 | `./.venv/Scripts/python.exe -m mypy app` |
| **pytest-cov** | 测试覆盖率(看哪些代码没被测到) | `./.venv/Scripts/python.exe -m pytest --cov=app --cov-report=term-missing` |
| **Playwright** | 端到端浏览器测试(验证真实 UI 行为) | `./.venv/Scripts/python.exe -m pytest e2e` |

约定:
- **单元/接口测试在 `tests/`**(TestClient,快);**E2E 在 `backend/e2e/`**(真浏览器+真服务,慢)。默认 `pytest -q` 只跑 `tests/`(testpaths 限定),**E2E 要显式 `pytest e2e`**。
- E2E 会自起一个隔离的 uvicorn 子进程(端口 8099 + 临时库),不碰开发库;写新 E2E 用 `page`/`base_url` 夹具(见 `e2e/conftest.py`),标 `@pytest.mark.e2e`。
- Ruff 配置已忽略本项目惯用的 `db.add(); db.commit()` 分号风格(E701/E702),只报真问题。提交前建议跑一次 `ruff check app`。
- **改完代码的完整自检**:`ruff check app` → `pytest -q` 全绿;动了核心管线/前端再补 `pytest e2e`。

## Alembic(数据库 schema 迁移)

> 配置在 `backend/alembic/`(`env.py` 从 `app.settings` 读 `POD_DATABASE_URL`,不硬编码密码;`target_metadata=Base.metadata`)。基线版本 `5d2d1973fc1a` = 当前全量表结构。本地/生产库都已 `stamp` 到基线。`deploy.sh [2b/6]` 会自动 `alembic upgrade head`(首次无版本记录的库先 `stamp head`)。

**改表标准流程**(加/改/删列、加索引等对**已存在表**的改动——`create_all` 做不到):
1. 改模型(`models_*.py`)。
2. 本地生成迁移:`cd backend && ./.venv/Scripts/alembic.exe revision --autogenerate -m "说明"`。
3. **打开 `alembic/versions/` 新文件核对**(autogenerate 偶有 MySQL 反射误差,如类型/默认值;删掉多余的 op)。
4. 本地应用 + 验证:`alembic upgrade head` → `alembic check`(应「No new upgrade operations」)→ `pytest -q`。
5. 提交(含新迁移文件)→ push → `deploy.sh` 自动 `upgrade head` 把线上表对齐。

**注意**:
- **新建表**:`create_all` 启动时会自动建(本地+线上都自愈),所以新表不强制走迁移也能跑;但**改存量表必须走 Alembic**。
- **测试不走 Alembic**:`conftest` 用 `Base.metadata.create_all` 直接按当前模型建 `*_test` 库(快、对齐模型),不跑迁移。
- 别手动 `ALTER TABLE` 改线上库 —— 走迁移才能本地/线上一致、可追溯、可回滚(`alembic downgrade -1`)。

## 配置开关(`backend/.env`,前缀 POD_)

| 变量 | 默认 | 说明 |
|---|---|---|
| `POD_DATABASE_URL` | 空 | **必填**(已全面转 MySQL,不再支持 SQLite);`mysql+pymysql://用户:密码@127.0.0.1:3306/podsys?charset=utf8mb4`。留空/非 mysql → `db.py` 直接抛错。测试由 conftest 自动改用 `<库>_test` 隔离库 |
| `POD_MATTING_PROVIDER` | `pillow` | 抠图引擎:pillow / rembg / api / gptimage |
| `POD_UPSCALE_PROVIDER` | `pillow` | 放大引擎(gpt-image 不做超分,会重绘像素,生产禁用) |
| `POD_OPENAI_API_KEY` | 空 | 配了才能用 gptimage / 文生图 / 图生图 |
| `POD_VIDEO_PROVIDER` | `local` | 图生视频引擎:local(本地兜底 GIF,无 key 也出东西=降级) / cogvideox(智谱 CogVideoX-3 真视频)。换厂商只加 `ai/video.py` 的 Provider,业务/前端不动 |
| `POD_VIDEO_API_KEY` | 空 | 智谱开放平台 key;`POD_VIDEO_PROVIDER=cogvideox` 时必填。其余 `POD_VIDEO_*`(model/quality/fps/seconds/with_audio/size/timeout)见 `.env.example`,均有默认、**不暴露给前端**(扣费与分辨率无关) |
| `POD_JWT_SECRET` | dev 默认值 | **生产必须改** |
| `POD_DEV_BILLING` | `true` | 自助充值;**生产必须置 false** |
| `POD_REGISTER_RATE_LIMIT` | `1000` | 注册限流;**生产应调到 ~5** 防刷点 |
| `POD_CELERY_BROKER_URL` | `redis://127.0.0.1:6380/0` | 异步作业 broker(**独立** Redis 实例,与别的项目隔离) |
| `POD_CELERY_EAGER` | `false` | true=任务同进程同步执行(测试用,conftest 强制开)。生产/本地真跑保持 false |

## 已知技术债 / 生产前必处理

- 生产前三件套:换 `POD_JWT_SECRET`、关 `POD_DEV_BILLING`、收紧 `POD_REGISTER_RATE_LIMIT`。
- `services/phash.py` 用了 Pillow 已弃用的 `getdata()`(Pillow 14 移除),是测试 warning 主因,可顺手清理。
- **已全面转 MySQL 8(不再支持 SQLite)**:`db.py` 只建 MySQL 引擎(`POD_DATABASE_URL` 必填、非 mysql 直接抛错;连接池硬化 pool_pre_ping/recycle)。已加索引:`jobs(owner_id,created_at)`+`jobs(status)`、`assets(owner_id,deleted)`、`collected_images(task_id,synced)`。**测试也走 MySQL** 的 `*_test` 隔离库(conftest 自动 `<库>_test` + `_test` 栅栏 + drop/create)。本地 MySQL 8.4 在 `D:\mysql-local\data`(localhost-only,Windows 服务 `MySQLPodsys` 自启),库 `podsys`+`podsys_test`;生产是系统 MySQL 8.0 上的独立库 `podsys`+独立用户(权限只授 `podsys.*`,与同机 Django 的 `kejing`/`kejing_staging` 物理隔离)。存储仍本地文件,上量再换 S3/MinIO。
- **已接入 Alembic 管 schema 版本**(`backend/alembic/`,基线 `5d2d1973fc1a`)。`create_all` 仍留作安全网(新表自愈),但**改/加列、加索引等对已存在表的改动必须走 Alembic**(`create_all` 改不了存量表)。改表标准流程见下「Alembic」一节。一次性迁移脚本 `migrate_sqlite_to_mysql.py` 已完成使命删除(git 历史 da577a4 可取回)。
- `ai/upscale.py` 的 `realesrgan` 已接 Real-ESRGAN(SRVGG general-x4v3,onnx,~几秒真提质);模型 `models/realesr_x4v3.onnx` 不入库,缺失自动降级 Lanczos。

## ⚠️ 额外注意事项 / 易踩的坑(代码里真实存在,docs 未必写)

### 配置文件有"误导"——别被带偏
- **`.env.example` 默认 `POD_MATTING_PROVIDER=gptimage`(需 key),但实际运行的 `backend/.env` 是 `pillow`(离线)。** 改配置/调试时以**实际 `.env`** 为准,默认就是纯离线 pillow 模式。
- **`.claude/launch.json` 是本地私有配置**(含机器相关绝对路径),已修正为本项目路径(`D:/podsys`、端口 10000)并加入 `.gitignore`(不入库,各人填各自路径)。启动一律用 `backend/.venv/Scripts/python.exe -m uvicorn app.main:app --port 10000`(项目约定端口 **10000**)。
- **数据库/`.env` 路径已锚定到 `backend/`(`config.py` 用 `__file__` 定位),不再依赖启动目录。** 唯一的开发库永远是 `backend/data/podstudio.db`。
  - 历史坑:早期 `data_dir=Path("data")` 是相对启动目录的,从项目根目录启动会在根目录新建一个**空库**,导致"已登录用户突然变成『用户不存在』(401)"。已修复,勿改回相对路径。
  - 若项目根目录出现遗留的 `./data/podstudio.db`(空库),是历史误启动产生的,可安全删除,不要和 `backend/data/` 的真库混淆。

### 印花提取 vs 一键抠图(两个不同功能,别再合并)
- **一键抠图**(`/api/process`,rembg):去背景、留主体(人/物)。
- **印花提取**(`/api/print-extract`):把布料/产品上的图案提取成可用花样。**双引擎,`services/print_extract.py` 编排**:
  - 🟢 **默认 = AI 重绘**(`gpt-image edit` 展平):把实拍图(含挂拍窗帘/褶皱/透视)重绘成一张『含底色的平整花样图』,**95% 视觉一致、不保原像素**。能处理本地算法根本做不了的褶皱实拍图。开关 `POD_PRINT_EXTRACT_AI`(默认 true),仅在有 key 时生效。
  - 🟡 **兜底 = 本地保真算法**(`design_extract.extract_design`):无 key / AI 失败(502/超时/配额)/ `POD_PRINT_EXTRACT_AI=false` 时自动降级。输出『透明背景的忠实印花抠图』(原像素、保真),用于套版。**全本地、不依赖网关**,**不只衣服——枕头/袋子/杯子等也支持**。
  - ⚠️ **引擎决策(为何默认走会重绘的 AI)**:对"实拍场景图→可用花样"这个场景,本地算法启动不了(锐利折痕去不掉、拿不到干净 tile),只能靠 AI 展平;已确认**接受 95% 视觉一致而非 100% 保真**。要 100% 保真请置 `POD_PRINT_EXTRACT_AI=false`。**两引擎输出性质不同**(AI=含底色重绘图 / 本地=透明保真抠图),下游要能接受降级时的差异。
  - 本地算法流程(`extract_design`)=**①框出产品本体:衣服→cloth-seg(`u2net_cloth_seg`,排除人/皮肤);非衣服→通用 rembg(`u2net`)→ ②mask 小幅腐蚀(只削轮廓,不伤袖子/边缘印花)→ ③产品内去『主导材质色』(白/黑/任意),留差异大的像素=印花 → ④连通块面积过滤 + 自动裁剪**。缩小图(1000px)算 mask,再放大套回全分辨率原图。
  - **演进史(别走回头路)**:踩过一串坑——写死"去白色"黑衣失败、"去边缘色"框带皮肤残留、vision 定位不准/依赖网关、rembg-on-crop 对平面印花出雾、**大幅腐蚀砍袖子**、**开运算砍细线印花**、**fold-rejection 砍浅色印花**。最终=**分割模型框本体 + 去主导材质色 + 小腐蚀 + 连通块过滤**。(历史定调"绝不用识图再生图做提取"是在**100% 保真**前提下;现已新增 AI 重绘引擎作默认,见上「引擎决策」——它是**主动接受 95% 视觉一致**的产品取舍,**本地保真算法仍是兜底、未删**,两者并存。)
  - 都没分到产品(输入本身就是设计图)→ 退化为 `extract_on_fabric`(整图按边缘色去底)。`method` ∈ garment / product / whole_image / whole_image_fallback。
  - **已知边界(别过拟合)**:深色衣服、枕头等清晰产品都干净;**重褶皱浅色(白)布料的褶皱阴影颜色上和浅色印花分不开,会有残留**——颜色分离的固有难点。**宁留残留也别加"开运算/取最大块/fold-rejection"等启发式**(会损坏印花本身,且过拟合)。要更极致得上真 AI 编辑(edit key)。

### 前端↔后端鉴权(Vue:需登录,无游客自动注册)
- **`frontend-vue` 需登录才能用**:`Login.vue` 走 `/api/auth/login`(或注册 `/api/auth/register`),token 存 `localStorage('pod_token')`(沿用老 key),`api/client.js` 自动加 `Authorization: Bearer <token>`。
- **路由守卫**(`router/index.js` `beforeEach`):只有 `meta.public` 的页(落地页 `/`、`/login`)免登录,其余未登录一律重定向 `/login?redirect=...`;已登录访问 `/login` 跳 `/app`。
- **401 自动登出**:`api/client.js` 收到 401 → 清 token、回登录页。
- **坑**:`pod_token` 指向的用户在当前库不存在(换库 / 清库 / token 过期)→ 后端 401「用户不存在」→ 前端自动登出回登录页。重新登录即可;若反复异常,排查是不是连错了库(见上「数据库锚定」)。

### docs/plans 不是完整记录
- `docs/plans/` 只覆盖到 **batch2~batch10**。**batch11(`effects.py` 真实离线引擎)、batch12(深度审计整改:产物入库 / 真超分 / 工具去重 / 扩侵权库)只在 git 历史里**。
- 想了解某功能的来龙去脉,**先 `git log --oneline` 查提交**,别只信 docs。

### Git / 提交规范(照抄历史风格)
- 提交信息用 **conventional commits**:`feat(batchN): ...` / `fix(batchN): ...` / `chore: ...` / `merge: ...`,**中文描述**,常在末尾标注当时测试数如 `(211 tests green)`。
- 当前在 `main` 分支。**按 harness 规则:只有用户明确要求才提交/推送;在 main 上动手前先开分支。**
- **绝不提交 `backend/data/`、`*.db`、`.env`**(`.gitignore` 已覆盖;历史上误跟踪过 db,已清理)。
- **🔑 严禁提交任何密钥/API key(每次提交前必查!)**:真实 key 只能存在 `backend/.env`(已 gitignore)。
  - **提交前强制自检**:`git diff --cached` 里搜 `sk-` / `key` / `token` / `secret`,确认没有真实密钥混进任何**被跟踪文件**(代码 / 文档 / `.env.example` / CLAUDE.md 等)。发现就**立即抹掉**再提交。
  - 占位符(如 `.env.example` 里的 `sk-xxxxxxxx`)可以,真 key 绝对不行。
  - 万一 key 已被 commit:不是删文件就完事——**git 历史里还在**,必须改用新 key(让旧 key 作废)+ 清理历史。所以重在"提交前拦住",别等泄漏。
  - AI 执行提交类操作时,**默认先跑这条自检**,有可疑密钥先停下问用户,不擅自提交。

### 退点的笔数要对齐(易错)
- `web_utils.read_image_or_refund` **只适用于"单次预扣"端点**(`charge_for` 扣 1 笔)。
- **按张多扣的端点**(如 `design_tools` 的 `variants` 批量生成)**必须自己按笔数退点**,不能直接用这个函数,否则退点数不平。

### gpt-image / OpenAI 调用的硬约束
- gpt-image-1 **只返回 base64 PNG**(无 url),且尺寸**只接受** `1024x1024` / `1536x1024` / `1024x1536` / `auto`,其它会被强制成 `auto`。
- **绝不能用 gpt-image 做"无损放大"**——它是生成模型,会重绘像素、改动印花,毁掉生产文件。放大永远走 `pillow`(Lanczos)或 Real-ESRGAN。
- OpenAI SDK 客户端按凭证**缓存复用**(`openai_image.py` 的 `_SDK_CACHE`),且重依赖(`openai`/`rembg`)都是**惰性 import**——新增 Provider 要保持这个习惯,别在模块顶层 import 重依赖,以免拖慢离线启动。
- **无 key 时的兜底**:`services/effects.py` 提供纯 Pillow 的真实图像变换。AI 类工具的范式是"**有 key 走 gpt-image,无 key 回退 effects.py**",新工具尽量遵循,保证离线也产出真东西。

### 并发 / 单机假设(上量前必知)
- 同步端点跑在 FastAPI 线程池里;SQLite 用 `check_same_thread=False`。
- **限流器(`ratelimit.py`)和一切内存态都是进程级单例,只对单机有效**。多实例部署会失效,需换 Redis 等共享存储。
- **后台任务(`run_job`)必须自己 `SessionLocal()` 开新 DB session**,不能复用请求里的 session(请求结束 session 已关)。参考 `services/jobs.py`。

### 图像处理要设上限防 OOM
- 处理用户上传图时要**限制像素/矩形数量**(如 `effects.py` 的 `MAX_PX = 50_000_000`、`vectorize` 的矩形数上限)。新增图像操作时**保留/加上这类保护**,避免超大图打爆内存。

## 验证主流程是否正常(冒烟)

启动后端:`cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 10000`
启动前端(本地开发):`npm --prefix frontend-vue run dev`(Vite,5173,代理 `/api`、`/files` 到 10000)。
流程:登录/注册 `/api/auth/*` → 拿 token → `POST /api/process`(带图)→ 检查返回的
print/mockup/production 三个 URL 可访问(HTTP 200),production 为 30×40cm@300DPI(3543×4724)。

## 部署 / 生产(pod.kejing.online)

> 生产机:Ubuntu 24.04,**root@pod.kejing.online**(SSH 别名常配为 `pod-kejing`)。
> 项目在 **`/www/wwwroot/podsys`**,运行用户 **`www`**;后端由 systemd **`podsys.service`** 跑
> (`uvicorn app.main:app --port 10000`),**nginx** 反代 `pod.kejing.online` → `127.0.0.1:10000`。
> **同机还有个无关的 Django 项目 `kejing-gunicorn`(/www/wwwroot/django,8000)——别碰。
> 它占用系统 Redis 的 6379/db1,所以本项目的 Celery broker 必须另起一个 6380 实例,别串。**

> **✅ Phase D 已上线**:异步执行层已部署。生产侧基建(一次性,已建好,勿重复建):
> - **独立 Redis 实例 6380**:`/etc/redis/redis-podsys.conf`(`port 6380` + `bind 127.0.0.1` + `save ""`
>   无持久化,broker 消息可丢、Job 表才是真相源)+ systemd 模板实例 **`redis-server@podsys`**(enable+start)。
>   与 Django 的 6379/db1 **物理隔离**。排障:`redis-cli -p 6380 ping`。
> - **`podsys-worker.service`**(用户 `www`,`celery -A app.celery_app worker -l info --concurrency=3`,
>   prefork 池=最多 3 任务并发;连 6380;enable+start)。日志 `/var/log/podsys/worker-{out,err}.log`。
>   (本地 Windows 用 `--pool=threads --concurrency=3`,同样 3 并发。)
> - `deploy.sh` 已补:`pip install -r requirements.txt`(装 celery/redis)+ 末尾 restart worker。
> 排障:`systemctl status redis-server@podsys podsys-worker podsys`。**动服务器前先 SSH 核对边界,不碰 Django/6379。**

**架构**:nginx 把 `/` 全转给后端;后端一身二职——`/api`、`/files` 走业务,其余路径服务
Vue 构建产物 `frontend-vue/dist`(`main.py` 的 `_SPAStaticFiles`:404 回退 `index.html`,支持
history 深链刷新;`/api`、`/files` 仍保留真 404)。前端**在服务器上构建**(生产机已装 Node LTS)。

**部署 = 一条命令**(本机先 `git push` 到 `origin/main`,然后):
```bash
ssh pod-kejing 'bash /www/wwwroot/podsys/deploy.sh'   # 没配别名就 ssh root@pod.kejing.online
```
`deploy.sh`(仓库根,**在服务器上跑**)流程:`git pull --ff-only` → `npm ci` → `npm run build`
**到临时 dist.new、成功才原子替换**现网 `dist`(构建失败现网不动=零停机)→ `systemctl restart
podsys.service` → 健康检查 `/` 与 `/api` 均 200。幂等可重跑,断了重跑即可。

**注意 / 坑**:
- 前端只改源码也要**重新构建**才生效(走 `deploy.sh`);后端改了由 `deploy.sh` 的 restart 生效。
- `www` 的 home(`/www/wwwroot`)属 root,**npm 没法在那写缓存** → `deploy.sh` 用 `HOME=/tmp/wwwbuild`(脚本会自建)。
- **`frontend-vue/src/data/` 是源码不是运行时数据**:根 `.gitignore` 的 `data/` 规则会误伤它,已加例外
  `!frontend-vue/src/data/`。**以后在前端新建 `data/` 类目录注意别被 gitignore 吞掉**(`git status --ignored` 自检)。
- `*.sh` 由 `.gitattributes` 强制 LF(防 Windows CRLF 在 Linux 上把脚本搞坏)。
- 生产前三件套仍要处理:`POD_JWT_SECRET`、关 `POD_DEV_BILLING`、收紧 `POD_REGISTER_RATE_LIMIT`(见上)。
