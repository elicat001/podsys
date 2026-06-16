# PODStudio / 灵犀POD — POD 设计工作站(自研版)

对标「灵图 POD / LingVisions」的一站式 **POD(按需印制)设计工作流系统**:
**采集找图 → AI 作图 → 套图 / 生产文件 → 图生视频**,目标打通到「一键上架」。

> **进度速览**:**找图、作图、套图 / 生产文件、图生视频 均已打通可用**;
> 平台底座(鉴权 / 计费 / 异步队列 / 对象存储 / 我的空间)也已就绪。
> **下一步重点 = 「商品 / 上架」**(商品库 · 店铺管理 · 一键上架,目前留接口)。

## 系统分层(现状)

| 层 | 模块 | 状态 |
|---|---|---|
| ① 采集层(找图) | 浏览器插件多平台采集(Temu / Amazon / Shopee / 美客多 / TikTok)→ 采集箱 → 同步入库「找图」 | 🟢 已实现(`extension/` 插件 + 后端同步) |
| ② AI 图像层(作图) | 抠图 / 印花提取 / 文生图 / 改图换装换背景 / 放大提质 / 转矢量 / 侵权检测 / 标题提取 / 图裂变 · 风格转绘 · 梗图 · 去水印 | 🟢 已实现(可插拔 Provider,本地引擎打底、可切云网关) |
| ③ 套图 + 履约 | 商品套图 mockup(+ 批量替换印花)/ 生产文件导出(多格式 · CMYK · 出血) | 🟢 已实现 |
| ④ 图生视频 | 商品展示视频(智谱 CogVideoX-3 真视频 / 有声,可插拔;无 key 兜底 GIF)+ 智能识别镜头脚本 + 案例库 | 🟢 已实现 |
| ⑤ 商品 / 上架 | 商品库 / 店铺管理 / 一键上架到各平台 | 🔲 **待做(接口预留,下一步重点)** |
| ⑥ 平台底座 | 多租户鉴权(JWT)/ 计费(点数)/ 异步队列(Celery + 独立 Redis)/ 存储(MySQL + 可选 MinIO/S3 对象存储)/ 我的空间(配额 · 回收站 · 素材库)/ Alembic 迁移 | 🟢 已实现 |

## 核心主线(已跑通)

上传图片 → AI 抠图 / 提取印花(自动裁剪到内容)→ 放大提质 → 套图预览(贴到产品模板)→
导出生产文件(30×40cm@300DPI 起,多格式)。产物自动进「我的空间」,可下载 / 管理 / 回收站。

## 技术栈

- **后端**:Python 3 + FastAPI + Uvicorn;耗时作业走 **Celery + 独立 Redis(6380)** 异步队列,前端轮询 `/api/jobs/{id}`
- **图像**:Pillow 打底 + **本地引擎**(rembg 抠图 / Real-ESRGAN 超分 / vtracer 转矢量 / OpenCV);AI 类可切 OpenAI 兼容网关或第三方 API
- **视频**:可插拔 Provider —— 默认本地兜底 GIF(无 key 也出东西);接智谱 **CogVideoX-3** 出真 · 有声视频
- **前端**:**Vue 3 SPA**(`frontend-vue/`:Vite + vue-router + pinia + element-plus,深色风格;**需登录**)
- **数据**:**MySQL 8**(utf8mb4,`POD_DATABASE_URL` 必填)+ **Alembic** 迁移管 schema
- **存储**:本地盘默认;可切 **MinIO/S3 对象存储**(`POD_STORAGE_BACKEND=s3`:作业产物镜像 + `/files` 本地缺失自动回源 + 按天 retention 释放本地盘)。生产已部署 MinIO
- **鉴权 / 计费**:JWT + pbkdf2 密码哈希;点数(credits)计费

## 快速开始

前置:**Python 3.10+**(开发用 3.12)+ **MySQL 8**。

```powershell
cd backend
python -m venv .venv                 # 建虚拟环境(隔离依赖)
.\.venv\Scripts\Activate.ps1         # 激活
pip install -r requirements.txt      # 装依赖(含 rembg/onnxruntime/opencv,首次较慢)

copy .env.example .env               # 复制环境变量模板,再按下面说明编辑 .env

# 启动(开发热重载)
uvicorn app.main:app --reload --port 10000
# 打开 http://127.0.0.1:10000
```

> 🗄️ **必须先有 MySQL 8**(项目已全面转 MySQL,不再支持 SQLite):建 `podsys` 库 + 用户后,
> 在 `.env` 填 `POD_DATABASE_URL=mysql+pymysql://podsys:密码@127.0.0.1:3306/podsys?charset=utf8mb4`
> (没填后端会直接报错)。建库 / 授权 / 测试库命令见 [`backend/.env.example`](backend/.env.example)。

