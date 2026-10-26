"""Stage 13 指标计算：全部确定性规则比对，不使用 LLM-as-judge。

口径定义（与任务书一一对应）：
- 召回率：金标 (contract_id, clause_id, risk_type) 被终态 findings 命中（非 stale）；
  按 risk_type 分组报告。
- 误报率：系统产出但金标无对应标注的 finding 占全部产出的比例（陷阱条款是主要来源）。
- 引用准确率：对 50 条人工金标（gold_citations.jsonl，含正确 source_id），
  比对 finding 实际引用的 source_id；fragile 风险类型刻意引错（相邻法条），
  用于让该指标非平凡。金标覆盖比例即「人工抽检比例」，如实标注。
- 条款定位准确率：系统定位（切分器输出的条款 span）与金标 span_hint 的 IoU > 0.8
  记为命中。
- P95 端到端时延、单份 token/成本：来自 runner 实测（examiner 固定延迟主导）。
"""

from __future__ import annotations

import math
from typing import Any

from eval.runner import ContractResult

IOU_HIT_THRESHOLD = 0.8


def _percentile(values: list[int], fraction: float) -> int:
    """分位数：升序取第 ceil(fraction*n) 位（n=0 时为 0）。"""
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _iou(a: list[int], b: list[int]) -> float:
    """区间 IoU：交集长度 / 并集长度（不相交为 0）。"""
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def _annotation_keys(record: dict[str, Any]) -> set[tuple[str, str, str]]:
    """金标标注键集：(contract_id, clause_id, risk_type)。clause_no → clause_id 用 gold_clauses。"""
    by_no = {
        str(gc["clause_no"]): str(gc["clause_id"]) for gc in record.get("gold_clauses", [])
    }
    keys: set[tuple[str, str, str]] = set()
    for ann in record.get("annotations", []):
        clause_id = by_no.get(str(ann["clause_no"]))
        if clause_id:
            keys.add((str(record["contract_id"]), clause_id, str(ann["risk_type"])))
    return keys


