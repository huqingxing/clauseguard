"""阶段 4 指标层的单测：重点是 macro 口径、nDCG 归一化与可复现的显著性判断。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metrics import (  # noqa: E402
    Case,
    evaluate,
    ndcg_at_k,
    paired_bootstrap_ci,
    per_query_metrics,
    summarize,
)

G1 = ["civil_code:第五百八十五条:0:S1_whole"]
G2 = ["civil_code:第五百八十四条:0:S1_whole"]
G3 = ["civil_code:第五百八十三条:0:S1_whole"]
MISS = ["civil_code:第五百七十七条:0:S1_whole"]


def test_ndcg_single_gold_top1_is_one() -> None:
    assert ndcg_at_k(G1, G1, 10) == 1.0


def test_ndcg_discounts_lower_rank() -> None:
    assert ndcg_at_k([MISS[0], G1[0]], G1, 10) == pytest.approx(1 / math.log2(3))


def test_ndcg_respects_k_and_missing_gold() -> None:
    assert ndcg_at_k([G1[0]], G1, 0) == 0.0
    assert ndcg_at_k([MISS[0], G1[0]], G1, 1) == 0.0
    assert ndcg_at_k(G1, [], 10) == 0.0


def test_ndcg_multi_gold_uses_min_gold_k_for_idcg() -> None:
    gold = G1 + G2
    # 两个理想命中都在前 2 位 => DCG == IDCG => 1.0
    assert ndcg_at_k([G1[0], G2[0]], gold, 10) == 1.0
    # 只召回 1 个但排第 1 => 1 / (1 + 1/log2(3))
    assert ndcg_at_k([G1[0]], gold, 10) == pytest.approx(1 / (1 + 1 / math.log2(3)))


def test_ndcg_never_exceeds_one_with_duplicate_article_chunks() -> None:
    # S4_paragraph 把一条法条切成多个款；top-k 里同 article 的多个切片
    # 只能贡献一次增益，否则 DCG > IDCG（真库跑批曾测到 nDCG=1.93）。
    retrieved = [
        G1[0],
        "civil_code:第五百八十五条:1:S4_paragraph",
        "civil_code:第五百八十五条:2:S4_paragraph",
        "civil_code:第五百八十五条:3:S4_paragraph",
    ]
    assert ndcg_at_k(retrieved, G1, 10) == 1.0
    assert ndcg_at_k(retrieved, G1 + G2, 10) == pytest.approx(1 / (1 + 1 / math.log2(3)))


def test_summarize_macro_weights_each_query_equally() -> None:
    # q1 有 2 个 gold 只中 1 个（recall=0.5），q2 有 1 个 gold 全中（recall=1）。
    # macro 平均 = 0.75；micro 按 gold 数加权会是 2/3，两者必须能区分。
    cases = [
        Case("q1", [G1[0]], G1 + G2, "liability", "concept", latency_ms=10.0),
        Case("q2", [G3[0]], G3, "ip", "scene", latency_ms=30.0),
    ]
    report = summarize(cases, k=5, index_ms=123.0)
    assert report["n"] == 2
    assert report["overall"]["recall"] == pytest.approx(0.75)
    assert report["overall"]["hit_rate"] == 1.0
    assert report["overall"]["latency_ms_mean"] == pytest.approx(20.0)
    assert report["overall"]["latency_ms_p50"] == pytest.approx(20.0)
    assert report["overall"]["latency_ms_max"] == 30.0
    assert report["overall"]["index_ms"] == 123.0
    assert report["overall"]["mrr"] == 1.0
    assert report["overall"]["ndcg"] == pytest.approx((1.0 / (1 + 1 / math.log2(3)) + 1.0) / 2)


def test_summarize_groups_and_keeps_per_query_detail() -> None:
    cases = [
        Case("q1", G1, G1, "liability", "concept", 5.0),
        Case("q2", MISS, G2, "data", "colloquial", 7.0),
    ]
    report = summarize(cases)
    assert set(report["by_scene"]) == {"liability", "data"}
    assert report["by_scene"]["data"]["recall"] == 0.0
    assert [row["qid"] for row in report["per_query"]] == ["q1", "q2"]
    assert report["per_query"][1]["latency_ms"] == 7.0
    assert set(report["by_type"]) == {"concept", "colloquial"}


def test_summarize_empty_cases() -> None:
    report = summarize([])
    assert report["n"] == 0
    assert report["overall"]["recall"] == 0.0
    assert report["per_query"] == []


def test_per_query_metrics_honors_mrr_and_ndcg_k() -> None:
    cases = [Case("q1", [MISS[0], G1[0], G2[0]], G1 + G2, "ip", "scene")]
    row = per_query_metrics(cases, k=5, mrr_k=10, ndcg_k=10)[0]
    assert row["mrr"] == pytest.approx(0.5)
    assert row["recall"] == 1.0
    assert row["ndcg"] == pytest.approx(
        (1 / math.log2(3) + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    )


def test_paired_bootstrap_is_deterministic_and_detects_shift() -> None:
    base = [0.0] * 40
    other = [1.0] * 40
    point, low, high = paired_bootstrap_ci(base, other, seed=20261107)
    assert point == 1.0
    assert low > 0.0 and high > 0.0  # 恒定正差值，CI 不含 0 => 显著
    assert paired_bootstrap_ci(base, other, seed=20261107) == (point, low, high)


def test_paired_bootstrap_zero_effect_interval_contains_zero() -> None:
    same = [0.2, 0.8] * 20
    point, low, high = paired_bootstrap_ci(same, same, seed=42)
    assert point == 0.0 and low == 0.0 and high == 0.0


def test_paired_bootstrap_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="等长"):
        paired_bootstrap_ci([1.0], [1.0, 2.0], seed=1)


def test_stage3_evaluate_still_works_and_reports_ndcg() -> None:
    """阶段 4 扩展不得破坏阶段 3 的 evaluate 契约。"""
    report = evaluate([Case("q1", G1, G1, "liability", "concept")], k=5)
    assert report["n"] == 1
    assert report["overall"]["hit_rate"] == 1.0
    assert report["overall"]["ndcg"] == 1.0
