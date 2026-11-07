"""阶段 3 可执行验收：结构 / 分布 / gold↔语料 / 5 策略覆盖 / 抽样复核材料。

用法（任意目录）：
    uv run --project python-agent python lab/dataset/validate.py

为什么把「索引有效性」也放进标注集验收：gold 写的是 S1_whole 的 chunk_id，
如果某条法条在 S2/S3/S4/S5 下压根没产出任何 chunk，那这条标注在对照实验里
就是死标签，实验指标会被无声拉低。宁可在标注阶段就逐策略确认覆盖。

验收输出即交付物（要贴进报告），CLI 打印是意图而非疏漏；全局 T20 规则在此豁免。
"""

# ruff: noqa: T201

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent
REPO_ROOT = DATA_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "python-agent"))
sys.path.insert(0, str(REPO_ROOT / "lab"))

from metrics import Case, evaluate, is_hit, parse_chunk_id  # noqa: E402

from retrieval.chunker import ALL_STRATEGIES, LAW_CODES, all_strategies  # noqa: E402
from retrieval.corpus.loader import load_corpus  # noqa: E402

QUERIES_FILE = DATA_DIR / "queries.jsonl"
REVIEW_FILE = DATA_DIR / "review_sample.md"
SEED = 20261107
SAMPLE_N = 10
EXPECTED_TOTAL = 80
SCENES = ("subject", "commercial", "liability", "ip", "data", "compliance")
TYPES = ("concept", "scene", "colloquial")
EXPECTED_DIST = {"concept": 32, "scene": 24, "colloquial": 24}