def compute_metrics(
    records: list[dict[str, Any]],
    results: list[ContractResult],
    gold: list[dict[str, Any]],
) -> dict[str, Any]:
    """主入口：数据集 + 运行结果 + 引用金标 → 全量指标 dict（JSON 可序列化）。"""
    annotations: set[tuple[str, str, str]] = set()
    for record in records:
        annotations |= _annotation_keys(record)

    results_by_id = {r.contract_id: r for r in results}
    reported: set[tuple[str, str, str]] = set()
    finding_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for result in results:
        for finding in result.findings:
            if finding["stale"]:
                continue
            key = (result.contract_id, finding["clause_id"], finding["risk_type"])
            reported.add(key)
            finding_by_key[key] = finding

    tp_keys = annotations & reported
    fn_keys = annotations - reported
    fp_keys = reported - annotations

    # ---- 按 risk_type 分组 ----
    risk_types = sorted({key[2] for key in annotations} | {key[2] for key in reported})
    per_risk: list[dict[str, Any]] = []
    for risk_type in risk_types:
        tp = sum(1 for k in tp_keys if k[2] == risk_type)
        fn = sum(1 for k in fn_keys if k[2] == risk_type)
        fp = sum(1 for k in fp_keys if k[2] == risk_type)
        per_risk.append(
            {
                "risk_type": risk_type,
                "gold_count": tp + fn,
                "tp": tp,
                "fn": fn,
                "fp": fp,
                "recall": round(tp / (tp + fn), 4) if tp + fn else None,
                "fp_rate": round(fp / (tp + fp), 4) if tp + fp else None,
            }
        )

    # ---- 引用准确率（金标 50 条） ----
    gold_total = len(gold)
    gold_checked = 0
    gold_correct = 0
    for entry in gold:
        key = (
            str(entry["contract_id"]),
            str(entry["clause_id"]),
            str(entry["risk_type"]),
        )
        finding = finding_by_key.get(key)
        if finding is None:
            # 系统未产出该发现：计为召回缺失，不进引用准确率分母。
            continue
        gold_checked += 1
        citations = finding.get("citations") or []
        if citations and str(citations[0]["source_id"]) == str(entry["gold_source_id"]):
            gold_correct += 1

    # ---- 条款定位（span IoU） ----
    span_by_key: dict[tuple[str, str, str], list[int]] = {}
    gold_span_by_key: dict[tuple[str, str, str], list[int]] = {}
    for record in records:
        contract_id = str(record["contract_id"])
        result = results_by_id.get(contract_id)
        if result is None:
            continue
        by_no = {
            str(gc["clause_no"]): gc for gc in record.get("gold_clauses", [])
        }
        for ann in record.get("annotations", []):
            gc = by_no.get(str(ann["clause_no"]))
            if gc is None:
                continue
            key = (contract_id, str(gc["clause_id"]), str(ann["risk_type"]))
            if key in tp_keys:
                gold_span_by_key[key] = [int(gc["span_start"]), int(gc["span_end"])]
                span_by_key[key] = result.clause_spans.get(
                    str(gc["clause_id"]), [0, 0]
                )
    ious = [
        _iou(span_by_key[key], gold_span_by_key[key]) for key in span_by_key
    ]
    iou_hits = sum(1 for v in ious if v > IOU_HIT_THRESHOLD)
    localization_total = len(ious)

    # ---- 时延 / 成本 ----
    durations = [r.duration_ms for r in results]
    input_tokens = [r.input_tokens for r in results]
    output_tokens = [r.output_tokens for r in results]
    costs = [r.cost_cny for r in results]
    n = len(results)

    total_findings = len(reported)
    citation_rate = (
        round(gold_correct / gold_checked, 4) if gold_checked else None
    )
    return {
        "contracts": n,
        "annotations": len(annotations),
        "findings": total_findings,
        "tp": len(tp_keys),
        "fn": len(fn_keys),
        "fp": len(fp_keys),
        "recall": round(len(tp_keys) / len(annotations), 4) if annotations else None,
        "fp_rate": round(len(fp_keys) / total_findings, 4) if total_findings else None,
        "per_risk": per_risk,
        "citation": {
            "gold_total": gold_total,
            "gold_checked": gold_checked,
            "gold_correct": gold_correct,
            "accuracy": citation_rate,
            # 人工抽检比例：金标条数 / 系统全部产出（LLM 未参与判分，仅规则比对）。
            "manual_sample_ratio": (
                round(gold_total / total_findings, 4) if total_findings else None
            ),
        },
        "localization": {
            "matched": localization_total,
            "iou_hits": iou_hits,
            "accuracy": (
                round(iou_hits / localization_total, 4) if localization_total else None
            ),
            "mean_iou": round(sum(ious) / len(ious), 4) if ious else None,
            "threshold": IOU_HIT_THRESHOLD,
        },
        "latency_ms": {
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "mean": round(sum(durations) / n) if n else 0,
            "max": max(durations) if durations else 0,
        },
        "tokens": {
            "input_per_contract": round(sum(input_tokens) / n) if n else 0,
            "output_per_contract": round(sum(output_tokens) / n) if n else 0,
            "total": sum(input_tokens) + sum(output_tokens),
        },
        "cost_cny": {
            "per_contract": round(sum(costs) / n, 4) if n else 0.0,
            "total": round(sum(costs), 4),
        },
        "degraded_contracts": sum(1 for r in results if r.degraded),
    }


def diff_metrics(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """当前 vs 基线的关键指标涨跌（百分比；基线值为 0 时标注 new/inf）。"""
    def _pct(cur: Any, base: Any) -> Any:
        if cur is None or base is None:
            return None
        if base == 0:
            return "new" if cur else 0.0
        return round((cur - base) / base * 100, 2)

    return {
        "recall": _pct(current.get("recall"), baseline.get("recall")),
        "fp_rate": _pct(current.get("fp_rate"), baseline.get("fp_rate")),
        "citation_accuracy": _pct(
            (current.get("citation") or {}).get("accuracy"),
            (baseline.get("citation") or {}).get("accuracy"),
        ),
        "localization_accuracy": _pct(
            (current.get("localization") or {}).get("accuracy"),
            (baseline.get("localization") or {}).get("accuracy"),
        ),
        "p95_latency_ms": _pct(
            (current.get("latency_ms") or {}).get("p95"),
            (baseline.get("latency_ms") or {}).get("p95"),
        ),
        "tokens_total": _pct(
            (current.get("tokens") or {}).get("total"),
            (baseline.get("tokens") or {}).get("total"),
        ),
    }
