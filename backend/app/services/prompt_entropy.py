"""Prompt Entropy(Prompt 熵)体检(audit N5)。

北极星「Capability > Strategy > Prompt」的【度量与守门】:Prompt 会随案例越堆越长、越重复、越互相冲突,这本身是技术债。
本模块提供轻量纯函数扫描器,测出常见熵升高信号——同句重复、同义堆叠、连续一长串「不要/必须」。配合测试做回归守门:
熵超阈 = 该把这段 prompt 往 Capability/Strategy 收,而不是继续往里塞句子。

纯函数、零依赖、离线可测。
"""
from __future__ import annotations

import re
from collections import Counter

# 负向/强制句的标志词(连续一长串 = 模型会变保守、僵化)
_NEGATION = ("不要", "别", "绝不", "禁止", "勿", "不得", "不能", "不可", "don't", "do not", "must not", "never")
_IMPERATIVE = ("必须", "务必", "一定要", "切记", "must ")


def _sentences(text: str) -> list[str]:
    """按中英文句读切句,去短碎片(标点/单字不算句)。"""
    parts = re.split(r"[。;;\n!!??]+", text or "")
    return [s.strip() for s in parts if len(s.strip()) >= 5]


def scan(text: str) -> dict:
    """返回熵指标:句数 / 同句重复 / 负向句数 / 强制句数 / 最长连续负向句串。"""
    sents = _sentences(text)
    counts = Counter(sents)
    dup = {s: n for s, n in counts.items() if n > 1}
    neg_flags = [any(k in s for k in _NEGATION) for s in sents]
    imp_flags = [any(k in s for k in _IMPERATIVE) for s in sents]
    # 最长连续「负向或强制」句串(≈ 用户说的「连续十几个 don't/must」)
    run = best = 0
    for i in range(len(sents)):
        run = run + 1 if (neg_flags[i] or imp_flags[i]) else 0
        best = max(best, run)
    return {
        "sentences": len(sents),
        "duplicate_sentences": dup,
        "negation_sentences": sum(neg_flags),
        "imperative_sentences": sum(imp_flags),
        "max_negation_run": best,
    }


def entropy_issues(text: str, *, max_negation_run: int = 6) -> list[str]:
    """返回这段 prompt 的熵问题清单(空 = 健康)。用于测试/CI 守门,防 prompt 越堆越臃肿。

    - 同句重复:任何句子出现 ≥2 次(纯堆叠)。
    - 连续负向/强制句串过长:> max_negation_run(模型会保守僵化,该收成能力/正向表述)。
    """
    r = scan(text)
    issues: list[str] = []
    if r["duplicate_sentences"]:
        issues.append(f"同句重复 {len(r['duplicate_sentences'])} 处:{list(r['duplicate_sentences'])[:3]}")
    if r["max_negation_run"] > max_negation_run:
        issues.append(f"连续负向/强制句串过长({r['max_negation_run']}>{max_negation_run})→ 该收成能力/正向表述")
    return issues
