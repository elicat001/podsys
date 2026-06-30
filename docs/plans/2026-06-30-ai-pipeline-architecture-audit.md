# AI 生成流程架构审计(2026-06-30)

> **性质**:技术债清单 / 长期可维护性审计。**只审计、给抽象建议,不改业务**。逐项推进时各自 `pytest -q` 全绿 + 单独 commit。
> **审计方法**:3 个并行子代理只读通审 `app/ai/`、`app/services/`(视频提示词层)、`app/services/` 非视频 AI + `tasks.py` 编排,按四类反模式分类:① 硬编码 ② 案例驱动修复 ③ 特判逻辑 ④ 规则堆叠。
> **判定准则(老大定的)**:每条想新增的规则先问——「这是需要抽象的新能力,还是只是一个案例?未来 10 个同类问题会不会变成 10 条规则?」会,就别加规则,找更高层抽象。

## 0. 元结论(最有用的一条)

**本仓库反复出现「反模式」与「它的正确抽象」并排共存**:
- `vidu.py` 让模型看图自判品类 ↔ `video.py` 写死 `_SCENE_BY_CAT`;
- `effects.py` 标题用「数据表 + 通用 `_infer`」↔ 同文件 `stylize` 用 `if/elif` 关键词链;
- `print_extract` 有干净的「主引擎→兜底」降级阶梯 ↔ `design_extract` 按 `garment` 分叉 6 处。

→ **整改主路径 = 让落后者收敛到仓库里已有的好模式,而不是发明新架构**。低风险、高确定性。

另一条主线:**多处「正确的可插拔抽象」已经存在并运转良好**(见 §3 不该动清单),说明团队有能力做对——问题是局部退化,不是系统性缺失。

---

## 1. Tier 1 — 最高维护风险(抽象清晰、且会持续增长)

### T1-1 退点/计费真相重复 4 份(**兼有计费正确性隐患**)
- **现状**:`(kind → 退点 op, n)` 在 **4 处各写一遍**:`tasks.py:TOOL_WORKS`、`jobs.py:_KIND_REFUND_OP`、各 router 的 `submit_celery(op=, n=)` 调用点、前端 `tools.js` 的 `cost/costPerN`。
- **危害**:新增工具要在 4 处对齐 op;**漏了或写错 → reaper 对未知 kind 默认按 `edit` 退点 → 静默退错金额、无报错**。CLAUDE.md「碰视频计费先确认四条退点路径」这条警告本身就是该脆弱性的证据。
- **分类**:规则堆叠 + 特判。**10→10?** YES + 计费正确性风险。
- **抽象**:单一 `TOOL_SPECS: dict[str, ToolSpec(work, op, n_field, …)]` 一张表,`run_tool` / `reap_stuck_jobs` / `submit_celery` 都读它;前端 cost 由 `op→COST` 派生。消掉 4 份里的 3 份。
- **风险**:中(动 tasks.py + jobs.py reaper + 各 router 调用点)。需全退点测试护航。

### T1-2 `_SCENE_BY_CAT` + `CATEGORIES` + 提示词内「若是衣物/若是杯子」品类分支(`video.py`)
- **现状**:品类→母帧场景查找表(已 10 行,"卫衣"≈"T恤" 近乎重复),且 `scene_frame_prompt` 提示词里又用散文写了一遍「若是衣物…若是杯子…」的品类分支。
- **分类**:特判 + 硬编码。**10→10?** YES。
- **抽象**:**删表——让模型看图自判品类**。`vidu.py:118` 的 `scene_frame_prompt` 已经是这么做的(「绝不写死品类,由你看图自适应」)。把 `video.py` 收敛到 `vidu.py` 同款;保留可选 `scene` 覆盖参数。
- **风险**:低-中(提示词层;母帧效果需眼验,但 vidu 同款已在线验证)。

