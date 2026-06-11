# PODStudio 采集助手(浏览器扩展 · v0.3)

在 **Temu 商品页**一键采集**整页商品**(图 + 标题 + 价格 + 评分 + 来源链接),回传到 PODStudio
**采集箱**(带你的登录态、绕过反爬);在采集页**勾选「同步」**才真正取图入库(此时才占存储),
同步时自动**侵权查重**,入库后进「我的空间 / 找图」按平台分类。Manifest V3,纯前端,不内置账号/密钥。

> 当前聚焦 **Temu**;Amazon/Etsy/TikTok 的 URL 升级规则已内置,后续可扩页面注入。

---

## ⚠️ 合规边界(请先读)
抓取并复用他人商品图存在**平台反爬条款**与**著作权**双重风险。本扩展**仅可用于「已获授权 /
自有内容」场景**;严禁抓取第三方受版权图片并商用、或违反目标站 ToS。扩展只做「页面 `<img>` 扫描 +
URL 字符串变换 + 用你自己的登录态上传」,不破解、不绕过任何防护,合规责任由使用者自负。

---

## 安装(加载已解压扩展)
1. Chrome/Edge 打开 `chrome://extensions`,开启右上角 **开发者模式**。
2. 点 **加载已解压的扩展程序**,选本 `extension/` 目录。
3. 改完代码在扩展卡片点 **刷新/Reload**。

## 使用
1. **先在 PODStudio 网站登录**(线上 `pod.kejing.online` 或本地)。扩展会自动读取登录态;
   点扩展图标的 popup 可确认「已登录 ✓」,并选择 PODStudio 地址(线上/本地)。
2. 打开 **Temu 商品页**(列表页或详情页),右下角出现采集面板。
3. 点 **「全部采集本页」** 批量采集;或**悬停某张商品图**点 **「采集此商品」** 单采。
4. 采集进 **采集页 / 采集箱**(只暂存,零存储)→ 勾选要的项点 **「开始同步」** → 入库到
   **「我的空间 / 找图」(按平台分类)**,同步时自动侵权查重。

## 工作原理(MV3 架构 · 采集→选择→同步)
- `auth.js`(运行在 PODStudio 站点)→ 把 `localStorage.pod_token` 同步到扩展存储,并记录 API 地址。
- `content.js`(运行在 Temu)→ 扫描 temu CDN ≥120px 商品图,**反查整张卡片**抽 标题/价格/评分/来源链接,
  升级高清、注入面板/悬停按钮。
- `background.js`(service worker)→ 带 `Authorization: Bearer <token>` 把商品卡数组 **POST
  `/api/collect-tasks/ingest`**(JSON,只暂存元数据+URL,**不下载图**,采集快且零存储)。
- 用户在采集页**勾选同步** → 后端 `/api/collect-tasks/sync` **服务端取图**入库(此时存储才增长)+ 侵权查重。

> 为什么必须用扩展而不是后端直接抓:Temu 反爬严、图是 JS 渲染、有登录墙/跨域,服务器端抓不到;
> 扩展跑在你已登录的真实浏览器里,天然能拿到高清直链与商品信息。

## URL 升级规则(与后端保持一致 ⚠️)
`content.js` 的 `upgradeToHiRes()` 与后端 `backend/app/services/collectors.py` 的 `upgrade_to_hires()`
是**同一套规则**,改一侧要同步另一侧(后端有 `tests/test_collectors.py` 护航):

| 平台 | 规则 |
|---|---|
| amazon | 去掉文件名尺寸段 `._AC_SX466_`/`._SL1500_` → 原图 |
| etsy | `il_340x270` 替换为 `il_fullxfull` |
| temu/tiktok | 去掉缩放 query(`width`/`imageView2`/`x-oss-process`…) |
| unknown | 原样返回 |

## 已知边界 / 待办
- 当前只在 **Temu** 注入页面 UI;扩到 SHEIN/AliExpress/Pinterest 需各自加平台规则。
- 商品图识别用「temu CDN + 显示宽 ≥120px」启发式(不依赖易变的 Temu CSS 类名),稳但偶尔会多/漏几张;
  在真实 Temu 页面实测后我再按页面结构微调阈值。
- 一次最多采 80 张(防滥采);上传是逐张串行,大批量会稍慢。
