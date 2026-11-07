# ruff: noqa: T201, E402, E501
"""由 metrics.json 生成 report.md。

为什么不在 run.py 里一边跑一边写 Markdown：报告必须能从落盘的 metrics.json
重放生成。结论段要跑配对 bootstrap，若把「计算」和「跑批」耦合，改一个措辞
就得重跑 15 组×80 条才能看到结果，也无法对同一份结果做多次口径对照。

为什么额外豁免 E501：本模块大部分行是 Markdown 模板字符串，宽度由输出内容
决定；把一句完整说明切成多段拼接，反而更难核对生成结果是否与预期一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations as pairs
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from lab.metrics import paired_bootstrap_ci  # noqa: E402

OFFLINE_DISCLAIMER = (
    "> **口径声明（重要）**：本次 `provider=deterministic`，E1/E2/E3 是三套确定性\n"
    "> 字符 n-gram 哈希投影基线（维度 1024/1024/512），**不是** dashscope\n"
    "> `text-embedding-v3`、`bge-m3`、`bge-small-zh` 的真实输出。本机\n"
    "> `127.0.0.1:9997` 推理服务未启动，bge 权重需框架级新依赖（铁律禁止），\n"
    "> 且 E3 的 512 维写不进 `law_chunk.embedding vector(1024)` 单列。\n"
    "> 因此本矩阵验证的是「切分策略 × 向量特征粒度」对指标的影响，以及整套\n"
    "> 跑批 / 指标 / 复现链路的正确性，**不可用作模型选型结论**。真实档位请跑\n"
    "> `--provider live`（E1 复用阶段 2 已入库的百炼向量；E2/E3 不可用时按失败组合留痕）。\n"
)

LIVE_DISCLAIMER = (
    "> **口径声明**：本次 `provider=live`，chunk 向量来自阶段 2 写入 pgvector 的\n"
    "> 真实百炼结果，query 向量由真实 `/embeddings` 端点计算并缓存。E2/E3 因\n"
    "> 本地推理服务缺失 / 维度不匹配会记为 failed 并保留，**不参与最优评选**。\n"
)


def _ok(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in metrics["combinations"] if item["status"] == "ok"]


def _recall(item: dict[str, Any]) -> float:
    return float(item["summary"]["overall"]["recall"])


def _rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # tie-break 全用确定性键，保证同一 metrics.json 生成的结论逐字一致。
    return sorted(
        items,
        key=lambda item: (
            -_recall(item),
            -float(item["summary"]["overall"]["mrr"]),
            -float(item["summary"]["overall"]["ndcg"]),
            item["key"],
        ),
    )


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


BUCKET_LABEL = {
    "top3": "gold 已在 off top-3",
    "in_pool": "gold 在 off 4–20 名",
    "miss_pool": "off top-20 无 gold（rerank 天花板）",
}


def _recommendation(rr: dict[str, Any]) -> tuple[str, str]:
    """三选一的推荐完全由落盘数据推导，报告可从 json 重放，不夹带人工措辞。

    判定规则（阈值写在报告里，业务方可按延迟预算调整）：
    1. 条件开优于全开 = 召回损失 ≤ 0.005 且平均延迟降到全开的 70% 以下；
    2. Δrecall > 0 但配对 bootstrap 不显著 = 不认为有真实收益，默认不开；
    3. Δrecall ≤ 0 = 全开有害，只有当存在有效条件开策略才推荐条件开。
    """
    off, on = rr["off"]["overall"], rr["on"]["overall"]
    delta_pp = (on["recall"] - off["recall"]) * 100
    significant = bool(rr["bootstrap"]["significant"])
    table = rr["tradeoff"]
    all_on = next(row for row in table if row["policy"] == "全开")
    conditional = [
        row
        for row in table[2:]
        if row["recall"] >= all_on["recall"] - 0.005
        and row["latency_mean_ms"] < all_on["latency_mean_ms"] * 0.7
        and row["trigger_rate"] < 0.95
    ]
    if conditional:
        best = max(conditional, key=lambda row: (row["recall"], -row["latency_mean_ms"]))
        return (
            "条件开",
            f"策略「{best['policy']}」在触发率 {best['trigger_rate'] * 100:.1f}% 下达到 "
            f"recall@5={best['recall']:.4f}（全开 {all_on['recall']:.4f}），平均延迟 "
            f"{best['latency_mean_ms']:.1f}ms（全开 {all_on['latency_mean_ms']:.1f}ms），"
            f"平均 tokens/query {best['tokens_per_query']:.0f}（全开 {all_on['tokens_per_query']:.0f}）。",
        )
    if delta_pp > 0 and significant:
        return (
            "开",
            f"Δrecall={delta_pp:+.2f}pp 且 95% CI 不含 0，且不存在「召回几乎不降、延迟明显更低」"
            f"的条件开策略，全开是唯一能拿到该收益的配置。",
        )
    if delta_pp > 0:
        return (
            "关",
            f"Δrecall={delta_pp:+.2f}pp 但 95% CI 含 0，收益无法与抽样噪声区分；"
            f"却要付出 {on['latency_ms_mean'] - off['latency_ms_mean']:+.1f}ms 延迟与 "
            f"{rr['usage']['tokens']} tokens，不值得开。",
        )
    return (
        "关",
        f"Δrecall={delta_pp:+.2f}pp 为负或零，全开反而损伤排序，且没有有效条件开策略。",
    )


def _rerank_section(rr: dict[str, Any]) -> list[str]:
    """阶段 5「rerank 收益」节；只消费 rerank.json，不重新跑检索。"""
    cfg, off, on = rr["config"], rr["off"]["overall"], rr["on"]["overall"]
    usage, boot = rr["usage"], rr["bootstrap"]
    lines: list[str] = ["", "## 8. rerank 收益（阶段 5）", ""]
    lines.append(
        f"- 固定组合：`{rr['fixed']['strategy']}|{rr['fixed']['embedding']}`"
        f"（由本报告 §5 矩阵按 `{rr['fixed']['rule']}` 自动选出，阶段 4 recall={rr['fixed']['stage4_recall']:.4f}）"
    )
    lines.append(
        f"- reranker：`{cfg['model']}`，输入 top-{cfg['candidates']} 候选 → 输出重排 top-{cfg['k']}；"
        f"query {len(rr['per_query'])} 条，失败 {len(rr['failed_queries'])} 条"
    )
    if cfg["reranker"] == "deterministic":
        lines += [
            "",
            "> **口径声明（重要）**：本轮 `reranker=deterministic`，用的是字符二元组覆盖率"
            "离线代理，**不是** `gte-rerank-v2`。它与阶段 3 的标注问法同源，会系统性高估收益，"
            "**不能用来回答「rerank 值不值」**。请用 `--reranker live` 重跑真实模型后再采信本节结论。",
        ]
    else:
        lines += [
            "",
            "> **口径声明**：本轮为 DashScope `gte-rerank-v2` 真实调用；token / 请求数取自 API "
            "回传值。off 与 on 从**同一个 top-20 融合候选池**出发，recall@5 用前 5 条、"
            "mrr@10 / ndcg@10 用前 10 条，因此 @10 指标与阶段 4（只返回 k=5）不可直接比。",
        ]
    if rr["failed_queries"]:
        lines += ["", "**失败 query（不删除、不补假结果）**："]
        lines.extend(f"- `{item}`" for item in rr["failed_queries"])

    delta_latency = on["latency_ms_mean"] - off["latency_ms_mean"]
    rerank_mean = usage["live_latency_ms_total"] / max(usage["live_calls"], 1)
    lines += [
        "",
        "### 8.1 主对比",
        "",
    ]
    lines.extend(
        _table(
            ["指标", "rerank off", "rerank on", "Δ", "说明"],
            [
                [
                    f"recall@{cfg['k']}",
                    f"{off['recall']:.4f}",
                    f"{on['recall']:.4f}",
                    f"{(on['recall'] - off['recall']) * 100:+.2f}pp",
                    "macro，每条 query 等权",
                ],
                [
                    "mrr@10",
                    f"{off['mrr']:.4f}",
                    f"{on['mrr']:.4f}",
                    f"{on['mrr'] - off['mrr']:+.4f}",
                    "前 10 条内首个正确答案的倒数排名",
                ],
                [
                    "ndcg@10",
                    f"{off['ndcg']:.4f}",
                    f"{on['ndcg']:.4f}",
                    f"{on['ndcg'] - off['ndcg']:+.4f}",
                    "二元相关度，同 article 去重计增益",
                ],
                [
                    "延迟 mean / p50 (ms)",
                    f"{off['latency_ms_mean']:.2f} / {off['latency_ms_p50']:.2f}",
                    f"{on['latency_ms_mean']:.2f} / {on['latency_ms_p50']:.2f}",
                    f"{delta_latency:+.2f}ms",
                    "on 含检索 + rerank 全链路",
                ],
                [
                    "rerank 单次延迟 mean (ms)",
                    "—",
                    f"{rerank_mean:.2f}",
                    f"+{rerank_mean:.2f}",
                    "只统计首次真实调用，缓存命中不计入",
                ],
                [
                    "API tokens / 请求数",
                    "0 / 0",
                    f"{usage['tokens']} / {usage['requests']}",
                    f"+{usage['tokens']}",
                    "API 真实回传，非估算",
                ],
            ],
        )
    )
    lines += [
        "",
        f"**显著性**：固定 seed 的配对 bootstrap（2000 次重采样 {len(rr['per_query'])} 条 query）"
        f"给出 Δrecall 点估计 {boot['point'] * 100:+.2f}pp，95% CI = "
        f"[{boot['low'] * 100:+.2f}, {boot['high'] * 100:+.2f}]pp，"
        + ("区间不含 0，差异显著。" if boot["significant"] else "区间含 0，差异不显著。"),
        "",
        "### 8.2 三个问题",
        "",
        f"**Q1｜rerank 带来多少 pp 提升？** recall@{cfg['k']} 从 {off['recall']:.4f} 到 "
        f"{on['recall']:.4f}，Δ = {(on['recall'] - off['recall']) * 100:+.2f}pp"
        + ("（显著）" if boot["significant"] else "（不显著，不能与抽样噪声区分）")
        + f"；mrr@10 {on['mrr'] - off['mrr']:+.4f}、ndcg@10 {on['ndcg'] - off['ndcg']:+.4f}。",
        f"**Q2｜延迟增加多少？** 全链路 mean {off['latency_ms_mean']:.2f} → "
        f"{on['latency_ms_mean']:.2f}ms（{delta_latency:+.2f}ms，"
        f"{(delta_latency / max(off['latency_ms_mean'], 0.001)) * 100:+.0f}%），其中 rerank "
        f"单次 mean {rerank_mean:.2f}ms。另需支付 {usage['tokens']} tokens / "
        f"{usage['requests']} 次请求；结果已落盘缓存，同 label 重跑不重复计费。",
        "**Q3｜什么场景下不值得？** 按「off 基线中 gold 首次出现的位置」分组（离线分析口径，"
        "线上拿不到 gold，但可据它判断 rerank 的收益来源与天花板）：",
        "",
    ]
    bucket_rows = [
        [
            BUCKET_LABEL.get(row["name"], row["name"]),
            str(row["n"]),
            f"{row['off_recall']:.4f}",
            f"{row['on_recall']:.4f}",
            f"{row['delta_recall'] * 100:+.2f}pp",
            f"{row['off_mrr']:.4f} → {row['on_mrr']:.4f}",
            f"{row['rerank_latency_mean_ms']:.1f}",
        ]
        for row in rr["groups"]["by_first_hit_rank"]
    ]
    lines.extend(
        _table(
            ["分组", "n", "off recall", "on recall", "Δrecall", "mrr off→on", "rerank ms"],
            bucket_rows,
        )
    )
    top3 = next((row for row in rr["groups"]["by_first_hit_rank"] if row["name"] == "top3"), None)
    miss = next(
        (row for row in rr["groups"]["by_first_hit_rank"] if row["name"] == "miss_pool"), None
    )
    notes: list[str] = []
    if top3:
        notes.append(
            f"`gold 已在 top-3` 的 {top3['n']} 条：Δrecall={top3['delta_recall'] * 100:+.2f}pp，"
            + (
                "rerank 只会把已排好的答案往下挤，这一组开 rerank 不值得。"
                if top3["delta_recall"] <= 0
                else "仍有提升，但收益上限受 gold 数限制。"
            )
        )
    if miss:
        notes.append(
            f"`off top-20 无 gold` 的 {miss['n']} 条：候选池里根本没有答案，"
            f"rerank 无法凭空召回，Δrecall={miss['delta_recall'] * 100:+.2f}pp，"
            "这一组只付延迟和成本、零收益；要改善必须回到召回侧（换 embedding / 加 BM25 / 调候选池）。"
        )
    if notes:
        lines += [""] + [f"- {note}" for note in notes]

    lines += [
        "",
        "按场景 / 问法类型看长尾（Δrecall 为负说明该类问题被 rerank 拖累）：",
        "",
    ]
    group_rows = [
        [
            "scene" if key == "by_scene" else "type",
            row["name"],
            str(row["n"]),
            f"{row['off_recall']:.4f}",
            f"{row['on_recall']:.4f}",
            f"{row['delta_recall'] * 100:+.2f}pp",
        ]
        for key in ("by_scene", "by_type")
        for row in rr["groups"][key]
    ]
    lines.extend(_table(["维度", "分组", "n", "off recall", "on recall", "Δrecall"], group_rows))

    lines += [
        "",
        "### 8.3 延迟 vs 召回取舍表",
        "",
        "条件开策略的触发信号全部取自 off 侧（线上可得），不使用 gold；"
        "未触发的 query 走基线、不付 rerank 延迟与 tokens。分数阈值为 off top-1 融合分的"
        f"经验分位（P25={rr['score_quantiles']['p25']:.6f}, "
        f"P50={rr['score_quantiles']['p50']:.6f}, P75={rr['score_quantiles']['p75']:.6f}）。",
        "",
    ]
    lines.extend(
        _table(
            [
                "策略",
                "触发条件",
                "触发率",
                f"recall@{cfg['k']}",
                "mrr@10",
                "平均延迟 (ms)",
                "平均 tokens/q",
            ],
            [
                [
                    row["policy"],
                    row["threshold"],
                    f"{row['trigger_rate'] * 100:.1f}%",
                    f"{row['recall']:.4f}",
                    f"{row['mrr']:.4f}",
                    f"{row['latency_mean_ms']:.2f}",
                    f"{row['tokens_per_query']:.0f}",
                ]
                for row in rr["tradeoff"]
            ],
        )
    )

    verdict, reason = _recommendation(rr)
    lines += ["", "### 8.4 推荐", "", f"**推荐：{verdict}**", "", reason]
    lines += [
        "",
        "判定规则（可由业务延迟预算调整）：「条件开优于全开」= 召回损失 ≤ 0.005 且平均延迟降到"
        "全开的 70% 以下；Δrecall 为正但配对 bootstrap 不显著时，不认为存在真实收益。",
        "",
        "### 8.5 成本与复现",
        "",
    ]
    lines.extend(
        _table(
            ["项", "值"],
            [
                [
                    "rerank 调用 / 首次真实调用 / 缓存命中",
                    f"{usage['calls']} / {usage['live_calls']} / {usage['cache_hits']}",
                ],
                ["HTTP 请求数 / 重试", f"{usage['requests']} / {usage['retries']}"],
                ["tokens（API 回传）", str(usage["tokens"])],
                [
                    "送入文档字符数 / 被截断文档数",
                    f"{usage['doc_chars']} / {usage['truncated_docs']}",
                ],
                ["费用 (¥)", "未填：单价需以控制台实时报价为准，不用未经验证的估算值冒充真实支出"],
            ],
        )
    )
    lines += [
        "",
        f"缓存键含 query、候选 chunk_id 与正文、模型名与候选条数，语料或模型变更自动失效；"
        f"seed={rr['seed']}。同 label 重跑时逐 query recall 应逐位一致（由 `lab/verify.py` 断言）。",
        "",
    ]
    return lines


def render(metrics: dict[str, Any], rerank: dict[str, Any] | None = None) -> str:
    config = metrics["config"]
    strategies = list(config["strategies"])
    embeddings = list(config["embeddings"])
    by_key = {item["key"]: item for item in metrics["combinations"]}
    ok = _ok(metrics)
    failed = [item for item in metrics["combinations"] if item["status"] != "ok"]
    k = config["k"]

    lines: list[str] = [
        "# 检索实验台 · 阶段 4 矩阵报告",
        "",
        f"- label：`{metrics['label']}`　seed：`{metrics['seed']}`　provider：`{metrics['provider_mode']}`",
        f"- 数据集：`{metrics['dataset']['path']}`（{metrics['dataset']['n']} 条，sha256 `{metrics['dataset']['sha256'][:16]}…`）",
        f"- 配置：k={k}　candidate_k={config['candidate_k']}　bm25={config['bm25']}　rerank={config['rerank']}　融合={config['fusion']}　命中口径={config['match_mode']}",
        f"- 组合：{len(metrics['combinations'])} 组（成功 {len(ok)} / 失败 {len(failed)}），每组 {metrics['dataset']['n']} 条 query",
        "",
        OFFLINE_DISCLAIMER if metrics["provider_mode"] == "deterministic" else LIVE_DISCLAIMER,
        "## 1. 主矩阵：recall@" + str(k),
        "",
        "行 = 切分策略，列 = embedding 档位；每个单元格是该组合 80 条 query 的 macro 平均 recall。",
        "",
    ]
    matrix_rows: list[list[str]] = []
    for strategy in strategies:
        cells = [f"`{strategy}`"]
        for code in embeddings:
            item = by_key.get(f"{strategy}|{code}")
            if item is None or item["status"] != "ok":
                cells.append("FAIL")
            else:
                cells.append(f"{_recall(item):.4f}")
        matrix_rows.append(cells)
    lines.extend(_table(["strategy \\ embedding", *embeddings], matrix_rows))

    lines += [
        "",
        "## 2. 每个组合的完整指标",
        "",
        "所有指标按每条 query 等权 macro 平均；延迟为单次检索耗时，索引用时为组合级准备耗时。",
        "",
    ]
    detail_rows: list[list[str]] = []
    for item in metrics["combinations"]:
        if item["status"] != "ok":
            detail_rows.append(
                [f"`{item['key']}`", "FAILED", "-", "-", "-", "-", "-", "-", "-", "-"]
            )
            continue
        overall = item["summary"]["overall"]
        usage = item["embedding_usage"]
        detail_rows.append(
            [
                f"`{item['key']}`",
                "ok",
                str(item["n_chunks"]),
                f"{overall['recall']:.4f}",
                f"{overall['hit_rate']:.4f}",
                f"{overall['mrr']:.4f}",
                f"{overall['ndcg']:.4f}",
                f"{overall['latency_ms_mean']:.2f} / {overall['latency_ms_p50']:.2f}",
                f"{overall['index_ms']:.1f}",
                str(usage.get("tokens", 0)),
            ]
        )
    lines.extend(
        _table(
            [
                "组合",
                "状态",
                "chunks",
                f"recall@{k}",
                f"hit@{k}",
                "mrr@10",
                "ndcg@10",
                "延迟 mean/p50 (ms)",
                "索引用时 (ms)",
                "API tokens",
            ],
            detail_rows,
        )
    )

    lines += ["", "## 3. embedding token 成本", ""]
    cost_rows: list[list[str]] = []
    for item in metrics["combinations"]:
        usage = item["embedding_usage"]
        if item["status"] != "ok":
            cost_rows.append([f"`{item['key']}`", "-", "-", "-", "组合失败，未产生向量化消耗"])
            continue
        cost_rows.append(
            [
                f"`{item['key']}`",
                str(usage.get("tokens", 0)),
                str(usage.get("requests", 0)),
                f"{float(usage.get('cost_cny', 0.0)):.4f}",
                str(usage.get("note", "")),
            ]
        )
    lines.extend(_table(["组合", "tokens", "API 请求数", "费用 (¥)", "说明"], cost_rows))
    if metrics["provider_mode"] == "deterministic":
        lines += [
            "",
            "离线哈希投影**不产生任何 API token 与费用**，tokens 列恒为 0。为便于换算量级，"
            "下面给出「若改用真实 API」的保守字符数上界（中文约 1 字 ≈ 1 token）：",
            "",
        ]
        est_rows = [
            [
                f"`{item['key']}`",
                str(item["embedding_usage"].get("chars", 0)),
                str(item["embedding_usage"].get("estimated_tokens_if_live", 0)),
                "hit" if item["cache_hit"] else "miss",
            ]
            for item in ok
        ]
        lines.extend(
            _table(["组合", "字符数", "估算 tokens（若走真实 API）", "向量缓存"], est_rows)
        )

    lines += ["", "## 4. 索引构建耗时拆分 (ms)", ""]
    build_rows = [
        [
            f"`{item['key']}`",
            f"{item['index_breakdown'].get('chunk_ms', 0):.1f}",
            f"{item['index_breakdown'].get('vector_ms', 0):.1f}",
            f"{item['index_breakdown'].get('query_vector_ms', 0):.1f}",
            f"{item['index_breakdown'].get('bm25_ms', 0):.1f}",
            f"{item['index_ms']:.1f}",
        ]
        for item in ok
    ]
    lines.extend(_table(["组合", "切分", "chunk 向量", "query 向量", "BM25", "合计"], build_rows))
    lines += [
        "",
        "BM25 索引与 embedding 档位无关，按 strategy 建一次后跨 3 个档位复用；向量矩阵按语料指纹缓存，重跑命中时该列为 0。",
        "",
    ]

    lines += ["", "## 5. 结论", ""]
    ranked = _rank(ok)
    if not ranked:
        lines.append("所有组合均失败，无法评选最优。失败原因见 §6。")
    else:
        best, second = ranked[0], (ranked[1] if len(ranked) > 1 else None)
        lines.append(
            f"- **最优组合**：`{best['key']}`，recall@{k}={_recall(best):.4f}，"
            f"mrr@10={best['summary']['overall']['mrr']:.4f}，"
            f"ndcg@10={best['summary']['overall']['ndcg']:.4f}，"
            f"延迟 p50={best['summary']['overall']['latency_ms_p50']:.2f}ms。"
        )
        if second is not None:
            lines.append(
                f"- **次优组合**：`{second['key']}`，recall@{k}={_recall(second):.4f}"
                f"（与最优相差 {_recall(best) - _recall(second):+.4f}）。"
            )
        lines += ["", "### 同策略、不同 embedding 的差值是否显著", ""]
        lines.append(
            "对同一策略下的两两档位，用固定 seed 的**配对 bootstrap**（2000 次重采样 80 条 query）"
            "估计 recall 差值的 95% 置信区间；区间不含 0 判为显著。配对而非独立重采样，"
            "是为了消掉「题目本身难易」这一共同因子。"
        )
        lines.append("")
        sig_rows: list[list[str]] = []
        for strategy in strategies:
            for left, right in pairs(embeddings, 2):
                a, b = by_key.get(f"{strategy}|{left}"), by_key.get(f"{strategy}|{right}")
                if not a or not b or a["status"] != "ok" or b["status"] != "ok":
                    sig_rows.append(
                        [f"`{strategy}`", f"{left}→{right}", "-", "-", "组合失败，跳过"]
                    )
                    continue
                base = [float(row["recall"]) for row in a["summary"]["per_query"]]
                other = [float(row["recall"]) for row in b["summary"]["per_query"]]
                point, low, high = paired_bootstrap_ci(base, other, seed=metrics["seed"])
                significant = low > 0 or high < 0
                sig_rows.append(
                    [
                        f"`{strategy}`",
                        f"{left}→{right}",
                        f"{point:+.4f}",
                        f"[{low:+.4f}, {high:+.4f}]",
                        "是" if significant else "否",
                    ]
                )
        lines.extend(_table(["strategy", "档位对比", "Δrecall", "95% CI", "显著"], sig_rows))

        lines += [
            "",
            "### 切分策略是否真的改变了结果",
            "",
            "主矩阵里同一列常出现多行数值相同，这不等于切分器没生效：命中判定按 `article`，"
            "切分只决定「同一法条的哪个片段排在前面」，不改变 article 集合；逐 query 的正负"
            "变化在 macro 平均里还会互相抵消。下面以每列第一个策略为基准，统计逐 query 指标"
            "真正发生变化的条数。",
            "",
        ]
        baseline = strategies[0]
        diff_rows: list[list[str]] = []
        for code in embeddings:
            base_item = by_key.get(f"{baseline}|{code}")
            if not base_item or base_item["status"] != "ok":
                continue
            base_rows = {row["qid"]: row for row in base_item["summary"]["per_query"]}
            for strategy in strategies[1:]:
                item = by_key.get(f"{strategy}|{code}")
                if not item or item["status"] != "ok":
                    continue
                changed = sum(
                    1
                    for row in item["summary"]["per_query"]
                    if any(
                        abs(float(row[field]) - float(base_rows[row["qid"]][field])) > 1e-12
                        for field in ("recall", "mrr", "ndcg")
                    )
                )
                diff_rows.append(
                    [
                        f"`{code}`",
                        f"`{strategy}`",
                        f"{changed} / {metrics['dataset']['n']}",
                        f"{_recall(item) - _recall(base_item):+.4f}",
                    ]
                )
        lines.extend(
            _table(["embedding", "相对基准策略", "逐 query 变化条数", "Δrecall (macro)"], diff_rows)
        )
        lines += [
            "",
            f"基准策略为 `{baseline}`。`S2_fixed512` 与基准逐 query 完全一致是真实结论而非 bug："
            "512 字窗口大于语料里绝大多数法条，阶段 1 统计里它只比整条切分多出 9 个 chunk，"
            "而这 9 条不在 80 条查询的 gold 中；`S5_context` 因注入「法律名+章节+条号」前缀，"
            "变化最明显。若要放大策略差异，需要增加长法条 / 跨款提问，而不是改指标口径。",
            "",
        ]

        if best and second:
            base = [float(row["recall"]) for row in second["summary"]["per_query"]]
            other = [float(row["recall"]) for row in best["summary"]["per_query"]]
            point, low, high = paired_bootstrap_ci(base, other, seed=metrics["seed"])
            lines += [
                "",
                f"**最优 vs 次优**：`{second['key']}` → `{best['key']}` 的 Δrecall={point:+.4f}，"
                f"95% CI=[{low:+.4f}, {high:+.4f}]，"
                + (
                    "差异显著。"
                    if low > 0 or high < 0
                    else "置信区间含 0，差异不显著，不能据此断言该组合更优。"
                ),
            ]

    lines += ["", "## 6. 失败组合（不删除、不参与评选）", ""]
    if failed:
        lines.extend(
            _table(
                ["组合", "原因"],
                [[f"`{item['key']}`", str(item["error"]).replace("|", "\\|")] for item in failed],
            )
        )
    else:
        lines.append("本次无失败组合。")

    lines += [
        "",
        "## 7. 复现",
        "",
        "```bash",
        f"make lab LABEL={metrics['label']}",
        "# 等价（本机未安装 GNU make 时）",
        "cd python-agent && uv run python ../lab/run.py --label " + metrics["label"],
        "# 仅重放报告",
        "cd python-agent && uv run python ../lab/report.py --metrics ../lab/out/"
        + metrics["label"]
        + "/metrics.json",
        "```",
        "",
        "固定 seed、按 chunk_id 稳定排序、向量矩阵按语料指纹落盘缓存、组合严格串行，"
        "因此同一 label 重跑的逐 query recall 应逐位一致（由 `lab/verify.py` 断言）。",
        "",
    ]
    if rerank is not None:
        lines.extend(_rerank_section(rerank))
    return "\n".join(lines)


def generate(metrics: dict[str, Any], out: Path) -> Path:
    path = out / "report.md"
    # 为什么由目录而不是由调用方传：阶段 5 的实验产物是独立的 rerank.json，
    # 只重放阶段 4 报告时也应自动带上这一节，避免「跑了实验却忘了加参数」。
    candidate = out / "rerank.json"
    rerank: dict[str, Any] | None = None
    if candidate.exists():
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
            rerank = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            rerank = None
    path.write_text(render(metrics, rerank), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="由 metrics.json 重放生成 report.md")
    parser.add_argument("--metrics", required=True)
    args = parser.parse_args(argv)
    path = Path(args.metrics)
    metrics = json.loads(path.read_text(encoding="utf-8"))
    target = generate(metrics, path.parent)
    print(f"report.md -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
