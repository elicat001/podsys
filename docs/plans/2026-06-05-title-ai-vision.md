# 标题提取:接通真 AI(gpt-5.4-mini 识图)+ 计费对齐 + 本地降级兜底

**日期:** 2026-06-05 ｜ **状态:** 已完成(234 tests green,已并入 `dev`)

**背景:** 标题提取此前永远走本地占位——代码写死调 `gpt-4o-mini`,而网关无此模型 → 恒 503 降级。本批接通网关真实文本/视觉模型,本地规则引擎退为兜底。

## 改了什么
1. **接通真 AI**(`services/studio_tools.generate_title`):改调 `settings.openai_text_model`(默认 `gpt-5.4-mini`)。
   - 网关怪癖:chat 接口**必须 `stream=true`** 才吐内容(非流式返回空 choices)→ 累加 delta;新增 `openai_text_stream` 开关。
   - 修了写死 `gpt-4o-mini`(网关无)导致恒 503 的 bug。
2. **图文模型分离**:新增 `POD_OPENAI_TEXT_MODEL`(≠ `POD_OPENAI_IMAGE_MODEL`),共用同一 key/网关。
3. **vision 识图 + prompt 工程**:传图 → 压 512px JPEG base64 → 视觉模型看图;system prompt 转化导向、明令禁止 "Apparel Collection" 类水货;有关键词则一并作线索。识图比纯文本仅多 ~1.3s。
4. **本地规则引擎升级**(`effects.smart_title`):多模板 + SEO 词库(风格/受众/场合)+ 品类同义词 + 主色调,确定性派生。**纯作无 key / AI 失败时的降级兜底**。
5. **计费对齐**:新增 `title` op = **1 点**(原误用 `generate`=5);降级(`degraded=True`)自动退点 → 本地兜底实际 0;前端按钮显示「扣1点」、删除占位提示文案。

## 验证
- `pytest -q` 234 green(含 2 个新计费测试:AI 扣1 / 降级退回)。
- 线上实测:识图 6.5s、纯文本 5.2s;扣点 100→99;`degraded=false`。

## 未决(等老大拍板)
- 标题走 AI vs 纯本地、每条单价(网关后台登录态才看得到)。技术上两套均已就绪。

## 网关事实(备查)
- base `…/v1` 有文本模型 `gpt-5.4-mini / 5.4 / 5.5 / 5.2-pro / ...`(**无** `gpt-4o-mini`)+ 图像 `gpt-image-2`;`-openai-compact` 变体 chat 调用 503,不可用。
- 模型单价 API key 看不到,需网关后台。
