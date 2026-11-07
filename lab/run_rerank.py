# ruff: noqa: T201, E402
"""阶段 5｜rerank 收益实验：固定最优组合，唯一变量是 rerank on/off。

## 为什么固定组合要由 metrics.json 自动选出

阶段 4 的结论明确写了「最优与次优差异不显著」。若把 `S5_context|E3` 硬编码
进脚本，等于悄悄把一个统计上不成立的选择包装成既定事实；从报告数据按
`report._rank()` 同一套确定性规则读取，并把这个来源写进 rerank.json，才能让
「固定最优」可复核、换一组矩阵结果时无需改代码。

## 为什么 off / on 都取完整 top-20

`summarize()` 用 retrieved[:5] 算 recall、[:10] 算 mrr/ndcg。基线若只保留 5 条，
@10 指标在 off 侧被削平、on 侧却有 10 条，差值里就混进了口径差而不是排序质量。
两侧都从同一个 top-20 候选池出发，recall@5 仍与阶段 4 基线逐位一致（由
verify.py 硬断言），@10 则第一次有了真实区分度。

## 为什么延迟要拆成「检索」与「rerank」两段

真实 API 首次调用约 1.4s，而 rank 缓存命中后只有亚毫秒。若把两次重跑混在一起
统计，「延迟增加多少」会随缓存命中率漂移。这里只把首次真实调用的耗时计入
`live_latency_ms_total`，取舍表用的是这个稳定口径，并在报告里注明缓存用途。

## 为什么「条件开」用可执行信号而不是预知 gold

回答「gold 已在 top-3 时不值得」只能用离线分组来描述现象；真要落地，路由阶段
拿不到 gold。取舍表因此额外给出两条线上可判定的策略：按 query 类型、按基线
top-1 融合分数分位。前者零成本，后者只需一次 argmax 比较。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from lab import index as ix  # noqa: E402
from lab import providers as pv  # noqa: E402
from lab import report as rp  # noqa: E402
from lab import run as matrix_run  # noqa: E402
from lab.metrics import (  # noqa: E402
    Case,
    gold_keys,
    match_key,
    paired_bootstrap_ci,
    summarize,
)
from lab.rerank import (  # noqa: E402
    DEFAULT_CANDIDATES,
    DEFAULT_TOP_N,
    RERANK_ENDPOINT,
    RERANK_MODEL,
    RerankUsage,
    make_reranker,
)
from retrieval.service import Hit  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检索实验台阶段 5：rerank 收益实验")
    parser.add_argument("--label", default="v1")
    parser.add_argument("--metrics", default="", help="阶段 4 metrics.json，用于选最优组合")
    parser.add_argument("--out", default="")
    parser.add_argument("--reranker", choices=("live", "deterministic"), default="live")
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES)
    parser.add_argument("--k", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--seed", type=int, default=pv.DEFAULT_SEED)
    parser.add_argument("--limit", type=int, default=0, help="调试用：只跑前 N 条（0=全部）")
    return parser.parse_args(argv)


def select_best(metrics: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """复用 report._rank 的确定性规则，避免「固定最优」变成硬编码。"""
    ok = [item for item in metrics["combinations"] if item["status"] == "ok"]
    if not ok:
        raise ValueError("阶段 4 矩阵无成功组合，无法固定最优 (strategy, embedding)")
    ranked = sorted(
        ok,
        key=lambda item: (
            -float(item["summary"]["overall"]["recall"]),
            -float(item["summary"]["overall"]["mrr"]),
            -float(item["summary"]["overall"]["ndcg"]),
            item["key"],
        ),
    )
    best = ranked[0]
    evidence = {
        "key": best["key"],
        "stage4_recall": float(best["summary"]["overall"]["recall"]),
        "stage4_mrr": float(best["summary"]["overall"]["mrr"]),
        "stage4_ndcg": float(best["summary"]["overall"]["ndcg"]),
        "runner_up": ranked[1]["key"] if len(ranked) > 1 else None,
        "rule": "按 (recall desc, mrr desc, ndcg desc, key asc) 排序取第一，与 report._rank 同源",
        "caveat": "阶段 4 结论已注明最优与次优差异不显著；此处固定只为控制变量，不代表选型结论",
    }
    return str(best["strategy"]), str(best["embedding"]), evidence


def first_hit_rank(hits: list[Hit], gold: list[str]) -> int | None:
    keys = gold_keys(gold)
    for rank, hit in enumerate(hits, start=1):
        if match_key(hit) in keys:
            return rank
    return None


def _hit_score(hit: Hit) -> float:
    return float(getattr(hit, "score", 0.0) or 0.0)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[pos]


def _group_stats(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[key]), []).append(row)
    out: list[dict[str, Any]] = []
    for name in sorted(groups):
        subset = groups[name]
        off_recall = sum(row["off_recall"] for row in subset) / len(subset)
        on_recall = sum(row["on_recall"] for row in subset) / len(subset)
        out.append(
            {
                "name": name,
                "n": len(subset),
                "off_recall": off_recall,
                "on_recall": on_recall,
                "delta_recall": on_recall - off_recall,
                "off_mrr": sum(row["off_mrr"] for row in subset) / len(subset),
                "on_mrr": sum(row["on_mrr"] for row in subset) / len(subset),
                "off_ndcg": sum(row["off_ndcg"] for row in subset) / len(subset),
                "on_ndcg": sum(row["on_ndcg"] for row in subset) / len(subset),
                "rerank_latency_mean_ms": sum(row["rerank_latency_ms"] for row in subset)
                / len(subset),
                "tokens_per_query": sum(row["tokens"] for row in subset) / len(subset),
            }
        )
    return out


def build_tradeoff(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """延迟 vs 召回取舍表。每行是一条可落地的开关策略。

    条件开只在「触发」的 query 上付出 rerank 延迟与 tokens；未触发的 query
    用 off 侧结果。触发信号一律取自 off 侧（线上可得），不使用 gold。
    """
    scores = [row["off_top1_score"] for row in rows]
    quantiles = {f"p{int(q * 100)}": _quantile(scores, q) for q in (0.25, 0.5, 0.75)}
    policies: list[tuple[str, str, Any]] = [
        ("全关", "baseline", lambda row: False),
        ("全开", "all", lambda row: True),
        ("条件开：非概念型", "type != concept", lambda row: row["type"] != "concept"),
        (
            "条件开：top1 分数 < P25",
            f"score < {quantiles['p25']:.6f}",
            lambda row, t=quantiles["p25"]: row["off_top1_score"] < t,
        ),
        (
            "条件开：top1 分数 < P50",
            f"score < {quantiles['p50']:.6f}",
            lambda row, t=quantiles["p50"]: row["off_top1_score"] < t,
        ),
        (
            "条件开：top1 分数 < P75",
            f"score < {quantiles['p75']:.6f}",
            lambda row, t=quantiles["p75"]: row["off_top1_score"] < t,
        ),
    ]
    table: list[dict[str, Any]] = []
    for name, threshold, predicate in policies:
        triggered = [predicate(row) for row in rows]
        recalls = [
            row["on_recall"] if flag else row["off_recall"]
            for row, flag in zip(rows, triggered, strict=True)
        ]
        mrrs = [
            row["on_mrr"] if flag else row["off_mrr"]
            for row, flag in zip(rows, triggered, strict=True)
        ]
        latencies = [
            row["off_latency_ms"] + (row["rerank_latency_ms"] if flag else 0.0)
            for row, flag in zip(rows, triggered, strict=True)
        ]
        tokens = [row["tokens"] if flag else 0 for row, flag in zip(rows, triggered, strict=True)]
        table.append(
            {
                "policy": name,
                "threshold": threshold,
                "trigger_rate": sum(triggered) / len(triggered) if triggered else 0.0,
                "recall": statistics.fmean(recalls) if recalls else 0.0,
                "mrr": statistics.fmean(mrrs) if mrrs else 0.0,
                "latency_mean_ms": statistics.fmean(latencies) if latencies else 0.0,
                "tokens_per_query": statistics.fmean(tokens) if tokens else 0.0,
            }
        )
    return table, quantiles


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    random.seed(args.seed)
    np.random.seed(args.seed)
    out = Path(args.out) if args.out else _REPO / "lab" / "out" / args.label
    out.mkdir(parents=True, exist_ok=True)
    metrics_path = Path(args.metrics) if args.metrics else out / "metrics.json"
    matrix = json.loads(metrics_path.read_text(encoding="utf-8"))
    strategy, embedding, evidence = select_best(matrix)
    provider_mode = str(matrix.get("provider_mode", "deterministic"))
    bm25 = str(matrix["config"].get("bm25", "on"))
    # service.search 内部用 max(k, settings.candidate_k) 决定融合候选池。保持阶段 4
    # 的 50，只把返回条数收到 20，off 的前 5 条才能与阶段 4 基线逐位一致；
    # 否则融合名次被重算，「固定最优组合」这条控制变量就失效了。
    fusion_k = int(matrix["config"].get("candidate_k", 50))

    queries = matrix_run.load_queries(matrix_run.DATASET)
    if args.limit > 0:
        queries = queries[: args.limit]
    n_queries = len(queries)

    print(
        f"label={args.label} reranker={args.reranker} fixed={strategy}|{embedding} "
        f"candidates={args.candidates} k={args.k} bm25={bm25} queries={n_queries}"
    )
    print(f"  fixed 依据: {evidence['rule']}；阶段4 recall={evidence['stage4_recall']:.4f}")

    # 复用阶段 4 的索引准备与向量缓存，保证矩阵与阶段 5 用的是同一套 chunk / 向量。
    margs = Namespace(
        label=args.label,
        strategies=strategy,
        embeddings=embedding,
        rerank="off",
        bm25=bm25,
        k=args.k,
        candidate_k=fusion_k,
        out=str(out),
        provider=provider_mode,
        pg_dsn=pv.DEFAULT_PG_DSN,
        seed=args.seed,
    )
    ctx = matrix_run.MatrixContext(margs, out, queries)
    ctx.prepare_strategy(strategy)
    spec = pv.spec_for(embedding, provider_mode)
    vectorizer = pv.make_vectorizer(spec, ctx.cache, margs.pg_dsn)
    chunk_vectors = ctx.chunk_matrix(strategy, spec, vectorizer)[0]
    query_vectors = ctx.query_matrix(spec, vectorizer)
    bm25_index, _ = ctx.bm25_for(strategy)
    store = ix.MatrixStore(ctx.rows[strategy], chunk_vectors)
    service = ix.make_service(
        store,
        strategy=strategy,
        embedding=embedding,
        use_bm25=bm25 == "on",
        candidate_k=fusion_k,
        bm25=bm25_index,
    )

    reranker = make_reranker(args.reranker, cache_dir=out / "rerank_cache")
    off_cases: list[Case] = []
    on_cases: list[Case] = []
    off_hits: dict[str, list[Hit]] = {}
    on_hits: dict[str, list[Hit]] = {}
    per_query_usage: dict[str, dict[str, Any]] = {}
    failed: list[str] = []

    for position, query in enumerate(queries):
        qid = str(query["qid"])
        gold = list(query["gold"])
        qvec = query_vectors[position]
        qtext = str(query["query"])

        started = time.perf_counter()
        baseline = ix.retrieve(
            service,
            query=qtext,
            query_vector=qvec,
            k=args.k,
            strategy=strategy,
            use_bm25=bm25 == "on",
            use_rerank=False,
            candidate_k=fusion_k,
            candidate_pool=args.candidates,
        )
        off_ms = (time.perf_counter() - started) * 1000
        off_cases.append(
            Case(
                qid=qid,
                retrieved=baseline,
                gold=gold,
                scene=str(query["scene"]),
                qtype=str(query["type"]),
                latency_ms=off_ms,
            )
        )
        off_hits[qid] = baseline

        slot: dict[str, Any] = {}
        failed_here = False
        started = time.perf_counter()
        try:
            reranked = ix.retrieve(
                service,
                query=qtext,
                query_vector=qvec,
                k=args.k,
                strategy=strategy,
                use_bm25=bm25 == "on",
                use_rerank=False,
                candidate_k=fusion_k,
                reranker=reranker,
                rerank_candidates=args.candidates,
                out=slot,
            )
        except Exception as exc:  # noqa: BLE001 - 失败留痕，不静默降级
            # 单条失败不编造结果：用基线结果占位并标记，报告里如实计入失败条数。
            failed.append(f"{qid}: {type(exc).__name__}: {exc}")
            reranked = baseline
            slot["rerank"] = None
            failed_here = True
        on_ms = (time.perf_counter() - started) * 1000
        result = slot.get("rerank")
        rerank_ms = float(result.latency_ms) if result is not None else 0.0
        on_cases.append(
            Case(
                qid=qid,
                retrieved=reranked,
                gold=gold,
                scene=str(query["scene"]),
                qtype=str(query["type"]),
                latency_ms=on_ms,
            )
        )
        on_hits[qid] = reranked
        per_query_usage[qid] = {
            "off_latency_ms": off_ms,
            "on_latency_ms": on_ms,
            "rerank_latency_ms": rerank_ms,
            "tokens": int(result.tokens) if result is not None else 0,
            "cache_hit": bool(result.cache_hit) if result is not None else False,
            "failed": failed_here,
            # 契约证据交给 verify.py 断言：避免「声称 top-20→top-5，实际只排了 5 条」
            # 这种实现偏差混进报告却没人发现。
            "candidates_n": len(baseline),
            "ranked_n": len(reranked),
            "top5_n": len(result.top5) if result is not None else 0,
        }
        print(
            f"  [{qid}] off_top1={baseline[0].article if baseline else '-':<12s} "
            f"on_top1={reranked[0].article if reranked else '-':<12s} "
            f"rerank={rerank_ms:7.1f}ms tokens={per_query_usage[qid]['tokens']:5d} "
            f"cache={'hit' if per_query_usage[qid]['cache_hit'] else 'miss'}"
        )

    off_summary = summarize(off_cases, k=args.k, mrr_k=10, ndcg_k=10)
    on_summary = summarize(on_cases, k=args.k, mrr_k=10, ndcg_k=10)
    off_rows = {row["qid"]: row for row in off_summary["per_query"]}
    on_rows = {row["qid"]: row for row in on_summary["per_query"]}

    per_query: list[dict[str, Any]] = []
    for query in queries:
        qid = str(query["qid"])
        off_row, on_row = off_rows[qid], on_rows[qid]
        usage = per_query_usage[qid]
        rank = first_hit_rank(off_hits[qid], list(query["gold"]))
        bucket = (
            "top3"
            if rank is not None and rank <= 3
            else ("in_pool" if rank is not None else "miss_pool")
        )
        per_query.append(
            {
                "qid": qid,
                "scene": str(query["scene"]),
                "type": str(query["type"]),
                "off_first_hit_rank": rank,
                "bucket": bucket,
                "off_recall": off_row["recall"],
                "on_recall": on_row["recall"],
                "off_mrr": off_row["mrr"],
                "on_mrr": on_row["mrr"],
                "off_ndcg": off_row["ndcg"],
                "on_ndcg": on_row["ndcg"],
                "off_top1_score": _hit_score(off_hits[qid][0]) if off_hits[qid] else 0.0,
                **usage,
            }
        )

    point, low, high = paired_bootstrap_ci(
        [row["off_recall"] for row in per_query],
        [row["on_recall"] for row in per_query],
        seed=args.seed,
    )
    tradeoff, quantiles = build_tradeoff(per_query)
    usage: RerankUsage = reranker.usage
    if hasattr(reranker, "close"):
        reranker.close()  # type: ignore[attr-defined]

    # 第二次重跑会全命中缓存，usage.live_* 本轮为 0。若直接写进报告，「延迟增加
    # 多少」会随缓存命中率漂移，同一 label 重跑结论就不一致了。首次真实调用的
    # 账本单独留存，后续重跑只读回它；calls / cache_hits 仍反映本次真实行为。
    target = out / "rerank.json"
    previous: dict[str, Any] = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            previous = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            previous = {}
    ledger_fields = (
        "tokens",
        "requests",
        "retries",
        "doc_chars",
        "truncated_docs",
        "live_calls",
        "live_latency_ms_total",
    )
    usage_dict = usage.as_dict()
    if usage.live_calls > 0:
        live_ledger = {field: usage_dict[field] for field in ledger_fields}
    else:
        live_ledger = dict(previous.get("live_ledger") or {})
        for field in ledger_fields:
            usage_dict[field] = live_ledger.get(field, 0.0 if field.endswith("_ms_total") else 0)

    payload: dict[str, Any] = {
        "version": 1,
        "label": args.label,
        "seed": args.seed,
        "generated_by": "lab.run_rerank",
        "status": "failed" if failed and len(failed) == n_queries else "ok",
        "error": "; ".join(failed[:3]) if failed else None,
        "fixed": {"strategy": strategy, "embedding": embedding, **evidence},
        "config": {
            "candidates": args.candidates,
            "k": args.k,
            "mrr_k": 10,
            "ndcg_k": 10,
            "reranker": args.reranker,
            "model": RERANK_MODEL if args.reranker == "live" else "char-bigram-coverage",
            "endpoint": RERANK_ENDPOINT if args.reranker == "live" else "",
            "provider_mode": provider_mode,
            "bm25": bm25,
            "match_mode": "article",
            "seed": args.seed,
        },
        "off": {key: value for key, value in off_summary.items() if key != "per_query"},
        "on": {key: value for key, value in on_summary.items() if key != "per_query"},
        "contract": {
            "candidates": args.candidates,
            "top_n": args.k,
            "observed_candidates_min": min(row["candidates_n"] for row in per_query_usage.values())
            if per_query_usage
            else 0,
            "observed_ranked_min": min(row["ranked_n"] for row in per_query_usage.values())
            if per_query_usage
            else 0,
            "observed_top5_max": max(row["top5_n"] for row in per_query_usage.values())
            if per_query_usage
            else 0,
        },
        "bootstrap": {
            "point": point,
            "low": low,
            "high": high,
            "significant": bool(low > 0 or high < 0),
        },
        "groups": {
            "by_first_hit_rank": _group_stats(per_query, "bucket"),
            "by_scene": _group_stats(per_query, "scene"),
            "by_type": _group_stats(per_query, "type"),
        },
        "tradeoff": tradeoff,
        "score_quantiles": quantiles,
        "usage": usage_dict,
        "live_ledger": live_ledger,
        "failed_queries": failed,
        "per_query": per_query,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rp.generate(matrix, out)  # report.py 检测到 rerank.json 后自动追加「rerank 收益」节

    off_all = off_summary["overall"]
    on_all = on_summary["overall"]
    rerank_mean = live_ledger.get("live_latency_ms_total", 0.0) / max(
        int(live_ledger.get("live_calls", 0)), 1
    )
    print(
        f"\noff: recall@{args.k}={off_all['recall']:.4f} mrr@10={off_all['mrr']:.4f} "
        f"ndcg@10={off_all['ndcg']:.4f} p50={off_all['latency_ms_p50']:.2f}ms"
    )
    print(
        f"on : recall@{args.k}={on_all['recall']:.4f} mrr@10={on_all['mrr']:.4f} "
        f"ndcg@10={on_all['ndcg']:.4f} p50={on_all['latency_ms_p50']:.2f}ms"
    )
    print(
        f"Δrecall={point * 100:+.2f}pp 95%CI=[{low * 100:+.2f}, {high * 100:+.2f}] "
        f"Δlatency_mean={on_all['latency_ms_mean'] - off_all['latency_ms_mean']:+.2f}ms "
        f"rerank_mean={rerank_mean:.2f}ms"
    )
    print(
        f"usage: calls={usage_dict['calls']} live={usage_dict['live_calls']} "
        f"cache_hits={usage_dict['cache_hits']} requests={usage_dict['requests']} "
        f"retries={usage_dict['retries']} tokens={usage_dict['tokens']} "
        f"failed_queries={len(failed)}"
    )
    print(f"rerank.json -> {target}")
    print(f"report.md   -> {out / 'report.md'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
