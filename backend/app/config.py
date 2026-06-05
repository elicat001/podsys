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

    # auth
    jwt_secret: str = "dev-secret-change-me-please-set-POD_JWT_SECRET-in-prod"

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

    # third-party API config (used when *_provider == "api")
    matting_api_url: str = ""
    matting_api_key: str = ""

    # OpenAI gpt-image-1 ("image2") — 抠图/文生图/图生图/换装换背景
    openai_api_key: str = ""
    openai_base_url: str = ""                 # 留空走官方;可填代理/Azure 兼容网关
    openai_image_model: str = "gpt-image-1"
    openai_text_model: str = "gpt-5.4-mini"   # 文本模型(标题/文案)。与图片模型分开;本网关有 gpt-5.4-mini 等
    openai_text_stream: bool = True           # 本网关 chat 接口需 stream=true 才吐内容(不流式返回空)。官方 OpenAI 置 false 也可
    openai_timeout: float = 120.0             # OpenAI 调用超时(秒)。文生图较慢(20~40s),放宽防被掐断
    openai_max_retries: int = 2               # 网关抖动自动重试次数(SDK 指数退避)。调小=抽风时快速失败、不干等几分钟

    # print extraction
    autocrop_padding: int = 8                # px padding around detected content
    bg_tolerance: int = 28                   # pillow matting: color distance threshold
    print_target_px: int = 2048              # 提取结果长边低于此 → 放大到此(超分);0=关闭
    print_max_upscale: float = 3.0           # 单次放大倍数上限(防过度插值变糊)
    # 印花提取引擎:默认走 AI 重绘(gpt-image edit 展平,95% 视觉一致,挂拍/褶皱也能处理);
    # 有 key 才生效,失败/无 key 自动回退本地保真算法(extract_design)。置 false=永远本地。
    print_extract_ai: bool = True

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
