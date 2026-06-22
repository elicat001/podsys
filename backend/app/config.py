"""Runtime configuration (env-driven, with sane defaults)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 锚定到 backend/ 目录(本文件在 backend/app/config.py),使数据库与 .env 的解析
# 不再依赖"启动时的工作目录"。否则从不同目录启动 uvicorn 会读/建不同的 data/podstudio.db,
# 导致"换了个空库 → 已登录用户变成'用户不存在'"。env 变量(POD_DATA_DIR)仍可覆盖。
_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="POD_", env_file=str(_BACKEND_DIR / ".env"), extra="ignore"
    )

    # storage
    data_dir: Path = _BACKEND_DIR / "data"

    # 对象存储(MinIO/S3)。默认 local=纯本地盘(所有对象层函数 no-op,行为与今天一致)。
    # storage_backend=s3 时:作业收尾镜像产物进 MinIO(=存储 of record),/files 本地缺失自动回源,
    # 删除两边都删。MinIO 生产部署在 127.0.0.1:9000(localhost-only,与旁边 Django 物理隔离)。
    # 换阿里云 OSS / 腾讯 COS 只需填它们的 S3 兼容 endpoint/密钥,业务/前端不动。
    storage_backend: str = "local"          # local | s3
    s3_endpoint_url: str = ""               # 如 http://127.0.0.1:9000;官方 AWS S3 留空
    s3_region: str = "us-east-1"
    s3_access_key: str = ""                 # = MinIO 的 MINIO_ROOT_USER(或独立 access key)
    s3_secret_key: str = ""                 # = MinIO 的 MINIO_ROOT_PASSWORD(或独立 secret key)
    s3_bucket: str = "podsys"
    s3_addressing: str = "path"             # MinIO 必须 path-style;AWS 可 virtual
    s3_mirror_uploads: bool = False         # 是否连输入图(uploads)也镜像(默认只镜像产物 outputs)
    s3_retention_days: int = 0              # >0:retention 删早于 N 天且 MinIO 已有副本的本地产物缓存(释放应用盘)。0=不清理

    # 我的空间:每用户默认存储额度(GB)。演示用软上限,超出则拦新作业(submit_celery 413)。
    user_quota_gb: float = 1.0

    # 数据库:**必须**是 MySQL 连接串(项目已全面转 MySQL,不再支持 SQLite),如:
    #   mysql+pymysql://podsys:<pwd>@127.0.0.1:3306/podsys?charset=utf8mb4
    # dev/prod 在 .env 配;测试由 conftest 指向同库名加 _test 的隔离库(见 tests/conftest.py)。
    database_url: str = ""

    # auth
    jwt_secret: str = "dev-secret-change-me-please-set-POD_JWT_SECRET-in-prod"

    # 异步作业(Celery)。broker 指向**独立**的 Redis 实例(默认本地 6380,与旁边
    # Django 项目占用的 6379/db1 物理隔离;生产同样另起一个 6380 实例)。结果存 Job 表,
    # 不用 Celery result backend。celery_eager=true 时任务在调用进程内同步执行(测试用,
    # 无需 broker/worker);conftest 会强制开启,保持 pytest 离线确定性。
    celery_broker_url: str = "redis://127.0.0.1:6380/0"
    celery_eager: bool = False

    # 计费:dev 模式允许自助充值(topup)。生产务必置 false(POD_DEV_BILLING=false)
    dev_billing: bool = True

    # 注册限流(每 IP 每窗口最多注册次数)。默认宽松以便 dev/demo;
    # 生产应调低(如 POD_REGISTER_RATE_LIMIT=5)以堵 guest 刷点(评审 P0-3)。
    register_rate_limit: int = 1000
    register_rate_window_sec: int = 3600

    # AI providers — swap implementation without touching call sites
    matting_provider: str = "pillow"        # pillow | rembg | api | gptimage
    upscale_provider: str = "pillow"         # pillow(Lanczos 兜底) | realesrgan(本地AI·真提质·~几秒)
    upscale_realesrgan_model: str = "realesr_x4v3.onnx"  # Real-ESRGAN 精简版(SRVGG x4,onnx,真提质);缺失降级 Lanczos
    upscale_sr_max_input: int = 768          # AI 超分输入长边上限:大图先缩(控耗时;~768→几秒)
    upscale_sr_threads: int = 0              # Real-ESRGAN onnxruntime 线程数(0=自动取满核;共享服务器应设 2~3 防吃满 CPU 拖垮旁站)

    # third-party API config (used when *_provider == "api")
    matting_api_url: str = ""
    matting_api_key: str = ""

    # OpenAI gpt-image-1 ("image2") — 抠图/文生图/图生图/换装换背景
    openai_api_key: str = ""
    openai_base_url: str = ""                 # 留空走官方;可填代理/Azure 兼容网关
    openai_image_model: str = "gpt-image-1"
    openai_text_model: str = "gpt-5.4-mini"   # 文本模型(标题/文案)。与图片模型分开;本网关有 gpt-5.4-mini 等
    openai_text_stream: bool = True           # 本网关 chat 接口需 stream=true 才吐内容(不流式返回空)。官方 OpenAI 置 false 也可
    openai_timeout: float = 250.0             # 单次调用超时(秒)。本网关 gpt-image 慢且波动,太紧会被掐断→静默回退本地算法(浅色主体被腐蚀=用户报的"白底/像本地")。放宽到 250s 让 AI 真正出图
    openai_max_retries: int = 1               # 重试次数。该网关失败时是"挂满整个超时"型,重试会成倍堆时间;1 次(共2次尝试)覆盖瞬时抖动。需更稳可在 .env 调 POD_OPENAI_MAX_RETRIES=2
    openai_max_concurrency: int = 2           # 同时在飞的 gpt-image 网关调用数上限(进程级信号量)。也用作**商品套图多图并发度**(一个套图任务里同时处理几张)。本网关并发跑多张会让每张都拖过单次超时→整批 APITimeoutError(图裂变 4 路并发实测全挂),故 2=保守(中转站限并发时);若中转站不限并发,可在 .env 调 POD_OPENAI_MAX_CONCURRENCY=10 提速;仍超时可调 1

    # print extraction
    autocrop_padding: int = 8                # px padding around detected content
    bg_tolerance: int = 28                   # pillow matting: color distance threshold
    print_target_px: int = 2048              # 提取结果长边低于此 → 放大到此(超分);0=关闭
    print_max_upscale: float = 3.0           # 单次放大倍数上限(防过度插值变糊)
    # 印花提取引擎:默认走 AI 重绘(gpt-image edit 展平,95% 视觉一致,挂拍/褶皱也能处理);
    # 有 key 才生效,失败/无 key 自动回退本地保真算法(extract_design)。置 false=永远本地。
    print_extract_ai: bool = True
    # 本地标题 OCR:从设计图里识别文字(标语/typography)当标题主体。需系统装 tesseract-ocr 二进制;
    # 缺二进制/包时静默降级(不影响出标题)。测试环境关闭以保离线确定性(conftest 强制 false)。
    title_ocr: bool = True

    # ── AI 图生视频。可插拔 Provider(对齐 matting/upscale 范式):默认 local=本地 GIF 兜底(无需 key);
    # 拟用智谱 CogVideoX-3 —— 把 video_provider 设 cogvideox + 填 video_api_key 即用,业务/前端不动。
    # 不暴露分辨率选择(扣费与分辨率无关,按画幅直接用高分辨率;见 ai/video.py 的 ASPECT_SIZE)。
    video_provider: str = "local"          # local | cogvideox
    video_api_key: str = ""                # 智谱开放平台 key(POD_VIDEO_API_KEY)
    video_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    video_model: str = "cogvideox-3"
    video_quality: str = "quality"         # quality(质量优先) | speed
    video_fps: int = 30                    # 30 | 60
    video_seconds: int = 10                # 5 | 10(老大要 8~10s,取 10)
    video_with_audio: bool = False         # 默认无声(CogVideoX 自带音频=AI 音效乱音,非真人);真人解说走「旁白」
    video_size: str = ""                   # 留空=按画幅取高分辨率(ASPECT_SIZE);填则强制(如 3840x2160 上 4K)
    video_timeout: float = 1500.0          # 轮询总超时(秒);视频远比图片慢,给 25min(4K/排队时真要这么久)
    video_poll_interval: float = 5.0
    # 后期「节奏快切」(services/video_edit.punch_up):按 beat 切段、交替全景/推近,加短视频节奏感。
    # 不改时长/音轨/商品像素(一致性零风险);experimental,默认关,验证调好再开。
    video_punchup: bool = False
    # 图生视频「旁白配音」总开关:看图写目标语言口播稿(网关视觉模型)→ edge-tts 免费配音 → ffmpeg 叠回。
    # CogVideoX 只产音效不产语音,故旁白靠这条管线补。false=全局关(运维兜底);best-effort,失败保留原视频。
    voiceover_enabled: bool = True

    @property
    def upscale_realesrgan_path(self) -> Path:
        return _BACKEND_DIR / "models" / self.upscale_realesrgan_model

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    def ensure_dirs(self) -> None:
        for d in (self.uploads_dir, self.outputs_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
