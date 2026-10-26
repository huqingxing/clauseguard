"""Stage 13 评测入口：一条命令产出 report.md（make eval 的实现体）。

流程：确保数据集 → 跑批（并发 3）→ sqlite 落库 → 算指标 → 写 metrics JSON →
渲染 report.md（含与 baseline 的对比）。种子固定（dataset.generate.SEED），
examiner 确定性，同一 commit 重复运行的质量指标逐位一致。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from eval.dataset.generate import DATASET_JSONL, SEED, generate
from eval.metrics import compute_metrics, diff_metrics
from eval.report import render_report
from eval.runner import OUT_DIR, load_dataset, persist_results, run_all

GOLD_JSONL = Path(__file__).resolve().parent / "dataset" / "gold_citations.jsonl"
BASELINE_JSON = OUT_DIR / "metrics-baseline.json"


def _load_gold() -> list[dict[str, Any]]:
    if not GOLD_JSONL.exists():
        raise FileNotFoundError(f"引用金标不存在: {GOLD_JSONL}")
    lines = GOLD_JSONL.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _git_commit() -> str:
    """当前 commit（报告可复现性元数据）；非 git 环境返回 unknown。"""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="ClauseGuard 评测（Stage 13）")
    parser.add_argument(
        "--smoke",
        type=int,
        default=0,
        help="只跑前 N 份合同（CI push 冒烟）；0 = 全量 120 份",
    )
    parser.add_argument(
        "--label",
        type=str,
        default="run",
        help="本次运行标签（metrics-<label>.json 与落库 label）",
    )
    parser.add_argument(
        "--set-baseline",
        action="store_true",
        help="把本次指标写入 baseline（首次建基线或 prompt 变更后重设时用）",
    )
    parser.add_argument(
        "--compare",
        type=str,
        default="",
        help="与指定 metrics JSON 对比（缺省与 baseline 对比；baseline 缺失则跳过对比）",
    )
    args = parser.parse_args()

    # 1. 数据集（幂等：已存在即跳过，种子固定）。
    if not DATASET_JSONL.exists():
        generate()

    records = load_dataset()
    if args.smoke > 0:
        records = records[: args.smoke]
    gold = _load_gold()

    # 2. 跑批 + 落库。
    results = asyncio.run(run_all(records))
    persist_results(results, label=args.label)

    # 3. 指标 + 持久化。
    metrics = compute_metrics(records, results, gold)
    metrics["meta"] = {
        "label": args.label,
        "seed": SEED,
        "commit": _git_commit(),
        "contracts_run": len(records),
        "smoke": bool(args.smoke > 0),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path = OUT_DIR / f"metrics-{args.label}.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 4. baseline 对比 + 报告。
    baseline: dict[str, Any] | None = None
    compare_path = Path(args.compare) if args.compare else BASELINE_JSON
    if compare_path.exists():
        baseline = json.loads(compare_path.read_text(encoding="utf-8"))
    delta = diff_metrics(metrics, baseline) if baseline else None
    report = render_report(metrics, baseline, delta)
    report_path = OUT_DIR / "report.md"
    report_path.write_text(report, encoding="utf-8")

    if args.set_baseline:
        BASELINE_JSON.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[eval] baseline 已更新 -> {BASELINE_JSON}")

    print(f"[eval] 指标 -> {metrics_path}")
    print(f"[eval] 报告 -> {report_path}")
    print(
        f"[eval] recall={metrics['recall']} fp_rate={metrics['fp_rate']} "
        f"citation_acc={metrics['citation']['accuracy']} "
        f"loc_acc={metrics['localization']['accuracy']} "
        f"p95={metrics['latency_ms']['p95']}ms"
    )


if __name__ == "__main__":
    main()
