"""20 份样本人工抽检脚本：输出条款边界对照表 + 切片完整性 + 边界正确率。

为什么单独成脚本：验收要求「人工抽检条款边界正确率 ≥ 90%」，脚本把解析结果摊开成对照表供人审，
并在提供 manifest（专家标注的 expected no/level/start/end）时自动算正确率。切片完整性（硬约束）
在这里也会复查，作为自动化兜底。
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from app.parse.loader import load_document
from app.parse.models import Clause, ParseError
from app.parse.splitter import split_document


def discover_samples(root: Path) -> list[Path]:
    """递归收集 PDF/DOCX 样本（排除临时文件）。"""
    if root.is_file():
        return [root]
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in (".pdf", ".docx", ".doc") and not p.name.startswith("~"):
            out.append(p)
    return out


def _load_manifest(path: Path) -> dict[str, list[tuple[str, int, int, int]]]:
    """manifest CSV：source_stem,no,level,start,end -> 期望边界。"""
    table: dict[str, list[tuple[str, int, int, int]]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            stem = row["source"].rsplit(".", 1)[0]
            table.setdefault(stem, []).append(
                (row["no"], int(row["level"]), int(row["start"]), int(row["end"]))
            )
    return table


def build_report(
    samples: list[Path], manifest: dict[str, list[tuple[str, int, int, int]]] | None = None
) -> tuple[str, dict[str, object]]:
    """生成对照表 Markdown 与汇总统计；manifest 非空时计算边界正确率。"""
    manifest = manifest or {}
    lines: list[str] = []
    lines.append("# 条款切分抽检对照表\n")
    total_clauses = 0
    total_slice_ok = 0
    boundary_checks = 0
    boundary_ok = 0
    type_dist: dict[str, int] = {}

    for sample in samples:
        try:
            doc = load_document(sample)
            result = split_document(doc)
        except ParseError as exc:
            lines.append(f"## {sample.name}\n> 解析失败：{exc}\n")
            continue

        stem = sample.stem
        slice_ok = sum(1 for c in result.clauses if result.raw[c.start : c.end] == c.text)
        total_clauses += len(result.clauses)
        total_slice_ok += slice_ok
        for c in result.clauses:
            type_dist[c.type.value] = type_dist.get(c.type.value, 0) + 1

        slice_note = f"切片完整 {slice_ok}/{len(result.clauses)}"
        lines.append(
            f"## {sample.name}（{result.doc_type}，条款 {len(result.clauses)}，{slice_note}）\n"
        )
        lines.append("| no | level | type | page | start | end | 文本预览 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for c in result.clauses:
            preview = c.text.replace("\n", " ")[:48]
            row = (
                f"| {c.no or '-'} | {c.level} | {c.type.value} | {c.page} "
                f"| {c.start} | {c.end} | {preview} |"
            )
            lines.append(row)

        expected = manifest.get(stem)
        if expected:
            by_no: dict[str, Clause] = {c.no: c for c in result.clauses}
            for no, level, start, end in expected:
                boundary_checks += 1
                clause = by_no.get(no)
                if clause and clause.level == level and clause.start == start and clause.end == end:
                    boundary_ok += 1
            acc = boundary_ok / boundary_checks if boundary_checks else 1.0
            accuracy = f"边界正确率（manifest 比对）：{boundary_ok}/{boundary_checks} = {acc:.1%}"
            lines.append(f"\n> {accuracy}\n")

    stats: dict[str, object] = {
        "samples": len(samples),
        "total_clauses": total_clauses,
        "slice_integrity": (total_slice_ok / total_clauses) if total_clauses else 1.0,
        "type_distribution": type_dist,
        "boundary_accuracy": (boundary_ok / boundary_checks) if boundary_checks else None,
    }
    lines.append("\n## 汇总\n")
    lines.append(f"- 样本数：{stats['samples']}")
    lines.append(f"- 条款总数：{stats['total_clauses']}")
    lines.append(f"- 切片完整性（raw[start:end]==text）：{stats['slice_integrity']:.1%}")
    lines.append(f"- 类型分布：{type_dist}")
    if stats["boundary_accuracy"] is not None:
        lines.append(f"- 边界正确率（manifest）：{stats['boundary_accuracy']:.1%}")
    return "\n".join(lines), stats


def main() -> None:  # pragma: no cover - CLI 入口
    parser = argparse.ArgumentParser(description="ClauseGuard 条款切分抽检")
    parser.add_argument("--root", required=True, type=Path, help="样本目录或单文件")
    parser.add_argument("--manifest", type=Path, default=None, help="可选：专家标注边界 CSV")
    args = parser.parse_args()

    samples = discover_samples(args.root)
    manifest = _load_manifest(args.manifest) if args.manifest else None
    report, _ = build_report(samples, manifest)
    print(report)  # noqa: T201  CLI 输出


if __name__ == "__main__":  # pragma: no cover
    main()
