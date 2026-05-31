# Batch 5: 作图工具矩阵 + 侵权检测升级 Implementation Plan

**Goal:** 把对标灵图「作图」的缺口工具批量补齐(印花设计/图案处理/套图标题来图/侵权升级),并让其可注册进工作流引擎复用,达到 v0.5。

**Architecture:** 每组工具 = 一个 service 模块(纯/调 gpt-image)+ 一个 router。gpt-image 类工具复用 `app/ai/openai_image.py`,沿用既有「`charge_for` 预扣 → 失败 `refund` 退点 → 502」范式。无 key 时端点 502 / 服务降级,测试只验证管线(鉴权/扣点/退点/路由/降级),不验证真实 AI 输出。集成点(main.py 注册 4 路由、workflow.py 注册新 step)由 Tech Lead 收口。

**Tech Stack:** FastAPI、gpt-image-1、Pillow、pytest。

**复用约定(所有 agent 必读):**
- gpt-image:`from app.ai.openai_image import OpenAIImageClient`;`.generate(prompt,size)` / `.edit(image, prompt, mask=None, size, background)` / `.remove_background(image)`。无 key 时构造即抛 RuntimeError。
- 计费:`from app.services.billing import charge_for, refund`;端点签名 `user: User = Depends(charge_for("<op>"))`, `db: Session = Depends(get_db)`;AI 失败时 `refund(db, user, "<op>")` 再 `raise HTTPException(502, ...)`。
- 鉴权:`from app.auth import current_user`。读图:`Image.open(io.BytesIO(await file.read())); img.load()`,失败 400(并退点)。
- 存储:`from app import storage`;`storage.new_job_id()`、`storage.output_path(jid,name)`、`storage.output_url(jid,name)`。
- 路由风格参考 `app/routers/design.py`、`app/main.py` 的 generate/edit。
- venv:`backend/.venv/Scripts/python.exe`;测试 `python -m pytest tests/<your_test>.py -q`;**禁止**启动 uvicorn、删 `data/podstudio.db`、改 main.py / workflow.py / requirements.txt / 他人文件 / conftest.py。

**团队拓扑(并行 4 + 评审 1):**
| 角色 | 任务 | 仅碰文件 |
|---|---|---|
| E1 印花设计 | 图裂变/元素融合/风格转绘/梗图印花 | `services/design_tools.py` `routers/design_tools.py` `tests/test_design_tools.py` |
| E2 图案处理 | 扩图/去水印(gpt-image)+ 裁剪压缩(离线 Pillow) | `services/image_tools.py` `routers/image_tools.py` `tests/test_image_tools.py` |
| E3 套图标题来图 | 标题提取(gpt文本)/模特试衣/宠物换装/合照 | `services/studio_tools.py` `routers/studio_tools.py` `tests/test_studio_tools.py` |
| E4 侵权升级 | TRO+艺术家版权库(本地种子库)+ 深度检索报告 | `services/ip_guard.py` `routers/ip_guard.py` `tests/test_ip_guard.py` `backend/app/data_seed/tro_seed.json` |

**Tech Lead 收口:** main.py 注册 4 路由;workflow.py 选若干注册成 step;requirements 如需(尽量不加新依赖)。

---

### Task E1 印花设计工具(gpt-image edit 系)
路由前缀 `/api/design-tools`,各端点接图 + 可选参数,op 用 `"edit"`(扣 4 点):
- `POST /variants` 图裂变:对输入印花用 gpt-image edit 生成 N 个卖点变体(prompt 模板:保持主体、变换配色/排版/风格)。无 key → 502+退点。
- `POST /fuse` 元素融合:两张/一张图 + prompt 融合出新爆款。
- `POST /restyle` 风格转绘:prompt 控制风格(如 Temu 2D flat)。
- `POST /meme` 梗图印花:加梗文案/排版。
每个端点成功返回 `{job_id, image_url(s)}`,产物存 `storage.output_path`。
测试:无 key 时各端点 401(未登录)/ 502(已登录但无 key)且**退点**(余额不变);路由存在且参数校验。

### Task E2 图案处理工具
路由前缀 `/api/image-tools`:
- `POST /expand` 扩图(outpaint via gpt-image edit,op="edit")— 无 key 502+退点。
- `POST /dewatermark` 去水印(gpt-image edit,op="edit")。
- `POST /compress` 裁剪压缩(**离线 Pillow**,op="process"扣2):入参 target_w/target_h/quality/format(png|jpeg|webp),返回压缩后文件 + 原始/压缩后字节数。**这个必须真实可跑并测真实行为**(无需 key)。
测试:compress 真实改变尺寸/体积/格式(重点覆盖);expand/dewatermark 无 key 502+退点。

### Task E3 套图&标题&来图定制
路由前缀 `/api/studio`:
- `POST /title` 标题提取(gpt **文本**:用 `from openai import OpenAI` 调 chat/responses 生成电商标题;无 key 降级为基于参数的占位标题,op="generate"扣5但若降级则不扣——简单起见无 key 直接 200 返回占位且**不扣点**,有 key 才扣)。返回 `{title, keywords}`。
- `POST /tryon` 模特试衣(gpt-image edit,op="edit",无 key 502+退点)。
- `POST /pet-costume` 宠物换装(gpt-image edit)。
- `POST /group-photo` 合照(gpt-image edit/generate)。
测试:title 无 key → 200 占位且不扣点;tryon 无 key → 502+退点;路由存在。

### Task E4 侵权检测升级(TRO + 版权库 + 报告)
- `backend/app/data_seed/tro_seed.json`:种子库,若干条 `{name, brand, type:'tro'|'artist', dhash?, keywords:[...]}`(可含已知 IP 的 dhash 占位与关键词)。
- `services/ip_guard.py`:加载种子库;`scan(image, title?) -> report`:① 用 `services/phash.dhash` + `hamming` 与库中带 dhash 的条目比对(结构相似命中);② 标题/关键词命中(若给 title)。产出**深度检索报告** dict:`{risk: safe|review|high, matches:[{name,brand,type,reason,distance?}], checked:{visual:bool,keyword:bool}, advice}`。
- `routers/ip_guard.py` 前缀 `/api/ip-guard`:`POST /scan`(图 + 可选 title,需登录,op="process"扣2,失败退点)、`GET /library`(列种子库条数与类型分布)。
测试(**离线真实可跑**):构造一张与种子库某条 dhash 相同的图 → high;无关图 → safe;标题含已知品牌关键词 → 命中 keyword。

---

## 集成与验收(Tech Lead)
1. main.py 注册 design_tools/image_tools/studio/ip_guard 4 路由。
2. workflow.py:把 `variants`(裂变)、`title`、`tryon` 等可串联的注册成新 step(无 key 降级)。
3. `pytest -q` 全绿。
4. 代码评审 agent 过 diff,修 P0/P1。
5. 合并 master。
