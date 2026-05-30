# PODStudio 采集助手(浏览器扩展)

从 Temu / Amazon / Etsy / TikTok 商品页扫描主图,把缩略图 URL 升级为
**原图(高清)**,一键发送到 PODStudio 工作站做后续处理(印花提取 / 套图 / 上架)。

> Manifest V3 扩展,纯前端,不内置任何账号或密钥。

---

## ⚠️ 合规边界声明(请先阅读)

抓取并复用他人商品图存在 **平台反爬条款** 与 **著作权** 双重法律风险。

- 本扩展**仅可用于「已获授权 / 自有内容」场景**——例如:你整理自己上架
  的商品图、客户书面授权你使用的设计稿、或你拥有版权的素材。
- **严禁**用于抓取第三方受版权保护的图片并商用、规避平台反爬措施、或任何
  违反目标网站服务条款(ToS / robots)的行为。
- 扩展本身只做「URL 字符串变换 + 页面 `<img>` 扫描」,不破解、不绕过任何
  防护。是否合法合规由使用者自行判断并承担全部责任。

---

## 安装(加载已解压扩展)

1. Chrome / Edge 打开 `chrome://extensions`(Edge 为 `edge://extensions`)。
2. 打开右上角 **开发者模式 / Developer mode**。
3. 点 **加载已解压的扩展程序 / Load unpacked**,选择本 `extension/` 目录。
4. 列表中出现「PODStudio 采集助手」即安装成功;固定到工具栏方便使用。

修改代码后,在扩展卡片上点 **刷新/Reload** 重新加载即可。

---

## 使用

1. 打开受支持站点的商品页(`*.temu.com` / `*.amazon.com` / `*.etsy.com` /
   `*.tiktok.com`,见 `manifest.json` 的 `host_permissions`)。
2. 点扩展图标打开 popup,点 **扫描** → content script 扫描页面主图。
3. 在缩略图网格中选一张,点 **发送** → 经 `background.js` 发送到后端。

---

## URL 升级规则(与后端保持一致)

`content.js` 的 `upgradeToHiRes()` 与后端
`backend/app/services/collectors.py` 的 `upgrade_to_hires()` 实现 **同一套规则**,
任何一侧改动都要同步另一侧(后端有 `tests/test_collectors.py` 单测护航):

| 平台      | 规则 |
|-----------|------|
| amazon    | 去掉文件名尺寸段 `._AC_SX466_` / `._SL1500_` 等 → 原图 |
| etsy      | `il_340x270` 这类替换为 `il_fullxfull` |
| temu/tiktok | 去掉缩放 query(`width`、`imageView2`、`x-oss-process` 等) |
| unknown   | 原样返回 |

`collectImages()` 只采集 `naturalWidth >= 500` 的主图,避免收到图标/缩略图。

---

## 与后端 `/api/process` 的联动

- **消息契约(不可破坏)**:popup 向 content script 发
  `{ type: "COLLECT_IMAGES" }`,content script 回
  `{ images: string[], page: string }`。
- popup 选图后向 `background.js` 发 `{ type: "SEND_TO_POD", imageUrl }`。
- `background.js` 负责把图片(或其 URL)提交给 PODStudio 后端的处理接口
  (当前 MVP 走 `/api/process`),返回作业号(`job_id`)。
- 后端把采集到的原图喂给印花提取 / 套图 / 上架流水线。

> 说明:目前后端**没有**专门的采集端点。如需让后端直接接收「图片 URL +
> 来源平台」并复用 `collectors.upgrade_to_hires`,可考虑新增
> `POST /api/collect`(仅建议,见后端 Tech Lead)。
