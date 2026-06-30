"""工具计费/退点【单一真相源】(TOOL_SPECS)。

历史:`(kind → 退点 op, 笔数)` 曾在 3+ 处各写一遍——`tasks.TOOL_WORKS` 元组、`jobs._KIND_REFUND_OP`(reaper 专用)、
各 router 的 `submit_celery(op=,n=)`——彼此会漂移。实证后果:reaper 的 `_KIND_REFUND_OP` 漏了 `viduvideo`/`matting`/
`imgreplace`,僵尸 Vidu 视频会被按 `edit`(而非 `vidu`×秒数)静默退错点。

本模块把【计费真相】收成一张表,`tasks.run_tool`(正常错误退点)与 `jobs.reap_stuck_jobs`(僵尸回收退点)共读它,
不再各写一份。**刻意零重依赖**(只 dataclass + dict),让 `jobs.py` 能直接 import 而不拉进 worker 的重代码。

加新工具:在 `TOOL_BILLING` 加一行(+ 在 `tasks.TOOL_WORKS` 注册 work)。**op/笔数只此一处**。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolBilling:
    """一个 kind 的计费/退点规格(单一真相源)。

    op                 : 计费/退点 op(process|generate|edit|asset|video|vidu|title…);None = 免费、永不退点。
    n_field            : params 里表示【扣点笔数】的键(如裂变 variants 按张扣 → "n");None = 1 笔。
    worker_self_refunds: work 函数在自己内部处理退点(如 title 按 degrade 自退)→ run_job_in_worker 不要再自动退(防双退)。
                         注意:这只影响【正常执行失败】路径;【僵尸回收】(reaper)仍按 op 退(worker 没机会自退)。
    free_unless_ai     : 仅当 params["engine"]=="ai" 才计费(title:快速免费、智能/AI 才扣)→ 否则视为免费、不退。
    """
    op: str | None = None
    n_field: str | None = None
    worker_self_refunds: bool = False
    free_unless_ai: bool = False


# ── 单一真相源:每个 kind 的计费规格 ──────────────────────────────────────────────
# 覆盖所有异步工具(tasks.TOOL_WORKS)+ 核心/旁路 kind(process / print-extract 等 reaper 也会遇到)。
TOOL_BILLING: dict[str, ToolBilling] = {
    # 核心管线 / 本地快作业
    "process": ToolBilling(op="process"),
    "print-extract": ToolBilling(op="process"),   # 印花提取(独立 task run_print_extract;此处供 reaper)
    "upscale": ToolBilling(op="process"),
    "vectorize": ToolBilling(op="process"),
    "matting": ToolBilling(op="process"),
    "ipguard": ToolBilling(op="process"),
    # 文生图
    "generate": ToolBilling(op="generate", n_field="n"),   # 一组=4 笔,单张=1 笔(按 n 退)
    # 改图类(op=edit)
    "edit": ToolBilling(op="edit"),
    "variants": ToolBilling(op="edit", n_field="n"),       # 裂变按张扣
    "restyle": ToolBilling(op="edit"),
    "meme": ToolBilling(op="edit"),
    "dewatermark": ToolBilling(op="edit"),
    "imgreplace": ToolBilling(op="edit"),
    # 套图/生产(op=asset)
    "mockup": ToolBilling(op="asset"),
    "mockup-replace": ToolBilling(op="asset", n_field="n"),
    "production": ToolBilling(op="asset"),
    # 分析类
    "title": ToolBilling(op="title", worker_self_refunds=True, free_unless_ai=True),  # 快速免费、智能/AI 才扣;worker 自退
    # 采集同步:免费
    "collect_sync": ToolBilling(op=None),
    # 图生视频:按笔数(单段=1;双分镜 15s=2,价格翻倍)
    "aivideo": ToolBilling(op="video", n_field="n"),
    "viduvideo": ToolBilling(op="vidu", n_field="n"),
}

# reaper 兜底:未登记的 kind 退 "edit"(保守,沿用历史默认)。理想情况所有 kind 都在表里 → 不会命中。
_REAPER_FALLBACK_OP = "edit"


def billing_op_for(kind: str, params: dict | None = None) -> str | None:
    """该 kind 应退/计费的 op;None = 不退(免费 / 条件未满足)。用于 reaper 僵尸回收 + router 计费查询。"""
    spec = TOOL_BILLING.get(kind)
    if spec is None:
        return _REAPER_FALLBACK_OP
    if spec.op is None:
        return None
    if spec.free_unless_ai and (params or {}).get("engine") != "ai":
        return None
    return spec.op


def billing_n_for(kind: str, params: dict | None = None) -> int:
    """该 kind 的退点笔数(按张扣的取 params[n_field],否则 1)。"""
    spec = TOOL_BILLING.get(kind)
    if spec is None or not spec.n_field:
        return 1
    try:
        return int((params or {}).get(spec.n_field, 1) or 1)
    except (TypeError, ValueError):
        return 1


def worker_refund_op_for(kind: str, params: dict | None = None) -> str | None:
    """run_tool / run_job_in_worker【正常失败】路径应自动退的 op。
    worker_self_refunds 的 kind(title)由 work 函数自退 → 这里返回 None,避免双退。"""
    spec = TOOL_BILLING.get(kind)
    if spec is not None and spec.worker_self_refunds:
        return None
    return billing_op_for(kind, params)
