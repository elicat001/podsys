"""印花提取编排:AI 重绘为默认,本地保真算法做兜底。

两个引擎产出的『东西』性质不同,调用方要知道(见 meta.method / meta.engine):
- **AI(默认,engine="ai")**:gpt-image edit 把布料展平/去褶皱 → 一张『含底色的平整花样图』
  (95% 视觉一致,重绘,**不保原像素**)。能处理挂拍窗帘/褶皱/透视等本地算法做不了的实拍图。
- **本地(兜底,engine="local")**:`design_extract.extract_design` → 透明背景的『忠实印花抠图』
  (原像素,保真)。无 key / AI 关闭 / AI 调用失败(502/超时/配额)时自动降级到这条。

为什么默认走会重绘的 AI(违背早期『绝不能识图再生图做提取』的取舍)?
  产品决策:对『实拍场景图(挂拍窗帘等)→ 可用花样』这个场景,本地算法根本启动不了
  (锐利折痕去不掉、拿不到干净 tile),只能靠 AI 展平;并已确认接受 95% 视觉一致而非 100% 保真。
  详见 CLAUDE.md「印花提取」一节。需要 100% 保真请把 POD_PRINT_EXTRACT_AI=false。

本模块只做『prompt 拼装 + 引擎选择 + 降级』;router 负责鉴权/扣点/退点/存盘。
gpt 调用统一走 `app/ai/openai_image.py`(不在此写死厂商,符合可插拔范式)。
"""
from __future__ import annotations

import logging

from PIL import Image

from ..ai.upscale import get_upscale_provider
from ..config import settings
from .design_extract import extract_design

log = logging.getLogger(__name__)

# 提取 prompt:通用『忠实提取印花到干净背景』。两类输入都覆盖——
#   - 图案衫(NEO 骷髅/文字/表情包):抠出那块图案,去掉衣服/人/褶皱/背景;
#   - 满铺布料(窗帘/抱枕):展平成平整花型,去褶皱/阴影/透视。
# 强调忠实:保原 motif/文字/配色/比例,禁止重设计/重排/风格化/增删元素。
_FLATTEN_PROMPT = (
    "Extract the printed design from this photo of a garment, fabric or home-textile "
    "product. Remove the product itself, any person, all fabric folds, wrinkles, drape "
    "shadows, lighting gradients, perspective distortion and the background scene. "
    "Reproduce the print FAITHFULLY and flat: keep the exact same artwork, motifs, text, "
    "colors, layout and proportions, evenly lit and straightened, on a clean plain white "
    "background. Do NOT redesign, restyle, stylize, add or remove anything — output only "
    "the original print itself, flattened."
)
_EDIT_MAX_SIDE = 1024   # 发给 gpt-image 前等比缩小(输出本就 ≤1024,发原图纯浪费上传/网关时间)


def _downscale(image: Image.Image, max_side: int = _EDIT_MAX_SIDE) -> Image.Image:
    m = max(image.size)
    if m <= max_side:
        return image
    s = max_side / m
    return image.resize((max(1, round(image.width * s)), max(1, round(image.height * s))), Image.LANCZOS)


def _upscale_to_target(img: Image.Image) -> Image.Image:
    """放大到目标长边(印花是生产文件,需高清);走 upscale Provider(默认 Lanczos)。"""
    target = settings.print_target_px
    long = max(img.size)
    if not target or long >= target:
        return img
    scale = min(target / long, settings.print_max_upscale)
    if scale <= 1.01:
        return img
    return get_upscale_provider().upscale(img, scale).convert("RGBA")


def _extract_ai(image: Image.Image) -> tuple[Image.Image, dict]:
    """AI 重绘提取。无 key → OpenAIImageClient() 构造即抛 RuntimeError;调用失败 → 抛异常。
    由 `extract_print_design` 捕获后降级到本地。"""
    from ..ai.openai_image import OpenAIImageClient  # 惰性 import(重依赖,且离线不应触发)

    out = OpenAIImageClient().edit(_downscale(image), _FLATTEN_PROMPT).convert("RGBA")
    out = _upscale_to_target(out)
    return out, {"method": "ai_flatten", "engine": "ai", "size": list(out.size)}


def extract_print_design(image: Image.Image) -> tuple[Image.Image, dict]:
    """印花提取入口。默认 AI 重绘,失败/无 key/关闭 → 自动回退本地保真算法。

    返回 (图, meta);meta.engine ∈ ai|local,meta.method 见各引擎。
    本地路径可能抛 ValueError(图过大),由 router 处理为 400;AI 失败不外抛(已降级)。
    """
    if settings.print_extract_ai and settings.openai_api_key:
        try:
            return _extract_ai(image)
        except Exception as exc:  # noqa: BLE001 — AI 任何失败都降级,不影响出图
            log.warning("AI 印花提取失败,降级本地: %s", exc)

    design, meta = extract_design(image)
    meta.setdefault("engine", "local")
    return design, meta
