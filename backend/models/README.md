# backend/models/ —— 本地超分模型(可选)

这个目录放**本地快速超分(放大提清晰)**用的模型文件。

> ⚠️ **大文件不进 git 仓库**(见根 `.gitignore`)。所以你刚 clone 下来时,这个目录基本是空的
> (只有这份 README)。**缺文件不影响项目运行** —— 代码会自动降级到 **Lanczos**(纯 CPU、无需模型),
> 功能照常,只是放大清晰度一般。只有想要更好的放大效果时,才需要把模型下载放进来。

## 需要哪个文件

| 文件名(必须完全一致) | 对应引擎 `POD_UPSCALE_PROVIDER` | 特点 | 下载来源 |
|---|---|---|---|
| `FSRCNN_x4.pb` | `fsrcnn` | 轻量 CNN,**毫秒级**,比 Lanczos 略清晰(边缘更锐) | OpenCV 官方超分模型:[Saafke/FSRCNN_Tensorflow → models/FSRCNN_x4.pb](https://github.com/Saafke/FSRCNN_Tensorflow/blob/master/models/FSRCNN_x4.pb)(约 40KB) |

## 怎么启用

1. 把 `FSRCNN_x4.pb` 下载放进**本目录**(`backend/models/`)。
2. 在 `backend/.env` 里切换引擎(默认是 `pillow`):
   ```ini
   POD_UPSCALE_PROVIDER=fsrcnn   # 留空/pillow=Lanczos(无需模型)
   ```
3. 重启后端生效。

不放文件就保持默认 `pillow`,一切正常。

## 想换文件名/换模型?

文件名由 `app/config.py` 的 `upscale_fsrcnn_model` 决定,无需改代码 —— 在 `.env` 覆盖即可:
```ini
POD_UPSCALE_FSRCNN_MODEL=别的名字.pb
```
路径固定为本目录(`backend/models/<文件名>`)。
