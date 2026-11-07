"""矩阵实验的索引层：把阶段 2 的 RetrievalService 接到内存向量矩阵上。

为什么不直接连 PG 跑 15 组：E3 是 512 维，写不进 law_chunk.embedding
vector(1024) 单列；15 组还要反复换列/换表，等于为了跑批去改阶段 2 已验收的
存储实现。这里让 store 变成一个可注入的内存实现，检索编排、RRF 融合、BM25
分词与线上完全同源——矩阵里变化的只有「切分策略」和「向量档位」两个变量，
结论才能迁移回线上。

为什么 rows 按 chunk_id 排序后用 stable argsort：余弦分数相同的情况在短法条上
很常见，排序算法的 tie-break 一旦不稳定，同 label 两次重跑就会给出不同 top-k，
「可复现」验收会假失败。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from retrieval.bm25 import BM25Index
from retrieval.chunker import ALL_STRATEGIES, Strategy, chunk_corpus
from retrieval.config import RetrievalSettings
from retrieval.corpus.loader import load_corpus
from retrieval.service import Hit, RetrievalService
from retrieval.store import ChunkRow, ScoredRow

from lab.rerank import DeterministicLexicalRerank, RerankResult, Reranker

VALID_STRATEGIES: tuple[str, ...] = tuple(str(strategy) for strategy in ALL_STRATEGIES)


def build_chunks(strategy: str) -> list[Any]:
    """从阶段 1 的真实语料切出指定策略的 chunk；排序后返回，保证索引顺序确定。"""
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f"未知切分策略: {strategy}（可选 {VALID_STRATEGIES}）")
    chunks = chunk_corpus(load_corpus(), Strategy(strategy))
    return sorted(chunks, key=lambda chunk: chunk.chunk_id)


def to_rows(chunks: Sequence[Any]) -> list[ChunkRow]:
    return [
        ChunkRow(
            chunk_id=chunk.chunk_id,
            law=chunk.law,
            article=chunk.article,
            chapter=chunk.chapter,
            text=chunk.text,
            strategy=str(chunk.strategy),
        )
        for chunk in chunks
    ]


class MatrixStore:
    """实现 ChunkStore 的读接口，供 RetrievalService 原样注入。"""

    def __init__(self, rows: Sequence[ChunkRow], matrix: np.ndarray) -> None:
        if len(rows) != matrix.shape[0]:
            raise ValueError(f"rows={len(rows)} 与 matrix={matrix.shape[0]} 不对齐")
        self._rows = list(rows)
        self._matrix = matrix
        self._by_id = {row.chunk_id: row for row in rows}

    def load_all(self, strategy: str | None = None) -> list[ChunkRow]:
        return list(self._rows)

    def fetch_by_ids(self, chunk_ids: Sequence[str]) -> dict[str, ChunkRow]:
        return {cid: self._by_id[cid] for cid in chunk_ids if cid in self._by_id}

    def dense_search(
        self, query_embedding: Sequence[float], k: int, strategy: str | None = None
    ) -> list[ScoredRow]:
        query = np.asarray(query_embedding, dtype=np.float32)
        # 文档向量已 L2 归一化，点积即余弦相似度；与阶段 2 的 1 - cosine_distance 同口径。
        scores = self._matrix @ query
        order = np.argsort(-scores, kind="stable")[:k]
        return [
            ScoredRow(row=self._rows[int(index)], dense_score=float(scores[int(index)]))
            for index in order
        ]


def build_bm25(rows: Sequence[ChunkRow]) -> BM25Index:
    return BM25Index(rows)


def make_service(
    store: MatrixStore,
    *,
    strategy: str,
    embedding: str,
    use_bm25: bool,
    candidate_k: int,
    bm25: BM25Index | None,
) -> RetrievalService:
    """固定融合超参，避免 .env 里的开发配置悄悄改变实验口径。

    _env_file=None 是刻意的：评测必须不读开发者本机 .env，否则同一 label 在
    不同机器上跑出不同结果，可复现验收就失去意义（阶段 15 的教训）。
    """
    settings = RetrievalSettings(
        # 评测禁止读本机 .env；该参数由 pydantic-settings 在构造期动态接受。
        _env_file=None,  # type: ignore[call-arg]
        embedding_backend=embedding,  # type: ignore[arg-type]
        fusion_method="rrf",
        rrf_k=60,
        dense_weight=0.6,
        sparse_weight=0.4,
        candidate_k=candidate_k,
        default_k=5,
        index_strategy=strategy,
        use_bm25_default=use_bm25,
        bm25_enabled=use_bm25,
    )
    # MatrixStore 按鸭子类型实现读接口，不改阶段 2 的 ChunkStore 继承关系。
    service = RetrievalService(store=store, settings=settings)  # type: ignore[arg-type]
    if bm25 is not None:
        # BM25 与 embedding 档位无关，按 strategy 建一次后注入，避免 15 组重复分词。
        service._bm25_cache[strategy] = bm25
    return service


def retrieve(
    service: RetrievalService,
    *,
    query: str,
    query_vector: Sequence[float],
    k: int,
    strategy: str,
    use_bm25: bool,
    use_rerank: bool,
    candidate_k: int,
    reranker: Reranker | None = None,
    rerank_candidates: int = 0,
    candidate_pool: int = 0,
    out: dict[str, Any] | None = None,
) -> list[Hit]:
    """单次检索；返回用于指标计算的完整有序列表。

    为什么阶段 5 注入 reranker 后返回的是完整候选池而不是 top-k：`summarize()`
    用 retrieved[:5] 算 recall、[:10] 算 mrr/ndcg。若这里先截成 5 条，@10
    指标就被人为削平，on/off 的排序质量差异看不出来；off 侧同样取完整池，
    两侧才严格同口径。任务书要求的「输出 top-5」由 RerankResult.top5 承载。

    为什么保留 use_rerank 这个旧分支：阶段 4 的 `--rerank on` 用它接通开关并
    验证可复现，直接删掉会让已交付的矩阵实验行为变化。它走确定性离线代理，
    与阶段 5 的真实 reranker 互不干扰。
    """
    if reranker is not None:
        pool = rerank_candidates or candidate_k or k
        hits = service.search(
            query,
            k=pool,
            strategy=strategy,
            embedding=query_vector,
            use_bm25=use_bm25,
            use_rerank=False,
        )
        result = reranker.rerank(query, hits, top_n=k)
        if out is not None:
            out["rerank"] = result
        return list(result.ranked)

    # off 侧也要能拿完整候选池：阶段 5 的基线必须和 rerank 侧从同一个 top-20
    # 出发，否则 @10 指标在两侧的分母不同，「延迟换召回」无从比较。
    pool = candidate_pool or (candidate_k if use_rerank else k)
    hits = service.search(
        query,
        k=pool,
        strategy=strategy,
        embedding=query_vector,
        use_bm25=use_bm25,
        use_rerank=False,
    )
    if not use_rerank:
        return hits
    proxy = DeterministicLexicalRerank()
    result: RerankResult = proxy.rerank(query, hits, top_n=k)
    return list(result.top5)
