"""两路召回融合：RRF(k=60) 与加权分融合，不含 rerank。

为什么 RRF 用排名而不是原始分数：dense 是余弦相似度（有界、接近 1），BM25 是
无上界的词频统计，二者量纲不可加。RRF 只看名次，天然规避归一化口径之争，是
混合检索的默认起点；加权模式则显式做 min-max 归一化，供实验台对比两种口径。

权重/k 由调用方从配置传入，本模块不写死任何超参（阶段 2 要求权重可配）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FusionHit:
    chunk_id: str
    score: float
    dense_score: float
    sparse_score: float
    dense_rank: int | None
    sparse_rank: int | None


def _ranks(items: Sequence[str]) -> dict[str, int]:
    """1-based 名次；同一列表里重复 id 只认第一次，避免自己抬高自己的 RRF 分。"""
    out: dict[str, int] = {}
    for index, chunk_id in enumerate(items):
        out.setdefault(chunk_id, index + 1)
    return out


def _scores(items: Sequence[tuple[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for chunk_id, score in items:
        out.setdefault(chunk_id, float(score))
    return out


def _normalize(values: Mapping[str, float]) -> dict[str, float]:
    """min-max 到 [0,1]；单值或全相等时统一给 1，保住「被召回」这一信号。"""
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low < 1e-12:
        return {key: 1.0 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def rrf_fuse(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[FusionHit]:
    """reciprocal rank fusion：score = Σ w_i / (k + rank_i)，按分数降序。"""
    if k <= 0:
        raise ValueError("rrf k 必须为正")
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("权重数必须与召回路数一致")

    rank_maps = [_ranks(items) for items in rankings]
    totals: dict[str, float] = {}
    for rank_map, weight in zip(rank_maps, weights, strict=True):
        for chunk_id, rank in rank_map.items():
            totals[chunk_id] = totals.get(chunk_id, 0.0) + weight / (k + rank)

    dense_ranks = rank_maps[0] if rank_maps else {}
    sparse_ranks = rank_maps[1] if len(rank_maps) > 1 else {}
    return [
        FusionHit(
            chunk_id=chunk_id,
            score=round(score, 8),
            dense_score=1.0 / (k + dense_ranks[chunk_id]) if chunk_id in dense_ranks else 0.0,
            sparse_score=1.0 / (k + sparse_ranks[chunk_id]) if chunk_id in sparse_ranks else 0.0,
            dense_rank=dense_ranks.get(chunk_id),
            sparse_rank=sparse_ranks.get(chunk_id),
        )
        for chunk_id, score in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def weighted_fuse(
    dense: Sequence[tuple[str, float]],
    sparse: Sequence[tuple[str, float]],
    *,
    dense_weight: float,
    sparse_weight: float,
) -> list[FusionHit]:
    """各路 min-max 归一化后线性加权；权重不要求和为 1（由配置决定口径）。"""
    dense_norm = _normalize(_scores(dense))
    sparse_norm = _normalize(_scores(sparse))
    dense_ranks = _ranks([chunk_id for chunk_id, _ in dense])
    sparse_ranks = _ranks([chunk_id for chunk_id, _ in sparse])
    chunk_ids = set(dense_norm) | set(sparse_norm)
    hits = [
        FusionHit(
            chunk_id=chunk_id,
            score=round(
                dense_weight * dense_norm.get(chunk_id, 0.0)
                + sparse_weight * sparse_norm.get(chunk_id, 0.0),
                8,
            ),
            dense_score=dense_norm.get(chunk_id, 0.0),
            sparse_score=sparse_norm.get(chunk_id, 0.0),
            dense_rank=dense_ranks.get(chunk_id),
            sparse_rank=sparse_ranks.get(chunk_id),
        )
        for chunk_id in chunk_ids
    ]
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))


def fuse(
    dense: Sequence[tuple[str, float]],
    sparse: Sequence[tuple[str, float]],
    *,
    method: str,
    rrf_k: int = 60,
    dense_weight: float = 0.6,
    sparse_weight: float = 0.4,
) -> list[FusionHit]:
    """统一入口；service 只传配置，不在这里做策略判断之外的分支。"""
    if method == "rrf":
        return rrf_fuse([[chunk_id for chunk_id, _ in dense],
                         [chunk_id for chunk_id, _ in sparse]], k=rrf_k)
    if method == "weighted":
        return weighted_fuse(dense, sparse,
                            dense_weight=dense_weight, sparse_weight=sparse_weight)
    raise ValueError(f"未知融合方式: {method}")
