"""Runtime configuration (env-driven, with sane defaults)."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POD_", env_file=".env", extra="ignore")

    # storage
    data_dir: Path = Path("data")

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
    upscale_provider: str = "pillow"         # pillow | realesrgan  (gpt-image 不做超分,见 README)

    # third-party API config (used when *_provider == "api")
    matting_api_url: str = ""
    matting_api_key: str = ""

    # OpenAI gpt-image-1 ("image2") — 抠图/文生图/图生图/换装换背景
    openai_api_key: str = ""
    openai_base_url: str = ""                 # 留空走官方;可填代理/Azure 兼容网关
    openai_image_model: str = "gpt-image-1"
    openai_timeout: float = 60.0              # P1-2:OpenAI 调用超时(秒),防线程池被慢请求吃满

    # print extraction
    autocrop_padding: int = 8                # px padding around detected content
    bg_tolerance: int = 28                   # pillow matting: color distance threshold

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
