"""Stage 13 报告渲染：metrics + baseline → report.md（含对比表与复现说明）。

报告读者是「想看这个项目有没有评测体系的面试官/评审」：指标表、与 baseline 的
涨跌、口径定义、复现命令、以及「LLM 未参与判分」的显式声明一次给全。
"""

from __future__ import annotations

from typing import Any

from eval.metrics import IOU_HIT_THRESHOLD


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2%}{suffix}" if value <= 1 else f"{value}{suffix}"
    return f"{value}{suffix}"


def _delta_cell(value: Any) -> str:
    """涨跌单元格：None = 无基线；数值带符号（百分比指标按百分点展示）。"""
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    if value == 0:
        return "0（一致）"
    sign = "+" if value > 0 else ""
    return f"{sign}{value}%"


def render_report(
    metrics: dict[str, Any],
    baseline: dict[str, Any] | None,
    delta: dict[str, Any] | None,
) -> str:
    meta = metrics.get("meta") or {}
    citation = metrics["citation"]
    localization = metrics["localization"]
    latency = metrics["latency_ms"]
    tokens = metrics["tokens"]
    cost = metrics["cost_cny"]

    lines: list[str] = []
    lines.append("# ClauseGuard 评测报告（Stage 13）")
    lines.append("")
    lines.append("## 运行元数据")
    lines.append("")
    lines.append(f"- 标签：`{meta.get('label', 'run')}`　commit：`{meta.get('commit', 'unknown')}`")
    lines.append(f"- 数据集：{metrics['contracts']} 份合同（种子 {meta.get('seed')}，"
                 f"{metrics['annotations']} 条标注风险点，全量 120 份生成于固定种子）")
    lines.append("- examiner：确定性替身（prompt 词表驱动；延迟注入使时延可复现）")
    lines.append("- 判分方式：**全部规则比对，未使用 LLM-as-judge**；"
                 "引用准确率为 50 条人工金标的确定性比对")
    lines.append("")

    lines.append("## 总体指标")
    lines.append("")
    lines.append("| 指标 | 本次" + (" | baseline | Δ" if baseline else "") + " |")
    lines.append("|---|---|" + ("|---|---|" if baseline else "") + "")
    base = baseline or {}

    def _row2(name: str, cur: Any, base_val: Any, dkey: str) -> None:
        if baseline:
            lines.append(
                f"| {name} | {cur} | {base_val} | {_delta_cell((delta or {}).get(dkey))} |"
            )
        else:
            lines.append(f"| {name} | {cur} |")

    _row2("召回率", _fmt(metrics["recall"]), _fmt(base.get("recall")), "recall")
    _row2("误报率", _fmt(metrics["fp_rate"]), _fmt(base.get("fp_rate")), "fp_rate")
    _row2("引用准确率", _fmt(citation["accuracy"]),
          _fmt((base.get("citation") or {}).get("accuracy")), "citation_accuracy")
    _row2("条款定位准确率", _fmt(localization["accuracy"]),
          _fmt((base.get("localization") or {}).get("accuracy")), "localization_accuracy")
    _row2("P95 时延 (ms)", latency["p95"],
          (base.get("latency_ms") or {}).get("p95", "—"), "p95_latency_ms")
    _row2("总 token", tokens["total"], (base.get("tokens") or {}).get("total", "—"),
          "tokens_total")
    lines.append("")

    lines.append("## 按 risk_type 分组")
    lines.append("")
    lines.append("| risk_type | 金标 | TP | FN | FP | 召回率 | 误报率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in metrics["per_risk"]:
        lines.append(
            f"| {row['risk_type']} | {row['gold_count']} | {row['tp']} | {row['fn']} "
            f"| {row['fp']} | {_fmt(row['recall'])} | {_fmt(row['fp_rate'])} |"
        )
    lines.append("")

    lines.append("## 引用准确率（人工金标）")
    lines.append("")
    lines.append(f"- 金标 {citation['gold_total']} 条，实际比对 {citation['gold_checked']} 条"
                 f"（系统未召回的不进分母，计为召回缺失）")
    lines.append(f"- 正确 {citation['gold_correct']} 条 → 准确率 {_fmt(citation['accuracy'])}")
    lines.append(f"- 人工抽检比例：{_fmt(citation['manual_sample_ratio'])}"
                 "（金标条数 / 系统全部产出）")
    lines.append("- 已知缺陷注入：`invoice_risk` / `jurisdiction_risk` 为 fragile 类型，"
                 "examiner 刻意引用相邻法条（能过白名单校验、与金标不符）——"
                 "该准确率 <100% 是评测信号，不是 bug。")
    lines.append("")

    lines.append("## 条款定位（span IoU）")
    lines.append("")
    lines.append(f"- 口径：系统条款 span 与金标风险句 span 的 IoU > {IOU_HIT_THRESHOLD} 记命中")
    lines.append(f"- 命中 {localization['iou_hits']} / {localization['matched']}"
                 f" → 准确率 {_fmt(localization['accuracy'])}，平均 IoU {localization['mean_iou']}")
    lines.append("")

    lines.append("## 时延与成本")
    lines.append("")
    lines.append(f"- 端到端时延（ms）：P50 {latency['p50']} / P95 {latency['p95']}"
                 "（P95 为验收口径；墙钟测量受调度噪声影响，质量指标才是逐位可复现的）")
    lines.append(f"- 单份合同 token：输入 {tokens['input_per_contract']}"
                 f" / 输出 {tokens['output_per_contract']}（总 {tokens['total']}）")
    lines.append(f"- 单份合同成本：¥{cost['per_contract']}（总 ¥{cost['total']}）")
    lines.append(f"- 降级合同数：{metrics['degraded_contracts']}")
    lines.append("")

    lines.append("## 复现方式")
    lines.append("")
    lines.append("```bash")
    lines.append("make eval                 # 全量 120 份 + 与 baseline 对比")
    lines.append("make eval-smoke          # 前 10 份冒烟")
    lines.append("# 同一 commit 跑两次：")
    lines.append("make eval LABEL=run1 && make eval LABEL=run2   # 质量指标应逐位一致")
    lines.append("```")
    lines.append("")
    lines.append("数据集由 `python -m eval.dataset.generate` 以固定种子重建，"
                 "docx 经生产 loader+splitter 解析回填金标；改 prompt 后重跑 "
                 "`make eval` 即可在本报告看到涨跌（prompt 词表驱动 examiner 行为）。")
    lines.append("")
    return "\n".join(lines)