def _table(title: str, headers: tuple[str, ...], rows: list[list[object]]) -> None:
    print(f"\n===== {title} =====")
    widths = [
        max([len(str(header)), *(len(str(row[i])) for row in rows)])
        for i, header in enumerate(headers)
    ]
    print("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)))


def load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(QUERIES_FILE.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        obj = json.loads(line)
        if set(obj) != {"qid", "query", "gold", "scene", "type"}:
            raise AssertionError(f"第 {lineno} 行字段不符: {sorted(obj)}")
        rows.append(obj)
    return rows


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))

    rows = load_rows()
    entries = load_corpus()
    # (law, article) -> 原文；law_code 反查表用于确认 gold 的法律名没写串。
    corpus: dict[tuple[str, str], str] = {(e["law"], e["article"]): e["text"] for e in entries}
    code_to_law = {v: k for k, v in LAW_CODES.items()}
    # 统计每个条号被几部法律共用：只比 article 时，跨法同条号会造成误命中。
    laws_per_article: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        laws_per_article[e["article"]].add(e["law"])

    # ---- 1. 结构 ----
    qids = [str(row["qid"]) for row in rows]
    check("总条数 = 80", len(rows) == EXPECTED_TOTAL, f"实际 {len(rows)}")
    check(
        "qid 唯一且格式 qNNN",
        len(set(qids)) == len(rows) and all(re.fullmatch(r"q\d{3}", q) for q in qids),
        f"{qids[0]} .. {qids[-1]}",
    )
    check(
        "query 非空且互不重复",
        all(str(row["query"]).strip() for row in rows)
        and len({row["query"] for row in rows}) == len(rows),
        f"唯一查询 {len({row['query'] for row in rows})} 条",
    )
    check(
        "scene / type 取值合法",
        all(row["scene"] in SCENES and row["type"] in TYPES for row in rows),
        f"{sorted({r['scene'] for r in rows})} / {sorted({r['type'] for r in rows})}",
    )
    gold_sizes = [len(row["gold"]) for row in rows]
    check(
        "每条 gold 1–3 个且组内不重复",
        all(1 <= n <= 3 for n in gold_sizes)
        and all(len(set(row["gold"])) == len(row["gold"]) for row in rows),
        f"分布 {dict(Counter(gold_sizes))}",
    )

    # ---- 2. 分布表 ----
    cross: dict[tuple[str, str], int] = Counter((row["scene"], row["type"]) for row in rows)
    dist_rows: list[list[object]] = []
    for scene in SCENES:
        line: list[object] = [scene]
        line.extend(cross[(scene, qtype)] for qtype in TYPES)
        line.append(sum(cross[(scene, qtype)] for qtype in TYPES))
        dist_rows.append(line)
    total_row: list[object] = ["合计"]
    total_row.extend(sum(cross[(scene, qtype)] for scene in SCENES) for qtype in TYPES)
    total_row.append(EXPECTED_TOTAL)
    dist_rows.append(total_row)
    _table(
        "场景 × 问法类型分布（每场景 ≥ 10）",
        ("scene", "concept", "scene", "colloquial", "合计"),
        dist_rows,
    )
    check(
        "6 场景各 ≥ 10 条",
        all(sum(cross[(s, t)] for t in TYPES) >= 10 for s in SCENES),
        ", ".join(f"{s}={sum(cross[(s, t)] for t in TYPES)}" for s in SCENES),
    )
    type_counts = Counter(row["type"] for row in rows)
    pct = {t: f"{type_counts[t] / len(rows):.0%}" for t in TYPES}
    check(
        "问法类型 40% / 30% / 30%",
        all(type_counts[t] == EXPECTED_DIST[t] for t in TYPES),
        f"{dict(type_counts)} -> {pct}",
    )

    # ---- 3. gold 的 article 必须能在语料找到 ----
    failures: list[str] = []
    gold_pairs: set[tuple[str, str]] = set()
    for row in rows:
        for chunk_id in row["gold"]:
            try:
                ref = parse_chunk_id(str(chunk_id))
            except ValueError as exc:
                failures.append(f"{row['qid']}: {exc}")
                continue
            law = code_to_law.get(ref.law_code)
            if law is None:
                failures.append(f"{row['qid']}: 未知 law_code {ref.law_code}")
                continue
            if ref.strategy != "S1_whole" or ref.idx != "0":
                failures.append(f"{row['qid']}: 策略/下标异常 {chunk_id}")
                continue
            if (law, ref.article) not in corpus:
                failures.append(f"{row['qid']}: 语料无 {law}:{ref.article}")
                continue
            gold_pairs.add((law, ref.article))
    check(
        "每条 gold 的 article 都能在语料找到",
        not failures,
        f"校验 {sum(gold_sizes)} 个 gold，失败 {len(failures)} 个"
        + (f"；首例 {failures[0]}" if failures else ""),
    )

    # ---- 4. 5 种切分策略都要覆盖 gold 对应条 ----
    strategy_chunks = all_strategies(entries)
    coverage: dict[str, dict[tuple[str, str], list[str]]] = {}
    for strategy, chunks in strategy_chunks.items():
        index: dict[tuple[str, str], list[str]] = defaultdict(list)
        for chunk in chunks:
            index[(chunk.law, chunk.article)].append(chunk.chunk_id)
        coverage[strategy.value] = index
    missing: list[str] = []
    for law, article in sorted(gold_pairs):
        for strategy in ALL_STRATEGIES:
            if not coverage[strategy.value].get((law, article)):
                missing.append(f"{law}:{article} 缺 {strategy.value}")
    check(
        "gold 对应条在 5 种切分下均可覆盖",
        not missing,
        f"{len(gold_pairs)} 个不同 (law, article) × 5 策略 = {len(gold_pairs) * 5} 项，"
        f"缺失 {len(missing)} 项",
    )
    cover_rows = [
        [
            s.value,
            len(strategy_chunks[s]),
            sum(1 for pair in gold_pairs if coverage[s.value].get(pair)),
        ]
        for s in ALL_STRATEGIES
    ]
    _table(
        "5 种切分策略的 chunk 总数与 gold 覆盖数",
        ("strategy", "全库 chunk 数", "覆盖 gold 条数"),
        cover_rows,
    )

    # ---- 5. 跨法同条号风险量化（只报告，不判失败） ----
    ambiguous = sorted(
        (law, article) for law, article in gold_pairs if len(laws_per_article[article]) > 1
    )
    print(
        f"\n[口径提示] gold 中「条号被多部法律共用」的条目 = {len(ambiguous)}"
        f" / {len(gold_pairs)}；只用 article 判命中时这些条目存在跨法误命中风险，"
        f"可用 metrics.match_key(mode='law_article') 做稳健性对照。"
    )

    # ---- 6. 命中判定口径自检 ----
    sample_gold = ["civil_code:第五百八十五条:0:S1_whole"]
    other_strategy = ["civil_code:第五百八十五条:2:S3_fixed256"]
    unrelated = ["civil_code:第五百七十七条:0:S1_whole"]
    check(
        "不同策略、同 article 算命中（核心口径）",
        is_hit(other_strategy, sample_gold, 5),
        "S3_fixed256:2 vs gold S1_whole:0 -> hit",
    )
    check(
        "不同 article 不算命中",
        not is_hit(unrelated, sample_gold, 5),
        "第577条 vs gold 第585条 -> miss",
    )
    demo = evaluate(
        [
            Case("q001", other_strategy, sample_gold, "liability", "concept"),
            Case("q002", unrelated, sample_gold, "liability", "concept"),
        ]
    )
    check(
        "evaluate 宏平均可算且不崩溃",
        demo["overall"]["hit_rate"] == 0.5 and demo["overall"]["mrr"] == 0.5,
        f"hit_rate={demo['overall']['hit_rate']} mrr={demo['overall']['mrr']}",
    )

    # ---- 7. 抽样 10 条，输出人工复核材料 ----
    picked = random.Random(SEED).sample(rows, SAMPLE_N)
    print(f"\n===== 人工复核抽样 {SAMPLE_N} 条（seed={SEED}）=====")
    review_lines: list[str] = [
        f"# 阶段 3 标注人工复核抽样（seed={SEED}，共 {SAMPLE_N} 条）",
        "",
        "复核方式：逐条对照 `python-agent/retrieval/corpus/*.jsonl` 中的法条原文，",
        "判断「query 问的是不是 gold 指向的那条」，以及多 gold 是否都属于同一答案。",
        "",
    ]
    for row in picked:
        print(f"\n  {row['qid']} [{row['scene']}/{row['type']}] {row['query']}")
        review_lines.append(
            f"\n## {row['qid']} · {row['scene']}/{row['type']}\n\n- query：{row['query']}"
        )
        for chunk_id in row["gold"]:
            ref = parse_chunk_id(chunk_id)
            law = code_to_law[ref.law_code]
            text = corpus[(law, ref.article)].splitlines()[0][:88]
            print(f"    gold {chunk_id}")
            print(f"    原文 {law} {ref.article}：{text}…")
            review_lines.append(f"- gold：`{chunk_id}` → {law} {ref.article}")
            review_lines.append(f"  - 原文首句：{text}…")
        # 脚本只负责把「问法 + gold + 原文」摆在一起；语义是否对得上必须由人判断，
        # 让代码自己写「✔ 正确」等于自己给自己盖章，复核就失去意义了。
        review_lines.append("- 人工复核结论：________（复核人对照上方原文填写）")
    REVIEW_FILE.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    print(f"\n    复核材料已写入 {REVIEW_FILE}")

    # ---- 结论 ----
    _table(
        "验收结论",
        ("检查项", "结论", "说明"),
        [[name, "PASS" if ok else "FAIL", detail] for name, ok, detail in checks],
    )
    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        print(f"\n===== 验收结论：FAIL（{len(failed)} 项）=====")
        return 1
    print(
        f"\n共 {len(checks)} 项检查，全部 PASS；数据集 {len(rows)} 条，"
        f"不同 gold 法条 {len(gold_pairs)} 条"
    )
    print("===== 验收结论：全部 PASS =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
