"""一键上架:Publisher 抽象 + 上架包构建 + 平台适配器。

LocalPublisher 生成可验证的「上架包」(标题/描述/图片/价格/属性 JSON),无需任何
平台资质即可跑通闭环。Temu/TikTok 适配器是桩——真实上架需平台开放权限与店铺授权。
"""
from __future__ import annotations

from typing import Protocol

from ..models_db import Product


def build_listing_payload(product: Product, platform: str) -> dict:
    """把商品库条目转成平台上架字段(通用版,各平台再做字段映射)。"""
    return {
        "platform": platform,
        "title": product.title[:120],
        "category": product.category,
        "price": product.price,
        "currency": "USD",
        "images": [p for p in [product.mockup_path, product.print_path] if p],
        "attributes": product.attrs or {},
        "description": f"{product.title} — print-on-demand, made to order.",
    }


class Publisher(Protocol):
    platform: str
    def publish(self, payload: dict) -> dict: ...


class LocalPublisher:
    """本地/Mock:不真正上架,返回可验证的上架包 + 伪 external_id。"""
    platform = "local"

    def publish(self, payload: dict) -> dict:
        ext = "LOCAL-" + str(abs(hash(payload["title"])) % 10_000_000)
        return {"status": "published", "external_id": ext, "payload": payload}


class TemuPublisher:
    platform = "temu"
    def publish(self, payload: dict) -> dict:
        # TODO: 接 Temu 开放平台 API(需店铺授权/资质)
        raise NotImplementedError("Temu 上架需平台资质与店铺授权,待接入")


class TikTokPublisher:
    platform = "tiktok"
    def publish(self, payload: dict) -> dict:
        # TODO: 接 TikTok Shop API(需 shop_cipher / 授权)
        raise NotImplementedError("TikTok Shop 上架需店铺授权,待接入")


_PUBLISHERS = {p.platform: p for p in [LocalPublisher(), TemuPublisher(), TikTokPublisher()]}


def get_publisher(platform: str) -> Publisher:
    pub = _PUBLISHERS.get(platform)
    if pub is None:
        raise ValueError(f"unknown platform: {platform} (have {list(_PUBLISHERS)})")
    return pub
