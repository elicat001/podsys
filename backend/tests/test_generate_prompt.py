"""文生图 prompt 温和补全(refine_prompt)测试 —— 离线纯函数,确定性。"""
from __future__ import annotations

from app.services.generate import refine_prompt


def test_empty_prompt_gets_default():
    used, hint = refine_prompt("")
    assert used  # 非空
    assert "background" in used
    assert hint and "默认" in hint


def test_short_prompt_gets_style_and_bg():
    used, hint = refine_prompt("柯基印花")
    assert used.startswith("柯基印花")
    assert "white background" in used          # 补了白底
    assert "sticker" in used or "high detail" in used  # 补了风格引导
    assert hint is not None


def test_full_prompt_unchanged():
    p = "a corgi dog, cartoon sticker style, white background, high detail"
    used, hint = refine_prompt(p)
    assert used == p          # 写全了就原样不动
    assert hint is None


def test_has_background_only_appends_style_when_short():
    # 有背景关键词,但很短 → 只补风格,不重复补背景
    used, hint = refine_prompt("猫 背景")
    assert used.count("white background") == 0  # 已含"背景",不再补白底
    assert hint is not None
