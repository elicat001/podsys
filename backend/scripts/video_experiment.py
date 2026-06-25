r"""批量视频生成实验脚手架(工程指标优先,不看 CTR/转化)。

目的(P0):验证「不同视频结构在一批 SKU 上能不能稳定、低成本、无人工地批量出片」。
这是可复用的流水线实验工具——以后新增任何结构(universal/开箱/口播/OOTD…)都丢进来跑,看数字,不靠拍脑袋。

怎么跑(无 web / 无计费 / 无 DB——直接驱动真实生成引擎 _work_aivideo):
  # 先用 local provider 验证框架(快、免费;success 恒 100% 只为验证脚手架通路):
  $env:POD_VIDEO_PROVIDER="local"; .\.venv\Scripts\python.exe scripts\video_experiment.py --images data\uploads --limit 3
  # 真实良品率(需配 cogvideox+gpt-image key,慢、耗额度,建议小批量起步):
  .\.venv\Scripts\python.exe scripts\video_experiment.py --images <SKU图目录> --structures universal,single_frame,multi_ootd --repeat 1

输出:终端工程指标看板(每结构一行)+ data/experiments/report_*.json 明细。
第一版只测工程指标:success_rate / clean_rate / human_fix_rate / avg_time / avg_cost。
retry_count 暂未 instrument(provider 内部重试不易外部捕获,v2 再补)。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import traceback
from types import SimpleNamespace

# 让脚本能 import app.*(scripts/ 在 backend/ 下)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COST_PER_SEGMENT = 3   # video op = 3 点/段;成本 = 段数 × 3(单镜=3,三分镜=9)

# 结构预设:把"视频结构"参数化成一组 ai-generate 参数。新增结构=加一条,不改引擎。
# 关键:universal = 产品前置(template=universal)+ 单镜 + 不依赖 gpt-image 母帧 → 翻车面最小、任意 SKU 通用。
STRUCTURES: dict[str, dict] = {
    # 工业化默认主力:商品为主角、近乎无人、单镜、无 gpt-image 母帧(绕开 #1 翻车源:母帧超时)
    "universal": {
        "template": "universal", "two_shot": False, "n": 1, "seconds": 10,
        "scene_frame": False, "prompt": "", "category": "通用",
    },
    # 对照:单镜 + 开启 gpt-image 场景母帧(测母帧那一步的良品率/耗时)
    "single_frame": {
        "two_shot": False, "n": 1, "seconds": 10,
        "scene_frame": True, "prompt": "商品自然出现在真实使用场景里,镜头缓缓推近展示", "category": "通用",
    },
    # 对照:三分镜 OOTD/动作链(最复杂、最贵、历史翻车最多——验证它确实工业化最差)
    "multi_ootd": {
        "two_shot": True, "n": 3, "seconds": 15, "scene_frame": True,
        "prompt": "0-5秒:在家看手机", "prompt2": "0-5秒:玄关拿钥匙推门", "prompt3": "0-5秒:走在街头",
        "scene1": "", "scene2": "", "scene3": "", "category": "通用",
    },
}

_COMMON = {"language": "葡萄牙语", "aspect": "portrait", "resolution": "1080p",
           "native_sound": False, "voiceover": False, "subtitle": True, "frames2": False}


def _run_one(struct_name: str, img_bytes: bytes, idx: int) -> dict:
    """把一张商品图按某结构跑一次真实生成,返回该次的工程指标。无 web/计费/DB。"""
    from app import storage, tasks
    jid = f"exp_{struct_name}_{idx}_{int(time.time() * 1000) % 100000}"
    storage.upload_path(jid).write_bytes(img_bytes)        # 引擎从 upload_path 读输入
    params = {**_COMMON, **STRUCTURES[struct_name]}
    n_seg = int(params.get("n", 1))
    job = SimpleNamespace(id=jid, owner_id=None, kind="aivideo", params=params)  # owner_id=None → 不碰 DB/不入库
    t0 = time.monotonic()
    rec = {"struct": struct_name, "job": jid, "cost": n_seg * COST_PER_SEGMENT}
    try:
        result = tasks._work_aivideo(jid, job, db=None)    # 真实引擎:母帧→provider→拼接→旁白
        rec["ok"] = True
        rec["secs"] = round(time.monotonic() - t0, 1)
        rec["engine"] = result.get("engine", "")
        rec["warnings"] = result.get("warnings") or []     # 非空 = 降级(母帧/配音失败)≈ 需人工修
    except Exception as exc:  # noqa: BLE001 — 出片失败=最严重(规模化的命门)
        rec["ok"] = False
        rec["secs"] = round(time.monotonic() - t0, 1)
        rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
        rec["trace"] = traceback.format_exc()[-400:]
    finally:
        try:
            storage.upload_path(jid).unlink(missing_ok=True)   # 清理输入图
        except Exception:  # noqa: BLE001
            pass
    return rec


def _aggregate(records: list[dict]) -> dict:
    """按结构聚合工程指标。"""
    out: dict[str, dict] = {}
    by_struct: dict[str, list[dict]] = {}
    for r in records:
        by_struct.setdefault(r["struct"], []).append(r)
    for s, rs in by_struct.items():
        n = len(rs)
        ok = [r for r in rs if r.get("ok")]
        clean = [r for r in ok if not r.get("warnings")]       # 成功且零降级 = 真正不用人工
        degraded_or_failed = [r for r in rs if (not r.get("ok")) or r.get("warnings")]
        out[s] = {
            "n": n,
            "success_rate": round(len(ok) / n, 3) if n else 0,        # 出片成功率
            "clean_rate": round(len(clean) / n, 3) if n else 0,       # 无降级良品率
            "human_fix_rate": round(len(degraded_or_failed) / n, 3) if n else 0,
            "avg_time_s": round(sum(r["secs"] for r in ok) / len(ok), 1) if ok else None,
            "avg_cost": round(sum(r["cost"] for r in rs) / n, 1) if n else 0,
            "failures": [r.get("error", "") for r in rs if not r.get("ok")][:5],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="批量视频结构实验(工程指标)")
    ap.add_argument("--images", default="data/uploads", help="商品图目录或逗号分隔的文件列表")
    ap.add_argument("--structures", default=",".join(STRUCTURES), help="逗号分隔的结构名")
    ap.add_argument("--repeat", type=int, default=1, help="每张图每结构重复次数(看稳定性)")
    ap.add_argument("--limit", type=int, default=0, help="最多取几张图(0=全部)")
    args = ap.parse_args()

    # 收集商品图
    if os.path.isdir(args.images):
        files = sorted(glob.glob(os.path.join(args.images, "*.png")) +
                       glob.glob(os.path.join(args.images, "*.jpg")))
    else:
        files = [f.strip() for f in args.images.split(",") if f.strip()]
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("没有找到商品图。用 --images 指定目录或文件列表。"); return
    structs = [s.strip() for s in args.structures.split(",") if s.strip() in STRUCTURES]

    from app.config import settings
    print(f"provider={settings.video_provider} | 图片 {len(files)} 张 | 结构 {structs} | 每项 ×{args.repeat}")
    print("(provider=local 时 success 恒为 1,仅验证脚手架通路;真实良品率需 cogvideox+gpt-image key)\n")

    records: list[dict] = []
    for f in files:
        img = open(f, "rb").read()
        for s in structs:
            for rep in range(args.repeat):
                r = _run_one(s, img, rep)
                records.append(r)
                tag = "OK " if r.get("ok") else "FAIL"
                warn = " ⚠降级" if r.get("warnings") else ""
                print(f"  [{tag}] {s:<13} {os.path.basename(f):<28} {r['secs']:>6.1f}s{warn}"
                      + ("" if r.get("ok") else f"  {r.get('error','')}"))

    agg = _aggregate(records)
    print("\n===== 工程指标看板 =====")
    print(f"{'结构':<14}{'成功率':>8}{'无降级率':>10}{'需人工率':>10}{'平均耗时':>10}{'平均成本':>10}")
    for s in structs:
        a = agg.get(s, {})
        t = f"{a.get('avg_time_s')}s" if a.get("avg_time_s") is not None else "-"
        print(f"{s:<14}{a.get('success_rate',0):>8.0%}{a.get('clean_rate',0):>10.0%}"
              f"{a.get('human_fix_rate',0):>10.0%}{t:>10}{a.get('avg_cost',0):>9}点")

    os.makedirs("data/experiments", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join("data", "experiments", f"report_{ts}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({"summary": agg, "records": records}, fp, ensure_ascii=False, indent=2)
    print(f"\n明细已写 {path}")
    print("\n第一版只看工程指标(success/clean/human_fix/time/cost);CTR/CVR 等真实数据投放后再回来比。")


if __name__ == "__main__":
    main()
