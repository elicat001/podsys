"""Collection layer — 商品图 URL 平台识别与原图(高清)升级。

本模块是**纯函数**集合,不发起任何网络请求、不依赖任何第三方库,
因此可以被单测完全覆盖,也可以被后端任意路由/Worker 复用。

平台缩略图 URL 通常在文件名或 query 里编码了尺寸(如 amazon 的
`._AC_SX466_`、etsy 的 `il_340x270`、temu/tiktok 的 `?width=200`)。
把这些尺寸修饰去掉/还原,即可拿到原始大图地址。`extension/content.js`
里有一份等价的 JS 实现,二者规则必须保持一致。

合规边界(硬要求)
------------------
抓取并复用他人商品图存在 **平台反爬条款** 与 **著作权** 双重风险。
本模块仅提供 URL 字符串变换工具,**仅可用于「已获授权 / 自有内容」场景**
(例如卖家整理自己上架的商品图、客户授权的设计稿)。任何对第三方
受版权保护内容的抓取与商用,使用者自行承担法律责任。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

__all__ = ["detect_platform", "upgrade_to_hires"]

# --- 平台识别 ---------------------------------------------------------------
# 按出现顺序匹配主机名中的关键片段。tiktokcdn / tiktok 都归到 tiktok。
_PLATFORM_HOST_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("amazon", ("amazon.", "media-amazon.", "ssl-images-amazon.")),
    ("etsy", ("etsy.", "etsystatic.")),
    ("temu", ("temu.", "kwcdn.")),                       # temu CDN 常用 kwcdn
    ("tiktok", ("tiktok.", "tiktokcdn", "ttwstatic.", "ibyteimg.")),
]

# amazon 文件名里的尺寸/裁剪修饰段,如 `._AC_SX466_`、`._SL1500_`、`._SY400,300_`。
# 段内可含内部下划线(`AC_SX466`),整段位于扩展名之前,可能连续出现多段。
_AMAZON_SIZE_SEG = re.compile(r"\.(?:_[A-Z0-9,]+)+_(?=\.)")

# etsy 文件名里的尺寸段,如 `il_340x270`、`il_600x600`、`il_794xN`。
# 不用 IGNORECASE:etsy 真实 URL 一律小写 il_,避免把大写误改并把模板写成小写造成大小写不一致(评审 P1-2)。
_ETSY_SIZE_SEG = re.compile(r"il_\d+x[\dN]+")

# query 中代表「签名/鉴权」的参数;一旦出现就保守地完全不动该 URL,
# 以免重排/重编码破坏签名(评审 P1-4)。
_SIGNED_QUERY_KEYS = frozenset(
    {"sign", "signature", "sig", "token", "expires", "expire", "policy",
     "keyid", "x-amz-signature", "x-amz-credential", "auth", "st"}
)

# temu / tiktok 等需要从 query 里剔除的缩放/处理参数(小写比较)。
_SCALING_QUERY_KEYS = frozenset(
    {
        "imageview2",
        "imageview",
        "width",
        "w",
        "height",
        "h",
        "quality",
        "q",
        "x-oss-process",
        "imagemogr2",
        "thumbnail",
        "format",
    }
)


def detect_platform(url: str) -> str:
    """根据 URL 主机名判定来源平台。

    返回值之一:``'amazon' | 'etsy' | 'temu' | 'tiktok' | 'unknown'``。

    >>> detect_platform("https://m.media-amazon.com/images/I/71x.jpg")
    'amazon'
    >>> detect_platform("https://i.etsystatic.com/1/r/il/a/il_340x270.jpg")
    'etsy'
    >>> detect_platform("https://www.temu.com/x")
    'temu'
    >>> detect_platform("https://p16.tiktokcdn.com/x.jpeg")
    'tiktok'
    >>> detect_platform("https://example.com/x.png")
    'unknown'
    """
    if not url:
        return "unknown"
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        # 没有 scheme 时 hostname 为空,退化为整串小写匹配
        host = url.lower()
    for platform, markers in _PLATFORM_HOST_MARKERS:
        if any(marker in host for marker in markers):
            return platform
    return "unknown"


def _strip_scaling_query(url: str) -> str:
    """剔除 query 中的缩放/图片处理参数,保留其余主体。

    安全护栏(评审 P1-4):
    - query 含签名/鉴权参数 → 原样返回,避免重排破坏签名;
    - 没有任何需要剔除的参数 → 原样返回,避免无谓重编码(如 `+`↔`%20` 漂移)。
    """
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not pairs:
        return url
    lowered = {k.lower() for k, _ in pairs}
    if lowered & _SIGNED_QUERY_KEYS:
        return url
    kept = [(k, v) for k, v in pairs if k.lower() not in _SCALING_QUERY_KEYS]
    if len(kept) == len(pairs):
        return url  # 无可剔除项,保持原样
    new_query = urlencode(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def upgrade_to_hires(url: str, platform: str | None = None) -> str:
    """把缩略图 URL 升级为原图(高清)URL。

    ``platform`` 为空时先用 :func:`detect_platform` 判定。各平台规则:

    - **amazon**: 去掉文件名中的尺寸修饰段(``._AC_SX466_``、``._SL1500_`` 等)。
    - **etsy**: 把 ``il_340x270`` 这类尺寸段替换为 ``il_fullxfull``。
    - **temu / tiktok**: 去掉 query 里的缩放参数(``width``、``imageView2``、
      ``x-oss-process`` 等),保留主体 URL。
    - **unknown**: 原样返回。

    >>> upgrade_to_hires("https://m.media-amazon.com/images/I/71abcXYZ._AC_SX466_.jpg")
    'https://m.media-amazon.com/images/I/71abcXYZ.jpg'
    >>> upgrade_to_hires("https://i.etsystatic.com/123/r/il/abc/456/il_340x270.456.jpg")
    'https://i.etsystatic.com/123/r/il/abc/456/il_fullxfull.456.jpg'
    >>> upgrade_to_hires("https://img.temu.com/a/b.jpg?imageView2=2/w/300")
    'https://img.temu.com/a/b.jpg'
    >>> upgrade_to_hires("https://example.com/a.png?width=200")
    'https://example.com/a.png?width=200'
    """
    if not url:
        return url
    platform = platform or detect_platform(url)

    if platform == "amazon":
        # 反复去除尺寸段,直到没有残留(应对连续多段的情况)。
        prev = None
        out = url
        while out != prev:
            prev = out
            out = _AMAZON_SIZE_SEG.sub("", out)
        return out

    if platform == "etsy":
        return _ETSY_SIZE_SEG.sub("il_fullxfull", url)

    if platform in ("temu", "tiktok"):
        return _strip_scaling_query(url)

    # unknown:原样返回
    return url
