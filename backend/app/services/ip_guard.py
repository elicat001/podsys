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


def _advice(risk: str) -> str:
    if risk == "high":
        return "检测到高风险:疑似与受保护品牌/IP/版权作品高度相似或直接引用,强烈建议停止上架并人工法务复核。"
    if risk == "review":
        return "检测到中等风险:存在疑似相似/疑似 IP,建议人工复核确认后再上架。"
    return "未发现明显侵权风险(结果仅供参考,不构成法律意见)。"


def _max_risk(a: str, b: str) -> str:
    order = {"safe": 0, "review": 1, "high": 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def scan(image: Image.Image, title: str | None = None, use_ocr: bool = True) -> dict:
    """快速检测(本地):视觉近似(phash)+ 关键词(标题 **+ 图上 OCR 文字**),产出风险报告。

    返回:
      {risk, matches:[{name,brand,type,reason,distance?}], checked:{visual,keyword,ocr}, advice}
    全本地、CPU 极轻(字符串 + 64 位哈希 + 小 OCR),无大模型。
    """
    lib = load_library()
    img_hash = phash.dhash(image)
    # 关键词来源:标题 + 图上印的文字(OCR)——很多侵权图直接印了品牌/角色名
    ocr_text = ""
    if use_ocr:
        try:
            from . import ocr
            ocr_text = ocr.extract_text(image)
        except Exception:  # noqa: BLE001
            ocr_text = ""
    title_l = (((title or "") + " " + ocr_text).strip().lower())

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

    checked = {"visual": True, "keyword": bool(title_l), "ocr": bool(ocr_text)}
    risk = "high" if has_strong else ("review" if has_weak else "safe")
    return {"risk": risk, "matches": matches, "checked": checked, "advice": _advice(risk)}


def _vision_identify(image: Image.Image, title: str | None = None) -> dict:
    """调网关视觉模型识别 IP(角色/品牌/logo/艺术家)。**远程 API、零本地模型**;并发受 _API_GATE 限流。

    返回 {ip, owner, risk(high|medium|low), reason}(ip 为空=没认出)。无 key / 调用失败 → 抛异常,调用方降级。
    """
    import base64
    import io

    from ..config import settings
    if not settings.openai_api_key:
        raise RuntimeError("未配置 AI key")

    im = image.convert("RGB")
    im.thumbnail((512, 512))                    # 压小省 token/带宽
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    prompt = (
        "You are an IP/copyright risk checker for print-on-demand products. Look at the image and decide if it "
        "depicts any copyrighted CHARACTER, trademarked LOGO/BRAND, or famous ARTWORK/artist style. "
        + (f'Listing title: "{title}". ' if title else "")
        + 'Respond with JSON ONLY: {"ip":"<name or empty>","owner":"<rights holder or empty>",'
        '"risk":"high|medium|low","reason":"<short reason in Chinese>"}. '
        "If nothing recognizable/likely-original, set ip to empty and risk to low."
    )
    from openai import OpenAI  # 惰性
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None,
                    timeout=settings.openai_timeout)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}]
    from ..ai.openai_image import _API_GATE   # 复用全局网关并发信号量限流
    with _API_GATE:
        if settings.openai_text_stream:
            content = ""
            stream = client.chat.completions.create(model=settings.openai_text_model, messages=msgs, stream=True)
            for ch in stream:
                if ch.choices and ch.choices[0].delta:
                    content += ch.choices[0].delta.content or ""
        else:
            resp = client.chat.completions.create(model=settings.openai_text_model, messages=msgs)
            content = resp.choices[0].message.content or ""
    content = content.strip()
    if content.startswith("```"):              # 容错剥 ```json 围栏
        content = content.strip("`")
        if content.lstrip().lower().startswith("json"):
            content = content.lstrip()[4:]
    data = json.loads(content)
    return {
        "ip": str(data.get("ip", "")).strip(),
        "owner": str(data.get("owner", "")).strip(),
        "risk": str(data.get("risk", "low")).strip().lower(),
        "reason": str(data.get("reason", "")).strip(),
    }


def scan_ai(image: Image.Image, title: str | None = None) -> dict:
    """深度检测:本地信号(关键词+OCR+phash)**叠加**网关视觉模型语义识别,取较高风险。

    AI 不可用(无 key/失败)→ 退化为仅本地结果(degraded=True),不抛错。
    """
    report = scan(image, title)               # 先拿本地信号
    try:
        ai = _vision_identify(image, title)
        report["checked"]["ai"] = True
        report["degraded"] = False
        ai_risk_map = {"high": "high", "medium": "review", "low": "safe"}
        # 只有视觉模型判为中/高风险才记为命中并升级;低风险(没认出 IP / 原创)不记命中,避免"匹配但安全"的困惑
        if ai.get("ip") and ai.get("risk") in ("high", "medium"):
            report["matches"].append({
                "name": ai["ip"], "brand": ai.get("owner", ""), "type": "ai-vision",
                "reason": "视觉模型识别为已知 IP" + (f":{ai['reason']}" if ai.get("reason") else ""),
            })
            report["risk"] = _max_risk(report["risk"], ai_risk_map.get(ai.get("risk", "low"), "safe"))
        report["advice"] = _advice(report["risk"])
    except Exception:  # noqa: BLE001 — AI 不可用 → 仅本地,标记降级
        report["checked"]["ai"] = False
        report["degraded"] = True
    return report