> ⚙️ **建议在 `.env` 填好 `POD_OPENAI_API_KEY`**(用兼容网关再填 `POD_OPENAI_BASE_URL`):
> 文生图 / 改图换装 / 印花提取(默认 AI 重绘)/ 多种作图工具**需要 key**,没 key 会 502 或降级兜底。
> 抠图默认用本地 **`rembg`**(离线免费,**首次自动下载 ~170MB 模型**,需一次联网);放大默认 Lanczos。
> 套图 / 导出生产文件 / 转矢量等本地能力始终离线可用。

> 🎬 **图生视频要真视频**:填 `POD_VIDEO_PROVIDER=cogvideox` + `POD_VIDEO_API_KEY`(智谱开放平台);
> 不填则默认本地兜底出 GIF(离线可用)。

跑测试(用 TestClient,不需起 uvicorn;但需 MySQL + 一个 `podsys_test` 隔离库,见 `.env.example`):
`.\.venv\Scripts\python.exe -m pytest -q`

> 📌 **可选的本地超分模型**:真"提质放大"需把 Real-ESRGAN 模型 `realesr_x4v3.onnx`(~4.7MB)放到 `backend/models/`。
> 该文件**不在仓库里**,缺失时自动降级 Lanczos(仍可用)。下载/放置说明见 [`backend/models/README.md`](backend/models/README.md)。

## AI / 视频 引擎切换(本地优先,可切云 API)

每类能力都是**可插拔 Provider**,改 `backend/.env` 一行即可切换,业务代码不动。
**代码默认全部走本地/离线**(`config.py` 里 matting/upscale 默认 `pillow`、video 默认 `local`),无 key、无模型也能跑通整条流水线。

**抠图 `POD_MATTING_PROVIDER`:**
- `pillow` —— 代码默认。按图四角估计背景色 + 颜色距离抠图,纯 CPU、无需模型;仅适合纯色/干净背景。
- `rembg` —— **推荐**。本地开源神经网络(U2Net),复杂背景/人物也干净,离线免费;**首次调用自动下载 ~170MB 模型**。
- `gptimage` —— 走 OpenAI 兼容网关的 gpt-image(透明背景),需 `POD_OPENAI_API_KEY`。
- `api` —— 通用第三方 HTTP 抠图 API,配 `POD_MATTING_API_URL` / `POD_MATTING_API_KEY`。

**放大 `POD_UPSCALE_PROVIDER`:**
- `pillow` —— 代码默认,Lanczos 重采样,纯 CPU。
- `realesrgan` —— 本地 AI 超分『真提质』(Real-ESRGAN SRVGG x4 onnx),需 `backend/models/realesr_x4v3.onnx`(不入库,缺失自动降级 Lanczos)。
- ⚠️ **放大绝不要用 gpt-image**:它是生成模型,会重绘像素、改动印花,毁掉生产文件。

**图生视频 `POD_VIDEO_PROVIDER`:**
- `local` —— 代码默认,本地 Ken-Burns/轮播出 GIF,离线兜底。
- `cogvideox` —— 智谱开放平台 CogVideoX-3 真视频(有声 mp4),需 `POD_VIDEO_API_KEY`。

**文生图 / 改图**:`POST /api/generate` / `POST /api/edit`,走网关 gpt-image,需 key。
**标题提取**:`POST /api/studio/title`,走网关文本模型;传图则识图生成 SEO 标题,无 key 降级本地规则(不扣点)。

> 另有一批**全本地、不依赖网关**的能力:印花提取(`/api/print-extract`,无 key 时本地保真兜底)、
> 转矢量图(`/api/vectorize`,vtracer)、套图 mockup、导出生产文件等 —— 离线即可用。

## 对象存储 / 部署 / 运维

- **对象存储(可选)**:默认本地盘;`POD_STORAGE_BACKEND=s3` 切 MinIO/S3。桶内按媒体类型分目录(`images/` 与 `videos/`),作业收尾镜像、`/files` 本地缺失回源、retention 按天清本地缓存释放盘。配置见 [`backend/.env.example`](backend/.env.example)。
- **部署**:生产在服务器上一条命令 `bash deploy.sh`(拉代码 → 构建前端 → 迁移 → 重启 → 体检,零停机)。
- **详细运维**(Celery worker / 独立 Redis / MinIO / retention timer / Alembic 改表流程 / 红线约定)见 [`CLAUDE.md`](CLAUDE.md)。
