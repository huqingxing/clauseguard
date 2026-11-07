"""融合函数单测：RRF 与加权各 2 条（任务硬性要求），外加边界与统一入口。

为什么用构造的榜单而不是真实召回结果：融合只依赖名次与分数，真实语料会让
断言掺入分词/向量化的不确定性；这里要证明的是融合公式本身。
"""

from __future__ import annotations

import pytest

from retrieval.fusion import fuse, rrf_fuse, weighted_fuse

# ---- RRF ------------------------------------------------------------------

def test_rrf_two_routes_shared_top_rises_first() -> None:
    """两路都靠前的文档必须超过各自单路第一：RRF 累加的是各路倒数名次贡献。"""
    dense = ["A", "B", "C"]
    sparse = ["B", "C", "A"]
    hits = rrf_fuse([dense, sparse], k=60)

    # B 在两路都是前二（1/62 + 1/61），A 虽是 dense 第一但 sparse 第三（1/61 + 1/63）。
    assert hits[0].chunk_id == "B"
    assert hits[1].chunk_id == "A"
    assert hits[0].score > hits[1].score
    assert hits[0].dense_rank == 2 and hits[0].sparse_rank == 1
    assert hits[1].dense_rank == 1 and hits[1].sparse_rank == 3
    assert hits[0].dense_score == pytest.approx(1 / 62)
    assert hits[0].sparse_score == pytest.approx(1 / 61)

    # 两路名次互换且完全对称时分数应相同，排序退化为 chunk_id 升序（确定性）。
    tied = rrf_fuse([["A", "B"], ["B", "A"]], k=60)
    assert [hit.chunk_id for hit in tied] == ["A", "B"]
    assert tied[0].score == pytest.approx(tied[1].score)


def test_rrf_k_changes_relative_gap_and_weights_apply() -> None:
    """k 越大名次差异越被压平；权重只放大对应路的贡献。"""
    dense = ["A", "B"]
    sparse = ["B"]
    small_k = {hit.chunk_id: hit.score for hit in rrf_fuse([dense, sparse], k=1)}
    large_k = {hit.chunk_id: hit.score for hit in rrf_fuse([dense, sparse], k=1000)}
    gap_small = small_k["A"] - small_k["B"]
    gap_large = large_k["A"] - large_k["B"]
    assert abs(gap_small) > abs(gap_large)

    plain = {hit.chunk_id: hit.score for hit in rrf_fuse([dense, sparse], k=60)}
    # A 只在 dense 路出现：dense 权重翻倍，其分数应恰好翻倍。
    dense_x2 = {hit.chunk_id: hit.score for hit in
                rrf_fuse([dense, sparse], k=60, weights=[2.0, 0.0])}
    assert dense_x2["A"] == pytest.approx(2 * plain["A"])
    # B 在两路都出现：只把 sparse 权重减半，其分数应下降但 dense 分量不变。
    sparse_half = {hit.chunk_id: hit.score for hit in
                   rrf_fuse([dense, sparse], k=60, weights=[1.0, 0.5])}
    assert sparse_half["B"] < plain["B"]
    assert sparse_half["A"] == pytest.approx(plain["A"])


# ---- 加权 ------------------------------------------------------------------

def test_weighted_pure_dense_degrades_to_dense_order() -> None:
    """sparse_weight=0 时结果排序必须完全等于 dense 路，且只保留 dense 命中。"""
    dense = [("A", 0.9), ("B", 0.5), ("C", 0.1)]
    sparse = [("C", 99.0), ("D", 50.0)]
    hits = weighted_fuse(dense, sparse, dense_weight=1.0, sparse_weight=0.0)

    assert [hit.chunk_id for hit in hits[:3]] == ["A", "B", "C"]
    assert hits[0].score == pytest.approx(1.0)  # 归一化后最大值
    assert hits[2].score == pytest.approx(0.0)  # 归一化后最小值
    assert hits[3].chunk_id == "D" and hits[3].score == 0.0  # 仅稀疏命中，权重为 0
    assert hits[0].dense_rank == 1 and hits[0].sparse_rank is None


def test_weighted_normalizes_scale_before_combining() -> None:
    """BM25 分数无上界，未归一化会让稀疏路完全淹没向量相似度。"""
    dense = [("A", 0.80), ("B", 0.20)]
    sparse = [("B", 100.0), ("A", 1.0)]
    hits = weighted_fuse(dense, sparse, dense_weight=0.5, sparse_weight=0.5)
    by_id = {hit.chunk_id: hit.score for hit in hits}

    # dense 归一化 A=1,B=0；sparse 归一化 B=1,A=0 → 各得 0.5，不应出现 50:0.4 的悬殊。
    assert by_id["A"] == pytest.approx(0.5)
    assert by_id["B"] == pytest.approx(0.5)
    assert hits[0].dense_score in (0.0, 1.0)
    assert hits[0].sparse_score in (0.0, 1.0)


# ---- 统一入口与边界 --------------------------------------------------------

def test_fuse_dispatch_rrf_uses_rank_not_score() -> None:
    """A 的 dense 分更高，但 B 在两路都有名次 → RRF 只看名次，B 胜。"""
    dense = [("A", 0.99), ("B", 0.01)]
    sparse = [("B", 100.0), ("C", 1.0)]
    hits = fuse(dense, sparse, method="rrf", rrf_k=60,
                dense_weight=0.5, sparse_weight=0.5)
    assert hits[0].chunk_id == "B"
    assert {hit.chunk_id for hit in hits} == {"A", "B", "C"}


def test_fuse_dispatch_weighted_uses_normalized_scores() -> None:
    """加权模式下 A 的 dense 分量占优即可反超，证明入口确实走了另一条公式。"""
    dense = [("A", 0.9), ("B", 0.1)]
    sparse = [("B", 2.0), ("A", 1.0)]
    hits = fuse(dense, sparse, method="weighted", rrf_k=60,
                dense_weight=0.8, sparse_weight=0.2)
    assert hits[0].chunk_id == "A"
    assert hits[0].score == pytest.approx(0.8)
    assert {hit.chunk_id for hit in hits} == {"A", "B"}


def test_fuse_boundaries() -> None:
    assert rrf_fuse([], k=60) == []
    assert weighted_fuse([], [], dense_weight=1.0, sparse_weight=0.0) == []
    with pytest.raises(ValueError, match="k 必须为正"):
        rrf_fuse([["A"]], k=0)
    with pytest.raises(ValueError, match="权重数"):
        rrf_fuse([["A"]], k=60, weights=[1.0, 2.0])
    with pytest.raises(ValueError, match="未知融合方式"):
        fuse([], [], method="bm25")
