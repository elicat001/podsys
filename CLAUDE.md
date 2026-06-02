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

- 后端:Python 3 + FastAPI + Uvicorn(单体,无 Celery/Redis)
- 图像:Pillow(默认离线 CPU),可切换 rembg / OpenAI gpt-image / 第三方 API
- 存储:本地文件 + SQLite(SQLAlchemy 2.0);接口已预留可换 S3/Postgres
- 前端:静态 HTML/JS(`frontend/*.html`,深色风格)
- 鉴权:JWT(PyJWT)+ pbkdf2 密码哈希;计费:点数(credits)模式

**⚠️ 别低估范围**:核心是上面那条 5 步主线,但项目其实已铺开 **~26 个 router** 的完整能力矩阵(对标灵图 ipoddy 的"采集→作图→上架→制图"):
采集(collectors/collect_tasks/Chrome 插件规划)、抠图、印花提取、放大、套图、生产图导出、文生图/图生图/改图、作图工具(裂变/融合/风格转绘/梗图)、图案处理(扩图/去水印/裁剪压缩)、套图标题(标题/试衣/宠物换装/合照)、侵权检测、以图搜图、转矢量、店铺/商品/上架、可视化工作流编辑器、我的空间(配额/回收站)、视频生成、视频案例库、模板。
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
│   ├── upscale.py     # 放大实现:pillow(Lanczos,默认) / realesrgan(占位)
│   └── openai_image.py# OpenAI gpt-image-1 客户端
├── routers/           # 接口层(薄,只做参数校验 + 调 service)
├── services/          # 业务逻辑层(extract/mockup/export/workflow/billing/...)
└── data_seed/         # 演示种子数据(JSON)

