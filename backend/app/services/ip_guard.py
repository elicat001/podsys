"""侵权检测升级:TRO + 艺术家版权库 + 深度检索报告(本地种子库)。

本模块对接的是 `app/data_seed/tro_seed.json` —— 一个**演示用**的本地侵权风险种子库。
真实生产必须替换为权威数据源(法院 TRO 名单 / 商标库 / 权利人黑名单 / 艺术家授权
登记),并建立定期同步与人工复核流程。此处仅做管线演示:

  ① 视觉相似:对库中带 dhash 的条目,用 services.phash.dhash + hamming 比对结构相似度;
  ② 标题关键词:若给定标题,小写匹配条目 keywords / brand;
  ③ 汇总风险评级 + 深度检索报告(matches / checked / advice)。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from PIL import Image

from . import phash

# 视觉命中阈值(64-bit dhash 的 hamming 距离)
_VISUAL_HIGH = 8   # <=8 视为强命中(结构高度相似)
_VISUAL_WEAK = 14  # 8<dist<=14 视为弱命中(需人工复核)

_SEED_PATH = Path(__file__).resolve().parent.parent / "data_seed" / "tro_seed.json"


@lru_cache(maxsize=1)
def load_library() -> list[dict]:
    """读取并缓存本地侵权风险种子库。

    种子文件首条可能是 `_comment` 说明对象,这里只保留含 name+brand 的真实条目。
    """
    with _SEED_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [e for e in raw if isinstance(e, dict) and e.get("name") and e.get("brand")]


def library_stats() -> dict:
    """种子库统计:{total, by_type:{tro:n, artist:m}}。"""
    lib = load_library()
    by_type: dict[str, int] = {}
    for e in lib:
        t = e.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    by_type.setdefault("tro", 0)
    by_type.setdefault("artist", 0)
    return {"total": len(lib), "by_type": by_type}


def scan(image: Image.Image, title: str | None = None) -> dict:
    """深度侵权检索:视觉相似 + 标题关键词,产出风险报告。

    返回:
      {
        risk: "safe" | "review" | "high",
        matches: [{name, brand, type, reason, distance?}],
        checked: {visual: True, keyword: bool},
        advice: str,
      }
    """
    lib = load_library()
    img_hash = phash.dhash(image)
    title_l = (title or "").strip().lower()

    matches: list[dict] = []
    has_strong = False   # 强命中 → high
    has_weak = False     # 弱命中 → review

    for entry in lib:
        # ① 视觉相似
        seed_hash = entry.get("dhash")
        if seed_hash:
            dist = phash.hamming(img_hash, seed_hash)
            if dist <= _VISUAL_HIGH:
                matches.append({
                    "name": entry["name"], "brand": entry["brand"],
                    "type": entry.get("type", "tro"),
                    "reason": f"视觉结构高度相似(hamming={dist})",
                    "distance": dist,
                })
                has_strong = True
                continue  # 已强命中,无需再看该条关键词
            elif dist <= _VISUAL_WEAK:
                matches.append({
                    "name": entry["name"], "brand": entry["brand"],
                    "type": entry.get("type", "tro"),
                    "reason": f"视觉结构疑似相似(hamming={dist})",
                    "distance": dist,
                })
                has_weak = True
                # 弱视觉命中后仍继续看关键词,可能升级为强命中

        # ② 标题 / 品牌关键词
        if title_l:
            brand_l = entry.get("brand", "").lower()
            hit_kw = None
            if brand_l and brand_l in title_l:
                hit_kw = brand_l
            else:
                for kw in entry.get("keywords", []):
                    if kw and kw.lower() in title_l:
                        hit_kw = kw.lower()
                        break
            if hit_kw:
                matches.append({
                    "name": entry["name"], "brand": entry["brand"],
                    "type": entry.get("type", "tro"),
                    "reason": f"标题命中风险关键词: '{hit_kw}'",
                })
                has_strong = True

    checked = {"visual": True, "keyword": bool(title_l)}

    if has_strong:
        risk = "high"
        advice = "检测到高风险:疑似与受保护品牌/IP/版权作品高度相似或直接引用,强烈建议停止上架并人工法务复核。"
    elif has_weak:
        risk = "review"
        advice = "检测到中等风险:存在疑似相似条目,建议人工复核确认后再上架。"
    else:
        risk = "safe"
        advice = "未命中本地侵权风险库。注意:种子库仅为演示,正式上架仍建议对接权威 TRO/商标数据源复核。"

    return {"risk": risk, "matches": matches, "checked": checked, "advice": advice}