### T1-3 `design_extract` 的 `kind=="garment"` 二元分叉,branch 在 6 处
- **现状**:`_print_alpha`/`_flatten_illumination`/腐蚀估计/`lo=37 if garment else 12`/detail-mask/`method=="garment"` rembg-merge —— 同一个裸字符串 `kind` 分叉 6 次,各带一组共调常数。第三类材质(玻璃/金属/网纱)就要再叉 6 处。
- **分类**:特判。**10→10?** YES(分叉点 ×6 / 新材质)。
- **抽象**:`_product_mask` 返回一个**按材质类的 `ExtractStrategy` 策略对象**(`erosion_frac/seed_dist/weak_dist/fine_lo/cleanup()/post_merge()` 等为字段/方法),`extract_design` 调 `strategy.x` 取代 `if kind=="garment"`。新材质 = 1 个策略子类 + 1 个分类分支,不是 6 处编辑。
- **⚠ 重要边界**:**常数本身是不可约的经验值**(CLAUDE.md 史料证明:每次试图用「原则」替代这些阈值——开运算/取最大块/fold-rejection——都让效果**退化**)。所以**只收敛「分叉结构」、把散落常数归到策略对象的字段上,绝不动数值、绝不再加启发式**。
- **风险**:中-高(动核心提取管线,效果难单测、靠眼验;改坏会回归)。**建议最后做、且小步**。

---

## 2. Tier 2 — 真重复 / 漂移(中等工作量)

### T2-4 视频 Provider 是「分叉」而非「统一」
- `base.py` 只有 `MattingProvider`/`UpscaleProvider`;`video.py` 与 `vidu.py` **各自**定义 `VideoProvider`/`ViduVideoProvider`(签名还不同)+ 各自 `get_*_provider()` 工厂 + `tasks.py` 里各自一个 `_work_*`。**加第 3 家视频厂商 = 一整条新竖井**,与代码自己注释的「换厂商业务不动」矛盾。
- **抽象**:`base.py` 里一个统一 `VideoProvider` Protocol(规范化签名,如 `VideoRequest` dataclass)+ 一个工厂 + 一个 `_work_video`;厂商差异(多分镜/音频/强项动作)收到 provider 的**能力描述符**后面。
- **风险**:中-高(动 tasks.py 两个 `_work_*` 合一)。结构正确但工作量大。

### T2-5 地区/语言表定义 3 份且已漂移
- `_REGION_HINT`/`REGION_STYLE`/`LANGUAGES` 在 video.py + vidu.py 各写,值已分叉(video 有「无对白」、vidu 没有;"中文"→"中国" 不一致)。
- **抽象**:单一 `LANGUAGE_MARKETS`(language→{region, person_region, tone})源头表 + 一个插值模板;两端共用。
- **风险**:低。

### T2-6 4 个提示词生成器的「硬性要求」墙重复且漂移
- `video_wizard`/`video_describe`/`vidu_wizard`/`vidu_script` 各自重写「任务驱动 / 去僵硬 / 真实运动幅度+接触+重力 / 印花保真」——同 4 个主题在 4 文件各措辞一遍。`video_continuity.py` 已证明集中化可行,**只是没集中完**。
- **抽象**:把这 4 个复现主题提升为 `video_continuity.py` 里的具名共享常量(如 `TASK_DRIVEN_GUIDE`/`LIVELINESS_GUIDE`/`MOTION_PHYSICS_GUIDE`/`PRINT_FIDELITY_GUIDE`),生成器 compose。builder 只剩真正独有的(输出 JSON schema、单镜 vs 多镜)。
- **风险**:低-中(提示词层;需眼验不跑偏)。

### T2-7 错误分类靠字符串嗅探(随厂商增长)
- `openai_image._is_capacity_error`、`tasks._mufra_permanent` 都用子串列表匹配 `str(exc)` 判容量错/永久错。每接一个新网关/新厂商措辞就要加串;对改写/i18n 脆弱(`" 401"`/`"code: 400"` 当子串)。
- **抽象**:`ai/` 层抛**带类型/状态码的异常**(`PermanentAIError`/`TransientAIError` 或 `.status_code`),上层按类型判;字符串嗅探只作最后兜底。
- **风险**:中(动 ai/ 层异常契约 + 退避逻辑)。

---

## 3. Tier 3 — 清理 / 低风险

