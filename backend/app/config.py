"""Runtime configuration (env-driven, with sane defaults)."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POD_", env_file=".env", extra="ignore")

    # storage
    data_dir: Path = Path("data")

    # auth
    jwt_secret: str = "dev-secret-change-me-please-set-POD_JWT_SECRET-in-prod"

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
