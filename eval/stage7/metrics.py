"""阶段 7 的业务指标：风险召回率。

口径与 eval/metrics.py 完全一致：金标 (contract_id, clause_id, risk_type) 被终态
非 stale findings 命中即算 TP。这里独立实现一遍而不是直接调私有函数，是为了让
「改造前后两组数字怎么算出来的」在实验报告里可逐行复现；一致性由单测锁住。

为什么不沿用 eval/metrics.py 的 citation.accuracy：那套金标的 gold_source_id 是
`src-vat` 这类 mock 池 ID，改造后引用是 `civil_code:第五百八十五条:0:S5_context`，
两套 ID 体系不可比。阶段 7 的引用准确率改走「人工抽检 30 条语义判定」。
"""

from __future__ import annotations

from typing import Any


def annotation_keys(record: dict[str, Any]) -> set[tuple[str, str, str]]:
    """金标键集：clause_no → clause_id 走数据集自带的 gold_clauses 映射。"""
    by_no = {
        str(gc["clause_no"]): str(gc["clause_id"])
        for gc in record.get("gold_clauses", [])
    }
    keys: set[tuple[str, str, str]] = set()
    for ann in record.get("annotations", []):
        clause_id = by_no.get(str(ann["clause_no"]))
        if clause_id:
            keys.add((str(record["contract_id"]), clause_id, str(ann["risk_type"])))
    return keys


def compute_recall(
    records: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """返回总召回率 + 按 risk_type 分组 + 混淆计数，供报告直接引用。"""
    gold: set[tuple[str, str, str]] = set()
    for record in records:
        gold |= annotation_keys(record)

    reported: set[tuple[str, str, str]] = set()
    for result in results:
        for finding in result.get("findings", []):
            if finding.get("stale"):
                continue
            reported.add(
                (
                    str(result["contract_id"]),
                    str(finding["clause_id"]),
                    str(finding["risk_type"]),
                )
            )

    tp = gold & reported
    fn = gold - reported
    fp = reported - gold
    per_risk: list[dict[str, Any]] = []
    for risk_type in sorted({key[2] for key in gold | reported}):
        hit = sum(1 for key in tp if key[2] == risk_type)
        miss = sum(1 for key in fn if key[2] == risk_type)
        per_risk.append(
            {
                "risk_type": risk_type,
                "gold": hit + miss,
                "tp": hit,
                "fn": miss,
                "recall": round(hit / (hit + miss), 4) if hit + miss else None,
            }
        )
    return {
        "contracts": len(records),
        "gold": len(gold),
        "tp": len(tp),
        "fn": len(fn),
        "fp": len(fp),
        "findings": len(reported),
        "recall": round(len(tp) / len(gold), 4) if gold else None,
        "per_risk": per_risk,
    }