- **T3-8 `STORY_TEMPLATES`(`video_templates.py`,94 行)= 死代码**:生产无人用(只有测试 pin),且内嵌已废弃的「出门/咖啡店」单一文化。→ **删掉它 + 对应测试**,只留 `default_scenes()`。未来类目融合作为单独任务,而非留 100 行陈旧数据。**风险:低**。
- **T3-9 `effects.stylize` 的 `if/elif` 关键词链**(每加一种风格 +1 分支)→ `STYLE_RECIPES: dict[关键词集, 变换函数]` 注册表迭代(同文件 `colorway_variants`/`_TITLE_*` 已是这模式)。**风险:低**。
- **T3-10 `ip_guard` 直接 `import openai` + 内联 prompt**,绕过 `ai/` 层 → 违反「严禁在 services 写死某家 API」红线。→ 走 `ai/` 的视觉 provider 接口。**风险:低-中**。
- **T3-11 `_DIRECTION_BLOCK` + 枚举的难动作/情境清单 + `_flatten_storyboard` 3 条正则栈** → 收敛为**原则**;per-incident 提示词修正当作**版本化数据/few-shot**,别再 append 内联子句。`_flatten_storyboard` 学 15s 路径(服务端合成、不信模型格式)或塌成一条宽容正则。**风险:低,但属提示词质量、主观**。
- **T3-12 video.py/vidu.py 助手重复**(`fit_to_aspect`/`LocalGifProvider`/`gptimage_size`/`_data_url`/`_loads_json`/`_chat` 样板)→ 抽 `video_common.py`/`video_llm.py`。**风险:低**。
- **T3-13 `print_extract` 跑在 `TOOL_WORKS` 之外的专用 task** → 折进 `TOOL_WORKS`、删 `run_print_extract`,统一一条 dispatch 路径(reaper 不必认两种)。**风险:低-中**(与 T1-1 一起做更顺)。

---

## 4. 判定为「真·可接受常量 / 已是好模式」——不要去抽象

| 项 | 为什么不动 |
|---|---|
| `design_extract` 经验阈值(腐蚀/seed/lo 等) | **不可约经验值**;CLAUDE.md 史料证明原则化会退化。只收敛分叉结构(T1-3)、不动数值 |
| `openai_image.VALID_SIZES` | gpt-image-1 真实 API 限制,外部常量 |
| `_GUARD_BLOCK` | 刻意压短、只守真踩过的失败——是 T3-11 的**自律版**,正面榜样 |
| `_AdaptiveLimiter` | 真·可复用抽象(自适应并发原语) |
| `matting._SUBJECT_PROMPT` | 刻意品类无关——其它提示词应学它 |
| `effects._TITLE_*` / `_HUE_NAMES` + `_infer` | 已是数据表 + 通用分发,**目标模式** |
| `default_scenes()` / `_DEFAULT_SCENE_CHAIN` | 刻意中性、不随品类增长,纯数据+纯函数 |
| `print_extract` 降级阶梯 / `_work_gptedit` 参数化复用 / `TOOL_WORKS`+`run_tool` 常规路径 | 可插拔/注册驱动的**范本**,整改应朝它们靠 |
| 连续性 L0–L4 **分层本身** | 4 个有界失败族的分类,非 per-incident;只有其内嵌**示例清单**有增长风险(归 T3-11) |

---

## 5. 推进建议(供老大排期)

- **最安全高价值先做**:T1-1(TOOL_SPECS,治静默退错点)、T1-2(品类表收敛 vidu 同款)、T2-5(语言表合一)、T2-6(提示词主题集中化)、T3-8(删死代码)、T3-9(stylize 注册表)、T3-13(print_extract 折进注册表)。
- **结构正确但工作量/风险大、单独评估**:T2-4(视频 provider 统一)、T1-3(ExtractStrategy)、T2-7(异常类型化)。
- **主观/提示词质量、择机**:T3-11、T3-10、T3-12。
- 每项独立 commit + `pytest -q` 全绿;动核心管线(T1-3 / T2-4)前先单独确认范围与回归面。
