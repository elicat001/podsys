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

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..ai.upscale import get_upscale_provider
from ..config import settings
from .design_extract import extract_design

log = logging.getLogger(__name__)

# 提取 prompt:通用『忠实提取印花到干净背景』。覆盖三类输入——
#   - 图案衫(NEO 骷髅/文字/表情包):抠出那块图案,去掉衣服/人/褶皱/背景;
#   - 满铺布料(窗帘/抱枕):展平成平整花型,去褶皱/阴影/透视;
#   - 硬质产品(水杯/瓶子/袋子):抠出印上去的 logo/文字/图形,**忽略瓶身透明/反光/瓶内液体**。
# 关键:产品无关 + 强制『实心、不透明、高对比、印刷级』——否则会把磨砂/半透明质感一起复刻
# (踩过坑:水杯白字白底+雾感→几乎不可见,用户报『效果差/文字没提出来』)。
# 仍强调忠实:保原 motif/文字/配色/比例,禁止重设计/重排/风格化/增删元素。
_FLATTEN_PROMPT = (
    "Extract ONLY the printed or applied design (logos, lettering, text, graphics, "
    "patterns) from this product photo. The product may be apparel, fabric, a mug, a "
    "bottle, a bag or any item. Completely ignore and remove the product itself and its "
    "shape, material, color, transparency, reflections, any liquid or contents inside it, "
    "the person, hands, fabric folds, drape, shadows, lighting gradients, perspective "
    "distortion and the background scene. Reproduce the design FAITHFULLY as clean, SOLID, "
    "fully OPAQUE, high-contrast, print-ready flat artwork: keep the exact same text "
    "wording, logos, motifs, colors, layout and proportions. Keep the design's ORIGINAL "
    "upright orientation exactly as it reads on the product — text must read horizontally "
    "left-to-right; do NOT rotate, tilt or flip the artwork, even when the product photo is "
    "tall and narrow (pad with white space around it instead of rotating). Lay it out evenly "
    "lit and straightened on a pure white background (centered for a single motif, or filled "
    "edge-to-edge for an all-over repeating pattern). Do NOT redesign, restyle, add or remove "
    "anything, and do NOT make it translucent, frosted or blurry — output only the original "
    "print as crisp graphics."
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


def _border_bg_color(rgb: Image.Image) -> tuple[int, int, int]:
    """取四边像素中位色作为背景色估计(AI 重绘把印花放在干净底上,边缘=底色)。"""
    w, h = rgb.size
    px = rgb.load()
    rs: list[int] = []; gs: list[int] = []; bs: list[int] = []
    for x in range(0, w, max(1, w // 64)):
        for y in (0, h - 1):
            p = px[x, y]; rs.append(p[0]); gs.append(p[1]); bs.append(p[2])
    for y in range(0, h, max(1, h // 64)):
        for x in (0, w - 1):
            p = px[x, y]; rs.append(p[0]); gs.append(p[1]); bs.append(p[2])
    rs.sort(); gs.sort(); bs.sort(); m = len(rs) // 2
    return (rs[m], gs[m], bs[m])


def _white_bg_to_transparent(img: Image.Image, tol: int = 55,
                             min_bg_frac: float = 0.02, max_bg_frac: float = 0.97) -> Image.Image:
    """把『连通到画面边缘的底色背景』抠成透明,**完整保留设计**(含满铺花型/装饰图)。

    为什么用这个而不是 rembg:rembg 做语义"抠主体",对满铺/装饰类印花(枕套花纹、窗帘)
    会把整片设计当背景删光(实测仅剩残影)。这里只去掉"连通到边缘的底色",设计内部一律保留:
    1. 取四边中位色当底色;2. 从四边 flood-fill(容差 tol)标记连通背景;3. 该区域 alpha→0。
    防呆:背景占比 <min 或 >max(没有真正可抠的底)→ 原样返回不透明,绝不弄巧成拙。
    """
    base = img.convert("RGBA")
    w, h = base.size
    rgb = base.convert("RGB")
    bg = _border_bg_color(rgb)
    pad = Image.new("RGB", (w + 2, h + 2), bg)
    pad.paste(rgb, (1, 1))
    seed = (255, 0, 255)
    ImageDraw.floodfill(pad, (0, 0), seed, thresh=tol)
    filled = pad.crop((1, 1, w + 1, h + 1))
    r, g, b = filled.split()
    mask = ImageChops.multiply(
        ImageChops.multiply(r.point(lambda v: 255 if v == seed[0] else 0),
                            g.point(lambda v: 255 if v == seed[1] else 0)),
        b.point(lambda v: 255 if v == seed[2] else 0))
    if not (min_bg_frac <= mask.histogram()[255] / float(w * h) <= max_bg_frac):
        return base  # 没有明显可抠的底色 → 保持不透明
    mask = mask.filter(ImageFilter.GaussianBlur(0.6))
    base.putalpha(ImageChops.subtract(base.split()[3], mask))
    return base


def _extract_ai(image: Image.Image) -> tuple[Image.Image, dict]:
    """AI 重绘提取。无 key → OpenAIImageClient() 构造即抛 RuntimeError;调用失败 → 抛异常。
    由 `extract_print_design` 捕获后降级到本地。

    ⚠️ 方向的已知局限:对**圆柱硬质产品**(瓶子/杯子)的横向 logo,gpt-image 有时会把设计
    旋转 90°(为填充竖画布)。实测 prompt 文字约束、正方输入+正方输出都压不住它。
    因此**方向交给前端的『旋转』按钮**让用户一键校正(见前端 ResultView),后端不强拗。"""
    from ..ai.openai_image import OpenAIImageClient  # 惰性 import(重依赖,且离线不应触发)

    out = OpenAIImageClient().edit(_downscale(image), _FLATTEN_PROMPT).convert("RGBA")
    # 顺序很关键:先放大,再抠透明。Real-ESRGAN 超分内部 convert("RGB") 会丢 alpha,
    # 故抠透明必须放在**最后一步**,否则透明会被超分抹掉(变回白底)。
    out = _upscale_to_target(out)
    out = _white_bg_to_transparent(out)   # 去掉连通边缘的白底 → 透明,设计完整保留(含满铺花型)
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
