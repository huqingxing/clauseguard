# ruff: noqa: T201, E402
"""阶段 4 / 5 验收：结构断言 + 两次运行的逐 query 指标比对。

为什么比「逐 query 明细」而不比报告里的 macro 均值：均值会被四舍五入掩盖
单条 query 的排序漂移，而可复现性要求的是同一批题目得到同一批结果。

阶段 5 额外断言三件事：off 基线必须与阶段 4 最优组合逐 query 一致（证明「固定
最优组合」没有把融合候选池偷偷换掉）、top-N→top-k 契约真的成立、真实调用
确实产生了 token 消耗（而不是拿离线代理冒充 live）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

FIELDS = ("recall", "mrr", "ndcg", "hit")


def check_structure(metrics: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    config = metrics["config"]
    strategies, embeddings = list(config["strategies"]), list(config["embeddings"])
    combos = metrics["combinations"]
    expected = {f"{s}|{e}" for s in strategies for e in embeddings}
    actual = [item["key"] for item in combos]
    if len(actual) != len(expected):
        problems.append(f"组合数 {len(actual)} != 期望 {len(expected)}（有组合被删或重复）")
    if set(actual) != expected:
        problems.append(
            f"矩阵覆盖不全：缺 {sorted(expected - set(actual))}，"
            f"多 {sorted(set(actual) - expected)}"
        )
    if len(set(actual)) != len(actual):
        problems.append("存在重复组合 key")

    n_queries = int(metrics["dataset"]["n"])
    for item in combos:
        key = item["key"]
        if item["status"] == "failed":
            if not item.get("error"):
                problems.append(f"{key} 标记 failed 却没有失败原因")
            continue  # 失败组合必须保留，但不要求有 80 条结果
        if item["n_queries"] != n_queries:
            problems.append(f"{key} 跑了 {item['n_queries']} 条，应为 {n_queries}")
        rows = item["summary"]["per_query"]
        if len(rows) != n_queries:
            problems.append(f"{key} per_query 只有 {len(rows)} 条")
        if {row["qid"] for row in rows} != {
            row["qid"] for row in combos[0]["summary"]["per_query"]
        }:
            problems.append(f"{key} 的 qid 集合与其他组合不一致")
        for row in rows:
            for field in FIELDS:
                value = float(row[field])
                if not 0.0 <= value <= 1.0 + 1e-9:
                    problems.append(f"{key}/{row['qid']} {field}={value} 越界")
    if not metrics.get("seed"):
        problems.append("未记录 seed")
    return problems


def compare(left: dict[str, Any], right: dict[str, Any]) -> tuple[list[str], float]:
    problems: list[str] = []
    for field in ("seed", "provider_mode"):
        if left.get(field) != right.get(field):
            problems.append(f"两次运行的 {field} 不一致：{left.get(field)} vs {right.get(field)}")
    for field in ("strategies", "embeddings", "k", "candidate_k", "rerank", "bm25"):
        if left["config"].get(field) != right["config"].get(field):
            problems.append(f"两次运行的 config.{field} 不一致")
    if left["dataset"]["sha256"] != right["dataset"]["sha256"]:
        problems.append("两次运行的数据集 sha256 不一致")

    left_map = {item["key"]: item for item in left["combinations"]}
    right_map = {item["key"]: item for item in right["combinations"]}
    max_diff = 0.0
    for key in sorted(left_map):
        a, b = left_map[key], right_map.get(key)
        if b is None:
            problems.append(f"第二次运行缺少组合 {key}")
            continue
        if a["status"] != b["status"]:
            problems.append(f"{key} 状态从 {a['status']} 变成 {b['status']}")
            continue
        if a["status"] != "ok":
            continue
        ra = {row["qid"]: row for row in a["summary"]["per_query"]}
        rb = {row["qid"]: row for row in b["summary"]["per_query"]}
        if set(ra) != set(rb):
            problems.append(f"{key} 两次运行的 qid 集合不一致")
            continue
        for qid in sorted(ra):
            for field in FIELDS:
                diff = abs(float(ra[qid][field]) - float(rb[qid][field]))
                max_diff = max(max_diff, diff)
                if diff != 0.0:
                    problems.append(f"{key}/{qid}/{field} 差异 {diff}")
    return problems, max_diff


RERANK_FIELDS = (
    "off_recall",
    "on_recall",
    "off_mrr",
    "on_mrr",
    "off_ndcg",
    "on_ndcg",
    "off_first_hit_rank",
)


def _best_combo(matrix: dict[str, Any]) -> dict[str, Any]:
    ok = [item for item in matrix["combinations"] if item["status"] == "ok"]
    return sorted(
        ok,
        key=lambda item: (
            -float(item["summary"]["overall"]["recall"]),
            -float(item["summary"]["overall"]["mrr"]),
            -float(item["summary"]["overall"]["ndcg"]),
            item["key"],
        ),
    )[0]


def check_rerank(rr: dict[str, Any], matrix: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if rr.get("status") != "ok":
        problems.append(f"rerank 实验 status={rr.get('status')}，error={rr.get('error')}")
    n_queries = int(matrix["dataset"]["n"])
    rows = rr["per_query"]
    if len(rows) != n_queries:
        problems.append(f"rerank per_query {len(rows)} 条 != 数据集 {n_queries} 条")

    contract = rr["contract"]
    if contract["observed_candidates_min"] < contract["candidates"]:
        problems.append(
            f"候选池不足：最少 {contract['observed_candidates_min']} < {contract['candidates']}"
        )
    if contract["observed_ranked_min"] < contract["candidates"]:
        problems.append(
            f"重排结果不足 top-{contract['candidates']}：最少 {contract['observed_ranked_min']}"
        )
    if contract["observed_top5_max"] != contract["top_n"]:
        problems.append(
            f"对外输出不是 top-{contract['top_n']}：实测 {contract['observed_top5_max']}"
        )
    bad_top5 = [
        row["qid"]
        for row in rows
        if not row.get("failed") and row.get("top5_n") != contract["top_n"]
    ]
    if bad_top5:
        problems.append(f"逐 query 输出 top-{contract['top_n']} 不成立：{bad_top5[:5]}")

    best = _best_combo(matrix)
    if rr["fixed"]["key"] != best["key"]:
        problems.append(f"固定组合 {rr['fixed']['key']} 与矩阵最优 {best['key']} 不一致")
    stage4 = {row["qid"]: row for row in best["summary"]["per_query"]}
    baseline_diffs = [
        row["qid"]
        for row in rows
        if abs(float(row["off_recall"]) - float(stage4[row["qid"]]["recall"])) > 1e-12
    ]
    if baseline_diffs:
        problems.append(
            f"off 基线 recall 与阶段 4 不一致 {len(baseline_diffs)} 条：{baseline_diffs[:5]}"
        )

    for row in rows:
        for field in RERANK_FIELDS:
            if field == "off_first_hit_rank":
                continue
            value = float(row[field])
            if not 0.0 <= value <= 1.0 + 1e-9:
                problems.append(f"{row['qid']} {field}={value} 越界")

    usage = rr["usage"]
    if rr["config"]["reranker"] == "live":
        if int(usage["tokens"]) <= 0:
            problems.append("live 模式 tokens=0，疑似未真实调用或用离线结果冒充")
        if int(usage["requests"]) <= 0:
            problems.append("live 模式 requests=0")
        if float(usage["live_latency_ms_total"]) <= 0:
            problems.append("live 模式未记录真实调用延迟")
    elif int(usage["tokens"]) != 0:
        problems.append("deterministic 模式不应产生 tokens")

    table = rr["tradeoff"]
    if len(table) < 2 or table[0]["policy"] != "全关" or table[1]["policy"] != "全开":
        problems.append("取舍表缺少「全关 / 全开」边界行")
    else:
        if abs(table[0]["recall"] - rr["off"]["overall"]["recall"]) > 1e-12:
            problems.append("取舍表「全关」recall 与 off 基线不一致")
        if abs(table[1]["recall"] - rr["on"]["overall"]["recall"]) > 1e-12:
            problems.append("取舍表「全开」recall 与 on 结果不一致")

    from lab.report import _recommendation  # noqa: PLC0415 - 避免 verify 顶层依赖报告渲染

    verdict, reason = _recommendation(rr)
    if verdict not in ("开", "关", "条件开"):
        problems.append(f"推荐结论不是三选一：{verdict}")
    if not reason:
        problems.append("推荐结论缺少数据依据")

    if rr["failed_queries"] and rr["status"] == "ok" and len(rr["failed_queries"]) == len(rows):
        problems.append("全部 query 失败却标记 status=ok")
    return problems


def compare_rerank(left: dict[str, Any], right: dict[str, Any]) -> tuple[list[str], float]:
    problems: list[str] = []
    for field in ("seed", "fixed", "config", "contract"):
        if left.get(field) != right.get(field):
            problems.append(f"两次 rerank 运行的 {field} 不一致")
    a_rows = {row["qid"]: row for row in left["per_query"]}
    b_rows = {row["qid"]: row for row in right["per_query"]}
    if set(a_rows) != set(b_rows):
        return ["两次 rerank 运行的 qid 集合不一致"], 1.0
    max_diff = 0.0
    for qid in sorted(a_rows):
        for field in RERANK_FIELDS:
            av, bv = a_rows[qid][field], b_rows[qid][field]
            if field == "off_first_hit_rank":
                if str(av) != str(bv):
                    problems.append(f"{qid}/{field} 差异 {av} vs {bv}")
                continue
            diff = abs(float(av) - float(bv))
            max_diff = max(max_diff, diff)
            if diff != 0.0:
                problems.append(f"{qid}/{field} 差异 {diff}")
    for side in ("off", "on"):
        if abs(left[side]["overall"]["recall"] - right[side]["overall"]["recall"]) > 1e-12:
            problems.append(f"{side} macro recall 两次不一致")
    return problems, max_diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段 4 / 5 验收")
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--against", default="")
    parser.add_argument("--rerank", default="", help="阶段 5 rerank.json")
    parser.add_argument("--rerank-against", default="", help="第二次 rerank.json")
    args = parser.parse_args(argv)

    primary = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    problems = check_structure(primary)
    combos = primary["combinations"]
    ok = [item for item in combos if item["status"] == "ok"]
    failed = [item for item in combos if item["status"] == "failed"]
    print(
        f"[结构] 组合 {len(combos)}（成功 {len(ok)} / 失败 {len(failed)}），"
        f"数据集 {primary['dataset']['n']} 条，seed={primary['seed']}，"
        f"provider={primary['provider_mode']}"
    )
    print(
        f"[结构] 成功组合每组 per_query 条数："
        f"{sorted({len(i['summary']['per_query']) for i in ok})}"
    )
    print(f"[结构] 问题数 = {len(problems)}")

    if args.against:
        other = json.loads(Path(args.against).read_text(encoding="utf-8"))
        diff_problems, max_diff = compare(primary, other)
        print(f"[重跑] 逐 query 比对字段 = {FIELDS}")
        print(f"[重跑] MAX_RECALL_DIFF = {max_diff:.17g}")
        print(f"[重跑] 问题数 = {len(diff_problems)}")
        problems.extend(diff_problems)

    if args.rerank:
        rr = json.loads(Path(args.rerank).read_text(encoding="utf-8"))
        rr_problems = check_rerank(rr, primary)
        off, on = rr["off"]["overall"], rr["on"]["overall"]
        print(
            f"[阶段5] 固定组合 {rr['fixed']['strategy']}|{rr['fixed']['embedding']} "
            f"reranker={rr['config']['reranker']} 候选 top-{rr['contract']['candidates']}"
            f"→top-{rr['contract']['top_n']} query={len(rr['per_query'])}"
        )
        print(
            f"[阶段5] off recall={off['recall']:.4f} → on recall={on['recall']:.4f} "
            f"(Δ={(on['recall'] - off['recall']) * 100:+.2f}pp) "
            f"tokens={rr['usage']['tokens']} 失败={len(rr['failed_queries'])}"
        )
        print(f"[阶段5] 问题数 = {len(rr_problems)}")
        problems.extend(rr_problems)

        if args.rerank_against:
            rr2 = json.loads(Path(args.rerank_against).read_text(encoding="utf-8"))
            dp, md = compare_rerank(rr, rr2)
            print(f"[阶段5重跑] 逐 query 比对字段 = {list(RERANK_FIELDS)}")
            print(f"[阶段5重跑] MAX_RERANK_DIFF = {md:.17g}")
            print(f"[阶段5重跑] 问题数 = {len(dp)}")
            problems.extend(dp)

    for problem in problems[:20]:
        print(f"  ! {problem}")
    print("VERIFY_RESULT =", "PASS" if not problems else "FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
