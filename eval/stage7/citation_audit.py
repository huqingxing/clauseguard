"""阶段 7 引用抽检：抽 30 条真实引用，逐条判断「法条原文是否支持结论」。

为什么不用 eval/metrics.py 的金标比对：那份金标的 gold_source_id 是 `src-vat`
这类 mock 池 ID，改造后引用是 `civil_code:第五百八十五条:0:S5_context`，
两套 ID 体系不可比。本次要回答的是语义问题（引用撑不撑得住结论），只能人工读。

抽样单位 = 一条 citation；按合同轮转取样（每份合同先各取 1 条，再取第 2 条…），
保证 30 条尽量铺开在多个合同上，且不依赖随机数，任何人重跑得到同一批样本。

判定口径：
- support     ：条文构成要件/法律后果能直接支撑该结论
- partial     ：主题相关，但只支撑结论的一部分或仅作背景依据
- not_support ：条文与结论无关、含义相反，或引用原文缺失
主报严格准确率 = support / 30；宽松口径 (support+partial)/30 只作参考。
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "eval" / "out" / "stage7"
RESULTS = OUT_DIR / "live" / "results.jsonl"
POOL_PATH = OUT_DIR / "citation_pool.jsonl"
ANNOTATIONS_PATH = Path(__file__).with_name("annotations.jsonl")
REPORT_PATH = OUT_DIR / "citation_audit.md"
SUMMARY_PATH = OUT_DIR / "citation_audit.json"
DSN = "postgresql://clauseguard@127.0.0.1:55432/clauseguard_retrieval?connect_timeout=2"
SAMPLE_SIZE = 30
VALID_VERDICTS = ("support", "partial", "not_support")


def _load_results() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in RESULTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _clause_texts(contract_ids: list[str]) -> dict[str, str]:
    """按生产 loader+splitter 还原条款原文，让标注者看到结论所依据的合同上下文。"""
    from app.parse.loader import load_document
    from app.parse.splitter import split_document

    from eval.runner import load_dataset

    wanted = set(contract_ids)
    by_path = {
        str(r["contract_id"]): REPO_ROOT / str(r["path"])
        for r in load_dataset()
        if str(r["contract_id"]) in wanted
    }
    out: dict[str, str] = {}
    for contract_id, path in by_path.items():
        parsed = split_document(load_document(path))
        for clause in parsed.clauses:
            out[f"{contract_id}|{clause.id}"] = clause.text
    return out


def _law_texts(source_ids: list[str]) -> dict[str, str]:
    """chunk_id → 法条原文；查不到就留空，标注时按 not_support 处理，不臆造。"""
    import psycopg

    out: dict[str, str] = {}
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for source_id in dict.fromkeys(source_ids):
            cur.execute(
                "SELECT law, article, text FROM law_chunk WHERE chunk_id = %s",
                (source_id,),
            )
            row = cur.fetchone()
            if row:
                out[source_id] = f"《{row[0]}》{row[1]}：{row[2]}"
    return out


def _collect(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        for finding in result["findings"]:
            if finding.get("stale"):
                continue
            for citation in finding["citations"]:
                rows.append(
                    {
                        "contract_id": result["contract_id"],
                        "clause_id": finding["clause_id"],
                        "risk_type": finding["risk_type"],
                        "risk_level": finding["risk_level"],
                        "summary": finding["summary"],
                        "source_id": citation["source_id"],
                        "title": citation["title"],
                    }
                )
    return rows


def _round_robin(rows: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["contract_id"]].append(row)
    for group_rows in groups.values():
        group_rows.sort(key=lambda r: (r["clause_id"], r["risk_type"], r["source_id"]))
    picked: list[dict[str, Any]] = []
    depth = 0
    while len(picked) < size:
        added = False
        for contract_id in sorted(groups):
            if depth < len(groups[contract_id]):
                picked.append(groups[contract_id][depth])
                added = True
                if len(picked) == size:
                    break
        if not added:
            break
        depth += 1
    return picked


def build_pool() -> None:
    results = _load_results()
    rows = _collect(results)
    sample = _round_robin(rows, SAMPLE_SIZE)
    if len(sample) < SAMPLE_SIZE:
        raise RuntimeError(
            f"可用引用仅 {len(sample)} 条，不足 {SAMPLE_SIZE} 条；不得凑数"
        )

    clause_texts = _clause_texts([row["contract_id"] for row in sample])
    law_texts = _law_texts([row["source_id"] for row in sample])
    pool: list[dict[str, Any]] = []
    for seq, row in enumerate(sample, start=1):
        pool.append(
            {
                "seq": seq,
                **row,
                "clause_text": clause_texts.get(
                    f"{row['contract_id']}|{row['clause_id']}", ""
                ),
                "law_text": law_texts.get(row["source_id"], ""),
            }
        )
    POOL_PATH.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in pool) + "\n",
        encoding="utf-8",
    )
    all_findings = [f for r in results for f in r["findings"]]
    unverified = sum(1 for f in all_findings if f["risk_level"] == "unverified")
    print(f"[audit] citations_total={len(rows)} sample={len(pool)} -> {POOL_PATH}")
    print(
        f"[audit] findings_total={len(all_findings)} unverified={unverified} "
        "(无引用 finding 不进抽检池，另在报告里单列)"
    )


def render() -> None:
    pool = [
        json.loads(line)
        for line in POOL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    annotations = [
        json.loads(line)
        for line in ANNOTATIONS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_seq = {int(row["seq"]): row for row in annotations}
    problems: list[str] = []
    if len(pool) != SAMPLE_SIZE:
        problems.append(f"抽检池 {len(pool)} 条，应为 {SAMPLE_SIZE}")
    if sorted(by_seq) != list(range(1, SAMPLE_SIZE + 1)):
        problems.append(f"台账 seq 不连续/缺失: {sorted(by_seq)}")
    for seq, row in by_seq.items():
        if row.get("verdict") not in VALID_VERDICTS:
            problems.append(f"seq={seq} verdict 非法: {row.get('verdict')!r}")
        if not str(row.get("reason") or "").strip():
            problems.append(f"seq={seq} 缺判定理由")
    if problems:
        raise AssertionError("台账校验失败: " + "; ".join(problems))

    counts = {v: 0 for v in VALID_VERDICTS}
    for row in annotations:
        counts[row["verdict"]] += 1
    strict = round(counts["support"] / SAMPLE_SIZE, 4)
    loose = round((counts["support"] + counts["partial"]) / SAMPLE_SIZE, 4)

    lines = [
        "# 阶段 7 · 引用准确率人工抽检（改造后 / live 检索）",
        "",
        f"- 抽样：live 组 30 份合同产出引用中按合同轮转取 {SAMPLE_SIZE} 条，非随机、可复现。",
        (
            "- 判定：`support` 条文直接支撑结论；`partial` 主题相关但只支撑一部分；"
            "`not_support` 无关/相反/原文缺失。"
        ),
        (
            f"- 严格准确率（support/30）= **{strict:.2%}**；"
            f"宽松准确率（(support+partial)/30）= {loose:.2%}。"
        ),
        (
            f"- 分布：support={counts['support']} partial={counts['partial']} "
            f"not_support={counts['not_support']}。"
        ),
        (
            "- 标注者：单一标注者（本仓库维护者视角），未做多人独立标注与一致性检验，"
            "见 `docs/rag-lab.md` 局限章节。"
        ),
        "",
        "| # | 合同/条款 | 结论（摘要） | 引用 | 判定 | 理由 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in pool:
        verdict = by_seq[row["seq"]]["verdict"]
        reason = by_seq[row["seq"]]["reason"]
        summary = str(row["summary"]).replace("|", "／").replace("\n", " ")[:60]
        law = str(row["title"]).replace("|", "／")
        lines.append(
            f"| {row['seq']} | {row['contract_id']} / {row['clause_id']} | {summary} | "
            f"{law} | {verdict} | {reason} |"
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    SUMMARY_PATH.write_text(
        json.dumps(
            {
                "sample": SAMPLE_SIZE,
                "counts": counts,
                "strict_accuracy": strict,
                "loose_accuracy": loose,
                "annotators": 1,
                "sampling": "round-robin by contract, deterministic",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[audit] strict={strict} loose={loose} counts={counts} -> {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段 7 引用抽检")
    parser.add_argument("--build", action="store_true", help="生成 30 条待抽检池")
    parser.add_argument("--render", action="store_true", help="校验台账并渲染明细表")
    args = parser.parse_args()
    if args.build:
        build_pool()
    if args.render:
        render()
    if not args.build and not args.render:
        parser.print_help()


if __name__ == "__main__":
    main()
