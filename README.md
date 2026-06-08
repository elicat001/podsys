# PODStudio — POD 设计工作站(自研版)

对标「灵图 POD / LingVisions」的一站式 POD(按需印制)设计工作流系统。

## 系统分层(目标全景)

| 层 | 模块 | 状态 |
|---|---|---|
| ① 采集层 | Chrome 插件抓取 Temu/Amazon/Etsy/TikTok 高清图 | 🔲 规划(`extension/`) |
| ② AI 图像层 | 抠图 / 印花提取 / 文生图 / 换装换背景 / 无损放大 / 侵权检测 | 🟡 MVP(可插拔 Provider) |
| ③ 设计工作流 | 套图 mockup / DIY 编辑器 / 多图裁剪 / 多联画 | 🟡 MVP(套图合成) |
| ④ 商品/上架 | 商品库 / 店铺管理 / 一键上架 | 🔲 留接口 |
| ⑤ 履约层 | 生产图生成 / 工厂对接 | 🟡 MVP(导出生产文件) |
| ⑥ 平台底座 | 多租户 / 计费 / 任务队列 / 存储 / 鉴权 | 🟡 MVP(本地存储+任务模型) |

## MVP 主线(已实现/在建)

上传图片 → AI 抠图 → 提取印花(自动裁剪到内容) → 无损放大 → 套图预览(贴到产品模板) → 导出生产文件

## 技术栈

- 后端:Python 3 + FastAPI + Uvicorn
- 图像:Pillow 打底 + **本地引擎**(rembg 抠图 / Real-ESRGAN 超分 / vtracer 转矢量 / OpenCV);可切换 OpenAI 兼容网关或第三方 API
- 前端:静态 HTML/JS(MVP);后续可换 Next.js + Fabric.js 编辑器
- 存储:本地文件(MVP);后续 S3/MinIO/OSS

## 快速开始

前置:**Python 3.10+**(开发用 3.12)。

```powershell
cd backend
python -m venv .venv                 # 建虚拟环境(隔离依赖,Java 的话类比"项目专属 JDK+依赖")
.\.venv\Scripts\Activate.ps1         # 激活;之后命令都在虚拟环境里跑
pip install -r requirements.txt      # 装依赖(含 rembg/onnxruntime/opencv,首次较慢)

copy .env.example .env               # 复制环境变量模板,再按下面说明编辑 .env

# 启动(开发热重载)
uvicorn app.main:app --reload --port 10000
# 打开 http://127.0.0.1:10000
```

> ⚙️ **强烈建议立刻在 `.env` 填好 `POD_OPENAI_API_KEY`**(用兼容网关再填 `POD_OPENAI_BASE_URL`):
> 文生图 / 图生图改图 / 换装换背景 / 印花提取(默认 AI 重绘)/ 作图工具等**很多功能都要 key**,
> 没 key 会 502 或降级兜底。
> 抠图默认用本地 **`rembg`**(离线免费,**首次抠图自动下载 ~170MB 模型**,需一次联网);放大默认 Lanczos。
> 套图 / 导出生产文件 / 转矢量等本地能力始终离线可用。引擎切换详见下方「AI 引擎切换」。

跑测试(用 TestClient,不需要起服务):`.\.venv\Scripts\python.exe -m pytest -q`

> 📌 **可选的本地超分模型**:真"提质放大"需把 Real-ESRGAN 模型 `realesr_x4v3.onnx`(~4.7MB)放到 `backend/models/`。
> 这个文件**不在仓库里**,缺失时自动降级 Lanczos(仍可用)。
> 下载地址与放置/启用说明见 [`backend/models/README.md`](backend/models/README.md)。

## AI 引擎切换(本地优先,可切云 API)

每类能力都是**可插拔 Provider**,改 `backend/.env` 一行即可切换,业务代码不动。
**代码默认全部走本地/离线**(`config.py` 里 matting/upscale 默认 `pillow`),无 key、无模型也能跑通整条流水线。

**抠图 `POD_MATTING_PROVIDER`:**
- `pillow` —— 代码默认。按图**四角估计背景色 + 颜色距离**抠图(`matting.py`),纯 CPU、无需模型;**仅适合纯色/干净背景**的产品图,复杂背景/人物抠不干净(主要用于离线跑通和测试)。
- `rembg` —— **推荐**。本地开源神经网络(U2Net),复杂背景/人物也干净,离线免费。依赖**已在 `requirements.txt`**(无需额外 pip);**首次调用自动下载 ~170MB 模型**(需一次联网)。
- `gptimage` —— 走 OpenAI 兼容网关的 gpt-image(`images.edit` + 透明背景),需配 `POD_OPENAI_API_KEY`(用网关再加 `POD_OPENAI_BASE_URL`)。
- `api` —— 通用第三方 HTTP 抠图 API,配 `POD_MATTING_API_URL` / `POD_MATTING_API_KEY`。

**放大 `POD_UPSCALE_PROVIDER`:**
- `pillow` —— 代码默认。Lanczos 重采样,纯 CPU、无需模型。
- `realesrgan` —— 本地 AI 超分『真提质』(Real-ESRGAN SRVGG x4 onnx,去噪+复原细节,~几秒),需 `backend/models/realesr_x4v3.onnx`(不入库,见 [`backend/models/README.md`](backend/models/README.md);**缺失自动降级 Lanczos**)。
- ⚠️ **放大绝不要用 gpt-image**:它是生成模型,会**重绘像素、改动印花**,毁掉生产文件。

**文生图 / 图生图 / 改图**:`POST /api/generate` / `POST /api/edit`,走网关 gpt-image(模型 id 由 `POD_OPENAI_IMAGE_MODEL` 配置,网关支持 `gpt-image-2`),需 key。

**标题提取**:`POST /api/studio/title`,走网关**文本模型**(`POD_OPENAI_TEXT_MODEL`,默认 `gpt-5.4-mini`);传图则**识图**生成吸引人的 SEO 标题,无 key/失败自动降级本地规则引擎(不扣点)。

> 另有一批**全本地、不依赖网关**的能力:印花提取(`/api/print-extract`,无 key 时本地保真兜底)、
> 转矢量图(`/api/vectorize`,vtracer)、套图 mockup、导出生产文件等 —— 离线即可用。
