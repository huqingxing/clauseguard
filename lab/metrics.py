"""检索实验台的统一裁判口径。

## 命中判定（阶段 3 约定，所有实验共用）

    命中 = 检索结果中任一条的 article 与 gold 的 article 相同

**为什么用 article 而不是 chunk_id**：阶段 1 有 5 种切分策略，同一条法条在
S1_whole / S2_fixed512 / S3_fixed256 / S4_paragraph / S5_context 下会生成完全不同的
chunk_id（数量、下标、甚至切出来的文本都不同）。若按 chunk_id 判命中，切换策略时
结果不可比，「策略 A 比策略 B 好」这个结论就是切分下标造成的假象。article 是语料
自带的法条号，在 5 种策略下恒定，因此是唯一可跨策略比较的粒度。

**为什么不直接比文本**：S5_context 会给 chunk 注入「《民法典》第三编合同 第585条：」
前缀，S2/S3 会截断长条，文本比对同样会把「同一条」判成「不同条」。

**已知的口径缺陷（重要）**：不同法律存在相同条号（公司法第三条、广告法第三条、
民法典第三条……）。只比 article 时，召回「别部法律的同一条号」会算命中。
`mode="law_article"` 提供带 law_code 的严格口径用于稳健性对照；默认仍是
`mode="article"`，因为任务书把 article 定为唯一裁判，实验之间必须口径一致。
validate.py 会量化数据集中受此影响的 gold 比例，便于解读指标时打折。

**为什么 recall 的分母用去重后的 key 数**：一条查询的 gold 最多 3 个 chunk_id，
但可能指向同一 article（不同策略的等价标注）；按去重 key 计数才不会把同一
正确答案重复计分。
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

MatchMode = Literal["article", "law_article"]

Matchable = Mapping[str, Any] | object | str


@dataclass(frozen=True, slots=True)
class ChunkRef:
    """chunk_id = {law_code}:{article}:{idx}:{strategy} 的解析结果。"""

    law_code: str
    article: str
    idx: str
    strategy: str


def parse_chunk_id(chunk_id: str) -> ChunkRef:
    # 用 rsplit 而不是 split：article 是中文条号不含冒号，但 law_code 未来若带
    # 命名空间分隔符，从右往左拆才不会把前缀截错。
    parts = chunk_id.rsplit(":", 3)
    if len(parts) != 4:
        raise ValueError(f"非法 chunk_id（应为 4 段）: {chunk_id}")
    return ChunkRef(*parts)


def _field(item: Matchable, name: str) -> str | None:
    if isinstance(item, Mapping):
        value = item.get(name)
        return str(value) if value is not None else None
    value = getattr(item, name, None)
    return str(value) if value is not None else None


def _as_ref(item: Matchable) -> ChunkRef | None:
    """把字符串结果按 chunk_id 解析；解析不了就当作裸 article 由调用方处理。"""
    if isinstance(item, str) and item.count(":") == 3:
        return parse_chunk_id(item)
    return None


def match_key(item: Matchable, mode: MatchMode = "article") -> str:
    """把一条检索结果或 gold 归一成参与比对的字符串。

    为什么同时接受 Hit/dataclass、dict 和裸字符串：阶段 2 的 service 返回 Hit，
    HTTP 端点返回 dict，而离线评测脚本常直接读 JSON 里的 chunk_id 列表；
    让调用方各处手工转换，迟早会出现一处转错而全表指标虚高。
    """
    ref = _as_ref(item)
    if ref is not None:
        resolved = (ref.law_code, ref.article)
    elif isinstance(item, str):
        resolved = ("", item)
    else:
        article = _field(item, "article")
        if article is None:
            raise TypeError(f"无法从结果中取到 article: {item!r}")
        resolved = (_field(item, "law_code") or _field(item, "law") or "", article)
    law_code, article = resolved
    if mode == "law_article":
        return f"{law_code}:{article}"
    if mode == "article":
        return article
    raise ValueError(f"未知比对口径: {mode}")


def gold_keys(gold: Sequence[str], mode: MatchMode = "article") -> set[str]:
    return {match_key(parse_chunk_id(chunk_id), mode) for chunk_id in gold}


def is_hit(
    retrieved: Sequence[Matchable],
    gold: Sequence[str],
    k: int = 5,
    *,
    mode: MatchMode = "article",
) -> bool:
    """前 k 条中任一条命中任一 gold 即算命中（任务书口径）。"""
    if k <= 0:
        return False
    keys = gold_keys(gold, mode)
    return any(match_key(item, mode) in keys for item in retrieved[:k])


def recall_at_k(
    retrieved: Sequence[Matchable],
    gold: Sequence[str],
    k: int = 5,
    *,
    mode: MatchMode = "article",
) -> float:
    """前 k 条覆盖了几个不同的 gold article / gold 总数，取值 0..1。"""
    keys = gold_keys(gold, mode)
    if not keys:
        return 0.0
    found = {match_key(item, mode) for item in retrieved[:k]} & keys
    return len(found) / len(keys)


def reciprocal_rank(
    retrieved: Sequence[Matchable],
    gold: Sequence[str],
    k: int = 5,
    *,
    mode: MatchMode = "article",
) -> float:
    """第一个正确答案的倒数排名；未命中返回 0。"""
    keys = gold_keys(gold, mode)
    for rank, item in enumerate(retrieved[:k], start=1):
        if match_key(item, mode) in keys:
            return 1.0 / rank
    return 0.0


@dataclass(frozen=True, slots=True)
class Case:
    """一条查询的一次检索结果；scene/type 用于分组看长尾短板。

    为什么 latency_ms 有默认值：阶段 3 的标注校验只关心命中，构造 Case 时不该被迫
    编造一个耗时；阶段 4 的跑批才真实填入。默认 0 让旧调用方零改动。
    """

    qid: str
    retrieved: Sequence[Matchable]
    gold: Sequence[str]
    scene: str
    qtype: str
    latency_ms: float = 0.0


def evaluate(cases: Sequence[Case], k: int = 5, *, mode: MatchMode = "article") -> dict[str, Any]:
    """宏平均 + 按场景/问法类型分组，避免「总体指标不错但某类场景全灭」被掩盖。"""
    if not cases:
        return {"k": k, "mode": mode, "n": 0}

    def summary(subset: Sequence[Case]) -> dict[str, float]:
        n = len(subset)
        return {
            "n": float(n),
            "hit_rate": sum(is_hit(c.retrieved, c.gold, k, mode=mode) for c in subset) / n,
            "recall": sum(recall_at_k(c.retrieved, c.gold, k, mode=mode) for c in subset) / n,
            "mrr": sum(reciprocal_rank(c.retrieved, c.gold, k, mode=mode) for c in subset) / n,
            "ndcg": sum(ndcg_at_k(c.retrieved, c.gold, 10, mode=mode) for c in subset) / n,
        }

    overall: dict[str, Any] = {"k": k, "mode": mode, "n": len(cases), "overall": summary(cases)}
    overall["by_scene"] = {
        scene: summary([c for c in cases if c.scene == scene])
        for scene in sorted({c.scene for c in cases})
    }
    overall["by_type"] = {
        qtype: summary([c for c in cases if c.qtype == qtype])
        for qtype in sorted({c.qtype for c in cases})
    }
    return overall


# ---------------------------------------------------------------------------
# 阶段 4：矩阵跑批共用的指标层
# ---------------------------------------------------------------------------


def ndcg_at_k(
    retrieved: Sequence[Matchable],
    gold: Sequence[str],
    k: int = 10,
    *,
    mode: MatchMode = "article",
) -> float:
    """二元相关度的 nDCG@k；命中越靠前收益越大，按 gold 数归一化到 0..1。

    为什么用二元相关度：阶段 3 的 gold 只标「哪些条是答案」，没有分级相关性
    标签。硬造 0/1/2 三级只会把标注者的主观强度包装成客观指标。
    为什么 IDCG 取 min(|gold|, k)：单 gold 查询把答案排第 1 时 nDCG 应恰为 1；
    若用固定 k 个理想命中，单 gold 查询的 nDCG 上界会小于 1，跨查询 macro 平均
    就被 gold 条数系统性压低，指标不再只反映排序质量。
    """
    if k <= 0:
        return 0.0
    keys = gold_keys(gold, mode)
    if not keys:
        return 0.0
    seen: set[str] = set()
    dcg = 0.0
    for rank, item in enumerate(retrieved[:k], start=1):
        key = match_key(item, mode)
        if key in keys and key not in seen:
            # 为什么必须去重：S3_fixed256 / S4_paragraph 会把同一条法条切成多个
            # chunk，top-k 里可能同时出现 3~4 个同 article 的切片。若每个都计一次
            # 增益，DCG 会超过按去重 gold 数构造的 IDCG（实测 nDCG 跑到 1.93），
            # 切分越碎的指标反而越高——这正好把策略对比引向错误结论。
            seen.add(key)
            dcg += 1.0 / math.log2(rank + 1)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(keys), k) + 1))
    return dcg / ideal if ideal > 0 else 0.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def per_query_metrics(
    cases: Sequence[Case],
    *,
    k: int = 5,
    mrr_k: int = 10,
    ndcg_k: int = 10,
    mode: MatchMode = "article",
) -> list[dict[str, Any]]:
    """保留逐 query 明细，而不是只留平均值。

    为什么必须留明细：结论段要回答「A 比 B 高 0.03 是否显著」，配对 bootstrap
    需要两组的逐 query 差值；只留均值时任何显著性判断都是编的。同一份明细也让
    「同 label 重跑 recall 差异 = 0」可以逐条比对，而不只是比对一个被四舍五入
    掩盖了问题的平均数。
    """
    return [
        {
            "qid": case.qid,
            "scene": case.scene,
            "type": case.qtype,
            "hit": 1.0 if is_hit(case.retrieved, case.gold, k, mode=mode) else 0.0,
            "recall": recall_at_k(case.retrieved, case.gold, k, mode=mode),
            "mrr": reciprocal_rank(case.retrieved, case.gold, mrr_k, mode=mode),
            "ndcg": ndcg_at_k(case.retrieved, case.gold, ndcg_k, mode=mode),
            "latency_ms": case.latency_ms,
        }
        for case in cases
    ]


def summarize(
    cases: Sequence[Case],
    *,
    k: int = 5,
    mrr_k: int = 10,
    ndcg_k: int = 10,
    index_ms: float = 0.0,
    mode: MatchMode = "article",
) -> dict[str, Any]:
    """所有指标按「每条 query 等权」做 macro 平均。

    为什么是 macro 而不是 micro：micro 会按命中条数加权，等于让 gold 多、命中多
    的少数场景型查询主导全局分；实验台要比较的是「一个典型提问能不能答对」，
    每条 query 无论 gold 有几个都必须同权。分组指标同理对组内 query 等权。
    索引用时不属于逐 query 指标，单独作为组合级字段记录，不参与 macro。
    """
    rows = per_query_metrics(cases, k=k, mrr_k=mrr_k, ndcg_k=ndcg_k, mode=mode)

    def aggregate(subset: Sequence[dict[str, Any]]) -> dict[str, float]:
        return {
            "n": float(len(subset)),
            "hit_rate": _mean([row["hit"] for row in subset]),
            "recall": _mean([row["recall"] for row in subset]),
            "mrr": _mean([row["mrr"] for row in subset]),
            "ndcg": _mean([row["ndcg"] for row in subset]),
        }

    latencies = [float(row["latency_ms"]) for row in rows]
    overall = aggregate(rows)
    overall["latency_ms_mean"] = _mean(latencies)
    overall["latency_ms_p50"] = statistics.median(latencies) if latencies else 0.0
    overall["latency_ms_max"] = max(latencies) if latencies else 0.0
    overall["index_ms"] = index_ms
    return {
        "k": k,
        "mrr_k": mrr_k,
        "ndcg_k": ndcg_k,
        "mode": mode,
        "n": len(rows),
        "overall": overall,
        "by_scene": {
            scene: aggregate([row for row in rows if row["scene"] == scene])
            for scene in sorted({row["scene"] for row in rows})
        },
        "by_type": {
            qtype: aggregate([row for row in rows if row["type"] == qtype])
            for qtype in sorted({row["type"] for row in rows})
        },
        "per_query": rows,
    }


def paired_bootstrap_ci(
    base: Sequence[float],
    other: Sequence[float],
    *,
    seed: int,
    rounds: int = 2000,
) -> tuple[float, float, float]:
    """固定 seed 的配对 bootstrap，返回 (点估计差值, 2.5% 分位, 97.5% 分位)。

    为什么用配对而不是独立重采样：两组跑的是同一批 80 条 query，query 难度是
    共同的配对因子；独立重采样会把「题目本身难易」的方差算进差值里，置信区间
    虚宽，容易把真实差异判成不显著。seed 固定保证报告每次生成完全一致。
    """
    if len(base) != len(other) or not base:
        raise ValueError("两组必须是等长的非空逐 query 指标")
    rng = random.Random(seed)
    n = len(base)
    diffs: list[float] = []
    for _ in range(rounds):
        picked = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(other[i] - base[i] for i in picked) / n)
    diffs.sort()
    point = sum(other[i] - base[i] for i in range(n)) / n
    low = diffs[int(0.025 * rounds)]
    high = diffs[min(rounds - 1, int(0.975 * rounds))]
    return point, low, high
