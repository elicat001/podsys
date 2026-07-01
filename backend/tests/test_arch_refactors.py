"""架构整改(audit 推进)单元测试:把「特判/堆叠」收敛成「注册表/能力」后,锁住通用性。"""
from __future__ import annotations

from PIL import Image


# ---------- T3-9:stylize 风格 = 注册表(关键词集→变换),非 if/elif ----------
def test_stylize_recipe_registry():
    from app.services import effects
    from app.services.effects import STYLE_RECIPES, stylize
    # 注册表存在、可迭代(加新风格=加一条数据,不动 stylize)
    assert len(STYLE_RECIPES) >= 3
    img = Image.new("RGB", (24, 24), (180, 120, 60))
    # 每种风格 + 默认 flat 都能出图、尺寸不变(行为保持)
    for style in ("line", "sketch", "oil", "flat", "随便没匹配的"):
        out = stylize(img, style)
        assert isinstance(out, Image.Image) and out.size == img.size
    # 命中关键词走对应 recipe;未命中走默认 flat(与历史 if/elif 行为一致)
    assert stylize(img, "矢量").size == img.size
    assert effects.stylize(img).mode == "RGB"


# ---------- N5:Prompt Entropy 体检(防 prompt 越堆越臃肿)----------
def test_prompt_entropy_scanner():
    from app.services.prompt_entropy import entropy_issues, scan
    # 同句重复被测出
    dup_text = "保持商品一致。保持商品一致。换个场景。"
    assert scan(dup_text)["duplicate_sentences"]
    assert any("同句重复" in i for i in entropy_issues(dup_text))
    # 连续一长串负向/强制 → 被测出(连续 7 句 don't,超默认阈 6)
    neg = "。".join([f"不要做第{i}件事" for i in range(7)]) + "。"
    assert scan(neg)["max_negation_run"] >= 7
    assert any("连续负向" in i for i in entropy_issues(neg))
    # 健康文本无问题
    assert entropy_issues("她端起杯子喝一口,放松靠回椅背,转身去切水果。") == []


def test_prompt_entropy_live_assets_healthy():
    # 现网核心 prompt 资产应通过熵体检(无同句重复、无超长负向串)——守门未来不让它们退化堆叠。
    from app.services.prompt_entropy import entropy_issues
    from app.services.video_continuity import (
        CONTINUITY_GUIDE, CONTINUITY_GUIDE_VIDU, SCENE_INIT_GUIDE,
    )
    for asset in (SCENE_INIT_GUIDE, CONTINUITY_GUIDE, CONTINUITY_GUIDE_VIDU):
        assert entropy_issues(asset) == [], entropy_issues(asset)


# ---------- T1-3:印花提取材质策略(散落 6 处的 kind=="garment" 常数收敛到一处,数值一字不改)----------
def test_extract_strategy_constants_unchanged():
    from app.services.design_extract import _GARMENT, _PRODUCT, _strategy_for
    assert _strategy_for("garment") is _GARMENT and _strategy_for("product") is _PRODUCT
    assert _strategy_for("whatever") is _PRODUCT   # 非 garment 一律走 product 策略(= 历史 kind != "garment")
    # garment 历史值:压平/无 detail / fine_lo=37 / 精细腐蚀=2 / 粗腐蚀=0.03 / 种子=90 / 弱=50
    assert _GARMENT.flatten_illumination and not _GARMENT.use_detail_mask
    assert (_GARMENT.fine_lo, _GARMENT.fine_inner_erosion) == (37, 2)
    assert (_GARMENT.coarse_erosion_frac, _GARMENT.seed_dist, _GARMENT.weak_dist) == (0.03, 90, 50)
    # product 历史值:不压平/有 detail / fine_lo=12 / 不腐蚀(整块)/ 粗色差阈=16
    assert not _PRODUCT.flatten_illumination and _PRODUCT.use_detail_mask
    assert (_PRODUCT.fine_lo, _PRODUCT.fine_inner_erosion, _PRODUCT.product_dist) == (12, 0, 16)
