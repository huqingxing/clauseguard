"""检索编排：dense / sparse 两路召回 → 融合 → Hit 列表。

为什么把「取候选」和「融合」分开：候选池用 candidate_k（默认 50）而非最终 k，
否则只在某一路排进前 k 的文档，即使另一路也给高分也进不了融合，混合检索会
退化成两路 top-k 的简单拼接。

为什么不接 rerank：阶段 2 明确先不引入；use_rerank=True 时显式报错而非静默忽略，
避免调用方以为已经生效、拿到的却是未重排的结果。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from retrieval.bm25 import BM25Index
from retrieval.config import RetrievalSettings, get_retrieval_settings
from retrieval.embedder import Embedder
from retrieval.fusion import FusionHit, fuse
from retrieval.store import ChunkRow, ChunkStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: str
    law: str
    article: str
    text: str
    score: float
    dense_score: float
    sparse_score: float


@dataclass(frozen=True, slots=True)
class SearchMeta:
    """所用配置与耗时，随响应返回，让每次结果可复现、可归因。"""

    strategy: str
    fusion_method: str
    embedding_backend: str
    embedding_model: str
    embedding_dim: int
    rrf_k: int
    dense_weight: float
    sparse_weight: float
    use_dense: bool
    use_bm25: bool
    use_rerank: bool
    candidate_k: int
    elapsed_ms: float
    query_embedding_source: str


class RetrievalService:
    def __init__(
        self,
        store: ChunkStore,
        embedder: Embedder | None = None,
        settings: RetrievalSettings | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_retrieval_settings()
        self.embedder = embedder
        self._bm25_cache: dict[str, BM25Index] = {}

    def describe(self) -> dict[str, object]:
        """返回当前生效配置（不含密钥），供 /v1/retrieval/search 回显。"""
        model, dim, _ = self.settings.resolved_embedding()
        return {
            "embedding_backend": self.settings.embedding_backend,
            "embedding_model": model,
            "embedding_dim": dim,
            "fusion_method": self.settings.fusion_method,
            "rrf_k": self.settings.rrf_k,
            "dense_weight": self.settings.dense_weight,
            "sparse_weight": self.settings.sparse_weight,
            "candidate_k": self.settings.candidate_k,
            "default_strategy": self.settings.index_strategy,
            "provider_batch_limit": self.settings.provider_batch_limit,
        }

    def warmup(self, strategy: str | None = None) -> int:
        """从 PG 全量加载并构建 BM25 内存索引；返回索引文档数。"""
        resolved = strategy or self.settings.index_strategy
        if resolved not in self._bm25_cache:
            rows = self.store.load_all(resolved)
            self._bm25_cache[resolved] = BM25Index(rows, self.settings.stopwords_file)
            logger.info("BM25 索引就绪 strategy=%s docs=%d", resolved, len(rows))
        return len(self._bm25_cache[resolved])

    def _embedder(self) -> Embedder:
        # 延迟创建：BM25-only 的查询不应因为密钥/网络缺失而失败。
        if self.embedder is None:
            self.embedder = Embedder(self.settings)
        return self.embedder

    def _query_vector(
        self, query: str, embedding: Sequence[float] | None
    ) -> tuple[list[float], str]:
        if embedding is not None:
            vector = list(map(float, embedding))
            expected = self.settings.resolved_embedding()[1]
            if len(vector) != expected:
                raise ValueError(f"传入 embedding 为 {len(vector)} 维，配置要求 {expected} 维")
            return vector, "provided"
        result = self._embedder().embed([query])
        return result.vectors[0], f"computed:{self.settings.resolved_embedding()[0]}"

    def _single_channel(
        self, items: Sequence[tuple[str, float]], channel: str
    ) -> list[FusionHit]:
        """单路召回不套融合公式：RRF 会把原始相似度压成 1/(k+rank)，丢掉分数语义。"""
        ranks: dict[str, int] = {}
        for index, (chunk_id, _) in enumerate(items):
            ranks.setdefault(chunk_id, index + 1)
        if channel == "dense":
            return [
                FusionHit(chunk_id, round(score, 8), score, 0.0, ranks[chunk_id], None)
                for chunk_id, score in items
            ]
        return [
            FusionHit(chunk_id, round(score, 8), 0.0, score, None, ranks[chunk_id])
            for chunk_id, score in items
        ]

    def search(
        self,
        query: str,
        k: int = 5,
        strategy: str | None = None,
        embedding: Sequence[float] | None = None,
        use_bm25: bool | None = None,
        use_rerank: bool = False,
        *,
        use_dense: bool = True,
    ) -> list[Hit]:
        """按任务签名返回 list[Hit]；use_dense 为 keyword-only 扩展，供 bm25-only 验收。"""
        if not query.strip():
            raise ValueError("query 不能为空")
        if use_rerank:
            raise NotImplementedError("阶段 2 不接 rerank；rerank 留到阶段 3")
        requested_bm25 = use_bm25 if use_bm25 is not None else self.settings.use_bm25_default
        if not use_dense and not requested_bm25:
            raise ValueError("dense 与 bm25 不能同时关闭")

        resolved_strategy = strategy or self.settings.index_strategy
        want_bm25 = requested_bm25 and self.settings.bm25_enabled
        candidate_k = max(k, self.settings.candidate_k)
        rows: dict[str, ChunkRow] = {}
        dense_items: list[tuple[str, float]] = []
        sparse_items: list[tuple[str, float]] = []

        if use_dense:
            vector, _source = self._query_vector(query, embedding)
            scored = self.store.dense_search(vector, candidate_k, resolved_strategy)
            for item in scored:
                rows.setdefault(item.row.chunk_id, item.row)
                dense_items.append((item.row.chunk_id, item.dense_score))

        if want_bm25:
            index = self._bm25_cache.get(resolved_strategy)
            if index is None:
                self.warmup(resolved_strategy)
                index = self._bm25_cache[resolved_strategy]
            for row in index.rows:
                rows.setdefault(row.chunk_id, row)
            for hit in index.search(query, candidate_k):
                sparse_items.append((hit.chunk_id, hit.sparse_score))

        if use_dense and want_bm25:
            fused = fuse(
                dense_items, sparse_items,
                method=self.settings.fusion_method,
                rrf_k=self.settings.rrf_k,
                dense_weight=self.settings.dense_weight,
                sparse_weight=self.settings.sparse_weight,
            )
        elif use_dense:
            fused = self._single_channel(dense_items, "dense")
        else:
            fused = self._single_channel(sparse_items, "bm25")

        missing = [hit.chunk_id for hit in fused[:k] if hit.chunk_id not in rows]
        if missing:
            rows.update(self.store.fetch_by_ids(missing))

        # 融合分（score）与两路原始分（dense/sparse）分开回显：RRF 分只有相对
        # 名次意义，直接拿来当 dense_score 会让「同一文档在 dense-only 与 hybrid
        # 下分数不同」，三组对比无法解释。原始分由召回阶段单独保留。
        dense_raw = dict(dense_items)
        sparse_raw = dict(sparse_items)

        hits: list[Hit] = []
        for fused_hit in fused[:k]:
            matched = rows.get(fused_hit.chunk_id)
            if matched is None:  # 索引与库不同步时跳过，而不是返回缺字段的假 Hit
                logger.warning("chunk %s 在库中不存在，已跳过", fused_hit.chunk_id)
                continue
            hits.append(
                Hit(
                    chunk_id=matched.chunk_id,
                    law=matched.law,
                    article=matched.article,
                    text=matched.text,
                    score=fused_hit.score,
                    dense_score=round(dense_raw.get(fused_hit.chunk_id, 0.0), 8),
                    sparse_score=round(sparse_raw.get(fused_hit.chunk_id, 0.0), 8),
                )
            )
        return hits
