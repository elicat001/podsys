# backend/models/ —— 本地超分模型(可选)

这个目录放**本地 AI 超分(图像提质)**用的模型文件。

> ⚠️ **大文件不进 git 仓库**(见根 `.gitignore`)。刚 clone 时本目录基本是空的(只有这份 README)。
> **缺文件不影响运行** —— 代码自动降级到 **Lanczos**(纯 CPU、无需模型),功能照常,只是提质效果一般。
> 想要更好的提质效果时才需要把模型下载放进来。

## 需要哪个文件

| 文件名(必须完全一致) | 对应引擎 `POD_UPSCALE_PROVIDER` | 特点 | 来源 |
|---|---|---|---|
| `realesr_x4v3.onnx` | `realesrgan` | Real-ESRGAN 精简版(SRVGG general-x4v3,onnx,~4.7MB),CPU ~几秒,去噪 + 复原细节,真提质 | Real-ESRGAN 项目 `realesr-general-x4v3` 模型的 onnx 版,重命名为本文件名:[xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) |

## 怎么启用

1. 把 `realesr_x4v3.onnx` 放进**本目录**(`backend/models/`)。
2. 在 `backend/.env` 切换引擎(默认 `pillow`):
   ```ini
   POD_UPSCALE_PROVIDER=realesrgan   # 留空/pillow=Lanczos(无需模型)
   ```
3. 重启后端生效。不放文件就保持默认 `pillow`,一切正常。

## 想换文件名/换模型?

文件名由 `app/config.py` 的 `upscale_realesrgan_model` 决定,无需改代码 —— 在 `.env` 覆盖即可:
```ini
POD_UPSCALE_REALESRGAN_MODEL=别的名字.onnx
```
路径固定为本目录(`backend/models/<文件名>`)。