backend/tests/         # pytest 测试(conftest.py 提供 client / auth_headers / png fixtures)
docs/plans/            # 历史迭代计划(batch2~batch10),记录每批做了什么
frontend/              # 静态页面
```

**核心流程在 `main.py` 的 `/api/process`**:扣点 → 存原图 → `extract_print`(抠图+裁剪+放大)
→ `render_mockup`(套图)→ `export_production`(导出生产文件)→ 返回三件套 URL。

## 🔴 红线(绝对禁止)

- **禁止删除 `backend/data/podstudio.db`**(开发数据库,删了用户/素材全没)
- **禁止用系统 Python**,必须用项目 venv:`backend/.venv/Scripts/python.exe`
- **禁止擅自启动 uvicorn 跑测试**(测试用 TestClient,见下);需要手动验证再单独启
- 改动 `main.py` / `db.py` / `models_db.py` / `tests/conftest.py` 要格外小心(集成总线)
- **任何改动后必须 `pytest -q` 全绿**才算完成(当前基线 227 passed)

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
- `conftest.py` 已做两层隔离,**不会污染开发库、也不碰真实外部 API**:① `POD_DATA_DIR` 指向临时库;② **强制离线**——清空 `POD_OPENAI_API_KEY` + 锁定 `pillow` 引擎。所以即使 `backend/.env` 配了真 key,`pytest` 仍跑离线占位路径(确定性、~50s)。**改 conftest 要保留这两层隔离**(早期踩过坑:配了真 key 后没隔离,AI 类测试真去调网关,11 个超时失败、跑了 29 分钟)。
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

## 配置开关(`backend/.env`,前缀 POD_)

| 变量 | 默认 | 说明 |
|---|---|---|
| `POD_MATTING_PROVIDER` | `pillow` | 抠图引擎:pillow / rembg / api / gptimage |
| `POD_UPSCALE_PROVIDER` | `pillow` | 放大引擎(gpt-image 不做超分,会重绘像素,生产禁用) |
| `POD_OPENAI_API_KEY` | 空 | 配了才能用 gptimage / 文生图 / 图生图 |
| `POD_JWT_SECRET` | dev 默认值 | **生产必须改** |
| `POD_DEV_BILLING` | `true` | 自助充值;**生产必须置 false** |
| `POD_REGISTER_RATE_LIMIT` | `1000` | 注册限流;**生产应调到 ~5** 防刷点 |

## 已知技术债 / 生产前必处理

- 生产前三件套:换 `POD_JWT_SECRET`、关 `POD_DEV_BILLING`、收紧 `POD_REGISTER_RATE_LIMIT`。
- `services/phash.py` 用了 Pillow 已弃用的 `getdata()`(Pillow 14 移除),是测试 warning 主因,可顺手清理。
- 存储/DB 是本地文件 + SQLite,上量需换 S3/MinIO + Postgres(接口已预留)。
- `ai/upscale.py` 的 `realesrgan` 是占位未实现;真要高质量放大需接 Real-ESRGAN。

## ⚠️ 额外注意事项 / 易踩的坑(代码里真实存在,docs 未必写)

### 配置文件有"误导"——别被带偏
- **`.env.example` 默认 `POD_MATTING_PROVIDER=gptimage`(需 key),但实际运行的 `backend/.env` 是 `pillow`(离线)。** 改配置/调试时以**实际 `.env`** 为准,默认就是纯离线 pillow 模式。
- **`.claude/launch.json` 是本地私有配置**(含机器相关绝对路径),已修正为本项目路径(`D:/podsys`、端口 8000)并加入 `.gitignore`(不入库,各人填各自路径)。启动一律用 `backend/.venv/Scripts/python.exe -m uvicorn app.main:app`。
- **数据库/`.env` 路径已锚定到 `backend/`(`config.py` 用 `__file__` 定位),不再依赖启动目录。** 唯一的开发库永远是 `backend/data/podstudio.db`。
  - 历史坑:早期 `data_dir=Path("data")` 是相对启动目录的,从项目根目录启动会在根目录新建一个**空库**,导致"已登录用户突然变成『用户不存在』(401)"。已修复,勿改回相对路径。
  - 若项目根目录出现遗留的 `./data/podstudio.db`(空库),是历史误启动产生的,可安全删除,不要和 `backend/data/` 的真库混淆。

### 印花提取 vs 一键抠图(两个不同功能,别再合并)
- **一键抠图**(`/api/process`,rembg):去背景、留主体(人/物)。
- **印花提取**(`/api/print-extract`):把**产品上**『印刷的图案』单独抠出来(保真,用于套版)。**全本地、不依赖网关**,**不只衣服——枕头/袋子/杯子等也支持**。流程(`services/design_extract.py` 的 `extract_design`)=**①框出产品本体:衣服→cloth-seg(`u2net_cloth_seg`,排除人/皮肤);非衣服→通用 rembg(`u2net`)→ ②mask 小幅腐蚀(只削轮廓,不伤袖子/边缘印花)→ ③产品内去『主导材质色』(白/黑/任意),留差异大的像素=印花 → ④连通块面积过滤 + 自动裁剪**。缩小图(1000px)算 mask,再放大套回全分辨率原图。
  - **演进史(别走回头路)**:踩过一串坑——写死"去白色"黑衣失败、"去边缘色"框带皮肤残留、vision 定位不准/依赖网关、rembg-on-crop 对平面印花出雾、**大幅腐蚀砍袖子**、**开运算砍细线印花**、**fold-rejection 砍浅色印花**。最终=**分割模型框本体 + 去主导材质色 + 小腐蚀 + 连通块过滤**。**绝不能用"识图再生图"做提取**(会重画成另一张,毁原设计)。
  - 都没分到产品(输入本身就是设计图)→ 退化为 `extract_on_fabric`(整图按边缘色去底)。`method` ∈ garment / product / whole_image / whole_image_fallback。
  - **已知边界(别过拟合)**:深色衣服、枕头等清晰产品都干净;**重褶皱浅色(白)布料的褶皱阴影颜色上和浅色印花分不开,会有残留**——颜色分离的固有难点。**宁留残留也别加"开运算/取最大块/fold-rejection"等启发式**(会损坏印花本身,且过拟合)。要更极致得上真 AI 编辑(edit key)。

### 前端↔后端鉴权(访客自动注册,易引发"用户不存在")
- 前端各页(`frontend/*.html`)无登录界面:首次打开自动 `POST /api/auth/register` 注册一个 guest 用户,把 JWT 存进浏览器 `localStorage` 的 `pod_token`,之后所有请求带 `Authorization: Bearer <token>`。
- **坑**:`pod_token` 指向的用户若在当前数据库里不存在(换了库 / 清了库 / token 过期),后端 `current_user` 返回 401「用户不存在」。处理:浏览器清掉 `localStorage` 的 `pod_token` 后刷新(会重新注册),或排查是不是连错了库(见上条数据库锚定)。

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

启动:`cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000`
流程:注册 `/api/auth/register` → 拿 token → `POST /api/process`(带图)→ 检查返回的
print/mockup/production 三个 URL 可访问(HTTP 200),production 为 30×40cm@300DPI(3543×4724)。
