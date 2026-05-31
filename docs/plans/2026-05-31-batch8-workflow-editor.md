# Batch 8: 可视化工作流编辑器 + 自定义/可保存工作流 + 我的空间 Implementation Plan

**Goal:** 完成平台外壳——用户可在前端拖拽编排 step 成自定义工作流、保存复用、一键运行(对标灵图首页);并补「我的空间」总览。达到 v0.7。

**Architecture:** E1 把 step 注册表暴露元数据 + 支持运行任意 step 序列(复用 jobs 异步);E2 持久化用户自定义工作流(新表);E3 纯前端编辑器(按既定 API 契约);E4 我的空间聚合 + 前端页。集成点(main 路由、db.init_db、新表)Tech Lead 收口。

**复用约定(必读):** 同前几批——`current_user`/`get_db`/`charge_for`/`refund`/`storage`/`web_utils.read_image_or_refund`;workflow 引擎在 `app/services/workflow.py`(`STEP_REGISTRY`、`run_workflow`、`WORKFLOWS`);异步作业 `app/services/jobs.py`(`create_job`/`run_job`)、查询 `/api/jobs/{id}`(需鉴权+owner)。jobs 创建要带 `owner_id`。测试用 conftest `client`/`auth_headers`,**勿改 conftest**。venv `backend/.venv/Scripts/python.exe`。**禁改** main.py/db.py/models_db.py/conftest.py/requirements.txt/他人文件;**禁启** uvicorn;**禁删** data/podstudio.db。新表 `from app.db import Base`。

**API 契约(E3 前端按此对接,E1/E2 据此实现):**
- `GET /api/workflows/steps` → `[{id,label,category,needs_ai,offline}]`(E1)
- `POST /api/workflows/run-custom` multipart:`file`、`steps`(逗号分隔 step id)、`params`(JSON 字符串) → `{job_id,status}`,异步,扣 process,失败退点,owner(E1)
- `POST /api/my-workflows` JSON `{name, steps:[...], params:{}}` → `{id}`;`GET /api/my-workflows` → 列表;`GET /api/my-workflows/{id}`;`DELETE /api/my-workflows/{id}`(E2,均 owner 隔离)
- `GET /api/me/overview` → `{credits, assets, products, shops, jobs, collect_tasks}`(E4)
- 轮询 `GET /api/jobs/{id}`(已有)

**团队拓扑:**
| 角色 | 任务 | 仅碰文件 |
|---|---|---|
| E1 自定义工作流 | step 元数据 + 运行任意 step 序列 | `services/workflow.py`(本批仅你动)、`routers/workflow_custom.py`、`tests/test_workflow_custom.py` |
| E2 保存工作流 | 用户自定义工作流持久化 | `models_workflow.py`、`routers/my_workflows.py`、`tests/test_my_workflows.py` |
| E3 前端编辑器 | 拖拽编排 + 保存 + 运行 + 轮询 | `frontend/workflow-editor.html` |
| E4 我的空间 | 聚合总览 + 前端页 | `services/overview.py`、`routers/me.py`、`frontend/my-space.html`、`tests/test_overview.py` |

**Tech Lead 收口:** main.py 注册 workflow_custom/my_workflows/me 路由;db.init_db import models_workflow。

---

### Task E1 自定义工作流(/api/workflows/steps + /run-custom)
- 在 `services/workflow.py` 加 `STEP_META: dict[str, dict]`,为每个已注册 step 给 `{label, category, needs_ai:bool, offline:bool}`(extract/split/mockup/production/title/variants/compress/seamless);加 `list_steps()` 返回 `[{id,**meta}]`;加 `run_custom(image, steps:list[str], job_id, params)`:校验每个 step ∈ STEP_REGISTRY(非法→ValueError),复用现有 step 执行,返回与 run_workflow 同结构(outputs/steps_run/meta)。
- `routers/workflow_custom.py`:`GET /api/workflows/steps`(公开或 current_user 皆可,建议 current_user);`POST /api/workflows/run-custom`(multipart file+steps(逗号分隔)+params(JSON),`charge_for("process")`+db,读图失败/空 steps/非法 step → 退点+400,合法则 create_job(owner_id)+后台 run_custom,失败退点;返回 {job_id,status})。注意:prefix 不要与现有 `/api/workflows`(workflow.py 的 router)冲突——用同前缀不同路径即可,但**你新建独立 router 文件**,TL 会一起 include。
- 测试:GET steps 含 8 个 step 且字段齐;run-custom steps="extract,mockup,compress" 异步→ job done 且 outputs 有 compressed.*;非法 step→400 退点;空 steps→400;未登录 401。

### Task E2 保存工作流(/api/my-workflows)
- `models_workflow.py`:`SavedWorkflow(id pk, owner_id FK users.id index, name str, steps JSON(list), params JSON(dict), created_at)`。`from app.db import Base`。
- `routers/my_workflows.py`(均 current_user):POST 建(校验 steps 非空 list)、GET 列本人、GET/{id}(非本人404)、DELETE/{id}(非本人404)。
- 测试:建→列表含→取详情→删除后404;steps 空→400;他人 GET/DELETE→404;未登录401。测试顶部确保表存在(`from app.models_workflow import SavedWorkflow; from app.db import engine,Base; Base.metadata.create_all(engine)`)。

### Task E3 前端可视化编辑器(frontend/workflow-editor.html)
- 单页:左侧「可用节点」列表(GET /api/workflows/steps 渲染,可点击/拖入);中间「我的流水线」有序列表(可增删、上下移调序);右侧上传输入图 + 运行按钮 + 结果区。
- 顶部:访客自动鉴权(复用 index.html 的 ensureToken 模式:localStorage pod_token,无则 /api/auth/register 注册 guest,所有请求带 Bearer);余额显示。
- 「保存为工作流」:POST /api/my-workflows(name+steps);「我的工作流」下拉:GET /api/my-workflows 加载回填。
- 「运行」:POST /api/workflows/run-custom(file+steps 逗号分隔)→ 拿 job_id → 轮询 GET /api/jobs/{id} 到 done → 展示 result.outputs 里的图片 + steps_run。
- 纯静态 HTML+JS(可用原生拖拽或上下移按钮,优先简单可用);风格与现有 index.html/editor.html 一致(深色)。无需 pytest。

### Task E4 我的空间(/api/me/overview + frontend/my-space.html)
- `services/overview.py`:`overview(db, user) -> dict`:统计该用户 `credits` + 各表计数(Asset/Product/Shop/Job/CollectionTask,用 `select(func.count()).where(owner_id==user.id)`)。
- `routers/me.py`:`GET /api/me/overview`(current_user)。
- `frontend/my-space.html`:访客自动鉴权 + 调 overview 展示卡片(余额/素材/商品/店铺/作业/采集任务数),链接到 index/editor/workflow-editor。
- 测试:新用户 overview credits=100 且各计数=0;建 1 商品后 products==1;未登录 401。

---

## 集成与验收(Tech Lead)
1. db.init_db import models_workflow;main.py 注册 workflow_custom/my_workflows/me 3 路由。
2. `pytest -q` 全绿。
3. 代码评审 agent 过 diff,修 P0/P1。
4. 真实 HTTP 冒烟 + 合并 master。
