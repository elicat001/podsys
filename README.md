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
- 图像:Pillow(MVP);可切换 rembg / GPU 模型 / 第三方 API
- 前端:静态 HTML/JS(MVP);后续可换 Next.js + Fabric.js 编辑器
- 存储:本地文件(MVP);后续 S3/MinIO/OSS

## 快速开始

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000
```

## AI Provider 切换(不本地自研,走 API)

抠图 `POD_MATTING_PROVIDER`:
- `pillow`(默认,纯 CPU,无需模型,可离线验证)
- `gptimage` —— **OpenAI gpt-image-1（"image2"）**,需配 `POD_OPENAI_API_KEY`
- `rembg`(需 `pip install rembg onnxruntime`)
- `api`(通用第三方 API,配 `POD_MATTING_API_URL` / `POD_MATTING_API_KEY`)

复制 `backend/.env.example` 为 `.env` 填入 key 即可。

### gpt-image（image2）能做的任务
| 任务 | 入口 | 实现 |
|---|---|---|
| 抠图/去背景 | `/api/process`(provider=gptimage)| `images.edit` + 透明背景 |
| 文生图 | `POST /api/generate` | `images.generate` |
| 图生图/改图/换装/换背景 | `POST /api/edit`(可带 mask)| `images.edit` |

> ⚠️ **无损放大不要用 gpt-image**:gpt-image-1 是生成模型不是超分模型,用它放大会**重绘像素、改动印花**,对生产文件是致命的(尺寸/细节失真)。放大仍走 `POD_UPSCALE_PROVIDER=pillow`(Lanczos)或后续接 Real-ESRGAN。
