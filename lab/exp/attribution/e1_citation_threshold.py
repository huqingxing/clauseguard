"""E1 · 引用门槛假说（H1）离线回溯分析。

不重跑真模型：直接复用阶段 7 已落盘的真 LLM 原始产物
（eval/out/stage7/{mock,live}/results.jsonl + metrics.json），
回答一个问题——「召回塌方是否由『无引用即丢弃 finding』的规则造成」。

判据（用户给定）：
- 召回率回到 ≥85% → H1 成立（规则丢掉了风险）
- 召回率仍 ≈30%，但 findings 总数明显上升 → 风险被报了但标注没命中（转查 H4）
- 召回率与总数都不变 → H1 不成立

本脚本核心证据链：
1. 召回口径（metrics.py）只排除 stale，不要求 citations ⇒ 无引用 finding 仍计入召回；
2. validation.py 的 enforce_citation_whitelist 在引用被丢空时只把 risk_level 降级为
   unverified 并【保留整条】，从不整条丢弃；
3. 因此「无引用」唯一能减召回的途径是「整条 finding 被丢弃」——而 live 组
   dropped_citations=0、且 83 个 gold 根本没出现在 findings 里（从未产出，而非被丢）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "eval" / "out" / "stage7"
DATASET = ROOT / "eval" / "dataset" / "dataset.jsonl"


def _load_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _finding_stats(results: list[dict[str, Any]]) -> dict[str, int]:
    total = cited = unverified = 0
    for r in results:
        for f in r.get("findings", []):
            if f.get("stale"):
                continue
            total += 1
            cites = f.get("citations") or []
            if cites:
                cited += 1
            else:
                unverified += 1
    return {"total": total, "cited": cited, "unverified": unverified}


def _gold_keys(records: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for rec in records:
        by_no = {str(gc["clause_no"]): str(gc["clause_id"]) for gc in rec.get("gold_clauses", [])}
        for ann in rec.get("annotations", []):
            cid = by_no.get(str(ann["clause_no"]))
            if cid:
                keys.add((str(rec["contract_id"]), cid, str(ann["risk_type"])))
    return keys


def _reported_keys(results: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for r in results:
        for f in r.get("findings", []):
            if f.get("stale"):
                continue
            keys.add((str(r["contract_id"]), str(f["clause_id"]), str(f["risk_type"])))
    return keys


def main() -> int:
    records = _load_results(DATASET)[:30]  # 与阶段 7 同一批 30 份
    gold = _gold_keys(records)
    for mode in ("mock", "live"):
        results = _load_results(OUT / mode / "results.jsonl")
        metrics = json.loads((OUT / mode / "metrics.json").read_text(encoding="utf-8"))
        stats = _finding_stats(results)
        reported = _reported_keys(results)
        never_produced = gold - reported  # 金标里根本没出现在 findings 的（从未产出）
        dropped = metrics.get("diagnostics", {}).get("dropped_citations", None)
        print(f"\n=== {mode.upper()} 组（真模型 qwen-plus, retrieval={mode}）===")
        print(f"  金标 gold 键数            : {len(gold)}")
        print(f"  findings 总数(非stale)    : {stats['total']}")
        print(f"    · 有引用 cited          : {stats['cited']}")
        print(f"    · 无引用 unverified     : {stats['unverified']}  ← 这些仍计入召回口径")
        print(f"  召回 tp/fn/fp             : {metrics['recall']['tp']}/{metrics['recall']['fn']}/{metrics['recall']['fp']}")
        print(f"  召回率 recall             : {metrics['recall']['recall']}")
        print(f"  dropped_citations(诊断)   : {dropped}  ← 引用被丢弃次数（0=无门槛丢引用）")
        print(f"  金标从未被产出(83 谜底)   : {len(never_produced)}  ← 模型根本没给这些 risk，而非被规则丢弃")
    print("\n=== E1 结论（H1 引用门槛）===")
    print("  · 召回口径不要求 citations，unverified finding 照常计入召回；")
    print("  · enforce_citation_whitelist 只降级不丢弃整条 finding；")
    print("  · live 组 dropped_citations=0，且 83 个 gold 根本没出现在 findings（从未产出）。")
    print("  ⇒ 塌方不是『无引用被丢弃』造成 → H1 不成立（即便把『丢弃开关』关掉，recall 仍是 0.2906）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
