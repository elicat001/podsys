"""Collection 层纯函数单测:平台识别 + 原图(高清)URL 升级。

这些测试不触网、不依赖 fixture,只验证 collectors 的字符串变换规则。
"""
from __future__ import annotations

import pytest

from app.services.collectors import detect_platform, upgrade_to_hires


# --- detect_platform -------------------------------------------------------
@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://m.media-amazon.com/images/I/71abcXYZ._AC_SX466_.jpg", "amazon"),
        ("https://www.amazon.com/dp/B000", "amazon"),
        ("https://i.etsystatic.com/123/r/il/abc/456/il_340x270.456.jpg", "etsy"),
        ("https://www.etsy.com/listing/1", "etsy"),
        ("https://www.temu.com/x", "temu"),
        ("https://img.kwcdn.com/product/a.jpg", "temu"),
        ("https://p16.tiktokcdn.com/obj/a.jpeg", "tiktok"),
        ("https://www.tiktok.com/@x", "tiktok"),
        ("https://example.com/some/image.png", "unknown"),
    ],
)
def test_detect_platform(url: str, expected: str):
    assert detect_platform(url) == expected


def test_detect_platform_empty():
    assert detect_platform("") == "unknown"


# --- upgrade_to_hires:amazon ----------------------------------------------
def test_upgrade_amazon_strips_size_segment():
    src = "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX466_.jpg"
    assert upgrade_to_hires(src) == "https://m.media-amazon.com/images/I/71abcXYZ.jpg"


def test_upgrade_amazon_sl_variant():
    src = "https://m.media-amazon.com/images/I/91foo._SL1500_.jpg"
    assert upgrade_to_hires(src) == "https://m.media-amazon.com/images/I/91foo.jpg"


def test_upgrade_amazon_multiple_segments():
    # 连续多段尺寸修饰应被全部去除
    src = "https://m.media-amazon.com/images/I/71bar._AC_._SY400_.jpg"
    assert upgrade_to_hires(src) == "https://m.media-amazon.com/images/I/71bar.jpg"


def test_upgrade_amazon_already_clean_is_noop():
    src = "https://m.media-amazon.com/images/I/71clean.jpg"
    assert upgrade_to_hires(src) == src


# --- upgrade_to_hires:etsy -------------------------------------------------
def test_upgrade_etsy_to_fullxfull():
    src = "https://i.etsystatic.com/123/r/il/abc/456/il_340x270.456.jpg"
    out = upgrade_to_hires(src)
    assert "il_fullxfull" in out
    assert "il_340x270" not in out
    assert out == "https://i.etsystatic.com/123/r/il/abc/456/il_fullxfull.456.jpg"


def test_upgrade_etsy_600():
    src = "https://i.etsystatic.com/9/r/il/x/9/il_600x600.9.jpg"
    assert "il_fullxfull" in upgrade_to_hires(src)


# --- upgrade_to_hires:temu / tiktok ---------------------------------------
def test_upgrade_temu_strips_imageview2():
    src = "https://img.temu.com/a/b.jpg?imageView2=2/w/300"
    assert upgrade_to_hires(src) == "https://img.temu.com/a/b.jpg"


def test_upgrade_tiktok_strips_width():
    src = "https://p16.tiktokcdn.com/obj/a.jpeg?width=200"
    assert upgrade_to_hires(src) == "https://p16.tiktokcdn.com/obj/a.jpeg"


def test_upgrade_temu_strips_oss_process_keeps_others():
    src = "https://img.kwcdn.com/p/a.jpg?x-oss-process=image/resize,w_300&id=42"
    out = upgrade_to_hires(src)
    assert "x-oss-process" not in out
    assert "id=42" in out


# --- upgrade_to_hires:unknown ----------------------------------------------
def test_upgrade_unknown_is_noop():
    src = "https://example.com/a.png?width=200"
    assert upgrade_to_hires(src) == src


# --- explicit platform override -------------------------------------------
def test_upgrade_respects_explicit_platform():
    # 即便域名未知,显式传 platform 也应按规则处理
    src = "https://cdn.unknown.test/a.jpg?width=200"
    assert upgrade_to_hires(src, platform="temu") == "https://cdn.unknown.test/a.jpg"
