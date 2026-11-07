"""供 Java MCP 转发的检索端点；只做参数校验、计时与配置回显。

为什么服务实例懒加载：app.main 被 300+ 现有单测直接 create_app() 导入，若模块
导入时就连 Postgres/BM25，未起库的环境会集体失败。懒加载 + 可注入保证「不加
检索端点」的既有行为完全不变。

阶段 6：请求里可省略的字段一律回落到 config/retrieval.yaml（实验台结论），调用方
只需给 query。这样 Java 里不会出现第二份参数副本——否则「改 YAML 重启生效」会被
Java 写死的 top_k / strategy 悄悄覆盖，配置漂移还很难查。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from retrieval.config import get_retrieval_settings
from retrieval.service import RetrievalService, SearchMeta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/retrieval", tags=["retrieval"])

_service: RetrievalService | None = None


def set_service(service: RetrievalService | None) -> None:
    """测试注入 / 关闭时清理；生产路径由 get_service 自动构造。"""
    global _service
    _service = service


def get_service() -> RetrievalService:
    global _service
    if _service is None:
        from retrieval.embedder import Embedder
        from retrieval.store import ChunkStore

        settings = get_retrieval_settings()
        _service = RetrievalService(
            store=ChunkStore(settings.pg_dsn, schema_dim=settings.resolved_embedding()[1]),
            embedder=Embedder(settings),
            settings=settings,
        )
    return _service


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # None = 用 YAML 的 top_k；显式给出时以调用方为准（实验台压测需要改 k）。
    k: int | None = Field(default=None, ge=1, le=100)
    strategy: str | None = None
    # 调用方（Java）已算好的 query 向量；传入时服务端不再调 embedding 接口，
    # 这也是压测 P50 时不把外部 API 延迟算进检索耗时的关键。
    embedding: list[float] | None = None
    use_bm25: bool | None = None
    use_rerank: bool | None = None


class HitPayload(BaseModel):
    chunk_id: str
    law: str
    article: str
    text: str
    score: float
    dense_score: float
    sparse_score: float


class SearchResponse(BaseModel):
    hits: list[HitPayload]
    elapsed_ms: float
    config: dict[str, object]


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest) -> SearchResponse:
    """同步 def：psycopg 与 rank_bm25 都是阻塞 IO，交给 FastAPI 线程池执行。"""
    service = get_service()
    settings = service.settings
    effective_k = body.k if body.k is not None else settings.default_k
    effective_strategy = body.strategy or settings.index_strategy
    effective_bm25 = body.use_bm25 if body.use_bm25 is not None else settings.use_bm25_default
    effective_rerank = (
        body.use_rerank if body.use_rerank is not None else settings.use_rerank_default
    )
    started = time.perf_counter()
    try:
        hits = service.search(
            query=body.query,
            k=effective_k,
            strategy=effective_strategy,
            embedding=body.embedding,
            use_bm25=effective_bm25,
            use_rerank=effective_rerank,
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 库未起/扩展缺失时给结构化错误，便于 Java 侧定位
        logger.exception("retrieval search failed")
        raise HTTPException(status_code=503, detail=f"检索后端不可用: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    model, dim, _ = settings.resolved_embedding()
    meta = SearchMeta(
        strategy=effective_strategy,
        fusion_method=settings.fusion_method,
        embedding_backend=settings.embedding_backend,
        embedding_model=model,
        embedding_dim=dim,
        rrf_k=settings.rrf_k,
        dense_weight=settings.dense_weight,
        sparse_weight=settings.sparse_weight,
        use_dense=True,
        use_bm25=bool(effective_bm25),
        use_rerank=effective_rerank,
        candidate_k=settings.candidate_k,
        elapsed_ms=round(elapsed_ms, 3),
        query_embedding_source="provided" if body.embedding else f"computed:{model}",
    )
    config = dict(service.describe())
    # SearchMeta 是 slots dataclass，没有 __dict__；asdict 同时保证不把密钥带出去。
    config.update(asdict(meta))
    config["top_k"] = effective_k
    return SearchResponse(
        hits=[HitPayload(**asdict(hit)) for hit in hits],
        elapsed_ms=round(elapsed_ms, 3),
        config=config,
    )
